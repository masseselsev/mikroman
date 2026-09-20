package services

import (
	"context"
	"fmt"
	"log/slog"
	"runtime"
	"runtime/debug"
	"strconv"
	"time"

	"github.com/masseselsev/mikroman/internal/db"
)

// Raw metric rows are only needed as long as their bucket is still being written: once a
// 15-minute window has closed, its bucket is final and the raw samples behind it can go.
// RawRetentionDays is therefore the memory of the fix, not a chart horizon — charts read
// buckets, and buckets are kept far longer. Defaults chosen so an unconfigured install
// still answers the full 30-day chart horizon from buckets and holds at most about a day
// of raw samples.
const (
	defaultRawMetricRetentionDays    = 2
	defaultMetricBucketRetentionDays = 400
)

// rawMetricPruneBatch is the number of raw rows deleted per pruning batch. Bounded so a
// pass never holds the single SQLite writer lock long enough to starve the 5-second
// busy_timeout of other workers. (Kept a var: tests shrink it to force the multi-batch
// path.) Buckets are small — one row per 15 minutes per router/interface — and pruned in
// one statement per table; the range delete stays cheap even for a year of data.
var rawMetricPruneBatch = 5000

// getRetentionDays resolves a retention setting in days, falling back to def when unset
// or malformed. A value below 1 is ignored rather than wiping the table by accident.
func getRetentionDays(database *db.DB, key string, def int) int {
	val, err := database.GetSetting(key)
	if err != nil || val == "" {
		return def
	}
	if d, err := strconv.Atoi(val); err == nil && d > 0 {
		return d
	}
	return def
}

// pruneRawMetricTable deletes rows older than cutoff, oldest first, in batches. Each
// batch selects its rowids through the timestamp index — an ORDER-free seek to the
// oldest survivors plus a LIMIT walk — so batch work is O(batch), not O(table), and the
// predicate is exactly "timestamp < cutoff" regardless of insertion order. The earlier
// design computed a max(id)-below-cutoff anchor per batch to walk the clustered rowid
// order instead; that anchor cannot use any index (max over a filtered set), rescanned
// every old row per batch, and on a router-sized history blew the prune deadline after
// deleting only part of the backlog.
func pruneRawMetricTable(ctx context.Context, database *db.DB, table, cutoff string) (int64, error) {
	var total int64
	var batches int
	started := time.Now()
	for {
		if err := ctx.Err(); err != nil {
			return total, fmt.Errorf("%w (after %d batches, %d rows, %v)", err, batches, total, time.Since(started).Round(time.Millisecond))
		}

		// DELETE ... LIMIT is not plain SQLite syntax; the batch is a subselect whose
		// index walk stops at rawMetricPruneBatch rowids.
		res, err := database.SqlDB.ExecContext(ctx, fmt.Sprintf(
			"DELETE FROM %s WHERE id IN (SELECT id FROM %s WHERE timestamp < ? LIMIT ?)",
			table, table), cutoff, rawMetricPruneBatch)
		if err != nil {
			return total, fmt.Errorf("delete %s prune batch after %d batches/%d rows in %v: %w",
				table, batches, total, time.Since(started).Round(time.Millisecond), err)
		}
		deleted, err := res.RowsAffected()
		if err != nil {
			return total, err
		}
		total += deleted
		batches++

		if deleted == 0 {
			return total, nil
		}
		// One writer per database: let the collector, scrapers and API readers interleave
		// between batches instead of queuing behind one long transaction.
		select {
		case <-ctx.Done():
			return total, ctx.Err()
		default:
		}
	}
}

// PruneRawMetrics removes raw system_metrics and interface_metrics rows past the
// configured retention. The rowid order of these append-only tables is time order, so the
// oldest rows go first and the pass leaves the newest retention window untouched. Deletion
// is batched and never drops below the bucket horizon: the raw rows that are still needed
// to answer the 24 h/7 d/30 d chart queries at raw speed (and to re-run the backfill)
// survive until their buckets alone are sufficient.
func (s *TelemetryService) PruneRawMetrics(ctx context.Context) (int64, error) {
	retentionDays := getRetentionDays(s.database, "raw_metric_retention_days", defaultRawMetricRetentionDays)
	// Buckets stay above raw: the whole point is that chart horizons outlive raw samples.
	bucketRetentionDays := getRetentionDays(s.database, "metric_bucket_retention_days", defaultMetricBucketRetentionDays)
	if bucketRetentionDays < retentionDays {
		bucketRetentionDays = retentionDays
	}

	cutoff := time.Now().UTC().AddDate(0, 0, -retentionDays).Format("2006-01-02 15:04:05")

	var total int64
	for _, table := range []string{"system_metrics", "interface_metrics"} {
		started := time.Now()
		deleted, err := pruneRawMetricTable(ctx, s.database, table, cutoff)
		if deleted > 0 {
			// Throughput is the field-diagnostic that matters for this loop: a pass that
			// dies at its deadline should show how far it got and how fast, not just that
			// it timed out. Logged on error paths too — `deleted` is the progress made.
			slog.Info("Raw metric prune pass", "table", table, "deleted", deleted,
				"seconds", time.Since(started).Round(time.Millisecond).Seconds(), "err", err)
		}
		if err != nil {
			return total, fmt.Errorf("prune %s: %w", table, err)
		}
		total += deleted
	}

	// Prune the bucket tables along the raw ones so the downsampling itself cannot grow
	// without bound; one bounded range delete per table.
	bucketCutoff := time.Now().UTC().AddDate(0, 0, -bucketRetentionDays).Format("2006-01-02 15:04:05")
	for _, table := range []string{"system_metric_buckets", "interface_metric_buckets"} {
		if _, err := s.database.SqlDB.ExecContext(ctx, fmt.Sprintf(
			"DELETE FROM %s WHERE bucket_start < ?", table), bucketCutoff); err != nil {
			return total, fmt.Errorf("prune %s: %w", table, err)
		}
	}

	if total > 0 {
		// Truncate WAL sidecars back to 0 bytes so unreferenced pages do not linger
		// inside the Linux page cache / cgroup memory quota.
		if _, err := s.database.SqlDB.ExecContext(ctx, "PRAGMA wal_checkpoint(TRUNCATE)"); err != nil {
			slog.Debug("Post-prune WAL checkpoint notice", "err", err)
		}
		// Actively scavenge heap arenas allocated by modernc.org/sqlite back to the host kernel
		runtime.GC()
		debug.FreeOSMemory()
	}

	return total, nil
}

// StartMetricRetentionLoop prunes the raw and bucket metric tables on an hourly tick. A
// first pass runs immediately on start-up so an upgraded install reclaims its
// accumulated history right away instead of waiting an hour.
func (s *TelemetryService) StartMetricRetentionLoop(ctx context.Context, interval time.Duration) {
	if interval <= 0 {
		interval = time.Hour
	}
	go func() {
		prune := func() {
			// The budget is generous on purpose: the first pass after an upgrade can face
			// a backlog of millions of raw rows on slow USB storage, and a pass that dies
			// mid-backlog just retries the same work an hour later. Progress is logged per
			// table so the next field report shows throughput, not just a deadline error.
			pruneCtx, cancel := context.WithTimeout(ctx, 15*time.Minute)
			defer cancel()
			deleted, err := s.PruneRawMetrics(pruneCtx)
			if err != nil {
				slog.Warn("Metric retention pass failed", "err", err, "deleted", deleted)
				return
			}
			if deleted > 0 {
				slog.Info("Pruned raw metric rows past retention", "deleted", deleted)
			}
		}

		prune()
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				prune()
			}
		}
	}()
}

package services

import (
	"context"
	"fmt"
	"log/slog"
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

// pruneRawMetricTable deletes rows older than cutoff, oldest first, in batches. The
// cursor is the primary key, not the timestamp, so each batch is a contiguous range of
// the clustered rowid order (which is also time order for append-only metric tables)
// instead of a fresh index range search per batch. Zero rows deleted stops the loop.
func pruneRawMetricTable(ctx context.Context, database *db.DB, table, cutoff string) (int64, error) {
	var total int64
	for {
		if err := ctx.Err(); err != nil {
			return total, err
		}

		var maxID int64
		// Anchor of one batch: the highest id of the rows below the cutoff. The rowid
		// order matches insertion order, so "id <= anchor" is exactly the oldest rows.
		// COALESCE: an empty tail (no row below the cutoff) yields NULL, which means done.
		err := database.SqlDB.QueryRowContext(ctx,
			fmt.Sprintf("SELECT coalesce(max(id), 0) FROM %s WHERE timestamp < ?", table), cutoff).Scan(&maxID)
		if err != nil {
			return total, fmt.Errorf("select %s prune anchor: %w", table, err)
		}
		if maxID == 0 {
			return total, nil
		}

		// DELETE ... LIMIT is not plain SQLite syntax; the batch is a subselect over the
		// clustered rowid order, which keeps each batch a contiguous oldest-first range.
		res, err := database.SqlDB.ExecContext(ctx, fmt.Sprintf(
			"DELETE FROM %s WHERE id IN (SELECT id FROM %s WHERE id <= ? AND timestamp < ? LIMIT ?)",
			table, table), maxID, cutoff, rawMetricPruneBatch)
		if err != nil {
			return total, fmt.Errorf("delete %s prune batch: %w", table, err)
		}
		deleted, err := res.RowsAffected()
		if err != nil {
			return total, err
		}
		total += deleted

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
		deleted, err := pruneRawMetricTable(ctx, s.database, table, cutoff)
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
			pruneCtx, cancel := context.WithTimeout(ctx, 5*time.Minute)
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

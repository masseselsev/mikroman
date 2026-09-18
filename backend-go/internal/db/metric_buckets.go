package db

import (
	"database/sql"
	"fmt"
	"time"
)

// MetricBucketSeconds is the width of the pre-aggregated metric buckets: 900 s, the
// 24-hour chart's own display bucket. Every longer chart range is a whole multiple of
// this grid (1 h = 4 buckets, 4 h = 16), so a chart bucket always contains complete
// buckets and no aggregate has to be split.
const MetricBucketSeconds = 900

// MetricCollectionCadence models how often the raw tables receive a sample. Buckets are
// refreshed on that cadence, which is what makes a bucket row converge to its final
// value within one sampling interval of the bucket's last instant.
const MetricCollectionCadence = 10 * time.Second

// MetricBucketEpoch floors an instant to the start of its metric bucket in Unix seconds.
// The value is what the raw-sample queries use to derive a bucket (`epoch / 900 * 900`),
// so a bucket written by the collector and a bucket asked for by a chart agree exactly.
func MetricBucketEpoch(t time.Time) int64 {
	sec := t.UTC().Unix()
	return (sec / MetricBucketSeconds) * MetricBucketSeconds
}

// MetricBucketWindow selects the raw samples whose buckets are rebuilt. FromEpoch is
// inclusive and aligned to a bucket start; UntilEpoch is exclusive and 0 means "no upper
// bound". RouterID nil rebuilds every router.
//
// A window that does not start on a bucket boundary would split a bucket: the later
// chunk would upsert an aggregate over its half of the bucket and replace the earlier
// half instead of adding to it. Both callers align, and the backfill relies on it.
type MetricBucketWindow struct {
	RouterID   *int
	FromEpoch  int64
	UntilEpoch int64
}

// Execer is satisfied by *sql.DB and *sql.Tx. Bucket maintenance joins the collector's
// write transaction so a raw sample and the bucket describing it commit together.
type Execer interface {
	Exec(query string, args ...interface{}) (sql.Result, error)
}

// bucketTimeFilter builds the raw-sample predicate of a window: the sample's own bucket
// start is compared, not its raw timestamp, so the grid lives in exactly one place.
//
// The comparison runs on integer epoch seconds rather than text timestamps: SQLite
// applies COLLATE BINARY to bare `timestamp` comparisons, and a stored value whose text
// differs from the datetime()-produced bound by anything (fractional seconds, for
// instance) then falls to the wrong side of the bound — the text form silently changed
// which samples entered the newest bucket, and the chart's bucket/raw equivalence
// drifted by one point at boundary phases.
func bucketTimeFilter(window MetricBucketWindow) (string, []interface{}) {
	// Inclusive lower edge on the bucket grid: any sample at/after FromEpoch has its
	// bucket start at/after FromEpoch.
	//
	// The epoch cast is the exact predicate (see the COLLATE BINARY history above), but
	// an expression on the column is not sargable: alone it forces a full scan of the
	// raw table for every backfill chunk, which on a real router's 30-day history took
	// the one-off backfill past half an hour and blocked startup behind it. The text
	// bounds are lexicographically ordered for the CURRENT_TIMESTAMP format, so they
	// prune through the timestamp index first; the cast then double-checks each
	// surviving row and keeps the semantics identical.
	where := "timestamp >= datetime(?, 'unixepoch') AND cast(strftime('%s', timestamp) as integer) >= ?"
	args := []interface{}{window.FromEpoch, window.FromEpoch}
	if window.UntilEpoch > 0 {
		where += " AND timestamp < datetime(?, 'unixepoch') AND cast(strftime('%s', timestamp) as integer) < ?"
		args = append(args, window.UntilEpoch, window.UntilEpoch)
	}
	return where, args
}

// recomputeSystemBucketsSQL rebuilds every system bucket touched by a window. The upsert
// writes absolute values computed from the raw rows of the bucket, never increments, so
// re-running it any number of times converges on the same row instead of double counting.
// Grouping is done on the derived bucket start, therefore one statement covers as many
// buckets as the window spans.
// temperature_avg and voltage_avg are averages over NON-NULL samples (SQLite avg()
// semantics), and *_nonnull_samples records that denominator. A display bucket
// combining quarter-hour buckets must weight them by the non-NULL counts, not by the
// total sample counts — otherwise a bucket with many NULL readings silently dilutes
// the average it contributes.
const recomputeSystemBucketsSQL = `
INSERT INTO system_metric_buckets (
    router_id, bucket_start, samples, cpu_load_avg, cpu_load_max,
    memory_usage_pct_avg, memory_used_bytes_avg, memory_total_bytes_max,
    temperature_avg, temperature_max, temperature_nonnull_samples,
    voltage_avg, voltage_min, voltage_max, voltage_nonnull_samples,
    last_seen, updated_at
)
SELECT
    router_id,
    datetime((cast(strftime('%%s', timestamp) as integer) / %d) * %d, 'unixepoch') AS bstart,
    count(*),
    avg(cpu_load),
    max(cpu_load),
    avg(memory_usage_pct),
    avg(memory_used_bytes),
    max(memory_total_bytes),
    avg(temperature),
    max(temperature),
    count(temperature),
    avg(voltage),
    min(voltage),
    max(voltage),
    count(voltage),
    max(timestamp),
    CURRENT_TIMESTAMP
FROM system_metrics
WHERE router_id IS NOT NULL AND %s
GROUP BY router_id, bstart
ON CONFLICT(router_id, bucket_start) DO UPDATE SET
    samples = excluded.samples,
    cpu_load_avg = excluded.cpu_load_avg,
    cpu_load_max = excluded.cpu_load_max,
    memory_usage_pct_avg = excluded.memory_usage_pct_avg,
    memory_used_bytes_avg = excluded.memory_used_bytes_avg,
    memory_total_bytes_max = excluded.memory_total_bytes_max,
    temperature_avg = excluded.temperature_avg,
    temperature_max = excluded.temperature_max,
    temperature_nonnull_samples = excluded.temperature_nonnull_samples,
    voltage_avg = excluded.voltage_avg,
    voltage_min = excluded.voltage_min,
    voltage_max = excluded.voltage_max,
    voltage_nonnull_samples = excluded.voltage_nonnull_samples,
    last_seen = excluded.last_seen,
    updated_at = CURRENT_TIMESTAMP
`

// recomputeInterfaceBucketsSQL rebuilds every interface bucket touched by a window, one
// row per interface present in the window: the number of sample instants, the summed
// rate and the per-interface rate peak.
//
// What is deliberately NOT stored: the peak of the per-instant SUM across interfaces.
// For a chart selecting a proper subset of the router's interfaces that value is not
// reconstructable from per-interface aggregates at all — summing per-interface peaks
// invents a simultaneous spike, the maximum of them understates a genuine one — and the
// collector samples every interface, so a selection is almost always a proper subset.
// The read side therefore recombines averages exactly (the summed rate of a display
// bucket is the sum of the per-interface sums; the number of instants is the maximum of
// per-interface sample counts within each quarter hour, since one tick samples every
// interface at the same instant) and reports the subset peak as the maximum of the
// selected interfaces' own peaks: exact for a single interface, a strict lower bound for
// a subset — it can understate a combined spike but never manufacture one.
const recomputeInterfaceBucketsSQL = `
INSERT INTO interface_metric_buckets (
    router_id, interface_name, bucket_start, samples,
    rx_rate_bps_sum, rx_rate_bps_max, tx_rate_bps_sum, tx_rate_bps_max,
    last_seen, updated_at
)
SELECT
    router_id,
    interface_name,
    datetime((cast(strftime('%%s', timestamp) as integer) / %d) * %d, 'unixepoch') AS bstart,
    count(*),
    sum(rx_rate_bps),
    max(rx_rate_bps),
    sum(tx_rate_bps),
    max(tx_rate_bps),
    max(timestamp),
    CURRENT_TIMESTAMP
FROM interface_metrics
WHERE router_id IS NOT NULL AND %s
GROUP BY router_id, interface_name, bstart
ON CONFLICT(router_id, interface_name, bucket_start) DO UPDATE SET
    samples = excluded.samples,
    rx_rate_bps_sum = excluded.rx_rate_bps_sum,
    rx_rate_bps_max = excluded.rx_rate_bps_max,
    tx_rate_bps_sum = excluded.tx_rate_bps_sum,
    tx_rate_bps_max = excluded.tx_rate_bps_max,
    last_seen = excluded.last_seen,
    updated_at = CURRENT_TIMESTAMP
`

// RecomputeSystemMetricBuckets rebuilds the system buckets covering a window of raw
// samples. The collector calls it for the current bucket on every sample tick; the
// backfill calls it once per day of retained history.
func RecomputeSystemMetricBuckets(ex Execer, window MetricBucketWindow) error {
	where, timeArgs := bucketTimeFilter(window)
	// The raw-sample time predicates come first in the WHERE clause, so the router
	// filter's placeholder is appended after them: the arg order must match the SQL.
	args := append([]interface{}{}, timeArgs...)
	if window.RouterID != nil {
		where += " AND router_id = ?"
		args = append(args, *window.RouterID)
	}

	query := fmt.Sprintf(recomputeSystemBucketsSQL, MetricBucketSeconds, MetricBucketSeconds, where)
	_, err := ex.Exec(query, args...)
	return err
}

// RecomputeInterfaceMetricBuckets rebuilds the interface buckets covering a window of
// raw samples, one row per interface present in the window.
func RecomputeInterfaceMetricBuckets(ex Execer, window MetricBucketWindow) error {
	where, timeArgs := bucketTimeFilter(window)
	// Same placeholder order as the system recompute: time predicates first, router last.
	args := append([]interface{}{}, timeArgs...)
	if window.RouterID != nil {
		where += " AND router_id = ?"
		args = append(args, *window.RouterID)
	}

	// Verb order follows the SQL text: the two %d of the bucket formula come before the
	// %s window predicate of the WHERE clause.
	s := MetricBucketSeconds
	query := fmt.Sprintf(recomputeInterfaceBucketsSQL, s, s, where)
	_, err := ex.Exec(query, args...)
	return err
}

// ComposeMetricBuckets rebuilds all bucket-side tables for the same window in one call,
// so the tables cannot disagree about which buckets exist.
func ComposeMetricBuckets(ex Execer, window MetricBucketWindow) error {
	if err := RecomputeSystemMetricBuckets(ex, window); err != nil {
		return err
	}
	return RecomputeInterfaceMetricBuckets(ex, window)
}

// MetricBucketBackfillMarker is the app_settings key that records that the one-off
// backfill of existing raw samples has already run.
const MetricBucketBackfillMarker = "metric_buckets_backfilled"

// MetricBucketBackfillVersion is bumped when the bucket layout changes in a way that
// needs existing buckets rebuilt from the raw rows again. The bucket feature has not
// shipped in a release yet, so "1" is the only version that will ever have been stored.
const MetricBucketBackfillVersion = "1"

// backfillChunkSeconds is the width of one backfill chunk: an hour, a whole multiple of
// the bucket grid. Chunking bounds the size of a single statement (and of SQLite's
// temporary sort) so an install with months of history does not build one enormous
// aggregate, and it lets an interrupted backfill resume without having written partial
// buckets. The size is a lock-contention budget, not just a memory one: each chunk is a
// write transaction, and the collector's 10 s tick needs the write lock too. A day-sized
// chunk held it past the 5 s busy_timeout on router-class storage, so every tick that
// landed during a chunk rolled back and its raw samples were lost (SQLITE_BUSY); an
// hour-sized chunk plus a pause between chunks keeps each lock window short enough for
// the tick to slip in.
const backfillChunkSeconds = 60 * 60

// backfillChunkPause is how long the backfill yields the database between chunks. It
// does not have to be long — the collector ticks every 10 s — but zero pause lets one
// connection re-acquire the write lock immediately and starve everyone else. (Kept a
// var: tests shrink it, as with rawMetricPruneBatch.)
var backfillChunkPause = 200 * time.Millisecond

// BackfillMetricBuckets builds metric buckets from the raw samples already in the
// database, so charts are populated the moment an existing install is upgraded instead of
// only from the next sample onward. It reads the retention window and works backwards
// from the newest day, because the recent history is what the charts ask for first.
//
// It runs once: a marker in app_settings records completion and later starts skip the
// scan. It is also idempotent — every chunk upserts absolute aggregates over bucket
// aligned windows — so re-running it (or crashing halfway and starting again) converges
// on the same rows. Returns whether the backfill actually ran.
func BackfillMetricBuckets(database *DB, retentionDays int) (bool, error) {
	if database == nil || database.SqlDB == nil {
		return false, nil
	}
	if done, err := database.GetSetting(MetricBucketBackfillMarker); err == nil && done == MetricBucketBackfillVersion {
		return false, nil
	}

	// Nothing to backfill: a fresh install has no raw samples and will build buckets as
	// it collects. Marking it done keeps the check out of every later start.
	var rawRows int64
	if err := database.SqlDB.QueryRow("SELECT count(*) FROM system_metrics").Scan(&rawRows); err != nil {
		return false, err
	}
	if rawRows == 0 {
		_ = database.SetSetting(MetricBucketBackfillMarker, MetricBucketBackfillVersion,
			"One-off backfill of metric buckets from raw samples has run")
		return false, nil
	}

	now := time.Now().UTC()
	newest := MetricBucketEpoch(now)
	if retentionDays < 1 {
		retentionDays = 1
	}
	oldest := MetricBucketEpoch(now.AddDate(0, 0, -retentionDays))

	for chunkEnd := newest + MetricBucketSeconds; chunkEnd > oldest; chunkEnd -= backfillChunkSeconds {
		chunkStart := chunkEnd - backfillChunkSeconds
		if chunkStart < oldest {
			chunkStart = oldest
		}
		// Both bounds sit on the bucket grid, so a bucket is never split across chunks.
		if err := ComposeMetricBuckets(database.SqlDB, MetricBucketWindow{
			FromEpoch:  chunkStart,
			UntilEpoch: chunkEnd,
		}); err != nil {
			return false, fmt.Errorf("backfill metric buckets %d..%d: %w", chunkStart, chunkEnd, err)
		}
		// Yield between chunks so the collector's tick can take the write lock; see
		// backfillChunkPause.
		time.Sleep(backfillChunkPause)
	}

	if err := database.SetSetting(MetricBucketBackfillMarker, MetricBucketBackfillVersion,
		"One-off backfill of metric buckets from raw samples has run"); err != nil {
		return true, err
	}
	return true, nil
}

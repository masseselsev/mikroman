package db

import (
	"path/filepath"
	"testing"
	"time"

	"github.com/masseselsev/mikroman/internal/crypto"
)

// openBucketTestDB creates an in-temp database with the full schema applied, a single
// router, and the raw metric rows a test needs.
func openBucketTestDB(t *testing.T) *DB {
	t.Helper()
	fernet, err := crypto.NewFernet("cw_z4pYJ2-8_9V18R5v6R1XbJ9i9w9G1R1XbJ9i9w9E=")
	if err != nil {
		t.Fatalf("failed to create Fernet: %v", err)
	}
	database, err := Open(filepath.Join(t.TempDir(), "buckets.db"), fernet)
	if err != nil {
		t.Fatalf("failed to open database: %v", err)
	}
	if _, err := database.SqlDB.Exec(
		"INSERT INTO routers (id, name, host, is_default, is_active) VALUES (1, 'Test-Gateway', '192.0.2.1', 1, 1)"); err != nil {
		t.Fatalf("failed to seed router: %v", err)
	}
	return database
}

// insertSystemRaw inserts one raw system sample at a fixed instant.
func insertSystemRaw(t *testing.T, database *DB, at time.Time, cpu, memPct float64, memUsed, memTotal int64, temp, volt *float64) {
	t.Helper()
	var tempArg, voltArg interface{}
	if temp != nil {
		tempArg = *temp
	}
	if volt != nil {
		voltArg = *volt
	}
	_, err := database.SqlDB.Exec(`
		INSERT INTO system_metrics (router_id, cpu_load, memory_used_bytes, memory_total_bytes, memory_usage_pct, temperature, voltage, timestamp)
		VALUES (1, ?, ?, ?, ?, ?, ?, ?)
	`, cpu, memUsed, memTotal, memPct, tempArg, voltArg, at.UTC().Format("2006-01-02 15:04:05"))
	if err != nil {
		t.Fatalf("failed to insert raw system sample: %v", err)
	}
}

// insertInterfaceRaw inserts one raw interface sample at a fixed instant.
func insertInterfaceRaw(t *testing.T, database *DB, at time.Time, name string, rx, tx float64) {
	t.Helper()
	_, err := database.SqlDB.Exec(`
		INSERT INTO interface_metrics (router_id, interface_name, rx_rate_bps, tx_rate_bps, rx_bytes_total, tx_bytes_total, timestamp)
		VALUES (1, ?, ?, ?, 0, 0, ?)
	`, name, rx, tx, at.UTC().Format("2006-01-02 15:04:05"))
	if err != nil {
		t.Fatalf("failed to insert raw interface sample: %v", err)
	}
}

// bucketRow reads one system bucket row for a router keyed by its bucket-start epoch.
func bucketRow(t *testing.T, database *DB, table string, epoch int64) map[string]interface{} {
	t.Helper()
	// bucket_start is stored as a UTC string; compare via its epoch.
	query := "SELECT * FROM " + table + " WHERE router_id = 1 AND cast(strftime('%s', bucket_start) as integer) = ?"
	rows, err := database.SqlDB.Query(query, epoch)
	if err != nil {
		t.Fatalf("failed to query %s: %v", table, err)
	}
	defer rows.Close()

	cols, _ := rows.Columns()
	if !rows.Next() {
		return nil
	}
	vals := make([]interface{}, len(cols))
	ptrs := make([]interface{}, len(cols))
	for i := range vals {
		ptrs[i] = &vals[i]
	}
	if err := rows.Scan(ptrs...); err != nil {
		t.Fatalf("failed to scan %s row: %v", table, err)
	}
	out := make(map[string]interface{}, len(cols))
	for i, c := range cols {
		v := vals[i]
		if b, ok := v.([]byte); ok {
			v = string(b)
		}
		out[c] = v
	}
	return out
}

// TestMetricBucketAggregationFromRawRows seeds a handful of raw samples across two
// 15-minute buckets and one interface, then recomputes the buckets and checks every
// aggregate the charts need: sample counts, means, maxima, minima and the NULL handling
// of temperature/voltage.
func TestMetricBucketAggregationFromRawRows(t *testing.T) {
	database := openBucketTestDB(t)
	defer database.Close()

	base := time.Date(2026, 9, 18, 12, 0, 0, 0, time.UTC) // 12:00 exactly, bucket-aligned

	// Bucket A (12:00-12:15): three samples, one with NULL temperature/voltage.
	insertSystemRaw(t, database, base.Add(0*time.Second), 10, 50, 100, 256, ptrOf(40.0), ptrOf(24.0))
	insertSystemRaw(t, database, base.Add(300*time.Second), 20, 60, 200, 256, ptrOf(42.0), ptrOf(24.5))
	insertSystemRaw(t, database, base.Add(600*time.Second), 40, 70, 300, 512, nil, nil)

	// Bucket B (12:15-12:30): one sample.
	insertSystemRaw(t, database, base.Add(900*time.Second), 5, 40, 50, 512, ptrOf(38.0), ptrOf(23.5))

	// Interface rows for the same two buckets, two interfaces.
	insertInterfaceRaw(t, database, base.Add(0*time.Second), "ether1", 100, 10)
	insertInterfaceRaw(t, database, base.Add(300*time.Second), "ether1", 200, 20)
	insertInterfaceRaw(t, database, base.Add(0*time.Second), "ether2", 500, 50)
	insertInterfaceRaw(t, database, base.Add(900*time.Second), "ether1", 300, 30)

	err := ComposeMetricBuckets(database.SqlDB, MetricBucketWindow{
		RouterID:   intPtr(1),
		FromEpoch:  MetricBucketEpoch(base),
		UntilEpoch: MetricBucketEpoch(base) + 2*MetricBucketSeconds,
	})
	if err != nil {
		t.Fatalf("ComposeMetricBuckets failed: %v", err)
	}

	bucketA := bucketRow(t, database, "system_metric_buckets", base.Unix())
	if bucketA == nil {
		t.Fatal("expected a system bucket for 12:00")
	}
	if got := bucketA["samples"].(int64); got != 3 {
		t.Fatalf("bucket A samples: got %d, want 3", got)
	}
	// avg(10, 20, 40) = 70/3
	if got := bucketA["cpu_load_avg"].(float64); got != 70.0/3 {
		t.Fatalf("bucket A cpu_load_avg: got %v, want %v", got, 70.0/3)
	}
	if got := bucketA["cpu_load_max"].(float64); got != 40 {
		t.Fatalf("bucket A cpu_load_max: got %v, want 40", got)
	}
	if got := bucketA["memory_usage_pct_avg"].(float64); got != 60 {
		t.Fatalf("bucket A memory_usage_pct_avg: got %v, want 60", got)
	}
	if got := bucketA["memory_used_bytes_avg"].(float64); got != 200 {
		t.Fatalf("bucket A memory_used_bytes_avg: got %v, want 200", got)
	}
	if got := bucketA["memory_total_bytes_max"].(int64); got != 512 {
		t.Fatalf("bucket A memory_total_bytes_max: got %v, want 512", got)
	}
	// avg(40, 42, NULL) = 41 over the two non-NULL samples.
	if got := bucketA["temperature_avg"].(float64); got != 41 {
		t.Fatalf("bucket A temperature_avg: got %v, want 41", got)
	}
	if got := bucketA["temperature_max"].(float64); got != 42 {
		t.Fatalf("bucket A temperature_max: got %v, want 42", got)
	}
	if got := bucketA["voltage_avg"].(float64); got != 24.25 {
		t.Fatalf("bucket A voltage_avg: got %v, want 24.25", got)
	}
	if got := bucketA["voltage_min"].(float64); got != 24 {
		t.Fatalf("bucket A voltage_min: got %v, want 24", got)
	}
	if got := bucketA["voltage_max"].(float64); got != 24.5 {
		t.Fatalf("bucket A voltage_max: got %v, want 24.5", got)
	}

	bucketB := bucketRow(t, database, "system_metric_buckets", base.Add(15*time.Minute).Unix())
	if bucketB == nil {
		t.Fatal("expected a system bucket for 12:15")
	}
	if got := bucketB["samples"].(int64); got != 1 {
		t.Fatalf("bucket B samples: got %d, want 1", got)
	}

	// Interface buckets: one row per (router, interface, bucket).
	var count int
	if err := database.SqlDB.QueryRow("SELECT count(*) FROM interface_metric_buckets").Scan(&count); err != nil {
		t.Fatalf("failed to count interface buckets: %v", err)
	}
	if count != 3 { // ether1 x 2 buckets + ether2 x 1 bucket
		t.Fatalf("interface bucket rows: got %d, want 3", count)
	}

	ibA1 := bucketRow(t, database, "interface_metric_buckets", base.Unix())
	if ibA1 == nil {
		t.Fatal("expected an ether1 bucket for 12:00")
	}
	// The map is keyed only by bucket_start + router, so fetch ether1 explicitly here.
	// The bucket row is the per-interface aggregate; there is no avg column and no
	// combined-sum peak — see the schema comment on interface_metric_buckets.
	var samples int64
	var rxSum, rxMax, txSum, txMax float64
	if err := database.SqlDB.QueryRow(`
		SELECT samples, rx_rate_bps_sum, rx_rate_bps_max, tx_rate_bps_sum, tx_rate_bps_max
		FROM interface_metric_buckets
		WHERE router_id = 1 AND interface_name = 'ether1' AND bucket_start = ?
	`, base.Format("2006-01-02 15:04:05")).Scan(&samples, &rxSum, &rxMax, &txSum, &txMax); err != nil {
		t.Fatalf("failed to read ether1 bucket A: %v", err)
	}
	if samples != 2 {
		t.Fatalf("ether1 bucket A samples: got %d, want 2", samples)
	}
	if rxSum != 300 || rxMax != 200 {
		t.Fatalf("ether1 bucket A rx sum/max: got %v/%v, want 300/200", rxSum, rxMax)
	}
	if txSum != 30 || txMax != 20 {
		t.Fatalf("ether1 bucket A tx sum/max: got %v/%v, want 30/20", txSum, txMax)
	}
	_ = ibA1
}

// TestMetricBucketUpsertIdempotency recomputes the same window twice (as a restart or a
// re-run of the collector tick would) and requires the aggregates not to double count:
// a sum upserted twice must stay a sum, not become a sum of sums.
func TestMetricBucketUpsertIdempotency(t *testing.T) {
	database := openBucketTestDB(t)
	defer database.Close()

	base := time.Date(2026, 9, 18, 12, 0, 0, 0, time.UTC)
	for i := 0; i < 5; i++ {
		insertSystemRaw(t, database, base.Add(time.Duration(i)*20*time.Second), float64(i), float64(i*10), int64(i*100), 256, nil, nil)
		insertInterfaceRaw(t, database, base.Add(time.Duration(i)*20*time.Second), "ether1", float64(i*100), float64(i*10))
	}

	window := MetricBucketWindow{
		RouterID:   intPtr(1),
		FromEpoch:  MetricBucketEpoch(base),
		UntilEpoch: MetricBucketEpoch(base) + MetricBucketSeconds,
	}
	for run := 1; run <= 3; run++ {
		if err := ComposeMetricBuckets(database.SqlDB, window); err != nil {
			t.Fatalf("ComposeMetricBuckets run %d failed: %v", run, err)
		}
	}

	var sysSamples int64
	var cpuAvg float64
	if err := database.SqlDB.QueryRow(
		"SELECT samples, cpu_load_avg FROM system_metric_buckets WHERE router_id = 1").Scan(&sysSamples, &cpuAvg); err != nil {
		t.Fatalf("failed to read system bucket: %v", err)
	}
	if sysSamples != 5 {
		t.Fatalf("system bucket samples after 3 runs: got %d, want 5 (double counted?)", sysSamples)
	}
	if cpuAvg != 2 {
		t.Fatalf("system bucket cpu_load_avg after 3 runs: got %v, want 2 (double counted?)", cpuAvg)
	}

	var iSamples int64
	var rxSum float64
	if err := database.SqlDB.QueryRow(
		"SELECT samples, rx_rate_bps_sum FROM interface_metric_buckets WHERE router_id = 1 AND interface_name = 'ether1'").Scan(&iSamples, &rxSum); err != nil {
		t.Fatalf("failed to read interface bucket: %v", err)
	}
	if iSamples != 5 {
		t.Fatalf("interface bucket samples after 3 runs: got %d, want 5 (double counted?)", iSamples)
	}
	// 0+100+200+300+400 = 1000; a non-idempotent upsert would triple it.
	if rxSum != 1000 {
		t.Fatalf("interface bucket rx sum after 3 runs: got %v, want 1000 (double counted?)", rxSum)
	}
}

// TestMetricBucketBackfillRunsOnceAndIsIdempotent checks the one-shot marker in
// app_settings and that a re-run leaves the buckets byte-identical.
func TestMetricBucketBackfillRunsOnceAndIsIdempotent(t *testing.T) {
	database := openBucketTestDB(t)
	defer database.Close()

	base := time.Date(2026, 9, 18, 12, 0, 0, 0, time.UTC)
	for i := 0; i < 4; i++ {
		insertSystemRaw(t, database, base.Add(time.Duration(i)*10*time.Second), float64(i+1), 50, 100, 256, nil, nil)
		insertInterfaceRaw(t, database, base.Add(time.Duration(i)*10*time.Second), "ether1", 10, 20)
	}

	ran, err := BackfillMetricBuckets(database, 30)
	if err != nil {
		t.Fatalf("first backfill failed: %v", err)
	}
	if !ran {
		t.Fatal("first backfill should have run on a database with raw samples")
	}

	var marker string
	if err := database.SqlDB.QueryRow(
		"SELECT value FROM app_settings WHERE key = ?", MetricBucketBackfillMarker).Scan(&marker); err != nil {
		t.Fatalf("failed to read backfill marker: %v", err)
	}
	if marker != MetricBucketBackfillVersion {
		t.Fatalf("backfill marker: got %q, want %q", marker, MetricBucketBackfillVersion)
	}

	// Snapshot the buckets, then force a re-run by clearing the marker: rows must not change.
	snapshot := func() (sys, iface string) {
		_ = database.SqlDB.QueryRow(
			"SELECT group_concat(samples || ':' || cpu_load_avg || ':' || rx_rate_bps_sum) FROM (SELECT samples, cpu_load_avg, 0 AS rx_rate_bps_sum FROM system_metric_buckets ORDER BY id)").Scan(&sys)
		_ = database.SqlDB.QueryRow(
			"SELECT group_concat(samples || ':' || rx_rate_bps_sum) FROM (SELECT samples, rx_rate_bps_sum FROM interface_metric_buckets ORDER BY id)").Scan(&iface)
		return
	}
	beforeSys, beforeIface := snapshot()

	if _, err := database.SqlDB.Exec(
		"DELETE FROM app_settings WHERE key = ?", MetricBucketBackfillMarker); err != nil {
		t.Fatalf("failed to reset marker: %v", err)
	}
	ranAgain, err := BackfillMetricBuckets(database, 30)
	if err != nil {
		t.Fatalf("second backfill failed: %v", err)
	}
	if !ranAgain {
		t.Fatal("second backfill (marker cleared) should have run again")
	}

	afterSys, afterIface := snapshot()
	if beforeSys != afterSys || beforeIface != afterIface {
		t.Fatalf("backfill re-run changed bucket rows:\n system before=%q after=%q\n iface before=%q after=%q", beforeSys, afterSys, beforeIface, afterIface)
	}

	// With the marker present the backfill must skip the scan entirely.
	skipped, err := BackfillMetricBuckets(database, 30)
	if err != nil || skipped {
		t.Fatalf("third backfill should be skipped once marked: got ran=%v err=%v", skipped, err)
	}
}

// TestMetricBucketBackfillEmptyDatabase checks a fresh install: no raw rows, backfill
// marks itself done and writes no buckets.
func TestMetricBucketBackfillEmptyDatabase(t *testing.T) {
	database := openBucketTestDB(t)
	defer database.Close()

	ran, err := BackfillMetricBuckets(database, 30)
	if err != nil {
		t.Fatalf("backfill on empty database failed: %v", err)
	}
	if ran {
		t.Fatal("backfill on an empty database should report that it did not run")
	}

	var sysCount, ifaceCount int
	_ = database.SqlDB.QueryRow("SELECT count(*) FROM system_metric_buckets").Scan(&sysCount)
	_ = database.SqlDB.QueryRow("SELECT count(*) FROM interface_metric_buckets").Scan(&ifaceCount)
	if sysCount != 0 || ifaceCount != 0 {
		t.Fatalf("empty database must not gain buckets: system=%d interface=%d", sysCount, ifaceCount)
	}
}

func ptrOf(v float64) *float64 { return &v }
func intPtr(v int) *int        { return &v }

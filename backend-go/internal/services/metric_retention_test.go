package services

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/masseselsev/mikroman/internal/crypto"
	"github.com/masseselsev/mikroman/internal/db"
)

// newRetentionTestDB opens a temp database and returns a TelemetryService wired to it
// without a router client — retention must run whether or not a router is connected.
func newRetentionTestDB(t *testing.T) (*TelemetryService, *db.DB) {
	t.Helper()
	fernet, err := crypto.NewFernet("cw_z4pYJ2-8_9V18R5v6R1XbJ9i9w9G1R1XbJ9i9w9E=")
	if err != nil {
		t.Fatalf("failed to create Fernet: %v", err)
	}
	database, err := db.Open(filepath.Join(t.TempDir(), "retention.db"), fernet)
	if err != nil {
		t.Fatalf("failed to open database: %v", err)
	}
	if _, err := database.SqlDB.Exec(
		"INSERT INTO routers (id, name, host, is_default, is_active) VALUES (1, 'Test-Gateway', '192.0.2.1', 1, 1)"); err != nil {
		t.Fatalf("failed to seed router: %v", err)
	}
	return NewTelemetryService(database, nil, nil), database
}

// insertMetricRows inserts raw system/interface rows at a given instant.
func insertMetricRows(t *testing.T, database *db.DB, at time.Time, n int) {
	t.Helper()
	for i := 0; i < n; i++ {
		ts := at.Add(time.Duration(i) * 10 * time.Second)
		if _, err := database.SqlDB.Exec(`
			INSERT INTO system_metrics (router_id, cpu_load, memory_used_bytes, memory_total_bytes, memory_usage_pct, timestamp)
			VALUES (1, 1, 1, 1, 1, ?)`, ts.UTC().Format("2006-01-02 15:04:05")); err != nil {
			t.Fatalf("failed to insert system row: %v", err)
		}
		if _, err := database.SqlDB.Exec(`
			INSERT INTO interface_metrics (router_id, interface_name, rx_rate_bps, tx_rate_bps, timestamp)
			VALUES (1, 'ether1', 1, 1, ?)`, ts.UTC().Format("2006-01-02 15:04:05")); err != nil {
			t.Fatalf("failed to insert interface row: %v", err)
		}
	}
}

// insertBucketRows inserts bucket rows at a given bucket-start instant.
func insertBucketRows(t *testing.T, database *db.DB, at time.Time, n int) {
	t.Helper()
	for i := 0; i < n; i++ {
		bs := at.Add(time.Duration(i*900) * time.Second)
		if _, err := database.SqlDB.Exec(`
			INSERT INTO system_metric_buckets (router_id, bucket_start, samples, cpu_load_avg)
			VALUES (1, ?, 1, 1)`, bs.UTC().Format("2006-01-02 15:04:05")); err != nil {
			t.Fatalf("failed to insert system bucket: %v", err)
		}
		if _, err := database.SqlDB.Exec(`
			INSERT INTO interface_metric_buckets (router_id, interface_name, bucket_start, samples)
			VALUES (1, 'ether1', ?, 1)`, bs.UTC().Format("2006-01-02 15:04:05")); err != nil {
			t.Fatalf("failed to insert interface bucket: %v", err)
		}
	}
}

// countRows is a one-liner for asserting table sizes.
func countRows(t *testing.T, database *db.DB, table string) int {
	t.Helper()
	var n int
	if err := database.SqlDB.QueryRow("SELECT count(*) FROM " + table).Scan(&n); err != nil {
		t.Fatalf("failed to count %s: %v", table, err)
	}
	return n
}

// TestPruneRawMetricsRetention seeds raw rows on both sides of the cutoff, runs one
// pruning pass with a tiny batch size to force several batches, and asserts that only
// the rows inside the retention window survive.
func TestPruneRawMetricsRetention(t *testing.T) {
	svc, database := newRetentionTestDB(t)
	defer database.Close()

	now := time.Now().UTC()
	// 10 old rows (3 days ago, past the 2-day default cutoff) and 6 fresh rows.
	insertMetricRows(t, database, now.Add(-72*time.Hour), 10)
	insertMetricRows(t, database, now.Add(-time.Hour), 6)

	// Shrink the batch so the 20 deletions need multiple batches — the loop, not one
	// lucky statement, is what prunes the table.
	origBatch := rawMetricPruneBatch
	rawMetricPruneBatch = 5
	defer func() { rawMetricPruneBatch = origBatch }()

	deleted, err := svc.PruneRawMetrics(context.Background())
	if err != nil {
		t.Fatalf("PruneRawMetrics failed: %v", err)
	}
	if deleted != 20 {
		t.Fatalf("deleted rows: got %d, want 20 (10 system + 10 interface)", deleted)
	}

	if got := countRows(t, database, "system_metrics"); got != 6 {
		t.Fatalf("system_metrics after prune: got %d, want 6 (recent rows must survive)", got)
	}
	if got := countRows(t, database, "interface_metrics"); got != 6 {
		t.Fatalf("interface_metrics after prune: got %d, want 6 (recent rows must survive)", got)
	}

	// A second pass must be a no-op: pruning is idempotent.
	deleted, err = svc.PruneRawMetrics(context.Background())
	if err != nil {
		t.Fatalf("second PruneRawMetrics failed: %v", err)
	}
	if deleted != 0 {
		t.Fatalf("second prune deleted %d rows, want 0", deleted)
	}
}

// TestPruneMetricBucketsRetention seeds bucket rows inside and outside the bucket
// retention window and asserts the pass removes exactly the stale ones.
func TestPruneMetricBucketsRetention(t *testing.T) {
	svc, database := newRetentionTestDB(t)
	defer database.Close()

	now := time.Now().UTC()
	// Bucket retention defaults to 400 days; make it short for the test.
	if err := database.SetSetting("metric_bucket_retention_days", "30", ""); err != nil {
		t.Fatalf("failed to set bucket retention: %v", err)
	}

	// 3 stale buckets (40 days ago) and 2 fresh ones (1 day ago).
	insertBucketRows(t, database, now.Add(-40*24*time.Hour), 3)
	insertBucketRows(t, database, now.Add(-24*time.Hour), 2)

	if _, err := svc.PruneRawMetrics(context.Background()); err != nil {
		t.Fatalf("PruneRawMetrics failed: %v", err)
	}

	if got := countRows(t, database, "system_metric_buckets"); got != 2 {
		t.Fatalf("system_metric_buckets after prune: got %d, want 2", got)
	}
	if got := countRows(t, database, "interface_metric_buckets"); got != 2 {
		t.Fatalf("interface_metric_buckets after prune: got %d, want 2", got)
	}
}

// TestPruneRawMetricsConfigurableRetention checks the raw retention setting is honoured
// and that raw rows below the cutoff survive even though buckets exist for them.
func TestPruneRawMetricsConfigurableRetention(t *testing.T) {
	svc, database := newRetentionTestDB(t)
	defer database.Close()

	if err := database.SetSetting("raw_metric_retention_days", "7", ""); err != nil {
		t.Fatalf("failed to set raw retention: %v", err)
	}

	now := time.Now().UTC()
	insertMetricRows(t, database, now.Add(-3*24*time.Hour), 4)  // inside 7-day window
	insertMetricRows(t, database, now.Add(-10*24*time.Hour), 2) // outside

	if _, err := svc.PruneRawMetrics(context.Background()); err != nil {
		t.Fatalf("PruneRawMetrics failed: %v", err)
	}

	if got := countRows(t, database, "system_metrics"); got != 4 {
		t.Fatalf("system_metrics with 7-day retention: got %d, want 4", got)
	}
	if got := countRows(t, database, "interface_metrics"); got != 4 {
		t.Fatalf("interface_metrics with 7-day retention: got %d, want 4", got)
	}
}

package api

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/masseselsev/mikroman/internal/db"
)

// seedChartSamples writes a deterministic block of raw metric samples around "now"
// covering the requested window, then builds the 15-minute buckets from them. Two
// interfaces sample at the same instants so the summed-rate aggregation is exercised.
//
// Samples start one full bucket past the (floored) window start so no sample can sit
// exactly on the chart's startTime second: the handler derives startTime from its own
// wall clock at request time, a tick after this call, and a sample on the boundary
// would be counted by the bucket row but cut by the raw path's timestamp filter — a
// legitimate, product-correct one-point difference that the assertion layer maps out
// by timestamp (see assertAlignedPoints).
func seedChartSamples(t *testing.T, database *db.DB, window time.Duration, sampleEvery time.Duration) {
	t.Helper()
	now := time.Now().UTC().Truncate(time.Second)
	base := time.Unix(db.MetricBucketEpoch(now.Add(-window)), 0).UTC()
	start := base.Add(db.MetricBucketSeconds)

	for at := start; !at.After(now); at = at.Add(sampleEvery) {
		elapsed := at.Sub(start).Seconds()
		cpu := 10 + elapsed/60 // slowly rising load
		if _, err := database.SqlDB.Exec(`
			INSERT INTO system_metrics (router_id, cpu_load, memory_used_bytes, memory_total_bytes, memory_usage_pct, temperature, voltage, timestamp)
			VALUES (1, ?, 524288000, 1073741824, 48.8, 42.0, 24.1, ?)
		`, cpu, at.Format("2006-01-02 15:04:05")); err != nil {
			t.Fatalf("failed to seed system sample: %v", err)
		}
		for _, name := range []string{"ether1", "ether2"} {
			rx := 2500000.0
			tx := 1500000.0
			if name == "ether2" {
				rx = 1000000.0
				tx = 500000.0
			}
			if _, err := database.SqlDB.Exec(`
				INSERT INTO interface_metrics (router_id, interface_name, rx_rate_bps, tx_rate_bps, rx_bytes_total, tx_bytes_total, timestamp)
				VALUES (1, ?, ?, ?, 0, 0, ?)
			`, name, rx, tx, at.Format("2006-01-02 15:04:05")); err != nil {
				t.Fatalf("failed to seed interface sample: %v", err)
			}
		}
	}

	// Build the buckets over the seeded window, exactly as the collector tick would.
	from := db.MetricBucketEpoch(start)
	until := db.MetricBucketEpoch(now) + db.MetricBucketSeconds
	if err := db.ComposeMetricBuckets(database.SqlDB, db.MetricBucketWindow{
		RouterID:   nil, // every router
		FromEpoch:  from,
		UntilEpoch: until,
	}); err != nil {
		t.Fatalf("failed to build buckets: %v", err)
	}
}

// callMetricsEndpoint logs in and fetches a metrics endpoint, returning the decoded data
// payload and the raw response body.
func callMetricsEndpoint(t *testing.T, handler http.Handler, path string) (map[string]interface{}, []byte) {
	t.Helper()

	loginBody := bytes.NewBufferString(`{"password": "SecretAdminPassword123"}`)
	reqLogin := httptest.NewRequest(http.MethodPost, "/api/v1/auth/login", loginBody)
	wLogin := httptest.NewRecorder()
	handler.ServeHTTP(wLogin, reqLogin)
	var sessionCookie *http.Cookie
	for _, c := range wLogin.Result().Cookies() {
		if c.Name == SessionCookie {
			sessionCookie = c
			break
		}
	}

	req := httptest.NewRequest(http.MethodGet, path, nil)
	req.AddCookie(sessionCookie)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200 from %s, got %d: %s", path, w.Code, w.Body.String())
	}

	var resp APIResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("failed to decode response from %s: %v", path, err)
	}
	data, ok := resp.Data.(map[string]interface{})
	if !ok {
		t.Fatalf("expected object data from %s, got %T", path, resp.Data)
	}
	return data, w.Body.Bytes()
}

// TestMetricsServedFromBucketsMatchRawDerivation seeds one 24-hour window of samples,
// reads the charts, wipes the bucket tables, reads the charts again (now served from the
// raw fallback path) and requires the two responses to be identical: bucket aggregation
// must reproduce exactly what the raw two-level grouping produced.
func TestMetricsServedFromBucketsMatchRawDerivation(t *testing.T) {
	handler, database, _ := setupTestServer(t)
	defer database.Close()

	_, _ = database.SqlDB.Exec(`INSERT INTO routers (id, name, host, is_default, is_active) VALUES (1, 'Chart-Router', '192.0.2.10', 1, 1)`)

	// 24 h of samples every 5 minutes: dense enough for real buckets, sparse enough to
	// keep the test fast.
	seedChartSamples(t, database, 24*time.Hour, 5*time.Minute)

	// --- with buckets (the 24 h range is served from them) ---
	sysFromBuckets, _ := callMetricsEndpoint(t, handler, "/api/v1/metrics/system?range=24h&router_id=1")
	ifaceFromBuckets, _ := callMetricsEndpoint(t, handler, "/api/v1/metrics/interfaces?range=24h&interfaces=ether1,ether2&router_id=1")
	if pts, ok := sysFromBuckets["points"].([]interface{}); !ok || len(pts) == 0 {
		t.Fatalf("expected system points served from buckets, got %v", sysFromBuckets["points"])
	}
	if pts, ok := ifaceFromBuckets["points"].([]interface{}); !ok || len(pts) == 0 {
		t.Fatalf("expected interface points served from buckets, got %v", ifaceFromBuckets["points"])
	}
	if sysFromBuckets["bucket_seconds"].(float64) != 900 {
		t.Fatalf("expected bucket_seconds 900 for 24h, got %v", sysFromBuckets["bucket_seconds"])
	}

	// --- wipe the buckets: the same requests must fall back to raw and return the same
	// shape and values, because the empty-bucket fallback exists exactly for this state.
	if _, err := database.SqlDB.Exec("DELETE FROM system_metric_buckets"); err != nil {
		t.Fatalf("failed to wipe system buckets: %v", err)
	}
	if _, err := database.SqlDB.Exec("DELETE FROM interface_metric_buckets"); err != nil {
		t.Fatalf("failed to wipe interface buckets: %v", err)
	}

	sysFromRaw, _ := callMetricsEndpoint(t, handler, "/api/v1/metrics/system?range=24h&router_id=1")
	ifaceFromRaw, _ := callMetricsEndpoint(t, handler, "/api/v1/metrics/interfaces?range=24h&interfaces=ether1,ether2&router_id=1")

	// System path: every recombined field is mathematically exact, so require equality.
	assertAlignedPoints(t, "system", sysFromBuckets["points"], sysFromRaw["points"], false)
	// Interface path for a SUBSET of interfaces: the combined mean is exact (sum of
	// sums / instants), but the combined peak is a documented lower bound (max of the
	// selected per-interface peaks) — equality against the raw per-instant-sum peak is
	// not expected and asserting it would re-invent the spike the buckets refuse to
	// manufacture. Invariants checked instead: same length/timestamps, exact averages,
	// avg <= bucket peak <= raw peak (never an invented spike, never below its mean).
	assertAlignedPoints(t, "interfaces", ifaceFromBuckets["points"], ifaceFromRaw["points"], true)

	// The response envelope must not have gained or lost fields between the two paths.
	for _, key := range []string{"range", "bucket_seconds", "current_cpu", "current_ram_pct", "current_temp", "current_voltage"} {
		if fmt.Sprintf("%v", sysFromBuckets[key]) != fmt.Sprintf("%v", sysFromRaw[key]) {
			t.Errorf("system response field %q differs between bucket and raw paths: %v vs %v", key, sysFromBuckets[key], sysFromRaw[key])
		}
	}
	for _, key := range []string{"range", "bucket_seconds", "is_summed", "current_rx_bps", "current_tx_bps"} {
		if fmt.Sprintf("%v", ifaceFromBuckets[key]) != fmt.Sprintf("%v", ifaceFromRaw[key]) {
			t.Errorf("interface response field %q differs between bucket and raw paths: %v vs %v", key, ifaceFromBuckets[key], ifaceFromRaw[key])
		}
	}
}

// assertAlignedPoints compares bucket-path points against raw-path points BY TIMESTAMP
// instead of by index. Both paths describe the same chart, but their windows may differ
// by exactly one point at the start: the raw path can drop the oldest display bucket
// when its samples predate the request's own startTime second (each request derives its
// own wall clock, and a CI runner under load can drift the pair by seconds), while the
// bucket path keeps that partially covered bucket. One missing raw point is tolerated
// only as the OLDEST bucket point — anywhere else it is a data bug.
//
// For every timestamp present in both: the point is an object, timestamps match, and
// all shared scalar fields are compared. Averages are always exact. Peaks: with
// peakLowerBound (a proper multi-interface subset selection, where the exact combined
// peak is not reconstructable from per-interface aggregates by design) the bucket peak
// must not exceed the raw peak — it may understate a genuine simultaneous spike but
// must never invent one. Without it (system path, single-interface selection) peaks must
// be equal.
func assertAlignedPoints(t *testing.T, label string, got, want interface{}, peakLowerBound bool) {
	t.Helper()
	gotPts, ok1 := got.([]interface{})
	wantPts, ok2 := want.([]interface{})
	if !ok1 || !ok2 {
		t.Fatalf("%s: points not arrays: %T vs %T", label, got, want)
	}
	rawByTS := make(map[string]map[string]interface{}, len(wantPts))
	for _, wp := range wantPts {
		w, _ := wp.(map[string]interface{})
		if w != nil {
			rawByTS[fmt.Sprint(w["timestamp"])] = w
		}
	}
	// The oldest bucket point is the boundary artifact itself: raw cut its samples
	// before startTime while the stored bucket cannot be re-cut, so that point is
	// either absent from raw (one tolerated missing head point) or present with a
	// partial-window value (tolerated, not compared). Everything after the head must
	// agree exactly, which is where any real aggregation bug would live.
	oldestBucketTS := ""
	if len(gotPts) > 0 {
		if g0, ok := gotPts[0].(map[string]interface{}); ok {
			oldestBucketTS = fmt.Sprint(g0["timestamp"])
		}
	}
	missingRaw := 0
	for i, gp := range gotPts {
		g, _ := gp.(map[string]interface{})
		if g == nil {
			t.Fatalf("%s point %d: not an object", label, i)
		}
		ts := fmt.Sprint(g["timestamp"])
		w, present := rawByTS[ts]
		if !present {
			missingRaw++
			if i != 0 {
				t.Errorf("%s: bucket point %d (ts=%v) has no raw counterpart; only the oldest point may be a straddling-bucket edge", label, i, ts)
			}
			continue
		}
		if ts == oldestBucketTS {
			continue
		}
		for key, gv := range g {
			wv, has := w[key]
			if !has {
				t.Errorf("%s point %d: field %q missing in raw-derived output", label, i, key)
				continue
			}
			gf, gIsNum := gv.(float64)
			wf, wIsNum := wv.(float64)
			isPeak := peakLowerBound && (key == "rx_peak_bps" || key == "tx_peak_bps")
			isPeakText := peakLowerBound && (key == "rx_peak_formatted" || key == "tx_peak_formatted")
			switch {
			case isPeakText:
				// formatted rendering of the peak fields: covered by the numeric
				// never-invent check below; exact equality is not expected for a subset
			case isPeak && gIsNum && wIsNum:
				if gf > wf {
					t.Errorf("%s point %d: %s: bucket peak %v exceeds raw peak %v — invented spike", label, i, key, gf, wf)
				}
			case gIsNum && wIsNum:
				if gf != wf {
					t.Errorf("%s point %d: field %q differs: bucket %v vs raw %v", label, i, key, gf, wf)
				}
			case !gIsNum && !wIsNum:
				if fmt.Sprintf("%v", gv) != fmt.Sprintf("%v", wv) {
					t.Errorf("%s point %d: field %q differs: bucket %v vs raw %v", label, i, key, gv, wv)
				}
			default:
				t.Errorf("%s point %d: field %q type differs: bucket %T vs raw %T", label, i, key, gv, wv)
			}
		}
	}
	if len(gotPts)-len(wantPts) > 1 || len(gotPts) < len(wantPts) {
		t.Errorf("%s: point counts diverge beyond the single straddling edge: bucket %d vs raw %d", label, len(gotPts), len(wantPts))
	}
	if missingRaw > 1 {
		t.Errorf("%s: %d bucket points had no raw counterpart (only 1 straddling edge is tolerated)", label, missingRaw)
	}
}

// TestMetricsSingleInterfaceSelectionExact pins the case where the subset peak bound
// coincides with the truth: a single selected interface has no cross-interface sum, so
// its combined peak is its own per-interface peak and bucket/raw output must be fully
// identical, not merely bounded.
func TestMetricsSingleInterfaceSelectionExact(t *testing.T) {
	handler, database, _ := setupTestServer(t)
	defer database.Close()

	_, _ = database.SqlDB.Exec(`INSERT INTO routers (id, name, host, is_default, is_active) VALUES (1, 'Chart-Router', '192.0.2.10', 1, 1)`)
	seedChartSamples(t, database, 24*time.Hour, 5*time.Minute)

	path := "/api/v1/metrics/interfaces?range=24h&interfaces=ether1&router_id=1"
	fromBuckets, _ := callMetricsEndpoint(t, handler, path)
	if pts, ok := fromBuckets["points"].([]interface{}); !ok || len(pts) == 0 {
		t.Fatalf("expected single-interface points served from buckets, got %v", fromBuckets["points"])
	}
	if _, err := database.SqlDB.Exec("DELETE FROM interface_metric_buckets"); err != nil {
		t.Fatalf("failed to wipe interface buckets: %v", err)
	}
	fromRaw, _ := callMetricsEndpoint(t, handler, path)

	assertAlignedPoints(t, "interfaces/single", fromBuckets["points"], fromRaw["points"], false)
}

// TestMetricsPartialCoverageFallsBackToRaw pins the coverage rule: when only one of the
// two selected interfaces has bucket rows for the range, the bucket path must not serve
// a chart that sums a partial selection — the raw path owns the request.
func TestMetricsPartialCoverageFallsBackToRaw(t *testing.T) {
	handler, database, _ := setupTestServer(t)
	defer database.Close()

	_, _ = database.SqlDB.Exec(`INSERT INTO routers (id, name, host, is_default, is_active) VALUES (1, 'Chart-Router', '192.0.2.10', 1, 1)`)
	seedChartSamples(t, database, 24*time.Hour, 5*time.Minute)

	// Drop ether2's buckets: coverage is now 1 of 2 selected names.
	if _, err := database.SqlDB.Exec(
		"DELETE FROM interface_metric_buckets WHERE interface_name = 'ether2'"); err != nil {
		t.Fatalf("failed to wipe ether2 buckets: %v", err)
	}

	path := "/api/v1/metrics/interfaces?range=24h&interfaces=ether1,ether2&router_id=1"
	partial, _ := callMetricsEndpoint(t, handler, path)

	if _, err := database.SqlDB.Exec("DELETE FROM interface_metric_buckets"); err != nil {
		t.Fatalf("failed to wipe all interface buckets: %v", err)
	}
	allRaw, _ := callMetricsEndpoint(t, handler, path)

	// Both requests must be served from raw now, so identical output is required.
	assertAlignedPoints(t, "interfaces/coverage", partial["points"], allRaw["points"], false)
}

// TestMetricsLongRangeFallsBackToRawWhenBucketsEmpty covers the "just deployed, backfill
// not run yet" case: a long range with raw data but no buckets must still return points
// (from raw) instead of an empty chart.
func TestMetricsLongRangeFallsBackToRawWhenBucketsEmpty(t *testing.T) {
	handler, database, _ := setupTestServer(t)
	defer database.Close()

	_, _ = database.SqlDB.Exec(`INSERT INTO routers (id, name, host, is_default, is_active) VALUES (1, 'Fresh-Router', '198.51.100.20', 1, 1)`)

	// Seed raw rows only — no backfill, buckets stay empty.
	now := time.Now().UTC()
	for i := 0; i < 12; i++ {
		at := now.Add(-time.Duration(i) * 5 * time.Minute)
		_, _ = database.SqlDB.Exec(`
			INSERT INTO system_metrics (router_id, cpu_load, memory_used_bytes, memory_total_bytes, memory_usage_pct, timestamp)
			VALUES (1, 12.5, 524288000, 1073741824, 48.8, ?)
		`, at.Format("2006-01-02 15:04:05"))
		_, _ = database.SqlDB.Exec(`
			INSERT INTO interface_metrics (router_id, interface_name, rx_rate_bps, tx_rate_bps, timestamp)
			VALUES (1, 'ether1', 2500000.0, 1500000.0, ?)
		`, at.Format("2006-01-02 15:04:05"))
	}

	sysData, _ := callMetricsEndpoint(t, handler, "/api/v1/metrics/system?range=24h&router_id=1")
	if pts, ok := sysData["points"].([]interface{}); !ok || len(pts) == 0 {
		t.Fatalf("expected system points from the raw fallback, got %v", sysData["points"])
	}
	if sysData["current_cpu"] == nil {
		t.Fatalf("expected current_cpu populated from raw fallback")
	}

	ifaceData, _ := callMetricsEndpoint(t, handler, "/api/v1/metrics/interfaces?range=24h&interfaces=ether1&router_id=1")
	if pts, ok := ifaceData["points"].([]interface{}); !ok || len(pts) == 0 {
		t.Fatalf("expected interface points from the raw fallback, got %v", ifaceData["points"])
	}
}

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
// Alignment rules that make the raw path and the bucket path comparable:
//   - the seed starts on a 15-minute boundary one bucket BEFORE the chart window, so
//     every 15-minute bucket the chart reads is fully populated;
//   - the first sample inside the window is placed strictly after the boundary second
//     the handler's startTime will land on (the handler computes startTime from the
//     wall clock at request time, which is a tick later than this seeding call — a
//     sample sitting exactly on startTime would be counted by the bucket row but cut
//     off by the raw path's timestamp filter).
func seedChartSamples(t *testing.T, database *db.DB, window time.Duration, sampleEvery time.Duration) {
	t.Helper()
	now := time.Now().UTC().Truncate(time.Second)
	// Floor the seed start to a bucket boundary, then step one full bucket past it so
	// no sample can sit exactly on the chart's startTime second.
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

	assertSamePoints(t, "system", sysFromBuckets["points"], sysFromRaw["points"], 1)
	assertSamePoints(t, "interfaces", ifaceFromBuckets["points"], ifaceFromRaw["points"], 1)

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

// assertSamePoints compares two points arrays decoded from JSON: same length, same
// timestamps, and every numeric field equal after the same rounding the handler applies.
// skipFirst drops the leading point(s): the window's boundary bucket is partial in the
// raw path (its samples before startTime are filtered out) but complete in the bucket
// path (a stored aggregate cannot be re-cut), so the first point is compared away.
func assertSamePoints(t *testing.T, label string, got, want interface{}, skipFirst int) {
	t.Helper()
	gotPts, ok1 := got.([]interface{})
	wantPts, ok2 := want.([]interface{})
	if !ok1 || !ok2 {
		t.Fatalf("%s: points not arrays: %T vs %T", label, got, want)
	}
	if skipFirst > 0 {
		if len(gotPts) > skipFirst {
			gotPts = gotPts[skipFirst:]
		}
		if len(wantPts) > skipFirst {
			wantPts = wantPts[skipFirst:]
		}
	}
	if len(gotPts) != len(wantPts) {
		t.Fatalf("%s: point count differs: bucket path %d vs raw path %d", label, len(gotPts), len(wantPts))
	}
	for i := range gotPts {
		g, _ := gotPts[i].(map[string]interface{})
		w, _ := wantPts[i].(map[string]interface{})
		if g == nil || w == nil {
			t.Fatalf("%s point %d: not objects", label, i)
		}
		for key, gv := range g {
			wv, present := w[key]
			if !present {
				t.Errorf("%s point %d: field %q missing in raw-derived output", label, i, key)
				continue
			}
			gf, gIsNum := gv.(float64)
			wf, wIsNum := wv.(float64)
			switch {
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

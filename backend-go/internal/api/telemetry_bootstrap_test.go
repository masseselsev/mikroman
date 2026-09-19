package api

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	"github.com/masseselsev/mikroman/internal/crypto"
	"github.com/masseselsev/mikroman/internal/db"
)

// startHubTestServer serves hub.HandleWS on a local httptest server and
// returns the ws:// URL plus the shutdown func.
func startHubTestServer(t *testing.T, hub *Hub) (string, func()) {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(hub.HandleWS))
	wsURL := "ws" + strings.TrimPrefix(server.URL, "http")
	return wsURL, server.Close
}

func dialWS(t *testing.T, wsURL string, routerID int) *websocket.Conn {
	t.Helper()
	conn, _, err := websocket.DefaultDialer.Dial(fmt.Sprintf("%s?router_id=%d", wsURL, routerID), nil)
	if err != nil {
		t.Fatalf("ws dial: %v", err)
	}
	return conn
}

func readWSJSON(t *testing.T, conn *websocket.Conn) map[string]interface{} {
	t.Helper()
	_ = conn.SetReadDeadline(time.Now().Add(2 * time.Second))
	var msg map[string]interface{}
	if err := conn.ReadJSON(&msg); err != nil {
		t.Fatalf("ws read: %v", err)
	}
	return msg
}

func openBootstrapDB(t *testing.T) *db.DB {
	t.Helper()
	fernet, err := crypto.NewFernet("cw_z4pYJ2-8_9V18R5v6R1XbJ9i9w9G1R1XbJ9i9w9E=")
	if err != nil {
		t.Fatalf("fernet: %v", err)
	}
	database, err := db.Open(filepath.Join(t.TempDir(), "boot.db"), fernet)
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	t.Cleanup(func() { database.Close() })
	return database
}

func seedBootstrapHistory(t *testing.T, database *db.DB) {
	t.Helper()
	// One router with 15-minute buckets over a few hours. cpu_load alternates
	// so the median is a stable, hand-checkable number; ether1+ether2 are the
	// monitored pair, and one late bucket deliberately misses ether2 — it must
	// be dropped by the coverage HAVING, not dilute the WAN median.
	if _, err := database.SqlDB.Exec(`
		INSERT INTO routers (id, name, host, is_default, is_active)
		VALUES (1, 'BootRouter', '127.0.0.1', 1, 1)`); err != nil {
		t.Fatalf("seed router: %v", err)
	}
	sys := []struct {
		start string
		cpu   float64
		pct   float64
		temp  interface{}
	}{
		{"2026-09-19 08:00:00", 10, 40, 44.0},
		{"2026-09-19 08:15:00", 20, 50, 46.0},
		{"2026-09-19 08:30:00", 30, 60, nil},
		{"2026-09-19 08:45:00", 40, 70, 50.0},
		{"2026-09-19 09:00:00", 50, 80, 52.0},
	}
	for _, s := range sys {
		if _, err := database.SqlDB.Exec(`
			INSERT INTO system_metric_buckets (router_id, bucket_start, samples,
				cpu_load_avg, cpu_load_max, memory_usage_pct_avg, memory_used_bytes_avg,
				memory_total_bytes_max, temperature_avg, temperature_max, temperature_nonnull_samples)
			VALUES (1, ?, 60, ?, ?, ?, ?, 1073741824, ?, ?, ?)`,
			s.start, s.cpu, s.cpu, s.pct, s.pct*10485760, s.temp, s.temp, boolToInt(s.temp != nil)); err != nil {
			t.Fatalf("seed system bucket: %v", err)
		}
	}
	iface := []struct{ start, name string; rx, tx float64 }{
		{"2026-09-19 08:00:00", "ether1", 1e6, 5e5},
		{"2026-09-19 08:00:00", "ether2", 2e6, 1e6},
		{"2026-09-19 08:15:00", "ether1", 3e6, 1.5e6},
		{"2026-09-19 08:15:00", "ether2", 4e6, 2e6},
		{"2026-09-19 08:30:00", "ether1", 5e6, 2.5e6},
		{"2026-09-19 08:30:00", "ether2", 6e6, 3e6},
		// Partial bucket: ether2 absent — HAVING must exclude it. Its 9e7 outlier
		// would otherwise drag the rx median from 9e6 to 11e6. Complete buckets
		// have rx sums 3e6, 7e6, 11e6, 17e6 → median 9e6 (tx: 4.5e6).
		{"2026-09-19 08:45:00", "ether1", 9e7, 9e7},
		{"2026-09-19 09:00:00", "ether1", 8e6, 4e6},
		{"2026-09-19 09:00:00", "ether2", 9e6, 5e6},
	}
	for _, f := range iface {
		if _, err := database.SqlDB.Exec(`
			INSERT INTO interface_metric_buckets (router_id, interface_name, bucket_start, samples,
				rx_rate_bps_sum, rx_rate_bps_max, tx_rate_bps_sum, tx_rate_bps_max)
			VALUES (1, ?, ?, 60, ?, ?, ?, ?)`,
			f.name, f.start, f.rx, f.rx, f.tx, f.tx); err != nil {
			t.Fatalf("seed interface bucket: %v", err)
		}
	}
	if err := database.SetSetting("monitored_interfaces_1", `["ether1","ether2"]`, ""); err != nil {
		t.Fatalf("seed monitored: %v", err)
	}
	if _, err := database.SqlDB.Exec(`
		INSERT INTO users (id, name, router_id, avatar_icon, speed_limit, is_paused, priority, sort_order)
		VALUES (1, 'Alice', 1, 'a', '0', 0, 0, 0), (2, 'Bob', 1, 'b', '0', 0, 0, 1)`); err != nil {
		t.Fatalf("seed users: %v", err)
	}
	if _, err := database.SqlDB.Exec(`
		INSERT INTO devices (id, user_id, router_id, mac_address, is_active, is_hidden, is_deleted, is_container, speed_limit, is_paused, priority, last_seen)
		VALUES (1, 1, 1, 'AA:BB:CC:00:00:01', 1, 0, 0, 0, '0', 0, 0, datetime('now')),
		       (2, 2, 1, 'AA:BB:CC:00:00:02', 0, 0, 0, 0, '0', 0, 0, datetime('now'))`); err != nil {
		t.Fatalf("seed devices: %v", err)
	}
}

func boolToInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

func decodeBootstrap(t *testing.T, frame []byte) map[string]interface{} {
	t.Helper()
	var msg map[string]interface{}
	if err := json.Unmarshal(frame, &msg); err != nil {
		t.Fatalf("frame is not valid JSON: %v", err)
	}
	return msg
}

func TestBootstrapFrameMedians(t *testing.T) {
	database := openBootstrapDB(t)
	seedBootstrapHistory(t, database)

	frame := buildBootstrapFrame(database, 1)
	if frame == nil {
		t.Fatal("expected a bootstrap frame, got none")
	}
	msg := decodeBootstrap(t, frame)
	if msg["type"] != "telemetry_tick" {
		t.Fatalf("type: %v", msg["type"])
	}
	if msg["bootstrap"] != true {
		t.Fatalf("bootstrap flag: %v", msg["bootstrap"])
	}
	router, _ := msg["router"].(map[string]interface{})
	if router == nil {
		t.Fatal("frame has no router object")
	}

	// CPU median over {10,20,30,40,50} = 30; memory % median = 60.
	if got := router["cpu_load"]; got != 30.0 {
		t.Errorf("cpu_load: got %v want 30", got)
	}
	if got := router["memory_usage_pct"]; got != 60.0 {
		t.Errorf("memory_usage_pct: got %v want 60", got)
	}
	// Temperature: 4 non-null of 5 buckets, median of {44,46,50,52} = 48.
	if got := router["temperature"]; got != 48.0 {
		t.Errorf("temperature: got %v want 48", got)
	}
	// WAN: complete buckets have rx sums 3e6/7e6/11e6/17e6 (the partial 08:45
	// bucket is excluded by the coverage HAVING); median = 9e6.
	if got := router["wan_rx_bps"]; got != 9e6 {
		t.Errorf("wan_rx_bps: got %v want 9000000", got)
	}
	if got := router["wan_tx_bps"]; got != 4.5e6 {
		t.Errorf("wan_tx_bps: got %v want 4500000", got)
	}
	// Counts come from the live DB, not history.
	if router["user_count"] != float64(2) {
		t.Errorf("user_count: %v", router["user_count"])
	}
	if router["client_device_count"] != float64(2) || router["active_clients"] != float64(1) {
		t.Errorf("devices: total=%v active=%v", router["client_device_count"], router["active_clients"])
	}
	// Memory: total 1024 MB; median used_bytes 629145600 = 600 MB, so free ≈ 424.
	if got, ok := router["total_memory_mb"].(float64); !ok || got != 1024 {
		t.Errorf("total_memory_mb: %v", router["total_memory_mb"])
	}
	if got, ok := router["free_memory_mb"].(float64); !ok || got < 420 || got > 428 {
		t.Errorf("free_memory_mb: %v want ~424", got)
	}
	if mon, ok := router["monitored_interfaces"].([]interface{}); !ok || len(mon) != 2 {
		t.Errorf("monitored_interfaces: %v", router["monitored_interfaces"])
	}
}

func TestBootstrapFrameAbsentWhenNoHistory(t *testing.T) {
	database := openBootstrapDB(t)
	seedBootstrapHistory(t, database)
	// Delete every bucket: the frame must NOT be emitted with zeroed fields.
	if _, err := database.SqlDB.Exec("DELETE FROM system_metric_buckets; DELETE FROM interface_metric_buckets"); err != nil {
		t.Fatalf("clear buckets: %v", err)
	}
	frame := buildBootstrapFrame(database, 1)
	if frame == nil {
		t.Fatal("expected a counts-only frame")
	}
	router := decodeBootstrap(t, frame)["router"].(map[string]interface{})
	for _, key := range []string{"cpu_load", "memory_usage_pct", "wan_rx_bps", "wan_tx_bps", "temperature"} {
		if _, present := router[key]; present {
			t.Errorf("%s present without history — absent must stay absent", key)
		}
	}
	// Roster counts survive: they are live DB facts, not history medians.
	if router["user_count"] != float64(2) {
		t.Errorf("user_count: %v", router["user_count"])
	}
}

func TestBootstrapFrameUnknownAndDefaultRouter(t *testing.T) {
	database := openBootstrapDB(t)
	seedBootstrapHistory(t, database)

	if frame := buildBootstrapFrame(database, 77); frame != nil {
		t.Error("unknown router id must not produce a frame")
	}
	// routerID 0 resolves to the default router.
	frame := buildBootstrapFrame(database, 0)
	if frame == nil {
		t.Fatal("default router should resolve")
	}
	if id := decodeBootstrap(t, frame)["router_id"]; id != float64(1) {
		t.Errorf("router_id: %v want 1", id)
	}
}

func TestHubBootstrapFallbackOnColdConnect(t *testing.T) {
	database := openBootstrapDB(t)
	seedBootstrapHistory(t, database)

	hub := NewHub()
	hub.AttachDatabase(database)

	wsURL, closeServer := startHubTestServer(t, hub)
	defer closeServer()

	conn := dialWS(t, wsURL, 1)
	defer conn.Close()

	// First message: connected. Second: the bootstrap frame, because no live
	// tick has ever been broadcast for this router.
	initMsg := readWSJSON(t, conn)
	if initMsg["type"] != "connected" {
		t.Fatalf("expected connected, got %v", initMsg["type"])
	}
	boot := readWSJSON(t, conn)
	if boot["type"] != "telemetry_tick" || boot["bootstrap"] != true {
		t.Fatalf("expected bootstrap tick, got %v", boot)
	}

	// A live tick now overrides the cache: a second cold connection must get
	// the LIVE frame, not a stale bootstrap.
	hub.BroadcastRouter(1, true, map[string]interface{}{
		"type": "telemetry_tick", "router_id": 1, "live": true,
	})
	conn2 := dialWS(t, wsURL, 1)
	defer conn2.Close()
	readWSJSON(t, conn2) // connected
	second := readWSJSON(t, conn2)
	if second["bootstrap"] == true {
		t.Error("live tick did not take precedence over the bootstrap frame")
	}
	if second["live"] != true {
		t.Errorf("expected the live frame, got %v", second)
	}
}

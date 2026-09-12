package services

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"path/filepath"
	"strconv"
	"testing"
	"time"

	"github.com/masseselsev/mikroman/internal/crypto"
	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

type testBroadcaster struct{}

func (b *testBroadcaster) Broadcast(event interface{})                                {}
func (b *testBroadcaster) BroadcastRouter(routerID int, isDefault bool, event interface{}) {}

func TestDiscoveryAndTelemetryServices(t *testing.T) {
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test.db")

	fernet, _ := crypto.NewFernet("cw_z4pYJ2-8_9V18R5v6R1XbJ9i9w9G1R1XbJ9i9w9E=")
	database, err := db.Open(dbPath, fernet)
	if err != nil {
		t.Fatalf("failed to open database: %v", err)
	}
	defer database.Close()

	// Insert test router
	_, err = database.SqlDB.Exec("INSERT INTO routers (id, name, host) VALUES (1, 'TestRouter', '127.0.0.1')")
	if err != nil {
		t.Fatalf("failed to insert router: %v", err)
	}

	// Setup mock RouterOS server
	mux := http.NewServeMux()
	mux.HandleFunc("/rest/system/resource", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(routeros.Resource{
			CPULoad:     "5",
			FreeMemory:  "500000000",
			TotalMemory: "1000000000",
			Uptime:      "2d5h",
		})
	})
	mux.HandleFunc("/rest/system/health", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode([]map[string]interface{}{
			{"name": "cpu-temperature", "value": "48.5", "type": "C"},
			{"name": "voltage", "value": "24.0", "type": "V"},
		})
	})
	mux.HandleFunc("/rest/interface", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode([]routeros.Interface{
			{Name: "ether1", RxByte: "100000", TxByte: "50000"},
		})
	})
	mux.HandleFunc("/rest/ip/dhcp-server/lease", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode([]routeros.DHCPLease{
			{Address: "192.168.1.101", MacAddress: "AA:BB:CC:11:22:33", HostName: "iPad"},
		})
	})
	mux.HandleFunc("/rest/ip/arp", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode([]routeros.ARPEntry{})
	})
	mux.HandleFunc("/rest/interface/wifi/registration-table", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode([]routeros.WiFiRegistration{})
	})
	mux.HandleFunc("/rest/queue/simple", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode([]routeros.SimpleQueue{})
	})
	mux.HandleFunc("/rest/ip/firewall/mangle", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode([]routeros.MangleRule{})
	})
	mux.HandleFunc("/rest/ip/firewall/filter", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode([]routeros.FilterRule{})
	})
	mux.HandleFunc("/rest/ip/firewall/raw", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode([]routeros.FilterRule{})
	})

	server := httptest.NewServer(mux)
	defer server.Close()

	u, _ := url.Parse(server.URL)
	port, _ := strconv.Atoi(u.Port())
	client, err := routeros.NewClient(routeros.Config{
		Host:    u.Hostname(),
		Port:    port,
		Timeout: 2 * time.Second,
	})
	if err != nil {
		t.Fatalf("failed to create client: %v", err)
	}

	hub := &testBroadcaster{}
	ctx := context.Background()

	// 1. Test Discovery
	disc := NewDiscoveryService(database, client, hub)
	newCount, err := disc.SyncDevices(ctx, 1)
	if err != nil {
		t.Fatalf("discovery failed: %v", err)
	}
	if newCount != 1 {
		t.Fatalf("expected 1 new device, got %d", newCount)
	}

	dev, err := database.GetDeviceByMAC("AA:BB:CC:11:22:33")
	if err != nil || dev == nil {
		t.Fatalf("expected device in database: %v", err)
	}
	if dev.Hostname.String != "iPad" {
		t.Fatalf("expected hostname iPad, got %q", dev.Hostname.String)
	}

	// 2. Test Telemetry
	telem := NewTelemetryService(database, client, hub)
	if err := telem.Collect(ctx, 1); err != nil {
		t.Fatalf("telemetry collect failed: %v", err)
	}

	var cpuLoad, temperature, voltage float64
	err = database.SqlDB.QueryRow("SELECT cpu_load, temperature, voltage FROM system_metrics WHERE router_id = 1").Scan(&cpuLoad, &temperature, &voltage)
	if err != nil || cpuLoad != 5.0 || temperature != 48.5 || voltage != 24.0 {
		t.Fatalf("expected cpu_load 5.0, temp 48.5, volt 24.0 in system_metrics, got cpu=%f, temp=%f, volt=%f (err: %v)", cpuLoad, temperature, voltage, err)
	}

	var rxBytes int64
	err = database.SqlDB.QueryRow("SELECT rx_bytes_total FROM interface_metrics WHERE interface_name = 'ether1'").Scan(&rxBytes)
	if err != nil || rxBytes != 100000 {
		t.Fatalf("expected 100000 rx_bytes in interface_metrics, got %d (err: %v)", rxBytes, err)
	}

	// 3. Test GetLatestRates snapshot
	uRates, dRates := telem.GetLatestRates(1)
	if uRates == nil || dRates == nil {
		t.Fatalf("expected non-nil rate maps from GetLatestRates, got uRates=%v, dRates=%v", uRates, dRates)
	}

	// 4. Test dynamic telemetry interval
	if interval := telem.getInterval(0); interval != 3*time.Second {
		t.Fatalf("expected default 3s interval, got %v", interval)
	}
	_ = database.SetSetting("telemetry_interval_seconds", "2", "")
	if interval := telem.getInterval(0); interval != 2*time.Second {
		t.Fatalf("expected 2s interval from DB setting, got %v", interval)
	}
}

func TestTrafficServiceReconcile(t *testing.T) {
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "traffic_test.db")

	fernet, _ := crypto.NewFernet("cw_z4pYJ2-8_9V18R5v6R1XbJ9i9w9G1R1XbJ9i9w9E=")
	database, err := db.Open(dbPath, fernet)
	if err != nil {
		t.Fatalf("failed to open database: %v", err)
	}
	defer database.Close()

	// Insert router
	_, err = database.SqlDB.Exec("INSERT INTO routers (id, name, host, is_default) VALUES (1, 'TestRouter', '127.0.0.1', 1)")
	if err != nil {
		t.Fatalf("failed to insert router: %v", err)
	}

	// Insert limited user
	_, err = database.SqlDB.Exec("INSERT INTO users (id, router_id, name, speed_limit) VALUES (1, 1, 'Alice', '15M/15M')")
	if err != nil {
		t.Fatalf("failed to insert user: %v", err)
	}

	// Insert assigned device for Alice
	_, err = database.SqlDB.Exec("INSERT INTO devices (id, router_id, user_id, mac_address, ip_address, is_active) VALUES (1, 1, 1, '00:11:22:33:44:55', '192.0.2.10', 1)")
	if err != nil {
		t.Fatalf("failed to insert assigned device: %v", err)
	}

	// Insert unassigned device
	_, err = database.SqlDB.Exec("INSERT INTO devices (id, router_id, user_id, mac_address, ip_address, is_active) VALUES (2, 1, NULL, '00:11:22:33:44:66', '192.0.2.20', 1)")
	if err != nil {
		t.Fatalf("failed to insert unassigned device: %v", err)
	}

	var createdQueues []routeros.SimpleQueue
	var addressList []routeros.AddressListEntry
	filterRules := []routeros.FilterRule{
		{ID: "*1", Action: "fasttrack-connection", Chain: "forward"},
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/rest/queue/simple", func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPut {
			var q routeros.SimpleQueue
			_ = json.NewDecoder(r.Body).Decode(&q)
			q.ID = "*q" + strconv.Itoa(len(createdQueues)+1)
			createdQueues = append(createdQueues, q)
			_ = json.NewEncoder(w).Encode(q)
			return
		}
		_ = json.NewEncoder(w).Encode(createdQueues)
	})
	mux.HandleFunc("/rest/ip/firewall/address-list", func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPut {
			var entry routeros.AddressListEntry
			_ = json.NewDecoder(r.Body).Decode(&entry)
			entry.ID = "*al" + strconv.Itoa(len(addressList)+1)
			addressList = append(addressList, entry)
			_ = json.NewEncoder(w).Encode(entry)
			return
		}
		_ = json.NewEncoder(w).Encode(addressList)
	})
	mux.HandleFunc("/rest/ip/firewall/filter", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(filterRules)
	})
	mux.HandleFunc("/rest/ip/firewall/filter/*1", func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPatch {
			var fields map[string]interface{}
			_ = json.NewDecoder(r.Body).Decode(&fields)
			if src, ok := fields["src-address-list"].(string); ok {
				filterRules[0].SrcAddressList = src
			}
			if dst, ok := fields["dst-address-list"].(string); ok {
				filterRules[0].DstAddressList = dst
			}
			w.WriteHeader(http.StatusOK)
			return
		}
	})
	mux.HandleFunc("/rest/ip/firewall/raw", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode([]routeros.FilterRule{})
	})

	server := httptest.NewServer(mux)
	defer server.Close()

	u, _ := url.Parse(server.URL)
	port, _ := strconv.Atoi(u.Port())
	client, err := routeros.NewClient(routeros.Config{
		Host:    u.Hostname(),
		Port:    port,
		Timeout: 2 * time.Second,
	})
	if err != nil {
		t.Fatalf("failed to create client: %v", err)
	}

	trafficSvc := NewTrafficService(database, client)
	ctx := context.Background()
	if err := trafficSvc.ReconcileQueues(ctx, 1); err != nil {
		t.Fatalf("ReconcileQueues failed: %v", err)
	}

	// Verify FastTrack exemption rule was patched
	if filterRules[0].SrcAddressList != "!mikroman_queued" || filterRules[0].DstAddressList != "!mikroman_queued" {
		t.Fatalf("expected fasttrack exemption to be '!mikroman_queued', got src=%q, dst=%q",
			filterRules[0].SrcAddressList, filterRules[0].DstAddressList)
	}

	// Verify Simple Queues were created: Alice and dev-2
	if len(createdQueues) != 2 {
		t.Fatalf("expected 2 Simple Queues, got %d: %+v", len(createdQueues), createdQueues)
	}

	// Verify address list mikroman_queued contains both 192.0.2.10 and 192.0.2.20
	queuedIPs := make(map[string]bool)
	for _, entry := range addressList {
		if entry.List == "mikroman_queued" {
			queuedIPs[entry.Address] = true
		}
	}
	if !queuedIPs["192.0.2.10"] || !queuedIPs["192.0.2.20"] {
		t.Fatalf("expected 192.0.2.10 and 192.0.2.20 in mikroman_queued, got %+v", queuedIPs)
	}
}

func TestFormatRate(t *testing.T) {
	cases := map[string]string{
		"15M/15M":   "15M/15M",
		"11m/11m":   "11M/11M",
		"15M":       "15M/15M",
		"15m":       "15M/15M",
		"15":        "15M/15M",
		"500k":      "500k/500k",
		"1g":        "1G/1G",
		"unlimited": "0/0",
		"default":   "0/0",
		"0":         "0/0",
		"0/0":       "0/0",
		"":          "0/0",
	}
	for in, expected := range cases {
		out := formatRate(in)
		if out != expected {
			t.Errorf("formatRate(%q) = %q; expected %q", in, out, expected)
		}
	}
}

func TestNormalizeRateLimitAndTarget(t *testing.T) {
	// 1. Rate limits
	if normalizeRateLimit("11M/11M") != normalizeRateLimit("11000000/11000000") {
		t.Fatalf("expected 11M/11M to equal 11000000/11000000 in normalized form, got %q vs %q",
			normalizeRateLimit("11M/11M"), normalizeRateLimit("11000000/11000000"))
	}
	if normalizeRateLimit("5M/5M") != normalizeRateLimit("5000000/5000000") {
		t.Fatalf("expected 5M/5M to equal 5000000/5000000 in normalized form")
	}
	if normalizeRateLimit("500k/1M") != "500000/1000000" {
		t.Fatalf("expected 500000/1000000, got %q", normalizeRateLimit("500k/1M"))
	}

	// 2. Targets (order independence and /32 normalization)
	t1 := normalizeTarget("192.0.2.250/32,192.0.2.246/32,192.0.2.239/32")
	t2 := normalizeTarget("192.0.2.246/32, 192.0.2.239/32, 192.0.2.250/32")
	if t1 != t2 {
		t.Fatalf("expected normalized targets to match regardless of order, got %q vs %q", t1, t2)
	}

	// 3. Parent (none vs empty)
	if normalizeParent("none") != normalizeParent("") {
		t.Fatalf("expected 'none' and '' to normalize identically")
	}
}

func TestReconcileQueuesNoSpamWhenMatching(t *testing.T) {
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "queue_nospam.db")
	fernet, _ := crypto.NewFernet("cw_z4pYJ2-8_9V18R5v6R1XbJ9i9w9G1R1XbJ9i9w9E=")
	database, _ := db.Open(dbPath, fernet)
	defer database.Close()

	_, _ = database.SqlDB.Exec("INSERT INTO routers (id, name, host) VALUES (1, 'TestRouter', '127.0.0.1')")
	_, _ = database.SqlDB.Exec("INSERT INTO users (id, router_id, name, speed_limit) VALUES (1, 1, 'Mark', '11M/11M')")
	_, _ = database.SqlDB.Exec("INSERT INTO devices (id, router_id, user_id, mac_address, ip_address, is_active) VALUES (1, 1, 1, 'AA:BB:CC:11:22:33', '192.0.2.100', 1)")

	updateCount := 0
	createCount := 0

	mux := http.NewServeMux()
	mux.HandleFunc("/rest/queue/simple", func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet {
			// RouterOS returns expanded bps rate limit ("11000000/11000000") and parent "none"
			_ = json.NewEncoder(w).Encode([]map[string]interface{}{
				{
					".id":       "*10",
					"name":      "mikroman-Mark",
					"target":    "192.0.2.100/32",
					"max-limit": "11000000/11000000",
					"parent":    "none",
					"comment":   "mikroman:user_1",
				},
			})
		} else if r.Method == http.MethodPut || r.Method == http.MethodPost {
			createCount++
			_ = json.NewEncoder(w).Encode(map[string]interface{}{".id": "*11"})
		}
	})
	mux.HandleFunc("/rest/queue/simple/*10", func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPatch || r.Method == http.MethodPost {
			updateCount++
			_ = json.NewEncoder(w).Encode(map[string]interface{}{".id": "*10"})
		}
	})
	mux.HandleFunc("/rest/ip/firewall/address-list", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode([]map[string]interface{}{
			{".id": "*20", "list": "mikroman_queued", "address": "192.0.2.100", "comment": "mikroman:queued:user_1"},
		})
	})

	server := httptest.NewServer(mux)
	defer server.Close()

	u, _ := url.Parse(server.URL)
	port, _ := strconv.Atoi(u.Port())
	client, _ := routeros.NewClient(routeros.Config{
		Host:    u.Hostname(),
		Port:    port,
		Timeout: 2 * time.Second,
	})

	trafficSvc := NewTrafficService(database, client)
	ctx := context.Background()

	// Run ReconcileQueues
	if err := trafficSvc.ReconcileQueues(ctx, 1); err != nil {
		t.Fatalf("ReconcileQueues failed: %v", err)
	}

	// Verify that NO update or create calls were sent to RouterOS!
	if updateCount != 0 {
		t.Fatalf("expected 0 queue updates, got %d (redundant queue set spam!)", updateCount)
	}
	if createCount != 0 {
		t.Fatalf("expected 0 queue creates, got %d", createCount)
	}
}


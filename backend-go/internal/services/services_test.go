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

	"github.com/masseselsev/mikroman/internal/api"
	"github.com/masseselsev/mikroman/internal/crypto"
	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

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

	hub := api.NewHub()
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

	var cpuLoad float64
	err = database.SqlDB.QueryRow("SELECT cpu_load FROM system_metrics WHERE router_id = 1").Scan(&cpuLoad)
	if err != nil || cpuLoad != 5.0 {
		t.Fatalf("expected cpu_load 5.0 in system_metrics, got %f (err: %v)", cpuLoad, err)
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

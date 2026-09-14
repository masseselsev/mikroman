package services

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"path/filepath"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/masseselsev/mikroman/internal/crypto"
	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

func TestAccountingPass_AccumulatesDeltasAndHandlesReboot(t *testing.T) {
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test_traffic_acct.db")

	fernet, _ := crypto.NewFernet("cw_z4pYJ2-8_9V18R5v6R1XbJ9i9w9G1R1XbJ9i9w9E=")
	database, err := db.Open(dbPath, fernet)
	if err != nil {
		t.Fatalf("failed to open database: %v", err)
	}
	defer database.Close()

	// Insert test router and device
	_, err = database.SqlDB.Exec("INSERT INTO routers (id, name, host) VALUES (1, 'MainRouter', '192.0.2.1')")
	if err != nil {
		t.Fatalf("failed to insert router: %v", err)
	}
	_, err = database.SqlDB.Exec("INSERT INTO devices (id, router_id, mac_address, ip_address, is_active) VALUES (1, 1, 'AA:BB:CC:00:00:01', '192.0.2.50', 1)")
	if err != nil {
		t.Fatalf("failed to insert device: %v", err)
	}

	var mu sync.Mutex
	mangleBytesUp := int64(1000)
	mangleBytesDown := int64(5000)

	mux := http.NewServeMux()
	mux.HandleFunc("/rest/ip/firewall/mangle", func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		if r.Method == http.MethodGet {
			rules := []map[string]interface{}{
				{
					".id":     "*m1",
					"chain":   "forward",
					"action":  "passthrough",
					"comment": "mikroman:acct:dev_1:up",
					"bytes":   strconv.FormatInt(mangleBytesUp, 10),
				},
				{
					".id":     "*m2",
					"chain":   "forward",
					"action":  "passthrough",
					"comment": "mikroman:acct:dev_1:down",
					"bytes":   strconv.FormatInt(mangleBytesDown, 10),
				},
			}
			_ = json.NewEncoder(w).Encode(rules)
		} else {
			_ = json.NewEncoder(w).Encode(map[string]interface{}{".id": "*new"})
		}
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

	svc := NewTrafficService(database, client)
	ctx := context.Background()

	// Pass 1: Baseline initialization
	if err := svc.AccountingPass(ctx, 1); err != nil {
		t.Fatalf("AccountingPass pass 1 failed: %v", err)
	}

	// Verify no rollup yet (pass 1 is purely baseline establishment)
	var count int
	_ = database.SqlDB.QueryRow("SELECT COUNT(*) FROM device_traffic_rollups WHERE device_id = 1").Scan(&count)
	if count != 0 {
		t.Fatalf("expected 0 rollups after baseline pass 1, got %d", count)
	}

	// Pass 2: Simulate traffic accumulation (Up +500 bytes, Down +2000 bytes)
	mu.Lock()
	mangleBytesUp = 1500
	mangleBytesDown = 7000
	mu.Unlock()

	if err := svc.AccountingPass(ctx, 1); err != nil {
		t.Fatalf("AccountingPass pass 2 failed: %v", err)
	}

	var bytesIn, bytesOut int64
	today := time.Now().Format("2006-01-02")
	err = database.SqlDB.QueryRow("SELECT bytes_in, bytes_out FROM device_traffic_rollups WHERE device_id = 1 AND record_date = ?", today).Scan(&bytesIn, &bytesOut)
	if err != nil {
		t.Fatalf("failed to query rollups after pass 2: %v", err)
	}

	if bytesOut != 500 || bytesIn != 2000 {
		t.Fatalf("expected (in=2000, out=500), got (in=%d, out=%d)", bytesIn, bytesOut)
	}

	// Pass 3: Simulate router reboot / counter rollover (counters drop to 300 and 1000)
	mu.Lock()
	mangleBytesUp = 300
	mangleBytesDown = 1000
	mu.Unlock()

	if err := svc.AccountingPass(ctx, 1); err != nil {
		t.Fatalf("AccountingPass pass 3 (reboot) failed: %v", err)
	}

	err = database.SqlDB.QueryRow("SELECT bytes_in, bytes_out FROM device_traffic_rollups WHERE device_id = 1 AND record_date = ?", today).Scan(&bytesIn, &bytesOut)
	if err != nil {
		t.Fatalf("failed to query rollups after pass 3: %v", err)
	}

	// Reboot delta should accumulate: Out = 500 + 300 = 800; In = 2000 + 1000 = 3000
	if bytesOut != 800 || bytesIn != 3000 {
		t.Fatalf("expected after reboot (in=3000, out=800), got (in=%d, out=%d)", bytesIn, bytesOut)
	}
}


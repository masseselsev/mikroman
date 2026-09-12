package services

import (
	"path/filepath"
	"testing"

	"github.com/masseselsev/mikroman/internal/crypto"
	"github.com/masseselsev/mikroman/internal/db"
)

func TestIsIgnoredDiscoveryInterface(t *testing.T) {
	wanIfaces := []string{"ether1", "sfp-sfpplus1"}
	userIgnored := []string{"vlan-guest", "custom-tunnel"}

	tests := []struct {
		iface    string
		expected bool
	}{
		// ZeroTier
		{"zerotier1", true},
		{"zt0", true},
		{"ZeroTier_Home", true},
		// WireGuard & VPNs
		{"wg0", true},
		{"wireguard-client", true},
		{"ovpn-out1", true},
		{"tun0", true},
		{"tap1", true},
		{"gre-tunnel1", true},
		{"eoip-tunnel1", true},
		{"pppoe-out1", true},
		{"sstp-out1", true},
		{"l2tp-out1", true},
		{"ipip1", true},
		// Containers
		{"veth1", true},
		{"veth-mikroman", true},
		// WAN interfaces
		{"ether1", true},
		{"sfp-sfpplus1", true},
		// User configured ignored
		{"vlan-guest", true},
		{"custom-tunnel", true},
		// Valid LAN interfaces (MUST NOT be ignored)
		{"bridge", false},
		{"bridge1", false},
		{"ether2", false},
		{"ether3", false},
		{"wlan1", false},
		{"wlan2", false},
		{"wifi1", false},
		{"", false},
	}

	for _, tt := range tests {
		got := IsIgnoredDiscoveryInterface(tt.iface, wanIfaces, userIgnored)
		if got != tt.expected {
			t.Errorf("IsIgnoredDiscoveryInterface(%q) = %v; want %v", tt.iface, got, tt.expected)
		}
	}
}

func TestDiscoveryCleanupIgnoredUnassignedDevices(t *testing.T) {
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test_discovery.db")

	fernet, err := crypto.NewFernet("cw_z4pYJ2-8_9V18R5v6R1XbJ9i9w9G1R1XbJ9i9w9E=")
	if err != nil {
		t.Fatalf("failed to create Fernet: %v", err)
	}

	database, err := db.Open(dbPath, fernet)
	if err != nil {
		t.Fatalf("failed to open database: %v", err)
	}
	defer database.Close()

	router := &db.Router{
		Name:      "Test-Router",
		Host:      "192.168.1.1",
		Port:      443,
		UseSSL:    true,
		SSLVerify: false,
		Username:  "admin",
		Password:  "secret",
		IsActive:  true,
		IsDefault: true,
	}
	if err := database.CreateRouter(router); err != nil {
		t.Fatalf("failed to create router: %v", err)
	}

	user := &db.User{
		Name:       "testuser",
		RouterID:   &router.ID,
		SpeedLimit: "10M/10M",
	}
	if err := database.CreateUser(user); err != nil {
		t.Fatalf("failed to create user: %v", err)
	}

	// Insert:
	// 1) Unassigned device on zerotier1 (should be soft-deleted)
	// 2) Unassigned device on bridge (should remain active)
	// 3) Assigned device on zerotier1 (should NOT be deleted because user explicitly assigned it)
	_, err = database.SqlDB.Exec(`
		INSERT INTO devices (router_id, mac_address, ip_address, hostname, last_interface, is_active, is_hidden, is_deleted, user_id)
		VALUES
			(?, 'AA:BB:CC:11:22:33', '172.22.22.188', 'remote-zt-node', 'zerotier1', 1, 0, 0, NULL),
			(?, 'AA:BB:CC:44:55:66', '192.168.88.100', 'local-pc', 'bridge', 1, 0, 0, NULL),
			(?, 'AA:BB:CC:77:88:99', '172.22.22.200', 'assigned-zt-node', 'zerotier1', 1, 0, 0, ?)
	`, router.ID, router.ID, router.ID, user.ID)
	if err != nil {
		t.Fatalf("failed to seed devices: %v", err)
	}

	discSvc := NewDiscoveryService(database, nil, nil)
	wanIfaces := []string{"ether1"}
	userIgnored := []string{}

	discSvc.cleanupIgnoredUnassignedDevices(router.ID, wanIfaces, userIgnored)

	// Verify unassigned ZT device is soft-deleted
	var ztDeleted, ztActive int
	err = database.SqlDB.QueryRow(`SELECT is_deleted, is_active FROM devices WHERE mac_address = 'AA:BB:CC:11:22:33'`).Scan(&ztDeleted, &ztActive)
	if err != nil {
		t.Fatalf("failed to query zt device: %v", err)
	}
	if ztDeleted != 1 || ztActive != 0 {
		t.Fatalf("expected zt device to be deleted (1, 0), got (%d, %d)", ztDeleted, ztActive)
	}

	// Verify unassigned LAN device is intact
	var lanDeleted, lanActive int
	err = database.SqlDB.QueryRow(`SELECT is_deleted, is_active FROM devices WHERE mac_address = 'AA:BB:CC:44:55:66'`).Scan(&lanDeleted, &lanActive)
	if err != nil {
		t.Fatalf("failed to query lan device: %v", err)
	}
	if lanDeleted != 0 || lanActive != 1 {
		t.Fatalf("expected lan device to be active (0, 1), got (%d, %d)", lanDeleted, lanActive)
	}

	// Verify assigned ZT device is intact
	var assignedDeleted int
	err = database.SqlDB.QueryRow(`SELECT is_deleted FROM devices WHERE mac_address = 'AA:BB:CC:77:88:99'`).Scan(&assignedDeleted)
	if err != nil {
		t.Fatalf("failed to query assigned device: %v", err)
	}
	if assignedDeleted != 0 {
		t.Fatalf("expected assigned zt device to NOT be deleted, got %d", assignedDeleted)
	}
}

package services

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log/slog"
	"strings"
	"sync"
	"time"

	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

type DiscoveryService struct {
	database *db.DB
	client   *routeros.Client
	hub      EventBroadcaster
	mu       sync.Mutex
	clients  map[int]*routeros.Client
}

func NewDiscoveryService(database *db.DB, client *routeros.Client, hub EventBroadcaster) *DiscoveryService {
	clients := make(map[int]*routeros.Client)
	if client != nil {
		if def, err := database.GetDefaultRouter(); err == nil && def != nil {
			clients[def.ID] = client
		}
	}
	return &DiscoveryService{
		database: database,
		client:   client,
		hub:      hub,
		clients:  clients,
	}
}

func (s *DiscoveryService) getClient(routerID int) (*routeros.Client, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if c, ok := s.clients[routerID]; ok && c != nil {
		return c, nil
	}

	defaultRouter, _ := s.database.GetDefaultRouter()
	if defaultRouter != nil && defaultRouter.ID == routerID && s.client != nil {
		s.clients[routerID] = s.client
		return s.client, nil
	}
	if defaultRouter == nil && s.client != nil {
		routers, _ := s.database.GetRouters()
		if len(routers) <= 1 {
			s.clients[routerID] = s.client
			return s.client, nil
		}
	}

	router, err := s.database.GetRouter(routerID)
	if err != nil {
		return nil, err
	}
	if router == nil {
		return nil, fmt.Errorf("router %d not found", routerID)
	}

	newClient, err := routeros.NewClient(routeros.Config{
		Host:      router.Host,
		Port:      router.Port,
		Username:  router.Username,
		Password:  router.Password,
		UseSSL:    router.UseSSL,
		SSLVerify: router.SSLVerify,
		CACert:    router.CACert.String,
		Timeout:   5 * time.Second,
	})
	if err != nil {
		return nil, err
	}

	s.clients[routerID] = newClient
	return newClient, nil
}

func (s *DiscoveryService) getWanInterfaces(routerID int) []string {
	key := fmt.Sprintf("monitored_interfaces_%d", routerID)
	val, err := s.database.GetSetting(key)
	var ifaces []string
	if err == nil && val != "" {
		_ = json.Unmarshal([]byte(val), &ifaces)
	}
	if len(ifaces) == 0 {
		defVal, _ := s.database.GetSetting("monitored_interfaces_default")
		if defVal != "" {
			_ = json.Unmarshal([]byte(defVal), &ifaces)
		}
	}
	return ifaces
}

func (s *DiscoveryService) getIgnoredDiscoveryInterfaces(routerID int) []string {
	key := fmt.Sprintf("ignored_discovery_interfaces_%d", routerID)
	val, err := s.database.GetSetting(key)
	var ifaces []string
	if err == nil && val != "" {
		_ = json.Unmarshal([]byte(val), &ifaces)
	}
	if len(ifaces) == 0 {
		defVal, _ := s.database.GetSetting("ignored_discovery_interfaces_default")
		if defVal != "" {
			_ = json.Unmarshal([]byte(defVal), &ifaces)
		}
	}
	return ifaces
}

// IsIgnoredDiscoveryInterface checks whether an interface should be excluded from client device discovery.
func IsIgnoredDiscoveryInterface(iface string, wanIfaces []string, userIgnored []string) bool {
	trimmed := strings.TrimSpace(iface)
	if trimmed == "" {
		return false
	}
	lower := strings.ToLower(trimmed)

	// 1. Known tunnel, VPN and overlay interfaces (e.g. ZeroTier, WireGuard, OpenVPN, PPP)
	if strings.HasPrefix(lower, "zerotier") || strings.HasPrefix(lower, "zt") ||
		strings.HasPrefix(lower, "wg") || strings.HasPrefix(lower, "wireguard") ||
		strings.HasPrefix(lower, "ovpn") || strings.HasPrefix(lower, "tun") ||
		strings.HasPrefix(lower, "tap") || strings.HasPrefix(lower, "gre") ||
		strings.HasPrefix(lower, "eoip") || strings.HasPrefix(lower, "ppp") ||
		strings.HasPrefix(lower, "sstp") || strings.HasPrefix(lower, "l2tp") ||
		strings.HasPrefix(lower, "ipip") {
		return true
	}

	// 2. Container interfaces (veth pairs)
	if strings.HasPrefix(lower, "veth") {
		return true
	}

	// 3. Monitored WAN interfaces (facing upstream ISP gateway)
	for _, w := range wanIfaces {
		if strings.EqualFold(strings.TrimSpace(w), trimmed) {
			return true
		}
	}

	// 4. User-configured ignored interfaces
	for _, ign := range userIgnored {
		if strings.EqualFold(strings.TrimSpace(ign), trimmed) {
			return true
		}
	}

	return false
}

func (s *DiscoveryService) cleanupIgnoredUnassignedDevices(routerID int, wanIfaces []string, userIgnored []string) {
	query := `SELECT id, last_interface FROM devices WHERE user_id IS NULL AND is_deleted = 0 AND router_id = ?`
	rows, err := s.database.SqlDB.Query(query, routerID)
	if err != nil {
		return
	}
	defer rows.Close()

	var idsToSoftDelete []int
	for rows.Next() {
		var id int
		var lastIface sql.NullString
		if err := rows.Scan(&id, &lastIface); err == nil {
			if lastIface.Valid && IsIgnoredDiscoveryInterface(lastIface.String, wanIfaces, userIgnored) {
				idsToSoftDelete = append(idsToSoftDelete, id)
			}
		}
	}

	for _, devID := range idsToSoftDelete {
		_, _ = s.database.SqlDB.Exec(`UPDATE devices SET is_deleted = 1, is_active = 0 WHERE id = ?`, devID)
	}
}

// SyncDevices performs one full discovery sweep across DHCP, ARP, and WiFi tables.
func (s *DiscoveryService) SyncDevices(ctx context.Context, routerID int) (int, error) {
	client, err := s.getClient(routerID)
	if err != nil || client == nil {
		return 0, err
	}

	wanIfaces := s.getWanInterfaces(routerID)
	ignoredIfaces := s.getIgnoredDiscoveryInterfaces(routerID)

	leases, _ := client.GetDHCPLeases(ctx)
	arps, _ := client.GetARPTable(ctx)
	wifis, _ := client.GetWiFiRegistrations(ctx)

	// Clean up any unassigned stray devices previously discovered on now-ignored interfaces
	s.cleanupIgnoredUnassignedDevices(routerID, wanIfaces, ignoredIfaces)

	// Index by MAC
	type DiscoveredInfo struct {
		MAC       string
		IP        string
		Hostname  string
		Interface string
		Signal    *int
	}

	discovered := make(map[string]*DiscoveredInfo)

	for _, l := range leases {
		if bool(l.Disabled) {
			continue
		}
		mac := strings.ToUpper(strings.TrimSpace(l.MacAddress))
		if mac == "" {
			continue
		}
		discovered[mac] = &DiscoveredInfo{
			MAC:      mac,
			IP:       l.Address,
			Hostname: l.HostName,
		}
	}

	for _, a := range arps {
		if a.Complete == "false" || bool(a.Disabled) {
			continue
		}
		if IsIgnoredDiscoveryInterface(a.Interface, wanIfaces, ignoredIfaces) {
			continue
		}
		mac := strings.ToUpper(strings.TrimSpace(a.MacAddress))
		if mac == "" {
			continue
		}
		item, exists := discovered[mac]
		if !exists {
			item = &DiscoveredInfo{MAC: mac}
			discovered[mac] = item
		}
		if item.IP == "" {
			item.IP = a.Address
		}
		if a.Interface != "" {
			item.Interface = a.Interface
		}
	}

	for _, w := range wifis {
		if IsIgnoredDiscoveryInterface(w.Interface, wanIfaces, ignoredIfaces) {
			continue
		}
		mac := strings.ToUpper(strings.TrimSpace(w.MacAddress))
		if mac == "" {
			continue
		}
		item, exists := discovered[mac]
		if !exists {
			item = &DiscoveredInfo{MAC: mac}
			discovered[mac] = item
		}
		if w.Interface != "" {
			item.Interface = w.Interface
		}
	}

	newCount := 0

	for mac, info := range discovered {
		existing, err := s.database.GetDeviceByMAC(mac)
		if err != nil {
			continue
		}

		if info.IP != "" {
			info.IP = strings.TrimSpace(strings.Split(info.IP, "/")[0])
		}

		if existing == nil {
			// Insert new device
			res, err := s.database.SqlDB.Exec(`
				INSERT INTO devices (
					router_id, mac_address, ip_address, hostname, last_interface,
					is_active, is_hidden, is_deleted, last_seen
				) VALUES (?, ?, ?, ?, ?, 1, 0, 0, CURRENT_TIMESTAMP)
			`, routerID, mac, info.IP, info.Hostname, info.Interface)
			if err == nil {
				newCount++
				devID, _ := res.LastInsertId()
				// Record discovery in device history
				_, _ = s.database.SqlDB.Exec(`
					INSERT INTO device_history (device_id, mac_address, hostname, ip_address, event_type, created_at)
					VALUES (?, ?, ?, ?, 'discovered', CURRENT_TIMESTAMP)
				`, devID, mac, info.Hostname, info.IP)
			}
		} else {
			// Update existing device
			_, _ = s.database.SqlDB.Exec(`
				UPDATE devices SET
					router_id = coalesce(?, router_id),
					ip_address = coalesce(?, ip_address),
					hostname = coalesce(nullif(?, ''), hostname),
					last_interface = coalesce(nullif(?, ''), last_interface),
					is_active = 1,
					last_seen = CURRENT_TIMESTAMP
				WHERE id = ?
			`, routerID, nullIfEmpty(info.IP), nullIfEmpty(info.Hostname), nullIfEmpty(info.Interface), existing.ID)
		}
	}

	if newCount > 0 && s.hub != nil {
		slog.Info("Discovered new devices on router", "count", newCount, "router_id", routerID)
		s.hub.Broadcast(map[string]interface{}{
			"type":      "devices_updated",
			"new_count": newCount,
			"router_id": routerID,
		})
	}

	return newCount, nil
}

func nullIfEmpty(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}

// StartBackgroundLoop runs device discovery periodically in a goroutine.
func (s *DiscoveryService) StartBackgroundLoop(ctx context.Context, interval time.Duration) {
	runPass := func() {
		routers, err := s.database.GetRouters()
		if err != nil {
			return
		}
		for _, r := range routers {
			if r.IsActive {
				sweepCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
				_, _ = s.SyncDevices(sweepCtx, r.ID)
				cancel()
			}
		}
	}

	go func() {
		// Run immediate discovery pass on startup
		runPass()

		ticker := time.NewTicker(interval)
		defer ticker.Stop()

		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				runPass()
			}
		}
	}()
}

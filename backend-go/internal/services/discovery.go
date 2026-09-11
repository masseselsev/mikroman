package services

import (
	"context"
	"log/slog"
	"strings"
	"time"

	"github.com/masseselsev/mikroman/internal/api"
	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

type DiscoveryService struct {
	database *db.DB
	client   *routeros.Client
	hub      *api.Hub
}

func NewDiscoveryService(database *db.DB, client *routeros.Client, hub *api.Hub) *DiscoveryService {
	return &DiscoveryService{
		database: database,
		client:   client,
		hub:      hub,
	}
}

// SyncDevices performs one full discovery sweep across DHCP, ARP, and WiFi tables.
func (s *DiscoveryService) SyncDevices(ctx context.Context, routerID int) (int, error) {
	if s.client == nil {
		return 0, nil
	}

	leases, _ := s.client.GetDHCPLeases(ctx)
	arps, _ := s.client.GetARPTable(ctx)
	wifis, _ := s.client.GetWiFiRegistrations(ctx)

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
					ip_address = coalesce(?, ip_address),
					hostname = coalesce(nullif(?, ''), hostname),
					last_interface = coalesce(nullif(?, ''), last_interface),
					is_active = 1,
					last_seen = CURRENT_TIMESTAMP
				WHERE id = ?
			`, nullIfEmpty(info.IP), nullIfEmpty(info.Hostname), nullIfEmpty(info.Interface), existing.ID)
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
	go func() {
		ticker := time.NewTicker(interval)
		defer ticker.Stop()

		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				routers, err := s.database.GetRouters()
				if err != nil {
					continue
				}
				for _, r := range routers {
					if r.IsActive {
						sweepCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
						_, _ = s.SyncDevices(sweepCtx, r.ID)
						cancel()
					}
				}
			}
		}
	}()
}

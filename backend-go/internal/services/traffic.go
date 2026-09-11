package services

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

type TrafficService struct {
	database *db.DB
	client   *routeros.Client
	mu       sync.Mutex
	clients  map[int]*routeros.Client

	// Previous counter state: routerID -> ruleID -> cumulativeBytes
	prevCounters map[int]map[string]int64
}

func NewTrafficService(database *db.DB, client *routeros.Client) *TrafficService {
	clients := make(map[int]*routeros.Client)
	if client != nil {
		if def, err := database.GetDefaultRouter(); err == nil && def != nil {
			clients[def.ID] = client
		}
	}
	return &TrafficService{
		database:     database,
		client:       client,
		clients:      clients,
		prevCounters: make(map[int]map[string]int64),
	}
}

func (s *TrafficService) getClient(routerID int) (*routeros.Client, error) {
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

// ReconcileQueues ensures Simple Queues match active user limits and assignments.
func (s *TrafficService) ReconcileQueues(ctx context.Context, routerID int) error {
	client, err := s.getClient(routerID)
	if err != nil || client == nil {
		return err
	}

	users, err := s.database.GetUsers(&routerID)
	if err != nil {
		return err
	}

	devices, err := s.database.GetDevices(&routerID)
	if err != nil {
		return err
	}

	existingQueues, err := client.GetSimpleQueues(ctx)
	if err != nil {
		return err
	}

	queueByName := make(map[string]routeros.SimpleQueue)
	for _, q := range existingQueues {
		queueByName[q.Name] = q
	}

	// Map devices per user
	userIPs := make(map[int][]string)
	for _, dev := range devices {
		if dev.UserID != nil && dev.IPAddress.Valid && dev.IPAddress.String != "" {
			userIPs[*dev.UserID] = append(userIPs[*dev.UserID], dev.IPAddress.String+"/32")
		}
	}

	for _, u := range users {
		ips := userIPs[u.ID]
		qName := fmt.Sprintf("mikroman-%s", u.Name)
		if len(ips) == 0 {
			// No active IPs for user
			if q, exists := queueByName[qName]; exists {
				_ = client.DeleteSimpleQueue(ctx, q.ID)
			}
			continue
		}

		target := strings.Join(ips, ",")
		maxLimit := formatRate(u.SpeedLimit)

		existing, exists := queueByName[qName]
		if !exists {
			newQ := routeros.SimpleQueue{
				Name:     qName,
				Target:   target,
				MaxLimit: maxLimit,
				Disabled: false,
				Comment:  fmt.Sprintf("mikroman:user_%d", u.ID),
			}
			_ = client.CreateSimpleQueue(ctx, &newQ)
		} else if existing.Target != target || existing.MaxLimit != maxLimit {
			_ = client.UpdateSimpleQueue(ctx, existing.ID, map[string]interface{}{
				"target":    target,
				"max-limit": maxLimit,
			})
		}
	}

	// Ensure pause rules safely
	_ = client.EnsurePauseRules(ctx)

	// Reconcile mikroman_blocked address list
	userPaused := make(map[int]bool)
	for _, u := range users {
		if u.IsPaused {
			userPaused[u.ID] = true
		}
	}

	targetBlockedIPs := make(map[string]string)
	for _, dev := range devices {
		if dev.IPAddress.Valid && dev.IPAddress.String != "" {
			ip := dev.IPAddress.String
			if dev.IsPaused || (dev.UserID != nil && userPaused[*dev.UserID]) {
				targetBlockedIPs[ip] = fmt.Sprintf("mikroman:paused:dev_%d", dev.ID)
			}
		}
	}

	currentBlocked, _ := client.GetAddressList(ctx, "mikroman_blocked")
	currentMap := make(map[string]string)
	for _, item := range currentBlocked {
		currentMap[item.Address] = item.ID
	}

	for ip, comment := range targetBlockedIPs {
		if _, exists := currentMap[ip]; !exists {
			_ = client.AddToAddressList(ctx, "mikroman_blocked", ip, comment)
		}
	}

	for ip, id := range currentMap {
		if _, needed := targetBlockedIPs[ip]; !needed {
			_ = client.RemoveFromAddressList(ctx, id)
		}
	}

	return nil
}

func formatRate(limit string) string {
	l := strings.ToLower(strings.TrimSpace(limit))
	if l == "" || l == "unlimited" || l == "default" {
		return "0/0"
	}
	// If already in upload/download format
	if strings.Contains(l, "/") {
		return l
	}
	return fmt.Sprintf("%s/%s", l, l)
}

// SyncCounterRules ensures RouterOS has mangle accounting rules for all accountable devices and self-traffic.
func (s *TrafficService) SyncCounterRules(ctx context.Context, routerID int) error {
	client, err := s.getClient(routerID)
	if err != nil || client == nil {
		return err
	}

	devices, err := s.database.GetDevices(&routerID)
	if err != nil {
		return err
	}

	// Filter accountable devices (not deleted, has non-empty IP)
	var candidates []db.Device
	for _, d := range devices {
		if !d.IsDeleted && d.IPAddress.Valid && strings.TrimSpace(d.IPAddress.String) != "" {
			candidates = append(candidates, d)
		}
	}

	// Deduplicate by IP: deterministic winner (active first, then newer last seen, then lower ID)
	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].IsActive != candidates[j].IsActive {
			return candidates[i].IsActive
		}
		if !candidates[i].LastSeen.Equal(candidates[j].LastSeen) {
			return candidates[i].LastSeen.After(candidates[j].LastSeen)
		}
		return candidates[i].ID < candidates[j].ID
	})

	accountableByIP := make(map[string]db.Device)
	for _, d := range candidates {
		ip := strings.TrimSpace(d.IPAddress.String)
		if _, exists := accountableByIP[ip]; !exists {
			accountableByIP[ip] = d
		}
	}

	// Read monitored WAN interfaces
	monKey := fmt.Sprintf("monitored_interfaces_%d", routerID)
	monVal, _ := s.database.GetSetting(monKey)
	if monVal == "" {
		monVal, _ = s.database.GetSetting("monitored_interfaces_default")
	}
	var monitored []string
	if monVal != "" {
		_ = json.Unmarshal([]byte(monVal), &monitored)
	}
	if monitored == nil {
		monitored = []string{}
	}

	desired := make(map[string]routeros.MangleRule)

	// Router self traffic rules
	for _, wan := range monitored {
		downComment := fmt.Sprintf("mikroman:acct:self:down:%s", wan)
		desired[downComment] = routeros.MangleRule{
			Chain:       "input",
			Action:      "passthrough",
			InInterface: wan,
			Comment:     downComment,
			Disabled:    false,
		}
		upComment := fmt.Sprintf("mikroman:acct:self:up:%s", wan)
		desired[upComment] = routeros.MangleRule{
			Chain:        "output",
			Action:       "passthrough",
			OutInterface: wan,
			Comment:      upComment,
			Disabled:     false,
		}
	}

	// Device rules
	if len(monitored) <= 1 {
		wan := ""
		if len(monitored) == 1 {
			wan = monitored[0]
		}
		for _, dev := range accountableByIP {
			upComment := fmt.Sprintf("mikroman:acct:dev_%d:up", dev.ID)
			desired[upComment] = routeros.MangleRule{
				Chain:        "forward",
				Action:       "passthrough",
				SrcAddress:   dev.IPAddress.String,
				OutInterface: wan,
				Comment:      upComment,
				Disabled:     false,
			}
			downComment := fmt.Sprintf("mikroman:acct:dev_%d:down", dev.ID)
			desired[downComment] = routeros.MangleRule{
				Chain:       "forward",
				Action:      "passthrough",
				DstAddress:  dev.IPAddress.String,
				InInterface: wan,
				Comment:     downComment,
				Disabled:    false,
			}
		}
	} else {
		for _, dev := range accountableByIP {
			for _, wan := range monitored {
				upComment := fmt.Sprintf("mikroman:acct:dev_%d:up:%s", dev.ID, wan)
				desired[upComment] = routeros.MangleRule{
					Chain:        "forward",
					Action:       "passthrough",
					SrcAddress:   dev.IPAddress.String,
					OutInterface: wan,
					Comment:      upComment,
					Disabled:     false,
				}
				downComment := fmt.Sprintf("mikroman:acct:dev_%d:down:%s", dev.ID, wan)
				desired[downComment] = routeros.MangleRule{
					Chain:       "forward",
					Action:      "passthrough",
					DstAddress:  dev.IPAddress.String,
					InInterface: wan,
					Comment:     downComment,
					Disabled:    false,
				}
			}
		}
	}

	rules, err := client.GetMangleRules(ctx)
	if err != nil {
		return err
	}

	existing := make(map[string]routeros.MangleRule)
	for _, r := range rules {
		if strings.HasPrefix(r.Comment, "mikroman:acct:") {
			existing[r.Comment] = r
		}
	}

	// Create or update desired rules
	for comment, spec := range desired {
		ruleCopy := spec
		if existingRule, ok := existing[comment]; !ok {
			if err := client.CreateMangleRule(ctx, &ruleCopy); err != nil {
				slog.Warn("Failed to create mangle rule", "comment", comment, "router_id", routerID, "error", err)
			}
		} else {
			updates := make(map[string]interface{})
			if existingRule.SrcAddress != spec.SrcAddress {
				updates["src-address"] = spec.SrcAddress
			}
			if existingRule.DstAddress != spec.DstAddress {
				updates["dst-address"] = spec.DstAddress
			}
			if existingRule.InInterface != spec.InInterface {
				updates["in-interface"] = spec.InInterface
			}
			if existingRule.OutInterface != spec.OutInterface {
				updates["out-interface"] = spec.OutInterface
			}
			if len(updates) > 0 {
				if err := client.UpdateMangleRule(ctx, existingRule.ID, updates); err != nil {
					slog.Warn("Failed to update mangle rule", "comment", comment, "router_id", routerID, "error", err)
				}
			}
		}
	}

	// Prune rules no longer desired
	for comment, r := range existing {
		if _, needed := desired[comment]; !needed {
			if err := client.DeleteMangleRule(ctx, r.ID); err != nil {
				slog.Warn("Failed to delete mangle rule", "comment", comment, "router_id", routerID, "error", err)
			}
		}
	}

	return nil
}

// AccountingPass computes byte deltas from mangle rules and stores rollups.
func (s *TrafficService) AccountingPass(ctx context.Context, routerID int) error {
	client, err := s.getClient(routerID)
	if err != nil || client == nil {
		return err
	}

	_ = s.SyncCounterRules(ctx, routerID)

	rules, err := client.GetMangleRules(ctx)
	if err != nil {
		return err
	}

	currentMap := make(map[string]int64)
	for _, r := range rules {
		if strings.HasPrefix(r.Comment, "mikroman:acct:") {
			bytesVal := r.Bytes.Int64()
			currentMap[r.Comment] = bytesVal
		}
	}

	s.mu.Lock()
	prevMap := s.prevCounters[routerID]
	if prevMap == nil {
		s.prevCounters[routerID] = currentMap
		s.mu.Unlock()
		return nil
	}
	prevCopy := make(map[string]int64, len(prevMap))
	for k, v := range prevMap {
		prevCopy[k] = v
	}
	s.prevCounters[routerID] = currentMap
	s.mu.Unlock()

	now := time.Now()
	today := now.Format("2006-01-02")
	bucketStart := now.Truncate(15 * time.Minute).Format("2006-01-02 15:04:05")

	for comment, currentBytes := range currentMap {
		prevBytes := prevCopy[comment]
		if currentBytes < prevBytes {
			prevBytes = 0
		}
		delta := currentBytes - prevBytes
		if delta <= 0 {
			continue
		}

		parts := strings.Split(comment, ":")
		if len(parts) >= 4 {
			if strings.HasPrefix(parts[2], "dev_") {
				devIDStr := strings.TrimPrefix(parts[2], "dev_")
				devID, err := strconv.Atoi(devIDStr)
				if err != nil {
					continue
				}

				isUp := parts[3] == "up"
				var bytesIn, bytesOut int64
				if isUp {
					bytesOut = delta
				} else {
					bytesIn = delta
				}

				// Update device_traffic_rollups safely
				res, err := s.database.SqlDB.Exec(`
					UPDATE device_traffic_rollups
					SET bytes_in = bytes_in + ?, bytes_out = bytes_out + ?
					WHERE device_id = ? AND record_date = ?
				`, bytesIn, bytesOut, devID, today)
				if err == nil {
					if aff, _ := res.RowsAffected(); aff == 0 {
						_, _ = s.database.SqlDB.Exec(`
							INSERT INTO device_traffic_rollups (device_id, record_date, bytes_in, bytes_out)
							VALUES (?, ?, ?, ?)
						`, devID, today, bytesIn, bytesOut)
					}
				}

				// Update device_traffic_buckets (15-min)
				bRes, bErr := s.database.SqlDB.Exec(`
					UPDATE device_traffic_buckets
					SET bytes_in = bytes_in + ?, bytes_out = bytes_out + ?
					WHERE device_id = ? AND bucket_start = ?
				`, bytesIn, bytesOut, devID, bucketStart)
				if bErr == nil {
					if aff, _ := bRes.RowsAffected(); aff == 0 {
						_, _ = s.database.SqlDB.Exec(`
							INSERT INTO device_traffic_buckets (device_id, bucket_start, bytes_in, bytes_out)
							VALUES (?, ?, ?, ?)
						`, devID, bucketStart, bytesIn, bytesOut)
					}
				}

				// Attribute to user if assigned
				var userID *int
				_ = s.database.SqlDB.QueryRow("SELECT user_id FROM devices WHERE id = ?", devID).Scan(&userID)
				if userID != nil {
					uRes, uErr := s.database.SqlDB.Exec(`
						UPDATE traffic_rollups
						SET bytes_in = bytes_in + ?, bytes_out = bytes_out + ?
						WHERE user_id = ? AND record_date = ?
					`, bytesIn, bytesOut, *userID, today)
					if uErr == nil {
						if aff, _ := uRes.RowsAffected(); aff == 0 {
							_, _ = s.database.SqlDB.Exec(`
								INSERT INTO traffic_rollups (user_id, record_date, bytes_in, bytes_out)
								VALUES (?, ?, ?, ?)
							`, *userID, today, bytesIn, bytesOut)
						}
					}

					ubRes, ubErr := s.database.SqlDB.Exec(`
						UPDATE user_traffic_buckets
						SET bytes_in = bytes_in + ?, bytes_out = bytes_out + ?
						WHERE user_id = ? AND bucket_start = ?
					`, bytesIn, bytesOut, *userID, bucketStart)
					if ubErr == nil {
						if aff, _ := ubRes.RowsAffected(); aff == 0 {
							_, _ = s.database.SqlDB.Exec(`
								INSERT INTO user_traffic_buckets (user_id, bucket_start, bytes_in, bytes_out)
								VALUES (?, ?, ?, ?)
							`, *userID, bucketStart, bytesIn, bytesOut)
						}
					}
				}
			} else if parts[2] == "self" {
				isUp := parts[3] == "up"
				var bytesIn, bytesOut int64
				if isUp {
					bytesOut = delta
				} else {
					bytesIn = delta
				}

				sRes, sErr := s.database.SqlDB.Exec(`
					UPDATE router_self_traffic_rollups
					SET bytes_in = bytes_in + ?, bytes_out = bytes_out + ?
					WHERE router_id = ? AND record_date = ?
				`, bytesIn, bytesOut, routerID, today)
				if sErr == nil {
					if aff, _ := sRes.RowsAffected(); aff == 0 {
						_, _ = s.database.SqlDB.Exec(`
							INSERT INTO router_self_traffic_rollups (router_id, record_date, bytes_in, bytes_out)
							VALUES (?, ?, ?, ?)
						`, routerID, today, bytesIn, bytesOut)
					}
				}
			}
		}
	}

	return nil
}

func (s *TrafficService) StartBackgroundLoop(ctx context.Context, interval time.Duration) {
	runPass := func() {
		routers, err := s.database.GetRouters()
		if err != nil {
			return
		}
		for _, r := range routers {
			if r.IsActive {
				tCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
				_ = s.SyncCounterRules(tCtx, r.ID)
				_ = s.ReconcileQueues(tCtx, r.ID)
				_ = s.AccountingPass(tCtx, r.ID)
				cancel()
			}
		}
		slog.Debug("Completed traffic accounting pass")
	}

	go func() {
		// Run immediate reconciliation and counter setup on startup
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

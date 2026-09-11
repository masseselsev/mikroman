package services

import (
	"context"
	"fmt"
	"log/slog"
	"strconv"
	"strings"
	"time"

	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

type TrafficService struct {
	database *db.DB
	client   *routeros.Client

	// Previous counter state: routerID -> ruleID -> cumulativeBytes
	prevCounters map[int]map[string]int64
}

func NewTrafficService(database *db.DB, client *routeros.Client) *TrafficService {
	return &TrafficService{
		database:     database,
		client:       client,
		prevCounters: make(map[int]map[string]int64),
	}
}

// ReconcileQueues ensures Simple Queues match active user limits and assignments.
func (s *TrafficService) ReconcileQueues(ctx context.Context, routerID int) error {
	if s.client == nil {
		return nil
	}

	users, err := s.database.GetUsers(&routerID)
	if err != nil {
		return err
	}

	devices, err := s.database.GetDevices(&routerID)
	if err != nil {
		return err
	}

	existingQueues, err := s.client.GetSimpleQueues(ctx)
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
				_ = s.client.DeleteSimpleQueue(ctx, q.ID)
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
				Disabled: "false",
				Comment:  fmt.Sprintf("mikroman:user_%d", u.ID),
			}
			_ = s.client.CreateSimpleQueue(ctx, &newQ)
		} else if existing.Target != target || existing.MaxLimit != maxLimit {
			_ = s.client.UpdateSimpleQueue(ctx, existing.ID, map[string]interface{}{
				"target":    target,
				"max-limit": maxLimit,
			})
		}
	}

	// Ensure pause rules safely
	_ = s.client.EnsurePauseRules(ctx)

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

	currentBlocked, _ := s.client.GetAddressList(ctx, "mikroman_blocked")
	currentMap := make(map[string]string)
	for _, item := range currentBlocked {
		currentMap[item.Address] = item.ID
	}

	for ip, comment := range targetBlockedIPs {
		if _, exists := currentMap[ip]; !exists {
			_ = s.client.AddToAddressList(ctx, "mikroman_blocked", ip, comment)
		}
	}

	for ip, id := range currentMap {
		if _, needed := targetBlockedIPs[ip]; !needed {
			_ = s.client.RemoveFromAddressList(ctx, id)
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

// AccountingPass computes byte deltas from mangle rules and stores rollups.
func (s *TrafficService) AccountingPass(ctx context.Context, routerID int) error {
	if s.client == nil {
		return nil
	}

	rules, err := s.client.GetMangleRules(ctx)
	if err != nil {
		return err
	}

	currentMap := make(map[string]int64)
	for _, r := range rules {
		if strings.HasPrefix(r.Comment, "mikroman:acct:") {
			bytesVal, _ := strconv.ParseInt(r.Bytes, 10, 64)
			currentMap[r.Comment] = bytesVal
		}
	}

	prevMap := s.prevCounters[routerID]
	if prevMap == nil {
		// First pass: seed baseline without bogus deltas
		s.prevCounters[routerID] = currentMap
		return nil
	}

	today := time.Now().Format("2006-01-02")

	for comment, currentBytes := range currentMap {
		prevBytes := prevMap[comment]
		if currentBytes < prevBytes {
			// Counter reset (reboot)
			prevBytes = 0
		}
		delta := currentBytes - prevBytes
		if delta <= 0 {
			continue
		}

		// Comment formats:
		// mikroman:acct:dev_<devID>:up
		// mikroman:acct:dev_<devID>:down
		parts := strings.Split(comment, ":")
		if len(parts) >= 4 && strings.HasPrefix(parts[2], "dev_") {
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

			// Update device_traffic_rollups
			_, _ = s.database.SqlDB.Exec(`
				INSERT INTO device_traffic_rollups (device_id, record_date, bytes_in, bytes_out)
				VALUES (?, ?, ?, ?)
				ON CONFLICT(device_id, record_date) DO UPDATE SET
					bytes_in = bytes_in + excluded.bytes_in,
					bytes_out = bytes_out + excluded.bytes_out;
			`, devID, today, bytesIn, bytesOut)

			// Attribute to owning user if exists
			var userID *int
			_ = s.database.SqlDB.QueryRow("SELECT user_id FROM devices WHERE id = ?", devID).Scan(&userID)
			if userID != nil {
				_, _ = s.database.SqlDB.Exec(`
					INSERT INTO traffic_rollups (user_id, record_date, bytes_in, bytes_out)
					VALUES (?, ?, ?, ?)
					ON CONFLICT(user_id, record_date) DO UPDATE SET
						bytes_in = bytes_in + excluded.bytes_in,
						bytes_out = bytes_out + excluded.bytes_out;
				`, *userID, today, bytesIn, bytesOut)
			}
		}
	}

	s.prevCounters[routerID] = currentMap
	return nil
}

func (s *TrafficService) StartBackgroundLoop(ctx context.Context, interval time.Duration) {
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
						tCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
						_ = s.ReconcileQueues(tCtx, r.ID)
						_ = s.AccountingPass(tCtx, r.ID)
						cancel()
					}
				}
				slog.Debug("Completed traffic accounting pass")
			}
		}
	}()
}

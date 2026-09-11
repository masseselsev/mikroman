package services

import (
	"context"
	"log/slog"
	"strconv"
	"time"

	"github.com/masseselsev/mikroman/internal/api"
	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

type TelemetryService struct {
	database *db.DB
	client   *routeros.Client
	hub      *api.Hub
}

func NewTelemetryService(database *db.DB, client *routeros.Client, hub *api.Hub) *TelemetryService {
	return &TelemetryService{
		database: database,
		client:   client,
		hub:      hub,
	}
}

func (s *TelemetryService) Collect(ctx context.Context, routerID int) error {
	if s.client == nil {
		return nil
	}

	res, err := s.client.GetSystemResource(ctx)
	if err != nil {
		return err
	}

	cpuLoad, _ := strconv.ParseFloat(res.CPULoad, 64)
	freeMem, _ := strconv.ParseInt(res.FreeMemory, 10, 64)
	totalMem, _ := strconv.ParseInt(res.TotalMemory, 10, 64)

	var memPct float64
	var usedMem int64
	if totalMem > 0 {
		usedMem = totalMem - freeMem
		memPct = (float64(usedMem) / float64(totalMem)) * 100.0
	}

	// Insert into system_metrics
	_, _ = s.database.SqlDB.Exec(`
		INSERT INTO system_metrics (router_id, cpu_load, memory_used_bytes, memory_total_bytes, memory_usage_pct, timestamp)
		VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
	`, routerID, cpuLoad, usedMem, totalMem, memPct)

	// Fetch interfaces
	ifaces, _ := s.client.GetInterfaces(ctx)
	for _, iface := range ifaces {
		rx, _ := strconv.ParseInt(iface.RxByte, 10, 64)
		tx, _ := strconv.ParseInt(iface.TxByte, 10, 64)

		_, _ = s.database.SqlDB.Exec(`
			INSERT INTO interface_metrics (router_id, interface_name, rx_bytes_total, tx_bytes_total, timestamp)
			VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
		`, routerID, iface.Name, rx, tx)
	}

	// Broadcast to frontend
	if s.hub != nil {
		s.hub.Broadcast(map[string]interface{}{
			"type":      "telemetry_tick",
			"router_id": routerID,
			"cpu":       cpuLoad,
			"mem_pct":   memPct,
			"uptime":    res.Uptime,
		})
	}

	return nil
}

func (s *TelemetryService) StartBackgroundLoop(ctx context.Context, interval time.Duration) {
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
						callCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
						if err := s.Collect(callCtx, r.ID); err != nil {
							slog.Debug("Telemetry collection failed", "router_id", r.ID, "err", err)
						}
						cancel()
					}
				}
			}
		}
	}()
}

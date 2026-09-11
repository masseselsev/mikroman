package services

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/masseselsev/mikroman/internal/api"
	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

type TelemetryService struct {
	database        *db.DB
	client          *routeros.Client
	hub             *api.Hub
	mu              sync.Mutex
	prevIfaces      map[string][2]int64
	prevTime        time.Time
	prevDeviceBytes map[int][2]int64
	prevMangleTime  time.Time
}

func NewTelemetryService(database *db.DB, client *routeros.Client, hub *api.Hub) *TelemetryService {
	return &TelemetryService{
		database:        database,
		client:          client,
		hub:             hub,
		prevIfaces:      make(map[string][2]int64),
		prevDeviceBytes: make(map[int][2]int64),
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
	now := time.Now()

	s.mu.Lock()
	dt := now.Sub(s.prevTime).Seconds()
	currentIfaces := make(map[string][2]int64)

	for _, iface := range ifaces {
		rx, _ := strconv.ParseInt(iface.RxByte, 10, 64)
		tx, _ := strconv.ParseInt(iface.TxByte, 10, 64)
		currentIfaces[iface.Name] = [2]int64{rx, tx}

		_, _ = s.database.SqlDB.Exec(`
			INSERT INTO interface_metrics (router_id, interface_name, rx_bytes_total, tx_bytes_total, timestamp)
			VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
		`, routerID, iface.Name, rx, tx)
	}

	// Read monitored interfaces configuration
	monKey := fmt.Sprintf("monitored_interfaces_%d", routerID)
	monVal, _ := s.database.GetSetting(monKey)
	if monVal == "" {
		monVal, _ = s.database.GetSetting("monitored_interfaces_default")
	}
	var monitoredList []string
	if monVal != "" {
		_ = json.Unmarshal([]byte(monVal), &monitoredList)
	}
	if monitoredList == nil {
		monitoredList = []string{}
	}

	// Calculate WAN rates across monitored interfaces
	var wanRxBps, wanTxBps float64
	if len(monitoredList) > 0 {
		rates, err := s.client.MonitorInterfaceTraffic(ctx, monitoredList)
		if err == nil && len(rates) > 0 {
			for _, r := range rates {
				wanRxBps += r.RxBitsPerSecond
				wanTxBps += r.TxBitsPerSecond
			}
		} else if dt > 0.1 && len(s.prevIfaces) > 0 {
			monMap := make(map[string]bool)
			for _, m := range monitoredList {
				monMap[m] = true
			}
			for name, curr := range currentIfaces {
				if monMap[name] {
					if prev, ok := s.prevIfaces[name]; ok {
						dRx := curr[0] - prev[0]
						dTx := curr[1] - prev[1]
						if dRx > 0 {
							wanRxBps += float64(dRx*8) / dt
						}
						if dTx > 0 {
							wanTxBps += float64(dTx*8) / dt
						}
					}
				}
			}
		}
	}

	s.prevIfaces = currentIfaces
	s.prevTime = now
	s.mu.Unlock()

	// Resolve WAN IP & Public IP
	var wanIP string
	ipAddrs, _ := s.client.GetIPAddresses(ctx)
	for _, entry := range ipAddrs {
		clean := strings.Split(entry.Address, "/")[0]
		if clean == "" || strings.HasPrefix(clean, "127.") {
			continue
		}
		if len(monitoredList) > 0 {
			for _, m := range monitoredList {
				if entry.Interface == m {
					wanIP = clean
					break
				}
			}
		}
		if wanIP != "" {
			break
		}
		if wanIP == "" {
			wanIP = clean
		}
	}
	publicIP, _ := s.client.GetCloudPublicAddress(ctx)
	clockStr := now.Format("15:04:05")

	// Differentiate mangle rules for live per-device rates
	mangleRules, _ := s.client.GetMangleRules(ctx)
	currentDevBytes := make(map[int][2]int64)
	for _, r := range mangleRules {
		if !strings.HasPrefix(r.Comment, "mikroman:acct:dev_") {
			continue
		}
		parts := strings.Split(r.Comment, ":")
		if len(parts) >= 4 {
			devIDStr := strings.TrimPrefix(parts[2], "dev_")
			devID, err := strconv.Atoi(devIDStr)
			if err != nil {
				continue
			}
			bVal, _ := strconv.ParseInt(r.Bytes, 10, 64)
			cur := currentDevBytes[devID]
			if parts[3] == "up" {
				cur[1] += bVal
			} else if parts[3] == "down" {
				cur[0] += bVal
			}
			currentDevBytes[devID] = cur
		}
	}

	devRates := make(map[int][2]int64)
	s.mu.Lock()
	mDt := now.Sub(s.prevMangleTime).Seconds()
	if mDt > 0.1 && s.prevDeviceBytes != nil && len(s.prevDeviceBytes) > 0 {
		for devID, cur := range currentDevBytes {
			prev, ok := s.prevDeviceBytes[devID]
			if !ok {
				continue
			}
			dDown := cur[0] - prev[0]
			if dDown < 0 {
				dDown = cur[0]
			}
			dUp := cur[1] - prev[1]
			if dUp < 0 {
				dUp = cur[1]
			}
			rxBps := int64(float64(dDown*8) / mDt)
			txBps := int64(float64(dUp*8) / mDt)
			if rxBps < 0 {
				rxBps = 0
			}
			if txBps < 0 {
				txBps = 0
			}
			devRates[devID] = [2]int64{rxBps, txBps}
		}
	}
	s.prevDeviceBytes = currentDevBytes
	s.prevMangleTime = now
	s.mu.Unlock()

	// Fetch health & hardware
	health, _ := s.client.GetSystemHealth(ctx)
	rb, _ := s.client.GetRouterBoard(ctx)

	var temp, volt *float64
	for _, item := range health {
		if strings.EqualFold(item.Name, "temperature") {
			if v, err := strconv.ParseFloat(item.Value, 64); err == nil {
				temp = &v
			}
		}
		if strings.EqualFold(item.Name, "voltage") {
			if v, err := strconv.ParseFloat(item.Value, 64); err == nil {
				volt = &v
			}
		}
	}

	rbModel := ""
	rbSerial := ""
	if rb != nil {
		rbModel = rb.Model
		rbSerial = rb.SerialNumber
	}

	cpuCount, _ := strconv.Atoi(res.CPUCount)
	cpuFreq, _ := strconv.Atoi(res.CPUFrequency)

	// Fetch users and devices for active router
	users, _ := s.database.GetUsers(&routerID)
	devices, _ := s.database.GetDevices(&routerID)

	todayStr := now.Format("2006-01-02")
	anchorDay := s.database.GetBillingAnchorDay(&routerID)
	cycleStart := db.CalculateBillingCycleStart(anchorDay, now)
	devStats, _ := s.database.GetDeviceVolumeStats(cycleStart, todayStr)
	userStats, _ := s.database.GetUserVolumeStats(cycleStart, todayStr)

	activeCount := 0
	devsByUser := make(map[int][]db.Device)
	for _, dev := range devices {
		if dev.IsActive {
			activeCount++
		}
		if dev.UserID != nil {
			devsByUser[*dev.UserID] = append(devsByUser[*dev.UserID], dev)
		}
	}

	type UserTickDTO struct {
		UserID            int                               `json:"user_id"`
		Name              string                            `json:"name"`
		AvatarIcon        string                            `json:"avatar_icon"`
		SpeedLimit        string                            `json:"speed_limit"`
		IsPaused          bool                              `json:"is_paused"`
		DeviceCount       int                               `json:"device_count"`
		ActiveDeviceCount int                               `json:"active_device_count"`
		CurrentRateIn     int64                             `json:"current_rate_in"`
		CurrentRateOut    int64                             `json:"current_rate_out"`
		BytesIn           int64                             `json:"bytes_in"`
		BytesOut          int64                             `json:"bytes_out"`
		Devices           map[string]map[string]interface{} `json:"devices"`
	}

	usersPayload := make([]UserTickDTO, 0, len(users))
	for _, u := range users {
		owned := devsByUser[u.ID]
		activeDevs := 0
		devMap := make(map[string]map[string]interface{})
		var uTodayIn, uTodayOut int64
		var userRxBps, userTxBps int64

		for _, d := range owned {
			if d.IsActive {
				activeDevs++
			}
			st := devStats[d.ID]
			rate := devRates[d.ID]
			devMap[strconv.Itoa(d.ID)] = map[string]interface{}{
				"current_rate_in":  rate[0],
				"current_rate_out": rate[1],
				"bytes_today_in":   st.TodayIn,
				"bytes_today_out":  st.TodayOut,
			}
			userRxBps += rate[0]
			userTxBps += rate[1]
			uTodayIn += st.TodayIn
			uTodayOut += st.TodayOut
		}

		if len(owned) == 0 {
			if st, ok := userStats[u.ID]; ok {
				uTodayIn = st.TodayIn
				uTodayOut = st.TodayOut
			}
		}

		usersPayload = append(usersPayload, UserTickDTO{
			UserID:            u.ID,
			Name:              u.Name,
			AvatarIcon:        u.AvatarIcon,
			SpeedLimit:        u.SpeedLimit,
			IsPaused:          u.IsPaused,
			DeviceCount:       len(owned),
			ActiveDeviceCount: activeDevs,
			CurrentRateIn:     userRxBps,
			CurrentRateOut:    userTxBps,
			BytesIn:           uTodayIn,
			BytesOut:          uTodayOut,
			Devices:           devMap,
		})
	}

	// Broadcast full structure to frontend
	if s.hub != nil {
		s.hub.Broadcast(map[string]interface{}{
			"type":      "telemetry_tick",
			"timestamp": float64(now.Unix()),
			"router": map[string]interface{}{
				"board_name":           res.BoardName,
				"version":              res.Version,
				"cpu_load":             cpuLoad,
				"cpu_model":            res.BoardName,
				"cpu_model_exact":      true,
				"cpu_platform":         res.Platform,
				"cpu_arch":             res.ArchitectureName,
				"cpu_count":            cpuCount,
				"cpu_frequency_mhz":    cpuFreq,
				"routerboard_model":    rbModel,
				"routerboard_serial":   rbSerial,
				"free_memory_mb":       float64(freeMem) / (1024 * 1024),
				"total_memory_mb":      float64(totalMem) / (1024 * 1024),
				"temperature":          temp,
				"voltage":              volt,
				"uptime":               res.Uptime,
				"wan_rx_bps":           wanRxBps,
				"wan_tx_bps":           wanTxBps,
				"wan_ip":               wanIP,
				"public_ip":            publicIP,
				"clock":                clockStr,
				"monitored_interfaces": monitoredList,
				"user_count":           len(users),
				"client_device_count":  len(devices),
				"active_clients":       activeCount,
			},
			"users": usersPayload,
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

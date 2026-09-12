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

	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

// EventBroadcaster allows decoupling telemetry from WebSocket hub implementations.
type EventBroadcaster interface {
	Broadcast(event interface{})
	BroadcastRouter(routerID int, isDefault bool, event interface{})
}

// LiveRateSnapshot stores the latest sampled rates for users and devices on a router.
type LiveRateSnapshot struct {
	UserRates   map[int][2]int64 // userID -> [2]int64{rxBps, txBps}
	DeviceRates map[int][2]int64 // deviceID -> [2]int64{rxBps, txBps}
}

type TelemetryService struct {
	database        *db.DB
	client          *routeros.Client
	hub             EventBroadcaster
	mu              sync.Mutex
	clients         map[int]*routeros.Client
	prevIfaces      map[int]map[string][2]int64
	prevTime        map[int]time.Time
	prevDeviceBytes map[int]map[int][2]int64
	prevMangleTime  map[int]time.Time
	latestRates     map[int]LiveRateSnapshot
}

func NewTelemetryService(database *db.DB, client *routeros.Client, hub EventBroadcaster) *TelemetryService {
	clients := make(map[int]*routeros.Client)
	if client != nil {
		if def, err := database.GetDefaultRouter(); err == nil && def != nil {
			clients[def.ID] = client
		}
	}
	return &TelemetryService{
		database:        database,
		client:          client,
		hub:             hub,
		clients:         clients,
		prevIfaces:      make(map[int]map[string][2]int64),
		prevTime:        make(map[int]time.Time),
		prevDeviceBytes: make(map[int]map[int][2]int64),
		prevMangleTime:  make(map[int]time.Time),
		latestRates:     make(map[int]LiveRateSnapshot),
	}
}

// GetLatestRates returns the latest user and device live rates for a router.
func (s *TelemetryService) GetLatestRates(routerID int) (map[int][2]int64, map[int][2]int64) {
	s.mu.Lock()
	defer s.mu.Unlock()

	snap, ok := s.latestRates[routerID]
	if !ok {
		return nil, nil
	}

	uRates := make(map[int][2]int64, len(snap.UserRates))
	for k, v := range snap.UserRates {
		uRates[k] = v
	}
	dRates := make(map[int][2]int64, len(snap.DeviceRates))
	for k, v := range snap.DeviceRates {
		dRates[k] = v
	}
	return uRates, dRates
}

func (s *TelemetryService) getClient(routerID int) (*routeros.Client, error) {
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

func (s *TelemetryService) Collect(ctx context.Context, routerID int) error {
	client, err := s.getClient(routerID)
	if err != nil || client == nil {
		return err
	}

	res, err := client.GetSystemResource(ctx)
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

	// Fetch health early so temperature & voltage are saved with system_metrics
	health, _ := client.GetSystemHealth(ctx)
	temp, volt := routeros.ExtractHealthMetrics(health)

	// Insert into system_metrics
	_, _ = s.database.SqlDB.Exec(`
		INSERT INTO system_metrics (router_id, cpu_load, memory_used_bytes, memory_total_bytes, memory_usage_pct, temperature, voltage, timestamp)
		VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
	`, routerID, cpuLoad, usedMem, totalMem, memPct, temp, volt)

	// Fetch interfaces
	ifaces, _ := client.GetInterfaces(ctx)
	now := time.Now()

	s.mu.Lock()
	pTime := s.prevTime[routerID]
	var dt float64
	if !pTime.IsZero() {
		dt = now.Sub(pTime).Seconds()
	}
	currentIfaces := make(map[string][2]int64)

	for _, iface := range ifaces {
		rx, _ := strconv.ParseInt(iface.RxByte, 10, 64)
		tx, _ := strconv.ParseInt(iface.TxByte, 10, 64)
		currentIfaces[iface.Name] = [2]int64{rx, tx}
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

	pIfaces := s.prevIfaces[routerID]
	ifacesRatesMap := make(map[string][2]float64)

	// Fetch live rates from RouterOS monitor-traffic for monitored interfaces
	if len(monitoredList) > 0 {
		rates, err := client.MonitorInterfaceTraffic(ctx, monitoredList)
		if err == nil && len(rates) > 0 {
			for _, r := range rates {
				ifacesRatesMap[r.Name] = [2]float64{r.RxBitsPerSecond, r.TxBitsPerSecond}
			}
		}
	}

	// For any interface without monitor-traffic, compute from byte deltas
	if dt > 0.1 && len(pIfaces) > 0 {
		for name, curr := range currentIfaces {
			if _, ok := ifacesRatesMap[name]; !ok {
				if prev, exists := pIfaces[name]; exists {
					dRx := curr[0] - prev[0]
					dTx := curr[1] - prev[1]
					var rRx, rTx float64
					if dRx > 0 {
						rRx = float64(dRx*8) / dt
					}
					if dTx > 0 {
						rTx = float64(dTx*8) / dt
					}
					ifacesRatesMap[name] = [2]float64{rRx, rTx}
				}
			}
		}
	}

	// Calculate WAN rates across monitored interfaces & record interface_metrics
	var wanRxBps, wanTxBps float64
	monMap := make(map[string]bool)
	for _, m := range monitoredList {
		monMap[m] = true
	}

	for _, iface := range ifaces {
		rx, _ := strconv.ParseInt(iface.RxByte, 10, 64)
		tx, _ := strconv.ParseInt(iface.TxByte, 10, 64)
		rate := ifacesRatesMap[iface.Name]

		if monMap[iface.Name] {
			wanRxBps += rate[0]
			wanTxBps += rate[1]
		}

		_, _ = s.database.SqlDB.Exec(`
			INSERT INTO interface_metrics (router_id, interface_name, rx_rate_bps, tx_rate_bps, rx_bytes_total, tx_bytes_total, timestamp)
			VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
		`, routerID, iface.Name, rate[0], rate[1], rx, tx)
	}

	if s.prevIfaces == nil {
		s.prevIfaces = make(map[int]map[string][2]int64)
	}
	s.prevIfaces[routerID] = currentIfaces
	if s.prevTime == nil {
		s.prevTime = make(map[int]time.Time)
	}
	s.prevTime[routerID] = now
	s.mu.Unlock()

	// Resolve WAN IP & Public IP
	var wanIP string
	ipAddrs, _ := client.GetIPAddresses(ctx)
	if len(monitoredList) > 0 {
		for _, entry := range ipAddrs {
			clean := strings.TrimSpace(strings.Split(entry.Address, "/")[0])
			if clean == "" || strings.HasPrefix(clean, "127.") {
				continue
			}
			for _, m := range monitoredList {
				if entry.Interface == m {
					wanIP = clean
					break
				}
			}
			if wanIP != "" {
				break
			}
		}
	}
	if wanIP == "" {
		for _, entry := range ipAddrs {
			clean := strings.TrimSpace(strings.Split(entry.Address, "/")[0])
			if clean == "" || strings.HasPrefix(clean, "127.") {
				continue
			}
			ifaceLower := strings.ToLower(entry.Interface)
			if strings.Contains(ifaceLower, "bridge") || strings.Contains(ifaceLower, "veth") || strings.Contains(ifaceLower, "docker") {
				continue
			}
			wanIP = clean
			break
		}
		if wanIP == "" && len(ipAddrs) > 0 {
			for _, entry := range ipAddrs {
				clean := strings.TrimSpace(strings.Split(entry.Address, "/")[0])
				if clean != "" && !strings.HasPrefix(clean, "127.") {
					wanIP = clean
					break
				}
			}
		}
	}
	publicIP, _ := client.GetCloudPublicAddress(ctx)
	clockStr := now.Format("15:04:05")

	// Differentiate mangle rules for live per-device rates
	mangleRules, _ := client.GetMangleRules(ctx)
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
			bVal := r.Bytes.Int64()
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
	prevDevs := s.prevDeviceBytes[routerID]
	pMTime := s.prevMangleTime[routerID]
	var mDt float64
	if !pMTime.IsZero() {
		mDt = now.Sub(pMTime).Seconds()
	}
	if mDt > 0.1 && prevDevs != nil && len(prevDevs) > 0 {
		for devID, cur := range currentDevBytes {
			prev, ok := prevDevs[devID]
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
	if s.prevDeviceBytes == nil {
		s.prevDeviceBytes = make(map[int]map[int][2]int64)
	}
	s.prevDeviceBytes[routerID] = currentDevBytes
	if s.prevMangleTime == nil {
		s.prevMangleTime = make(map[int]time.Time)
	}
	s.prevMangleTime[routerID] = now
	s.mu.Unlock()

	// Fetch hardware details
	rb, _ := client.GetRouterBoard(ctx)

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

	// Save snapshot of current rates for REST endpoints
	userRatesSnapshot := make(map[int][2]int64, len(usersPayload))
	for _, up := range usersPayload {
		userRatesSnapshot[up.UserID] = [2]int64{up.CurrentRateIn, up.CurrentRateOut}
	}
	s.mu.Lock()
	if s.latestRates == nil {
		s.latestRates = make(map[int]LiveRateSnapshot)
	}
	s.latestRates[routerID] = LiveRateSnapshot{
		UserRates:   userRatesSnapshot,
		DeviceRates: devRates,
	}
	s.mu.Unlock()

	// Broadcast router-scoped structure to frontend
	if s.hub != nil {
		defaultRouter, _ := s.database.GetDefaultRouter()
		isDefault := (defaultRouter != nil && defaultRouter.ID == routerID)
		cpuIdent := ResolveCPUIdentity(rbModel, res.BoardName, res.Platform, res.ArchitectureName, res.ArchitectureName)
		s.hub.BroadcastRouter(routerID, isDefault, map[string]interface{}{
			"type":      "telemetry_tick",
			"timestamp": float64(now.Unix()),
			"router": map[string]interface{}{
				"id":                   routerID,
				"board_name":           res.BoardName,
				"version":              res.Version,
				"cpu_load":             cpuLoad,
				"cpu_model":            cpuIdent.Model,
				"cpu_model_exact":      cpuIdent.Exact,
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

func (s *TelemetryService) getInterval(defaultInterval time.Duration) time.Duration {
	val, err := s.database.GetSetting("telemetry_interval_seconds")
	if err == nil && val != "" {
		if sec, err := strconv.ParseFloat(val, 64); err == nil && sec >= 1 && sec <= 60 {
			return time.Duration(sec * float64(time.Second))
		}
	}
	if defaultInterval > 0 && defaultInterval < 10*time.Second {
		return defaultInterval
	}
	return 3 * time.Second
}

func (s *TelemetryService) StartBackgroundLoop(ctx context.Context, defaultInterval time.Duration) {
	go func() {
		// Run initial collection immediately
		routers, err := s.database.GetRouters()
		if err == nil {
			for _, r := range routers {
				if r.IsActive {
					callCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
					if err := s.Collect(callCtx, r.ID); err != nil {
						slog.Debug("Initial telemetry collection failed", "router_id", r.ID, "err", err)
					}
					cancel()
				}
			}
		}

		for {
			interval := s.getInterval(defaultInterval)
			select {
			case <-ctx.Done():
				return
			case <-time.After(interval):
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

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
	pubNet          *PublicNetworkService
	cachedRB        map[int]*routeros.RouterBoard
	cachedRBAt      map[int]time.Time
	cachedIPAddrs   map[int][]routeros.IPAddress
	cachedIPAddrsAt map[int]time.Time
	cachedPublicIP  map[int]string
	cachedPubIPAt   map[int]time.Time
	cachedHealth    map[int][]routeros.HealthItem
	cachedHealthAt  map[int]time.Time
	cachedDevStats  map[int]map[int]db.VolumeStats
	cachedUserStats map[int]map[int]db.VolumeStats
	cachedStatsAt   map[int]time.Time
	lastMetricsSave map[int]time.Time
}

func NewTelemetryService(database *db.DB, client *routeros.Client, hub EventBroadcaster) *TelemetryService {
	clients := make(map[int]*routeros.Client)
	if client != nil {
		if def, err := database.GetDefaultRouter(); err == nil && def != nil {
			clients[def.ID] = client
		} else if routers, err := database.GetRouters(); err == nil && len(routers) == 1 {
			clients[routers[0].ID] = client
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
		pubNet:          NewPublicNetworkService(),
		cachedRB:        make(map[int]*routeros.RouterBoard),
		cachedRBAt:      make(map[int]time.Time),
		cachedIPAddrs:   make(map[int][]routeros.IPAddress),
		cachedIPAddrsAt: make(map[int]time.Time),
		cachedPublicIP:  make(map[int]string),
		cachedPubIPAt:   make(map[int]time.Time),
		cachedHealth:    make(map[int][]routeros.HealthItem),
		cachedHealthAt:  make(map[int]time.Time),
		cachedDevStats:  make(map[int]map[int]db.VolumeStats),
		cachedUserStats: make(map[int]map[int]db.VolumeStats),
		cachedStatsAt:   make(map[int]time.Time),
		lastMetricsSave: make(map[int]time.Time),
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

	router, err := s.database.GetRouter(routerID)
	if err != nil {
		return nil, err
	}
	if router == nil {
		return nil, fmt.Errorf("router %d not found", routerID)
	}

	if s.client != nil && s.client.Matches(router.Host, router.Port) {
		s.clients[routerID] = s.client
		return s.client, nil
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

func (s *TelemetryService) saveMetricsAndRollups(routerID int, now time.Time, res *routeros.Resource, health []routeros.HealthItem, ifaces []routeros.Interface) {
	if res == nil {
		return
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
	temp, volt := routeros.ExtractHealthMetrics(health)

	s.mu.Lock()
	pTime := s.prevTime[routerID]
	pIfaces := make(map[string][2]int64)
	if existing, ok := s.prevIfaces[routerID]; ok {
		for k, v := range existing {
			pIfaces[k] = v
		}
	}
	s.mu.Unlock()

	var dt float64
	if !pTime.IsZero() {
		dt = now.Sub(pTime).Seconds()
	}
	currentIfaces := make(map[string][2]int64)

	tx, err := s.database.SqlDB.Begin()
	if err != nil {
		return
	}
	defer tx.Rollback()

	_, _ = tx.Exec(`
		INSERT INTO system_metrics (router_id, cpu_load, memory_used_bytes, memory_total_bytes, memory_usage_pct, temperature, voltage, timestamp)
		VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
	`, routerID, cpuLoad, usedMem, totalMem, memPct, temp, volt)

	todayStr := now.Format("2006-01-02")
	for _, iface := range ifaces {
		rx, _ := strconv.ParseInt(iface.RxByte, 10, 64)
		txBytes, _ := strconv.ParseInt(iface.TxByte, 10, 64)
		currentIfaces[iface.Name] = [2]int64{rx, txBytes}

		var rRx, rTx float64
		if dt > 0.1 {
			if prev, exists := pIfaces[iface.Name]; exists {
				dRx := rx - prev[0]
				dTx := txBytes - prev[1]
				if dRx > 0 {
					rRx = float64(dRx*8) / dt
				}
				if dTx > 0 {
					rTx = float64(dTx*8) / dt
				}
				if dRx > 0 || dTx > 0 {
					_, _ = tx.Exec(`
						INSERT INTO interface_traffic_rollups (router_id, interface_name, record_date, bytes_in, bytes_out)
						VALUES (?, ?, ?, ?, ?)
						ON CONFLICT(router_id, interface_name, record_date) DO UPDATE SET
							bytes_in = bytes_in + excluded.bytes_in,
							bytes_out = bytes_out + excluded.bytes_out
					`, routerID, iface.Name, todayStr, dRx, dTx)
				}
			}
		}

		_, _ = tx.Exec(`
		INSERT INTO interface_metrics (router_id, interface_name, rx_rate_bps, tx_rate_bps, rx_bytes_total, tx_bytes_total, timestamp)
		VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
	`, routerID, iface.Name, rRx, rTx, rx, txBytes)
	}

	// Refresh the 15-minute buckets for the window this tick's samples landed in, inside
	// the same transaction so a raw sample and the bucket describing it commit together.
	// The upsert recomputes the whole bucket from the raw rows of its quarter hour, which
	// keeps it idempotent under the 10 s tick: re-running it converges on the same row
	// instead of adding a second contribution. Both buckets of the sample window are
	// covered because a tick that straddles a boundary has to close the earlier one.
	refreshFrom := db.MetricBucketEpoch(now.Add(-db.MetricCollectionCadence))
	refreshUntil := db.MetricBucketEpoch(now) + db.MetricBucketSeconds
	_ = db.ComposeMetricBuckets(tx, db.MetricBucketWindow{
		RouterID:   &routerID,
		FromEpoch:  refreshFrom,
		UntilEpoch: refreshUntil,
	})

	_ = tx.Commit()

	s.mu.Lock()
	if s.prevIfaces == nil {
		s.prevIfaces = make(map[int]map[string][2]int64)
	}
	s.prevIfaces[routerID] = currentIfaces
	if s.prevTime == nil {
		s.prevTime = make(map[int]time.Time)
	}
	s.prevTime[routerID] = now
	s.mu.Unlock()
}

func (s *TelemetryService) Collect(ctx context.Context, routerID int) error {
	client, err := s.getClient(routerID)
	if err != nil || client == nil {
		return err
	}

	now := time.Now()

	s.mu.Lock()
	cachedHealth := s.cachedHealth[routerID]
	cachedHealthAt := s.cachedHealthAt[routerID]
	lastSave := s.lastMetricsSave[routerID]
	s.mu.Unlock()

	shouldSaveMetrics := lastSave.IsZero() || now.Sub(lastSave) >= 10*time.Second
	shouldFetchHealth := cachedHealthAt.IsZero() || now.Sub(cachedHealthAt) >= 5*time.Second

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

	var (
		res         *routeros.Resource
		resErr      error
		health      []routeros.HealthItem
		rates       []routeros.InterfaceTrafficRate
		mangleRules []routeros.MangleRule
		ifaces      []routeros.Interface
	)

	var wg sync.WaitGroup

	// 1. System Resource
	wg.Add(1)
	go func() {
		defer wg.Done()
		res, resErr = client.GetSystemResource(ctx)
	}()

	// 2. Monitored interface traffic rates
	if len(monitoredList) > 0 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			rates, _ = client.MonitorInterfaceTraffic(ctx, monitoredList)
		}()
	}

	// 3. Mangle accounting rules (minimal payload via .proplist)
	wg.Add(1)
	go func() {
		defer wg.Done()
		mangleRules, _ = client.GetMangleAccountingRules(ctx)
	}()

	// 4. Health (cached 5s)
	if shouldFetchHealth {
		wg.Add(1)
		go func() {
			defer wg.Done()
			health, _ = client.GetSystemHealth(ctx)
		}()
	} else {
		health = cachedHealth
	}

	// 5. Interfaces (only when metrics save is due or no monitored WAN interfaces)
	if shouldSaveMetrics || len(monitoredList) == 0 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			ifaces, _ = client.GetInterfaces(ctx)
		}()
	}

	wg.Wait()

	if resErr != nil || res == nil {
		return resErr
	}

	if shouldFetchHealth && health != nil {
		s.mu.Lock()
		s.cachedHealth[routerID] = health
		s.cachedHealthAt[routerID] = now
		s.mu.Unlock()
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
	temp, volt := routeros.ExtractHealthMetrics(health)

	// Save historical metrics if due
	if shouldSaveMetrics {
		s.saveMetricsAndRollups(routerID, now, res, health, ifaces)
		s.mu.Lock()
		s.lastMetricsSave[routerID] = now
		s.mu.Unlock()
	}

	// Calculate WAN rates across monitored interfaces
	var wanRxBps, wanTxBps float64
	if len(monitoredList) > 0 && len(rates) > 0 {
		for _, r := range rates {
			wanRxBps += r.RxBitsPerSecond.Float64()
			wanTxBps += r.TxBitsPerSecond.Float64()
		}
	} else if len(ifaces) > 0 {
		s.mu.Lock()
		pIfaces := s.prevIfaces[routerID]
		pTime := s.prevTime[routerID]
		s.mu.Unlock()
		if pIfaces != nil && !pTime.IsZero() {
			dt := now.Sub(pTime).Seconds()
			if dt > 0.1 {
				monMap := make(map[string]bool, len(monitoredList))
				for _, m := range monitoredList {
					monMap[m] = true
				}
				for _, iface := range ifaces {
					if len(monMap) > 0 && !monMap[iface.Name] {
						continue
					}
					rx, _ := strconv.ParseInt(iface.RxByte, 10, 64)
					txBytes, _ := strconv.ParseInt(iface.TxByte, 10, 64)
					if prev, ok := pIfaces[iface.Name]; ok {
						dRx := rx - prev[0]
						dTx := txBytes - prev[1]
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

	// Resolve WAN IP & Public IP with caching
	s.mu.Lock()
	cachedAddrs := s.cachedIPAddrs[routerID]
	cachedAddrsAt := s.cachedIPAddrsAt[routerID]
	cachedPub := s.cachedPublicIP[routerID]
	cachedPubAt := s.cachedPubIPAt[routerID]
	s.mu.Unlock()

	var ipAddrs []routeros.IPAddress
	if cachedAddrs != nil && time.Since(cachedAddrsAt) < 30*time.Second {
		ipAddrs = cachedAddrs
	} else {
		ipAddrs, _ = client.GetIPAddresses(ctx)
		if len(ipAddrs) > 0 {
			s.mu.Lock()
			s.cachedIPAddrs[routerID] = ipAddrs
			s.cachedIPAddrsAt[routerID] = time.Now()
			s.mu.Unlock()
		}
	}

	var wanIP string
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

	var publicIP string
	if cachedPub != "" && time.Since(cachedPubAt) < 60*time.Second {
		publicIP = cachedPub
	} else {
		publicIP, _ = client.GetCloudPublicAddress(ctx)
		if publicIP != "" {
			s.mu.Lock()
			s.cachedPublicIP[routerID] = publicIP
			s.cachedPubIPAt[routerID] = time.Now()
			s.mu.Unlock()
		}
	}
	clockStr := now.Format("15:04:05")

	// Differentiate mangle rules for live per-device rates (rules fetched concurrently with .proplist)
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

	// Fetch hardware details with caching (hardware never changes at runtime)
	s.mu.Lock()
	cachedRb := s.cachedRB[routerID]
	cachedRbAt := s.cachedRBAt[routerID]
	s.mu.Unlock()

	var rb *routeros.RouterBoard
	if cachedRb != nil && time.Since(cachedRbAt) < 10*time.Minute {
		rb = cachedRb
	} else {
		rb, _ = client.GetRouterBoard(ctx)
		if rb != nil {
			s.mu.Lock()
			s.cachedRB[routerID] = rb
			s.cachedRBAt[routerID] = time.Now()
			s.mu.Unlock()
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

	// Fetch or use cached volume stats (cached 5s)
	s.mu.Lock()
	cachedDevs := s.cachedDevStats[routerID]
	cachedUsers := s.cachedUserStats[routerID]
	cachedStatsAt := s.cachedStatsAt[routerID]
	s.mu.Unlock()

	var devStats map[int]db.VolumeStats
	var userStats map[int]db.VolumeStats

	if cachedDevs != nil && cachedUsers != nil && now.Sub(cachedStatsAt) < 5*time.Second {
		devStats = cachedDevs
		userStats = cachedUsers
	} else {
		todayStr := now.Format("2006-01-02")
		anchorDay := s.database.GetBillingAnchorDay(&routerID)
		cycleStart := db.CalculateBillingCycleStart(anchorDay, now)
		devStats, _ = s.database.GetDeviceVolumeStats(cycleStart, todayStr)
		userStats, _ = s.database.GetUserVolumeStats(cycleStart, todayStr)

		s.mu.Lock()
		s.cachedDevStats[routerID] = devStats
		s.cachedUserStats[routerID] = userStats
		s.cachedStatsAt[routerID] = now
		s.mu.Unlock()
	}

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
				"memory_usage_pct":     memPct,
				"temperature":          temp,
				"voltage":              volt,
				"uptime":               res.Uptime,
				"wan_rx_bps":           wanRxBps,
				"wan_tx_bps":           wanTxBps,
				"wan_ip":               wanIP,
				"public_ip":            publicIP,
				"isp":                  s.pubNet.GetISP(routerID, publicIP),
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
			start := time.Now()

			routers, err := s.database.GetRouters()
			if err == nil {
				var wg sync.WaitGroup
				for _, r := range routers {
					if r.IsActive {
						wg.Add(1)
						go func(rID int) {
							defer wg.Done()
							callCtx, cancel := context.WithTimeout(ctx, 4*time.Second)
							defer cancel()
							if err := s.Collect(callCtx, rID); err != nil {
								slog.Debug("Telemetry collection failed", "router_id", rID, "err", err)
							}
						}(r.ID)
					}
				}
				wg.Wait()
			}

			elapsed := time.Since(start)
			sleepDuration := interval - elapsed
			if sleepDuration < 20*time.Millisecond {
				sleepDuration = 20 * time.Millisecond
			}

			select {
			case <-ctx.Done():
				return
			case <-time.After(sleepDuration):
			}
		}
	}()
}

package api

import (
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/masseselsev/mikroman/internal/db"
)

type AnalyticsHandler struct {
	database *db.DB
}

func NewAnalyticsHandler(database *db.DB) *AnalyticsHandler {
	return &AnalyticsHandler{database: database}
}

// BillingCycleConfigDTO represents the ISP monthly billing cycle configuration.
type BillingCycleConfigDTO struct {
	AnchorDay    int  `json:"anchor_day"`
	AnchorHour   int  `json:"anchor_hour"`
	AnchorMinute int  `json:"anchor_minute"`
	RouterID     *int `json:"router_id,omitempty"`
}

func (h *AnalyticsHandler) GetBillingCycle(w http.ResponseWriter, r *http.Request) {
	var routerID *int
	if rIDStr := r.URL.Query().Get("router_id"); rIDStr != "" {
		if id, err := strconv.Atoi(rIDStr); err == nil {
			routerID = &id
		}
	}
	if routerID == nil {
		routers, _ := h.database.GetRouters()
		for _, rtr := range routers {
			if rtr.IsActive {
				id := rtr.ID
				routerID = &id
				break
			}
		}
	}
	day := h.database.GetBillingAnchorDay(routerID)
	hour, minute := h.database.GetBillingAnchorTime(routerID)
	WriteJSON(w, http.StatusOK, BillingCycleConfigDTO{
		AnchorDay:    day,
		AnchorHour:   hour,
		AnchorMinute: minute,
		RouterID:     routerID,
	})
}

func (h *AnalyticsHandler) SaveBillingCycle(w http.ResponseWriter, r *http.Request) {
	var payload BillingCycleConfigDTO
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid request body")
		return
	}
	var routerID *int
	if payload.RouterID != nil {
		routerID = payload.RouterID
	} else if rIDStr := r.URL.Query().Get("router_id"); rIDStr != "" {
		if id, err := strconv.Atoi(rIDStr); err == nil {
			routerID = &id
		}
	}
	if routerID == nil {
		routers, _ := h.database.GetRouters()
		for _, rtr := range routers {
			if rtr.IsActive {
				id := rtr.ID
				routerID = &id
				break
			}
		}
	}
	_ = h.database.SetBillingAnchorConfig(payload.AnchorDay, payload.AnchorHour, payload.AnchorMinute, routerID)
	savedDay := h.database.GetBillingAnchorDay(routerID)
	savedHour, savedMinute := h.database.GetBillingAnchorTime(routerID)
	WriteJSON(w, http.StatusOK, BillingCycleConfigDTO{
		AnchorDay:    savedDay,
		AnchorHour:   savedHour,
		AnchorMinute: savedMinute,
		RouterID:     routerID,
	})
}

// DailyTrafficPoint is one point on the timeline chart.
type DailyTrafficPoint struct {
	RecordDate string  `json:"record_date"`
	Label      *string `json:"label,omitempty"`
	BytesIn    int64   `json:"bytes_in"`
	BytesOut   int64   `json:"bytes_out"`
	TotalBytes int64   `json:"total_bytes"`
}

type GatewayTrafficSummary struct {
	TotalBytesIn        int64    `json:"total_bytes_in"`
	TotalBytesOut       int64    `json:"total_bytes_out"`
	TotalBytes          int64    `json:"total_bytes"`
	PeakRxBps           float64  `json:"peak_rx_bps"`
	PeakTxBps           float64  `json:"peak_tx_bps"`
	MonitoredInterfaces []string `json:"monitored_interfaces"`
}

type RouterSelfTrafficSummary struct {
	BytesIn    int64   `json:"bytes_in"`
	BytesOut   int64   `json:"bytes_out"`
	TotalBytes int64   `json:"total_bytes"`
	PctOfTotal float64 `json:"pct_of_total"`
}

type UnassignedTrafficSummary struct {
	DeviceCount int     `json:"device_count"`
	BytesIn     int64   `json:"bytes_in"`
	BytesOut    int64   `json:"bytes_out"`
	TotalBytes  int64   `json:"total_bytes"`
	PctOfTotal  float64 `json:"pct_of_total"`
}

type UserTrafficSummary struct {
	UserID       int      `json:"user_id"`
	UserName     string   `json:"user_name"`
	AvatarIcon   string   `json:"avatar_icon"`
	BytesIn      int64    `json:"bytes_in"`
	BytesOut     int64    `json:"bytes_out"`
	TotalBytes   int64    `json:"total_bytes"`
	PctOfTotal   float64  `json:"pct_of_total"`
	DeviceCount  int      `json:"device_count"`
	LastSeen     *string  `json:"last_seen,omitempty"`
	CycleBytes   int64    `json:"cycle_bytes"`
	AllTimeBytes int64    `json:"all_time_bytes"`
}

type DeviceTrafficSummary struct {
	DeviceID      int     `json:"device_id"`
	MacAddress    string  `json:"mac_address"`
	Hostname      *string `json:"hostname,omitempty"`
	CustomName    *string `json:"custom_name,omitempty"`
	IPAddress     *string `json:"ip_address,omitempty"`
	Vendor        *string `json:"vendor,omitempty"`
	UserID        *int    `json:"user_id,omitempty"`
	UserName      *string `json:"user_name,omitempty"`
	BytesIn       int64   `json:"bytes_in"`
	BytesOut      int64   `json:"bytes_out"`
	TotalBytes    int64   `json:"total_bytes"`
	PctOfTotal    float64 `json:"pct_of_total"`
	SpeedLimit    string  `json:"speed_limit"`
	IsPaused      bool    `json:"is_paused"`
	IsHidden      bool    `json:"is_hidden"`
	LastSeen      *string `json:"last_seen,omitempty"`
	CycleBytes    int64   `json:"cycle_bytes"`
	AllTimeBytes  int64   `json:"all_time_bytes"`
	IsRetiredPool bool    `json:"is_retired_pool"`
}

type InterfaceTrafficSummary struct {
	InterfaceName string  `json:"interface_name"`
	IsTunnel      bool    `json:"is_tunnel"`
	IsMonitored   bool    `json:"is_monitored"`
	BytesIn       int64   `json:"bytes_in"`
	BytesOut      int64   `json:"bytes_out"`
	TotalBytes    int64   `json:"total_bytes"`
	PctOfTotal    float64 `json:"pct_of_total"`
	CycleBytes    int64   `json:"cycle_bytes"`
	AllTimeBytes  int64   `json:"all_time_bytes"`
}

type AccountingHealth struct {
	GatewayBytes   int64   `json:"gateway_bytes"`
	AccountedBytes int64   `json:"accounted_bytes"`
	CoveragePct    float64 `json:"coverage_pct"`
	Status         string  `json:"status"`
	Message        *string `json:"message,omitempty"`
}

type TrafficAnalyticsResponse struct {
	StartDate          string                    `json:"start_date"`
	EndDate            string                    `json:"end_date"`
	RangePreset        string                    `json:"range_preset"`
	BillingAnchorDay   int                       `json:"billing_anchor_day"`
	Gateway            GatewayTrafficSummary     `json:"gateway"`
	RouterSelf         RouterSelfTrafficSummary  `json:"router_self"`
	Unassigned         UnassignedTrafficSummary  `json:"unassigned"`
	Users              []UserTrafficSummary      `json:"users"`
	Devices            []DeviceTrafficSummary    `json:"devices"`
	Interfaces         []InterfaceTrafficSummary `json:"interfaces"`
	Timeline           []DailyTrafficPoint       `json:"timeline"`
	UnaccountedBytes   int64                     `json:"unaccounted_bytes"`
	OverAccountedBytes int64                     `json:"over_accounted_bytes"`
	AccountingHealth   AccountingHealth          `json:"accounting_health"`
}

func resolveDateRange(preset string, customStart, customEnd string, anchorDay, anchorHour, anchorMinute int, now time.Time) (time.Time, time.Time, string) {
	today := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, now.Location())
	p := strings.ToLower(strings.TrimSpace(preset))
	if p == "" {
		p = "7d"
	}

	switch p {
	case "24h":
		return today.AddDate(0, 0, -1), today, "24h"
	case "today", "1d", "day":
		return today, today, "today"
	case "yesterday":
		yest := today.AddDate(0, 0, -1)
		return yest, yest, "yesterday"
	case "7d", "week", "1w":
		return today.AddDate(0, 0, -6), today, "7d"
	case "30d", "month", "1m":
		return today.AddDate(0, 0, -29), today, "30d"
	case "1y", "year", "365d":
		return today.AddDate(0, 0, -364), today, "1y"
	case "all_time", "all", "alltime":
		return time.Date(2000, 1, 1, 0, 0, 0, 0, now.Location()), today, "all_time"
	case "billing_current":
		sDt, eDt := db.CalculateBillingCycleBounds(anchorDay, anchorHour, anchorMinute, now, false)
		endDate := eDt.Add(-time.Microsecond)
		if endDate.After(today) {
			endDate = today
		}
		return sDt, endDate, "billing_current"
	case "billing_previous":
		sDt, eDt := db.CalculateBillingCycleBounds(anchorDay, anchorHour, anchorMinute, now, true)
		endDate := eDt.Add(-time.Microsecond)
		return sDt, endDate, "billing_previous"
	case "custom":
		if customStart != "" && customEnd != "" {
			s, err1 := time.Parse("2006-01-02", customStart)
			e, err2 := time.Parse("2006-01-02", customEnd)
			if err1 == nil && err2 == nil {
				if s.After(e) {
					s, e = e, s
				}
				return s, e, "custom"
			}
		}
		return today.AddDate(0, 0, -6), today, "7d"
	default:
		return today.AddDate(0, 0, -6), today, "7d"
	}
}

func (h *AnalyticsHandler) GetTrafficOverview(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	preset := q.Get("preset")
	customStart := q.Get("start_date")
	customEnd := q.Get("end_date")

	var routerID *int
	if rIDStr := q.Get("router_id"); rIDStr != "" {
		if id, err := strconv.Atoi(rIDStr); err == nil {
			routerID = &id
		}
	}

	anchorDay := h.database.GetBillingAnchorDay(routerID)
	anchorHour, anchorMinute := h.database.GetBillingAnchorTime(routerID)
	now := time.Now()

	startDt, endDt, rangePreset := resolveDateRange(preset, customStart, customEnd, anchorDay, anchorHour, anchorMinute, now)
	startDateStr := startDt.Format("2006-01-02")
	endDateStr := endDt.Format("2006-01-02")

	// Read monitored interfaces
	monKey := "monitored_interfaces_default"
	if routerID != nil {
		monKey = fmt.Sprintf("monitored_interfaces_%d", *routerID)
	}
	monVal, _ := h.database.GetSetting(monKey)
	if monVal == "" && routerID != nil {
		monVal, _ = h.database.GetSetting("monitored_interfaces_default")
	}
	var monitoredList []string
	if monVal != "" {
		_ = json.Unmarshal([]byte(monVal), &monitoredList)
	}
	if monitoredList == nil {
		monitoredList = []string{}
	}

	users, _ := h.database.GetUsers(routerID)
	devices, _ := h.database.GetDevices(routerID)

	userMap := make(map[int]db.User)
	for _, u := range users {
		userMap[u.ID] = u
	}

	cycStartDt, cycEndDt := db.CalculateBillingCycleBounds(anchorDay, anchorHour, anchorMinute, now, false)
	cycStartStr := cycStartDt.Format("2006-01-02")
	cycEndStr := cycEndDt.Add(-time.Microsecond).Format("2006-01-02")

	// Query device totals for selected range
	devRangeTotals := make(map[int][2]int64)
	rows, err := h.database.SqlDB.Query(`
		SELECT device_id, SUM(bytes_in), SUM(bytes_out)
		FROM device_traffic_rollups
		WHERE record_date >= ? AND record_date <= ?
		GROUP BY device_id
	`, startDateStr, endDateStr)
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var dID int
			var bIn, bOut int64
			if err := rows.Scan(&dID, &bIn, &bOut); err == nil {
				devRangeTotals[dID] = [2]int64{bIn, bOut}
			}
		}
	}

	// Query device totals for current cycle
	devCycleTotals := make(map[int][2]int64)
	cRows, cErr := h.database.SqlDB.Query(`
		SELECT device_id, SUM(bytes_in), SUM(bytes_out)
		FROM device_traffic_rollups
		WHERE record_date >= ? AND record_date <= ?
		GROUP BY device_id
	`, cycStartStr, cycEndStr)
	if cErr == nil {
		defer cRows.Close()
		for cRows.Next() {
			var dID int
			var bIn, bOut int64
			if err := cRows.Scan(&dID, &bIn, &bOut); err == nil {
				devCycleTotals[dID] = [2]int64{bIn, bOut}
			}
		}
	}

	// Query device totals all-time
	devAllTimeTotals := make(map[int][2]int64)
	aRows, aErr := h.database.SqlDB.Query(`
		SELECT device_id, SUM(bytes_in), SUM(bytes_out)
		FROM device_traffic_rollups
		GROUP BY device_id
	`)
	if aErr == nil {
		defer aRows.Close()
		for aRows.Next() {
			var dID int
			var bIn, bOut int64
			if err := aRows.Scan(&dID, &bIn, &bOut); err == nil {
				devAllTimeTotals[dID] = [2]int64{bIn, bOut}
			}
		}
	}

	// Query router traffic rollups per day
	routerDailyMap := make(map[string][2]int64)
	var rQuery string
	var rArgs []interface{}
	if routerID != nil {
		rQuery = `SELECT record_date, SUM(bytes_in), SUM(bytes_out) FROM router_traffic_rollups WHERE router_id = ? AND record_date >= ? AND record_date <= ? GROUP BY record_date`
		rArgs = []interface{}{*routerID, startDateStr, endDateStr}
	} else {
		rQuery = `SELECT record_date, SUM(bytes_in), SUM(bytes_out) FROM router_traffic_rollups WHERE record_date >= ? AND record_date <= ? GROUP BY record_date`
		rArgs = []interface{}{startDateStr, endDateStr}
	}
	rdRows, rdErr := h.database.SqlDB.Query(rQuery, rArgs...)
	if rdErr == nil {
		defer rdRows.Close()
		for rdRows.Next() {
			var dStr string
			var bIn, bOut int64
			if err := rdRows.Scan(&dStr, &bIn, &bOut); err == nil {
				routerDailyMap[dStr] = [2]int64{bIn, bOut}
			}
		}
	}

	// Query device traffic rollups per day
	deviceDailyMap := make(map[string][2]int64)
	ddRows, ddErr := h.database.SqlDB.Query(`
		SELECT record_date, SUM(bytes_in), SUM(bytes_out)
		FROM device_traffic_rollups
		WHERE record_date >= ? AND record_date <= ?
		GROUP BY record_date
	`, startDateStr, endDateStr)
	if ddErr == nil {
		defer ddRows.Close()
		for ddRows.Next() {
			var dStr string
			var bIn, bOut int64
			if err := ddRows.Scan(&dStr, &bIn, &bOut); err == nil {
				deviceDailyMap[dStr] = [2]int64{bIn, bOut}
			}
		}
	}

	// Query router self traffic in range
	var selfIn, selfOut int64
	var selfQuery string
	var selfArgs []interface{}
	if routerID != nil {
		selfQuery = `SELECT SUM(bytes_in), SUM(bytes_out) FROM router_self_traffic_rollups WHERE router_id = ? AND record_date >= ? AND record_date <= ?`
		selfArgs = []interface{}{*routerID, startDateStr, endDateStr}
	} else {
		selfQuery = `SELECT SUM(bytes_in), SUM(bytes_out) FROM router_self_traffic_rollups WHERE record_date >= ? AND record_date <= ?`
		selfArgs = []interface{}{startDateStr, endDateStr}
	}
	_ = h.database.SqlDB.QueryRow(selfQuery, selfArgs...).Scan(&selfIn, &selfOut)

	// Build full date timeline (every calendar day in range)
	var timeline []DailyTrafficPoint
	cur := startDt
	for !cur.After(endDt) {
		curStr := cur.Format("2006-01-02")
		rTotals := routerDailyMap[curStr]
		dTotals := deviceDailyMap[curStr]
		dayIn := max(rTotals[0], dTotals[0])
		dayOut := max(rTotals[1], dTotals[1])
		timeline = append(timeline, DailyTrafficPoint{
			RecordDate: curStr,
			BytesIn:    dayIn,
			BytesOut:   dayOut,
			TotalBytes: dayIn + dayOut,
		})
		cur = cur.AddDate(0, 0, 1)
	}

	// Calculate Gateway Total
	var gwIn, gwOut int64
	for _, pt := range timeline {
		gwIn += pt.BytesIn
		gwOut += pt.BytesOut
	}
	gwTotal := gwIn + gwOut

	// Accounted volume: sum of devices + router self
	var sumDevIn, sumDevOut int64
	for _, dev := range devices {
		totals := devRangeTotals[dev.ID]
		sumDevIn += totals[0]
		sumDevOut += totals[1]
	}
	accountedTotal := sumDevIn + sumDevOut + selfIn + selfOut

	if gwTotal == 0 && accountedTotal > 0 {
		gwIn = sumDevIn + selfIn
		gwOut = sumDevOut + selfOut
		gwTotal = gwIn + gwOut
	}

	// Build per-device list
	devicesList := make([]DeviceTrafficSummary, 0, len(devices))
	for _, dev := range devices {
		totals := devRangeTotals[dev.ID]
		cycTotals := devCycleTotals[dev.ID]
		allTotals := devAllTimeTotals[dev.ID]
		dIn, dOut := totals[0], totals[1]
		dTotal := dIn + dOut

		var pct float64
		if gwTotal > 0 {
			pct = math.Round((float64(dTotal)/float64(gwTotal))*1000.0) / 10.0
		}

		var uName *string
		if dev.UserID != nil {
			if u, ok := userMap[*dev.UserID]; ok {
				name := u.Name
				uName = &name
			}
		}

		var customName, hostName, ipAddr, vendor *string
		if dev.CustomName.Valid && dev.CustomName.String != "" {
			cName := dev.CustomName.String
			customName = &cName
		}
		if dev.Hostname.Valid && dev.Hostname.String != "" {
			hName := dev.Hostname.String
			hostName = &hName
		}
		if dev.IPAddress.Valid && dev.IPAddress.String != "" {
			ip := dev.IPAddress.String
			ipAddr = &ip
		}
		if dev.Vendor.Valid && dev.Vendor.String != "" {
			v := dev.Vendor.String
			vendor = &v
		}
		var lastSeenStr *string
		if !dev.LastSeen.IsZero() {
			ls := dev.LastSeen.Format("2006-01-02T15:04:05Z")
			lastSeenStr = &ls
		}

		devicesList = append(devicesList, DeviceTrafficSummary{
			DeviceID:      dev.ID,
			MacAddress:    dev.MacAddress,
			Hostname:      hostName,
			CustomName:    customName,
			IPAddress:     ipAddr,
			Vendor:        vendor,
			UserID:        dev.UserID,
			UserName:      uName,
			BytesIn:       dIn,
			BytesOut:      dOut,
			TotalBytes:    dTotal,
			PctOfTotal:    pct,
			SpeedLimit:    dev.SpeedLimit,
			IsPaused:      dev.IsPaused,
			IsHidden:      dev.IsHidden,
			LastSeen:      lastSeenStr,
			CycleBytes:    cycTotals[0] + cycTotals[1],
			AllTimeBytes:  allTotals[0] + allTotals[1],
			IsRetiredPool: false,
		})
	}

	// Build per-user list (folded by current device ownership)
	usersList := make([]UserTrafficSummary, 0, len(users))
	for _, u := range users {
		var uIn, uOut, uCycle, uAllTime int64
		var dCount int
		var maxSeen *time.Time

		for _, dev := range devices {
			if dev.UserID != nil && *dev.UserID == u.ID {
				dCount++
				totals := devRangeTotals[dev.ID]
				cycTotals := devCycleTotals[dev.ID]
				allTotals := devAllTimeTotals[dev.ID]
				uIn += totals[0]
				uOut += totals[1]
				uCycle += cycTotals[0] + cycTotals[1]
				uAllTime += allTotals[0] + allTotals[1]

				if !dev.LastSeen.IsZero() {
					if maxSeen == nil || dev.LastSeen.After(*maxSeen) {
						t := dev.LastSeen
						maxSeen = &t
					}
				}
			}
		}

		uTotal := uIn + uOut
		var pct float64
		if gwTotal > 0 {
			pct = math.Round((float64(uTotal)/float64(gwTotal))*1000.0) / 10.0
		}

		var lastSeenStr *string
		if maxSeen != nil {
			ls := maxSeen.Format("2006-01-02T15:04:05Z")
			lastSeenStr = &ls
		}

		usersList = append(usersList, UserTrafficSummary{
			UserID:       u.ID,
			UserName:     u.Name,
			AvatarIcon:   u.AvatarIcon,
			BytesIn:      uIn,
			BytesOut:     uOut,
			TotalBytes:   uTotal,
			PctOfTotal:   pct,
			DeviceCount:  dCount,
			LastSeen:     lastSeenStr,
			CycleBytes:   uCycle,
			AllTimeBytes: uAllTime,
		})
	}

	// Unassigned summary
	var unassignIn, unassignOut int64
	var unassignCount int
	for _, dev := range devices {
		if dev.UserID == nil {
			unassignCount++
			totals := devRangeTotals[dev.ID]
			unassignIn += totals[0]
			unassignOut += totals[1]
		}
	}
	unassignTotal := unassignIn + unassignOut
	var unassignPct float64
	if gwTotal > 0 {
		unassignPct = math.Round((float64(unassignTotal)/float64(gwTotal))*1000.0) / 10.0
	}

	// Router self summary
	selfTotal := selfIn + selfOut
	var selfPct float64
	if gwTotal > 0 {
		selfPct = math.Round((float64(selfTotal)/float64(gwTotal))*1000.0) / 10.0
	}

	// Interface summaries
	ifaceMap := make(map[string][2]int64)
	var ifaceQuery string
	var ifaceArgs []interface{}
	if routerID != nil {
		ifaceQuery = `SELECT interface_name, SUM(bytes_in), SUM(bytes_out) FROM interface_traffic_rollups WHERE router_id = ? AND record_date >= ? AND record_date <= ? GROUP BY interface_name`
		ifaceArgs = []interface{}{*routerID, startDateStr, endDateStr}
	} else {
		ifaceQuery = `SELECT interface_name, SUM(bytes_in), SUM(bytes_out) FROM interface_traffic_rollups WHERE record_date >= ? AND record_date <= ? GROUP BY interface_name`
		ifaceArgs = []interface{}{startDateStr, endDateStr}
	}
	ifRows, ifErr := h.database.SqlDB.Query(ifaceQuery, ifaceArgs...)
	if ifErr == nil {
		defer ifRows.Close()
		for ifRows.Next() {
			var iName string
			var bIn, bOut int64
			if err := ifRows.Scan(&iName, &bIn, &bOut); err == nil {
				ifaceMap[iName] = [2]int64{bIn, bOut}
			}
		}
	}

	var interfacesList []InterfaceTrafficSummary
	monSet := make(map[string]bool)
	for _, m := range monitoredList {
		monSet[m] = true
	}
	for iName, v := range ifaceMap {
		iTotal := v[0] + v[1]
		var iPct float64
		if gwTotal > 0 {
			iPct = math.Round((float64(iTotal)/float64(gwTotal))*1000.0) / 10.0
		}
		interfacesList = append(interfacesList, InterfaceTrafficSummary{
			InterfaceName: iName,
			IsTunnel:      strings.HasPrefix(iName, "wg") || strings.HasPrefix(iName, "zt") || strings.HasPrefix(iName, "ovpn"),
			IsMonitored:   monSet[iName],
			BytesIn:       v[0],
			BytesOut:      v[1],
			TotalBytes:    iTotal,
			PctOfTotal:    iPct,
			CycleBytes:    iTotal,
			AllTimeBytes:  iTotal,
		})
	}

	// Accounting health & coverage
	healthStatus := "ok"
	var coveragePct float64 = 100.0
	if gwTotal == 0 && accountedTotal == 0 {
		healthStatus = "no_data"
	} else if gwTotal > 0 {
		coveragePct = math.Round((float64(accountedTotal)/float64(gwTotal))*1000.0) / 10.0
		if coveragePct < 50.0 {
			healthStatus = "degraded"
		}
	}

	response := TrafficAnalyticsResponse{
		StartDate:        startDateStr,
		EndDate:          endDateStr,
		RangePreset:      rangePreset,
		BillingAnchorDay: anchorDay,
		Gateway: GatewayTrafficSummary{
			TotalBytesIn:        gwIn,
			TotalBytesOut:       gwOut,
			TotalBytes:          gwTotal,
			MonitoredInterfaces: monitoredList,
		},
		RouterSelf: RouterSelfTrafficSummary{
			BytesIn:    selfIn,
			BytesOut:   selfOut,
			TotalBytes: selfTotal,
			PctOfTotal: selfPct,
		},
		Unassigned: UnassignedTrafficSummary{
			DeviceCount: unassignCount,
			BytesIn:     unassignIn,
			BytesOut:    unassignOut,
			TotalBytes:  unassignTotal,
			PctOfTotal:  unassignPct,
		},
		Users:              usersList,
		Devices:            devicesList,
		Interfaces:         interfacesList,
		Timeline:           timeline,
		UnaccountedBytes:   max(0, gwTotal-accountedTotal),
		OverAccountedBytes: max(0, accountedTotal-gwTotal),
		AccountingHealth: AccountingHealth{
			GatewayBytes:   gwTotal,
			AccountedBytes: accountedTotal,
			CoveragePct:    coveragePct,
			Status:         healthStatus,
		},
	}

	WriteJSON(w, http.StatusOK, response)
}

func (h *AnalyticsHandler) GetQuota(w http.ResponseWriter, r *http.Request) {
	quotaVal, _ := h.database.GetSetting("isp_monthly_quota_bytes")
	quotaBytes, _ := strconv.ParseInt(quotaVal, 10, 64)

	WriteJSON(w, http.StatusOK, map[string]interface{}{
		"quota_bytes": quotaBytes,
		"enabled":     quotaBytes > 0,
	})
}

func (h *AnalyticsHandler) SaveQuota(w http.ResponseWriter, r *http.Request) {
	var payload struct {
		QuotaBytes int64 `json:"quota_bytes"`
	}
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid quota payload")
		return
	}

	_ = h.database.SetSetting("isp_monthly_quota_bytes", strconv.FormatInt(payload.QuotaBytes, 10), "Monthly ISP Quota in Bytes")
	WriteJSON(w, http.StatusOK, map[string]string{"message": "Quota updated"})
}

func (h *AnalyticsHandler) UserHistory(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, _ := strconv.Atoi(idStr)

	rows, err := h.database.SqlDB.Query(`
		SELECT record_date, bytes_in, bytes_out
		FROM traffic_rollups
		WHERE user_id = ?
		ORDER BY record_date DESC LIMIT 30
	`, id)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to load history")
		return
	}
	defer rows.Close()

	type DayHistory struct {
		Date     string `json:"date"`
		BytesIn  int64  `json:"bytes_in"`
		BytesOut int64  `json:"bytes_out"`
	}

	var items []DayHistory
	for rows.Next() {
		var d DayHistory
		if err := rows.Scan(&d.Date, &d.BytesIn, &d.BytesOut); err == nil {
			items = append(items, d)
		}
	}

	if items == nil {
		items = []DayHistory{}
	}
	WriteJSON(w, http.StatusOK, items)
}

func (h *AnalyticsHandler) DeviceHistory(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, _ := strconv.Atoi(idStr)

	rows, err := h.database.SqlDB.Query(`
		SELECT record_date, bytes_in, bytes_out
		FROM device_traffic_rollups
		WHERE device_id = ?
		ORDER BY record_date DESC LIMIT 30
	`, id)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to load history")
		return
	}
	defer rows.Close()

	type DayHistory struct {
		Date     string `json:"date"`
		BytesIn  int64  `json:"bytes_in"`
		BytesOut int64  `json:"bytes_out"`
	}

	var items []DayHistory
	for rows.Next() {
		var d DayHistory
		if err := rows.Scan(&d.Date, &d.BytesIn, &d.BytesOut); err == nil {
			items = append(items, d)
		}
	}

	if items == nil {
		items = []DayHistory{}
	}
	WriteJSON(w, http.StatusOK, items)
}

package api

import (
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"sort"
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

type QuotaConfigDTO struct {
	LimitBytes     int64   `json:"limit_bytes"`
	Thresholds     []int   `json:"thresholds"`
	NotifyTelegram *bool   `json:"notify_telegram"`
	PortalURL      *string `json:"portal_url"`
	PortalLabel    *string `json:"portal_label"`
}

type QuotaStatusDTO struct {
	LimitBytes           int64   `json:"limit_bytes"`
	UsedBytes            int64   `json:"used_bytes"`
	RemainingBytes       int64   `json:"remaining_bytes"`
	UsedPct              float64 `json:"used_pct"`
	CycleStart           *string `json:"cycle_start"`
	CycleEnd             *string `json:"cycle_end"`
	CycleEndAt           *string `json:"cycle_end_at,omitempty"`
	DaysRemaining        int     `json:"days_remaining"`
	ProjectedDailyBudget int64   `json:"projected_daily_budget"`
	CycleDaysTotal       int     `json:"cycle_days_total"`
	CycleDaysElapsed     int     `json:"cycle_days_elapsed"`
	ProjectedBytesLinear int64   `json:"projected_bytes_linear"`
	ProjectedPctLinear   float64 `json:"projected_pct_linear"`
	PaceBytesPerDay      int64   `json:"pace_bytes_per_day"`
	ProjectedBytesAtPace int64   `json:"projected_bytes_at_pace"`
	ProjectedPctAtPace   float64 `json:"projected_pct_at_pace"`
	PrevCycleBytes       int64   `json:"prev_cycle_bytes"`
	PrevCycleBytesPerDay int64   `json:"prev_cycle_bytes_per_day"`
	PaceBlendWeight      float64 `json:"pace_blend_weight"`
	PaceBasis            string  `json:"pace_basis"`
	OnTrack              bool    `json:"on_track"`
	Thresholds           []int   `json:"thresholds"`
	ThresholdsReached    []int   `json:"thresholds_reached"`
	Enabled              bool    `json:"enabled"`
	NotifyTelegram       bool    `json:"notify_telegram"`
	PortalURL            *string `json:"portal_url,omitempty"`
	PortalLabel          *string `json:"portal_label,omitempty"`
}

func parseThresholds(raw string) []int {
	if raw == "" {
		return []int{}
	}
	parts := strings.Split(raw, ",")
	seen := make(map[int]bool)
	var res []int
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if val, err := strconv.Atoi(p); err == nil && val >= 1 && val <= 100 {
			if !seen[val] {
				seen[val] = true
				res = append(res, val)
			}
		}
	}
	sort.Ints(res)
	return res
}

func (h *AnalyticsHandler) buildQuotaStatus(routerID *int) QuotaStatusDTO {
	getSetting := func(base string) string {
		if routerID != nil {
			if val, _ := h.database.GetSetting(fmt.Sprintf("%s_%d", base, *routerID)); val != "" {
				return val
			}
		}
		val, _ := h.database.GetSetting(base)
		return val
	}

	limitStr := getSetting("quota_limit_bytes")
	if limitStr == "" {
		limitStr = getSetting("isp_monthly_quota_bytes")
	}
	limitBytes, _ := strconv.ParseInt(limitStr, 10, 64)
	if limitBytes < 0 {
		limitBytes = 0
	}

	threshStr := getSetting("quota_alert_thresholds")
	thresholds := parseThresholds(threshStr)

	notifyStr := getSetting("quota_notify_telegram")
	notifyTelegram := notifyStr == "" || !strings.EqualFold(notifyStr, "false")

	portalURLStr := strings.TrimSpace(getSetting("isp_portal_url"))
	var portalURL *string
	if portalURLStr != "" {
		portalURL = &portalURLStr
	}

	portalLabelStr := strings.TrimSpace(getSetting("isp_portal_label"))
	var portalLabel *string
	if portalLabelStr != "" {
		portalLabel = &portalLabelStr
	}

	anchorDay := h.database.GetBillingAnchorDay(routerID)
	anchorHour, anchorMinute := h.database.GetBillingAnchorTime(routerID)
	now := time.Now()
	today := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, now.Location())
	todayStr := today.Format("2006-01-02")

	startDt, endDt := db.CalculateBillingCycleBounds(anchorDay, anchorHour, anchorMinute, now, false)
	cycleStartStr := startDt.Format("2006-01-02")
	cycleEndStr := endDt.Add(-time.Microsecond).Format("2006-01-02")

	var cycleEndAt *string
	if anchorHour != 0 || anchorMinute != 0 {
		s := endDt.Format("2006-01-02T15:04:05")
		cycleEndAt = &s
	}

	var usedIn, usedOut int64
	if routerID != nil {
		_ = h.database.SqlDB.QueryRow(`
			SELECT COALESCE(SUM(bytes_in), 0), COALESCE(SUM(bytes_out), 0)
			FROM router_traffic_rollups
			WHERE router_id = ? AND record_date >= ? AND record_date <= ?
		`, *routerID, cycleStartStr, todayStr).Scan(&usedIn, &usedOut)
	} else {
		_ = h.database.SqlDB.QueryRow(`
			SELECT COALESCE(SUM(bytes_in), 0), COALESCE(SUM(bytes_out), 0)
			FROM router_traffic_rollups
			WHERE record_date >= ? AND record_date <= ?
		`, cycleStartStr, todayStr).Scan(&usedIn, &usedOut)
	}
	usedBytes := usedIn + usedOut
	if usedBytes == 0 {
		_ = h.database.SqlDB.QueryRow(`
			SELECT COALESCE(SUM(bytes_in), 0), COALESCE(SUM(bytes_out), 0)
			FROM device_traffic_rollups
			WHERE record_date >= ? AND record_date <= ?
		`, cycleStartStr, todayStr).Scan(&usedIn, &usedOut)
		usedBytes = usedIn + usedOut
	}

	totalDays := math.Max(1.0, endDt.Sub(startDt).Seconds()/86400.0)
	var elapsedDays float64
	if anchorHour == 0 && anchorMinute == 0 {
		daysDiff := float64(int(today.Sub(startDt.Truncate(24*time.Hour)).Hours()/24) + 1)
		elapsedDays = math.Min(totalDays, math.Max(1.0, daysDiff))
	} else {
		elapsedDays = math.Min(totalDays, math.Max(1.0, now.Sub(startDt).Seconds()/86400.0))
	}
	daysLeftAfterToday := math.Max(0.0, totalDays-elapsedDays)
	remainingSeconds := math.Max(0.0, endDt.Sub(now).Seconds())
	daysRemaining := int(remainingSeconds / 86400.0)
	if int64(remainingSeconds)%86400 != 0 {
		daysRemaining++
	}
	cycleDaysTotal := int(math.Round(totalDays))
	if cycleDaysTotal < 1 {
		cycleDaysTotal = 1
	}
	cycleDaysElapsed := int(math.Round(elapsedDays))
	if cycleDaysElapsed < 1 {
		cycleDaysElapsed = 1
	}
	if cycleDaysElapsed > cycleDaysTotal {
		cycleDaysElapsed = cycleDaysTotal
	}

	avgPerDay := float64(usedBytes) / elapsedDays
	projectedBytesLinear := int64(avgPerDay * totalDays)

	prevStartDt, prevEndDt := db.CalculateBillingCycleBounds(anchorDay, anchorHour, anchorMinute, now, true)
	prevStartStr := prevStartDt.Format("2006-01-02")
	prevEndStr := prevEndDt.Add(-time.Microsecond).Format("2006-01-02")
	var prevIn, prevOut int64
	if routerID != nil {
		_ = h.database.SqlDB.QueryRow(`
			SELECT COALESCE(SUM(bytes_in), 0), COALESCE(SUM(bytes_out), 0)
			FROM router_traffic_rollups
			WHERE router_id = ? AND record_date >= ? AND record_date <= ?
		`, *routerID, prevStartStr, prevEndStr).Scan(&prevIn, &prevOut)
	} else {
		_ = h.database.SqlDB.QueryRow(`
			SELECT COALESCE(SUM(bytes_in), 0), COALESCE(SUM(bytes_out), 0)
			FROM router_traffic_rollups
			WHERE record_date >= ? AND record_date <= ?
		`, prevStartStr, prevEndStr).Scan(&prevIn, &prevOut)
	}
	prevCycleBytes := prevIn + prevOut
	prevDays := math.Max(1.0, prevEndDt.Sub(prevStartDt).Seconds()/86400.0)
	var prevPerDay float64
	if prevCycleBytes > 0 {
		prevPerDay = float64(prevCycleBytes) / prevDays
	}

	rows, err := h.database.SqlDB.Query(`
		SELECT COALESCE(SUM(bytes_in + bytes_out), 0)
		FROM (
			SELECT record_date, bytes_in, bytes_out FROM router_traffic_rollups WHERE record_date >= ? AND record_date <= ?
			UNION ALL
			SELECT record_date, bytes_in, bytes_out FROM device_traffic_rollups WHERE record_date >= ? AND record_date <= ?
		)
		GROUP BY record_date
		ORDER BY record_date DESC
		LIMIT 7
	`, cycleStartStr, todayStr, cycleStartStr, todayStr)
	var recentDaily []float64
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var b int64
			if err := rows.Scan(&b); err == nil && b > 0 {
				recentDaily = append(recentDaily, float64(b))
			}
		}
	}
	recentMean := avgPerDay
	if len(recentDaily) > 0 {
		var sumRecent float64
		for _, v := range recentDaily {
			sumRecent += v
		}
		recentMean = sumRecent / float64(len(recentDaily))
	}

	var paceBlendWeight float64 = 1.0
	var pacePerDay float64
	var paceBasis string
	if prevCycleBytes > 0 {
		paceBlendWeight = math.Min(1.0, float64(cycleDaysElapsed)/7.0)
		pacePerDay = paceBlendWeight*recentMean + (1.0-paceBlendWeight)*prevPerDay
		paceBasis = "blended"
	} else if len(recentDaily) >= 3 {
		paceBlendWeight = 1.0
		pacePerDay = recentMean
		paceBasis = "recent"
	} else {
		paceBlendWeight = 1.0
		pacePerDay = avgPerDay
		paceBasis = "sparse"
	}

	projectedBytesAtPace := int64(float64(usedBytes) + pacePerDay*daysLeftAfterToday)

	var remainingBytes int64
	if limitBytes > usedBytes {
		remainingBytes = limitBytes - usedBytes
	}

	var usedPct float64
	var projectedPctLinear float64
	var projectedPctAtPace float64
	if limitBytes > 0 {
		usedPct = math.Round((float64(usedBytes)/float64(limitBytes))*10000) / 100
		projectedPctLinear = math.Round((float64(projectedBytesLinear)/float64(limitBytes))*1000) / 10
		projectedPctAtPace = math.Round((float64(projectedBytesAtPace)/float64(limitBytes))*1000) / 10
	}

	var projectedDailyBudget int64
	if limitBytes > 0 && daysRemaining > 0 {
		projectedDailyBudget = remainingBytes / int64(daysRemaining)
	}

	onTrack := limitBytes > 0 && projectedBytesLinear <= limitBytes

	var thresholdsReached []int
	for _, th := range thresholds {
		if usedPct >= float64(th) {
			thresholdsReached = append(thresholdsReached, th)
		}
	}
	if thresholdsReached == nil {
		thresholdsReached = []int{}
	}

	return QuotaStatusDTO{
		LimitBytes:           limitBytes,
		UsedBytes:            usedBytes,
		RemainingBytes:       remainingBytes,
		UsedPct:              usedPct,
		CycleStart:           &cycleStartStr,
		CycleEnd:             &cycleEndStr,
		CycleEndAt:           cycleEndAt,
		DaysRemaining:        daysRemaining,
		ProjectedDailyBudget: projectedDailyBudget,
		CycleDaysTotal:       cycleDaysTotal,
		CycleDaysElapsed:     cycleDaysElapsed,
		ProjectedBytesLinear: projectedBytesLinear,
		ProjectedPctLinear:   projectedPctLinear,
		PaceBytesPerDay:      int64(pacePerDay),
		ProjectedBytesAtPace: projectedBytesAtPace,
		ProjectedPctAtPace:   projectedPctAtPace,
		PrevCycleBytes:       prevCycleBytes,
		PrevCycleBytesPerDay: int64(prevPerDay),
		PaceBlendWeight:      math.Round(paceBlendWeight*100) / 100,
		PaceBasis:            paceBasis,
		OnTrack:              onTrack,
		Thresholds:           thresholds,
		ThresholdsReached:    thresholdsReached,
		Enabled:              limitBytes > 0,
		NotifyTelegram:       notifyTelegram,
		PortalURL:            portalURL,
		PortalLabel:          portalLabel,
	}
}

func (h *AnalyticsHandler) GetQuota(w http.ResponseWriter, r *http.Request) {
	var routerID *int
	if rIDStr := r.URL.Query().Get("router_id"); rIDStr != "" {
		if id, err := strconv.Atoi(rIDStr); err == nil {
			routerID = &id
		}
	}
	if routerID == nil {
		if def, err := h.database.GetDefaultRouter(); err == nil && def != nil {
			routerID = &def.ID
		}
	}

	status := h.buildQuotaStatus(routerID)
	WriteJSON(w, http.StatusOK, status)
}

func (h *AnalyticsHandler) SaveQuota(w http.ResponseWriter, r *http.Request) {
	var routerID *int
	if rIDStr := r.URL.Query().Get("router_id"); rIDStr != "" {
		if id, err := strconv.Atoi(rIDStr); err == nil {
			routerID = &id
		}
	}
	if routerID == nil {
		if def, err := h.database.GetDefaultRouter(); err == nil && def != nil {
			routerID = &def.ID
		}
	}

	var payload QuotaConfigDTO
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid quota payload")
		return
	}

	if payload.PortalURL != nil && *payload.PortalURL != "" {
		u := strings.TrimSpace(*payload.PortalURL)
		low := strings.ToLower(u)
		if !strings.HasPrefix(low, "http://") && !strings.HasPrefix(low, "https://") {
			WriteError(w, http.StatusBadRequest, "Portal URL must start with http:// or https://")
			return
		}
		if strings.Contains(u, "@") {
			WriteError(w, http.StatusBadRequest, "Portal URL must not contain embedded credentials")
			return
		}
	}

	threshStr := ""
	if len(payload.Thresholds) > 0 {
		var valid []string
		seen := make(map[int]bool)
		for _, t := range payload.Thresholds {
			if t >= 1 && t <= 100 && !seen[t] {
				seen[t] = true
				valid = append(valid, strconv.Itoa(t))
			}
		}
		threshStr = strings.Join(valid, ",")
	}

	notifyVal := "true"
	if payload.NotifyTelegram != nil && !*payload.NotifyTelegram {
		notifyVal = "false"
	}

	portalURLVal := ""
	if payload.PortalURL != nil {
		portalURLVal = strings.TrimSpace(*payload.PortalURL)
	}

	portalLabelVal := ""
	if payload.PortalLabel != nil {
		portalLabelVal = strings.TrimSpace(*payload.PortalLabel)
		if len(portalLabelVal) > 40 {
			portalLabelVal = portalLabelVal[:40]
		}
	}

	limitVal := strconv.FormatInt(max(0, payload.LimitBytes), 10)

	// Save router-scoped settings
	if routerID != nil {
		_ = h.database.SetSetting(fmt.Sprintf("quota_limit_bytes_%d", *routerID), limitVal, "ISP data limit in bytes")
		_ = h.database.SetSetting(fmt.Sprintf("quota_alert_thresholds_%d", *routerID), threshStr, "Alert thresholds")
		_ = h.database.SetSetting(fmt.Sprintf("quota_notify_telegram_%d", *routerID), notifyVal, "Notify Telegram")
		_ = h.database.SetSetting(fmt.Sprintf("isp_portal_url_%d", *routerID), portalURLVal, "ISP portal link")
		_ = h.database.SetSetting(fmt.Sprintf("isp_portal_label_%d", *routerID), portalLabelVal, "ISP portal label")
	}

	// Always maintain global fallbacks as well
	_ = h.database.SetSetting("quota_limit_bytes", limitVal, "ISP data limit in bytes")
	_ = h.database.SetSetting("quota_alert_thresholds", threshStr, "Alert thresholds")
	_ = h.database.SetSetting("quota_notify_telegram", notifyVal, "Notify Telegram")
	_ = h.database.SetSetting("isp_portal_url", portalURLVal, "ISP portal link")
	_ = h.database.SetSetting("isp_portal_label", portalLabelVal, "ISP portal label")

	status := h.buildQuotaStatus(routerID)
	WriteJSON(w, http.StatusOK, status)
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

type UserDestinationStatItem struct {
	ID            int       `json:"id"`
	UserID        int       `json:"user_id"`
	DeviceID      int       `json:"device_id"`
	DestinationIP string    `json:"destination_ip"`
	Domain        string    `json:"domain"`
	CountryCode   string    `json:"country_code"`
	CountryName   string    `json:"country_name"`
	FlagEmoji     string    `json:"flag_emoji"`
	BytesIn       int64     `json:"bytes_in"`
	BytesOut      int64     `json:"bytes_out"`
	TotalBytes    int64     `json:"total_bytes"`
	HitCount      int       `json:"hit_count"`
	LastSeen      time.Time `json:"last_seen"`
}

func (h *AnalyticsHandler) UserDestinations(w http.ResponseWriter, r *http.Request) {
	WriteJSON(w, http.StatusOK, []UserDestinationStatItem{})
}

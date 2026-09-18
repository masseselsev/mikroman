package api

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

type MetricsHandler struct {
	database *db.DB
	client   *routeros.Client
	mu       sync.Mutex
	clients  map[int]*routeros.Client
}

func NewMetricsHandler(database *db.DB, client *routeros.Client) *MetricsHandler {
	clients := make(map[int]*routeros.Client)
	if client != nil {
		if def, err := database.GetDefaultRouter(); err == nil && def != nil {
			clients[def.ID] = client
		} else if routers, err := database.GetRouters(); err == nil && len(routers) == 1 {
			clients[routers[0].ID] = client
		}
	}
	return &MetricsHandler{
		database: database,
		client:   client,
		clients:  clients,
	}
}

func (h *MetricsHandler) getClient(routerID *int) (*routeros.Client, error) {
	h.mu.Lock()
	defer h.mu.Unlock()

	var targetID int
	if routerID != nil && *routerID > 0 {
		targetID = *routerID
	} else {
		def, err := h.database.GetDefaultRouter()
		if err != nil || def == nil {
			if h.client != nil {
				return h.client, nil
			}
			return nil, fmt.Errorf("no default router configured")
		}
		targetID = def.ID
	}

	if c, ok := h.clients[targetID]; ok && c != nil {
		return c, nil
	}

	router, err := h.database.GetRouter(targetID)
	if err != nil || router == nil {
		if h.client != nil {
			return h.client, nil
		}
		return nil, fmt.Errorf("router %d not found", targetID)
	}

	if h.client != nil && h.client.Matches(router.Host, router.Port) {
		h.clients[targetID] = h.client
		return h.client, nil
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

	h.clients[targetID] = newClient
	return newClient, nil
}

type MonitoredInterfacesConfigDTO struct {
	RouterID                   *int     `json:"router_id,omitempty"`
	SelectedInterfaces         []string `json:"selected_interfaces"`
	IgnoredDiscoveryInterfaces []string `json:"ignored_discovery_interfaces"`
}

func (h *MetricsHandler) GetMonitoredInterfacesConfig(w http.ResponseWriter, r *http.Request) {
	rID := r.URL.Query().Get("router_id")
	settingKey := "monitored_interfaces_default"
	ignKey := "ignored_discovery_interfaces_default"
	if rID != "" {
		settingKey = fmt.Sprintf("monitored_interfaces_%s", rID)
		ignKey = fmt.Sprintf("ignored_discovery_interfaces_%s", rID)
	}

	val, err := h.database.GetSetting(settingKey)
	var selected []string
	if err == nil && val != "" {
		_ = json.Unmarshal([]byte(val), &selected)
	}
	if selected == nil {
		selected = []string{}
	}

	ignVal, err := h.database.GetSetting(ignKey)
	var ignored []string
	if err == nil && ignVal != "" {
		_ = json.Unmarshal([]byte(ignVal), &ignored)
	}
	if ignored == nil {
		ignored = []string{}
	}

	var routerID *int
	if id, err := strconv.Atoi(rID); err == nil {
		routerID = &id
	}

	WriteJSON(w, http.StatusOK, MonitoredInterfacesConfigDTO{
		RouterID:                   routerID,
		SelectedInterfaces:         selected,
		IgnoredDiscoveryInterfaces: ignored,
	})
}

func (h *MetricsHandler) SaveMonitoredInterfacesConfig(w http.ResponseWriter, r *http.Request) {
	var payload MonitoredInterfacesConfigDTO
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid payload")
		return
	}

	settingKey := "monitored_interfaces_default"
	ignKey := "ignored_discovery_interfaces_default"
	if payload.RouterID != nil {
		settingKey = fmt.Sprintf("monitored_interfaces_%d", *payload.RouterID)
		ignKey = fmt.Sprintf("ignored_discovery_interfaces_%d", *payload.RouterID)
	}

	if payload.SelectedInterfaces != nil {
		bytesVal, _ := json.Marshal(payload.SelectedInterfaces)
		_ = h.database.SetSetting(settingKey, string(bytesVal), "Monitored WAN interfaces")
	}

	if payload.IgnoredDiscoveryInterfaces != nil {
		ignBytes, _ := json.Marshal(payload.IgnoredDiscoveryInterfaces)
		_ = h.database.SetSetting(ignKey, string(ignBytes), "Interfaces excluded from device discovery")
	}

	WriteJSON(w, http.StatusOK, payload)
}

func isTunnelIface(name, ifaceType string) bool {
	lower := strings.ToLower(name)
	tLower := strings.ToLower(ifaceType)
	return strings.HasPrefix(lower, "zt") || strings.HasPrefix(lower, "zerotier") ||
		strings.HasPrefix(lower, "wg") || strings.HasPrefix(lower, "wireguard") ||
		strings.HasPrefix(lower, "ovpn") || strings.HasPrefix(lower, "tun") ||
		strings.HasPrefix(lower, "tap") || strings.HasPrefix(lower, "gre") ||
		strings.HasPrefix(lower, "eoip") || strings.HasPrefix(lower, "ppp") ||
		strings.HasPrefix(lower, "sstp") || strings.HasPrefix(lower, "l2tp") ||
		strings.HasPrefix(lower, "ipip") || strings.Contains(tLower, "tunnel") ||
		strings.Contains(tLower, "wireguard") || strings.Contains(tLower, "ovpn") ||
		strings.Contains(tLower, "ppp")
}

func (h *MetricsHandler) ListAvailableInterfaces(w http.ResponseWriter, r *http.Request) {
	var routerID *int
	if rID := r.URL.Query().Get("router_id"); rID != "" {
		if id, err := strconv.Atoi(rID); err == nil && id > 0 {
			routerID = &id
		}
	}

	client, err := h.getClient(routerID)
	if err != nil || client == nil {
		WriteJSON(w, http.StatusOK, []InterfaceDTO{})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	rawIfaces, err := client.GetInterfaces(ctx)
	if err != nil {
		WriteJSON(w, http.StatusOK, []InterfaceDTO{})
		return
	}

	dtos := make([]InterfaceDTO, 0, len(rawIfaces))
	for _, iface := range rawIfaces {
		rx, _ := strconv.ParseInt(iface.RxByte, 10, 64)
		tx, _ := strconv.ParseInt(iface.TxByte, 10, 64)
		dtos = append(dtos, InterfaceDTO{
			ID:        iface.ID,
			Name:      iface.Name,
			Type:      iface.Type,
			Running:   iface.Running == "true",
			Disabled:  iface.Disabled == "true",
			Comment:   iface.Comment,
			RxByte:    rx,
			TxByte:    tx,
			ActualMTU: iface.ActualMTU,
			IsTunnel:  isTunnelIface(iface.Name, iface.Type),
		})
	}
	WriteJSON(w, http.StatusOK, dtos)
}

type SystemMetricPoint struct {
	Timestamp       string   `json:"timestamp"`
	CPULoad         float64  `json:"cpu_load"`
	CPUPeak         float64  `json:"cpu_peak"`
	MemoryUsagePct  float64  `json:"memory_usage_pct"`
	MemoryUsedMB    float64  `json:"memory_used_mb"`
	MemoryTotalMB   float64  `json:"memory_total_mb"`
	Temperature     *float64 `json:"temperature"`
	TemperaturePeak *float64 `json:"temperature_peak"`
	Voltage         *float64 `json:"voltage"`
	VoltageMin      *float64 `json:"voltage_min"`
	VoltageMax      *float64 `json:"voltage_max"`
}

type SystemMetricsResponse struct {
	Range          string              `json:"range"`
	Points         []SystemMetricPoint `json:"points"`
	CurrentCPU     *float64            `json:"current_cpu"`
	CurrentRAMPct  *float64            `json:"current_ram_pct"`
	CurrentTemp    *float64            `json:"current_temp"`
	CurrentVoltage *float64            `json:"current_voltage"`
	BucketSeconds  int                 `json:"bucket_seconds"`
}

type InterfaceRatePoint struct {
	Timestamp       string  `json:"timestamp"`
	RxRateBps       float64 `json:"rx_rate_bps"`
	TxRateBps       float64 `json:"tx_rate_bps"`
	RxRateFormatted string  `json:"rx_rate_formatted"`
	TxRateFormatted string  `json:"tx_rate_formatted"`
	RxPeakBps       float64 `json:"rx_peak_bps"`
	TxPeakBps       float64 `json:"tx_peak_bps"`
	RxPeakFormatted string  `json:"rx_peak_formatted"`
	TxPeakFormatted string  `json:"tx_peak_formatted"`
}

type InterfaceHistoryResponse struct {
	Range         string               `json:"range"`
	Interfaces    []string             `json:"interfaces"`
	IsSummed      bool                 `json:"is_summed"`
	Points        []InterfaceRatePoint `json:"points"`
	CurrentRxBps  float64              `json:"current_rx_bps"`
	CurrentTxBps  float64              `json:"current_tx_bps"`
	BucketSeconds int                  `json:"bucket_seconds"`
}

type rangeConfig struct {
	duration      time.Duration
	bucketSeconds int
}

var rangeConfigs = map[string]rangeConfig{
	"1h":  {duration: 1 * time.Hour, bucketSeconds: 60},
	"6h":  {duration: 6 * time.Hour, bucketSeconds: 300},
	"24h": {duration: 24 * time.Hour, bucketSeconds: 900},
	"7d":  {duration: 7 * 24 * time.Hour, bucketSeconds: 3600},
	"30d": {duration: 30 * 24 * time.Hour, bucketSeconds: 14400},
}

func roundFloat(val float64, precision int) float64 {
	ratio := math.Pow(10, float64(precision))
	return math.Round(val*ratio) / ratio
}

// bucketsServeRange reports whether a range is served from the pre-aggregated 15-minute
// bucket tables. The short ranges (1 h, 6 h) stay on the raw tables because their windows
// are small enough that the raw scan is already the cheaper query (~7 ms and ~15 ms on a
// 30-day synthetic database); the long ranges are where the scan of every raw sample in
// the window dominated the request.
func bucketsServeRange(rangeKey string) bool {
	switch rangeKey {
	case "24h", "7d", "30d":
		return true
	}
	return false
}

// bucketsCoverRange checks whether the bucket tables actually have rows covering a range
// on a router. Immediately after an upgrade the backfill may not have run (or only
// partially), and a chart must not silently go empty: the handler falls back to the raw
// tables until buckets exist for the range.
func bucketsCoverRange(database *db.DB, routerID int, startTime string) bool {
	var sysCount int
	if err := database.SqlDB.QueryRow(
		"SELECT count(*) FROM system_metric_buckets WHERE router_id = ? AND bucket_start >= ? AND bucket_start <= ?",
		routerID, startTime, time.Now().UTC().Format("2006-01-02 15:04:05")).Scan(&sysCount); err != nil {
		return false
	}
	return sysCount > 0
}

// countInterfaceBucketsInRange reports how many interface bucket rows exist for a router
// within a range; the interface handler uses it for the same empty-range fallback as
// bucketsCoverRange.
func countInterfaceBucketsInRange(database *db.DB, routerID int, startTime string) (int, error) {
	var count int
	err := database.SqlDB.QueryRow(
		"SELECT count(*) FROM interface_metric_buckets WHERE router_id = ? AND bucket_start >= ?",
		routerID, startTime).Scan(&count)
	return count, err
}

// placeholdersOf renders a comma-joined "?, ?, …" list for an IN clause of n items.
func placeholdersOf(n int) string {
	if n <= 0 {
		return "''"
	}
	items := make([]string, n)
	for i := range items {
		items[i] = "?"
	}
	return strings.Join(items, ",")
}

// scanInterfacePoints turns the (identically shaped) rows of either the raw-sample
// query or the bucket-reaggregated query into chart points. Both emit the same six
// columns — bucket id, newest timestamp, then avg/peak pairs per direction — so the JSON
// response is byte-identical whichever path served the range.
func scanInterfacePoints(rows *sql.Rows, points *[]InterfaceRatePoint) {
	for rows.Next() {
		var bucket int64
		var tsEpoch int64
		var rxAvg, rxPeak, txAvg, txPeak sql.NullFloat64

		if err := rows.Scan(&bucket, &tsEpoch, &rxAvg, &rxPeak, &txAvg, &txPeak); err != nil {
			continue
		}

		rxVal := roundFloat(rxAvg.Float64, 1)
		txVal := roundFloat(txAvg.Float64, 1)
		rxP := roundFloat(rxPeak.Float64, 1)
		txP := roundFloat(txPeak.Float64, 1)

		*points = append(*points, InterfaceRatePoint{
			Timestamp:       time.Unix(tsEpoch, 0).UTC().Format("2006-01-02T15:04:05Z"),
			RxRateBps:       rxVal,
			TxRateBps:       txVal,
			RxRateFormatted: formatRateBps(rxVal),
			TxRateFormatted: formatRateBps(txVal),
			RxPeakBps:       rxP,
			TxPeakBps:       txP,
			RxPeakFormatted: formatRateBps(rxP),
			TxPeakFormatted: formatRateBps(txP),
		})
	}
}

func formatRateBps(bps float64) string {
	if bps >= 1_000_000_000 {
		return fmt.Sprintf("%.2f Gbps", bps/1_000_000_000)
	} else if bps >= 1_000_000 {
		return fmt.Sprintf("%.2f Mbps", bps/1_000_000)
	} else if bps >= 1_000 {
		return fmt.Sprintf("%.1f Kbps", bps/1_000)
	}
	return fmt.Sprintf("%.0f bps", bps)
}

// scanSystemPoints turns the (identically shaped) rows of either the raw-sample query or
// the bucket-reaggregated query into chart points. Both queries emit the same 12 columns
// — bucket id, newest timestamp, then avg/peak pairs per metric — which is what lets the
// JSON response stay byte-identical whichever path served the range.
func scanSystemPoints(rows *sql.Rows, points *[]SystemMetricPoint) {
	for rows.Next() {
		var bucket int64
		var tsEpoch int64
		var cpuAvg, cpuPeak, ramAvg, ramUsedAvg, ramTotal sql.NullFloat64
		var tempAvg, tempPeak, voltAvg, voltMin, voltMax sql.NullFloat64

		if err := rows.Scan(&bucket, &tsEpoch, &cpuAvg, &cpuPeak, &ramAvg, &ramUsedAvg, &ramTotal,
			&tempAvg, &tempPeak, &voltAvg, &voltMin, &voltMax); err != nil {
			continue
		}

		point := SystemMetricPoint{
			Timestamp:      time.Unix(tsEpoch, 0).UTC().Format("2006-01-02T15:04:05Z"),
			CPULoad:        roundFloat(cpuAvg.Float64, 1),
			CPUPeak:        roundFloat(cpuPeak.Float64, 1),
			MemoryUsagePct: roundFloat(ramAvg.Float64, 1),
			MemoryUsedMB:   roundFloat(ramUsedAvg.Float64/(1024*1024), 1),
			MemoryTotalMB:  roundFloat(ramTotal.Float64/(1024*1024), 1),
		}
		if tempAvg.Valid {
			v := roundFloat(tempAvg.Float64, 1)
			point.Temperature = &v
		}
		if tempPeak.Valid {
			v := roundFloat(tempPeak.Float64, 1)
			point.TemperaturePeak = &v
		}
		if voltAvg.Valid {
			v := roundFloat(voltAvg.Float64, 1)
			point.Voltage = &v
		}
		if voltMin.Valid {
			v := roundFloat(voltMin.Float64, 1)
			point.VoltageMin = &v
		}
		if voltMax.Valid {
			v := roundFloat(voltMax.Float64, 1)
			point.VoltageMax = &v
		}
		*points = append(*points, point)
	}
}

func (h *MetricsHandler) GetSystemMetrics(w http.ResponseWriter, r *http.Request) {
	rangeKey := r.URL.Query().Get("range")
	cfg, ok := rangeConfigs[rangeKey]
	if !ok {
		rangeKey = "1h"
		cfg = rangeConfigs["1h"]
	}

	var routerID *int
	if rID := r.URL.Query().Get("router_id"); rID != "" {
		if id, err := strconv.Atoi(rID); err == nil && id > 0 {
			routerID = &id
		}
	}
	if routerID == nil {
		if def, err := h.database.GetDefaultRouter(); err == nil && def != nil {
			routerID = &def.ID
		}
	}

	now := time.Now().UTC()
	startTime := now.Add(-cfg.duration).Format("2006-01-02 15:04:05")

	whereClause := "timestamp >= ?"
	args := []interface{}{startTime}
	if routerID != nil {
		whereClause += " AND router_id = ?"
		args = append(args, *routerID)
	}

	points := []SystemMetricPoint{}

	// Long ranges are served from the pre-aggregated 15-minute buckets; the raw table is
	// scanned only for the short ranges where it is already the cheaper query, and as a
	// fallback when the bucket range is empty (e.g. right after deploy, before the
	// backfill ran, so a chart never silently goes empty).
	useBuckets := routerID != nil && bucketsServeRange(rangeKey) && bucketsCoverRange(h.database, *routerID, startTime)

	if useBuckets {
		bucketQuery := fmt.Sprintf(`
			SELECT
				cast(strftime('%%s', bucket_start) as integer) / %d AS bucket,
				strftime('%%s', last_seen) AS ts_epoch,
				sum(cpu_load_avg * samples) / sum(samples),
				max(cpu_load_max),
				sum(memory_usage_pct_avg * samples) / sum(samples),
				sum(memory_used_bytes_avg * samples) / sum(samples),
				max(memory_total_bytes_max),
				sum(temperature_avg * samples) / nullif(sum(case when temperature_avg is not null then samples else 0 end), 0),
				max(temperature_max),
				sum(voltage_avg * samples) / nullif(sum(case when voltage_avg is not null then samples else 0 end), 0),
				min(voltage_min),
				max(voltage_max)
			FROM system_metric_buckets
			WHERE router_id = ? AND bucket_start >= datetime(?, '-900 seconds')
			GROUP BY bucket
			ORDER BY bucket ASC
		`, cfg.bucketSeconds)
		rows, err := h.database.SqlDB.Query(bucketQuery, *routerID, startTime)
		if err != nil {
			WriteJSON(w, http.StatusOK, SystemMetricsResponse{
				Range:         rangeKey,
				Points:        []SystemMetricPoint{},
				BucketSeconds: cfg.bucketSeconds,
			})
			return
		}
		defer rows.Close()
		scanSystemPoints(rows, &points)
	} else {
		query := fmt.Sprintf(`
			SELECT
				cast(strftime('%%s', timestamp) as integer) / %d AS bucket,
				max(strftime('%%s', timestamp)) AS ts_epoch,
				avg(cpu_load),
				max(cpu_load),
				avg(memory_usage_pct),
				avg(memory_used_bytes),
				max(memory_total_bytes),
				avg(temperature),
				max(temperature),
				avg(voltage),
				min(voltage),
				max(voltage)
			FROM system_metrics
			WHERE %s
			GROUP BY bucket
			ORDER BY bucket ASC
		`, cfg.bucketSeconds, whereClause)

		rows, err := h.database.SqlDB.Query(query, args...)
		if err != nil {
			WriteJSON(w, http.StatusOK, SystemMetricsResponse{
				Range:         rangeKey,
				Points:        []SystemMetricPoint{},
				BucketSeconds: cfg.bucketSeconds,
			})
			return
		}
		defer rows.Close()
		scanSystemPoints(rows, &points)
	}

	var curCPU, curRAM, curTemp, curVolt *float64
	curQuery := fmt.Sprintf(`
		SELECT cpu_load, memory_usage_pct, temperature, voltage
		FROM system_metrics
		WHERE %s
		ORDER BY timestamp DESC LIMIT 1
	`, whereClause)
	var cCPU, cRAM, cTemp, cVolt sql.NullFloat64
	if err := h.database.SqlDB.QueryRow(curQuery, args...).Scan(&cCPU, &cRAM, &cTemp, &cVolt); err == nil {
		if cCPU.Valid {
			v := roundFloat(cCPU.Float64, 1)
			curCPU = &v
		}
		if cRAM.Valid {
			v := roundFloat(cRAM.Float64, 1)
			curRAM = &v
		}
		if cTemp.Valid {
			v := roundFloat(cTemp.Float64, 1)
			curTemp = &v
		}
		if cVolt.Valid {
			v := roundFloat(cVolt.Float64, 1)
			curVolt = &v
		}
	}

	WriteJSON(w, http.StatusOK, SystemMetricsResponse{
		Range:          rangeKey,
		Points:         points,
		CurrentCPU:     curCPU,
		CurrentRAMPct:  curRAM,
		CurrentTemp:    curTemp,
		CurrentVoltage: curVolt,
		BucketSeconds:  cfg.bucketSeconds,
	})
}

func (h *MetricsHandler) GetInterfaceMetrics(w http.ResponseWriter, r *http.Request) {
	rangeKey := r.URL.Query().Get("range")
	cfg, ok := rangeConfigs[rangeKey]
	if !ok {
		rangeKey = "1h"
		cfg = rangeConfigs["1h"]
	}

	var routerID *int
	if rID := r.URL.Query().Get("router_id"); rID != "" {
		if id, err := strconv.Atoi(rID); err == nil && id > 0 {
			routerID = &id
		}
	}
	if routerID == nil {
		if def, err := h.database.GetDefaultRouter(); err == nil && def != nil {
			routerID = &def.ID
		}
	}

	ifacesParam := r.URL.Query().Get("interfaces")
	hasIfacesParam := r.URL.Query().Has("interfaces")

	var selectedList []string
	if hasIfacesParam {
		trimmed := strings.TrimSpace(ifacesParam)
		if trimmed == "" {
			WriteJSON(w, http.StatusOK, InterfaceHistoryResponse{
				Range:         rangeKey,
				Interfaces:    []string{},
				IsSummed:      true,
				Points:        []InterfaceRatePoint{},
				CurrentRxBps:  0,
				CurrentTxBps:  0,
				BucketSeconds: cfg.bucketSeconds,
			})
			return
		}
		for _, item := range strings.Split(trimmed, ",") {
			if s := strings.TrimSpace(item); s != "" {
				selectedList = append(selectedList, s)
			}
		}
	} else if routerID != nil {
		settingKey := fmt.Sprintf("monitored_interfaces_%d", *routerID)
		val, err := h.database.GetSetting(settingKey)
		if err == nil && val != "" {
			_ = json.Unmarshal([]byte(val), &selectedList)
		}
		if len(selectedList) == 0 {
			valDef, _ := h.database.GetSetting("monitored_interfaces_default")
			if valDef != "" {
				_ = json.Unmarshal([]byte(valDef), &selectedList)
			}
		}
	}

	now := time.Now().UTC()
	startTime := now.Add(-cfg.duration).Format("2006-01-02 15:04:05")

	whereParts := []string{"timestamp >= ?"}
	args := []interface{}{startTime}
	if routerID != nil {
		whereParts = append(whereParts, "router_id = ?")
		args = append(args, *routerID)
	}
	if len(selectedList) > 0 {
		placeholders := make([]string, len(selectedList))
		for i, s := range selectedList {
			placeholders[i] = "?"
			args = append(args, s)
		}
		whereParts = append(whereParts, fmt.Sprintf("interface_name IN (%s)", strings.Join(placeholders, ",")))
	}

	whereClause := strings.Join(whereParts, " AND ")

	if len(selectedList) == 0 {
		// No interface list was pinned (and no monitored setting): discover the names from
		// the bucket table first — cheap for long ranges — and only fall back to the raw
		// table's DISTINCT scan when buckets are empty for the range.
		if routerID != nil {
			rows, err := h.database.SqlDB.Query(
				"SELECT DISTINCT interface_name FROM interface_metric_buckets WHERE router_id = ? AND bucket_start >= ?",
				*routerID, startTime)
			if err == nil {
				for rows.Next() {
					var name string
					if err := rows.Scan(&name); err == nil && name != "" {
						selectedList = append(selectedList, name)
					}
				}
				rows.Close()
			}
		}
	}

	if len(selectedList) == 0 {
		rows, err := h.database.SqlDB.Query(fmt.Sprintf("SELECT DISTINCT interface_name FROM interface_metrics WHERE %s", whereClause), args...)
		if err == nil {
			for rows.Next() {
				var name string
				if err := rows.Scan(&name); err == nil && name != "" {
					selectedList = append(selectedList, name)
				}
			}
			rows.Close()
		}
	}

	points := []InterfaceRatePoint{}

	// Long ranges are served from the pre-aggregated 15-minute buckets; the raw table is
	// scanned only for the short ranges where it is already the cheaper query, and as a
	// fallback when the bucket range is empty (e.g. right after deploy, before the
	// backfill ran, so a chart never silently goes empty).
	useBuckets := false
	if routerID != nil && bucketsServeRange(rangeKey) && len(selectedList) > 0 {
		if count, err := countInterfaceBucketsInRange(h.database, *routerID, startTime); err == nil && count > 0 {
			useBuckets = true
		}
	}

	if useBuckets {
		// Rebuild the chart's two-level aggregation from the per-interface buckets. The
		// inner level of the raw query summed the selected interfaces per sample instant;
		// summing the per-interface bucket sums reproduces exactly that sum, and dividing
		// by the instant count (the sample count of one interface — every interface in a
		// bucket sampled the same instants) reproduces its mean. The peak of the summed
		// rate is the max over per-instant totals, which equals the sum of per-interface
		// maxima only when all interfaces peaked at the same instant; when they did not,
		// the honest reconstruction is the largest per-instant total, so the buckets
		// additionally carry that sum: sum of the per-interface maxima would be the
		// "invented combined spike" the chart's semantics exist to avoid, and the max of
		// per-interface peaks would understate a genuine simultaneous load. The bucket
		// row stores the true summed peak at write time (rx_sum_rate_bps_max), so the
		// read side just takes its max.
		bucketQuery := fmt.Sprintf(`
			SELECT
				cast(strftime('%%s', bucket_start) as integer) / %d AS bucket,
				strftime('%%s', last_seen) AS ts_epoch,
				sum(rx_rate_bps_sum) / nullif(max(samples), 0) AS rx_avg,
				max(rx_rate_sum_bps_max) AS rx_peak,
				sum(tx_rate_bps_sum) / nullif(max(samples), 0) AS tx_avg,
				max(tx_rate_sum_bps_max) AS tx_peak
			FROM interface_metric_buckets
			WHERE router_id = ? AND bucket_start >= datetime(?, '-900 seconds') AND interface_name IN (%s)
			GROUP BY bucket
			ORDER BY bucket ASC
		`, cfg.bucketSeconds, placeholdersOf(len(selectedList)))

		bucketArgs := make([]interface{}, 0, len(selectedList)+2)
		bucketArgs = append(bucketArgs, *routerID, startTime)
		for _, s := range selectedList {
			bucketArgs = append(bucketArgs, s)
		}

		rows, err := h.database.SqlDB.Query(bucketQuery, bucketArgs...)
		if err != nil {
			WriteJSON(w, http.StatusOK, InterfaceHistoryResponse{
				Range:         rangeKey,
				Interfaces:    selectedList,
				IsSummed:      true,
				Points:        []InterfaceRatePoint{},
				BucketSeconds: cfg.bucketSeconds,
			})
			return
		}
		defer rows.Close()
		scanInterfacePoints(rows, &points)
	} else {
		query := fmt.Sprintf(`
			SELECT
				cast(strftime('%%s', timestamp) as integer) / %d AS bucket,
				max(strftime('%%s', timestamp)) AS ts_epoch,
				avg(rx) AS rx_avg,
				max(rx) AS rx_peak,
				avg(tx) AS tx_avg,
				max(tx) AS tx_peak
			FROM (
				SELECT
					timestamp,
					sum(rx_rate_bps) AS rx,
					sum(tx_rate_bps) AS tx
				FROM interface_metrics
				WHERE %s
				GROUP BY timestamp
			)
			GROUP BY bucket
			ORDER BY bucket ASC
		`, cfg.bucketSeconds, whereClause)

		rows, err := h.database.SqlDB.Query(query, args...)
		if err != nil {
			WriteJSON(w, http.StatusOK, InterfaceHistoryResponse{
				Range:         rangeKey,
				Interfaces:    selectedList,
				IsSummed:      true,
				Points:        []InterfaceRatePoint{},
				BucketSeconds: cfg.bucketSeconds,
			})
			return
		}
		defer rows.Close()
		scanInterfacePoints(rows, &points)
	}

	var curRx, curTx float64
	curQuery := fmt.Sprintf(`
		SELECT sum(rx_rate_bps), sum(tx_rate_bps)
		FROM interface_metrics
		WHERE %s AND timestamp = (
			SELECT max(timestamp) FROM interface_metrics WHERE %s
		)
	`, whereClause, whereClause)

	curArgs := append(args, args...)
	var cRx, cTx sql.NullFloat64
	if err := h.database.SqlDB.QueryRow(curQuery, curArgs...).Scan(&cRx, &cTx); err == nil {
		curRx = roundFloat(cRx.Float64, 1)
		curTx = roundFloat(cTx.Float64, 1)
	}

	WriteJSON(w, http.StatusOK, InterfaceHistoryResponse{
		Range:         rangeKey,
		Interfaces:    selectedList,
		IsSummed:      true,
		Points:        points,
		CurrentRxBps:  curRx,
		CurrentTxBps:  curTx,
		BucketSeconds: cfg.bucketSeconds,
	})
}

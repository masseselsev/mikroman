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
	"time"

	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

type MetricsHandler struct {
	database *db.DB
	client   *routeros.Client
}

func NewMetricsHandler(database *db.DB, client *routeros.Client) *MetricsHandler {
	return &MetricsHandler{database: database, client: client}
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
	if h.client == nil {
		WriteJSON(w, http.StatusOK, []InterfaceDTO{})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	rawIfaces, err := h.client.GetInterfaces(ctx)
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

	points := []SystemMetricPoint{}
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
		points = append(points, point)
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

	points := []InterfaceRatePoint{}
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

		points = append(points, InterfaceRatePoint{
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


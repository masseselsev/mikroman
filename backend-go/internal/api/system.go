package api

import (
	"context"
	"encoding/json"
	"net/http"
	"runtime"
	"strconv"
	"time"

	"github.com/masseselsev/mikroman/internal/config"
	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

var startedAt = time.Now()

// TelegramReloader allows dynamic reconfiguration of the Telegram bot service.
type TelegramReloader interface {
	Reconfigure()
}

type SystemHandler struct {
	cfg              *config.Config
	database         *db.DB
	client           *routeros.Client
	telegramReloader TelegramReloader
}

func NewSystemHandler(cfg *config.Config, database *db.DB, client *routeros.Client) *SystemHandler {
	return &SystemHandler{
		cfg:      cfg,
		database: database,
		client:   client,
	}
}

func (h *SystemHandler) SetTelegramReloader(r TelegramReloader) {
	h.telegramReloader = r
}

func (h *SystemHandler) GetHealth(w http.ResponseWriter, r *http.Request) {
	WriteJSON(w, http.StatusOK, map[string]string{
		"status":  "healthy",
		"version": h.cfg.AppVersion,
	})
}

func (h *SystemHandler) GetDiagnostics(w http.ResponseWriter, r *http.Request) {
	var m runtime.MemStats
	runtime.ReadMemStats(&m)

	WriteJSON(w, http.StatusOK, map[string]interface{}{
		"uptime_seconds":    time.Since(startedAt).Seconds(),
		"memory_bytes":      m.Alloc,
		"memory_sys_bytes":  m.Sys,
		"goroutines":        runtime.NumGoroutine(),
		"memory_note":       "Pure Go memory footprint: minimal RSS with zero Python GIL / thread thrashing.",
		"backend_version":   h.cfg.AppVersion,
	})
}

func (h *SystemHandler) GetSettings(w http.ResponseWriter, r *http.Request) {
	rows, err := h.database.SqlDB.Query("SELECT key, value, description, updated_at FROM app_settings")
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to load settings")
		return
	}
	defer rows.Close()

	settings := make(map[string]interface{})
	for rows.Next() {
		var s db.AppSetting
		if err := rows.Scan(&s.Key, &s.Value, &s.Description, &s.UpdatedAt); err == nil {
			settings[s.Key] = s.Value
		}
	}

	// Supply production defaults if not stored in DB
	if _, ok := settings["telemetry_interval_seconds"]; !ok {
		settings["telemetry_interval_seconds"] = "3"
	}
	if _, ok := settings["poll_interval_seconds"]; !ok {
		settings["poll_interval_seconds"] = "10"
	}
	if _, ok := settings["heavy_sync_interval_seconds"]; !ok {
		settings["heavy_sync_interval_seconds"] = "60"
	}
	if _, ok := settings["unassigned_device_speed_limit"]; !ok {
		settings["unassigned_device_speed_limit"] = "5M/5M"
	}
	if _, ok := settings["temp_warning_threshold"]; !ok {
		settings["temp_warning_threshold"] = "80"
	}
	if _, ok := settings["auto_scan_enabled"]; !ok {
		settings["auto_scan_enabled"] = "true"
	}
	if _, ok := settings["alert_cpu_threshold"]; !ok {
		settings["alert_cpu_threshold"] = "90"
	}
	if _, ok := settings["alert_new_device_enabled"]; !ok {
		settings["alert_new_device_enabled"] = "true"
	}
	if _, ok := settings["telegram_lang"]; !ok {
		settings["telegram_lang"] = "en"
	}
	if _, ok := settings["pause_allowed_networks"]; !ok {
		settings["pause_allowed_networks"] = "192.168.0.0/16, 10.0.0.0/8, 172.16.0.0/12"
	}
	if _, ok := settings["traffic_accounting_scope"]; !ok {
		settings["traffic_accounting_scope"] = "wan_only"
	}
	if _, ok := settings["backup_enabled"]; !ok {
		settings["backup_enabled"] = "true"
	}
	if _, ok := settings["backup_interval_hours"]; !ok {
		settings["backup_interval_hours"] = "24"
	}
	if _, ok := settings["backup_retention_days"]; !ok {
		settings["backup_retention_days"] = "90"
	}
	if _, ok := settings["backup_max_count"]; !ok {
		settings["backup_max_count"] = "30"
	}
	if _, ok := settings["log_scraping_enabled"]; !ok {
		settings["log_scraping_enabled"] = "true"
	}
	if _, ok := settings["log_retention_days"]; !ok {
		settings["log_retention_days"] = "14"
	}
	if _, ok := settings["telegram_mode"]; !ok {
		settings["telegram_mode"] = "polling"
	}
	if _, ok := settings["telegram_webhook_url"]; !ok {
		settings["telegram_webhook_url"] = ""
	}
	if _, ok := settings["telegram_bot_token"]; !ok {
		settings["telegram_bot_token"] = ""
	}
	if _, ok := settings["telegram_admin_ids"]; !ok {
		settings["telegram_admin_ids"] = ""
	}

	WriteJSON(w, http.StatusOK, settings)
}

func (h *SystemHandler) SaveSettings(w http.ResponseWriter, r *http.Request) {
	var payload map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid request payload")
		return
	}

	for k, v := range payload {
		strVal := ""
		switch val := v.(type) {
		case string:
			strVal = val
		case bool:
			if val {
				strVal = "true"
			} else {
				strVal = "false"
			}
		default:
			bytesVal, _ := json.Marshal(v)
			strVal = string(bytesVal)
		}
		_ = h.database.SetSetting(k, strVal, "")
	}

	if h.telegramReloader != nil {
		go h.telegramReloader.Reconfigure()
	}

	WriteJSON(w, http.StatusOK, map[string]string{"message": "Settings saved successfully"})
}

func (h *SystemHandler) Reboot(w http.ResponseWriter, r *http.Request) {
	if h.client == nil {
		WriteError(w, http.StatusBadRequest, "No active router connected to reboot")
		return
	}

	if err := h.client.Reboot(r.Context()); err != nil {
		WriteError(w, http.StatusInternalServerError, err.Error())
		return
	}

	WriteJSON(w, http.StatusOK, map[string]string{"message": "Reboot command sent to router"})
}

// AlertDTO models system alert logs for the frontend.
type AlertDTO struct {
	ID              int                    `json:"id"`
	AlertType       string                 `json:"alert_type"`
	Message         string                 `json:"message"`
	MetadataPayload map[string]interface{} `json:"metadata_payload,omitempty"`
	CreatedAt       string                 `json:"created_at"`
}

// GetAlerts returns recent alert log items.
func (h *SystemHandler) GetAlerts(w http.ResponseWriter, r *http.Request) {
	rows, err := h.database.SqlDB.Query("SELECT id, alert_type, message, created_at FROM alert_logs ORDER BY created_at DESC LIMIT 50")
	if err != nil {
		WriteJSON(w, http.StatusOK, []AlertDTO{})
		return
	}
	defer rows.Close()

	alerts := make([]AlertDTO, 0)
	for rows.Next() {
		var a AlertDTO
		if err := rows.Scan(&a.ID, &a.AlertType, &a.Message, &a.CreatedAt); err == nil {
			alerts = append(alerts, a)
		}
	}

	WriteJSON(w, http.StatusOK, alerts)
}

// InterfaceDTO represents a network interface on the router.
type InterfaceDTO struct {
	ID        string `json:"id,omitempty"`
	Name      string `json:"name"`
	Type      string `json:"type,omitempty"`
	Running   bool   `json:"running"`
	Disabled  bool   `json:"disabled"`
	Comment   string `json:"comment,omitempty"`
	RxByte    int64  `json:"rx_byte"`
	TxByte    int64  `json:"tx_byte"`
	ActualMTU string `json:"actual_mtu,omitempty"`
}

// GetInterfaces returns interface statistics from the connected router.
func (h *SystemHandler) GetInterfaces(w http.ResponseWriter, r *http.Request) {
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
		})
	}

	WriteJSON(w, http.StatusOK, dtos)
}

// GetSystemStatus fetches hardware status, RouterBOARD info, and health sensors.
func (h *SystemHandler) GetSystemStatus(w http.ResponseWriter, r *http.Request) {
	if h.client == nil {
		WriteJSON(w, http.StatusOK, map[string]interface{}{
			"connected": false,
			"error":     "No active router connected",
		})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	res, err := h.client.GetSystemResource(ctx)
	if err != nil {
		WriteJSON(w, http.StatusOK, map[string]interface{}{
			"connected": false,
			"error":     err.Error(),
		})
		return
	}

	health, _ := h.client.GetSystemHealth(ctx)
	rb, _ := h.client.GetRouterBoard(ctx)
	temp, volt := routeros.ExtractHealthMetrics(health)

	rbModel := ""
	rbSerial := ""
	currentFw := ""
	upgradeFw := ""
	if rb != nil {
		rbModel = rb.Model
		rbSerial = rb.SerialNumber
		currentFw = rb.CurrentFirmware
		upgradeFw = rb.UpgradeFirmware
	}

	freeMem, _ := strconv.ParseInt(res.FreeMemory, 10, 64)
	totalMem, _ := strconv.ParseInt(res.TotalMemory, 10, 64)
	cpuLoad, _ := strconv.Atoi(res.CPULoad)
	cpuCount, _ := strconv.Atoi(res.CPUCount)
	cpuFreq, _ := strconv.Atoi(res.CPUFrequency)

	WriteJSON(w, http.StatusOK, map[string]interface{}{
		"connected": true,
		"resource": map[string]interface{}{
			"board_name":        res.BoardName,
			"version":           res.Version,
			"cpu_load":          cpuLoad,
			"free_memory":       freeMem,
			"total_memory":      totalMem,
			"uptime":            res.Uptime,
			"cpu":               res.ArchitectureName,
			"cpu_count":         cpuCount,
			"cpu_frequency":     cpuFreq,
			"architecture_name": res.ArchitectureName,
		},
		"routerboard": map[string]interface{}{
			"is_routerboard":   rb != nil,
			"model":            rbModel,
			"serial_number":    rbSerial,
			"current_firmware": currentFw,
			"upgrade_firmware": upgradeFw,
		},
		"cpu_model":       res.BoardName,
		"cpu_model_exact": true,
		"health": map[string]interface{}{
			"temperature": temp,
			"voltage":     volt,
		},
		"app_version": h.cfg.AppVersion,
	})
}

// IpLookupService represents a selectable third-party lookup provider.
type IpLookupService struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	URLTemplate string `json:"url_template"`
	Builtin     bool   `json:"builtin"`
}

// IpLookupConfigDTO models configured external IP lookup services.
type IpLookupConfigDTO struct {
	Services  []IpLookupService `json:"services"`
	DefaultID string            `json:"default_id"`
}

var defaultLookupServices = []IpLookupService{
	{ID: "2ip", Name: "2ip.io", URLTemplate: "https://2ip.io/ip/{ip}/", Builtin: true},
	{ID: "ipinfo", Name: "IPinfo", URLTemplate: "https://ipinfo.io/{ip}", Builtin: true},
	{ID: "whatismyip", Name: "WhatIsMyIPAddress", URLTemplate: "https://whatismyipaddress.com/ip/{ip}", Builtin: true},
	{ID: "abuseipdb", Name: "AbuseIPDB", URLTemplate: "https://www.abuseipdb.com/check/{ip}", Builtin: true},
	{ID: "shodan", Name: "Shodan", URLTemplate: "https://www.shodan.io/host/{ip}", Builtin: true},
	{ID: "bgp_he", Name: "BGP Toolkit", URLTemplate: "https://bgp.he.net/ip/{ip}", Builtin: true},
}

// GetIpLookup retrieves the saved IP lookup configuration or defaults.
func (h *SystemHandler) GetIpLookup(w http.ResponseWriter, r *http.Request) {
	val, err := h.database.GetSetting("ip_lookup_config")
	if err == nil && val != "" {
		var cfg IpLookupConfigDTO
		if err := json.Unmarshal([]byte(val), &cfg); err == nil && len(cfg.Services) > 0 {
			WriteJSON(w, http.StatusOK, cfg)
			return
		}
	}

	WriteJSON(w, http.StatusOK, IpLookupConfigDTO{
		Services:  defaultLookupServices,
		DefaultID: "2ip",
	})
}

// SaveIpLookup saves external IP lookup configuration.
func (h *SystemHandler) SaveIpLookup(w http.ResponseWriter, r *http.Request) {
	var payload IpLookupConfigDTO
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid IP lookup payload")
		return
	}

	if payload.DefaultID == "" {
		payload.DefaultID = "2ip"
	}
	if len(payload.Services) == 0 {
		payload.Services = defaultLookupServices
	}

	raw, _ := json.Marshal(payload)
	_ = h.database.SetSetting("ip_lookup_config", string(raw), "Configured external IP lookup services")
	WriteJSON(w, http.StatusOK, payload)
}

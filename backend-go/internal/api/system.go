package api

import (
	"encoding/json"
	"net/http"
	"runtime"
	"time"

	"github.com/masseselsev/mikroman/internal/config"
	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

var startedAt = time.Now()

type SystemHandler struct {
	cfg      *config.Config
	database *db.DB
	client   *routeros.Client
}

func NewSystemHandler(cfg *config.Config, database *db.DB, client *routeros.Client) *SystemHandler {
	return &SystemHandler{
		cfg:      cfg,
		database: database,
		client:   client,
	}
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

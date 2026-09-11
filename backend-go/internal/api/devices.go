package api

import (
	"encoding/json"
	"net/http"
	"strconv"

	"github.com/go-chi/chi/v5"

	"github.com/masseselsev/mikroman/internal/db"
)

type DeviceHandler struct {
	database *db.DB
}

func NewDeviceHandler(database *db.DB) *DeviceHandler {
	return &DeviceHandler{database: database}
}

func (h *DeviceHandler) List(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	unassignedOnly := q.Get("unassigned_only") == "true"
	showHidden := q.Get("show_hidden") != "false"
	kind := q.Get("kind")

	var routerID *int
	if rID := q.Get("router_id"); rID != "" {
		if id, err := strconv.Atoi(rID); err == nil {
			routerID = &id
		}
	}

	allDevices, err := h.database.GetDevices(routerID)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to load devices")
		return
	}

	var filtered []db.Device
	for _, dev := range allDevices {
		if unassignedOnly && dev.UserID != nil {
			continue
		}
		if !showHidden && dev.IsHidden {
			continue
		}
		if kind == "container" && !dev.IsContainer {
			continue
		}
		if (kind == "client" || kind == "") && dev.IsContainer {
			continue
		}
		filtered = append(filtered, dev)
	}

	if filtered == nil {
		filtered = []db.Device{}
	}
	WriteJSON(w, http.StatusOK, filtered)
}

func (h *DeviceHandler) Update(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, _ := strconv.Atoi(idStr)

	var payload map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid payload")
		return
	}

	query := "UPDATE devices SET "
	var args []interface{}
	first := true

	if name, ok := payload["custom_name"]; ok {
		if !first {
			query += ", "
		}
		query += "custom_name = ?"
		args = append(args, name)
		first = false
	}
	if uID, ok := payload["user_id"]; ok {
		if !first {
			query += ", "
		}
		query += "user_id = ?"
		args = append(args, uID)
		first = false
	}
	if hidden, ok := payload["is_hidden"]; ok {
		if !first {
			query += ", "
		}
		query += "is_hidden = ?"
		args = append(args, hidden)
		first = false
	}
	if paused, ok := payload["is_paused"]; ok {
		if !first {
			query += ", "
		}
		query += "is_paused = ?"
		args = append(args, paused)
		first = false
	}
	if limit, ok := payload["speed_limit"]; ok {
		if !first {
			query += ", "
		}
		query += "speed_limit = ?"
		args = append(args, limit)
		first = false
	}

	if first {
		WriteJSON(w, http.StatusOK, map[string]string{"message": "No changes"})
		return
	}

	query += " WHERE id = ?"
	args = append(args, id)

	_, err := h.database.SqlDB.Exec(query, args...)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to update device: "+err.Error())
		return
	}

	WriteJSON(w, http.StatusOK, map[string]string{"message": "Device updated successfully"})
}

func (h *DeviceHandler) Delete(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, _ := strconv.Atoi(idStr)

	// Soft delete device
	_, err := h.database.SqlDB.Exec("UPDATE devices SET is_deleted = 1 WHERE id = ?", id)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to delete device")
		return
	}

	WriteJSON(w, http.StatusOK, map[string]string{"message": "Device deleted successfully"})
}

func (h *DeviceHandler) Pause(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, _ := strconv.Atoi(idStr)

	var payload struct {
		IsPaused bool `json:"is_paused"`
	}
	_ = json.NewDecoder(r.Body).Decode(&payload)

	_, err := h.database.SqlDB.Exec("UPDATE devices SET is_paused = ? WHERE id = ?", payload.IsPaused, id)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to toggle pause")
		return
	}

	WriteJSON(w, http.StatusOK, map[string]interface{}{"id": id, "is_paused": payload.IsPaused})
}

func (h *DeviceHandler) Limit(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, _ := strconv.Atoi(idStr)

	var payload struct {
		SpeedLimit string `json:"speed_limit"`
	}
	_ = json.NewDecoder(r.Body).Decode(&payload)

	_, err := h.database.SqlDB.Exec("UPDATE devices SET speed_limit = ? WHERE id = ?", payload.SpeedLimit, id)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to update speed limit")
		return
	}

	WriteJSON(w, http.StatusOK, map[string]interface{}{"id": id, "speed_limit": payload.SpeedLimit})
}

func (h *DeviceHandler) History(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, _ := strconv.Atoi(idStr)

	rows, err := h.database.SqlDB.Query(`
		SELECT id, device_id, mac_address, hostname, ip_address, event_type, details, created_at
		FROM device_history WHERE device_id = ?
		ORDER BY created_at DESC LIMIT 50
	`, id)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to load history")
		return
	}
	defer rows.Close()

	var history []db.DeviceHistory
	for rows.Next() {
		var hItem db.DeviceHistory
		if err := rows.Scan(&hItem.ID, &hItem.DeviceID, &hItem.MacAddress, &hItem.Hostname, &hItem.IPAddress, &hItem.EventType, &hItem.Details, &hItem.CreatedAt); err == nil {
			history = append(history, hItem)
		}
	}

	if history == nil {
		history = []db.DeviceHistory{}
	}
	WriteJSON(w, http.StatusOK, history)
}

// Scan handles POST /devices/scan.
func (h *DeviceHandler) Scan(w http.ResponseWriter, r *http.Request) {
	var routerID *int
	if rID := r.URL.Query().Get("router_id"); rID != "" {
		if id, err := strconv.Atoi(rID); err == nil {
			routerID = &id
		}
	}
	allDevices, err := h.database.GetDevices(routerID)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to scan devices")
		return
	}
	if allDevices == nil {
		allDevices = []db.Device{}
	}
	WriteJSON(w, http.StatusOK, allDevices)
}

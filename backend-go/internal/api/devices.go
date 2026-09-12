package api

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/masseselsev/mikroman/internal/db"
)

type DeviceHandler struct {
	database   *db.DB
	reconciler QueueReconciler
}

func NewDeviceHandler(database *db.DB, reconciler QueueReconciler) *DeviceHandler {
	return &DeviceHandler{database: database, reconciler: reconciler}
}

func (h *DeviceHandler) triggerReconcile(deviceID int) {
	if h.reconciler == nil {
		return
	}
	dev, _ := h.database.GetDevice(deviceID)
	var rID int
	if dev != nil && dev.RouterID != nil {
		rID = *dev.RouterID
	} else if def, err := h.database.GetDefaultRouter(); err == nil && def != nil {
		rID = def.ID
	}
	if rID > 0 {
		go func(routerID int) {
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			_ = h.reconciler.ReconcileQueues(ctx, routerID)
		}(rID)
	}
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

	now := time.Now()
	today := now.Format("2006-01-02")
	anchorDay := h.database.GetBillingAnchorDay()
	cycleStart := db.CalculateBillingCycleStart(anchorDay, now)

	devStats, _ := h.database.GetDeviceVolumeStats(cycleStart, today)

	var filtered []db.Device
	for _, dev := range allDevices {
		if s, ok := devStats[dev.ID]; ok {
			dev.BytesTotalIn = s.TotalIn
			dev.BytesTotalOut = s.TotalOut
			dev.BytesCycleIn = s.CycleIn
			dev.BytesCycleOut = s.CycleOut
			dev.BytesTodayIn = s.TodayIn
			dev.BytesTodayOut = s.TodayOut
		}

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

	h.triggerReconcile(id)

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

	h.triggerReconcile(id)

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

	h.triggerReconcile(id)

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

	h.triggerReconcile(id)

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

type DeviceLinkRequest struct {
	PrimaryDeviceID int `json:"primary_device_id"`
}

type DeviceMergeRequest struct {
	TargetDeviceID int    `json:"target_device_id"`
	Note           string `json:"note"`
}

type DeviceSplitRequest struct {
	MacAddress string `json:"mac_address"`
}

type DeviceSuggestionDTO struct {
	UnassignedDeviceID      int     `json:"unassigned_device_id"`
	SuggestedTargetDeviceID int     `json:"suggested_target_device_id"`
	SuggestedUserID         int     `json:"suggested_user_id"`
	SuggestedUserName       string  `json:"suggested_user_name"`
	TargetDeviceName        string  `json:"target_device_name"`
	Confidence              float64 `json:"confidence"`
	Reason                  string  `json:"reason"`
}

type LinkSuggestion struct {
	DeviceID          int     `json:"device_id"`
	PrimaryDeviceID   int     `json:"primary_device_id"`
	DeviceName        string  `json:"device_name"`
	PrimaryDeviceName string  `json:"primary_device_name"`
	DeviceConnection  *string `json:"device_connection,omitempty"`
	PrimaryConnection *string `json:"primary_connection,omitempty"`
	Confidence        float64 `json:"confidence"`
	Reason            string  `json:"reason"`
}

func (h *DeviceHandler) Link(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	deviceID, _ := strconv.Atoi(idStr)

	var payload DeviceLinkRequest
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid link payload")
		return
	}

	primaryID := payload.PrimaryDeviceID
	// Follow primary to head of its group
	var existingHead *int
	_ = h.database.SqlDB.QueryRow("SELECT linked_to_device_id FROM devices WHERE id = ?", primaryID).Scan(&existingHead)
	if existingHead != nil && *existingHead > 0 {
		primaryID = *existingHead
	}

	if deviceID == primaryID {
		WriteError(w, http.StatusBadRequest, "A device cannot be linked to itself")
		return
	}

	primary, err := h.database.GetDevice(primaryID)
	if err != nil || primary == nil {
		WriteError(w, http.StatusNotFound, "Primary device not found")
		return
	}

	device, err := h.database.GetDevice(deviceID)
	if err != nil || device == nil {
		WriteError(w, http.StatusNotFound, "Device not found")
		return
	}

	// Move anything already linked to this device up to primary
	_, _ = h.database.SqlDB.Exec("UPDATE devices SET linked_to_device_id = ? WHERE linked_to_device_id = ?", primaryID, deviceID)

	// Link device to primary and inherit owner user_id
	if primary.UserID != nil {
		_, _ = h.database.SqlDB.Exec("UPDATE devices SET linked_to_device_id = ?, user_id = ? WHERE id = ?", primaryID, *primary.UserID, deviceID)
	} else {
		_, _ = h.database.SqlDB.Exec("UPDATE devices SET linked_to_device_id = ? WHERE id = ?", primaryID, deviceID)
	}

	// Record in device_history
	_, _ = h.database.SqlDB.Exec(`
		INSERT INTO device_history (device_id, mac_address, hostname, ip_address, event_type, details, created_at)
		VALUES (?, ?, ?, ?, 'linked', ?, CURRENT_TIMESTAMP)
	`, deviceID, device.MacAddress, device.Hostname.String, device.IPAddress.String, fmt.Sprintf("Linked as adapter of device %d", primaryID))

	updated, _ := h.database.GetDevice(deviceID)
	WriteJSON(w, http.StatusOK, updated)
}

func (h *DeviceHandler) Unlink(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	deviceID, _ := strconv.Atoi(idStr)

	device, err := h.database.GetDevice(deviceID)
	if err != nil || device == nil {
		WriteError(w, http.StatusNotFound, "Device not found")
		return
	}

	_, _ = h.database.SqlDB.Exec("UPDATE devices SET linked_to_device_id = NULL WHERE id = ?", deviceID)

	_, _ = h.database.SqlDB.Exec(`
		INSERT INTO device_history (device_id, mac_address, hostname, ip_address, event_type, details, created_at)
		VALUES (?, ?, ?, ?, 'unlinked', 'Detached adapter into standalone device', CURRENT_TIMESTAMP)
	`, deviceID, device.MacAddress, device.Hostname.String, device.IPAddress.String)

	updated, _ := h.database.GetDevice(deviceID)
	WriteJSON(w, http.StatusOK, updated)
}

func (h *DeviceHandler) Merge(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	sourceID, _ := strconv.Atoi(idStr)

	var payload DeviceMergeRequest
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid merge payload")
		return
	}

	if sourceID == payload.TargetDeviceID {
		WriteError(w, http.StatusBadRequest, "Cannot merge device into itself")
		return
	}

	source, err := h.database.GetDevice(sourceID)
	if err != nil || source == nil {
		WriteError(w, http.StatusNotFound, "Source device not found")
		return
	}

	target, err := h.database.GetDevice(payload.TargetDeviceID)
	if err != nil || target == nil {
		WriteError(w, http.StatusNotFound, "Target device not found")
		return
	}

	// Soft-delete source device
	_, _ = h.database.SqlDB.Exec("UPDATE devices SET is_deleted = 1 WHERE id = ?", sourceID)

	// Record in history
	note := payload.Note
	if note == "" {
		note = fmt.Sprintf("Merged from device %d (%s)", sourceID, source.MacAddress)
	}
	_, _ = h.database.SqlDB.Exec(`
		INSERT INTO device_history (device_id, mac_address, hostname, ip_address, event_type, details, created_at)
		VALUES (?, ?, ?, ?, 'merged', ?, CURRENT_TIMESTAMP)
	`, target.ID, source.MacAddress, source.Hostname.String, source.IPAddress.String, note)

	updated, _ := h.database.GetDevice(target.ID)
	WriteJSON(w, http.StatusOK, updated)
}

func (h *DeviceHandler) Split(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	deviceID, _ := strconv.Atoi(idStr)

	device, err := h.database.GetDevice(deviceID)
	if err != nil || device == nil {
		WriteError(w, http.StatusNotFound, "Device not found")
		return
	}

	WriteJSON(w, http.StatusOK, device)
}

func (h *DeviceHandler) GetMergeSuggestions(w http.ResponseWriter, r *http.Request) {
	WriteJSON(w, http.StatusOK, []DeviceSuggestionDTO{})
}

func (h *DeviceHandler) GetLinkSuggestions(w http.ResponseWriter, r *http.Request) {
	WriteJSON(w, http.StatusOK, []LinkSuggestion{})
}


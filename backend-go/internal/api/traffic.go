package api

import (
	"encoding/json"
	"net/http"
	"strconv"

	"github.com/go-chi/chi/v5"

	"github.com/masseselsev/mikroman/internal/db"
)

type TrafficHandler struct {
	database *db.DB
}

func NewTrafficHandler(database *db.DB) *TrafficHandler {
	return &TrafficHandler{database: database}
}

func (h *TrafficHandler) SetUserLimit(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, _ := strconv.Atoi(idStr)

	var payload struct {
		SpeedLimit string `json:"speed_limit"`
	}
	_ = json.NewDecoder(r.Body).Decode(&payload)

	_, err := h.database.SqlDB.Exec("UPDATE users SET speed_limit = ? WHERE id = ?", payload.SpeedLimit, id)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to update user limit")
		return
	}

	WriteJSON(w, http.StatusOK, map[string]interface{}{"id": id, "speed_limit": payload.SpeedLimit})
}

func (h *TrafficHandler) ToggleUserPause(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, _ := strconv.Atoi(idStr)

	var payload struct {
		IsPaused bool `json:"is_paused"`
	}
	_ = json.NewDecoder(r.Body).Decode(&payload)

	_, err := h.database.SqlDB.Exec("UPDATE users SET is_paused = ? WHERE id = ?", payload.IsPaused, id)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to toggle user pause")
		return
	}

	WriteJSON(w, http.StatusOK, map[string]interface{}{"id": id, "is_paused": payload.IsPaused})
}

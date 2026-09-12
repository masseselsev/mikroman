package api

import (
	"context"
	"encoding/json"
	"net/http"
	"strconv"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/masseselsev/mikroman/internal/db"
)

type TrafficHandler struct {
	database   *db.DB
	reconciler QueueReconciler
}

func NewTrafficHandler(database *db.DB, reconciler QueueReconciler) *TrafficHandler {
	return &TrafficHandler{database: database, reconciler: reconciler}
}

func (h *TrafficHandler) triggerReconcile(userID int) {
	if h.reconciler == nil {
		return
	}
	u, _ := h.database.GetUser(userID)
	var rID int
	if u != nil && u.RouterID != nil {
		rID = *u.RouterID
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

	h.triggerReconcile(id)

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

	h.triggerReconcile(id)

	WriteJSON(w, http.StatusOK, map[string]interface{}{"id": id, "is_paused": payload.IsPaused})
}

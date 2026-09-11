package api

import (
	"net/http"

	"github.com/masseselsev/mikroman/internal/db"
)

type SpeedTestHandler struct {
	database *db.DB
}

func NewSpeedTestHandler(database *db.DB) *SpeedTestHandler {
	return &SpeedTestHandler{database: database}
}

type SpeedTestStatusDTO struct {
	CanRun     bool        `json:"can_run"`
	Reason     string      `json:"reason"`
	LastResult interface{} `json:"last_result"`
}

func (h *SpeedTestHandler) Status(w http.ResponseWriter, r *http.Request) {
	status := SpeedTestStatusDTO{
		CanRun:     false,
		Reason:     "package_missing",
		LastResult: nil,
	}
	WriteJSON(w, http.StatusOK, status)
}

func (h *SpeedTestHandler) Run(w http.ResponseWriter, r *http.Request) {
	WriteError(w, http.StatusConflict, "Speed test container is not configured on this router")
}

func (h *SpeedTestHandler) CreateContainer(w http.ResponseWriter, r *http.Request) {
	WriteError(w, http.StatusConflict, "Container creation not supported")
}

func (h *SpeedTestHandler) History(w http.ResponseWriter, r *http.Request) {
	WriteJSON(w, http.StatusOK, []interface{}{})
}

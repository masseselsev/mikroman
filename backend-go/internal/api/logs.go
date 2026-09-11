package api

import (
	"net/http"
	"strconv"
	"time"

	"github.com/masseselsev/mikroman/internal/db"
)

type LogHandler struct {
	database *db.DB
}

func NewLogHandler(database *db.DB) *LogHandler {
	return &LogHandler{database: database}
}

func (h *LogHandler) GetLogs(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	limit := 100
	if l := q.Get("limit"); l != "" {
		if val, err := strconv.Atoi(l); err == nil && val > 0 {
			limit = val
		}
	}

	rows, err := h.database.SqlDB.Query(`
		SELECT id, router_id, external_id, timestamp, topics, message, severity, category, created_at
		FROM router_logs
		ORDER BY timestamp DESC LIMIT ?
	`, limit)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to load logs")
		return
	}
	defer rows.Close()

	type LogDTO struct {
		ID        int       `json:"id"`
		RouterID  int       `json:"router_id"`
		Time      time.Time `json:"time"`
		Topics    string    `json:"topics"`
		Message   string    `json:"message"`
		Severity  string    `json:"severity"`
		Category  string    `json:"category"`
	}

	var logs []LogDTO
	for rows.Next() {
		var l LogDTO
		var extID *string
		var createdAt time.Time
		if err := rows.Scan(&l.ID, &l.RouterID, &extID, &l.Time, &l.Topics, &l.Message, &l.Severity, &l.Category, &createdAt); err == nil {
			logs = append(logs, l)
		}
	}

	if logs == nil {
		logs = []LogDTO{}
	}
	WriteJSON(w, http.StatusOK, logs)
}

func (h *LogHandler) ClearLogs(w http.ResponseWriter, r *http.Request) {
	_, err := h.database.SqlDB.Exec("DELETE FROM router_logs")
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to clear logs")
		return
	}
	WriteJSON(w, http.StatusOK, map[string]string{"message": "Logs cleared"})
}

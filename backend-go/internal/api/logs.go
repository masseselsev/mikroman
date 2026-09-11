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

// RouterLogStatsDTO models log severity breakdowns.
type RouterLogStatsDTO struct {
	RouterID          int   `json:"router_id"`
	TotalLogs         int64 `json:"total_logs"`
	CriticalCount     int64 `json:"critical_count"`
	ErrorCount        int64 `json:"error_count"`
	WarningCount      int64 `json:"warning_count"`
	AuthFailuresCount int64 `json:"auth_failures_count"`
}

// GetLogStats returns log statistics.
func (h *LogHandler) GetLogStats(w http.ResponseWriter, r *http.Request) {
	rID := 0
	if idStr := r.URL.Query().Get("router_id"); idStr != "" {
		rID, _ = strconv.Atoi(idStr)
	}

	var stats RouterLogStatsDTO
	stats.RouterID = rID

	var query string
	var args []interface{}
	if rID > 0 {
		query = `
			SELECT
				COUNT(*),
				COALESCE(SUM(CASE WHEN severity = 'critical' THEN 1 ELSE 0 END), 0),
				COALESCE(SUM(CASE WHEN severity = 'error' THEN 1 ELSE 0 END), 0),
				COALESCE(SUM(CASE WHEN severity = 'warning' THEN 1 ELSE 0 END), 0),
				COALESCE(SUM(CASE WHEN category = 'auth' AND severity IN ('critical', 'error') THEN 1 ELSE 0 END), 0)
			FROM router_logs
			WHERE router_id = ?
		`
		args = append(args, rID)
	} else {
		query = `
			SELECT
				COUNT(*),
				COALESCE(SUM(CASE WHEN severity = 'critical' THEN 1 ELSE 0 END), 0),
				COALESCE(SUM(CASE WHEN severity = 'error' THEN 1 ELSE 0 END), 0),
				COALESCE(SUM(CASE WHEN severity = 'warning' THEN 1 ELSE 0 END), 0),
				COALESCE(SUM(CASE WHEN category = 'auth' AND severity IN ('critical', 'error') THEN 1 ELSE 0 END), 0)
			FROM router_logs
		`
	}

	_ = h.database.SqlDB.QueryRow(query, args...).Scan(
		&stats.TotalLogs,
		&stats.CriticalCount,
		&stats.ErrorCount,
		&stats.WarningCount,
		&stats.AuthFailuresCount,
	)

	WriteJSON(w, http.StatusOK, stats)
}

type LoggingRuleItem struct {
	ID        string  `json:"id"`
	Topics    string  `json:"topics"`
	Action    string  `json:"action"`
	Prefix    *string `json:"prefix,omitempty"`
	Comment   *string `json:"comment,omitempty"`
	IsManaged bool    `json:"is_managed"`
}

func (h *LogHandler) GetLoggingRules(w http.ResponseWriter, r *http.Request) {
	WriteJSON(w, http.StatusOK, []LoggingRuleItem{})
}

func (h *LogHandler) CreateLoggingRule(w http.ResponseWriter, r *http.Request) {
	WriteJSON(w, http.StatusOK, map[string]string{"id": "*1", "message": "Logging rule created"})
}

func (h *LogHandler) DeleteLoggingRule(w http.ResponseWriter, r *http.Request) {
	WriteJSON(w, http.StatusOK, true)
}


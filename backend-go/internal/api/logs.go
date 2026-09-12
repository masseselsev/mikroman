package api

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

type LogDTO struct {
	ID       int       `json:"id"`
	RouterID int       `json:"router_id"`
	Time     time.Time `json:"time"`
	Topics   string    `json:"topics"`
	Message  string    `json:"message"`
	Severity string    `json:"severity"`
	Category string    `json:"category"`
}

type LogHandler struct {
	database *db.DB
	client   *routeros.Client
	dataDir  string
	mu       sync.Mutex
	clients  map[int]*routeros.Client
}

func NewLogHandler(database *db.DB, client *routeros.Client, dataDir string) *LogHandler {
	clients := make(map[int]*routeros.Client)
	if client != nil {
		if def, err := database.GetDefaultRouter(); err == nil && def != nil {
			clients[def.ID] = client
		}
	}
	return &LogHandler{
		database: database,
		client:   client,
		dataDir:  dataDir,
		clients:  clients,
	}
}

func (h *LogHandler) getClient(routerID int) (*routeros.Client, error) {
	h.mu.Lock()
	defer h.mu.Unlock()

	if c, ok := h.clients[routerID]; ok && c != nil {
		return c, nil
	}

	defaultRouter, _ := h.database.GetDefaultRouter()
	if defaultRouter != nil && defaultRouter.ID == routerID && h.client != nil {
		h.clients[routerID] = h.client
		return h.client, nil
	}
	if (defaultRouter == nil || routerID == 0) && h.client != nil {
		return h.client, nil
	}

	router, err := h.database.GetRouter(routerID)
	if err != nil || router == nil {
		if h.client != nil {
			return h.client, nil
		}
		return nil, fmt.Errorf("router %d not found", routerID)
	}

	newClient, err := routeros.NewClient(routeros.Config{
		Host:      router.Host,
		Port:      router.Port,
		Username:  router.Username,
		Password:  router.Password,
		UseSSL:    router.UseSSL,
		SSLVerify: router.SSLVerify,
		CACert:    router.CACert.String,
		Timeout:   5 * time.Second,
	})
	if err != nil {
		return nil, err
	}

	h.clients[routerID] = newClient
	return newClient, nil
}

func (h *LogHandler) GetLogs(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	limit := 100
	if l := q.Get("limit"); l != "" {
		if val, err := strconv.Atoi(l); err == nil && val > 0 {
			if val > 50000 {
				limit = 50000
			} else {
				limit = val
			}
		}
	}

	rID := 0
	if idStr := q.Get("router_id"); idStr != "" {
		rID, _ = strconv.Atoi(idStr)
	}

	source := strings.ToLower(strings.TrimSpace(q.Get("source")))
	if source == "" {
		source = "live"
	}

	hideSelfApi := q.Get("hide_self_api") == "true" || q.Get("hide_self_api") == "1"
	hideContainerLogs := q.Get("hide_container_logs") == "true" || q.Get("hide_container_logs") == "1"
	categoryFilter := strings.ToLower(strings.TrimSpace(q.Get("category")))
	severityFilter := strings.ToLower(strings.TrimSpace(q.Get("severity")))
	searchFilter := strings.ToLower(strings.TrimSpace(q.Get("search")))

	if source == "app" {
		logs := h.getAppLogs(limit, searchFilter)
		WriteJSON(w, http.StatusOK, logs)
		return
	}

	if source == "live" {
		client, err := h.getClient(rID)
		if err == nil && client != nil {
			ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
			defer cancel()

			rawEntries, err := client.GetLogs(ctx, "", limit*3)
			if err == nil && len(rawEntries) > 0 {
				now := time.Now().UTC()
				var logs []LogDTO
				// RouterOS returns oldest first; reverse to newest first
				for i := len(rawEntries) - 1; i >= 0; i-- {
					e := rawEntries[i]
					tLower := strings.ToLower(e.Topics)
					mLower := strings.ToLower(e.Message)

					if hideSelfApi {
						if strings.Contains(mLower, "logged in") || strings.Contains(mLower, "logged out") || strings.Contains(mLower, "login failure") ||
							strings.Contains(mLower, "by api:rest") || strings.Contains(mLower, "by api@") || strings.Contains(mLower, "by api:") ||
							strings.Contains(mLower, "by api") || strings.Contains(mLower, "api:rest@") ||
							strings.Contains(mLower, "via api") || strings.Contains(mLower, "via rest-api") || strings.Contains(mLower, "user rest") ||
							strings.Contains(mLower, "rest-api") {
							continue
						}
					}

					if hideContainerLogs {
						if strings.Contains(tLower, "container") && (strings.Contains(mLower, "mikroman") || strings.Contains(mLower, "container") || strings.Contains(tLower, "container")) {
							continue
						}
					}

					sev, cat := routeros.CategorizeLog(e.Topics, e.Message)
					if categoryFilter != "" && categoryFilter != "all" && cat != categoryFilter {
						continue
					}
					if severityFilter != "" && sev != severityFilter {
						continue
					}
					if searchFilter != "" && !strings.Contains(tLower, searchFilter) && !strings.Contains(mLower, searchFilter) {
						continue
					}

					tStamp := routeros.ParseLogTimestamp(e.Time, now)
					logs = append(logs, LogDTO{
						ID:       len(rawEntries) - i,
						RouterID: rID,
						Time:     tStamp,
						Topics:   e.Topics,
						Message:  e.Message,
						Severity: sev,
						Category: cat,
					})

					if len(logs) >= limit {
						break
					}
				}

				if logs == nil {
					logs = []LogDTO{}
				}
				WriteJSON(w, http.StatusOK, logs)
				return
			}
		}
		// Fall through to DB if live query failed or returned no entries
	}

	// source == "db" or live fallback
	where := []string{"1=1"}
	var args []interface{}

	if rID > 0 {
		where = append(where, "router_id = ?")
		args = append(args, rID)
	}
	if categoryFilter != "" && categoryFilter != "all" {
		where = append(where, "category = ?")
		args = append(args, categoryFilter)
	}
	if severityFilter != "" {
		where = append(where, "severity = ?")
		args = append(args, severityFilter)
	}
	if hideSelfApi {
		where = append(where, "(message NOT LIKE '%logged in%' AND message NOT LIKE '%logged out%' AND message NOT LIKE '%login failure%' AND message NOT LIKE '%by api:rest%' AND message NOT LIKE '%by api@%' AND message NOT LIKE '%by api:%' AND message NOT LIKE '%by api %' AND message NOT LIKE '%api:rest@%' AND message NOT LIKE '%via api%' AND message NOT LIKE '%via rest-api%' AND message NOT LIKE '%user rest%' AND message NOT LIKE '%rest-api%')")
	}
	if hideContainerLogs {
		where = append(where, "NOT (topics LIKE '%container%' AND message LIKE '%mikroman%')")
	}
	if searchFilter != "" {
		where = append(where, "(LOWER(message) LIKE ? OR LOWER(topics) LIKE ?)")
		pattern := "%" + searchFilter + "%"
		args = append(args, pattern, pattern)
	}

	query := fmt.Sprintf(`
		SELECT id, router_id, external_id, timestamp, topics, message, severity, category, created_at
		FROM router_logs
		WHERE %s
		ORDER BY timestamp DESC, id DESC LIMIT ?
	`, strings.Join(where, " AND "))
	args = append(args, limit)

	rows, err := h.database.SqlDB.Query(query, args...)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to load logs")
		return
	}
	defer rows.Close()

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

func (h *LogHandler) getAppLogs(limit int, search string) []LogDTO {
	candidates := []string{
		filepath.Join(h.dataDir, "app.log"),
		filepath.Join(h.dataDir, "mikroman.log"),
		"app.log",
		"mikroman.log",
	}

	var logPath string
	for _, p := range candidates {
		if p != "" {
			if _, err := os.Stat(p); err == nil {
				logPath = p
				break
			}
		}
	}

	if logPath == "" {
		return []LogDTO{}
	}

	file, err := os.Open(logPath)
	if err != nil {
		return []LogDTO{}
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	var lines []string
	for scanner.Scan() {
		text := scanner.Text()
		if search == "" || strings.Contains(strings.ToLower(text), search) {
			lines = append(lines, text)
		}
	}

	var logs []LogDTO
	start := 0
	if len(lines) > limit {
		start = len(lines) - limit
	}

	now := time.Now().UTC()
	for i := len(lines) - 1; i >= start; i-- {
		line := lines[i]
		sev := "info"
		if strings.Contains(strings.ToLower(line), "level=error") || strings.Contains(strings.ToLower(line), "err") {
			sev = "error"
		} else if strings.Contains(strings.ToLower(line), "level=warn") {
			sev = "warning"
		}

		logs = append(logs, LogDTO{
			ID:       i + 1,
			RouterID: 0,
			Time:     now,
			Topics:   "app",
			Message:  line,
			Severity: sev,
			Category: "system",
		})
	}

	return logs
}

func (h *LogHandler) ClearLogs(w http.ResponseWriter, r *http.Request) {
	rID := 0
	if idStr := r.URL.Query().Get("router_id"); idStr != "" {
		rID, _ = strconv.Atoi(idStr)
	}

	var err error
	if rID > 0 {
		_, err = h.database.SqlDB.Exec("DELETE FROM router_logs WHERE router_id = ?", rID)
	} else {
		_, err = h.database.SqlDB.Exec("DELETE FROM router_logs")
	}
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

	sinceStr := strings.TrimSpace(r.URL.Query().Get("since"))
	var cutoff time.Time
	if sinceStr != "" {
		// Try parsing ISO8601 / RFC3339 formats
		if t, err := time.Parse(time.RFC3339Nano, sinceStr); err == nil {
			cutoff = t.UTC()
		} else if t, err := time.Parse(time.RFC3339, sinceStr); err == nil {
			cutoff = t.UTC()
		} else if t, err := time.Parse("2006-01-02 15:04:05", sinceStr); err == nil {
			cutoff = t.UTC()
		}
	}
	if cutoff.IsZero() {
		// Default to trailing 24 hours
		cutoff = time.Now().UTC().Add(-24 * time.Hour)
	}

	cutoffStr := cutoff.Format("2006-01-02 15:04:05")

	where := []string{"timestamp >= ?"}
	args := []interface{}{cutoffStr}

	if rID > 0 {
		where = append(where, "router_id = ?")
		args = append(args, rID)
	}

	query := fmt.Sprintf(`
		SELECT
			COUNT(*),
			COALESCE(SUM(CASE WHEN severity = 'critical' THEN 1 ELSE 0 END), 0),
			COALESCE(SUM(CASE WHEN severity = 'error' THEN 1 ELSE 0 END), 0),
			COALESCE(SUM(CASE WHEN severity = 'warning' THEN 1 ELSE 0 END), 0),
			COALESCE(SUM(CASE WHEN category = 'auth' AND severity IN ('critical', 'error') THEN 1 ELSE 0 END), 0)
		FROM router_logs
		WHERE %s
	`, strings.Join(where, " AND "))

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
	rID := 0
	if idStr := r.URL.Query().Get("router_id"); idStr != "" {
		rID, _ = strconv.Atoi(idStr)
	}

	client, err := h.getClient(rID)
	if err != nil || client == nil {
		WriteJSON(w, http.StatusOK, []LoggingRuleItem{})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	type rosRule struct {
		ID      string `json:".id"`
		Topics  string `json:"topics"`
		Action  string `json:"action"`
		Prefix  string `json:"prefix"`
		Comment string `json:"comment"`
	}

	var rosRules []rosRule
	if err := client.Get(ctx, "/system/logging", &rosRules); err != nil {
		WriteJSON(w, http.StatusOK, []LoggingRuleItem{})
		return
	}

	items := make([]LoggingRuleItem, 0, len(rosRules))
	for _, rule := range rosRules {
		item := LoggingRuleItem{
			ID:        rule.ID,
			Topics:    rule.Topics,
			Action:    rule.Action,
			IsManaged: strings.HasPrefix(rule.Comment, "mikroman:"),
		}
		if rule.Prefix != "" {
			item.Prefix = &rule.Prefix
		}
		if rule.Comment != "" {
			item.Comment = &rule.Comment
		}
		items = append(items, item)
	}

	WriteJSON(w, http.StatusOK, items)
}

func (h *LogHandler) CreateLoggingRule(w http.ResponseWriter, r *http.Request) {
	rID := 0
	if idStr := r.URL.Query().Get("router_id"); idStr != "" {
		rID, _ = strconv.Atoi(idStr)
	}

	client, err := h.getClient(rID)
	if err != nil || client == nil {
		WriteError(w, http.StatusServiceUnavailable, "Router client not available")
		return
	}

	var req struct {
		Topics string `json:"topics"`
		Action string `json:"action"`
		Prefix string `json:"prefix"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid request body")
		return
	}

	if req.Action == "" {
		req.Action = "memory"
	}

	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	payload := map[string]interface{}{
		"topics":  req.Topics,
		"action":  req.Action,
		"comment": "mikroman:custom",
	}
	if req.Prefix != "" {
		payload["prefix"] = req.Prefix
	}

	var res map[string]interface{}
	if err := client.Put(ctx, "/system/logging", payload, &res); err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to create logging rule: "+err.Error())
		return
	}

	id := "*1"
	if res != nil {
		if idVal, ok := res[".id"].(string); ok {
			id = idVal
		}
	}

	WriteJSON(w, http.StatusOK, map[string]string{"id": id, "message": "Logging rule created"})
}

func (h *LogHandler) DeleteLoggingRule(w http.ResponseWriter, r *http.Request) {
	ruleID := chi.URLParam(r, "id")
	if ruleID == "" {
		WriteError(w, http.StatusBadRequest, "Rule ID required")
		return
	}

	rID := 0
	if idStr := r.URL.Query().Get("router_id"); idStr != "" {
		rID, _ = strconv.Atoi(idStr)
	}

	client, err := h.getClient(rID)
	if err != nil || client == nil {
		WriteError(w, http.StatusServiceUnavailable, "Router client not available")
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	path := fmt.Sprintf("/system/logging/%s", ruleID)
	_ = client.Delete(ctx, path)
	WriteJSON(w, http.StatusOK, true)
}


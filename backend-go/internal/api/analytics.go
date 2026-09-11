package api

import (
	"encoding/json"
	"net/http"
	"strconv"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/masseselsev/mikroman/internal/db"
)

type AnalyticsHandler struct {
	database *db.DB
}

func NewAnalyticsHandler(database *db.DB) *AnalyticsHandler {
	return &AnalyticsHandler{database: database}
}

func (h *AnalyticsHandler) GetTrafficOverview(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	preset := q.Get("preset")
	if preset == "" {
		preset = "7d"
	}

	days := 7
	if preset == "30d" {
		days = 30
	} else if preset == "today" || preset == "1d" {
		days = 1
	}

	sinceDate := time.Now().AddDate(0, 0, -days).Format("2006-01-02")

	rows, err := h.database.SqlDB.Query(`
		SELECT user_id, sum(bytes_in) as total_in, sum(bytes_out) as total_out
		FROM traffic_rollups
		WHERE record_date >= ?
		GROUP BY user_id
	`, sinceDate)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to query rollups")
		return
	}
	defer rows.Close()

	type UserSummary struct {
		UserID   int   `json:"user_id"`
		BytesIn  int64 `json:"bytes_in"`
		BytesOut int64 `json:"bytes_out"`
		Total    int64 `json:"total_bytes"`
	}

	var users []UserSummary
	var grandTotal int64
	for rows.Next() {
		var s UserSummary
		if err := rows.Scan(&s.UserID, &s.BytesIn, &s.BytesOut); err == nil {
			s.Total = s.BytesIn + s.BytesOut
			grandTotal += s.Total
			users = append(users, s)
		}
	}

	WriteJSON(w, http.StatusOK, map[string]interface{}{
		"preset":      preset,
		"users":       users,
		"grand_total": grandTotal,
	})
}

func (h *AnalyticsHandler) GetQuota(w http.ResponseWriter, r *http.Request) {
	quotaVal, _ := h.database.GetSetting("isp_monthly_quota_bytes")
	quotaBytes, _ := strconv.ParseInt(quotaVal, 10, 64)

	WriteJSON(w, http.StatusOK, map[string]interface{}{
		"quota_bytes": quotaBytes,
		"enabled":     quotaBytes > 0,
	})
}

func (h *AnalyticsHandler) SaveQuota(w http.ResponseWriter, r *http.Request) {
	var payload struct {
		QuotaBytes int64 `json:"quota_bytes"`
	}
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid quota payload")
		return
	}

	_ = h.database.SetSetting("isp_monthly_quota_bytes", strconv.FormatInt(payload.QuotaBytes, 10), "Monthly ISP Quota in Bytes")
	WriteJSON(w, http.StatusOK, map[string]string{"message": "Quota updated"})
}

func (h *AnalyticsHandler) UserHistory(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, _ := strconv.Atoi(idStr)

	rows, err := h.database.SqlDB.Query(`
		SELECT record_date, bytes_in, bytes_out
		FROM traffic_rollups
		WHERE user_id = ?
		ORDER BY record_date DESC LIMIT 30
	`, id)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to load history")
		return
	}
	defer rows.Close()

	type DayHistory struct {
		Date     string `json:"date"`
		BytesIn  int64  `json:"bytes_in"`
		BytesOut int64  `json:"bytes_out"`
	}

	var items []DayHistory
	for rows.Next() {
		var d DayHistory
		if err := rows.Scan(&d.Date, &d.BytesIn, &d.BytesOut); err == nil {
			items = append(items, d)
		}
	}

	if items == nil {
		items = []DayHistory{}
	}
	WriteJSON(w, http.StatusOK, items)
}

func (h *AnalyticsHandler) DeviceHistory(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, _ := strconv.Atoi(idStr)

	rows, err := h.database.SqlDB.Query(`
		SELECT record_date, bytes_in, bytes_out
		FROM device_traffic_rollups
		WHERE device_id = ?
		ORDER BY record_date DESC LIMIT 30
	`, id)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to load history")
		return
	}
	defer rows.Close()

	type DayHistory struct {
		Date     string `json:"date"`
		BytesIn  int64  `json:"bytes_in"`
		BytesOut int64  `json:"bytes_out"`
	}

	var items []DayHistory
	for rows.Next() {
		var d DayHistory
		if err := rows.Scan(&d.Date, &d.BytesIn, &d.BytesOut); err == nil {
			items = append(items, d)
		}
	}

	if items == nil {
		items = []DayHistory{}
	}
	WriteJSON(w, http.StatusOK, items)
}

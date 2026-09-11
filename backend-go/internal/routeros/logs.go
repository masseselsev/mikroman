package routeros

import (
	"context"
	"strings"
	"time"
)

// GetLogs reads /log and applies optional topic filtering and limit
func (c *Client) GetLogs(ctx context.Context, topics string, limit int) ([]LogEntry, error) {
	var entries []LogEntry
	if err := c.Get(ctx, "/log", &entries); err != nil {
		return nil, err
	}

	if topics != "" {
		wanted := strings.ToLower(topics)
		var filtered []LogEntry
		for _, e := range entries {
			if strings.Contains(strings.ToLower(e.Topics), wanted) {
				filtered = append(filtered, e)
			}
		}
		entries = filtered
	}

	if limit > 0 && len(entries) > limit {
		entries = entries[len(entries)-limit:]
	}

	return entries, nil
}

// CategorizeLog computes log severity and functional category from topics and message.
func CategorizeLog(topics, message string) (severity string, category string) {
	tLower := strings.ToLower(topics)
	mLower := strings.ToLower(message)

	if strings.Contains(tLower, "critical") || strings.Contains(mLower, "critical") {
		severity = "critical"
	} else if strings.Contains(tLower, "error") || strings.Contains(mLower, "error") || strings.Contains(mLower, "failed") {
		severity = "error"
	} else if strings.Contains(tLower, "warning") || strings.Contains(mLower, "warning") {
		severity = "warning"
	} else {
		severity = "info"
	}

	if strings.Contains(tLower, "account") || strings.Contains(tLower, "login") || strings.Contains(tLower, "auth") ||
		strings.Contains(mLower, "logged in") || strings.Contains(mLower, "logged out") || strings.Contains(mLower, "login failure") {
		category = "auth"
	} else if strings.Contains(tLower, "dhcp") || strings.Contains(mLower, "dhcp") || strings.Contains(mLower, "assigned") || strings.Contains(mLower, "offered") {
		category = "dhcp"
	} else if strings.Contains(tLower, "wireless") || strings.Contains(tLower, "wifi") || (strings.Contains(mLower, "connected") && strings.Contains(tLower, "info")) {
		category = "wireless"
	} else if strings.Contains(tLower, "firewall") || strings.Contains(tLower, "raw") {
		category = "firewall"
	} else if strings.Contains(tLower, "interface") || strings.Contains(tLower, "ether") || strings.Contains(tLower, "sfp") || strings.Contains(tLower, "link") {
		category = "interface"
	} else {
		category = "system"
	}
	return severity, category
}

// ParseLogTimestamp safely parses RouterOS timestamp variations into a valid UTC time.Time.
func ParseLogTimestamp(raw string, now time.Time) time.Time {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return now
	}
	if t, err := time.Parse(time.RFC3339, raw); err == nil {
		return t
	}
	if len(raw) == 8 && strings.Count(raw, ":") == 2 {
		if t, err := time.Parse("15:04:05", raw); err == nil {
			return time.Date(now.Year(), now.Month(), now.Day(), t.Hour(), t.Minute(), t.Second(), 0, time.UTC)
		}
	}
	if t, err := time.Parse("jan/02 15:04:05", strings.ToLower(raw)); err == nil {
		return time.Date(now.Year(), t.Month(), t.Day(), t.Hour(), t.Minute(), t.Second(), 0, time.UTC)
	}
	if t, err := time.Parse("jan/02/2006 15:04:05", strings.ToLower(raw)); err == nil {
		return t
	}
	return now
}

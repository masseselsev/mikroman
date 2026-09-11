package routeros

import (
	"context"
	"strings"
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

package services

import (
	"context"
	"time"

	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

type LogScraperService struct {
	database *db.DB
	client   *routeros.Client
}

func NewLogScraperService(database *db.DB, client *routeros.Client) *LogScraperService {
	return &LogScraperService{
		database: database,
		client:   client,
	}
}

func (s *LogScraperService) Scrape(ctx context.Context, routerID int) error {
	if s.client == nil {
		return nil
	}

	logs, err := s.client.GetLogs(ctx, "", 100)
	if err != nil {
		return err
	}

	for _, l := range logs {
		// Dedup by router_id, external_id and message
		_, _ = s.database.SqlDB.Exec(`
			INSERT OR IGNORE INTO router_logs (router_id, external_id, timestamp, topics, message, created_at)
			VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, CURRENT_TIMESTAMP)
		`, routerID, l.ID, l.Topics, l.Message)
	}

	// Prune logs older than 14 days
	_, _ = s.database.SqlDB.Exec(`
		DELETE FROM router_logs WHERE created_at < datetime('now', '-14 days')
	`)

	return nil
}

func (s *LogScraperService) StartBackgroundLoop(ctx context.Context, interval time.Duration) {
	go func() {
		ticker := time.NewTicker(interval)
		defer ticker.Stop()

		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				routers, err := s.database.GetRouters()
				if err != nil {
					continue
				}
				for _, r := range routers {
					if r.IsActive {
						lCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
						_ = s.Scrape(lCtx, r.ID)
						cancel()
					}
				}
			}
		}
	}()
}

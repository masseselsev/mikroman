package services

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

type LogScraperService struct {
	database *db.DB
	client   *routeros.Client
	mu       sync.Mutex
	clients  map[int]*routeros.Client
}

func NewLogScraperService(database *db.DB, client *routeros.Client) *LogScraperService {
	clients := make(map[int]*routeros.Client)
	if client != nil {
		if def, err := database.GetDefaultRouter(); err == nil && def != nil {
			clients[def.ID] = client
		}
	}
	return &LogScraperService{
		database: database,
		client:   client,
		clients:  clients,
	}
}

func (s *LogScraperService) getClient(routerID int) (*routeros.Client, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if c, ok := s.clients[routerID]; ok && c != nil {
		return c, nil
	}

	defaultRouter, _ := s.database.GetDefaultRouter()
	if defaultRouter != nil && defaultRouter.ID == routerID && s.client != nil {
		s.clients[routerID] = s.client
		return s.client, nil
	}
	if defaultRouter == nil && s.client != nil {
		routers, _ := s.database.GetRouters()
		if len(routers) <= 1 {
			s.clients[routerID] = s.client
			return s.client, nil
		}
	}

	router, err := s.database.GetRouter(routerID)
	if err != nil {
		return nil, err
	}
	if router == nil {
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

	s.clients[routerID] = newClient
	return newClient, nil
}

func (s *LogScraperService) Scrape(ctx context.Context, routerID int) error {
	enabled, _ := s.database.GetSetting("log_scraping_enabled")
	if strings.EqualFold(enabled, "false") || enabled == "0" {
		return nil
	}

	client, err := s.getClient(routerID)
	if err != nil || client == nil {
		return err
	}

	logs, err := client.GetLogs(ctx, "", 100)
	if err != nil {
		return err
	}

	now := time.Now().UTC()
	for _, l := range logs {
		sev, cat := routeros.CategorizeLog(l.Topics, l.Message)
		tStamp := routeros.ParseLogTimestamp(l.Time, now)
		_, _ = s.database.SqlDB.Exec(`
			INSERT OR IGNORE INTO router_logs (router_id, external_id, timestamp, topics, message, severity, category, created_at)
			VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
		`, routerID, l.ID, tStamp.Format("2006-01-02 15:04:05"), l.Topics, l.Message, sev, cat)
	}

	// Prune logs according to configured retention days (default 14)
	retentionDays := 14
	if retVal, err := s.database.GetSetting("log_retention_days"); err == nil && retVal != "" {
		if d, err := strconv.Atoi(retVal); err == nil && d > 0 {
			retentionDays = d
		}
	}

	pruneQuery := fmt.Sprintf("DELETE FROM router_logs WHERE created_at < datetime('now', '-%d days')", retentionDays)
	_, _ = s.database.SqlDB.Exec(pruneQuery)

	return nil
}

func (s *LogScraperService) StartBackgroundLoop(ctx context.Context, interval time.Duration) {
	runPass := func() {
		routers, err := s.database.GetRouters()
		if err != nil {
			return
		}
		for _, r := range routers {
			if r.IsActive {
				lCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
				_ = s.Scrape(lCtx, r.ID)
				cancel()
			}
		}
	}

	go func() {
		// Run immediate scrape pass on startup
		runPass()

		ticker := time.NewTicker(interval)
		defer ticker.Stop()

		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				runPass()
			}
		}
	}()
}

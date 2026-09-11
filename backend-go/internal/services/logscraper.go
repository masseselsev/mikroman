package services

import (
	"context"
	"fmt"
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
	if (defaultRouter == nil || defaultRouter.ID == routerID) && s.client != nil {
		s.clients[routerID] = s.client
		return s.client, nil
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
	client, err := s.getClient(routerID)
	if err != nil || client == nil {
		return err
	}

	logs, err := client.GetLogs(ctx, "", 100)
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

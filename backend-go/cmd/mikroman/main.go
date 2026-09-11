package main

import (
	"context"
	"flag"
	"fmt"
	"log/slog"
	"mime"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/masseselsev/mikroman/internal/api"
	"github.com/masseselsev/mikroman/internal/config"
	"github.com/masseselsev/mikroman/internal/crypto"
	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
	"github.com/masseselsev/mikroman/internal/services"
)

func main() {
	_ = mime.AddExtensionType(".js", "text/javascript; charset=utf-8")
	_ = mime.AddExtensionType(".mjs", "text/javascript; charset=utf-8")
	_ = mime.AddExtensionType(".css", "text/css; charset=utf-8")
	_ = mime.AddExtensionType(".svg", "image/svg+xml")
	_ = mime.AddExtensionType(".json", "application/json")

	distDirFlag := flag.String("dist-dir", "../frontend/dist", "Path to frontend dist directory")
	dataDirFlag := flag.String("data-dir", "", "Path to data directory")
	portFlag := flag.Int("port", 0, "HTTP server port (overrides PORT env)")
	flag.Parse()

	// 1. Load config
	cfg := config.Load()
	if *portFlag > 0 {
		cfg.Port = *portFlag
	}
	if *dataDirFlag != "" {
		cfg.DataDir = *dataDirFlag
		if os.Getenv("DATABASE_URL") == "" {
			cfg.DatabaseURL = filepath.Join(cfg.DataDir, "app.db")
		}
	}

	// 2. Setup structured logging
	logLevel := slog.LevelInfo
	if cfg.Debug {
		logLevel = slog.LevelDebug
	}
	logger := slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: logLevel}))
	slog.SetDefault(logger)

	slog.Info("Initializing MikroMan Engine (Golang High-Performance Core)", "version", cfg.AppVersion)

	// 3. Resolve master cipher
	fernet, err := crypto.ResolveKey(cfg.SecretKey, cfg.DataDir)
	if err != nil {
		slog.Error("Failed to initialize master cipher", "err", err)
		os.Exit(1)
	}

	// 4. Connect SQLite database
	database, err := db.Open(cfg.DatabaseURL, fernet)
	if err != nil {
		slog.Error("Failed to open database", "url", cfg.DatabaseURL, "err", err)
		os.Exit(1)
	}
	defer database.Close()
	slog.Info("SQLite database initialized successfully", "path", cfg.DatabaseURL)

	// 5. Connect default router if available
	var client *routeros.Client
	defaultRouter, err := database.GetDefaultRouter()
	if err == nil && defaultRouter != nil {
		client, err = routeros.NewClient(routeros.Config{
			Host:      defaultRouter.Host,
			Port:      defaultRouter.Port,
			Username:  defaultRouter.Username,
			Password:  defaultRouter.Password,
			UseSSL:    defaultRouter.UseSSL,
			SSLVerify: defaultRouter.SSLVerify,
			CACert:    defaultRouter.CACert.String,
			Timeout:   5 * time.Second,
		})
		if err != nil {
			slog.Warn("Could not create RouterOS client for default router", "err", err)
		} else {
			slog.Info("Connected default router", "name", defaultRouter.Name, "host", defaultRouter.Host)
		}
	}

	// 6. Initialize WebSocket Hub
	hub := api.NewHub()

	// 7. Initialize & start background services
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if client != nil {
		discSvc := services.NewDiscoveryService(database, client, hub)
		discSvc.StartBackgroundLoop(ctx, cfg.HeavySyncIntervalSeconds)

		telemSvc := services.NewTelemetryService(database, client, hub)
		telemSvc.StartBackgroundLoop(ctx, cfg.PollIntervalSeconds)

		trafficSvc := services.NewTrafficService(database, client)
		trafficSvc.StartBackgroundLoop(ctx, cfg.HeavySyncIntervalSeconds)

		logSvc := services.NewLogScraperService(database, client)
		logSvc.StartBackgroundLoop(ctx, cfg.LogScrapeIntervalSeconds)

		slog.Info("Background workers started (Goroutines M:N scheduler)")
	}

	// 8. Build HTTP Router
	distDir := *distDirFlag
	if _, err := os.Stat(distDir); os.IsNotExist(err) {
		// Try fallback to local frontend/dist
		if _, err := os.Stat("frontend/dist"); err == nil {
			distDir = "frontend/dist"
		} else {
			distDir = ""
		}
	}

	routerHandler := api.NewRouter(api.RouterConfig{
		Config:  cfg,
		DB:      database,
		Fernet:  fernet,
		Client:  client,
		Hub:     hub,
		DistDir: distDir,
	})

	server := &http.Server{
		Addr:         fmt.Sprintf("%s:%d", cfg.Host, cfg.Port),
		Handler:      routerHandler,
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 30 * time.Second,
		IdleTimeout:  60 * time.Second,
	}

	// Graceful shutdown listener
	shutdownDone := make(chan struct{})
	go func() {
		sigChan := make(chan os.Signal, 1)
		signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)
		<-sigChan

		slog.Info("Shutting down MikroMan gracefully...")
		cancel()

		shutdownCtx, sCancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer sCancel()
		_ = server.Shutdown(shutdownCtx)
		close(shutdownDone)
	}()

	slog.Info("MikroMan HTTP server listening", "addr", server.Addr)
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		slog.Error("HTTP server failed", "err", err)
		os.Exit(1)
	}

	<-shutdownDone
	slog.Info("MikroMan cleanly stopped.")
}

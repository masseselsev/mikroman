package api

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"

	"github.com/masseselsev/mikroman/internal/config"
	"github.com/masseselsev/mikroman/internal/crypto"
	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

// RouterConfig holds all dependencies for API routes.
type RouterConfig struct {
	Config   *config.Config
	DB       *db.DB
	Fernet   *crypto.Fernet
	Client   *routeros.Client
	Hub      *Hub
	DistDir  string
}

// NewRouter constructs the Chi router with all endpoints and SPA fallback.
func NewRouter(rc RouterConfig) http.Handler {
	r := chi.NewRouter()

	// Global middleware
	r.Use(middleware.RequestID)
	r.Use(middleware.RealIP)
	r.Use(middleware.Recoverer)
	r.Use(CORSMiddleware)
	r.Use(AuthMiddleware(rc.Config, rc.Fernet))

	// API v1 Subrouter
	r.Route("/api/v1", func(api chi.Router) {
		// Public health check
		api.Get("/health", func(w http.ResponseWriter, r *http.Request) {
			WriteJSON(w, http.StatusOK, map[string]string{"status": "ok"})
		})

		// WebSocket
		if rc.Hub != nil {
			api.Get("/ws", rc.Hub.HandleWS)
		}

		// Auth
		authH := NewAuthHandler(rc.Config, rc.DB, rc.Fernet)
		api.Route("/auth", func(a chi.Router) {
			a.Get("/status", authH.GetStatus)
			a.Post("/login", authH.Login)
			a.Post("/logout", authH.Logout)
			a.Post("/setup", authH.Setup)
		})

		// System
		sysH := NewSystemHandler(rc.Config, rc.DB, rc.Client)
		api.Route("/system", func(s chi.Router) {
			s.Get("/health", sysH.GetHealth)
			s.Get("/diagnostics", sysH.GetDiagnostics)
			s.Get("/settings", sysH.GetSettings)
			s.Post("/settings", sysH.SaveSettings)
			s.Post("/reboot", sysH.Reboot)
		})

		// Routers
		routerH := NewRouterHandler(rc.DB)
		api.Route("/routers", func(rt chi.Router) {
			rt.Get("/", routerH.List)
			rt.Post("/", routerH.Create)
			rt.Post("/test", routerH.TestConnection)
			rt.Get("/{id}", routerH.Get)
			rt.Post("/{id}/activate", routerH.Activate)
			rt.Delete("/{id}", routerH.Delete)
		})

		// Devices
		devH := NewDeviceHandler(rc.DB)
		api.Route("/devices", func(d chi.Router) {
			d.Get("/", devH.List)
			d.Patch("/{id}", devH.Update)
			d.Delete("/{id}", devH.Delete)
			d.Post("/{id}/pause", devH.Pause)
			d.Post("/{id}/limit", devH.Limit)
			d.Get("/{id}/history", devH.History)
		})

		// Users
		userH := NewUserHandler(rc.DB)
		api.Route("/users", func(u chi.Router) {
			u.Get("/", userH.List)
			u.Post("/", userH.Create)
			u.Post("/reorder", userH.Reorder)
			u.Get("/{id}", userH.Get)
			u.Patch("/{id}", userH.Update)
			u.Delete("/{id}", userH.Delete)
		})

		// Traffic
		trafficH := NewTrafficHandler(rc.DB)
		api.Route("/traffic", func(t chi.Router) {
			t.Post("/users/{id}/limit", trafficH.SetUserLimit)
			t.Post("/users/{id}/pause", trafficH.ToggleUserPause)
		})

		// Analytics
		analyticsH := NewAnalyticsHandler(rc.DB)
		api.Route("/analytics", func(an chi.Router) {
			an.Get("/traffic", analyticsH.GetTrafficOverview)
			an.Get("/quota", analyticsH.GetQuota)
			an.Post("/quota", analyticsH.SaveQuota)
			an.Get("/users/{id}/traffic-history", analyticsH.UserHistory)
			an.Get("/devices/{id}/traffic-history", analyticsH.DeviceHistory)
		})

		// Logs
		logH := NewLogHandler(rc.DB)
		api.Route("/logs", func(l chi.Router) {
			l.Get("/", logH.GetLogs)
			l.Delete("/", logH.ClearLogs)
		})
	})

	// Serve Frontend Single-Page App (SPA)
	if rc.DistDir != "" {
		serveSPA(r, rc.DistDir)
	}

	return r
}

func serveSPA(r chi.Router, distDir string) {
	fileServer := http.FileServer(http.Dir(distDir))

	r.Get("/*", func(w http.ResponseWriter, req *http.Request) {
		path := req.URL.Path

		// Do not intercept API requests
		if strings.HasPrefix(path, "/api/") {
			http.NotFound(w, req)
			return
		}

		fullPath := filepath.Join(distDir, filepath.Clean(path))
		info, err := os.Stat(fullPath)
		if err == nil && !info.IsDir() {
			fileServer.ServeHTTP(w, req)
			return
		}

		// Fallback to index.html for client-side routing
		http.ServeFile(w, req, filepath.Join(distDir, "index.html"))
	})
}

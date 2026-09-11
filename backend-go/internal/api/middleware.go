package api

import (
	"context"
	"net/http"
	"strings"

	"github.com/masseselsev/mikroman/internal/config"
	"github.com/masseselsev/mikroman/internal/crypto"
)

type contextKey string

const (
	UserContextKey contextKey = "user"
	SessionCookie             = "mikroman_session"
	CSRFCookie                = "mikroman_csrf"
	CSRFHeader                = "X-CSRF-Token"
)

// CORSMiddleware enables CORS for the Vue frontend.
func CORSMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		origin := r.Header.Get("Origin")
		if origin != "" {
			w.Header().Set("Access-Control-Allow-Origin", origin)
		} else {
			w.Header().Set("Access-Control-Allow-Origin", "*")
		}
		w.Header().Set("Access-Control-Allow-Credentials", "true")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key, X-CSRF-Token")

		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusOK)
			return
		}

		next.ServeHTTP(w, r)
	})
}

// AuthMiddleware enforces session tokens and Double-Submit CSRF on protected routes.
func AuthMiddleware(cfg *config.Config, fernet *crypto.Fernet) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			path := r.URL.Path

			// Allow preflight options
			if r.Method == http.MethodOptions {
				next.ServeHTTP(w, r)
				return
			}

			// Only protect /api/v1/* routes
			if !strings.HasPrefix(path, "/api/v1/") {
				next.ServeHTTP(w, r)
				return
			}

			// Public endpoint exemptions
			if strings.HasPrefix(path, "/api/v1/auth/") ||
				path == "/api/v1/health" ||
				path == "/api/v1/system/health" ||
				path == "/api/v1/telegram/webhook" ||
				path == "/api/v1/ws" {
				next.ServeHTTP(w, r)
				return
			}

			// If authentication disabled in config
			if !cfg.AuthEnabled {
				ctx := context.WithValue(r.Context(), UserContextKey, "admin")
				next.ServeHTTP(w, r.WithContext(ctx))
				return
			}

			// 1. Check Bearer token or X-API-Key header
			authHeader := r.Header.Get("Authorization")
			apiKeyHeader := r.Header.Get("X-API-Key")
			var token string
			if strings.HasPrefix(strings.ToLower(authHeader), "bearer ") {
				token = strings.TrimSpace(authHeader[7:])
			} else if apiKeyHeader != "" {
				token = strings.TrimSpace(apiKeyHeader)
			}

			if token != "" {
				if fernet != nil {
					payload, err := fernet.VerifySessionToken(token)
					if err == nil && payload != nil {
						ctx := context.WithValue(r.Context(), UserContextKey, payload.Sub)
						next.ServeHTTP(w, r.WithContext(ctx))
						return
					}
				}
				WriteError(w, http.StatusUnauthorized, "Invalid API token")
				return
			}

			// 2. Check Session cookie
			sessionCookie, err := r.Cookie(SessionCookie)
			if err != nil || sessionCookie.Value == "" {
				WriteError(w, http.StatusUnauthorized, "Authentication required")
				return
			}

			if fernet == nil {
				WriteError(w, http.StatusInternalServerError, "Cipher not initialized")
				return
			}

			payload, err := fernet.VerifySessionToken(sessionCookie.Value)
			if err != nil || payload == nil {
				WriteError(w, http.StatusUnauthorized, "Session expired or invalid")
				return
			}

			// 3. Double-Submit CSRF validation on mutating methods
			if r.Method == http.MethodPost || r.Method == http.MethodPut || r.Method == http.MethodPatch || r.Method == http.MethodDelete {
				csrfCookie, err := r.Cookie(CSRFCookie)
				csrfHeader := r.Header.Get(CSRFHeader)

				if err != nil || csrfCookie.Value == "" || csrfHeader == "" || !crypto.VerifyCSRFToken(csrfHeader, csrfCookie.Value) {
					WriteError(w, http.StatusForbidden, "CSRF verification failed")
					return
				}
			}

			ctx := context.WithValue(r.Context(), UserContextKey, payload.Sub)
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}

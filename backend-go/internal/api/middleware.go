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

// CORSMiddleware enables CORS for the frontend while complying strictly with W3C Fetch specs.
func CORSMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		origin := r.Header.Get("Origin")
		if origin != "" {
			w.Header().Set("Access-Control-Allow-Origin", origin)
			w.Header().Set("Access-Control-Allow-Credentials", "true")
			w.Header().Set("Vary", "Origin")
		}
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key, X-CSRF-Token")

		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusOK)
			return
		}

		next.ServeHTTP(w, r)
	})
}

// VerifyRequestAuth verifies if the request carries valid credentials (cookie, Bearer token, or query parameter token).
// Returns (username, true) if authenticated, or ("", false) otherwise.
func VerifyRequestAuth(r *http.Request, cfg *config.Config, fernet *crypto.Fernet) (string, bool) {
	if cfg == nil || !cfg.AuthEnabled {
		return "admin", true
	}
	if fernet == nil {
		return "", false
	}

	// 1. Query token parameter (?token=...)
	if token := r.URL.Query().Get("token"); token != "" {
		if payload, err := fernet.VerifySessionToken(token); err == nil && payload != nil {
			return payload.Sub, true
		}
	}

	// 2. Check Bearer token or X-API-Key header
	authHeader := r.Header.Get("Authorization")
	apiKeyHeader := r.Header.Get("X-API-Key")
	var token string
	if strings.HasPrefix(strings.ToLower(authHeader), "bearer ") {
		token = strings.TrimSpace(authHeader[7:])
	} else if apiKeyHeader != "" {
		token = strings.TrimSpace(apiKeyHeader)
	}
	if token != "" {
		if payload, err := fernet.VerifySessionToken(token); err == nil && payload != nil {
			return payload.Sub, true
		}
		return "", false
	}

	// 3. Check Session cookie
	if sessionCookie, err := r.Cookie(SessionCookie); err == nil && sessionCookie.Value != "" {
		if payload, err := fernet.VerifySessionToken(sessionCookie.Value); err == nil && payload != nil {
			return payload.Sub, true
		}
	}

	return "", false
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
				path == "/api/v1/system/version-check" ||
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

			username, ok := VerifyRequestAuth(r, cfg, fernet)
			if !ok {
				WriteError(w, http.StatusUnauthorized, "Authentication required")
				return
			}

			// Double-Submit CSRF validation on mutating methods for cookie-based requests
			if r.Method == http.MethodPost || r.Method == http.MethodPut || r.Method == http.MethodPatch || r.Method == http.MethodDelete {
				authHeader := r.Header.Get("Authorization")
				apiKeyHeader := r.Header.Get("X-API-Key")
				// Token-authenticated requests don't require CSRF header
				if authHeader == "" && apiKeyHeader == "" && r.URL.Query().Get("token") == "" {
					csrfCookie, err := r.Cookie(CSRFCookie)
					csrfHeader := r.Header.Get(CSRFHeader)

					if err != nil || csrfCookie.Value == "" || csrfHeader == "" || !crypto.VerifyCSRFToken(csrfHeader, csrfCookie.Value) {
						WriteError(w, http.StatusForbidden, "CSRF verification failed")
						return
					}
				}
			}

			ctx := context.WithValue(r.Context(), UserContextKey, username)
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}

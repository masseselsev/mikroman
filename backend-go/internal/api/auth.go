package api

import (
	"crypto/subtle"
	"encoding/json"
	"net/http"

	"github.com/masseselsev/mikroman/internal/config"
	"github.com/masseselsev/mikroman/internal/crypto"
	"github.com/masseselsev/mikroman/internal/db"
)

type AuthHandler struct {
	cfg      *config.Config
	database *db.DB
	fernet   *crypto.Fernet
}

func NewAuthHandler(cfg *config.Config, database *db.DB, fernet *crypto.Fernet) *AuthHandler {
	return &AuthHandler{
		cfg:      cfg,
		database: database,
		fernet:   fernet,
	}
}

type LoginRequest struct {
	Password string `json:"password"`
}

type AuthStatusResponse struct {
	AuthEnabled   bool   `json:"auth_enabled"`
	Authenticated bool   `json:"authenticated"`
	NeedsSetup    bool   `json:"needs_setup"`
	Username      string `json:"username"`
}

func (h *AuthHandler) GetStatus(w http.ResponseWriter, r *http.Request) {
	// Ensure frontend has a CSRF cookie on initial load
	if _, err := r.Cookie(CSRFCookie); err != nil {
		csrfToken := crypto.GenerateCSRFToken()
		http.SetCookie(w, &http.Cookie{
			Name:     CSRFCookie,
			Value:    csrfToken,
			Path:     "/",
			HttpOnly: false,
			SameSite: http.SameSiteLaxMode,
			MaxAge:   86400 * 7,
		})
	}

	if !h.cfg.AuthEnabled {
		WriteJSON(w, http.StatusOK, AuthStatusResponse{
			AuthEnabled:   false,
			Authenticated: true,
			NeedsSetup:    false,
			Username:      "admin",
		})
		return
	}

	storedHash, _ := h.database.GetSetting("admin_password_hash")
	needsSetup := (storedHash == "" && h.cfg.AdminPassword == "")

	authenticated := false
	username := ""
	if cookie, err := r.Cookie(SessionCookie); err == nil && cookie.Value != "" && h.fernet != nil {
		if payload, err := h.fernet.VerifySessionToken(cookie.Value); err == nil && payload != nil {
			authenticated = true
			username = payload.Sub
		}
	}

	WriteJSON(w, http.StatusOK, AuthStatusResponse{
		AuthEnabled:   true,
		Authenticated: authenticated,
		NeedsSetup:    needsSetup,
		Username:      username,
	})
}

func (h *AuthHandler) Login(w http.ResponseWriter, r *http.Request) {
	var req LoginRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid request payload")
		return
	}

	valid := false

	// 1. Environment variable comparison
	if h.cfg.AdminPassword != "" && subtle.ConstantTimeCompare([]byte(req.Password), []byte(h.cfg.AdminPassword)) == 1 {
		valid = true
	} else {
		// 2. Database hash verification
		storedHash, _ := h.database.GetSetting("admin_password_hash")
		if storedHash != "" && crypto.VerifyPassword(req.Password, storedHash) {
			valid = true
		}
	}

	if !valid {
		WriteError(w, http.StatusUnauthorized, "Invalid credentials")
		return
	}

	// Create session token
	sessionToken, err := h.fernet.CreateSessionToken("admin", 7)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to create session token")
		return
	}

	csrfToken := crypto.GenerateCSRFToken()

	// Set HttpOnly session cookie
	http.SetCookie(w, &http.Cookie{
		Name:     SessionCookie,
		Value:    sessionToken,
		Path:     "/",
		HttpOnly: true,
		SameSite: http.SameSiteLaxMode,
		MaxAge:   86400 * 7,
	})

	// Set non-HttpOnly CSRF cookie
	http.SetCookie(w, &http.Cookie{
		Name:     CSRFCookie,
		Value:    csrfToken,
		Path:     "/",
		HttpOnly: false,
		SameSite: http.SameSiteLaxMode,
		MaxAge:   86400 * 7,
	})

	WriteJSON(w, http.StatusOK, map[string]interface{}{
		"access_token": sessionToken,
		"token_type":   "bearer",
		"username":     "admin",
	})
}

func (h *AuthHandler) Logout(w http.ResponseWriter, r *http.Request) {
	http.SetCookie(w, &http.Cookie{
		Name:     SessionCookie,
		Value:    "",
		Path:     "/",
		HttpOnly: true,
		MaxAge:   -1,
	})
	http.SetCookie(w, &http.Cookie{
		Name:     CSRFCookie,
		Value:    "",
		Path:     "/",
		HttpOnly: false,
		MaxAge:   -1,
	})
	WriteJSON(w, http.StatusOK, map[string]string{"message": "Logged out successfully"})
}

func (h *AuthHandler) Setup(w http.ResponseWriter, r *http.Request) {
	var req LoginRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || len(req.Password) < 4 {
		WriteError(w, http.StatusBadRequest, "Password must be at least 4 characters long")
		return
	}

	hash, err := crypto.HashPassword(req.Password)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to hash password")
		return
	}

	if err := h.database.SetSetting("admin_password_hash", hash, "PBKDF2 hash of admin password"); err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to save password hash")
		return
	}

	WriteJSON(w, http.StatusOK, map[string]string{"message": "Admin password configured successfully"})
}

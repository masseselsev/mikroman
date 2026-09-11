package api

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"

	"github.com/masseselsev/mikroman/internal/config"
	"github.com/masseselsev/mikroman/internal/crypto"
	"github.com/masseselsev/mikroman/internal/db"
)

func setupTestServer(t *testing.T) (http.Handler, *db.DB, *crypto.Fernet) {
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test.db")

	fernet, err := crypto.NewFernet("cw_z4pYJ2-8_9V18R5v6R1XbJ9i9w9G1R1XbJ9i9w9E=")
	if err != nil {
		t.Fatalf("failed to create Fernet: %v", err)
	}

	database, err := db.Open(dbPath, fernet)
	if err != nil {
		t.Fatalf("failed to open database: %v", err)
	}

	cfg := &config.Config{
		AppVersion:    "0.3.0-test",
		AdminPassword: "SecretAdminPassword123",
		AuthEnabled:   true,
	}

	handler := NewRouter(RouterConfig{
		Config: cfg,
		DB:     database,
		Fernet: fernet,
		Hub:    NewHub(),
	})

	return handler, database, fernet
}

func TestHealthEndpoint(t *testing.T) {
	handler, database, _ := setupTestServer(t)
	defer database.Close()

	req := httptest.NewRequest(http.MethodGet, "/api/v1/health", nil)
	w := httptest.NewRecorder()

	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
}

func TestAuthStatusAndLoginFlow(t *testing.T) {
	handler, database, _ := setupTestServer(t)
	defer database.Close()

	// 1. Status sets CSRF cookie
	reqStatus := httptest.NewRequest(http.MethodGet, "/api/v1/auth/status", nil)
	wStatus := httptest.NewRecorder()
	handler.ServeHTTP(wStatus, reqStatus)

	if wStatus.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", wStatus.Code)
	}

	var csrfCookie *http.Cookie
	for _, c := range wStatus.Result().Cookies() {
		if c.Name == CSRFCookie {
			csrfCookie = c
			break
		}
	}
	if csrfCookie == nil || csrfCookie.Value == "" {
		t.Fatalf("expected CSRF cookie to be set")
	}

	// 2. Bad Login
	badBody := bytes.NewBufferString(`{"password": "WrongPassword"}`)
	reqBad := httptest.NewRequest(http.MethodPost, "/api/v1/auth/login", badBody)
	wBad := httptest.NewRecorder()
	handler.ServeHTTP(wBad, reqBad)

	if wBad.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 for bad password, got %d", wBad.Code)
	}

	// 3. Good Login
	goodBody := bytes.NewBufferString(`{"password": "SecretAdminPassword123"}`)
	reqGood := httptest.NewRequest(http.MethodPost, "/api/v1/auth/login", goodBody)
	wGood := httptest.NewRecorder()
	handler.ServeHTTP(wGood, reqGood)

	if wGood.Code != http.StatusOK {
		t.Fatalf("expected 200 for good password, got %d", wGood.Code)
	}

	var sessionCookie *http.Cookie
	for _, c := range wGood.Result().Cookies() {
		if c.Name == SessionCookie {
			sessionCookie = c
			break
		}
	}
	if sessionCookie == nil || sessionCookie.Value == "" {
		t.Fatalf("expected session cookie to be set")
	}

	// 4. Protected endpoint with valid session
	reqRouters := httptest.NewRequest(http.MethodGet, "/api/v1/routers", nil)
	reqRouters.AddCookie(sessionCookie)
	wRouters := httptest.NewRecorder()
	handler.ServeHTTP(wRouters, reqRouters)

	if wRouters.Code != http.StatusOK {
		t.Fatalf("expected 200 for authenticated user, got %d", wRouters.Code)
	}

	// 5. Mutating endpoint: User creation without CSRF should fail 403
	userJSON := []byte(`{"name": "Alice"}`)
	reqMutateNoCSRF := httptest.NewRequest(http.MethodPost, "/api/v1/users", bytes.NewReader(userJSON))
	reqMutateNoCSRF.AddCookie(sessionCookie)
	wMutateNoCSRF := httptest.NewRecorder()
	handler.ServeHTTP(wMutateNoCSRF, reqMutateNoCSRF)

	if wMutateNoCSRF.Code != http.StatusForbidden {
		t.Fatalf("expected 403 without CSRF token, got %d", wMutateNoCSRF.Code)
	}

	// 6. Mutating endpoint with valid CSRF should succeed 201
	reqMutate := httptest.NewRequest(http.MethodPost, "/api/v1/users", bytes.NewReader(userJSON))
	reqMutate.AddCookie(sessionCookie)
	reqMutate.AddCookie(csrfCookie)
	reqMutate.Header.Set(CSRFHeader, csrfCookie.Value)
	wMutate := httptest.NewRecorder()
	handler.ServeHTTP(wMutate, reqMutate)

	if wMutate.Code != http.StatusCreated {
		t.Fatalf("expected 201 with valid CSRF, got %d", wMutate.Code)
	}

	// Verify user was created
	var resp APIResponse
	_ = json.NewDecoder(wMutate.Body).Decode(&resp)
	if !resp.Success {
		t.Fatalf("expected success: true, got %+v", resp)
	}
}

package api

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
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
		AppVersion:    "0.3.2-test",
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

func TestCORSBehavior(t *testing.T) {
	handler, database, _ := setupTestServer(t)
	defer database.Close()

	// 1. Request with Origin header
	reqWithOrigin := httptest.NewRequest(http.MethodGet, "/api/v1/health", nil)
	reqWithOrigin.Header.Set("Origin", "http://192.168.123.1:1928")
	wWithOrigin := httptest.NewRecorder()
	handler.ServeHTTP(wWithOrigin, reqWithOrigin)

	if wWithOrigin.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", wWithOrigin.Code)
	}
	if got := wWithOrigin.Header().Get("Access-Control-Allow-Origin"); got != "http://192.168.123.1:1928" {
		t.Errorf("expected reflected origin, got %q", got)
	}
	if got := wWithOrigin.Header().Get("Access-Control-Allow-Credentials"); got != "true" {
		t.Errorf("expected Allow-Credentials true, got %q", got)
	}
	if got := wWithOrigin.Header().Get("Vary"); got != "Origin" {
		t.Errorf("expected Vary Origin, got %q", got)
	}

	// 2. Request WITHOUT Origin header: MUST NOT return wildcard '*' with Allow-Credentials 'true'
	reqNoOrigin := httptest.NewRequest(http.MethodGet, "/api/v1/health", nil)
	wNoOrigin := httptest.NewRecorder()
	handler.ServeHTTP(wNoOrigin, reqNoOrigin)

	if wNoOrigin.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", wNoOrigin.Code)
	}
	if wNoOrigin.Header().Get("Access-Control-Allow-Origin") == "*" && wNoOrigin.Header().Get("Access-Control-Allow-Credentials") == "true" {
		t.Fatalf("CORS violation: Access-Control-Allow-Origin: * combined with Access-Control-Allow-Credentials: true")
	}
}

func TestSPAServingAndAssets(t *testing.T) {
	tempDist := t.TempDir()
	assetsDir := filepath.Join(tempDist, "assets")

	// Create test index.html
	indexPath := filepath.Join(tempDist, "index.html")
	if err := os.WriteFile(indexPath, []byte("<!DOCTYPE html><html><body><div id=\"root\"></div></body></html>"), 0644); err != nil {
		t.Fatal(err)
	}

	// Create test assets
	if err := os.MkdirAll(assetsDir, 0755); err != nil {
		t.Fatal(err)
	}
	jsPath := filepath.Join(assetsDir, "bundle-test.js")
	if err := os.WriteFile(jsPath, []byte("console.log('test');"), 0644); err != nil {
		t.Fatal(err)
	}

	tempDB := filepath.Join(t.TempDir(), "spa.db")
	database, err := db.Open(tempDB, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer database.Close()

	handler := NewRouter(RouterConfig{
		Config:  &config.Config{AuthEnabled: false},
		DB:      database,
		DistDir: tempDist,
	})

	// 1. Root / returns index.html with no-cache headers
	reqRoot := httptest.NewRequest(http.MethodGet, "/", nil)
	wRoot := httptest.NewRecorder()
	handler.ServeHTTP(wRoot, reqRoot)
	if wRoot.Code != http.StatusOK {
		t.Fatalf("expected 200 for /, got %d", wRoot.Code)
	}
	if cc := wRoot.Header().Get("Cache-Control"); cc != "no-cache, no-store, must-revalidate" {
		t.Errorf("expected no-cache headers for index.html, got %q", cc)
	}

	// 2. Existing asset returns 200 with immutable cache and no CORS headers
	reqAsset := httptest.NewRequest(http.MethodGet, "/assets/bundle-test.js", nil)
	wAsset := httptest.NewRecorder()
	handler.ServeHTTP(wAsset, reqAsset)
	if wAsset.Code != http.StatusOK {
		t.Fatalf("expected 200 for /assets/bundle-test.js, got %d", wAsset.Code)
	}
	if cc := wAsset.Header().Get("Cache-Control"); cc != "public, max-age=31536000, immutable" {
		t.Errorf("expected immutable cache for asset, got %q", cc)
	}
	if cors := wAsset.Header().Get("Access-Control-Allow-Origin"); cors != "" {
		t.Errorf("expected no CORS on static asset, got %q", cors)
	}

	// 3. Missing asset returns 404 Not Found (NOT 200 index.html!)
	reqMissingAsset := httptest.NewRequest(http.MethodGet, "/assets/non-existent.js", nil)
	wMissingAsset := httptest.NewRecorder()
	handler.ServeHTTP(wMissingAsset, reqMissingAsset)
	if wMissingAsset.Code != http.StatusNotFound {
		t.Fatalf("expected 404 for missing asset, got %d", wMissingAsset.Code)
	}

	// 4. Client-side route fallback to index.html with no-cache
	reqRoute := httptest.NewRequest(http.MethodGet, "/users/123", nil)
	wRoute := httptest.NewRecorder()
	handler.ServeHTTP(wRoute, reqRoute)
	if wRoute.Code != http.StatusOK {
		t.Fatalf("expected 200 for client-side route, got %d", wRoute.Code)
	}
	if cc := wRoute.Header().Get("Cache-Control"); cc != "no-cache, no-store, must-revalidate" {
		t.Errorf("expected no-cache headers for SPA route, got %q", cc)
	}

	// 5. API 404 does NOT serve index.html
	reqAPI404 := httptest.NewRequest(http.MethodGet, "/api/v1/does-not-exist", nil)
	wAPI404 := httptest.NewRecorder()
	handler.ServeHTTP(wAPI404, reqAPI404)
	if wAPI404.Code != http.StatusNotFound {
		t.Fatalf("expected 404 for missing API route, got %d", wAPI404.Code)
	}
}

func TestAlertsAndScanEndpoints(t *testing.T) {
	handler, database, _ := setupTestServer(t)
	defer database.Close()

	// Login to obtain session
	goodBody := bytes.NewBufferString(`{"password": "SecretAdminPassword123"}`)
	reqGood := httptest.NewRequest(http.MethodPost, "/api/v1/auth/login", goodBody)
	wGood := httptest.NewRecorder()
	handler.ServeHTTP(wGood, reqGood)

	var sessionCookie *http.Cookie
	for _, c := range wGood.Result().Cookies() {
		if c.Name == SessionCookie {
			sessionCookie = c
			break
		}
	}

	// 1. GET /api/v1/system/alerts
	reqAlerts := httptest.NewRequest(http.MethodGet, "/api/v1/system/alerts", nil)
	reqAlerts.AddCookie(sessionCookie)
	wAlerts := httptest.NewRecorder()
	handler.ServeHTTP(wAlerts, reqAlerts)
	if wAlerts.Code != http.StatusOK {
		t.Fatalf("expected 200 for alerts, got %d", wAlerts.Code)
	}

	// 2. POST /api/v1/devices/scan
	// Fetch CSRF
	reqStatus := httptest.NewRequest(http.MethodGet, "/api/v1/auth/status", nil)
	wStatus := httptest.NewRecorder()
	handler.ServeHTTP(wStatus, reqStatus)
	var csrfCookie *http.Cookie
	for _, c := range wStatus.Result().Cookies() {
		if c.Name == CSRFCookie {
			csrfCookie = c
			break
		}
	}

	reqScan := httptest.NewRequest(http.MethodPost, "/api/v1/devices/scan", nil)
	reqScan.AddCookie(sessionCookie)
	reqScan.AddCookie(csrfCookie)
	reqScan.Header.Set(CSRFHeader, csrfCookie.Value)
	wScan := httptest.NewRecorder()
	handler.ServeHTTP(wScan, reqScan)
	if wScan.Code != http.StatusOK {
		t.Fatalf("expected 200 for scan, got %d", wScan.Code)
	}
}

func TestNewDashboardEndpoints(t *testing.T) {
	handler, database, _ := setupTestServer(t)
	defer database.Close()

	// 1. Obtain session & csrf
	reqStatus := httptest.NewRequest(http.MethodGet, "/api/v1/auth/status", nil)
	wStatus := httptest.NewRecorder()
	handler.ServeHTTP(wStatus, reqStatus)
	var csrfCookie *http.Cookie
	for _, c := range wStatus.Result().Cookies() {
		if c.Name == CSRFCookie {
			csrfCookie = c
			break
		}
	}

	goodBody := bytes.NewBufferString(`{"password": "SecretAdminPassword123"}`)
	reqLogin := httptest.NewRequest(http.MethodPost, "/api/v1/auth/login", goodBody)
	wLogin := httptest.NewRecorder()
	handler.ServeHTTP(wLogin, reqLogin)
	var sessionCookie *http.Cookie
	for _, c := range wLogin.Result().Cookies() {
		if c.Name == SessionCookie {
			sessionCookie = c
			break
		}
	}

	// 2. GET /api/v1/system/interfaces
	reqIfaces := httptest.NewRequest(http.MethodGet, "/api/v1/system/interfaces", nil)
	reqIfaces.AddCookie(sessionCookie)
	wIfaces := httptest.NewRecorder()
	handler.ServeHTTP(wIfaces, reqIfaces)
	if wIfaces.Code != http.StatusOK {
		t.Fatalf("expected 200 for interfaces, got %d", wIfaces.Code)
	}

	// 3. GET /api/v1/system/ip-lookup
	reqIP := httptest.NewRequest(http.MethodGet, "/api/v1/system/ip-lookup", nil)
	reqIP.AddCookie(sessionCookie)
	wIP := httptest.NewRecorder()
	handler.ServeHTTP(wIP, reqIP)
	if wIP.Code != http.StatusOK {
		t.Fatalf("expected 200 for ip-lookup, got %d", wIP.Code)
	}

	// 4. POST /api/v1/system/ip-lookup
	ipCfgJSON := []byte(`{"default_id":"shodan","services":[{"id":"shodan","name":"Shodan","url_template":"https://shodan.io/host/{ip}","builtin":true}]}`)
	reqSaveIP := httptest.NewRequest(http.MethodPost, "/api/v1/system/ip-lookup", bytes.NewReader(ipCfgJSON))
	reqSaveIP.AddCookie(sessionCookie)
	reqSaveIP.AddCookie(csrfCookie)
	reqSaveIP.Header.Set(CSRFHeader, csrfCookie.Value)
	wSaveIP := httptest.NewRecorder()
	handler.ServeHTTP(wSaveIP, reqSaveIP)
	if wSaveIP.Code != http.StatusOK {
		t.Fatalf("expected 200 for save ip-lookup, got %d", wSaveIP.Code)
	}

	// 5. GET /api/v1/metrics/config
	reqMCfg := httptest.NewRequest(http.MethodGet, "/api/v1/metrics/config", nil)
	reqMCfg.AddCookie(sessionCookie)
	wMCfg := httptest.NewRecorder()
	handler.ServeHTTP(wMCfg, reqMCfg)
	if wMCfg.Code != http.StatusOK {
		t.Fatalf("expected 200 for metrics config, got %d", wMCfg.Code)
	}

	// 6. POST /api/v1/metrics/config
	mCfgJSON := []byte(`{"selected_interfaces":["ether1"]}`)
	reqSaveM := httptest.NewRequest(http.MethodPost, "/api/v1/metrics/config", bytes.NewReader(mCfgJSON))
	reqSaveM.AddCookie(sessionCookie)
	reqSaveM.AddCookie(csrfCookie)
	reqSaveM.Header.Set(CSRFHeader, csrfCookie.Value)
	wSaveM := httptest.NewRecorder()
	handler.ServeHTTP(wSaveM, reqSaveM)
	if wSaveM.Code != http.StatusOK {
		t.Fatalf("expected 200 for save metrics config, got %d", wSaveM.Code)
	}

	// 7. GET /api/v1/logs/stats
	reqLogStats := httptest.NewRequest(http.MethodGet, "/api/v1/logs/stats", nil)
	reqLogStats.AddCookie(sessionCookie)
	wLogStats := httptest.NewRecorder()
	handler.ServeHTTP(wLogStats, reqLogStats)
	if wLogStats.Code != http.StatusOK {
		t.Fatalf("expected 200 for log stats, got %d", wLogStats.Code)
	}

	// 8. GET /api/v1/users (returns enriched user list)
	reqUsers := httptest.NewRequest(http.MethodGet, "/api/v1/users", nil)
	reqUsers.AddCookie(sessionCookie)
	wUsers := httptest.NewRecorder()
	handler.ServeHTTP(wUsers, reqUsers)
	if wUsers.Code != http.StatusOK {
		t.Fatalf("expected 200 for users, got %d", wUsers.Code)
	}
}


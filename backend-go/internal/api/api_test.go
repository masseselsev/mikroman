package api

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"

	"github.com/masseselsev/mikroman/internal/config"
	"github.com/masseselsev/mikroman/internal/crypto"
	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
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
		AppVersion:    "0.3.4-test",
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

func TestBillingCycleAndTrafficAnalytics(t *testing.T) {
	handler, database, _ := setupTestServer(t)
	defer database.Close()

	// 1. Status sets CSRF cookie
	reqStatus := httptest.NewRequest(http.MethodGet, "/api/v1/auth/status", nil)
	wStatus := httptest.NewRecorder()
	handler.ServeHTTP(wStatus, reqStatus)
	var csrfCookie *http.Cookie
	for _, c := range wStatus.Result().Cookies() {
		if c.Name == CSRFCookie {
			csrfCookie = c
		}
	}
	if csrfCookie == nil {
		t.Fatal("expected CSRF cookie")
	}

	// 2. Authenticate
	loginJSON := []byte(`{"password":"SecretAdminPassword123"}`)
	reqLogin := httptest.NewRequest(http.MethodPost, "/api/v1/auth/login", bytes.NewReader(loginJSON))
	reqLogin.Header.Set("Content-Type", "application/json")
	reqLogin.AddCookie(csrfCookie)
	reqLogin.Header.Set(CSRFHeader, csrfCookie.Value)
	wLogin := httptest.NewRecorder()
	handler.ServeHTTP(wLogin, reqLogin)

	var sessionCookie *http.Cookie
	for _, c := range wLogin.Result().Cookies() {
		if c.Name == SessionCookie {
			sessionCookie = c
		}
	}
	if sessionCookie == nil {
		t.Fatal("expected session cookie")
	}

	// 1. GET /api/v1/analytics/billing-cycle (default Day 1, 00:00)
	reqBC := httptest.NewRequest(http.MethodGet, "/api/v1/analytics/billing-cycle", nil)
	reqBC.AddCookie(sessionCookie)
	wBC := httptest.NewRecorder()
	handler.ServeHTTP(wBC, reqBC)
	if wBC.Code != http.StatusOK {
		t.Fatalf("expected 200 for billing cycle, got %d: %s", wBC.Code, wBC.Body.String())
	}
	var bcResp APIResponse
	if err := json.NewDecoder(wBC.Body).Decode(&bcResp); err != nil {
		t.Fatalf("failed to decode billing cycle response: %v", err)
	}
	bcData, ok := bcResp.Data.(map[string]interface{})
	if !ok {
		t.Fatalf("expected object in response data, got %T", bcResp.Data)
	}
	if int(bcData["anchor_day"].(float64)) != 1 {
		t.Fatalf("expected default anchor_day 1, got %v", bcData["anchor_day"])
	}

	// 2. POST /api/v1/analytics/billing-cycle
	saveBCJSON := []byte(`{"anchor_day":18,"anchor_hour":10,"anchor_minute":30}`)
	reqSaveBC := httptest.NewRequest(http.MethodPost, "/api/v1/analytics/billing-cycle", bytes.NewReader(saveBCJSON))
	reqSaveBC.AddCookie(sessionCookie)
	reqSaveBC.AddCookie(csrfCookie)
	reqSaveBC.Header.Set(CSRFHeader, csrfCookie.Value)
	wSaveBC := httptest.NewRecorder()
	handler.ServeHTTP(wSaveBC, reqSaveBC)
	if wSaveBC.Code != http.StatusOK {
		t.Fatalf("expected 200 for save billing cycle, got %d: %s", wSaveBC.Code, wSaveBC.Body.String())
	}

	// 3. GET /api/v1/analytics/billing-cycle again to verify persistence
	reqBC2 := httptest.NewRequest(http.MethodGet, "/api/v1/analytics/billing-cycle", nil)
	reqBC2.AddCookie(sessionCookie)
	wBC2 := httptest.NewRecorder()
	handler.ServeHTTP(wBC2, reqBC2)
	if wBC2.Code != http.StatusOK {
		t.Fatalf("expected 200 for billing cycle, got %d", wBC2.Code)
	}
	var bcResp2 APIResponse
	_ = json.NewDecoder(wBC2.Body).Decode(&bcResp2)
	bcData2 := bcResp2.Data.(map[string]interface{})
	if int(bcData2["anchor_day"].(float64)) != 18 {
		t.Fatalf("expected persisted anchor_day 18, got %v", bcData2["anchor_day"])
	}
	if int(bcData2["anchor_hour"].(float64)) != 10 {
		t.Fatalf("expected persisted anchor_hour 10, got %v", bcData2["anchor_hour"])
	}
	if int(bcData2["anchor_minute"].(float64)) != 30 {
		t.Fatalf("expected persisted anchor_minute 30, got %v", bcData2["anchor_minute"])
	}

	// 4. GET /api/v1/analytics/traffic?preset=7d
	reqTraffic := httptest.NewRequest(http.MethodGet, "/api/v1/analytics/traffic?preset=7d", nil)
	reqTraffic.AddCookie(sessionCookie)
	wTraffic := httptest.NewRecorder()
	handler.ServeHTTP(wTraffic, reqTraffic)
	if wTraffic.Code != http.StatusOK {
		t.Fatalf("expected 200 for traffic analytics, got %d: %s", wTraffic.Code, wTraffic.Body.String())
	}
	var tResp APIResponse
	if err := json.NewDecoder(wTraffic.Body).Decode(&tResp); err != nil {
		t.Fatalf("failed to decode traffic response: %v", err)
	}
	tData, ok := tResp.Data.(map[string]interface{})
	if !ok {
		t.Fatalf("expected traffic data map, got %T", tResp.Data)
	}
	timeline, ok := tData["timeline"].([]interface{})
	if !ok || len(timeline) != 7 {
		t.Fatalf("expected 7 days in timeline, got %d (ok: %v)", len(timeline), ok)
	}
	if int(tData["billing_anchor_day"].(float64)) != 18 {
		t.Fatalf("expected billing_anchor_day 18 in traffic analytics, got %v", tData["billing_anchor_day"])
	}
}

func TestMetricsEndpoints(t *testing.T) {
	handler, database, _ := setupTestServer(t)
	defer database.Close()

	// 1. Obtain session
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

	// 2. Insert test system_metrics and interface_metrics
	_, _ = database.SqlDB.Exec(`
		INSERT INTO routers (id, name, host, is_default, is_active)
		VALUES (1, 'MainRouter', '127.0.0.1', 1, 1)
	`)
	_, _ = database.SqlDB.Exec(`
		INSERT INTO system_metrics (router_id, cpu_load, memory_used_bytes, memory_total_bytes, memory_usage_pct, temperature, voltage, timestamp)
		VALUES (1, 12.5, 524288000, 1073741824, 48.8, 42.0, 24.1, datetime('now', '-5 minutes'))
	`)
	_, _ = database.SqlDB.Exec(`
		INSERT INTO interface_metrics (router_id, interface_name, rx_rate_bps, tx_rate_bps, rx_bytes_total, tx_bytes_total, timestamp)
		VALUES (1, 'ether1', 2500000.0, 1500000.0, 1000000, 500000, datetime('now', '-5 minutes'))
	`)

	// 3. GET /api/v1/metrics/system
	reqSys := httptest.NewRequest(http.MethodGet, "/api/v1/metrics/system?range=1h&router_id=1", nil)
	reqSys.AddCookie(sessionCookie)
	wSys := httptest.NewRecorder()
	handler.ServeHTTP(wSys, reqSys)
	if wSys.Code != http.StatusOK {
		t.Fatalf("expected 200 for system metrics, got %d: %s", wSys.Code, wSys.Body.String())
	}
	var sysResp APIResponse
	if err := json.NewDecoder(wSys.Body).Decode(&sysResp); err != nil {
		t.Fatalf("failed to decode system metrics: %v", err)
	}
	sysData := sysResp.Data.(map[string]interface{})
	if sysData["range"] != "1h" {
		t.Fatalf("expected range 1h, got %v", sysData["range"])
	}
	pts, ok := sysData["points"].([]interface{})
	if !ok || len(pts) == 0 {
		t.Fatalf("expected points in system metrics, got %v", sysData["points"])
	}
	if sysData["current_cpu"] == nil {
		t.Fatalf("expected current_cpu to be populated")
	}

	// 4. GET /api/v1/metrics/interfaces
	reqIface := httptest.NewRequest(http.MethodGet, "/api/v1/metrics/interfaces?range=1h&interfaces=ether1&router_id=1", nil)
	reqIface.AddCookie(sessionCookie)
	wIface := httptest.NewRecorder()
	handler.ServeHTTP(wIface, reqIface)
	if wIface.Code != http.StatusOK {
		t.Fatalf("expected 200 for interface metrics, got %d: %s", wIface.Code, wIface.Body.String())
	}
	var ifaceResp APIResponse
	if err := json.NewDecoder(wIface.Body).Decode(&ifaceResp); err != nil {
		t.Fatalf("failed to decode interface metrics: %v", err)
	}
	ifaceData := ifaceResp.Data.(map[string]interface{})
	if pts, ok := ifaceData["points"].([]interface{}); !ok || len(pts) == 0 {
		t.Fatalf("expected points in interface metrics, got %v", ifaceData["points"])
	}
	if ifaceData["current_rx_bps"].(float64) <= 0 {
		t.Fatalf("expected current_rx_bps > 0, got %v", ifaceData["current_rx_bps"])
	}

	// 5. GET /api/v1/metrics/interfaces with empty interfaces param -> empty points
	reqEmpty := httptest.NewRequest(http.MethodGet, "/api/v1/metrics/interfaces?range=1h&interfaces=&router_id=1", nil)
	reqEmpty.AddCookie(sessionCookie)
	wEmpty := httptest.NewRecorder()
	handler.ServeHTTP(wEmpty, reqEmpty)
	if wEmpty.Code != http.StatusOK {
		t.Fatalf("expected 200 for empty interface metrics, got %d", wEmpty.Code)
	}
	var emptyResp APIResponse
	_ = json.NewDecoder(wEmpty.Body).Decode(&emptyResp)
	emptyData := emptyResp.Data.(map[string]interface{})
	if pts, ok := emptyData["points"].([]interface{}); !ok || len(pts) != 0 {
		t.Fatalf("expected 0 points when interfaces is empty, got %d", len(pts))
	}
}

func TestConnectionsEndpoints(t *testing.T) {
	mockMux := http.NewServeMux()
	mockMux.HandleFunc("/rest/ip/firewall/connection", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode([]routeros.FirewallConnection{
			{
				ID:         "*1",
				Protocol:   "tcp",
				SrcAddress: "192.0.2.100:54321",
				DstAddress: "198.51.100.1:443",
				OrigBytes:  500,
				ReplBytes:  1200,
				OrigRate:   100,
				ReplRate:   200,
				TCPState:   "established",
			},
		})
	})
	mockMux.HandleFunc("/rest/ip/firewall/connection/*1", func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodDelete {
			w.WriteHeader(http.StatusOK)
		}
	})
	mockServer := httptest.NewServer(mockMux)
	defer mockServer.Close()

	u, _ := url.Parse(mockServer.URL)
	port, _ := strconv.Atoi(u.Port())
	mockClient, err := routeros.NewClient(routeros.Config{
		Host:    u.Hostname(),
		Port:    port,
		Timeout: 2 * time.Second,
	})
	if err != nil {
		t.Fatalf("failed to create routeros client: %v", err)
	}

	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test.db")
	fernet, _ := crypto.NewFernet("cw_z4pYJ2-8_9V18R5v6R1XbJ9i9w9G1R1XbJ9i9w9E=")
	database, _ := db.Open(dbPath, fernet)
	defer database.Close()

	cfg := &config.Config{
		AppVersion:    "0.3.4-test",
		AdminPassword: "SecretAdminPassword123",
		AuthEnabled:   true,
	}

	handler := NewRouter(RouterConfig{
		Config: cfg,
		DB:     database,
		Fernet: fernet,
		Client: mockClient,
		Hub:    NewHub(),
	})

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

	// 1. GET /api/v1/connections
	reqConns := httptest.NewRequest(http.MethodGet, "/api/v1/connections", nil)
	reqConns.AddCookie(sessionCookie)
	wConns := httptest.NewRecorder()
	handler.ServeHTTP(wConns, reqConns)
	if wConns.Code != http.StatusOK {
		t.Fatalf("expected 200 for connections, got %d: %s", wConns.Code, wConns.Body.String())
	}
	var connResp APIResponse
	if err := json.NewDecoder(wConns.Body).Decode(&connResp); err != nil {
		t.Fatalf("failed to decode connections: %v", err)
	}
	connData := connResp.Data.(map[string]interface{})
	items := connData["items"].([]interface{})
	if len(items) != 1 {
		t.Fatalf("expected 1 connection item, got %d", len(items))
	}
	item0 := items[0].(map[string]interface{})
	if item0["id"] != "*1" || item0["protocol"] != "tcp" {
		t.Fatalf("unexpected connection item: %v", item0)
	}

	// 2. POST /api/v1/connections/*1/kill
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

	reqKill := httptest.NewRequest(http.MethodPost, "/api/v1/connections/*1/kill", bytes.NewBufferString(`{}`))
	reqKill.AddCookie(sessionCookie)
	if csrfCookie != nil {
		reqKill.AddCookie(csrfCookie)
		reqKill.Header.Set(CSRFHeader, csrfCookie.Value)
	}
	wKill := httptest.NewRecorder()
	handler.ServeHTTP(wKill, reqKill)
	if wKill.Code != http.StatusOK {
		t.Fatalf("expected 200 for kill connection, got %d: %s", wKill.Code, wKill.Body.String())
	}
}

func TestHubRouterScoping(t *testing.T) {
	hub := NewHub()
	server := httptest.NewServer(http.HandlerFunc(hub.HandleWS))
	defer server.Close()

	wsURL := "ws" + strings.TrimPrefix(server.URL, "http")

	// Connect client 1 for router 1
	c1, _, err := websocket.DefaultDialer.Dial(wsURL+"?router_id=1", nil)
	if err != nil {
		t.Fatalf("failed to connect ws client 1: %v", err)
	}
	defer c1.Close()

	var initMsg1 map[string]interface{}
	_ = c1.ReadJSON(&initMsg1)
	if initMsg1["type"] != "connected" {
		t.Fatalf("expected connected event on c1, got %v", initMsg1)
	}

	// Connect client 2 for router 2
	c2, _, err := websocket.DefaultDialer.Dial(wsURL+"?router_id=2", nil)
	if err != nil {
		t.Fatalf("failed to connect ws client 2: %v", err)
	}
	defer c2.Close()
	var initMsg2 map[string]interface{}
	_ = c2.ReadJSON(&initMsg2)

	// Broadcast for router 1 (isDefault: true)
	hub.BroadcastRouter(1, true, map[string]interface{}{
		"type":      "telemetry_tick",
		"router_id": 1,
		"bps":       5000,
	})

	// Client 1 MUST receive it
	var tick1 map[string]interface{}
	_ = c1.SetReadDeadline(time.Now().Add(2 * time.Second))
	if err := c1.ReadJSON(&tick1); err != nil {
		t.Fatalf("c1 failed to receive router 1 frame: %v", err)
	}
	if int(tick1["router_id"].(float64)) != 1 {
		t.Fatalf("expected router_id 1 on c1, got %v", tick1["router_id"])
	}

	// Client 2 MUST NOT receive router 1 frame
	_ = c2.SetReadDeadline(time.Now().Add(200 * time.Millisecond))
	var leakMsg map[string]interface{}
	if err := c2.ReadJSON(&leakMsg); err == nil {
		t.Fatalf("c2 leaked message from router 1: %v", leakMsg)
	}

	// Client 4 connects for router 1 -> MUST immediately receive the cached frame!
	c4, _, err := websocket.DefaultDialer.Dial(wsURL+"?router_id=1", nil)
	if err != nil {
		t.Fatalf("failed to connect c4: %v", err)
	}
	defer c4.Close()

	var initMsg4 map[string]interface{}
	_ = c4.ReadJSON(&initMsg4)
	if initMsg4["type"] != "connected" {
		t.Fatalf("expected connected on c4, got %v", initMsg4)
	}

	var replayMsg map[string]interface{}
	_ = c4.SetReadDeadline(time.Now().Add(2 * time.Second))
	if err := c4.ReadJSON(&replayMsg); err != nil {
		t.Fatalf("c4 failed to receive immediate replay: %v", err)
	}
	if replayMsg["type"] != "telemetry_tick" || int(replayMsg["router_id"].(float64)) != 1 {
		t.Fatalf("c4 received unexpected replay: %v", replayMsg)
	}
}

package services

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/masseselsev/mikroman/internal/crypto"
	"github.com/masseselsev/mikroman/internal/db"
)

type mockQueueReconciler struct {
	reconciledCount int32
	lastRouterID    int32
}

func (m *mockQueueReconciler) ReconcileQueues(ctx context.Context, routerID int) error {
	atomic.AddInt32(&m.reconciledCount, 1)
	atomic.StoreInt32(&m.lastRouterID, int32(routerID))
	return nil
}

func TestTelegramBot_MultiRouterIsolationAndDeviceMapping(t *testing.T) {
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test_telegram.db")

	fernet, _ := crypto.NewFernet("cw_z4pYJ2-8_9V18R5v6R1XbJ9i9w9G1R1XbJ9i9w9E=")
	database, err := db.Open(dbPath, fernet)
	if err != nil {
		t.Fatalf("failed to open database: %v", err)
	}
	defer database.Close()

	// 1. Setup routers (Router 1 = Main, Router 2 = Branch)
	_, _ = database.SqlDB.Exec("INSERT INTO routers (id, name, host, is_default, is_active) VALUES (1, 'MainRouter', '192.0.2.1', 1, 1)")
	_, _ = database.SqlDB.Exec("INSERT INTO routers (id, name, host, is_default, is_active) VALUES (2, 'BranchRouter', '192.0.2.2', 0, 1)")

	// 2. Setup users for Router 1 and Router 2
	_, _ = database.SqlDB.Exec("INSERT INTO users (id, router_id, name, speed_limit, is_paused) VALUES (1, 1, 'Mark', 'unlimited', 0)")
	_, _ = database.SqlDB.Exec("INSERT INTO users (id, router_id, name, speed_limit, is_paused) VALUES (2, 1, 'Kristina', '50M', 0)")
	_, _ = database.SqlDB.Exec("INSERT INTO users (id, router_id, name, speed_limit, is_paused) VALUES (3, 2, 'BranchAdmin', 'unlimited', 0)")

	// 3. Setup devices for Router 1 (Mark has 2 devices, 1 unassigned device)
	_, _ = database.SqlDB.Exec(`INSERT INTO devices (id, router_id, user_id, mac_address, ip_address, custom_name, is_active, is_deleted)
		VALUES (1, 1, 1, 'AA:BB:CC:00:00:01', '192.0.2.10', 'MacBook Pro', 1, 0)`)
	_, _ = database.SqlDB.Exec(`INSERT INTO devices (id, router_id, user_id, mac_address, ip_address, custom_name, is_active, is_deleted)
		VALUES (2, 1, 1, 'AA:BB:CC:00:00:02', '192.0.2.11', 'iPhone 15', 0, 0)`)
	_, _ = database.SqlDB.Exec(`INSERT INTO devices (id, router_id, user_id, mac_address, ip_address, custom_name, is_active, is_deleted)
		VALUES (3, 1, NULL, 'AA:BB:CC:00:00:03', '192.0.2.99', 'Smart Plug', 1, 0)`)

	// 4. Capture Telegram API calls
	var mu sync.Mutex
	var sentMessages []map[string]interface{}
	var editedMessages []map[string]interface{}
	var answeredCallbacks []map[string]interface{}

	tgServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()

		var body map[string]interface{}
		_ = json.NewDecoder(r.Body).Decode(&body)

		if strings.Contains(r.URL.Path, "sendMessage") {
			sentMessages = append(sentMessages, body)
		} else if strings.Contains(r.URL.Path, "editMessageText") {
			editedMessages = append(editedMessages, body)
		} else if strings.Contains(r.URL.Path, "answerCallbackQuery") {
			answeredCallbacks = append(answeredCallbacks, body)
		}

		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]interface{}{"ok": true, "result": true})
	}))
	defer tgServer.Close()

	// Initialize bot service with mock Telegram client
	reconciler := &mockQueueReconciler{}
	svc := NewTelegramBotService(database, nil, reconciler)
	svc.token = "TEST_TOKEN"
	svc.adminIDs = []int64{12345}

	// Override Telegram API URL helper by using test server client
	svc.httpClient = tgServer.Client()
	// Replace URL prefixes in helper by monkey-patching or testing handlers directly
	// To test Telegram HTTP calls via tgServer, we can redirect through a custom Transport:
	tgTransport := &testServerTransport{targetURL: tgServer.URL}
	svc.httpClient.Transport = tgTransport

	chatID := int64(12345)

	// --- TEST 1: Send Users on Router 1 ---
	svc.sendUsers(chatID, 0)

	mu.Lock()
	if len(sentMessages) == 0 {
		mu.Unlock()
		t.Fatalf("expected sent message for sendUsers, got none")
	}
	lastMsg := sentMessages[len(sentMessages)-1]
	text := lastMsg["text"].(string)
	mu.Unlock()

	// Must contain Router 1 users and NOT Router 2 user
	if !strings.Contains(text, "Mark") {
		t.Errorf("expected text to contain 'Mark', got: %s", text)
	}
	if !strings.Contains(text, "Kristina") {
		t.Errorf("expected text to contain 'Kristina', got: %s", text)
	}
	if strings.Contains(text, "BranchAdmin") {
		t.Errorf("expected text NOT to contain 'BranchAdmin' (Router 2 user was mixed in!), got: %s", text)
	}

	// Must show Mark has 2 devices and Kristina has 0 devices (not 0 for all!)
	if !strings.Contains(text, "Devices: <b>2</b>") {
		t.Errorf("expected text to show 2 devices for Mark, got: %s", text)
	}

	// --- TEST 2: User Detail View ---
	svc.sendUserDetail(chatID, 0, 1)

	mu.Lock()
	lastMsg = sentMessages[len(sentMessages)-1]
	text = lastMsg["text"].(string)
	mu.Unlock()

	if !strings.Contains(text, "User: Mark") {
		t.Errorf("expected User Detail to show 'User: Mark', got: %s", text)
	}
	if !strings.Contains(text, "MacBook Pro") || !strings.Contains(text, "192.0.2.10") {
		t.Errorf("expected User Detail to list MacBook Pro device, got: %s", text)
	}
	if !strings.Contains(text, "iPhone 15") || !strings.Contains(text, "192.0.2.11") {
		t.Errorf("expected User Detail to list iPhone 15 device, got: %s", text)
	}

	// --- TEST 3: User Pause triggers Queue Reconciliation ---
	queryPause := &TelegramCallbackQuery{
		ID:   "cb1",
		From: TelegramUser{ID: chatID},
		Data: "user:pause:1",
	}
	svc.handleCallbackQuery(queryPause)

	// Verify DB updated
	u, _ := database.GetUser(1)
	if !u.IsPaused {
		t.Errorf("expected user Mark to be paused in DB, got IsPaused=false")
	}

	// Verify reconciler was triggered
	time.Sleep(50 * time.Millisecond) // Allow async goroutine to execute
	if atomic.LoadInt32(&reconciler.reconciledCount) == 0 {
		t.Errorf("expected queue reconciler to be triggered on user pause")
	}
	if atomic.LoadInt32(&reconciler.lastRouterID) != 1 {
		t.Errorf("expected reconciler to run for routerID 1, got %d", reconciler.lastRouterID)
	}

	// --- TEST 4: User Speed Limit Preset ---
	queryLimit := &TelegramCallbackQuery{
		ID:   "cb2",
		From: TelegramUser{ID: chatID},
		Data: "user:limit:1:20M",
	}
	svc.handleCallbackQuery(queryLimit)

	u, _ = database.GetUser(1)
	if u.SpeedLimit != "20M" {
		t.Errorf("expected user speed limit to be 20M, got %s", u.SpeedLimit)
	}

	// --- TEST 5: Devices view shows unassigned and assigned devices ---
	svc.sendDevices(chatID, 0)
	mu.Lock()
	lastMsg = sentMessages[len(sentMessages)-1]
	text = lastMsg["text"].(string)
	mu.Unlock()

	if !strings.Contains(text, "Unassigned Devices") || !strings.Contains(text, "Smart Plug") {
		t.Errorf("expected Devices view to list Unassigned 'Smart Plug', got: %s", text)
	}
	if !strings.Contains(text, "MacBook Pro → <b>Mark</b>") {
		t.Errorf("expected Devices view to show MacBook Pro assigned to Mark, got: %s", text)
	}

	// --- TEST 6: Switch Router context to Router 2 ---
	querySwitch := &TelegramCallbackQuery{
		ID:   "cb3",
		From: TelegramUser{ID: chatID},
		Data: "router:select:2",
	}
	svc.handleCallbackQuery(querySwitch)

	activeRtr, err := svc.getActiveRouter(chatID)
	if err != nil || activeRtr.ID != 2 {
		t.Fatalf("expected active router to be 2, got %v", activeRtr)
	}

	// After switching to Router 2, sendUsers must now show BranchAdmin and NOT Mark
	svc.sendUsers(chatID, 0)
	mu.Lock()
	lastMsg = sentMessages[len(sentMessages)-1]
	text = lastMsg["text"].(string)
	mu.Unlock()

	if !strings.Contains(text, "BranchAdmin") {
		t.Errorf("expected text to contain 'BranchAdmin' for Router 2, got: %s", text)
	}
	if strings.Contains(text, "Mark") {
		t.Errorf("expected text NOT to contain 'Mark' after switching to Router 2, got: %s", text)
	}

	// --- TEST 7: Russian Localization ---
	svc.lang = "ru"
	svc.sendUsers(chatID, 0)
	mu.Lock()
	lastMsg = sentMessages[len(sentMessages)-1]
	text = lastMsg["text"].(string)
	mu.Unlock()

	if !strings.Contains(text, "Профили пользователей") {
		t.Errorf("expected Russian heading 'Профили пользователей', got: %s", text)
	}
	if !strings.Contains(text, "Устройств:") {
		t.Errorf("expected Russian 'Устройств:', got: %s", text)
	}

	// --- TEST 8: Reboot Cancel returns to Main Menu ---
	queryCancel := &TelegramCallbackQuery{
		ID:   "cb4",
		From: TelegramUser{ID: chatID},
		Data: "reboot:cancel",
	}
	svc.handleCallbackQuery(queryCancel)

	mu.Lock()
	lastMsg = sentMessages[len(sentMessages)-1]
	text = lastMsg["text"].(string)
	mu.Unlock()

	if !strings.Contains(text, "MikroMan") {
		t.Errorf("expected cancel reboot to return to Main Menu, got: %s", text)
	}
}

// testServerTransport redirects Telegram API calls to the local httptest server
type testServerTransport struct {
	targetURL string
}

func (t *testServerTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	// Rewrite scheme and host to target httptest server
	serverURL, _ := http.NewRequest(http.MethodGet, t.targetURL, nil)
	req.URL.Scheme = serverURL.URL.Scheme
	req.URL.Host = serverURL.URL.Host
	return http.DefaultTransport.RoundTrip(req)
}


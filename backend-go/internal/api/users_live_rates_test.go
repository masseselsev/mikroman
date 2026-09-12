package api

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strconv"
	"testing"

	"github.com/go-chi/chi/v5"

	"github.com/masseselsev/mikroman/internal/crypto"
	"github.com/masseselsev/mikroman/internal/db"
)

type mockLiveRatesProvider struct {
	userRates map[int][2]int64
	devRates  map[int][2]int64
}

func (m *mockLiveRatesProvider) GetLatestRates(routerID int) (map[int][2]int64, map[int][2]int64) {
	return m.userRates, m.devRates
}

func TestUserHandler_LiveRatesEnrichment(t *testing.T) {
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test_users_rates.db")

	fernet, err := crypto.NewFernet("cw_z4pYJ2-8_9V18R5v6R1XbJ9i9w9G1R1XbJ9i9w9E=")
	if err != nil {
		t.Fatalf("failed to create Fernet: %v", err)
	}

	database, err := db.Open(dbPath, fernet)
	if err != nil {
		t.Fatalf("failed to open database: %v", err)
	}
	defer database.Close()

	// Create user
	user := &db.User{
		Name:       "Test User",
		AvatarIcon: "user",
		SpeedLimit: "10M/10M",
	}
	if err := database.CreateUser(user); err != nil {
		t.Fatalf("failed to create user: %v", err)
	}

	// Create device linked to user
	res, err := database.SqlDB.Exec(`
		INSERT INTO devices (user_id, mac_address, ip_address, custom_name, is_active, is_deleted)
		VALUES (?, '00:11:22:33:44:55', '192.0.2.10', 'Test Laptop', 1, 0)
	`, user.ID)
	if err != nil {
		t.Fatalf("failed to insert device: %v", err)
	}
	devID, _ := res.LastInsertId()

	mockProvider := &mockLiveRatesProvider{
		userRates: map[int][2]int64{
			user.ID: {1500000, 300000},
		},
		devRates: map[int][2]int64{
			int(devID): {1500000, 300000},
		},
	}

	handler := NewUserHandler(database, mockProvider, nil)

	// Test List
	t.Run("List enriches live rates", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/api/v1/users", nil)
		w := httptest.NewRecorder()
		handler.List(w, req)

		if w.Code != http.StatusOK {
			t.Fatalf("expected status 200, got %d", w.Code)
		}

		var resp struct {
			Success bool      `json:"success"`
			Data    []db.User `json:"data"`
		}
		if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
			t.Fatalf("failed to decode response: %v", err)
		}

		if len(resp.Data) != 1 {
			t.Fatalf("expected 1 user, got %d", len(resp.Data))
		}

		u := resp.Data[0]
		if u.CurrentRateIn != 1500000 || u.CurrentRateOut != 300000 {
			t.Errorf("expected user rates (1500000, 300000), got (%d, %d)", u.CurrentRateIn, u.CurrentRateOut)
		}

		if len(u.Devices) != 1 {
			t.Fatalf("expected 1 device, got %d", len(u.Devices))
		}

		d := u.Devices[0]
		if d.CurrentRateIn != 1500000 || d.CurrentRateOut != 300000 {
			t.Errorf("expected device rates (1500000, 300000), got (%d, %d)", d.CurrentRateIn, d.CurrentRateOut)
		}
	})

	// Test Get
	t.Run("Get enriches live rates", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, fmt.Sprintf("/api/v1/users/%d", user.ID), nil)
		rctx := chi.NewRouteContext()
		rctx.URLParams.Add("id", strconv.Itoa(user.ID))
		req = req.WithContext(context.WithValue(req.Context(), chi.RouteCtxKey, rctx))

		w := httptest.NewRecorder()
		handler.Get(w, req)

		if w.Code != http.StatusOK {
			t.Fatalf("expected status 200, got %d", w.Code)
		}

		var resp struct {
			Success bool    `json:"success"`
			Data    db.User `json:"data"`
		}
		if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
			t.Fatalf("failed to decode response: %v", err)
		}

		u := resp.Data
		if u.CurrentRateIn != 1500000 || u.CurrentRateOut != 300000 {
			t.Errorf("expected user rates (1500000, 300000), got (%d, %d)", u.CurrentRateIn, u.CurrentRateOut)
		}

		if len(u.Devices) != 1 {
			t.Fatalf("expected 1 device, got %d", len(u.Devices))
		}

		d := u.Devices[0]
		if d.CurrentRateIn != 1500000 || d.CurrentRateOut != 300000 {
			t.Errorf("expected device rates (1500000, 300000), got (%d, %d)", d.CurrentRateIn, d.CurrentRateOut)
		}
	})
}


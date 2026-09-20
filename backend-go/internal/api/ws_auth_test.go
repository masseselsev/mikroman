package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	"github.com/masseselsev/mikroman/internal/config"
	"github.com/masseselsev/mikroman/internal/crypto"
)

func TestWS_UnauthenticatedClosedWith1008(t *testing.T) {
	cfg := &config.Config{AuthEnabled: true}
	fernet, err := crypto.NewFernet("cw_z4pYJ2-8_9V18R5v6R1XbJ9i9w9G1R1XbJ9i9w9E=")
	if err != nil {
		t.Fatalf("failed to create fernet: %v", err)
	}

	hub := NewHub()
	hub.AttachAuth(cfg, fernet)

	server := httptest.NewServer(http.HandlerFunc(hub.HandleWS))
	defer server.Close()

	wsURL := "ws" + strings.TrimPrefix(server.URL, "http")

	dialer := websocket.Dialer{}
	conn, _, err := dialer.Dial(wsURL, nil)
	if err != nil {
		// Some dialers error on handshake closure, which is acceptable
		return
	}
	defer conn.Close()

	// Read message or close frame
	_, _, err = conn.ReadMessage()
	if err == nil {
		t.Fatalf("expected error/close reading from unauthenticated connection, got nil")
	}

	closeErr, ok := err.(*websocket.CloseError)
	if !ok {
		t.Fatalf("expected websocket.CloseError, got %T: %v", err, err)
	}
	if closeErr.Code != websocket.ClosePolicyViolation {
		t.Fatalf("expected close code %d (ClosePolicyViolation), got %d", websocket.ClosePolicyViolation, closeErr.Code)
	}
}

func TestWS_AuthenticatedSessionCookie(t *testing.T) {
	cfg := &config.Config{AuthEnabled: true}
	fernet, err := crypto.NewFernet("cw_z4pYJ2-8_9V18R5v6R1XbJ9i9w9G1R1XbJ9i9w9E=")
	if err != nil {
		t.Fatalf("failed to create fernet: %v", err)
	}

	token, err := fernet.CreateSessionToken("admin", 1)
	if err != nil {
		t.Fatalf("failed to generate token: %v", err)
	}

	hub := NewHub()
	hub.AttachAuth(cfg, fernet)

	server := httptest.NewServer(http.HandlerFunc(hub.HandleWS))
	defer server.Close()

	wsURL := "ws" + strings.TrimPrefix(server.URL, "http")

	header := http.Header{}
	header.Add("Cookie", SessionCookie+"="+token)

	dialer := websocket.Dialer{}
	conn, resp, err := dialer.Dial(wsURL, header)
	if err != nil {
		t.Fatalf("failed to dial with valid cookie: %v", err)
	}
	defer conn.Close()
	defer resp.Body.Close()

	_ = conn.SetReadDeadline(time.Now().Add(2 * time.Second))
	_, msg, err := conn.ReadMessage()
	if err != nil {
		t.Fatalf("failed to read connected message: %v", err)
	}

	var payload map[string]interface{}
	if err := json.Unmarshal(msg, &payload); err != nil {
		t.Fatalf("failed to parse message: %v", err)
	}
	if payload["type"] != "connected" {
		t.Fatalf("expected payload type 'connected', got %v", payload["type"])
	}
}

func TestWS_AuthenticatedQueryToken(t *testing.T) {
	cfg := &config.Config{AuthEnabled: true}
	fernet, err := crypto.NewFernet("cw_z4pYJ2-8_9V18R5v6R1XbJ9i9w9G1R1XbJ9i9w9E=")
	if err != nil {
		t.Fatalf("failed to create fernet: %v", err)
	}

	token, err := fernet.CreateSessionToken("admin", 1)
	if err != nil {
		t.Fatalf("failed to generate token: %v", err)
	}

	hub := NewHub()
	hub.AttachAuth(cfg, fernet)

	server := httptest.NewServer(http.HandlerFunc(hub.HandleWS))
	defer server.Close()

	wsURL := "ws" + strings.TrimPrefix(server.URL, "http") + "?token=" + token

	dialer := websocket.Dialer{}
	conn, resp, err := dialer.Dial(wsURL, nil)
	if err != nil {
		t.Fatalf("failed to dial with valid query token: %v", err)
	}
	defer conn.Close()
	defer resp.Body.Close()

	_ = conn.SetReadDeadline(time.Now().Add(2 * time.Second))
	_, msg, err := conn.ReadMessage()
	if err != nil {
		t.Fatalf("failed to read connected message: %v", err)
	}

	var payload map[string]interface{}
	if err := json.Unmarshal(msg, &payload); err != nil {
		t.Fatalf("failed to parse message: %v", err)
	}
	if payload["type"] != "connected" {
		t.Fatalf("expected payload type 'connected', got %v", payload["type"])
	}
}

func TestWS_AuthDisabledAllowsAnyConnection(t *testing.T) {
	cfg := &config.Config{AuthEnabled: false}

	hub := NewHub()
	hub.AttachAuth(cfg, nil)

	server := httptest.NewServer(http.HandlerFunc(hub.HandleWS))
	defer server.Close()

	wsURL := "ws" + strings.TrimPrefix(server.URL, "http")

	dialer := websocket.Dialer{}
	conn, resp, err := dialer.Dial(wsURL, nil)
	if err != nil {
		t.Fatalf("failed to dial with auth disabled: %v", err)
	}
	defer conn.Close()
	defer resp.Body.Close()

	_ = conn.SetReadDeadline(time.Now().Add(2 * time.Second))
	_, msg, err := conn.ReadMessage()
	if err != nil {
		t.Fatalf("failed to read connected message: %v", err)
	}

	var payload map[string]interface{}
	if err := json.Unmarshal(msg, &payload); err != nil {
		t.Fatalf("failed to parse message: %v", err)
	}
	if payload["type"] != "connected" {
		t.Fatalf("expected payload type 'connected', got %v", payload["type"])
	}
}

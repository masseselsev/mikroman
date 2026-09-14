package routeros

import (
	"context"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"strings"
	"testing"
)

func TestParseBps(t *testing.T) {
	cases := []struct {
		input    string
		expected int64
		hasErr   bool
	}{
		{"0", 0, false},
		{"5M", 5_000_000, false},
		{"100k", 100_000, false},
		{"1G", 1_000_000_000, false},
		{"500", 500, false},
		{"invalid", 0, true},
	}

	for _, tc := range cases {
		got, err := ParseBps(tc.input)
		if tc.hasErr && err == nil {
			t.Errorf("ParseBps(%q) expected error, got nil", tc.input)
		}
		if !tc.hasErr && (err != nil || got != tc.expected) {
			t.Errorf("ParseBps(%q) = %d (err: %v); want %d", tc.input, got, err, tc.expected)
		}
	}
}

func TestParseRatePair(t *testing.T) {
	up, down, err := ParseRatePair("10M/20M")
	if err != nil || up != 10_000_000 || down != 20_000_000 {
		t.Fatalf("ParseRatePair('10M/20M') = (%d, %d, %v); want (10M, 20M, nil)", up, down, err)
	}

	up0, down0, err := ParseRatePair("0/0")
	if err != nil || up0 != 0 || down0 != 0 {
		t.Fatalf("ParseRatePair('0/0') = (%d, %d, %v); want (0, 0, nil)", up0, down0, err)
	}

	_, _, err = ParseRatePair("invalid_pair")
	if err == nil {
		t.Fatalf("ParseRatePair('invalid_pair') expected error, got nil")
	}
}

func TestGuardImmuneTargets(t *testing.T) {
	immunes := map[string]bool{
		"192.168.88.1":   true,
		"192.168.88.250": true,
		"172.17.0.1":     true,
		"172.17.0.2":     true,
	}

	// 1. Regular targets must be allowed
	if err := GuardImmuneTarget("192.168.88.45", immunes, "block"); err != nil {
		t.Errorf("expected 192.168.88.45 to be allowed, got %v", err)
	}
	if err := GuardImmuneTarget("192.168.88.45", immunes, "queue"); err != nil {
		t.Errorf("expected 192.168.88.45 to be allowed for queue, got %v", err)
	}

	// 2. Loopback refused
	if err := GuardImmuneTarget("127.0.0.1", immunes, "block"); err == nil || !strings.Contains(strings.ToLower(err.Error()), "loopback") {
		t.Errorf("expected loopback error, got %v", err)
	}

	// 3. Wildcard refused
	if err := GuardImmuneTarget("0.0.0.0/0", immunes, "queue"); err == nil || !strings.Contains(strings.ToLower(err.Error()), "wildcard") {
		t.Errorf("expected wildcard error, got %v", err)
	}

	// 4. Immune address refused
	if err := GuardImmuneTarget("192.168.88.1", immunes, "block"); err == nil || !strings.Contains(strings.ToLower(err.Error()), "protected") {
		t.Errorf("expected protected router target error, got %v", err)
	}

	// 5. Supernet covering immune address refused
	broadSubnets := []string{"172.17.0.0/24", "172.17.0.0/25", "172.16.0.0/12"}
	for _, broad := range broadSubnets {
		err := GuardImmuneTarget(broad, immunes, "queue")
		if err == nil || !strings.Contains(strings.ToLower(err.Error()), "supernet") {
			t.Errorf("expected supernet error for %s, got %v", broad, err)
		}
	}

	// 6. Subnet NOT covering immune address must be allowed
	if err := GuardImmuneTarget("10.0.0.0/8", immunes, "block"); err != nil {
		t.Errorf("expected 10.0.0.0/8 to be allowed, got %v", err)
	}

	// 7. Non-overlapping sibling address allowed
	if err := GuardImmuneTarget("172.17.0.5", immunes, "queue"); err != nil {
		t.Errorf("expected 172.17.0.5 to be allowed, got %v", err)
	}
}

func TestGuardForeignResources(t *testing.T) {
	// Managed resources pass
	if err := GuardForeignResource("mikroman:managed:user_1", "delete", "queue"); err != nil {
		t.Errorf("expected mikroman prefix to pass, got %v", err)
	}
	if err := GuardForeignResource("mikroman:paused:Alex", "delete", "address-list"); err != nil {
		t.Errorf("expected mikroman prefix to pass, got %v", err)
	}

	// Foreign resources refused
	if err := GuardForeignResource("Admin Winbox rule", "delete", "queue"); err == nil || !strings.Contains(strings.ToLower(err.Error()), "foreign") {
		t.Errorf("expected foreign resource error, got %v", err)
	}
}

func TestGuardQueueInvariants(t *testing.T) {
	// Valid queue
	if err := GuardQueueInvariants("192.168.88.45", "10M/20M", "2M/5M", "none", "dev_1"); err != nil {
		t.Errorf("expected valid queue to pass, got %v", err)
	}
	if err := GuardQueueInvariants("192.168.88.45", "0/0", "0/0", "", ""); err != nil {
		t.Errorf("expected 0/0 queue to pass, got %v", err)
	}

	// Invalid rate format
	if err := GuardQueueInvariants("192.168.88.45", "invalid_rate", "", "", ""); err == nil || !strings.Contains(strings.ToLower(err.Error()), "format") {
		t.Errorf("expected invalid format error, got %v", err)
	}

	// limit_at exceeds max_limit
	if err := GuardQueueInvariants("192.168.88.45", "5M/5M", "10M/2M", "", ""); err == nil || !strings.Contains(strings.ToLower(err.Error()), "cannot exceed") {
		t.Errorf("expected limit_at exceeds max_limit error, got %v", err)
	}

	// Circular parent
	if err := GuardQueueInvariants("192.168.88.45", "10M/10M", "", "dev_1", "dev_1"); err == nil || !strings.Contains(strings.ToLower(err.Error()), "circular") {
		t.Errorf("expected circular parent error, got %v", err)
	}
}

func TestClientRefusesBlockingImmuneHost(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	u, _ := url.Parse(server.URL)
	port, _ := strconv.Atoi(u.Port())

	client, err := NewClient(Config{
		Host:      u.Hostname(),
		Port:      port,
		Username:  "admin",
		Password:  "x",
		UseSSL:    false,
		SSLVerify: false,
	})
	if err != nil {
		t.Fatalf("failed to create client: %v", err)
	}

	// Host itself is immune
	err = client.AddToAddressList(context.Background(), "mikroman_blocked", u.Hostname(), "test")
	if err == nil {
		t.Fatalf("expected client to refuse blocking immune host %s, got nil", u.Hostname())
	}

	// Wildcard is immune
	err = client.AddToAddressList(context.Background(), "mikroman_blocked", "0.0.0.0/0", "test")
	if err == nil {
		t.Fatalf("expected client to refuse blocking wildcard, got nil")
	}

	// Queue for immune host refused
	q := &SimpleQueue{
		Name:     "dev_q",
		Target:   u.Hostname(),
		MaxLimit: "10M/10M",
	}
	err = client.CreateSimpleQueue(context.Background(), q)
	if err == nil {
		t.Fatalf("expected client to refuse queueing immune host %s, got nil", u.Hostname())
	}
}


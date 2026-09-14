package services

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

func TestParseSemVer(t *testing.T) {
	tests := []struct {
		input     string
		wantMajor int
		wantMinor int
		wantPatch int
		wantOk    bool
	}{
		{"0.3.25", 0, 3, 25, true},
		{"v0.3.26", 0, 3, 26, true},
		{"1.0.0", 1, 0, 0, true},
		{"v2.10.3", 2, 10, 3, true},
		{"0.3.26-beta.1", 0, 3, 26, true},
		{"v0.3.26+build123", 0, 3, 26, true},
		{"invalid", 0, 0, 0, false},
		{"", 0, 0, 0, false},
		{"v1.x.y", 0, 0, 0, false},
	}

	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			major, minor, patch, ok := ParseSemVer(tt.input)
			if ok != tt.wantOk {
				t.Fatalf("ParseSemVer(%q) ok = %v, want %v", tt.input, ok, tt.wantOk)
			}
			if ok {
				if major != tt.wantMajor || minor != tt.wantMinor || patch != tt.wantPatch {
					t.Errorf("ParseSemVer(%q) = (%d, %d, %d), want (%d, %d, %d)",
						tt.input, major, minor, patch, tt.wantMajor, tt.wantMinor, tt.wantPatch)
				}
			}
		})
	}
}

func TestIsNewerVersion(t *testing.T) {
	tests := []struct {
		current   string
		candidate string
		want      bool
	}{
		{"0.3.25", "0.3.26", true},
		{"0.3.25", "v0.3.26", true},
		{"v0.3.25", "0.3.26", true},
		{"0.3.25", "0.4.0", true},
		{"0.3.25", "1.0.0", true},
		{"0.3.25", "0.3.25", false},
		{"0.3.26", "0.3.25", false},
		{"1.0.0", "0.9.9", false},
		{"0.3.25", "0.3.24", false},
		{"0.3.25", "invalid", false},
		{"invalid", "0.3.26", false},
	}

	for _, tt := range tests {
		t.Run(fmt.Sprintf("%s_vs_%s", tt.current, tt.candidate), func(t *testing.T) {
			got := IsNewerVersion(tt.current, tt.candidate)
			if got != tt.want {
				t.Errorf("IsNewerVersion(%q, %q) = %v, want %v", tt.current, tt.candidate, got, tt.want)
			}
		})
	}
}

func TestVersionChecker_Check(t *testing.T) {
	var requestCount int32

	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&requestCount, 1)

		if r.Header.Get("If-None-Match") == `"etag-123"` {
			w.WriteHeader(http.StatusNotModified)
			return
		}

		w.Header().Set("ETag", `"etag-123"`)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{
			"tag_name": "v0.3.26",
			"name": "MikroMan v0.3.26",
			"html_url": "https://github.com/masseselsev/mikroman/releases/tag/v0.3.26",
			"published_at": "2026-09-14T12:00:00Z"
		}`))
	}))
	defer ts.Close()

	checker := NewVersionChecker("0.3.25")
	checker.SetAPIURL(ts.URL)
	checker.cacheTTL = 1 * time.Hour

	// 1. Initial check: should query mock server
	info, err := checker.Check(context.Background(), false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !info.HasUpdate {
		t.Errorf("expected HasUpdate = true, got false")
	}
	if info.LatestVersion != "0.3.26" {
		t.Errorf("expected LatestVersion = 0.3.26, got %s", info.LatestVersion)
	}
	if atomic.LoadInt32(&requestCount) != 1 {
		t.Errorf("expected 1 request, got %d", atomic.LoadInt32(&requestCount))
	}

	// 2. Second check without force: should use in-memory cache without HTTP request
	info2, err := checker.Check(context.Background(), false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !info2.HasUpdate {
		t.Errorf("expected HasUpdate = true on cached check")
	}
	if atomic.LoadInt32(&requestCount) != 1 {
		t.Errorf("expected still 1 request due to cache, got %d", atomic.LoadInt32(&requestCount))
	}

	// 3. Force check: triggers HTTP request with If-None-Match header -> receives 304 Not Modified
	info3, err := checker.Check(context.Background(), true)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !info3.HasUpdate {
		t.Errorf("expected HasUpdate = true after 304")
	}
	if atomic.LoadInt32(&requestCount) != 2 {
		t.Errorf("expected 2 requests after force check, got %d", atomic.LoadInt32(&requestCount))
	}
}

func TestVersionChecker_FallbackOnError(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden) // Simulating rate limit
	}))
	defer ts.Close()

	checker := NewVersionChecker("0.3.25")
	checker.SetAPIURL(ts.URL)

	info, err := checker.Check(context.Background(), false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if info.HasUpdate {
		t.Errorf("expected HasUpdate = false on fallback")
	}
	if info.CurrentVersion != "0.3.25" {
		t.Errorf("expected CurrentVersion = 0.3.25, got %s", info.CurrentVersion)
	}
}

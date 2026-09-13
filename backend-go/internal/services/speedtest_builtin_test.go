package services

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestRunBuiltinSpeedTest_MockServer(t *testing.T) {
	// Create mock HTTP server simulating edge speedtest endpoints
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/trace":
			w.Header().Set("Content-Type", "text/plain")
			_, _ = w.Write([]byte("ip=192.0.2.1\nloc=UZ\ncolo=TAS\nwarp=off\n"))
		case "/down":
			w.Header().Set("Content-Type", "application/octet-stream")
			buf := make([]byte, 1024*64)
			for i := 0; i < 50; i++ {
				_, _ = w.Write(buf)
			}
		case "/up":
			w.WriteHeader(http.StatusOK)
			_, _ = io.Copy(io.Discard, r.Body)
		default:
			w.WriteHeader(http.StatusOK)
		}
	}))
	defer ts.Close()

	endpoints := &BuiltinSpeedTestEndpoints{
		ServerName: "Mock Server",
		ISP:        "Mock ISP",
		TraceURL:   ts.URL + "/trace",
		DownURL:    ts.URL + "/down",
		UpURL:      ts.URL + "/up",
	}

	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()

	reading, err := RunBuiltinSpeedTest(ctx, endpoints)
	if err != nil {
		t.Fatalf("expected no error, got %v", err)
	}

	if reading.Status != "ok" {
		t.Errorf("expected status 'ok', got %q", reading.Status)
	}
	if reading.PingMs == nil || *reading.PingMs <= 0 {
		t.Errorf("expected positive ping, got %v", reading.PingMs)
	}
	if reading.DownloadMbps == nil || *reading.DownloadMbps <= 0 {
		t.Errorf("expected positive download rate, got %v", reading.DownloadMbps)
	}
	if reading.UploadMbps == nil || *reading.UploadMbps <= 0 {
		t.Errorf("expected positive upload rate, got %v", reading.UploadMbps)
	}
	if reading.ServerName == nil || *reading.ServerName == "" {
		t.Errorf("expected non-empty server name, got %v", reading.ServerName)
	}
}

func TestRunBuiltinSpeedTest_ConcurrencyLock(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(200 * time.Millisecond)
		w.WriteHeader(http.StatusOK)
	}))
	defer ts.Close()

	endpoints := &BuiltinSpeedTestEndpoints{
		TraceURL: ts.URL,
		DownURL:  ts.URL,
		UpURL:    ts.URL,
	}

	ctx := context.Background()

	go func() {
		_, _ = RunBuiltinSpeedTest(ctx, endpoints)
	}()

	time.Sleep(30 * time.Millisecond)

	// Second concurrent call should fail due to lock
	_, err := RunBuiltinSpeedTest(ctx, endpoints)
	if err == nil {
		t.Errorf("expected error for concurrent speed test, got nil")
	}
}

func TestDiscoverSpeedTestEndpoints_Mock(t *testing.T) {
	var ts *httptest.Server
	ts = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/cli/config":
			w.Header().Set("Content-Type", "application/json")
			// Strip protocol to get host:port
			host := strings.TrimPrefix(ts.URL, "http://")
			respJSON := fmt.Sprintf(`{
				"app": {"ispName": "Mock Regional ISP"},
				"servers": [
					{
						"id": "1234",
						"host": "%s",
						"name": "Regional Node",
						"country": "Uzbekistan",
						"sponsor": "Mock Provider"
					}
				]
			}`, host)
			_, _ = w.Write([]byte(respJSON))
		case "/hello":
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte("hello 2.11\n"))
		default:
			w.WriteHeader(http.StatusOK)
		}
	}))
	defer ts.Close()

	origURL := ooklaConfigURL
	ooklaConfigURL = ts.URL + "/api/cli/config"
	defer func() { ooklaConfigURL = origURL }()

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	client := ts.Client()
	discovered := discoverSpeedTestEndpoints(ctx, client)
	if discovered == nil {
		t.Fatalf("expected discovery to succeed, got nil")
	}
	if !strings.Contains(discovered.ServerName, "Mock Provider") {
		t.Errorf("expected sponsor 'Mock Provider' in ServerName, got %q", discovered.ServerName)
	}
	if discovered.ISP != "Mock Regional ISP" {
		t.Errorf("expected ISP 'Mock Regional ISP', got %q", discovered.ISP)
	}
	if !strings.HasSuffix(discovered.DownURL, "/download") {
		t.Errorf("expected DownURL to end in /download, got %q", discovered.DownURL)
	}
}

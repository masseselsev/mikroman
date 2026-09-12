package services

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
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
			for i := 0; i < 100; i++ {
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
		TraceURL: ts.URL + "/trace",
		DownURL:  ts.URL + "/down",
		UpURL:    ts.URL + "/up",
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
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


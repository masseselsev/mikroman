package services

import (
	"strings"
	"testing"
)

var sampleOoklaOutput = `
   Speedtest by Ookla

      Server: Some ISP - Riga (id: 12345)
         ISP: Example Telecom
Idle Latency:     8.42 ms   (jitter: 0.51ms, low: 8.10ms, high: 9.30ms)
    Download:   482.17 Mbps (data used: 512.4 MB)
      Upload:    93.66 Mbps (data used: 101.2 MB)
 Packet Loss:     0.0%
  Result URL: https://www.speedtest.net/result/c/abc-123
`

var sampleOlderOutput = `
Testing download speed
    Download:   95.31 Mbps
      Upload:   19.02 Mbps
        Ping:   23.10 ms
`

func TestParseSpeedTestOutput(t *testing.T) {
	lines := strings.Split(strings.TrimSpace(sampleOoklaOutput), "\n")
	res := ParseSpeedTestOutput(lines)

	if res.Status != "ok" {
		t.Fatalf("expected status ok, got %s", res.Status)
	}
	if res.DownloadMbps == nil || *res.DownloadMbps != 482.17 {
		t.Fatalf("expected download 482.17, got %v", res.DownloadMbps)
	}
	if res.UploadMbps == nil || *res.UploadMbps != 93.66 {
		t.Fatalf("expected upload 93.66, got %v", res.UploadMbps)
	}
	if res.PingMs == nil || *res.PingMs != 8.42 {
		t.Fatalf("expected ping 8.42, got %v", res.PingMs)
	}
	if res.JitterMs == nil || *res.JitterMs != 0.51 {
		t.Fatalf("expected jitter 0.51, got %v", res.JitterMs)
	}
	if res.PacketLossPct == nil || *res.PacketLossPct != 0.0 {
		t.Fatalf("expected packet loss 0.0, got %v", res.PacketLossPct)
	}
	if res.ServerName == nil || *res.ServerName != "Some ISP - Riga (id: 12345)" {
		t.Fatalf("expected server name 'Some ISP - Riga (id: 12345)', got %v", res.ServerName)
	}
	if res.ISP == nil || *res.ISP != "Example Telecom" {
		t.Fatalf("expected isp 'Example Telecom', got %v", res.ISP)
	}
	if res.ResultURL == nil || *res.ResultURL != "https://www.speedtest.net/result/c/abc-123" {
		t.Fatalf("expected result url, got %v", res.ResultURL)
	}
}

func TestParseOlderSpeedTestOutput(t *testing.T) {
	lines := strings.Split(strings.TrimSpace(sampleOlderOutput), "\n")
	res := ParseSpeedTestOutput(lines)

	if res.Status != "ok" {
		t.Fatalf("expected status ok, got %s", res.Status)
	}
	if res.DownloadMbps == nil || *res.DownloadMbps != 95.31 {
		t.Fatalf("expected download 95.31, got %v", res.DownloadMbps)
	}
	if res.UploadMbps == nil || *res.UploadMbps != 19.02 {
		t.Fatalf("expected upload 19.02, got %v", res.UploadMbps)
	}
	if res.PingMs == nil || *res.PingMs != 23.10 {
		t.Fatalf("expected ping 23.10, got %v", res.PingMs)
	}
	if res.JitterMs != nil {
		t.Fatalf("expected nil jitter, got %v", res.JitterMs)
	}
}

func TestParseEmptySpeedTestOutput(t *testing.T) {
	res := ParseSpeedTestOutput([]string{"random startup noise", "nothing useful"})
	if res.Status != "failed" {
		t.Fatalf("expected failed status, got %s", res.Status)
	}
	if res.HasAnyFigure() {
		t.Fatalf("expected HasAnyFigure to be false")
	}
}

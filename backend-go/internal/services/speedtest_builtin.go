package services

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

var (
	builtinSpeedTestMu sync.Mutex
	isSpeedTestRunning bool
)

// BuiltinSpeedTestEndpoints configures the endpoints used by the in-process speedtest engine.
type BuiltinSpeedTestEndpoints struct {
	TraceURL string
	DownURL  string
	UpURL    string
}

var defaultSpeedTestEndpoints = BuiltinSpeedTestEndpoints{
	TraceURL: "https://speed.cloudflare.com/cdn-cgi/trace",
	DownURL:  "https://speed.cloudflare.com/__down",
	UpURL:    "https://speed.cloudflare.com/__up",
}

// RunBuiltinSpeedTest executes a pure-Go network throughput and latency test.
func RunBuiltinSpeedTest(ctx context.Context, endpoints *BuiltinSpeedTestEndpoints) (SpeedTestReading, error) {
	builtinSpeedTestMu.Lock()
	if isSpeedTestRunning {
		builtinSpeedTestMu.Unlock()
		errMsg := "Speed test is already in progress"
		return SpeedTestReading{Status: "failed", Error: &errMsg}, errors.New(errMsg)
	}
	isSpeedTestRunning = true
	builtinSpeedTestMu.Unlock()

	defer func() {
		builtinSpeedTestMu.Lock()
		isSpeedTestRunning = false
		builtinSpeedTestMu.Unlock()
	}()

	if endpoints == nil {
		endpoints = &defaultSpeedTestEndpoints
	}

	client := &http.Client{
		Timeout: 20 * time.Second,
	}

	reading := SpeedTestReading{
		Status: "ok",
	}
	var outputLog []string
	outputLog = append(outputLog, "Starting built-in on-router speed test...")

	// 1. Trace / Metadata probe
	isp, loc := probeTrace(ctx, client, endpoints.TraceURL)
	if isp != "" {
		reading.ISP = &isp
		outputLog = append(outputLog, "ISP: "+isp)
	}
	serverName := "Cloudflare Edge (" + loc + ")"
	if loc == "" {
		serverName = "Cloudflare Edge"
	}
	reading.ServerName = &serverName

	// 2. Latency & Jitter Probe (3 samples)
	pingMs, jitterMs := measureLatency(ctx, client, endpoints.TraceURL, 3)
	if pingMs > 0 {
		reading.PingMs = &pingMs
		reading.JitterMs = &jitterMs
		outputLog = append(outputLog, fmt.Sprintf("Latency: %.2f ms, Jitter: %.2f ms", pingMs, jitterMs))
	}

	// 3. Download Test
	downMbps, downErr := measureDownload(ctx, client, endpoints.DownURL)
	if downErr == nil && downMbps > 0 {
		reading.DownloadMbps = &downMbps
		outputLog = append(outputLog, fmt.Sprintf("Download: %.2f Mbps", downMbps))
	} else if downErr != nil {
		outputLog = append(outputLog, "Download error: "+downErr.Error())
	}

	// 4. Upload Test
	upMbps, upErr := measureUpload(ctx, client, endpoints.UpURL)
	if upErr == nil && upMbps > 0 {
		reading.UploadMbps = &upMbps
		outputLog = append(outputLog, fmt.Sprintf("Upload: %.2f Mbps", upMbps))
	} else if upErr != nil {
		outputLog = append(outputLog, "Upload error: "+upErr.Error())
	}

	zeroLoss := 0.0
	reading.PacketLossPct = &zeroLoss
	reading.RawOutput = strings.Join(outputLog, "\n")

	if !reading.HasAnyFigure() {
		reading.Status = "failed"
		errMsg := "Built-in speed test produced no measurements"
		reading.Error = &errMsg
	}

	return reading, nil
}

func probeTrace(ctx context.Context, client *http.Client, traceURL string) (string, string) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, traceURL, nil)
	if err != nil {
		return "", ""
	}
	resp, err := client.Do(req)
	if err != nil {
		return "", ""
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(io.LimitReader(resp.Body, 4096))
	if err != nil {
		return "", ""
	}

	var isp, loc string
	for _, line := range strings.Split(string(body), "\n") {
		parts := strings.SplitN(line, "=", 2)
		if len(parts) != 2 {
			continue
		}
		k, v := strings.TrimSpace(parts[0]), strings.TrimSpace(parts[1])
		switch k {
		case "loc":
			loc = v
		case "warp":
			if v == "on" {
				isp = "Cloudflare WARP"
			}
		case "colo":
			if loc == "" {
				loc = v
			}
		}
	}
	return isp, loc
}

func measureLatency(ctx context.Context, client *http.Client, url string, samples int) (float64, float64) {
	var rtts []float64
	for i := 0; i < samples; i++ {
		select {
		case <-ctx.Done():
			return 0, 0
		default:
		}
		start := time.Now()
		req, err := http.NewRequestWithContext(ctx, http.MethodHead, url, nil)
		if err != nil {
			req, _ = http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
		}
		resp, err := client.Do(req)
		if err == nil {
			_ = resp.Body.Close()
			rtts = append(rtts, float64(time.Since(start).Microseconds())/1000.0)
		}
		time.Sleep(50 * time.Millisecond)
	}

	if len(rtts) == 0 {
		return 0, 0
	}

	// Calculate minimum latency and average jitter
	minRTT := rtts[0]
	for _, r := range rtts {
		if r < minRTT {
			minRTT = r
		}
	}

	var jitterSum float64
	for i := 1; i < len(rtts); i++ {
		jitterSum += math.Abs(rtts[i] - rtts[i-1])
	}
	jitter := 0.0
	if len(rtts) > 1 {
		jitter = jitterSum / float64(len(rtts)-1)
	}

	return minRTT, jitter
}

func measureDownload(ctx context.Context, client *http.Client, downURL string) (float64, error) {
	targetURL := downURL
	if !strings.Contains(targetURL, "?") {
		targetURL += "?bytes=15000000" // ~15 MB stream per worker
	}

	workers := 3
	var totalBytes int64
	var wg sync.WaitGroup

	start := time.Now()
	testCtx, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()

	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			req, err := http.NewRequestWithContext(testCtx, http.MethodGet, targetURL, nil)
			if err != nil {
				return
			}
			resp, err := client.Do(req)
			if err != nil {
				return
			}
			defer resp.Body.Close()

			buf := make([]byte, 32*1024)
			for {
				n, err := resp.Body.Read(buf)
				if n > 0 {
					atomic.AddInt64(&totalBytes, int64(n))
				}
				if err != nil {
					break
				}
			}
		}()
	}

	wg.Wait()
	duration := time.Since(start).Seconds()
	if duration <= 0 || atomic.LoadInt64(&totalBytes) == 0 {
		return 0, fmt.Errorf("no download data received")
	}

	bytesReceived := atomic.LoadInt64(&totalBytes)
	mbps := (float64(bytesReceived) * 8.0) / (duration * 1_000_000.0)
	return math.Round(mbps*100) / 100, nil
}

func measureUpload(ctx context.Context, client *http.Client, upURL string) (float64, error) {
	workers := 2
	payloadSize := 2 * 1024 * 1024 // 2 MB per POST
	payload := bytes.Repeat([]byte{0x5A}, payloadSize)

	var totalBytes int64
	var wg sync.WaitGroup

	start := time.Now()
	testCtx, cancel := context.WithTimeout(ctx, 6*time.Second)
	defer cancel()

	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				select {
				case <-testCtx.Done():
					return
				default:
				}

				req, err := http.NewRequestWithContext(testCtx, http.MethodPost, upURL, bytes.NewReader(payload))
				if err != nil {
					return
				}
				req.Header.Set("Content-Type", "application/octet-stream")
				resp, err := client.Do(req)
				if err != nil {
					return
				}
				_, _ = io.Copy(io.Discard, resp.Body)
				_ = resp.Body.Close()
				atomic.AddInt64(&totalBytes, int64(payloadSize))
			}
		}()
	}

	wg.Wait()
	duration := time.Since(start).Seconds()
	if duration <= 0 || atomic.LoadInt64(&totalBytes) == 0 {
		return 0, fmt.Errorf("no upload data transmitted")
	}

	bytesSent := atomic.LoadInt64(&totalBytes)
	mbps := (float64(bytesSent) * 8.0) / (duration * 1_000_000.0)
	return math.Round(mbps*100) / 100, nil
}

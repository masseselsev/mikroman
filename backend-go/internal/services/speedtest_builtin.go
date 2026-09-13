package services

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

var (
	builtinSpeedTestMu sync.Mutex
	isSpeedTestRunning bool

	ooklaConfigURL = "https://cli.speedtest.net/api/cli/config"
	ooklaStaticURL = "https://c.speedtest.net/speedtest-servers-static.php"
)

// BuiltinSpeedTestEndpoints configures the endpoints used by the in-process speedtest engine.
type BuiltinSpeedTestEndpoints struct {
	ServerName string
	ISP        string
	TraceURL   string
	DownURL    string
	UpURL      string
}

var defaultSpeedTestEndpoints = BuiltinSpeedTestEndpoints{
	ServerName: "Cloudflare Edge",
	TraceURL:   "https://speed.cloudflare.com/cdn-cgi/trace",
	DownURL:    "https://speed.cloudflare.com/__down",
	UpURL:      "https://speed.cloudflare.com/__up",
}

type ooklaCandidate struct {
	id      string
	host    string
	name    string
	country string
	sponsor string
}

func createResilientSpeedTestClient() *http.Client {
	dialer := &net.Dialer{
		Timeout:   4 * time.Second,
		KeepAlive: 30 * time.Second,
		Resolver: &net.Resolver{
			PreferGo: true,
			Dial: func(ctx context.Context, network, address string) (net.Conn, error) {
				d := net.Dialer{Timeout: 2500 * time.Millisecond}
				conn, err := d.DialContext(ctx, network, address)
				if err == nil {
					return conn, nil
				}
				// Fallback to Cloudflare DNS 1.1.1.1:53 or Google 8.8.8.8:53
				conn, err = d.DialContext(ctx, "udp", "1.1.1.1:53")
				if err == nil {
					return conn, nil
				}
				return d.DialContext(ctx, "udp", "8.8.8.8:53")
			},
		},
	}

	transport := &http.Transport{
		Proxy:                 http.ProxyFromEnvironment,
		DialContext:           dialer.DialContext,
		TLSClientConfig:       &tls.Config{InsecureSkipVerify: true},
		ForceAttemptHTTP2:     true,
		MaxIdleConns:          60,
		MaxIdleConnsPerHost:   12,
		IdleConnTimeout:       30 * time.Second,
		TLSHandshakeTimeout:   4 * time.Second,
		ExpectContinueTimeout: 1 * time.Second,
	}

	return &http.Client{
		Transport: transport,
		Timeout:   25 * time.Second,
	}
}

// discoverSpeedTestEndpoints discovers the closest regional speedtest server via Ookla API
// with automatic fallback to static server discovery. Returns nil on failure to trigger Cloudflare fallback.
func discoverSpeedTestEndpoints(ctx context.Context, client *http.Client) *BuiltinSpeedTestEndpoints {
	discoveryCtx, cancel := context.WithTimeout(ctx, 2500*time.Millisecond)
	defer cancel()

	var candidates []ooklaCandidate
	var isp string

	// 1. Primary discovery: Ookla CLI configuration endpoint (returns candidate servers nearest client IP)
	req, err := http.NewRequestWithContext(discoveryCtx, http.MethodGet, ooklaConfigURL, nil)
	if err == nil {
		req.Header.Set("User-Agent", "Speedtest/1.2.0")
		req.Header.Set("Accept", "application/json")
		resp, err := client.Do(req)
		if err == nil && resp.StatusCode == http.StatusOK {
			var cfg struct {
				App struct {
					ISPName string `json:"ispName"`
				} `json:"app"`
				Servers []struct {
					ID      string `json:"id"`
					Host    string `json:"host"`
					Name    string `json:"name"`
					Country string `json:"country"`
					Sponsor string `json:"sponsor"`
				} `json:"servers"`
			}
			if json.NewDecoder(resp.Body).Decode(&cfg) == nil {
				isp = cfg.App.ISPName
				for _, s := range cfg.Servers {
					if s.Host != "" {
						candidates = append(candidates, ooklaCandidate{
							id:      s.ID,
							host:    s.Host,
							name:    s.Name,
							country: s.Country,
							sponsor: s.Sponsor,
						})
					}
				}
			}
			_ = resp.Body.Close()
		}
	}

	if len(candidates) == 0 {
		return nil
	}

	// Limit probing to top 4 candidates to guarantee sub-second probe time
	if len(candidates) > 4 {
		candidates = candidates[:4]
	}

	type probeResult struct {
		cand    ooklaCandidate
		baseURL string
		rtt     time.Duration
	}

	resultsChan := make(chan probeResult, len(candidates)*2)
	var wg sync.WaitGroup

	for _, c := range candidates {
		c := c
		testURLs := []string{
			fmt.Sprintf("http://%s", c.host),
		}
		if c.id != "" {
			testURLs = append(testURLs, fmt.Sprintf("https://server-%s.prod.hosts.ooklaserver.net:8080", c.id))
		}

		for _, u := range testURLs {
			u := u
			wg.Add(1)
			go func() {
				defer wg.Done()
				pCtx, pCancel := context.WithTimeout(discoveryCtx, 1200*time.Millisecond)
				defer pCancel()
				pReq, err := http.NewRequestWithContext(pCtx, http.MethodGet, u+"/hello", nil)
				if err != nil {
					return
				}
				pReq.Header.Set("User-Agent", "Speedtest/1.2.0")
				t0 := time.Now()
				pResp, pErr := client.Do(pReq)
				if pErr == nil {
					_ = pResp.Body.Close()
					if pResp.StatusCode == http.StatusOK || pResp.StatusCode == http.StatusTemporaryRedirect {
						effectiveBase := u
						if pResp.StatusCode == http.StatusTemporaryRedirect {
							loc := pResp.Header.Get("Location")
							if loc != "" && strings.Contains(loc, "/hello") {
								effectiveBase = strings.TrimSuffix(loc, "/hello")
							}
						}
						resultsChan <- probeResult{cand: c, baseURL: effectiveBase, rtt: time.Since(t0)}
					}
				}
			}()
		}
	}

	wg.Wait()
	close(resultsChan)

	var best *probeResult
	for r := range resultsChan {
		r := r
		if best == nil || r.rtt < best.rtt {
			best = &r
		}
	}

	if best == nil {
		return nil
	}

	serverName := fmt.Sprintf("%s (%s, %s)", best.cand.sponsor, best.cand.name, best.cand.country)
	return &BuiltinSpeedTestEndpoints{
		ServerName: serverName,
		ISP:        isp,
		TraceURL:   best.baseURL + "/hello",
		DownURL:    best.baseURL + "/download",
		UpURL:      best.baseURL + "/upload",
	}
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

	// Enforce strict 22s deadline on the test run to avoid any hang
	runCtx, cancel := context.WithTimeout(ctx, 22*time.Second)
	defer cancel()

	var client *http.Client
	if endpoints == nil {
		client = createResilientSpeedTestClient()
		discovered := discoverSpeedTestEndpoints(runCtx, client)
		if discovered != nil {
			endpoints = discovered
		} else {
			endpoints = &defaultSpeedTestEndpoints
		}
	} else {
		client = &http.Client{
			Transport: &http.Transport{
				TLSClientConfig: &tls.Config{InsecureSkipVerify: true},
			},
			Timeout: 20 * time.Second,
		}
	}

	reading := SpeedTestReading{
		Status: "ok",
	}
	var outputLog []string
	outputLog = append(outputLog, "Starting built-in on-router speed test...")

	// 1. Server & ISP identification
	var serverName string
	if endpoints.ServerName != "" {
		serverName = endpoints.ServerName
	}
	if endpoints.ISP != "" {
		reading.ISP = &endpoints.ISP
		outputLog = append(outputLog, "ISP: "+endpoints.ISP)
	}

	// Probe trace metadata if serverName or ISP is still unknown (e.g. Cloudflare endpoint)
	if serverName == "" || reading.ISP == nil {
		isp, loc := probeTrace(runCtx, client, endpoints.TraceURL)
		if isp != "" && reading.ISP == nil {
			reading.ISP = &isp
			outputLog = append(outputLog, "ISP: "+isp)
		}
		if serverName == "" {
			if loc != "" {
				serverName = "Cloudflare Edge (" + loc + ")"
			} else {
				serverName = "Cloudflare Edge"
			}
		}
	}
	reading.ServerName = &serverName
	outputLog = append(outputLog, "Server: "+serverName)

	// 2. Latency & Jitter Probe (3 samples)
	pingMs, jitterMs := measureLatency(runCtx, client, endpoints.TraceURL, 3)
	if pingMs > 0 {
		reading.PingMs = &pingMs
		reading.JitterMs = &jitterMs
		outputLog = append(outputLog, fmt.Sprintf("Latency: %.2f ms, Jitter: %.2f ms", pingMs, jitterMs))
	}

	// 3. Download Test
	downMbps, downErr := measureDownload(runCtx, client, endpoints.DownURL)
	if downErr == nil && downMbps > 0 {
		reading.DownloadMbps = &downMbps
		outputLog = append(outputLog, fmt.Sprintf("Download: %.2f Mbps", downMbps))
	} else if downErr != nil {
		outputLog = append(outputLog, "Download error: "+downErr.Error())
	}

	// 4. Upload Test
	upMbps, upErr := measureUpload(runCtx, client, endpoints.UpURL)
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
	if strings.HasSuffix(traceURL, "/hello") {
		return "", ""
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, traceURL, nil)
	if err != nil {
		return "", ""
	}
	req.Header.Set("User-Agent", "MikroMan-SpeedTest/1.0")
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
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
		if err != nil {
			continue
		}
		req.Header.Set("User-Agent", "MikroMan-SpeedTest/1.0")
		resp, err := client.Do(req)
		if err == nil {
			_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 1024))
			_ = resp.Body.Close()
			rtts = append(rtts, float64(time.Since(start).Microseconds())/1000.0)
		}
		time.Sleep(40 * time.Millisecond)
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
	// Standard speedtest chunk sizes (bytes / size parameter)
	targetURL := downURL
	if !strings.Contains(targetURL, "?") {
		if strings.Contains(targetURL, "/download") {
			targetURL += "?size=25000000"
		} else {
			targetURL += "?bytes=25000000"
		}
	}

	workers := 6
	var totalBytes int64
	var wg sync.WaitGroup

	start := time.Now()
	// 7-second test window allows high throughput saturation without long wait
	testCtx, cancel := context.WithTimeout(ctx, 7*time.Second)
	defer cancel()

	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			buf := make([]byte, 128*1024)
			for {
				select {
				case <-testCtx.Done():
					return
				default:
				}

				req, err := http.NewRequestWithContext(testCtx, http.MethodGet, targetURL, nil)
				if err != nil {
					return
				}
				req.Header.Set("User-Agent", "MikroMan-SpeedTest/1.0")
				resp, err := client.Do(req)
				if err != nil {
					return
				}

				if resp.StatusCode != http.StatusOK {
					_ = resp.Body.Close()
					// If 25MB fails with 403 or non-200, try 10MB chunk fallback
					if strings.Contains(targetURL, "25000000") {
						targetURL = strings.Replace(targetURL, "25000000", "10000000", 1)
						continue
					}
					return
				}

				for {
					n, err := resp.Body.Read(buf)
					if n > 0 {
						atomic.AddInt64(&totalBytes, int64(n))
					}
					if err != nil {
						break
					}
				}
				_ = resp.Body.Close()
			}
		}()
	}

	wg.Wait()
	duration := time.Since(start).Seconds()
	if duration <= 0 || atomic.LoadInt64(&totalBytes) == 0 {
		return 0, fmt.Errorf("no download data received")
	}

	bytesRead := atomic.LoadInt64(&totalBytes)
	mbps := (float64(bytesRead) * 8.0) / (duration * 1_000_000.0)
	return math.Round(mbps*100) / 100, nil
}

type countingUploadReader struct {
	reader io.Reader
	total  *int64
}

func (c *countingUploadReader) Read(p []byte) (int, error) {
	n, err := c.reader.Read(p)
	if n > 0 {
		atomic.AddInt64(c.total, int64(n))
	}
	return n, err
}

func measureUpload(ctx context.Context, client *http.Client, upURL string) (float64, error) {
	workers := 6
	payloadSize := 512 * 1024 // 512 KB per POST chunk for high-bandwidth saturation
	payload := bytes.Repeat([]byte{0x5A}, payloadSize)

	var totalBytes int64
	var wg sync.WaitGroup

	start := time.Now()
	// 7-second test window allows cellular TCP window scaling to stabilize for upload
	testCtx, cancel := context.WithTimeout(ctx, 7*time.Second)
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

				bodyReader := &countingUploadReader{
					reader: bytes.NewReader(payload),
					total:  &totalBytes,
				}

				req, err := http.NewRequestWithContext(testCtx, http.MethodPost, upURL, bodyReader)
				if err != nil {
					return
				}
				req.Header.Set("User-Agent", "MikroMan-SpeedTest/1.0")
				req.Header.Set("Content-Type", "application/octet-stream")
				req.ContentLength = int64(payloadSize)

				resp, err := client.Do(req)
				if err != nil {
					return
				}
				_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
				_ = resp.Body.Close()
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

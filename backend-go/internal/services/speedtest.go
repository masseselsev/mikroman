package services

import (
	"context"
	"fmt"
	"log/slog"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

const (
	DefaultSpeedTestImage  = "quay.io/tangent/speedtest-cli:latest"
	SpeedTestComment       = "mikroman:speedtest"
	SpeedTestLogTopic      = "container"
	DefaultSpeedTimeout    = 120 * time.Second
	SpeedTestPollInterval  = 2 * time.Second
)

var (
	reDownload   = regexp.MustCompile(`(?i)Download:\s*([\d.]+)\s*Mbps`)
	reUpload     = regexp.MustCompile(`(?i)Upload:\s*([\d.]+)\s*Mbps`)
	rePing       = regexp.MustCompile(`(?i)(?:Idle\s+)?Latency:\s*([\d.]+)\s*ms|Ping:\s*([\d.]+)\s*ms`)
	reJitter     = regexp.MustCompile(`(?i)jitter:\s*([\d.]+)\s*ms`)
	rePacketLoss = regexp.MustCompile(`(?i)Packet Loss:\s*([\d.]+)\s*%`)
	reServer     = regexp.MustCompile(`(?i)Server:\s*(.+?)\s*$`)
	reISP        = regexp.MustCompile(`(?i)ISP:\s*(.+?)\s*$`)
	reResultURL  = regexp.MustCompile(`(?i)(https?://\S*speedtest\.net/result\S*)|Result\s*URL:\s*(\S+)`)
)

type SpeedTestReading struct {
	DownloadMbps  *float64 `json:"download_mbps"`
	UploadMbps    *float64 `json:"upload_mbps"`
	PingMs        *float64 `json:"ping_ms"`
	JitterMs      *float64 `json:"jitter_ms"`
	PacketLossPct *float64 `json:"packet_loss_pct"`
	ServerName    *string  `json:"server_name"`
	ISP           *string  `json:"isp"`
	ResultURL     *string  `json:"result_url"`
	RawOutput     string   `json:"raw_output"`
	Status        string   `json:"status"` // "ok", "failed", "timeout"
	Error         *string  `json:"error"`
}

func (r *SpeedTestReading) HasAnyFigure() bool {
	return r.DownloadMbps != nil || r.UploadMbps != nil || r.PingMs != nil
}

// ParseSpeedTestOutput extracts figures from Ookla CLI stdout lines.
func ParseSpeedTestOutput(lines []string) SpeedTestReading {
	reading := SpeedTestReading{
		RawOutput: strings.Join(lines, "\n"),
		Status:    "ok",
	}

	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}

		if reading.DownloadMbps == nil {
			if m := reDownload.FindStringSubmatch(line); len(m) > 1 {
				if v, err := strconv.ParseFloat(m[1], 64); err == nil {
					reading.DownloadMbps = &v
				}
			}
		}

		if reading.UploadMbps == nil {
			if m := reUpload.FindStringSubmatch(line); len(m) > 1 {
				if v, err := strconv.ParseFloat(m[1], 64); err == nil {
					reading.UploadMbps = &v
				}
			}
		}

		if reading.PingMs == nil {
			if m := rePing.FindStringSubmatch(line); len(m) > 1 {
				valStr := m[1]
				if valStr == "" && len(m) > 2 {
					valStr = m[2]
				}
				if v, err := strconv.ParseFloat(valStr, 64); err == nil {
					reading.PingMs = &v
				}
			}
		}

		if reading.JitterMs == nil {
			if m := reJitter.FindStringSubmatch(line); len(m) > 1 {
				if v, err := strconv.ParseFloat(m[1], 64); err == nil {
					reading.JitterMs = &v
				}
			}
		}

		if reading.PacketLossPct == nil {
			if m := rePacketLoss.FindStringSubmatch(line); len(m) > 1 {
				if v, err := strconv.ParseFloat(m[1], 64); err == nil {
					reading.PacketLossPct = &v
				}
			}
		}

		if reading.ServerName == nil {
			if m := reServer.FindStringSubmatch(line); len(m) > 1 {
				srv := strings.TrimSpace(m[1])
				reading.ServerName = &srv
			}
		}

		if reading.ISP == nil {
			if m := reISP.FindStringSubmatch(line); len(m) > 1 {
				isp := strings.TrimSpace(m[1])
				reading.ISP = &isp
			}
		}

		if reading.ResultURL == nil {
			if m := reResultURL.FindStringSubmatch(line); len(m) > 1 {
				urlStr := m[1]
				if urlStr == "" && len(m) > 2 {
					urlStr = m[2]
				}
				urlStr = strings.TrimSpace(urlStr)
				reading.ResultURL = &urlStr
			}
		}
	}

	if !reading.HasAnyFigure() {
		reading.Status = "failed"
		errMsg := "Container produced no recognisable speed test output."
		reading.Error = &errMsg
	}

	return reading
}

// SpeedTestRunner coordinates speed test execution on RouterOS via container.
type SpeedTestRunner struct {
	client *routeros.Client
}

func NewSpeedTestRunner(client *routeros.Client) *SpeedTestRunner {
	return &SpeedTestRunner{client: client}
}

// FindContainer identifies the speedtest container by comment or image name.
func (r *SpeedTestRunner) FindContainer(ctx context.Context) (*routeros.Container, error) {
	containers, err := r.client.GetContainers(ctx)
	if err != nil {
		return nil, err
	}

	for _, c := range containers {
		if c.Comment == SpeedTestComment {
			return &c, nil
		}
	}

	for _, c := range containers {
		tagLower := strings.ToLower(c.Tag)
		nameLower := strings.ToLower(c.Name)
		if strings.Contains(tagLower, "speedtest") || strings.Contains(nameLower, "speedtest") {
			return &c, nil
		}
	}

	return nil, nil
}

// EnsureLogging ensures that RouterOS routes container stdout to the system log.
func (r *SpeedTestRunner) EnsureLogging(ctx context.Context) error {
	type logRule struct {
		ID       string `json:".id"`
		Topics   string `json:"topics"`
		Action   string `json:"action"`
		Disabled string `json:"disabled"`
	}

	var rules []logRule
	if err := r.client.Get(ctx, "/system/logging", &rules); err == nil {
		for _, rule := range rules {
			if strings.Contains(strings.ToLower(rule.Topics), SpeedTestLogTopic) &&
				rule.Disabled != "true" && rule.Disabled != "yes" {
				return nil
			}
		}
	}

	payload := map[string]string{
		"topics": SpeedTestLogTopic,
		"action": "memory",
	}
	var res interface{}
	return r.client.Put(ctx, "/system/logging", payload, &res)
}

// CreateContainer provisions the speed test container on the router.
func (r *SpeedTestRunner) CreateContainer(ctx context.Context, iface, rootDir, image string) error {
	if image == "" {
		image = DefaultSpeedTestImage
	}

	payload := map[string]interface{}{
		"remote-image":  image,
		"interface":     iface,
		"root-dir":      rootDir,
		"comment":       SpeedTestComment,
		"logging":       "yes",
		"start-on-boot": "no",
	}

	return r.client.CreateContainer(ctx, payload)
}

// Run starts the container, polls the log for output, and returns the result.
func (r *SpeedTestRunner) Run(ctx context.Context, timeout time.Duration) (SpeedTestReading, error) {
	if timeout <= 0 {
		timeout = DefaultSpeedTimeout
	}

	container, err := r.FindContainer(ctx)
	if err != nil {
		errMsg := fmt.Sprintf("Failed to query containers: %v", err)
		return SpeedTestReading{Status: "failed", Error: &errMsg}, err
	}
	if container == nil {
		errMsg := "No speed test container configured on this router."
		return SpeedTestReading{Status: "failed", Error: &errMsg}, nil
	}

	_ = r.EnsureLogging(ctx)

	// Snapshot existing container log IDs so previous runs don't get read
	beforeIDs := make(map[string]bool)
	if logs, err := r.client.GetLogs(ctx, SpeedTestLogTopic, 200); err == nil {
		for _, l := range logs {
			if l.ID != "" {
				beforeIDs[l.ID] = true
			}
		}
	}

	// Start container
	if err := r.client.RunContainerAction(ctx, container.ID, "start"); err != nil {
		errMsg := fmt.Sprintf("Could not start speed test container: %v", err)
		return SpeedTestReading{Status: "failed", Error: &errMsg}, err
	}

	// Poll log until both download and upload are available or deadline expires
	deadline := time.Now().Add(timeout)
	ticker := time.NewTicker(SpeedTestPollInterval)
	defer ticker.Stop()

	var newest []string

	for {
		select {
		case <-ctx.Done():
			errMsg := "Speed test canceled"
			return SpeedTestReading{Status: "failed", Error: &errMsg}, ctx.Err()
		case now := <-ticker.C:
			if now.After(deadline) {
				reading := ParseSpeedTestOutput(newest)
				if reading.HasAnyFigure() {
					reading.Status = "ok"
					msg := "Timed out before test completed; measurements are partial."
					reading.Error = &msg
				} else {
					reading.Status = "timeout"
					msg := fmt.Sprintf("No speed test output received within %ds. Check that the container has WAN connectivity and container logging is active.", int(timeout.Seconds()))
					reading.Error = &msg
				}
				return reading, nil
			}

			logs, err := r.client.GetLogs(ctx, SpeedTestLogTopic, 100)
			if err != nil {
				slog.Debug("Failed to poll container logs for speedtest", "err", err)
				continue
			}

			var fresh []string
			for _, l := range logs {
				if l.ID != "" && !beforeIDs[l.ID] {
					fresh = append(fresh, l.Message)
				}
			}
			newest = fresh

			if len(newest) > 0 {
				reading := ParseSpeedTestOutput(newest)
				if reading.DownloadMbps != nil && reading.UploadMbps != nil {
					return reading, nil
				}
			}
		}
	}
}

// ConvertToDBModel converts a SpeedTestReading to a db.SpeedTestResult model.
func (r *SpeedTestReading) ToDBModel(routerID int) *db.SpeedTestResult {
	res := &db.SpeedTestResult{
		RouterID:      routerID,
		CreatedAt:     time.Now().UTC(),
		DownloadMbps:  r.DownloadMbps,
		UploadMbps:    r.UploadMbps,
		PingMs:        r.PingMs,
		JitterMs:      r.JitterMs,
		PacketLossPct: r.PacketLossPct,
		Status:        r.Status,
		RawOutput:     r.RawOutput,
	}

	if r.ServerName != nil {
		res.ServerName = db.NewNullString(*r.ServerName)
	}
	if r.ISP != nil {
		res.ISP = db.NewNullString(*r.ISP)
	}
	if r.ResultURL != nil {
		res.ResultURL = db.NewNullString(*r.ResultURL)
	}
	if r.Error != nil {
		res.Error = db.NewNullString(*r.Error)
	}

	return res
}

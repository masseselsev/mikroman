package routeros

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

var (
	ErrUnreachable = errors.New("router is unreachable (circuit breaker open)")
	ErrNotFound    = errors.New("resource not found")
)

const (
	DefaultCooldown = 15 * time.Second
	DefaultTimeout  = 5 * time.Second
)

// Config configures a RouterOS REST client connection.
type Config struct {
	Host      string
	Port      int
	Username  string
	Password  string
	UseSSL    bool
	SSLVerify bool
	CACert    string
	Timeout   time.Duration
}

// Client provides high-performance access to RouterOS REST API with pooling and circuit breaker.
type Client struct {
	cfg        Config
	httpClient *http.Client
	baseURL    string

	// Circuit breaker state
	mu             sync.RWMutex
	unreachableUntil time.Time
	lastError      error

	// Immune IPs cache
	immuneMu       sync.RWMutex
	immuneIPs      map[string]bool
	immuneDetected bool
}

// NewClient creates a new Client configured for the specified router.
func NewClient(cfg Config) (*Client, error) {
	if cfg.Port <= 0 {
		if cfg.UseSSL {
			cfg.Port = 443
		} else {
			cfg.Port = 80
		}
	}
	if cfg.Timeout <= 0 {
		cfg.Timeout = DefaultTimeout
	}

	scheme := "http"
	if cfg.UseSSL {
		scheme = "https"
	}
	baseURL := fmt.Sprintf("%s://%s:%d/rest", scheme, cfg.Host, cfg.Port)

	tlsConfig := &tls.Config{
		InsecureSkipVerify: !cfg.SSLVerify,
	}

	if cfg.CACert != "" {
		certPool := x509.NewCertPool()
		if certPool.AppendCertsFromPEM([]byte(cfg.CACert)) {
			tlsConfig.RootCAs = certPool
			tlsConfig.InsecureSkipVerify = false
		}
	}

	transport := &http.Transport{
		Proxy: http.ProxyFromEnvironment,
		DialContext: (&net.Dialer{
			Timeout:   3 * time.Second,
			KeepAlive: 30 * time.Second,
		}).DialContext,
		ForceAttemptHTTP2:     true,
		MaxIdleConns:          10,
		MaxIdleConnsPerHost:   5,
		IdleConnTimeout:       90 * time.Second,
		TLSHandshakeTimeout:   3 * time.Second,
		ExpectContinueTimeout: 1 * time.Second,
		TLSClientConfig:       tlsConfig,
	}

	httpClient := &http.Client{
		Transport: transport,
		Timeout:   cfg.Timeout,
	}

	c := &Client{
		cfg:        cfg,
		httpClient: httpClient,
		baseURL:    baseURL,
		immuneIPs:  make(map[string]bool),
	}

	// Pre-seed host into immune IPs
	c.immuneIPs[cfg.Host] = true

	return c, nil
}

// GetImmuneIPs returns addresses that must never be blocked, paused or throttled.
func (c *Client) GetImmuneIPs() map[string]bool {
	c.immuneMu.Lock()
	defer c.immuneMu.Unlock()

	if !c.immuneDetected {
		c.immuneIPs[c.cfg.Host] = true
		// UDP connect probe to find our outbound local IP towards router
		if conn, err := net.Dial("udp", net.JoinHostPort(c.cfg.Host, strconv.Itoa(c.cfg.Port))); err == nil {
			if localAddr, ok := conn.LocalAddr().(*net.UDPAddr); ok {
				c.immuneIPs[localAddr.IP.String()] = true
			}
			_ = conn.Close()
		}
		c.immuneDetected = true
	}

	res := make(map[string]bool, len(c.immuneIPs))
	for k, v := range c.immuneIPs {
		res[k] = v
	}
	return res
}

// AddImmuneIP dynamically whitelists an IP from blocking.
func (c *Client) AddImmuneIP(ip string) {
	c.immuneMu.Lock()
	defer c.immuneMu.Unlock()
	c.immuneIPs[strings.TrimSpace(ip)] = true
}

func (c *Client) isBreakerOpen() bool {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return time.Now().Before(c.unreachableUntil)
}

func (c *Client) noteFailure(err error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	// Only connection errors trip the breaker, not HTTP 4xx/5xx responses
	var netErr net.Error
	if errors.As(err, &netErr) || strings.Contains(err.Error(), "connection refused") || strings.Contains(err.Error(), "no route to host") {
		c.unreachableUntil = time.Now().Add(DefaultCooldown)
		c.lastError = err
	}
}

func (c *Client) noteSuccess() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.unreachableUntil = time.Time{}
	c.lastError = nil
}

func (c *Client) doRequest(ctx context.Context, method, path string, body interface{}, target interface{}) error {
	if c.isBreakerOpen() {
		return fmt.Errorf("%w (last error: %v)", ErrUnreachable, c.lastError)
	}

	url := c.baseURL + path
	var bodyReader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return fmt.Errorf("failed to marshal request body: %w", err)
		}
		bodyReader = bytes.NewReader(data)
	}

	req, err := http.NewRequestWithContext(ctx, method, url, bodyReader)
	if err != nil {
		return err
	}

	req.SetBasicAuth(c.cfg.Username, c.cfg.Password)
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		c.noteFailure(err)
		return err
	}
	defer resp.Body.Close()

	c.noteSuccess()

	if resp.StatusCode == http.StatusNotFound {
		return ErrNotFound
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		respBody, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("routeros api error (status %d): %s", resp.StatusCode, string(respBody))
	}

	if target == nil {
		return nil
	}

	respData, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("failed to read response body: %w", err)
	}

	// Normalizer: if target is a pointer to a slice, ensure single objects wrap in slice
	return unmarshalFlexible(respData, target)
}

// unmarshalFlexible safely unpacks either [...] or {...} into slice or single struct targets.
func unmarshalFlexible(data []byte, target interface{}) error {
	trimmed := bytes.TrimSpace(data)
	if len(trimmed) == 0 {
		return nil
	}

	// Try standard unmarshal first
	if err := json.Unmarshal(trimmed, target); err == nil {
		return nil
	}

	// If target is slice and json is a single object: wrap in [ ... ]
	if trimmed[0] == '{' {
		wrapped := append(append([]byte{'['}, trimmed...), ']')
		if err := json.Unmarshal(wrapped, target); err == nil {
			return nil
		}
	}

	return json.Unmarshal(trimmed, target)
}

// Get executes a GET request against RouterOS REST API.
func (c *Client) Get(ctx context.Context, path string, target interface{}) error {
	return c.doRequest(ctx, http.MethodGet, path, nil, target)
}

// Put executes a PUT request (create/add).
func (c *Client) Put(ctx context.Context, path string, body, target interface{}) error {
	return c.doRequest(ctx, http.MethodPut, path, body, target)
}

// Patch executes a PATCH request (update).
func (c *Client) Patch(ctx context.Context, path string, body, target interface{}) error {
	return c.doRequest(ctx, http.MethodPatch, path, body, target)
}

// Post executes a POST request (action/command).
func (c *Client) Post(ctx context.Context, path string, body, target interface{}) error {
	return c.doRequest(ctx, http.MethodPost, path, body, target)
}

// Delete executes a DELETE request.
func (c *Client) Delete(ctx context.Context, path string) error {
	return c.doRequest(ctx, http.MethodDelete, path, nil, nil)
}

// DoRaw executes an HTTP request against the RouterOS REST API and returns raw body bytes and HTTP status code.
func (c *Client) DoRaw(ctx context.Context, method, path string, body io.Reader, contentType string) ([]byte, int, error) {
	if c.isBreakerOpen() {
		return nil, 0, fmt.Errorf("%w (last error: %v)", ErrUnreachable, c.lastError)
	}

	url := c.baseURL + path
	req, err := http.NewRequestWithContext(ctx, method, url, body)
	if err != nil {
		return nil, 0, err
	}

	req.SetBasicAuth(c.cfg.Username, c.cfg.Password)
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	req.Header.Set("Accept", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		c.noteFailure(err)
		return nil, 0, err
	}
	defer resp.Body.Close()

	c.noteSuccess()

	respData, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, resp.StatusCode, fmt.Errorf("failed to read response body: %w", err)
	}

	return respData, resp.StatusCode, nil
}

// PostJSONRaw executes a POST request with JSON body and returns raw response body bytes.
func (c *Client) PostJSONRaw(ctx context.Context, path string, body interface{}) ([]byte, int, error) {
	var bodyReader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return nil, 0, fmt.Errorf("failed to marshal request body: %w", err)
		}
		bodyReader = bytes.NewReader(data)
	}
	return c.DoRaw(ctx, http.MethodPost, path, bodyReader, "application/json")
}


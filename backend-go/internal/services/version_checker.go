package services

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	defaultGitHubReleasesURL = "https://api.github.com/repos/masseselsev/mikroman/releases/latest"
	// defaultVersionCacheTTL bounds how long a discovery result may be served
	// without asking GitHub again.
	//
	// It is deliberately short: refreshes are conditional (If-None-Match) and
	// a 304 "Not Modified" reply does not count against GitHub's
	// unauthenticated rate limit, so revalidation is free per request. A long
	// TTL buys nothing and costs freshness - a release published while a
	// dashboard is open stays invisible until the TTL lapses *and* the page is
	// loaded again.
	defaultVersionCacheTTL = 15 * time.Minute
	defaultCheckTimeout    = 6 * time.Second
)

// VersionInfo models the application version state and update availability.
type VersionInfo struct {
	CurrentVersion string    `json:"current_version"`
	LatestVersion  string    `json:"latest_version"`
	HasUpdate      bool      `json:"has_update"`
	ReleaseURL     string    `json:"release_url,omitempty"`
	ReleaseName    string    `json:"release_name,omitempty"`
	PublishedAt    string    `json:"published_at,omitempty"`
	CheckedAt      time.Time `json:"checked_at"`
	// CheckFailed marks a reply that GitHub could not confirm: the process
	// reached neither a fresh payload nor a 304, so the fields above may hold
	// the last known release or the running version.
	//
	// Consumers must treat HasUpdate=false together with CheckFailed=true as
	// "unknown", never as "up to date" - otherwise a rate-limited or offline
	// router silently claims to be current.
	CheckFailed bool `json:"check_failed,omitempty"`
}

type githubReleasePayload struct {
	TagName     string `json:"tag_name"`
	Name        string `json:"name"`
	HTMLURL     string `json:"html_url"`
	PublishedAt string `json:"published_at"`
	Draft       bool   `json:"draft"`
	Prerelease  bool   `json:"prerelease"`
}

// VersionChecker queries GitHub Releases to determine whether an updated
// release of MikroMan is available. It caches responses with an in-memory TTL
// and uses HTTP ETag headers to minimize network usage and avoid rate limits.
type VersionChecker struct {
	currentVersion string
	apiURL         string
	cacheTTL       time.Duration
	client         *http.Client

	mu          sync.RWMutex
	cachedInfo  *VersionInfo
	etag        string
	lastChecked time.Time
}

// NewVersionChecker constructs a new VersionChecker instance.
func NewVersionChecker(currentVersion string) *VersionChecker {
	return &VersionChecker{
		currentVersion: currentVersion,
		apiURL:         defaultGitHubReleasesURL,
		cacheTTL:       defaultVersionCacheTTL,
		client: &http.Client{
			Timeout: defaultCheckTimeout,
		},
	}
}

// SetAPIURL overrides the releases URL (useful for testing or mirrors).
func (v *VersionChecker) SetAPIURL(url string) {
	v.mu.Lock()
	defer v.mu.Unlock()
	v.apiURL = url
}

// SetHTTPClient sets a custom HTTP client (useful for unit testing).
func (v *VersionChecker) SetHTTPClient(client *http.Client) {
	v.mu.Lock()
	defer v.mu.Unlock()
	v.client = client
}

// ParseSemVer extracts (major, minor, patch) from strings like "0.3.25", "v0.3.26", "0.4.0-rc1".
func ParseSemVer(v string) (major, minor, patch int, ok bool) {
	v = strings.TrimSpace(v)
	v = strings.TrimPrefix(v, "v")
	if idx := strings.IndexAny(v, "-+"); idx != -1 {
		v = v[:idx]
	}
	parts := strings.Split(v, ".")
	if len(parts) == 0 {
		return 0, 0, 0, false
	}
	var err error
	if len(parts) >= 1 {
		major, err = strconv.Atoi(parts[0])
		if err != nil {
			return 0, 0, 0, false
		}
	}
	if len(parts) >= 2 {
		minor, err = strconv.Atoi(parts[1])
		if err != nil {
			return 0, 0, 0, false
		}
	}
	if len(parts) >= 3 {
		patch, err = strconv.Atoi(parts[2])
		if err != nil {
			return 0, 0, 0, false
		}
	}
	return major, minor, patch, true
}

// IsNewerVersion returns true if candidate is strictly greater than current.
func IsNewerVersion(current, candidate string) bool {
	cMaj, cMin, cPatch, cOk := ParseSemVer(current)
	candMaj, candMin, candPatch, candOk := ParseSemVer(candidate)
	if !cOk || !candOk {
		return false
	}
	if candMaj != cMaj {
		return candMaj > cMaj
	}
	if candMin != cMin {
		return candMin > cMin
	}
	return candPatch > cPatch
}

// Check queries GitHub for the latest release or serves from cache.
func (v *VersionChecker) Check(ctx context.Context, force bool) (*VersionInfo, error) {
	v.mu.RLock()
	if !force && v.cachedInfo != nil && time.Since(v.lastChecked) < v.cacheTTL {
		cached := *v.cachedInfo
		v.mu.RUnlock()
		return &cached, nil
	}
	apiURL := v.apiURL
	etag := v.etag
	curVersion := v.currentVersion
	client := v.client
	v.mu.RUnlock()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, apiURL, nil)
	if err != nil {
		return v.fallbackInfo(curVersion), nil
	}

	req.Header.Set("User-Agent", fmt.Sprintf("MikroMan/%s (+https://github.com/masseselsev/mikroman)", curVersion))
	req.Header.Set("Accept", "application/vnd.github.v3+json")
	if etag != "" {
		req.Header.Set("If-None-Match", etag)
	}

	resp, err := client.Do(req)
	if err != nil {
		slog.Warn("GitHub release check failed: upstream unreachable", "err", err)
		return v.fallbackInfo(curVersion), nil
	}
	defer resp.Body.Close()

	// 304 Not Modified: Cached information is still completely fresh
	if resp.StatusCode == http.StatusNotModified {
		v.mu.Lock()
		v.lastChecked = time.Now()
		if v.cachedInfo != nil {
			cached := *v.cachedInfo
			v.mu.Unlock()
			return &cached, nil
		}
		v.mu.Unlock()
	}

	// Any non-200 status (e.g. rate limit 403, network glitch 5xx)
	if resp.StatusCode != http.StatusOK {
		slog.Warn("GitHub release check failed: unexpected status",
			"status", resp.StatusCode,
			"remaining", resp.Header.Get("X-RateLimit-Remaining"))
		return v.fallbackInfo(curVersion), nil
	}

	var payload githubReleasePayload
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		slog.Warn("GitHub release check failed: unparsable payload", "err", err)
		return v.fallbackInfo(curVersion), nil
	}

	newEtag := resp.Header.Get("ETag")
	cleanLatest := strings.TrimPrefix(payload.TagName, "v")
	hasUpdate := IsNewerVersion(curVersion, cleanLatest)

	info := &VersionInfo{
		CurrentVersion: strings.TrimPrefix(curVersion, "v"),
		LatestVersion:  cleanLatest,
		HasUpdate:      hasUpdate,
		ReleaseURL:     payload.HTMLURL,
		ReleaseName:    payload.Name,
		PublishedAt:    payload.PublishedAt,
		CheckedAt:      time.Now(),
	}

	v.mu.Lock()
	v.cachedInfo = info
	v.etag = newEtag
	v.lastChecked = time.Now()
	v.mu.Unlock()

	return info, nil
}

// fallbackInfo answers when the upstream check could not be completed.
//
// Three things have to hold at once: a release that was already visible must
// not disappear because one request failed, the caller must be able to tell
// "no update" from "could not tell" (CheckFailed), and a broken upstream must
// not be retried on every page load - the attempt time is recorded so the next
// attempt comes at most one TTL later.
func (v *VersionChecker) fallbackInfo(curVersion string) *VersionInfo {
	v.mu.Lock()
	defer v.mu.Unlock()

	v.lastChecked = time.Now()
	if v.cachedInfo != nil {
		cached := *v.cachedInfo
		cached.CheckFailed = true
		cached.CheckedAt = time.Now()
		return &cached
	}
	cleanCur := strings.TrimPrefix(curVersion, "v")
	return &VersionInfo{
		CurrentVersion: cleanCur,
		LatestVersion:  cleanCur,
		HasUpdate:      false,
		CheckFailed:    true,
		CheckedAt:      time.Now(),
	}
}

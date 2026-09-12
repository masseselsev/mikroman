package services

import (
	"compress/gzip"
	"context"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/oschwald/maxminddb-golang"
)

// GeoIPUpdater manages automatic background downloads, integrity validation,
// and hot-swapping of the local DB-IP Country MMDB file.
type GeoIPUpdater struct {
	dataDir    string
	dbPath     string
	client     *http.Client
	mu         sync.Mutex
	isUpdating bool
	lastUpdate time.Time
	lastErr    error
	stopCh     chan struct{}
}

var globalGeoIPUpdater *GeoIPUpdater

// InitGeoIPUpdater creates and starts the global GeoIP updater service.
func InitGeoIPUpdater(dataDir string) *GeoIPUpdater {
	dbPath := filepath.Join(dataDir, "geoip.mmdb")
	u := &GeoIPUpdater{
		dataDir: dataDir,
		dbPath:  dbPath,
		client: &http.Client{
			Timeout: 60 * time.Second,
		},
		stopCh: make(chan struct{}),
	}
	globalGeoIPUpdater = u

	// 1. Try to load existing local database immediately
	if err := u.loadLocalFile(); err != nil {
		log.Printf("[GeoIP] Local database not ready (%v), queuing initial background download", err)
		go func() {
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
			defer cancel()
			_ = u.Update(ctx)
		}()
	} else {
		// If existing file is older than 30 days, trigger background refresh
		if info, err := os.Stat(dbPath); err == nil && time.Since(info.ModTime()) > 30*24*time.Hour {
			log.Printf("[GeoIP] Local database is older than 30 days, scheduling refresh")
			go func() {
				ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
				defer cancel()
				_ = u.Update(ctx)
			}()
		}
	}

	// 2. Start periodic daily checker ticker
	go u.runScheduler()

	return u
}

// GetGlobalGeoIPUpdater returns the active updater instance.
func GetGlobalGeoIPUpdater() *GeoIPUpdater {
	return globalGeoIPUpdater
}

// Close terminates the periodic scheduler.
func (u *GeoIPUpdater) Close() {
	if u.stopCh != nil {
		select {
		case <-u.stopCh:
		default:
			close(u.stopCh)
		}
	}
}

func (u *GeoIPUpdater) runScheduler() {
	ticker := time.NewTicker(24 * time.Hour)
	defer ticker.Stop()

	for {
		select {
		case <-u.stopCh:
			return
		case <-ticker.C:
			info, err := os.Stat(u.dbPath)
			if err != nil || time.Since(info.ModTime()) > 30*24*time.Hour {
				ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
				_ = u.Update(ctx)
				cancel()
			}
		}
	}
}

func (u *GeoIPUpdater) loadLocalFile() error {
	info, err := os.Stat(u.dbPath)
	if err != nil {
		return err
	}
	if info.Size() < 100*1024 { // Sanity check: valid MMDB should be at least 100 KB
		return fmt.Errorf("local file too small (%d bytes)", info.Size())
	}

	reader, err := maxminddb.Open(u.dbPath)
	if err != nil {
		return fmt.Errorf("failed to parse mmdb: %w", err)
	}

	SetActiveMMDB(reader)
	u.mu.Lock()
	u.lastUpdate = info.ModTime()
	u.mu.Unlock()
	log.Printf("[GeoIP] Successfully loaded local database from %s (size: %d bytes, modified: %s)",
		u.dbPath, info.Size(), info.ModTime().Format(time.RFC3339))
	return nil
}

// Update downloads the latest monthly DB-IP Country Lite database, validates it, and hot-swaps the active reader.
func (u *GeoIPUpdater) Update(ctx context.Context) error {
	u.mu.Lock()
	if u.isUpdating {
		u.mu.Unlock()
		return errors.New("GeoIP update is already in progress")
	}
	u.isUpdating = true
	u.mu.Unlock()

	defer func() {
		u.mu.Lock()
		u.isUpdating = false
		u.mu.Unlock()
	}()

	now := time.Now().UTC()
	currentMonthURL := fmt.Sprintf("https://download.db-ip.com/free/dbip-country-lite-%04d-%02d.mmdb.gz", now.Year(), int(now.Month()))
	prevMonth := now.AddDate(0, -1, 0)
	prevMonthURL := fmt.Sprintf("https://download.db-ip.com/free/dbip-country-lite-%04d-%02d.mmdb.gz", prevMonth.Year(), int(prevMonth.Month()))

	urls := []struct {
		url      string
		isGzip   bool
	}{
		{currentMonthURL, true},
		{prevMonthURL, true},
		{"https://raw.githubusercontent.com/sapics/ip-location-db/main/dbip-country/dbip-country-lite.mmdb", false},
	}

	var downloadErr error
	tmpFile := u.dbPath + ".tmp"
	_ = os.Remove(tmpFile)

	for _, target := range urls {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}

		log.Printf("[GeoIP] Attempting download from %s...", target.url)
		err := u.downloadAndExtract(ctx, target.url, target.isGzip, tmpFile)
		if err == nil {
			downloadErr = nil
			break
		}
		log.Printf("[GeoIP] Download from %s failed: %v", target.url, err)
		downloadErr = err
	}

	if downloadErr != nil {
		u.mu.Lock()
		u.lastErr = downloadErr
		u.mu.Unlock()
		_ = os.Remove(tmpFile)
		return fmt.Errorf("all GeoIP download sources failed: %w", downloadErr)
	}

	// Validate downloaded MMDB integrity
	reader, err := maxminddb.Open(tmpFile)
	if err != nil {
		_ = os.Remove(tmpFile)
		u.mu.Lock()
		u.lastErr = err
		u.mu.Unlock()
		return fmt.Errorf("downloaded GeoIP file failed verification: %w", err)
	}

	// Atomically replace existing database file
	if err := os.Rename(tmpFile, u.dbPath); err != nil {
		_ = reader.Close()
		_ = os.Remove(tmpFile)
		return fmt.Errorf("failed to commit updated GeoIP file: %w", err)
	}

	// Hot-swap in memory
	SetActiveMMDB(reader)

	u.mu.Lock()
	u.lastUpdate = time.Now().UTC()
	u.lastErr = nil
	u.mu.Unlock()

	log.Printf("[GeoIP] Successfully updated and hot-swapped database at %s", u.dbPath)
	return nil
}

func (u *GeoIPUpdater) downloadAndExtract(ctx context.Context, url string, isGz bool, destPath string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return err
	}
	req.Header.Set("User-Agent", "MikroMan/0.3.12 (Network Management Appliance)")

	resp, err := u.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP status %d", resp.StatusCode)
	}

	out, err := os.OpenFile(destPath, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0644)
	if err != nil {
		return err
	}
	defer out.Close()

	var reader io.Reader = resp.Body
	if isGz {
		gzReader, err := gzip.NewReader(resp.Body)
		if err != nil {
			return fmt.Errorf("gzip init failed: %w", err)
		}
		defer gzReader.Close()
		reader = gzReader
	}

	copied, err := io.Copy(out, reader)
	if err != nil {
		return err
	}
	if copied < 100*1024 {
		return fmt.Errorf("download payload unexpectedly small (%d bytes)", copied)
	}

	return nil
}

// Status returns the current operational status of the GeoIP engine.
type GeoIPStatus struct {
	Loaded     bool      `json:"loaded"`
	Path       string    `json:"path"`
	SizeBytes  int64     `json:"size_bytes"`
	LastUpdate time.Time `json:"last_update"`
	IsUpdating bool      `json:"is_updating"`
	LastError  string    `json:"last_error,omitempty"`
}

// GetStatus returns the current status of the GeoIP service.
func (u *GeoIPUpdater) GetStatus() GeoIPStatus {
	u.mu.Lock()
	defer u.mu.Unlock()

	status := GeoIPStatus{
		Loaded:     GetActiveMMDB() != nil,
		Path:       u.dbPath,
		LastUpdate: u.lastUpdate,
		IsUpdating: u.isUpdating,
	}
	if u.lastErr != nil {
		status.LastError = u.lastErr.Error()
	}
	if info, err := os.Stat(u.dbPath); err == nil {
		status.SizeBytes = info.Size()
		if status.LastUpdate.IsZero() {
			status.LastUpdate = info.ModTime()
		}
	}
	return status
}


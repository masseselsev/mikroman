package services

import (
	"context"
	"database/sql"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

var (
	backupLocksMu sync.Mutex
	backupLocks   = make(map[int]*sync.Mutex)
)

func getRouterBackupLock(routerID int) *sync.Mutex {
	backupLocksMu.Lock()
	defer backupLocksMu.Unlock()
	if lock, ok := backupLocks[routerID]; ok {
		return lock
	}
	lock := &sync.Mutex{}
	backupLocks[routerID] = lock
	return lock
}

// BackupService handles manual and automated backups of MikroTik routers.
type BackupService struct {
	database *db.DB
}

func NewBackupService(database *db.DB) *BackupService {
	return &BackupService{database: database}
}

func (s *BackupService) GetRouterClient(r *db.Router) (*routeros.Client, error) {
	password := r.Password
	if s.database.Fernet != nil && strings.HasPrefix(password, "enc:v1:") {
		password = s.database.Fernet.DecryptSecret(password)
	}

	cfg := routeros.Config{
		Host:      r.Host,
		Port:      r.Port,
		Username:  r.Username,
		Password:  password,
		UseSSL:    r.UseSSL,
		SSLVerify: r.SSLVerify,
		CACert:    r.CACert.String,
		Timeout:   35 * time.Second,
	}
	return routeros.NewClient(cfg)
}

func (s *BackupService) RunRouterBackup(ctx context.Context, routerID int, source string) (*db.RouterBackup, error) {
	lock := getRouterBackupLock(routerID)
	lock.Lock()
	defer lock.Unlock()

	r, err := s.database.GetRouter(routerID)
	if err != nil || r == nil {
		return nil, fmt.Errorf("router %d not found", routerID)
	}

	client, err := s.GetRouterClient(r)
	if err != nil {
		return nil, fmt.Errorf("failed to create routeros client: %w", err)
	}

	startedAt := time.Now()
	stem := fmt.Sprintf("%d", startedAt.UnixMilli())

	// Pre-flight sweep
	_, _ = client.SweepTemporaryFiles(ctx, "")

	var model, serial, osVersion string
	if res, resErr := client.GetSystemResource(ctx); resErr == nil && res != nil {
		model = res.BoardName
		osVersion = res.Version
	}
	if rb, rbErr := client.GetRouterBoard(ctx); rbErr == nil && rb != nil {
		serial = rb.SerialNumber
	}

	// 1. Export plaintext configuration script (.rsc)
	rawRSC, err := client.ExportConfig(ctx, stem, 35*time.Second)
	if err != nil {
		durationMS := int(time.Since(startedAt).Milliseconds())
		failRec := &db.RouterBackup{
			RouterID:     routerID,
			CreatedAt:    startedAt.UTC(),
			Outcome:      "failed",
			Source:       source,
			Model:        db.NullString{sql.NullString{String: model, Valid: model != ""}},
			Serial:       db.NullString{sql.NullString{String: serial, Valid: serial != ""}},
			OSVersion:    db.NullString{sql.NullString{String: osVersion, Valid: osVersion != ""}},
			ErrorMessage: db.NullString{sql.NullString{String: err.Error(), Valid: true}},
			DurationMS:   durationMS,
		}
		_, _ = s.database.CreateRouterBackup(failRec)
		_, _ = client.SweepTemporaryFiles(ctx, "")
		return failRec, err
	}

	normalizedRSC := NormalizeRSC(rawRSC)
	fingerprint := ComputeFingerprint(normalizedRSC)
	durationMS := int(time.Since(startedAt).Milliseconds())

	// 2. Check for deduplication against latest successful backup
	latest, _ := s.database.GetLatestSuccessfulBackup(routerID)
	if latest != nil && latest.Fingerprint.Valid && latest.Fingerprint.String == fingerprint {
		unchangedRec := &db.RouterBackup{
			RouterID:     routerID,
			CreatedAt:    startedAt.UTC(),
			Outcome:      "unchanged",
			Source:       source,
			Fingerprint:  db.NullString{sql.NullString{String: fingerprint, Valid: true}},
			RSCBytes:     int64(len([]byte(normalizedRSC))),
			Model:        db.NullString{sql.NullString{String: model, Valid: model != ""}},
			Serial:       db.NullString{sql.NullString{String: serial, Valid: serial != ""}},
			OSVersion:    db.NullString{sql.NullString{String: osVersion, Valid: osVersion != ""}},
			DurationMS:   durationMS,
		}
		_, err := s.database.CreateRouterBackup(unchangedRec)
		if err != nil {
			log.Printf("[Backup] Failed to record unchanged backup: %v", err)
		}
		_, _ = client.SweepTemporaryFiles(ctx, "")
		return unchangedRec, nil
	}

	// 3. Changed or initial backup: create encrypted binary system backup (.backup)
	backupPW := routeros.GenerateBackupPassword(24)
	binaryBytes, bErr := client.CreateSystemBackup(ctx, stem, backupPW, 35*time.Second)
	if bErr != nil {
		durationMS := int(time.Since(startedAt).Milliseconds())
		failRec := &db.RouterBackup{
			RouterID:     routerID,
			CreatedAt:    startedAt.UTC(),
			Outcome:      "failed",
			Source:       source,
			Fingerprint:  db.NullString{sql.NullString{String: fingerprint, Valid: true}},
			Model:        db.NullString{sql.NullString{String: model, Valid: model != ""}},
			Serial:       db.NullString{sql.NullString{String: serial, Valid: serial != ""}},
			OSVersion:    db.NullString{sql.NullString{String: osVersion, Valid: osVersion != ""}},
			ErrorMessage: db.NullString{sql.NullString{String: bErr.Error(), Valid: true}},
			DurationMS:   durationMS,
		}
		_, _ = s.database.CreateRouterBackup(failRec)
		_, _ = client.SweepTemporaryFiles(ctx, "")
		return failRec, bErr
	}

	// 4. Store binary backup file on disk
	storageDir := os.Getenv("BACKUP_STORAGE_DIR")
	if storageDir == "" {
		storageDir = "data/backups"
	}
	routerDir := filepath.Join(storageDir, strconv.Itoa(routerID))
	_ = os.MkdirAll(routerDir, 0750)

	fpShort := fingerprint
	if len(fpShort) > 8 {
		fpShort = fpShort[:8]
	}
	relPath := fmt.Sprintf("%d/%s_%s.backup", routerID, stem, fpShort)
	absPath := filepath.Join(storageDir, relPath)
	if err := os.WriteFile(absPath, binaryBytes, 0600); err != nil {
		log.Printf("[Backup] Failed to save binary file to %s: %v", absPath, err)
	}

	durationMS = int(time.Since(startedAt).Milliseconds())

	changedRec := &db.RouterBackup{
		RouterID:       routerID,
		CreatedAt:      startedAt.UTC(),
		Outcome:        "changed",
		Source:         source,
		Fingerprint:    db.NullString{sql.NullString{String: fingerprint, Valid: true}},
		RSCContent:     db.NullString{sql.NullString{String: normalizedRSC, Valid: true}},
		RSCBytes:       int64(len([]byte(normalizedRSC))),
		BackupFilePath: db.NullString{sql.NullString{String: relPath, Valid: true}},
		BackupBytes:    int64(len(binaryBytes)),
		BackupPassword: db.NullString{sql.NullString{String: backupPW, Valid: true}},
		Model:          db.NullString{sql.NullString{String: model, Valid: model != ""}},
		Serial:         db.NullString{sql.NullString{String: serial, Valid: serial != ""}},
		OSVersion:      db.NullString{sql.NullString{String: osVersion, Valid: osVersion != ""}},
		DurationMS:     durationMS,
	}

	_, err = s.database.CreateRouterBackup(changedRec)
	if err != nil {
		log.Printf("[Backup] Failed to insert changed backup record: %v", err)
	}

	// Post-backup sweep
	_, _ = client.SweepTemporaryFiles(ctx, "")

	// 5. Trigger auto-pruning
	_, _ = s.PruneRouterBackups(ctx, routerID, nil, nil)

	return changedRec, nil
}

func (s *BackupService) PruneRouterBackups(ctx context.Context, routerID int, maxCount, maxDays *int) (int, error) {
	effMaxCount := 30
	if maxCount != nil {
		effMaxCount = *maxCount
	} else if val, err := s.database.GetSetting("backup_max_count"); err == nil && val != "" {
		if c, err := strconv.Atoi(val); err == nil && c > 0 {
			effMaxCount = c
		}
	}

	effMaxDays := 90
	if maxDays != nil {
		effMaxDays = *maxDays
	} else if val, err := s.database.GetSetting("backup_retention_days"); err == nil && val != "" {
		if d, err := strconv.Atoi(val); err == nil && d > 0 {
			effMaxDays = d
		}
	}

	unpinned, err := s.database.GetUnpinnedBackups(routerID)
	if err != nil {
		return 0, err
	}

	storageDir := os.Getenv("BACKUP_STORAGE_DIR")
	if storageDir == "" {
		storageDir = "data/backups"
	}

	cutoff := time.Now().UTC().AddDate(0, 0, -effMaxDays)
	pruned := 0

	for idx, b := range unpinned {
		if idx >= effMaxCount || b.CreatedAt.Before(cutoff) {
			if b.BackupFilePath.Valid && b.BackupFilePath.String != "" {
				filePath := filepath.Join(storageDir, b.BackupFilePath.String)
				_ = os.Remove(filePath)
			}
			_ = s.database.DeleteRouterBackup(b.ID)
			pruned++
		}
	}

	if pruned > 0 {
		log.Printf("[Backup] Pruned %d old backups for router %d", pruned, routerID)
	}
	return pruned, nil
}

// BackupScheduler periodically inspects active routers and runs due backups.
type BackupScheduler struct {
	service       *BackupService
	checkInterval time.Duration
	stopCh        chan struct{}
	running       bool
	mu            sync.Mutex
}

func NewBackupScheduler(service *BackupService, checkInterval time.Duration) *BackupScheduler {
	if checkInterval <= 0 {
		checkInterval = 1 * time.Hour
	}
	return &BackupScheduler{
		service:       service,
		checkInterval: checkInterval,
		stopCh:        make(chan struct{}),
	}
}

func (sched *BackupScheduler) Start() {
	sched.mu.Lock()
	if sched.running {
		sched.mu.Unlock()
		return
	}
	sched.running = true
	sched.stopCh = make(chan struct{})
	sched.mu.Unlock()

	go sched.runLoop()
	log.Printf("[BackupScheduler] Started background backup scheduler (interval: %v)", sched.checkInterval)
}

func (sched *BackupScheduler) Stop() {
	sched.mu.Lock()
	defer sched.mu.Unlock()
	if !sched.running {
		return
	}
	sched.running = false
	close(sched.stopCh)
	log.Printf("[BackupScheduler] Stopped background backup scheduler")
}

func (sched *BackupScheduler) runLoop() {
	ticker := time.NewTicker(sched.checkInterval)
	defer ticker.Stop()

	// Initial check after short grace period
	select {
	case <-time.After(10 * time.Second):
		sched.RunDueBackups()
	case <-sched.stopCh:
		return
	}

	for {
		select {
		case <-ticker.C:
			sched.RunDueBackups()
		case <-sched.stopCh:
			return
		}
	}
}

func (sched *BackupScheduler) RunDueBackups() int {
	// Check if backups are enabled globally
	enabledStr, _ := sched.service.database.GetSetting("backup_enabled")
	if strings.EqualFold(enabledStr, "false") || enabledStr == "0" {
		return 0
	}

	intervalHours := 24
	if val, err := sched.service.database.GetSetting("backup_interval_hours"); err == nil && val != "" {
		if h, err := strconv.Atoi(val); err == nil && h > 0 {
			intervalHours = h
		}
	}

	routers, err := sched.service.database.GetRouters()
	if err != nil {
		return 0
	}

	now := time.Now().UTC()
	threshold := now.Add(-time.Duration(intervalHours) * time.Hour)
	dueCount := 0

	for _, r := range routers {
		if !r.IsActive || (r.ArchivedAt != nil && !r.ArchivedAt.IsZero()) {
			continue
		}

		latest, _ := sched.service.database.GetLatestSuccessfulBackup(r.ID)
		isDue := false
		if latest == nil {
			isDue = true
		} else if latest.CreatedAt.Before(threshold) {
			isDue = true
		}

		if isDue {
			log.Printf("[BackupScheduler] Router %d (%s) backup is due. Triggering...", r.ID, r.Name)
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
			_, bErr := sched.service.RunRouterBackup(ctx, r.ID, "scheduled")
			cancel()
			if bErr == nil {
				dueCount++
			} else {
				log.Printf("[BackupScheduler] Scheduled backup for router %d failed: %v", r.ID, bErr)
			}
		}
	}

	return dueCount
}

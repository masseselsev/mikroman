package services

import (
	"context"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/masseselsev/mikroman/internal/crypto"
	"github.com/masseselsev/mikroman/internal/db"
)

func TestBackupNormalizer(t *testing.T) {
	raw := "# 2026-09-11 12:00:00 by RouterOS 7.15\n# software id = 1234\n/ip firewall filter\nadd action=accept chain=input   \r\n\r\n"
	normalized := NormalizeRSC(raw)

	if strings.Contains(normalized, "# 2026-09-11") {
		t.Fatalf("expected volatile timestamp header to be stripped, got:\n%s", normalized)
	}
	if strings.Contains(normalized, "\r") {
		t.Fatalf("expected carriage returns to be stripped, got:\n%s", normalized)
	}
	if strings.HasSuffix(normalized, "   ") {
		t.Fatalf("expected trailing spaces to be stripped")
	}
	if !strings.Contains(normalized, "/ip firewall filter") {
		t.Fatalf("expected firewall rule to remain intact")
	}

	// Stable fingerprint check
	fp1 := ComputeFingerprint(raw)
	raw2 := "# 2026-09-12 18:30:00 by RouterOS 7.16\n# software id = 1234\n/ip firewall filter\nadd action=accept chain=input\n"
	fp2 := ComputeFingerprint(raw2)

	if fp1 != fp2 {
		t.Fatalf("expected identical fingerprints for semantically identical exports across timestamp variations; fp1=%s, fp2=%s", fp1, fp2)
	}
}

func TestDiffEngine(t *testing.T) {
	base := `/interface bridge
add name=bridge1
/ip pool
add name=dhcp-pool ranges=192.0.2.10-192.0.2.100`

	target := `/interface bridge
add name=bridge1
add name=bridge2
/ip pool
add name=dhcp-pool ranges=192.0.2.10-192.0.2.200`

	baseID := 1
	targetID := 2
	res := DiffTexts(base, target, "base.rsc", "target.rsc", 3, &baseID, &targetID, false)

	if res.LinesAdded != 2 {
		t.Fatalf("expected 2 lines added, got %d", res.LinesAdded)
	}
	if res.LinesRemoved != 1 {
		t.Fatalf("expected 1 line removed, got %d", res.LinesRemoved)
	}
	if res.TotalChanges != 3 {
		t.Fatalf("expected 3 total changes, got %d", res.TotalChanges)
	}
	if len(res.Hunks) == 0 {
		t.Fatalf("expected at least 1 hunk, got %d", len(res.Hunks))
	}
	if !strings.Contains(res.RawUnified, "--- base.rsc") {
		t.Fatalf("expected diff header to contain fromfile")
	}
	if !strings.Contains(res.RawUnified, "+add name=bridge2") {
		t.Fatalf("expected diff to show added line")
	}
}

func TestBackupPruning(t *testing.T) {
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test_prune.db")

	fernet, _ := crypto.NewFernet("cw_z4pYJ2-8_9V18R5v6R1XbJ9i9w9G1R1XbJ9i9w9E=")
	database, err := db.Open(dbPath, fernet)
	if err != nil {
		t.Fatalf("failed to open database: %v", err)
	}
	defer database.SqlDB.Close()

	r := &db.Router{
		Name:     "TestRouter",
		Host:     "192.0.2.1",
		Port:     80,
		Username: "admin",
		IsActive: true,
	}
	if err := database.CreateRouter(r); err != nil {
		t.Fatalf("failed to create router: %v", err)
	}
	routerID := r.ID

	svc := NewBackupService(database)

	// Create 5 backups: 2 pinned, 3 unpinned
	for i := 1; i <= 5; i++ {
		isPinned := i <= 2
		b := &db.RouterBackup{
			RouterID:  routerID,
			CreatedAt: time.Now().UTC().Add(-time.Duration(10-i) * 24 * time.Hour),
			Outcome:   "changed",
			Source:    "manual",
			IsPinned:  isPinned,
		}
		_, err := database.CreateRouterBackup(b)
		if err != nil {
			t.Fatalf("failed to insert backup: %v", err)
		}
	}

	// Prune with maxCount = 1
	maxCount := 1
	maxDays := 365
	pruned, err := svc.PruneRouterBackups(context.Background(), routerID, &maxCount, &maxDays)
	if err != nil {
		t.Fatalf("pruning failed: %v", err)
	}

	// 3 unpinned backups, maxCount is 1 -> 2 should be pruned
	if pruned != 2 {
		t.Fatalf("expected 2 unpinned backups pruned, got %d", pruned)
	}

	// Pinned backups must remain
	list, total, _ := database.ListRouterBackups(routerID, nil, false, 1, 50)
	if total != 3 {
		t.Fatalf("expected 3 backups left (2 pinned + 1 unpinned), got %d (list len %d)", total, len(list))
	}

	pinnedCount := 0
	for _, b := range list {
		if b.IsPinned {
			pinnedCount++
		}
	}
	if pinnedCount != 2 {
		t.Fatalf("expected 2 pinned backups to strictly survive pruning, got %d", pinnedCount)
	}
}

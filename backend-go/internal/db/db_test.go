package db

import (
	"database/sql"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/masseselsev/mikroman/internal/crypto"
)

func TestDBSchemaAndOperations(t *testing.T) {
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test.db")

	fernet, err := crypto.NewFernet("cw_z4pYJ2-8_9V18R5v6R1XbJ9i9w9G1R1XbJ9i9w9E=")
	if err != nil {
		t.Fatalf("failed to create Fernet: %v", err)
	}

	database, err := Open(dbPath, fernet)
	if err != nil {
		t.Fatalf("failed to open database: %v", err)
	}
	defer database.Close()

	// 1. Test App Settings
	if err := database.SetSetting("test_key", "test_val", "A test description"); err != nil {
		t.Fatalf("failed to set setting: %v", err)
	}

	val, err := database.GetSetting("test_key")
	if err != nil || val != "test_val" {
		t.Fatalf("expected 'test_val', got %q (err: %v)", val, err)
	}

	// 2. Test Router with encrypted password
	router := &Router{
		Name:      "Home-Gateway",
		Host:      "192.168.1.1",
		Port:      443,
		UseSSL:    true,
		SSLVerify: false,
		Username:  "admin",
		Password:  "P@ssw0rdSecure!",
		IsActive:  true,
		IsDefault: true,
	}
	if err := database.CreateRouter(router); err != nil {
		t.Fatalf("failed to create router: %v", err)
	}

	// Verify raw password in database is encrypted
	var rawPass string
	err = database.SqlDB.QueryRow("SELECT password FROM routers WHERE id = ?", router.ID).Scan(&rawPass)
	if err != nil {
		t.Fatalf("failed to read raw password: %v", err)
	}
	if rawPass == "P@ssw0rdSecure!" || len(rawPass) < 20 {
		t.Fatalf("expected encrypted password, got %q", rawPass)
	}

	// Verify GetRouter decrypts it transparently
	fetchedRouter, err := database.GetRouter(router.ID)
	if err != nil || fetchedRouter == nil {
		t.Fatalf("failed to get router: %v", err)
	}
	if fetchedRouter.Password != "P@ssw0rdSecure!" {
		t.Fatalf("expected decrypted password 'P@ssw0rdSecure!', got %q", fetchedRouter.Password)
	}

	// 3. Test Users
	user := &User{
		RouterID:   &router.ID,
		Name:       "John Doe",
		AvatarIcon: "gamer",
		SpeedLimit: "50M/50M",
		IsPaused:   false,
		Priority:   2,
		SortOrder:  1,
	}
	if err := database.CreateUser(user); err != nil {
		t.Fatalf("failed to create user: %v", err)
	}

	users, err := database.GetUsers(&router.ID)
	if err != nil || len(users) != 1 {
		t.Fatalf("expected 1 user, got %d (err: %v)", len(users), err)
	}
	if users[0].Name != "John Doe" {
		t.Fatalf("expected 'John Doe', got %q", users[0].Name)
	}

	// Test Update User
	user.Name = "Johnathan Doe"
	if err := database.UpdateUser(user); err != nil {
		t.Fatalf("failed to update user: %v", err)
	}
	updatedUser, _ := database.GetUser(user.ID)
	if updatedUser.Name != "Johnathan Doe" {
		t.Fatalf("expected updated name, got %q", updatedUser.Name)
	}

	// 4. Test Delete User
	if err := database.DeleteUser(user.ID); err != nil {
		t.Fatalf("failed to delete user: %v", err)
	}
	deletedUser, _ := database.GetUser(user.ID)
	if deletedUser != nil {
		t.Fatalf("expected user to be deleted, found: %+v", deletedUser)
	}
}

func TestExistingDatabaseRead(t *testing.T) {
	// If a backup DB exists in backups/, test that Go can open and read it!
	backupPath := "../backups/app-20260902-151021.db"
	if _, err := os.Stat(backupPath); os.IsNotExist(err) {
		t.Skip("backup database not present, skipping")
	}

	// Use data/.secret_key if exists
	fernet, _ := crypto.ResolveKey("", "../data")

	database, err := Open(backupPath, fernet)
	if err != nil {
		t.Fatalf("failed to open existing backup database: %v", err)
	}
	defer database.Close()

	routers, err := database.GetRouters()
	if err != nil {
		t.Fatalf("failed to read routers from existing database: %v", err)
	}
	if len(routers) == 0 {
		t.Fatalf("expected at least 1 router in existing database")
	}

	t.Logf("Successfully read %d router(s) from existing production backup DB!", len(routers))
}

func TestNullSerialization(t *testing.T) {
	type Sample struct {
		Name  NullString `json:"name"`
		Count NullInt64  `json:"count"`
	}

	// 1. Valid values
	s1 := Sample{
		Name:  NullString{sql.NullString{String: "Router1", Valid: true}},
		Count: NullInt64{sql.NullInt64{Int64: 42, Valid: true}},
	}
	b1, err := json.Marshal(s1)
	if err != nil {
		t.Fatal(err)
	}
	if string(b1) != `{"name":"Router1","count":42}` {
		t.Fatalf("expected raw values, got %s", string(b1))
	}

	// 2. Invalid (null) values
	s2 := Sample{
		Name:  NullString{sql.NullString{Valid: false}},
		Count: NullInt64{sql.NullInt64{Valid: false}},
	}
	b2, err := json.Marshal(s2)
	if err != nil {
		t.Fatal(err)
	}
	if string(b2) != `{"name":null,"count":null}` {
		t.Fatalf("expected nulls, got %s", string(b2))
	}
}

func TestSpeedTestDBOperations(t *testing.T) {
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test_speedtest.db")

	fernet, err := crypto.NewFernet("cw_z4pYJ2-8_9V18R5v6R1XbJ9i9w9G1R1XbJ9i9w9E=")
	if err != nil {
		t.Fatalf("failed to create Fernet: %v", err)
	}

	database, err := Open(dbPath, fernet)
	if err != nil {
		t.Fatalf("failed to open database: %v", err)
	}
	defer database.Close()

	// Create a router first to satisfy foreign key constraint
	router := &Router{
		Name:      "Test-Router",
		Host:      "192.168.1.1",
		Port:      443,
		UseSSL:    true,
		SSLVerify: false,
		Username:  "admin",
		Password:  "secret",
		IsActive:  true,
		IsDefault: true,
	}
	if err := database.CreateRouter(router); err != nil {
		t.Fatalf("failed to create router: %v", err)
	}

	// Initial latest should be nil
	latest, err := database.GetLatestSpeedTestResult(router.ID)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if latest != nil {
		t.Fatalf("expected nil latest result, got %+v", latest)
	}

	down := 85.5
	up := 42.1
	ping := 12.3
	jitter := 1.5
	loss := 0.0

	res := &SpeedTestResult{
		RouterID:      router.ID,
		DownloadMbps:  &down,
		UploadMbps:    &up,
		PingMs:        &ping,
		JitterMs:      &jitter,
		PacketLossPct: &loss,
		ServerName:    NewNullString("Cloudflare Edge (WAW)"),
		ISP:           NewNullString("ISP Test"),
		Status:        "ok",
	}

	if err := database.InsertSpeedTestResult(res); err != nil {
		t.Fatalf("failed to insert speed test result: %v", err)
	}

	if res.ID <= 0 {
		t.Fatalf("expected positive result ID, got %d", res.ID)
	}

	// Now latest should exist
	latest, err = database.GetLatestSpeedTestResult(router.ID)
	if err != nil {
		t.Fatalf("failed to get latest result: %v", err)
	}
	if latest == nil || latest.DownloadMbps == nil || *latest.DownloadMbps != down {
		t.Fatalf("expected download %.1f, got %+v", down, latest)
	}
	if latest.UploadMbps == nil || *latest.UploadMbps != up {
		t.Fatalf("expected upload %.1f, got %+v", up, latest)
	}

	history, err := database.GetSpeedTestHistory(router.ID, 10)
	if err != nil {
		t.Fatalf("failed to get history: %v", err)
	}
	if len(history) != 1 {
		t.Fatalf("expected 1 history entry, got %d", len(history))
	}
}


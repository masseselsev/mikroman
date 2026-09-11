package db

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	_ "modernc.org/sqlite"

	"github.com/masseselsev/mikroman/internal/crypto"
)

// DB wraps a sql.DB connection with repository methods and cipher.
type DB struct {
	SqlDB  *sql.DB
	Fernet *crypto.Fernet
}

// Open initializes SQLite connection with WAL mode and appropriate pools.
func Open(dbPath string, fernet *crypto.Fernet) (*DB, error) {
	if dbPath == "" {
		dbPath = "app.db"
	}

	// Ensure directory exists
	dir := filepath.Dir(dbPath)
	if dir != "" && dir != "." {
		_ = os.MkdirAll(dir, 0755)
	}

	dsn := fmt.Sprintf("%s?_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)&_pragma=synchronous(NORMAL)&_pragma=foreign_keys(1)", dbPath)
	sqlDB, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("failed to open sqlite database: %w", err)
	}

	// Bounded connection pool: pure Go driver handles concurrency without futex spinning
	sqlDB.SetMaxOpenConns(10)
	sqlDB.SetMaxIdleConns(5)
	sqlDB.SetConnMaxLifetime(time.Hour)

	// Run table creation
	if _, err := sqlDB.Exec(SchemaDDL); err != nil {
		_ = sqlDB.Close()
		return nil, fmt.Errorf("failed to execute schema DDL: %w", err)
	}

	// Apply column migrations for older database files
	if err := migrateColumns(sqlDB); err != nil {
		_ = sqlDB.Close()
		return nil, fmt.Errorf("failed to migrate columns: %w", err)
	}

	// Create indexes after ensuring columns exist
	if _, err := sqlDB.Exec(IndexesDDL); err != nil {
		_ = sqlDB.Close()
		return nil, fmt.Errorf("failed to execute index DDL: %w", err)
	}

	database := &DB{
		SqlDB:  sqlDB,
		Fernet: fernet,
	}

	// Encrypt any plaintext passwords from legacy databases
	if fernet != nil {
		_ = database.encryptLegacySecrets()
	}

	return database, nil
}

func (d *DB) encryptLegacySecrets() error {
	rows, err := d.SqlDB.Query("SELECT id, password FROM routers WHERE password NOT LIKE 'enc:v1:%' AND password != ''")
	if err != nil {
		return err
	}
	defer rows.Close()

	type item struct {
		id   int
		pass string
	}
	var toUpdate []item
	for rows.Next() {
		var it item
		if err := rows.Scan(&it.id, &it.pass); err == nil {
			toUpdate = append(toUpdate, it)
		}
	}
	_ = rows.Close()

	for _, it := range toUpdate {
		if enc, err := d.Fernet.EncryptSecret(it.pass); err == nil && enc != it.pass {
			_, _ = d.SqlDB.Exec("UPDATE routers SET password = ? WHERE id = ?", enc, it.id)
		}
	}
	return nil
}

// Close terminates the database connection pool.
func (d *DB) Close() error {
	return d.SqlDB.Close()
}

// --- App Settings ---

func (d *DB) GetSetting(key string) (string, error) {
	var val string
	err := d.SqlDB.QueryRow("SELECT value FROM app_settings WHERE key = ?", key).Scan(&val)
	if err == sql.ErrNoRows {
		return "", nil
	}
	return val, err
}

func (d *DB) SetSetting(key, val, desc string) error {
	query := `
		INSERT INTO app_settings (key, value, description, updated_at)
		VALUES (?, ?, ?, CURRENT_TIMESTAMP)
		ON CONFLICT(key) DO UPDATE SET
			value = excluded.value,
			description = coalesce(excluded.description, app_settings.description),
			updated_at = CURRENT_TIMESTAMP;
	`
	_, err := d.SqlDB.Exec(query, key, val, desc)
	return err
}

// --- Routers ---

func (d *DB) GetRouters() ([]Router, error) {
	rows, err := d.SqlDB.Query(`
		SELECT id, name, host, port, use_ssl, ssl_verify, ca_cert, username, password,
		       comment, is_active, is_default, serial_number, archived_at, created_at, updated_at
		FROM routers
		ORDER BY is_default DESC, id ASC
	`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var routers []Router
	for rows.Next() {
		var r Router
		var encPass string
		err := rows.Scan(
			&r.ID, &r.Name, &r.Host, &r.Port, &r.UseSSL, &r.SSLVerify, &r.CACert,
			&r.Username, &encPass, &r.Comment, &r.IsActive, &r.IsDefault, &r.SerialNumber,
			&r.ArchivedAt, &r.CreatedAt, &r.UpdatedAt,
		)
		if err != nil {
			return nil, err
		}
		if d.Fernet != nil {
			r.Password = d.Fernet.DecryptSecret(encPass)
		} else {
			r.Password = encPass
		}
		routers = append(routers, r)
	}
	return routers, rows.Err()
}

func (d *DB) GetRouter(id int) (*Router, error) {
	var r Router
	var encPass string
	err := d.SqlDB.QueryRow(`
		SELECT id, name, host, port, use_ssl, ssl_verify, ca_cert, username, password,
		       comment, is_active, is_default, serial_number, archived_at, created_at, updated_at
		FROM routers WHERE id = ?
	`, id).Scan(
		&r.ID, &r.Name, &r.Host, &r.Port, &r.UseSSL, &r.SSLVerify, &r.CACert,
		&r.Username, &encPass, &r.Comment, &r.IsActive, &r.IsDefault, &r.SerialNumber,
		&r.ArchivedAt, &r.CreatedAt, &r.UpdatedAt,
	)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if d.Fernet != nil {
		r.Password = d.Fernet.DecryptSecret(encPass)
	} else {
		r.Password = encPass
	}
	return &r, nil
}

func (d *DB) GetDefaultRouter() (*Router, error) {
	var r Router
	var encPass string
	err := d.SqlDB.QueryRow(`
		SELECT id, name, host, port, use_ssl, ssl_verify, ca_cert, username, password,
		       comment, is_active, is_default, serial_number, archived_at, created_at, updated_at
		FROM routers WHERE is_default = 1 LIMIT 1
	`).Scan(
		&r.ID, &r.Name, &r.Host, &r.Port, &r.UseSSL, &r.SSLVerify, &r.CACert,
		&r.Username, &encPass, &r.Comment, &r.IsActive, &r.IsDefault, &r.SerialNumber,
		&r.ArchivedAt, &r.CreatedAt, &r.UpdatedAt,
	)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if d.Fernet != nil {
		r.Password = d.Fernet.DecryptSecret(encPass)
	} else {
		r.Password = encPass
	}
	return &r, nil
}

func (d *DB) CreateRouter(r *Router) error {
	encPass := r.Password
	if d.Fernet != nil {
		var err error
		encPass, err = d.Fernet.EncryptSecret(r.Password)
		if err != nil {
			return err
		}
	}

	res, err := d.SqlDB.Exec(`
		INSERT INTO routers (name, host, port, use_ssl, ssl_verify, ca_cert, username, password, comment, is_active, is_default)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, r.Name, r.Host, r.Port, r.UseSSL, r.SSLVerify, r.CACert, r.Username, encPass, r.Comment, r.IsActive, r.IsDefault)
	if err != nil {
		return err
	}
	id, err := res.LastInsertId()
	if err == nil {
		r.ID = int(id)
	}
	return err
}

// --- Users ---

func (d *DB) GetUsers(routerID *int) ([]User, error) {
	var query string
	var args []interface{}
	if routerID != nil {
		query = "SELECT id, router_id, name, avatar_icon, speed_limit, is_paused, priority, sort_order, created_at, updated_at FROM users WHERE router_id = ? ORDER BY sort_order ASC, id ASC"
		args = append(args, *routerID)
	} else {
		query = "SELECT id, router_id, name, avatar_icon, speed_limit, is_paused, priority, sort_order, created_at, updated_at FROM users ORDER BY sort_order ASC, id ASC"
	}

	rows, err := d.SqlDB.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var users []User
	for rows.Next() {
		var u User
		if err := rows.Scan(&u.ID, &u.RouterID, &u.Name, &u.AvatarIcon, &u.SpeedLimit, &u.IsPaused, &u.Priority, &u.SortOrder, &u.CreatedAt, &u.UpdatedAt); err != nil {
			return nil, err
		}
		users = append(users, u)
	}
	return users, rows.Err()
}

func (d *DB) GetUser(id int) (*User, error) {
	var u User
	err := d.SqlDB.QueryRow(`
		SELECT id, router_id, name, avatar_icon, speed_limit, is_paused, priority, sort_order, created_at, updated_at
		FROM users WHERE id = ?
	`, id).Scan(&u.ID, &u.RouterID, &u.Name, &u.AvatarIcon, &u.SpeedLimit, &u.IsPaused, &u.Priority, &u.SortOrder, &u.CreatedAt, &u.UpdatedAt)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &u, nil
}

func (d *DB) CreateUser(u *User) error {
	res, err := d.SqlDB.Exec(`
		INSERT INTO users (router_id, name, avatar_icon, speed_limit, is_paused, priority, sort_order)
		VALUES (?, ?, ?, ?, ?, ?, ?)
	`, u.RouterID, u.Name, u.AvatarIcon, u.SpeedLimit, u.IsPaused, u.Priority, u.SortOrder)
	if err != nil {
		return err
	}
	id, err := res.LastInsertId()
	if err == nil {
		u.ID = int(id)
	}
	return err
}

func (d *DB) UpdateUser(u *User) error {
	_, err := d.SqlDB.Exec(`
		UPDATE users SET
			router_id = ?, name = ?, avatar_icon = ?, speed_limit = ?,
			is_paused = ?, priority = ?, sort_order = ?, updated_at = CURRENT_TIMESTAMP
		WHERE id = ?
	`, u.RouterID, u.Name, u.AvatarIcon, u.SpeedLimit, u.IsPaused, u.Priority, u.SortOrder, u.ID)
	return err
}

func (d *DB) DeleteUser(id int) error {
	_, err := d.SqlDB.Exec("DELETE FROM users WHERE id = ?", id)
	return err
}

// --- Devices ---

func (d *DB) GetDevices(routerID *int) ([]Device, error) {
	var query string
	var args []interface{}
	if routerID != nil {
		query = `SELECT id, user_id, router_id, mac_address, ip_address, hostname, custom_name,
		                vendor, last_interface, last_wifi_signal, is_active, is_hidden, is_deleted,
		                linked_to_device_id, connection_kind, is_container, speed_limit, is_paused,
		                priority, last_seen
		         FROM devices WHERE router_id = ? AND is_deleted = 0 ORDER BY last_seen DESC`
		args = append(args, *routerID)
	} else {
		query = `SELECT id, user_id, router_id, mac_address, ip_address, hostname, custom_name,
		                vendor, last_interface, last_wifi_signal, is_active, is_hidden, is_deleted,
		                linked_to_device_id, connection_kind, is_container, speed_limit, is_paused,
		                priority, last_seen
		         FROM devices WHERE is_deleted = 0 ORDER BY last_seen DESC`
	}

	rows, err := d.SqlDB.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var devices []Device
	for rows.Next() {
		var dev Device
		if err := rows.Scan(
			&dev.ID, &dev.UserID, &dev.RouterID, &dev.MacAddress, &dev.IPAddress, &dev.Hostname,
			&dev.CustomName, &dev.Vendor, &dev.LastInterface, &dev.LastWifiSignal, &dev.IsActive,
			&dev.IsHidden, &dev.IsDeleted, &dev.LinkedToDeviceID, &dev.ConnectionKind, &dev.IsContainer,
			&dev.SpeedLimit, &dev.IsPaused, &dev.Priority, &dev.LastSeen,
		); err != nil {
			return nil, err
		}
		dev.IsRandomizedMac = IsRandomizedMAC(dev.MacAddress)
		devices = append(devices, dev)
	}
	if devices == nil {
		devices = []Device{}
	}
	return devices, rows.Err()
}

func (d *DB) GetDeviceByMAC(mac string) (*Device, error) {
	var dev Device
	mac = strings.ToUpper(strings.TrimSpace(mac))
	err := d.SqlDB.QueryRow(`
		SELECT id, user_id, router_id, mac_address, ip_address, hostname, custom_name,
		       vendor, last_interface, last_wifi_signal, is_active, is_hidden, is_deleted,
		       linked_to_device_id, connection_kind, is_container, speed_limit, is_paused,
		       priority, last_seen
		FROM devices WHERE UPPER(mac_address) = ? LIMIT 1
	`, mac).Scan(
		&dev.ID, &dev.UserID, &dev.RouterID, &dev.MacAddress, &dev.IPAddress, &dev.Hostname,
		&dev.CustomName, &dev.Vendor, &dev.LastInterface, &dev.LastWifiSignal, &dev.IsActive,
		&dev.IsHidden, &dev.IsDeleted, &dev.LinkedToDeviceID, &dev.ConnectionKind, &dev.IsContainer,
		&dev.SpeedLimit, &dev.IsPaused, &dev.Priority, &dev.LastSeen,
	)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	dev.IsRandomizedMac = IsRandomizedMAC(dev.MacAddress)
	return &dev, nil
}

// VolumeStats holds aggregated traffic volumes across multiple time horizons.
type VolumeStats struct {
	TotalIn  int64
	TotalOut int64
	CycleIn  int64
	CycleOut int64
	TodayIn  int64
	TodayOut int64
}

// GetDeviceVolumeStats computes all-time, cycle, and today volume totals per device.
func (d *DB) GetDeviceVolumeStats(cycleStartDate, today string) (map[int]VolumeStats, error) {
	rows, err := d.SqlDB.Query(`
		SELECT device_id,
		       COALESCE(SUM(bytes_in), 0) AS total_in,
		       COALESCE(SUM(bytes_out), 0) AS total_out,
		       COALESCE(SUM(CASE WHEN record_date >= ? THEN bytes_in ELSE 0 END), 0) AS cycle_in,
		       COALESCE(SUM(CASE WHEN record_date >= ? THEN bytes_out ELSE 0 END), 0) AS cycle_out,
		       COALESCE(SUM(CASE WHEN record_date = ? THEN bytes_in ELSE 0 END), 0) AS today_in,
		       COALESCE(SUM(CASE WHEN record_date = ? THEN bytes_out ELSE 0 END), 0) AS today_out
		FROM device_traffic_rollups
		GROUP BY device_id
	`, cycleStartDate, cycleStartDate, today, today)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	res := make(map[int]VolumeStats)
	for rows.Next() {
		var devID int
		var s VolumeStats
		if err := rows.Scan(&devID, &s.TotalIn, &s.TotalOut, &s.CycleIn, &s.CycleOut, &s.TodayIn, &s.TodayOut); err == nil {
			res[devID] = s
		}
	}
	return res, rows.Err()
}

// GetUserVolumeStats computes all-time, cycle, and today volume totals per user.
func (d *DB) GetUserVolumeStats(cycleStartDate, today string) (map[int]VolumeStats, error) {
	rows, err := d.SqlDB.Query(`
		SELECT user_id,
		       COALESCE(SUM(bytes_in), 0) AS total_in,
		       COALESCE(SUM(bytes_out), 0) AS total_out,
		       COALESCE(SUM(CASE WHEN record_date >= ? THEN bytes_in ELSE 0 END), 0) AS cycle_in,
		       COALESCE(SUM(CASE WHEN record_date >= ? THEN bytes_out ELSE 0 END), 0) AS cycle_out,
		       COALESCE(SUM(CASE WHEN record_date = ? THEN bytes_in ELSE 0 END), 0) AS today_in,
		       COALESCE(SUM(CASE WHEN record_date = ? THEN bytes_out ELSE 0 END), 0) AS today_out
		FROM traffic_rollups
		GROUP BY user_id
	`, cycleStartDate, cycleStartDate, today, today)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	res := make(map[int]VolumeStats)
	for rows.Next() {
		var userID int
		var s VolumeStats
		if err := rows.Scan(&userID, &s.TotalIn, &s.TotalOut, &s.CycleIn, &s.CycleOut, &s.TodayIn, &s.TodayOut); err == nil {
			res[userID] = s
		}
	}
	return res, rows.Err()
}

// CalculateBillingCycleStart returns YYYY-MM-DD for the current cycle start date.
func CalculateBillingCycleStart(anchorDay int, ref time.Time) string {
	if anchorDay < 1 {
		anchorDay = 1
	} else if anchorDay > 31 {
		anchorDay = 31
	}

	year, month, day := ref.Date()
	var cycleYear int
	var cycleMonth time.Month

	if day >= anchorDay {
		cycleYear = year
		cycleMonth = month
	} else {
		if month == time.January {
			cycleYear = year - 1
			cycleMonth = time.December
		} else {
			cycleYear = year
			cycleMonth = month - 1
		}
	}

	lastDay := time.Date(cycleYear, cycleMonth+1, 0, 0, 0, 0, 0, time.UTC).Day()
	actualDay := anchorDay
	if actualDay > lastDay {
		actualDay = lastDay
	}

	return fmt.Sprintf("%04d-%02d-%02d", cycleYear, int(cycleMonth), actualDay)
}

// CalculateBillingCycleBounds calculates start and end instants of an ISP billing cycle.
func CalculateBillingCycleBounds(anchorDay, anchorHour, anchorMinute int, ref time.Time, previous bool) (time.Time, time.Time) {
	day := max(1, min(anchorDay, 31))
	hh := max(0, min(anchorHour, 23))
	mm := max(0, min(anchorMinute, 59))

	resetOn := func(year int, month time.Month) time.Time {
		lastDay := time.Date(year, month+1, 0, 0, 0, 0, 0, ref.Location()).Day()
		actualDay := min(day, lastDay)
		return time.Date(year, month, actualDay, hh, mm, 0, 0, ref.Location())
	}

	thisMonth := resetOn(ref.Year(), ref.Month())
	var start time.Time
	if !ref.Before(thisMonth) {
		start = thisMonth
	} else if ref.Month() == time.January {
		start = resetOn(ref.Year()-1, time.December)
	} else {
		start = resetOn(ref.Year(), ref.Month()-1)
	}

	var end time.Time
	if start.Month() == time.December {
		end = resetOn(start.Year()+1, time.January)
	} else {
		end = resetOn(start.Year(), start.Month()+1)
	}

	if previous {
		prevEnd := start
		var prevStart time.Time
		if start.Month() == time.January {
			prevStart = resetOn(start.Year()-1, time.December)
		} else {
			prevStart = resetOn(start.Year(), start.Month()-1)
		}
		return prevStart, prevEnd
	}

	return start, end
}

// GetBillingAnchorDay reads billing_cycle_anchor_day from app_settings, default 1.
func (d *DB) GetBillingAnchorDay(routerID ...*int) int {
	var rID *int
	if len(routerID) > 0 {
		rID = routerID[0]
	}

	if rID != nil {
		if val, err := d.GetSetting(fmt.Sprintf("billing_cycle_anchor_day_%d", *rID)); err == nil && val != "" {
			if day, err := strconv.Atoi(val); err == nil && day >= 1 && day <= 31 {
				return day
			}
		}
	}

	val, err := d.GetSetting("billing_cycle_anchor_day")
	if err != nil || val == "" {
		val, _ = d.GetSetting("isp_billing_cycle_anchor_day")
	}
	if val == "" {
		val, _ = d.GetSetting("isp_monthly_quota_anchor_day")
	}
	if day, err := strconv.Atoi(val); err == nil && day >= 1 && day <= 31 {
		return day
	}
	return 1
}

// GetBillingAnchorTime reads reset hour and minute (0-23, 0-59) from app_settings, default (0, 0).
func (d *DB) GetBillingAnchorTime(routerID ...*int) (int, int) {
	var rID *int
	if len(routerID) > 0 {
		rID = routerID[0]
	}

	hour := 0
	minute := 0

	hKey := "billing_cycle_anchor_hour"
	mKey := "billing_cycle_anchor_minute"
	if rID != nil {
		hKey = fmt.Sprintf("billing_cycle_anchor_hour_%d", *rID)
		mKey = fmt.Sprintf("billing_cycle_anchor_minute_%d", *rID)
	}

	hVal, _ := d.GetSetting(hKey)
	if hVal == "" && rID != nil {
		hVal, _ = d.GetSetting("billing_cycle_anchor_hour")
	}
	if h, err := strconv.Atoi(hVal); err == nil && h >= 0 && h <= 23 {
		hour = h
	}

	mVal, _ := d.GetSetting(mKey)
	if mVal == "" && rID != nil {
		mVal, _ = d.GetSetting("billing_cycle_anchor_minute")
	}
	if m, err := strconv.Atoi(mVal); err == nil && m >= 0 && m <= 59 {
		minute = m
	}

	return hour, minute
}

// SetBillingAnchorConfig persists the ISP billing cycle day and time.
func (d *DB) SetBillingAnchorConfig(day, hour, minute int, routerID *int) error {
	day = max(1, min(day, 31))
	hour = max(0, min(hour, 23))
	minute = max(0, min(minute, 59))

	dayKey := "billing_cycle_anchor_day"
	hourKey := "billing_cycle_anchor_hour"
	minKey := "billing_cycle_anchor_minute"
	if routerID != nil {
		dayKey = fmt.Sprintf("billing_cycle_anchor_day_%d", *routerID)
		hourKey = fmt.Sprintf("billing_cycle_anchor_hour_%d", *routerID)
		minKey = fmt.Sprintf("billing_cycle_anchor_minute_%d", *routerID)
	}

	_ = d.SetSetting(dayKey, strconv.Itoa(day), "ISP billing cycle monthly anchor day (1-31)")
	_ = d.SetSetting(hourKey, strconv.Itoa(hour), "ISP billing cycle reset hour (0-23)")
	_ = d.SetSetting(minKey, strconv.Itoa(minute), "ISP billing cycle reset minute (0-59)")

	if routerID != nil {
		_ = d.SetSetting("billing_cycle_anchor_day", strconv.Itoa(day), "ISP billing cycle monthly anchor day (1-31)")
		_ = d.SetSetting("billing_cycle_anchor_hour", strconv.Itoa(hour), "ISP billing cycle reset hour (0-23)")
		_ = d.SetSetting("billing_cycle_anchor_minute", strconv.Itoa(minute), "ISP billing cycle reset minute (0-59)")
	}

	return nil
}

func migrateColumns(db *sql.DB) error {
	migrations := []struct {
		table string
		col   string
		ctype string
	}{
		{"routers", "serial_number", "VARCHAR(120)"},
		{"routers", "archived_at", "DATETIME"},
		{"routers", "comment", "TEXT"},
		{"routers", "ca_cert", "TEXT"},
		{"routers", "is_default", "BOOLEAN NOT NULL DEFAULT 0"},
		{"users", "router_id", "INTEGER REFERENCES routers(id) ON DELETE CASCADE"},
		{"users", "sort_order", "INTEGER NOT NULL DEFAULT 0"},
		{"devices", "is_deleted", "BOOLEAN NOT NULL DEFAULT 0"},
		{"devices", "is_hidden", "BOOLEAN NOT NULL DEFAULT 0"},
		{"devices", "linked_to_device_id", "INTEGER REFERENCES devices(id) ON DELETE SET NULL"},
		{"devices", "connection_kind", "VARCHAR(20)"},
		{"devices", "is_container", "BOOLEAN NOT NULL DEFAULT 0"},
		{"devices", "wifi_links", "JSON"},
		{"router_logs", "external_id", "VARCHAR(32)"},
		{"router_logs", "severity", "VARCHAR(20) NOT NULL DEFAULT 'info'"},
		{"router_logs", "category", "VARCHAR(50) NOT NULL DEFAULT 'system'"},
	}

	for _, m := range migrations {
		if err := ensureColumn(db, m.table, m.col, m.ctype); err != nil {
			return err
		}
	}

	uniqueIndexes := []string{
		"CREATE UNIQUE INDEX IF NOT EXISTS uq_traffic_rollups_user_date ON traffic_rollups (user_id, record_date)",
		"CREATE UNIQUE INDEX IF NOT EXISTS uq_device_traffic_rollups_dev_date ON device_traffic_rollups (device_id, record_date)",
		"CREATE UNIQUE INDEX IF NOT EXISTS uq_router_traffic_rollups_rtr_date ON router_traffic_rollups (router_id, record_date)",
		"CREATE UNIQUE INDEX IF NOT EXISTS uq_router_self_traffic_rollups_rtr_date ON router_self_traffic_rollups (router_id, record_date)",
	}
	for _, sqlStmt := range uniqueIndexes {
		_, _ = db.Exec(sqlStmt)
	}

	return nil
}

func ensureColumn(db *sql.DB, table, col, colType string) error {
	rows, err := db.Query(fmt.Sprintf("PRAGMA table_info(%s)", table))
	if err != nil {
		return err
	}
	defer rows.Close()

	for rows.Next() {
		var cid int
		var name, ctype string
		var notnull, pk int
		var dfltValue sql.NullString
		if err := rows.Scan(&cid, &name, &ctype, &notnull, &dfltValue, &pk); err == nil {
			if strings.EqualFold(name, col) {
				return nil // already exists
			}
		}
	}

	_, err = db.Exec(fmt.Sprintf("ALTER TABLE %s ADD COLUMN %s %s", table, col, colType))
	return err
}


package db

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
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
		devices = append(devices, dev)
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
	return &dev, nil
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


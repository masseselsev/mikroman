package db

import (
	"database/sql"
	"time"
)

// Router represents a MikroTik router managed by MikroMan.
type Router struct {
	ID           int            `json:"id"`
	Name         string         `json:"name"`
	Host         string         `json:"host"`
	Port         int            `json:"port"`
	UseSSL       bool           `json:"use_ssl"`
	SSLVerify    bool           `json:"ssl_verify"`
	CACert       sql.NullString `json:"ca_cert"`
	Username     string         `json:"username"`
	Password     string         `json:"-"` // Decrypted in memory, never exposed in JSON
	Comment      sql.NullString `json:"comment"`
	IsActive     bool           `json:"is_active"`
	IsDefault    bool           `json:"is_default"`
	SerialNumber sql.NullString `json:"serial_number"`
	ArchivedAt   *time.Time     `json:"archived_at,omitempty"`
	CreatedAt    time.Time      `json:"created_at"`
	UpdatedAt    time.Time      `json:"updated_at"`
}

// User represents a household member or user group owning devices.
type User struct {
	ID         int       `json:"id"`
	RouterID   *int      `json:"router_id,omitempty"`
	Name       string    `json:"name"`
	AvatarIcon string    `json:"avatar_icon"`
	SpeedLimit string    `json:"speed_limit"`
	IsPaused   bool      `json:"is_paused"`
	Priority   int       `json:"priority"`
	SortOrder  int       `json:"sort_order"`
	CreatedAt  time.Time `json:"created_at"`
	UpdatedAt  time.Time `json:"updated_at"`

	// Joined relations
	Devices        []Device        `json:"devices,omitempty"`
	TrafficRollups []TrafficRollup `json:"traffic_rollups,omitempty"`
}

// Device represents a network client seen on RouterOS.
type Device struct {
	ID               int            `json:"id"`
	UserID           *int           `json:"user_id,omitempty"`
	RouterID         *int           `json:"router_id,omitempty"`
	MacAddress       string         `json:"mac_address"`
	IPAddress        sql.NullString `json:"ip_address"`
	Hostname         sql.NullString `json:"hostname"`
	CustomName       sql.NullString `json:"custom_name"`
	Vendor           sql.NullString `json:"vendor"`
	LastInterface    sql.NullString `json:"last_interface"`
	LastWifiSignal   sql.NullInt64  `json:"last_wifi_signal"`
	IsActive         bool           `json:"is_active"`
	IsHidden         bool           `json:"is_hidden"`
	IsDeleted        bool           `json:"is_deleted"`
	LinkedToDeviceID *int           `json:"linked_to_device_id,omitempty"`
	ConnectionKind   sql.NullString `json:"connection_kind"`
	IsContainer      bool           `json:"is_container"`
	SpeedLimit       string         `json:"speed_limit"`
	IsPaused         bool           `json:"is_paused"`
	Priority         int            `json:"priority"`
	LastSeen         time.Time      `json:"last_seen"`
}

// DeviceHistory logs MAC, IP, hostname changes, and merges.
type DeviceHistory struct {
	ID         int            `json:"id"`
	DeviceID   int            `json:"device_id"`
	MacAddress string         `json:"mac_address"`
	Hostname   sql.NullString `json:"hostname"`
	IPAddress  sql.NullString `json:"ip_address"`
	EventType  string         `json:"event_type"`
	Details    sql.NullString `json:"details"`
	CreatedAt  time.Time      `json:"created_at"`
}

// DeviceCoexistence tracks two MACs seen online simultaneously.
type DeviceCoexistence struct {
	ID                int            `json:"id"`
	MacA              string         `json:"mac_a"`
	MacB              string         `json:"mac_b"`
	Hostname          sql.NullString `json:"hostname"`
	FirstSeenTogether time.Time      `json:"first_seen_together"`
	LastSeenTogether  time.Time      `json:"last_seen_together"`
	Observations      int            `json:"observations"`
}

// TrafficRollup tracks daily aggregated traffic per user.
type TrafficRollup struct {
	ID         int       `json:"id"`
	UserID     int       `json:"user_id"`
	RecordDate string    `json:"record_date"` // YYYY-MM-DD
	BytesIn    int64     `json:"bytes_in"`    // Download
	BytesOut   int64     `json:"bytes_out"`   // Upload
}

// DeviceTrafficRollup tracks daily aggregated traffic per device.
type DeviceTrafficRollup struct {
	ID         int    `json:"id"`
	DeviceID   int    `json:"device_id"`
	RecordDate string `json:"record_date"` // YYYY-MM-DD
	BytesIn    int64  `json:"bytes_in"`
	BytesOut   int64  `json:"bytes_out"`
}

// UserTrafficBucket tracks 30-minute traffic snapshots for intraday charts.
type UserTrafficBucket struct {
	ID          int       `json:"id"`
	UserID      int       `json:"user_id"`
	BucketStart time.Time `json:"bucket_start"`
	BytesIn     int64     `json:"bytes_in"`
	BytesOut    int64     `json:"bytes_out"`
}

// AppSetting key-value store for app configuration and hashes.
type AppSetting struct {
	Key         string    `json:"key"`
	Value       string    `json:"value"`
	Description string    `json:"description"`
	UpdatedAt   time.Time `json:"updated_at"`
}

// RouterLog holds mirrored RouterOS syslog events.
type RouterLog struct {
	ID        int       `json:"id"`
	RouterID  int       `json:"router_id"`
	Timestamp time.Time `json:"timestamp"`
	Topics    string    `json:"topics"`
	Message   string    `json:"message"`
}

// RouterBackup holds router configuration snapshots.
type RouterBackup struct {
	ID         int       `json:"id"`
	RouterID   int       `json:"router_id"`
	BackupType string    `json:"backup_type"`
	FilePath   string    `json:"file_path"`
	FileSize   int64     `json:"file_size"`
	CreatedAt  time.Time `json:"created_at"`
	Details    string    `json:"details"`
}

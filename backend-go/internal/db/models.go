package db

import (
	"database/sql"
	"database/sql/driver"
	"encoding/json"
	"strconv"
	"strings"
	"time"
)

// NullString wraps sql.NullString to serialize to JSON as a string or null.
type NullString struct {
	sql.NullString
}

// NewNullString creates a valid NullString if s is not empty.
func NewNullString(s string) NullString {
	return NullString{sql.NullString{String: s, Valid: s != ""}}
}

// MarshalJSON returns the string value or null if invalid.
func (ns NullString) MarshalJSON() ([]byte, error) {
	if !ns.Valid {
		return []byte("null"), nil
	}
	return json.Marshal(ns.String)
}

// UnmarshalJSON parses a JSON string or null into NullString.
func (ns *NullString) UnmarshalJSON(data []byte) error {
	if string(data) == "null" {
		ns.String = ""
		ns.Valid = false
		return nil
	}
	var s string
	if err := json.Unmarshal(data, &s); err != nil {
		return err
	}
	ns.String = s
	ns.Valid = true
	return nil
}

// Scan implements the Scanner interface for database/sql.
func (ns *NullString) Scan(value interface{}) error {
	return ns.NullString.Scan(value)
}

// Value implements the driver.Valuer interface.
func (ns NullString) Value() (driver.Value, error) {
	if !ns.Valid {
		return nil, nil
	}
	return ns.String, nil
}

// NullInt64 wraps sql.NullInt64 to serialize to JSON as an int64 or null.
type NullInt64 struct {
	sql.NullInt64
}

// MarshalJSON returns the int64 value or null if invalid.
func (ni NullInt64) MarshalJSON() ([]byte, error) {
	if !ni.Valid {
		return []byte("null"), nil
	}
	return json.Marshal(ni.Int64)
}

// UnmarshalJSON parses a JSON number or null into NullInt64.
func (ni *NullInt64) UnmarshalJSON(data []byte) error {
	if string(data) == "null" {
		ni.Int64 = 0
		ni.Valid = false
		return nil
	}
	var i int64
	if err := json.Unmarshal(data, &i); err != nil {
		return err
	}
	ni.Int64 = i
	ni.Valid = true
	return nil
}

// Scan implements the Scanner interface for database/sql.
func (ni *NullInt64) Scan(value interface{}) error {
	return ni.NullInt64.Scan(value)
}

// Value implements the driver.Valuer interface.
func (ni NullInt64) Value() (driver.Value, error) {
	if !ni.Valid {
		return nil, nil
	}
	return ni.Int64, nil
}

// Router represents a MikroTik router managed by MikroMan.
type Router struct {
	ID           int        `json:"id"`
	Name         string     `json:"name"`
	Host         string     `json:"host"`
	Port         int        `json:"port"`
	UseSSL       bool       `json:"use_ssl"`
	SSLVerify    bool       `json:"ssl_verify"`
	CACert       NullString `json:"ca_cert"`
	Username     string     `json:"username"`
	Password     string     `json:"-"` // Decrypted in memory, never exposed in JSON
	Comment      NullString `json:"comment"`
	IsActive     bool       `json:"is_active"`
	IsDefault    bool       `json:"is_default"`
	SerialNumber NullString `json:"serial_number"`
	ArchivedAt   *time.Time `json:"archived_at,omitempty"`
	CreatedAt    time.Time  `json:"created_at"`
	UpdatedAt    time.Time  `json:"updated_at"`
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
	Devices        []Device        `json:"devices"`
	TrafficRollups []TrafficRollup `json:"traffic_rollups,omitempty"`

	// Real-time computed fields
	CurrentRateIn  int64      `json:"current_rate_in"`
	CurrentRateOut int64      `json:"current_rate_out"`
	BytesTodayIn   int64      `json:"bytes_today_in"`
	BytesTodayOut  int64      `json:"bytes_today_out"`
	BytesTotalIn   int64      `json:"bytes_total_in"`
	BytesTotalOut  int64      `json:"bytes_total_out"`
	BytesCycleIn   int64      `json:"bytes_cycle_in"`
	BytesCycleOut  int64      `json:"bytes_cycle_out"`
	LastSeen       *time.Time `json:"last_seen,omitempty"`
}

// Device represents a network client seen on RouterOS.
type Device struct {
	ID               int        `json:"id"`
	UserID           *int       `json:"user_id,omitempty"`
	RouterID         *int       `json:"router_id,omitempty"`
	MacAddress       string     `json:"mac_address"`
	IPAddress        NullString `json:"ip_address"`
	Hostname         NullString `json:"hostname"`
	CustomName       NullString `json:"custom_name"`
	Vendor           NullString `json:"vendor"`
	LastInterface    NullString `json:"last_interface"`
	LastWifiSignal   NullInt64  `json:"last_wifi_signal"`
	IsActive         bool       `json:"is_active"`
	IsHidden         bool       `json:"is_hidden"`
	IsDeleted        bool       `json:"is_deleted"`
	LinkedToDeviceID *int       `json:"linked_to_device_id,omitempty"`
	ConnectionKind   NullString `json:"connection_kind"`
	IsContainer      bool       `json:"is_container"`
	SpeedLimit       string     `json:"speed_limit"`
	IsPaused         bool       `json:"is_paused"`
	Priority         int        `json:"priority"`
	LastSeen         time.Time  `json:"last_seen"`

	// Real-time computed fields
	CurrentRateIn   int64 `json:"current_rate_in"`
	CurrentRateOut  int64 `json:"current_rate_out"`
	BytesTodayIn    int64 `json:"bytes_today_in"`
	BytesTodayOut   int64 `json:"bytes_today_out"`
	BytesTotalIn    int64 `json:"bytes_total_in"`
	BytesTotalOut   int64 `json:"bytes_total_out"`
	BytesCycleIn    int64 `json:"bytes_cycle_in"`
	BytesCycleOut   int64 `json:"bytes_cycle_out"`
	IsRandomizedMac bool  `json:"is_randomized_mac"`
}

// IsRandomizedMAC checks if the IEEE 802 Locally Administered bit is set.
func IsRandomizedMAC(mac string) bool {
	if len(mac) < 2 {
		return false
	}
	parts := strings.Split(mac, ":")
	if len(parts) > 0 {
		if val, err := strconv.ParseUint(parts[0], 16, 8); err == nil {
			return (val & 0x02) != 0
		}
	}
	return false
}

// DeviceHistory logs MAC, IP, hostname changes, and merges.
type DeviceHistory struct {
	ID         int        `json:"id"`
	DeviceID   int        `json:"device_id"`
	MacAddress string     `json:"mac_address"`
	Hostname   NullString `json:"hostname"`
	IPAddress  NullString `json:"ip_address"`
	EventType  string     `json:"event_type"`
	Details    NullString `json:"details"`
	CreatedAt  time.Time  `json:"created_at"`
}

// DeviceCoexistence tracks two MACs seen online simultaneously.
type DeviceCoexistence struct {
	ID                int        `json:"id"`
	MacA              string     `json:"mac_a"`
	MacB              string     `json:"mac_b"`
	Hostname          NullString `json:"hostname"`
	FirstSeenTogether time.Time  `json:"first_seen_together"`
	LastSeenTogether  time.Time  `json:"last_seen_together"`
	Observations      int        `json:"observations"`
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

// RouterBackup holds router configuration snapshots and binary backups.
type RouterBackup struct {
	ID             int        `json:"id"`
	RouterID       int        `json:"router_id"`
	CreatedAt      time.Time  `json:"created_at"`
	Outcome        string     `json:"outcome"`
	Source         string     `json:"source"`
	Fingerprint    NullString `json:"fingerprint"`
	RSCContent     NullString `json:"rsc_content,omitempty"`
	RSCBytes       int64      `json:"rsc_bytes"`
	BackupFilePath NullString `json:"backup_file_path"`
	BackupBytes    int64      `json:"backup_bytes"`
	BackupPassword NullString `json:"backup_password,omitempty"`
	IsPinned       bool       `json:"is_pinned"`
	Note           NullString `json:"note"`
	Model          NullString `json:"model"`
	Serial         NullString `json:"serial"`
	OSVersion      NullString `json:"os_version"`
	ErrorMessage   NullString `json:"error_message"`
	DurationMS     int        `json:"duration_ms"`
}

// SpeedTestResult tracks Ookla WAN speed test results run via container on RouterOS.
type SpeedTestResult struct {
	ID            int        `json:"id"`
	RouterID      int        `json:"router_id"`
	CreatedAt     time.Time  `json:"created_at"`
	DownloadMbps  *float64   `json:"download_mbps"`
	UploadMbps    *float64   `json:"upload_mbps"`
	PingMs        *float64   `json:"ping_ms"`
	JitterMs      *float64   `json:"jitter_ms"`
	PacketLossPct *float64   `json:"packet_loss_pct"`
	ServerName    NullString `json:"server_name"`
	ISP           NullString `json:"isp"`
	ResultURL     NullString `json:"result_url"`
	Status        string     `json:"status"`
	Error         NullString `json:"error"`
	RawOutput     string     `json:"raw_output"`
}

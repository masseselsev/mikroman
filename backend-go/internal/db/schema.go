package db

const SchemaDDL = `
CREATE TABLE IF NOT EXISTS routers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(100) NOT NULL UNIQUE,
    host VARCHAR(255) NOT NULL,
    port INTEGER NOT NULL DEFAULT 443,
    use_ssl BOOLEAN NOT NULL DEFAULT 1,
    ssl_verify BOOLEAN NOT NULL DEFAULT 0,
    ca_cert TEXT,
    username VARCHAR(100) NOT NULL DEFAULT 'admin',
    password VARCHAR(500) NOT NULL DEFAULT '',
    comment TEXT,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    is_default BOOLEAN NOT NULL DEFAULT 0,
    serial_number VARCHAR(120),
    archived_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    router_id INTEGER REFERENCES routers(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    avatar_icon VARCHAR(50) NOT NULL DEFAULT 'user',
    speed_limit VARCHAR(50) NOT NULL DEFAULT 'unlimited',
    is_paused BOOLEAN NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    router_id INTEGER REFERENCES routers(id) ON DELETE SET NULL,
    mac_address VARCHAR(17) NOT NULL UNIQUE,
    ip_address VARCHAR(45),
    hostname VARCHAR(255),
    custom_name VARCHAR(255),
    vendor VARCHAR(255),
    last_interface VARCHAR(100),
    last_wifi_signal INTEGER,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    is_hidden BOOLEAN NOT NULL DEFAULT 0,
    is_deleted BOOLEAN NOT NULL DEFAULT 0,
    linked_to_device_id INTEGER REFERENCES devices(id) ON DELETE SET NULL,
    connection_kind VARCHAR(20),
    is_container BOOLEAN NOT NULL DEFAULT 0,
    wifi_links JSON,
    speed_limit VARCHAR(50) NOT NULL DEFAULT 'default',
    is_paused BOOLEAN NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 1,
    last_seen DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS device_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    mac_address VARCHAR(17) NOT NULL,
    hostname VARCHAR(255),
    ip_address VARCHAR(45),
    event_type VARCHAR(50) NOT NULL,
    details VARCHAR(500),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS device_coexistence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mac_a VARCHAR(17) NOT NULL,
    mac_b VARCHAR(17) NOT NULL,
    hostname VARCHAR(255),
    first_seen_together DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_together DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    observations INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT uq_device_coexistence_pair UNIQUE (mac_a, mac_b)
);

CREATE TABLE IF NOT EXISTS traffic_rollups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    record_date DATE NOT NULL,
    bytes_in BIGINT NOT NULL DEFAULT 0,
    bytes_out BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS device_traffic_rollups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    record_date DATE NOT NULL,
    bytes_in BIGINT NOT NULL DEFAULT 0,
    bytes_out BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS user_traffic_buckets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    bucket_start DATETIME NOT NULL,
    bytes_in BIGINT NOT NULL DEFAULT 0,
    bytes_out BIGINT NOT NULL DEFAULT 0,
    CONSTRAINT uq_user_traffic_bucket UNIQUE (user_id, bucket_start)
);

CREATE TABLE IF NOT EXISTS device_traffic_buckets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    bucket_start DATETIME NOT NULL,
    bytes_in BIGINT NOT NULL DEFAULT 0,
    bytes_out BIGINT NOT NULL DEFAULT 0,
    CONSTRAINT uq_device_traffic_bucket UNIQUE (device_id, bucket_start)
);

CREATE TABLE IF NOT EXISTS user_destination_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    device_id INTEGER REFERENCES devices(id) ON DELETE SET NULL,
    destination_ip VARCHAR(45) NOT NULL,
    domain VARCHAR(255),
    country_code VARCHAR(8),
    bytes_in BIGINT NOT NULL DEFAULT 0,
    bytes_out BIGINT NOT NULL DEFAULT 0,
    total_bytes BIGINT NOT NULL DEFAULT 0,
    hit_count INTEGER NOT NULL DEFAULT 1,
    last_seen DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS router_traffic_rollups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    router_id INTEGER NOT NULL REFERENCES routers(id) ON DELETE CASCADE,
    record_date DATE NOT NULL,
    bytes_in BIGINT NOT NULL DEFAULT 0,
    bytes_out BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS interface_traffic_rollups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    router_id INTEGER REFERENCES routers(id) ON DELETE CASCADE,
    interface_name VARCHAR(100) NOT NULL,
    record_date DATE NOT NULL,
    bytes_in BIGINT NOT NULL DEFAULT 0,
    bytes_out BIGINT NOT NULL DEFAULT 0,
    CONSTRAINT uq_interface_traffic_rollup_day UNIQUE (router_id, interface_name, record_date)
);

CREATE TABLE IF NOT EXISTS router_self_traffic_rollups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    router_id INTEGER REFERENCES routers(id) ON DELETE CASCADE,
    record_date DATE NOT NULL,
    bytes_in BIGINT NOT NULL DEFAULT 0,
    bytes_out BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS speed_test_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    router_id INTEGER REFERENCES routers(id) ON DELETE CASCADE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    download_mbps REAL,
    upload_mbps REAL,
    ping_ms REAL,
    jitter_ms REAL,
    packet_loss_pct REAL,
    server_name VARCHAR(200),
    isp VARCHAR(200),
    result_url VARCHAR(300),
    status VARCHAR(20) NOT NULL DEFAULT 'ok',
    error TEXT,
    raw_output TEXT
);

CREATE TABLE IF NOT EXISTS app_settings (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT NOT NULL,
    description TEXT,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alert_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    router_id INTEGER REFERENCES routers(id) ON DELETE SET NULL,
    alert_type VARCHAR(50) NOT NULL,
    message VARCHAR(1000) NOT NULL,
    metadata_payload JSON,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS system_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    router_id INTEGER REFERENCES routers(id) ON DELETE CASCADE,
    cpu_load REAL NOT NULL DEFAULT 0.0,
    memory_used_bytes BIGINT NOT NULL DEFAULT 0,
    memory_total_bytes BIGINT NOT NULL DEFAULT 0,
    memory_usage_pct REAL NOT NULL DEFAULT 0.0,
    temperature REAL,
    voltage REAL,
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS interface_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    router_id INTEGER REFERENCES routers(id) ON DELETE CASCADE,
    interface_name VARCHAR(100) NOT NULL,
    rx_rate_bps REAL NOT NULL DEFAULT 0.0,
    tx_rate_bps REAL NOT NULL DEFAULT 0.0,
    rx_bytes_total BIGINT NOT NULL DEFAULT 0,
    tx_bytes_total BIGINT NOT NULL DEFAULT 0,
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS router_backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    router_id INTEGER NOT NULL REFERENCES routers(id) ON DELETE CASCADE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    outcome VARCHAR(20) NOT NULL,
    source VARCHAR(20) NOT NULL DEFAULT 'manual',
    fingerprint VARCHAR(64),
    rsc_content TEXT,
    rsc_bytes INTEGER NOT NULL DEFAULT 0,
    backup_file_path VARCHAR(500),
    backup_bytes INTEGER NOT NULL DEFAULT 0,
    backup_password VARCHAR(400),
    is_pinned BOOLEAN NOT NULL DEFAULT 0,
    note VARCHAR(255),
    model VARCHAR(100),
    serial VARCHAR(100),
    os_version VARCHAR(50),
    error_message TEXT,
    duration_ms INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS router_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    router_id INTEGER NOT NULL REFERENCES routers(id) ON DELETE CASCADE,
    external_id VARCHAR(32),
    timestamp DATETIME NOT NULL,
    topics VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    severity VARCHAR(20) NOT NULL DEFAULT 'info',
    category VARCHAR(50) NOT NULL DEFAULT 'system',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
`

const IndexesDDL = `
CREATE INDEX IF NOT EXISTS ix_routers_name ON routers (name);
CREATE INDEX IF NOT EXISTS ix_routers_serial_number ON routers (serial_number);
CREATE INDEX IF NOT EXISTS ix_users_name ON users (name);
CREATE INDEX IF NOT EXISTS ix_users_router_id ON users (router_id);
CREATE INDEX IF NOT EXISTS ix_devices_mac_address ON devices (mac_address);
CREATE INDEX IF NOT EXISTS ix_devices_ip_address ON devices (ip_address);
CREATE INDEX IF NOT EXISTS ix_devices_user_id ON devices (user_id);
CREATE INDEX IF NOT EXISTS ix_devices_router_id ON devices (router_id);
CREATE INDEX IF NOT EXISTS ix_devices_is_deleted ON devices (is_deleted);
CREATE INDEX IF NOT EXISTS ix_device_history_device_id ON device_history (device_id);
CREATE INDEX IF NOT EXISTS ix_device_history_device_time ON device_history (device_id, created_at);
CREATE INDEX IF NOT EXISTS ix_device_coexistence_mac_a ON device_coexistence (mac_a);
CREATE INDEX IF NOT EXISTS ix_device_coexistence_mac_b ON device_coexistence (mac_b);
CREATE INDEX IF NOT EXISTS ix_traffic_rollups_user_id ON traffic_rollups (user_id);
CREATE INDEX IF NOT EXISTS ix_traffic_rollups_user_date ON traffic_rollups (user_id, record_date);
CREATE INDEX IF NOT EXISTS ix_device_traffic_rollups_device_id ON device_traffic_rollups (device_id);
CREATE INDEX IF NOT EXISTS ix_device_rollups_device_date ON device_traffic_rollups (device_id, record_date);
CREATE INDEX IF NOT EXISTS ix_user_traffic_buckets_user_id ON user_traffic_buckets (user_id);
CREATE INDEX IF NOT EXISTS ix_user_traffic_buckets_start ON user_traffic_buckets (bucket_start);
CREATE INDEX IF NOT EXISTS ix_device_traffic_buckets_device_id ON device_traffic_buckets (device_id);
CREATE INDEX IF NOT EXISTS ix_device_traffic_buckets_start ON device_traffic_buckets (bucket_start);
CREATE INDEX IF NOT EXISTS ix_user_dest_user_id ON user_destination_stats (user_id);
CREATE INDEX IF NOT EXISTS ix_user_dest_device_id ON user_destination_stats (device_id);
CREATE INDEX IF NOT EXISTS ix_user_dest_dest_ip ON user_destination_stats (destination_ip);
CREATE INDEX IF NOT EXISTS ix_user_dest_total_bytes ON user_destination_stats (total_bytes);
CREATE INDEX IF NOT EXISTS ix_router_rollups_router_date ON router_traffic_rollups (router_id, record_date);
CREATE INDEX IF NOT EXISTS ix_system_metrics_router_time ON system_metrics (router_id, timestamp);
CREATE INDEX IF NOT EXISTS ix_interface_metrics_router_time ON interface_metrics (router_id, timestamp);
CREATE INDEX IF NOT EXISTS ix_interface_metrics_name_time ON interface_metrics (interface_name, timestamp);
CREATE INDEX IF NOT EXISTS ix_router_logs_router_id ON router_logs (router_id);
CREATE INDEX IF NOT EXISTS ix_router_logs_timestamp ON router_logs (timestamp);
CREATE UNIQUE INDEX IF NOT EXISTS uq_router_logs_entry ON router_logs (router_id, external_id, message);
`

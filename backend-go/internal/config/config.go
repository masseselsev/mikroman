package config

import (
	"os"
	"strconv"
	"strings"
	"time"
)

// Config holds all configuration settings for the application.
type Config struct {
	AppName                  string
	AppVersion               string
	Host                     string
	Port                     int
	DatabaseURL              string
	DataDir                  string
	Debug                    bool
	SecretKey                string
	AdminPassword            string
	AuthEnabled              bool
	PollIntervalSeconds      time.Duration
	HeavySyncIntervalSeconds time.Duration
	LogScrapeIntervalSeconds time.Duration
	TelegramBotToken         string
	TelegramAdminChatIDs     []int64
}

// Load populates Config from environment variables with sensible production defaults.
func Load() *Config {
	cfg := &Config{
		AppName:                  getEnv("APP_NAME", "MikroMan"),
		AppVersion:               getEnv("APP_VERSION", "0.3.11"),
		Host:                     getEnv("HOST", "0.0.0.0"),
		Port:                     getEnvInt("PORT", 1928),
		DatabaseURL:              getEnv("DATABASE_URL", "sqlite:///data/app.db"),
		DataDir:                  getEnv("MIKROMAN_DATA_DIR", "/data"),
		Debug:                    getEnvBool("DEBUG", false),
		SecretKey:                os.Getenv("MIKROMAN_SECRET_KEY"),
		AdminPassword:            getEnv("ADMIN_PASSWORD", "admin"),
		AuthEnabled:              getEnvBool("AUTH_ENABLED", true),
		PollIntervalSeconds:      time.Duration(getEnvInt("POLL_INTERVAL_SECONDS", 10)) * time.Second,
		HeavySyncIntervalSeconds: time.Duration(getEnvInt("HEAVY_SYNC_INTERVAL_SECONDS", 60)) * time.Second,
		LogScrapeIntervalSeconds: time.Duration(getEnvInt("LOG_SCRAPE_INTERVAL_SECONDS", 60)) * time.Second,
		TelegramBotToken:         os.Getenv("TELEGRAM_BOT_TOKEN"),
		TelegramAdminChatIDs:     parseChatIDs(os.Getenv("TELEGRAM_ADMIN_CHAT_ID")),
	}

	// Normalize Database path if starts with sqlite:// or sqlite+aiosqlite://
	cfg.DatabaseURL = CleanDBPath(cfg.DatabaseURL)

	return cfg
}

// CleanDBPath normalizes SQLite URI schemes (sqlite+aiosqlite://, sqlite://) into filesystem paths.
func CleanDBPath(raw string) string {
	val := strings.TrimSpace(raw)
	if strings.Contains(val, "://") {
		parts := strings.SplitN(val, "://", 2)
		path := parts[1]
		// In sqlite+aiosqlite:////data/app.db, four slashes represent an absolute path /data/app.db.
		// In sqlite+aiosqlite:///data/app.db, three slashes also represent /data/app.db on Unix.
		for strings.HasPrefix(path, "//") {
			path = strings.TrimPrefix(path, "/")
		}
		return path
	}
	return val
}

func getEnv(key, defaultVal string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return defaultVal
}

func getEnvInt(key string, defaultVal int) int {
	if val := os.Getenv(key); val != "" {
		if i, err := strconv.Atoi(val); err == nil {
			return i
		}
	}
	return defaultVal
}

func getEnvBool(key string, defaultVal bool) bool {
	if val := os.Getenv(key); val != "" {
		v := strings.ToLower(strings.TrimSpace(val))
		return v == "true" || v == "1" || v == "yes" || v == "on"
	}
	return defaultVal
}

func parseChatIDs(val string) []int64 {
	if val == "" {
		return nil
	}
	var ids []int64
	for _, part := range strings.Split(val, ",") {
		part = strings.TrimSpace(part)
		if id, err := strconv.ParseInt(part, 10, 64); err == nil {
			ids = append(ids, id)
		}
	}
	return ids
}

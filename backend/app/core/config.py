import json
from typing import List, Optional, Union

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    APP_NAME: str = "MikroMan"
    APP_VERSION: str = "0.2.13"
    DEBUG: bool = False

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/app.db"

    # Logging. The file lives in the same directory as the database so that a
    # container which restarts - or a router that reboots - does not take the
    # app's own history with it. See core/logging_config.py.
    LOG_TO_FILE: bool = True
    LOG_FILE_MAX_BYTES: int = 4_000_000
    LOG_FILE_BACKUP_COUNT: int = 3

    # MikroTik RouterOS REST API settings (7.1+, see routeros_compat.py)
    ROUTEROS_HOST: str = "192.168.88.1"
    ROUTEROS_PORT: int = 443
    ROUTEROS_USE_SSL: bool = True
    ROUTEROS_SSL_VERIFY: bool = False
    ROUTEROS_USER: str = "admin"
    ROUTEROS_PASSWORD: str = ""
    ROUTEROS_TIMEOUT_SECONDS: float = 5.0

    # Polling & Synchronization
    POLL_INTERVAL_SECONDS: int = 10
    # The housekeeping half of the background tick - device discovery, simple
    # queue and mangle-counter reconciliation, rollup recompute - costs dozens of
    # REST calls per router, while the telemetry half costs four. Nothing in the
    # housekeeping set changes once a minute on a home network, and a limit or
    # pause typed into the UI is applied by its own endpoint immediately, not by
    # this loop. So it runs on its own, slower clock.
    HEAVY_SYNC_INTERVAL_SECONDS: int = 60
    # 3s, matching the option the settings dialog marks as recommended. At 1s
    # the dialog showed "recommended" while the app actually polled three times
    # as often, and each poll costs several REST calls against the router.
    TELEMETRY_STREAM_INTERVAL_SECONDS: float = 3.0

    # Telegram Bot
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_ADMIN_CHAT_IDS: Union[List[int], str] = []
    TELEGRAM_MODE: str = "polling"  # 'polling' or 'webhook'
    TELEGRAM_WEBHOOK_URL: Optional[str] = None
    TELEGRAM_DEFAULT_LANG: str = "en"  # 'en' or 'ru'

    # Alerting Thresholds
    ALERT_CPU_THRESHOLD_PERCENT: int = 90
    ALERT_TEMP_THRESHOLD_CELSIUS: int = 70
    ALERT_NEW_DEVICE_ENABLED: bool = True

    # Application Authentication & Session Security
    AUTH_ENABLED: bool = True
    ADMIN_PASSWORD: Optional[str] = None
    API_KEY: Optional[str] = None
    SESSION_EXPIRE_DAYS: int = 7

    @field_validator("AUTH_ENABLED", mode="before")
    @classmethod
    def parse_auth_enabled(cls, v):
        import os
        env_val = os.environ.get("MIKROMAN_AUTH_ENABLED")
        if env_val is not None:
            return env_val.strip().lower() in ("true", "1", "yes")
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes")
        return bool(v)

    @field_validator("ADMIN_PASSWORD", mode="before")
    @classmethod
    def parse_admin_password(cls, v: Optional[str]) -> Optional[str]:
        import os
        env_val = os.environ.get("MIKROMAN_ADMIN_PASSWORD")
        if env_val and env_val.strip():
            return env_val.strip()
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("API_KEY", mode="before")
    @classmethod
    def parse_api_key(cls, v: Optional[str]) -> Optional[str]:
        import os
        env_val = os.environ.get("MIKROMAN_API_KEY")
        if env_val and env_val.strip():
            return env_val.strip()
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_URL", mode="before")
    @classmethod
    def clean_empty_str(cls, v: Optional[str]) -> Optional[str]:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("TELEGRAM_ADMIN_CHAT_IDS", mode="before")
    @classmethod
    def parse_chat_ids(cls, v: Union[str, List[Union[int, str]], None]) -> List[int]:
        if not v:
            return []
        if isinstance(v, str):
            v_str = v.strip()
            if not v_str:
                return []
            if v_str.startswith("[") and v_str.endswith("]"):
                try:
                    loaded = json.loads(v_str)
                    return [int(x) for x in loaded if str(x).strip()]
                except Exception:
                    pass
            # Comma-separated fallback
            ids = []
            for part in v_str.split(","):
                part_clean = part.strip()
                if part_clean:
                    ids.append(int(part_clean))
            return ids
        if isinstance(v, (list, tuple)):
            return [int(x) for x in v if str(x).strip()]
        return []


settings = Settings()


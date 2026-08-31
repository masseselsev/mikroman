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
    APP_VERSION: str = "0.2.0"
    DEBUG: bool = False

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/app.db"

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
    TELEMETRY_STREAM_INTERVAL_SECONDS: float = 1.0

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


import logging
import re
import time
from typing import Dict, Optional, Tuple

import httpx

logger = logging.getLogger("mikroman.changelog")

VERSION_REGEX = re.compile(r"^\d+\.\d+(\.\d+)?$")
MAX_BODY_BYTES = 256 * 1024  # 256 KB
CACHE_MAX = 32
NEG_TTL_SECONDS = 60.0


def validate_version(version: str) -> str:
    v = version.strip()
    if not VERSION_REGEX.match(v):
        raise ValueError(f"Invalid RouterOS version format '{version}'")
    return v


class ChangelogService:
    def __init__(self, timeout: float = 8.0):
        self.timeout = timeout
        # key: version -> (notes, error_message, timestamp)
        self._cache: Dict[str, Tuple[Optional[str], Optional[str], float]] = {}
        self._order: list[str] = []

    async def get_notes(self, version: str) -> str:
        v = validate_version(version)
        now = time.monotonic()

        # Check in-memory cache
        if v in self._cache:
            notes, err, ts = self._cache[v]
            if notes is not None:
                return notes
            if err is not None and (now - ts) < NEG_TTL_SECONDS:
                raise RuntimeError(err)

        # Upstream fetch
        url = f"https://upgrade.mikrotik.com/routeros/{v}/CHANGELOG"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.get(url, headers={"Accept": "text/plain"})
                if res.status_code != 200:
                    err_msg = f"HTTP {res.status_code} from upgrade server"
                    self._remember(v, None, err_msg)
                    raise RuntimeError(err_msg)

                content = res.content
                if len(content) > MAX_BODY_BYTES:
                    err_msg = "Changelog payload exceeds size limit"
                    self._remember(v, None, err_msg)
                    raise RuntimeError(err_msg)

                notes = content.decode("utf-8", errors="replace").strip()
                if not notes:
                    err_msg = "Changelog is empty"
                    self._remember(v, None, err_msg)
                    raise RuntimeError(err_msg)

                self._remember(v, notes, None)
                return notes

        except Exception as e:
            if not isinstance(e, RuntimeError):
                err_msg = f"Failed to reach upgrade server: {e}"
                self._remember(v, None, err_msg)
                raise RuntimeError(err_msg)
            raise

    def _remember(self, version: str, notes: Optional[str], err: Optional[str]) -> None:
        if version not in self._cache:
            if len(self._order) >= CACHE_MAX:
                oldest = self._order.pop(0)
                self._cache.pop(oldest, None)
            self._order.append(version)
        self._cache[version] = (notes, err, time.monotonic())


changelog_service = ChangelogService()


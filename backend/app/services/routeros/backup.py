import asyncio
import logging
import secrets
import string
from typing import List

logger = logging.getLogger("mikroman.routeros.backup")

FILE_PREFIX = "mikroman-backup-"
SETTLE_INTERVAL = 0.3
DEFAULT_TIMEOUT = 30.0


def generate_backup_password(length: int = 24) -> str:
    """Generate a secure alphanumeric password for RouterOS binary backup encryption."""
    chars = string.ascii_letters + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


class BackupMixin:
    """Methods for RouterOS configuration export, binary backup and flash sweep."""

    async def sweep_temporary_files(self, prefix: str = FILE_PREFIX) -> int:
        """Remove any temporary files created by backup runs.

        Prefix check is exact startswith to avoid touching user files.
        Never raises: logs errors and returns count of successfully removed files.
        """
        removed = 0
        try:
            async with self._get_client() as client:
                # `.proplist` is load-bearing, not an optimisation: an
                # unqualified GET /file includes each file's `contents`, so the
                # response body carries the raw bytes of every binary on flash
                # and fails to decode as UTF-8 ("invalid continuation byte").
                # The sweep then never ran and its temp files accumulated.
                resp = await client.get("/file", params={".proplist": ".id,name"})
                if resp.status_code != 200:
                    return 0
                files = resp.json()
                if not isinstance(files, list):
                    return 0

                for f in files:
                    name = f.get("name", "")
                    if name.startswith(prefix):
                        file_id = f.get(".id") or name
                        del_resp = await client.delete(f"/file/{file_id}")
                        if del_resp.status_code in (200, 204):
                            removed += 1
                        else:
                            # Fallback to POST /file/remove
                            fallback_resp = await client.post(
                                "/file/remove", json={"numbers": name}
                            )
                            if fallback_resp.status_code in (200, 204):
                                removed += 1
        except Exception as e:
            logger.warning(f"Error during flash sweep with prefix '{prefix}': {e}")
        return removed

    async def _wait_for_file_settled(
        self, filename: str, timeout: float = DEFAULT_TIMEOUT
    ) -> int:
        """Wait until filename exists and its reported size is >0 and stable across 2 checks."""
        deadline = asyncio.get_event_loop().time() + timeout
        last_size = -1
        stable_count = 0

        while asyncio.get_event_loop().time() < deadline:
            try:
                async with self._get_client() as client:
                    resp = await client.get("/file")
                    if resp.status_code == 200:
                        files = resp.json()
                        size = -1
                        if isinstance(files, list):
                            for f in files:
                                if f.get("name") == filename:
                                    try:
                                        size = int(f.get("size", -1))
                                    except (ValueError, TypeError):
                                        size = -1
                                    break
                        if size > 0 and size == last_size:
                            stable_count += 1
                            if stable_count >= 2:
                                return size
                        else:
                            stable_count = 0
                        last_size = size
            except Exception:
                pass
            await asyncio.sleep(SETTLE_INTERVAL)

        raise TimeoutError(f"Timed out waiting for {filename} to settle on router flash")

    async def export_config(self, stem: str, timeout: float = DEFAULT_TIMEOUT) -> str:
        """Execute /export to a temp file, wait for write to finish, fetch text, and sweep."""
        base = f"{FILE_PREFIX}{stem}"
        rsc_filename = f"{base}.rsc"
        async with self._get_client() as client:
            resp = await client.post("/export", json={"file": base})
            if resp.status_code not in (200, 204):
                raise RuntimeError(
                    f"Export command failed: {resp.status_code} {resp.text}"
                )

        await self._wait_for_file_settled(rsc_filename, timeout=timeout)

        content_chunks: List[str] = []
        offset = 0
        chunk_size = 32768
        async with self._get_client() as client:
            while True:
                read_resp = await client.post(
                    "/file/read",
                    json={"file": rsc_filename, "offset": offset, "chunk-size": chunk_size},
                )
                if read_resp.status_code != 200:
                    break
                body = read_resp.json()
                if not body:
                    break
                data = (
                    body[0].get("data")
                    if isinstance(body, list) and body
                    else (body.get("data") if isinstance(body, dict) else None)
                )
                if not data:
                    break
                content_chunks.append(data)
                offset += len(data)
                if len(data) < chunk_size:
                    break

        return "".join(content_chunks)

    async def create_system_backup(
        self, stem: str, password: str, timeout: float = DEFAULT_TIMEOUT
    ) -> bytes:
        """Execute /system/backup/save, wait for write to finish, fetch binary bytes."""
        base = f"{FILE_PREFIX}{stem}"
        backup_filename = f"{base}.backup"
        async with self._get_client() as client:
            resp = await client.post(
                "/system/backup/save",
                json={"name": base, "password": password, "encryption": "aes-sha256"},
            )
            if resp.status_code not in (200, 204):
                raise RuntimeError(
                    f"Backup save command failed: {resp.status_code} {resp.text}"
                )

        await self._wait_for_file_settled(backup_filename, timeout=timeout)

        chunks: List[bytes] = []
        offset = 0
        chunk_size = 32768
        async with self._get_client() as client:
            while True:
                read_resp = await client.post(
                    "/file/read",
                    json={"file": backup_filename, "offset": offset, "chunk-size": chunk_size},
                )
                if read_resp.status_code != 200:
                    break
                body = read_resp.json()
                if not body:
                    break
                data = (
                    body[0].get("data")
                    if isinstance(body, list) and body
                    else (body.get("data") if isinstance(body, dict) else None)
                )
                if not data:
                    break
                # Latin-1 preserves 1-to-1 byte values
                chunks.append(data.encode("latin-1"))
                offset += len(data)
                if len(data) < chunk_size:
                    break

        return b"".join(chunks)

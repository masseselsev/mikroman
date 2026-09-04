# Config Drift & Automated Backup with Visual Diff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement automated and on-demand dual-pair RouterOS backups (`.rsc` script export + encrypted binary `.backup`), volatile timestamp normalization for zero-drift deduplication, server-side unified diffing, retention pruning, and a rich interactive visual diff viewer in the frontend.

**Architecture:** RouterOS transport handles settle polling and flash cleanup (`mikroman-backup-*`). A normalizer strips volatile export headers for SHA-256 deduplication. Python's `difflib` powers a structured diff engine. FastAPI exposes paginated history, diffs, and file downloads. A React modal renders snapshot history, milestone pinning, and visual unified/split diffs.

**Tech Stack:** FastAPI, SQLAlchemy, SQLite, Pydantic v2, Python `difflib`, `httpx`, React 18, Tailwind CSS, Lucide React, Vitest.

## Global Constraints

- Never commit code to git without explicit user approval.
- Pure normalizer and diff functions must be stateless, deterministic, and free of I/O.
- Router flash sweep must execute on every backup code path (`finally` block) to guarantee no file leaks on the router.
- Volatile timestamp header matching `^# \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} by RouterOS .*$` must be stripped before SHA-256 fingerprinting.
- If normalized fingerprint matches the router's last successful backup, mark outcome as `unchanged` and do not write duplicate `.rsc` or `.backup` files to server disk.
- All Python tests must run with `.venv/bin/pytest` and pass `.venv/bin/ruff check`.
- All frontend tests must run with `npm test` and pass without warnings.

---

### Task 1: Database Model & Migration (`RouterBackup`)

**Files:**
- Create: `backend/app/schemas/backup.py`
- Modify: `backend/app/db/models.py`
- Create: `backend/migrations/versions/022_router_backups.py`
- Test: `tests/test_backup_models_and_schemas.py`

**Interfaces:**
- Consumes: `Router` from `backend.app.db.models`
- Produces: `RouterBackup` model and Pydantic schemas (`RouterBackupResponse`, `RouterBackupListResponse`, `RouterBackupUpdate`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backup_models_and_schemas.py
from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.app.db.models import Base, Router, RouterBackup
from backend.app.schemas.backup import RouterBackupResponse, RouterBackupUpdate

def test_router_backup_model_and_relationship():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    router = Router(name="Core-GW", host="192.168.88.1", port=80)
    session.add(router)
    session.commit()

    backup = RouterBackup(
        router_id=router.id,
        outcome="changed",
        source="manual",
        fingerprint="a"*64,
        rsc_content="/ip firewall filter add chain=input",
        rsc_bytes=35,
        backup_file_path="data/backups/1/1.backup",
        backup_bytes=1024,
        backup_password="secret-passphrase",
        is_pinned=True,
        note="Pre-upgrade",
        model="RB5009",
        os_version="7.15.2",
        duration_ms=450,
    )
    session.add(backup)
    session.commit()

    assert backup.id is not None
    assert backup.created_at is not None
    assert backup.router.name == "Core-GW"
    assert len(router.backups) == 1
    assert router.backups[0].fingerprint == "a"*64

    # Test Pydantic serialization
    schema = RouterBackupResponse.model_validate(backup)
    assert schema.id == backup.id
    assert schema.outcome == "changed"
    assert schema.is_pinned is True
    assert schema.note == "Pre-upgrade"

    update_schema = RouterBackupUpdate(is_pinned=False, note="Updated note")
    assert update_schema.is_pinned is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_backup_models_and_schemas.py -v`
Expected: FAIL with `ImportError: cannot import name 'RouterBackup' from 'backend.app.db.models'`

- [ ] **Step 3: Implement `RouterBackup` model and schemas**

Create `backend/app/schemas/backup.py`:
```python
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict

class RouterBackupBase(BaseModel):
    is_pinned: bool = False
    note: Optional[str] = None

class RouterBackupUpdate(BaseModel):
    is_pinned: Optional[bool] = None
    note: Optional[str] = None

class RouterBackupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    router_id: int
    created_at: datetime
    outcome: str
    source: str
    fingerprint: Optional[str] = None
    rsc_bytes: int = 0
    backup_bytes: int = 0
    is_pinned: bool = False
    note: Optional[str] = None
    model: Optional[str] = None
    serial: Optional[str] = None
    os_version: Optional[str] = None
    error_message: Optional[str] = None
    duration_ms: int = 0
    has_rsc: bool = False
    has_binary: bool = False

class RouterBackupListResponse(BaseModel):
    items: List[RouterBackupResponse]
    total: int
    page: int
    page_size: int
```

Modify `backend/app/db/models.py` to add `RouterBackup` class and relationship on `Router`:
```python
class RouterBackup(Base):
    __tablename__ = "router_backups"

    id = Column(Integer, primary_key=True, index=True)
    router_id = Column(Integer, ForeignKey("routers.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    outcome = Column(String(20), nullable=False, index=True)
    source = Column(String(20), nullable=False, default="manual")
    fingerprint = Column(String(64), nullable=True, index=True)
    rsc_content = Column(Text, nullable=True)
    rsc_bytes = Column(Integer, nullable=False, default=0)
    backup_file_path = Column(String(500), nullable=True)
    backup_bytes = Column(Integer, nullable=False, default=0)
    backup_password = Column(String(128), nullable=True)
    is_pinned = Column(Boolean, nullable=False, default=False)
    note = Column(String(255), nullable=True)
    model = Column(String(100), nullable=True)
    serial = Column(String(100), nullable=True)
    os_version = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=False, default=0)

    router = relationship("Router", back_populates="backups")
```
Also add to `Router`:
```python
backups = relationship("RouterBackup", back_populates="router", cascade="all, delete-orphan", order_by="desc(RouterBackup.created_at)")
```

Create migration `backend/migrations/versions/022_router_backups.py` creating the `router_backups` table with indexes.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_backup_models_and_schemas.py -v`
Expected: PASS

- [ ] **Step 5: Run linter**

Run: `.venv/bin/ruff check backend/app/db/models.py backend/app/schemas/backup.py tests/test_backup_models_and_schemas.py`
Expected: PASS

---

### Task 2: Volatile Header Normalization & SHA-256 Fingerprinting

**Files:**
- Create: `backend/app/services/backup_normalizer.py`
- Test: `tests/test_backup_normalizer.py`

**Interfaces:**
- Produces: `normalize_rsc(rsc_text: str) -> str`, `compute_fingerprint(rsc_text: str) -> str`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backup_normalizer.py
import hashlib
from backend.app.services.backup_normalizer import normalize_rsc, compute_fingerprint

SAMPLE_RSC_1 = """# 2026-09-04 15:21:49 by RouterOS 7.15.2
# software id = ABCD-1234
#
# model = RB5009UG+S+IN
# serial number = 1234567890AB
/ip pool add name=dhcp ranges=192.168.88.10-192.168.88.254
/ip dhcp-server add address-pool=dhcp disabled=no interface=bridge name=defconf
"""

SAMPLE_RSC_2 = """# 2026-09-05 03:00:00 by RouterOS 7.15.2
# software id = ABCD-1234
#
# model = RB5009UG+S+IN
# serial number = 1234567890AB
/ip pool add name=dhcp ranges=192.168.88.10-192.168.88.254
/ip dhcp-server add address-pool=dhcp disabled=no interface=bridge name=defconf
"""

SAMPLE_RSC_CHANGED = """# 2026-09-05 03:00:00 by RouterOS 7.15.2
# software id = ABCD-1234
#
# model = RB5009UG+S+IN
/ip pool add name=dhcp ranges=192.168.88.10-192.168.88.200
/ip dhcp-server add address-pool=dhcp disabled=no interface=bridge name=defconf
"""

def test_normalize_rsc_strips_volatile_timestamp():
    norm1 = normalize_rsc(SAMPLE_RSC_1)
    norm2 = normalize_rsc(SAMPLE_RSC_2)
    assert norm1 == norm2
    assert "2026-09-04 15:21:49" not in norm1
    assert "2026-09-05 03:00:00" not in norm2
    assert "/ip pool add name=dhcp" in norm1

def test_compute_fingerprint():
    fp1 = compute_fingerprint(SAMPLE_RSC_1)
    fp2 = compute_fingerprint(SAMPLE_RSC_2)
    fp_changed = compute_fingerprint(SAMPLE_RSC_CHANGED)

    assert fp1 == fp2
    assert len(fp1) == 64
    assert fp1 != fp_changed

def test_normalize_empty_or_whitespace():
    assert normalize_rsc("") == ""
    assert normalize_rsc("   \n\r\n  ") == ""
    assert len(compute_fingerprint("")) == 64
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_backup_normalizer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.app.services.backup_normalizer'`

- [ ] **Step 3: Implement `backup_normalizer.py`**

```python
# backend/app/services/backup_normalizer.py
import hashlib
import re

VOLATILE_HEADER_RE = re.compile(
    r"^#\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+by\s+RouterOS\b.*$",
    re.MULTILINE,
)

def normalize_rsc(rsc_text: str) -> str:
    """Normalize RouterOS .rsc script export by removing volatile timestamp headers

    and standardizing newlines and trailing whitespace.
    """
    if not rsc_text:
        return ""
    # Standardize line endings
    text = rsc_text.replace("\r\n", "\n").replace("\r", "\n")
    # Strip volatile RouterOS timestamp line
    text = VOLATILE_HEADER_RE.sub("", text)
    # Strip leading/trailing blank lines
    lines = [line.rstrip() for line in text.split("\n")]
    # Remove leading blank lines
    while lines and not lines[0]:
        lines.pop(0)
    # Remove trailing blank lines
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)

def compute_fingerprint(rsc_text: str) -> str:
    """Return SHA-256 hex digest of normalized RouterOS configuration script."""
    normalized = normalize_rsc(rsc_text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_backup_normalizer.py -v`
Expected: PASS

- [ ] **Step 5: Run linter**

Run: `.venv/bin/ruff check backend/app/services/backup_normalizer.py tests/test_backup_normalizer.py`
Expected: PASS

---

### Task 3: Diff Engine (Structured Hunks & Summary Stats)

**Files:**
- Create: `backend/app/services/diff_engine.py`
- Test: `tests/test_diff_engine.py`

**Interfaces:**
- Produces: `DiffEngine.diff_texts(base_text: str, target_text: str, fromfile: str, tofile: str) -> DiffResult`
- Schemas: `DiffLine`, `DiffHunk`, `DiffResult`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_diff_engine.py
from backend.app.services.diff_engine import DiffEngine

BASE_CONFIG = """/interface bridge
add name=bridge1
/ip address
add address=192.168.88.1/24 interface=bridge1
/ip pool
add name=pool1 ranges=192.168.88.10-192.168.88.100
"""

TARGET_CONFIG = """/interface bridge
add name=bridge1
/ip address
add address=192.168.88.1/24 interface=bridge1
add address=10.0.0.1/24 interface=ether2
/ip pool
add name=pool1 ranges=192.168.88.10-192.168.88.200
"""

def test_diff_engine_identical_texts():
    result = DiffEngine.diff_texts(BASE_CONFIG, BASE_CONFIG)
    assert result.lines_added == 0
    assert result.lines_removed == 0
    assert result.total_changes == 0
    assert len(result.hunks) == 0
    assert result.raw_unified == ""

def test_diff_engine_changes():
    result = DiffEngine.diff_texts(BASE_CONFIG, TARGET_CONFIG, fromfile="v1.rsc", tofile="v2.rsc")
    assert result.lines_added >= 2
    assert result.lines_removed >= 1
    assert result.total_changes == result.lines_added + result.lines_removed
    assert len(result.hunks) > 0
    assert "add address=10.0.0.1/24" in result.raw_unified

    # Check hunk structure
    hunk = result.hunks[0]
    assert hunk.old_start > 0
    assert hunk.new_start > 0
    types = [line.type for line in hunk.lines]
    assert "add" in types
    assert "del" in types
    assert "ctx" in types
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_diff_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.app.services.diff_engine'`

- [ ] **Step 3: Implement `diff_engine.py`**

```python
# backend/app/services/diff_engine.py
import difflib
import re
from typing import List, Optional
from pydantic import BaseModel

HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

class DiffLine(BaseModel):
    type: str  # "add", "del", "ctx"
    content: str
    old_line_no: Optional[int] = None
    new_line_no: Optional[int] = None

class DiffHunk(BaseModel):
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    lines: List[DiffLine]

class DiffResult(BaseModel):
    base_id: Optional[int] = None
    target_id: Optional[int] = None
    is_target_live: bool = False
    lines_added: int = 0
    lines_removed: int = 0
    total_changes: int = 0
    hunks: List[DiffHunk] = []
    raw_unified: str = ""

class DiffEngine:
    @staticmethod
    def diff_texts(
        base_text: str,
        target_text: str,
        fromfile: str = "base.rsc",
        tofile: str = "target.rsc",
        context_lines: int = 3,
        base_id: Optional[int] = None,
        target_id: Optional[int] = None,
        is_target_live: bool = False,
    ) -> DiffResult:
        base_lines = base_text.splitlines(keepends=True)
        target_lines = target_text.splitlines(keepends=True)

        raw_diff_lines = list(difflib.unified_diff(
            base_lines, target_lines, fromfile=fromfile, tofile=tofile, n=context_lines
        ))
        if not raw_diff_lines:
            return DiffResult(
                base_id=base_id,
                target_id=target_id,
                is_target_live=is_target_live,
                lines_added=0,
                lines_removed=0,
                total_changes=0,
                hunks=[],
                raw_unified="",
            )

        raw_unified = "".join(raw_diff_lines)
        hunks: List[DiffHunk] = []
        current_hunk: Optional[DiffHunk] = None
        curr_old = 0
        curr_new = 0
        lines_added = 0
        lines_removed = 0

        for line in raw_diff_lines:
            if line.startswith("--- ") or line.startswith("+++ "):
                continue
            m = HUNK_HEADER_RE.match(line)
            if m:
                old_start = int(m.group(1))
                old_count = int(m.group(2) or 1)
                new_start = int(m.group(3))
                new_count = int(m.group(4) or 1)
                curr_old = old_start
                curr_new = new_start
                current_hunk = DiffHunk(
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                    header=line.strip(),
                    lines=[],
                )
                hunks.append(current_hunk)
                continue

            if current_hunk is None:
                continue

            content = line[1:].rstrip("\r\n")
            if line.startswith("+"):
                lines_added += 1
                current_hunk.lines.append(DiffLine(
                    type="add", content=content, new_line_no=curr_new
                ))
                curr_new += 1
            elif line.startswith("-"):
                lines_removed += 1
                current_hunk.lines.append(DiffLine(
                    type="del", content=content, old_line_no=curr_old
                ))
                curr_old += 1
            elif line.startswith(" "):
                current_hunk.lines.append(DiffLine(
                    type="ctx", content=content, old_line_no=curr_old, new_line_no=curr_new
                ))
                curr_old += 1
                curr_new += 1

        return DiffResult(
            base_id=base_id,
            target_id=target_id,
            is_target_live=is_target_live,
            lines_added=lines_added,
            lines_removed=lines_removed,
            total_changes=lines_added + lines_removed,
            hunks=hunks,
            raw_unified=raw_unified,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_diff_engine.py -v`
Expected: PASS

- [ ] **Step 5: Run linter**

Run: `.venv/bin/ruff check backend/app/services/diff_engine.py tests/test_diff_engine.py`
Expected: PASS

---

### Task 4: RouterOS Backup Transport (Settle Polling, Prefix Sweeper & Chunk Transfer)

**Files:**
- Create: `backend/app/services/routeros/backup.py`
- Modify: `backend/app/services/routeros/client.py`
- Test: `tests/test_routeros_backup_transport.py`

**Interfaces:**
- Produces: `BackupMixin` methods on `RouterOSClient`:
  - `export_config(filename: str, timeout: float = 30.0) -> str`
  - `create_system_backup(filename: str, password: str, timeout: float = 30.0) -> bytes`
  - `sweep_temporary_files(prefix: str = "mikroman-backup-") -> int`
  - `get_system_identity() -> dict`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_routeros_backup_transport.py
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
import httpx
from backend.app.services.routeros.client import RouterOSClient

@pytest.mark.asyncio
async def test_sweep_temporary_files():
    client = RouterOSClient(host="192.168.88.1", username="admin", password="")
    
    mock_files = [
        {"name": "mikroman-backup-123.rsc", ".id": "*1"},
        {"name": "mikroman-backup-123.backup", ".id": "*2"},
        {"name": "user-file.txt", ".id": "*3"},
    ]

    with patch.object(client, "_get_client") as mock_get:
        mock_http = AsyncMock()
        mock_get.return_value.__aenter__.return_value = mock_http
        
        # GET /rest/file returns list
        mock_http.get.return_value = MagicMock(
            status_code=200, json=lambda: mock_files
        )
        # DELETE /rest/file/*
        mock_http.delete.return_value = MagicMock(status_code=200)

        swept = await client.sweep_temporary_files(prefix="mikroman-backup-")
        assert swept == 2
        assert mock_http.delete.call_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_routeros_backup_transport.py -v`
Expected: FAIL with `AttributeError: 'RouterOSClient' object has no attribute 'sweep_temporary_files'`

- [ ] **Step 3: Implement `BackupMixin` in `backend/app/services/routeros/backup.py` and attach to `RouterOSClient`**

```python
# backend/app/services/routeros/backup.py
import asyncio
import logging
import secrets
import string
from typing import Any, Dict, List, Optional
import httpx

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
                resp = await client.get("/file")
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
                            await client.post("/file/remove", json={"numbers": name})
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
            # Trigger export
            resp = await client.post("/export", json={"file": base})
            if resp.status_code not in (200, 204):
                raise RuntimeError(f"Export command failed: {resp.status_code} {resp.text}")

        # Wait for file to settle
        await self._wait_for_file_settled(rsc_filename, timeout=timeout)

        # Read file chunks via /file/read
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
                data = body[0].get("data") if isinstance(body, list) and body else (
                    body.get("data") if isinstance(body, dict) else None
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
                raise RuntimeError(f"Backup save command failed: {resp.status_code} {resp.text}")

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
                data = body[0].get("data") if isinstance(body, list) and body else (
                    body.get("data") if isinstance(body, dict) else None
                )
                if not data:
                    break
                # Latin-1 preserves 1-to-1 byte values
                chunks.append(data.encode("latin-1"))
                offset += len(data)
                if len(data) < chunk_size:
                    break

        return b"".join(chunks)
```

In `backend/app/services/routeros/client.py`:
Inherit `BackupMixin` into `RouterOSClient`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_routeros_backup_transport.py -v`
Expected: PASS

- [ ] **Step 5: Run linter**

Run: `.venv/bin/ruff check backend/app/services/routeros/backup.py backend/app/services/routeros/client.py tests/test_routeros_backup_transport.py`
Expected: PASS

---

### Task 5: Backup Service & Retention Pruning Engine

**Files:**
- Create: `backend/app/services/backup_service.py`
- Create: `backend/app/services/backup_scheduler.py`
- Test: `tests/test_backup_service_and_pruning.py`

**Interfaces:**
- Produces:
  - `run_router_backup(router_id: int, source: str = "manual", db_session = None) -> RouterBackup`
  - `prune_router_backups(router_id: int, max_count: int = 30, max_days: int = 90, db_session = None) -> int`
  - `BackupScheduler` class with background async loop

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backup_service_and_pruning.py
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.app.db.models import Base, Router, RouterBackup
from backend.app.services.backup_service import run_router_backup, prune_router_backups

@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()

@pytest.mark.asyncio
async def test_run_router_backup_changed_then_unchanged(db_session, tmp_path):
    router = Router(name="TestRouter", host="192.168.88.1", port=80)
    db_session.add(router)
    db_session.commit()

    sample_rsc = "# 2026-09-04 15:00:00 by RouterOS 7.15\n/ip address add address=192.168.88.1/24"
    sample_backup_bytes = b"\x01\x02\x03\x04"

    with patch("backend.app.services.backup_service.get_routeros_client") as mock_get_client, \
         patch("backend.app.services.backup_service.BACKUP_STORAGE_DIR", str(tmp_path)):
        
        client_mock = AsyncMock()
        mock_get_client.return_value = client_mock
        client_mock.sweep_temporary_files.return_value = 0
        client_mock.export_config.return_value = sample_rsc
        client_mock.create_system_backup.return_value = sample_backup_bytes
        client_mock.get_system_resource.return_value = {"board-name": "RB5009", "version": "7.15.2"}

        # First run: should be changed
        b1 = await run_router_backup(router.id, source="manual", db_session=db_session)
        assert b1.outcome == "changed"
        assert b1.rsc_content is not None
        assert b1.fingerprint is not None
        assert client_mock.sweep_temporary_files.call_count >= 2

        # Second run with same config: should be unchanged
        b2 = await run_router_backup(router.id, source="scheduled", db_session=db_session)
        assert b2.outcome == "unchanged"
        assert b2.fingerprint == b1.fingerprint
        assert b2.rsc_content is None  # deduplicated!

@pytest.mark.asyncio
async def test_prune_router_backups_preserves_pinned(db_session):
    router = Router(name="TestRouter", host="192.168.88.1")
    db_session.add(router)
    db_session.commit()

    now = datetime.now(timezone.utc)
    # Create 5 backups: 2 pinned, 3 unpinned
    for i in range(5):
        b = RouterBackup(
            router_id=router.id,
            outcome="changed",
            source="scheduled",
            created_at=now - timedelta(days=100 - i * 10),
            is_pinned=(i in (0, 2)),
            fingerprint=f"fp_{i}",
        )
        db_session.add(b)
    db_session.commit()

    # Max count = 1, should keep the 2 pinned + the 1 newest unpinned
    pruned = prune_router_backups(router.id, max_count=1, max_days=30, db_session=db_session)
    assert pruned == 2

    remaining = db_session.query(RouterBackup).filter(RouterBackup.router_id == router.id).all()
    assert len(remaining) == 3
    pinned_count = sum(1 for r in remaining if r.is_pinned)
    assert pinned_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_backup_service_and_pruning.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.app.services.backup_service'`

- [ ] **Step 3: Implement `backup_service.py` and `backup_scheduler.py`**

Create `backend/app/services/backup_service.py`:
- Handles in-flight locks per router.
- Executes sweep in `finally` block.
- Normalizes `.rsc` and compares fingerprint with latest successful backup.
- Deduplicates `unchanged` runs.
- Saves encrypted `.backup` files to `data/backups/{router_id}/{backup_id}.backup`.
- Implements `prune_router_backups` to delete unpinned records exceeding `max_count` or `max_days`, removing their binary files from disk.

Create `backend/app/services/backup_scheduler.py`:
- Background periodic worker checking active routers on configured intervals.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_backup_service_and_pruning.py -v`
Expected: PASS

- [ ] **Step 5: Run linter**

Run: `.venv/bin/ruff check backend/app/services/backup_service.py backend/app/services/backup_scheduler.py tests/test_backup_service_and_pruning.py`
Expected: PASS

---

### Task 6: REST API Endpoints & Schemas

**Files:**
- Create: `backend/app/api/v1/endpoints/backups.py`
- Modify: `backend/app/api/v1/router.py`
- Test: `tests/test_backups_api.py`

**Interfaces:**
- Produces: Endpoints under `/api/v1/routers/{router_id}/backups`:
  - `GET /` (list with filters)
  - `POST /run` (on-demand trigger)
  - `GET /{backup_id}` (detail)
  - `GET /{backup_id}/download/rsc` (stream text attachment)
  - `GET /{backup_id}/download/backup` (stream binary attachment with header `X-Backup-Password`)
  - `PATCH /{backup_id}` (pin / note update)
  - `DELETE /{backup_id}` (delete record & binary file)
  - `GET /diff` (compute diff between revisions or live)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backups_api.py
import pytest
from httpx import AsyncClient
from backend.app.main import app
from backend.app.db.models import Router, RouterBackup
from backend.app.db.session import get_db

@pytest.mark.asyncio
async def test_backups_api_crud_and_diff(async_client: AsyncClient, test_db):
    router = Router(name="APIRouter", host="192.168.88.1", port=80)
    test_db.add(router)
    test_db.commit()

    b1 = RouterBackup(
        router_id=router.id,
        outcome="changed",
        source="manual",
        fingerprint="fp1",
        rsc_content="/interface bridge add name=br0\n",
        rsc_bytes=32,
        is_pinned=False,
        note="Initial",
    )
    b2 = RouterBackup(
        router_id=router.id,
        outcome="changed",
        source="manual",
        fingerprint="fp2",
        rsc_content="/interface bridge add name=br0\n/ip address add address=10.0.0.1/24 interface=br0\n",
        rsc_bytes=80,
        is_pinned=False,
    )
    test_db.add_all([b1, b2])
    test_db.commit()

    # List backups
    resp = await async_client.get(f"/api/v1/routers/{router.id}/backups")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2

    # Toggle pin
    patch_resp = await async_client.patch(
        f"/api/v1/routers/{router.id}/backups/{b1.id}", json={"is_pinned": True, "note": "Milestone"}
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["is_pinned"] is True

    # Download RSC
    dl_resp = await async_client.get(f"/api/v1/routers/{router.id}/backups/{b1.id}/download/rsc")
    assert dl_resp.status_code == 200
    assert "attachment" in dl_resp.headers["content-disposition"]
    assert b"interface bridge" in dl_resp.content

    # Diff b1 vs b2
    diff_resp = await async_client.get(
        f"/api/v1/routers/{router.id}/backups/diff?base_id={b1.id}&target_id={b2.id}"
    )
    assert diff_resp.status_code == 200
    diff_data = diff_resp.json()
    assert diff_data["lines_added"] >= 1
    assert diff_data["lines_removed"] == 0

    # Delete b2
    del_resp = await async_client.delete(f"/api/v1/routers/{router.id}/backups/{b2.id}")
    assert del_resp.status_code == 204
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_backups_api.py -v`
Expected: FAIL with 404 Not Found (endpoint does not exist yet)

- [ ] **Step 3: Implement endpoints in `backend/app/api/v1/endpoints/backups.py` and register in `router.py`**

Implement the endpoints with error handling (404 on missing router/backup, 400 on invalid diff parameters), streaming responses with `FileResponse` / `StreamingResponse`, and include in `backend/app/api/v1/router.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_backups_api.py -v`
Expected: PASS

- [ ] **Step 5: Run linter**

Run: `.venv/bin/ruff check backend/app/api/v1/endpoints/backups.py backend/app/api/v1/router.py tests/test_backups_api.py`
Expected: PASS

---

### Task 7: Frontend UI (`RouterBackupsModal.jsx`, Visual Diff Viewer, Settings & Navbar Integration)

**Files:**
- Create: `frontend/src/components/RouterBackupsModal.jsx`
- Create: `frontend/src/components/RouterBackupsModal.test.jsx`
- Modify: `frontend/src/components/Navbar.jsx`
- Modify: `frontend/src/components/SettingsModal.jsx`
- Modify: `frontend/src/api/client.js`
- Modify: `frontend/src/i18n/translations.js`

**Interfaces:**
- Consumes: `/api/v1/routers/{router_id}/backups` endpoints via `apiClient`
- Produces: `RouterBackupsModal` interactive component with visual diff viewer, pin toggling, downloads, and live diffing.

- [ ] **Step 1: Write the failing frontend test**

```jsx
// frontend/src/components/RouterBackupsModal.test.jsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import RouterBackupsModal from './RouterBackupsModal';
import * as client from '../api/client';

vi.mock('../api/client');

const mockBackups = {
  items: [
    {
      id: 1,
      router_id: 1,
      created_at: new Date().toISOString(),
      outcome: 'changed',
      source: 'manual',
      rsc_bytes: 4096,
      backup_bytes: 65536,
      is_pinned: false,
      note: 'Initial backup',
      model: 'RB5009',
      os_version: '7.15.2',
      has_rsc: true,
      has_binary: true,
    },
    {
      id: 2,
      router_id: 1,
      created_at: new Date().toISOString(),
      outcome: 'unchanged',
      source: 'scheduled',
      rsc_bytes: 0,
      backup_bytes: 0,
      is_pinned: true,
      note: 'Daily check',
      model: 'RB5009',
      os_version: '7.15.2',
      has_rsc: false,
      has_binary: false,
    }
  ],
  total: 2,
  page: 1,
  page_size: 50,
};

describe('RouterBackupsModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    client.getRouterBackups.mockResolvedValue({ data: mockBackups });
  });

  it('renders backup entries and outcome badges', async () => {
    render(<RouterBackupsModal isOpen={true} onClose={vi.fn()} routerId={1} routerName="Main GW" />);
    
    await waitFor(() => {
      expect(screen.getByText(/Initial backup/i)).toBeInTheDocument();
      expect(screen.getByText(/Changed/i)).toBeInTheDocument();
      expect(screen.getByText(/Unchanged/i)).toBeInTheDocument();
    });
  });

  it('triggers on-demand backup on button click', async () => {
    client.triggerRouterBackup.mockResolvedValue({ data: { id: 3, outcome: 'changed' } });
    render(<RouterBackupsModal isOpen={true} onClose={vi.fn()} routerId={1} routerName="Main GW" />);

    const backupBtn = await screen.findByRole('button', { name: /backup now/i });
    fireEvent.click(backupBtn);

    await waitFor(() => {
      expect(client.triggerRouterBackup).toHaveBeenCalledWith(1);
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test RouterBackupsModal.test.jsx` in `frontend/`
Expected: FAIL (component not found)

- [ ] **Step 3: Implement `RouterBackupsModal.jsx` and integrations**

1. In `frontend/src/api/client.js`:
   - Add `getRouterBackups(routerId, params)`
   - Add `triggerRouterBackup(routerId)`
   - Add `updateRouterBackup(routerId, backupId, data)`
   - Add `deleteRouterBackup(routerId, backupId)`
   - Add `getBackupDiff(routerId, params)`
   - Add helper URL functions for `.rsc` and `.backup` downloads.
2. In `frontend/src/components/RouterBackupsModal.jsx`:
   - Modal header with backup stats, "Backup Now", "Live Diff", filter tabs (All, Changed, Pinned).
   - Snapshot table with relative timestamps, outcome pills, size metrics, pin toggle (`📌`), note editing, and action buttons (`Diff`, `Download RSC`, `Download Backup`, `Delete`).
   - Integrated Visual Diff Viewer:
     - Base revision selector vs Target revision selector (including "Live Router").
     - View mode toggle: `Unified` vs `Side-by-Side`.
     - Metrics badges: `+N lines added`, `-M lines removed`.
     - Monospace code diff with syntax colors (`+` green, `-` red, context gray) and hunk headers.
     - "Copy Patch" button.
3. In `frontend/src/components/Navbar.jsx`:
   - Add `"Backups"` modal launcher button.
4. In `frontend/src/components/SettingsModal.jsx`:
   - Add backup schedule and retention settings controls.
5. In `frontend/src/i18n/translations.js`:
   - Add English and Russian strings for all backup actions, table columns, badges, diff views, and alerts.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test RouterBackupsModal.test.jsx` in `frontend/`
Expected: PASS

- [ ] **Step 5: Run full test suites**

Run: `.venv/bin/pytest` in root
Run: `npm test` in `frontend/`
Expected: ALL PASS with zero failures or regressions.

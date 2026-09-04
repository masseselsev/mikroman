# Sub-project 4: Config Drift & Automated Backup with Visual Diff — Technical Design Specification

- **Date**: 2026-09-04
- **Status**: Approved
- **Author**: Antigravity & User

---

## 1. Overview & Business Value

MikroMan manages RouterOS devices where unintended configuration changes, rogue edits, or hardware failures can cause severe network downtime.
Sub-project 4 introduces:
1. **Dual-Pair Backups**: Automated and on-demand acquisition of both a human-readable, portable RouterOS `.rsc` export script and an encrypted binary `.backup` archive for full bare-metal disaster recovery.
2. **Volatile Header Normalization**: RouterOS exports prepend volatile timestamp comments (`# YYYY-MM-DD HH:MM:SS by RouterOS ...`). Normalizing this header eliminates false-positive drift and deduplicates identical runs with zero storage waste.
3. **Flash Safety & Sweep Invariant**: RouterOS writes exports asynchronously to flash before completion. MikroMan polls for write completion ("settled" file size) and guarantees cleanup of temporary files (`mikroman-backup-*`) on every code path (including success, unchanged, and failure), preventing router NAND exhaustion.
4. **Visual Diff Engine**: Server-side unified diff computation comparing arbitrary backup versions (consecutive drift, revision A vs B, or live router state vs baseline) rendered with syntax-highlighted additions and deletions in both Unified and Side-by-Side views.
5. **Retention & Milestone Pinning**: Configurable FIFO count and age pruning with bookmark pinning to protect critical milestone snapshots.

---

## 2. Architecture & Data Model

### 2.1 Database Schema (`backend/app/db/models.py`)

A new table `router_backups` with index on `(router_id, created_at)`:

```python
class RouterBackup(Base):
    __tablename__ = "router_backups"

    id = Column(Integer, primary_key=True, index=True)
    router_id = Column(Integer, ForeignKey("routers.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    
    # Run status: "changed", "unchanged", "failed"
    outcome = Column(String(20), nullable=False, index=True)
    # Trigger source: "scheduled", "manual"
    source = Column(String(20), nullable=False, default="manual")
    
    # SHA-256 hex digest of normalized .rsc content
    fingerprint = Column(String(64), nullable=True, index=True)
    
    # Plaintext configuration script (null if outcome == "unchanged" or "failed")
    rsc_content = Column(Text, nullable=True)
    rsc_bytes = Column(Integer, nullable=False, default=0)
    
    # Relative path on server disk to the encrypted binary .backup file
    backup_file_path = Column(String(500), nullable=True)
    backup_bytes = Column(Integer, nullable=False, default=0)
    # Encryption key/passphrase used when taking the binary .backup
    backup_password = Column(String(128), nullable=True)
    
    # Milestone bookmark: if True, auto-pruning never deletes this record
    is_pinned = Column(Boolean, nullable=False, default=False)
    note = Column(String(255), nullable=True)
    
    # Hardware & OS snapshot metadata
    model = Column(String(100), nullable=True)
    serial = Column(String(100), nullable=True)
    os_version = Column(String(50), nullable=True)
    
    # Error message if outcome == "failed"
    error_message = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=False, default=0)

    router = relationship("Router", back_populates="backups")
```

Add relationship to `Router`:
```python
backups = relationship("RouterBackup", back_populates="router", cascade="all, delete-orphan", order_by="desc(RouterBackup.created_at)")
```

### 2.2 System & Router Settings

The following settings are supported (per router or system-level settings):
- `backup_enabled`: bool (default `True`)
- `backup_interval_hours`: int (default `24`)
- `backup_retention_count`: int (default `30` changed backups)
- `backup_retention_days`: int (default `90` days)

### 2.3 Filesystem Storage Hierarchy
Binary `.backup` archives are stored securely on server disk under:
```
data/
  backups/
    {router_id}/
      {backup_id}.backup
```
When a `RouterBackup` record is pruned or deleted, the corresponding `.backup` file is unlinked from disk.

---

## 3. RouterOS Transport, Settle Polling & Flash Safety

### 3.1 Settle Polling
RouterOS `/export` and `/system/backup/save` write files to flash asynchronously, returning HTTP 200 before disk I/O completes.
- **Settle Algorithm**: Poll `/rest/file` or `/file/print` every 300ms.
- A file is considered "settled" when its size is `> 0` and identical across 2 consecutive checks.
- Maximum deadline timeout: 30 seconds. If timeout expires, abort and report failure.

### 3.2 Flash Sweep Invariant
- All temporary files on the router are named with prefix: `mikroman-backup-{timestamp}`.
- Every run begins with a sweep of any orphaned files left by prior interrupted runs.
- Every run executes a deferred / `finally` block:
  ```python
  try:
      # export and backup execution...
  finally:
      await client.sweep_temporary_files(prefix="mikroman-backup-")
  ```
- This ensures zero router flash memory leakage even if the connection drops or the server crashes.

### 3.3 Volatile Header Normalization
RouterOS `/export` prepends volatile lines:
```rsc
# 2026-09-04 15:21:49 by RouterOS 7.15.2
# software id = ABCD-1234
#
# model = RB5009UG+S+IN
# serial number = ...
```
- **Normalization Rule**:
  1. Remove any line starting with `# ` and matching timestamp pattern `\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} by RouterOS`.
  2. Strip trailing whitespaces and normalize newlines (`\r\n` -> `\n`).
  3. Strip leading/trailing empty lines.
- **Fingerprinting**: Compute SHA-256 hex digest of the normalized text.
- If `fingerprint == last_successful_backup.fingerprint`:
  - Run is marked `outcome: "unchanged"`.
  - Temporary files on router are swept.
  - No new duplicate `.rsc` text or binary `.backup` file is stored on the server.
  - The check is logged for historical auditability.

---

## 4. Diff Engine & Data Structures

Located in `backend/app/services/diff_engine.py`:

```python
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
    base_id: Optional[int]
    target_id: Optional[int]  # None if target is live
    is_target_live: bool = False
    lines_added: int
    lines_removed: int
    total_changes: int
    hunks: List[DiffHunk]
    raw_unified: str
```

- Uses Python `difflib.unified_diff` to generate standard unified diffs.
- Parses unified diff hunks into structured JSON objects with line numbers for frontend rendering.

---

## 5. REST API Specifications

Router prefix: `/api/v1/routers/{router_id}/backups`

### Endpoints:
1. `GET /`:
   - Query: `page` (int, default 1), `page_size` (int, default 50), `outcome` (optional filter: `changed`, `unchanged`, `failed`), `pinned_only` (bool, default False).
   - Response: `{ items: List[RouterBackupResponse], total: int, page: int, page_size: int }`.
2. `POST /run`:
   - Trigger immediate backup (`source="manual"`).
   - Response: `RouterBackupResponse`.
3. `GET /{backup_id}`:
   - Detailed view of a single backup metadata.
4. `GET /{backup_id}/download/rsc`:
   - Streams plaintext `.rsc` file (`Content-Disposition: attachment; filename="{router_name}_{timestamp}.rsc"`).
5. `GET /{backup_id}/download/backup`:
   - Streams binary `.backup` file (`Content-Disposition: attachment; filename="{router_name}_{timestamp}.backup"`).
   - Response header `X-Backup-Password` exposes the AES decryption key.
6. `PATCH /{backup_id}`:
   - Body: `{ is_pinned?: bool, note?: str }`.
   - Updates milestone bookmark and custom notes.
7. `DELETE /{backup_id}`:
   - Removes DB record and unlinks `.backup` binary file from server disk.
8. `GET /diff`:
   - Query: `base_id` (int), `target_id` (int or `"live"`).
   - Computes diff and returns `DiffResult`.

---

## 6. Frontend UI (`RouterBackupsModal.jsx` & Visual Diff Viewer)

### 6.1 Modal Layout & Interactivity
- **Modal Header**:
  - Router name, total backup count, last backup status.
  - Buttons:
    - `⚡ Backup Now`: Initiates manual backup with spinner and toast notification.
    - `🔍 Live Diff`: Compares current live config against the latest backup.
  - Filter Tabs: `All`, `Changed`, `Pinned`.
- **History Table**:
  - Columns: Timestamp (relative + exact tooltip), Outcome badge (`Changed` in emerald, `Unchanged` in slate, `Failed` in rose), Model & RouterOS version, Sizes (`.rsc` / `.backup`), Pin toggle icon (`📌`), Note (editable), Actions.
  - Action buttons: `Diff`, `Download RSC`, `Download Backup`, `Delete`.

### 6.2 Visual Diff Viewer
- Sub-view or expandable panel within `RouterBackupsModal.jsx`.
- **Selectors**:
  - `Base Revision` dropdown (all past backups).
  - `Target Revision` dropdown (all past backups + *"Live Router State"*).
  - Mode Switch: `Unified` vs `Side-by-Side` (Split columns).
  - Summary metric pill: `+X added`, `-Y removed`.
  - `Copy Patch` button copying raw unified diff.
- Line styling:
  - Added line: light green background, `+` indicator, `new_line_no`.
  - Deleted line: light red background, `-` indicator, `old_line_no`.
  - Context line: neutral background, both line numbers.
  - Hunk header: cyan/blue banner with `@@ ... @@`.

### 6.3 Settings & Navigation Integration
- In `Navbar.jsx`: Add `"Backups"` action with modal toggle.
- In `SettingsModal.jsx`: Add configuration fields for automatic backup interval and retention thresholds.
- Full localization in `frontend/src/i18n/translations.js` (English and Russian).

---

## 7. Testing & Verification Strategy

1. **Unit Tests (`tests/test_backup_normalization_and_diff.py`)**:
   - Verify volatile header stripping across various RouterOS formats (`# 2026-... by RouterOS 7.x`).
   - Verify SHA-256 fingerprint deduplication on identical configs.
   - Verify `DiffEngine`: accurate line counting, hunk parsing, and edge cases (empty diff, complete replacement, single-line change).
2. **Service & Transport Tests (`tests/test_backup_service.py`)**:
   - Mock RouterOS client simulating `/export` and `/system/backup/save`.
   - Test settle polling stability check.
   - Test flash sweep invariant: ensure `sweep_temporary_files` is called in success, failure, and timeout scenarios.
   - Test auto-pruning: ensures oldest unpinned records are pruned while pinned records remain untouched.
3. **API Integration Tests (`tests/test_backups_api.py`)**:
   - Verify all endpoints: `/run`, `/{id}`, `/download/rsc`, `/download/backup`, `/diff`, `PATCH`, `DELETE`.
4. **Frontend Unit Tests (`frontend/src/components/RouterBackupsModal.test.jsx`)**:
   - Vitest suite testing table rendering, outcome badges, filter switching, pin toggling, and diff view mode switching.


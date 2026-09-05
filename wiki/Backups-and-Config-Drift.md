# 🗂️ Backups, Config Drift & Visual Diff

MikroMan provides automated configuration exports, bare-metal disaster recovery snapshots, and visual change tracking.

---

## 📦 Dual-Pair Backup Strategy

Each backup run captures two complementary artifacts:
1. **Plain-Text Export (`.rsc`)**: Generated via `/export compact=yes`. Human-readable configuration script suitable for review and partial imports.
2. **Encrypted Binary Snapshot (`.backup`)**: Generated via `/system/backup/save` with AES-SHA256 password protection. Enables full bare-metal recovery including certificates and user databases.

---

## 🔍 Zero-False-Drift Fingerprinting

RouterOS `/export` output includes volatile timestamp headers that change on every execution:
```routeros
# 2026-09-05 12:00:00 by RouterOS 7.15.2
# software id = ABCD-1234
```
A naive hash of this script would report false configuration drift on every run.

### Normalization Pipeline:
1. Strip timestamp header comments via regex: `r"^# \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} by RouterOS .*$"`
2. Strip volatile byte counters and dynamic status comments.
3. Compute **SHA-256 fingerprint** over the normalized text.
4. **Deduplication**: If the fingerprint matches the preceding backup, the run is recorded as `outcome=unchanged`. Binary storage is avoided, preventing disk exhaustion.

---

## ⚡ RouterOS Flash Write Safety

RouterOS REST API returns HTTP 200 before the hardware storage subsystem has finished flushing backup archives to flash memory.

### Safety Invariants:
- **File Stability Polling**: The transport polls file metadata every 300 ms until file size is `> 0` and remains unchanged across two consecutive checks (30 s timeout).
- **Guaranteed Cleanup**: Every run sweeps orphaned temporary files matching `mikroman-backup-*` before starting and inside an unconditional `finally` block upon completion.

---

## 📊 Server-Side Unified Diff Engine

The visual diff viewer compares any two historical backups or compares a saved snapshot against the **live** router configuration:
- Parses `difflib.unified_diff` into structured JSON hunks (`old_start`, `old_count`, `new_start`, `new_count`).
- Line tags: `add` (green), `del` (red), `ctx` (neutral context).
- Computes aggregate change metrics: lines added, lines removed, total diffs.

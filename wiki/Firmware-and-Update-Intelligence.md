# ⚡ Firmware & Update Intelligence

MikroMan provides complete lifecycle management for RouterOS packages and RouterBOOT bootloaders.

---

## 📡 Multi-Channel Tracking

Monitors available updates across official MikroTik release channels:
- `stable`: General production releases.
- `long-term`: Extended maintenance releases.
- `testing`: Release candidate builds.
- `development`: Feature preview builds.

---

## 📜 Upstream Changelog Proxy

When updates are available, MikroMan fetches upstream release notes directly from MikroTik:
- **Source**: `https://upgrade.mikrotik.com/routeros/{version}/CHANGELOG`
- **SSRF & Path Traversal Prevention**: Version parameter validated against strict regex `^\d+\.\d+(\.\d+)?$`.
- **Bounded Stream Reading**: Body reads are strictly capped at 256 KB.
- **In-Memory FIFO-32 Cache**: Caches recent changelogs to eliminate repetitive upstream queries.
- **Negative Caching**: Upstream errors (404/timeouts) are cached with a 60-second TTL to avoid connection storms.

---

## 🛡️ Pre-Upgrade Safety Invariants

Executing an automated firmware upgrade involves irreversible gateway reboots. MikroMan enforces strict safety gates:
1. **Mandatory Pinned Backup**: Every upgrade request executes an automated configuration backup with `is_pinned=True` before dispatching the install command.
2. **Router Name Confirmation Gate**: The administrator must type the exact router name into the confirmation dialog (`confirm_name.strip() == router.name.strip()`).
3. **Update Validation**: Rejects upgrade requests with HTTP 400 if the router is already on the latest available version.
4. **RouterBOOT Staging**: Automatically checks `/system/routerboard` and stages pending bootloader updates if outdated (`/system/routerboard/upgrade`).

---

## 🔄 Reconnection State Machine

After dispatching the upgrade and reboot commands, the frontend modal enters an autonomous 4-stage reconnection state machine:
```
  [Initiating Upgrade]
           |
           v
    [Rebooting...]  <-- Fixed initial wait window
           |
           v
  [Waiting Online]  <-- Exponential backoff health probing
           |
     +-----+-----+
     |           |
     v           v
  [Ready]   [Timeout Alert]
```
Upon successful reconnection, the new version is verified against the gateway and the UI refreshes live.

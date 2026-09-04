# Sub-project 5 Design Spec: RouterOS Firmware & Update Intelligence

## 1. Executive Summary
Sub-project 5 delivers full firmware lifecycle and update intelligence for MikroTik RouterOS gateways in MikroMan. It bridges the gap between passive telemetry monitoring and active device maintenance by providing:
1. **Multi-Channel Update Intelligence**: Live package checking across `stable`, `long-term`, `testing`, and `development` channels.
2. **Upstream Changelog Delta Engine**: Fast, in-memory cached fetching of official MikroTik release notes from `upgrade.mikrotik.com` with input sanitization.
3. **RouterBOOT Bootloader Lifecycle**: Visibility into hardware bootloader firmware (`current-firmware` vs `upgrade-firmware`) with dedicated or chained upgrade actions.
4. **Zero-Data-Loss Safety Invariants**:
   - **Automated Pinned Pre-Upgrade Backup**: Automatically captures and pins a disaster-recovery backup (`.rsc` + encrypted `.backup`) before issuing an upgrade.
   - **Strict Confirmation Gate**: Requires typing the exact router name to unlock upgrade dispatch.
5. **Reboot Reconnection State Machine**: Real-time heartbeat polling that guides the operator through the 60–90 second reboot window until the router is verified back online.

---

## 2. Architecture & Components

```
┌────────────────────────────────────────────────────────┐
│                   Frontend (React)                     │
│  - Navbar Update Badge ("⚡ Update v7.16.1 Available")   │
│  - RouterFirmwareModal (Dual Cards, Changelog, Safety) │
│  - Reconnect State Machine (idle -> backup -> reboot)  │
└───────────────────────────┬────────────────────────────┘
                            │ REST / WebSocket
┌───────────────────────────▼────────────────────────────┐
│              FastAPI Backend Endpoints                 │
│  GET/POST /api/v1/routers/{id}/firmware/*              │
│  - /status, /check, /channel, /changelog, /upgrade     │
└─────────────┬───────────────────────────┬──────────────┘
              │                           │
┌─────────────▼──────────┐ ┌──────────────▼──────────────┐
│   Changelog Service    │ │   Backup & Safety Service   │
│ - Strict Regex Val     │ │ - Pre-upgrade Pinned Backup │
│ - Bounded In-Memory    │ │ - Name-Match Verification   │
│ - upgrade.mikrotik.com │ │ - Zero-Data-Loss Invariant  │
└────────────────────────┘ └──────────────┬──────────────┘
                                          │
                           ┌──────────────▼──────────────┐
                           │      RouterOS Client        │
                           │       (FirmwareMixin)       │
                           │ - /system/package/update    │
                           │ - /system/routerboard       │
                           └─────────────────────────────┘
```

---

## 3. Detailed Specifications

### 3.1 RouterOS Transport Layer (`FirmwareMixin`)
File: `backend/app/services/routeros/firmware.py`

Integrated into `RouterOSClient` (via `backend/app/services/routeros/client.py`).
- **`get_package_update_status() -> Dict[str, Any]`**:
  - Endpoint: `GET /rest/system/package/update`
  - Normalizes installed version by stripping channel suffix: `re.sub(r"\s*\(.*?\)", "", version)` (e.g. `"7.15.2 (stable)"` -> `"7.15.2"`).
  - Determines `update_available`: `bool(latest_version and latest_version != installed_version)` or `"new version" in status.lower()`.
- **`check_for_package_updates() -> Dict[str, Any]`**:
  - Endpoint: `POST /rest/system/package/update/check-for-updates`
  - Handles asynchronous check: polls `GET /rest/system/package/update` until `status` is not empty and not in transient states (e.g. `"checking..."`).
- **`set_package_update_channel(channel: str) -> Dict[str, Any]`**:
  - Validates channel in `{"stable", "long-term", "testing", "development"}`.
  - Calls `POST /rest/system/package/update/set` with `{"channel": channel}`.
  - Automatically triggers a check for the newly selected channel.
- **`install_package_update() -> None`**:
  - Calls `POST /rest/system/package/update/install`.
  - Router acknowledges HTTP 200 immediately before shutting down and rebooting.
- **`get_routerboard_status() -> Dict[str, Any]`**:
  - Endpoint: `GET /rest/system/routerboard`
  - Fields extracted: `routerboard` (`bool`), `model`, `serial_number`, `current_firmware`, `upgrade_firmware`, `firmware_type`.
  - Determines `firmware_available`: `bool(upgrade_firmware and upgrade_firmware != current_firmware)`.
- **`upgrade_routerboard_firmware() -> None`**:
  - Calls `POST /rest/system/routerboard/upgrade` to flash bootloader into SPI memory.

### 3.2 Upstream Changelog Engine (`changelog.py`)
File: `backend/app/services/changelog.py`

- **Version Validation**: Validates target version against anchored whitelist: `^\d+\.\d+(\.\d+)?$`. Refuses any path traversal or invalid version strings with `ValueError("Invalid version format")`.
- **Upstream Fetching**: Queries `https://upgrade.mikrotik.com/routeros/{version}/CHANGELOG` with `Accept: text/plain` and an 8-second timeout.
- **Memory & Size Safeguards**:
  - Maximum body size: 256 KB. Reading past 256 KB aborts with `"Changelog payload exceeds size limit"`.
  - In-memory FIFO bounded cache: maximum 32 versions. Released changelogs are immutable.
  - Negative cache: failures are cached for 60 seconds to prevent hammering upstream on air-gapped systems.

### 3.3 Schemas & Data Models
File: `backend/app/schemas/firmware.py`

- `PackageUpdateInfo`:
  - `installed_version: str`
  - `latest_version: Optional[str]`
  - `channel: str`
  - `status: str`
  - `update_available: bool`
- `RouterBoardInfo`:
  - `is_routerboard: bool`
  - `model: Optional[str]`
  - `serial_number: Optional[str]`
  - `current_firmware: Optional[str]`
  - `upgrade_firmware: Optional[str]`
  - `firmware_available: bool`
- `RouterFirmwareStatusOut`:
  - `router_id: int`
  - `router_name: str`
  - `packages: PackageUpdateInfo`
  - `routerboard: RouterBoardInfo`
  - `checked_at: datetime`
- `FirmwareChannelUpdatePayload`:
  - `channel: Literal["stable", "long-term", "testing", "development"]`
- `FirmwareUpgradePayload`:
  - `confirm_name: str`
  - `stage_bootloader: bool = True`
- `BootloaderUpgradePayload`:
  - `confirm_name: str`
  - `reboot: bool = False`
- `ChangelogOut`:
  - `version: str`
  - `notes: str`

### 3.4 REST API Endpoints
File: `backend/app/api/v1/endpoints/firmware.py`

- `GET /api/v1/routers/{router_id}/firmware`: Returns `RouterFirmwareStatusOut`.
- `POST /api/v1/routers/{router_id}/firmware/check`: Triggers live upstream check, returns updated status.
- `PUT /api/v1/routers/{router_id}/firmware/channel`: Changes update channel and refreshes status.
- `GET /api/v1/routers/{router_id}/firmware/changelog`: Fetches sanitized release notes for specified version.
- `POST /api/v1/routers/{router_id}/firmware/upgrade`:
  - Safety Step 1: Confirms `confirm_name.strip() == router.name.strip()`. Raises HTTP 400 (`Confirmation mismatch`).
  - Safety Step 2: Confirms `update_available` is True. Raises HTTP 400 (`Router is already up to date`).
  - Safety Step 3: Executes `run_router_backup(router_id, source="pre_upgrade", is_pinned=True, note=f"Pre-upgrade backup v{installed} -> v{latest}")`.
  - Safety Step 4: If `stage_bootloader=True` and `firmware_available=True`, dispatches `upgrade_routerboard_firmware()`.
  - Safety Step 5: Dispatches `install_package_update()`.
  - Returns `{"status": "rebooting", "backup_id": backup.id, "target_version": latest}`.
- `POST /api/v1/routers/{router_id}/firmware/bootloader`:
  - Validates `confirm_name`.
  - Executes `upgrade_routerboard_firmware()`.
  - If `payload.reboot=True`, executes `reboot()`.

### 3.5 Frontend UI & UX Components
Files:
- `frontend/src/components/RouterFirmwareModal.jsx`
- `frontend/src/components/Navbar.jsx`
- `frontend/src/api/client.js`
- `frontend/src/i18n/translations.js`

**Modal Design**:
- Header: Router name, model, serial number, and live "Check for Updates" button.
- Top Grid:
  - **RouterOS Packages Card**: Current vs Latest, Channel select pill, update badge.
  - **RouterBOOT Bootloader Card**: Current vs Upgrade firmware, status badge, standalone "Upgrade Bootloader" button if OS already updated.
- Middle Section:
  - Upstream release notes container with monospace typography, line-wrapped changelog items, and search box to filter changes by keyword.
- Bottom Drawer (Confirmation Gate):
  - Staging checkbox: *"Stage RouterBOOT bootloader upgrade upon reboot"*.
  - Safety notice: *"🛡️ Pinned backup will be created automatically before upgrade."*
  - Name Confirmation Field: `Type "{routerName}" to enable upgrade`.
  - Action Button: `"Upgrade & Reboot"` (disabled until name matches).
- Reconnection State Machine:
  - `idle` -> `backing_up` -> `issuing` -> `rebooting` -> `online`.
  - Live 3-second heartbeat polling detects when router boots back up, automatically displaying green success state with new version.

---

## 4. Verification Plan

### Automated Backend Tests
1. `tests/test_routeros_firmware_transport.py`:
   - Mock RouterOS `/system/package/update` and `/system/routerboard` responses.
   - Test channel setting, update checking, and install command dispatch.
   - Test RouterBOOT status parsing and bootloader upgrade dispatch.
2. `tests/test_changelog_service.py`:
   - Test version format regex validation and rejection of invalid/traversal paths.
   - Test upstream HTTP fetch, 256 KB size limit enforcement, and caching behavior.
   - Test 60-second negative TTL on network failures.
3. `tests/test_firmware_api.py`:
   - Test `GET /firmware` status schema.
   - Test `POST /firmware/upgrade` safety gate: reject name mismatch, reject up-to-date router, verify pre-upgrade backup creation, verify command execution.

### Automated Frontend Tests
1. `frontend/src/components/RouterFirmwareModal.test.jsx`:
   - Verify dual cards render installed and latest versions accurately.
   - Verify changelog search filters entries.
   - Verify "Upgrade & Reboot" button remains disabled until router name is typed exactly.
   - Verify state progression from `idle` through `rebooting` to `online`.

### Verification Commands
```bash
.venv/bin/pytest tests/test_routeros_firmware_transport.py tests/test_changelog_service.py tests/test_firmware_api.py -v
.venv/bin/pytest
.venv/bin/ruff check
npm test -- src/components/RouterFirmwareModal.test.jsx
npm test
npm run build
```


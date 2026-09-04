# RouterOS Firmware & Update Intelligence Implementation Plan

> **Status: implemented.** This file was committed empty alongside the feature; it
> is written here to record what was actually built, so the plan matches its
> sibling documents and the spec has a counterpart to review against. Checkboxes
> are ticked where the code and tests exist in the tree.

**Goal:** Give MikroMan the full RouterOS firmware lifecycle — multi-channel update checking, upstream release notes, RouterBOOT bootloader visibility, and an upgrade path that cannot lose configuration or strand the operator mid-reboot.

**Architecture:**
- Transport: `FirmwareMixin` (`backend/app/services/routeros/firmware.py`) over `/system/package/update` and `/system/routerboard`, composed into `RouterOSClient`.
- Changelog: `backend/app/services/changelog.py` — anchored version whitelist, 256 KB body cap, bounded FIFO cache, 60 s negative cache.
- API: `backend/app/api/v1/endpoints/firmware.py`, mounted at `/api/v1/routers/{router_id}/firmware`.
- Safety: the upgrade endpoint takes a **pinned pre-upgrade backup** through `run_router_backup` before issuing any command, and refuses unless the operator has typed the router's exact name.
- Frontend: `RouterFirmwareModal.jsx` (dual cards, searchable changelog, confirmation gate, reconnect state machine) plus a navbar update badge.

**Tech Stack:** FastAPI, Pydantic v2, httpx, RouterOS REST API, React 18, lucide-react, pytest + respx, Vitest.

## Global Constraints
- **Never change a port.** Firmware work touches `/system/package/update` and `/system/routerboard` only; `/ip/service` is off limits.
- Changelog versions must match `^\d+\.\d+(\.\d+)?$` exactly — anchored, so no path traversal reaches `upgrade.mikrotik.com`.
- An upgrade may not be dispatched without a successful pinned backup first.
- Channel values are restricted to `stable`, `long-term`, `testing`, `development` at both the schema and the transport layer.
- Full test pass: `.venv/bin/pytest`, `npm test`. Linter clean: `.venv/bin/ruff check`. Build clean: `npm run build`.

---

### Task 1: Firmware transport mixin

**Files:**
- Create: `backend/app/services/routeros/firmware.py`
- Modify: `backend/app/services/routeros/client.py`
- Test: `tests/test_routeros_firmware_transport.py`

**Interfaces:**
- Produces: `get_package_update_status()`, `check_for_package_updates()`, `set_package_update_channel(channel)`, `install_package_update()`, `get_routerboard_status()`, `upgrade_routerboard_firmware()`.

- [x] **Step 1: Failing test for version normalisation and availability**

RouterOS reports `installed-version` as `"7.15.2 (stable)"`. The channel suffix has to come off before any comparison, or every router looks out of date.

```python
@pytest.mark.asyncio
@respx.mock
async def test_installed_version_strips_the_channel_suffix():
    respx.get(f"{BASE}/system/package/update").mock(return_value=httpx.Response(200, json={
        "installed-version": "7.15.2 (stable)", "latest-version": "7.16.1",
        "channel": "stable", "status": "New version is available",
    }))
    status = await make_client().get_package_update_status()
    assert status["installed_version"] == "7.15.2"
    assert status["update_available"] is True
```

- [x] **Step 2: Implement `get_package_update_status`** — `re.sub(r"\s*\(.*?\)", "", version)`, then `update_available = bool(latest and latest != installed) or "new version" in status.lower()`.

- [x] **Step 3: `get_routerboard_status` degrades on non-RouterBOARD hardware** — CHR and x86 have no `/system/routerboard`; the call returns a fully-populated "not a routerboard" dict rather than raising, so the modal renders one card instead of erroring.

- [x] **Step 4: `set_package_update_channel` validates before writing** — an unknown channel raises `ValueError` locally; a valid one is written and immediately re-checked.

- [x] **Step 5: Run `.venv/bin/pytest tests/test_routeros_firmware_transport.py -v`**

---

### Task 2: Upstream changelog service

**Files:**
- Create: `backend/app/services/changelog.py`
- Test: `tests/test_changelog_service.py`

**Interfaces:**
- Produces: `fetch_changelog(version) -> str`, raising `ValueError("Invalid version format")` on anything the whitelist rejects.

- [x] **Step 1: Failing test for the version whitelist**

```python
@pytest.mark.parametrize("bad", ["../../etc/passwd", "7.15.2/../x", "latest", ""])
def test_invalid_versions_are_refused(bad):
    with pytest.raises(ValueError):
        validate_version(bad)
```

- [x] **Step 2: Anchored regex** `^\d+\.\d+(\.\d+)?$` — the anchors are the whole defence; an unanchored pattern would match `../7.15.2`.
- [x] **Step 3: 256 KB cap** — read in chunks and abort past the limit rather than buffering whatever upstream sends.
- [x] **Step 4: Bounded FIFO cache (32 versions) + 60 s negative cache** — released changelogs are immutable so a positive entry never needs invalidating; a failure is cached briefly so an air-gapped install does not retry on every modal open.
- [x] **Step 5: Run `.venv/bin/pytest tests/test_changelog_service.py -v`**

---

### Task 3: Schemas and REST endpoints

**Files:**
- Create: `backend/app/schemas/firmware.py`, `backend/app/api/v1/endpoints/firmware.py`
- Modify: `backend/app/api/v1/router.py`
- Test: `tests/test_firmware_api.py`

**Interfaces:**
- Consumes: `FirmwareMixin` (Task 1), `fetch_changelog` (Task 2), `run_router_backup` from the backups sub-project.
- Produces: `RouterFirmwareStatusOut`, `PackageUpdateInfo`, `RouterBoardInfo`, `FirmwareUpgradePayload`, `BootloaderUpgradePayload`, `ChangelogOut`.

- [x] **Step 1: Failing test for the confirmation gate**

```python
async def test_upgrade_refuses_a_name_mismatch(api_client):
    res = await api_client.post("/api/v1/routers/1/firmware/upgrade",
                                json={"confirm_name": "wrong", "stage_bootloader": True})
    assert res.status_code == 400
    assert "Confirmation mismatch" in res.json()["detail"]
```

- [x] **Step 2: Failing test that an up-to-date router is refused** — nothing to install means nothing to reboot for.
- [x] **Step 3: Failing test that a backup failure aborts the upgrade** — `run_router_backup` raising must leave `install_package_update` uncalled and answer `500`. Asserting the *absence* of the install is what actually pins the ordering; asserting that both ran would pass even if the install went first.
- [x] **Step 4: Implement the five safety steps in order** — name match → update available → pinned backup → optional bootloader staging → `install_package_update()`; return `{"status": "rebooting", "backup_id", "target_version"}`.
- [x] **Step 5: Mount the router with its `/routers/{router_id}/firmware` prefix and run `.venv/bin/pytest tests/test_firmware_api.py -v`**

---

### Task 4: Frontend modal, API client and navbar badge

**Files:**
- Create: `frontend/src/components/RouterFirmwareModal.jsx`
- Modify: `frontend/src/api/client.js`, `frontend/src/components/Navbar.jsx`, `frontend/src/App.jsx`, `frontend/src/i18n/translations.js`
- Test: `frontend/src/components/RouterFirmwareModal.test.jsx`

- [x] **Step 1: Failing test that the upgrade button is disabled until the name matches exactly**
- [x] **Step 2: Dual cards** — RouterOS packages (installed vs latest, channel select) and RouterBOOT (current vs upgrade firmware), the second hidden entirely on non-RouterBOARD hardware.
- [x] **Step 3: Searchable changelog pane** fed by `api.getChangelog`.
- [x] **Step 4: Reconnect state machine** `idle → backing_up → issuing → rebooting → online`, polling every 3 s until the router answers with its new version.
- [x] **Step 5: Navbar badge** driven by `firmwareStatus.packages.update_available`, refreshed on the slow poll (`SLOW_POLL_MS`, 5 min) rather than the 6 s data poll — a firmware check is two REST calls to the router and the answer changes monthly.
- [x] **Step 6: Run `npm test` and `npm run build`**

---

## Follow-ups applied after the review pass (2026-09-04)

- `ruff --fix` on `firmware.py`, `schemas/firmware.py`, `changelog.py` and their tests (unsorted imports, three unused imports in `tests/test_routeros_firmware_transport.py`).
- The navbar's firmware poll was riding the 6-second dashboard poll; moved behind `SLOW_POLL_MS`.
- `api.getChangelog` was present but the backups sibling feature's client methods were missing entirely — see `frontend/src/api/client.contract.test.js`, which now fails the build if any component calls a method the client does not export.

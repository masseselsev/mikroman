# Lockout Prevention ("Write Guards") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement automated Write Guards that intercept and refuse dangerous or invalid RouterOS mutations (preventing self-lockout, management interface throttling, accidental deletion of foreign queues/rules, and invalid queue rate relationships).

**Architecture:** Two-tier protection architecture. Tier 1 provides validation and clean HTTP 400 error reporting in endpoints (`devices.py`, `users.py`). Tier 2 provides pure, fail-safe wire-level guard enforcement within `RouterOSClient` (`FirewallMixin`, `QueueMixin`) to prevent any unvetted command packet from reaching the router hardware.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (async), Pytest, RouterOS REST/API client.

## Global Constraints
- Pure guard functions must be deterministic, stateless, and execute without network or database I/O.
- Deletions or updates of foreign resources (rules lacking `comment.startswith("mikroman:")`) must be refused.
- Protected targets include: loopbacks (`127.0.0.1`, `::1`), wildcards (`0.0.0.0/0`), router gateway IP, and the local host/container IP.
- Rate limits must satisfy `max-limit >= limit-at`.
- All tests must run cleanly under `.venv/bin/pytest` and pass `.venv/bin/ruff check`.

---

### Task 1: Pure Guard Functions & Invariants Module

**Files:**
- Create: `backend/app/services/guards.py`
- Create: `tests/test_write_guards.py`

**Interfaces:**
- Produces:
  - `class WriteGuardViolation(ValueError)`
  - `def parse_bps(rate_str: str) -> int`
  - `def guard_immune_targets(target: str, immune_ips: set[str], action: str = "block") -> None`
  - `def guard_foreign_resources(comment: str | None, action: str, resource_type: str) -> None`
  - `def guard_queue_invariants(target: str, max_limit: str, limit_at: str | None = None, parent: str | None = None, name: str | None = None) -> None`

- [ ] **Step 1: Write the failing unit tests for pure guards**

In `tests/test_write_guards.py`:
```python
import pytest
from backend.app.services.guards import (
    WriteGuardViolation,
    parse_bps,
    guard_immune_targets,
    guard_foreign_resources,
    guard_queue_invariants,
)

def test_parse_bps():
    assert parse_bps("0") == 0
    assert parse_bps("5M") == 5_000_000
    assert parse_bps("100k") == 100_000
    assert parse_bps("1G") == 1_000_000_000
    assert parse_bps("500") == 500
    with pytest.raises(ValueError):
        parse_bps("invalid")

def test_guard_immune_targets():
    immune = {"192.168.88.1", "192.168.88.250"}
    
    # Allowed regular targets
    guard_immune_targets("192.168.88.45", immune, action="block")
    guard_immune_targets("192.168.88.45", immune, action="queue")

    # Refused immune targets
    with pytest.raises(WriteGuardViolation) as exc:
        guard_immune_targets("127.0.0.1", immune, action="block")
    assert "loopback" in str(exc.value).lower()

    with pytest.raises(WriteGuardViolation) as exc:
        guard_immune_targets("0.0.0.0/0", immune, action="queue")
    assert "wildcard" in str(exc.value).lower()

    with pytest.raises(WriteGuardViolation) as exc:
        guard_immune_targets("192.168.88.1", immune, action="block")
    assert "protected" in str(exc.value).lower()

def test_guard_foreign_resources():
    # Managed resource passes
    guard_foreign_resources("mikroman:managed:user_1", action="delete", resource_type="queue")
    guard_foreign_resources("mikroman:paused:Alex", action="delete", resource_type="address-list")

    # Foreign resource refused
    with pytest.raises(WriteGuardViolation) as exc:
        guard_foreign_resources("Admin Winbox rule", action="delete", resource_type="queue")
    assert "foreign" in str(exc.value).lower()

    with pytest.raises(WriteGuardViolation):
        guard_foreign_resources(None, action="delete", resource_type="queue")

def test_guard_queue_invariants():
    # Valid queue
    guard_queue_invariants(target="192.168.88.45", max_limit="10M/20M", limit_at="2M/5M", parent="none", name="dev_1")
    guard_queue_invariants(target="192.168.88.45", max_limit="0/0", limit_at="0/0")

    # Invalid speed format
    with pytest.raises(WriteGuardViolation) as exc:
        guard_queue_invariants(target="192.168.88.45", max_limit="invalid_rate")
    assert "format" in str(exc.value).lower()

    # limit_at exceeds max_limit
    with pytest.raises(WriteGuardViolation) as exc:
        guard_queue_invariants(target="192.168.88.45", max_limit="5M/5M", limit_at="10M/2M")
    assert "cannot exceed" in str(exc.value).lower()

    # Circular parent
    with pytest.raises(WriteGuardViolation) as exc:
        guard_queue_invariants(target="192.168.88.45", max_limit="10M/10M", parent="dev_1", name="dev_1")
    assert "circular" in str(exc.value).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_write_guards.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.app.services.guards'`

- [ ] **Step 3: Implement `backend/app/services/guards.py`**

```python
"""Write safety guards for RouterOS mutations.

Prevents self-lockout, accidental throttling of management endpoints,
pruning of foreign WinBox queues/rules, and invalid rate configurations.
"""
import re
from typing import Optional, Set

RATE_PATTERN = re.compile(r"^(\d+)([kKMGT]?)/(\d+)([kKMGT]?)$")
MULTIPLIERS = {
    "": 1,
    "k": 1_000,
    "K": 1_000,
    "M": 1_000_000,
    "m": 1_000_000,
    "G": 1_000_000_000,
    "g": 1_000_000_000,
    "T": 1_000_000_000_000,
    "t": 1_000_000_000_000,
}

IMMUNE_WILDCARDS = {"0.0.0.0", "0.0.0.0/0", "::/0", "255.255.255.255"}
IMMUNE_LOOPBACKS = {"127.0.0.1", "::1", "localhost"}


class WriteGuardViolation(ValueError):
    """Raised when an operation is refused by a RouterOS write safety guard."""

    def __init__(self, guard_name: str, reason: str, target: str):
        super().__init__(f"[{guard_name}] Refused write for {target}: {reason}")
        self.guard_name = guard_name
        self.reason = reason
        self.target = target


def parse_bps(val: str) -> int:
    """Parse bandwidth rate like '5M', '100k', or '0' into bits per second."""
    raw = str(val).strip()
    if raw.isdigit():
        return int(raw)
    match = re.match(r"^(\d+)([kKMGT]?)$", raw)
    if not match:
        raise ValueError(f"Invalid rate string: {val}")
    num, unit = match.groups()
    return int(num) * MULTIPLIERS.get(unit, 1)


def parse_pair(pair_str: str) -> tuple[int, int]:
    """Parse a pair rate like '5M/10M' or '0/0' into (upload_bps, download_bps)."""
    clean = str(pair_str).strip()
    if clean in ("0", "0/0", "unlimited", "none"):
        return 0, 0
    match = RATE_PATTERN.match(clean)
    if not match:
        raise ValueError(f"Invalid rate pair format: {pair_str}")
    up_num, up_unit, down_num, down_unit = match.groups()
    return (
        int(up_num) * MULTIPLIERS.get(up_unit, 1),
        int(down_num) * MULTIPLIERS.get(down_unit, 1),
    )


def guard_immune_targets(target: str, immune_ips: Set[str], action: str = "block") -> None:
    """Refuse blocking or throttling of immune infrastructure and host targets."""
    clean_target = str(target).strip()
    ip_only = clean_target.split("/")[0]

    if ip_only in IMMUNE_LOOPBACKS or clean_target in IMMUNE_LOOPBACKS:
        raise WriteGuardViolation(
            "ImmuneTargetGuard",
            "Target is a loopback address and cannot be modified",
            clean_target,
        )

    if clean_target in IMMUNE_WILDCARDS:
        raise WriteGuardViolation(
            "ImmuneTargetGuard",
            "Target is a wildcard/broadcast and cannot be throttled or blocked",
            clean_target,
        )

    if ip_only in immune_ips or clean_target in immune_ips:
        raise WriteGuardViolation(
            "ImmuneTargetGuard",
            f"Target {clean_target} is a protected management/host IP",
            clean_target,
        )


def guard_foreign_resources(comment: Optional[str], action: str, resource_type: str) -> None:
    """Refuse destructive operations on resources not managed by MikroMan."""
    c = (comment or "").strip()
    if not c.startswith("mikroman:"):
        raise WriteGuardViolation(
            "ForeignResourceGuard",
            f"Cannot {action} foreign {resource_type} without 'mikroman:' comment prefix",
            c or "<empty>",
        )


def guard_queue_invariants(
    target: str,
    max_limit: str,
    limit_at: Optional[str] = None,
    parent: Optional[str] = None,
    name: Optional[str] = None,
) -> None:
    """Verify queue parameters comply with RouterOS relational requirements."""
    try:
        max_up, max_down = parse_pair(max_limit)
    except ValueError as e:
        raise WriteGuardViolation("QueueInvariantGuard", str(e), target)

    if limit_at:
        try:
            at_up, at_down = parse_pair(limit_at)
            if max_up > 0 and at_up > max_up:
                raise WriteGuardViolation(
                    "QueueInvariantGuard",
                    f"Upload limit-at ({at_up} bps) cannot exceed max-limit ({max_up} bps)",
                    target,
                )
            if max_down > 0 and at_down > max_down:
                raise WriteGuardViolation(
                    "QueueInvariantGuard",
                    f"Download limit-at ({at_down} bps) cannot exceed max-limit ({max_down} bps)",
                    target,
                )
        except ValueError as e:
            raise WriteGuardViolation("QueueInvariantGuard", str(e), target)

    if parent and name and parent.strip() == name.strip():
        raise WriteGuardViolation(
            "QueueInvariantGuard",
            f"Queue cannot be its own parent: circular parentage on {name}",
            target,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_write_guards.py -v`  
Expected: PASS (all tests pass)

---

### Task 2: Wire-Level Enforcement in `RouterOSClient`

**Files:**
- Modify: `backend/app/services/routeros/client.py`
- Modify: `backend/app/services/routeros/firewall.py`
- Modify: `backend/app/services/routeros/queues.py`
- Test: `tests/test_write_guards.py`

**Interfaces:**
- Consumes: `backend.app.services.guards` functions
- Enhances:
  - `RouterOSClient.get_immune_ips() -> set[str]`
  - `FirewallMixin.add_to_address_list`
  - `FirewallMixin.remove_from_address_list`
  - `FirewallMixin.delete_firewall_filter_rule`
  - `FirewallMixin.delete_firewall_raw_rule`
  - `QueueMixin.create_simple_queue`
  - `QueueMixin.update_simple_queue`
  - `QueueMixin.delete_simple_queue`

- [ ] **Step 1: Write integration tests for client-level guard interception**

Append to `tests/test_write_guards.py`:
```python
@pytest.mark.asyncio
async def test_client_refuses_blocking_immune_host():
    from backend.app.services.routeros.client import RouterOSClient
    from backend.app.schemas.router import RouterConfig
    
    cfg = RouterConfig(host="192.168.88.1", username="admin", password="x")
    client = RouterOSClient(cfg)
    client._immune_ips = {"192.168.88.1", "192.168.88.24"}

    # Attempting to add router IP to mikroman_blocked must raise WriteGuardViolation
    with pytest.raises(WriteGuardViolation) as exc:
        await client.add_to_address_list(address="192.168.88.1", list_name="mikroman_blocked")
    assert "protected management" in str(exc.value).lower()

@pytest.mark.asyncio
async def test_client_refuses_deleting_foreign_queue():
    from backend.app.services.routeros.client import RouterOSClient
    from backend.app.schemas.router import RouterConfig
    
    cfg = RouterConfig(host="192.168.88.1", username="admin", password="x")
    client = RouterOSClient(cfg)

    # Attempting to delete queue without mikroman: comment
    with pytest.raises(WriteGuardViolation) as exc:
        await client.delete_simple_queue(queue_id="*A", comment="Manual-Queue")
    assert "foreign" in str(exc.value).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_write_guards.py -k "test_client_" -v`  
Expected: FAIL

- [ ] **Step 3: Update `RouterOSClient`, `FirewallMixin`, and `QueueMixin`**

In `backend/app/services/routeros/client.py`:
Add `get_immune_ips()`:
```python
    def get_immune_ips(self) -> set[str]:
        """Return the set of protected IPs for this router connection."""
        if hasattr(self, "_immune_ips") and self._immune_ips:
            return set(self._immune_ips)
        immune = {self.config.host}
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((self.config.host, 80))
            local_ip = s.getsockname()[0]
            s.close()
            immune.add(local_ip)
        except Exception:
            pass
        return immune
```

In `backend/app/services/routeros/firewall.py`:
Call `guard_immune_targets` in `add_to_address_list`:
```python
        if list_name == "mikroman_blocked":
            immune = self.get_immune_ips() if hasattr(self, "get_immune_ips") else set()
            from backend.app.services.guards import guard_immune_targets
            guard_immune_targets(address, immune, action="block")
```
Call `guard_foreign_resources` in `remove_from_address_list`, `delete_firewall_filter_rule`, `delete_firewall_raw_rule` when comment is supplied.

In `backend/app/services/routeros/queues.py`:
Call `guard_queue_invariants` and `guard_immune_targets` in `create_simple_queue` and `update_simple_queue`:
```python
        from backend.app.services.guards import guard_queue_invariants, guard_immune_targets
        target = payload.get("target", "")
        max_limit = payload.get("max-limit", "0/0")
        limit_at = payload.get("limit-at")
        name = payload.get("name")
        parent = payload.get("parent")
        guard_queue_invariants(target=target, max_limit=max_limit, limit_at=limit_at, parent=parent, name=name)
        if max_limit not in ("0/0", "0"):
            immune = self.get_immune_ips() if hasattr(self, "get_immune_ips") else set()
            guard_immune_targets(target, immune, action="queue")
```
Call `guard_foreign_resources` in `delete_simple_queue`:
```python
        if comment is not None:
            from backend.app.services.guards import guard_foreign_resources
            guard_foreign_resources(comment, action="delete", resource_type="queue")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_write_guards.py -v`  
Expected: PASS

---

### Task 3: API & Background Reconciliation Resilience

**Files:**
- Modify: `backend/app/api/v1/endpoints/devices.py`
- Modify: `backend/app/services/traffic_controller.py`
- Test: `tests/test_write_guards.py`

**Interfaces:**
- Catches: `WriteGuardViolation`
- Produces: Friendly HTTP 400 in API endpoints, structured warnings in background loops without throwing.

- [ ] **Step 1: Write integration tests for API rejection and background loop handling**

Append to `tests/test_write_guards.py`:
```python
@pytest.mark.asyncio
async def test_api_pause_refused_on_immune_device(api_client):
    from backend.app.db.models import Device, User
    
    async with api_client.session_factory() as s:
        user = User(name="AdminUser")
        s.add(user)
        await s.commit()
        dev = Device(user_id=user.id, mac_address="00:11:22:33:44:55", ip_address="127.0.0.1", is_active=True)
        s.add(dev)
        await s.commit()
        dev_id = dev.id

    res = await api_client.patch(f"/api/v1/devices/{dev_id}", json={"is_paused": True})
    assert res.status_code == 400
    assert "WriteGuard" in res.json().get("detail", "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_write_guards.py -k "test_api_pause" -v`  
Expected: FAIL

- [ ] **Step 3: Update `devices.py` and `traffic_controller.py`**

In `backend/app/api/v1/endpoints/devices.py`:
Catch `WriteGuardViolation` and raise `HTTPException(status_code=400, detail=str(e))`.

In `backend/app/services/traffic_controller.py`:
Wrap calls in `sync_device_queue`, `sync_all_queues`, `pause_user_internet`:
```python
        except WriteGuardViolation as e:
            logger.warning(f"Skipped queue/pause operation due to WriteGuard: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_write_guards.py -v`  
Expected: PASS

---

### Task 4: Full Suite Regression & Linting Gate

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/pytest`  
Expected: PASS (all 465+ tests pass)

- [ ] **Step 2: Run ruff linter**

Run: `.venv/bin/ruff check backend/app/services/guards.py backend/app/services/routeros/ backend/app/api/v1/endpoints/devices.py tests/test_write_guards.py`  
Expected: `All checks passed!`

- [ ] **Step 3: Record lesson in `docs/LESSONS.md`**

Format: `[2026-09-04] Problem: ... -> Solution: ...`


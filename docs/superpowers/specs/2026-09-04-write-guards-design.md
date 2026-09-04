# Design Specification: Lockout Prevention ("Write Guards") for MikroMan

**Date:** 2026-09-04  
**Author:** Antigravity  
**Status:** Approved for Implementation Planning  
**Target Module:** `backend/app/services/guards.py`, `backend/app/services/routeros/`

---

## 1. Context & Motivation

MikroMan manages RouterOS devices by dynamically synchronizing:
* **Simple Queues:** Setting bandwidth limits for users, devices, and unassigned quarantine devices.
* **Firewall Filter & Raw Drop Rules:** Controlling the `mikroman_blocked` address list to pause or resume internet access for specific users and devices.
* **Firewall Mangle Rules:** Maintaining accounting counters.

Without explicit safety guards, automated background reconciliation or operator error (such as clicking "Pause" on an unassigned device that happens to be the MikroMan host container, or applying a 5M/5M limit to a subnet covering the router's own IP) can:
1. Sever the connection between MikroMan and the router (locking the dashboard out).
2. Sever administrator management access (WinBox, SSH, WebFig).
3. Accidentally modify or delete third-party or manually created queues in WinBox during automatic prune passes.
4. Push invalid queue configurations (e.g. `limit-at > max-limit`) that cause RouterOS API transactions to fail.

This specification introduces **Write Guards**: a pure, fail-safe validation layer that intercepts all mutation commands before they reach RouterOS.

---

## 2. Core Architecture

The design follows a **Two-Tier Defense**:
1. **Tier 1 (Service/API pre-validation):** High-level validation in endpoints (`devices.py`, `users.py`) to provide immediate, user-friendly HTTP 400 responses.
2. **Tier 2 (Wire-level client enforcement):** Hard runtime interception within `RouterOSClient` (specifically in `QueueMixin` and `FirewallMixin`) that raises `WriteGuardViolation` before any command packet is dispatched.

```
[Web UI / Background Reconcile]
               │
               ▼
   [Endpoints / Services]   ──(Tier 1: Pre-validation & Friendly HTTP 400)
               │
               ▼
      [RouterOSClient]      ──(Tier 2: Pure Write Guards Interception)
               │
       ┌───────┴───────┐
       ▼               ▼
[WriteGuardViolation]  [RouterOS Hardware]
(Refused & Logged)     (Safe Mutation)
```

---

## 3. Detailed Guard Specifications

### 3.1 Exception Definition
Defined in `backend/app/services/guards.py`:
```python
class WriteGuardViolation(ValueError):
    """Raised when an operation is refused by a RouterOS write safety guard."""
    def __init__(self, guard_name: str, reason: str, target: str):
        super().__init__(f"[{guard_name}] Refused write for {target}: {reason}")
        self.guard_name = guard_name
        self.reason = reason
        self.target = target
```

### 3.2 Guard 1: Immune Targets (`guard_immune_targets`)
Protects management and infrastructure endpoints from being throttled or blocked.

* **Protected IP Set:**
  * Loopbacks: `127.0.0.1`, `::1`
  * Wildcards/Broadcast: `0.0.0.0`, `0.0.0.0/0`, `255.255.255.255`
  * Router Host & Interfaces: The IP used to connect to the router (`client.config.host`) and any IP assigned to the router's local interfaces.
  * MikroMan Host: The IP address of the MikroMan host/container discovered via the active socket connection.
  * Configured Exemptions: Any IP addresses listed in `AppSetting("immune_ips")`.

* **Refusal Rules:**
  * **Block Action:** Adding any immune IP to `list_name="mikroman_blocked"` is strictly refused.
  * **Queue Action:** Creating or updating a Simple Queue with a target matching an immune IP that applies a non-zero throttling limit (`max-limit != "0/0"`) is strictly refused.

### 3.3 Guard 2: Foreign Resources (`guard_foreign_resources`)
Protects rules and queues configured manually by network administrators in WinBox or by other automation tools.

* **Refusal Rules:**
  * Any `delete` or destructive update on:
    * `/queue/simple`
    * `/ip/firewall/address-list`
    * `/ip/firewall/filter`
    * `/ip/firewall/raw`
    * `/ip/firewall/mangle`
  * Must verify that the resource's `comment` attribute starts with `mikroman:`.
  * If the comment is missing or does not start with `mikroman:`, deletion or mutation is strictly refused.

### 3.4 Guard 3: Queue Invariants (`guard_queue_invariants`)
Ensures queues conform to RouterOS relational invariants before execution.

* **Refusal Rules:**
  * **Bandwidth Format:** Rates must match `^[0-9]+[kKMGT]?/[0-9]+[kKMGT]?$` or `^0/0$`.
  * **Limit Invariant:** `max-limit` must be `>= limit-at` for both upload and download. If `limit-at` exceeds `max-limit`, RouterOS rejects the transaction; the guard catches this before the network call.
  * **Hierarchy Invariant:** Rejects self-referencing parents (`parent == name`) and empty parent strings when parent is specified.

---

## 4. Integration Points

### 4.1 `RouterOSClient`
* `RouterOSClient` caches the router's local interface IPs and resolves its own outbound local IP.
* `QueueMixin.create_simple_queue` and `QueueMixin.update_simple_queue` call `guard_queue_invariants` and `guard_immune_targets`.
* `QueueMixin.delete_simple_queue` calls `guard_foreign_resources`.
* `FirewallMixin.add_to_address_list` calls `guard_immune_targets`.
* `FirewallMixin.remove_from_address_list`, `delete_firewall_filter_rule`, `delete_firewall_raw_rule` call `guard_foreign_resources`.

### 4.2 Endpoint Error Handling
* Endpoints (`backend/app/api/v1/endpoints/devices.py`, `backend/app/api/v1/endpoints/traffic.py`) wrap actions in `try...except WriteGuardViolation as e:` and return:
  ```json
  {
    "success": false,
    "detail": f"Operation refused by safety guard: {e.reason}"
  }
  ```
  with HTTP status `400 Bad Request`.

### 4.3 Background Reconcile Resilience
* `TrafficController.sync_all_queues` and `reconcile_device_limits` catch `WriteGuardViolation`, log a warning, and skip the item rather than crashing the background worker.

---

## 5. Testing & Verification

1. **Unit Tests (`tests/test_write_guards.py`):**
   * Test immune target rejection across IPv4 and IPv6 loopback, router gateway, and local host IPs.
   * Test rejection of foreign queue/rule deletion when `comment` does not start with `mikroman:`.
   * Test rate parsing and rejection of `limit-at > max-limit`.
   * Test rejection of circular parentage.
2. **Integration Tests:**
   * Verify that attempting to pause an immune device via API returns HTTP 400.
   * Verify that queue reconciliation skips immune router IPs and logs a warning without throwing.


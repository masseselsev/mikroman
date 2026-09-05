# 🚦 Lockout Prevention & Write Guards

To ensure operational stability, all mutations directed at RouterOS pass through an independent, pure validation layer (`backend/app/services/guards.py`) before network packets are constructed.

---

## 🛡️ Guard Architecture

```
   [API Request / Background Sync]
                 |
                 v
   +-------------------------------+
   |      Pure Guard Invariants    |  <-- Stateless, deterministic,
   |         (`guards.py`)         |      no I/O, no database access
   +---------------+---------------+
                   | Pass
                   v
   +-------------------------------+
   |   RouterOS Transport Mixin    |  <-- REST API execution
   +-------------------------------+
```

If any guard fails, a `WriteGuardViolation` exception is raised:
- **API Endpoints**: Caught and converted to an explicit HTTP 400 Bad Request detailing the guard and violation target.
- **Background Sync Workers**: Caught, logged as a warning, and that specific mutation is skipped without terminating the background reconciliation loop.

---

## 🔒 Protected Targets (Immune Targets)

The system enforces strict immunity for administrative and gateway targets:
1. **Loopbacks & Localhost**: `127.0.0.1`, `::1`, `localhost`.
2. **Wildcards & Broadcasts**: `0.0.0.0`, `0.0.0.0/0`, `::/0`, `255.255.255.255`.
3. **Management Host**: The IP address MikroMan uses to reach the router.
4. **Container Outbound IP**: The egress address of the application container.
5. **Configured Immune IPs**: Custom addresses defined in app settings (e.g. secondary management workstations, jump boxes).

**Enforcement:**
- Immune targets can never be added to `mikroman_blocked` address lists.
- Simple Queues cannot target immune hosts.
- Active sockets involving immune targets cannot be killed via connection tracker endpoints.

---

## 🏷️ Foreign Resource Protection

MikroMan operates alongside human administrators using WinBox or WebFig:
- Any queue, firewall rule, address list entry, or logging rule **lacking** the `mikroman:` comment prefix is classified as a **Foreign Resource**.
- Foreign resources are strictly read-only to MikroMan.
- Pruning routines and deletion requests for foreign resources are rejected unconditionally.

---

## ⚖️ Queue Invariants

Before dispatching Simple Queue modifications:
1. **Rate Syntax Validation**: Rate parameters must strictly match `^(\d+)([kKMGT]?)/(\d+)([kKMGT]?)$`.
2. **Relational Limit Validation**: Guaranteed `limit-at <= max-limit`. RouterOS rejects inverted limits; MikroMan blocks them pre-flight.
3. **Circular Parentage Prevention**: A queue cannot declare itself as its own parent (`parent != name`).

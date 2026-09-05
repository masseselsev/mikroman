# 🛡️ Multi-Router Management & Lifecycle

MikroMan natively supports managing multiple independent RouterOS devices from a single installation.

---

## 🏢 Complete Environment Isolation

Each managed router represents an isolated operational domain:
- **Isolated Entities**: Users, devices, Simple Queues, FastTrack exemptions, and firewall rules exist strictly within their parent router context.
- **Heuristic Boundary Enforcement**: MAC-rotation merge heuristics, adapter linking, and hostname matching never evaluate candidates across router boundaries.
- **Per-Router Timezone Offsets**: The local UTC offset is maintained per router (`router_gmt_offset_minutes_<id>`). Daily rollups, billing boundaries, and time-series telemetry reflect the router's physical wall-clock.

---

## 🔄 Hardware Swap Workflow (`Change Router`)

When replacing router hardware (e.g. hardware upgrade or disaster recovery):
1. Click the ↻ button on the target router row in *Settings → Routers*.
2. Enter the new router's connection details and credentials.
3. Upon a successful connection test, the new host, port, credentials, and serial number are written onto the **existing** database row.
4. Historical traffic totals, assigned users, device profiles, and settings remain seamlessly attached.
5. **History Mode Choice**:
   - `keep`: Retains previous hardware metric graphs (CPU, RAM, temperature, interface history).
   - `reset_hardware`: Clears hardware metrics for fresh silicon while preserving all traffic rollups and user accounting.

---

## 📦 Archive vs Purge Lifecycle

Deleting a router provides two operational choices:

### 1. Archive (`mode=archive`, Default)
- Sets `routers.archived_at` timestamp.
- Hides the router from the navigation selector and pauses background polling loops.
- All historical traffic, devices, users, and rollups remain preserved in the database.
- If the hardware is reconnected later, reading its RouterBOARD `serial_number` automatically restores the archived record.

### 2. Permanent Purge (`mode=purge`)
- Requires typing the router's exact name as an explicit safety confirmation gate.
- Executes a transaction that permanently removes the router and cascades through all related records (rollups, metrics, logs, backups).

---

## 🔒 Automated SSL / TLS Provisioning

MikroMan can automatically enable HTTPS management on RouterOS:
1. Connects over plain HTTP to the RouterOS REST API.
2. Generates a self-signed TLS certificate directly on the router hardware using `/certificate/add`.
3. Binds the certificate to `/ip/service www-ssl`.
4. **Port Preservation Invariant**: Never overwrites `/ip/service` port or address configurations. The active port is queried from the router and adopted by MikroMan.

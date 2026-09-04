# Per-Device Live Connection Tracker, Offline Geo-IP & User Destination Analytics Design Spec

**Date:** 2026-09-04  
**Status:** Approved  
**Author:** Pair Programmer Agent & Operator  

---

## 1. Overview & Goals

This specification defines the architecture, data models, wire protocols, API endpoints, safety guards, and UI components for:
1. **Live Connection Tracking**: Real-time inspection of active connections from RouterOS (`/ip/firewall/connection`), attributing each socket to its owning device and user.
2. **Offline Geo-IP Resolution**: Instant, microsecond-latency lookup of remote destination IPs into country codes, country names, and flag emojis without external API rate-limits or privacy leakage.
3. **Safety-Guarded Connection Termination ("Kill")**: Allowing operators to terminate rogue or heavy connections on demand while guarding immune management streams against self-lockout.
4. **Persistent User Destination & Domain Analytics**: Aggregating historical traffic volume and hit counts per destination IP and domain name per user/device, with multi-column sorting (Total Traffic, Downloads, Uploads, Hits, Last Active).

---

## 2. Architecture & Components

```
+-------------------------------------------------------------+
|                      React Frontend                         |
|  +-------------------------+   +--------------------------+  |
|  |  LiveConnectionsModal   |   |   UserDestinationsTab    |  |
|  | (Auto-poll, Search,     |   | (Sortable Table: Domains |  |
|  |  Filter Chips, Kill)    |   |  & IPs, Hits, Volumes)   |  |
|  +-------------------------+   +--------------------------+  |
+------------------------------+------------------------------+
                               | REST API
+------------------------------v------------------------------+
|                    FastAPI Backend                          |
|  +-------------------------------------------------------+  |
|  | Endpoints:                                            |  |
|  |  * GET  /api/v1/connections                           |  |
|  |  * POST /api/v1/connections/{id}/kill                 |  |
|  |  * GET  /api/v1/analytics/users/{id}/destinations     |  |
|  +-------------------------------------------------------+  |
|         |                     |                     |       |
|  +------v------+       +------v------+       +------v-----+ |
|  | GeoIP Engine|       | WriteGuards |       |   SQLite   | |
|  | (In-Memory  |       | (Immune     |       | (Device/   | |
|  |  Lookup)    |       |  Session)   |       |  DestStat) | |
|  +-------------+       +-------------+       +------------+ |
|         |                                           |       |
|  +------v-------------------------------------------v-----+ |
|  |         RouterOSClient (ConnectionsMixin)              | |
|  +--------------------------------------------------------+ |
+------------------------------+------------------------------+
                               | REST (.proplist)
+------------------------------v------------------------------+
|                     MikroTik RouterOS                       |
|   /ip/firewall/connection  ·  /ip/dns/cache  ·  /remove     |
+-------------------------------------------------------------+
```

---

## 3. Detailed Specifications

### 3.1 Compact Offline Geo-IP Engine (`backend/app/services/geoip.py`)
- **Lookup Method**: `resolve_ip_location(ip: str) -> GeoLocation`
- **Output Schema (`GeoLocation`)**:
  - `country_code`: 2-letter ISO code (e.g. `"US"`, `"DE"`, `"UZ"`, `"RU"`, `"LOCAL"`).
  - `country_name`: Full country title (e.g. `"United States"`, `"Local Network"`).
  - `flag_emoji`: Regional indicator flag emoji (e.g. 🇺🇸, 🇩🇪, 🏠).
- **Engine Logic**:
  - Automatically identifies private, loopback, multicast, or link-local addresses (RFC 1918, RFC 3927, RFC 6890) and immediately returns `country_code="LOCAL"`, `flag_emoji="🏠"`.
  - Lookups run in memory against an embedded compact country CIDR index / MaxMind GeoLite2-Country database if available in `data/`, defaulting to built-in ranges.
  - Zero external HTTP requests; zero rate-limiting; execution under 1 microsecond.

### 3.2 RouterOS Connection & DNS Client (`backend/app/services/routeros/connections.py`)
- Integrated into `RouterOSClient` as `ConnectionsMixin`:
  - `get_active_connections(proplist: Optional[List[str]] = None) -> List[Dict[str, Any]]`:
    Calls `GET /rest/ip/firewall/connection`.
    Enforces `.proplist=.id,protocol,src-address,dst-address,reply-src-address,reply-dst-address,tcp-state,timeout,orig-rate,repl-rate,orig-bytes,repl-bytes,assured,fasttrack` to bound payload size.
  - `get_dns_cache_entries() -> Dict[str, str]`:
    Calls `GET /rest/ip/dns/cache`. Returns an IP-to-domain mapping dictionary (e.g. `{"142.250.190.46": "youtube.com"}`).
  - `remove_firewall_connection(connection_id: str, src_ip: Optional[str] = None, dst_ip: Optional[str] = None) -> bool`:
    Passes through Write Guard verification:
    - Verifies that neither `src_ip` nor `dst_ip` matches `router_client.get_immune_ips()`.
    - If an immune host or management port is targeted, raises `WriteGuardViolation` preventing self-disconnection.
    - Otherwise executes `POST /rest/ip/firewall/connection/remove` with `{"numbers": connection_id}`.

### 3.3 Database Model for User Destination Traffic (`backend/app/db/models.py`)
- New table `UserDestinationStat`:
  - `id`: Integer primary key, autoincrement.
  - `user_id`: Integer, ForeignKey(`users.id`, ondelete="CASCADE"), nullable=True.
  - `device_id`: Integer, ForeignKey(`devices.id`, ondelete="SET NULL"), nullable=True.
  - `destination_ip`: String(45), index=True, nullable=False.
  - `domain`: String(255), nullable=True, index=True.
  - `country_code`: String(8), nullable=True.
  - `bytes_in`: BigInteger, default=0 (download).
  - `bytes_out`: BigInteger, default=0 (upload).
  - `total_bytes`: BigInteger, default=0 (`bytes_in + bytes_out`), index=True.
  - `hit_count`: Integer, default=1, index=True.
  - `last_seen`: DateTime(timezone=True), default=utc_now, index=True.
- Migration: Managed via standard Alembic async migration.

### 3.4 API Endpoints

#### 1. Live Connections: `GET /api/v1/connections`
- **Query Params**:
  - `router_id`: Optional[int]
  - `device_id`: Optional[int]
  - `user_id`: Optional[int]
  - `protocol`: Optional[str] (`"tcp"`, `"udp"`, `"icmp"`)
  - `search`: Optional[str] (searches IP, domain, or device name)
  - `limit`: int = 250 (max 1000)
- **Response**: `APIResponse[List[LiveConnectionItem]]`
  - `id`: RouterOS connection ID
  - `protocol`: Protocol name
  - `src_ip`, `src_port`: Source endpoint
  - `dst_ip`, `dst_port`: Destination endpoint
  - `device_id`, `device_name`: Attributed device
  - `user_id`, `user_name`: Attributed user
  - `domain`: Associated domain if known
  - `country_code`, `country_name`, `flag_emoji`: Geo-IP info
  - `tcp_state`: TCP state (if TCP)
  - `orig_rate`, `repl_rate`: Current upload/download rates (bps)
  - `orig_bytes`, `repl_bytes`: Connection byte counters
  - `timeout`: Remaining connection timeout

#### 2. Kill Connection: `POST /api/v1/connections/{connection_id}/kill`
- **Body**:
  ```json
  {
    "router_id": 1,
    "src_ip": "192.168.88.100",
    "dst_ip": "198.51.100.20"
  }
  ```
- **Error Behavior**: If `src_ip` or `dst_ip` is an immune target, returns `HTTP 400 Bad Request` with `WriteGuard: Refused write...`.

#### 3. User Destination Statistics: `GET /api/v1/analytics/users/{user_id}/destinations`
- **Query Params**:
  - `sort_by`: `"total_bytes"` | `"bytes_in"` | `"bytes_out"` | `"hit_count"` | `"last_seen"` (default: `"total_bytes"`)
  - `order`: `"desc"` | `"asc"` (default: `"desc"`)
  - `device_id`: Optional[int]
  - `search`: Optional[str]
  - `limit`: int = 50
- **Response**: `APIResponse[List[UserDestinationStatItem]]`

### 3.5 Frontend UI Components

1. **`LiveConnectionsModal.jsx`**:
   - Header with active connection count and total throughput.
   - 3-second auto-poll interval with Pause/Resume button.
   - Filter chips (`All`, `TCP`, `UDP`, `Web 80/443`, `DNS`).
   - Search bar across IP, domain, country, device.
   - Sortable columns with color-coded protocol badges and country flags.
   - Row-level "Kill" action with confirmation popover.

2. **Integration into Existing Views**:
   - `Navbar.jsx`: "Connections" navigation button.
   - `UserCard.jsx` / `DeviceRow`: Activity pulse icon button to open `LiveConnectionsModal` pre-filtered by `device_id`.
   - `DeviceModal.jsx`: "Live Connections" button.
   - `TrafficHistoryModal.jsx`: "Destinations & Domains" tab with sortable table headers.

---

## 4. Verification Plan

### 4.1 Automated Tests
1. **Unit Tests (`tests/test_connections_and_geoip.py`)**:
   - Test Geo-IP resolution for public and private IP ranges.
   - Test RouterOS `ConnectionsMixin` connection retrieval and parameter serialization.
   - Test `remove_firewall_connection` immune guard protection (raises `WriteGuardViolation`).
   - Test `UserDestinationStat` database persistence, rollup aggregation, and multi-field sorting.
2. **API Endpoint Tests**:
   - Test `GET /api/v1/connections` with device and protocol filters.
   - Test `POST /api/v1/connections/{id}/kill` success and rejection on immune targets.
   - Test `GET /api/v1/analytics/users/{id}/destinations` sorting by `total_bytes`, `hit_count`, and `last_seen`.
3. **Frontend Component Tests (`frontend/src/components/LiveConnectionsModal.test.jsx`)**:
   - Modal rendering, search filtering, protocol tabs, and kill confirmation action.
4. **Regression Gate**:
   - Run `.venv/bin/pytest` (all existing 476+ tests must remain green).
   - Run `.venv/bin/ruff check`.
   - Run `npm test`.

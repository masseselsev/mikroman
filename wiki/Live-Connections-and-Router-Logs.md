# 📜 Live Connections & Router Log Stream

Real-time connection monitoring and centralized RouterOS event logging.

---

## 🌐 Live Connection Tracker

Queries `/ip/firewall/connection` to inspect active network sockets:
- **Device Attribution**: Maps source and destination IPs to known devices and assigned user profiles.
- **Traffic Metrics**: Displays live protocol, TCP states, endpoints, and accumulated byte counters.
- **Safety Connection Termination**: `POST /api/v1/connections/{id}/kill` is guarded by `WriteGuardViolation` — terminating connections to management interfaces or immune infrastructure is refused.

---

## 🗺️ Offline Geo-IP Resolution

Resolves remote IP addresses to geographic origins in memory:
- **Privacy Guarantee**: Zero external API calls during inspection; internal client browsing destinations are never leaked to third parties.
- **Built-in Prefix Database**: Embedded lookup for well-known CDN edges, public DNS resolvers, and major carriers.
- **MaxMind MMDB Support**: Place a `GeoLite2-Country.mmdb` file in `data/` (or configure `MIKROMAN_GEOIP_DB`) for comprehensive global resolution.
- **Honest Unknowns**: Unresolved IPs resolve strictly to `?? / Unknown` rather than guessing country codes.

---

## 📜 Terminal Log Viewer & Event Classification

Streams RouterOS system logs into an interactive web terminal:
- **Dual Data Sources**:
  - `source=live`: Reads current buffer directly from `/log` ring buffer on the router.
  - `source=db`: Queries persistent SQLite history stored by the background scraper.
- **Event Classifier (`log_classifier.py`)**:
  - Categorizes events into `auth`, `interface`, `dhcp`, `wireless`, `firewall`, and `system`.
  - Assigns severity levels: `info`, `warning`, `error`, `critical`.
- **Topic Management**:
  - Easily manage RouterOS `/system/logging` rules from the UI.
  - Managed rules are tagged `mikroman:log:<topics>`. Foreign rules created manually in WinBox are protected from deletion.

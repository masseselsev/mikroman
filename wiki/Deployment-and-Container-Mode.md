# 📦 Deployment, Storage & Container Mode

MikroMan can be deployed as a standard Docker container or hosted directly inside RouterOS 7.4+ hardware containers.

---

## 🐳 Docker Compose Deployment (Recommended)

### 1. `docker-compose.yml`
```yaml
services:
  mikroman:
    image: ghcr.io/masseselsev/mikroman:latest
    container_name: mikroman
    restart: unless-stopped
    ports:
      - "1928:1928"
    volumes:
      - mikroman_data:/data
    environment:
      - PORT=1928
      - DATABASE_URL=sqlite+aiosqlite:////data/app.db

volumes:
  mikroman_data:
```

### 2. Execution
```bash
docker compose up -d
```
Access the dashboard at `http://<host-ip>:1928`.

---

## 💾 Storage & Hot Backups

All state is stored in a single SQLite database file (`/data/app.db`) on the persistent volume.

### Online Backup Script (`scripts/backup.sh`)
Uses SQLite's online backup API via Python:
- Creates transactionally consistent snapshots with **zero downtime** while the poller continues writing.
- Runs an integrity check (`PRAGMA integrity_check`) before committing the backup file.
- Rotates backups, retaining the last `MIKROMAN_BACKUP_KEEP` snapshots (default 14).

```bash
# Manual execution or cron
scripts/backup.sh
```

### Restore Script (`scripts/restore.sh`)
```bash
scripts/restore.sh backups/app-20260905-030000.db
```
Safely stops the container, saves a pre-restore backup, cleans `-wal` and `-shm` sidecars, and restarts the service.

---

## 📦 Running in a MikroTik RouterOS Container (v7.13+)

RouterOS has supported Docker-style containers since v7.13, as an optional
package (`/system/package print where name=container`); enabling it needs a cold
reboot. Check the package on the board rather than assuming a version.

> ### ⚠️ Critical: Storage Recommendation
> **Always run containers and store databases on external storage (USB SSD or high-endurance USB flash)**, never on internal NAND storage.
> RouterOS internal flash has limited write endurance. Continuous database logging, metrics sampling, and rollups will cause premature wear on internal NAND flash.
>
> This is not hypothetical for MikroMan: the image unpacks to roughly 340 MB and
> a hAP be³ Media reports ~470 MB of internal flash free, so the default
> `layer-dir`/`tmpdir` can fail the pull mid-transfer even before wear enters it.

### Setup Instructions:

**Preferred - from the running app.** Containers page → *Prepare this router for
a container*. It shows a plan first (`POST /api/v1/routers/{id}/containers/setup/plan`,
which writes nothing), then applies exactly that plan (`/setup/apply`), creating
the storage settings, bridge, veth, gateway address, masquerade, LAN-bound web
forward, mount and the container itself. It is idempotent, refuses to modify
objects it did not create, refuses a subnet that overlaps an existing address, and
refuses a storage that `/disk` says is unmounted, read-only or too small for the
image — with the reason, before a pull fails halfway through.
`GET /api/v1/routers/{id}/containers/storage` is the inventory that picker is
built from, and `POST .../storage/format` prepares a device that cannot be used
as it is; formatting erases the device, so the slot must be typed back and it is
refused for anything holding `layer-dir`, `tmpdir` or a mount source.

Moving an *existing* installation's data across is deliberately not a feature. It
was a one-time cutover (old instance → `/data` on the stick), and left in the
product it is a button that replaces a live deployment's database. The manual
order — snapshot with `sqlite3 ".backup"`, copy `app.db` **and** `.secret_key`
together by SFTP, then start, then stop the old writer — is documented in
[`scripts/setup_ros_container.rsc`](../scripts/setup_ros_container.rsc) section 6.

**Fallback - on a bare router**, where there is no working MikroMan to ask:
1. **Enable Container Mode on RouterOS**:
   ```routeros
   /system/device-mode/update container=yes
   ```
2. **Attach External Storage**:
   Verify the disk appears under `/disk print`.
3. **Configure Container Subsystem**:
   ```routeros
   /container/config/set registry-url=https://registry-1.docker.io tmpdir=usb1/pull ram-high=256M
   ```
   MikroMan's own plan also sets `layer-dir`, which is the value that keeps the
   image off internal flash. It does *not* set `dns-servers`: on 7.24.2 that is
   not an attribute of `/container/config` (the device refuses the request), and
   because the set is atomic, including it would cost every other attribute in
   the same command. Containers inherit the router's resolver through the bridge.
4. **Deploy Container**:
   Import and run [`scripts/setup_ros_container.rsc`](../scripts/setup_ros_container.rsc) pointing mounts and root directories to `usb1/`.

---

## 🧾 Application Logs Inside a Container

A RouterOS container has no `docker logs`, so the app's own output needs somewhere
that outlives the process. MikroMan writes it to `mikroman.log` in the same
directory as the database - with the standard mount that is
`/usb1-part1/mikroman_data/mikroman.log` on the stick - and rotates it at
`LOG_FILE_MAX_BYTES` (4 MB) × `LOG_FILE_BACKUP_COUNT` (3), so the cap is ~16 MB of
flash. Read it back with `GET /api/v1/logs?source=app`, or in the UI:
**System Events → MikroMan Log**.

Two related decisions worth knowing before changing anything:

* **Per-request logging is disabled at the source** (`httpx`, `httpcore`,
  `uvicorn.access`, `aiogram` are held at WARNING). With the container created
  `logging=yes`, RouterOS copies the container's stdout into its own log ring,
  and measured on a hAP be³ Media that made MikroMan 995 of the ring's 1000
  lines - the ring turned over in about five minutes, so real device events were
  evicted before the scraper could store them.
* **`logging=yes` is still the right setting** once that chatter is quiet. It is
  the only channel that can carry a crash traceback that happens before the log
  file exists, and a few lines a day no longer displaces anything.

## 📊 Is the Container Using Too Much?

`GET /api/v1/system/diagnostics` answers this without a shell: the process's
resident set and peak, the number of RouterOS REST calls made per device, and the
count / average / worst-case duration of each background pass. It needs neither a
router nor a working database.

Compare two different numbers deliberately:

* `/container` → `memory-current` is the **cgroup** figure. It includes the page
  cache the app pushes through its data mount, which on a database of that size on USB
  is the difference between "500 MB used" and the ~230 MB the process actually
  holds. That cached memory is reclaimable and does not compete with the router.
* `diagnostics.memory_bytes` is the app's own **resident set**. Roughly 150 MB of
  it is `aiogram` - importing the Telegram library, whether or not a bot token is
  configured. If alerts are not wanted, that is the one large saving available.

Background load is split across two clocks, `POLL_INTERVAL_SECONDS` (telemetry:
4 calls per router) and `HEAVY_SYNC_INTERVAL_SECONDS` (discovery, queue and mangle
reconciliation, rollups, quota: a dozen or more). On the measured device the heavy
half was 303 of 741 REST calls in a 320-second window; both are settable through
the environment without rebuilding anything.

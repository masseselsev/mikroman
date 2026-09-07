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
`/migrate-data` carries the existing database and its `.secret_key` across (it
refuses while the target container is running, so a live database is never
replaced underneath its application).

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
   image off internal flash, and `dns-servers` for the container's resolver.
4. **Deploy Container**:
   Import and run [`scripts/setup_ros_container.rsc`](../scripts/setup_ros_container.rsc) pointing mounts and root directories to `usb1/`.

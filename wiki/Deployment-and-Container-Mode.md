# 📦 Deployment, Storage & Container Mode

MikroMan can be deployed as a standard Docker container or hosted directly inside RouterOS 7.4+ hardware containers.

---

## 🐳 Docker Compose Deployment (Recommended)

### 1. `docker-compose.yml`
```yaml
services:
  mikroman:
    image: ghcr.io/mikroman/mikroman:latest
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

## 📦 Running in MikroTik RouterOS 7.4+ Container

RouterOS 7.4+ supports running Docker containers directly on the router hardware.

> ### ⚠️ Critical: Storage Recommendation
> **Always run containers and store databases on external storage (USB SSD or high-endurance USB flash)**, never on internal NAND storage.
> RouterOS internal flash has limited write endurance. Continuous database logging, metrics sampling, and rollups will cause premature wear on internal NAND flash.

### Setup Instructions:
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
4. **Deploy Container**:
   Import and run [`scripts/setup_ros_container.rsc`](../scripts/setup_ros_container.rsc) pointing mounts and root directories to `usb1/`.

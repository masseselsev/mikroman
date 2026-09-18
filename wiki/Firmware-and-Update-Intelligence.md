# ⚡ Firmware & Update Intelligence

MikroMan provides complete lifecycle management for RouterOS packages and RouterBOOT bootloaders.

---

## 📡 Multi-Channel Tracking

Monitors available updates across official MikroTik release channels:
- `stable`: General production releases.
- `long-term`: Extended maintenance releases.
- `testing`: Release candidate builds.
- `development`: Feature preview builds.

---

## 📜 Upstream Changelog Proxy

When updates are available, MikroMan fetches upstream release notes directly from MikroTik:
- **Source**: `https://upgrade.mikrotik.com/routeros/{version}/CHANGELOG`
- **SSRF & Path Traversal Prevention**: Version parameter validated against strict regex `^\d+\.\d+(\.\d+)?$`.
- **Bounded Stream Reading**: Body reads are strictly capped at 256 KB.
- **In-Memory FIFO-32 Cache**: Caches recent changelogs to eliminate repetitive upstream queries.
- **Negative Caching**: Upstream errors (404/timeouts) are cached with a 60-second TTL to avoid connection storms.

---

## 🛡️ Pre-Upgrade Safety Invariants

Executing an automated firmware upgrade involves irreversible gateway reboots. MikroMan enforces strict safety gates:
1. **Mandatory Pinned Backup**: Every upgrade request executes an automated configuration backup with `is_pinned=True` before dispatching the install command.
2. **Router Name Confirmation Gate**: The administrator must type the exact router name into the confirmation dialog (`confirm_name.strip() == router.name.strip()`).
3. **Update Validation**: Rejects upgrade requests with HTTP 400 if the router is already on the latest available version.
4. **RouterBOOT Staging**: Automatically checks `/system/routerboard` and stages pending bootloader updates if outdated (`/system/routerboard/upgrade`).

---

## 🔄 Reconnection State Machine

After dispatching the upgrade and reboot commands, the frontend modal enters an autonomous 4-stage reconnection state machine:
```
  [Initiating Upgrade]
           |
           v
    [Rebooting...]  <-- Fixed initial wait window
           |
           v
  [Waiting Online]  <-- Exponential backoff health probing
           |
     +-----+-----+
     |           |
     v           v
  [Ready]   [Timeout Alert]
```
Upon successful reconnection, the new version is verified against the gateway and the UI refreshes live.

---

## 🏷️ Application Release Notifications

Separate from RouterOS firmware, MikroMan tracks its own releases so a running
deployment can tell its administrator that a newer build exists.

- **Endpoint**: `GET /api/v1/system/version-check` (public — it exposes nothing
  beyond what GitHub publishes; `?force=true` bypasses the cache).
- **Upstream**: `https://api.github.com/repos/masseselsev/mikroman/releases/latest`,
  revalidated with a conditional request (`If-None-Match`). A `304 Not Modified`
  reply confirms the cached answer for free: GitHub does not count conditional
  requests against its unauthenticated rate limit (60 requests/hour per source
  address).
- **Cache**: in-process, 15 minutes. Short on purpose — the refresh after the TTL
  is a conditional request, so freshness costs nothing, while a long TTL hides a
  freshly published release from a dashboard that is already open.
- **Comparison**: the running build's version (`APP_VERSION`, baked from
  `package.json` at build time) against the semver of the release tag. A
  pre-release or build suffix (`-rc1`, `+build123`) is truncated before the
  comparison, so only the numeric triple decides.
- **Presentation**: the footer shows the build version and, when a newer release
  exists, adds a glowing badge linking to the release notes. The check repeats on
  page load, on tab focus and every 15 minutes, and the badge disappears again
  once the running build catches up.
- **Failure signalling**: `check_failed: true` means GitHub could not be reached
  or answered with a non-200 status (offline network, upstream rate limit). The
  last known release is still reported so an offer does not vanish because of one
  failed request, but `has_update: false` together with `check_failed: true`
  means *unknown* — never *up to date*. A failed attempt is recorded and retried
  at most once per TTL instead of on every page load.

### Release correspondence a published release must satisfy

The notification path is built around GitHub Releases; a release that breaks one
of these assumptions is invisible to running deployments:

1. **Published, not draft, not prerelease.** `/releases/latest` skips both, so a
   prerelease build can never be advertised — its tag must not be the one
   deployments are expected to upgrade to.
2. **The tag is `vX.Y.Z` and equals the version baked into the build.** The
   comparison is numeric-triple based; a tag that disagrees with `package.json`
   and `APP_VERSION` makes the footer report a version that does not exist
   upstream, or suppresses a real update.
3. **`make_latest` stays on for the newest release.** GitHub elects "latest" by
   release *creation time*, not by semver, so back-filling a release for an older
   tag requires keeping it out of the "latest" slot
   (`gh release create --latest=false`) — otherwise the update check starts
   comparing against the back-filled version.
4. **No deployment file pins `APP_VERSION`.** The image's baked default is the
   source of truth; overriding it in a compose file or environment drifts from
   the released tag and breaks the comparison for that deployment alone.

The step-by-step procedure lives in [`docs/RELEASING.md`](../docs/RELEASING.md).

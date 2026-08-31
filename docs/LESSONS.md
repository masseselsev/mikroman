# Lessons

Project context ledger. Every defect that cost real debugging time is recorded
here as a rule, so the next person does not repeat the archaeology.

Format: `[DATE] Problem: X → Solution: Y`

---

## Testing

**[2026-08-31] Problem:** The test suite made real REST calls to the developer's
live MikroTik. `tests/test_routers_api.py` used the author's own router address
(`192.168.88.1`) as fixture data, and the request paths outside its respx blocks
were never mocked, so `POST /api/v1/routers` dialled the real router as
`admin`/`pwd`. The router logged three `login failure for user admin ... via
rest-api` per run — indistinguishable from a brute-force attempt against the
default account, and enough to trip an anti-bruteforce rule and blacklist the
development machine. Nothing in the suite could fail as a result: the calls sit
inside `try/except` blocks whose purpose is to tolerate an unreachable router.
The only evidence was in the router's own log.
**→ Solution:** An autouse `no_real_network` fixture in `tests/conftest.py`
refuses every socket the suite tries to open, and fixture addresses use RFC 5737
TEST-NET-1 (`192.0.2.0/24`), which cannot route. Never use a real address you
own as test data — a typo or an unmocked path turns the suite into a client.

**[2026-08-31] Problem:** The first two attempts at that guard broke fifteen
healthy tests. Patching `httpx.AsyncHTTPTransport.handle_async_request` and then
`httpcore.AsyncConnectionPool.handle_async_request` blocked mocked requests too,
because respx patches those same layers for itself.
**→ Solution:** Guard at the socket backend
(`httpcore._backends.{anyio,sync}.*.connect_tcp`), below everything respx
touches. A mocked request never opens a socket, so a socket-level guard blocks
exactly the real calls and nothing else.

**[2026-08-31] Problem:** Verifying "no more stray API calls" by reading code was
wrong twice. The earlier `OfflineRouterOSClient` fix addressed a real defect but
was credited with stopping traffic it never caused.
**→ Solution:** Count the router's own log entries before and after the action.
`/log` over REST is the ground truth; a claim about traffic is not established
until the counter is compared.

## RouterOS behaviour

**[2026-08-31] Problem:** `user rest logged in/out` appears in the router log
every ten minutes, which looked like the connection pool failing to hold a
session.
**→ Solution:** It is not ours to fix. Measured pairs at 18:37, 18:47, 18:57,
19:07, 19:17, 19:27 — exactly ten minutes apart, with the re-login landing
within 0–3 seconds. That is RouterOS ageing out its own REST session; the
session is keyed by source address and user, not by TCP connection, so it
neither reflects nor responds to client-side pooling.

## Connection handling

**[2026-08-31] Problem:** `RouterManager.get_client()` consulted its client cache
only when an explicit `router_id` was passed (`if router_id and router_id in
self._clients`). Every call site asks for the default router and passes no id, so
the cache was written on every call and read on none. Each request built a fresh
client, opened a fresh connection, and abandoned the previous pool unclosed —
keep-alive, worth the difference between 5% and 12–27% router CPU under load, was
never actually in effect.
**→ Solution:** Look the cache up by the resolved router's id, and key it on a
fingerprint of the connection parameters so a router edited in Settings retires
its old client instead of being served a stale one. Verified by asserting object
identity across calls, which is now a test.

## Recoverability

**[2026-08-31] Problem:** A saved router's connection details could not be
edited. `PUT /routers/{id}` and `api.updateRouter` both existed; no component
ever called them, so the UI offered only activate and delete. The gap surfaced
at the worst moment — a factory reset drops the certificate, the REST user and
the password together, leaving a record that cannot connect and cannot be
corrected. Delete-and-re-add looked equivalent but was not: devices are
`ON DELETE SET NULL` and survive, while gateway traffic rollups, system metrics
and interface metrics are `ON DELETE CASCADE` and would have been destroyed.
**→ Solution:** A shared `RouterConnectionForm` used for both adding and
editing. More generally: every stored configuration value needs an edit path,
and "delete and recreate" is only a workaround when nothing cascades off the row
being deleted — check the foreign keys before believing it is one.

**[2026-08-31] Problem:** An edit form cannot pre-fill a password the API
deliberately never returns, so a blank field has to mean "keep current" — which
would make *Test Connection* send an empty password and register a failed login
on the router for that user.
**→ Solution:** Disable the test until a password is typed, and say why. A
credential that cannot be read back changes what a "test" button is allowed to
do.

## Accounting durability

**[2026-08-31] Problem (asked, then verified already-handled):** does traffic
history survive a network outage? Yes - accounting is delta-against-a-*persisted*
baseline, the baseline is not advanced on a failed read, the router keeps
counting throughout, and the first successful poll after reconnect captures the
whole gap. A multi-day gap only loses per-day granularity for that window, not
the total.

**[2026-08-31] Problem (the real gap, now fixed):** a router *reboot* resets
every byte counter. `compute_delta` inferred a reset only from `current <
previous` - but right after a reboot a busy interface's counter can climb *past*
its stale pre-reboot baseline within one 10s poll, and that then read as a tiny
ordinary delta, silently dropping every byte since the reboot.
**→ Solution:** `/system/resource` uptime is read once per tick and threaded
into both accounting passes. Uptime running backwards (beyond a 90s slack) is an
unambiguous reboot signal; on it, `compute_delta(..., reset=True)` credits the
full current counter. `parse_uptime_seconds` handles RouterOS's `"1d3h58m3s"`
form. Native `/ip/accounting` was considered and rejected - it is equally
in-memory and resets on reboot too, so it offers no durability advantage over
the mangle-counter approach.

## Hardware identity

**[2026-08-31] Problem:** the CPU tile showed the *board* name ("hAP be³
Media"), not the processor. `/system/resource` `cpu` is just the instruction set
on MikroTik hardware ("ARM64").
**→ Solution:** the SoC/platform name is `firmware-type` at `/system/routerboard`
("ipq5300") - the closest RouterOS gets to a CPU part number on RouterBOARD.
`get_routerboard()` fetches it, cached per client (static between reboots, and a
reboot rebuilds the client). Resolution order for the label: `firmware_type`
(RouterBOARD) → `resource.cpu` (real on x86/CHR) → `architecture_name`.

## Layout

**[2026-08-31] Problem:** The device row kept overflowing - names truncated to
"Pixe...", the byte-total figures printed straight over the "seen 10h ago" text
beside them. The layout was a flex row with a *fixed 104px* metrics column whose
content ("731.2 MB · 79.8 MB", "94.0 Kbps") was wider than 104px, and the
column had no `overflow` rule.
**→ Solution:** Redesign, not another 5px patch. A vertical stack of up to three
lines where **exactly one element per line is greedy** and everything else is
`flex-shrink: 0` and genuinely small. The name truncates (full text in
`title`); the address/vendor/staleness collapse into one truncating run; the
live rate shows compactly (`12.4M`, not `12.4 Mbps`) and only when non-zero -
an idle row was printing "0 bps" twice; per-device byte totals moved to the row
tooltip and the modal, since the per-user panel already carries that number.
Principle: when a row will not fit, cut what it shows before you shrink what is
left.

## Responsiveness

**[2026-08-31] Problem:** With the router off the network the dashboard took
about five seconds to show anything, on every load and every poll tick.
Measured: `/routers` 4.65s, `/users` 4.93s, `/system/status` 4.91s,
`/system/interfaces` 4.96s, against under a millisecond for endpoints that touch
only the database. Every one was the RouterOS connect timeout being paid again
for an answer already known. `/system/interfaces` additionally returned 500 —
precisely when the operator is in Settings trying to repair the connection, so
the app looked broken rather than disconnected.
**→ Solution:** A circuit breaker on `RouterOSClient`: a failure to *reach* the
host suppresses further attempts for 15s, and any answer at all — including 401
or 500 — closes it immediately. Measured after: 0.002s. Two rules fell out of
it. The breaker belongs at the transport, because roughly forty client methods
catch their own exceptions and a breaker wrapped around callers would never see
those failures. And an unreachable dependency is a normal state, not a 500.

## Durability

**[2026-08-31] Problem:** All state — including months of daily traffic rollups
that cannot be reconstructed — lived in one SQLite file on a Docker volume, in
`journal_mode=delete`, with no backup. A `docker compose down -v`, a
`volume rm`, or a disk failure would take everything, and a plain `cp` of a
`delete`-mode file while the poll loop writes can capture a torn page. The poll
loop and dashboard requests also intermittently hit `database is locked`.
**→ Solution:** WAL + `synchronous=NORMAL`, applied per connection via a
`connect` listener (SQLite PRAGMAs are per-connection, not per-database). WAL
removes the reader/writer lock contention and makes the online-backup API safe
to run hot. `scripts/backup.sh` takes a consistent, integrity-checked, rotated
snapshot with no downtime; `scripts/restore.sh` swaps one back in, keeping the
replaced file and clearing the stale `-wal`/`-shm` sidecars first. The
connectivity-loss case needed no fix: delta-against-persisted-baseline
accounting already survives a gap, losing data only when the router's counters
reset underneath it (reboot, factory reset) or the volume is destroyed.

**[2026-08-31] Problem (noted, not yet fixed):** `PRAGMA foreign_keys` is `0`
(SQLite's default) on every connection, so the `ON DELETE CASCADE` /
`ON DELETE SET NULL` clauses the schema declares are **not enforced**. Deleting
a router does not actually cascade to its rollups and metrics at the database
level; whatever cleanup happens is ORM-relationship cascade only. Turning
`foreign_keys=ON` is correct but is a behaviour change that could surface latent
orphan rows, so it belongs in its own change with an orphan check first — not
bundled into the WAL work.

## Device identity

**[2026-08-31] Problem:** Discovery copied the quarantine bandwidth
(`unassigned_device_speed_limit`) onto `Device.speed_limit`. Assignment to a user
only ever set `user_id`, so the copy survived and the device kept a 5M/5M child
queue under an unlimited parent — the owner's limit never applied, and every
device in the household was throttled to 5 Mbps with nothing in the UI to explain
it.
**→ Solution:** A stored limit means "an override the operator chose". State that
follows from a *relationship* — here, having no owner — is derived where it is
used, never frozen into the row. Migration 010 cleared the rows already carrying
it, and `reconcile_device_limits` runs on the queue tick as a standing guard.

**[2026-08-31] Problem:** `classify_connection` answered "wired" for a bridge
interface. Every wireless client's ARP entry is recorded against the bridge, so
phones were labelled wired; a phone that had rotated its private MAC then
appeared as one "wired" and one "wireless" record of the same hostname, which is
the exact shape `find_link_suggestions` scores highest. It proposed joining a
phone to itself as a dual-homed machine.
**→ Solution:** Return `None` for aggregating interfaces (bridge, bond, lag,
vlan) rather than guessing, and have callers keep what they already knew. An
inconclusive signal must be representable, or it gets rounded to a wrong answer.

**[2026-08-31] Problem:** `find_rotation_candidate` bails when more than one
prior record shares the hostname - the safe choice when picking blind. But
duplicates had already accumulated (from before adoption shipped, and from a
Wi-Fi change producing several rotations in minutes), so the "exactly one
candidate" rule *permanently* blocked every future adoption. The dashboard grew
to five "Pixel-9-Pro-XL" rows for one phone and the queue tree a branch each.
**→ Solution:** `consolidate_rotated_devices` on the background tick collapses
same-owner randomized-MAC rows sharing a hostname into the active one. Lesson:
a guard that is right for the blind case ("don't guess") can be wrong for the
post-hoc case, where the operator has already disambiguated by assigning the
rows to one person. Handle both.

**[2026-08-31] Problem:** Reassigning `DeviceHistory` / `DeviceTrafficRollup`
rows off a device before deleting it, using a Core `UPDATE`, then
`session.delete(device)`. The relationships are `cascade="all, delete-orphan"`;
the Core UPDATE moved the rows in the DB but the session's in-memory collections
still pointed at them, and the cascade on delete deleted the rows that had just
been moved. Also: `merge_devices` never moved the rollups at all, silently
shrinking household totals on every merge.
**→ Solution:** Reassign through the `.device` relationship (keeps both
collection sides in sync); set `.device = None` to orphan a row you want the
cascade to delete. And `session.refresh(obj, ["history", "traffic_rollups"])`
before touching the collections - a selectin relationship loaded once for an
object already in the session is not reloaded by a later `selectinload` option,
so rows written since would be invisible and then duplicated.

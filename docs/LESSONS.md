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

**[2026-09-04] Problem:** Testing-library regex queries in Vitest caused multiple element collision errors across UI modal tests. Specifically:
1. Regex `/-1/` intended to match diff counter `<span ...>-1</span>` also matched git hunk header strings like `@@ -1,5 +1,7 @@`.
2. Case-insensitive regex `/Changed/i` intended to match outcome badges `<span ...>Changed</span>` also matched filter tab buttons `<button ...>changed</button>`.
**→ Solution:** Always use exact string matching (`screen.getByText('-1')`) or explicitly constrain by selector (`screen.getByText('Changed', { selector: 'span' })`) whenever the DOM contains composite code diffs, logs, or casing-differentiated controls.

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

**[2026-09-01] Problem (re-audit of the no-reboot outage path):** the outage
total is preserved, but `sync_counter_rules()` ran *before* `collect()` every
tick and **deletes** the mangle rule of any device that has gone inactive.
`collect()` then never read that rule's final counter, so the bytes the device
moved between the last successful `collect` and going idle were lost. Usually a
rounding error (a device about to idle is already idle); across a router outage
that also spanned the device dropping off, it is minutes of real volume, and it
only ever *under*-counts.
**→ Solution:** two changes. (1) `main.py` now calls `collect()` **before**
`sync_counter_rules()`, so the going-inactive device's rule is still present when
its final interval is read. (2) The prune branch of `sync_counter_rules()` reads
each counter one last time, flushes the delta via the shared `_flush_deltas`
helper, and drops the baseline key - a backstop that holds regardless of call
order. Tests: `TestOutageWithoutReboot` covers a failing poll leaving the
baseline untouched, repeated failures then recovery, and the prune-flush.
Remaining known limitation (documented, not a bug): all gap bytes are credited
to the router-local date of the first poll after reconnect, so an outage that
straddles midnight mis-splits those two days. The total is exact; a monotonic
counter carries no per-day breakdown to do better.

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

**[2026-09-02] Problem:** The dashboard "still felt sluggish" after the breaker.
The REST endpoints themselves were fast (`/users` ~30ms, `/devices` ~7ms), but
`GET /routers` sat at a steady ~220ms because it probed *every* configured
router for live status **sequentially**, and the frontend `loadData()` awaited
it *first*, on the 6-second poll, before fetching users/devices. One RouterOS
round trip per router on the critical path of every refresh, growing per router.
**→ Solution:** (1) `list_routers` runs the per-router probes concurrently
(`asyncio.gather`, each with its own `AsyncSession` — a session is single-use).
(2) The frontend split `loadRouters()` out of `loadData()`: the 6s poll now
only moves user/device data with the already-known id, and the router list
refreshes on mount, on switch, and every 30s (for the selector's dots).
Lesson: anything that fans out to N remote calls does not belong on a fast
timer's hot path — put it on its own slower beat and parallelise what remains.

**[2026-09-02] Problem:** `api.macvendors.com` / `api.maclookup.app` were called
on **every discovery sweep** for the same MACs, flooding the event loop. When
neither service could name an OUI, `lookup_async` returned `"Unknown Vendor"`
and cached nothing, and `device_manager` re-attempts on every sweep for any
device whose vendor is a generic label. So each unlisted OUI cost two HTTP
calls per sweep forever.
**→ Solution:** negative caching in `VendorLookupService` — a definitive
"not found" (maclookup `found:false`, or macvendors `404`) stores a `None`
sentinel under the prefix; a *network error* is not cached (transient). The
sentinel is cleared on restart, so each unknown device gets one re-check per
container lifetime. Lesson: a lookup with a "not found" outcome needs a
negative cache as much as a positive one, or the retry path becomes a DoS on
the upstream.

## Multi-router isolation

**[2026-09-02] Problem:** After switching the dashboard to router 2, its
*Traffic Analytics → By Users* table and the telemetry **USERS** count still
listed router 1's profiles (as rows of zeros). The *Users & Devices* tab was
correct. Two independent scoping gaps: (1) `AnalyticsEngine.get_historical_traffic`
did `select(User)` with no router filter and then built a summary row for
*every* user, even those with no device on the viewed router; (2)
`ws.py` constructed `TrafficController(client)` and called
`get_realtime_traffic_stats(session)` with **no** `router_id`, so `eff_router_id`
was `None` and the `(User.router_id == id) | (User.router_id.is_(None))` clause
never ran.
**→ Solution:** scope `users_query` the same way `devices_query` already was,
and resolve `eff_router_id` in the WS loop *before* the stats call so it can be
passed to both the controller and the query. Lesson: a `WHERE` that is only
added `if router_id:` is silently a no-op whenever the caller forgot to thread
the id through — grep every construction site, not just the one that showed the
symptom.

**[2026-09-02] Problem:** The WAN IP tile showed the *container's* egress
(address + operator) for whichever router was selected, because
`public_network_resolver` was a process-wide singleton that resolved "what is
*my* IP" once. On a multi-router install router 2 sits at a different site with
a different operator, so its tile was simply wrong.
**→ Solution:** the resolver caches **per router id**, and the WS loop passes
`hint_ip` = that router's own `/ip/cloud` `public-address` (RouterOS keeps it
current over DDNS, and it is correct even behind CGNAT). The operator is looked
up for *that* address. The container-egress echo is the fallback only when the
router reports nothing usable (`0.0.0.0`, CGNAT, RFC1918 — see
`public_ip_or_none`). Lesson: "what is my public IP" answered from the app
server is the app server's answer; when the question is really "what is this
router's uplink", ask the router — it already knows.

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

**[2026-08-31] Problem:** `consolidate_rotated_devices` treated "same normalised
hostname + one owner" as sufficient to merge. That is wrong when the name really
denotes several devices - three people who each own a bare `iPhone`, or one
person with two of the same Pixel. The generic-hostname bar (one vendor, ≤1
online) only helped if both happened to be online during the exact sweep the
pass ran in.
**→ Solution:** Persist *co-presence*. Every discovery sweep that sees two
same-named randomized MACs active at once writes the pair to `device_coexistence`
(migration 011); one radio cannot answer on two addresses at the same instant,
so co-presence is proof of two physical devices, not a guess. Consolidation
refuses any group containing a recorded pair (once-a-day advisory alert,
`alert_type="mac_rotated_multi"`, deduped by a same-day lookup so the per-tick
loop does not spam it). Second guard: a victim row is absorbed only after it has
been silent for `mac_rotation_settle_hours` (default 48h) - a phone asleep for an
evening is not yet a rotation. Discovery-time `_adopt_rotation` also refuses a
candidate that carries any co-presence record, and `find_merge_suggestions`
filters such pairs out. Lesson: "identical + same owner" is not identity;
non-overlapping presence over time is the signal that separates a rotation from
two real devices, and it has to be recorded when observed because the cleanup
pass runs later, when only one of the two is on.

**[2026-09-01] Problem:** Editing a profile's devices needed to (a) delete a
device without losing its traffic for the owner, (b) move a device back to
unassigned and have its share leave the owner's totals, and (c) undo a wrong
merge. Three different truths:
* **Delete keeps the traffic.** The per-user `TrafficRollup` is a separate table
  from `DeviceTrafficRollup`; `session.delete(device)` cascades the device's own
  rollups but never touches the user's, so the owner's monthly total is
  unchanged by design. Nothing to do but *not* touch the user rollup.
* **Unassign detaches the traffic.** `collect()` writes the per-device and
  per-user daily rollups from the *same* deltas, so `user.rollup[date]` is the
  sum of that user's devices' `rollup[date]`. Subtract the leaving device
  date-for-date - but **clamp at zero**: the rollups carry no per-date owner, so
  a device that was unassigned for part of its life would otherwise push an old
  month negative. Over-keeping a little beats a negative figure.
* **A merge cannot be undone for the past.** `_absorb_device` / `merge_devices`
  coalesce rollups by date (`existing.bytes_in += victim.bytes_in`); the
  addends are gone. `POST /devices/{id}/split` therefore only re-creates a
  separate record for a historical MAC (and writes a `device_coexistence` pair
  so it is never re-merged) - future traffic on that address is tracked apart,
  the past stays with the original device. Say so in the UI rather than
  pretending to divide it.
Lesson: "remove a device" is two operations with opposite effects on the
totals; pick the semantics deliberately and make the UI name them.

**[2026-09-01] Problem:** The analytics banner read `Partial coverage —
attributed to devices: 51.6%`, which reads as "half the traffic was lost".
Nothing was lost. **Solution:** `_assess_accounting_health` divided the *whole
range's* attributed bytes by the *whole range's* gateway bytes. Reconstructed
from the live database, day by day:

| day | gateway | attributed | note |
|---|---|---|---|
| 08-29 | 24.9 GB | 2.2 GB | no per-device counters existed yet |
| 08-30 | 41.8 GB | 20.7 GB | the day accounting was switched on — partial |
| 08-31 | 23.8 GB | 20.7 GB | LAN renumbered 88.x → 123.x, router rebooted |
| 09-01 | 8.5 GB | 8.3 GB | ordinary day |

44 of the 47 "missing" GB are the first two rows: volume that was never
attributable, not volume that went astray. Coverage is now judged over the
**measured window** only — days after `accounting_started`, excluding the
switch-on day, which is inherently partial — and the pre-accounting volume is
reported as its own figure. Same data: 52% → 90% for the range, 98% for a clean
day. The residual ~2% is Ethernet framing overhead on the WAN interface plus the
router's own DNS/NTP/REST traffic, which forward-chain per-device counters can
never see. Lesson: a ratio whose numerator and denominator cover different time
spans is not a measurement, it is an accusation. Split the window before
dividing, and print both volumes so the reader can check the arithmetic.

**[2026-09-01] Problem:** Merging a device away silently dropped its last
counter readings. **Solution:** `merge_devices` / `_absorb_device` delete the
source row, but its `mikroman:acct:dev_<id>:*` mangle rules stay on the router
until the next `sync_counter_rules` prunes them — so the next `collect()` reads
real bytes for a device id that no longer resolves, and `_flush_deltas` did
`session.get(Device, id) -> None -> continue`. An `acct_device_successors`
AppSetting now maps dead id → surviving id (repointing existing entries on write
so A→B→C resolves to C in one hop), `_flush_deltas` follows it, and the prune
branch clears entries once the rules that fed them are gone. A genuinely deleted
device has no successor and its bytes are correctly discarded. The magnitude is
one poll interval, but the same map is what makes a *manual* merge safe to offer
at any moment. Lesson: deleting a row does not stop the thing on the other side
of the network from counting; whenever a record disappears, decide explicitly
where its in-flight data goes.

**[2026-09-01] Problem:** The CPU tile showed `ipq5300` for a board whose
manufacturer publishes IPQ-5322. **Solution:** RouterOS has no CPU part number
on RouterBOARD hardware at all — `/system/routerboard` `firmware-type` is the
*bootloader platform family* (several SoCs share one) and `/system/resource`
`cpu` is the instruction set ("ARM64"). The family was being rendered in the
part-number slot, so it looked precise and was wrong. `services/hardware.py`
now resolves the exact part from the product code (`model`, unique per product)
against the published specification, and anything unlisted degrades to the
family *labelled as a family* in the tooltip. Lesson: when a field can only be
approximate, the failure mode to avoid is not imprecision — it is imprecision
that presents itself as precision.

**[2026-09-01] Problem:** The rewritten coverage banner read "30.2 GB
attributed" while the user table directly beneath it totalled 52.6 GB, which
looks like double counting. **Solution:** Neither figure was wrong; they covered
different windows. Coverage is measured over the days after per-device
accounting was switched on, while the breakdown tables cover the whole selected
range - and the switch-on day itself carries real attribution (the hours after
the mangle rules went up), plus older installs carry per-user volume from the
queue-based accounting that preceded them. So the banner now reports
`pre_accounting_accounted_bytes` too, defined as
`accounted_bytes - measured_accounted_bytes` rather than as an independent sum,
which makes the two halves add back up to the tables' total *exactly* (verified
on the live database: 30.24 + 22.38 = 52.62). Lesson: when a figure describes a
sub-window of what is displayed around it, showing it alone is worse than not
showing it - name the window and show the remainder, so the reader can do the
arithmetic instead of assuming a bug.

**[2026-09-01] Problem:** Splitting large React components moved JSX into new
files whose icon imports were guessed rather than derived. `vite build` passed
with a dozen undefined components. **Solution:** an undefined component is a
runtime `ReferenceError`, not a build error - the bundle is valid and the page
renders blank, which is the worst way to find out. `vitest` did not catch it
either, because nothing rendered those components. Two guards now exist:
`SplitComponents.smoke.test.jsx` renders every extracted component, and
`frontend/scripts/check-identifiers.cjs` diffs JSX component usage against what
each file imports or defines. Lesson: "it builds" proves nothing about a
component move; only rendering does. Write the render test before the move, not
after.

**[2026-09-01] Problem:** RouterOS containers would have shown up as ordinary
network clients. **Solution:** a container's `veth` end answers ARP with a MAC
and an IP and is indistinguishable from a laptop by every signal discovery
uses. Left alone it would create a device record, queue in the unassigned inbox
asking to be assigned to a family member, and get a quarantine queue. The
interface *type* is the one thing that separates them, so discovery reads
`/interface`, collects the `veth` names, and flags anything seen on one as
`is_container`. Suppressing the record instead would have been wrong - it would
lose the container's traffic, which is real and forwarded like any other. It
belongs to the router, not to a person, so it is listed separately and excluded
from the household breakdown. Lesson: when a new kind of thing starts appearing
in an existing pipeline, decide where it belongs *before* it arrives; the
default of "it looks like a client, so it is one" is a decision too.

**[2026-09-01] Problem:** How to run an internet speed test on a router whose
API has no way to execute a command inside a container. **Solution:** RouterOS
has no internet speed test at all - `/tool/speed-test` and
`/tool/bandwidth-test` measure against *another RouterOS device*. Ookla's CLI
answers the real question, and RouterOS 7.4+ runs OCI containers, but there is
no `docker exec` over REST and `/container/shell` is console-only. What made it
work was noticing that the purpose-built image *runs once and exits*: start the
container over REST, and read its stdout out of `/log`. Two caveats found while
building it: RouterOS ships no `container` logging action, so without adding one
the output is produced and discarded; and the log is a ring buffer that still
holds the *previous* run's numbers, so the ids present before the start must be
recorded and excluded or a stale measurement is returned instantly as a fresh
one. Lesson: when an API seems to be missing the verb you need, check whether
the thing you are driving can be shaped to fit the verbs that exist.

**[2026-09-01] Problem:** Per-device accounting can never see the router's own
traffic. **Solution:** the accounting rules match `chain=forward`, which by
definition only carries traffic passing *through* the router. DNS, NTP, package
and cloud checks, DDNS, container image pulls and MikroMan's own REST polling
all travel `input`/`output`, so none of it could ever appear in the device sum -
it could only ever look like accounting having lost it. A
`mikroman:acct:self:{direction}:{interface}` passthrough pair per monitored WAN
interface names it. Two details mattered: the self-traffic rollup has to be
split *per day* like every other level, because coverage is judged over a
sub-window and a range total cannot be split back into days; and `_add_rollup`
needed an `IS NULL` lookup, since `router_id` is nullable on single-router
installs and `= NULL` matches nothing - which would have created a new row on
every tick instead of finding the day's. Lesson: an unexplained gap in a
measurement is a question, not a constant. Ask what the instrument is
structurally incapable of seeing.

**[2026-09-01] Problem:** The ISP billing-cycle anchor was a day of the month
only; some ISPs reset at a specific time. **Solution:** every traffic figure
MikroMan stores is a daily total, so a mid-day reset splits the boundary day
between two cycles and the rollups cannot express that. But `interface_metrics`
samples the WAN interface's *cumulative* byte counter about every 1.5 s
(30-day retention), and the quota is billed on the gateway total - so the
current cycle's start day (always < 30 days old) can be sliced at the reset
minute from those samples: walk every sample in `[00:00, reset]`, sum
`max(0, curr - prev)` per interface (which drops a reboot's backwards step),
subtract from `used`. Previous-cycle boundaries are usually pruned, so that path
degrades to whole-day. Reworking the cycle math onto datetime bounds also
surfaced a latent bug: `get_billing_cycle_dates` returned an end date in the
*next* month for `anchor_day == 1`, giving the default config a two-month
window. Lesson: "we only store daily totals" is not the end of the question -
check whether a finer-grained series exists for the one figure that needs it.

**[2026-09-01] Problem:** The same rework surfaced a second latent bug in the
old `get_billing_cycle_dates`. For a clamped anchor - `anchor_day = 31` in a
short month - `get_billing_cycle_dates(31, <a Feb date>)` returned
`Jan 31 … Feb 28`: 29 *inclusive* days. Feb 28 was both the current cycle's
last day and the next cycle's first, so its whole-day rollup was double-counted
and the cycle "rolled over" a day late. **Solution:** the datetime-bounds shim
makes the interval half-open - `Jan 31 00:00 … Feb 28 00:00`, inclusive last
date `Feb 27` - so Feb 28 belongs cleanly to the new cycle and the count is a
correct 28 days. Lesson: an off-by-one in an *inclusive* date range only bites
at the clamp; test the February-31 case explicitly, not just mid-month anchors.

**[2026-09-01] Problem:** The daily traffic totals for the days around a
development deploy looked scrambled - one day's gateway rollup read ~18 GB
high, the neighbouring day ~13 GB low - and it read like "we lost 50 GB in
three days". Nothing was lost. Both accounting paths (`traffic_accounting.collect`
for per-device, `AnalyticsEngine.record_traffic_snapshot` for the gateway) did
`rollup[today] += (current_counter - baseline)` with **no split at the local
midnight**. When the collector was down across midnight - a 16-hour outage
spanning the 29→30 boundary, plus a LAN renumber and RouterOS upgrade on the
31st - the entire gap's bytes landed on whichever day the next poll happened
on. On the one day the collector ran end to end, every level reconciled to
~2%. **Solution:** two changes. (1) The gateway and per-interface rollups are
now **recomputed from `interface_metrics`** rather than accumulated: walk the
samples, bucket each `max(0, curr-prev)` by the router-local date of the later
sample, and time-split any pair that straddles midnight (`rollups.split_bytes_by_day`).
Each pass replaces the rows for the days it covers, so a misfiled day self-heals
on the next run; a full 30-day rebuild runs once on startup. (2) The per-device
path keeps its live counter but now stores the last collection's wall-clock
time and, when a tick lands on a later date, apportions the delta across the
spanned days with the same helper. Lesson: an accumulator keyed on "now" is
only correct if it runs often enough to never straddle a boundary - and a
process that can be restarted mid-day does not clear that bar. If a
finer-grained series exists (here, the ~3 s interface samples), derive the
rollup from it instead of trusting the accumulator.

**[2026-09-02] Problem:** Connecting a new router whose web services were
moved off the defaults - `www` on 88, `www-ssl` on 444 - and running HTTPS
auto-configuration, the app "restored" `www-ssl` to 443. The provisioning
code (`CertificatesMixin.provision_ssl` / `bind_ssl_certificate` /
`import_custom_certificate`) took a `port: int = 443` argument and wrote it
into every `/ip/service` PATCH/`set` payload, so enabling HTTPS silently
rewrote the administrator's port. The frontend then hardcoded `443` into the
wizard form and the bind/upload calls, and `provision_ssl_for_router` stored
`443` in the router record. **Solution:** provisioning now sends only
`{disabled: False, certificate: <name>}` to `/ip/service` - never `port`,
`address`, or anything else the operator owns - then **reads the port back**
from `/ip/service` (`_read_www_ssl_port`, falling back to the row in hand,
then 443) and returns it. `provision_ssl_for_router` repoints the stored
connection at that discovered port; the wizard fills the port field from
`ssl_status.www_ssl_port` and offers a one-click "Use HTTPS on port N"
switch when a plain HTTP test finds `www-ssl` already enabled. The `port`
field was removed from `RouterProvisionSslRequest` / `RouterBindCertRequest`
/ `RouterUploadCertRequest` so it cannot be reintroduced by accident.
Lesson: an auto-configuration flow may enable a service and bind a
certificate, but the listening port (and address list, TLS version, ...) is
the administrator's setting - discover it, never assert it. RouterOS
`/ip/service` names are a fixed built-in set (`api`, `api-ssl`, `ftp`,
`ssh`, `telnet`, `winbox`, `www`, `www-ssl`); they cannot be renamed or
added to, so matching by `name == "www-ssl"` is safe, but every other field
on the row is fair game for the operator to have changed.

**[2026-09-02] Problem:** Adding a new router and opening the dashboard, the
WAN selector and the bandwidth tiles showed `ether1` + `bridge` already
"selected" with WAN badges, and interface accounting started against them -
the operator never picked anything. Three independent fallbacks were
inventing a WAN when none was configured: the telemetry loop
(`ws.py`) ran a `default_pick` heuristic (`name in {ether1,wan,bridge,sfp}`,
else the first two running interfaces) and put the guess into the telemetry
frame as `monitored_interfaces`, so the frontend rendered it as a real
selection and never showed the "no WAN" warning; `resolve_monitored_interfaces`
(gateway / interface rollups, billing-cycle slices) and
`TrafficAccountingService._monitored_interfaces` (router self-traffic rules)
each `return ["ether1"]` when the setting was absent. **Solution:** all three
now yield an empty set when nothing is saved. `ws.py` reports
`monitored_interfaces: []` and `0` bps -> the tiles render the loud amber
"⚠ No WAN selected" state; `_get_wan_ip` returns `None` (tile reads `—`)
rather than the first non-loopback address. The rollup/slice/self-traffic
paths simply record nothing for a router until its WAN is chosen; per-device
counters are unaffected because they key off the device IP. Tests that
exercised accounting now seed a `monitored_interfaces_*` setting explicitly
(added to the shared `_seed` helper), which is the real new contract.
`device_manager._get_wan_interfaces` deliberately keeps its `{"ether1"}`
fallback - its job is to keep the upstream ISP gateway *out* of the client
list, and an empty set there would let the gateway be ingested as a device,
which is more intrusive than a name guess, not less. Lesson: a "helpful"
default that fabricates configuration is worse than an empty state the UI
can flag - especially when the same guess is duplicated across three code
paths that then disagree.

**[2026-09-02] Problem:** Adding a new router, its unassigned-device inbox
immediately offered "Identical hostname 'NamasT3k' on user 'Mark'" - where
'Mark' is a user on a *different* router. `DeviceConsolidationMixin.find_merge_suggestions`
selected assigned and unassigned devices with `select(Device).where(user_id ...)`
and **no router filter** (its own docstring claimed it filtered by
`self.router_id`; it did not); `consolidate_rotated_devices` (the automatic
background merge) and `device_linking.find_link_suggestions` had the same
unscoped `select(Device)`. A DHCP hostname is not unique across sites, so the
hostname match reached straight across routers and would have linked/merged a
new router's device onto another router's user - silently, in the background
worker's case. **Solution:** every device-identity query is now confined to
`(Device.router_id == <id>) | (Device.router_id.is_(None))` - the same
scope the live discovery sweep already used. `find_link_suggestions` took a
`router_id` param; the `/devices/suggestions` and `/devices/link-suggestions`
endpoints resolve `eff_router_id` (query param -> active router) and thread it
through, and the frontend passes the viewed router's id explicitly. Lesson:
a docstring that says "scoped to the router" is not a filter. Any query that
feeds a cross-record heuristic (hostname, vendor, MAC pattern) must carry the
router predicate, and the auto-acting paths (`consolidate_rotated_devices`)
are more dangerous than the propose-only ones because there is no click to
catch the mistake.

**[2026-09-02] Problem:** Freshly added remote routers (a CCR1009 and another
box, both across the internet) showed a grey "offline" dot in the switcher
even though selecting them worked and telemetry streamed fine. `/routers`
`_probe` reused the **pooled telemetry client** (5 s timeout, shared circuit
breaker) for its status check. A remote router that took >5 s to answer one
probe tripped that client's breaker for 15 s - grey dot, and the telemetry
loop briefly poisoned too - and the sparse 30 s poll cadence meant the grey
state lingered. **Solution:** two parts. (1) `_probe` now uses
`router_manager.build_probe_client(r)` - a throwaway client with a 10 s
timeout and its own breaker, closed after the single request - so a slow
probe can neither wait too little nor knock out the real client. (2) The
frontend `RouterSelector` treats the selected router as online when the
telemetry WS is connected (`telemetryLive`), regardless of the last
`/routers` probe result - a flowing stream is proof of reachability and
should win over a 30 s poll that flaps. Lesson: a health probe must be
isolated from the connection it is reporting on; sharing the pooled client
and its breaker means the probe's own timeout becomes an outage.

**[2026-09-02] Feature, not a bug, but a hazard worth recording:** deleting a
router used to be `db.delete(router_obj)` and nothing else. With SQLite
`PRAGMA foreign_keys=OFF` (the deployment's state - only `journal_mode`,
`synchronous`, `busy_timeout` are set on connect) the models' `ON DELETE
CASCADE` never fires, so that left `router_traffic_rollups`,
`interface_traffic_rollups`, `system_metrics`, `interface_metrics`,
`speed_test_results` and `alert_logs` rows with a dangling `router_id`, plus
every `<base>_<id>` row in `app_settings`, and SQLite reuses `max(id)+1` so a
newly added router could inherit all of it. Delete now takes a `mode`:
`archive` (set `routers.archived_at`, filtered out of `get_client` and every
`get_*_router` helper and the `/routers` list, all data kept, re-add by
`serial_number` restores the row) or `purge` (a new `router_lifecycle.purge_router`
deletes every child table in child-before-parent order, inside one
transaction, and the `*_<id>` settings via `key LIKE '%\_<id>' ESCAPE '\'`).
"Change router" (`/routers/{id}/change`) rewrites the connection fields on the
existing row after test-connecting the *new* box only, so a dead old router
does not block the swap. Lesson: on a database where cascade is not enforced,
"delete the parent row" is never the whole delete - either enumerate the
children explicitly or turn the pragma on (and re-test everything that relied
on the lax behaviour). And a stable hardware id (`/system/routerboard`
serial) is what makes "archive then re-add" safe against SQLite's id reuse.

**[2026-09-03] Problem:** Router Health graphs (Interface Bandwidth, CPU, RAM,
Board Temp) were ~5 h behind the header clock on a UTC+5 box - x-axis and
tooltips in UTC while the rest of the UI showed router-local. Two causes.
(1) `metrics_collector.get_system_history` / `get_interface_history` returned
the raw naive-UTC `timestamp`; `MetricCharts` renders it with
`new Date(str).getHours()` and never applied any offset. (2) The router UTC
offset was a single shared `AppSetting` (`router_gmt_offset_minutes`, no id),
overwritten by whichever router streamed telemetry last - so switching to a
router in a different zone skewed its rollup `record_date`, billing-cycle
bounds and 1D history until its next tick (and any `collect()` in that gap
misfiled a day). **Solution:** the offset is stored per router
(`router_gmt_offset_minutes_<id>`, mirrored to the un-suffixed key as a
fallback for calls that cannot name a router); `get_router_offset` /
`router_local_now` / `router_local_date` / `store_router_offset` take
`router_id`, threaded through ws, metrics_collector, analytics_engine,
interface_rollups, rollups.slice_of_day_bytes, traffic_accounting and the
analytics/users/devices endpoints (user/device history resolve the offset
from the row's own `router_id`). The two metrics-history methods shift each
returned point by that router's offset, so the frontend needs no change -
`new Date("2026-09-04T00:38:00").getHours()` round-trips a naive local
wall-clock on any browser. Also pinned `int(r.timestamp.timestamp())` in the
bucketing to UTC (`.replace(tzinfo=timezone.utc)`) - on a non-UTC host it
assumed the system zone. Lesson: naive-UTC that leaves the backend must be
shifted to the display zone before it does, not left for `new Date` to guess;
and any value keyed "the router's timezone" has to be per router the moment
there is more than one.

**[2026-09-03] Problem:** Adding a user on router Marusyan (a remote CCR that
was unreachable) failed with a 500, even though the `users` row had already
been committed. `create_user` writes the DB row, then calls
`sync_user_queue` to push the Simple Queue to RouterOS - and the create/update
queue calls inside `sync_user_queue` were not wrapped, so a `ConnectError`
(or the transport's `RouterUnreachableError`) propagated out of the endpoint.
On the reachable Main Router it worked, which is why it looked
router-specific. **Solution:** the RouterOS-facing calls in `sync_user_queue`
(`create_simple_queue` / `update_simple_queue`) now log-and-continue on
failure, and `create_user` wraps the whole `sync_user_queue` call as well.
The user is returned; the background reconcile creates the queue once the
router is back. Lesson: any endpoint whose contract is "DB write + best-effort
router sync" must treat the router half as truly best-effort - a remote box
being down is the normal case, not an error path.

**[2026-09-03] Problem:** Adding `range_24h: "24H"` for the traffic-history
modal's new 24H button silently changed the Router Health range picker from
"24 Hours" to "24H". `frontend/src/i18n/translations.js` already contained a
`range_24h` (used by MetricCharts) and several other duplicate keys
(`range_7d`/`range_30d` exist twice each): a JS object literal keeps the
*last* value for a duplicate key, so the second block was already overriding
the MetricCharts labels and a third definition compounded it. **Solution:**
the modal uses a namespaced key (`hist_range_24h`); the MetricCharts
`range_*` keys were left alone. Lesson: grep the whole i18n file for a key
before adding it - the parity test only catches an en/ru mismatch, not a
duplicate within one language.

**[2026-09-03] Problem:** Changing quota warning thresholds in Settings did not
reflect on the main QuotaStrip until a full page reload or a 30s poll.
Wiring an `onQuotaChanged` callback in `SettingsModal.jsx` threw
`ReferenceError: onQuotaChanged is not defined` when saving because the prop was
not declared in `SettingsModal`'s parameter list, while a syntax error during
hook patching caused a blank screen during build. **Solution:** Explicitly declared
`onQuotaChanged` in `SettingsModal` parameters with fallback handling; broadcasted
`mikroman:quota-changed` custom event and `storage` update for instant cross-tab
reactivity in `QuotaStrip`; added automated unit and smoke tests (`npm test`) to
verify complete tree mounting and bundle compilation before updating containers.

**[2026-09-04] Problem:** Claiming an unassigned device into a new or existing user
profile left the user card's today volume (`bytes_today_in/out`) starting from
zero (or only counting traffic earned after the assignment), while the device row
and the analytics breakdown showed the device's full 800+ MB daily traffic.
Two causes: (1) `TrafficController.get_realtime_traffic_stats` and `users.list_users`
read today's user volume from `TrafficRollup` (written only while a device is owned)
instead of summing the owned devices' rollups like analytics and cycle totals do;
(2) `update_device` and `create_user` had no logic to backfill `TrafficRollup`
on assignment (only `detach_device_traffic_from_user` existed in reverse).
**Solution:** Added `attach_device_traffic_to_user` in `device_manager.py` to merge
the device's daily history into `TrafficRollup` upon assignment; derived user today
volume in `TrafficController` and `list_users` from the user's owned devices
(`device_metrics` and `today_rows`); backfilled existing assignments in `app.db`.
Lesson: user-level rollups must always be derived dynamically from or kept in lockstep
with the devices owned now, so claiming an unassigned client retroactively brings
its recorded consumption with it everywhere.

**[2026-09-04] Problem:** Unvalidated mutations to RouterOS firewall address lists or Simple Queues can lock the operator out (e.g. blocking loopback, 0.0.0.0/0, the router gateway, or the app server egress IP), overwrite foreign administrative rules, or trigger router panics via inverted queue limits (`limit-at > max-limit`).
**→ Solution:** Implemented a two-tier defense: (1) Pure invariant guards in `backend/app/services/guards.py` (`guard_immune_targets`, `guard_foreign_resources`, `guard_queue_invariants`) and (2) Wire-level enforcement in `RouterOSClient` (`FirewallMixin`, `QueueMixin`) with local egress IP autodetection via UDP socket probing. All mutations raise `WriteGuardViolation`, which API endpoints catch to return HTTP 400 with state rollback, while background sync workers log structured warnings and continue without crashing.

**[2026-09-04] Problem:** Running `pytest` across the full test suite caused write guard `caplog` assertions to fail because earlier Alembic migration tests invoked `fileConfig(config.config_file_name)` with the default `disable_existing_loggers=True`, silencing `mikroman.traffic_controller`.
**→ Solution:** Explicitly passed `disable_existing_loggers=False` to `fileConfig` in `backend/migrations/env.py`.

**[2026-09-04] Problem:** Live connection tracking across active networks requires resolving hundreds of remote destination IPs into geographical locations (country flag and name) and domain names. Querying external third-party Geo-IP APIs per connection causes severe rate-limiting (HTTP 429), stalls UI rendering, and leaks user traffic destinations to public external servers. Furthermore, terminating connections ("Kill") on RouterOS without write-guard validation risks cutting the dashboard's own REST API session or management access.
**→ Solution:** Built an in-memory, offline Geo-IP engine in `backend/app/services/geoip.py` resolving IPs in microseconds with zero external network I/O, RFC 1918 / loopback local network detection, and country flag emoji conversion. Added `ConnectionsMixin` to `RouterOSClient` utilizing RouterOS `.proplist` parameters to bound transfer payload, cross-referencing RouterOS `/ip/dns/cache` for domain resolution, and enforcing `WriteGuard` immunity checks before executing connection removals. Stored persistent traffic volume and hit counts in `UserDestinationStat` with multi-field sorting.

**[2026-09-04] Problem:** Streaming RouterOS logs on demand or scraping them in the background presents two operational hazards: (1) RouterOS by default does not log high-value diagnostic topics like `wireless`, `firewall`, or `wireguard` to memory, so operators viewing logs see nothing for those events unless `/system/logging` rules exist; (2) Adding or deleting logging rules on RouterOS risks accidentally deleting default system logging actions (like `info`, `warning`, `error`), rendering the router silent. Furthermore, continuous background log polling across multi-router environments can cause SQLite table bloat without a deduplication and pruning strategy.
**→ Solution:** Added `add_logging_rule` and `remove_logging_rule` in `SystemMixin` guarded by Write Guard (`guard_foreign_resources`), ensuring rules not prefixed with `mikroman:log:` cannot be deleted. Built `log_classifier.py` with deterministic pattern matching for security (auth brute-force), interface flaps, DHCP, wireless, and firewall drops. Implemented `LogCollector` with external ID deduplication and retention window pruning (14 days / 10,000 records). Provided a high-contrast terminal console modal (`RouterLogsModal.jsx`) with live 2.5s streaming, freeze-on-scroll-up auto-scroll, category chips (`All`, `Wireless`, `Security`, `Interfaces`, `DHCP`, `Firewall`, `Errors`), dynamic topic tags, and a logging configuration drawer with 1-click presets.

**[2026-09-04] Problem:** Automated and on-demand router backups face three critical hazards: (1) RouterOS REST commands `/export` and `/system/backup/save` return HTTP 200 before flash writing completes, meaning immediate file retrieval yields 0 bytes or corrupted archives; (2) RouterOS script exports include a timestamp header (`# YYYY-MM-DD HH:MM:SS by RouterOS ...`), causing raw SHA-256 fingerprints to detect false configuration drift on every run even when configuration is unchanged; (3) Temporary files abandoned on router flash risk NAND exhaustion and boot failure; (4) Overzealous retention pruning could delete historic baseline configurations needed for disaster recovery.
**→ Solution:** Built `BackupMixin` in `RouterOSClient` with a settlement polling loop (ensuring non-zero size stability across consecutive reads) and a guaranteed `sweep_temporary_files` invariant cleaning `mikroman-backup-*` artifacts on all exit paths. Implemented volatile header normalization in `backup_normalizer.py` for zero-false-drift SHA-256 deduplication. Developed a server-side unified diff engine (`diff_engine.py`) with line-level hunk parsing and a full-featured visual diff UI (`RouterBackupsModal.jsx`) supporting unified/split views, live diffing against active router state, and `.rsc` / encrypted `.backup` downloads. Added retention pruning with strict milestone pinning protection (`is_pinned=True`).


**[2026-09-04] Problem:** A whole feature shipped unreachable: `RouterBackupsModal.jsx` called seven `api.*` methods (`getRouterBackups`, `triggerRouterBackup`, `updateRouterBackup`, `deleteRouterBackup`, `getBackupDiff`, and the two download-URL builders) that were never added to `frontend/src/api/client.js`, so every click threw `api.getRouterBackups is not a function` into a console nobody had open. The component's own test suite was green because it does `vi.mock('../api/client')` — a mocked method exists whether or not the real one does. This is the *second* time this exact class of bug shipped (the first dropped `api.createUser` in a refactor and silently broke "Add User").
**→ Solution:** Added the missing methods, plus `frontend/src/api/client.contract.test.js`: it reads every non-test source file, extracts each distinct `api.<name>` reference with its call site, and asserts the real exported client has a function of that name. It also asserts it found more than 20 references, so a broken scanner regex cannot make the check vacuously pass. Lesson: any test that mocks a module boundary cannot verify that boundary — the contract needs a test that reads the source instead.

**[2026-09-04] Problem:** `WriteGuardViolation` subclasses `ValueError`, and `guard_queue_invariants` raised its `limit-at > max-limit` violation *inside* a `try` whose `except ValueError` existed to wrap rate-parse failures. The violation was caught by its own handler and re-wrapped, so the operator saw `[WriteGuard] [QueueInvariantGuard] Refused write for u1: [WriteGuard] [QueueInvariantGuard] Refused write for u1: Upload limit-at ...` and `err.reason` was an entire formatted message rather than a reason.
**→ Solution:** Narrowed the `try` to the `parse_pair` call alone and moved the comparisons after it. Lesson: when a custom exception subclasses a builtin, never let it be raised inside a `try` that catches that builtin — keep the guarded call and the guarded logic in separate blocks.

**[2026-09-04] Problem:** Three services were written, tested and then never started or called, so their features were dead on the running system: `backup_scheduler.py` was never imported by `main.py` (no automatic backup ever ran), `LogCollector` was never scheduled (`GET /logs?source=db` could only ever answer empty), and nothing at all wrote to `user_destination_stats` (the "Destinations & Domains" tab was permanently blank though its table, endpoint and UI all existed). Additionally `LogCollector` called `client.get_logs()` while `SystemMixin` only defined `get_log()`, the logs API passed a `prefix=` argument `add_logging_rule` did not accept, and `remove_logging_rule` did not exist at all — every one of those surfaced as "no logs" rather than as an error, because both the scraper and the endpoint swallow transport exceptions.
**→ Solution:** Started `backup_scheduler` in the FastAPI lifespan and added a `log_scrape_worker` background task that also drives the new `destination_collector`; added `get_logs`, the `prefix` argument and a guarded `remove_logging_rule`; covered the transport contract in `tests/test_routeros_logging_rules.py` with `respx`. Lesson: a service module with no import site is dead code, and a caller that swallows `Exception` turns a missing method into a silent empty result — pin the transport contract with a test that asserts the HTTP call was actually made.

**[2026-09-04] Problem:** The offline Geo-IP engine reported every IPv6 peer as being on the local network, and invented a country for most of the public IPv4 internet. `resolve_ip_location` normalised its input with `.split(":")[0]` to strip a `host:port` suffix, which turns `2001:db8::1` into `2001` — unparseable, so it fell through to the `LOCAL` branch. Where an address did parse but matched no prefix, a fallback guessed a country from the first octet using IANA's pre-2011 regional `/8` allocations, printing a confident and usually wrong flag. The built-in table itself labelled RIR-wide blocks (`91/8` → RU, `80/8` → GB, `217/8` → DE) that RIPE allocates across fifty countries.
**→ Solution:** Added an IPv6-safe `strip_port` (brackets, or exactly one colon meaning IPv4+port); trimmed the prefix table to ranges whose country is genuinely known; replaced the octet heuristic with an explicit `??` / "Unknown" 🌐 result; anchored the optional MaxMind database lookup to real paths (`MIKROMAN_GEOIP_DB`, the app's `data/`) instead of a CWD-relative one that resolved differently under uvicorn and pytest. The same one-colon rule fixed `_split_endpoint` in the connections endpoint, which had been mangling bare IPv6 endpoints into `2001:db8:`. Lesson: a fabricated answer is worse than a blank one in a diagnostic tool — "Unknown" is information, a wrong flag is not.

**[2026-09-04] Problem:** Duplicate keys inside one language block of `translations.js` silently overrode earlier ones and could not be caught by the existing parity test — a JS object literal keeps the last value, so both `en` and `ru` still had the key and the key counts still matched. Three live regressions were sitting in the file: the traffic-history modal's compact `range_7d: "7D"` / `range_30d: "30D"` / `range_custom: "Custom"` had shortened the metric charts' and analytics tab's "7 Days" / "30 Days" / "Custom Dates" labels, and the connections modal's `confirm: "Yes, Kill"` had redefined the generic Confirm button for the whole app.
**→ Solution:** Namespaced the modal's compact labels as `hist_range_*` (matching the existing `hist_range_24h`), renamed the kill button's key to `kill_confirm_yes`, dropped the exact repeats, and added a `translation source hygiene` test that reads `translations.js` as text, splits it at the `ru:` block and asserts each language declares every key exactly once. Lesson: a parity check between two dictionaries cannot see a collision inside one of them; that needs the source, not the parsed object.

**[2026-09-04] Problem:** `RouterLog` shipped with a model but no Alembic migration. It went unnoticed because `init_db()` runs `Base.metadata.create_all`, which creates new tables on the live SQLite deployment — but a database built purely from `alembic upgrade head` would have had no `router_logs` table and the log scraper would have failed on every tick. `purge_router` likewise still deleted only the pre-existing child tables, orphaning `router_logs`, `router_backups` (and their files on disk), `user_destination_stats` and `device_traffic_buckets`.
**→ Solution:** Added migration `023_router_logs`, extended `purge_router` to cover every table added today (unlinking backup archives from disk before dropping the rows that name them), and verified `Base.metadata.tables` matches a freshly migrated database exactly, up and down. Lesson: `create_all` in `init_db` masks a missing migration — diff the model metadata against a migrated schema, do not trust the running database as evidence.

**[2026-09-05] Problem:** 6.6 GB of a 20.7 GB day showed as unaccounted: the gateway total on `ether1` read 20.663 GB while the sum of every device's rollup came to 14.023 GB and the router's own traffic to 0.003 GB. IPv6 and FastTrack were both ruled out on the live router (only link-local addresses exist; there is no `fasttrack-connection` rule at all). The real cause was `_accountable_devices` filtering on `Device.is_active`: `is_active` flaps with each discovery pass, and `sync_counter_rules` deletes the passthrough mangle rules of any device that is not currently active. Everything such a device moved between the prune and the next pass that noticed it crossed the WAN matching no rule. Confirmed against the router: 7 hosts in its ARP table, only 6 with counter rules — `192.168.123.243` was live and had no counter, because both device rows holding that address were marked inactive. A second defect sat behind it: two rows sharing one IP would each get a rule matching the same packets, counting every byte twice.
**→ Solution:** Dropped the `is_active` filter — a `passthrough` rule is a counter and nothing else, so keeping one for an idle device costs a rule slot while dropping it costs data — and made `_accountable_devices` return at most one device per IP, choosing deterministically (active first, then most recently seen, then lowest id) so the choice cannot flip between ticks and churn rules. Pruning still happens for devices that are deleted or lose their IP, and still flushes the final counter interval first. Lesson: liveness flags are a display concern; never let one gate a counter, because the gap it opens is exactly the traffic nobody can later explain.

**[2026-09-05] Problem:** `RouterBackupsModal.jsx` and `RouterFirmwareModal.jsx` shipped written entirely in Tailwind utility classes (`fixed inset-0 z-50 bg-slate-900 rounded-xl ...`). Tailwind is not a dependency of this project and no build step generates those utilities, so every one of those class names was inert: the backups modal rendered as unstyled text at the bottom of the page instead of as an overlay, and the firmware modal was barely readable. Their own component tests were green throughout — the classes are valid strings, the components mount, and the tests asserted on text content only. The backups modal also carried a leftover debug `throw new Error('API_DIFF_ERROR: ' + ...)` above an unreachable `setDiffError`, so any failed diff took the modal down instead of reporting itself.
**→ Solution:** Rewrote both against the app's own modal chrome and CSS custom properties, removed the debug throw, and added `frontend/src/components/NoTailwind.test.jsx`, which reads every `className` in the source and fails on utility patterns that nothing defines, plus asserts no Tailwind package is installed. Lesson: a styling framework that is not installed fails silently and invisibly to every test that only checks text — the guard has to be on the source.

**[2026-09-05] Problem:** `routers.password` was stored as plain text. The database file is the artefact that actually travels — `scripts/backup.sh` snapshots it, `data/` gets copied between machines, a volume is handed to someone to look at — and a plain-text credential in it is a working key to the router it describes.
**→ Solution:** Added `backend/app/core/secrets.py` (Fernet; key from `MIKROMAN_SECRET_KEY` or a 0600 `data/.secret_key` generated on first run) and an `EncryptedString` SQLAlchemy `TypeDecorator` applied to `routers.password` and `router_backups.backup_password`, so encryption happens in bind/result rather than at call sites and no future query can forget it. Values carry an `enc:v1:` marker; anything without it is legacy plain text and is read back unchanged, with `encrypt_legacy_secrets()` rewriting those rows once at startup. A value that will not decrypt logs the key mismatch and returns empty rather than raising, so the app reports "cannot log in" instead of failing to boot.

**[2026-09-05] Problem:** Nothing warned that RouterOS management services were reachable from any source address. `/ip/service` takes an `address=` list of allowed source prefixes and leaves it empty by default, so `www-ssl`, `api-ssl`, `ssh` and `winbox` answer whatever can route to them.
**→ Solution:** Added `backend/app/services/security_audit.py`, which reads `/ip/service` on each background tick and records one `open_management_service` alert per router per day naming each unrestricted service and its port. It reports the finding as what it is — "accepts connections from any source address" — rather than as "exposed to the internet", because reachability is not something the router can tell us: a box behind CGNAT with no port forward is not reachable at all. A prefix list containing `0.0.0.0/0` or `::/0` anywhere counts as unrestricted, since one all-zero entry defeats the rest.

**[2026-09-05] Problem:** `docker compose build` failed on a read timeout fetching `aiogram-3.31.0` from `files.pythonhosted.org`. The failure itself was the network, but `--no-cache-dir` turned a transient stall into an unrecoverable one: every retry re-downloaded all 43 wheels from scratch, so on a slow link the build could never make forward progress — it always timed out somewhere in the middle before finishing.
**→ Solution:** Replaced `--no-cache-dir` with a BuildKit cache mount (`--mount=type=cache,target=/root/.cache/pip`, plus `--default-timeout=180 --retries=15`), and gave `npm ci` the same treatment (`/root/.npm`, `--prefer-offline --fetch-timeout=180000`). The cache lives in the builder, not the image, so image size is unchanged and each attempt keeps whatever it managed to fetch. Requires the `# syntax=docker/dockerfile:1.7` directive on line 1. Lesson: `--no-cache-dir` is right for a single-shot CI image and wrong for a machine with a flaky link — it converts "retry and continue" into "retry from zero".

**[2026-09-05] Problem:** The flash-sweep invariant never ran. `sweep_temporary_files` issued an unqualified `GET /file`, and RouterOS includes each file's `contents` in that response — so the body carried the raw bytes of every binary on flash and httpx failed to decode it (`'utf-8' codec can't decode byte 0xf3 in position 16007`). The exception was caught and logged as a warning, so every backup run silently left its `mikroman-backup-*` temp files behind, which is precisely what the invariant exists to prevent.
**→ Solution:** `GET /file` now sends `.proplist=.id,name`. Here `.proplist` is load-bearing rather than an optimisation. Lesson: when a RouterOS menu can contain file contents, never fetch it unqualified.

**[2026-09-05] Problem:** The first security-audit alert on a live hAP be3 read `www-ssl:443, www-ssl:443, www-ssl:443, www-ssl:443, www-ssl:443, www-ssl:443, winbox:8291, winbox:8291, ...`. RouterOS 7.24's `/ip/service` returns one row per listening socket rather than one per service — twenty rows on that router, five of them `www-ssl` with distinct `.id`s — and it also lists `resolver`, `dhcp`, `dhcpclient`, `reverse-proxy`, `btest` and `discover`, which are not the fixed eight-name set earlier notes assumed.
**→ Solution:** Collapse findings by (name, port) — the same service on a different port stays a separate finding, since that is a separate thing to lock down — and keep the management-service whitelist explicit so the router's own service rows do not pad the alert.

**[2026-09-05] Problem:** The top bar wrapped: the clock, language switch, theme toggle and Settings dropped onto a second row below the router note. The row was `display:flex; flex-wrap:wrap; justify-content:space-between` with three groups, and the brand block had grown to carry the app version chip *and* the selected router's board name and firmware (`hAP be^3 Media (7.24.2 (stable))`). Those two additions pushed the sum of the groups' base widths past the container, and `flex-wrap` resolves that by breaking the line — it never shrinks a group first. Nothing failed: every component test passed, because jsdom has no layout.
**→ Solution:** Moved the version chip to the footer and the board/firmware into the router selector's expanded list (where they describe a specific router rather than the app), then made the row `nowrap` with the note as the only flexible element (`flex: 1 1 240px`) and the controls pinned by `margin-left: auto` — not `space-between`, which puts them in the wrong place when there is no note. Labels give way before the layout does: tool labels hide below 1240px, everything else below 1100px, where the row is finally allowed to stack. The `hide-mobile` class used in four components turned out never to have been defined in CSS at all, so it had been doing nothing; it is now real. Lesson: `flex-wrap: wrap` on a toolbar is a layout with no failure mode you can see in tests — decide explicitly what shrinks and what wraps, at which width.

**[2026-09-05] Problem:** The same "ISP quota 50% reached" alert fired repeatedly on every background tick (four times within ~90 seconds on one live router) instead of once per billing cycle. `check_quota_thresholds` in `backend/app/main.py` read `already_fired` via `build_quota_status`, which calls `unfired_for_cycle(db, cycle_start, router_id=router_id)` - a *per-router* settings key - but recorded a newly-fired threshold with `mark_fired(session, status.cycle_start, threshold)`, with no `router_id` argument, which writes the *global* (untagged) key instead. The write and the read never touched the same storage, so every tick saw the threshold as still unfired and alerted on it again. `get_quota_config(session)` in the same function had the identical omission for the quota limit/thresholds themselves, masked only because router 1 falls back to the global key by design.
**→ Solution:** Pass `router_id=router_id` through both calls. Added `tests/test_billing_quota.py::test_check_quota_thresholds_does_not_re_alert_on_the_next_poll`, which calls the real `check_quota_thresholds` twice in a row and asserts the alert count stays at 1 - it fails against the pre-fix code (2 alerts) and passes after. Lesson: a "write" and a "read" of the same conceptual state that build their storage key from different argument lists (one with `router_id`, one without) will silently diverge the moment more than one router exists, and nothing catches it without a test that calls the real integration function twice.

**[2026-09-05] Problem:** Opening RouterOS Firmware & Updates always returned `500 Internal Server Error`. `FirmwareMixin` (`backend/app/services/routeros/firmware.py`) called `self._get(path)` / `self._post(path, json=...)` throughout - methods that do not exist anywhere on `RouterOSClient`. Every other mixin uses `async with self._get_client() as client: resp = await client.get(path)`; this one was written against an older client API from before the transport refactor and never updated. `tests/test_routeros_firmware_transport.py` still passed because it assigned `client._get = AsyncMock(...)` directly onto the instance - Python allows setting an attribute a class never defined, so the tests exercised a method that was never called in production and never noticed the real one raised `AttributeError` on every call.
**→ Solution:** Rewrote the mixin against the real `_get_client()` pattern (matching `system.py`), including the singleton-menu list-or-dict normalisation `get_system_resource` already needed. Rewrote the tests to mock at the transport boundary (`patch.object(client, "_get_client")`), the same way `test_routeros_backup_transport.py` does, so a mixin drifting away from the real client API fails here first. Lesson: `client._x = AsyncMock()` in a test proves nothing about whether `_x` exists on the class - mock at the layer the code actually calls (the transport), never at a method name typed into the test.

**[2026-09-05] Problem:** `check_quota_thresholds` computed `already_fired` from `unfired_for_cycle`'s per-router key but recorded it with `mark_fired`'s default (global) key, so a crossed threshold never registered as fired where it was later checked and re-alerted on every background tick. See the full write-up above (same date) for the fix and the regression test.
**→ Solution:** Already covered above; noted here because live evidence of the still-unfixed symptom (identical "50% reached" alerts a few seconds apart) kept appearing in the dashboard after the code fix, until the container was rebuilt - the rows already written to `alert_logs` before the fix do not retroactively disappear, and a code fix alone does not clear a table.

**[2026-09-05] Problem:** The Live Connections list showed two related symptoms: the header badge always read "250 connections" no matter how many the router actually carried, and the whole card kept growing taller the further you scrolled - the page never reached a bottom. Separately, the list would periodically go completely empty and quietly repopulate a few seconds later. Root causes were distinct: (1) `GET /api/v1/connections` truncates to `limit` (default 250) but only ever returned the truncated array, with no way to tell "250 connections, capped" from "there are exactly 250"; (2) the inline (tab) rendering wrapped the table in a `.card` with no bounded height, so its `flex: 1; overflow-y: auto` table wrapper had no definite height to constrain against and simply grew to fit every row instead of scrolling internally - only the modal rendering (`max-height: 90vh`) had ever been height-bound; (3) `get_live_connections` caught every failure fetching the router's connection table (a slow response, the transport's own circuit breaker while it was mid-cooldown) and answered `200 OK` with `data: []` rather than letting it propagate like every other router-backed endpoint does. A 3-second poll that fully replaces its state on each response cannot tell a genuine zero from a swallowed failure, so it periodically wiped the table with no visible error before the next poll's real data came back.
**→ Solution:** The endpoint now returns `PaginatedResponse[LiveConnectionItem]` (`{total, items}`): `total` counts everything that passed the router-side filters before `limit` truncated `items`, so the frontend can say "250 / 812" instead of a bare "250". The two try/excepts around `require_client`/`get_active_connections` were removed entirely - a fetch failure now propagates and becomes a real HTTP error, and the frontend's existing catch block (which never touched `connections`) keeps the last known-good list on screen instead of blanking it. The inline card gets `maxHeight: '70vh'` so the table's internal scroll actually engages. Lesson: a background-polled view that does a full state replace on every response cannot afford an endpoint that turns a real failure into a fake success - the two are indistinguishable to a caller with no other signal, and the failure mode is "the UI silently forgets everything periodically."

**[2026-09-05] Problem:** The quota strip briefly showed grossly inflated usage on load - live, a router with ~230 GB of genuine cycle-to-date traffic against a 2 TB limit showed "1.6 TB used, 78.7%, OVER LIMIT, projected 271%, at pace 322%". `build_quota_status` (`backend/app/api/v1/endpoints/analytics.py`) resolves `eff_router_id` (falling back to the active router when the caller passes none) and correctly uses it for the quota config, the billing anchor, and the router-local clock - but then queried both the current- and previous-cycle traffic with the original, still-possibly-`None` `router_id` parameter instead of `eff_router_id`. `None` means "every router combined" to `AnalyticsEngine.get_historical_traffic`, so on this multi-router install, `used_bytes` silently became the *sum of every router's traffic* while the limit stayed a single router's configured allowance. The frontend's `QuotaStrip` passes exactly this shape (`api.getQuota(activeRouterId || null)`) whenever `activeRouterId` has not resolved yet - i.e. reliably during the page's first render, hence "during loading" - though the bug fires identically on any call that omits `router_id`.
**→ Solution:** Both `get_historical_traffic` calls, and the two `slice_of_day_bytes` boundary-slice calls used at a non-midnight billing anchor, now use `eff_router_id` throughout. Added `tests/test_billing_quota.py::test_quota_used_bytes_is_scoped_to_the_active_router_not_summed_across_all`, which seeds two routers with very different traffic volumes and calls `build_quota_status(session, None)` - it fails against the pre-fix code (returns the 1.01 TB combined total) and passes against the fix (returns only the active router's 10 GB). Lesson: when a function resolves an "effective" id with a documented fallback, grep every remaining use of the *original* parameter name in the same function - one overlooked call site silently reverts to the old, wrong scope, and it will be the one call nobody is looking at because the rest of the function so clearly does it right.

**[2026-09-05] Problem:** The Firmware modal's "Update channel" `<select>` showed only the top sliver of its text ("stable" rendered as if clipped). The box overrode `.form-select`'s height to 30px but kept the class's own `padding: 10px 14px`, which needs roughly 38px (20px padding + ~18px line height) to fit a line of text without clipping.
**→ Solution:** The inline style now also sets `padding: '0 36px 0 10px'` (keeping room on the right for the class's dropdown-arrow icon) and `lineHeight: '28px'` (the 30px box minus its 1px border on each side), so the text is vertically centred inside the shrunk box instead of overflowing its top edge. Lesson: shrinking a form control's `height` without also shrinking its padding/line-height is a silent clip, not a resize - the two must move together.

**[2026-09-05] Problem:** The telemetry tiles clipped mid-word ("DOWN...", "UPL...", "RAM F...", "UPT...") once the tile grid was narrowed to a 111px floor and the value moved onto the same row as the label - a full-word label competing with the value for space on one line no longer fit either of them.
**→ Solution:** Dropped the text label entirely on Download, Upload and Uptime - the arrow icons and the duration format already say what those rows are, the same reasoning already applied to the WAN IP tile. Added dedicated short labels (`tile_ram_label`: "RAM"/"ОЗУ", `tile_users_label`: "Users"/"Польз.") for the two tiles whose value needs a label for context, kept separate from the shared `ram_free`/`clients_label` keys used elsewhere where the full word has room. Also trimmed `.tile`'s horizontal padding from 11px to 8px. Lesson: shortening a tile's width and moving its value onto the label's row are two changes that only work together if the label text itself is short enough to need no truncation - CSS ellipsis on both label and value at once is illegible, not a graceful degradation.

**[2026-09-05] Problem:** Requested: hide MikroMan's own routine REST session logins from the log viewer without hiding a login from the same account arriving from somewhere unexpected (the actual anomaly worth seeing). RouterOS logs a REST session's login/logout as an "account" pair roughly every ten minutes even with connection reuse working, and it was drowning the Auth category.
**→ Solution:** Added `is_self_api_login(message, username, own_ip)` in `log_classifier.py`, matched only against the log line variant that carries a source address (`user X logged in from Y via api|rest-api`) - the sibling line with no address is left alone since there is nothing to compare an IP against. `own_ip` is discovered with a UDP "connect" (`socket.connect((router_host, router_port)); s.getsockname()[0]`), which asks the kernel to pick a route without sending anything on the wire - a synchronous, effectively free way to answer "what address does the router see me as" without depending on RouterOS reporting it. Wired as `hide_self_api` on `GET /logs` (both `live` and `db` sources use the same predicate, so the rule can't drift into two different definitions across two call sites) and a checkbox in `RouterLogsModal`, persisted in `localStorage` as a per-viewer preference.

**[2026-09-05] Problem:** Most public-internet destinations in Live Connections showed 🌐 Unknown, including plainly single-owner traffic (Google's `64.233.160.0/19` legacy web range, Telegram's `149.154.160.0/20` datacenter range) that has been stable for two decades. The built-in prefix table is deliberately small - only ranges whose country is actually known, no RIR-wide guessing - but these two concrete, evidenced gaps were worth closing.
**→ Solution:** Added both ranges to `_BUILTIN_PREFIXES` with the same provenance-comment convention as the existing entries. A MaxMind `GeoLite2-Country.mmdb` in `data/` remains the actual fix for comprehensive coverage - the built-in table only ever covers the handful of ranges someone noticed and added, one report at a time.

**[2026-09-05] Problem:** Multi-architecture Docker container builds across `linux/amd64`, `linux/arm64`, and `linux/arm/v7` for RouterOS container deployments hit two major friction points: (1) compiling the static frontend bundle inside QEMU emulation on 32-bit ARM consumes 15–20 minutes and risks out-of-memory crashes; (2) `uvloop` distributes pre-compiled wheels only for x86_64 and aarch64, so pip on `linux/arm/v7` attempts a source build and fails without GCC/python3-dev in `python:3.12-slim`. Additionally, PyYAML (YAML 1.1) parses unquoted `on:` in GitHub Actions workflows as boolean `True`.
**→ Solution:** Set `FROM --platform=$BUILDPLATFORM node:22-alpine AS frontend` in `Dockerfile` to build static JS/CSS assets natively on the runner host at full speed regardless of target container architecture; added platform markers to `backend/requirements.txt` (`uvloop==0.22.1; sys_platform != "win32" and (platform_machine == "x86_64" or platform_machine == "aarch64")`) allowing ARMv7 to run uvicorn on standard asyncio while 64-bit platforms retain uvloop; and quoted `'on':` in `.github/workflows/release-docker.yml`.


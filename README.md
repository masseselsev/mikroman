# ⚡ MikroMan — MikroTik RouterOS Companion

An ultra-lightweight, high-performance companion app and Telegram bot for **MikroTik RouterOS 7.1+**, designed to run smoothly in RouterOS native containers or standard Docker hosts.

### RouterOS compatibility

| | Version | Why |
|---|---|---|
| **Minimum** | **7.1** | The release that first shipped the REST API this app speaks. Every other menu it uses predates RouterOS v7. |
| **RouterOS container deployment** | **7.4** | The `container` package. Not needed when running on a Docker host. |
| **Recommended** | **7.13+** | The `wifiwave2` menu was renamed to `wifi` in 7.13. Below it the app falls back to the legacy `/interface/wireless` menu, which works but reports less. |
| **Verified against** | **7.25** | hAP be³ Media. Newer releases are expected to work but are unverified — the app says so in the connection result. |

The floor is **derived from the code**, not asserted here:
[`backend/app/services/routeros_compat.py`](backend/app/services/routeros_compat.py)
declares every RouterOS menu the app touches and the release that introduced it,
and `MINIMUM_VERSION` is computed from that table. A router below the floor, or
above the highest verified version, is reported in the connection response and
in `GET /api/v1/system/status` — as a warning, never as a refusal. See
[`docs/ROUTEROS_API_POLICY.md`](docs/ROUTEROS_API_POLICY.md) for the rules that
govern adding new router calls.

---

## 🌟 Key Features

* **👥 Hierarchical User & Device Traffic Control:**
  * **Parent-Child Simple Queues**: Set user-level shared bandwidth pools (`mikroman-{user}`) with nested per-device caps (`mikroman-{user}-{device}`).
  * **Device-Level Precision**: Configure individual device limits (e.g. 5M, 15M, 50M) or inherit the user group limit.
  * **Configurable Quarantine Limit for Unassigned / Rotated MAC Devices**: Automatically caps all new, unassigned, or randomized-MAC devices to a configurable speed limit (e.g. 5 Mbps default, 1M, 2M, 10M, or Unlimited) on RouterOS Simple Queues until explicitly assigned to a user profile. The quarantine rate is a consequence of *having no owner* and is resolved from settings each time a queue is built — it is never written onto the device record. Assigning a device to a user therefore releases it immediately and the owner's limit takes effect at once.
  * **Device Limit Reconciliation**: A standing check on the queue-sync tick clears a quarantine limit found on a device that has an owner. Earlier releases copied the quarantine rate onto the device row at discovery and assignment never cleared it, so every device kept a 5M/5M child queue underneath an unlimited parent — the owner's limit never applied, and the queue tree read as if the household had been throttled at random. Only an exact match against the configured quarantine value is cleared, so a limit an operator chose is left alone.
  * **Instant Pause / Resume**: Freeze internet access for an entire user profile or a single rogue device using dynamic RouterOS Firewall Address Lists (`mikroman_blocked`).
  * **Idempotent Queue Synchronisation**: RouterOS normalises the values it stores (`192.168.88.10` → `192.168.88.10/32`, `5M/5M` → `5000000/5000000`). All comparisons are made in that normalised form, so a queue that is already correct is never rewritten. Managed objects are identified by stable id-based tags (`mikroman:managed:user_{id}`, `mikroman:managed:dev_{id}`) matched exactly, so renames never orphan a queue and no profile name can be mistaken for another whose name it prefixes.
  * **FastTrack Firewall Exemption**: Automatically patches default FastTrack rule with `!mikroman_queued`, guaranteeing strict queue enforcement while unshaped clients maintain maximum hardware throughput.
  * **Real-time Live Telemetry**: Live rate meters (download/upload) and daily volume counters updated via WebSocket. Rates are differentiated from the per-device firewall counters rather than read from Simple Queue `rate`, which can freeze on RouterOS 7.x; today's volume is read from the same rollups the analytics view uses, so the dashboard and the reports can never disagree.
  * **Queue Reconciliation**: Managed queues whose owning user or device no longer exists — or which no longer need their own queue after a device reverts to *Inherit User* — are removed automatically. A stranded queue keeps its old target and `max-limit`, so if that address is later reused it would silently throttle the new host. Queues MikroMan did not create are never touched.

* **📊 Historical Traffic Accounting & ISP Billing Cycles:**
  * **Firewall-Counter Accounting Engine**: Per-device volume is measured with dedicated RouterOS `/ip/firewall/mangle action=passthrough` counter rules (one for upload, one for download per device), tagged `mikroman:acct:dev_{id}:{up|down}`. `passthrough` only increments a counter and forwards the packet — it never drops, alters or reroutes traffic, and MikroMan never touches mangle rules it did not create.
  * **Why not Simple Queue counters**: On RouterOS 7.x the `bytes` counter of a Simple Queue can silently stay frozen at zero while traffic flows (verified on a hAP be^3 / RouterOS 7.25: a freshly created queue placed first in the queue order, targeting the busiest client, counted 0 bytes through a 4.9 MB burst). Simple Queues are therefore used for **bandwidth shaping only**; all accounting comes from firewall counters, which tracked 243.8 MB against 246 MB of real WAN throughput (99.1%) in the same measurement.
  * **Accounting Health Cross-Check**: Every analytics response carries an `accounting_health` block comparing gateway volume (WAN interface counters) against the sum of per-device counters — reported as `ok`, `partial` (range predates the accounting rules, or a router outage left only the gateway total for part of it), `degraded` (accounting active but attributing almost nothing) or `no_data`. A broken accounting path is surfaced as a dashboard banner instead of being hidden behind a plausible-looking total. The **Partial coverage** notice is dismissible per browser; it comes back on its own once the gap widens (a worse coverage figure or a change of status), since the missing per-device split for those windows cannot be reconstructed.
  * **Coverage is measured over the window it can honestly describe**: `coverage_pct` counts only the days per-device accounting ran from midnight to midnight. Volume recorded before that — including the switch-on day itself, which is inherently partial — is reported separately as `pre_accounting_bytes` alongside `measured_bytes` / `measured_accounted_bytes`, and the banner prints all three. Dividing the whole range by the whole range instead made a range reaching one day past the switch-on read **51.6%**, which looks exactly like half the traffic being lost and was not: on the same data the measured window was **90%**, and a full day with no reconfiguration is **98%** (the residual is Ethernet framing overhead plus, until the self-traffic rules below existed, the router's own DNS/NTP/API traffic).
  * **The range reconciles to the gateway total**: per-user volume is folded up from the per-device rollups by *current* ownership, not read from the parallel `traffic_rollups` ledger — the two are written from the same deltas but keyed differently (the device ledger follows the device; the user ledger stamped whoever owned it at each poll), so any reassignment made them disagree permanently. The response also carries every slice that belongs to the gateway total but to no profile — `unassigned` (devices nobody has claimed), `router_self`, and `unaccounted_bytes` (what the WAN measured that no counter could attribute) — plus `over_accounted_bytes` for when per-device rules counted LAN-to-LAN traffic at both ends and the attributed sum *exceeds* the WAN. The **by-user** donut draws all of them, so the ring reaches the figure in its centre. `POST /api/v1/analytics/history/reconcile-overcount` folds a historical over-count out of the daily rollups — days whose device total exceeds what the WAN carried (minus the router's own traffic) are scaled down to match and their per-user rollups rebuilt; it only ever removes volume, defaults to a dry-run report, and needs `?apply=true` (and a backup) to write.
  * **Survives a network outage; recognises a router reboot**: volume is accumulated as deltas against a *persisted* baseline, and a failed poll does not advance the baseline. While the connection is down the router keeps counting, so the first successful poll after it returns picks up the entire gap by ordinary differencing. When that gap spans local midnight the delta is **apportioned across the days it covers by clock time** rather than dumped whole onto the recovery day — an approximation, but far smaller than mis-filing a full evening onto the wrong date. A device that goes inactive *during* an outage has its rule's final counter flushed before the rule is pruned, so its share of the gap is not dropped either. A **reboot** is different — every RouterOS byte counter resets to zero, and a busy interface can climb past its stale pre-reboot baseline within one poll, which would read as a tiny delta and lose everything since the reboot. So `/system/resource` uptime is checked each tick; uptime running backwards is treated as an explicit counter reset and the bytes since the reboot are credited in full. (RouterOS has no persistent counter anywhere — `/ip/accounting` resets on reboot too — so the ~10 s between the last poll and a clean reboot is unrecoverable by design; a factory reset additionally clears the accounting rules, so per-device history restarts.)
  * **ISP Billing Cycle Anchor**: Set the exact day of the month (1–31) when your provider quota resets. The anchor also takes an **optional time of day** (router-local). At a non-midnight reset the cycle-start day's traffic is split at the reset minute using the WAN interface's sampled cumulative counters (`interface_metrics`, ~1.5 s spacing, 30-day retention); when those samples are already pruned it keeps the whole day. Setting a time also fixes two pre-existing bugs from the old date-only cycle math: `anchor_day = 1` (the default) produced a **two-month** current-cycle window instead of one calendar month; and a clamped anchor such as `31` in February gave `Jan 31 … Feb 28` — 29 inclusive days that double-counted Feb 28 and rolled the cycle over a day late. The datetime bounds return `Jan 31 … Feb 27` and roll over correctly at the start of Feb 28.
  * **Flexible Date Filtering**: Presets for *Today*, *Yesterday*, *Last 7 Days*, *Last 30 Days*, *Current Billing Cycle*, *Previous Billing Cycle*, *All Time*, and *Custom Date Ranges*.
  * **4-Level Synchronized Accounting**:
    * **Gateway Level**: Total bandwidth consumed, download/upload split, and peak rates across all monitored WAN interfaces.
    * **User Group Level**: Aggregated consumption, active device counts, percentage share of total gateway bandwidth, plus each profile's **all-time** and **current-cycle** volume and how long since any of its devices was last seen.
    * **Individual Device Level**: Searchable and filterable table with MAC, IP, vendor, assigned user, total bytes, all-time / current-cycle volume, **last active** (rounded up, exact timestamp on hover), and custom speed limits.
    * **Interface Level** (*By Interfaces* tab): one row per interface, rebuilt from the sampled counters, with its selected-range, current-cycle and all-time volume. Tunnel / overlay interfaces (WireGuard, ZeroTier, GRE, L2TP, …) sort to the top and carry a `tunnel` badge so a VPN link can be watched on its own; the WAN interfaces that make up the gateway total carry a `WAN` badge so the two are not added together. Backed by `interface_traffic_rollups` (one row per router / interface / router-local date), so history outlives the 30-day `interface_metrics` sample retention.
  * **The gateway rollup is derived from the samples, not a live counter**: `router_traffic_rollups` (and the per-interface table) are now **recomputed from `interface_metrics`** — walking the samples and bucketing each delta by the router-local date of the later sample, splitting any pair that straddles midnight. This attributes every byte to the day it actually moved and is unaffected by a container restart. The old live-counter accumulator credited a whole poll-to-poll delta to whichever day the poll landed on, which on a poll resuming after an outage past midnight filed a full night of traffic under the next morning (measured on the developer's own install: one day's gateway rollup read ~18 GB high, the neighbouring day ~13 GB low). A short trailing window is recomputed every tick for live figures; the full 30-day window is rebuilt once on startup, which also heals any history a previous version misfiled.
  * **Visual Daily Timeline**: Interactive daily volume charts with download/upload color-coded stacks.
  * **📈 Interactive User & Device Traffic History Modals**: Dedicated graph modal on every user card and device row across *By Users* and *Unassigned Devices*. View traffic consumption trends across **Day (1D)**, **Week (7D)**, **Month (30D)**, **Year (1Y)**, **All Time**, and **Custom Date Ranges** with interactive stacked download/upload bar charts, summary metrics (total volume, daily average, peak activity date), and per-device breakdown lists.
  * **Share pie charts**: the Overview breakdown draws two dependency-free donut charts for the selected range — consumption **by user** and **by device** — with the long tail folded into a single *Other* slice and a legend showing each slice's bytes and percentage.

* **📈 Hardware & Multi-Interface Performance Graphs:**
  * Interactive time-series charts for CPU load %, RAM usage %, Board Temperature (°C), and Board Voltage (V).
  * **WAN speed test, run on the router**: a button on the WAN IP tile measures the ISP link from the router itself, so the figure is the line's speed and not the Wi-Fi between the router and whatever machine is looking at the dashboard. RouterOS has no internet speed test of its own — `/tool/speed-test` and `/tool/bandwidth-test` both measure against *another RouterOS device* — so this runs Ookla's CLI in a container (`quay.io/tangent/speedtest-cli`, 2.7 MiB, arm64 + amd64). There is no `docker exec` over REST and `/container/shell` is console-only, but the image runs once and exits, which fits the REST surface exactly: start the container, then read its stdout back out of the RouterOS log. MikroMan adds the `container` logging action itself, since RouterOS does not create one and the output would otherwise be produced and discarded. Results are kept as history (`GET …/speedtest/history`) rather than as one latest value, because a single reading of a noisy quantity says very little. The parser matches every field independently and keeps partial results — a run that measured download and timed out on upload beats discarding both.
  * **The router's own traffic is measured too**: per-device rules match the `forward` chain and therefore *cannot* see anything the router does for itself — DNS, NTP, package and cloud checks, DDNS, whatever its containers pull, and MikroMan's own REST polling. All of it travels `input`/`output`, and until now appeared only as part of the unexplained gap between the WAN total and the sum of the devices. A `mikroman:acct:self:*` passthrough pair per monitored WAN interface now names that volume; it lands in `router_self_traffic_rollups`, counts as attributed in the coverage check, and is called out above the analytics breakdown as **Router itself**.
  * **Container workloads are not somebody's device**: a container's `veth` end answers ARP with a MAC and an IP, exactly like a laptop, so discovery finds it and creates a device record whether or not one is wanted. Suppressing the record would lose its traffic; leaving it in the unassigned inbox asks the operator to assign a Docker image to a family member. Devices seen on a `veth` interface are therefore flagged `is_container` at discovery, kept out of the inbox and the household breakdown (`GET /api/v1/devices?kind=client|container|all`, default `client`), and listed in their own **Container workloads** panel on the Containers tab. Their traffic is still accounted — it simply belongs to the router rather than to a person.
  * **Exact processor identity on the CPU tile**: RouterOS never reports a CPU part number on RouterBOARD hardware — `/system/routerboard` `firmware-type` names the *bootloader platform family* (`tile`, `ipq5300`, `al21400`, `ipq6010`, …) and `/system/resource` `cpu` only holds the instruction set (`tilegx`, `ARM64`). For example, a CCR1009 answers `tile` while the processor MikroTik publishes for it is a **Tilera TILE-Gx8009** (9 cores @ 1.2 GHz), an hAP be³ Media answers `ipq5300` while its processor is a Qualcomm **IPQ-5322**, and an RB5009 carries a **Marvell Armada 88F7040**. The exact part is resolved from the product code and board model against the published MikroTik hardware catalog in `backend/app/services/hardware.py` (covering CCR1009, CCR1016, CCR1036, CCR1072, CCR2004, CCR2116, CCR2216, RB5009, RB4011, RB3011, RB1100, L009, hAP/cAP/wAP, hEX, and CRS series). An unlisted board falls back to the family and the tile's tooltip says which of the two it is showing. Architecture, core count and clock sit beneath (`tilegx · 9 cores · 1200 MHz`); x86 / CHR keep using the real `cpu` string.
  * **Dynamic Temperature Scaling**: Automatically scales the Y-axis to zoom in on actual router operating temperatures (e.g. 68°C–76°C) rather than squishing values on a fixed 20°C–75°C range.
  * **Configurable Temperature Warning Threshold**: Custom thermal alert threshold (e.g. 75°C, 80°C, 85°C) with dashed visual threshold markers and telemetry warnings.
  * Multi-interface aggregate bandwidth monitoring (e.g. WAN `ether1` + `sfp-plus1`) with selectable interface checkboxes and live throughput sum.

* **📦 Container Management (RouterOS `container` package):**
  * A **Containers** tab for the selected router: list every container with its status, architecture, veth interface, root directory and start-on-boot flag; **start / stop / remove** per row; and an **add** form that creates one from a remote image (interface, root dir, hostname, command, entrypoint, a mount name and an env-list name picked from the router's own `/container/mounts` and `/container/envs`, plus start-on-boot and logging).
  * **Degrades gracefully**: the container package is optional and absent on a stock install. `GET /api/v1/routers/{id}/containers` always returns a `support` block — `ready`, `not_installed`, `disabled` (installed but needs enable + reboot) or `unreachable` — and the page shows an explanatory banner with the controls disabled rather than an error. Action endpoints return `409` when the feature is not ready.
  * Reference panels show the router's global container config (`registry-url`, `tmpdir`, `layer-dir`, `ram-high`) and the defined mounts and env vars. MikroMan only ever touches containers through the documented REST endpoints.

* **🔍 Device Discovery, Auto-Scan Toggle & Hidden Devices:**
  * **⚡ Auto-Sort Devices by Activity**: Toolbar toggle on the Users tab that automatically sorts devices within each user card by real-time activity — highest moving throughput first, then online status, then all-time volume, then today's volume. Hovering the toggle spells out that order.
  * **Background Auto-Scan Toggle**: Control automatic network polling with a single switch; can be paused directly from the Unassigned Devices inbox or Settings modal.
  * **Hidden Devices for Technical / IoT Infrastructure**: Mark infrastructure hardware (modems, smart home gateways, IoT sensors) as hidden so they don't clutter default views, with an unselected-by-default "Show Hidden Devices" filter. The *Unassigned Devices* tab badge counts them **separately** — an amber count for devices actually waiting to be sorted, and a quiet `👁` count for hidden ones — because a permanent "2" that turns out to be two deliberately parked records trains the eye to stop reading the badge at all.
  * Automatic network scanner pulling ARP, DHCP leases, and Wireless registration tables.
  * **WAN-Side Filtering**: ARP entries seen on the monitored uplink interface (typically the ISP gateway on `ether1`) are excluded from discovery, so upstream hardware is never ingested as a LAN client or given a quarantine queue.
  * **Which interfaces the WAN counters sum over is a plain nested picklist**: the Gateway Interfaces modal used to lean on `WAN Only` / `Select All` / `Clear All` buttons, and `WAN Only` matched interface names against `ether1|wan|pppoe|sfp` — wrong on a box whose uplink is a VLAN, a second port or a renamed interface. Those buttons are gone. `GET /api/v1/metrics/interfaces/list` now returns, per interface, `is_wan` (it carries a live default route, read from `/ip/route`'s resolved next hop — `<gw>%<iface>`, or a bare interface name for PPPoE) and `parent` (the physical or bridge interface a VLAN / PPPoE client / bridge port rides on, from `/interface/vlan`, `/interface/pppoe-client`, `/interface/bridge/port`). The modal shows the interfaces as a tree — a logical interface nested under its parent with an `on <parent>` note — with a **WAN** badge on the default-route interface(s). You tick the interface(s) that face the internet; the WAN Download/Upload counters sum over exactly that set, so a split uplink such as `ether1` **+** `vlan500` is two ticks. The two bandwidth tiles carry a WAN marker and list the selected set, full list in their tooltip.
  * **Stale ARP Rejection**: RouterOS keeps unresolved (`complete=false`) ARP entries after a host leaves. These no longer count as proof of presence, so departed devices are correctly shown as offline instead of lingering as *Active* with a stale signal reading.
  * **Wireless Association as Presence**: an authorized entry in the Wi-Fi registration table proves a client is online — more reliably than ARP, since a client can hold a stable radio link while its ARP entry expires or roam on without a DHCP lease of its own.
  * **📶 WiFi 7 Multi-Link (MLO) Awareness**: RouterOS reports a multi-link client as a single `mld*` entry that names no actual radio. MikroMan expands `mld-interfaces` and `mld-link-addresses` into the individual radio links and shows **each link with its own interface, band and signal** (e.g. `wifi2 5G·BE −62`). The headline signal is the strongest link, so a weak secondary link never makes a well-connected device look bad, and per-link readings are never invented when the router reports fewer than there are links.
  * **🔗 Multi-Adapter Devices**: a machine that reaches the network over more than one adapter — a laptop docked over Ethernet and roaming over Wi-Fi — has a different MAC per adapter and was previously discovered as several unrelated devices with its traffic split between them. Adapters can be **linked into one logical device**: the row appears once, marked `N×`, listing every live connection, with rate and volume summed across them and pause applied to all adapters at once (otherwise a paused machine simply hops media). Matching hostnames on different media are proposed automatically. This is distinct from **merging**, which exists for MAC rotation and collapses two records because only one address was ever real — here both remain valid and both are kept. Clicking the `N×` chip opens the bundle: every adapter is listed, and one grouped in by mistake can be **detached** back to its own device (`POST /api/v1/devices/{id}/unlink`).
  * Automatic MAC OUI vendor resolution (Apple, Samsung, Sony, Intel, etc.).
  * **🔄 Automatic Private-MAC Rotation Recovery**: iOS, Android and Windows generate a *new* private MAC whenever a network changes identity — a renamed SSID, a changed passphrase, or the user re-joining the network. To the router that is a first-time arrival, so discovery created a second record and the device's owner, custom name, speed limit and traffic history stayed attached to the address it had abandoned. MikroMan now recognises the signature — *a never-before-seen private MAC arrives carrying a hostname exactly one known device answers to, while that device's address has vanished from the router entirely* — and re-keys the existing record onto the new address, logging a `mac_rotated` history entry and an alert. It deliberately declines to guess: a generic factory hostname (`iPhone`, `android`, `MacBook-Pro`), several matching records, or the old address still being present all leave the case to the manual merge suggestions, because a wrong adoption would hand one person's device to another.
  * **Rotation is never mistaken for a second adapter**: two randomized MACs sharing a hostname are one device that changed address, not a dual-homed machine — phones have one radio and no socket. Relatedly, a bridge interface is now treated as *inconclusive* rather than as evidence of a cable: every wireless client's ARP entry is recorded against the bridge, and calling that "wired" made a rotated phone appear as one wired and one wireless record, which is exactly the pattern the adapter-linking heuristic scores highest.
  * **Automatic consolidation of a rotation pile**: discovery-time adoption only fires when it can identify a *single* prior record. Once two or more duplicates for one phone exist — a Wi-Fi change during a session can produce several in minutes — it can no longer tell which to adopt onto and every further rotation adds a row, so the dashboard fills with "Pixel-9-Pro-XL ×5" and the queue tree grows a branch per ghost. A pass on the background tick groups randomized-MAC rows by normalised hostname and, when every owned row in a group has the **same owner**, collapses them into the currently-active one — moving history and daily traffic across, adopting any unassigned duplicates onto that owner, and repointing links. A group split across two users is left alone (two people with the same phone model); a **generic** hostname (`iPhone`, `android`) is held to a stricter bar — one vendor, at most one row online — since a house can hold two.
  * **Two devices with one name are never merged**: three people who each own a bare `iPhone`, or one person with two of the same Pixel, would otherwise be collapsed by the consolidation pass. Two safeguards prevent it. **Co-presence is decisive** — every discovery sweep that sees two same-named private MACs online at once records the pair in `device_coexistence` (one radio cannot answer on two addresses at the same instant), and any group containing such a pair is left completely alone, with a once-a-day advisory alert asking the operator to rename one or split them between people. **A quiet period must pass** — a duplicate row is only absorbed after it has been continuously silent for `mac_rotation_settle_hours` (default 48h, overridable via that app setting); a phone that is merely asleep for the evening is not yet evidence of a rotation. Discovery-time adoption also refuses any candidate that carries a co-presence record, and the manual merge suggestions hide such pairs.
  * **Per-device volume readout**: each device row carries a compact `today / all-time / share` figure beside its name, in whole gigabytes (rounded), where *share* is that device's all-time traffic as a percentage of every assigned device's all-time traffic. Hovering shows the field legend and the exact byte figures. All-time totals are summed from the daily per-device rollups.
  * **Direct Owner / User Reassignment in Device Settings**: Devices can be reassigned to any user profile or moved back to unassigned directly from the Device Settings modal, without needing to open the full user profile editor.
  * Complete lifecycle event log: Discovery, Hostname changes, IP shifts, and Private / Randomized MAC rotations.
  * One-click **Smart Merge** suggestions to link rotated MACs back into original device profiles.
  * **Manual merge into a specific device**: assigning an unassigned device to a person creates a record of its own, which is the wrong outcome when it is the same phone back on a fresh randomised MAC and the heuristics were not confident enough to say so. Each card in *Unassigned Devices* therefore also offers **Merge into device…** — a picker of every known device labelled with its owner, running the same `POST /api/v1/devices/{id}/merge` the automatic suggestion uses. The source supplies the current MAC and IP; the target keeps its name, owner, limits and history, and the two devices' daily rollups are summed. A merge by hand also **deletes any `device_coexistence` record** for the pair, since the operator has overruled the co-presence evidence and the next discovery sweep would otherwise pull them apart again. It is irreversible and the confirmation says so.
  * **Bytes are not lost when a record is merged away**: the merged-away device's mangle rules keep counting on the router until the next sync prunes them, and readings for a device id that no longer resolves used to be discarded. An `acct_device_successors` map now redirects them onto the surviving record, collapsing chains (A→B→C credits C), and is cleared when the rules are pruned. A *deleted* device has no successor, but its row and rollups survive the soft delete, so its final counter reading is flushed onto its own retained history before the rule goes.
  * **Device maintenance from inside a profile**: each assigned device in the profile editor expands to three actions the checkbox list cannot express.
    * **Clear a stale IP** — sends an explicit null; the accounting rule and any queue for the old address are pruned on the next sync.
    * **Split a wrongly-merged MAC** — pick any address from the device's history and it becomes its own **unassigned** device, with the pair written to `device_coexistence` so the consolidation pass never folds them together again. Traffic recorded *before* the split stays with the original device: once daily rollups were coalesced by a merge, the individual share is gone and cannot be divided back out. Only future traffic on the split-off address is tracked separately.
    * **Delete the device** — a soft delete, also offered from the Device Settings modal. The row and its daily rollups stay, but it leaves every live view (user cards, the unassigned inbox, the per-device breakdown), its IP is released, and its accounting rule is pruned on the next sync. Its bytes remain attributed to the profile, which the analytics fold into a single **Old devices** line per user rather than listing each retired device. The same MAC turning up on the network again clears the flag and the device returns with the history it kept. A linked adapter that pointed at it is detached.
  * **Moving a device back to unassigned takes its traffic with it.** Per-device and per-user daily rollups are written from the same deltas, so when a device leaves a profile its recorded volume is subtracted back out of that profile's daily totals, date for date, clamped at zero (a device that was unassigned earlier contributed nothing then, and the rollups carry no per-date owner). `detach_traffic: false` on the device PATCH opts out. Deleting keeps the traffic (pooled as **Old devices** on the profile); unassigning removes it — the two actions are deliberately different.

* **🛡️ Multi-Router Management & Automated SSL Provisioning:**
  * **Complete Multi-Router Environment Isolation**: Each managed router operates as an independent environment with its own isolated user profiles, assigned and unassigned devices, Simple Queues, FastTrack exemptions, live telemetry, historical analytics, provider quotas (limit, thresholds, ISP modem/portal URLs), and monitored WAN interfaces. Switching routers does not bleed quotas or settings from other hardware.
  * **Live Router Switching**: Seamlessly switch active router contexts from the top navigation selector or the Settings modal. The dashboard, device inboxes, user cards, and WebSocket telemetry stream update instantly to the selected router.
  * **Editable Connection Details**: A saved router's host, port, transport, username and password can be corrected in place from *Settings → Routers*. This matters most after a factory reset, which drops the router's certificate, its REST user and its password at once and leaves a stored record that can no longer connect. Deleting and re-adding is **not** an equivalent workaround: `devices.router_id` is `ON DELETE SET NULL` so devices survive, but the gateway traffic rollups, system metrics and interface metrics are all `ON DELETE CASCADE` — re-adding the same router silently discards every traffic total and health graph recorded for it. The stored password is never returned by the API, so the field is left blank and a blank field means *keep the current one*; **Test Connection** stays disabled until a password is typed, because testing with an empty one registers on the router as a failed login for that user.
  * **Auto-Configure SSL**: Automatically generates a TLS certificate directly on RouterOS and enables HTTPS REST API (port 443).
  * Support for custom CA root certificates for enterprise PKI environments.

* **🤖 Dual-Mode Telegram Bot:**
  * Works in both **Long Polling** (zero-config behind NAT) and **Webhook** modes.
  * **Authenticated Webhooks**: MikroMan registers a per-process secret with `setWebhook` and verifies the `X-Telegram-Bot-Api-Secret-Token` header on every delivery, rejecting anything unsigned — without which any host able to reach the endpoint could inject bot commands such as `/reboot`. It **fails closed**: if no secret has been established, deliveries are refused.
  * **Webhook setup**: Telegram must reach the URL from the internet over HTTPS on port 443, 80, 88 or 8443 with a valid certificate, so a LAN address will not work — point it at this app's `/api/v1/telegram/webhook` through a reverse proxy or tunnel. Switching back to Long Polling clears the registered webhook automatically, since Telegram refuses polling while one is set.
  * Remote router status snapshot: `/status`.
  * Interactive traffic management with inline buttons: `/users`, `/pause`, `/limit`, `/reboot`.
  * Proactive alerts for newly discovered devices, high CPU load, temperature warnings, and WAN IP changes.

* **🎨 Modern Responsive Web Dashboard:**
  * **Three-Column Device Rows**: Each device row is a text column (name and badges, IP and vendor, radio links and signal), a fixed-width figures column (live download/upload and today's volume) and fixed actions. Only the text column shrinks, so a device jumping from `0 bps` to `12.4 Mbps` can neither reflow the name nor push the numbers past the card edge. Radio bands (`5G·BE`, `5G·AX`) are tagged in a dedicated accent colour, deliberately outside the status palette — a band is not good or bad news, but it should be readable at a glance.
  * **Per-Device Live Metrics**: Live rate and daily volume are exposed per device, not only per user profile, so the specific device saturating the link is named directly.
  * **Compact Telemetry Strip**: Download, Upload, CPU, RAM and Temperature carry inline **sparklines** built from the live telemetry stream (no extra requests, no charting dependency), alongside the **WAN IP**, **active client count** and uptime. Temperature is coloured against your configured warning threshold.
  * **Compact range totals**: the four figures at the top of *Traffic Analytics* (combined / download / upload / active profiles) render through a shared `StatTile` at roughly half their former footprint — 140px minimum instead of 220px, a 30px icon instead of 44px. At full card size that strip alone filled the first screen on a laptop and pushed the breakdown, the part of the page anyone came for, below the fold.
  * **WAN Identity Tile**: shows the interface address, the address the internet actually sees (they differ under carrier-grade NAT), and the **provider name** — resolved together in one cached lookup, since an AS number and operator name are the only way to tell two links apart when both hand out CGNAT addresses. Failure is silent: the router may legitimately have no internet.
  * **External IP Lookup**: the public address is a link. Click it to open the address on 2ip.io, IPinfo, WhatIsMyIPAddress, AbuseIPDB, Shodan or the BGP Toolkit; with several enabled, the click opens a menu instead of guessing. Settings chooses which are offered and which one a plain click follows, and accepts **your own URL template** — anything containing `{ip}`, which is the only token substituted. Templates are validated on both sides of the wire (`http`/`https` only, no embedded credentials), because a stored template ends up as the `href` of a link you click, and a `javascript:` URL there would execute in the page's origin.
  * **Single Design System**: sizes, widths and corner radii come from one set of CSS tokens — an eight-step type scale, a five-step radius scale and two control heights — instead of the 29 font sizes, 8 raw pixel radii and 40 ad-hoc paddings the components had each invented for themselves. Segmented selectors, panels, list rows, setting rows, dropdowns and toolbar controls are shared classes, so a control cannot look different in two places.
  * **Sortable Analytics Tables** with share-of-traffic bars, defaulting to the heaviest consumer first.
  * **Interface Link Health**: per-interface RX/TX volume plus error and drop counters — the earliest warning of a failing cable or saturated link.
  * **Discovery Context**: unassigned devices show first-seen time and their consumed volume **today, this billing cycle, and all time**, so an unknown client that has been quietly pulling data for days stands out from a fresh arrival that moved nothing.
  * Native **RouterOS Dark Mode** (WinBox slate/blue) and **RouterOS Light Mode** (WebFig).
  * Full bilingual **English (`en`)** and **Russian (`ru`)** language switching.
  * Compact, responsive layouts tailored for mobile, tablet, and desktop viewports.

* **⚙️ Router-Friendly Polling:**
  * **Pooled keep-alive connections**: the RouterOS client holds one connection instead of opening a new TLS session per request. Measured on a live hAP be^3, this cut the app's CPU cost on the router from **+6.6 to +2.4 percentage points** over idle (median 8% → 5% against a 2% baseline), with peaks halved. Client instances are cached per router and keyed on a fingerprint of their connection parameters, so a router edited in Settings retires its old pool rather than being served a stale one. (The cache was previously consulted only when an explicit router id was passed, which no call site does — it was written on every call and read on none, so every request quietly built a fresh client and keep-alive was never actually in effect.)
  * **Fail-fast when the router is unreachable**: a failure to reach the router suppresses further connection attempts for 15 seconds, and any answer at all — including `401` or `500` — closes the circuit immediately. Without it every router-touching endpoint paid the full connect timeout on every request: with the router off the network, `/routers`, `/users`, `/system/status` and `/system/interfaces` measured 4.6–5.0s each, so the dashboard sat blank for five seconds on every load and every poll tick. After: 0.002s. Correcting the connection details in Settings retires the cached client, so a repaired router is picked up at once rather than after a cooldown.
  * **Router-side session cycling is RouterOS, not the client**: the router's log shows a `user rest logged in/out` pair every ten minutes even with pooling working. Measured over six consecutive cycles, the re-login lands within 0–3 seconds. RouterOS keys a REST session by source address and user rather than by TCP connection and ages it out on its own timer, so this neither reflects nor responds to client-side connection reuse.
  * **Configurable telemetry interval** (1–10s) in Settings: each poll costs several REST calls, so a longer interval trades responsiveness for router CPU.
  * Rarely-changing values — WAN IP, router clock — are cached rather than re-read every frame.

* **🗒️ Per-Router Note:** a free-text comment for the selected router, in the header between the router selector and the clock. Collapsed it shows the first three lines; a click drops a full editor down (`Ctrl`/`⌘`+`Enter` saves, `Esc` cancels) and it collapses again on save. Stored on the router record (`routers.comment`), so it follows the router, not the browser — a place for its location, ISP account number, config quirks or maintenance window.

* **🧭 UI chrome:** the vertical scrollbar gutter is reserved on every tab, so switching between a tab that scrolls and one that does not no longer shifts the layout sideways. A quiet page footer carries the copyright line and a link to the project source.

* **📉 ISP Cycle Data Limit:**
  * Set an allowance for the billing cycle and watch consumption against it, with remaining bytes and the **daily budget** needed to stay inside it.
  * **The quota lives only in the always-on strip.** When a limit is set, a slim band sits under the header tiles on *every* tab — used / limit, days left in the cycle, an **on-track / over-limit** verdict, and two end-of-cycle projections: a conservative one from the cycle-so-far daily average (the headline, and what the verdict is judged on) and an **at-current-pace** figure. The at-pace figure blends the last few recorded days with the **previous billing cycle's daily average** on a weight that ramps over the first week, so a single heavy day early in the cycle no longer throws it; a tooltip says whether it is `blended`, `recent`-only, or `sparse` (falls back to the projection). The hover also carries the cycle window, remaining bytes, daily budget and last cycle's average. Clicking the strip opens Settings. (There is no longer a separate quota panel on the Analytics tab — it was folded into this strip.)
  * **ISP / modem portal link**: Settings takes a URL for the provider's own usage page (or the modem's stats page) and an optional button label; it then appears as a one-click button on the quota strip. Validated `http`/`https` only, no embedded credentials, since it becomes the `href` of a link you click.
  * Alert thresholds and the Telegram toggle round-trip through the API, so turning notifications off stays off.
  * **Multiple alert thresholds** (50/75/80/90/100%) fire **once per cycle** each and re-arm automatically when the cycle resets — checked on the background tick, so a warning arrives even with no browser open, optionally to Telegram.

* **🕒 Router-Local Time:**
  * **Daily boundaries follow the router's calendar.** The container almost always runs UTC while the router sits in a local zone, so on a UTC+5 router everything after 19:00 local was filed under the previous day and "today" meant a different day than the router's clock showed. Rollups, billing cycles and range presets are all keyed to the router's date.
  * The navbar shows the **router's own clock and timezone**, since every figure on the dashboard (lease ages, billing cycles, daily rollups) is anchored to the router while the container usually runs UTC. The offset is fetched once a minute and the browser advances the clock itself, so a live time costs no extra polling.

* **🪶 Ultra-Lightweight Footprint:**
  * Consumes **< 45MB RAM** and negligible CPU.
  * Multi-stage Docker build with container image size **< 80MB**.

---

## 🧱 Code layout

Five files had grown past the point where anyone could hold them in their head,
and every new feature had to open them. They were split along the seams that
already existed, with public surfaces left untouched — every prior import still
resolves, and the test suite was green after each step.

| Module | Was | Now | Split along |
|---|---|---|---|
| `services/routeros/` | 1064 | 43 (+7 modules) | one mixin per RouterOS menu — `transport` (pooling + circuit breaker), `certificates`, `system`, `clients`, `queues`, `firewall`, `containers` — composed into `RouterOSClient` in `client.py`. The connection is genuinely shared; the menus have nothing to do with each other. |
| `services/device_manager.py` | 986 | 560 (+521) | discovery stays; merging and rotation cleanup move to `device_consolidation.py` as a mixin. Discovery asks what is on the network *now*; consolidation asks which of yesterday's rows were the same device, and answers with evidence gathered over days. |
| `components/SetupWizard.jsx` | 1041 | 258 (+3 steps) | one component per step. The ~20 pieces of connection-test and certificate state were used by step 1 and nowhere else, so they moved into it — the shell now holds only `step`, the two forms, and `saving`. |
| `components/TrafficAnalytics.jsx` | 897 | 584 (+5) | one component per breakdown tab (`OverviewTab`, `UsersTab`, `DevicesTab`, `InterfacesTab`), plus `analytics/tableParts.jsx` for the sort header, share bar and comparator the tables share. |
| `services/rollups.py` | — | new | six near-identical hand-written rollup aggregations became one `sum_by`. They had already drifted: the router filter was applied to two of the three router queries. |

Two things guard the frontend split, because a bundler cannot: the
`SplitComponents.smoke.test.jsx` suite renders every extracted component, and
`frontend/scripts/check-identifiers.cjs` reports JSX referencing a component
that was never imported — which builds cleanly and renders a blank page.

---

## 🚀 Quick Start (Docker / Docker Compose)

**No configuration files to edit.** The router is added through the setup wizard
on first run, and its credentials are stored in the database — not in a file on
disk.

```bash
git clone https://github.com/your-org/mikroman.git
cd mikroman
docker compose up -d
```

Open **`http://localhost:1928`** and the setup wizard walks you through the
router connection, an optional Telegram bot, and your theme and language.

> **`.env` is not used by the Docker deployment.** `docker-compose.yml` declares
> no `env_file` and performs no variable substitution, and `.env` is excluded
> from the image — so editing it has no effect on the container. It is read only
> when running the backend directly from the repository root (see below), where
> it remains an optional alternative to the setup wizard.
>
> Earlier revisions of this file told you to copy `.env.example` and put your
> router password in it. That was wrong: it did nothing, and it pre-filled the
> username `admin`, which the connection form then probed the router with.

### Running the backend directly (development)

```bash
python -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
cp .env.example .env          # optional — the setup wizard covers the same ground
.venv/bin/uvicorn backend.app.main:app --host 0.0.0.0 --port 1928
```

Here `.env` *is* read, because pydantic-settings loads it from the working
directory. Router credentials supplied this way are used only when no router has
been configured in the database; an unset `ROUTEROS_PASSWORD` means "no
credentials were supplied", and the app will not attempt a login rather than
guessing at one.

---

## 💾 Backup & restore

Everything MikroMan knows is in **one SQLite file** — `/data/app.db` inside the
container, on the `mikroman_data` volume. Users, the device inventory, router
credentials, and the part that cannot be reconstructed: the historical daily
traffic rollups, which accumulate over months.

**What the design already protects.** Traffic accounting reads the router's
monotonic counters and stores *deltas against a persisted baseline*. On a failed
read the baseline is not advanced, so an outage — router unreachable, or the
container itself stopped — loses almost nothing: the router keeps counting, and
the first successful poll after reconnect captures the whole gap (if it spanned
midnight, the gap's bytes land on the recovery day — the total is kept, the
per-day split for that window is not). Data is genuinely lost only when the
router's counters *reset* underneath a gap (a reboot loses up to one poll
interval; a **factory reset** also wipes the accounting rules, so per-device
history restarts from zero), or when the volume itself is destroyed.

**What you must set up: a backup.** Nothing guards against `docker compose down
-v`, `docker volume rm`, or a failed disk.

```bash
scripts/backup.sh                        # one consistent snapshot into ./backups, rotated
MIKROMAN_BACKUP_DIR=/mnt/nas/mikroman scripts/backup.sh
```

`backup.sh` uses SQLite's online-backup API through the container's own Python,
so it runs with **no downtime** and produces a transactionally consistent file
even while the poll loop is writing. Each snapshot is `integrity_check`ed before
it is kept, written atomically, and copies beyond `MIKROMAN_BACKUP_KEEP` (14)
are pruned. If the container is stopped it falls back to a cold copy. Put it on
cron:

```
15 3 * * * /path/to/mikroman/scripts/backup.sh >> /var/log/mikroman-backup.log 2>&1
```

To restore:

```bash
scripts/restore.sh backups/app-20260831-031500.db
```

`restore.sh` stops the container, keeps the database it is about to overwrite as
`app.db.pre-restore-<timestamp>` on the volume, clears the stale `-wal`/`-shm`
sidecars, swaps the file in, and starts the container again.

**WAL mode.** The database runs in `journal_mode=WAL` with
`synchronous=NORMAL`. WAL is what lets the 10-second poll loop and a dashboard
request stop colliding on `database is locked`, and what makes the hot backup
above safe. `NORMAL` is the durability level WAL is built for: an application
crash cannot corrupt the file, and an OS crash or power loss can cost at most
the last transaction — one telemetry sample, which the next poll rebuilds from
the router's counters. The setting is applied on every connection and the
effective mode is logged at startup.

---

## 📦 Running in MikroTik RouterOS 7.4+ Container

> ### ⚠️ Put the container on external storage
>
> **Store the container and its data on a USB flash drive or, preferably, a USB
> SSD — not on the router's internal storage.** This is a strong
> recommendation, not a preference.
>
> RouterOS's internal storage is NAND flash with a finite number of write
> cycles and no wear levelling worth relying on. MikroMan writes continuously
> by design: telemetry samples, interface metrics, daily traffic rollups and
> device history all land in SQLite, and the container image itself consumes a
> large share of the free space on most boards. Running that workload against
> internal flash wears it out and can eventually take the router's own
> configuration storage with it.
>
> A USB SSD is preferred over flash for the same reason: it has real wear
> levelling and far higher endurance under the sustained small writes a
> database produces.
>
> ```routeros
> # Verify the external disk is mounted, then point containers at it
> /disk print
> /container/config/set registry-url=https://registry-1.docker.io \
>     tmpdir=usb1/pull ram-high=256M
> # ...and give the container's root-dir and any mount a path on usb1
> ```

1. **Enable Container Mode on RouterOS:**
   ```routeros
   /system/device-mode/update container=yes
   ```
2. **Attach external storage** and confirm it appears under `/disk print`.
3. **Execute Setup Script:**
   Import or run the commands from [`scripts/setup_ros_container.rsc`](scripts/setup_ros_container.rsc),
   adjusting `root-dir` and the data mount to your USB disk.
4. Access the dashboard via your router LAN IP at port `1928` (e.g. `http://192.168.88.1:1928`).

### HTTP or HTTPS?

**Running inside the router's own container: use plain HTTP.** The REST session
never leaves the device, so TLS protects nothing and only adds a certificate to
manage and renew.

**Running on a separate host: use HTTPS.** The session crosses your network,
and RouterOS REST authenticates with HTTP Basic — credentials in every request.

TLS is *not* a performance argument either way. Measured on a hAP be³ /
RouterOS 7.25, 8 req/s of `GET /interface` over 40 s per phase, sampled by an
identical low-rate sampler in every phase:

| Transport | Median CPU | p90 | Max | Requests completed |
|---|---|---|---|---|
| Idle baseline | 2% | 3% | 3% | — |
| HTTP, keep-alive | 5% | 7% | 10% | 268 |
| **HTTPS, keep-alive** | **5%** | **6%** | **7%** | **268** |
| HTTPS, new connection per request | 12% | 24% | 27% | 193 |

Encrypting the *stream* is free; the expensive part is the TLS *handshake*.
Because MikroMan pools connections, it performs one handshake and reuses it —
so HTTPS and HTTP cost the router the same. Without pooling the same workload
costs **2.4× the CPU and completes 28% fewer requests**, which is what the
connection-reuse work fixed.

---

## 🧪 Testing & Verification

Run the automated backend suite with pytest:
```bash
. .venv/bin/activate
pytest -v
```

Run code formatting and lint check:
```bash
ruff check backend tests
```

Run the frontend unit tests (Vitest + Testing Library):
```bash
cd frontend && npx vitest run
```

Build the frontend React bundle:
```bash
cd frontend && npm run build
```

Check that every JSX component is actually imported where it is used:
```bash
node frontend/scripts/check-identifiers.cjs
```
`<Foo />` where `Foo` was never imported is a runtime `ReferenceError`, not a
build error — Vite bundles it happily and the page renders blank. That is
precisely the failure mode of moving markup between files, so this runs over
`frontend/src` and reports any component a file references but never brings in.

### The suite never touches the network

An autouse fixture refuses every socket the tests try to open, and all fixture
addresses come from RFC 5737 TEST-NET-1 (`192.0.2.0/24`), which cannot route.

This is not hypothetical hygiene. The suite previously used the author's own
router address as fixture data, and the request paths outside its `respx` blocks
dialled it for real — the router logged three `login failure for user admin ...
via rest-api` per run, indistinguishable from a brute-force attempt against the
default account and enough to trip an anti-bruteforce rule against the
development machine. Nothing could fail as a result, because those calls sit
inside `try/except` blocks meant to tolerate an unreachable router; the only
evidence was in the router's own log.

The guard sits at the socket backend rather than at the HTTP transport, because
a mocked request never opens a socket while `respx` patches the layers above.
A test that genuinely needs a connection must request the `allow_real_network`
fixture, which makes it a visible, deliberate act.

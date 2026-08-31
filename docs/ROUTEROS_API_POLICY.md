# RouterOS API Policy

Rules for anything in this repository that talks to a MikroTik router.

These are repository rules, not general working instructions — they live here
rather than in `AGENTS.md` because they bind the code, not the process.

---

## 1. The version floor is derived, never asserted

`backend/app/services/routeros_compat.py` is the single source of truth. It
lists every RouterOS menu the app touches, the release that introduced it, and
whether the app can run without it. `MINIMUM_VERSION` is computed from that
table — it is not a number typed into a README.

**Current floor: RouterOS 7.1** (the release that first shipped the REST API).
**Verified against: 7.25.** **Container deployment: 7.4** (the `container`
package).

If you change the floor, `tests/test_routeros_compat.py` fails on purpose. Fix
the table, the README, and this document together.

## 2. Every new router call gets an entry in the table

Adding a call to a RouterOS menu means adding an `ApiRequirement` for it before
the feature is considered done. The entry records:

- the menu path,
- the RouterOS release that introduced it,
- whether it is **required** (the app cannot run without it) or **optional**,
- a `note` saying *how you established the version*.

An entry without a real note is worse than no entry: it looks verified when it
is not.

## 3. Check the API's freshness before writing the call

Before using a RouterOS menu, property or command that is not already in the
table, confirm against MikroTik's current documentation:

- Does the menu still exist under that path? Menus get renamed — `wifiwave2`
  became `wifi` in 7.13, and code written against the old path breaks silently
  on newer routers because REST returns 404, not an error you would notice.
- Do the property names still match? RouterOS renames fields between releases,
  and a `.get()` that quietly returns `None` produces a blank panel, not a
  stack trace.
- Is the behaviour actually what the documentation claims? Verify against a
  real router where the answer matters. Simple Queue byte counters are
  documented as counters and do not count on 7.25 — that was found by
  measurement, not by reading.

Cite the source in the `note`. "MikroTik docs, WiFi page" is a citation;
"probably fine" is not.

## 4. Warn when a router falls outside the supported range

Version checks are **advisory and never blocking**. MikroTik ships releases
faster than this table is updated, and a stale table must not lock an operator
out of their own router.

- Below `MINIMUM_VERSION` → warn loudly, still attempt the connection.
- Missing an optional menu → say which feature is unavailable and why, in the
  connection response and in `/api/v1/system/status`.
- Above `VERIFIED_VERSION` → warn that the version is untested and point at
  this table.

Never fail a request purely because a version string looked unfamiliar.

## 5. Optional features degrade, they do not crash

A menu marked optional must have an explicit fallback or a clean omission:

- `/interface/wifi/registration-table` falls back to
  `/interface/wireless/registration-table` below 7.13.
- `/system/health` is absent on boards without sensors; the tiles are hidden.
- Wi-Fi 7 `mld-*` fields are read with `.get()`; a router without them shows a
  single link rather than several.

Read optional fields with `.get()`. Never index into a router payload.

## 6. Accept every shape RouterOS has used

REST returns different shapes across versions for the same menu — a single
record on one release, a list on another; `gmt-offset` as `+05:00` on one, as
raw seconds on another. Parsers accept every documented shape and return `None`
rather than guessing when they cannot tell. A wrong value shown confidently is
worse than a blank.

## 7. Do not trust a counter without measuring it

Any RouterOS counter used for accounting must be verified against real traffic
before being relied on. The reason this project accounts from
`/ip/firewall/mangle` passthrough rules rather than Simple Queue `bytes` is that
the queue counters were measured returning zero through a 4.9 MB burst on
7.25, while the firewall counters tracked 243.8 MB against 246 MB of real WAN
throughput.

---

## Compatibility table

| Capability | Since | Required | Effect when missing |
|---|---|---|---|
| `/rest` (REST API) | 7.1 | yes | Nothing works — this is the floor |
| `/system/resource`, `/interface`, `/interface/monitor-traffic` | 7.1 | yes | No telemetry |
| `/ip/address`, `/ip/arp`, `/ip/dhcp-server/lease` | 7.1 | yes | No device discovery |
| `/ip/firewall/mangle`, `/filter`, `/address-list` | 7.1 | yes | No accounting or pausing |
| `/queue/simple` | 7.1 | yes | No bandwidth shaping |
| `/system/clock` | 7.1 | yes | Daily boundaries fall back to container time |
| `/system/health` | 7.1 | no | Temperature and voltage tiles hidden |
| `/interface/wifi/registration-table` | 7.13 | no | Falls back to the legacy `wireless` menu |
| Wi-Fi 7 `mld-interfaces` / `mld-link-addresses` | 7.13 + 802.11be hardware | no | One link shown instead of each radio |
| `/certificate`, `/file`, `/ip/service` | 7.1 | no | No HTTPS auto-provisioning |
| `container` package (deployment) | 7.4 | no | Run on a Docker host instead |

## Sources

- [REST API — MikroTik Documentation](https://help.mikrotik.com/docs/spaces/ROS/pages/47579162/REST+API)
- [v7.1beta4 release announcement](https://forum.mikrotik.com/viewtopic.php?t=172274) — REST API introduced
- [WiFi — MikroTik Documentation](https://help.mikrotik.com/docs/spaces/ROS/pages/224559120/WiFi) — the `wifi` menu, renamed from `wifiwave2` in 7.13
- [Container — MikroTik Documentation](https://help.mikrotik.com/docs/spaces/ROS/pages/84901929/Container) — container support from 7.4beta

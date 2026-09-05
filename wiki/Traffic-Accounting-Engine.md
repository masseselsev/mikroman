# 📊 Traffic Accounting Engine

MikroMan features a high-precision, non-intrusive traffic accounting engine designed specifically for MikroTik RouterOS gateways.

---

## 🔍 Why Firewall Mangle Counters?

On RouterOS 7.x, byte counters in Simple Queues can silently freeze at zero while high-throughput traffic flows. Simple Queues are therefore dedicated exclusively to **bandwidth shaping**.

For **traffic accounting**, MikroMan provisions dedicated firewall mangle counter rules:
```routeros
/ip/firewall/mangle
add chain=forward action=passthrough src-address=192.168.88.100 comment="mikroman:acct:dev_1:up"
add chain=forward action=passthrough dst-address=192.168.88.100 comment="mikroman:acct:dev_1:down"
```

### Key Properties:
1. `action=passthrough` only increments packet and byte counters and immediately continues chain evaluation. It does not drop, alter, or route traffic.
2. Independent rules for upload (`src-address`) and download (`dst-address`).
3. Tagged with unambiguous comments (`mikroman:acct:dev_{id}:{up|down}`) for exact reconciliation.

---

## 🔄 Delta Accumulation Against Persisted Baselines

Accounting operates on **deltas against a persisted baseline** rather than absolute counters:
1. Each polling tick reads the cumulative `bytes` from the mangle rules.
2. The delta is calculated: $\Delta = \text{current\_bytes} - \text{baseline\_bytes}$.
3. $\Delta$ is added to the database rollups, and the persisted baseline advances to $\text{current\_bytes}$.
4. If a polling read fails (e.g. network timeout), the baseline is **not** advanced. The subsequent successful read captures the entire elapsed gap via standard differencing.

---

## ⚡ Handling Outages vs Router Reboots

### 1. Network Outages
- When connection to the router is lost, the router hardware continues counting packets uninterrupted.
- Upon reconnection, the first successful read computes the difference against the old pre-outage baseline.
- If the gap crosses local midnight, the volume is **apportioned across the spanned calendar dates** based on elapsed clock time.

### 2. Hardware Reboots
- RouterOS reset all byte counters to zero on reboot.
- A naive differencing engine would misinterpret a post-reboot counter of `5 MB` against a pre-reboot baseline of `100 MB` as a counter rollback or negative traffic.
- MikroMan monitors `/system/resource` `uptime`. A decrease in uptime is treated as an explicit hardware reboot:
  $$\text{Delta} = \text{current\_bytes}$$
  The pre-reboot baseline is cleared and traffic accumulated since the reboot is credited in full.

---

## 📅 ISP Billing Cycle Calculations

Providers often bill on non-calendar monthly cycles (e.g. starting on the 15th at 08:00):
- **Anchor Day**: Configurable day of the month (1–31).
- **Anchor Time**: Optional router-local time (e.g. `08:00`).
- **Boundary Slicing**: When an anchor time is specified, the cycle-start day is sliced at the exact reset minute using sampled WAN interface metrics (`interface_metrics`), ensuring byte-accurate quota tracking.

---

## 🛠️ Reconciling Historical LAN-to-LAN Over-Counts

In networks with heavy inter-VLAN routing, traffic passing between two local subnets could be captured twice (once leaving subnet A, once entering subnet B).
- **Ground Truth**: The physical WAN interface counter never double-counts.
- **Reconciliation Endpoint**: `POST /api/v1/analytics/history/reconcile-overcount`
  - Identifies days where summed device volume exceeds WAN throughput minus router self-traffic.
  - Proportionally scales down device rollups for affected dates to match the physical WAN reality.
  - Automatically rebuilds per-user rollups.
  - Defaults to dry-run reporting; writes changes only when invoked with `?apply=true`.

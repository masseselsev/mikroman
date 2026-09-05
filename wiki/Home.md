# 📖 MikroMan Documentation Wiki

Welcome to the **MikroMan Technical Documentation Wiki**. This repository contains in-depth engineering documentation, architecture guides, operational protocols, and hardware compatibility specifications for MikroMan.

---

## 🧭 Navigation Index

### 1. [System Architecture & Design](Architecture-and-Design.md)
Overview of system components, backend structure (FastAPI, Pydantic, SQLAlchemy), frontend architecture (React, Vite, Tailwind CSS), database schema, and asynchronous RouterOS REST transport.

### 2. [Traffic Accounting Engine](Traffic-Accounting-Engine.md)
Detailed breakdown of the firewall mangle passthrough accounting engine, counter differencing against persistent baselines, outage tolerance, reboot detection, ISP billing cycle math, and historical over-count reconciliation.

### 3. [Lockout Prevention & Write Guards](Lockout-Prevention-Write-Guards.md)
Architecture of pure write guard invariants (`guards.py`), immune targets, foreign resource protection, queue hierarchy validation, and error handling.

### 4. [Multi-Router Management & Lifecycle](Multi-Router-Management.md)
Multi-tenant router environment isolation, hardware swap lifecycle (`keep` vs `reset_hardware`), archive vs purge semantics, and automated TLS/SSL provisioning.

### 5. [Backups, Config Drift & Visual Diff](Backups-and-Config-Drift.md)
Dual-pair exports (`.rsc` plain-text and encrypted `.backup` archives), volatile header normalization for zero-false-drift fingerprinting, unified diff engine, and flash storage safety invariants.

### 6. [Firmware & Update Intelligence](Firmware-and-Update-Intelligence.md)
RouterOS package update tracking across channels, RouterBOOT bootloader staging, streaming upstream changelog caching, pre-upgrade disaster-recovery backups, and reboot reconnection state machines.

### 7. [Live Connections & Router Log Stream](Live-Connections-and-Router-Logs.md)
Live connection tracking via `/ip/firewall/connection`, offline GeoIP resolution, terminal log viewer, regex event classifier, and RouterOS logging topic rules.

### 8. [Deployment, Storage & Container Mode](Deployment-and-Container-Mode.md)
Docker Compose installation, running inside RouterOS 7.4+ container mode on external USB storage, SQLite WAL mode, and online hot backups.

---

## 🎯 Architectural Philosophy

MikroMan is built around three core principles:
1. **Safety First**: Non-destructive operations, strict write guards, pre-upgrade backups, and zero-lockout guarantees.
2. **Deterministic Data**: Accurate packet accounting using firewall mangle counters rather than unreliable queue counters, persisted baselines, and timestamp normalization.
3. **Router-Friendly Resource Usage**: Single pooled keep-alive TLS connections, circuit breakers for unreachable targets, and bounded memory streams.

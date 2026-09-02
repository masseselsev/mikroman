"""Router-wide facts: hardware, health, clock, addresses, interface counters.

The read-only picture of the box itself, as opposed to what its clients are
doing. ``get_routerboard`` is cached for the life of the connection because
hardware identity cannot change while the router is up, and it is asked for on
every telemetry frame.
"""
import logging
from typing import Any, Dict, List, Optional

from backend.app.schemas.routeros import (
    RouterBoardInfo,
    RouterSystemHealth,
    RouterSystemResource,
)
from backend.app.services.routeros.parsing import parse_gmt_offset_minutes

logger = logging.getLogger("mikroman.routeros")


class SystemMixin:
    """`/system/*`, `/ip/address` and interface monitoring for :class:`RouterOSClient`."""

    async def get_system_resource(self) -> RouterSystemResource:
        """Fetch /system/resource metrics."""
        async with self._get_client() as client:
            resp = await client.get("/system/resource")
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                data = data[0]

            freq_raw = data.get("cpu-frequency") or data.get("cpu_frequency")
            return RouterSystemResource(
                board_name=data.get("board-name") or data.get("board_name"),
                model=data.get("platform"),
                version=data.get("version"),
                cpu_load=int(data.get("cpu-load") or data.get("cpu_load") or 0),
                free_memory=int(data.get("free-memory") or data.get("free_memory") or 0),
                total_memory=int(data.get("total-memory") or data.get("total_memory") or 0),
                uptime=data.get("uptime"),
                cpu=data.get("cpu") or None,
                cpu_count=int(data.get("cpu-count") or data.get("cpu_count") or 1),
                cpu_frequency=int(freq_raw) if freq_raw else None,
                architecture_name=data.get("architecture-name") or data.get("architecture_name")
            )

    async def get_routerboard(self, *, refresh: bool = False) -> RouterBoardInfo:
        """Static hardware identity from `/system/routerboard`, cached per client.

        The SoC/platform name (`firmware_type`, e.g. "ipq5300") is the closest
        RouterOS gets to a CPU part number on MikroTik hardware; `/system/
        resource` only reports the instruction set there. Fetched once and
        reused - none of these fields change without a reboot, and a reboot
        drops the connection and rebuilds the client anyway.

        A CHR, x86 install or container has no RouterBOARD; this returns
        ``is_routerboard=False`` with empty fields and the caller falls back to
        ``RouterSystemResource.cpu``. Any failure is swallowed the same way, and
        the empty result is cached so a missing menu is not re-requested every
        telemetry tick.
        """
        if self._routerboard is not None and not refresh:
            return self._routerboard

        info = RouterBoardInfo()
        try:
            async with self._get_client() as client:
                resp = await client.get("/system/routerboard")
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list):
                    data = data[0] if data else {}

                def field(*names):
                    for n in names:
                        if data.get(n) not in (None, ""):
                            return data.get(n)
                    return None

                rb = str(field("routerboard") or "").lower()
                info = RouterBoardInfo(
                    is_routerboard=rb in ("true", "yes", "1"),
                    model=field("model"),
                    serial_number=field("serial-number", "serial_number"),
                    firmware_type=field("firmware-type", "firmware_type"),
                    current_firmware=field("current-firmware", "current_firmware"),
                    upgrade_firmware=field("upgrade-firmware", "upgrade_firmware"),
                    factory_firmware=field("factory-firmware", "factory_firmware"),
                )
        except Exception as e:
            logger.debug(f"Could not read /system/routerboard: {e}")

        self._routerboard = info
        return info

    async def get_system_health(self) -> RouterSystemHealth:
        """Fetch /system/health (temperature, voltage)."""
        async with self._get_client() as client:
            try:
                resp = await client.get("/system/health")
                resp.raise_for_status()
                data = resp.json()

                temp = None
                volt = None
                if isinstance(data, list):
                    for item in data:
                        name = item.get("name", "")
                        val = item.get("value")
                        if "temperature" in name.lower() and val is not None:
                            temp = float(val)
                        elif "voltage" in name.lower() and val is not None:
                            volt = float(val)
                elif isinstance(data, dict):
                    temp = float(data.get("temperature", 0)) if "temperature" in data else None
                    volt = float(data.get("voltage", 0)) if "voltage" in data else None

                return RouterSystemHealth(temperature=temp, voltage=volt)
            except Exception as e:
                logger.debug(f"RouterOS /system/health not available: {e}")
                return RouterSystemHealth(temperature=None, voltage=None)

    async def get_cloud_public_address(self) -> Optional[str]:
        """The router's own public IP as it knows it, from ``/ip/cloud``.

        RouterOS maintains this for its DDNS name and refreshes it on its own,
        so it is the router's real internet-facing address even when the box
        sits behind carrier-grade NAT. Returns ``None`` when the field is
        absent, ``0.0.0.0`` (DDNS never reached), or otherwise unusable - the
        caller then falls back to a container-side lookup.
        """
        async with self._get_client() as client:
            try:
                resp = await client.get("/ip/cloud")
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list):
                    data = data[0] if data else {}
                addr = str(data.get("public-address") or "").strip()
                return addr or None
            except Exception as e:
                logger.debug(f"RouterOS /ip/cloud not available: {e}")
                return None

    async def get_log(self, topics: Optional[str] = None, limit: int = 300) -> List[Dict[str, Any]]:
        """Recent entries from the RouterOS in-memory log, newest last.

        This is the only way to read a container's stdout over REST - there is
        no ``docker logs`` equivalent and ``/container/shell`` is an interactive
        console command. ``topics`` filters client-side rather than through a
        query parameter, because RouterOS stores topics as one comma-joined
        string and its REST filtering does not match inside it.
        """
        async with self._get_client() as client:
            resp = await client.get("/log")
            if resp.status_code != 200:
                return []
            raw = resp.json()
            entries = raw if isinstance(raw, list) else [raw]

        if topics:
            wanted = topics.lower()
            entries = [e for e in entries if wanted in (e.get("topics") or "").lower()]
        return entries[-limit:]

    async def get_logging_rules(self) -> List[Dict[str, Any]]:
        """Configured `/system/logging` actions."""
        async with self._get_client() as client:
            resp = await client.get("/system/logging")
            if resp.status_code != 200:
                return []
            raw = resp.json()
            return raw if isinstance(raw, list) else [raw]

    async def add_logging_rule(self, topics: str, action: str = "memory") -> str:
        """Start recording a topic RouterOS does not log by default.

        ``container`` is one of those: without a rule for it, a container's
        output is produced and discarded, so anything that reads results back
        out of the log has to make sure the rule exists first.
        """
        async with self._get_client() as client:
            resp = await client.put("/system/logging", json={"topics": topics, "action": action})
            resp.raise_for_status()
            body = resp.json()
            return body.get(".id", "") if isinstance(body, dict) else ""

    async def get_system_clock(self) -> Dict[str, Any]:
        """Router date, time and timezone.

        Returns the UTC offset in minutes so a client can advance the clock
        itself rather than polling for every tick.
        """
        async with self._get_client() as client:
            resp = await client.get("/system/clock")
            if resp.status_code != 200:
                return {}
            raw = resp.json()
            if not isinstance(raw, dict):
                return {}
            return {
                "date": raw.get("date"),
                "time": raw.get("time"),
                "timezone": raw.get("time-zone-name"),
                "gmt_offset_minutes": parse_gmt_offset_minutes(raw.get("gmt-offset")),
                "dst_active": str(raw.get("dst-active", "false")).lower() == "true",
            }

    async def get_ip_addresses(self) -> List[Dict[str, Any]]:
        """Fetch configured IP addresses (``/ip/address``) with their interfaces."""
        async with self._get_client() as client:
            resp = await client.get("/ip/address")
            if resp.status_code != 200:
                return []
            raw = resp.json()
            return raw if isinstance(raw, list) else [raw]

    async def monitor_interface_traffic(self, interface_names: List[str]) -> List[Dict[str, Any]]:
        """Fetch real-time traffic bandwidth rates using /interface/monitor-traffic."""
        if not interface_names:
            return []
        async with self._get_client() as client:
            try:
                ifaces_str = ",".join(interface_names)
                resp = await client.post("/interface/monitor-traffic", json={"interface": ifaces_str, "once": ""})
                if resp.status_code == 200:
                    data = resp.json()
                    if not isinstance(data, list):
                        data = [data]
                    return [
                        {
                            "name": item.get("name"),
                            "rx_bits_per_second": float(item.get("rx-bits-per-second", 0) or 0),
                            "tx_bits_per_second": float(item.get("tx-bits-per-second", 0) or 0),
                            "rx_packets_per_second": float(item.get("rx-packets-per-second", 0) or 0),
                            "tx_packets_per_second": float(item.get("tx-packets-per-second", 0) or 0),
                        }
                        for item in data if isinstance(item, dict) and "name" in item
                    ]
            except Exception as e:
                logger.debug(f"Failed to monitor interface traffic: {e}")
            return []

    async def reboot_system(self) -> bool:
        """Reboot MikroTik router."""
        async with self._get_client() as client:
            resp = await client.post("/system/reboot")
            return resp.status_code in [200, 204]

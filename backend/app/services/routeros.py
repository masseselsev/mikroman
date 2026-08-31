import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from backend.app.core.config import Settings
from backend.app.core.config import settings as global_settings
from backend.app.schemas.routeros import (
    ARPTableEntry,
    DHCPLeaseDTO,
    InterfaceDTO,
    RouterBoardInfo,
    RouterSystemHealth,
    RouterSystemResource,
    WiFiLinkDTO,
    WiFiRegistrationDTO,
)
from backend.app.schemas.traffic import SimpleQueueItem

logger = logging.getLogger("mikroman.routeros")


def parse_signal_list(raw: Optional[Any]) -> List[int]:
    """Parse a RouterOS signal field into dBm values.

    A single-link association reports one value ("-62"). A multi-link (WiFi 7
    MLO) association may report one value per link, comma separated.
    """
    if raw is None:
        return []
    values = []
    for part in str(raw).split(","):
        token = part.strip()
        if token and token.lstrip("-").isdigit():
            values.append(int(token))
    return values


def parse_gmt_offset_minutes(raw: Optional[str]) -> Optional[int]:
    """Parse a RouterOS ``gmt-offset`` into minutes east of UTC.

    Accepts the ``+05:00`` form, a bare ``+05``, and the raw-seconds form some
    RouterOS versions report. Returns None when the value cannot be understood,
    so the dashboard shows no router clock rather than a wrong one.
    """
    if raw is None:
        return None
    token = str(raw).strip()
    if not token:
        return None

    # Raw seconds, e.g. "18000" or "-10800".
    if token.lstrip("+-").isdigit() and ":" not in token:
        value = int(token)
        # Values small enough to be hours are treated as hours, not seconds.
        return value // 60 if abs(value) > 60 else value * 60

    sign = -1 if token.startswith("-") else 1
    body = token.lstrip("+-")
    parts = body.split(":")
    try:
        hours = int(parts[0])
        minutes = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return None
    return sign * (hours * 60 + minutes)


def parse_uptime_seconds(raw: Optional[str]) -> Optional[int]:
    """Parse a RouterOS uptime string into seconds.

    RouterOS reports uptime as a compact run of unit-suffixed parts, e.g.
    ``"38m35s"``, ``"1d3h58m3s"``, ``"6w2d5h"``. A bare integer (seconds) is
    also accepted. Returns None when the value cannot be understood.

    Used to detect a reboot: if uptime has gone *backwards* between two polls
    the router restarted, and every byte counter on it reset to zero at that
    moment - which the traffic accounting has to know so it credits the bytes
    since the reboot rather than a nonsensical delta against a stale baseline.
    """
    if raw is None:
        return None
    token = str(raw).strip().lower()
    if not token:
        return None
    if token.isdigit():
        return int(token)

    units = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}
    total = 0
    number = ""
    seen = False
    for ch in token:
        if ch.isdigit():
            number += ch
        elif ch in units and number:
            total += int(number) * units[ch]
            number = ""
            seen = True
        else:
            return None
    if number:  # trailing digits with no unit
        return None
    return total if seen else None


def build_wifi_links(
    interface: str,
    band: Optional[str],
    signals: List[int],
    mld_interfaces: Optional[str],
    mld_link_addresses: Optional[str],
) -> List[WiFiLinkDTO]:
    """Expand a registration entry into its individual radio links.

    RouterOS reports a WiFi 7 multi-link client as one entry on the ``mld*``
    interface, carrying parallel comma-separated lists of the member radios
    (``mld-interfaces``) and the per-link MAC addresses (``mld-link-addresses``).
    A conventional single-link client has neither, and yields one link.

    When the router reports fewer signal readings than links, the readings are
    assigned in order and the remaining links report no signal rather than
    repeating a value that was not measured for them.
    """
    members = [p.strip() for p in (mld_interfaces or "").split(",") if p.strip()]
    addresses = [p.strip().upper() for p in (mld_link_addresses or "").split(",") if p.strip()]

    if not members:
        return [WiFiLinkDTO(
            interface=interface,
            mac_address=addresses[0] if addresses else None,
            signal_strength=signals[0] if signals else None,
            band=band,
        )]

    links = []
    for index, member in enumerate(members):
        links.append(WiFiLinkDTO(
            interface=member,
            mac_address=addresses[index] if index < len(addresses) else None,
            signal_strength=signals[index] if index < len(signals) else None,
            band=band,
        ))
    return links


class RouterUnreachableError(ConnectionError):
    """Raised immediately while a router is known to be unreachable.

    A subclass of ConnectionError so that the many call sites which already
    tolerate a connection failure keep behaving exactly as they did.
    """


# How long a failed connection suppresses further attempts. Long enough that a
# dashboard polling every few seconds makes one real attempt rather than dozens,
# short enough that a router coming back is picked up almost immediately.
UNREACHABLE_COOLDOWN_SECONDS = 15.0


class _CircuitBreakerTransport(httpx.AsyncHTTPTransport):
    """Transport that reports connection failures back to its client.

    The bookkeeping lives here rather than around each call because the client
    exposes some forty request methods and many of them catch their own
    exceptions internally - a breaker wrapped around the caller would simply
    never see those failures. Every request passes through the transport, so
    this is the one place that sees them all.
    """

    def __init__(self, owner: "RouterOSClient", **kwargs):
        super().__init__(**kwargs)
        self._owner = owner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            response = await super().handle_async_request(request)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
            # Only failures to *reach* the host open the circuit. A 401, a 500 or
            # a slow read all prove the router is there and answering.
            self._owner._note_unreachable(e)
            raise
        self._owner._note_reachable()
        return response


class RouterOSClient:
    """Async HTTP client for the MikroTik RouterOS REST API (7.1+).

    See backend/app/services/routeros_compat.py for the menus this depends on
    and the release each was introduced in.
    """

    def __init__(
        self,
        config: Optional[Settings] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        use_ssl: Optional[bool] = None,
        ssl_verify: Optional[bool] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: Optional[float] = None
    ):
        self.config = config or global_settings
        self.host = host if host is not None else self.config.ROUTEROS_HOST
        self.port = port if port is not None else self.config.ROUTEROS_PORT
        self.use_ssl = use_ssl if use_ssl is not None else self.config.ROUTEROS_USE_SSL
        self.ssl_verify = ssl_verify if ssl_verify is not None else (self.config.ROUTEROS_SSL_VERIFY if self.use_ssl else False)
        self.username = username if username is not None else self.config.ROUTEROS_USER
        self.password = password if password is not None else self.config.ROUTEROS_PASSWORD
        timeout_val = timeout if timeout is not None else self.config.ROUTEROS_TIMEOUT_SECONDS

        protocol = "https" if self.use_ssl else "http"
        self.base_url = f"{protocol}://{self.host}:{self.port}/rest"
        self.auth = (self.username, self.password)
        self.verify_ssl = self.ssl_verify if self.use_ssl else False
        self.timeout = httpx.Timeout(timeout_val)
        self._client: Optional[httpx.AsyncClient] = None
        # Monotonic deadline before which the router is treated as unreachable
        # without trying. Zero means the circuit is closed.
        self._unreachable_until: float = 0.0
        # `/system/routerboard` is static between reboots (model, serial, SoC),
        # so it is fetched once per client and reused. The client itself is
        # cached per router and retired when its settings change, so this never
        # goes stale in a way that matters.
        self._routerboard: Optional[RouterBoardInfo] = None

    def _note_unreachable(self, error: Exception) -> None:
        """Open the circuit after a failure to reach the router."""
        was_open = self.is_unreachable
        self._unreachable_until = time.monotonic() + UNREACHABLE_COOLDOWN_SECONDS
        if not was_open:
            logger.warning(
                f"RouterOS at {self.host}:{self.port} is unreachable ({type(error).__name__}); "
                f"suppressing further attempts for {UNREACHABLE_COOLDOWN_SECONDS:.0f}s"
            )

    def _note_reachable(self) -> None:
        """Close the circuit: the router answered."""
        if self._unreachable_until:
            logger.info(f"RouterOS at {self.host}:{self.port} is reachable again")
        self._unreachable_until = 0.0

    @property
    def is_unreachable(self) -> bool:
        return self._unreachable_until > time.monotonic()

    def _build_client(self) -> httpx.AsyncClient:
        limits = httpx.Limits(max_keepalive_connections=4, max_connections=8, keepalive_expiry=60.0)
        return httpx.AsyncClient(
            base_url=self.base_url,
            auth=self.auth,
            timeout=self.timeout,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            # Keep-alive connections are the whole point: without them every
            # request repeats the TLS handshake, which is what made the polling
            # loop dominate router CPU.
            limits=limits,
            transport=_CircuitBreakerTransport(
                self, verify=self.verify_ssl, limits=limits, retries=0
            ),
        )

    @asynccontextmanager
    async def _get_client(self) -> AsyncIterator[httpx.AsyncClient]:
        """Yield the pooled HTTP client, creating it on first use.

        Fails fast while the router is known to be unreachable. Without this,
        every endpoint that touches the router waited out the full connect
        timeout on every request: with the router off the network, ``/routers``,
        ``/users``, ``/system/status`` and ``/system/interfaces`` each took
        ~4.9s, so the dashboard sat blank for five seconds on every load and
        every poll tick. One attempt per cooldown is enough to notice the router
        returning; the rest are pointless waiting.

        Deliberately does not close the client on exit: callers use
        ``async with self._get_client() as client`` for every request, and
        closing it there would discard the connection pool - and with it the
        keep-alive that avoids a TLS handshake per request.
        """
        if self.is_unreachable:
            raise RouterUnreachableError(
                f"RouterOS at {self.host}:{self.port} was unreachable moments ago; "
                f"not retrying for another "
                f"{self._unreachable_until - time.monotonic():.0f}s"
            )
        if self._client is None or self._client.is_closed:
            self._client = self._build_client()
        yield self._client

    async def aclose(self) -> None:
        """Release the pooled connections."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None
        self._routerboard = None

    async def check_ssl_status(self) -> Dict[str, Any]:
        """Check /ip/service for www-ssl and /certificate status."""
        async with self._get_client() as client:
            try:
                services_resp = await client.get("/ip/service")
                services = services_resp.json() if services_resp.status_code == 200 else []
                www_ssl = next((s for s in services if isinstance(s, dict) and s.get("name") == "www-ssl"), None)

                certs_resp = await client.get("/certificate")
                certs = certs_resp.json() if certs_resp.status_code == 200 else []

                return {
                    "www_ssl_enabled": not www_ssl.get("disabled", True) if www_ssl else False,
                    "www_ssl_port": int(www_ssl.get("port", 443)) if www_ssl else 443,
                    "www_ssl_certificate": www_ssl.get("certificate") if www_ssl else None,
                    "available_certificates": [c.get("name") for c in certs if isinstance(c, dict)]
                }
            except Exception as e:
                logger.warning(f"Failed to check SSL status: {e}")
                return {"error": str(e)}

    async def provision_ssl(self, common_name: str = "mikrotik.local", port: int = 443) -> Dict[str, Any]:
        """
        Automatically generate a self-signed TLS certificate on RouterOS and enable the www-ssl service.
        """
        async with self._get_client() as client:
            cert_name = "mikroman-ssl"
            try:
                # 1. Check existing certificates
                certs_resp = await client.get("/certificate")
                certs = certs_resp.json() if certs_resp.status_code == 200 else []
                has_cert = any(isinstance(c, dict) and c.get("name") == cert_name for c in certs)

                if not has_cert:
                    # Create certificate template on RouterOS
                    try:
                        add_resp = await client.post("/certificate/add", json={
                            "name": cert_name,
                            "common-name": common_name,
                            "days-valid": "3650",
                            "key-size": "2048"
                        })
                        if add_resp.status_code not in (200, 201):
                            await client.put(f"/certificate/{cert_name}", json={
                                "name": cert_name,
                                "common-name": common_name,
                                "days-valid": "3650",
                                "key-size": "2048"
                            })
                    except Exception as e:
                        logger.warning(f"Certificate add attempt: {e}")

                    # Sign the certificate
                    try:
                        await client.post("/certificate/sign", json={"number": cert_name})
                        await asyncio.sleep(1.0)
                    except Exception as e:
                        logger.info(f"Certificate sign command notice: {e}")

                # 2. Find and enable www-ssl service
                services_resp = await client.get("/ip/service")
                services = services_resp.json() if services_resp.status_code == 200 else []
                www_ssl = next((s for s in services if isinstance(s, dict) and s.get("name") == "www-ssl"), None)

                if www_ssl and ".id" in www_ssl:
                    patch_resp = await client.patch(f"/ip/service/{www_ssl['.id']}", json={
                        "disabled": False,
                        "certificate": cert_name,
                        "port": port
                    })
                    if patch_resp.status_code not in (200, 201):
                        await client.post("/ip/service/set", json={
                            "numbers": www_ssl[".id"],
                            "certificate": cert_name,
                            "disabled": False,
                            "port": port
                        })
                else:
                    await client.post("/ip/service/set", json={
                        "numbers": "www-ssl",
                        "certificate": cert_name,
                        "disabled": False,
                        "port": port
                    })

                return {
                    "success": True,
                    "certificate": cert_name,
                    "port": port,
                    "message": "SSL certificate generated and www-ssl service successfully enabled on MikroTik RouterOS"
                }
            except Exception as e:
                logger.error(f"Failed to provision SSL on RouterOS: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "message": f"Failed to configure SSL: {str(e)}"
                }

    async def list_certificates(self) -> List[Dict[str, Any]]:
        """Fetch all certificates available on the router."""
        async with self._get_client() as client:
            services_resp = await client.get("/ip/service")
            if services_resp.status_code in (401, 403):
                raise PermissionError("Authentication failed (401 Unauthorized). Please check your username and password.")
            services_resp.raise_for_status()
            services = services_resp.json() if services_resp.status_code == 200 else []
            www_ssl = next((s for s in services if isinstance(s, dict) and s.get("name") == "www-ssl"), None)
            active_cert = www_ssl.get("certificate") if www_ssl else None

            certs_resp = await client.get("/certificate")
            if certs_resp.status_code in (401, 403):
                raise PermissionError("Authentication failed (401 Unauthorized). Please check your username and password.")
            certs_resp.raise_for_status()
            certs = certs_resp.json() if certs_resp.status_code == 200 else []

            result = []
            for c in certs:
                if isinstance(c, dict) and "name" in c:
                    result.append({
                        "name": c.get("name"),
                        "common_name": c.get("common-name"),
                        "fingerprint": c.get("fingerprint"),
                        "days_valid": str(c.get("days-valid", "")),
                        "invalid_after": str(c.get("invalid-after", "")),
                        "expired": c.get("expired", False),
                        "is_active_ssl": c.get("name") == active_cert
                    })
            return result

    async def bind_ssl_certificate(self, cert_name: str, port: int = 443) -> Dict[str, Any]:
        """Bind an existing certificate to the www-ssl service and enable it."""
        async with self._get_client() as client:
            try:
                services_resp = await client.get("/ip/service")
                services = services_resp.json() if services_resp.status_code == 200 else []
                www_ssl = next((s for s in services if isinstance(s, dict) and s.get("name") == "www-ssl"), None)

                if www_ssl and ".id" in www_ssl:
                    patch_resp = await client.patch(f"/ip/service/{www_ssl['.id']}", json={
                        "disabled": False,
                        "certificate": cert_name,
                        "port": port
                    })
                    if patch_resp.status_code not in (200, 201):
                        await client.post("/ip/service/set", json={
                            "numbers": www_ssl[".id"],
                            "certificate": cert_name,
                            "disabled": False,
                            "port": port
                        })
                else:
                    await client.post("/ip/service/set", json={
                        "numbers": "www-ssl",
                        "certificate": cert_name,
                        "disabled": False,
                        "port": port
                    })

                return {
                    "success": True,
                    "certificate": cert_name,
                    "port": port,
                    "message": f"Certificate '{cert_name}' bound to www-ssl on port {port}"
                }
            except Exception as e:
                logger.error(f"Failed to bind certificate: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "message": f"Failed to bind certificate: {str(e)}"
                }

    async def import_custom_certificate(
        self,
        cert_content: str,
        key_content: Optional[str] = None,
        cert_name: str = "custom-ssl",
        passphrase: Optional[str] = None,
        port: int = 443
    ) -> Dict[str, Any]:
        """
        Upload custom PEM certificate and optional private key to RouterOS,
        import them into /certificate, and activate for www-ssl.
        """
        async with self._get_client() as client:
            try:
                # 1. Upload certificate file
                cert_file = f"mikroman-{cert_name}.crt"
                try:
                    await client.post("/file", json={
                        "name": cert_file,
                        "contents": cert_content
                    })
                except Exception:
                    pass

                # 2. Upload key file if present
                key_file = None
                if key_content:
                    key_file = f"mikroman-{cert_name}.key"
                    try:
                        await client.post("/file", json={
                            "name": key_file,
                            "contents": key_content
                        })
                    except Exception:
                        pass

                # 3. Import certificate
                import_payload = {"file-name": cert_file}
                if passphrase:
                    import_payload["passphrase"] = passphrase
                try:
                    await client.post("/certificate/import", json=import_payload)
                except Exception as e:
                    logger.warning(f"Certificate import attempt: {e}")

                if key_file:
                    try:
                        key_import_payload = {"file-name": key_file}
                        if passphrase:
                            key_import_payload["passphrase"] = passphrase
                        await client.post("/certificate/import", json=key_import_payload)
                    except Exception as e:
                        logger.warning(f"Key import attempt: {e}")

                await asyncio.sleep(0.5)

                # 4. Bind to www-ssl
                bind_res = await self.bind_ssl_certificate(cert_name=cert_name, port=port)
                if not bind_res.get("success"):
                    # Fallback: check if the cert was imported under common-name or filename
                    certs = await self.list_certificates()
                    matching = next((c["name"] for c in certs if cert_name in c["name"] or cert_file in c["name"]), None)
                    if matching:
                        bind_res = await self.bind_ssl_certificate(cert_name=matching, port=port)

                return bind_res
            except Exception as e:
                logger.error(f"Failed to import custom certificate: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "message": f"Failed to import certificate: {str(e)}"
                }

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

    async def get_dhcp_leases(self) -> List[DHCPLeaseDTO]:
        """Fetch active DHCP leases."""
        async with self._get_client() as client:
            resp = await client.get("/ip/dhcp-server/lease")
            resp.raise_for_status()
            raw_leases = resp.json()
            if not isinstance(raw_leases, list):
                raw_leases = [raw_leases]

            results = []
            for item in raw_leases:
                if not item.get("mac-address") or not item.get("address"):
                    continue
                results.append(DHCPLeaseDTO(
                    id=item.get(".id"),
                    address=item.get("address"),
                    mac_address=item.get("mac-address").upper(),
                    host_name=item.get("host-name") or item.get("comment"),
                    server=item.get("server"),
                    status=item.get("status", "bound"),
                    comment=item.get("comment"),
                    expires_after=item.get("expires-after")
                ))
            return results

    async def get_arp_table(self) -> List[ARPTableEntry]:
        """Fetch ARP table entries."""
        async with self._get_client() as client:
            resp = await client.get("/ip/arp")
            resp.raise_for_status()
            raw_arp = resp.json()
            if not isinstance(raw_arp, list):
                raw_arp = [raw_arp]

            results = []
            for item in raw_arp:
                if not item.get("mac-address") or not item.get("address"):
                    continue
                results.append(ARPTableEntry(
                    id=item.get(".id"),
                    address=item.get("address"),
                    mac_address=item.get("mac-address").upper(),
                    interface=item.get("interface"),
                    complete=item.get("complete", "true") == "true" or item.get("complete") is True
                ))
            return results

    async def get_wifi_registrations(self) -> List[WiFiRegistrationDTO]:
        """Fetch connected WiFi clients (supports WifiWave2 / WiFi and legacy wireless)."""
        async with self._get_client() as client:
            endpoints = ["/interface/wifi/registration-table", "/interface/wireless/registration-table"]
            for ep in endpoints:
                try:
                    resp = await client.get(ep)
                    if resp.status_code == 200:
                        raw = resp.json()
                        if not isinstance(raw, list):
                            raw = [raw]
                        results = []
                        for item in raw:
                            mac = item.get("mac-address") or item.get("mac")
                            if not mac:
                                continue
                            signals = parse_signal_list(item.get("signal-strength") or item.get("signal"))
                            iface = item.get("interface", "wifi")
                            band = item.get("band")
                            results.append(WiFiRegistrationDTO(
                                mac_address=mac.upper(),
                                interface=iface,
                                ssid=item.get("ssid"),
                                signal_strength=signals[0] if signals else None,
                                tx_rate=str(item.get("tx-rate", "")),
                                rx_rate=str(item.get("rx-rate", "")),
                                uptime=item.get("uptime"),
                                band=band,
                                links=build_wifi_links(
                                    interface=iface,
                                    band=band,
                                    signals=signals,
                                    mld_interfaces=item.get("mld-interfaces"),
                                    mld_link_addresses=item.get("mld-link-addresses"),
                                )
                            ))
                        return results
                except Exception:
                    continue
            return []

    async def get_interfaces(self) -> List[InterfaceDTO]:
        """Fetch network interfaces."""
        async with self._get_client() as client:
            resp = await client.get("/interface")
            resp.raise_for_status()
            raw = resp.json()
            if not isinstance(raw, list):
                raw = [raw]

            results = []
            for item in raw:
                results.append(InterfaceDTO(
                    id=item.get(".id"),
                    name=item.get("name", "unknown"),
                    type=item.get("type"),
                    running=item.get("running", "true") == "true" or item.get("running") is True,
                    disabled=item.get("disabled", "false") == "true" or item.get("disabled") is True,
                    rx_byte=int(item.get("rx-byte", 0)),
                    tx_byte=int(item.get("tx-byte", 0)),
                    rx_rate=int(item.get("rx-bits-per-second", 0) or item.get("rx-rate", 0)),
                    tx_rate=int(item.get("tx-bits-per-second", 0) or item.get("tx-rate", 0)),
                    rx_error=int(item.get("rx-error", 0) or 0),
                    tx_error=int(item.get("tx-error", 0) or 0),
                    rx_drop=int(item.get("rx-drop", 0) or 0),
                    tx_drop=int(item.get("tx-drop", 0) or 0),
                    mac_address=item.get("mac-address"),
                    mtu=str(item.get("mtu")) if item.get("mtu") is not None else None
                ))
            return results

    # --- Simple Queue Operations ---

    async def get_simple_queues(self) -> List[SimpleQueueItem]:
        """Fetch all simple queues."""
        async with self._get_client() as client:
            resp = await client.get("/queue/simple")
            resp.raise_for_status()
            raw = resp.json()
            if not isinstance(raw, list):
                raw = [raw]

            results = []
            for item in raw:
                results.append(SimpleQueueItem(
                    id=item.get(".id"),
                    name=item.get("name", ""),
                    target=item.get("target", ""),
                    max_limit=item.get("max-limit", "0/0"),
                    rate=item.get("rate", "0/0"),
                    bytes=item.get("bytes", "0/0"),
                    comment=item.get("comment"),
                    disabled=item.get("disabled", "false") == "true" or item.get("disabled") is True,
                    parent=item.get("parent")
                ))
            return results

    async def create_simple_queue(
        self,
        name: str,
        target: str,
        max_limit: str = "0/0",
        comment: Optional[str] = None,
        parent: Optional[str] = None
    ) -> str:
        """Create a new Simple Queue (supporting hierarchical parent queue trees)."""
        async with self._get_client() as client:
            payload = {
                "name": name,
                "target": target,
                "max-limit": max_limit,
                "comment": comment or "mikroman:managed"
            }
            if parent:
                payload["parent"] = parent
            resp = await client.put("/queue/simple", json=payload)
            resp.raise_for_status()
            res_data = resp.json()
            return res_data.get(".id", "")

    async def update_simple_queue(
        self,
        queue_id: str,
        name: Optional[str] = None,
        max_limit: Optional[str] = None,
        target: Optional[str] = None,
        disabled: Optional[bool] = None,
        comment: Optional[str] = None,
        parent: Optional[str] = None
    ) -> None:
        """Update an existing Simple Queue."""
        async with self._get_client() as client:
            payload = {}
            if name is not None:
                payload["name"] = name
            if max_limit is not None:
                payload["max-limit"] = max_limit
            if target is not None:
                payload["target"] = target
            if disabled is not None:
                payload["disabled"] = "true" if disabled else "false"
            if comment is not None:
                payload["comment"] = comment
            if parent is not None:
                payload["parent"] = parent

            resp = await client.patch(f"/queue/simple/{queue_id}", json=payload)
            resp.raise_for_status()

    async def delete_simple_queue(self, queue_id: str) -> None:
        """Delete a Simple Queue."""
        async with self._get_client() as client:
            resp = await client.delete(f"/queue/simple/{queue_id}")
            resp.raise_for_status()

    # --- Firewall Address List Operations (Pause / Block) ---

    async def get_address_list(self, list_name: str = "mikroman_blocked") -> List[Dict[str, Any]]:
        """Fetch entries from a firewall address-list."""
        async with self._get_client() as client:
            resp = await client.get("/ip/firewall/address-list")
            resp.raise_for_status()
            raw = resp.json()
            if not isinstance(raw, list):
                raw = [raw]
            return [item for item in raw if item.get("list") == list_name]

    async def add_to_address_list(self, address: str, list_name: str = "mikroman_blocked", comment: str = "mikroman:paused") -> str:
        """Add IP to firewall address-list."""
        async with self._get_client() as client:
            payload = {
                "address": address,
                "list": list_name,
                "comment": comment
            }
            resp = await client.put("/ip/firewall/address-list", json=payload)
            resp.raise_for_status()
            return resp.json().get(".id", "")

    async def remove_from_address_list(self, entry_id: str) -> None:
        """Remove IP entry from address-list."""
        async with self._get_client() as client:
            resp = await client.delete(f"/ip/firewall/address-list/{entry_id}")
            resp.raise_for_status()

    async def get_firewall_filter_rules(self) -> List[Dict[str, Any]]:
        """Fetch firewall filter rules from RouterOS."""
        async with self._get_client() as client:
            resp = await client.get("/ip/firewall/filter")
            if resp.status_code != 200:
                return []
            raw = resp.json()
            return raw if isinstance(raw, list) else [raw]

    async def update_firewall_filter_rule(self, rule_id: str, payload: Dict[str, Any]) -> bool:
        """Update a firewall filter rule on RouterOS."""
        async with self._get_client() as client:
            resp = await client.patch(f"/ip/firewall/filter/{rule_id}", json=payload)
            return resp.status_code in (200, 201, 204)

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

    # --- Firewall Mangle Operations (per-device traffic accounting) ---
    #
    # Simple Queue byte counters proved unusable for accounting on RouterOS 7.25
    # (they stay frozen at zero even while traffic flows), so per-device volume is
    # measured with `action=passthrough` mangle rules instead. Passthrough only
    # increments a counter and hands the packet on - it never alters traffic.

    async def get_mangle_rules(self) -> List[Dict[str, Any]]:
        """Fetch all firewall mangle rules from RouterOS."""
        async with self._get_client() as client:
            resp = await client.get("/ip/firewall/mangle")
            if resp.status_code != 200:
                return []
            raw = resp.json()
            return raw if isinstance(raw, list) else [raw]

    async def create_mangle_rule(self, payload: Dict[str, Any]) -> str:
        """Create a firewall mangle rule and return its RouterOS id."""
        async with self._get_client() as client:
            resp = await client.put("/ip/firewall/mangle", json=payload)
            resp.raise_for_status()
            return resp.json().get(".id", "")

    async def update_mangle_rule(self, rule_id: str, payload: Dict[str, Any]) -> bool:
        """Update an existing firewall mangle rule."""
        async with self._get_client() as client:
            resp = await client.patch(f"/ip/firewall/mangle/{rule_id}", json=payload)
            return resp.status_code in (200, 201, 204)

    async def delete_mangle_rule(self, rule_id: str) -> None:
        """Delete a firewall mangle rule."""
        async with self._get_client() as client:
            resp = await client.delete(f"/ip/firewall/mangle/{rule_id}")
            resp.raise_for_status()

    # --- Containers -----------------------------------------------------------
    # RouterOS ships container support as a separate, opt-in package that is not
    # present on a default install and cannot be enabled without a reboot. Every
    # method here tolerates the package being absent: the REST endpoints simply
    # 404 / error, and the caller decides how to present that.

    async def get_packages(self) -> List[Dict[str, Any]]:
        """Installed RouterOS packages, each with ``name``/``version``/``disabled``."""
        async with self._get_client() as client:
            resp = await client.get("/system/package")
            if resp.status_code != 200:
                return []
            raw = resp.json()
            return raw if isinstance(raw, list) else [raw]

    async def get_containers(self) -> List[Dict[str, Any]]:
        """Every container known to RouterOS, or ``[]`` if the package is absent."""
        async with self._get_client() as client:
            resp = await client.get("/container")
            if resp.status_code != 200:
                return []
            raw = resp.json()
            return raw if isinstance(raw, list) else [raw]

    async def get_container_config(self) -> Dict[str, Any]:
        """Global container config (``tmpdir``, ``registry-url``, ``layer-dir`` …)."""
        async with self._get_client() as client:
            resp = await client.get("/container/config")
            if resp.status_code != 200:
                return {}
            raw = resp.json()
            if isinstance(raw, list):
                return raw[0] if raw else {}
            return raw or {}

    async def get_container_mounts(self) -> List[Dict[str, Any]]:
        """Configured container mount points (``/container/mounts``)."""
        async with self._get_client() as client:
            resp = await client.get("/container/mounts")
            if resp.status_code != 200:
                return []
            raw = resp.json()
            return raw if isinstance(raw, list) else [raw]

    async def get_container_envs(self) -> List[Dict[str, Any]]:
        """Configured container environment variables (``/container/envs``)."""
        async with self._get_client() as client:
            resp = await client.get("/container/envs")
            if resp.status_code != 200:
                return []
            raw = resp.json()
            return raw if isinstance(raw, list) else [raw]

    async def container_command(self, action: str, container_id: str) -> bool:
        """Run ``start`` / ``stop`` / ``remove`` against one container by id."""
        if action not in {"start", "stop", "remove"}:
            raise ValueError(f"Unsupported container action: {action}")
        async with self._get_client() as client:
            resp = await client.post(f"/container/{action}", json={".id": container_id})
            return resp.status_code in (200, 201, 204)

    async def add_container(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create a container from a remote image (``/container/add``).

        ``payload`` is passed through to RouterOS - typically
        ``{"remote-image": "repo/name:tag", "interface": "veth1", ...}``.
        """
        async with self._get_client() as client:
            resp = await client.post("/container/add", json=payload)
            resp.raise_for_status()
            body = resp.json() if resp.content else {}
            return body if isinstance(body, dict) else {"result": body}

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

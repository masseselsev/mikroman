import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

from backend.app.core.config import Settings
from backend.app.core.config import settings as global_settings
from backend.app.schemas.routeros import (
    ARPTableEntry,
    DHCPLeaseDTO,
    InterfaceDTO,
    RouterSystemHealth,
    RouterSystemResource,
    WiFiRegistrationDTO,
)
from backend.app.schemas.traffic import SimpleQueueItem

logger = logging.getLogger("mikroman.routeros")


class RouterOSClient:
    """Async HTTP Client for MikroTik RouterOS 7.24+ REST API."""

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

    def _get_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            auth=self.auth,
            verify=self.verify_ssl,
            timeout=self.timeout,
            headers={"Content-Type": "application/json", "Accept": "application/json"}
        )

    async def aclose(self) -> None:
        """Cleanup handler."""
        pass

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

            return RouterSystemResource(
                board_name=data.get("board-name") or data.get("board_name"),
                model=data.get("platform"),
                version=data.get("version"),
                cpu_load=int(data.get("cpu-load") or data.get("cpu_load") or 0),
                free_memory=int(data.get("free-memory") or data.get("free_memory") or 0),
                total_memory=int(data.get("total-memory") or data.get("total_memory") or 0),
                uptime=data.get("uptime"),
                cpu_count=int(data.get("cpu-count") or data.get("cpu_count") or 1),
                cpu_frequency=int(data.get("cpu-frequency") or data.get("cpu_frequency") or 0) if data.get("cpu-frequency") or data.get("cpu_frequency") else None,
                architecture_name=data.get("architecture-name") or data.get("architecture_name")
            )

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
                            sig = item.get("signal-strength") or item.get("signal")
                            sig_int = int(sig) if sig is not None and str(sig).lstrip('-').isdigit() else None
                            results.append(WiFiRegistrationDTO(
                                mac_address=mac.upper(),
                                interface=item.get("interface", "wifi"),
                                ssid=item.get("ssid"),
                                signal_strength=sig_int,
                                tx_rate=str(item.get("tx-rate", "")),
                                rx_rate=str(item.get("rx-rate", "")),
                                uptime=item.get("uptime")
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
                    tx_rate=int(item.get("tx-bits-per-second", 0) or item.get("tx-rate", 0))
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
                    disabled=item.get("disabled", "false") == "true" or item.get("disabled") is True
                ))
            return results

    async def create_simple_queue(self, name: str, target: str, max_limit: str = "0/0", comment: Optional[str] = None) -> str:
        """Create a new Simple Queue."""
        async with self._get_client() as client:
            payload = {
                "name": name,
                "target": target,
                "max-limit": max_limit,
                "comment": comment or "mikroman:managed"
            }
            resp = await client.put("/queue/simple", json=payload)
            resp.raise_for_status()
            res_data = resp.json()
            return res_data.get(".id", "")

    async def update_simple_queue(self, queue_id: str, max_limit: Optional[str] = None, target: Optional[str] = None, disabled: Optional[bool] = None) -> None:
        """Update an existing Simple Queue."""
        async with self._get_client() as client:
            payload = {}
            if max_limit is not None:
                payload["max-limit"] = max_limit
            if target is not None:
                payload["target"] = target
            if disabled is not None:
                payload["disabled"] = "true" if disabled else "false"

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

    async def reboot_system(self) -> bool:
        """Reboot MikroTik router."""
        async with self._get_client() as client:
            resp = await client.post("/system/reboot")
            return resp.status_code in [200, 204]

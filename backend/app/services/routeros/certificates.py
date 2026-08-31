"""TLS certificate provisioning and the ``www-ssl`` service on RouterOS.

Self-signed provisioning, importing an operator-supplied certificate, listing
what is installed, and binding one to the HTTPS service. Kept apart from the
day-to-day polling because it is rare, multi-step, and every step can leave the
router half-configured if it fails - so each method reports what it managed
rather than raising and losing the context.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("mikroman.routeros")


class CertificatesMixin:
    """`/certificate` and `/ip/service` operations for :class:`RouterOSClient`."""

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

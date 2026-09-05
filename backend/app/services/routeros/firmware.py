import logging
import re
from typing import Any, Dict

logger = logging.getLogger("mikroman.routeros.firmware")


def _as_dict(data: Any) -> Dict[str, Any]:
    """Some singleton RouterOS menus answer with a one-item list rather than
    a bare object; the other mixins normalise the same way (see
    `SystemMixin.get_system_resource`)."""
    if isinstance(data, list):
        return data[0] if data else {}
    return data or {}


class FirmwareMixin:
    """RouterOS package update and RouterBOOT bootloader transport mixin."""

    async def get_package_update_status(self) -> Dict[str, Any]:
        """Fetch current package update status from /system/package/update."""
        async with self._get_client() as client:
            resp = await client.get("/system/package/update")
            resp.raise_for_status()
            data = _as_dict(resp.json())

        raw_installed = data.get("installed-version", "")
        # Strip parenthesized channel suffix, e.g. "7.15.2 (stable)" -> "7.15.2"
        installed_version = re.sub(r"\s*\(.*?\)", "", raw_installed).strip()
        latest_version = data.get("latest-version", "").strip() or None
        channel = data.get("channel", "stable").strip()
        status = data.get("status", "").strip()

        update_available = False
        if latest_version and latest_version != installed_version:
            update_available = True
        elif "new version" in status.lower():
            update_available = True

        return {
            "installed_version": installed_version,
            "latest_version": latest_version,
            "channel": channel,
            "status": status,
            "update_available": update_available,
        }

    async def check_for_package_updates(self) -> Dict[str, Any]:
        """Trigger an on-demand check against MikroTik update servers."""
        try:
            async with self._get_client() as client:
                resp = await client.post("/system/package/update/check-for-updates", json={})
                resp.raise_for_status()
        except Exception as e:
            logger.debug(f"check-for-updates call returned: {e}")
        return await self.get_package_update_status()

    async def set_package_update_channel(self, channel: str) -> Dict[str, Any]:
        """Switch update channel and refresh update check."""
        valid_channels = {"stable", "long-term", "testing", "development"}
        if channel not in valid_channels:
            raise ValueError(f"Invalid channel '{channel}'. Expected one of {valid_channels}")
        async with self._get_client() as client:
            resp = await client.post("/system/package/update/set", json={"channel": channel})
            resp.raise_for_status()
        return await self.check_for_package_updates()

    async def install_package_update(self) -> None:
        """Download packages and initiate reboot."""
        async with self._get_client() as client:
            resp = await client.post("/system/package/update/install", json={})
            resp.raise_for_status()

    async def get_routerboard_status(self) -> Dict[str, Any]:
        """Fetch RouterBOOT firmware information from /system/routerboard."""
        try:
            async with self._get_client() as client:
                resp = await client.get("/system/routerboard")
                resp.raise_for_status()
                data = _as_dict(resp.json())
        except Exception as e:
            logger.debug(f"Routerboard endpoint unavailable: {e}")
            return {
                "is_routerboard": False,
                "model": None,
                "serial_number": None,
                "current_firmware": None,
                "upgrade_firmware": None,
                "firmware_available": False,
            }

        is_rb = str(data.get("routerboard", "false")).lower() == "true"
        current_fw = data.get("current-firmware", "").strip() or None
        upgrade_fw = data.get("upgrade-firmware", "").strip() or None
        fw_available = bool(is_rb and upgrade_fw and current_fw and upgrade_fw != current_fw)

        return {
            "is_routerboard": is_rb,
            "model": data.get("model", "").strip() or None,
            "serial_number": data.get("serial-number", "").strip() or None,
            "current_firmware": current_fw,
            "upgrade_firmware": upgrade_fw,
            "firmware_available": fw_available,
        }

    async def upgrade_routerboard_firmware(self) -> None:
        """Trigger RouterBOOT bootloader flash upgrade."""
        async with self._get_client() as client:
            resp = await client.post("/system/routerboard/upgrade", json={})
            resp.raise_for_status()

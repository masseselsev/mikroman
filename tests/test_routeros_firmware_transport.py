"""Firmware/RouterBOOT transport: exercised against the real HTTP call shape.

An earlier version of this file mocked `client._get`/`client._post` directly
with `AsyncMock`. Python happily lets you assign an attribute like that to an
instance whose class defines no such method, so the tests passed while every
one of `FirmwareMixin`'s calls used `self._get(...)`/`self._post(...)` -
methods that do not exist anywhere on `RouterOSClient`. The mixin was
completely disconnected from the real transport (`self._get_client()`, an
`httpx.AsyncClient`), and only calling it against a live router - which none
of these tests did - raised `AttributeError: 'RouterOSClient' object has no
attribute '_get'`. These tests mock at the transport boundary instead, the
same way `test_routeros_backup_transport.py` does, so a mixin drifting away
from the real client API fails here first.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.routeros.client import RouterOSClient


@pytest.mark.asyncio
async def test_get_package_update_status():
    client = RouterOSClient(host="192.0.2.1", username="admin", password="pwd")

    with patch.object(client, "_get_client") as mock_get:
        mock_http = AsyncMock()
        mock_get.return_value.__aenter__.return_value = mock_http
        mock_http.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "installed-version": "7.15.2 (stable)",
                "latest-version": "7.16.1",
                "channel": "stable",
                "status": "New version is available",
            },
        )
        mock_http.get.return_value.raise_for_status = MagicMock()

        status = await client.get_package_update_status()

    assert status["installed_version"] == "7.15.2"
    assert status["latest_version"] == "7.16.1"
    assert status["channel"] == "stable"
    assert status["update_available"] is True
    mock_http.get.assert_called_once_with("/system/package/update")


@pytest.mark.asyncio
async def test_get_package_update_status_unwraps_a_singleton_list():
    """`/system/package/update` is a singleton menu; RouterOS answers those
    with either a bare object or a one-item list depending on version."""
    client = RouterOSClient(host="192.0.2.1", username="admin", password="pwd")

    with patch.object(client, "_get_client") as mock_get:
        mock_http = AsyncMock()
        mock_get.return_value.__aenter__.return_value = mock_http
        mock_http.get.return_value = MagicMock(
            status_code=200,
            json=lambda: [{
                "installed-version": "7.16.1",
                "latest-version": "7.16.1",
                "channel": "stable",
                "status": "System is already up to date",
            }],
        )
        mock_http.get.return_value.raise_for_status = MagicMock()

        status = await client.get_package_update_status()

    assert status["installed_version"] == "7.16.1"
    assert status["update_available"] is False


@pytest.mark.asyncio
async def test_get_routerboard_status():
    client = RouterOSClient(host="192.0.2.1", username="admin", password="pwd")

    with patch.object(client, "_get_client") as mock_get:
        mock_http = AsyncMock()
        mock_get.return_value.__aenter__.return_value = mock_http
        mock_http.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "routerboard": "true",
                "model": "RB5009UG+S+IN",
                "serial-number": "HF809ABC",
                "current-firmware": "7.15.2",
                "upgrade-firmware": "7.16.1",
                "firmware-type": "arm64",
            },
        )
        mock_http.get.return_value.raise_for_status = MagicMock()

        rb = await client.get_routerboard_status()

    assert rb["is_routerboard"] is True
    assert rb["model"] == "RB5009UG+S+IN"
    assert rb["current_firmware"] == "7.15.2"
    assert rb["upgrade_firmware"] == "7.16.1"
    assert rb["firmware_available"] is True


@pytest.mark.asyncio
async def test_get_routerboard_status_falls_back_on_a_transport_error():
    client = RouterOSClient(host="192.0.2.1", username="admin", password="pwd")

    with patch.object(client, "_get_client") as mock_get:
        mock_get.return_value.__aenter__.side_effect = ConnectionError("unreachable")

        rb = await client.get_routerboard_status()

    assert rb == {
        "is_routerboard": False,
        "model": None,
        "serial_number": None,
        "current_firmware": None,
        "upgrade_firmware": None,
        "firmware_available": False,
    }


@pytest.mark.asyncio
async def test_set_channel_and_install():
    client = RouterOSClient(host="192.0.2.1", username="admin", password="pwd")

    with patch.object(client, "_get_client") as mock_get:
        mock_http = AsyncMock()
        mock_get.return_value.__aenter__.return_value = mock_http
        mock_http.post.return_value = MagicMock(status_code=200)
        mock_http.post.return_value.raise_for_status = MagicMock()
        mock_http.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "installed-version": "7.15.2",
                "latest-version": "7.15.2",
                "channel": "long-term",
                "status": "System is already up to date",
            },
        )
        mock_http.get.return_value.raise_for_status = MagicMock()

        res = await client.set_package_update_channel("long-term")
        assert res["channel"] == "long-term"
        assert any(
            c.args[0] == "/system/package/update/set" for c in mock_http.post.call_args_list
        )

        await client.install_package_update()
        assert any(
            c.args[0] == "/system/package/update/install" for c in mock_http.post.call_args_list
        )

        await client.upgrade_routerboard_firmware()
        assert any(
            c.args[0] == "/system/routerboard/upgrade" for c in mock_http.post.call_args_list
        )


@pytest.mark.asyncio
async def test_set_package_update_channel_rejects_an_unknown_channel():
    client = RouterOSClient(host="192.0.2.1", username="admin", password="pwd")
    with pytest.raises(ValueError):
        await client.set_package_update_channel("nightly")

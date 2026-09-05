from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.routeros.client import RouterOSClient


@pytest.mark.asyncio
async def test_sweep_temporary_files():
    client = RouterOSClient(host="192.168.88.1", username="admin", password="")

    mock_files = [
        {"name": "mikroman-backup-123.rsc", ".id": "*1"},
        {"name": "mikroman-backup-123.backup", ".id": "*2"},
        {"name": "user-file.txt", ".id": "*3"},
    ]

    with patch.object(client, "_get_client") as mock_get:
        mock_http = AsyncMock()
        mock_get.return_value.__aenter__.return_value = mock_http

        # GET /file returns list
        mock_http.get.return_value = MagicMock(status_code=200, json=lambda: mock_files)
        # DELETE /file/*
        mock_http.delete.return_value = MagicMock(status_code=200)

        swept = await client.sweep_temporary_files(prefix="mikroman-backup-")
        assert swept == 2
        assert mock_http.delete.call_count == 2


@pytest.mark.asyncio
async def test_export_config_flow():
    client = RouterOSClient(host="192.168.88.1", username="admin", password="")

    with patch.object(client, "_get_client") as mock_get, \
         patch.object(client, "_wait_for_file_settled", new_callable=AsyncMock) as mock_settle:
        mock_http = AsyncMock()
        mock_get.return_value.__aenter__.return_value = mock_http

        mock_http.post.side_effect = [
            MagicMock(status_code=200),  # POST /export
            MagicMock(status_code=200, json=lambda: [{"data": "/ip firewall"}]),  # /file/read chunk 1
            MagicMock(status_code=200, json=lambda: []),  # /file/read EOF
        ]

        content = await client.export_config(stem="test1")
        assert content == "/ip firewall"
        assert mock_settle.call_count == 1


@pytest.mark.asyncio
async def test_the_flash_sweep_asks_only_for_names_not_file_contents():
    """An unqualified GET /file returns each file's `contents`.

    On a router holding any binary that body does not decode as UTF-8 - a live
    hAP be3 answered "invalid continuation byte in position 16007" - and the
    whole sweep failed, so its temporary files stayed on flash exactly as the
    invariant was written to prevent.
    """
    client = RouterOSClient(host="192.168.88.1", username="admin", password="")

    with patch.object(client, "_get_client") as mock_get:
        mock_http = AsyncMock()
        mock_get.return_value.__aenter__.return_value = mock_http
        mock_http.get.return_value = MagicMock(
            status_code=200,
            json=lambda: [{"name": "mikroman-backup-1.rsc", ".id": "*1"}],
        )
        mock_http.delete.return_value = MagicMock(status_code=204)

        await client.sweep_temporary_files()

        _args, kwargs = mock_http.get.call_args
        assert kwargs["params"][".proplist"] == ".id,name"

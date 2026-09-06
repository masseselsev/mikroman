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


@pytest.mark.asyncio
async def test_settle_poll_asks_only_for_names_and_sizes():
    """The settle poll must not pull every file's contents down with the listing.

    `GET /file` without a `.proplist` includes each file's `contents`, so the
    response body carries the raw bytes of every binary on flash - including the
    `.backup` this very call is waiting for. The body then fails to decode as
    UTF-8, the exception is swallowed by the poll loop, and the wait runs to its
    deadline: "Timed out waiting for ... to settle on router flash" on every
    single backup, with the real cause never surfacing.
    """
    client = RouterOSClient(host="192.168.88.1", username="admin", password="")

    listing = [{"name": "mikroman-backup-1.rsc", "size": "42"}]
    with patch.object(client, "_get_client") as mock_get:
        mock_http = AsyncMock()
        mock_get.return_value.__aenter__.return_value = mock_http
        mock_http.get.return_value = MagicMock(status_code=200, json=lambda: listing)

        size = await client._wait_for_file_settled("mikroman-backup-1.rsc", timeout=5.0)

    assert size == 42
    assert mock_http.get.call_args.args[0] == "/file"
    proplist = mock_http.get.call_args.kwargs.get("params", {}).get(".proplist", "")
    assert "name" in proplist and "size" in proplist, (
        "the settle poll must request a proplist; without one RouterOS returns "
        "file contents and the response cannot be decoded"
    )
    assert "contents" not in proplist


@pytest.mark.asyncio
async def test_binary_backup_survives_a_body_that_is_not_utf8():
    """A binary backup's bytes arrive inside the JSON body and are not UTF-8.

    `response.json()` hands the raw body to `json.loads`, which insists on
    UTF-8, so a `.backup` containing byte 0x80 aborted the read with
    "'utf-8' codec can't decode byte 0x80 in position 15". The body has to be
    decoded as latin-1 - which maps bytes one-to-one - before it is parsed, the
    same encoding the chunk is re-encoded with afterwards.
    """
    client = RouterOSClient(host="192.168.88.1", username="admin", password="")

    payload = bytes([0x80, 0x81, 0xFE])
    body = b'[{"data":"' + payload + b'"}]'

    def _explode():
        raise UnicodeDecodeError("utf-8", payload, 0, 1, "invalid start byte")

    with patch.object(client, "_get_client") as mock_get, \
         patch.object(client, "_wait_for_file_settled", new_callable=AsyncMock):
        mock_http = AsyncMock()
        mock_get.return_value.__aenter__.return_value = mock_http
        mock_http.post.side_effect = [
            MagicMock(status_code=200),  # POST /system/backup/save
            MagicMock(status_code=200, content=body, json=_explode),  # /file/read
        ]

        data = await client.create_system_backup(stem="t", password="pw")

    assert data == payload

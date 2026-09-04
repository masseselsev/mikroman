from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base, Router, RouterBackup
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.services.router_manager import router_manager


@pytest.fixture
async def async_client():
    await router_manager.aclose()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, session_factory

    app.dependency_overrides.clear()
    await router_manager.aclose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_firmware_status(async_client):
    ac, session_factory = async_client

    async with session_factory() as session:
        router = Router(id=1, name="Test-Router", host="192.168.88.1", port=80)
        session.add(router)
        await session.commit()

    with patch("backend.app.services.router_manager.router_manager.get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.get_package_update_status.return_value = {
            "installed_version": "7.15.2",
            "latest_version": "7.16.1",
            "channel": "stable",
            "status": "New version is available",
            "update_available": True,
        }
        mock_client.get_routerboard_status.return_value = {
            "is_routerboard": True,
            "model": "RB5009",
            "serial_number": "12345",
            "current_firmware": "7.15.2",
            "upgrade_firmware": "7.16.1",
            "firmware_available": True,
        }
        mock_get_client.return_value = mock_client

        res = await ac.get("/api/v1/routers/1/firmware")
        assert res.status_code == 200
        data = res.json()
        assert data["router_name"] == "Test-Router"
        assert data["packages"]["installed_version"] == "7.15.2"
        assert data["packages"]["latest_version"] == "7.16.1"
        assert data["routerboard"]["firmware_available"] is True


@pytest.mark.asyncio
async def test_check_and_channel_endpoints(async_client):
    ac, session_factory = async_client

    async with session_factory() as session:
        router = Router(id=1, name="Test-Router", host="192.168.88.1", port=80)
        session.add(router)
        await session.commit()

    with patch("backend.app.services.router_manager.router_manager.get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.check_for_package_updates.return_value = {
            "installed_version": "7.15.2",
            "latest_version": "7.16.1",
            "channel": "stable",
            "status": "New version is available",
            "update_available": True,
        }
        mock_client.set_package_update_channel.return_value = {
            "installed_version": "7.15.2",
            "latest_version": "7.17beta2",
            "channel": "testing",
            "status": "New version is available",
            "update_available": True,
        }
        mock_client.get_routerboard_status.return_value = {
            "is_routerboard": True,
            "current_firmware": "7.15.2",
            "upgrade_firmware": "7.16.1",
            "firmware_available": True,
        }
        mock_get_client.return_value = mock_client

        # POST /check
        res_check = await ac.post("/api/v1/routers/1/firmware/check")
        assert res_check.status_code == 200
        assert res_check.json()["packages"]["channel"] == "stable"
        assert mock_client.check_for_package_updates.called

        # PUT /channel
        res_chan = await ac.put("/api/v1/routers/1/firmware/channel", json={"channel": "testing"})
        assert res_chan.status_code == 200
        assert res_chan.json()["packages"]["channel"] == "testing"
        mock_client.set_package_update_channel.assert_called_with("testing")


@pytest.mark.asyncio
async def test_changelog_endpoint(async_client):
    ac, session_factory = async_client

    async with session_factory() as session:
        router = Router(id=1, name="Test-Router", host="192.168.88.1", port=80)
        session.add(router)
        await session.commit()

    with patch("backend.app.api.v1.endpoints.firmware.changelog_service.get_notes", new_callable=AsyncMock) as mock_notes:
        mock_notes.return_value = "*) bridge - fix vlan"
        res = await ac.get("/api/v1/routers/1/firmware/changelog?version=7.16.1")
        assert res.status_code == 200
        assert res.json()["version"] == "7.16.1"
        assert "fix vlan" in res.json()["notes"]

        # Invalid version error propagation
        mock_notes.side_effect = RuntimeError("Failed to fetch")
        err_res = await ac.get("/api/v1/routers/1/firmware/changelog?version=invalid")
        assert err_res.status_code == 400


@pytest.mark.asyncio
async def test_upgrade_safety_gates(async_client):
    ac, session_factory = async_client

    async with session_factory() as session:
        router = Router(id=1, name="Core-GW", host="192.168.88.1", port=80)
        session.add(router)
        await session.commit()

    with patch("backend.app.services.router_manager.router_manager.get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.get_package_update_status.return_value = {
            "installed_version": "7.15.2",
            "latest_version": "7.16.1",
            "channel": "stable",
            "status": "New version is available",
            "update_available": True,
        }
        mock_client.get_routerboard_status.return_value = {
            "is_routerboard": True,
            "current_firmware": "7.15.2",
            "upgrade_firmware": "7.16.1",
            "firmware_available": True,
        }
        mock_get_client.return_value = mock_client

        # Gate 1: Name mismatch
        res_mismatch = await ac.post(
            "/api/v1/routers/1/firmware/upgrade",
            json={"confirm_name": "Wrong-Name", "stage_bootloader": True},
        )
        assert res_mismatch.status_code == 400
        assert "Confirmation mismatch" in res_mismatch.json()["detail"]

        # Gate 2: No update available
        mock_client.get_package_update_status.return_value["update_available"] = False
        res_no_up = await ac.post(
            "/api/v1/routers/1/firmware/upgrade",
            json={"confirm_name": "Core-GW", "stage_bootloader": True},
        )
        assert res_no_up.status_code == 400
        assert "already on the newest version" in res_no_up.json()["detail"]

        # Reset update available
        mock_client.get_package_update_status.return_value["update_available"] = True

        # Gate 3: Backup failure aborts upgrade
        with patch("backend.app.api.v1.endpoints.firmware.run_router_backup", new_callable=AsyncMock) as mock_backup:
            mock_backup.side_effect = RuntimeError("Disk full")
            res_fail = await ac.post(
                "/api/v1/routers/1/firmware/upgrade",
                json={"confirm_name": "Core-GW", "stage_bootloader": True},
            )
            assert res_fail.status_code == 500
            assert "Pre-upgrade disaster recovery backup failed" in res_fail.json()["detail"]
            assert not mock_client.install_package_update.called

        # Gate 4 & 5: Successful upgrade with automated pinned backup and bootloader staging
        with patch("backend.app.api.v1.endpoints.firmware.run_router_backup", new_callable=AsyncMock) as mock_backup:
            dummy_backup = RouterBackup(
                id=42,
                router_id=1,
                outcome="changed",
                source="manual",
                is_pinned=False,
            )
            mock_backup.return_value = dummy_backup

            res_ok = await ac.post(
                "/api/v1/routers/1/firmware/upgrade",
                json={"confirm_name": "Core-GW", "stage_bootloader": True},
            )
            assert res_ok.status_code == 200
            data = res_ok.json()
            assert data["status"] == "rebooting"
            assert data["backup_id"] == 42
            assert data["target_version"] == "7.16.1"
            assert dummy_backup.is_pinned is True
            assert "Pre-upgrade backup" in dummy_backup.note
            assert mock_client.upgrade_routerboard_firmware.called
            assert mock_client.install_package_update.called


@pytest.mark.asyncio
async def test_bootloader_upgrade_endpoint(async_client):
    ac, session_factory = async_client

    async with session_factory() as session:
        router = Router(id=1, name="Core-GW", host="192.168.88.1", port=80)
        session.add(router)
        await session.commit()

    with patch("backend.app.services.router_manager.router_manager.get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.get_routerboard_status.return_value = {
            "is_routerboard": True,
            "current_firmware": "7.15.2",
            "upgrade_firmware": "7.16.1",
            "firmware_available": True,
        }
        mock_get_client.return_value = mock_client

        # Name mismatch
        res_mismatch = await ac.post(
            "/api/v1/routers/1/firmware/bootloader",
            json={"confirm_name": "Wrong", "reboot": False},
        )
        assert res_mismatch.status_code == 400

        # Bootloader already up to date
        mock_client.get_routerboard_status.return_value["firmware_available"] = False
        res_same = await ac.post(
            "/api/v1/routers/1/firmware/bootloader",
            json={"confirm_name": "Core-GW", "reboot": False},
        )
        assert res_same.status_code == 400
        assert "already on the upgrade firmware" in res_same.json()["detail"]

        # Reset firmware available
        mock_client.get_routerboard_status.return_value["firmware_available"] = True

        # Successful staging without reboot
        res_stage = await ac.post(
            "/api/v1/routers/1/firmware/bootloader",
            json={"confirm_name": "Core-GW", "reboot": False},
        )
        assert res_stage.status_code == 200
        assert res_stage.json()["status"] == "staged"
        assert mock_client.upgrade_routerboard_firmware.called
        assert not mock_client.reboot_system.called

        # Successful upgrade with reboot
        res_reboot = await ac.post(
            "/api/v1/routers/1/firmware/bootloader",
            json={"confirm_name": "Core-GW", "reboot": True},
        )
        assert res_reboot.status_code == 200
        assert res_reboot.json()["status"] == "rebooting"
        assert mock_client.reboot_system.called


import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.core.config import Settings
from backend.app.db.models import Base, Device, User
from backend.app.services.routeros import RouterOSClient
from backend.app.services.traffic_controller import (
    TrafficController,
    parse_bandwidth_string,
    parse_bytes_string,
    parse_rate_string,
)


@pytest.fixture
def mock_settings():
    return Settings(
        ROUTEROS_HOST="192.168.88.1",
        ROUTEROS_PORT=443,
        ROUTEROS_USE_SSL=True,
        ROUTEROS_SSL_VERIFY=False,
        ROUTEROS_USER="admin",
        ROUTEROS_PASSWORD="password"
    )


def test_bandwidth_string_parsing():
    assert parse_bandwidth_string("unlimited") == "0/0"
    assert parse_bandwidth_string("0") == "0/0"
    assert parse_bandwidth_string("20M") == "20M/20M"
    assert parse_bandwidth_string("10M/50M") == "10M/50M"
    assert parse_rate_string("1500000/45000000") == (1500000, 45000000)
    assert parse_bytes_string("1024000/8192000") == (1024000, 8192000)


@pytest.mark.asyncio
async def test_sync_user_queue_create_and_update(mock_settings):
    client = RouterOSClient(mock_settings)
    ctrl = TrafficController(client)

    with respx.mock(base_url="https://192.168.88.1:443/rest") as respx_mock:
        # Initial empty queue list
        respx_mock.get("/queue/simple").respond(200, json=[])
        # Create queue
        respx_mock.put("/queue/simple").respond(200, json={".id": "*Q1"})

        q_id = await ctrl.sync_user_queue(
            user_id=1,
            user_name="Alex",
            ip_addresses=["192.168.88.10"],
            speed_limit="25M/50M"
        )
        assert q_id == "*Q1"

        # Now test updating existing queue
        respx_mock.get("/queue/simple").respond(
            200,
            json=[{
                ".id": "*Q1",
                "name": "mikroman-user-1",
                "target": "192.168.88.10/32",
                "max-limit": "25M/50M",
                "comment": "mikroman:managed:user_1"
            }]
        )
        respx_mock.patch("/queue/simple/*Q1").respond(200, json={})

        updated_id = await ctrl.sync_user_queue(
            user_id=1,
            user_name="Alex",
            ip_addresses=["192.168.88.10", "192.168.88.11"],
            speed_limit="50M/100M"
        )
        assert updated_id == "*Q1"


@pytest.mark.asyncio
async def test_pause_and_resume_internet(mock_settings):
    client = RouterOSClient(mock_settings)
    ctrl = TrafficController(client)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        user = User(name="Kids", speed_limit="10M", is_paused=False)
        session.add(user)
        await session.commit()
        await session.refresh(user)

        device = Device(user_id=user.id, mac_address="11:22:33:44:55:66", ip_address="192.168.88.55", is_active=True)
        session.add(device)
        await session.commit()

        with respx.mock(base_url="https://192.168.88.1:443/rest") as respx_mock:
            respx_mock.put("/ip/firewall/address-list").respond(200, json={".id": "*A1"})
            respx_mock.get("/ip/firewall/address-list").respond(
                200,
                json=[{".id": "*A1", "list": "mikroman_blocked", "address": "192.168.88.55", "comment": "mikroman:paused:user_1"}]
            )
            respx_mock.delete("/ip/firewall/address-list/*A1").respond(200, json={})
            respx_mock.get("/ip/firewall/filter").respond(200, json=[])
            respx_mock.put("/ip/firewall/filter").respond(200, json={".id": "*F1"})

            # Test Pause
            pause_ok = await ctrl.pause_user_internet(user.id, session)
            assert pause_ok is True
            assert user.is_paused is True

            # Test Resume
            resume_ok = await ctrl.resume_user_internet(user.id, session)
            assert resume_ok is True
            assert user.is_paused is False

    await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_pause_firewall_rules(mock_settings):
    client = RouterOSClient(mock_settings)
    ctrl = TrafficController(client)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        from backend.app.db.models import AppSetting
        session.add(AppSetting(key="pause_allowed_networks", value="192.168.1.0/24, 10.10.0.0/16"))
        await session.commit()

        with respx.mock(base_url="https://192.168.88.1:443/rest") as respx_mock:
            # Address list mocks
            respx_mock.get("/ip/firewall/address-list").respond(
                200,
                json=[
                    {".id": "*OLD1", "list": "mikroman_allowed_lans", "address": "172.16.0.0/12", "comment": "mikroman:allowed_lan"}
                ]
            )
            respx_mock.delete("/ip/firewall/address-list/*OLD1").respond(200, json={})
            respx_mock.put("/ip/firewall/address-list").respond(200, json={".id": "*NEW1"})

            # Filter rule mocks
            respx_mock.get("/ip/firewall/filter").respond(
                200,
                json=[
                    {
                        ".id": "*F1",
                        "chain": "forward",
                        "action": "drop",
                        "src-address-list": "mikroman_blocked",
                        "dst-address-list": "!mikroman_allowed_lans",
                        "disabled": True,
                        "comment": "mikroman:drop_blocked_internet"
                    }
                ]
            )
            respx_mock.patch("/ip/firewall/filter/*F1").respond(200, json={})

            ok = await ctrl.ensure_pause_firewall_rules(session)
            assert ok is True

    await engine.dispose()

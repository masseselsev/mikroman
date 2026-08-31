"""Linking several network adapters of one physical machine into one device.

A laptop docked over Ethernet and roaming over Wi-Fi presents a different MAC
address per adapter, so discovery created two unrelated devices and split that
machine's traffic between them. Linking makes them one logical device with
several connection paths.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base, Device, User
from backend.app.services.device_linking import (
    build_device_groups,
    classify_connection,
    find_link_suggestions,
    link_device,
    unlink_device,
)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _laptop_pair(session):
    """The same laptop seen twice: once wired, once wireless."""
    user = User(name="M", speed_limit="unlimited")
    session.add(user)
    await session.commit()
    await session.refresh(user)

    wired = Device(
        user_id=user.id, mac_address="74:D4:DD:C6:51:73", ip_address="192.168.88.239",
        hostname="mpcX", custom_name="mpcX", vendor="Quanta Computer",
        last_interface="ether3", is_active=True,
    )
    wireless = Device(
        user_id=user.id, mac_address="AA:BB:CC:11:22:33", ip_address="192.168.88.250",
        hostname="mpcX", vendor="Intel", last_interface="wifi2",
        last_wifi_signal=-58, is_active=True,
    )
    session.add_all([wired, wireless])
    await session.commit()
    await session.refresh(wired)
    await session.refresh(wireless)
    return user, wired, wireless


def test_classify_connection_uses_signal_then_interface():
    """Wi-Fi is identified by a signal reading, or failing that by interface name."""
    assert classify_connection(interface="wifi2", signal=-58) == "wireless"
    assert classify_connection(interface="mld1", signal=None) == "wireless"
    assert classify_connection(interface="wlan1", signal=None) == "wireless"
    assert classify_connection(interface="ether3", signal=None) == "wired"
    assert classify_connection(interface=None, signal=None) is None
    # A signal reading wins: some drivers report wireless clients on a bridge.
    assert classify_connection(interface="bridge", signal=-70) == "wireless"


def test_aggregating_interfaces_are_inconclusive_rather_than_wired():
    """A bridge carries both media, so its name is not evidence of a cable.

    This previously answered "wired". Every wireless client's ARP entry is
    recorded against the bridge, so a phone seen only through ARP was labelled
    wired - and a phone that had rotated its MAC then appeared as one "wired"
    and one "wireless" record of the same hostname, which is the exact shape
    find_link_suggestions scores highest. It proposed joining a phone to itself
    as a dual-homed machine.
    """
    assert classify_connection(interface="bridge", signal=None) is None
    assert classify_connection(interface="bridge-lan", signal=None) is None
    assert classify_connection(interface="vlan10", signal=None) is None
    assert classify_connection(interface="bond1", signal=None) is None


@pytest.mark.asyncio
async def test_link_makes_one_logical_device(session):
    _, wired, wireless = await _laptop_pair(session)

    await link_device(session, device_id=wireless.id, primary_device_id=wired.id)

    groups = await build_device_groups(session)
    assert len(groups) == 1
    group = groups[0]
    assert group["primary"].id == wired.id
    assert [a.id for a in group["adapters"]] == [wired.id, wireless.id]
    assert {a.connection_kind for a in group["adapters"]} == {"wired", "wireless"}


@pytest.mark.asyncio
async def test_group_traffic_and_state_aggregate_across_adapters(session):
    _, wired, wireless = await _laptop_pair(session)
    await link_device(session, device_id=wireless.id, primary_device_id=wired.id)

    # Only the wireless adapter is currently carrying traffic.
    wired.is_active = False
    await session.commit()

    groups = await build_device_groups(session)
    group = groups[0]
    # The machine is online while any of its adapters is.
    assert group["is_active"] is True
    assert group["active_interfaces"] == ["wifi2"]


@pytest.mark.asyncio
async def test_unlink_restores_two_independent_devices(session):
    _, wired, wireless = await _laptop_pair(session)
    await link_device(session, device_id=wireless.id, primary_device_id=wired.id)

    await unlink_device(session, device_id=wireless.id)

    groups = await build_device_groups(session)
    assert len(groups) == 2
    assert all(len(g["adapters"]) == 1 for g in groups)


@pytest.mark.asyncio
async def test_a_device_cannot_link_to_itself(session):
    _, wired, _ = await _laptop_pair(session)
    with pytest.raises(ValueError):
        await link_device(session, device_id=wired.id, primary_device_id=wired.id)


@pytest.mark.asyncio
async def test_linking_to_a_secondary_attaches_to_its_primary(session):
    """Chains are flattened so a group is always one level deep."""
    user, wired, wireless = await _laptop_pair(session)
    await link_device(session, device_id=wireless.id, primary_device_id=wired.id)

    third = Device(
        user_id=user.id, mac_address="DD:EE:FF:00:11:22", ip_address="192.168.88.251",
        hostname="mpcX", last_interface="wifi3", is_active=True,
    )
    session.add(third)
    await session.commit()
    await session.refresh(third)

    # Link onto the *secondary* adapter; it must resolve to the primary.
    await link_device(session, device_id=third.id, primary_device_id=wireless.id)
    await session.refresh(third)
    assert third.linked_to_device_id == wired.id

    groups = await build_device_groups(session)
    assert len(groups) == 1
    assert len(groups[0]["adapters"]) == 3


@pytest.mark.asyncio
async def test_suggestions_pair_matching_hostnames_on_different_media(session):
    """The same hostname on two adapters is the strong signal for one machine."""
    await _laptop_pair(session)

    suggestions = await find_link_suggestions(session)
    assert len(suggestions) == 1
    s = suggestions[0]
    assert {s.device_id, s.primary_device_id}
    assert s.confidence >= 0.8
    assert "mpcX" in s.reason


@pytest.mark.asyncio
async def test_no_suggestion_for_unrelated_devices(session):
    user = User(name="M", speed_limit="unlimited")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    session.add_all([
        Device(user_id=user.id, mac_address="11:11:11:11:11:11", hostname="printer",
               ip_address="192.168.88.10", last_interface="ether2", is_active=True),
        Device(user_id=user.id, mac_address="22:22:22:22:22:22", hostname="tv",
               ip_address="192.168.88.11", last_interface="wifi2",
               last_wifi_signal=-60, is_active=True),
    ])
    await session.commit()

    assert await find_link_suggestions(session) == []


@pytest.mark.asyncio
async def test_already_linked_devices_are_not_suggested_again(session):
    _, wired, wireless = await _laptop_pair(session)
    await link_device(session, device_id=wireless.id, primary_device_id=wired.id)

    assert await find_link_suggestions(session) == []

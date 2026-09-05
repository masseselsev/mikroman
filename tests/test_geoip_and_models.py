import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base, UserDestinationStat
from backend.app.services.geoip import GeoLocation, resolve_ip_location


def test_resolve_ip_location_private_and_loopback():
    for ip in ["127.0.0.1", "::1", "192.168.88.1", "10.0.0.5", "172.16.0.1", "169.254.1.1"]:
        loc = resolve_ip_location(ip)
        assert loc.country_code == "LOCAL"
        assert loc.flag_emoji == "🏠"
        assert "Local" in loc.country_name or "Private" in loc.country_name


def test_resolve_ip_location_public():
    loc = resolve_ip_location("8.8.8.8")
    assert isinstance(loc, GeoLocation)
    assert loc.country_code != "LOCAL"
    assert len(loc.country_code) == 2
    assert loc.flag_emoji != ""
    assert loc.country_name != ""

    loc_cloudflare = resolve_ip_location("1.1.1.1")
    assert isinstance(loc_cloudflare, GeoLocation)
    assert len(loc_cloudflare.country_code) == 2


def test_resolve_ip_location_covers_googles_legacy_web_range():
    """A live Live Connections view showed 64.233.184.108, 64.233.166.109 and
    64.233.184.188 - all ordinary Google web/mail traffic - resolving to
    Unknown, because the built-in table only listed Google's newer
    142.250.0.0/15 and 172.217.0.0/16 blocks. 64.233.160.0/19 is Google's
    original web-services range and has been in continuous daily use for
    two decades; it belongs in the curated list alongside the others.
    """
    for ip in ("64.233.184.108", "64.233.166.109", "64.233.184.188"):
        loc = resolve_ip_location(ip)
        assert loc.country_code == "US", ip


def test_resolve_ip_location_covers_telegrams_datacenter_range():
    """Same live view: 149.154.167.50 and 149.154.167.35 (Telegram's own
    datacenter range, AS62041/AS44907) resolved to Unknown."""
    for ip in ("149.154.167.50", "149.154.167.35"):
        loc = resolve_ip_location(ip)
        assert loc.country_code == "GB", ip


@pytest.mark.asyncio
async def test_user_destination_stat_model_crud():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with factory() as s:
        stat = UserDestinationStat(
            user_id=None,
            device_id=None,
            destination_ip="142.250.190.46",
            domain="youtube.com",
            country_code="US",
            bytes_in=1000,
            bytes_out=500,
            total_bytes=1500,
            hit_count=5,
        )
        s.add(stat)
        await s.commit()

        loaded = (
            await s.execute(select(UserDestinationStat).where(UserDestinationStat.domain == "youtube.com"))
        ).scalar_one()
        assert loaded.total_bytes == 1500
        assert loaded.hit_count == 5
        assert loaded.country_code == "US"
        assert loaded.destination_ip == "142.250.190.46"


import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import AppSetting, Base, Device, User


@pytest.mark.asyncio
async def test_create_user_and_device():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        user = User(
            name="Alex",
            avatar_icon="user-laptop",
            speed_limit="50M",
            is_paused=False,
            priority=1
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        device = Device(
            user_id=user.id,
            mac_address="AA:BB:CC:DD:EE:FF",
            ip_address="192.168.88.100",
            hostname="Alex-MacBook",
            custom_name="MacBook Pro",
            vendor="Apple",
            last_interface="ether2",
            last_wifi_signal=-52,
            is_active=True
        )
        session.add(device)
        await session.commit()
        await session.refresh(device)

        assert user.id is not None
        assert user.name == "Alex"
        assert device.id is not None
        assert device.user_id == user.id
        assert device.mac_address == "AA:BB:CC:DD:EE:FF"
        assert device.is_active is True

        # Test app settings
        setting = AppSetting(key="theme", value="dark", description="UI Theme preference")
        session.add(setting)
        await session.commit()
        await session.refresh(setting)
        assert setting.key == "theme"
        assert setting.value == "dark"

    await engine.dispose()

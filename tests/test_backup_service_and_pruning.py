from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base, Router, RouterBackup
from backend.app.services.backup_service import prune_router_backups, run_router_backup


@pytest.fixture
async def async_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session
    # Without this the aiosqlite worker thread outlives the event loop and
    # raises "Event loop is closed" into pytest's thread-exception hook.
    await engine.dispose()


@pytest.mark.asyncio
async def test_run_router_backup_changed_then_unchanged(async_session, tmp_path):
    router = Router(name="TestRouter", host="192.168.88.1", port=80)
    async_session.add(router)
    await async_session.commit()
    await async_session.refresh(router)

    sample_rsc = "# 2026-09-04 15:00:00 by RouterOS 7.15\n/ip address add address=192.168.88.1/24"
    sample_backup_bytes = b"\x01\x02\x03\x04"

    with patch("backend.app.services.backup_service.get_routeros_client") as mock_get_client, \
         patch("backend.app.services.backup_service.BACKUP_STORAGE_DIR", str(tmp_path)):

        client_mock = AsyncMock()
        mock_get_client.return_value = client_mock
        client_mock.sweep_temporary_files.return_value = 0
        client_mock.export_config.return_value = sample_rsc
        client_mock.create_system_backup.return_value = sample_backup_bytes
        client_mock.get_system_resource.return_value = {"board-name": "RB5009", "version": "7.15.2"}
        client_mock.get_system_routerboard.return_value = {"serial-number": "SN12345"}

        # First run: should be changed
        b1 = await run_router_backup(router.id, source="manual", db_session=async_session)
        assert b1.outcome == "changed"
        assert b1.rsc_content is not None
        assert b1.fingerprint is not None
        assert client_mock.sweep_temporary_files.call_count >= 2

        # Second run with same config: should be unchanged
        b2 = await run_router_backup(router.id, source="scheduled", db_session=async_session)
        assert b2.outcome == "unchanged"
        assert b2.fingerprint == b1.fingerprint
        assert b2.rsc_content is None  # deduplicated!


@pytest.mark.asyncio
async def test_run_router_backup_failure_records_error_and_sweeps(async_session):
    router = Router(name="FailRouter", host="192.168.88.1", port=80)
    async_session.add(router)
    await async_session.commit()
    await async_session.refresh(router)

    with patch("backend.app.services.backup_service.get_routeros_client") as mock_get_client:
        client_mock = AsyncMock()
        mock_get_client.return_value = client_mock
        client_mock.sweep_temporary_files.return_value = 0
        client_mock.export_config.side_effect = RuntimeError("Connection reset by peer")

        b = await run_router_backup(router.id, source="manual", db_session=async_session)
        assert b.outcome == "failed"
        assert "Connection reset by peer" in b.error_message
        assert client_mock.sweep_temporary_files.call_count >= 2


@pytest.mark.asyncio
async def test_prune_router_backups_preserves_pinned(async_session):
    router = Router(name="TestRouter", host="192.168.88.1")
    async_session.add(router)
    await async_session.commit()
    await async_session.refresh(router)

    now = datetime.now(timezone.utc)
    for i in range(5):
        b = RouterBackup(
            router_id=router.id,
            outcome="changed",
            source="scheduled",
            created_at=now - timedelta(days=5 - i),
            is_pinned=(i in (0, 2)),
            fingerprint=f"fp_{i}",
        )
        async_session.add(b)
    await async_session.commit()

    pruned = await prune_router_backups(router.id, max_count=1, max_days=30, db_session=async_session)
    assert pruned == 2

    q = await async_session.execute(select(RouterBackup).filter(RouterBackup.router_id == router.id))
    remaining = list(q.scalars().all())
    assert len(remaining) == 3
    pinned_count = sum(1 for r in remaining if r.is_pinned)
    assert pinned_count == 2

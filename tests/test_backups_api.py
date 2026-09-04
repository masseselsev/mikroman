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


@pytest.mark.asyncio
async def test_backups_api_crud_and_diff(async_client):
    client, session_factory = async_client

    async with session_factory() as session:
        router = Router(name="APIRouter", host="192.168.88.1", port=80)
        session.add(router)
        await session.commit()
        await session.refresh(router)

        b1 = RouterBackup(
            router_id=router.id,
            outcome="changed",
            source="manual",
            fingerprint="fp1",
            rsc_content="/interface bridge add name=br0\n",
            rsc_bytes=32,
            is_pinned=False,
            note="Initial",
        )
        b2 = RouterBackup(
            router_id=router.id,
            outcome="changed",
            source="manual",
            fingerprint="fp2",
            rsc_content="/interface bridge add name=br0\n/ip address add address=10.0.0.1/24 interface=br0\n",
            rsc_bytes=80,
            is_pinned=False,
        )
        session.add_all([b1, b2])
        await session.commit()
        await session.refresh(b1)
        await session.refresh(b2)
        router_id = router.id
        b1_id = b1.id
        b2_id = b2.id

    # 1. List backups
    resp = await client.get(f"/api/v1/routers/{router_id}/backups")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2

    # 2. Toggle pin and note
    patch_resp = await client.patch(
        f"/api/v1/routers/{router_id}/backups/{b1_id}", json={"is_pinned": True, "note": "Milestone"}
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["is_pinned"] is True
    assert patch_resp.json()["note"] == "Milestone"

    # 3. Download RSC
    dl_resp = await client.get(f"/api/v1/routers/{router_id}/backups/{b1_id}/download/rsc")
    assert dl_resp.status_code == 200
    assert "attachment" in dl_resp.headers["content-disposition"]
    assert b"interface bridge" in dl_resp.content

    # 4. Diff b1 vs b2
    diff_resp = await client.get(
        f"/api/v1/routers/{router_id}/backups/diff?base_id={b1_id}&target_id={b2_id}"
    )
    assert diff_resp.status_code == 200
    diff_data = diff_resp.json()
    assert diff_data["lines_added"] >= 1
    assert diff_data["lines_removed"] == 0
    assert len(diff_data["hunks"]) > 0

    # 5. Delete b2
    del_resp = await client.delete(f"/api/v1/routers/{router_id}/backups/{b2_id}")
    assert del_resp.status_code == 204

    # Verify deleted
    get_del = await client.get(f"/api/v1/routers/{router_id}/backups/{b2_id}")
    assert get_del.status_code == 404

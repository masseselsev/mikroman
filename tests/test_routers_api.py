"""Router registration, activation and SSL provisioning through the API.

Every address here is from RFC 5737's TEST-NET-1 (192.0.2.0/24), which is
reserved for documentation and is guaranteed not to route. That is deliberate.
These fixtures previously used 192.168.88.1 - the author's own MikroTik - with
placeholder credentials, and the steps outside the respx blocks dialled it for
real. The router logged three "login failure for user admin via rest-api" per
run, which is indistinguishable from a brute-force attempt against the default
account and is enough to get the development machine blacklisted by an
anti-bruteforce rule.

The conftest ``no_real_network`` guard now blocks such a call outright; using an
unroutable address as well means the test is correct on its own terms rather
than merely contained.
"""
import json

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.db.models import Base
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
        yield client

    app.dependency_overrides.clear()
    await router_manager.aclose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_routers_crud_and_activation(async_client: AsyncClient):
    # 1. Initially empty
    res = await async_client.get("/api/v1/routers")
    assert res.status_code == 200
    assert len(res.json()["data"]) == 0

    # 2. Test connection endpoint
    with respx.mock(base_url="https://192.0.2.1:443/rest") as respx_mock:
        respx_mock.get("/system/resource").mock(
            return_value=httpx.Response(200, json={"version": "7.24.1", "board-name": "RB5009", "cpu-load": 4})
        )

        test_res = await async_client.post(
            "/api/v1/routers/test",
            json={
                "host": "192.0.2.1",
                "port": 443,
                "use_ssl": True,
                "username": "admin",
                "password": ""
            }
        )
        assert test_res.status_code == 200
        assert test_res.json()["data"]["success"] is True

    # 3. Create Router 1
    create_res1 = await async_client.post(
        "/api/v1/routers",
        json={
            "name": "Main Router",
            "host": "192.0.2.1",
            "port": 443,
            "use_ssl": True,
            "username": "admin",
            "password": "pwd"
        }
    )
    assert create_res1.status_code == 201
    r1_data = create_res1.json()["data"]
    assert r1_data["name"] == "Main Router"
    assert r1_data["is_default"] is True  # First router automatically becomes default

    # 4. Create Router 2
    create_res2 = await async_client.post(
        "/api/v1/routers",
        json={
            "name": "Branch Office",
            "host": "192.0.2.2",
            "port": 443,
            "use_ssl": True,
            "username": "admin",
            "password": "pwd2"
        }
    )
    assert create_res2.status_code == 201
    r2_data = create_res2.json()["data"]
    assert r2_data["name"] == "Branch Office"
    assert r2_data["is_default"] is False

    # 5. List routers
    list_res = await async_client.get("/api/v1/routers")
    assert list_res.status_code == 200
    assert len(list_res.json()["data"]) == 2

    # 6. Activate Router 2
    act_res = await async_client.post(f"/api/v1/routers/{r2_data['id']}/activate")
    assert act_res.status_code == 200
    assert act_res.json()["data"]["is_default"] is True

    # Verify Router 1 is no longer default
    get_r1 = await async_client.get(f"/api/v1/routers/{r1_data['id']}")
    assert get_r1.json()["data"]["is_default"] is False

    # 6b. The operator's per-router comment round-trips through PUT and GET,
    # newlines and all - the header shows it collapsed to the first lines.
    assert r1_data["comment"] is None
    note = "Rack 3, patch port 12\nISP: acct #55-1029\nreboot window Sun 04:00"
    put_res = await async_client.put(
        f"/api/v1/routers/{r1_data['id']}", json={"comment": note}
    )
    assert put_res.status_code == 200
    assert put_res.json()["data"]["comment"] == note
    reget = await async_client.get(f"/api/v1/routers/{r1_data['id']}")
    assert reget.json()["data"]["comment"] == note

    # 7. Delete Router 2
    del_res = await async_client.delete(f"/api/v1/routers/{r2_data['id']}")
    assert del_res.status_code == 200

    # Verify Router 1 restored as default
    get_r1_after = await async_client.get(f"/api/v1/routers/{r1_data['id']}")
    assert get_r1_after.json()["data"]["is_default"] is True


@pytest.mark.asyncio
async def test_provision_ssl_flow(async_client: AsyncClient):
    # Create an HTTP router on port 80
    create_res = await async_client.post(
        "/api/v1/routers",
        json={
            "name": "HTTP Router",
            "host": "192.0.2.1",
            "port": 80,
            "use_ssl": False,
            "username": "admin",
            "password": "pwd"
        }
    )
    assert create_res.status_code == 201
    router_id = create_res.json()["data"]["id"]

    # The router runs www-ssl on a non-default port (444). Provisioning must
    # discover and follow it, never rewrite it to 443.
    with respx.mock(base_url="http://192.0.2.1:80/rest") as respx_mock:
        respx_mock.get("/certificate").mock(return_value=httpx.Response(200, json=[]))
        respx_mock.post("/certificate/add").mock(return_value=httpx.Response(201, json={"ret": "*1"}))
        respx_mock.post("/certificate/sign").mock(return_value=httpx.Response(200, json={}))
        respx_mock.get("/ip/service").mock(return_value=httpx.Response(200, json=[{".id": "*www-ssl", "name": "www-ssl", "disabled": True, "port": 444}]))
        patch_route = respx_mock.patch("/ip/service/*www-ssl").mock(return_value=httpx.Response(200, json={}))

        prov_res = await async_client.post(
            f"/api/v1/routers/{router_id}/provision-ssl",
            json={"common_name": "router.lan"}
        )
        assert prov_res.status_code == 200, prov_res.text
        assert prov_res.json()["data"]["success"] is True
        assert prov_res.json()["data"]["port"] == 444

        # The service was enabled, but its port was never part of the write.
        assert patch_route.called
        sent = json.loads(patch_route.calls.last.request.content)
        assert "port" not in sent
        assert sent.get("disabled") is False

        # DB record now points at HTTPS on the router's own www-ssl port.
        updated = await async_client.get(f"/api/v1/routers/{router_id}")
        assert updated.json()["data"]["port"] == 444
        assert updated.json()["data"]["use_ssl"] is True


@pytest.mark.asyncio
async def test_certificate_management_flow(async_client: AsyncClient):
    with respx.mock(base_url="http://192.0.2.1:80/rest") as respx_mock:
        respx_mock.get("/ip/service").mock(return_value=httpx.Response(200, json=[{".id": "*www-ssl", "name": "www-ssl", "disabled": True, "certificate": "my-cert", "port": 444}]))
        respx_mock.get("/certificate").mock(return_value=httpx.Response(200, json=[
            {"name": "my-cert", "common-name": "mikrotik.lan", "days-valid": 365, "invalid-after": "2027-01-01"}
        ]))

        # Test list certificates
        list_res = await async_client.post(
            "/api/v1/routers/test-certificates",
            json={"host": "192.0.2.1", "port": 80, "use_ssl": False, "username": "admin", "password": ""}
        )
        assert list_res.status_code == 200
        certs = list_res.json()["data"]
        assert len(certs) == 1
        assert certs[0]["name"] == "my-cert"
        assert certs[0]["is_active_ssl"] is True

        # Test bind certificate - the custom www-ssl port (444) is preserved.
        bind_patch = respx_mock.patch("/ip/service/*www-ssl").mock(return_value=httpx.Response(200, json={}))
        bind_res = await async_client.post(
            "/api/v1/routers/test-bind-certificate",
            json={
                "conn": {"host": "192.0.2.1", "port": 80, "use_ssl": False, "username": "admin", "password": ""},
                "cert_req": {"certificate_name": "my-cert"}
            }
        )
        assert bind_res.status_code == 200
        assert bind_res.json()["data"]["success"] is True
        assert bind_res.json()["data"]["port"] == 444
        assert "port" not in json.loads(bind_patch.calls.last.request.content)


def _mock_router(respx_mock, *, serial="SN-1", version="7.24.1", board="hAP-be3"):
    respx_mock.get("/system/resource").mock(return_value=httpx.Response(
        200, json={"version": version, "board-name": board, "cpu-load": 3}
    ))
    respx_mock.get("/system/routerboard").mock(return_value=httpx.Response(
        200, json={"serial-number": serial, "model": board, "board-name": board}
    ))
    respx_mock.get("/ip/service").mock(return_value=httpx.Response(200, json=[]))
    respx_mock.get("/certificate").mock(return_value=httpx.Response(200, json=[]))


async def _add_router(async_client, name, host, serial):
    with respx.mock(base_url=f"https://{host}:443/rest") as m:
        _mock_router(m, serial=serial)
        res = await async_client.post("/api/v1/routers", json={
            "name": name, "host": host, "port": 443, "use_ssl": True,
            "username": "admin", "password": "pw",
        })
    assert res.status_code == 201, res.text
    return res.json()["data"]


@pytest.mark.asyncio
async def test_archived_route_is_not_shadowed_by_the_id_route(async_client: AsyncClient):
    res = await async_client.get("/api/v1/routers/archived")
    assert res.status_code == 200
    assert res.json()["data"] == []


@pytest.mark.asyncio
async def test_delete_archive_then_readd_by_serial_restores_history(async_client: AsyncClient):
    r1 = await _add_router(async_client, "Keep", "192.0.2.10", "SN-KEEP")
    r2 = await _add_router(async_client, "Edge", "192.0.2.11", "SN-EDGE")

    # Seed a user on the router we are about to archive.
    seed = await async_client.post("/api/v1/users", json={"name": "Mara", "router_id": r2["id"]})
    assert seed.status_code in (200, 201)

    # Archive (default delete mode).
    d = await async_client.request("DELETE", f"/api/v1/routers/{r2['id']}", json={"mode": "archive"})
    assert d.status_code == 200
    assert "archived" in d.json()["message"].lower()

    # Gone from the live list, present in the archived list.
    live = await async_client.get("/api/v1/routers")
    assert [r["id"] for r in live.json()["data"]] == [r1["id"]]
    arch = await async_client.get("/api/v1/routers/archived")
    assert [r["id"] for r in arch.json()["data"]] == [r2["id"]]

    # The user survived the archive.
    users_while_archived = await async_client.get(f"/api/v1/users?router_id={r2['id']}")
    assert any(u["name"] == "Mara" for u in users_while_archived.json()["data"])

    # Re-add the same box (same serial) - it restores, no second row.
    again = await _add_router(async_client, "Edge Reborn", "192.0.2.11", "SN-EDGE")
    assert again["id"] == r2["id"]
    live2 = await async_client.get("/api/v1/routers")
    assert sorted(r["id"] for r in live2.json()["data"]) == sorted([r1["id"], r2["id"]])
    assert (await async_client.get("/api/v1/routers/archived")).json()["data"] == []
    users_after = await async_client.get(f"/api/v1/users?router_id={r2['id']}")
    assert any(u["name"] == "Mara" for u in users_after.json()["data"])


@pytest.mark.asyncio
async def test_delete_purge_removes_everything(async_client: AsyncClient):
    r1 = await _add_router(async_client, "Keep", "192.0.2.20", "SN-A")
    r2 = await _add_router(async_client, "Doomed", "192.0.2.21", "SN-B")
    await async_client.post("/api/v1/users", json={"name": "Gone", "router_id": r2["id"]})

    d = await async_client.request("DELETE", f"/api/v1/routers/{r2['id']}", json={"mode": "purge"})
    assert d.status_code == 200
    assert "purged" in d.json()["message"].lower()

    assert (await async_client.get("/api/v1/routers/archived")).json()["data"] == []
    assert [r["id"] for r in (await async_client.get("/api/v1/routers")).json()["data"]] == [r1["id"]]
    # The purged router's user is gone; adding the box back starts clean.
    readd = await _add_router(async_client, "Doomed II", "192.0.2.21", "SN-B")
    fresh_users = await async_client.get(f"/api/v1/users?router_id={readd['id']}")
    assert all(u["name"] != "Gone" for u in fresh_users.json()["data"])


@pytest.mark.asyncio
async def test_change_router_keeps_users_and_swaps_the_connection(async_client: AsyncClient):
    r1 = await _add_router(async_client, "Site A", "192.0.2.30", "SN-OLD")
    await async_client.post("/api/v1/users", json={"name": "Stays", "router_id": r1["id"]})

    with respx.mock(base_url="https://192.0.2.31:443/rest") as m:
        _mock_router(m, serial="SN-NEW")
        ch = await async_client.post(f"/api/v1/routers/{r1['id']}/change", json={
            "name": "Site A", "host": "192.0.2.31", "port": 443, "use_ssl": True,
            "username": "admin", "password": "pw2", "history_mode": "keep",
        })
    assert ch.status_code == 200, ch.text
    body = ch.json()["data"]
    assert body["id"] == r1["id"]
    assert body["host"] == "192.0.2.31"
    assert body["serial_number"] == "SN-NEW"
    kept = await async_client.get(f"/api/v1/users?router_id={r1['id']}")
    assert any(u["name"] == "Stays" for u in kept.json()["data"])


@pytest.mark.asyncio
async def test_change_router_rejects_an_unreachable_new_router(async_client: AsyncClient):
    r1 = await _add_router(async_client, "Site B", "192.0.2.40", "SN-X")
    # No respx mock -> the probe fails.
    ch = await async_client.post(f"/api/v1/routers/{r1['id']}/change", json={
        "name": "Site B", "host": "192.0.2.41", "port": 443, "use_ssl": True,
        "username": "admin", "password": "pw",
    })
    assert ch.status_code == 400
    assert "could not reach" in ch.json()["detail"].lower()



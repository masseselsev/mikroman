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

    # Mock REST API for certificate creation and www-ssl service enabling
    with respx.mock(base_url="http://192.0.2.1:80/rest") as respx_mock:
        respx_mock.get("/certificate").mock(return_value=httpx.Response(200, json=[]))
        respx_mock.post("/certificate/add").mock(return_value=httpx.Response(201, json={"ret": "*1"}))
        respx_mock.post("/certificate/sign").mock(return_value=httpx.Response(200, json={}))
        respx_mock.get("/ip/service").mock(return_value=httpx.Response(200, json=[{".id": "*www-ssl", "name": "www-ssl", "disabled": True}]))
        respx_mock.patch("/ip/service/*www-ssl").mock(return_value=httpx.Response(200, json={}))

        prov_res = await async_client.post(
            f"/api/v1/routers/{router_id}/provision-ssl",
            json={"common_name": "router.lan", "port": 443}
        )
        assert prov_res.status_code == 200, prov_res.text
        assert prov_res.json()["data"]["success"] is True

        # Verify DB record updated to HTTPS / 443
        updated = await async_client.get(f"/api/v1/routers/{router_id}")
        assert updated.json()["data"]["port"] == 443
        assert updated.json()["data"]["use_ssl"] is True


@pytest.mark.asyncio
async def test_certificate_management_flow(async_client: AsyncClient):
    with respx.mock(base_url="http://192.0.2.1:80/rest") as respx_mock:
        respx_mock.get("/ip/service").mock(return_value=httpx.Response(200, json=[{".id": "*www-ssl", "name": "www-ssl", "disabled": True, "certificate": "my-cert"}]))
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

        # Test bind certificate
        respx_mock.patch("/ip/service/*www-ssl").mock(return_value=httpx.Response(200, json={}))
        bind_res = await async_client.post(
            "/api/v1/routers/test-bind-certificate",
            json={
                "conn": {"host": "192.0.2.1", "port": 80, "use_ssl": False, "username": "admin", "password": ""},
                "cert_req": {"certificate_name": "my-cert", "port": 443}
            }
        )
        assert bind_res.status_code == 200
        assert bind_res.json()["data"]["success"] is True



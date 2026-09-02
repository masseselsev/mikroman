import asyncio
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import Router
from backend.app.db.session import get_db
from backend.app.schemas.common import ApiResponse
from backend.app.schemas.router import (
    RouterBindCertRequest,
    RouterCertificateDTO,
    RouterChangeRequest,
    RouterCreate,
    RouterDeleteRequest,
    RouterProvisionSslRequest,
    RouterProvisionSslResponse,
    RouterResponse,
    RouterTestConnectionRequest,
    RouterTestConnectionResponse,
    RouterUpdate,
    RouterUploadCertRequest,
)
from backend.app.services import router_lifecycle
from backend.app.services.router_manager import router_manager
from backend.app.services.routeros import RouterOSClient

logger = logging.getLogger("mikroman.api.routers")

router = APIRouter(prefix="/routers", tags=["Routers"])


@router.get("", response_model=ApiResponse[List[RouterResponse]])
async def list_routers(
    db: AsyncSession = Depends(get_db)
):
    """List all registered MikroTik routers (archived ones are excluded)."""
    result = await db.execute(
        select(Router)
        .where(Router.archived_at.is_(None))
        .order_by(Router.is_default.desc(), Router.id.asc())
    )
    routers = list(result.scalars().all())

    async def _probe(r: Router) -> RouterResponse:
        """One router's response row, with a live status probe when active.

        Each probe is a RouterOS round trip; running them concurrently keeps the
        endpoint at the cost of the slowest single router rather than their sum,
        which matters because the dashboard calls this on a timer and the count
        of managed routers only grows.
        """
        item = RouterResponse.model_validate(r)
        item.is_online = False
        if not r.is_active:
            return item
        # A dedicated short-lived client, not the pooled telemetry one: a slow
        # or momentarily unreachable remote router must not trip the shared
        # circuit breaker here and vanish from the live view. Longer timeout for
        # the same reason - a router across the internet can take >5s to answer
        # and still be perfectly usable.
        probe = router_manager.build_probe_client(r)
        try:
            res = await probe.get_system_resource()
            item.is_online = True
            item.ros_version = res.version
            item.board_name = res.board_name or res.model
            item.model = res.model
            item.architecture = res.architecture_name
            item.cpu_load = res.cpu_load
        except Exception:
            item.is_online = False
        finally:
            await probe.aclose()
        return item

    response_items = await asyncio.gather(*(_probe(r) for r in routers))
    return ApiResponse(data=list(response_items))


@router.post("", response_model=ApiResponse[RouterResponse], status_code=status.HTTP_201_CREATED)
async def create_router(
    payload: RouterCreate,
    db: AsyncSession = Depends(get_db)
):
    """Register a new MikroTik router.

    If the router answers with a RouterBoard serial that belongs to a router
    the operator previously archived, that archived router is **restored** and
    its connection details refreshed instead of a second row being created -
    its users, devices, history and settings come straight back.
    """
    # Probe the router once up front: it tells us the serial (for archive
    # matching) and confirms it is reachable. A failure is not fatal here - the
    # operator may be pre-registering a box - so we fall back to a plain create.
    probe = await router_manager.test_connection(
        RouterTestConnectionRequest(
            host=payload.host, port=payload.port, use_ssl=payload.use_ssl,
            ssl_verify=payload.ssl_verify, username=payload.username,
            password=payload.password,
        )
    )
    serial = probe.serial_number if probe.success else None

    archived = await router_lifecycle.find_archived_by_serial(db, serial)
    if archived is not None:
        await router_lifecycle.restore_router(db, archived, {
            "name": payload.name, "host": payload.host, "port": payload.port,
            "use_ssl": payload.use_ssl, "ssl_verify": payload.ssl_verify,
            "username": payload.username, "password": payload.password,
            "comment": payload.comment, "serial_number": serial,
        })
        await db.commit()
        await db.refresh(archived)
        await router_manager.remove_client(archived.id)
        return ApiResponse(
            data=RouterResponse.model_validate(archived),
            message=f"'{archived.name}' was archived - restored it with its history and settings intact.",
        )

    # Check if name already exists
    existing = await db.execute(select(Router).where(Router.name == payload.name))
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Router with name '{payload.name}' already exists"
        )

    # If first router, automatically make it default
    count_res = await db.execute(select(Router).where(Router.archived_at.is_(None)))
    is_first = len(count_res.scalars().all()) == 0
    is_default = payload.is_default or is_first

    if is_default:
        await db.execute(update(Router).values(is_default=False))

    router_obj = Router(
        name=payload.name,
        host=payload.host,
        port=payload.port,
        use_ssl=payload.use_ssl,
        ssl_verify=payload.ssl_verify,
        username=payload.username,
        password=payload.password,
        comment=payload.comment,
        serial_number=serial,
        is_active=payload.is_active,
        is_default=is_default
    )
    db.add(router_obj)
    await db.commit()
    await db.refresh(router_obj)

    return ApiResponse(
        data=RouterResponse.model_validate(router_obj),
        message="Router added successfully"
    )


@router.post("/test", response_model=ApiResponse[RouterTestConnectionResponse])
async def test_router_connection(
    payload: RouterTestConnectionRequest
):
    """Test connectivity to a MikroTik router before saving."""
    res = await router_manager.test_connection(payload)
    return ApiResponse(data=res)


@router.get("/archived", response_model=ApiResponse[List[RouterResponse]])
async def list_archived_routers(db: AsyncSession = Depends(get_db)):
    """Routers the operator deleted with 'keep data'. Restorable or purgeable.

    Declared before ``/{router_id}`` so the literal path wins the match.
    """
    result = await db.execute(
        select(Router)
        .where(Router.archived_at.is_not(None))
        .order_by(Router.archived_at.desc())
    )
    return ApiResponse(data=[RouterResponse.model_validate(r) for r in result.scalars().all()])


@router.get("/{router_id}", response_model=ApiResponse[RouterResponse])
async def get_router(
    router_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get single router details."""
    router_obj = await db.get(Router, router_id)
    if not router_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Router not found")

    item = RouterResponse.model_validate(router_obj)
    try:
        client = await router_manager.get_client(router_id, session=db)
        if client:
            res = await client.get_system_resource()
            item.is_online = True
            item.ros_version = res.version
            item.board_name = res.board_name
            item.cpu_load = res.cpu_load
    except Exception:
        item.is_online = False

    return ApiResponse(data=item)


@router.put("/{router_id}", response_model=ApiResponse[RouterResponse])
async def update_router(
    router_id: int,
    payload: RouterUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Update router configuration."""
    router_obj = await db.get(Router, router_id)
    if not router_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Router not found")

    update_data = payload.model_dump(exclude_unset=True)

    if update_data.get("is_default"):
        await db.execute(update(Router).where(Router.id != router_id).values(is_default=False))

    for key, value in update_data.items():
        setattr(router_obj, key, value)

    await db.commit()
    await db.refresh(router_obj)

    # Invalidate cached client to force re-creation with updated credentials
    await router_manager.remove_client(router_id)

    return ApiResponse(
        data=RouterResponse.model_validate(router_obj),
        message="Router updated successfully"
    )


@router.delete("/{router_id}", response_model=ApiResponse[bool])
async def delete_router(
    router_id: int,
    payload: RouterDeleteRequest = RouterDeleteRequest(),
    db: AsyncSession = Depends(get_db)
):
    """Remove a router.

    ``mode=archive`` (default) hides it and stops every loop from touching it,
    but keeps every user, device, rollup, metric and setting so the same box
    can be re-added later - by serial - with its history intact.
    ``mode=purge`` deletes the router and all of that data permanently.
    """
    router_obj = await db.get(Router, router_id)
    if not router_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Router not found")

    if payload.mode == "purge":
        counts = await router_lifecycle.purge_router(db, router_obj)
        await db.commit()
        await router_manager.remove_client(router_id)
        removed = ", ".join(f"{v} {k}" for k, v in counts.items() if v) or "no attached data"
        return ApiResponse(data=True, message=f"Router purged permanently ({removed}).")

    await router_lifecycle.archive_router(db, router_obj)
    await db.commit()
    await router_manager.remove_client(router_id)
    return ApiResponse(
        data=True,
        message="Router archived. Add it again to restore its users, devices and history.",
    )


@router.post("/{router_id}/restore", response_model=ApiResponse[RouterResponse])
async def restore_router_endpoint(
    router_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Bring an archived router back exactly as it was."""
    router_obj = await db.get(Router, router_id)
    if not router_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Router not found")
    if router_obj.archived_at is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Router is not archived")

    await router_lifecycle.restore_router(db, router_obj, None)
    await db.commit()
    await db.refresh(router_obj)
    await router_manager.remove_client(router_id)
    return ApiResponse(
        data=RouterResponse.model_validate(router_obj),
        message=f"'{router_obj.name}' restored.",
    )


@router.post("/{router_id}/change", response_model=ApiResponse[RouterResponse])
async def change_router_hardware(
    router_id: int,
    payload: RouterChangeRequest,
    db: AsyncSession = Depends(get_db)
):
    """Swap the physical router behind this row, keeping everything attached.

    The new connection details are tested first; the old router is never
    contacted, so this works when it is already dead. All users, devices,
    traffic history and per-router settings stay on the row. ``history_mode``
    decides the fate of the previous hardware's CPU / temperature / interface
    graphs.
    """
    router_obj = await db.get(Router, router_id)
    if not router_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Router not found")

    probe = await router_manager.test_connection(
        RouterTestConnectionRequest(
            host=payload.host, port=payload.port, use_ssl=payload.use_ssl,
            ssl_verify=payload.ssl_verify, username=payload.username,
            password=payload.password,
        )
    )
    if not probe.success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not reach the new router: {probe.message or 'connection failed'}",
        )

    # A different serial that already belongs to another live router would mean
    # two rows pointing at one box.
    if probe.serial_number:
        clash = await db.execute(
            select(Router).where(
                Router.serial_number == probe.serial_number,
                Router.id != router_id,
                Router.archived_at.is_(None),
            )
        )
        other = clash.scalars().first()
        if other is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"That router is already managed as '{other.name}'.",
            )

    router_obj.host = payload.host
    router_obj.port = payload.port
    router_obj.use_ssl = payload.use_ssl
    router_obj.ssl_verify = payload.ssl_verify
    router_obj.username = payload.username
    router_obj.password = payload.password
    router_obj.serial_number = probe.serial_number
    router_obj.is_active = True
    if payload.name:
        router_obj.name = payload.name
    if payload.comment is not None:
        router_obj.comment = payload.comment

    reset = {}
    if payload.history_mode == "reset_hardware":
        reset = await router_lifecycle.reset_hardware_history(db, router_id)

    await db.commit()
    await db.refresh(router_obj)
    await router_manager.remove_client(router_id)

    wiped = ", ".join(f"{v} {k}" for k, v in reset.items() if v)
    msg = f"'{router_obj.name}' now points at the new router; users, devices and traffic history kept."
    if wiped:
        msg += f" Cleared hardware graphs ({wiped})."
    return ApiResponse(data=RouterResponse.model_validate(router_obj), message=msg)


@router.post("/{router_id}/activate", response_model=ApiResponse[RouterResponse])
async def set_active_router(
    router_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Set specified router as default / active."""
    router_obj = await db.get(Router, router_id)
    if not router_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Router not found")

    await db.execute(update(Router).values(is_default=False))
    router_obj.is_default = True
    router_obj.is_active = True
    await db.commit()
    await db.refresh(router_obj)

    return ApiResponse(
        data=RouterResponse.model_validate(router_obj),
        message=f"'{router_obj.name}' set as active router"
    )


@router.post("/{router_id}/provision-ssl", response_model=ApiResponse[RouterProvisionSslResponse])
async def provision_router_ssl(
    router_id: int,
    payload: RouterProvisionSslRequest = RouterProvisionSslRequest(),
    db: AsyncSession = Depends(get_db)
):
    """
    Generate a TLS certificate on MikroTik RouterOS, enable the www-ssl
    service, and repoint the MikroMan connection to HTTPS on the port the
    router's www-ssl service already uses. The router's port config is
    left untouched.
    """
    res = await router_manager.provision_ssl_for_router(router_id, payload, session=db)
    if not res.success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=res.message
        )
    return ApiResponse(data=res, message=res.message)


@router.post("/test-provision-ssl", response_model=ApiResponse[RouterProvisionSslResponse])
async def test_and_provision_ssl(
    payload: RouterTestConnectionRequest
):
    """
    Directly provision SSL on an unconfigured/unregistered MikroTik router over
    HTTP, enabling www-ssl before first save. The service's configured port is
    read and returned, never changed.
    """
    client = RouterOSClient(
        host=payload.host,
        port=payload.port,
        use_ssl=payload.use_ssl,
        username=payload.username,
        password=payload.password,
        timeout=8.0
    )
    try:
        prov = await client.provision_ssl()
        return ApiResponse(
            data=RouterProvisionSslResponse(
                success=prov.get("success", False),
                message=prov.get("message", "SSL setup complete"),
                certificate=prov.get("certificate"),
                port=prov.get("port", 443)
            )
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to auto-configure SSL on router: {str(e)}"
        )
    finally:
        await client.aclose()


@router.post("/test-certificates", response_model=ApiResponse[List[RouterCertificateDTO]])
async def test_list_certificates(payload: RouterTestConnectionRequest):
    """List all available certificates on the router before registration."""
    client = RouterOSClient(
        host=payload.host,
        port=payload.port,
        use_ssl=payload.use_ssl,
        username=payload.username,
        password=payload.password,
        timeout=5.0
    )
    try:
        certs = await client.list_certificates()
        return ApiResponse(data=[RouterCertificateDTO(**c) for c in certs])
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to fetch certificates: {str(e)}")
    finally:
        await client.aclose()


@router.post("/test-bind-certificate", response_model=ApiResponse[RouterProvisionSslResponse])
async def test_bind_certificate(
    cert_req: RouterBindCertRequest,
    conn: RouterTestConnectionRequest
):
    """Bind an existing router certificate to www-ssl during initial setup."""
    client = RouterOSClient(
        host=conn.host,
        port=conn.port,
        use_ssl=conn.use_ssl,
        username=conn.username,
        password=conn.password,
        timeout=5.0
    )
    try:
        res = await client.bind_ssl_certificate(cert_name=cert_req.certificate_name)
        if not res.get("success"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=res.get("message"))
        return ApiResponse(
            data=RouterProvisionSslResponse(
                success=True,
                certificate=cert_req.certificate_name,
                port=res.get("port", 443),
                message=res.get("message", "Certificate bound successfully")
            )
        )
    finally:
        await client.aclose()


@router.post("/test-upload-certificate", response_model=ApiResponse[RouterProvisionSslResponse])
async def test_upload_certificate(
    upload_req: RouterUploadCertRequest,
    conn: RouterTestConnectionRequest
):
    """Upload custom PEM certificate and key to router during initial setup."""
    client = RouterOSClient(
        host=conn.host,
        port=conn.port,
        use_ssl=conn.use_ssl,
        username=conn.username,
        password=conn.password,
        timeout=8.0
    )
    try:
        res = await client.import_custom_certificate(
            cert_content=upload_req.cert_content,
            key_content=upload_req.key_content,
            cert_name=upload_req.cert_name,
            passphrase=upload_req.passphrase,
        )
        if not res.get("success"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=res.get("message"))
        return ApiResponse(
            data=RouterProvisionSslResponse(
                success=True,
                certificate=res.get("certificate", upload_req.cert_name),
                port=res.get("port", 443),
                message=res.get("message", "Certificate uploaded and enabled")
            )
        )
    finally:
        await client.aclose()


@router.get("/{router_id}/certificates", response_model=ApiResponse[List[RouterCertificateDTO]])
async def get_router_certificates(
    router_id: int,
    db: AsyncSession = Depends(get_db)
):
    """List certificates installed on the registered router."""
    client = await router_manager.get_client(router_id, session=db)
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Router not connected")
    certs = await client.list_certificates()
    return ApiResponse(data=[RouterCertificateDTO(**c) for c in certs])



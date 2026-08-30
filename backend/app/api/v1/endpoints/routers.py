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
    RouterCreate,
    RouterProvisionSslRequest,
    RouterProvisionSslResponse,
    RouterResponse,
    RouterTestConnectionRequest,
    RouterTestConnectionResponse,
    RouterUpdate,
    RouterUploadCertRequest,
)
from backend.app.services.router_manager import router_manager
from backend.app.services.routeros import RouterOSClient

logger = logging.getLogger("mikroman.api.routers")

router = APIRouter(prefix="/routers", tags=["Routers"])


@router.get("", response_model=ApiResponse[List[RouterResponse]])
async def list_routers(
    db: AsyncSession = Depends(get_db)
):
    """List all registered MikroTik routers."""
    result = await db.execute(select(Router).order_by(Router.is_default.desc(), Router.id.asc()))
    routers = list(result.scalars().all())

    # Build response with live status check if active
    response_items = []
    for r in routers:
        item = RouterResponse.model_validate(r)
        if r.is_active:
            try:
                client = await router_manager.get_client(r.id, session=db)
                if client:
                    res = await client.get_system_resource()
                    item.is_online = True
                    item.ros_version = res.version
                    item.board_name = res.board_name or res.model
                    item.model = res.model
                    item.architecture = res.architecture_name
                    item.cpu_load = res.cpu_load
                else:
                    item.is_online = False
            except Exception:
                item.is_online = False
        else:
            item.is_online = False
        response_items.append(item)

    return ApiResponse(data=response_items)


@router.post("", response_model=ApiResponse[RouterResponse], status_code=status.HTTP_201_CREATED)
async def create_router(
    payload: RouterCreate,
    db: AsyncSession = Depends(get_db)
):
    """Register a new MikroTik router."""
    # Check if name already exists
    existing = await db.execute(select(Router).where(Router.name == payload.name))
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Router with name '{payload.name}' already exists"
        )

    # If first router, automatically make it default
    count_res = await db.execute(select(Router))
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
    db: AsyncSession = Depends(get_db)
):
    """Delete a router."""
    router_obj = await db.get(Router, router_id)
    if not router_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Router not found")

    await db.delete(router_obj)
    await db.commit()

    # If deleted router was default, assign default to first remaining router
    remaining = await db.execute(select(Router).order_by(Router.id.asc()))
    rem_list = remaining.scalars().all()
    if rem_list and not any(r.is_default for r in rem_list):
        rem_list[0].is_default = True
        await db.commit()

    await router_manager.remove_client(router_id)
    return ApiResponse(data=True, message="Router deleted successfully")


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
    Generate TLS certificate on MikroTik RouterOS, enable www-ssl service,
    and update MikroMan router connection to HTTPS (port 443).
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
    Directly provision SSL on an unconfigured/unregistered MikroTik router over HTTP,
    enabling www-ssl on port 443 before first save.
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
        res = await client.bind_ssl_certificate(cert_name=cert_req.certificate_name, port=cert_req.port)
        if not res.get("success"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=res.get("message"))
        return ApiResponse(
            data=RouterProvisionSslResponse(
                success=True,
                certificate=cert_req.certificate_name,
                port=cert_req.port,
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
            port=upload_req.port
        )
        if not res.get("success"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=res.get("message"))
        return ApiResponse(
            data=RouterProvisionSslResponse(
                success=True,
                certificate=res.get("certificate", upload_req.cert_name),
                port=upload_req.port,
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



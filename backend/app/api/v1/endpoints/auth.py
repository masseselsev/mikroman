"""Authentication endpoints for login, setup, status, and logout."""
import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    create_csrf_token,
    create_session_token,
    get_stored_admin_hash,
    hash_password,
    is_admin_password_configured,
    is_secure_request,
    set_stored_admin_hash,
    verify_password,
    verify_session_or_api_key,
    verify_session_token,
)
from backend.app.core.config import settings
from backend.app.db.session import get_db
from backend.app.schemas.auth import (
    AuthStatusResponse,
    LoginRequest,
    LoginResponseData,
    SetupRequest,
)
from backend.app.schemas.common import APIResponse

logger = logging.getLogger("mikroman.auth")
router = APIRouter(prefix="/auth", tags=["Authentication"])


def _set_auth_cookies(
    response: Response,
    request: Request,
    session_token: str,
    csrf_token: str,
) -> None:
    """Set hardened session and CSRF cookies on the HTTP response."""
    secure_flag = is_secure_request(request)
    max_age = settings.SESSION_EXPIRE_DAYS * 86400

    # HttpOnly session cookie - inaccessible to JS, protected against XSS
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=max_age,
        expires=max_age,
        path="/",
        secure=secure_flag,
        httponly=True,
        samesite="lax",
    )
    # Non-HttpOnly CSRF token cookie - read by frontend JS and sent in X-CSRF-Token header
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=max_age,
        expires=max_age,
        path="/",
        secure=secure_flag,
        httponly=False,
        samesite="lax",
    )


def _clear_auth_cookies(response: Response, request: Request) -> None:
    """Expire session and CSRF cookies on logout."""
    secure_flag = is_secure_request(request)
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=secure_flag,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        key=CSRF_COOKIE_NAME,
        path="/",
        secure=secure_flag,
        httponly=False,
        samesite="lax",
    )


@router.get("/status", response_model=APIResponse[AuthStatusResponse])
async def get_auth_status(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Report current authentication enforcement, setup status, and session validity."""
    if not settings.AUTH_ENABLED:
        return APIResponse(
            data=AuthStatusResponse(
                auth_enabled=False,
                authenticated=True,
                needs_setup=False,
                username="admin",
            )
        )

    configured = await is_admin_password_configured(db)

    # Check for active session or Bearer token
    user = None
    auth_header = request.headers.get("Authorization")
    api_key_header = request.headers.get("X-API-Key")
    if auth_header and auth_header.lower().startswith("bearer "):
        user = verify_session_or_api_key(auth_header[7:].strip())
    elif api_key_header:
        user = verify_session_or_api_key(api_key_header.strip())
    elif request.cookies.get(SESSION_COOKIE_NAME):
        user = verify_session_token(request.cookies.get(SESSION_COOKIE_NAME))

    # Ensure frontend has a CSRF cookie on initial load
    if not request.cookies.get(CSRF_COOKIE_NAME):
        csrf_token = create_csrf_token()
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=csrf_token,
            max_age=settings.SESSION_EXPIRE_DAYS * 86400,
            expires=settings.SESSION_EXPIRE_DAYS * 86400,
            path="/",
            secure=is_secure_request(request),
            httponly=False,
            samesite="lax",
        )

    return APIResponse(
        data=AuthStatusResponse(
            auth_enabled=True,
            authenticated=bool(user),
            needs_setup=not configured,
            username=user["sub"] if user else None,
        )
    )


@router.post("/setup", response_model=APIResponse[LoginResponseData])
async def setup_admin_password(
    payload: SetupRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Set the initial admin password. Can only be executed once when needs_setup is True."""
    if await is_admin_password_configured(db):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin password is already configured",
        )

    hashed = hash_password(payload.password)
    await set_stored_admin_hash(db, hashed)
    logger.info("Admin password configured successfully via first-run setup.")

    session_token = create_session_token(username="admin")
    csrf_token = create_csrf_token()
    _set_auth_cookies(response, request, session_token, csrf_token)

    return APIResponse(
        message="Admin password configured successfully",
        data=LoginResponseData(
            username="admin",
            expires_in=settings.SESSION_EXPIRE_DAYS * 86400,
        ),
    )


@router.post("/login", response_model=APIResponse[LoginResponseData])
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate with admin password, returning session and CSRF cookies."""
    configured = await is_admin_password_configured(db)
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin password not configured; please complete setup first",
        )

    valid = False
    # 1. Environment variable override
    if settings.ADMIN_PASSWORD and hmac.compare_digest(payload.password, settings.ADMIN_PASSWORD):
        valid = True
    else:
        # 2. Database hash
        stored_hash = await get_stored_admin_hash(db)
        if stored_hash and verify_password(payload.password, stored_hash):
            valid = True

    if not valid:
        # Constant-time dummy verification if needed, or return generic 401
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    session_token = create_session_token(username="admin")
    csrf_token = create_csrf_token()
    _set_auth_cookies(response, request, session_token, csrf_token)

    return APIResponse(
        message="Authenticated successfully",
        data=LoginResponseData(
            username="admin",
            expires_in=settings.SESSION_EXPIRE_DAYS * 86400,
        ),
    )


@router.post("/logout", response_model=APIResponse[None])
async def logout(
    request: Request,
    response: Response,
):
    """Log out by clearing session and CSRF cookies."""
    _clear_auth_cookies(response, request)
    return APIResponse(message="Logged out successfully", data=None)

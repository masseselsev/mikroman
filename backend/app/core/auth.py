"""Authentication and session security core primitives for MikroMan.

Engineering constraints:
1. Zero database write wear on RouterOS NAND flash:
   Session cookies are stateless Fernet-encrypted tokens signed using the
   existing master key cipher from `core/secrets.py`. Session verification runs
   in memory with zero SQLite writes per request.
2. Defense-in-depth:
   - `mikroman_session`: HttpOnly cookie (inaccessible to JavaScript, immune to XSS theft).
   - `mikroman_csrf`: non-HttpOnly cookie validated against `X-CSRF-Token` header on mutating methods.
   - Programmatic access via `Authorization: Bearer <token>` or `X-API-Key: <token>`.
   - PBKDF2-HMAC-SHA256 password hashing (600,000 rounds) using Python standard library.
"""
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Optional

from cryptography.fernet import InvalidToken
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.core.secrets import get_cipher
from backend.app.db.models import AppSetting
from backend.app.db.session import get_db

logger = logging.getLogger("mikroman.auth")

SESSION_COOKIE_NAME = "mikroman_session"
CSRF_COOKIE_NAME = "mikroman_csrf"
ADMIN_PASSWORD_SETTING_KEY = "admin_password_hash"
PBKDF2_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    """Hash a password using salted PBKDF2-HMAC-SHA256 with 600,000 rounds."""
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${derived.hex()}"


def verify_password(plain_password: str, stored_hash: str) -> bool:
    """Verify a plain password against a stored PBKDF2 hash using constant-time comparison."""
    if not stored_hash or not plain_password:
        return False
    try:
        parts = stored_hash.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            return False
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected_hex = parts[3]
        derived = hashlib.pbkdf2_hmac(
            "sha256",
            plain_password.encode("utf-8"),
            salt,
            iterations,
        )
        return hmac.compare_digest(derived.hex(), expected_hex)
    except Exception as e:
        logger.warning(f"Password verification error: {e}")
        return False


def create_session_token(username: str = "admin", expires_days: Optional[int] = None) -> str:
    """Generate a tamper-proof stateless session token encrypted with the app's master cipher."""
    if expires_days is None:
        expires_days = settings.SESSION_EXPIRE_DAYS
    now = int(time.time())
    payload = {
        "sub": username,
        "iat": now,
        "exp": now + (expires_days * 86400),
        "nonce": secrets.token_hex(8),
    }
    raw = json.dumps(payload).encode("utf-8")
    return get_cipher().encrypt(raw).decode("utf-8")


def verify_session_token(token: str) -> Optional[dict]:
    """Decrypt and validate a stateless session token.

    Returns the decoded payload dict if valid and non-expired, or None.
    Runs entirely in memory with zero disk access.
    """
    if not token:
        return None
    try:
        raw = get_cipher().decrypt(token.encode("utf-8"))
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except (InvalidToken, ValueError, Exception):
        return None


def verify_session_or_api_key(token: Optional[str]) -> Optional[dict]:
    """Validate a token either as a configured API key or as a Fernet session token."""
    if not token:
        return None
    # Check configured API key (constant-time comparison)
    if settings.API_KEY and hmac.compare_digest(token, settings.API_KEY):
        return {"sub": "api_key", "role": "admin"}
    # Check Fernet session token
    return verify_session_token(token)


def create_csrf_token() -> str:
    """Generate a cryptographically random token for double-submit CSRF defense."""
    return secrets.token_urlsafe(32)


def verify_csrf_token(header_token: Optional[str], cookie_token: Optional[str]) -> bool:
    """Constant-time verification of double-submit CSRF tokens."""
    if not header_token or not cookie_token:
        return False
    return hmac.compare_digest(header_token, cookie_token)


def is_secure_request(request: Request) -> bool:
    """Detect whether request came over HTTPS directly or through a reverse proxy."""
    if request.url.scheme == "https":
        return True
    forwarded_proto = request.headers.get("x-forwarded-proto", "").lower()
    return forwarded_proto == "https"


async def get_stored_admin_hash(session: AsyncSession) -> Optional[str]:
    """Retrieve the stored admin password hash from AppSetting."""
    setting = await session.get(AppSetting, ADMIN_PASSWORD_SETTING_KEY)
    return setting.value if setting and setting.value else None


async def set_stored_admin_hash(session: AsyncSession, password_hash: str) -> None:
    """Persist the admin password hash to AppSetting."""
    setting = await session.get(AppSetting, ADMIN_PASSWORD_SETTING_KEY)
    if setting:
        setting.value = password_hash
    else:
        session.add(AppSetting(
            key=ADMIN_PASSWORD_SETTING_KEY,
            value=password_hash,
            description="PBKDF2 hash of admin password",
        ))
    await session.commit()


async def is_admin_password_configured(session: AsyncSession) -> bool:
    """Check if admin password is configured via environment variable or database."""
    if settings.ADMIN_PASSWORD:
        return True
    stored = await get_stored_admin_hash(session)
    return bool(stored)


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """FastAPI dependency enforcing authentication and double-submit CSRF.

    - If AUTH_ENABLED is False, bypasses authentication.
    - If Authorization: Bearer or X-API-Key is supplied, validates without CSRF.
    - If mikroman_session cookie is supplied, validates session and enforces CSRF on mutating methods.
    - Raises 401 Unauthorized or 403 Forbidden on failure.
    """
    if not settings.AUTH_ENABLED:
        return {"sub": "admin", "role": "admin"}

    # 1. Bearer / X-API-Key token
    auth_header = request.headers.get("Authorization")
    api_key_header = request.headers.get("X-API-Key")
    token = None
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
    elif api_key_header:
        token = api_key_header.strip()

    if token:
        user = verify_session_or_api_key(token)
        if user:
            return user
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API token",
        )

    # 2. Session cookie
    session_cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_cookie:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    user = verify_session_token(session_cookie)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid",
        )

    # Mutating requests require CSRF match when using cookie session
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        csrf_header = request.headers.get("X-CSRF-Token")
        csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME)
        if not verify_csrf_token(csrf_header, csrf_cookie):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF token validation failed",
            )

    return user

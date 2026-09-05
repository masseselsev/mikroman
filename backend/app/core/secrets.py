"""Encryption for the secrets MikroMan has to keep in its own database.

``routers.password`` is a live RouterOS credential and it was stored in plain
text. That matters because the database file *travels*: `scripts/backup.sh`
snapshots it, people copy `data/` between machines, and a single shared
`app.db` hands over full control of the router it describes. Encrypting the
column does not protect against an attacker who has both the database and the
key file, but it does mean a stray copy of `app.db` on its own is inert.

Key resolution, in order:

1. ``MIKROMAN_SECRET_KEY`` - a urlsafe-base64 32-byte Fernet key. Set this when
   the database and the key must live apart (the usual production answer).
2. ``<data dir>/.secret_key`` - generated on first run with mode 0600.

Values are stored as ``enc:v1:<fernet token>``. Anything without that prefix is
returned unchanged, so a database written by an older build keeps working and
is re-encrypted the next time the row is saved.
"""
import base64
import logging
import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("mikroman.secrets")

PREFIX = "enc:v1:"
_ENV_KEY = "MIKROMAN_SECRET_KEY"
_KEY_FILENAME = ".secret_key"

_fernet: Optional[Fernet] = None


def _data_dir() -> Path:
    """Where the key file lives: alongside the database it protects."""
    explicit = os.environ.get("MIKROMAN_DATA_DIR")
    if explicit:
        return Path(explicit)
    # The container mounts /data; a source checkout uses ./data.
    container = Path("/data")
    if container.is_dir():
        return container
    return Path(__file__).resolve().parents[3] / "data"


def _load_or_create_key() -> bytes:
    env_key = os.environ.get(_ENV_KEY, "").strip()
    if env_key:
        return env_key.encode()

    directory = _data_dir()
    key_path = directory / _KEY_FILENAME
    if key_path.exists():
        return key_path.read_bytes().strip()

    directory.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    # Written before the chmod, so narrow the umask for the create itself.
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    logger.info(f"Generated a new secret key at {key_path} (mode 0600)")
    return key


def get_cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def reset_cipher_cache() -> None:
    """Forget the cached key. Only tests need this."""
    global _fernet
    _fernet = None


def is_encrypted(value: Optional[str]) -> bool:
    return bool(value) and str(value).startswith(PREFIX)


def encrypt_secret(plain: Optional[str]) -> Optional[str]:
    """Encrypt a secret for storage. Empty values and already-encrypted values
    pass through untouched, so this is safe to apply on every write."""
    if plain is None or plain == "":
        return plain
    if is_encrypted(plain):
        return plain
    token = get_cipher().encrypt(str(plain).encode())
    return PREFIX + token.decode()


def decrypt_secret(stored: Optional[str]) -> Optional[str]:
    """Read a stored secret.

    A value without the marker is legacy plain text and is returned as-is - that
    is what lets an existing database keep connecting after an upgrade. A value
    that carries the marker but will not decrypt means the key changed or is
    missing; that is logged and answered with an empty string rather than an
    exception, so the app reports "cannot log in" instead of failing to start.
    """
    if not stored or not is_encrypted(stored):
        return stored
    try:
        return get_cipher().decrypt(stored[len(PREFIX):].encode()).decode()
    except (InvalidToken, ValueError, base64.binascii.Error) as e:
        logger.error(
            f"Stored secret could not be decrypted ({e.__class__.__name__}). "
            f"The key in {_ENV_KEY} or {_data_dir() / _KEY_FILENAME} does not match "
            f"the one this value was written with."
        )
        return ""

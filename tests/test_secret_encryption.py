"""Router credentials must not sit in the database as plain text.

The database file is the thing that leaves the machine - `scripts/backup.sh`
snapshots it, people copy `data/` around, a volume gets handed to someone to
look at. A plain-text `routers.password` in that file is a working key to the
router it describes.
"""
import os
import sqlite3

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.core import secrets as secrets_mod
from backend.app.core.secrets import PREFIX, decrypt_secret, encrypt_secret, is_encrypted
from backend.app.db.models import Base, Router


@pytest.fixture(autouse=True)
def _fixed_key(monkeypatch):
    """A known key, so the tests never touch the real data directory."""
    monkeypatch.setenv("MIKROMAN_SECRET_KEY", Fernet.generate_key().decode())
    secrets_mod.reset_cipher_cache()
    yield
    secrets_mod.reset_cipher_cache()


@pytest_asyncio.fixture
async def db(tmp_path):
    """A real file-backed SQLite database, so the raw bytes can be inspected."""
    path = tmp_path / "app.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield maker, path, engine
    await engine.dispose()


def test_a_secret_round_trips():
    token = encrypt_secret("hunter2")
    assert is_encrypted(token)
    assert token != "hunter2"
    assert decrypt_secret(token) == "hunter2"


def test_encrypting_twice_does_not_double_wrap():
    once = encrypt_secret("hunter2")
    assert encrypt_secret(once) == once


def test_empty_values_pass_through():
    # An empty password means "no credentials supplied" and must stay
    # distinguishable from an encrypted empty string.
    assert encrypt_secret("") == ""
    assert encrypt_secret(None) is None
    assert decrypt_secret("") == ""


def test_a_legacy_plain_value_is_still_readable():
    # An existing database keeps connecting after the upgrade.
    assert decrypt_secret("plain-old-password") == "plain-old-password"


def test_a_value_written_with_a_different_key_fails_closed(monkeypatch):
    token = encrypt_secret("hunter2")
    monkeypatch.setenv("MIKROMAN_SECRET_KEY", Fernet.generate_key().decode())
    secrets_mod.reset_cipher_cache()
    # Empty, not an exception: the app should say "cannot log in", not refuse
    # to start.
    assert decrypt_secret(token) == ""


@pytest.mark.asyncio
async def test_the_password_is_ciphertext_on_disk_but_plain_in_the_model(db):
    maker, path, _engine = db
    async with maker() as session:
        session.add(Router(name="Core", host="192.168.88.1", username="api", password="s3cret"))
        await session.commit()

    # What the ORM hands back is the usable credential...
    async with maker() as session:
        router = (await session.execute(select(Router))).scalars().one()
        assert router.password == "s3cret"

    # ...but what a copy of the file contains is not.
    raw = sqlite3.connect(path).execute("SELECT password FROM routers").fetchone()[0]
    assert raw != "s3cret"
    assert raw.startswith(PREFIX)
    assert "s3cret" not in open(path, "rb").read().decode("latin-1")


@pytest.mark.asyncio
async def test_startup_rewrites_a_legacy_plain_text_password(db, monkeypatch):
    maker, path, engine = db

    # Simulate a row written by an older build: bypass the column type.
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO routers (name, host, port, use_ssl, ssl_verify, username, password,"
            " is_active, is_default, created_at, updated_at) VALUES ('Old', '192.168.88.1', 443, 1, 0,"
            " 'api', 'legacy-pw', 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ))
    assert sqlite3.connect(path).execute(
        "SELECT password FROM routers"
    ).fetchone()[0] == "legacy-pw"

    # Point the migration helper at this database and run it.
    from backend.app.db import session as session_mod
    monkeypatch.setattr(session_mod, "engine", engine)
    rewritten = await session_mod.encrypt_legacy_secrets()

    assert rewritten == 1
    stored = sqlite3.connect(path).execute("SELECT password FROM routers").fetchone()[0]
    assert stored.startswith(PREFIX)

    # Still the same credential once read back through the model.
    async with maker() as s:
        assert (await s.execute(select(Router))).scalars().one().password == "legacy-pw"

    # Idempotent: a second pass has nothing left to do.
    assert await session_mod.encrypt_legacy_secrets() == 0


def test_the_key_file_is_created_private(tmp_path, monkeypatch):
    monkeypatch.delenv("MIKROMAN_SECRET_KEY", raising=False)
    monkeypatch.setenv("MIKROMAN_DATA_DIR", str(tmp_path))
    secrets_mod.reset_cipher_cache()

    encrypt_secret("anything")

    key_file = tmp_path / ".secret_key"
    assert key_file.exists()
    # Readable by its owner only - it sits next to the database it protects.
    assert oct(os.stat(key_file).st_mode & 0o777) == "0o600"

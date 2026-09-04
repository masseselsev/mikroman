from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.db.models import Base, Router, RouterBackup
from backend.app.schemas.backup import RouterBackupResponse, RouterBackupUpdate


def test_router_backup_model_and_relationship():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    router = Router(name="Core-GW", host="192.168.88.1", port=80)
    session.add(router)
    session.commit()

    backup = RouterBackup(
        router_id=router.id,
        outcome="changed",
        source="manual",
        fingerprint="a" * 64,
        rsc_content="/ip firewall filter add chain=input",
        rsc_bytes=35,
        backup_file_path="data/backups/1/1.backup",
        backup_bytes=1024,
        backup_password="secret-passphrase",
        is_pinned=True,
        note="Pre-upgrade",
        model="RB5009",
        os_version="7.15.2",
        duration_ms=450,
    )
    session.add(backup)
    session.commit()

    assert backup.id is not None
    assert backup.created_at is not None
    assert backup.router.name == "Core-GW"
    assert len(router.backups) == 1
    assert router.backups[0].fingerprint == "a" * 64

    # Test Pydantic serialization
    schema = RouterBackupResponse.model_validate(backup)
    assert schema.id == backup.id
    assert schema.outcome == "changed"
    assert schema.is_pinned is True
    assert schema.note == "Pre-upgrade"

    update_schema = RouterBackupUpdate(is_pinned=False, note="Updated note")
    assert update_schema.is_pinned is False

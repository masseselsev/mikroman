import os

from alembic import command
from alembic.config import Config


def test_alembic_upgrade_and_downgrade(tmp_path):
    test_db = tmp_path / "test_migration.db"
    alembic_cfg = Config("backend/alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{test_db}")

    # Run upgrade to head
    command.upgrade(alembic_cfg, "head")
    assert os.path.exists(test_db)

    # Run downgrade back to base
    command.downgrade(alembic_cfg, "base")

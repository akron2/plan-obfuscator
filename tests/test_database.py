from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from plan_obfuscator.config import Settings
from plan_obfuscator.database import create_database_engine


def test_sqlite_uses_single_user_rollback_journal(tmp_path: Path) -> None:
    engine = create_database_engine(Settings(data_dir=tmp_path))
    try:
        with engine.connect() as connection:
            assert connection.execute(text("PRAGMA journal_mode")).scalar_one().lower() == "delete"
            assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
            assert connection.execute(text("PRAGMA busy_timeout")).scalar_one() == 5000
    finally:
        engine.dispose()

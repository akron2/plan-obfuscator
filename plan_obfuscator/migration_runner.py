from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from .config import Settings

SCRIPT_LOCATION = "plan_obfuscator:migrations"


def migration_config(settings: Settings) -> Config:
    config = Config()
    config.set_main_option("script_location", SCRIPT_LOCATION)
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    return config


def stored_revision(database_path: Path) -> str | None:
    if not database_path.is_file():
        return None
    connection = sqlite3.connect(database_path)
    try:
        table = connection.execute(
            "select name from sqlite_master where type='table' and name='alembic_version'"
        ).fetchone()
        if table is None:
            has_tables = connection.execute(
                "select 1 from sqlite_master where type='table' limit 1"
            ).fetchone()
            return "" if has_tables else None
        row = connection.execute("select version_num from alembic_version").fetchone()
    finally:
        connection.close()
    return row[0] if row else ""


def set_aside_incompatible_database(settings: Settings, config: Config) -> Path | None:
    revision = stored_revision(settings.database_path)
    if revision is None:
        return None
    known = {item.revision for item in ScriptDirectory.from_config(config).walk_revisions()}
    if revision in known:
        return None
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    backup = settings.database_path.with_name(
        f"{settings.database_path.stem}.incompatible-{timestamp}{settings.database_path.suffix}"
    )
    settings.database_path.replace(backup)
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = settings.database_path.with_name(settings.database_path.name + suffix)
        if sidecar.exists():
            sidecar.replace(backup.with_name(backup.name + suffix))
    print(
        f"Existing database uses an incompatible schema and was moved to {backup}. "
        "A new empty database will be created.",
        flush=True,
    )
    return backup


def upgrade_database(settings: Settings) -> None:
    settings.ensure_directories()
    config = migration_config(settings)
    set_aside_incompatible_database(settings, config)
    command.upgrade(config, "head")

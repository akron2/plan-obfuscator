from __future__ import annotations

from alembic import command
from alembic.config import Config

from .config import Settings


def upgrade_database(settings: Settings) -> None:
    settings.ensure_directories()
    config = Config()
    config.set_main_option("script_location", "plan_obfuscator:migrations")
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    command.upgrade(config, "head")

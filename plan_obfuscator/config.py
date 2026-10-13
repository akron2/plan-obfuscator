from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "PlanObfuscator"


def default_data_dir() -> Path:
    configured = os.getenv("PLAN_OBFUSCATOR_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_NAME

    return Path.home() / ".plan_obfuscator"


@dataclass(slots=True)
class Settings:
    data_dir: Path | None = None
    database_path: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8765
    open_browser: bool = True

    def __post_init__(self) -> None:
        if self.data_dir is None:
            self.data_dir = default_data_dir()
        else:
            self.data_dir = Path(self.data_dir).expanduser().resolve()

        if self.database_path is None:
            self.database_path = self.data_dir / "plan_obfuscator.db"
        else:
            self.database_path = Path(self.database_path).expanduser().resolve()

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

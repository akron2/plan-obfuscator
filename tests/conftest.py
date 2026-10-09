from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from plan_obfuscator.config import Settings
from plan_obfuscator.database import (
    create_database_engine,
    create_session_factory,
    initialize_schema,
)
from plan_obfuscator.web import create_app

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_text():
    def load(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    return load


@pytest.fixture
def db_factory(tmp_path: Path):
    settings = Settings(data_dir=tmp_path / "data")
    engine = create_database_engine(settings)
    initialize_schema(engine)
    factory = create_session_factory(engine)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture
def db_session(db_factory) -> Session:
    session = db_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(Settings(data_dir=tmp_path / "web-data"))
    with TestClient(app) as test_client:
        yield test_client

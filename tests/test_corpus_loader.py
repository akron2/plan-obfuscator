from __future__ import annotations

import json
from pathlib import Path

import pytest

from .corpus_loader import CORPUS_ROOT, load_corpus


def _temporary_corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "corpus.schema.json").write_bytes((CORPUS_ROOT / "corpus.schema.json").read_bytes())
    return root


def _expectations(cases: list[object] | None = None) -> dict[str, object]:
    return {
        "$schema": "../corpus.schema.json",
        "schemaVersion": 1,
        "suite": "test-suite",
        "source": "test fixture",
        "cases": cases or [],
    }


def test_discovers_unregistered_files_and_sentinels(tmp_path: Path) -> None:
    root = _temporary_corpus(tmp_path)
    user = root / "user"
    user.mkdir()
    (user / "query.sql").write_text(
        "select COLUMN_PRIVATE from TABLE_SECRET", encoding="utf-8"
    )
    cases = load_corpus(root)
    assert len(cases) == 1
    assert cases[0].case_id == "user/query.sql"
    assert cases[0].registered is False
    assert set(cases[0].replace) == {"COLUMN_PRIVATE", "TABLE_SECRET"}


def test_rejects_unknown_expectation_fields(tmp_path: Path) -> None:
    root = _temporary_corpus(tmp_path)
    suite = root / "suite"
    suite.mkdir()
    (suite / "case.sql").write_text("select VALUE_PRIVATE from dual", encoding="utf-8")
    manifest = {**_expectations(), "unexpected": True}
    (suite / "expectations.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="(?i)additional properties"):
        load_corpus(root)


def test_rejects_unregistered_file_without_sentinels(tmp_path: Path) -> None:
    root = _temporary_corpus(tmp_path)
    user = root / "user"
    user.mkdir()
    (user / "weak.sql").write_text(
        "select ordinary_column from ordinary_table", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="must contain PRIVATE/SECRET"):
        load_corpus(root)


def test_rejects_missing_and_escaping_paths(tmp_path: Path) -> None:
    root = _temporary_corpus(tmp_path)
    suite = root / "suite"
    suite.mkdir()
    case = {
        "file": "missing.sql",
        "kind": "sql",
        "replace": [],
        "keep": [],
        "minOccurrences": 1,
        "allowFindings": [],
        "tags": [],
    }
    manifest_path = suite / "expectations.json"
    manifest_path.write_text(json.dumps(_expectations([case])), encoding="utf-8")
    with pytest.raises(ValueError, match="does not exist"):
        load_corpus(root)

    manifest_path.write_text(
        json.dumps(_expectations([{**case, "file": "../outside.sql"}])), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="escapes its suite"):
        load_corpus(root)


def test_rejects_duplicate_suites_and_registrations(tmp_path: Path) -> None:
    root = _temporary_corpus(tmp_path)
    for name in ("one", "two"):
        directory = root / name
        directory.mkdir()
        (directory / "expectations.json").write_text(
            json.dumps(_expectations()), encoding="utf-8"
        )
    with pytest.raises(ValueError, match="Duplicate corpus suite"):
        load_corpus(root)

    for path in (root / "two").iterdir():
        path.unlink()
    (root / "two").rmdir()
    case_path = root / "one" / "case.sql"
    case_path.write_text("select VALUE_PRIVATE from dual", encoding="utf-8")
    duplicated = {
        "file": "case.sql",
        "kind": "sql",
        "replace": [],
        "keep": [],
        "minOccurrences": 1,
        "allowFindings": [],
        "tags": [],
    }
    (root / "one" / "expectations.json").write_text(
        json.dumps(_expectations([duplicated, duplicated])), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="registered more than once"):
        load_corpus(root)


def test_rejects_invalid_utf8(tmp_path: Path) -> None:
    root = _temporary_corpus(tmp_path)
    user = root / "user"
    user.mkdir()
    (user / "broken.sql").write_bytes(bytes((0xFF, 0xFE, 0xFD)))
    with pytest.raises(ValueError, match="not valid UTF-8"):
        load_corpus(root)

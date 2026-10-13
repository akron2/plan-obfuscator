from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator

SUPPORTED_EXTENSIONS = {".sql", ".txt", ".log", ".out"}
MAX_FILE_BYTES = 10 * 1024 * 1024
CORPUS_ROOT = Path(__file__).resolve().parent.parent / "test-corpus"


@dataclass(frozen=True, slots=True)
class CorpusCase:
    case_id: str
    suite: str
    relative_path: str
    path: Path
    source: str
    expected_kind: str | None
    replace: tuple[str, ...]
    keep: tuple[str, ...]
    min_occurrences: int
    allow_findings: tuple[str, ...]
    tags: tuple[str, ...]
    registered: bool


@dataclass(frozen=True, slots=True)
class RegisteredExpectation:
    suite: str
    suite_directory: Path
    expected: dict[str, object]


def _read_utf8(root: Path, path: Path) -> str:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"Corpus file exceeds 10 MiB: {path.relative_to(root).as_posix()}")
    try:
        return path.read_bytes().decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(
            f"Corpus file is not valid UTF-8: {path.relative_to(root).as_posix()}"
        ) from error


def _sentinel_values(source: str) -> set[str]:
    return {
        match.group(0)
        for match in re.finditer(
            r"[\w$#]*(?:PRIVATE|SECRET)[\w$#]*",
            source,
            flags=re.IGNORECASE,
        )
        if match.group(0)
    }


def _inside(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return child != parent


def load_corpus(root: Path = CORPUS_ROOT) -> list[CorpusCase]:
    root = root.resolve()
    schema = json.loads((root / "corpus.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    all_files = sorted(path for path in root.rglob("*") if path.is_file())
    expectation_paths = [path for path in all_files if path.name == "expectations.json"]
    registrations: dict[Path, RegisteredExpectation] = {}
    suite_directories: list[tuple[str, Path]] = []
    suite_names: set[str] = set()

    for manifest_path in expectation_paths:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        errors = sorted(validator.iter_errors(raw), key=lambda item: list(item.path))
        if errors:
            details = "; ".join(
                f"/{'/'.join(map(str, error.path))}: {error.message}" for error in errors
            )
            raise ValueError(f"{manifest_path.relative_to(root).as_posix()}: {details}")
        suite = raw["suite"]
        if suite in suite_names:
            raise ValueError(f"Duplicate corpus suite: {suite}")
        suite_names.add(suite)
        suite_directory = manifest_path.parent.resolve()
        suite_directories.append((suite, suite_directory))
        for expected in raw["cases"]:
            absolute_path = (suite_directory / expected["file"]).resolve()
            if not _inside(suite_directory, absolute_path):
                raise ValueError(f"{suite}: corpus path escapes its suite: {expected['file']}")
            if absolute_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                raise ValueError(f"{suite}: unsupported corpus extension: {expected['file']}")
            if absolute_path in registrations:
                raise ValueError(f"Corpus file is registered more than once: {expected['file']}")
            registrations[absolute_path] = RegisteredExpectation(
                suite=suite,
                suite_directory=suite_directory,
                expected=expected,
            )

    data_files = [
        path.resolve() for path in all_files if path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    data_file_set = set(data_files)
    for path, registration in registrations.items():
        if path not in data_file_set:
            raise ValueError(
                f"{registration.suite}: registered corpus file does not exist: "
                f"{registration.expected['file']}"
            )

    suite_directories.sort(key=lambda item: len(item[1].parts), reverse=True)
    cases: list[CorpusCase] = []
    case_ids: set[str] = set()
    for path in data_files:
        registration = registrations.get(path)
        containing_suite = next(
            (
                (suite, directory)
                for suite, directory in suite_directories
                if _inside(directory, path)
            ),
            None,
        )
        top_level = path.relative_to(root).parts[0]
        suite = registration.suite if registration else (
            containing_suite[0] if containing_suite else top_level
        )
        suite_directory = registration.suite_directory if registration else (
            containing_suite[1] if containing_suite else root / top_level
        )
        case_path = path.relative_to(suite_directory).as_posix()
        case_id = f"{suite}/{case_path}"
        if case_id in case_ids:
            raise ValueError(f"Duplicate corpus case ID: {case_id}")
        case_ids.add(case_id)
        source = _read_utf8(root, path)
        expected = registration.expected if registration else None
        automatic_replace = _sentinel_values(source)
        if expected is None and not automatic_replace:
            raise ValueError(
                f"{path.relative_to(root).as_posix()}: unregistered corpus file must contain "
                "PRIVATE/SECRET values or be added to expectations.json"
            )
        explicit_replace = expected.get("replace", []) if expected else []
        replace = tuple(dict.fromkeys([*explicit_replace, *sorted(automatic_replace)]))
        cases.append(
            CorpusCase(
                case_id=case_id,
                suite=suite,
                relative_path=path.relative_to(root).as_posix(),
                path=path,
                source=source,
                expected_kind=expected.get("kind") if expected else None,
                replace=replace,
                keep=tuple(expected.get("keep", [])) if expected else (),
                min_occurrences=int(expected.get("minOccurrences", 1)) if expected else 1,
                allow_findings=tuple(expected.get("allowFindings", [])) if expected else (),
                tags=tuple(expected.get("tags", [])) if expected else (),
                registered=expected is not None,
            )
        )
    if not cases:
        raise ValueError("Corpus contains no test files")
    return sorted(cases, key=lambda item: item.case_id)


CORPUS_CASES = load_corpus()


def corpus_case(path_suffix: str) -> CorpusCase:
    normalized = path_suffix.replace("\\", "/")
    matches = [item for item in CORPUS_CASES if item.relative_path.endswith(normalized)]
    if len(matches) != 1:
        raise ValueError(f"Expected one corpus case ending with {normalized}, found {len(matches)}")
    return matches[0]

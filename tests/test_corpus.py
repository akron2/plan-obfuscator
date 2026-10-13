from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from plan_obfuscator.parsing import (
    ArtifactKind,
    ParsedArtifact,
    parse_artifact,
    parse_prompt,
    split_materials,
)
from plan_obfuscator.parsing.utils import cell_bounds, trimmed_bounds
from plan_obfuscator.services.obfuscation import apply_replacements, exact_restore

from .corpus_loader import CORPUS_CASES, CorpusCase, corpus_case


def _obfuscate(parsed: ParsedArtifact):
    symbols: dict[tuple[str, str], SimpleNamespace] = {}
    replacements = []
    for occurrence in parsed.occurrences:
        key = (occurrence.entity_type.value, occurrence.canonical)
        symbol = symbols.setdefault(
            key,
            SimpleNamespace(
                marker=f"OBF_CRP_{occurrence.entity_type.value.upper()}_{len(symbols) + 1:04d}_AA"
            ),
        )
        replacements.append((occurrence, symbol))
    return apply_replacements(parsed.source_text, replacements)


def _identity_values(source: str) -> set[str]:
    values: set[str] = set()
    for pattern in (
        r"\bSQL(?:_| )ID\s*[:=]?\s+([a-z0-9]{8,})",
        r"\b(?:SQL )?Plan Hash Value\s*[:=]\s*(\d{6,})",
    ):
        values.update(match.group(1) for match in re.finditer(pattern, source, re.IGNORECASE))
    return values


def _assert_valid_spans(parsed: ParsedArtifact) -> None:
    previous_end = 0
    for occurrence in parsed.occurrences:
        assert occurrence.start >= previous_end
        assert occurrence.end > occurrence.start
        assert occurrence.end <= len(parsed.source_text)
        assert parsed.source_text[occurrence.start : occurrence.end] == occurrence.original
        previous_end = occurrence.end


@pytest.mark.parametrize("item", CORPUS_CASES, ids=lambda item: item.case_id)
def test_corpus_contract(item: CorpusCase) -> None:
    parsed = parse_artifact(item.source)
    unexpected_findings = [
        finding
        for finding in parsed.findings
        if finding.severity == "error"
        or (finding.severity == "warning" and finding.code not in item.allow_findings)
    ]
    assert unexpected_findings == []
    if item.expected_kind:
        assert parsed.kind.value == item.expected_kind
    else:
        assert parsed.kind != ArtifactKind.UNKNOWN
    assert len(parsed.occurrences) >= item.min_occurrences
    _assert_valid_spans(parsed)

    result = _obfuscate(parsed)
    sensitive = set(item.replace) | _identity_values(item.source)
    sensitive.update(
        match.group(0)
        for match in re.finditer(
            r"\b(?:SEL|SET|INS|UPD|DEL)\$[A-Z0-9_]+\b", item.source, re.IGNORECASE
        )
    )
    for value in sensitive:
        assert value in item.source, f"expected replacement is absent from source: {value}"
        assert value not in result.text, f"sensitive value leaked: {value}"
    for value in item.keep:
        assert value in result.text, f"preserved value changed: {value}"
    assert exact_restore(result.text, result.replacements) == item.source


SENSITIVE_HEADER = re.compile(
    r"(?:name|value|bind|object|\binst\b|instance|session|user|host|machine|process|"
    r"program|module|action|service|server|sql id|dbop|client)",
    re.IGNORECASE,
)
SAFE_HEADER = re.compile(
    r"^(?:id|operation|elapsed|time|rows|starts|cost|bytes|buffer|read|write|fetch|"
    r"activity|execs)",
    re.IGNORECASE,
)


def _metric_cells(parsed: ParsedArtifact) -> int:
    source = parsed.source_text
    lines = source.splitlines(keepends=True)
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line)
    checked = 0
    for line_index, header_line in enumerate(lines):
        if not header_line.lstrip().startswith("|"):
            continue
        headers = [
            " ".join(header_line[start:end].split()).lower()
            for start, end in cell_bounds(header_line)
        ]
        if len(headers) < 2 or not any(SAFE_HEADER.search(header) for header in headers):
            continue
        safe_columns = [
            index
            for index, header in enumerate(headers)
            if header and not SENSITIVE_HEADER.search(header)
        ]
        for row_index in range(line_index + 1, len(lines)):
            row = lines[row_index]
            if re.fullmatch(r"\s*[-=]+\s*", row):
                continue
            if not row.lstrip().startswith("|"):
                break
            bounds = cell_bounds(row)
            for column_index in safe_columns:
                if column_index >= len(bounds):
                    continue
                value_bounds = trimmed_bounds(row, *bounds[column_index])
                if value_bounds is None:
                    continue
                start = offsets[row_index] + value_bounds[0]
                end = offsets[row_index] + value_bounds[1]
                value = source[start:end]
                if not re.search(r"[\dA-Z]", value, re.IGNORECASE):
                    continue
                overlap = any(start < item.end and item.start < end for item in parsed.occurrences)
                assert not overlap, f"safe metric cell was masked: {value}"
                checked += 1
    return checked


def test_structured_metric_cells_are_not_masked() -> None:
    checked = 0
    for item in CORPUS_CASES:
        parsed = parse_artifact(item.source)
        if parsed.kind in {ArtifactKind.XPLAN, ArtifactKind.SQL_MONITOR}:
            checked += _metric_cells(parsed)
    assert checked > 250


def test_truncated_reports_fail_closed() -> None:
    variants = 0
    for item in CORPUS_CASES:
        parsed = parse_artifact(item.source)
        if parsed.kind not in {ArtifactKind.XPLAN, ArtifactKind.SQL_MONITOR}:
            continue
        for ratio in (0.25, 0.5, 0.75):
            source = item.source[: max(1, int(len(item.source) * ratio))]
            present_values = [value for value in item.replace if value in source]
            if not present_values:
                continue
            result = _obfuscate(parse_artifact(source))
            for value in present_values:
                assert value not in result.text, f"truncated report leaked: {value}"
            assert exact_restore(result.text, result.replacements) == source
            variants += 1
    assert variants > 40


def _generated_case(name: str) -> CorpusCase:
    return corpus_case(f"generated/oracle-21c/{name}")


def test_two_xplans_in_one_prompt() -> None:
    first = _generated_case("xplan-q1-index-basic.txt")
    second = _generated_case("xplan-q2-join-all.txt")
    source = f"{first.source}\nПлан второго child cursor:\n{second.source}"
    parsed = parse_prompt(source)
    result = _obfuscate(parsed)
    for value in {*first.replace, *second.replace}:
        assert value not in result.text
    assert exact_restore(result.text, result.replacements) == source


def test_fenced_sql_with_xplan_in_one_prompt() -> None:
    report = _generated_case("xplan-q1-index-basic.txt")
    source = (
        "Проверь отдельно запрос и его план:\n\n"
        "```sql\n"
        "select FENCED_COLUMN_PRIVATE from FENCED_TABLE_SECRET "
        "where FENCED_COLUMN_PRIVATE = 'FENCED_VALUE_PRIVATE'\n"
        "```\n\n"
        f"{report.source}"
    )
    parsed = parse_prompt(source)
    result = _obfuscate(parsed)
    for value in {
        "FENCED_COLUMN_PRIVATE",
        "FENCED_TABLE_SECRET",
        "FENCED_VALUE_PRIVATE",
        *report.replace,
    }:
        assert value not in result.text
    assert "Проверь отдельно запрос и его план" in result.text
    assert exact_restore(result.text, result.replacements) == source


def test_sql_monitor_and_xplan_in_one_prompt() -> None:
    monitor = _generated_case("monitor-q8-bind-all.txt")
    xplan = _generated_case("xplan-q3-partition-window.txt")
    source = f"{monitor.source}\nДополнительный execution plan:\n{xplan.source}"
    parsed = parse_prompt(source)
    result = _obfuscate(parsed)
    for value in {*monitor.replace, *xplan.replace}:
        assert value not in result.text
    for value in monitor.keep:
        assert value in result.text
    assert "991K (0)" in result.text
    assert exact_restore(result.text, result.replacements) == source


def test_two_sql_monitor_reports_in_one_prompt() -> None:
    first = _generated_case("monitor-q2-join-all.txt")
    second = _generated_case("monitor-q8-bind-all.txt")
    source = f"{first.source}\nСледующее выполнение:\n{second.source}"
    parsed = parse_prompt(source)
    result = _obfuscate(parsed)
    for value in {*first.replace, *second.replace}:
        assert value not in result.text
    for value in {*first.keep, *second.keep}:
        assert value in result.text
    assert exact_restore(result.text, result.replacements) == source


def test_split_materials_finds_fenced_sql_and_report() -> None:
    report = _generated_case("xplan-q1-index-basic.txt")
    source = (
        "Проверь:\n```sql\nselect COL_PRIVATE from TABLE_SECRET\n```\n"
        f"{report.source}"
    )
    materials = split_materials(source)
    assert [item.kind for item in materials] == [ArtifactKind.SQL, ArtifactKind.XPLAN]
    assert materials[0].text == "select COL_PRIVATE from TABLE_SECRET"
    assert materials[1].text.startswith("EXPLAINED SQL STATEMENT")

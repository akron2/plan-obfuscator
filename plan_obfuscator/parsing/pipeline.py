from __future__ import annotations

import re
from dataclasses import dataclass

from .detector import detect_artifact_kind
from .monitor import parse_sql_monitor
from .sql import parse_expression_fragment, parse_hint_fragment, parse_sql
from .types import ArtifactKind, EntityType, ParsedArtifact, ParseFinding, TextOccurrence
from .utils import make_occurrence
from .xplan import parse_xplan


def _parse_outline(text: str) -> ParsedArtifact:
    parsed = ParsedArtifact(kind=ArtifactKind.OUTLINE, source_text=text)
    for occurrence in parse_hint_fragment(text):
        parsed.add_occurrence(occurrence)
    parsed.finalize()
    return parsed


def _parse_predicates(text: str) -> ParsedArtifact:
    parsed = ParsedArtifact(kind=ArtifactKind.PREDICATES, source_text=text)
    matches = list(re.finditer(r"(?im)^\s*\d+\s*-\s*(?:access|filter)\(", text))
    if matches:
        for index, match in enumerate(matches):
            expression_start = match.end() - 1
            expression_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            fragment = text[expression_start:expression_end]
            for item in parse_expression_fragment(fragment, base_offset=expression_start):
                parsed.add_occurrence(item)
    else:
        for item in parse_expression_fragment(text):
            parsed.add_occurrence(item)
    parsed.finalize()
    return parsed


def _parse_unknown(text: str) -> ParsedArtifact:
    parsed = ParsedArtifact(kind=ArtifactKind.UNKNOWN, source_text=text)
    stripped_start = len(text) - len(text.lstrip())
    stripped_end = len(text.rstrip())
    occurrence = make_occurrence(
        text,
        stripped_start,
        stripped_end,
        EntityType.FRAGMENT,
        "unknown_artifact_fallback",
        confidence=0.5,
    )
    if occurrence:
        parsed.add_occurrence(occurrence)
    parsed.findings.append(
        ParseFinding(
            severity="warning",
            code="unknown_artifact_replaced",
            message="Unknown artifact format was conservatively replaced as one fragment.",
        )
    )
    parsed.finalize()
    return parsed


def parse_artifact(text: str, kind: ArtifactKind | str | None = None) -> ParsedArtifact:
    resolved = ArtifactKind(kind) if kind else detect_artifact_kind(text)
    if resolved == ArtifactKind.SQL:
        return parse_sql(text)
    if resolved == ArtifactKind.XPLAN:
        return parse_xplan(text)
    if resolved == ArtifactKind.SQL_MONITOR:
        return parse_sql_monitor(text)
    if resolved == ArtifactKind.OUTLINE:
        return _parse_outline(text)
    if resolved == ArtifactKind.PREDICATES:
        return _parse_predicates(text)
    return _parse_unknown(text)


def _shift_occurrence(item: TextOccurrence, offset: int, source: str) -> TextOccurrence:
    return TextOccurrence(
        start=item.start + offset,
        end=item.end + offset,
        entity_type=item.entity_type,
        original=source[item.start + offset : item.end + offset],
        canonical=item.canonical,
        provenance=item.provenance,
        confidence=item.confidence,
        quoting_context=item.quoting_context,
        priority=item.priority,
    )


def _append_region(target: ParsedArtifact, parsed: ParsedArtifact, offset: int) -> None:
    for occurrence in parsed.occurrences:
        target.add_occurrence(_shift_occurrence(occurrence, offset, target.source_text))
    for finding in parsed.findings:
        target.findings.append(
            ParseFinding(
                severity=finding.severity,
                code=finding.code,
                message=finding.message,
                start=finding.start + offset if finding.start is not None else None,
                end=finding.end + offset if finding.end is not None else None,
            )
        )
    target.extracted_sql.extend(parsed.extracted_sql)


def _sql_region_end(source: str) -> int:
    if re.match(r"^\s*(?:BEGIN|DECLARE)\b", source, re.IGNORECASE):
        return len(source)
    depth = 0
    index = 0
    quote = ""
    while index < len(source):
        character = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if quote:
            if character == quote:
                if following == quote:
                    index += 2
                    continue
                quote = ""
            index += 1
            continue
        if character in {"'", '"'}:
            quote = character
            index += 1
            continue
        if character == "-" and following == "-":
            newline = source.find("\n", index + 2)
            index = len(source) if newline < 0 else newline + 1
            continue
        if character == "/" and following == "*":
            closing = source.find("*/", index + 2)
            index = len(source) if closing < 0 else closing + 2
            continue
        if character == "(":
            depth += 1
        elif character == ")":
            depth = max(0, depth - 1)
        elif character == ";" and depth == 0:
            return index + 1
        index += 1
    return len(source)


@dataclass(frozen=True, slots=True)
class _ReportRegion:
    kind: ArtifactKind
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class MaterialRegion:
    kind: ArtifactKind
    start: int
    end: int
    text: str


def _report_regions(source: str) -> list[_ReportRegion]:
    pattern = re.compile(
        r"^(?:[ \t]*SQL Monitoring Report[ \t]*|"
        r"[ \t]*SQL_ID\s+[a-z0-9]+\s*,\s*child number\s+\d+[ \t]*|"
        r"[ \t]*(?:EXPLAINED SQL STATEMENT|SQL STATEMENT)\s*:[ \t]*|"
        r"[ \t]*Plan hash value\s*:\s*\d+[ \t]*)\r?$",
        re.IGNORECASE | re.MULTILINE,
    )
    accepted: list[dict[str, object]] = []
    for match in pattern.finditer(source):
        label = match.group(0).strip().upper()
        marker = (
            "monitor"
            if label.startswith("SQL MONITORING REPORT")
            else "plan_hash"
            if label.startswith("PLAN HASH VALUE")
            else "xplan_header"
        )
        previous = accepted[-1] if accepted else None
        if (
            marker == "plan_hash"
            and previous is not None
            and previous["kind"] == ArtifactKind.XPLAN
            and not previous["owns_plan_hash"]
        ):
            previous["owns_plan_hash"] = True
            continue
        accepted.append(
            {
                "start": match.start(),
                "kind": (
                    ArtifactKind.SQL_MONITOR if marker == "monitor" else ArtifactKind.XPLAN
                ),
                "owns_plan_hash": marker == "plan_hash",
            }
        )
    return [
        _ReportRegion(
            kind=item["kind"],
            start=int(item["start"]),
            end=int(accepted[index + 1]["start"]) if index + 1 < len(accepted) else len(source),
        )
        for index, item in enumerate(accepted)
    ]


def split_materials(source: str) -> list[MaterialRegion]:
    """Return independently storable Oracle materials found in one paste."""

    regions: list[MaterialRegion] = []
    occupied: list[tuple[int, int]] = []
    for report in _report_regions(source):
        text = source[report.start : report.end].strip()
        if not text:
            continue
        leading = len(source[report.start : report.end]) - len(
            source[report.start : report.end].lstrip()
        )
        actual_start = report.start + leading
        actual_end = actual_start + len(text)
        regions.append(
            MaterialRegion(
                kind=report.kind,
                start=actual_start,
                end=actual_end,
                text=text,
            )
        )
        occupied.append((report.start, report.end))

    fence_pattern = re.compile(
        r"```(?P<language>oracle|sql|plsql|text)?[ \t]*\r?\n"
        r"(?P<body>[\s\S]*?)```",
        re.IGNORECASE,
    )
    for match in fence_pattern.finditer(source):
        body_start = match.start("body")
        body_end = match.end("body")
        if any(body_start < end and start < body_end for start, end in occupied):
            continue
        body = match.group("body").strip()
        if not body:
            continue
        leading = len(match.group("body")) - len(match.group("body").lstrip())
        actual_start = body_start + leading
        kind = detect_artifact_kind(body)
        language = (match.group("language") or "").lower()
        if kind == ArtifactKind.UNKNOWN and language in {"sql", "oracle", "plsql"}:
            kind = ArtifactKind.SQL
        if kind == ArtifactKind.UNKNOWN and language != "text":
            continue
        regions.append(
            MaterialRegion(
                kind=kind,
                start=actual_start,
                end=actual_start + len(body),
                text=body,
            )
        )

    if not regions:
        kind = detect_artifact_kind(source)
        if kind != ArtifactKind.UNKNOWN:
            stripped = source.strip()
            start = len(source) - len(source.lstrip())
            regions.append(
                MaterialRegion(
                    kind=kind,
                    start=start,
                    end=start + len(stripped),
                    text=stripped,
                )
            )
    return sorted(regions, key=lambda item: (item.start, item.end))


def parse_prompt(source: str) -> ParsedArtifact:
    """Parse Oracle materials embedded in ordinary prompt prose."""

    detected = detect_artifact_kind(source)
    aggregate = ParsedArtifact(kind=detected, source_text=source)
    covered: list[tuple[int, int]] = []
    reports = _report_regions(source)
    if reports:
        for report in reports:
            _append_region(
                aggregate,
                parse_artifact(source[report.start : report.end], report.kind),
                report.start,
            )
            covered.append((report.start, report.end))
    elif detected != ArtifactKind.UNKNOWN:
        _append_region(aggregate, parse_artifact(source, detected), 0)

    fence_pattern = re.compile(
        r"```(?P<language>oracle|sql|plsql|text)?[ \t]*\r?\n"
        r"(?P<body>[\s\S]*?)```",
        re.IGNORECASE,
    )
    for match in fence_pattern.finditer(source):
        language = (match.group("language") or "").lower()
        body = match.group("body")
        body_start = match.start("body")
        kind = detect_artifact_kind(body)
        if kind == ArtifactKind.UNKNOWN and not language:
            continue
        parsed = (
            parse_sql(body)
            if kind == ArtifactKind.UNKNOWN and language != "text"
            else parse_artifact(body, kind)
        )
        _append_region(aggregate, parsed, body_start)
        covered.append((body_start, body_start + len(body)))

    if detected == ArtifactKind.UNKNOWN and not covered:
        sql_match = re.search(
            r"(?:^|[\s:])"
            r"(?:SELECT|WITH|INSERT|UPDATE|DELETE|MERGE|EXPLAIN|CREATE|ALTER|DROP|"
            r"TRUNCATE|BEGIN|DECLARE)\b",
            source,
            re.IGNORECASE | re.MULTILINE,
        )
        if sql_match:
            keyword_match = re.search(
                r"(?:SELECT|WITH|INSERT|UPDATE|DELETE|MERGE|EXPLAIN|CREATE|ALTER|DROP|"
                r"TRUNCATE|BEGIN|DECLARE)\b",
                source[sql_match.start() :],
                re.IGNORECASE,
            )
            absolute = sql_match.start() + (keyword_match.start() if keyword_match else 0)
            region = source[absolute:]
            end = _sql_region_end(region)
            _append_region(aggregate, parse_sql(region[:end]), absolute)
            covered.append((absolute, absolute + end))

    if detected == ArtifactKind.UNKNOWN and not covered:
        if re.search(
            r"\b(?:DBMS_XPLAN|SQL_ID|PLAN HASH VALUE|PREDICATE INFORMATION|"
            r"SQL MONITOR(?:ING)? REPORT)\b",
            source,
            re.IGNORECASE,
        ):
            _append_region(aggregate, _parse_unknown(source), 0)
            aggregate.findings.append(
                ParseFinding(
                    severity="warning",
                    code="oracle_artifact_conservative_fallback",
                    message=(
                        "Oracle-like content was not structurally recognized and was replaced "
                        "as one fragment."
                    ),
                )
            )
        else:
            aggregate.findings.append(
                ParseFinding(
                    severity="info",
                    code="no_oracle_artifact_detected",
                    message=(
                        "No supported Oracle artifact was detected; "
                        "prompt prose was unchanged."
                    ),
                )
            )
    aggregate.metadata = {"embedded_artifacts": len(covered)}
    aggregate.finalize()
    return aggregate

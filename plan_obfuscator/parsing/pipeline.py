from __future__ import annotations

import re

from .detector import detect_artifact_kind
from .monitor import parse_sql_monitor
from .sql import parse_expression_fragment, parse_hint_fragment, parse_sql
from .types import ArtifactKind, EntityType, ParsedArtifact, ParseFinding
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

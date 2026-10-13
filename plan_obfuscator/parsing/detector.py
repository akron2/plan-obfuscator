from __future__ import annotations

import re

import sqlglot

from .types import ArtifactKind

SQL_START_RE = re.compile(
    r"^\s*(?:(?:/\*[\s\S]*?\*/|--[^\r\n]*(?:\r?\n|$))\s*)*"
    r"(?:SELECT|WITH|INSERT|UPDATE|DELETE|MERGE|EXPLAIN|CREATE|ALTER|DROP|"
    r"TRUNCATE|GRANT|REVOKE|COMMENT|CALL|BEGIN|DECLARE)\b",
    re.IGNORECASE,
)


def detect_artifact_kind(text: str) -> ArtifactKind:
    stripped = text.strip()
    if not stripped:
        return ArtifactKind.UNKNOWN

    if re.search(r"(?im)^\s*SQL Monitoring Report\s*$", text) or (
        re.search(r"(?im)^\s*Global Information\s*$", text)
        and re.search(r"(?im)^\s*SQL Plan Monitoring Details", text)
    ):
        return ArtifactKind.SQL_MONITOR

    if re.search(r"(?im)^\s*Plan hash value\s*[:=]", text) or (
        re.search(r"(?im)^\s*\|\s*Id\s*\|\s*Operation\s*\|", text)
        and re.search(r"(?im)^\s*Predicate Information", text)
    ):
        return ArtifactKind.XPLAN

    if "BEGIN_OUTLINE_DATA" in text.upper() or re.search(r"(?im)^\s*Outline Data\s*$", text):
        return ArtifactKind.OUTLINE

    if re.search(r"(?im)^\s*Predicate Information", text) or re.search(
        r"(?im)^\s*\d+\s*-\s*(?:access|filter)\(", text
    ):
        return ArtifactKind.PREDICATES

    if SQL_START_RE.search(text):
        return ArtifactKind.SQL

    try:
        expressions = sqlglot.parse(text, read="oracle")
        if any(expression is not None for expression in expressions):
            return ArtifactKind.SQL
    except Exception:
        pass
    return ArtifactKind.UNKNOWN

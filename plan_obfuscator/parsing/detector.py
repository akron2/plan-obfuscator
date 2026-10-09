from __future__ import annotations

import re

import sqlglot

from .types import ArtifactKind


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

    try:
        expressions = sqlglot.parse(text, read="oracle")
        if any(expression is not None for expression in expressions):
            return ArtifactKind.SQL
    except Exception:
        pass
    return ArtifactKind.UNKNOWN

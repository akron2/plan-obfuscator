from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from sqlglot import Dialect
from sqlglot.tokens import TokenType

from .types import EntityType, TextOccurrence

LINE_WITH_ENDING_RE = re.compile(r".*(?:\r\n|\n|\r|$)")


def iter_lines_with_offsets(text: str) -> Iterable[tuple[int, str]]:
    for match in LINE_WITH_ENDING_RE.finditer(text):
        line = match.group(0)
        if not line:
            continue
        yield match.start(), line


def canonical_identifier(value: str, *, quoted: bool = False) -> str:
    unescaped = value.replace('""', '"') if quoted else value
    return f"Q:{unescaped}" if quoted else f"U:{unescaped.upper()}"


def canonical_value(
    entity_type: EntityType,
    value: str,
    *,
    quoted: bool = False,
) -> str:
    if entity_type in {
        EntityType.STRING_LITERAL,
        EntityType.COMMENT,
        EntityType.FRAGMENT,
    }:
        return value
    if entity_type in {EntityType.NUMBER_LITERAL, EntityType.DATETIME_LITERAL}:
        return value.upper()
    return canonical_identifier(value, quoted=quoted)


def make_occurrence(
    text: str,
    start: int,
    end: int,
    entity_type: EntityType,
    provenance: str,
    *,
    canonical: str | None = None,
    quoted: bool = False,
    quoting_context: str = "none",
    confidence: float = 1.0,
    priority: int = 0,
) -> TextOccurrence | None:
    if start < 0 or end <= start or end > len(text):
        return None
    original = text[start:end]
    if not original or not original.strip():
        return None
    return TextOccurrence(
        start=start,
        end=end,
        entity_type=entity_type,
        original=original,
        canonical=canonical
        if canonical is not None
        else canonical_value(entity_type, original, quoted=quoted),
        provenance=provenance,
        confidence=confidence,
        quoting_context=quoting_context,
        priority=priority,
    )


def _relaxed_sql_signature(sql: str) -> str:
    output: list[str] = []
    index = 0
    length = len(sql)
    paired_delimiters = {"[": "]", "{": "}", "(": ")", "<": ">"}
    while index < length:
        char = sql[index]
        if char.isspace():
            index += 1
            continue
        if sql.startswith("--", index):
            newline = re.search(r"[\r\n]", sql[index + 2 :])
            index = length if newline is None else index + 2 + newline.end()
            continue
        if sql.startswith("/*", index) and not sql.startswith("/*+", index):
            close = sql.find("*/", index + 2)
            index = length if close < 0 else close + 2
            continue
        if sql.startswith("/*+", index):
            close = sql.find("*/", index + 3)
            end = length if close < 0 else close + 2
            output.append(re.sub(r"\s+", "", sql[index:end]).upper())
            index = end
            continue
        if (
            char.upper() == "Q"
            and index + 2 < length
            and sql[index + 1] == "'"
            and sql[index + 2] in paired_delimiters
        ):
            close_sequence = paired_delimiters[sql[index + 2]] + "'"
            close = sql.find(close_sequence, index + 3)
            end = length if close < 0 else close + 2
            output.append("Q" + sql[index + 1 : end])
            index = end
            continue
        if char in {"'", '"'}:
            quote = char
            end = index + 1
            while end < length:
                if sql[end] == quote:
                    if end + 1 < length and sql[end + 1] == quote:
                        end += 2
                        continue
                    end += 1
                    break
                end += 1
            output.append(sql[index:end])
            index = end
            continue
        output.append(char.upper())
        index += 1
    return "".join(output).rstrip(";")


def normalize_sql(sql: str) -> str:
    raw_signature = _relaxed_sql_signature(sql)
    if not raw_signature:
        raise ValueError("No SQL tokens found")
    try:
        tokens = Dialect.get_or_raise("oracle").tokenize(sql)
    except Exception:
        return f"T:\x1eR:{raw_signature}"
    parts: list[str] = []
    for token in tokens:
        if token.token_type == TokenType.SEMICOLON:
            continue
        if token.token_type in {TokenType.STRING, TokenType.NATIONAL_STRING}:
            value = token.text
        elif token.token_type == TokenType.IDENTIFIER:
            value = f"Q:{token.text}"
        elif token.token_type == TokenType.HINT:
            value = re.sub(r"\s+", " ", token.text).strip().upper()
        else:
            value = token.text.upper()
        parts.append(f"{token.token_type.name}:{value}")
    token_signature = "\x1f".join(parts)
    return f"T:{token_signature}\x1eR:{raw_signature}"


def sql_fingerprint(sql: str) -> tuple[str, str]:
    try:
        normalized = normalize_sql(sql)
    except Exception:
        normalized = re.sub(r"\s+", " ", sql).strip().upper()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest, normalized


def sql_is_compatible(existing: str, candidate: str) -> bool:
    if existing == candidate:
        return True
    existing_token, _, existing_raw = existing.partition("\x1eR:")
    candidate_token, _, candidate_raw = candidate.partition("\x1eR:")
    existing_token = existing_token.removeprefix("T:")
    candidate_token = candidate_token.removeprefix("T:")
    existing_parts = existing_token.split("\x1f") if existing_token else []
    candidate_parts = candidate_token.split("\x1f") if candidate_token else []
    if existing_parts and candidate_parts:
        shorter, longer = sorted(
            (existing_parts, candidate_parts),
            key=lambda value: (len(value), sum(map(len, value))),
        )
        if len(shorter) >= 12:
            compatible = True
            for index, part in enumerate(shorter):
                if index >= len(longer):
                    compatible = False
                    break
                if part == longer[index]:
                    continue
                compatible = index == len(shorter) - 1 and longer[index].startswith(part)
                break
            if compatible:
                return True

    shorter_raw, longer_raw = sorted((existing_raw, candidate_raw), key=len)
    return len(shorter_raw) >= 80 and longer_raw.startswith(shorter_raw)


def cell_bounds(line: str) -> list[tuple[int, int]]:
    pipes = [index for index, char in enumerate(line) if char == "|"]
    return [(pipes[index] + 1, pipes[index + 1]) for index in range(len(pipes) - 1)]


def trimmed_bounds(text: str, start: int, end: int) -> tuple[int, int] | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if start < end else None

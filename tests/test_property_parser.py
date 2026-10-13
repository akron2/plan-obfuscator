from __future__ import annotations

import re
import time
from types import SimpleNamespace

from hypothesis import given, seed, settings
from hypothesis import strategies as st

from plan_obfuscator.parsing import ArtifactKind, ParsedArtifact, parse_artifact, parse_prompt
from plan_obfuscator.services.obfuscation import apply_replacements, exact_restore

from .corpus_loader import CORPUS_CASES, CorpusCase

IDENTIFIER_SUFFIX = st.from_regex(r"[A-Z][A-Z0-9_]{0,15}", fullmatch=True)
VALUE_SUFFIX = st.text(min_size=1, max_size=30).filter(
    lambda value: "]'" not in value and bool(value.strip())
)


def _obfuscate(parsed: ParsedArtifact):
    symbols: dict[tuple[str, str], SimpleNamespace] = {}
    replacements = []
    for occurrence in parsed.occurrences:
        key = (occurrence.entity_type.value, occurrence.canonical)
        symbol = symbols.setdefault(
            key,
            SimpleNamespace(
                marker=f"OBF_PRP_{occurrence.entity_type.value.upper()}_{len(symbols) + 1:04d}_AA"
            ),
        )
        replacements.append((occurrence, symbol))
    return apply_replacements(parsed.source_text, replacements)


def _assert_span_invariants(source: str) -> ParsedArtifact:
    parsed = parse_prompt(source)
    previous_end = 0
    for occurrence in parsed.occurrences:
        assert occurrence.start >= previous_end
        assert occurrence.end > occurrence.start
        assert occurrence.end <= len(source)
        assert source[occurrence.start : occurrence.end] == occurrence.original
        previous_end = occurrence.end
    return parsed


@seed(20261012)
@settings(max_examples=300, deadline=None)
@given(
    schema_part=IDENTIFIER_SUFFIX,
    table_part=IDENTIFIER_SUFFIX,
    column_part=IDENTIFIER_SUFFIX,
    index_part=IDENTIFIER_SUFFIX,
    bind_part=IDENTIFIER_SUFFIX,
    value_part=VALUE_SUFFIX,
    number=st.integers(min_value=70_000_000, max_value=99_999_999),
)
def test_generated_oracle_identifiers_and_literals_round_trip(
    schema_part: str,
    table_part: str,
    column_part: str,
    index_part: str,
    bind_part: str,
    value_part: str,
    number: int,
) -> None:
    schema = f"SCH_PRIVATE_{schema_part}"
    table = f"TBL_SECRET_{table_part}"
    column = f"COL_PRIVATE_{column_part}"
    index = f"IDX_SECRET_{index_part}"
    bind = f"BIND_PRIVATE_{bind_part}"
    value = f"VALUE_PRIVATE_{value_part}"
    sql = (
        f'select /*+ INDEX(t "{index}") */ t."{column}"\n'
        f'from "{schema}"."{table}" t\n'
        f'where t."{column}" = q\'[{value}]\'\n'
        f"  and t.NUMERIC_PRIVATE > {number}\n"
        f"  and t.BIND_COLUMN_PRIVATE = :{bind}"
    )
    result = _obfuscate(_assert_span_invariants(sql))
    for secret in (schema, table, column, index, bind, value, str(number)):
        assert secret not in result.text
    assert exact_restore(result.text, result.replacements) == sql


DANGEROUS_CHUNK = st.one_of(
    st.text(max_size=80),
    st.sampled_from(
        (
            "'",
            '"',
            "/*",
            "*/",
            "--",
            "q'[",
            "]'",
            "nq'{",
            "}'",
            "(+)",
            "SELECT",
            "WITH",
            "SQL_ID",
            "Plan hash value:",
            "Predicate Information",
            "| Id | Operation | Name | Rows |",
            "\r\n",
            "\n",
            "\0",
            "Конфиденциально",
        )
    ),
)


@seed(20261013)
@settings(max_examples=600, deadline=None)
@given(source=st.lists(DANGEROUS_CHUNK, min_size=0, max_size=60).map("".join))
def test_hostile_text_never_breaks_span_or_round_trip_invariants(source: str) -> None:
    parsed = _assert_span_invariants(source)
    result = _obfuscate(parsed)
    assert exact_restore(result.text, result.replacements) == source


REPORT_CASES = tuple(
    item
    for item in CORPUS_CASES
    if item.suite == "generated-oracle-21c"
    and item.expected_kind in {ArtifactKind.XPLAN.value, ArtifactKind.SQL_MONITOR.value}
)


@seed(20261014)
@settings(max_examples=250, deadline=None)
@given(
    item=st.sampled_from(REPORT_CASES),
    use_crlf=st.booleans(),
    blank_lines=st.integers(min_value=0, max_value=3),
)
def test_report_mutations_keep_secrets_protected(
    item: CorpusCase,
    use_crlf: bool,
    blank_lines: int,
) -> None:
    newline = "\r\n" if use_crlf else "\n"
    source = re.sub(r"\r?\n", newline, item.source)
    source = re.sub(
        r"(Plan hash value[^\r\n]*)(\r?\n)",
        lambda match: f"{match.group(1)}{newline * (blank_lines + 1)}",
        source,
        count=1,
        flags=re.IGNORECASE,
    )
    result = _obfuscate(parse_artifact(source))
    for secret in (value for value in item.replace if value in source):
        assert secret not in result.text
    for safe_value in item.keep:
        assert safe_value in result.text
    assert exact_restore(result.text, result.replacements) == source


def test_committed_report_matrix_stays_within_performance_budget() -> None:
    sources = [
        item.source
        for item in CORPUS_CASES
        if item.suite == "generated-oracle-21c" and item.expected_kind != ArtifactKind.SQL.value
    ]
    started = time.perf_counter()
    for _ in range(20):
        for source in sources:
            parse_artifact(source)
    assert time.perf_counter() - started < 5.0

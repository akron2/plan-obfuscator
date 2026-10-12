from __future__ import annotations

import re
from collections.abc import Iterable

import sqlglot
from sqlglot import Dialect, exp
from sqlglot.errors import ParseError
from sqlglot.tokens import Token, TokenType

from .types import ArtifactKind, EntityType, ParsedArtifact, ParseFinding, TextOccurrence
from .utils import canonical_value, make_occurrence

KNOWN_FUNCTIONS = {
    "ABS",
    "ADD_MONTHS",
    "AVG",
    "BITAND",
    "CAST",
    "CEIL",
    "COALESCE",
    "COUNT",
    "CURRENT_DATE",
    "CURRENT_TIMESTAMP",
    "DECODE",
    "DENSE_RANK",
    "EXTRACT",
    "FLOOR",
    "GREATEST",
    "JSON_EXISTS",
    "JSON_QUERY",
    "JSON_TABLE",
    "JSON_VALUE",
    "LAG",
    "LEAD",
    "LEAST",
    "LISTAGG",
    "LOWER",
    "MAX",
    "MIN",
    "MOD",
    "MONTHS_BETWEEN",
    "NVL",
    "NVL2",
    "ORA_HASH",
    "RANK",
    "REGEXP_LIKE",
    "REGEXP_REPLACE",
    "REGEXP_SUBSTR",
    "ROUND",
    "ROW_NUMBER",
    "RTRIM",
    "SUBSTR",
    "SUM",
    "SYS_CONTEXT",
    "TO_CHAR",
    "TO_DATE",
    "TO_NUMBER",
    "TO_TIMESTAMP",
    "TRIM",
    "TRUNC",
    "UPPER",
}

KNOWN_HINTS = {
    "ALL_ROWS",
    "APPEND",
    "BEGIN_OUTLINE_DATA",
    "CARDINALITY",
    "CLUSTER",
    "DB_VERSION",
    "END_OUTLINE_DATA",
    "FIRST_ROWS",
    "FULL",
    "GATHER_PLAN_STATISTICS",
    "IGNORE_OPTIM_EMBEDDED_HINTS",
    "INDEX",
    "INDEX_ASC",
    "INDEX_DESC",
    "INDEX_RS_ASC",
    "LEADING",
    "MERGE",
    "MONITOR",
    "NO_MERGE",
    "NO_PARALLEL",
    "NO_UNNEST",
    "NLJ_BATCHING",
    "OPTIMIZER_FEATURES_ENABLE",
    "OPT_PARAM",
    "ORDERED",
    "OUTLINE",
    "OUTLINE_LEAF",
    "PARALLEL",
    "PQ_DISTRIBUTE",
    "PQ_FILTER",
    "PUSH_PRED",
    "RBO_OUTLINE",
    "SERIAL",
    "SWAP_JOIN_INPUTS",
    "UNNEST",
    "USE_HASH",
    "USE_MERGE",
    "USE_NL",
}

SPECIAL_IDENTIFIERS_TO_KEEP = {
    "ROWID",
    "ROWNUM",
    "LEVEL",
    "SYSDATE",
    "SYSTIMESTAMP",
    "NULL",
    "TRUE",
    "FALSE",
}


def _identifier_type(identifier: exp.Identifier) -> EntityType:
    parent = identifier.parent
    key = identifier.arg_key

    if isinstance(parent, exp.Table):
        if key in {"catalog", "db"}:
            return EntityType.SCHEMA
        if key == "this":
            return EntityType.TABLE
        if key == "alias":
            return EntityType.ALIAS
    if isinstance(parent, exp.Column):
        if key == "this":
            return EntityType.COLUMN
        if key == "table":
            return EntityType.ALIAS
        if key in {"catalog", "db"}:
            return EntityType.SCHEMA
    if isinstance(parent, exp.TableAlias):
        return EntityType.ALIAS
    if isinstance(parent, exp.Alias) and key == "alias":
        return EntityType.ALIAS
    if isinstance(parent, exp.CTE):
        return EntityType.ALIAS
    if isinstance(parent, exp.Index):
        return EntityType.INDEX
    return EntityType.IDENTIFIER


def _identifier_occurrence(sql: str, identifier: exp.Identifier) -> TextOccurrence | None:
    meta = identifier.meta
    if "start" not in meta or "end" not in meta:
        return None
    start = int(meta["start"])
    end = int(meta["end"]) + 1
    quoted = bool(identifier.args.get("quoted"))
    quoting_context = "quoted_identifier" if quoted else "none"
    if quoted and sql[start:end].startswith('"') and sql[start:end].endswith('"'):
        start += 1
        end -= 1

    entity_type = _identifier_type(identifier)
    return make_occurrence(
        sql,
        start,
        end,
        entity_type,
        "sql_ast",
        canonical=canonical_value(entity_type, str(identifier.this), quoted=quoted),
        quoted=quoted,
        quoting_context=quoting_context,
    )


def _retag_occurrence(item: TextOccurrence, entity_type: EntityType) -> TextOccurrence:
    semantic_quoted = item.canonical.startswith("Q:")
    return TextOccurrence(
        start=item.start,
        end=item.end,
        entity_type=entity_type,
        original=item.original,
        canonical=canonical_value(
            entity_type,
            item.original,
            quoted=semantic_quoted,
        ),
        provenance=item.provenance,
        confidence=item.confidence,
        quoting_context=item.quoting_context,
    )


def _literal_occurrence(sql: str, literal: exp.Literal) -> TextOccurrence | None:
    meta = literal.meta
    if "start" not in meta or "end" not in meta:
        return None
    start = int(meta["start"])
    end = int(meta["end"]) + 1
    segment = sql[start:end]
    if literal.is_string:
        entity_type = EntityType.STRING_LITERAL
        quoting_context = "string"
        q_match = Q_QUOTE_RE.fullmatch(segment)
        if q_match:
            canonical = canonical_value(entity_type, q_match.group("body"))
            quoting_context = "q_string"
        elif segment.startswith("'") and segment.endswith("'"):
            start += 1
            end -= 1
            canonical = canonical_value(entity_type, str(literal.this))
        elif len(segment) >= 3 and segment[0].upper() == "N" and segment[1] == "'":
            start += 2
            end -= 1
            canonical = canonical_value(entity_type, str(literal.this))
        else:
            canonical = canonical_value(entity_type, str(literal.this))
    else:
        entity_type = EntityType.NUMBER_LITERAL
        quoting_context = "none"
        canonical = canonical_value(entity_type, segment)
    return make_occurrence(
        sql,
        start,
        end,
        entity_type,
        "sql_ast",
        canonical=canonical,
        quoting_context=quoting_context,
    )


def _scan_comment_spans(sql: str) -> list[tuple[int, int, bool]]:
    spans: list[tuple[int, int, bool]] = []
    index = 0
    length = len(sql)
    while index < length:
        q_match = Q_QUOTE_RE.match(sql, index)
        if q_match:
            index = q_match.end()
            continue
        char = sql[index]
        if char == "'":
            index += 1
            while index < length:
                if sql[index] == "'":
                    if index + 1 < length and sql[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue
        if char == '"':
            index += 1
            while index < length:
                if sql[index] == '"':
                    if index + 1 < length and sql[index + 1] == '"':
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue
        if sql.startswith("--", index):
            end = index + 2
            while end < length and sql[end] not in "\r\n":
                end += 1
            spans.append((index, end, False))
            index = end
            continue
        if sql.startswith("/*", index):
            end_marker = sql.find("*/", index + 2)
            end = length if end_marker < 0 else end_marker + 2
            spans.append((index, end, sql.startswith("/*+", index)))
            index = end
            continue
        index += 1
    return spans


def _mask_regular_comment(sql: str, start: int, end: int) -> TextOccurrence | None:
    if sql.startswith("--", start):
        content_start = start + 2
        while content_start < end and sql[content_start] == " ":
            content_start += 1
        return make_occurrence(
            sql,
            content_start,
            end,
            EntityType.COMMENT,
            "sql_comment",
            quoting_context="line_comment",
        )
    content_start = start + 2
    content_end = end - 2 if sql[end - 2 : end] == "*/" else end
    while content_start < content_end and sql[content_start].isspace():
        content_start += 1
    while content_end > content_start and sql[content_end - 1].isspace():
        content_end -= 1
    return make_occurrence(
        sql,
        content_start,
        content_end,
        EntityType.COMMENT,
        "sql_comment",
        quoting_context="block_comment",
    )


def _hint_occurrences(sql: str, start: int, end: int) -> list[TextOccurrence]:
    result: list[TextOccurrence] = []
    body_start = start + 3
    body_end = end - 2 if sql[end - 2 : end] == "*/" else end
    body = sql[body_start:body_end]

    covered: list[tuple[int, int]] = []
    for match in re.finditer(r'@"((?:""|[^"])*)"', body):
        item_start = body_start + match.start(1)
        item_end = body_start + match.end(1)
        clean = match.group(1)
        occurrence = make_occurrence(
            sql,
            item_start,
            item_end,
            EntityType.QUERY_BLOCK,
            "oracle_hint",
            canonical=canonical_value(EntityType.QUERY_BLOCK, clean, quoted=False),
            quoting_context="quoted_identifier",
        )
        if occurrence:
            result.append(occurrence)
            covered.append((item_start, item_end))

    for match in re.finditer(r'"((?:""|[^"])*)"', body):
        item_start = body_start + match.start(1)
        item_end = body_start + match.end(1)
        if any(item_start >= left and item_end <= right for left, right in covered):
            continue
        clean = match.group(1)
        semantic_quoted = clean != clean.upper() or not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean)
        occurrence = make_occurrence(
            sql,
            item_start,
            item_end,
            EntityType.IDENTIFIER,
            "oracle_hint",
            canonical=canonical_value(EntityType.IDENTIFIER, clean, quoted=semantic_quoted),
            quoted=semantic_quoted,
            quoting_context="quoted_identifier",
        )
        if occurrence:
            result.append(occurrence)
            covered.append((item_start, item_end))

    for match in re.finditer(r"\b(?:SEL|SET|INS|UPD|DEL)\$[A-Z0-9_]+\b", body, re.I):
        item_start = body_start + match.start()
        item_end = body_start + match.end()
        if any(item_start < right and left < item_end for left, right in covered):
            continue
        occurrence = make_occurrence(
            sql,
            item_start,
            item_end,
            EntityType.QUERY_BLOCK,
            "oracle_hint",
        )
        if occurrence:
            result.append(occurrence)

    # Unquoted hint arguments are masked unless they are known hint/control words.
    for match in re.finditer(r"[A-Za-z_#$][A-Za-z0-9_#$]*", body):
        word = match.group(0)
        item_start = body_start + match.start()
        item_end = body_start + match.end()
        if any(item_start < right and left < item_end for left, right in covered):
            continue
        if word.upper() in KNOWN_HINTS or word.upper() in {"DEFAULT", "FIXED"}:
            continue
        previous = body[match.start() - 1] if match.start() else ""
        if previous in {'"', "'", "@"}:
            continue
        occurrence = make_occurrence(
            sql,
            item_start,
            item_end,
            EntityType.IDENTIFIER,
            "oracle_hint",
            confidence=0.8,
        )
        if occurrence:
            result.append(occurrence)
    return result


def _ranges_overlap(start: int, end: int, occurrences: Iterable[TextOccurrence]) -> bool:
    return any(start < item.end and item.start < end for item in occurrences)


def _fallback_tokens(sql: str, existing: list[TextOccurrence]) -> list[TextOccurrence]:
    result: list[TextOccurrence] = []
    try:
        tokens = Dialect.get_or_raise("oracle").tokenize(sql)
    except Exception:
        tokens = []

    for index, token in enumerate(tokens):
        start = token.start
        end = token.end + 1
        if _ranges_overlap(start, end, [*existing, *result]):
            continue
        previous = tokens[index - 1] if index else None
        following = tokens[index + 1] if index + 1 < len(tokens) else None

        if token.token_type in {TokenType.STRING, TokenType.NATIONAL_STRING}:
            segment = sql[start:end]
            content_start, content_end = start, end
            if segment.startswith("'") and segment.endswith("'"):
                content_start += 1
                content_end -= 1
            elif len(segment) >= 3 and segment[0].upper() == "N" and segment[1] == "'":
                content_start += 2
                content_end -= 1
            occurrence = make_occurrence(
                sql,
                content_start,
                content_end,
                EntityType.STRING_LITERAL,
                "sql_lexer",
                canonical=canonical_value(EntityType.STRING_LITERAL, token.text),
                quoting_context="string",
            )
        elif token.token_type == TokenType.NUMBER:
            if previous and previous.token_type == TokenType.COLON:
                occurrence = make_occurrence(
                    sql,
                    start,
                    end,
                    EntityType.BIND,
                    "sql_lexer",
                    quoting_context="bind",
                )
            else:
                occurrence = make_occurrence(
                    sql,
                    start,
                    end,
                    EntityType.NUMBER_LITERAL,
                    "sql_lexer",
                )
        elif token.token_type == TokenType.IDENTIFIER:
            occurrence = make_occurrence(
                sql,
                start + 1,
                end - 1,
                EntityType.IDENTIFIER,
                "sql_lexer",
                canonical=canonical_value(EntityType.IDENTIFIER, token.text, quoted=True),
                quoted=True,
                quoting_context="quoted_identifier",
                confidence=0.75,
            )
        elif token.token_type == TokenType.VAR:
            upper = token.text.upper()
            if upper in SPECIAL_IDENTIFIERS_TO_KEEP:
                continue
            if previous and previous.token_type == TokenType.COLON:
                occurrence = make_occurrence(
                    sql,
                    start,
                    end,
                    EntityType.BIND,
                    "sql_lexer",
                    quoting_context="bind",
                )
            elif following and following.token_type == TokenType.L_PAREN:
                if upper in KNOWN_FUNCTIONS:
                    continue
                occurrence = make_occurrence(
                    sql,
                    start,
                    end,
                    EntityType.FUNCTION,
                    "sql_lexer",
                    confidence=0.75,
                )
            elif "@" in token.text:
                local_name, _, link_name = token.text.partition("@")
                if local_name:
                    local = make_occurrence(
                        sql,
                        start,
                        start + len(local_name),
                        EntityType.TABLE,
                        "sql_lexer",
                        confidence=0.75,
                    )
                    if local:
                        result.append(local)
                occurrence = make_occurrence(
                    sql,
                    start + len(local_name) + 1,
                    end,
                    EntityType.DB_LINK,
                    "sql_lexer",
                    canonical=canonical_value(EntityType.DB_LINK, link_name),
                    confidence=0.75,
                )
            else:
                occurrence = make_occurrence(
                    sql,
                    start,
                    end,
                    EntityType.IDENTIFIER,
                    "sql_lexer",
                    confidence=0.65,
                )
        else:
            occurrence = None

        if occurrence:
            result.append(occurrence)

    return result


Q_QUOTE_RE = re.compile(
    r"(?i)q'(?P<open>[\[\{\(<])(?P<body>.*?)(?P<close>[\]\}\)>])'",
    re.DOTALL,
)


def parse_sql(sql: str, *, base_offset: int = 0) -> ParsedArtifact:
    parsed = ParsedArtifact(kind=ArtifactKind.SQL, source_text=sql, extracted_sql=[sql])

    try:
        # SQLGlot does not currently parse every Oracle alternative-quoting form.
        # Replace q-quoted literals with same-length ordinary string literals for
        # structural analysis; all coordinates continue to point into the source.
        ast_input = Q_QUOTE_RE.sub(
            lambda match: "'" + ("x" * (len(match.group(0)) - 2)) + "'",
            sql,
        )
        trees = sqlglot.parse(ast_input, read="oracle")
        for tree in trees:
            if tree is None:
                continue
            cte_canonicals = {
                canonical_value(EntityType.ALIAS, cte.alias_or_name)
                for cte in tree.find_all(exp.CTE)
                if cte.alias_or_name
            }
            select_alias_canonicals = {
                canonical_value(EntityType.ALIAS, alias.alias)
                for alias in tree.find_all(exp.Alias)
                if alias.alias
            }
            for identifier in tree.find_all(exp.Identifier):
                occurrence = _identifier_occurrence(sql, identifier)
                if occurrence:
                    if (
                        occurrence.entity_type == EntityType.TABLE
                        and "@" in occurrence.original
                        and occurrence.quoting_context == "none"
                    ):
                        local_name, link_name = occurrence.original.rsplit("@", 1)
                        local = make_occurrence(
                            sql,
                            occurrence.start,
                            occurrence.start + len(local_name),
                            EntityType.TABLE,
                            "sql_ast",
                        )
                        link = make_occurrence(
                            sql,
                            occurrence.start + len(local_name) + 1,
                            occurrence.end,
                            EntityType.DB_LINK,
                            "sql_ast",
                        )
                        if local:
                            parsed.add_occurrence(local)
                        if link:
                            parsed.add_occurrence(link)
                    elif (
                        occurrence.entity_type == EntityType.TABLE
                        and canonical_value(EntityType.ALIAS, occurrence.original) in cte_canonicals
                    ):
                        parsed.add_occurrence(_retag_occurrence(occurrence, EntityType.ALIAS))
                    elif (
                        occurrence.entity_type == EntityType.COLUMN
                        and canonical_value(EntityType.ALIAS, occurrence.original)
                        in select_alias_canonicals
                        and isinstance(identifier.parent, exp.Column)
                        and not identifier.parent.table
                        and identifier.find_ancestor(exp.Order, exp.Group, exp.Qualify) is not None
                    ):
                        parsed.add_occurrence(_retag_occurrence(occurrence, EntityType.ALIAS))
                    else:
                        parsed.add_occurrence(occurrence)
            for literal in tree.find_all(exp.Literal):
                occurrence = _literal_occurrence(sql, literal)
                if occurrence:
                    parsed.add_occurrence(occurrence)
    except ParseError as error:
        parsed.findings.append(
            ParseFinding(
                severity="warning",
                code="sql_ast_parse_failed",
                message=f"SQL AST parser switched to lexical fallback: {error}",
            )
        )
    except Exception as error:
        parsed.findings.append(
            ParseFinding(
                severity="warning",
                code="sql_ast_unexpected_error",
                message=f"SQL AST parser switched to lexical fallback: {error}",
            )
        )

    comment_spans = _scan_comment_spans(sql)
    for start, end, is_hint in comment_spans:
        if is_hint:
            for occurrence in _hint_occurrences(sql, start, end):
                parsed.add_occurrence(occurrence)
        else:
            occurrence = _mask_regular_comment(sql, start, end)
            if occurrence:
                parsed.add_occurrence(occurrence)

    for match in Q_QUOTE_RE.finditer(sql):
        occurrence = make_occurrence(
            sql,
            match.start(),
            match.end(),
            EntityType.STRING_LITERAL,
            "oracle_q_quote",
            canonical=canonical_value(EntityType.STRING_LITERAL, match.group("body")),
            quoting_context="q_string",
        )
        if occurrence:
            parsed.add_occurrence(occurrence)

    for occurrence in _fallback_tokens(sql, parsed.occurrences):
        parsed.add_occurrence(occurrence)

    if not parsed.occurrences and sql.strip():
        start = len(sql) - len(sql.lstrip())
        end = len(sql.rstrip())
        occurrence = make_occurrence(
            sql,
            start,
            end,
            EntityType.FRAGMENT,
            "sql_total_fallback",
            confidence=0.4,
        )
        if occurrence:
            parsed.add_occurrence(occurrence)
            parsed.findings.append(
                ParseFinding(
                    severity="warning",
                    code="sql_total_fallback",
                    message="SQL could not be tokenized safely and was replaced as one fragment.",
                )
            )

    # Reconcile conservative hint/lexer identifiers with semantic AST symbols.
    # This keeps aliases and object names stable between SQL text and hints.
    semantic_by_canonical: dict[str, set[EntityType]] = {}
    for item in parsed.occurrences:
        if item.provenance == "sql_ast" and item.entity_type != EntityType.IDENTIFIER:
            semantic_by_canonical.setdefault(item.canonical, set()).add(item.entity_type)
    reconciled: list[TextOccurrence] = []
    for item in parsed.occurrences:
        choices = semantic_by_canonical.get(item.canonical, set())
        if item.entity_type == EntityType.IDENTIFIER and len(choices) == 1:
            entity_type = next(iter(choices))
            reconciled.append(
                TextOccurrence(
                    start=item.start,
                    end=item.end,
                    entity_type=entity_type,
                    original=item.original,
                    canonical=canonical_value(
                        entity_type,
                        item.original,
                        quoted=item.quoting_context == "quoted_identifier",
                    ),
                    provenance=item.provenance,
                    confidence=item.confidence,
                    quoting_context=item.quoting_context,
                    priority=0,
                )
            )
        else:
            reconciled.append(item)
    parsed.occurrences = reconciled

    parsed.finalize()

    if base_offset:
        parsed.occurrences = [
            TextOccurrence(
                start=item.start + base_offset,
                end=item.end + base_offset,
                entity_type=item.entity_type,
                original=item.original,
                canonical=item.canonical,
                provenance=item.provenance,
                confidence=item.confidence,
                quoting_context=item.quoting_context,
                priority=item.priority,
            )
            for item in parsed.occurrences
        ]
    return parsed


def parse_hint_fragment(text: str, *, base_offset: int = 0) -> list[TextOccurrence]:
    """Parse an Oracle outline/hint fragment while preserving hint names."""

    wrapped = text
    prefix_length = 0
    if "/*+" not in text:
        wrapped = f"/*+{text}*/"
        prefix_length = 3
    start = wrapped.find("/*+")
    occurrences = _hint_occurrences(wrapped, start, len(wrapped))
    shifted: list[TextOccurrence] = []
    for item in occurrences:
        local_start = item.start - prefix_length
        local_end = item.end - prefix_length
        if local_start < 0 or local_end > len(text):
            continue
        shifted.append(
            TextOccurrence(
                start=local_start + base_offset,
                end=local_end + base_offset,
                entity_type=item.entity_type,
                original=text[local_start:local_end],
                canonical=item.canonical,
                provenance=item.provenance,
                confidence=item.confidence,
                quoting_context=item.quoting_context,
                priority=item.priority,
            )
        )
    return shifted


def parse_expression_fragment(
    text: str,
    *,
    base_offset: int = 0,
    mask_numbers: bool = True,
    mask_strings: bool = True,
) -> list[TextOccurrence]:
    """Conservative tokenizer for XPLAN predicates and projection fragments."""

    result: list[TextOccurrence] = []
    try:
        tokens: list[Token] = Dialect.get_or_raise("oracle").tokenize(text)
    except Exception:
        tokens = []

    for index, token in enumerate(tokens):
        start = token.start
        end = token.end + 1
        previous = tokens[index - 1] if index else None
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        occurrence: TextOccurrence | None = None

        if token.token_type == TokenType.IDENTIFIER:
            raw_start = start + 1
            raw_end = end - 1
            previous_char = text[start - 1] if start else ""
            following_char = text[end] if end < len(text) else ""
            if previous_char == "@":
                entity_type = EntityType.QUERY_BLOCK
            elif following_char == "@":
                entity_type = EntityType.ALIAS
            elif following and following.token_type == TokenType.DOT:
                entity_type = EntityType.ALIAS
            else:
                entity_type = EntityType.COLUMN
            semantic_quoted = token.text != token.text.upper() or not re.fullmatch(
                r"[A-Z][A-Z0-9_$#]*", token.text
            )
            occurrence = make_occurrence(
                text,
                raw_start,
                raw_end,
                entity_type,
                "oracle_expression",
                canonical=canonical_value(entity_type, token.text, quoted=semantic_quoted),
                quoted=semantic_quoted,
                quoting_context="quoted_identifier",
            )
        elif token.token_type in {TokenType.STRING, TokenType.NATIONAL_STRING} and mask_strings:
            segment = text[start:end]
            raw_start, raw_end = start, end
            if segment.startswith("'") and segment.endswith("'"):
                raw_start += 1
                raw_end -= 1
            elif len(segment) >= 3 and segment[0].upper() == "N" and segment[1] == "'":
                raw_start += 2
                raw_end -= 1
            occurrence = make_occurrence(
                text,
                raw_start,
                raw_end,
                EntityType.STRING_LITERAL,
                "oracle_expression",
                canonical=canonical_value(EntityType.STRING_LITERAL, token.text),
                quoting_context="string",
            )
        elif token.token_type == TokenType.NUMBER and mask_numbers:
            if previous and previous.token_type == TokenType.COLON:
                entity_type = EntityType.BIND
                quoting_context = "bind"
            else:
                entity_type = EntityType.NUMBER_LITERAL
                quoting_context = "none"
            occurrence = make_occurrence(
                text,
                start,
                end,
                entity_type,
                "oracle_expression",
                quoting_context=quoting_context,
            )
        elif token.token_type == TokenType.VAR:
            upper = token.text.upper()
            if previous and previous.token_type == TokenType.COLON:
                occurrence = make_occurrence(
                    text,
                    start,
                    end,
                    EntityType.BIND,
                    "oracle_expression",
                    quoting_context="bind",
                )
            elif re.fullmatch(r"(?:SEL|SET|INS|UPD|DEL)\$[A-Z0-9_]+", upper):
                occurrence = make_occurrence(
                    text,
                    start,
                    end,
                    EntityType.QUERY_BLOCK,
                    "oracle_expression",
                )
            elif following and following.token_type == TokenType.L_PAREN:
                # Oracle and user functions are useful structural information. Built-ins
                # remain visible; user-defined names are masked.
                if upper not in KNOWN_FUNCTIONS and upper not in {"ACCESS", "FILTER"}:
                    occurrence = make_occurrence(
                        text,
                        start,
                        end,
                        EntityType.FUNCTION,
                        "oracle_expression",
                        confidence=0.8,
                    )

        if occurrence:
            result.append(
                TextOccurrence(
                    start=occurrence.start + base_offset,
                    end=occurrence.end + base_offset,
                    entity_type=occurrence.entity_type,
                    original=occurrence.original,
                    canonical=occurrence.canonical,
                    provenance=occurrence.provenance,
                    confidence=occurrence.confidence,
                    quoting_context=occurrence.quoting_context,
                    priority=occurrence.priority,
                )
            )

    return result

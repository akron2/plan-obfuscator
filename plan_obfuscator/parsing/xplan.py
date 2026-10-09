from __future__ import annotations

import re

from .sql import parse_expression_fragment, parse_hint_fragment, parse_sql
from .types import ArtifactKind, EntityType, ParsedArtifact, ParseFinding, TextOccurrence
from .utils import (
    canonical_value,
    cell_bounds,
    iter_lines_with_offsets,
    make_occurrence,
    trimmed_bounds,
)

SECTION_HEADINGS = {
    "Query Block Name / Object Alias",
    "Outline Data",
    "Predicate Information",
    "Column Projection Information",
    "Peeked Binds",
    "Remote SQL Information",
    "Note",
    "Hint Report",
}


def _shift(item: TextOccurrence, offset: int, source: str) -> TextOccurrence:
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


def _add_sql_region(parsed: ParsedArtifact, start: int, end: int) -> None:
    sql_text = parsed.source_text[start:end].strip()
    if not sql_text:
        return
    leading = len(parsed.source_text[start:end]) - len(parsed.source_text[start:end].lstrip())
    actual_start = start + leading
    local = parse_sql(sql_text)
    parsed.extracted_sql.append(sql_text)
    for item in local.occurrences:
        parsed.add_occurrence(_shift(item, actual_start, parsed.source_text))
    parsed.findings.extend(local.findings)


def _mask_qualified_name(
    parsed: ParsedArtifact,
    start: int,
    end: int,
    object_type: EntityType,
    provenance: str,
) -> None:
    value = parsed.source_text[start:end]
    matches = list(re.finditer(r'"(?:""|[^"])+"|[A-Za-z_$#][A-Za-z0-9_$#]*', value))
    if not matches:
        occurrence = make_occurrence(
            parsed.source_text,
            start,
            end,
            object_type,
            provenance,
            confidence=0.7,
        )
        if occurrence:
            parsed.add_occurrence(occurrence)
        return

    for index, match in enumerate(matches):
        part_start = start + match.start()
        part_end = start + match.end()
        raw = match.group(0)
        quoted = raw.startswith('"') and raw.endswith('"')
        if quoted:
            part_start += 1
            part_end -= 1
            clean = raw[1:-1]
        else:
            clean = raw
        entity_type = EntityType.SCHEMA if index < len(matches) - 1 else object_type
        occurrence = make_occurrence(
            parsed.source_text,
            part_start,
            part_end,
            entity_type,
            provenance,
            canonical=canonical_value(entity_type, clean, quoted=quoted),
            quoted=quoted,
            quoting_context="quoted_identifier" if quoted else "none",
        )
        if occurrence:
            parsed.add_occurrence(occurrence)


def _plan_tables(parsed: ParsedArtifact) -> None:
    lines = list(iter_lines_with_offsets(parsed.source_text))
    index = 0
    while index < len(lines):
        offset, line = lines[index]
        if not ("|" in line and re.search(r"\|\s*Id\s*\|", line, re.I)):
            index += 1
            continue
        headers = [line[left:right].strip() for left, right in cell_bounds(line)]
        lowered = [header.lower() for header in headers]
        if "operation" not in lowered or "name" not in lowered:
            index += 1
            continue
        operation_index = lowered.index("operation")
        name_index = lowered.index("name")
        identity_columns = {
            column_index: (
                EntityType.OBJECT_ID
                if header in {"object id", "object#"}
                else EntityType.SESSION_ID
            )
            for column_index, header in enumerate(lowered)
            if header in {"object id", "object#", "inst", "instance", "instance id"}
        }
        index += 1
        while index < len(lines):
            row_offset, row = lines[index]
            if not row.lstrip().startswith("|"):
                if re.fullmatch(r"\s*[-]+\s*(?:\r?\n)?", row):
                    index += 1
                    continue
                break
            bounds = cell_bounds(row)
            if len(bounds) <= max(operation_index, name_index):
                index += 1
                continue
            for column_index, identity_type in identity_columns.items():
                if column_index >= len(bounds):
                    continue
                identity_bounds = trimmed_bounds(row, *bounds[column_index])
                if not identity_bounds:
                    continue
                identity_occurrence = make_occurrence(
                    parsed.source_text,
                    row_offset + identity_bounds[0],
                    row_offset + identity_bounds[1],
                    identity_type,
                    "xplan_plan_identity",
                )
                if identity_occurrence:
                    parsed.add_occurrence(identity_occurrence)
            op_left, op_right = bounds[operation_index]
            name_left, name_right = bounds[name_index]
            operation = row[op_left:op_right].strip().upper()
            name_bounds = trimmed_bounds(row, name_left, name_right)
            if name_bounds:
                local_start, local_end = name_bounds
                absolute_start = row_offset + local_start
                absolute_end = row_offset + local_end
                name = parsed.source_text[absolute_start:absolute_end]
                if name.startswith(":"):
                    entity_type = EntityType.QUERY_BLOCK
                    absolute_start += 1
                elif "TABLE" in operation:
                    entity_type = EntityType.TABLE
                elif "VIEW" in operation:
                    entity_type = EntityType.VIEW
                elif "PARTITION" in operation:
                    entity_type = EntityType.PARTITION
                elif "INDEX" in operation:
                    entity_type = EntityType.INDEX
                else:
                    entity_type = EntityType.IDENTIFIER
                _mask_qualified_name(
                    parsed,
                    absolute_start,
                    absolute_end,
                    entity_type,
                    "xplan_name_column",
                )
            index += 1


def _section_ranges(text: str) -> list[tuple[str, int, int]]:
    headings: list[tuple[str, int, int]] = []
    heading_pattern = re.compile(
        r"(?im)^\s*(Query Block Name / Object Alias|Outline Data|Predicate Information|"
        r"Column Projection Information|Peeked Binds|Remote SQL Information|Note|Hint Report)"
        r"(?:\s*\([^\r\n]*\))?\s*:?\s*$"
    )
    for match in heading_pattern.finditer(text):
        content_start = match.end()
        line_end = re.match(r"(?:\r\n|\n|\r)", text[content_start:])
        if line_end:
            content_start += line_end.end()
        separator = re.match(r"[-]+\s*(?:\r\n|\n|\r)", text[content_start:])
        if separator:
            content_start += separator.end()
        headings.append((match.group(1), match.start(), content_start))
    result: list[tuple[str, int, int]] = []
    for index, (name, _heading_start, content_start) in enumerate(headings):
        content_end = headings[index + 1][1] if index + 1 < len(headings) else len(text)
        result.append((name, content_start, content_end))
    return result


def _query_block_section(parsed: ParsedArtifact, start: int, end: int) -> None:
    fragment = parsed.source_text[start:end]
    for match in re.finditer(r"\b(?:SEL|SET|INS|UPD|DEL)\$[A-Z0-9_]+\b", fragment, re.I):
        occurrence = make_occurrence(
            parsed.source_text,
            start + match.start(),
            start + match.end(),
            EntityType.QUERY_BLOCK,
            "xplan_query_block",
        )
        if occurrence:
            parsed.add_occurrence(occurrence)
    for match in re.finditer(r'"((?:""|[^"])*)"', fragment):
        absolute_start = start + match.start(1)
        absolute_end = start + match.end(1)
        after = fragment[match.end() : match.end() + 1]
        entity_type = EntityType.ALIAS if after == "@" else EntityType.IDENTIFIER
        clean = match.group(1)
        semantic_quoted = clean != clean.upper() or not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean)
        occurrence = make_occurrence(
            parsed.source_text,
            absolute_start,
            absolute_end,
            entity_type,
            "xplan_query_block",
            canonical=canonical_value(entity_type, clean, quoted=semantic_quoted),
            quoted=semantic_quoted,
            quoting_context="quoted_identifier",
        )
        if occurrence:
            parsed.add_occurrence(occurrence)
    for match in re.finditer(
        r'(?<!["A-Za-z0-9_$#])([A-Za-z_][A-Za-z0-9_$#]*)@(?=(?:SEL|SET|INS|UPD|DEL)\$)',
        fragment,
        re.I,
    ):
        occurrence = make_occurrence(
            parsed.source_text,
            start + match.start(1),
            start + match.end(1),
            EntityType.ALIAS,
            "xplan_query_block",
        )
        if occurrence:
            parsed.add_occurrence(occurrence)


def _predicate_section(parsed: ParsedArtifact, start: int, end: int) -> None:
    fragment = parsed.source_text[start:end]
    # Operation ids occur only before " - access/filter(". Exclude those prefixes
    # from numeric-literal handling while parsing the expressions themselves.
    cursor = 0
    expression_start = 0
    for match in re.finditer(r"(?im)^\s*\d+\s*-\s*(?:access|filter)\(", fragment):
        if expression_start < match.start():
            expression_start = match.start()
        prefix_end = match.end() - 1
        next_match = re.search(r"(?im)^\s*\d+\s*-\s*(?:access|filter)\(", fragment[prefix_end:])
        expression_end = prefix_end + next_match.start() if next_match else len(fragment)
        expression = fragment[prefix_end:expression_end]
        for item in parse_expression_fragment(expression, base_offset=start + prefix_end):
            parsed.add_occurrence(item)
        cursor = expression_end
        expression_start = expression_end
        if not next_match:
            break
    if cursor == 0:
        for item in parse_expression_fragment(fragment, base_offset=start):
            parsed.add_occurrence(item)


def _projection_section(parsed: ParsedArtifact, start: int, end: int) -> None:
    fragment = parsed.source_text[start:end]
    for item in parse_expression_fragment(
        fragment,
        base_offset=start,
        mask_numbers=False,
        mask_strings=True,
    ):
        parsed.add_occurrence(item)


def _peeked_binds(parsed: ParsedArtifact, start: int, end: int) -> None:
    fragment = parsed.source_text[start:end]
    for match in re.finditer(r"(?im)(:[A-Za-z0-9_$#]+).*?:\s*(.+?)\s*$", fragment):
        bind_start = start + match.start(1) + 1
        bind_end = start + match.end(1)
        occurrence = make_occurrence(
            parsed.source_text,
            bind_start,
            bind_end,
            EntityType.BIND,
            "xplan_peeked_bind",
            quoting_context="bind",
        )
        if occurrence:
            parsed.add_occurrence(occurrence)
        value_start = start + match.start(2)
        value_end = start + match.end(2)
        raw_value = parsed.source_text[value_start:value_end]
        if raw_value.upper() == "NULL":
            continue
        if raw_value.startswith("'") and raw_value.endswith("'") and len(raw_value) >= 2:
            value_start += 1
            value_end -= 1
            entity_type = EntityType.STRING_LITERAL
            quoting_context = "string"
        elif re.fullmatch(r"[-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?", raw_value):
            entity_type = EntityType.NUMBER_LITERAL
            quoting_context = "bind_value"
        else:
            entity_type = EntityType.STRING_LITERAL
            quoting_context = "bind_value"
        occurrence = make_occurrence(
            parsed.source_text,
            value_start,
            value_end,
            entity_type,
            "xplan_peeked_bind_value",
            quoting_context=quoting_context,
        )
        if occurrence:
            parsed.add_occurrence(occurrence)


def _notes(parsed: ParsedArtifact, start: int, end: int) -> None:
    fragment = parsed.source_text[start:end]
    for match in re.finditer(r'"((?:""|[^"])*)"', fragment):
        occurrence = make_occurrence(
            parsed.source_text,
            start + match.start(1),
            start + match.end(1),
            EntityType.IDENTIFIER,
            "xplan_note",
            quoted=True,
            quoting_context="quoted_identifier",
        )
        if occurrence:
            parsed.add_occurrence(occurrence)
    safe_note = re.compile(
        r"(?i)(?:dynamic statistics|statistics feedback|cardinality feedback|"
        r"adaptive plan|basic plan statistics|degree of parallelism|"
        r"SQL (?:profile|plan baseline)|outline|rule based optimizer|"
        r"this is an adaptive plan)"
    )
    for line_offset, line in iter_lines_with_offsets(fragment):
        clean = line.strip()
        if not clean or set(clean) <= {"-", "*", " "} or safe_note.search(clean):
            continue
        local_start = 0
        while local_start < len(line) and line[local_start].isspace():
            local_start += 1
        if line[local_start : local_start + 2] in {"- ", "* "}:
            local_start += 2
        local_end = len(line.rstrip("\r\n "))
        occurrence = make_occurrence(
            parsed.source_text,
            start + line_offset + local_start,
            start + line_offset + local_end,
            EntityType.FRAGMENT,
            "xplan_unknown_note",
            confidence=0.5,
            priority=110,
        )
        if occurrence:
            parsed.add_occurrence(occurrence)


def parse_xplan(text: str) -> ParsedArtifact:
    parsed = ParsedArtifact(kind=ArtifactKind.XPLAN, source_text=text)

    sql_header = re.search(
        r"(?ims)^\s*SQL_ID\s+(?P<sqlid>[a-z0-9]+)\s*,\s*child number\s+"
        r"(?P<child>\d+)\s*\r?\n[-]+\s*\r?\n(?P<sql>.*?)"
        r"(?=\r?\n\s*\r?\n\s*Plan hash value\s*:)",
        text,
    )
    if sql_header:
        for group, entity_type in (
            ("sqlid", EntityType.SQL_ID),
            ("child", EntityType.EXECUTION_ID),
        ):
            occurrence = make_occurrence(
                text,
                sql_header.start(group),
                sql_header.end(group),
                entity_type,
                "xplan_header",
            )
            if occurrence:
                parsed.add_occurrence(occurrence)
        _add_sql_region(parsed, sql_header.start("sql"), sql_header.end("sql"))

    for match in re.finditer(r"(?i)(?:Plan hash value\s*[:=]\s*)(\d+)", text):
        occurrence = make_occurrence(
            text,
            match.start(1),
            match.end(1),
            EntityType.PLAN_HASH,
            "xplan_plan_hash",
        )
        if occurrence:
            parsed.add_occurrence(occurrence)

    _plan_tables(parsed)

    for name, start, end in _section_ranges(text):
        if name == "Query Block Name / Object Alias":
            _query_block_section(parsed, start, end)
        elif name == "Outline Data":
            for item in parse_hint_fragment(text[start:end], base_offset=start):
                parsed.add_occurrence(item)
        elif name == "Predicate Information":
            _predicate_section(parsed, start, end)
        elif name == "Column Projection Information":
            _projection_section(parsed, start, end)
        elif name == "Peeked Binds":
            _peeked_binds(parsed, start, end)
        elif name == "Remote SQL Information":
            _add_sql_region(parsed, start, end)
        elif name in {"Note", "Hint Report"}:
            _notes(parsed, start, end)

    generic_section_re = re.compile(
        r"(?im)^(?P<heading>[A-Za-z][^\r\n]{0,140})[ \t]*\r?\n[-]{3,}[ \t]*$"
    )
    generic_matches = list(generic_section_re.finditer(text))
    known_heading_prefixes = {name.lower() for name in SECTION_HEADINGS}
    for index, match in enumerate(generic_matches):
        heading = re.sub(r"\s*\([^\r\n]*\)\s*:?\s*$", "", match.group("heading")).strip()
        if heading.lower() in known_heading_prefixes or re.match(r"(?i)^SQL_ID\s", heading):
            continue
        content_start = match.end()
        newline = re.match(r"(?:\r\n|\n|\r)", text[content_start:])
        if newline:
            content_start += newline.end()
        content_end = (
            generic_matches[index + 1].start() if index + 1 < len(generic_matches) else len(text)
        )
        bounds = trimmed_bounds(text, content_start, content_end)
        if not bounds:
            continue
        occurrence = make_occurrence(
            text,
            bounds[0],
            bounds[1],
            EntityType.FRAGMENT,
            "xplan_unknown_section",
            confidence=0.4,
            priority=110,
        )
        if occurrence:
            parsed.add_occurrence(occurrence)
            parsed.findings.append(
                ParseFinding(
                    severity="warning",
                    code="xplan_unknown_section_masked",
                    message=f"Unknown DBMS_XPLAN section was masked: {heading}",
                    start=match.start(),
                    end=content_end,
                )
            )

    # Reconcile generic outline identifiers with symbols already known from SQL,
    # plan Name columns and predicate sections.
    semantic_by_canonical: dict[str, set[EntityType]] = {}
    for item in parsed.occurrences:
        if item.entity_type != EntityType.IDENTIFIER:
            semantic_by_canonical.setdefault(item.canonical, set()).add(item.entity_type)
    reconciled: list[TextOccurrence] = []
    for item in parsed.occurrences:
        choices = semantic_by_canonical.get(item.canonical, set())
        choices = {choice for choice in choices if choice != EntityType.QUERY_BLOCK}
        if item.entity_type == EntityType.IDENTIFIER and len(choices) == 1:
            entity_type = next(iter(choices))
            semantic_quoted = item.canonical.startswith("Q:")
            reconciled.append(
                TextOccurrence(
                    start=item.start,
                    end=item.end,
                    entity_type=entity_type,
                    original=item.original,
                    canonical=canonical_value(entity_type, item.original, quoted=semantic_quoted),
                    provenance=item.provenance,
                    confidence=item.confidence,
                    quoting_context=item.quoting_context,
                    priority=0,
                )
            )
        else:
            reconciled.append(item)
    parsed.occurrences = reconciled

    if not parsed.occurrences:
        parsed.findings.append(
            ParseFinding(
                severity="warning",
                code="xplan_no_sensitive_fields_detected",
                message="DBMS_XPLAN structure was detected, but no replaceable values were found.",
            )
        )
    parsed.finalize()
    return parsed

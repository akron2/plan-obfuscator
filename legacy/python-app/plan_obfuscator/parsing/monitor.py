from __future__ import annotations

import re

from .sql import parse_sql
from .types import ArtifactKind, EntityType, ParsedArtifact, ParseFinding, TextOccurrence
from .utils import cell_bounds, iter_lines_with_offsets, make_occurrence, trimmed_bounds

SENSITIVE_GLOBAL_FIELDS: dict[str, EntityType] = {
    "instance id": EntityType.SESSION_ID,
    "session": EntityType.SESSION_ID,
    "sql id": EntityType.SQL_ID,
    "sql execution id": EntityType.EXECUTION_ID,
    "module/action": EntityType.MODULE,
    "service": EntityType.SERVICE,
    "program": EntityType.PROGRAM,
    "database": EntityType.DATABASE,
    "db name": EntityType.DATABASE,
    "db unique name": EntityType.DATABASE,
    "container": EntityType.DATABASE,
    "host": EntityType.HOST,
    "user": EntityType.USER,
    "sql plan hash value": EntityType.PLAN_HASH,
    "plan hash value": EntityType.PLAN_HASH,
    "sql full plan hash value": EntityType.PLAN_HASH,
    "sql exec id": EntityType.EXECUTION_ID,
    "session id": EntityType.SESSION_ID,
    "session serial": EntityType.SESSION_ID,
    "dbop name": EntityType.IDENTIFIER,
    "dbop execution id": EntityType.EXECUTION_ID,
    "pdb name": EntityType.DATABASE,
    "con name": EntityType.DATABASE,
    "client identifier": EntityType.IDENTIFIER,
    "client info": EntityType.IDENTIFIER,
    "machine": EntityType.HOST,
    "os user": EntityType.USER,
    "object id": EntityType.OBJECT_ID,
    "object#": EntityType.OBJECT_ID,
    "dbid": EntityType.DATABASE,
    "database id": EntityType.DATABASE,
}

SAFE_GLOBAL_FIELDS = {
    "status",
    "execution started",
    "sql execution start",
    "first refresh time",
    "last refresh time",
    "duration",
    "fetch calls",
    "dop",
    "degree of parallelism",
    "px servers allocated",
    "report level",
}

SENSITIVE_TABLE_COLUMNS: dict[str, EntityType] = {
    "instance": EntityType.SESSION_ID,
    "instance id": EntityType.SESSION_ID,
    "session": EntityType.SESSION_ID,
    "session id": EntityType.SESSION_ID,
    "session serial": EntityType.SESSION_ID,
    "user": EntityType.USER,
    "username": EntityType.USER,
    "host": EntityType.HOST,
    "machine": EntityType.HOST,
    "process": EntityType.PROGRAM,
    "program": EntityType.PROGRAM,
    "module": EntityType.MODULE,
    "action": EntityType.ACTION,
    "service": EntityType.SERVICE,
    "server#": EntityType.SESSION_ID,
}


SECTION_RE = re.compile(
    r"(?im)^\s*(SQL Text|Global Information|Global Stats|SQL Plan Monitoring Details|"
    r"Parallel Execution Details|Activity|Metrics|Bind Variables|Binds)"
    r"(?:\s*\([^\r\n]*\))?\s*$"
)

GENERIC_SECTION_RE = re.compile(
    r"(?im)^(?P<heading>[A-Za-z][^\r\n]{0,140})[ \t]*\r?\n"
    r"(?P<separator>[-=]{3,})[ \t]*$"
)


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


def _sections(text: str) -> list[tuple[str, int, int, str]]:
    matches = list(SECTION_RE.finditer(text))
    result: list[tuple[str, int, int, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        newline = re.match(r"(?:\r\n|\n|\r)", text[start:])
        if newline:
            start += newline.end()
        separator = re.match(r"[-]+\s*(?:\r\n|\n|\r)", text[start:])
        if separator:
            start += separator.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result.append((match.group(1), start, end, match.group(0)))
    return result


def _sql_text(parsed: ParsedArtifact, start: int, end: int) -> None:
    region = parsed.source_text[start:end]
    stripped = region.strip()
    if not stripped:
        return
    local_offset = len(region) - len(region.lstrip())
    sql_start = start + local_offset
    local = parse_sql(stripped)
    parsed.extracted_sql.append(stripped)
    for item in local.occurrences:
        parsed.add_occurrence(_shift(item, sql_start, parsed.source_text))
    parsed.findings.extend(local.findings)


def _global_information(parsed: ParsedArtifact, start: int, end: int) -> None:
    region = parsed.source_text[start:end]
    for line_offset, line in iter_lines_with_offsets(region):
        match = re.match(r"\s*([^:]+?)\s*:\s*(.*?)\s*(?:\r?\n)?$", line)
        if not match:
            continue
        label = re.sub(r"\s+", " ", match.group(1)).strip().lower()
        entity_type = SENSITIVE_GLOBAL_FIELDS.get(label)
        if entity_type is None and label in SAFE_GLOBAL_FIELDS:
            continue
        value = match.group(2)
        if not value or value == "-":
            continue
        if entity_type is None:
            entity_type = EntityType.FRAGMENT
            parsed.findings.append(
                ParseFinding(
                    severity="warning",
                    code="sql_monitor_unknown_global_field_masked",
                    message=(
                        "Unknown Global Information field was masked: "
                        f"{match.group(1).strip()}"
                    ),
                    start=start + line_offset,
                    end=start + line_offset + len(line.rstrip("\r\n")),
                )
            )
        value_start = start + line_offset + match.start(2)
        value_end = start + line_offset + match.end(2)
        occurrence = make_occurrence(
            parsed.source_text,
            value_start,
            value_end,
            entity_type,
            "sql_monitor_global",
        )
        if occurrence:
            parsed.add_occurrence(occurrence)


def _plan_table(parsed: ParsedArtifact, start: int, end: int, heading: str) -> None:
    heading_start = start - len(heading)
    heading_prefix = parsed.source_text[max(0, heading_start - 80) : start]
    for match in re.finditer(r"(?i)Plan Hash Value\s*=\s*(\d+)", heading_prefix):
        absolute = max(0, heading_start - 80) + match.start(1)
        occurrence = make_occurrence(
            parsed.source_text,
            absolute,
            max(0, heading_start - 80) + match.end(1),
            EntityType.PLAN_HASH,
            "sql_monitor_plan_hash",
        )
        if occurrence:
            parsed.add_occurrence(occurrence)

    lines = list(iter_lines_with_offsets(parsed.source_text[start:end]))
    header_index = None
    headers: list[str] = []
    header_bounds: list[tuple[int, int]] = []
    for index, (_offset, line) in enumerate(lines):
        if "|" in line and re.search(r"\|\s*Id\s*\|", line, re.I):
            header_bounds = cell_bounds(line)
            headers = [line[left:right].strip().lower() for left, right in header_bounds]
            if "operation" in headers and "name" in headers:
                header_index = index
                break
    if header_index is None:
        return
    operation_index = headers.index("operation")
    name_index = headers.index("name")
    identity_columns = {
        column_index: (
            EntityType.OBJECT_ID
            if header in {"object id", "object#"}
            else EntityType.SESSION_ID
        )
        for column_index, header in enumerate(headers)
        if header in {"object id", "object#", "inst", "instance", "instance id"}
    }
    for line_offset, line in lines[header_index + 1 :]:
        if not line.lstrip().startswith("|"):
            continue
        bounds = cell_bounds(line)
        if len(bounds) <= max(operation_index, name_index):
            continue
        for column_index, identity_type in identity_columns.items():
            if column_index >= len(bounds):
                continue
            identity_bounds = trimmed_bounds(line, *bounds[column_index])
            if not identity_bounds:
                continue
            identity_occurrence = make_occurrence(
                parsed.source_text,
                start + line_offset + identity_bounds[0],
                start + line_offset + identity_bounds[1],
                identity_type,
                "sql_monitor_plan_identity",
            )
            if identity_occurrence:
                parsed.add_occurrence(identity_occurrence)
        operation = line[bounds[operation_index][0] : bounds[operation_index][1]].strip().upper()
        value_bounds = trimmed_bounds(line, *bounds[name_index])
        if not value_bounds:
            continue
        local_start, local_end = value_bounds
        absolute_start = start + line_offset + local_start
        absolute_end = start + line_offset + local_end
        if "INDEX" in operation:
            entity_type = EntityType.INDEX
        elif "VIEW" in operation:
            entity_type = EntityType.VIEW
        elif "TABLE" in operation:
            entity_type = EntityType.TABLE
        else:
            entity_type = EntityType.IDENTIFIER
        raw_name = parsed.source_text[absolute_start:absolute_end]
        parts = list(re.finditer(r'"(?:""|[^"])+"|[A-Za-z_$#][A-Za-z0-9_$#]*', raw_name))
        if not parts:
            parts = [re.match(r".+", raw_name)] if raw_name else []
        for part_index, part in enumerate(parts):
            if part is None:
                continue
            part_start = absolute_start + part.start()
            part_end = absolute_start + part.end()
            raw_part = part.group(0)
            quoted = raw_part.startswith('"') and raw_part.endswith('"')
            if quoted:
                part_start += 1
                part_end -= 1
            part_type = EntityType.SCHEMA if part_index < len(parts) - 1 else entity_type
            occurrence = make_occurrence(
                parsed.source_text,
                part_start,
                part_end,
                part_type,
                "sql_monitor_plan_name",
                quoted=quoted,
                quoting_context="quoted_identifier" if quoted else "none",
            )
            if occurrence:
                parsed.add_occurrence(occurrence)


def _sensitive_table_columns(parsed: ParsedArtifact, start: int, end: int) -> None:
    lines = list(iter_lines_with_offsets(parsed.source_text[start:end]))
    header_index: int | None = None
    sensitive_indexes: dict[int, EntityType] = {}
    for index, (_line_offset, line) in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        bounds = cell_bounds(line)
        headers = [re.sub(r"\s+", " ", line[left:right]).strip().lower() for left, right in bounds]
        matches = {
            column_index: SENSITIVE_TABLE_COLUMNS[header]
            for column_index, header in enumerate(headers)
            if header in SENSITIVE_TABLE_COLUMNS
        }
        if matches:
            header_index = index
            sensitive_indexes = matches
            break
    if header_index is None:
        return
    for line_offset, line in lines[header_index + 1 :]:
        if not line.lstrip().startswith("|"):
            continue
        bounds = cell_bounds(line)
        for column_index, entity_type in sensitive_indexes.items():
            if column_index >= len(bounds):
                continue
            value_bounds = trimmed_bounds(line, *bounds[column_index])
            if not value_bounds:
                continue
            local_start, local_end = value_bounds
            occurrence = make_occurrence(
                parsed.source_text,
                start + line_offset + local_start,
                start + line_offset + local_end,
                entity_type,
                "sql_monitor_sensitive_table",
            )
            if occurrence:
                parsed.add_occurrence(occurrence)


def _bind_section(parsed: ParsedArtifact, start: int, end: int) -> None:
    region = parsed.source_text[start:end]
    for match in re.finditer(r"(?im)(:[A-Za-z0-9_$#]+).*?(?:=|:)\s*(.+?)\s*$", region):
        occurrence = make_occurrence(
            parsed.source_text,
            start + match.start(1) + 1,
            start + match.end(1),
            EntityType.BIND,
            "sql_monitor_bind",
            quoting_context="bind",
        )
        if occurrence:
            parsed.add_occurrence(occurrence)
        value = match.group(2)
        if value and value != "NULL":
            value_start = start + match.start(2)
            value_end = start + match.end(2)
            if value.startswith("'") and value.endswith("'") and len(value) >= 2:
                value_start += 1
                value_end -= 1
                entity_type = EntityType.STRING_LITERAL
                quoting_context = "string"
            elif re.fullmatch(r"[-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?", value):
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
                "sql_monitor_bind_value",
                quoting_context=quoting_context,
            )
            if occurrence:
                parsed.add_occurrence(occurrence)


def parse_sql_monitor(text: str) -> ParsedArtifact:
    parsed = ParsedArtifact(kind=ArtifactKind.SQL_MONITOR, source_text=text)
    sections = _sections(text)
    for name, start, end, heading in sections:
        normalized = name.lower()
        if normalized == "sql text":
            _sql_text(parsed, start, end)
        elif normalized == "global information":
            _global_information(parsed, start, end)
        elif normalized == "sql plan monitoring details":
            _plan_table(parsed, start, end, heading)
        elif normalized in {"bind variables", "binds"}:
            _bind_section(parsed, start, end)
        elif normalized in {"parallel execution details", "activity"}:
            _sensitive_table_columns(parsed, start, end)

    for match in re.finditer(r"(?i)Plan Hash Value\s*=\s*(\d+)", text):
        occurrence = make_occurrence(
            text,
            match.start(1),
            match.end(1),
            EntityType.PLAN_HASH,
            "sql_monitor_plan_hash",
        )
        if occurrence:
            parsed.add_occurrence(occurrence)

    generic_matches = list(GENERIC_SECTION_RE.finditer(text))
    known_names = {
        "sql text",
        "global information",
        "global stats",
        "sql plan monitoring details",
        "parallel execution details",
        "activity",
        "metrics",
        "bind variables",
        "binds",
    }
    for index, match in enumerate(generic_matches):
        heading = re.sub(r"\s*\([^\r\n]*\)\s*$", "", match.group("heading")).strip()
        if heading.lower() in known_names:
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
            "sql_monitor_unknown_section",
            confidence=0.4,
            priority=110,
        )
        if occurrence:
            parsed.add_occurrence(occurrence)
            parsed.findings.append(
                ParseFinding(
                    severity="warning",
                    code="sql_monitor_unknown_section_masked",
                    message=f"Unknown SQL Monitor section was masked: {heading}",
                    start=match.start(),
                    end=content_end,
                )
            )

    if not sections:
        parsed.findings.append(
            ParseFinding(
                severity="warning",
                code="sql_monitor_sections_not_found",
                message=(
                    "SQL Monitor marker was detected but standard TEXT sections "
                    "were not found."
                ),
            )
        )
    parsed.finalize()
    return parsed

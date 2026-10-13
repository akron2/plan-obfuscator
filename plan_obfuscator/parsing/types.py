from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ArtifactKind(StrEnum):
    SQL = "sql"
    XPLAN = "xplan"
    SQL_MONITOR = "sql_monitor"
    OUTLINE = "outline"
    PREDICATES = "predicates"
    UNKNOWN = "unknown"


class EntityType(StrEnum):
    SCHEMA = "schema"
    TABLE = "table"
    VIEW = "view"
    COLUMN = "column"
    INDEX = "index"
    CONSTRAINT = "constraint"
    PARTITION = "partition"
    ALIAS = "alias"
    QUERY_BLOCK = "query_block"
    DB_LINK = "db_link"
    USER = "user"
    DATABASE = "database"
    SERVICE = "service"
    HOST = "host"
    PROGRAM = "program"
    MODULE = "module"
    ACTION = "action"
    SQL_ID = "sql_id"
    PLAN_HASH = "plan_hash"
    EXECUTION_ID = "execution_id"
    SESSION_ID = "session_id"
    OBJECT_ID = "object_id"
    BIND = "bind"
    STRING_LITERAL = "string_literal"
    NUMBER_LITERAL = "number_literal"
    DATETIME_LITERAL = "datetime_literal"
    COMMENT = "comment"
    FUNCTION = "function"
    IDENTIFIER = "identifier"
    FRAGMENT = "fragment"


ENTITY_CODES: dict[EntityType, str] = {
    EntityType.SCHEMA: "SCH",
    EntityType.TABLE: "TBL",
    EntityType.VIEW: "VIW",
    EntityType.COLUMN: "COL",
    EntityType.INDEX: "IDX",
    EntityType.CONSTRAINT: "CST",
    EntityType.PARTITION: "PRT",
    EntityType.ALIAS: "ALS",
    EntityType.QUERY_BLOCK: "QB",
    EntityType.DB_LINK: "DBL",
    EntityType.USER: "USR",
    EntityType.DATABASE: "DB",
    EntityType.SERVICE: "SVC",
    EntityType.HOST: "HST",
    EntityType.PROGRAM: "PRG",
    EntityType.MODULE: "MOD",
    EntityType.ACTION: "ACT",
    EntityType.SQL_ID: "SQLID",
    EntityType.PLAN_HASH: "PHV",
    EntityType.EXECUTION_ID: "EXE",
    EntityType.SESSION_ID: "SID",
    EntityType.OBJECT_ID: "OID",
    EntityType.BIND: "BND",
    EntityType.STRING_LITERAL: "STR",
    EntityType.NUMBER_LITERAL: "NUM",
    EntityType.DATETIME_LITERAL: "DT",
    EntityType.COMMENT: "CMT",
    EntityType.FUNCTION: "FUN",
    EntityType.IDENTIFIER: "IDN",
    EntityType.FRAGMENT: "FRG",
}


ENTITY_PRIORITY: dict[EntityType, int] = {
    EntityType.STRING_LITERAL: 100,
    EntityType.NUMBER_LITERAL: 100,
    EntityType.DATETIME_LITERAL: 100,
    EntityType.COMMENT: 100,
    EntityType.QUERY_BLOCK: 95,
    EntityType.SQL_ID: 95,
    EntityType.PLAN_HASH: 95,
    EntityType.EXECUTION_ID: 95,
    EntityType.SESSION_ID: 95,
    EntityType.BIND: 94,
    EntityType.SCHEMA: 90,
    EntityType.TABLE: 85,
    EntityType.VIEW: 85,
    EntityType.INDEX: 85,
    EntityType.COLUMN: 80,
    EntityType.CONSTRAINT: 80,
    EntityType.PARTITION: 80,
    EntityType.DB_LINK: 80,
    EntityType.ALIAS: 75,
    EntityType.USER: 75,
    EntityType.DATABASE: 75,
    EntityType.SERVICE: 75,
    EntityType.HOST: 75,
    EntityType.PROGRAM: 75,
    EntityType.MODULE: 75,
    EntityType.ACTION: 75,
    EntityType.OBJECT_ID: 75,
    EntityType.FUNCTION: 60,
    EntityType.IDENTIFIER: 50,
    EntityType.FRAGMENT: 40,
}


@dataclass(slots=True, frozen=True)
class TextOccurrence:
    start: int
    end: int
    entity_type: EntityType
    original: str
    canonical: str
    provenance: str
    confidence: float = 1.0
    quoting_context: str = "none"
    priority: int = 0

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError(f"Invalid occurrence range: {self.start}:{self.end}")
        if not self.original:
            raise ValueError("Occurrence original value cannot be empty")
        if not self.priority:
            object.__setattr__(self, "priority", ENTITY_PRIORITY[self.entity_type])


@dataclass(slots=True, frozen=True)
class ParseFinding:
    severity: str
    code: str
    message: str
    start: int | None = None
    end: int | None = None


@dataclass(slots=True)
class ParsedArtifact:
    kind: ArtifactKind
    source_text: str
    occurrences: list[TextOccurrence] = field(default_factory=list)
    findings: list[ParseFinding] = field(default_factory=list)
    extracted_sql: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)

    def add_occurrence(self, occurrence: TextOccurrence) -> None:
        if occurrence.end > len(self.source_text):
            raise ValueError("Occurrence is outside of source text")
        if self.source_text[occurrence.start : occurrence.end] != occurrence.original:
            raise ValueError(
                "Occurrence original does not match source text at "
                f"{occurrence.start}:{occurrence.end}"
            )
        self.occurrences.append(occurrence)

    def finalize(self) -> None:
        """Deduplicate occurrences and remove overlapping lower-priority spans."""

        best_by_range: dict[tuple[int, int], TextOccurrence] = {}
        for item in self.occurrences:
            key = (item.start, item.end)
            current = best_by_range.get(key)
            if current is None or item.priority > current.priority:
                best_by_range[key] = item

        candidates = sorted(
            best_by_range.values(),
            key=lambda item: (item.start, -item.priority, -(item.end - item.start)),
        )
        accepted: list[TextOccurrence] = []
        for candidate in candidates:
            overlaps = [
                item
                for item in accepted
                if candidate.start < item.end and item.start < candidate.end
            ]
            if not overlaps:
                accepted.append(candidate)
                continue

            if all(candidate.priority > item.priority for item in overlaps):
                accepted = [item for item in accepted if item not in overlaps]
                accepted.append(candidate)

        self.occurrences = sorted(accepted, key=lambda item: (item.start, item.end))

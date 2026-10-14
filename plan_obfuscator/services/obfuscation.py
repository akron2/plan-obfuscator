from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import insert, select
from sqlalchemy.orm import Session, selectinload

from ..models import (
    Artifact,
    ArtifactRevision,
    Case,
    Finding,
    ObfuscationRun,
    ReplacementOccurrence,
    RunArtifact,
    Symbol,
    new_uuid,
    utc_now,
)
from ..parsing import ArtifactKind, EntityType, ParsedArtifact, TextOccurrence, parse_artifact
from ..parsing.types import ParseFinding
from ..parsing.utils import canonical_value
from .cases import CaseNotFoundError
from .prompt import PromptArtifact, build_prompt
from .tokens import TOKEN_SCHEME_VERSION, SymbolRegistry

PARSER_VERSION = "1"
TYPE_ORDER = {
    ArtifactKind.SQL.value: 0,
    ArtifactKind.XPLAN.value: 1,
    ArtifactKind.SQL_MONITOR.value: 2,
    ArtifactKind.OUTLINE.value: 3,
    ArtifactKind.PREDICATES.value: 4,
    ArtifactKind.UNKNOWN.value: 5,
}

LEAK_CHECK_TYPES = {
    EntityType.SCHEMA.value,
    EntityType.TABLE.value,
    EntityType.VIEW.value,
    EntityType.COLUMN.value,
    EntityType.INDEX.value,
    EntityType.CONSTRAINT.value,
    EntityType.PARTITION.value,
    EntityType.DB_LINK.value,
    EntityType.USER.value,
    EntityType.DATABASE.value,
    EntityType.SERVICE.value,
    EntityType.HOST.value,
    EntityType.PROGRAM.value,
    EntityType.MODULE.value,
    EntityType.ACTION.value,
    EntityType.SQL_ID.value,
}

LEAK_CHECK_ALLOWLIST = {
    "TABLE",
    "INDEX",
    "USER",
    "SESSION",
    "SERVICE",
    "PROGRAM",
    "MODULE",
    "ACTION",
    "DATABASE",
    "COLUMN",
    "VIEW",
    "DUAL",
    "STATUS",
    "NAME",
    "TYPE",
    "ID",
    "TIME",
    "ROWS",
    "BYTES",
    "COST",
}

WORD_TOKEN_RE = re.compile(r"[A-Za-z0-9_$#]+")


@dataclass(slots=True, frozen=True)
class AppliedReplacement:
    occurrence: TextOccurrence
    symbol: Symbol
    output_start: int
    output_end: int


@dataclass(slots=True, frozen=True)
class ObfuscatedText:
    text: str
    replacements: list[AppliedReplacement]


def apply_replacements(
    source: str,
    replacements: list[tuple[TextOccurrence, Symbol]],
) -> ObfuscatedText:
    ordered = sorted(replacements, key=lambda pair: pair[0].start)
    parts: list[str] = []
    applied: list[AppliedReplacement] = []
    source_cursor = 0
    output_cursor = 0
    for occurrence, symbol in ordered:
        if occurrence.start < source_cursor:
            raise ValueError("Overlapping replacement occurrences")
        unchanged = source[source_cursor : occurrence.start]
        parts.append(unchanged)
        output_cursor += len(unchanged)
        output_start = output_cursor
        parts.append(symbol.marker)
        output_cursor += len(symbol.marker)
        applied.append(
            AppliedReplacement(
                occurrence=occurrence,
                symbol=symbol,
                output_start=output_start,
                output_end=output_cursor,
            )
        )
        source_cursor = occurrence.end
    parts.append(source[source_cursor:])
    return ObfuscatedText(text="".join(parts), replacements=applied)


def exact_restore(obfuscated: str, replacements: list[AppliedReplacement]) -> str:
    ordered = sorted(replacements, key=lambda item: item.output_start)
    parts: list[str] = []
    cursor = 0
    for item in ordered:
        if item.output_start < cursor:
            raise ValueError("Obfuscated text no longer matches replacement manifest")
        if obfuscated[item.output_start : item.output_end] != item.symbol.marker:
            raise ValueError("Obfuscated text no longer matches replacement manifest")
        parts.append(obfuscated[cursor : item.output_start])
        parts.append(item.occurrence.original)
        cursor = item.output_end
    parts.append(obfuscated[cursor:])
    return "".join(parts)


def _reconcile_across_artifacts(parsed_items: list[ParsedArtifact]) -> None:
    semantic_by_canonical: dict[str, set[EntityType]] = {}
    for parsed in parsed_items:
        for item in parsed.occurrences:
            if item.entity_type not in {EntityType.IDENTIFIER, EntityType.FRAGMENT}:
                semantic_by_canonical.setdefault(item.canonical, set()).add(item.entity_type)

    for parsed in parsed_items:
        updated: list[TextOccurrence] = []
        for item in parsed.occurrences:
            choices = semantic_by_canonical.get(item.canonical, set())
            if item.entity_type == EntityType.IDENTIFIER and len(choices) == 1:
                entity_type = next(iter(choices))
                quoted = item.canonical.startswith("Q:")
                updated.append(
                    TextOccurrence(
                        start=item.start,
                        end=item.end,
                        entity_type=entity_type,
                        original=item.original,
                        canonical=canonical_value(entity_type, item.original, quoted=quoted),
                        provenance=item.provenance,
                        confidence=item.confidence,
                        quoting_context=item.quoting_context,
                    )
                )
            else:
                updated.append(item)
        parsed.occurrences = updated
        parsed.finalize()


def _post_transform_findings(
    case: Case,
    source: str,
    obfuscated: ObfuscatedText,
) -> list[ParseFinding]:
    findings: list[ParseFinding] = []
    if re.search(rf"(?i)\bOBF_{re.escape(case.token_prefix)}_", source):
        findings.append(
            ParseFinding(
                severity="warning",
                code="input_marker_collision",
                message="Input already contains a marker prefix belonging to this case.",
            )
        )

    word_positions: dict[str, tuple[int, int]] | None = None
    seen: set[tuple[str, str]] = set()
    for replacement in obfuscated.replacements:
        symbol = replacement.symbol
        original = symbol.preferred_original
        upper = original.upper()
        key = (symbol.entity_type, upper)
        if key in seen or symbol.entity_type not in LEAK_CHECK_TYPES:
            continue
        seen.add(key)
        if len(original) < 3 or upper in LEAK_CHECK_ALLOWLIST:
            continue
        if WORD_TOKEN_RE.fullmatch(original):
            if word_positions is None:
                word_positions = {}
                for word_match in WORD_TOKEN_RE.finditer(obfuscated.text):
                    token_upper = word_match.group(0).upper()
                    if token_upper not in word_positions:
                        word_positions[token_upper] = (word_match.start(), word_match.end())
            span = word_positions.get(upper)
        else:
            pattern = re.compile(
                rf"(?<![A-Za-z0-9_$#]){re.escape(original)}(?![A-Za-z0-9_$#])",
                re.IGNORECASE,
            )
            match = pattern.search(obfuscated.text)
            span = (match.start(), match.end()) if match else None
        if span is not None:
            findings.append(
                ParseFinding(
                    severity="warning",
                    code="possible_unmasked_symbol",
                    message=(
                        f"A known {symbol.entity_type} value may remain outside parsed "
                        "occurrences. The output is safe to review before copying."
                    ),
                    start=span[0],
                    end=span[1],
                )
            )
    return findings


class ObfuscationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _load_case(self, case_id: str) -> Case:
        case = self.session.scalar(
            select(Case)
            .where(Case.id == case_id)
            .execution_options(populate_existing=True)
            .options(
                selectinload(Case.artifacts).selectinload(Artifact.revisions),
                selectinload(Case.symbols),
            )
        )
        if case is None:
            raise CaseNotFoundError(case_id)
        return case

    @staticmethod
    def _current_revision(artifact: Artifact) -> ArtifactRevision:
        for revision in artifact.revisions:
            if revision.id == artifact.current_revision_id:
                return revision
        if not artifact.revisions:
            raise ValueError(f"Artifact {artifact.id} has no revisions")
        return max(artifact.revisions, key=lambda revision: revision.version)

    def run(self, case_id: str, question: str = "") -> ObfuscationRun:
        case = self._load_case(case_id)
        active_artifacts = [artifact for artifact in case.artifacts if artifact.is_active]
        if not active_artifacts:
            raise ValueError("Case has no artifacts")

        ordered_artifacts = sorted(
            active_artifacts,
            key=lambda item: (TYPE_ORDER.get(item.detected_type, 99), item.created_at),
        )
        revisions = [self._current_revision(artifact) for artifact in ordered_artifacts]
        parsed_items = [
            parse_artifact(revision.source_text, artifact.detected_type)
            for artifact, revision in zip(ordered_artifacts, revisions, strict=True)
        ]
        _reconcile_across_artifacts(parsed_items)

        registry = SymbolRegistry(self.session, case)
        transformed: list[tuple[Artifact, ArtifactRevision, ParsedArtifact, ObfuscatedText]] = []
        for artifact, revision, parsed in zip(
            ordered_artifacts, revisions, parsed_items, strict=True
        ):
            pairs = [(item, registry.get_or_create(item)) for item in parsed.occurrences]
            obfuscated = apply_replacements(revision.source_text, pairs)
            if exact_restore(obfuscated.text, obfuscated.replacements) != revision.source_text:
                raise AssertionError("Exact round-trip validation failed")
            parsed.findings.extend(_post_transform_findings(case, revision.source_text, obfuscated))
            transformed.append((artifact, revision, parsed, obfuscated))

        prompt = build_prompt(
            [
                PromptArtifact(
                    artifact_type=artifact.detected_type,
                    display_name=artifact.display_name,
                    text=obfuscated.text,
                )
                for artifact, _revision, _parsed, obfuscated in transformed
            ],
            question=question,
        )
        finding_count = sum(len(parsed.findings) for _a, _r, parsed, _o in transformed)
        run = ObfuscationRun(
            case_id=case.id,
            question=question.strip(),
            prompt_text=prompt,
            parser_version=PARSER_VERSION,
            token_scheme_version=TOKEN_SCHEME_VERSION,
            settings_snapshot={
                "oracle_version": case.oracle_version,
                "artifact_count": len(transformed),
                "automatic_mapping": True,
            },
            validation_status="warning" if finding_count else "ok",
        )
        self.session.add(run)
        case.updated_at = utc_now()
        self.session.flush()

        replacement_rows: list[dict[str, object]] = []
        for position, (artifact, revision, parsed, obfuscated) in enumerate(transformed):
            run_artifact = RunArtifact(
                id=new_uuid(),
                run_id=run.id,
                artifact_revision_id=revision.id,
                position=position,
                artifact_type=artifact.detected_type,
                display_name=artifact.display_name,
                obfuscated_text=obfuscated.text,
            )
            self.session.add(run_artifact)
            for item in obfuscated.replacements:
                replacement_rows.append(
                    {
                        "id": new_uuid(),
                        "run_artifact_id": run_artifact.id,
                        "symbol_id": item.symbol.id,
                        "source_start": item.occurrence.start,
                        "source_end": item.occurrence.end,
                        "output_start": item.output_start,
                        "output_end": item.output_end,
                        "original_lexeme": item.occurrence.original,
                        "marker": item.symbol.marker,
                        "quoting_context": item.occurrence.quoting_context,
                    }
                )
            for parse_finding in parsed.findings:
                self.session.add(
                    Finding(
                        run_id=run.id,
                        artifact_revision_id=revision.id,
                        severity=parse_finding.severity,
                        code=parse_finding.code,
                        message=parse_finding.message,
                        start_offset=parse_finding.start,
                        end_offset=parse_finding.end,
                    )
                )

        self.session.flush()
        if replacement_rows:
            self.session.execute(insert(ReplacementOccurrence), replacement_rows)
        self.session.commit()
        return self.get_run(run.id, include_replacements=False)

    def get_run(self, run_id: str, *, include_replacements: bool = True) -> ObfuscationRun:
        run_artifacts_loader = selectinload(ObfuscationRun.run_artifacts)
        if include_replacements:
            run_artifacts_loader = run_artifacts_loader.selectinload(RunArtifact.replacements)
        run = self.session.scalar(
            select(ObfuscationRun)
            .where(ObfuscationRun.id == run_id)
            .options(
                run_artifacts_loader,
                selectinload(ObfuscationRun.responses),
                selectinload(ObfuscationRun.findings),
                selectinload(ObfuscationRun.case).selectinload(Case.symbols),
            )
        )
        if run is None:
            raise LookupError(run_id)
        return run

    def restore_run_artifact(self, run_artifact_id: str) -> str:
        run_artifact = self.session.scalar(
            select(RunArtifact)
            .where(RunArtifact.id == run_artifact_id)
            .options(
                selectinload(RunArtifact.replacements).selectinload(ReplacementOccurrence.symbol),
                selectinload(RunArtifact.revision),
            )
        )
        if run_artifact is None:
            raise LookupError(run_artifact_id)
        obfuscated = run_artifact.obfuscated_text
        ordered = sorted(
            run_artifact.replacements,
            key=lambda replacement: replacement.output_start,
        )
        parts: list[str] = []
        cursor = 0
        for item in ordered:
            if item.output_start < cursor:
                raise ValueError("Stored obfuscated text does not match its manifest")
            if obfuscated[item.output_start : item.output_end].upper() != item.marker.upper():
                raise ValueError("Stored obfuscated text does not match its manifest")
            parts.append(obfuscated[cursor : item.output_start])
            parts.append(item.original_lexeme)
            cursor = item.output_end
        parts.append(obfuscated[cursor:])
        return "".join(parts)

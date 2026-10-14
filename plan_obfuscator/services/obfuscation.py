from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, defer, selectinload

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
from .tokens import TOKEN_SCHEME_VERSION, SymbolEntry, SymbolRegistry

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

ENTITY_MASK_PRIORITY = {
    EntityType.TABLE.value: 1,
    EntityType.VIEW.value: 2,
    EntityType.INDEX.value: 3,
    EntityType.SCHEMA.value: 4,
    EntityType.CONSTRAINT.value: 5,
    EntityType.PARTITION.value: 6,
    EntityType.DB_LINK.value: 7,
    EntityType.SQL_ID.value: 8,
    EntityType.COLUMN.value: 9,
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
    symbol: Symbol | SymbolEntry
    output_start: int
    output_end: int


@dataclass(slots=True, frozen=True)
class ObfuscatedText:
    text: str
    replacements: list[AppliedReplacement]


def apply_replacements(
    source: str,
    replacements: list[tuple[TextOccurrence, Symbol | SymbolEntry]],
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


def mask_question_symbols(
    question: str,
    token_prefix: str,
    symbols: Iterable[Symbol | SymbolEntry],
) -> str:
    if not question or not question.strip():
        return ""
    eligible = sorted(
        (sym for sym in symbols if sym.entity_type in LEAK_CHECK_TYPES),
        key=lambda sym: (ENTITY_MASK_PRIORITY.get(sym.entity_type, 50), sym.sequence),
    )
    if not eligible:
        return question

    word_map: dict[str, str] = {}
    complex_candidates: dict[str, tuple[str, str]] = {}
    for sym in eligible:
        raw = sym.preferred_original.strip()
        is_quoted = len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"'
        unquoted = raw[1:-1].replace('""', '"') if is_quoted else raw
        for candidate in (raw, unquoted):
            if len(candidate) < 3:
                continue
            upper = candidate.upper()
            if upper in LEAK_CHECK_ALLOWLIST:
                continue
            if WORD_TOKEN_RE.fullmatch(candidate):
                word_map.setdefault(upper, sym.marker)
            else:
                complex_candidates.setdefault(upper, (candidate, sym.marker))

    result = question
    if complex_candidates:
        for candidate, marker in sorted(
            complex_candidates.values(),
            key=lambda pair: len(pair[0]),
            reverse=True,
        ):
            pattern = re.compile(
                rf"(?<![A-Za-z0-9_$#]){re.escape(candidate)}(?![A-Za-z0-9_$#])",
                re.IGNORECASE,
            )
            result = pattern.sub(marker, result)

    if word_map:
        marker_prefix_upper = f"OBF_{token_prefix}_".upper()

        def _replace_word(match: re.Match[str]) -> str:
            word = match.group(0)
            if len(word) < 3:
                return word
            upper = word.upper()
            if upper.startswith(marker_prefix_upper):
                return word
            return word_map.get(upper, word)

        result = WORD_TOKEN_RE.sub(_replace_word, result)
    return result


def _reconcile_across_artifacts(
    parsed_items: list[ParsedArtifact],
    existing_symbols: Iterable[Symbol | SymbolEntry] = (),
) -> None:
    semantic_by_canonical: dict[str, set[EntityType]] = {}
    for sym in existing_symbols:
        try:
            etype = EntityType(sym.entity_type)
        except ValueError:
            continue
        if etype not in {EntityType.IDENTIFIER, EntityType.FRAGMENT}:
            semantic_by_canonical.setdefault(sym.canonical_value, set()).add(etype)

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
    marker_prefix = f"OBF_{case.token_prefix}_"
    if re.search(rf"(?i)\b{re.escape(marker_prefix)}", source):
        findings.append(
            ParseFinding(
                severity="warning",
                code="input_marker_collision",
                message="Input already contains a marker prefix belonging to this case.",
            )
        )

    word_positions: dict[str, tuple[int, int]] | None = None
    seen: set[tuple[str, str]] = set()
    marker_prefix_upper = marker_prefix.upper()
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
                    token = word_match.group(0)
                    if len(token) < 3:
                        continue
                    token_upper = token.upper()
                    if token_upper.startswith(marker_prefix_upper):
                        continue
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
                selectinload(Case.runs)
                .selectinload(ObfuscationRun.run_artifacts)
                .options(defer(RunArtifact.obfuscated_text)),
                selectinload(Case.runs).selectinload(ObfuscationRun.responses),
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

    @staticmethod
    def _cycle_state(
        case: Case,
    ) -> tuple[list[ObfuscationRun], ObfuscationRun | None, list[Artifact]]:
        ordered_runs = sorted(case.runs, key=lambda item: item.created_at)
        closed_runs = [item for item in ordered_runs if item.responses]
        open_runs = [item for item in ordered_runs if not item.responses]
        open_run = open_runs[-1] if open_runs else None
        sent_revision_ids = {
            ra.artifact_revision_id
            for closed in closed_runs
            for ra in closed.run_artifacts
        }
        unsent_artifacts = sorted(
            [
                artifact
                for artifact in case.artifacts
                if artifact.is_active and artifact.current_revision_id not in sent_revision_ids
            ],
            key=lambda item: item.created_at,
        )
        return closed_runs, open_run, unsent_artifacts

    def _populate_run(
        self,
        case: Case,
        run: ObfuscationRun,
        question_items: list[dict[str, str]],
        ordered_artifacts: list[Artifact],
        *,
        has_closed_runs: bool,
        is_new_run: bool,
    ) -> ObfuscationRun:
        revisions = [self._current_revision(artifact) for artifact in ordered_artifacts]
        existing_ordered = sorted(run.run_artifacts, key=lambda item: item.position)
        artifacts_unchanged = (
            not is_new_run
            and [ra.artifact_revision_id for ra in existing_ordered]
            == [rev.id for rev in revisions]
            and [ra.display_name for ra in existing_ordered]
            == [artifact.display_name for artifact in ordered_artifacts]
        )
        if artifacts_unchanged:
            registry = SymbolRegistry(self.session, case)
            masked_questions = [
                mask_question_symbols(item["text"], case.token_prefix, registry.entries())
                for item in question_items
            ]
            loaded_ras = list(
                self.session.scalars(
                    select(RunArtifact)
                    .where(RunArtifact.run_id == run.id)
                    .execution_options(populate_existing=True)
                    .order_by(RunArtifact.position)
                )
            )
            can_build_prompt = bool(loaded_ras) or (has_closed_runs and bool(masked_questions))
            if can_build_prompt:
                prompt = build_prompt(
                    [
                        PromptArtifact(
                            artifact_type=ra.artifact_type,
                            display_name=ra.display_name,
                            text=ra.obfuscated_text,
                        )
                        for ra in loaded_ras
                    ],
                    questions=masked_questions,
                )
                if run.validation_status == "pending":
                    run.validation_status = "ok"
            else:
                prompt = ""
                run.validation_status = "pending"
            run.question = "\n\n".join(item["text"] for item in question_items)
            run.prompt_text = prompt
            run.settings_snapshot = {
                "oracle_version": case.oracle_version,
                "artifact_count": len(loaded_ras),
                "automatic_mapping": True,
                "questions": question_items,
            }
            case.updated_at = utc_now()
            self.session.commit()
            return self.get_run(run.id, include_replacements=False)

        if not is_new_run:
            self.session.execute(
                delete(ReplacementOccurrence).where(
                    ReplacementOccurrence.run_artifact_id.in_(
                        select(RunArtifact.id).where(RunArtifact.run_id == run.id)
                    )
                )
            )
            self.session.execute(delete(RunArtifact).where(RunArtifact.run_id == run.id))
            self.session.execute(
                delete(Finding).where(Finding.run_id == run.id, Finding.response_id.is_(None))
            )
            self.session.flush()

        registry = SymbolRegistry(self.session, case)
        transformed: list[tuple[Artifact, ArtifactRevision, ParsedArtifact, ObfuscatedText]] = []
        if ordered_artifacts:
            parsed_items = [
                parse_artifact(revision.source_text, artifact.detected_type)
                for artifact, revision in zip(ordered_artifacts, revisions, strict=True)
            ]
            _reconcile_across_artifacts(parsed_items, existing_symbols=registry.entries())

            for artifact, revision, parsed in zip(
                ordered_artifacts, revisions, parsed_items, strict=True
            ):
                pairs = [(item, registry.get_or_create(item)) for item in parsed.occurrences]
                obfuscated = apply_replacements(revision.source_text, pairs)
                if exact_restore(obfuscated.text, obfuscated.replacements) != revision.source_text:
                    raise AssertionError("Exact round-trip validation failed")
                parsed.findings.extend(
                    _post_transform_findings(case, revision.source_text, obfuscated)
                )
                transformed.append((artifact, revision, parsed, obfuscated))

            registry.persist()

        masked_questions = [
            mask_question_symbols(item["text"], case.token_prefix, registry.entries())
            for item in question_items
        ]
        can_build_prompt = bool(transformed) or (has_closed_runs and bool(masked_questions))
        if can_build_prompt:
            prompt = build_prompt(
                [
                    PromptArtifact(
                        artifact_type=artifact.detected_type,
                        display_name=artifact.display_name,
                        text=obfuscated.text,
                    )
                    for artifact, _revision, _parsed, obfuscated in transformed
                ],
                questions=masked_questions,
            )
            finding_count = sum(len(parsed.findings) for _a, _r, parsed, _o in transformed)
            validation_status = "warning" if finding_count else "ok"
        else:
            prompt = ""
            validation_status = "pending"

        run.question = "\n\n".join(item["text"] for item in question_items)
        run.prompt_text = prompt
        run.parser_version = PARSER_VERSION
        run.token_scheme_version = TOKEN_SCHEME_VERSION
        run.settings_snapshot = {
            "oracle_version": case.oracle_version,
            "artifact_count": len(transformed),
            "automatic_mapping": True,
            "questions": question_items,
        }
        run.validation_status = validation_status
        if is_new_run:
            self.session.add(run)
        case.updated_at = utc_now()
        self.session.flush()

        repl_id_prefix = f"{uuid.uuid4().hex[:24]}-"
        repl_seq = 0
        replacement_tuples: list[tuple[object, ...]] = []
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
            run_artifact_id = run_artifact.id
            for item in obfuscated.replacements:
                repl_seq += 1
                replacement_tuples.append(
                    (
                        f"{repl_id_prefix}{repl_seq:08x}",
                        run_artifact_id,
                        item.symbol.id,
                        item.occurrence.start,
                        item.occurrence.end,
                        item.output_start,
                        item.output_end,
                        item.occurrence.original,
                        item.symbol.marker,
                        item.occurrence.quoting_context,
                    )
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
        if replacement_tuples:
            raw_conn = self.session.connection().connection.dbapi_connection
            if raw_conn is not None:
                raw_conn.cursor().executemany(
                    "INSERT INTO replacement_occurrences ("
                    "id, run_artifact_id, symbol_id, source_start, source_end, "
                    "output_start, output_end, original_lexeme, marker, quoting_context"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    replacement_tuples,
                )
        self.session.commit()
        return self.get_run(run.id, include_replacements=False)

    def sync_open_run(self, case_id: str) -> ObfuscationRun | None:
        case = self._load_case(case_id)
        closed_runs, open_run, unsent_artifacts = self._cycle_state(case)
        question_items = list(open_run.question_items) if open_run is not None else []
        if not unsent_artifacts and not question_items:
            if open_run is not None:
                self.session.delete(open_run)
                case.updated_at = utc_now()
                self.session.commit()
            return None

        is_new_run = open_run is None
        target_run = (
            open_run
            if open_run is not None
            else ObfuscationRun(
                id=new_uuid(),
                case_id=case.id,
                question="",
                prompt_text="",
                parser_version=PARSER_VERSION,
                token_scheme_version=TOKEN_SCHEME_VERSION,
                settings_snapshot={},
                validation_status="pending",
            )
        )
        return self._populate_run(
            case,
            target_run,
            question_items,
            unsent_artifacts,
            has_closed_runs=bool(closed_runs),
            is_new_run=is_new_run,
        )

    def add_question(self, case_id: str, question: str) -> ObfuscationRun:
        clean_question = question.strip()
        if not clean_question:
            raise ValueError("Текст вопроса не может быть пустым.")
        case = self._load_case(case_id)
        closed_runs, open_run, unsent_artifacts = self._cycle_state(case)
        question_items = list(open_run.question_items) if open_run is not None else []
        question_items.append(
            {
                "id": new_uuid(),
                "text": clean_question,
                "created_at": utc_now().strftime("%d.%m.%Y %H:%M"),
            }
        )
        is_new_run = open_run is None
        target_run = (
            open_run
            if open_run is not None
            else ObfuscationRun(
                id=new_uuid(),
                case_id=case.id,
                question="",
                prompt_text="",
                parser_version=PARSER_VERSION,
                token_scheme_version=TOKEN_SCHEME_VERSION,
                settings_snapshot={},
                validation_status="pending",
            )
        )
        return self._populate_run(
            case,
            target_run,
            question_items,
            unsent_artifacts,
            has_closed_runs=bool(closed_runs),
            is_new_run=is_new_run,
        )

    def update_question(self, run_id: str, question_id: str, text: str) -> ObfuscationRun:
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("Текст вопроса не может быть пустым.")
        run = self.get_run(run_id, include_replacements=False)
        if run.responses:
            raise ValueError("Нельзя редактировать вопрос в закрытом цикле.")
        case = self._load_case(run.case_id)
        closed_runs, open_run, unsent_artifacts = self._cycle_state(case)
        if open_run is None or open_run.id != run.id:
            raise ValueError("Нельзя редактировать вопрос в закрытом цикле.")
        question_items = list(open_run.question_items)
        updated = False
        for item in question_items:
            if item["id"] == question_id:
                item["text"] = clean_text
                updated = True
                break
        if not updated:
            raise LookupError(question_id)
        return self._populate_run(
            case,
            open_run,
            question_items,
            unsent_artifacts,
            has_closed_runs=bool(closed_runs),
            is_new_run=False,
        )

    def delete_question(self, run_id: str, question_id: str) -> ObfuscationRun | None:
        run = self.get_run(run_id, include_replacements=False)
        if run.responses:
            raise ValueError("Нельзя удалить вопрос из закрытого цикла.")
        case = self._load_case(run.case_id)
        closed_runs, open_run, unsent_artifacts = self._cycle_state(case)
        if open_run is None or open_run.id != run.id:
            raise ValueError("Нельзя удалить вопрос из закрытого цикла.")
        question_items = [item for item in open_run.question_items if item["id"] != question_id]
        if len(question_items) == len(open_run.question_items):
            raise LookupError(question_id)
        if not question_items and not unsent_artifacts:
            self.session.delete(open_run)
            case.updated_at = utc_now()
            self.session.commit()
            return None
        return self._populate_run(
            case,
            open_run,
            question_items,
            unsent_artifacts,
            has_closed_runs=bool(closed_runs),
            is_new_run=False,
        )

    def run(self, case_id: str, question: str = "") -> ObfuscationRun:
        if question.strip():
            return self.add_question(case_id, question.strip())
        synced = self.sync_open_run(case_id)
        if synced is None:
            raise ValueError("Case has no artifacts")
        return synced

    def get_run(self, run_id: str, *, include_replacements: bool = True) -> ObfuscationRun:
        run_artifacts_loader = selectinload(ObfuscationRun.run_artifacts)
        case_loader = selectinload(ObfuscationRun.case)
        if include_replacements:
            run_artifacts_loader = run_artifacts_loader.selectinload(RunArtifact.replacements)
            case_loader = case_loader.selectinload(Case.symbols)
        run = self.session.scalar(
            select(ObfuscationRun)
            .where(ObfuscationRun.id == run_id)
            .execution_options(populate_existing=True)
            .options(
                run_artifacts_loader,
                selectinload(ObfuscationRun.responses),
                selectinload(ObfuscationRun.findings),
                case_loader,
            )
        )
        if run is None:
            raise LookupError(run_id)
        return run

    def restore_run_artifact(self, run_artifact_id: str) -> str:
        obfuscated = self.session.scalar(
            select(RunArtifact.obfuscated_text).where(RunArtifact.id == run_artifact_id)
        )
        if obfuscated is None:
            raise LookupError(run_artifact_id)
        rows = self.session.execute(
            select(
                ReplacementOccurrence.output_start,
                ReplacementOccurrence.output_end,
                ReplacementOccurrence.marker,
                ReplacementOccurrence.original_lexeme,
            )
            .where(ReplacementOccurrence.run_artifact_id == run_artifact_id)
            .order_by(ReplacementOccurrence.output_start)
        ).all()
        parts: list[str] = []
        cursor = 0
        for output_start, output_end, marker, original_lexeme in rows:
            if output_start < cursor:
                raise ValueError("Stored obfuscated text does not match its manifest")
            if obfuscated[output_start:output_end].upper() != marker.upper():
                raise ValueError("Stored obfuscated text does not match its manifest")
            parts.append(obfuscated[cursor:output_start])
            parts.append(original_lexeme)
            cursor = output_end
        parts.append(obfuscated[cursor:])
        return "".join(parts)



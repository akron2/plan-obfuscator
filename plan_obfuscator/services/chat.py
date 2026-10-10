from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Case, ObfuscationRun, Response
from ..parsing import ArtifactKind, detect_artifact_kind
from .cases import ArtifactResult, CaseService
from .obfuscation import ObfuscationService
from .responses import ResponseService

NEW_CASE_TITLE = "Новый кейс"
SQL_START_RE = re.compile(
    r"(?is)^\s*(?:(?:/\*.*?\*/|--[^\r\n]*(?:\r?\n|$))\s*)*"
    r"(?:SELECT|WITH|INSERT|UPDATE|DELETE|MERGE|EXPLAIN|BEGIN|DECLARE)\b"
)


class ComposerMode(StrEnum):
    AUTO = "auto"
    MATERIAL = "material"
    QUESTION = "question"
    RESPONSE = "response"


class SubmissionKind(StrEnum):
    MATERIAL = "material"
    QUESTION = "question"
    RESPONSE = "response"


class MissingContextError(ValueError):
    pass


class MissingRunError(ValueError):
    pass


class AmbiguousInputError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class Classification:
    kind: SubmissionKind | None
    artifact_kind: ArtifactKind | None = None
    confidence: float = 1.0
    reason: str = ""


@dataclass(slots=True, frozen=True)
class ChatSubmission:
    kind: SubmissionKind
    classification: Classification
    artifact: ArtifactResult | None = None
    run: ObfuscationRun | None = None
    response: Response | None = None


def title_from_question(question: str, limit: int = 60) -> str:
    title = re.sub(r"\s+", " ", question).strip()
    if len(title) <= limit:
        return title
    return title[: limit - 1].rstrip() + "…"


class ChatService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _case(self, case_id: str) -> Case:
        return CaseService(self.session).get_case(case_id)

    def latest_run(self, case_id: str) -> ObfuscationRun | None:
        return self.session.scalar(
            select(ObfuscationRun)
            .where(ObfuscationRun.case_id == case_id)
            .order_by(ObfuscationRun.created_at.desc())
            .limit(1)
            .options(selectinload(ObfuscationRun.responses))
        )

    @staticmethod
    def _is_strong_material(text: str, detected: ArtifactKind) -> bool:
        if detected == ArtifactKind.SQL:
            return bool(SQL_START_RE.search(text))
        return detected in {
            ArtifactKind.XPLAN,
            ArtifactKind.SQL_MONITOR,
            ArtifactKind.OUTLINE,
            ArtifactKind.PREDICATES,
        }

    def classify(
        self,
        case: Case,
        text: str,
        *,
        has_file: bool = False,
    ) -> Classification:
        detected = detect_artifact_kind(text)
        if has_file or self._is_strong_material(text, detected):
            return Classification(
                kind=SubmissionKind.MATERIAL,
                artifact_kind=detected,
                reason="oracle_material",
            )

        marker_pattern = re.compile(
            rf"(?i)(?<![A-Za-z0-9_])OBF_{re.escape(case.token_prefix)}_"
            r"[A-Za-z0-9_]+(?![A-Za-z0-9_])"
        )
        if marker_pattern.search(text) and self.latest_run(case.id) is not None:
            return Classification(
                kind=SubmissionKind.RESPONSE,
                confidence=0.98,
                reason="known_case_marker",
            )

        line_count = text.count("\n") + 1
        looks_like_unknown_material = (
            len(text) >= 400
            or line_count >= 9
            or ("|" in text and "-" in text)
            or bool(re.search(r"(?i)\b(?:predicate|outline|plan hash|sql_id)\b", text))
        )
        if looks_like_unknown_material:
            return Classification(
                kind=None,
                artifact_kind=detected,
                confidence=0.35,
                reason="ambiguous_long_text",
            )
        return Classification(
            kind=SubmissionKind.QUESTION,
            confidence=0.9,
            reason="plain_text",
        )

    def submit(
        self,
        case_id: str,
        text: str,
        *,
        mode: ComposerMode | str = ComposerMode.AUTO,
        display_name: str = "",
        has_file: bool = False,
    ) -> ChatSubmission:
        source_text = text
        clean_text = source_text.strip()
        if not clean_text:
            raise ValueError("Введите текст или выберите файл.")
        resolved_mode = ComposerMode(mode)
        case = self._case(case_id)
        if resolved_mode == ComposerMode.AUTO:
            classification = self.classify(case, source_text, has_file=has_file)
            if classification.kind is None:
                raise AmbiguousInputError(
                    "Не удалось надёжно определить содержимое. "
                    "Выберите: Материал, Вопрос или Ответ ИИ."
                )
            kind = classification.kind
        else:
            kind = SubmissionKind(resolved_mode.value)
            classification = Classification(
                kind=kind,
                artifact_kind=(
                    detect_artifact_kind(source_text)
                    if kind == SubmissionKind.MATERIAL
                    else None
                ),
                confidence=1.0,
                reason="explicit_mode",
            )

        if kind == SubmissionKind.MATERIAL:
            artifact = CaseService(self.session).add_artifact(
                case.id,
                source_text,
                display_name,
            )
            return ChatSubmission(
                kind=kind,
                classification=classification,
                artifact=artifact,
            )

        if kind == SubmissionKind.QUESTION:
            if not any(artifact.is_active for artifact in case.artifacts):
                raise MissingContextError(
                    "Сначала добавьте SQL, план или другой диагностический материал."
                )
            if case.title == NEW_CASE_TITLE:
                case.title = title_from_question(clean_text)
                self.session.flush()
            run = ObfuscationService(self.session).run(case.id, clean_text)
            return ChatSubmission(kind=kind, classification=classification, run=run)

        latest_run = self.latest_run(case.id)
        if latest_run is None:
            raise MissingRunError("Сначала сформируйте prompt вопросом к модели.")
        response = ResponseService(self.session).create_response(
            latest_run.id,
            clean_text,
        )
        return ChatSubmission(
            kind=kind,
            classification=classification,
            response=response,
        )

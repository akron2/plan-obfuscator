from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from ..models import Artifact, ArtifactRevision, Case
from ..parsing import ArtifactKind, detect_artifact_kind, parse_artifact
from ..parsing.utils import sql_fingerprint, sql_is_compatible
from .tokens import generate_case_prefix


class CaseNotFoundError(LookupError):
    pass


class SqlMismatchError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class ArtifactResult:
    artifact: Artifact
    revision: ArtifactRevision


class CaseService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_case(self, title: str, description: str = "") -> Case:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("Case title is required")
        prefix = generate_case_prefix()
        while self.session.scalar(select(Case.id).where(Case.token_prefix == prefix)):
            prefix = generate_case_prefix()
        case = Case(title=clean_title, description=description.strip(), token_prefix=prefix)
        self.session.add(case)
        self.session.commit()
        self.session.refresh(case)
        return case

    def list_cases(self) -> list[Case]:
        return list(
            self.session.scalars(
                select(Case)
                .options(
                    selectinload(Case.artifacts).selectinload(Artifact.revisions),
                    selectinload(Case.runs),
                )
                .order_by(Case.updated_at.desc())
            )
        )

    def get_case(self, case_id: str) -> Case:
        case = self.session.scalar(
            select(Case)
            .where(Case.id == case_id)
            .execution_options(populate_existing=True)
            .options(
                selectinload(Case.artifacts).selectinload(Artifact.revisions),
                selectinload(Case.symbols),
                selectinload(Case.runs),
            )
        )
        if case is None:
            raise CaseNotFoundError(case_id)
        return case

    def _validate_sql(self, case: Case, text: str, kind: ArtifactKind) -> dict[str, object]:
        parsed = parse_artifact(text, kind)
        details: dict[str, object] = {
            "kind": parsed.kind.value,
            "finding_codes": [finding.code for finding in parsed.findings],
            "extracted_sql_count": len(parsed.extracted_sql),
        }
        if not parsed.extracted_sql:
            return details

        primary_sql = parsed.extracted_sql[0]
        fingerprint, normalized = sql_fingerprint(primary_sql)
        details["sql_fingerprint"] = fingerprint
        if case.normalized_sql is None:
            case.sql_fingerprint = fingerprint
            case.normalized_sql = normalized
        elif not sql_is_compatible(case.normalized_sql, normalized):
            raise SqlMismatchError(
                "Артефакт содержит другой логический SQL. Создайте для него отдельный кейс."
            )
        return details

    def add_artifact(
        self,
        case_id: str,
        text: str,
        display_name: str = "",
    ) -> ArtifactResult:
        case = self.get_case(case_id)
        if not text.strip():
            raise ValueError("Artifact text is empty")
        kind = detect_artifact_kind(text)
        details = self._validate_sql(case, text, kind)
        default_name = {
            ArtifactKind.SQL: "SQL",
            ArtifactKind.XPLAN: "DBMS_XPLAN",
            ArtifactKind.SQL_MONITOR: "SQL Monitor TEXT",
            ArtifactKind.OUTLINE: "Outline Data",
            ArtifactKind.PREDICATES: "Predicates",
            ArtifactKind.UNKNOWN: "Other material",
        }[kind]
        artifact = Artifact(
            case=case,
            detected_type=kind.value,
            display_name=display_name.strip() or default_name,
        )
        self.session.add(artifact)
        self.session.flush()
        revision = ArtifactRevision(
            artifact=artifact,
            version=1,
            source_text=text,
            detection_details=details,
        )
        self.session.add(revision)
        self.session.flush()
        artifact.current_revision_id = revision.id
        self.session.commit()
        self.session.refresh(artifact)
        self.session.refresh(revision)
        return ArtifactResult(artifact=artifact, revision=revision)

    def revise_artifact(self, artifact_id: str, text: str) -> ArtifactRevision:
        artifact = self.session.scalar(
            select(Artifact)
            .where(Artifact.id == artifact_id)
            .execution_options(populate_existing=True)
            .options(selectinload(Artifact.case), selectinload(Artifact.revisions))
        )
        if artifact is None:
            raise LookupError(artifact_id)
        if not text.strip():
            raise ValueError("Artifact text is empty")
        kind = ArtifactKind(artifact.detected_type)
        detected = detect_artifact_kind(text)
        if detected != kind and detected != ArtifactKind.UNKNOWN:
            kind = detected
            artifact.detected_type = detected.value
        details = self._validate_sql(artifact.case, text, kind)
        next_version = max((revision.version for revision in artifact.revisions), default=0) + 1
        revision = ArtifactRevision(
            artifact=artifact,
            version=next_version,
            source_text=text,
            detection_details=details,
        )
        self.session.add(revision)
        self.session.flush()
        artifact.current_revision_id = revision.id
        self.session.commit()
        self.session.refresh(revision)
        return revision

    def delete_case(self, case_id: str) -> None:
        result = self.session.execute(delete(Case).where(Case.id == case_id))
        if result.rowcount == 0:
            raise CaseNotFoundError(case_id)
        self.session.commit()

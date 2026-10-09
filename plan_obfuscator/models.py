from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def new_uuid() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    oracle_version: Mapped[str] = mapped_column(String(32), nullable=False, default="19c")
    token_prefix: Mapped[str] = mapped_column(String(12), nullable=False, unique=True)
    sql_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    normalized_sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    artifacts: Mapped[list[Artifact]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    symbols: Mapped[list[Symbol]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    runs: Mapped[list[ObfuscationRun]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    detected_type: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(240), nullable=False)
    current_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    case: Mapped[Case] = relationship(back_populates="artifacts")
    revisions: Mapped[list[ArtifactRevision]] = relationship(
        back_populates="artifact",
        cascade="all, delete-orphan",
        order_by="ArtifactRevision.version",
    )


class ArtifactRevision(Base):
    __tablename__ = "artifact_revisions"
    __table_args__ = (UniqueConstraint("artifact_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    detection_details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    artifact: Mapped[Artifact] = relationship(back_populates="revisions")


class Symbol(Base):
    __tablename__ = "symbols"
    __table_args__ = (
        UniqueConstraint("case_id", "entity_type", "canonical_value"),
        UniqueConstraint("case_id", "marker"),
        UniqueConstraint("case_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    canonical_value: Mapped[str] = mapped_column(Text, nullable=False)
    preferred_original: Mapped[str] = mapped_column(Text, nullable=False)
    marker: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    provenance: Mapped[str] = mapped_column(String(80), nullable=False, default="parser")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    case: Mapped[Case] = relationship(back_populates="symbols")


class ObfuscationRun(Base):
    __tablename__ = "obfuscation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    token_scheme_version: Mapped[str] = mapped_column(String(32), nullable=False)
    settings_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    case: Mapped[Case] = relationship(back_populates="runs")
    run_artifacts: Mapped[list[RunArtifact]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="RunArtifact.position",
    )
    responses: Mapped[list[Response]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    findings: Mapped[list[Finding]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class RunArtifact(Base):
    __tablename__ = "run_artifacts"
    __table_args__ = (UniqueConstraint("run_id", "position"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("obfuscation_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_revision_id: Mapped[str] = mapped_column(
        ForeignKey("artifact_revisions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(240), nullable=False)
    obfuscated_text: Mapped[str] = mapped_column(Text, nullable=False)

    run: Mapped[ObfuscationRun] = relationship(back_populates="run_artifacts")
    revision: Mapped[ArtifactRevision] = relationship()
    replacements: Mapped[list[ReplacementOccurrence]] = relationship(
        back_populates="run_artifact",
        cascade="all, delete-orphan",
        order_by="ReplacementOccurrence.output_start",
    )


class ReplacementOccurrence(Base):
    __tablename__ = "replacement_occurrences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    run_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("run_artifacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    symbol_id: Mapped[str] = mapped_column(
        ForeignKey("symbols.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_start: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end: Mapped[int] = mapped_column(Integer, nullable=False)
    output_start: Mapped[int] = mapped_column(Integer, nullable=False)
    output_end: Mapped[int] = mapped_column(Integer, nullable=False)
    original_lexeme: Mapped[str] = mapped_column(Text, nullable=False)
    marker: Mapped[str] = mapped_column(String(64), nullable=False)
    quoting_context: Mapped[str] = mapped_column(String(32), nullable=False, default="none")

    run_artifact: Mapped[RunArtifact] = relationship(back_populates="replacements")
    symbol: Mapped[Symbol] = relationship()


class Response(Base):
    __tablename__ = "responses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("obfuscation_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_label: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    restored_text: Mapped[str] = mapped_column(Text, nullable=False)
    unknown_markers: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[ObfuscationRun] = relationship(back_populates="responses")
    findings: Mapped[list[Finding]] = relationship(
        back_populates="response", cascade="all, delete-orphan"
    )


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("obfuscation_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifact_revisions.id", ondelete="SET NULL"), nullable=True
    )
    response_id: Mapped[str | None] = mapped_column(
        ForeignKey("responses.id", ondelete="CASCADE"), nullable=True, index=True
    )
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    start_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[ObfuscationRun] = relationship(back_populates="findings")
    revision: Mapped[ArtifactRevision | None] = relationship()
    response: Mapped[Response | None] = relationship(back_populates="findings")


Index("ix_artifacts_case_updated", Artifact.case_id, Artifact.updated_at)
Index("ix_runs_case_created", ObfuscationRun.case_id, ObfuscationRun.created_at)
Index("ix_responses_run_created", Response.run_id, Response.created_at)

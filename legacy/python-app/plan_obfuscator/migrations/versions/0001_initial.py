"""Initial persistent case history.

Revision ID: 0001_initial
Revises: None
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("oracle_version", sa.String(length=32), nullable=False),
        sa.Column("token_prefix", sa.String(length=12), nullable=False),
        sa.Column("sql_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("normalized_sql", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_prefix"),
    )
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("detected_type", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=240), nullable=False),
        sa.Column("current_revision_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_artifacts_case_id", "artifacts", ["case_id"])
    op.create_index("ix_artifacts_case_updated", "artifacts", ["case_id", "updated_at"])
    op.create_table(
        "artifact_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("detection_details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_id", "version"),
    )
    op.create_index("ix_artifact_revisions_artifact_id", "artifact_revisions", ["artifact_id"])
    op.create_table(
        "symbols",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("canonical_value", sa.Text(), nullable=False),
        sa.Column("preferred_original", sa.Text(), nullable=False),
        sa.Column("marker", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("provenance", sa.String(length=80), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "entity_type", "canonical_value"),
        sa.UniqueConstraint("case_id", "marker"),
        sa.UniqueConstraint("case_id", "sequence"),
    )
    op.create_index("ix_symbols_case_id", "symbols", ["case_id"])
    op.create_table(
        "obfuscation_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("parser_version", sa.String(length=32), nullable=False),
        sa.Column("token_scheme_version", sa.String(length=32), nullable=False),
        sa.Column("settings_snapshot", sa.JSON(), nullable=False),
        sa.Column("validation_status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_obfuscation_runs_case_id", "obfuscation_runs", ["case_id"])
    op.create_index("ix_runs_case_created", "obfuscation_runs", ["case_id", "created_at"])
    op.create_table(
        "run_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_revision_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("artifact_type", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=240), nullable=False),
        sa.Column("obfuscated_text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["artifact_revision_id"], ["artifact_revisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["obfuscation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "position"),
    )
    op.create_index(
        "ix_run_artifacts_artifact_revision_id",
        "run_artifacts",
        ["artifact_revision_id"],
    )
    op.create_index("ix_run_artifacts_run_id", "run_artifacts", ["run_id"])
    op.create_table(
        "replacement_occurrences",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_artifact_id", sa.String(length=36), nullable=False),
        sa.Column("symbol_id", sa.String(length=36), nullable=False),
        sa.Column("source_start", sa.Integer(), nullable=False),
        sa.Column("source_end", sa.Integer(), nullable=False),
        sa.Column("output_start", sa.Integer(), nullable=False),
        sa.Column("output_end", sa.Integer(), nullable=False),
        sa.Column("original_lexeme", sa.Text(), nullable=False),
        sa.Column("marker", sa.String(length=64), nullable=False),
        sa.Column("quoting_context", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["run_artifact_id"], ["run_artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_replacement_occurrences_run_artifact_id",
        "replacement_occurrences",
        ["run_artifact_id"],
    )
    op.create_index(
        "ix_replacement_occurrences_symbol_id",
        "replacement_occurrences",
        ["symbol_id"],
    )
    op.create_table(
        "responses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("model_label", sa.String(length=120), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("restored_text", sa.Text(), nullable=False),
        sa.Column("unknown_markers", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["obfuscation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_responses_run_id", "responses", ["run_id"])
    op.create_index("ix_responses_run_created", "responses", ["run_id", "created_at"])
    op.create_table(
        "findings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_revision_id", sa.String(length=36), nullable=True),
        sa.Column("response_id", sa.String(length=36), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=True),
        sa.Column("end_offset", sa.Integer(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["artifact_revision_id"], ["artifact_revisions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["response_id"], ["responses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["obfuscation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_findings_run_id", "findings", ["run_id"])
    op.create_index("ix_findings_response_id", "findings", ["response_id"])


def downgrade() -> None:
    op.drop_index("ix_findings_response_id", table_name="findings")
    op.drop_index("ix_findings_run_id", table_name="findings")
    op.drop_table("findings")
    op.drop_index("ix_responses_run_created", table_name="responses")
    op.drop_index("ix_responses_run_id", table_name="responses")
    op.drop_table("responses")
    op.drop_index("ix_replacement_occurrences_symbol_id", table_name="replacement_occurrences")
    op.drop_index(
        "ix_replacement_occurrences_run_artifact_id",
        table_name="replacement_occurrences",
    )
    op.drop_table("replacement_occurrences")
    op.drop_index("ix_run_artifacts_run_id", table_name="run_artifacts")
    op.drop_index("ix_run_artifacts_artifact_revision_id", table_name="run_artifacts")
    op.drop_table("run_artifacts")
    op.drop_index("ix_runs_case_created", table_name="obfuscation_runs")
    op.drop_index("ix_obfuscation_runs_case_id", table_name="obfuscation_runs")
    op.drop_table("obfuscation_runs")
    op.drop_index("ix_symbols_case_id", table_name="symbols")
    op.drop_table("symbols")
    op.drop_index("ix_artifact_revisions_artifact_id", table_name="artifact_revisions")
    op.drop_table("artifact_revisions")
    op.drop_index("ix_artifacts_case_updated", table_name="artifacts")
    op.drop_index("ix_artifacts_case_id", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_table("cases")

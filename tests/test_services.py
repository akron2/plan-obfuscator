from __future__ import annotations

import re

import pytest
from sqlalchemy import func, select

from plan_obfuscator.models import ArtifactRevision, Case, ObfuscationRun, Response, Symbol
from plan_obfuscator.services import CaseService, ObfuscationService, ResponseService
from plan_obfuscator.services.cases import SqlMismatchError
from plan_obfuscator.services.tokens import build_marker, token_checksum


def build_case(db_session, fixture_text):
    cases = CaseService(db_session)
    case = cases.create_case("Monthly report")
    cases.add_artifact(case.id, fixture_text("query.sql"), "query.sql")
    cases.add_artifact(case.id, fixture_text("xplan_19c.txt"), "xplan.txt")
    cases.add_artifact(case.id, fixture_text("sql_monitor_19c.txt"), "monitor.txt")
    return case


def test_end_to_end_mapping_roundtrip_and_metrics(db_session, fixture_text) -> None:
    case = build_case(db_session, fixture_text)
    service = ObfuscationService(db_session)
    run = service.run(case.id, "Find the bottleneck")

    assert run.validation_status == "ok"
    assert "Find the bottleneck" in run.prompt_text
    assert "98765" in run.prompt_text
    assert "10GB" in run.prompt_text
    assert "3829104756" not in run.prompt_text
    assert "sales_prod" not in run.prompt_text

    table_symbol = db_session.scalar(
        select(Symbol).where(
            Symbol.case_id == case.id,
            Symbol.entity_type == "table",
            Symbol.canonical_value == "U:ORDERS",
        )
    )
    assert table_symbol is not None
    assert run.prompt_text.count(table_symbol.marker) >= 3

    for run_artifact in run.run_artifacts:
        restored = service.restore_run_artifact(run_artifact.id)
        assert restored == run_artifact.revision.source_text


def test_mapping_is_stable_across_runs(db_session, fixture_text) -> None:
    case = build_case(db_session, fixture_text)
    service = ObfuscationService(db_session)
    first = service.run(case.id, "First")
    symbols_before = {
        (item.entity_type, item.canonical_value): item.marker for item in first.case.symbols
    }
    second = service.run(case.id, "Second")
    symbols_after = {
        (item.entity_type, item.canonical_value): item.marker for item in second.case.symbols
    }
    assert symbols_before == symbols_after


def test_multiple_executions_of_same_sql_share_one_case(db_session, fixture_text) -> None:
    cases = CaseService(db_session)
    case = cases.create_case("Repeated execution")
    cases.add_artifact(case.id, fixture_text("query.sql"), "query")
    first_monitor = fixture_text("sql_monitor_19c.txt")
    second_monitor = first_monitor.replace("12.01", "24.02").replace("98765", "120000")
    cases.add_artifact(case.id, first_monitor, "first execution")
    cases.add_artifact(case.id, second_monitor, "second execution")
    run = ObfuscationService(db_session).run(case.id)
    assert len(run.run_artifacts) == 3
    assert "12.01" in run.prompt_text
    assert "24.02" in run.prompt_text
    assert "98765" in run.prompt_text
    assert "120000" in run.prompt_text


def test_standalone_outline_and_predicates_join_case_mapping(db_session, fixture_text) -> None:
    cases = CaseService(db_session)
    case = cases.create_case("Separate diagnostics")
    cases.add_artifact(case.id, fixture_text("query.sql"), "query")
    outline = """/*+
BEGIN_OUTLINE_DATA
FULL(@"SEL$1" "C"@"SEL$1")
INDEX_RS_ASC(@"SEL$1" "O"@"SEL$1" ("SALES"."IX_ORDERS_STATUS"))
END_OUTLINE_DATA
*/"""
    predicates = """1 - access("C"."CUSTOMER_ID"="O"."CUSTOMER_ID")
2 - filter("O"."STATUS"='OPEN' AND "O"."AMOUNT">1000)"""
    outline_result = cases.add_artifact(case.id, outline, "outline")
    predicate_result = cases.add_artifact(case.id, predicates, "predicates")
    assert outline_result.artifact.detected_type == "outline"
    assert predicate_result.artifact.detected_type == "predicates"

    run = ObfuscationService(db_session).run(case.id)
    types = {item.artifact_type for item in run.run_artifacts}
    assert {"sql", "outline", "predicates"} <= types
    open_symbol = next(
        symbol
        for symbol in run.case.symbols
        if symbol.entity_type == "string_literal" and symbol.preferred_original == "OPEN"
    )
    amount_symbol = next(
        symbol
        for symbol in run.case.symbols
        if symbol.entity_type == "number_literal" and symbol.preferred_original == "1000"
    )
    assert run.prompt_text.count(open_symbol.marker) == 2
    assert run.prompt_text.count(amount_symbol.marker) == 2


def test_mapping_is_independent_between_cases(db_session, fixture_text) -> None:
    service = CaseService(db_session)
    first = service.create_case("First")
    second = service.create_case("Second")
    for case in (first, second):
        service.add_artifact(case.id, fixture_text("query.sql"), "query")
        ObfuscationService(db_session).run(case.id)
    markers = []
    for case in (first, second):
        symbol = db_session.scalar(
            select(Symbol).where(
                Symbol.case_id == case.id,
                Symbol.entity_type == "table",
                Symbol.canonical_value == "U:ORDERS",
            )
        )
        markers.append(symbol.marker)
    assert markers[0] != markers[1]


def test_response_restoration_and_unknown_marker_finding(db_session, fixture_text) -> None:
    case = build_case(db_session, fixture_text)
    run = ObfuscationService(db_session).run(case.id)
    symbol = next(item for item in run.case.symbols if item.canonical_value == "U:ORDERS")
    corrupted = f"OBF_{case.token_prefix}_TBL_9999_ZZ"
    response = ResponseService(db_session).create_response(
        run.id,
        f"Full scan of {symbol.marker.lower()}; unknown {corrupted}.",
        "ChatGPT",
    )
    assert "orders" in response.restored_text.lower()
    assert corrupted in response.unknown_markers
    assert corrupted in response.restored_text
    assert response.findings[0].code == "unknown_response_markers"


def test_history_survives_new_database_session(db_factory, fixture_text) -> None:
    first_session = db_factory()
    case = build_case(first_session, fixture_text)
    run = ObfuscationService(first_session).run(case.id)
    symbol = next(item for item in run.case.symbols if item.canonical_value == "U:ORDERS")
    ResponseService(first_session).create_response(run.id, f"Inspect {symbol.marker}")
    case_id = case.id
    first_session.close()

    second_session = db_factory()
    loaded = CaseService(second_session).get_case(case_id)
    loaded_run = ObfuscationService(second_session).get_run(loaded.runs[0].id)
    assert loaded_run.responses[0].restored_text == "Inspect orders"
    assert len(loaded.artifacts) == 3
    second_session.close()


def test_different_sql_is_rejected(db_session, fixture_text) -> None:
    service = CaseService(db_session)
    case = service.create_case("One SQL")
    service.add_artifact(case.id, fixture_text("query.sql"), "main")
    with pytest.raises(SqlMismatchError):
        service.add_artifact(case.id, "select * from hr.employees", "different")


def test_revision_history_is_immutable(db_session, fixture_text) -> None:
    service = CaseService(db_session)
    case = service.create_case("Revisions")
    result = service.add_artifact(case.id, fixture_text("query.sql"), "query")
    revised_text = fixture_text("query.sql").replace("  AND c.region_code = :region\n", "")
    revision = service.revise_artifact(result.artifact.id, revised_text)
    assert revision.version == 2
    rows = list(
        db_session.scalars(
            select(ArtifactRevision)
            .where(ArtifactRevision.artifact_id == result.artifact.id)
            .order_by(ArtifactRevision.version)
        )
    )
    assert len(rows) == 2
    assert rows[0].source_text == fixture_text("query.sql")
    assert rows[1].source_text == revised_text


def test_case_delete_cascades_history(db_session, fixture_text) -> None:
    case = build_case(db_session, fixture_text)
    run = ObfuscationService(db_session).run(case.id)
    ResponseService(db_session).create_response(run.id, "No markers")
    CaseService(db_session).delete_case(case.id)
    assert db_session.scalar(select(func.count()).select_from(Case)) == 0
    assert db_session.scalar(select(func.count()).select_from(ObfuscationRun)) == 0
    assert db_session.scalar(select(func.count()).select_from(Response)) == 0


def test_token_checksum_is_deterministic() -> None:
    checksum = token_checksum("ABCD", "TBL", 1)
    assert build_marker("ABCD", "TBL", 1) == f"OBF_ABCD_TBL_0001_{checksum}"
    assert re.fullmatch(r"OBF_ABCD_TBL_0001_[0-9A-F]{2}", build_marker("ABCD", "TBL", 1))


def test_existing_case_marker_is_reported(db_session) -> None:
    cases = CaseService(db_session)
    case = cases.create_case("Collision")
    cases.add_artifact(
        case.id,
        f"select secret_column from secret_table -- OBF_{case.token_prefix}_OLD_0001_AA",
        "query",
    )
    run = ObfuscationService(db_session).run(case.id)
    assert any(item.code == "input_marker_collision" for item in run.findings)
    assert run.validation_status == "warning"

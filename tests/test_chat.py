from __future__ import annotations

import time

import pytest

from plan_obfuscator.services import CaseService, ChatService, ObfuscationService
from plan_obfuscator.services.chat import (
    NEW_CASE_TITLE,
    AmbiguousInputError,
    ComposerMode,
    MissingRunError,
    SubmissionKind,
    title_from_question,
)

from .corpus_loader import corpus_case


def test_universal_composer_material_question_response_flow(db_session, fixture_text) -> None:
    cases = CaseService(db_session)
    case = cases.create_case(NEW_CASE_TITLE)
    chat = ChatService(db_session)

    material = chat.submit(case.id, fixture_text("query.sql"))
    assert material.kind == SubmissionKind.MATERIAL
    assert material.artifact is not None
    assert material.artifact.artifact.detected_type == "sql"

    question_text = "Почему этот запрос выполняется медленно?"
    question = chat.submit(case.id, question_text)
    assert question.kind == SubmissionKind.QUESTION
    assert question.run is not None
    assert question.run.question == question_text
    assert cases.get_case(case.id).title == question_text

    table_symbol = next(
        symbol
        for symbol in question.run.case.symbols
        if symbol.entity_type == "table" and symbol.canonical_value == "U:ORDERS"
    )
    raw_answer = f"Проверьте full scan таблицы {table_symbol.marker}."
    answer = chat.submit(case.id, raw_answer)
    assert answer.kind == SubmissionKind.RESPONSE
    assert answer.response is not None
    assert answer.response.restored_text == "Проверьте full scan таблицы orders."


def test_questions_before_materials_auto_build_prompt_when_material_added(
    db_session, fixture_text
) -> None:
    case = CaseService(db_session).create_case(NEW_CASE_TITLE)
    chat = ChatService(db_session)

    q1 = chat.submit(case.id, "Почему запрос медленный?")
    assert q1.kind == SubmissionKind.QUESTION
    assert q1.run is not None
    assert q1.run.prompt_text == ""
    assert q1.run.questions == ["Почему запрос медленный?"]

    q2 = chat.submit(case.id, "Нужен ли составной индекс?")
    assert q2.kind == SubmissionKind.QUESTION
    assert q2.run is not None
    assert q2.run.id == q1.run.id
    assert q2.run.prompt_text == ""
    assert q2.run.questions == ["Почему запрос медленный?", "Нужен ли составной индекс?"]

    mat = chat.submit(case.id, fixture_text("query.sql"))
    assert mat.kind == SubmissionKind.MATERIAL
    assert mat.run is not None
    assert mat.run.id == q1.run.id
    assert mat.run.prompt_text != ""
    assert (
        mat.run.prompt_text.index("=== ВОПРОС #1 ===")
        < mat.run.prompt_text.index("=== ВОПРОС #2 ===")
        < mat.run.prompt_text.index("=== SQL #1:")
    )


def test_response_requires_run(db_session, fixture_text) -> None:
    case = CaseService(db_session).create_case(NEW_CASE_TITLE)
    chat = ChatService(db_session)
    chat.submit(case.id, fixture_text("query.sql"))
    with pytest.raises(MissingRunError, match="Сначала сформируйте prompt"):
        chat.submit(case.id, "обычный ответ", mode=ComposerMode.RESPONSE)


def test_ambiguous_long_text_requires_inline_override(db_session) -> None:
    case = CaseService(db_session).create_case(NEW_CASE_TITLE)
    text = "\n".join(f"proprietary diagnostic line {index}" for index in range(12))
    chat = ChatService(db_session)
    with pytest.raises(AmbiguousInputError, match="Выберите"):
        chat.submit(case.id, text)

    forced = chat.submit(case.id, text, mode=ComposerMode.MATERIAL)
    assert forced.kind == SubmissionKind.MATERIAL
    assert forced.artifact is not None
    assert forced.artifact.artifact.detected_type == "unknown"


def test_file_is_material_even_when_content_is_unknown(db_session) -> None:
    case = CaseService(db_session).create_case(NEW_CASE_TITLE)
    result = ChatService(db_session).submit(
        case.id,
        "vendor-specific diagnostic payload",
        has_file=True,
        display_name="diagnostic.log",
    )
    assert result.kind == SubmissionKind.MATERIAL
    assert result.artifact.artifact.display_name == "diagnostic.log"


def test_clipboard_html_is_ignored_for_oracle_material(db_session) -> None:
    case = CaseService(db_session).create_case(NEW_CASE_TITLE)
    source = "select secret_column from secret_table"

    result = ChatService(db_session).submit(
        case.id,
        source,
        mode=ComposerMode.MATERIAL,
        pasted_plain=source,
        pasted_html="<h1>HTML from SQL Developer must not replace SQL</h1>",
    )

    assert result.artifact is not None
    assert result.artifact.revision.source_text == source


def test_title_generation_is_compact() -> None:
    question = "  Почему   очень длинный запрос " + ("работает медленно " * 8)
    title = title_from_question(question)
    assert len(title) == 60
    assert title.endswith("…")
    assert "  " not in title


def test_one_paste_adds_matching_monitor_and_xplan_as_separate_materials(db_session) -> None:
    monitor = corpus_case("generated/oracle-21c/monitor-q8-bind-all.txt")
    xplan = corpus_case("generated/oracle-21c/xplan-q8-bind.txt")
    source = f"{monitor.source}\nДополнительный execution plan:\n{xplan.source}"
    case = CaseService(db_session).create_case("Combined input")

    result = ChatService(db_session).submit(case.id, source)

    assert result.kind == SubmissionKind.MATERIAL
    assert len(result.artifacts) == 2
    assert [item.artifact.detected_type for item in result.artifacts] == [
        "sql_monitor",
        "xplan",
    ]


def test_large_500k_xplan_upload_obfuscate_and_restore_performance(db_session) -> None:
    header = (
        "SQL_ID  9babjv8yq8ru3, child number 0\n"
        "-------------------------------------\n"
        "SELECT /*+ GATHER_PLAN_STATISTICS */ e.employee_id, e.first_name, d.department_name\n"
        "FROM hr.employees e JOIN hr.departments d ON e.department_id = d.department_id\n"
        "WHERE e.salary > 5000 AND d.location_id = 1700\n\n"
        "Plan hash value: 1234567890\n\n"
        "---------------------------------------------------------------------------------\n"
        "| Id  | Operation             | Name          | Starts | E-Rows | A-Rows |Cost |\n"
        "---------------------------------------------------------------------------------\n"
    )
    row_tpl = (
        "| {:3d} |  TABLE ACCESS BY INDEX| TBL_EMP_{:04d}  |      1 |     10 |     10 |   5 |\n"
    )
    pred_header = (
        "---------------------------------------------------------------------------------\n\n"
        "Predicate Information (identified by operation id):\n"
        "---------------------------------------------------\n"
    )
    pred_tpl = (
        '   {:d} - access("E_{:04d}"."DEPARTMENT_ID"="D_{:04d}"."DEPARTMENT_ID")\n'
        '       filter(("E_{:04d}"."SALARY">5000 AND "D_{:04d}"."LOCATION_ID"=1700))\n'
    )
    rows = "".join(row_tpl.format(i % 999, i % 500) for i in range(1, 2400))
    preds = "".join(
        pred_tpl.format(i, i % 500, i % 500, i % 500, i % 500) for i in range(1, 2400)
    )
    plan_text = (header + rows + pred_header + preds).strip()
    assert len(plan_text) >= 500_000

    case = CaseService(db_session).create_case("Large 500k XPLAN")
    chat = ChatService(db_session)

    t0 = time.perf_counter()
    upload = chat.submit(case.id, plan_text)
    upload_seconds = time.perf_counter() - t0
    assert upload.kind == SubmissionKind.MATERIAL
    assert upload_seconds < 1.0

    t1 = time.perf_counter()
    question = chat.submit(case.id, "Что оптимизировать в этом плане?")
    obfuscate_seconds = time.perf_counter() - t1
    assert question.kind == SubmissionKind.QUESTION
    assert question.run is not None
    assert obfuscate_seconds < 5.0

    run_artifact = question.run.run_artifacts[0]
    assert "TBL_EMP_0001" not in run_artifact.obfuscated_text
    restored = ObfuscationService(db_session).restore_run_artifact(run_artifact.id)
    assert restored == plan_text

    # Add a second 500 KB plan with unique predicate expressions and ask a 1-sentence question.
    preds_2 = "".join(
        pred_tpl.format(i, i, (i + 7) % 1900, i, (i + 13) % 1900) for i in range(1, 2400)
    )
    plan_text_2 = (header + rows + pred_header + preds_2).strip()
    assert len(plan_text_2) >= 500_000

    upload_2 = chat.submit(case.id, plan_text_2)
    assert upload_2.kind == SubmissionKind.MATERIAL

    t2 = time.perf_counter()
    question_2 = chat.submit(case.id, "Сравни оба плана в одном предложении.")
    question_2_seconds = time.perf_counter() - t2
    assert question_2.kind == SubmissionKind.QUESTION
    assert question_2.run is not None
    assert len(question_2.run.run_artifacts) == 2
    assert question_2_seconds < 4.0

    t3 = time.perf_counter()
    question_3 = chat.submit(case.id, "А что по предикатам во втором плане?")
    question_3_seconds = time.perf_counter() - t3
    assert question_3.kind == SubmissionKind.QUESTION
    assert question_3.run is not None
    assert question_3_seconds < 4.0



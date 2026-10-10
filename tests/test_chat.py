from __future__ import annotations

import pytest

from plan_obfuscator.services import CaseService, ChatService
from plan_obfuscator.services.chat import (
    NEW_CASE_TITLE,
    AmbiguousInputError,
    ComposerMode,
    MissingContextError,
    MissingRunError,
    SubmissionKind,
    title_from_question,
)


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


def test_question_requires_context(db_session) -> None:
    case = CaseService(db_session).create_case(NEW_CASE_TITLE)
    with pytest.raises(MissingContextError, match="Сначала добавьте"):
        ChatService(db_session).submit(case.id, "Почему запрос медленный?")


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


def test_title_generation_is_compact() -> None:
    question = "  Почему   очень длинный запрос " + ("работает медленно " * 8)
    title = title_from_question(question)
    assert len(title) == 60
    assert title.endswith("…")
    assert "  " not in title


from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class PromptArtifact:
    artifact_type: str
    display_name: str
    text: str


TYPE_LABELS = {
    "sql": "SQL",
    "xplan": "DBMS_XPLAN",
    "sql_monitor": "SQL MONITOR TEXT",
    "outline": "OUTLINE DATA",
    "predicates": "PREDICATES",
    "unknown": "OTHER MATERIAL",
}


def build_prompt(
    artifacts: list[PromptArtifact],
    question: str = "",
    *,
    questions: list[str] | None = None,
) -> str:
    if questions is not None:
        clean_questions = [item.strip() for item in questions if item.strip()]
    elif question.strip():
        clean_questions = [question.strip()]
    else:
        clean_questions = []

    if not artifacts and not clean_questions:
        return ""

    if artifacts:
        preamble = (
            "Материалы ниже псевдонимизированы. Маркеры вида OBF_* обозначают "
            "согласованные сущности. Не изменяйте, не сокращайте и не склоняйте эти "
            "маркеры в ответе — они будут восстановлены локально."
        )
    else:
        preamble = (
            "В вопросах ниже используются псевдонимизированные маркеры вида OBF_* "
            "из предыдущего контекста диалога. Не изменяйте, не сокращайте и не "
            "склоняйте эти маркеры в ответе — они будут восстановлены локально."
        )

    parts = [preamble]
    if len(clean_questions) == 1:
        parts.extend(["=== ВОПРОС ===", clean_questions[0]])
    elif len(clean_questions) > 1:
        for index, item in enumerate(clean_questions, start=1):
            parts.extend([f"=== ВОПРОС #{index} ===", item])

    counters: dict[str, int] = {}
    for artifact in artifacts:
        counters[artifact.artifact_type] = counters.get(artifact.artifact_type, 0) + 1
        number = counters[artifact.artifact_type]
        label = TYPE_LABELS.get(artifact.artifact_type, artifact.artifact_type.upper())
        title = f"=== {label} #{number}: {artifact.display_name} ==="
        parts.extend([title, artifact.text.rstrip()])
    return "\n\n".join(parts).rstrip() + "\n"


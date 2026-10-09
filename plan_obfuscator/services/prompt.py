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


def build_prompt(artifacts: list[PromptArtifact], question: str = "") -> str:
    parts = [
        "Материалы ниже псевдонимизированы. Маркеры вида OBF_* обозначают "
        "согласованные сущности. Не изменяйте, не сокращайте и не склоняйте эти "
        "маркеры в ответе — они будут восстановлены локально.",
    ]
    counters: dict[str, int] = {}
    for artifact in artifacts:
        counters[artifact.artifact_type] = counters.get(artifact.artifact_type, 0) + 1
        number = counters[artifact.artifact_type]
        label = TYPE_LABELS.get(artifact.artifact_type, artifact.artifact_type.upper())
        title = f"=== {label} #{number}: {artifact.display_name} ==="
        parts.extend([title, artifact.text.rstrip()])
    if question.strip():
        parts.extend(["=== ВОПРОС ===", question.strip()])
    return "\n\n".join(parts).rstrip() + "\n"

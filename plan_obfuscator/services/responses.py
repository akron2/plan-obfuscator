from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Case, Finding, ObfuscationRun, Response


class ResponseService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _load_run(self, run_id: str) -> ObfuscationRun:
        run = self.session.scalar(
            select(ObfuscationRun)
            .where(ObfuscationRun.id == run_id)
            .options(selectinload(ObfuscationRun.case).selectinload(Case.symbols))
        )
        if run is None:
            raise LookupError(run_id)
        return run

    def restore(self, run_id: str, raw_text: str) -> tuple[str, list[str]]:
        run = self._load_run(run_id)
        symbols = run.case.symbols
        by_marker = {symbol.marker.upper(): symbol.preferred_original for symbol in symbols}
        if not by_marker:
            return raw_text, []

        exact_pattern = re.compile(
            r"(?<![A-Za-z0-9_])("
            + "|".join(re.escape(marker) for marker in sorted(by_marker, key=len, reverse=True))
            + r")(?![A-Za-z0-9_])",
            re.IGNORECASE,
        )
        restored = exact_pattern.sub(lambda match: by_marker[match.group(0).upper()], raw_text)

        candidate_pattern = re.compile(
            rf"(?<![A-Za-z0-9_])OBF_{re.escape(run.case.token_prefix)}_"
            r"[A-Za-z0-9_]+(?![A-Za-z0-9_])",
            re.IGNORECASE,
        )
        unknown = sorted(
            {
                match.group(0)
                for match in candidate_pattern.finditer(raw_text)
                if match.group(0).upper() not in by_marker
            }
        )
        return restored, unknown

    def create_response(
        self,
        run_id: str,
        raw_text: str,
        model_label: str = "",
    ) -> Response:
        if not raw_text.strip():
            raise ValueError("Response text is empty")
        restored, unknown = self.restore(run_id, raw_text)
        response = Response(
            run_id=run_id,
            model_label=model_label.strip(),
            raw_text=raw_text,
            restored_text=restored,
            unknown_markers=unknown,
        )
        self.session.add(response)
        self.session.flush()
        if unknown:
            self.session.add(
                Finding(
                    run_id=run_id,
                    response_id=response.id,
                    severity="warning",
                    code="unknown_response_markers",
                    message=(
                        "Ответ содержит неизвестные или повреждённые маркеры: " + ", ".join(unknown)
                    ),
                    details={"markers": unknown},
                )
            )
        self.session.commit()
        self.session.refresh(response)
        return response

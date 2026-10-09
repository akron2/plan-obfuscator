from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, selectinload, sessionmaker
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Settings
from .database import create_database_engine, create_session_factory, initialize_schema
from .migration_runner import upgrade_database
from .models import Artifact, ArtifactRevision, Case, ObfuscationRun, RunArtifact
from .services import CaseService, ObfuscationService, ResponseService
from .services.cases import CaseNotFoundError, SqlMismatchError

PACKAGE_DIR = Path(__file__).resolve().parent
MAX_ARTIFACT_BYTES = 20 * 1024 * 1024


class CaseCreatePayload(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""


class ArtifactCreatePayload(BaseModel):
    text: str = Field(min_length=1)
    display_name: str = ""


class ArtifactRevisionPayload(BaseModel):
    text: str = Field(min_length=1)


class RunCreatePayload(BaseModel):
    question: str = ""


class ResponseCreatePayload(BaseModel):
    text: str = Field(min_length=1)
    model_label: str = ""


def decode_uploaded_content(content: bytes) -> str:
    if len(content) > MAX_ARTIFACT_BYTES:
        raise ValueError("Файл превышает лимит 20 МБ")
    for encoding in ("utf-8-sig", "utf-16", "cp1251", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Unsupported text encoding")


def redirect_to_case(case_id: str, **query: str) -> RedirectResponse:
    suffix = f"?{urlencode(query)}" if query else ""
    return RedirectResponse(f"/cases/{case_id}{suffix}", status_code=303)


def get_session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    session = factory()
    try:
        yield session
    finally:
        session.close()


SessionDependency = Annotated[Session, Depends(get_session)]


def create_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
) -> FastAPI:
    settings = settings or Settings()
    if engine is None:
        upgrade_database(settings)
        database_engine = create_database_engine(settings)
    else:
        database_engine = engine
        initialize_schema(database_engine)
    session_factory = create_session_factory(database_engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        database_engine.dispose()

    app = FastAPI(
        title="Plan Obfuscator",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )
    app.state.settings = settings
    app.state.engine = database_engine
    app.state.session_factory = session_factory

    templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
    app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")

    def load_case_view(session: Session, case_id: str) -> Case:
        case = session.scalar(
            select(Case)
            .where(Case.id == case_id)
            .execution_options(populate_existing=True)
            .options(
                selectinload(Case.artifacts).selectinload(Artifact.revisions),
                selectinload(Case.symbols),
                selectinload(Case.runs)
                .selectinload(ObfuscationRun.run_artifacts)
                .selectinload(RunArtifact.replacements),
                selectinload(Case.runs).selectinload(ObfuscationRun.responses),
                selectinload(Case.runs).selectinload(ObfuscationRun.findings),
            )
        )
        if case is None:
            raise HTTPException(status_code=404, detail="Case not found")
        case.artifacts.sort(key=lambda item: item.created_at)
        case.runs.sort(key=lambda item: item.created_at, reverse=True)
        for artifact in case.artifacts:
            artifact.revisions.sort(key=lambda item: item.version)
        for run in case.runs:
            run.responses.sort(key=lambda item: item.created_at, reverse=True)
        return case

    @app.exception_handler(SqlMismatchError)
    async def sql_mismatch_handler(_request: Request, error: SqlMismatchError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.get("/health", name="health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse, name="home")
    def home(request: Request, session: SessionDependency) -> HTMLResponse:
        cases = CaseService(session).list_cases()
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"cases": cases},
        )

    @app.post("/cases", name="create_case_form")
    def create_case_form(
        session: SessionDependency,
        title: Annotated[str, Form()],
        description: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        try:
            case = CaseService(session).create_case(title, description)
        except ValueError as error:
            return RedirectResponse(f"/?{urlencode({'error': str(error)})}", status_code=303)
        return redirect_to_case(case.id, notice="Кейс создан")

    @app.get("/cases/{case_id}", response_class=HTMLResponse, name="case_detail")
    def case_detail(
        request: Request,
        case_id: str,
        session: SessionDependency,
    ) -> HTMLResponse:
        case = load_case_view(session, case_id)
        return templates.TemplateResponse(
            request=request,
            name="case.html",
            context={
                "case": case,
                "notice": request.query_params.get("notice", ""),
                "error": request.query_params.get("error", ""),
            },
        )

    @app.post("/cases/{case_id}/artifacts", name="add_artifact_form")
    async def add_artifact_form(
        case_id: str,
        session: SessionDependency,
        text: Annotated[str, Form()] = "",
        display_name: Annotated[str, Form()] = "",
        upload: Annotated[UploadFile | None, File()] = None,
    ) -> RedirectResponse:
        source = text
        if upload is not None and upload.filename:
            try:
                source = decode_uploaded_content(await upload.read())
            except ValueError as error:
                return redirect_to_case(case_id, error=str(error))
            if not display_name:
                display_name = upload.filename
        try:
            result = CaseService(session).add_artifact(case_id, source, display_name)
        except (ValueError, SqlMismatchError) as error:
            return redirect_to_case(case_id, error=str(error))
        return redirect_to_case(
            case_id,
            notice=f"Добавлен артефакт: {result.artifact.display_name}",
        )

    @app.post("/artifacts/{artifact_id}/revisions", name="revise_artifact_form")
    def revise_artifact_form(
        artifact_id: str,
        session: SessionDependency,
        text: Annotated[str, Form()],
    ) -> RedirectResponse:
        artifact = session.get(Artifact, artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Artifact not found")
        try:
            revision = CaseService(session).revise_artifact(artifact_id, text)
        except (ValueError, SqlMismatchError) as error:
            return redirect_to_case(artifact.case_id, error=str(error))
        return redirect_to_case(
            artifact.case_id,
            notice=f"Создана ревизия {revision.version}",
        )

    @app.post("/artifacts/{artifact_id}/delete", name="delete_artifact_form")
    def delete_artifact_form(
        artifact_id: str,
        session: SessionDependency,
    ) -> RedirectResponse:
        artifact = session.get(Artifact, artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Artifact not found")
        case_id = artifact.case_id
        used_count = session.scalar(
            select(RunArtifact.id)
            .join(ArtifactRevision, RunArtifact.artifact_revision_id == ArtifactRevision.id)
            .where(ArtifactRevision.artifact_id == artifact_id)
            .limit(1)
        )
        if used_count is not None:
            return redirect_to_case(
                case_id,
                error="Источник уже входит в историю запусков и не может быть удалён.",
            )
        session.delete(artifact)
        session.commit()
        return redirect_to_case(case_id, notice="Артефакт удалён")

    @app.post("/cases/{case_id}/runs", name="create_run_form")
    def create_run_form(
        case_id: str,
        session: SessionDependency,
        question: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        try:
            run = ObfuscationService(session).run(case_id, question)
        except ValueError as error:
            return redirect_to_case(case_id, error=str(error))
        return redirect_to_case(case_id, notice=f"Prompt создан: {run.id[:8]}")

    @app.post("/runs/{run_id}/responses", name="create_response_form")
    def create_response_form(
        run_id: str,
        session: SessionDependency,
        text: Annotated[str, Form()],
        model_label: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        run = session.get(ObfuscationRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        try:
            response = ResponseService(session).create_response(run_id, text, model_label)
        except ValueError as error:
            return redirect_to_case(run.case_id, error=str(error))
        notice = "Ответ восстановлен"
        if response.unknown_markers:
            notice += f"; неизвестных маркеров: {len(response.unknown_markers)}"
        return redirect_to_case(run.case_id, notice=notice)

    @app.post("/cases/{case_id}/delete", name="delete_case_form")
    def delete_case_form(case_id: str, session: SessionDependency) -> RedirectResponse:
        try:
            CaseService(session).delete_case(case_id)
        except CaseNotFoundError:
            raise HTTPException(status_code=404, detail="Case not found") from None
        return RedirectResponse("/?notice=Кейс+удалён", status_code=303)

    # JSON API. It mirrors the UI workflow and is intentionally local-only.
    @app.get("/api/cases", name="api_list_cases")
    def api_list_cases(session: SessionDependency) -> list[dict[str, object]]:
        return [
            {
                "id": case.id,
                "title": case.title,
                "description": case.description,
                "oracle_version": case.oracle_version,
                "artifact_count": len(case.artifacts),
                "run_count": len(case.runs),
                "created_at": case.created_at.isoformat(),
                "updated_at": case.updated_at.isoformat(),
            }
            for case in CaseService(session).list_cases()
        ]

    @app.post("/api/cases", status_code=201, name="api_create_case")
    def api_create_case(
        payload: CaseCreatePayload,
        session: SessionDependency,
    ) -> dict[str, object]:
        case = CaseService(session).create_case(payload.title, payload.description)
        return {"id": case.id, "title": case.title, "token_prefix": case.token_prefix}

    @app.get("/api/cases/{case_id}", name="api_case_detail")
    def api_case_detail(case_id: str, session: SessionDependency) -> dict[str, object]:
        case = load_case_view(session, case_id)
        return {
            "id": case.id,
            "title": case.title,
            "description": case.description,
            "oracle_version": case.oracle_version,
            "token_prefix": case.token_prefix,
            "artifacts": [
                {
                    "id": artifact.id,
                    "type": artifact.detected_type,
                    "display_name": artifact.display_name,
                    "current_revision_id": artifact.current_revision_id,
                    "revision_count": len(artifact.revisions),
                }
                for artifact in case.artifacts
            ],
            "runs": [
                {
                    "id": run.id,
                    "created_at": run.created_at.isoformat(),
                    "validation_status": run.validation_status,
                    "response_count": len(run.responses),
                }
                for run in case.runs
            ],
        }

    @app.post("/api/cases/{case_id}/artifacts", status_code=201, name="api_add_artifact")
    def api_add_artifact(
        case_id: str,
        payload: ArtifactCreatePayload,
        session: SessionDependency,
    ) -> dict[str, object]:
        try:
            result = CaseService(session).add_artifact(case_id, payload.text, payload.display_name)
        except CaseNotFoundError:
            raise HTTPException(status_code=404, detail="Case not found") from None
        return {
            "id": result.artifact.id,
            "type": result.artifact.detected_type,
            "revision_id": result.revision.id,
        }

    @app.post("/api/artifacts/{artifact_id}/revisions", status_code=201)
    def api_revise_artifact(
        artifact_id: str,
        payload: ArtifactRevisionPayload,
        session: SessionDependency,
    ) -> dict[str, object]:
        try:
            revision = CaseService(session).revise_artifact(artifact_id, payload.text)
        except LookupError:
            raise HTTPException(status_code=404, detail="Artifact not found") from None
        return {"id": revision.id, "version": revision.version}

    @app.post("/api/cases/{case_id}/runs", status_code=201, name="api_create_run")
    def api_create_run(
        case_id: str,
        payload: RunCreatePayload,
        session: SessionDependency,
    ) -> dict[str, object]:
        try:
            run = ObfuscationService(session).run(case_id, payload.question)
        except CaseNotFoundError:
            raise HTTPException(status_code=404, detail="Case not found") from None
        return {
            "id": run.id,
            "prompt_text": run.prompt_text,
            "validation_status": run.validation_status,
            "findings": [
                {"severity": item.severity, "code": item.code, "message": item.message}
                for item in run.findings
            ],
        }

    @app.get("/api/runs/{run_id}", name="api_get_run")
    def api_get_run(run_id: str, session: SessionDependency) -> dict[str, object]:
        try:
            run = ObfuscationService(session).get_run(run_id)
        except LookupError:
            raise HTTPException(status_code=404, detail="Run not found") from None
        return {
            "id": run.id,
            "case_id": run.case_id,
            "prompt_text": run.prompt_text,
            "validation_status": run.validation_status,
            "artifacts": [
                {
                    "type": item.artifact_type,
                    "display_name": item.display_name,
                    "obfuscated_text": item.obfuscated_text,
                    "replacement_count": len(item.replacements),
                }
                for item in run.run_artifacts
            ],
            "responses": [
                {
                    "id": response.id,
                    "model_label": response.model_label,
                    "restored_text": response.restored_text,
                    "unknown_markers": response.unknown_markers,
                }
                for response in run.responses
            ],
        }

    @app.get("/api/run-artifacts/{run_artifact_id}/restored")
    def api_restore_run_artifact(
        run_artifact_id: str,
        session: SessionDependency,
    ) -> dict[str, str]:
        try:
            restored = ObfuscationService(session).restore_run_artifact(run_artifact_id)
        except LookupError:
            raise HTTPException(status_code=404, detail="Run artifact not found") from None
        return {"restored_text": restored}

    @app.post("/api/runs/{run_id}/responses", status_code=201, name="api_response")
    def api_response(
        run_id: str,
        payload: ResponseCreatePayload,
        session: SessionDependency,
    ) -> dict[str, object]:
        try:
            response = ResponseService(session).create_response(
                run_id, payload.text, payload.model_label
            )
        except LookupError:
            raise HTTPException(status_code=404, detail="Run not found") from None
        return {
            "id": response.id,
            "restored_text": response.restored_text,
            "unknown_markers": response.unknown_markers,
        }

    @app.delete("/api/cases/{case_id}", status_code=204, name="api_delete_case")
    def api_delete_case(case_id: str, session: SessionDependency) -> None:
        try:
            CaseService(session).delete_case(case_id)
        except CaseNotFoundError:
            raise HTTPException(status_code=404, detail="Case not found") from None

    return app

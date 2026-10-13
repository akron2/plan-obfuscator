from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from fastapi.testclient import TestClient

from plan_obfuscator.config import Settings
from plan_obfuscator.migration_runner import migration_config, upgrade_database
from plan_obfuscator.web import REQUEST_TOKEN_FIELD, REQUEST_TOKEN_HEADER, create_app


def test_full_json_api_workflow(client, fixture_text) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    created = client.post("/api/cases", json={"title": "API case", "description": ""})
    assert created.status_code == 201
    case_id = created.json()["id"]

    for name in ("query.sql", "xplan_19c.txt", "sql_monitor_19c.txt"):
        response = client.post(
            f"/api/cases/{case_id}/artifacts",
            json={"text": fixture_text(name), "display_name": name},
        )
        assert response.status_code == 201, response.text

    run_response = client.post(f"/api/cases/{case_id}/runs", json={"question": "Explain the plan"})
    assert run_response.status_code == 201, run_response.text
    payload = run_response.json()
    assert "OBF_" in payload["prompt_text"]
    run_id = payload["id"]

    table_marker = next(
        word.strip(".,;:()\"'") for word in payload["prompt_text"].split() if "_TBL_" in word
    )
    restored = client.post(
        f"/api/runs/{run_id}/responses",
        json={"text": f"Inspect {table_marker}", "model_label": "Claude"},
    )
    assert restored.status_code == 201
    assert "Inspect " in restored.json()["restored_text"]
    assert "OBF_" not in restored.json()["restored_text"]

    detail = client.get(f"/api/cases/{case_id}")
    assert detail.status_code == 200
    assert len(detail.json()["artifacts"]) == 3
    assert detail.json()["runs"][0]["response_count"] == 1
    assert client.get(f"/cases/{case_id}").status_code == 200


def test_form_upload_accepts_cp1251(client) -> None:
    case_id = client.post("/api/cases", json={"title": "Upload"}).json()["id"]
    text = "select name from private_table where code = 'СЕКРЕТ'"
    response = client.post(
        f"/cases/{case_id}/artifacts",
        files={"upload": ("query.sql", text.encode("cp1251"), "text/plain")},
        data={"display_name": "cp1251 query", "text": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    detail = client.get(f"/api/cases/{case_id}").json()
    assert detail["artifacts"][0]["type"] == "sql"


def test_used_artifact_leaves_context_but_preserves_history(client, fixture_text) -> None:
    case_id = client.post("/api/cases", json={"title": "History"}).json()["id"]
    artifact = client.post(
        f"/api/cases/{case_id}/artifacts",
        json={"text": fixture_text("query.sql"), "display_name": "query"},
    ).json()
    client.post(f"/api/cases/{case_id}/runs", json={"question": ""})
    response = client.post(f"/artifacts/{artifact['id']}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert "notice=" in response.headers["location"]
    detail = client.get(f"/api/cases/{case_id}").json()
    assert detail["artifacts"] == []
    assert len(detail["runs"]) == 1


def test_migration_is_repeatable(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "migration")
    upgrade_database(settings)
    upgrade_database(settings)
    connection = sqlite3.connect(settings.database_path)
    try:
        version = connection.execute("select version_num from alembic_version").fetchone()
        tables = {
            row[0]
            for row in connection.execute("select name from sqlite_master where type='table'")
        }
    finally:
        connection.close()
    assert version == ("0001_clean_schema",)
    assert {"cases", "symbols", "obfuscation_runs", "responses"} <= tables

    config = migration_config(settings)
    command.check(config)
    command.downgrade(config, "base")
    connection = sqlite3.connect(settings.database_path)
    try:
        remaining = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table'"
            )
        }
    finally:
        connection.close()
    assert "cases" not in remaining
    command.upgrade(config, "head")


def test_incompatible_database_is_set_aside(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "old")
    settings.ensure_directories()
    connection = sqlite3.connect(settings.database_path)
    try:
        connection.execute("create table alembic_version (version_num varchar(32) not null)")
        connection.execute("insert into alembic_version values ('0002_artifact_active')")
        connection.execute("create table cases (id text primary key)")
        connection.commit()
    finally:
        connection.close()

    upgrade_database(settings)

    backups = list(settings.data_dir.glob("plan_obfuscator.incompatible-*.db"))
    assert len(backups) == 1
    connection = sqlite3.connect(settings.database_path)
    try:
        version = connection.execute("select version_num from alembic_version").fetchone()
    finally:
        connection.close()
    assert version == ("0001_clean_schema",)


def test_state_changing_requests_require_run_token(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "token-data"))
    with TestClient(app, base_url="http://127.0.0.1") as anonymous:
        assert anonymous.get("/health").status_code == 200
        assert anonymous.get("/").status_code == 200
        assert anonymous.post("/cases/quick", follow_redirects=False).status_code == 403
        assert (
            anonymous.post(
                "/cases/quick",
                data={REQUEST_TOKEN_FIELD: "wrong"},
                follow_redirects=False,
            ).status_code
            == 403
        )
        assert anonymous.post("/api/cases", json={"title": "x"}).status_code == 403

        created = anonymous.post(
            "/cases/quick",
            data={REQUEST_TOKEN_FIELD: app.state.request_token},
            follow_redirects=False,
        )
        assert created.status_code == 303
        page = anonymous.get(created.headers["location"].split("#", 1)[0])
        assert page.status_code == 200
        form_count = page.text.count('<form method="post"')
        assert form_count > 0
        assert page.text.count(f'value="{app.state.request_token}"') == form_count

        listed = anonymous.get("/api/cases").json()
        case_id = listed[0]["id"]
        assert anonymous.delete(f"/api/cases/{case_id}").status_code == 403
        assert (
            anonymous.delete(
                f"/api/cases/{case_id}",
                headers={REQUEST_TOKEN_HEADER: app.state.request_token},
            ).status_code
            == 204
        )


def test_foreign_host_header_is_rejected(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "host-data"))
    with TestClient(app, base_url="http://attacker.example") as foreign:
        assert foreign.get("/health").status_code == 400


def test_multipart_upload_accepts_form_run_token(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "upload-token"))
    with TestClient(app, base_url="http://127.0.0.1") as anonymous:
        token = {REQUEST_TOKEN_FIELD: app.state.request_token}
        created = anonymous.post("/cases/quick", data=token, follow_redirects=False)
        case_id = created.headers["location"].split("/cases/", 1)[1].split("#", 1)[0]
        response = anonymous.post(
            f"/cases/{case_id}/messages",
            files={"upload": ("query.sql", b"select secret_col from private_tab", "text/plain")},
            data={**token, "text": "", "mode": "auto", "display_name": ""},
            follow_redirects=False,
        )
        assert response.status_code in {200, 303}, response.text
        detail = anonymous.get(f"/api/cases/{case_id}").json()
        assert len(detail["artifacts"]) == 1


def test_chat_workspace_and_universal_composer_flow(client, fixture_text) -> None:
    empty = client.get("/")
    assert empty.status_code == 200
    assert "Начните новый кейс" in empty.text
    assert "case-sidebar" in empty.text

    created = client.post("/api/cases", json={"title": "Новый кейс"}).json()
    case_id = created["id"]
    material = client.post(
        f"/api/cases/{case_id}/messages",
        json={"text": fixture_text("query.sql"), "mode": "auto"},
    )
    assert material.status_code == 201
    assert material.json()["kind"] == "material"

    question = client.post(
        f"/api/cases/{case_id}/messages",
        json={"text": "Почему запрос медленный?", "mode": "auto"},
    )
    assert question.status_code == 201
    question_payload = question.json()
    assert question_payload["kind"] == "question"
    marker = next(
        word.strip(".,;:()\"'")
        for word in question_payload["prompt_text"].split()
        if "_TBL_" in word
    )

    response = client.post(
        f"/api/cases/{case_id}/messages",
        json={"text": f"Проверьте {marker}", "mode": "auto"},
    )
    assert response.status_code == 201
    assert response.json()["kind"] == "response"
    assert "OBF_" not in response.json()["restored_text"]

    detail = client.get(f"/cases/{case_id}")
    assert detail.status_code == 200
    assert "Почему запрос медленный?" in detail.text
    assert 'role="log"' in detail.text
    assert "Последний цикл" in detail.text
    assert "Вся история" in detail.text
    assert "Восстановленный ответ" in detail.text
    assert "Показать оригинал" in detail.text
    assert 'data-composer' in detail.text


def test_chat_question_without_context_preserves_draft(client) -> None:
    case_id = client.post("/api/cases", json={"title": "Новый кейс"}).json()["id"]
    question = "Почему запрос медленный?"
    response = client.post(
        f"/cases/{case_id}/messages",
        data={"text": question, "mode": "auto", "display_name": ""},
    )
    assert response.status_code == 422
    assert "Сначала добавьте SQL" in response.text
    assert question in response.text


def test_chat_ambiguous_text_shows_inline_mode_choice(client) -> None:
    case_id = client.post("/api/cases", json={"title": "Новый кейс"}).json()["id"]
    payload = "\n".join(f"unknown diagnostic row {index}" for index in range(12))
    response = client.post(
        f"/cases/{case_id}/messages",
        data={"text": payload, "mode": "auto", "display_name": ""},
    )
    assert response.status_code == 422
    assert "Что находится в поле?" in response.text
    assert 'data-force-mode="material"' in response.text
    assert payload in response.text


def test_context_changed_is_visible_after_new_material(client, fixture_text) -> None:
    case_id = client.post("/api/cases", json={"title": "Новый кейс"}).json()["id"]
    client.post(
        f"/api/cases/{case_id}/messages",
        json={"text": fixture_text("query.sql"), "mode": "auto"},
    )
    client.post(
        f"/api/cases/{case_id}/messages",
        json={"text": "Проанализируй запрос", "mode": "auto"},
    )
    client.post(
        f"/api/cases/{case_id}/messages",
        json={"text": '1 - filter("O"."STATUS"=\'OPEN\')', "mode": "auto"},
    )
    page = client.get(f"/cases/{case_id}")
    assert "Контекст изменился" in page.text
    assert "Предыдущий цикл сохранён" in page.text

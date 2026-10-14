# Memory Bank — Plan Obfuscator

## О проекте
Локальный веб-сервис (FastAPI + SQLite + SQLGlot) для безопасной обфускации SQL-текста и планов выполнения Oracle (`DBMS_XPLAN`, SQL Monitor, 10046/tkprof, AWR/Statspack) перед отправкой в публичные LLM (ChatGPT, Claude) с последующим обратным восстановлением (`OBF_*` маркеров) в ответах ИИ.

## Последние изменения
- **Поддержка больших планов (> 1 МБ) и устранение раздувания форм/DOM**:
  - В `plan_obfuscator/web.py` добавлен `LargeFormRoute` (`APIRoute`), выполняющий парсинг `multipart/form-data` и `application/x-www-form-urlencoded` с `max_part_size = 100 МБ` (`MAX_FORM_PART_BYTES`) до запуска `Form()` в FastAPI. Это устранило ошибку Starlette `{"detail":"Part exceeded maximum size of 1024KB."}` при вставке больших планов и при сохранении ревизий.
  - Добавлен обработчик `FormTooLargeError` (возвращает страницу кейса со статусом `413` и понятным сообщением об ошибке вместо сырого JSON Starlette), лимит материалов `MAX_ARTIFACT_BYTES` поднят до `100 МБ` и единообразно проверяется в `CaseService` и при потоковом чтении файлов (`read_uploaded_content`).
  - Лимит `MAX_CLIPBOARD_HTML_CHARS` в `plan_obfuscator/services/answer_formatting.py` увеличен до `10 МБ`.
  - В `plan_obfuscator/static/app.js` и `plan_obfuscator/templates/case.html` убрана дублирующая пересылка полного текста в скрытом поле `pasted_plain` (неизменность вставки отслеживается в памяти JS через `lastPastedPlain`), `pasted_html` не захватывается при вставке SQL/планов, убран дублирующий скрытый `<textarea id="prompt-copy-...">` (копирование идёт напрямую из `<pre>`), а автоопределение типа ввода ограничено срезом первых 32 КБ текста.
  - Обновлены `ARCHITECTURE.md` и тесты (`tests/test_web.py`, `tests/test_answer_formatting.py`).
- **Релиз v0.4.0 — Форматирование и рендеринг ответов ИИ**:
  - Добавлен конвертер HTML буфера обмена (`clipboard_html_to_markdown`) и нормализатор plain-текста (`normalize_plain_answer`) в `plan_obfuscator/services/answer_formatting.py` с поддержкой разметки ChatGPT (таблицы, списки, блоки кода, диаграммы/виджеты `data-d-component`).
  - Добавлен серверный рендеринг Markdown-ответов (`plan_obfuscator/services/response_rendering.py`) через `markdown-it-py` и `Pygments`, при котором восстановление `OBF_*` маркеров выполняется после рендера Markdown (чтобы спецсимволы в оригинальных идентификаторах не ломали разметку).
  - Подключён локальный `mermaid.min.js` (`plan_obfuscator/static/vendor/`) для отображения Mermaid-диаграмм и переключатель «Оформленный / Исходный текст» в UI кейса.
  - Добавлены фикстуры буфера обмена ChatGPT (`tests/fixtures/clipboard/`) и модульные/интеграционные тесты (`tests/test_answer_formatting.py`, `tests/test_chat.py`, `tests/test_web.py`).
  - Обновлены `ARCHITECTURE.md`, `README.md`, `pyproject.toml` и `requirements.lock`.
  - Поднята версия до `0.4.0`, собран standalone Windows-исполняемый файл `PlanObfuscator-v0.4.0-windows-x64.exe` и опубликован GitHub Release `v0.4.0`.

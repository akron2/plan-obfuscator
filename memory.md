# Memory Bank — Plan Obfuscator

## О проекте
Локальный веб-сервис (FastAPI + SQLite + SQLGlot) для безопасной обфускации SQL-текста и планов выполнения Oracle (`DBMS_XPLAN`, SQL Monitor, 10046/tkprof, AWR/Statspack) перед отправкой в публичные LLM (ChatGPT, Claude) с последующим обратным восстановлением (`OBF_*` маркеров) в ответах ИИ.

## Последние изменения
- **Релиз v0.4.0 — Форматирование и рендеринг ответов ИИ**:
  - Добавлен конвертер HTML буфера обмена (`clipboard_html_to_markdown`) и нормализатор plain-текста (`normalize_plain_answer`) в `plan_obfuscator/services/answer_formatting.py` с поддержкой разметки ChatGPT (таблицы, списки, блоки кода, диаграммы/виджеты `data-d-component`).
  - Добавлен серверный рендеринг Markdown-ответов (`plan_obfuscator/services/response_rendering.py`) через `markdown-it-py` и `Pygments`, при котором восстановление `OBF_*` маркеров выполняется после рендера Markdown (чтобы спецсимволы в оригинальных идентификаторах не ломали разметку).
  - Подключён локальный `mermaid.min.js` (`plan_obfuscator/static/vendor/`) для отображения Mermaid-диаграмм и переключатель «Оформленный / Исходный текст» в UI кейса.
  - Добавлены фикстуры буфера обмена ChatGPT (`tests/fixtures/clipboard/`) и модульные/интеграционные тесты (`tests/test_answer_formatting.py`, `tests/test_chat.py`, `tests/test_web.py`).
  - Обновлены `ARCHITECTURE.md`, `README.md`, `pyproject.toml` и `requirements.lock`.
  - Поднята версия до `0.4.0`, собран standalone Windows-исполняемый файл `PlanObfuscator-v0.4.0-windows-x64.exe` и опубликован GitHub Release `v0.4.0`.

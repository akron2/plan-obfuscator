# Memory Bank — Plan Obfuscator

## О проекте
Локальный веб-сервис (FastAPI + SQLite + SQLGlot) для безопасной обфускации SQL-текста и планов выполнения Oracle (`DBMS_XPLAN`, SQL Monitor, 10046/tkprof, AWR/Statspack) перед отправкой в публичные LLM (ChatGPT, Claude) с последующим обратным восстановлением (`OBF_*` маркеров) в ответах ИИ.

## Последние изменения
- **Ускорение загрузки и обфускации больших планов (~500 тыс. символов и более)**:
  - В `plan_obfuscator/parsing/types.py` (`ParsedArtifact.finalize`) квадратичная проверка пересечений `O(M²)` по всему списку `accepted` заменена на линейный sweep-line `O(M log M)` с проверкой только последнего непересекающегося интервала `accepted[-1]` (ускорение `finalize` на 30 тыс. вхождений с ~21.3 с до ~15 мс).
  - В `plan_obfuscator/parsing/pipeline.py`, `xplan.py`, `monitor.py` и `services/cases.py` (`_validate_sql`) выделено быстрое извлечение SQL (`extract_artifact_sql`) без холостого полного разбора таблицы плана и предикатов при добавлении материала в чат (`ChatService.submit`), а также проброс уже определённого `ArtifactKind` без повторных вызовов `detect_artifact_kind` (загрузка плана 500 КБ ускорена с ~16–24 с до ~70 мс).
  - В `plan_obfuscator/parsing/xplan.py` (`_predicate_section`) и `pipeline.py` (`_parse_predicates`) устранён квадратичный `re.search` по остатку секции и добавлено локальное кэширование повторяющихся выражений предикатов; в `sql.py` (`_fallback_tokens`) квадратичная проверка пересечений заменена на проход курсором по слитым интервалам, закэширован `ORACLE_DIALECT` и скомпилированы регулярные выражения.
  - В `plan_obfuscator/services/obfuscation.py` (`exact_restore`, `restore_run_artifact`) посимвольное перерезание строки `O(M × N)` заменено на линейную сборку `"".join(parts)` (`O(N)`), а пост-проверка утечек `_post_transform_findings` переведена на однопроходную индексацию слов в `obfuscated.text`.
  - В `plan_obfuscator/services/tokens.py` (`SymbolRegistry`) убран поштучный `session.flush()` и линейный поиск в `case.symbols` внутри цикла; в `ObfuscationService.run` сохранение `ReplacementOccurrence` переведено на пакетный `insert(ReplacementOccurrence)`, а из `load_case_view` и `CaseService.list_cases` убрана холостая жадная вычитка `RunArtifact.replacements` и `Artifact.revisions`.
  - В `plan_obfuscator/static/app.js` функция `autoGrow()` для текстов > 4 КБ сразу выставляет максимальную высоту `240px` без принудительного пересчёта `scrollHeight`.
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

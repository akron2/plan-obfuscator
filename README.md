# Plan Obfuscator

Локальное веб-приложение для обратимой обфускации Oracle SQL, `DBMS_XPLAN`,
outline, predicates и SQL Monitor `TEXT` перед передачей материалов в ChatGPT
или Claude. Ответ модели вставляется обратно, и приложение восстанавливает
исходные имена.

Приложение работает только на `127.0.0.1`, не вызывает внешние API и хранит
историю в локальной SQLite. Архитектура и принятые решения описаны в
[`ARCHITECTURE.md`](ARCHITECTURE.md).

## Быстрый запуск

Требуется Windows и Python 3.11, 3.12 или 3.13.

```bat
run.bat
```

`run.bat` находит подходящий Python, при необходимости создаёт `.venv` в каталоге
проекта и сверяет установленные пакеты с `requirements.lock`. Если всё совпадает,
`pip` не запускается и обращений в интернет нет. Иначе точные версии
устанавливаются с проверкой хешей только в `.venv`.

Установка зависимостей через proxy:

```bat
run.bat --proxy http://proxy.example:3128
```

Параметры приложения можно совмещать с `--proxy`:

```bat
run.bat --proxy http://proxy.example:3128 --port 9000 --no-browser
```

URL proxy с логином или специальными символами заключайте в двойные кавычки.

После запуска приложение открывается по адресу `http://127.0.0.1:8765`.
SQLite по умолчанию находится в
`%LOCALAPPDATA%\PlanObfuscator\plan_obfuscator.db`; каталог можно изменить
параметром `--data-dir` или переменной `PLAN_OBFUSCATOR_DATA_DIR`.

## Работа с приложением

1. Нажмите «Новый кейс». Один кейс соответствует одному логическому SQL.
2. Вставьте SQL, планы и отчёты SQL Monitor в единое поле внизу. Формат
   определяется автоматически; одна вставка может содержать несколько
   материалов и вопрос обычным текстом. Поддерживается drag-and-drop `.sql`,
   `.txt` и `.log`.
3. Отправьте вопрос — приложение сформирует обфусцированный prompt.
4. Скопируйте prompt в ChatGPT или Claude. Не просите модель переименовывать
   маркеры `OBF_*`.
5. Вставьте ответ модели в то же поле. Приложение распознает его по маркерам,
   восстановит известные сущности и сохранит сырой и восстановленный варианты.

`Enter` добавляет строку, `Ctrl+Enter` отправляет. Лента по умолчанию показывает
последний цикл; переключатель в заголовке открывает всю историю кейса.

Mapping стабилен только внутри кейса. Повреждённые и неизвестные маркеры не
восстанавливаются наугад: они остаются в тексте и отмечаются предупреждением.

### Получение материалов Oracle 19c

Расширенный план последнего cursor:

```sql
SELECT *
FROM TABLE(
  DBMS_XPLAN.DISPLAY_CURSOR(
    NULL,
    NULL,
    'ALLSTATS LAST +OUTLINE +ALIAS +PREDICATE +PROJECTION +PEEKED_BINDS'
  )
);
```

SQL Monitor в поддерживаемом формате:

```sql
SELECT DBMS_SQLTUNE.REPORT_SQL_MONITOR(
  sql_id       => :sql_id,
  type         => 'TEXT',
  report_level => 'ALL'
)
FROM dual;
```

Использование SQL Monitor должно соответствовать лицензированию Oracle в вашем
контуре.

## Что изменяется

Маскируются identifiers Oracle, aliases, query blocks, SQL ID, plan hash,
session/execution IDs, module/service/program/host, binds, SQL literals и
пользовательские comments. Cost, rows, bytes, elapsed/CPU time, I/O, memory и
timestamps сохраняются без изменений.

Незнакомые секции и фрагменты маскируются целиком: приложение предпочитает
потерять часть диагностической информации, а не оставить возможный секрет.

## История и резервное копирование

База — обычная SQLite без шифрования, с rollback journal. Для резервного
копирования при остановленном приложении достаточно скопировать файл
`plan_obfuscator.db`. Защита файла обеспечивается локальным контуром.

Версия 0.3 использует новую чистую схему. Базу от более ранней версии
приложение не мигрирует: при первом запуске она переименовывается в
`plan_obfuscator.incompatible-<время>.db`, и создаётся пустая база.

## Локальная защита

Сервер слушает только `127.0.0.1` и принимает лишь Host `127.0.0.1` или
`localhost`. При каждом запуске создаётся случайный ключ; все формы получают его
автоматически, а изменяющие запросы без ключа отклоняются с кодом 403. Так
посторонняя веб-страница не может отправить данные в локальное приложение. Если
после перезапуска сервера отправка формы вернула ошибку, обновите страницу.

## Разработка

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest
```

Тесты автоматически подключают каждый файл из [`test-corpus`](test-corpus).
Как добавить свои обезличенные примеры, описано в
[`test-corpus/README.md`](test-corpus/README.md).

Обновление `requirements.lock` после изменения зависимостей в `pyproject.toml`:

```powershell
.\.venv\Scripts\python.exe -m piptools compile --generate-hashes --strip-extras --output-file=requirements.lock pyproject.toml
```

Smoke-тест на локальном Docker Oracle:

```powershell
.\.venv\Scripts\python.exe .\scripts\oracle_smoke.py --container sqlexplorer-oracle21
```

Сборка автономного `dist\PlanObfuscator.exe`:

```powershell
.\scripts\build_windows.ps1
```

## Экспериментальное расширение Chrome

В [`experimental/chrome-extension`](experimental/chrome-extension) сохранён
замороженный эксперимент со встраиванием в интерфейс ChatGPT и Claude. Он не
входит в основную сборку и проверяется отдельным CI только при изменениях в его
каталоге или в общем corpus. Основной продукт — Python-приложение.

## Ограничения

- Oracle Database 19c; live smoke дополнительно выполняется на Oracle XE 21c;
- SQL Monitor только в формате `TEXT`;
- один логический SQL на кейс, несколько планов и выполнений одного SQL
  поддерживаются;
- обмен с языковой моделью выполняется вручную через copy/paste.

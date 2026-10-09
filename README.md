# Plan Obfuscator

Локальное веб-приложение для обратимой обфускации Oracle SQL,
`DBMS_XPLAN`, outline, predicates и SQL Monitor `TEXT` перед передачей материалов
в ChatGPT или Claude.

Целевая архитектура и принятые решения описаны в
[`ARCHITECTURE.md`](ARCHITECTURE.md).

## Быстрый запуск

Требуется Python 3.11 или новее.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m plan_obfuscator
```

После запуска приложение откроется по адресу `http://127.0.0.1:8765`.
По умолчанию SQLite находится в
`%LOCALAPPDATA%\PlanObfuscator\plan_obfuscator.db`.

Путь можно переопределить переменной `PLAN_OBFUSCATOR_DATA_DIR`.

### Запуск через `run.bat`

`run.bat` проверяет установленные runtime-зависимости локально. Если их версии
подходят, `pip` не запускается и обращений в интернет нет. Если зависимости
отсутствуют или несовместимы, они устанавливаются из `pyproject.toml`.

Обычный запуск:

```bat
run.bat
```

Установка недостающих зависимостей через proxy:

```bat
run.bat --proxy http://proxy.example:3128
```

Параметр можно совмещать с параметрами приложения:

```bat
run.bat --proxy http://proxy.example:3128 --port 9000 --no-browser
```

Если используется proxy с логином или специальными символами, URL следует
заключить в двойные кавычки. Готовый `PlanObfuscator.exe` уже содержит
зависимости и в bootstrap не нуждается.

## Работа с приложением

1. Создайте кейс. Один кейс соответствует одному логическому SQL.
2. Добавьте SQL и связанные материалы в любом порядке. Формат определяется
   автоматически.
3. В одном кейсе можно хранить несколько `DBMS_XPLAN` и SQL Monitor отчётов для
   разных выполнений этого SQL.
4. Укажите вопрос и нажмите «Сформировать prompt».
5. Скопируйте prompt в ChatGPT или Claude. Не просите модель переименовывать
   `OBF_*` маркеры.
6. Вставьте ответ модели обратно. Приложение восстановит известные сущности,
   покажет повреждённые маркеры и сохранит обе версии ответа.

Mapping стабилен только внутри кейса. Новый кейс получает независимый набор
маркеров. Редактирование источника создаёт новую ревизию; старые runs и ответы
остаются привязаны к прежней ревизии.

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
timestamps сохраняются.

Незнакомые секции и фрагменты маскируются целиком: приложение предпочитает
потерять часть диагностической информации, а не оставить возможный секрет.

## История и резервное копирование

База является обычным SQLite без шифрования. Для резервного копирования при
остановленном приложении достаточно скопировать файл `plan_obfuscator.db`.
Защита файла и его резервных копий обеспечивается локальным контуром.

## Ограничения первой версии

- Oracle Database 19c;
- SQL Monitor только в формате `TEXT`;
- один логический SQL на кейс;
- несколько планов и выполнений одного SQL поддерживаются;
- обмен с языковой моделью выполняется вручную через copy/paste;
- приложение не вызывает внешние API.

## Тесты

```powershell
pytest
```

Дополнительный smoke-тест на локальном Docker Oracle:

```powershell
python .\scripts\oracle_smoke.py --container sqlexplorer-oracle21
```

## Windows-сборка

```powershell
.\scripts\build_windows.ps1
```

Готовый executable появится в `dist\PlanObfuscator.exe`.

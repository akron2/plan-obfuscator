# Plan Obfuscator

Самодостаточное Chrome Extension для обратимой обфускации Oracle SQL,
`DBMS_XPLAN`, SQL Monitor `TEXT`, outline и predicates непосредственно в
ChatGPT и Claude.

Расширение не запускает Python, localhost service или Native Messaging host.
Parser, mapping, storage и UI полностью входят в Manifest V3 package.

## Основной workflow

1. Расширение размещает защищённый composer поверх штатного поля ChatGPT/Claude.
2. Пользователь вводит или вставляет Oracle-материалы.
3. Кнопка `Обфусцировать` заменяет чувствительные значения на стабильные
   `OBF_*` markers.
4. Только обфусцированный prompt записывается в штатный composer.
5. Пользователь нажимает обычную кнопку Send.
6. После завершения ответа расширение восстанавливает identifiers и показывает
   текст в extension-origin iframe.

Mapping хранится локально по conversation. История prompts и responses не
дублируется — её хранит сам AI chat.

## Разработка

Требуется Node.js 22 или новее.

```powershell
npm install
npm run verify
```

Готовый unpacked extension находится в `extension/dist`.

Отдельные проверки:

```powershell
npm run test:coverage
npm run smoke:chrome
npm run smoke:oracle -- --container sqlexplorer-oracle21
npm run smoke:oracle-matrix -- --container sqlexplorer-oracle21
```

Oracle smoke выполняет реальный запрос в Docker, получает `DBMS_XPLAN` и SQL
Monitor `TEXT`, проверяет source spans и exact round-trip TypeScript parser.

Расширенная Oracle matrix создаёт временную синтетическую schema, выполняет
index/join/partition/window/hierarchy/JSON/PIVOT/bind/distributed сценарии,
строит DML plans и SQL Monitor разных уровней, затем удаляет schema и возвращает
изменённые Oracle-параметры. Зафиксированный corpus находится в
`test-corpus/oracle-live-21c`; пользовательских данных в нём нет.

## Установка unpacked build

1. Откройте `chrome://extensions`.
2. Включите Developer mode.
3. Нажмите `Load unpacked`.
4. Выберите каталог `extension/dist`.

Для воспроизводимого ZIP:

```powershell
npm run package
```

Артефакт появится в `release/plan-obfuscator-v0.2.0.zip`.

## Использование

1. Откройте ChatGPT или Claude. Расширение накроет штатный composer своим
   extension-origin редактором.
2. Вставьте prompt вместе с Oracle SQL/планом. Для смешанного текста и SQL
   рекомендуется fenced code block с языком `sql`.
3. Нажмите `Обфусцировать`. В штатное поле попадёт только результат с `OBF_*`.
4. Нажмите штатную кнопку Send.
5. После завершения streaming восстановленный ответ появится в отдельном
   extension-origin блоке с переключателем `восстановленный / OBF`.

По умолчанию guard блокирует отправку незащищённого текста. Режимы `warn` и
`off`, автоматическое восстановление, видимость OBF-оригинала и поддерживаемые
сайты настраиваются в popup расширения.

Расширение хранит только reversible mapping на conversation в
`chrome.storage.local`. Prompt/response history не дублируется. Одноразовый
payload восстановленного ответа живёт в `chrome.storage.session` до первого
чтения iframe.

## Архитектура

Полная спецификация: [`ARCHITECTURE_EXTENSION.md`](ARCHITECTURE_EXTENSION.md).

Предыдущая Python-реализация находится в [`legacy/python-app`](legacy/python-app)
и не входит в extension build.

## Поддержка и ограничения

- Chrome 114+, Manifest V3; incognito отключён.
- Oracle 19c semantics; live smoke дополнительно проверен на Oracle XE 21c.
- Поддерживаются SQL, `DBMS_XPLAN`, SQL Monitor `TEXT`, outline, predicates,
  quoted identifiers, q/nq strings, binds, database links и legacy outer join.
- DOM ChatGPT/Claude не является публичным API. Adapter работает fail-safe:
  если composer не найден, plaintext в страницу не записывается.
- Mapping не шифруется: продукт рассчитан на локальный защищённый профиль, как
  и было согласовано.

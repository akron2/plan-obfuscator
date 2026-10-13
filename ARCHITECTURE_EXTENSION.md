# Архитектура: Plan Obfuscator Chrome Extension

Статус: реализовано и проверено
Дата фиксации: 2026-10-13

## Статус реализации

Архитектура реализована в `extension/` без runtime-зависимости от Python.
Предыдущий продукт изолирован в `legacy/python-app`. Сборка создаёт unpacked
каталог и воспроизводимый ZIP.

Проверено 2026-10-12:

- strict TypeScript compilation;
- 108 unit/integration tests для lexer/parser, exact restore, storage, service
  worker protocol, content integration и DOM adapters;
- coverage: 89.8% statements, 78.6% branches, 92.3% lines, 94.1% functions;
- Manifest V3 permission/remote-code audit;
- Chrome headless load-unpacked smoke без manifest/runtime ошибок;
- собранный content bundle проверен на актуальном публичном DOM ChatGPT:
  `textarea#mobile-composer-prompt` найден, скрыт и накрыт защищённым iframe;
- live Docker Oracle smoke: ~39k DBMS_XPLAN и ~14k SQL Monitor TEXT обработаны
  за 117 ms, spans корректны, SQL извлечён, parser findings отсутствуют, exact
  round-trip подтверждён;
- live Oracle matrix: 23 реальных XPLAN/SQL Monitor отчёта, 19 SQL/DDL/DML
  входов, 440 golden sensitive values и 1 044 report spans; полный прогон
  parser занимает около 70 ms;
- 1 150 seeded property-based cases проверяют generated SQL, hostile/malformed
  text и mutations реальных отчётов;
- повторная ZIP-сборка с одинаковым SHA-256.

## Цель и критерии успеха

Plan Obfuscator становится самодостаточным Chrome Extension Manifest V3. Оно
встраивается в web-интерфейсы ChatGPT и Claude, обфусцирует Oracle-материалы до
отправки и показывает восстановленные ответы непосредственно рядом с сообщениями
модели.

Расширение не запускает Python, EXE, localhost server или Native Messaging host.
Весь parser, mapping, storage и UI входят в extension package.

Решение считается завершённым, если:

1. Plaintext prompt вводится в extension-origin composer iframe и недоступен
   JavaScript страницы.
2. В штатный composer ChatGPT/Claude записывается только обфусцированный текст.
3. Пользователь явно запускает преобразование кнопкой защищённого редактора и
   самостоятельно нажимает штатный Send; при отказе adapter остаётся видимый
   плавающий fallback launcher.
4. Необфусцированный Oracle-текст перед отправкой обрабатывается согласно
   настройке `block`, `warn` или `off`; default — `block`.
5. Ответ модели автоматически восстанавливается после завершения streaming.
6. Восстановленный ответ отображается в extension-origin iframe, а не как
   plaintext text node страницы.
7. Mapping стабилен в пределах conversation и переживает reload/reopen.
8. Расширение не хранит историю prompts, source artifacts и responses; историю
   отображает сам AI chat.
9. Поддерживаются Oracle SQL, DBMS_XPLAN, SQL Monitor TEXT, outline и predicates
   с сохранением performance metrics.
10. Прямое преобразование имеет exact round-trip, а повреждённые markers
    диагностируются и не восстанавливаются нечётким поиском.
11. ChatGPT и Claude изолированы отдельными site adapters.
12. Extension собирается в каталог для `Load unpacked` и воспроизводимый ZIP.
13. Python-приложение перемещено в `legacy/python-app` и не участвует в extension
    build или runtime.

## Ограничения и допущения

- Целевой браузер первой версии — Google Chrome 114+.
- Используется Manifest V3 и полностью локальный bundled JavaScript.
- Поддерживаемые сайты: `https://chatgpt.com/*` и `https://claude.ai/*`.
- Целевая версия Oracle — 19c; parser остаётся fail-closed для неизвестных
  конструкций.
- Страница сайта может видеть наличие extension iframes, их положение и размеры,
  но не их cross-origin document или plaintext.
- Страница может удалить injected UI; content script обнаруживает rerender и
  восстанавливает интеграцию.
- DOM сайтов не является стабильным API. Adapter failures должны быть явными и
  не приводить к отправке plaintext.
- Отправка выполняется штатной кнопкой сайта после обфускации.
- Response restoration первой версии выполняется после завершения генерации, не
  token-by-token.
- Mapping хранится локально без дополнительного шифрования, как и ранее SQLite в
  доверенном локальном контуре.
- Старые conversation mappings хранятся до ручной очистки или удаления
  расширения; автоматического TTL нет.
- Incognito mode в первой версии не поддерживается.

## Выбранное решение

### Общая схема

```text
ChatGPT / Claude page
        │
        ├── Site Adapter
        │    ├── locate native composer/send
        │    ├── position secure composer iframe
        │    ├── write OBF prompt
        │    ├── guard Send
        │    └── observe assistant messages
        │
        ├── Secure Composer iframe (chrome-extension://)
        │    ├── plaintext editor
        │    ├── floating shield action
        │    ├── protected/dirty/error states
        │    └── runtime messaging only
        │
        └── Secure Restored iframe (chrome-extension://)
             ├── restored text
             ├── copy
             └── restored/raw toggle

MV3 Service Worker
        ├── Oracle parsing core
        ├── symbol registry
        ├── obfuscation/restoration
        ├── validation
        ├── conversation coordination
        └── storage gateway
                 │
                 ▼
          chrome.storage.local/session
```

### Безопасный composer

Content script находит штатный editable element и размещает над его текстовой
областью web-accessible extension iframe. Штатный editable остаётся в layout, но
не принимает пользовательский plaintext.

Если adapter не находит пригодный видимый editable, content script не исчезает:
он показывает фиксированную кнопку `Обфусцировать` в правом нижнем углу. Кнопка
открывает тот же extension-origin composer как отдельную fallback-панель. В этом
режиме безопасный OBF-результат можно скопировать отдельной кнопкой. При появлении
поддерживаемого штатного composer iframe автоматически возвращается в режим
overlay.

Пользователь вводит prompt внутри iframe. Iframe отправляет plaintext напрямую
service worker через `chrome.runtime.sendMessage`. После обфускации iframe
передаёт parent только безопасный OBF-текст. Content script записывает этот текст
в штатный composer framework-compatible способом и dispatches `input`.

Состояния кнопки:

```text
idle       🛡 Обфусцировать
working    ◌ Анализ…
protected  ✓ Защищено · N замен
dirty      △ Изменён
error      ! Ошибка
```

Изменение plaintext после успешного преобразования переводит состояние в
`dirty` и очищает штатный composer до повторной обфускации.

### Send guard

Content script перехватывает Send click и клавиатурную отправку на capture phase.
Настройка:

- `block` — default; отправка останавливается, iframe показывает действия
  `Обфусцировать` и `Отправить как есть`;
- `warn` — первое действие показывает warning, повторное или explicit override
  разрешает один Send;
- `off` — extension не блокирует отправку.

Override одноразовый и не меняет global setting.

### Безопасное восстановление ответа

Content script читает только обфусцированный assistant response. После окончания
streaming он передаётся service worker, который создаёт opaque `renderId` и
сохраняет render payload в `chrome.storage.session`.

Content script скрывает штатное OBF-содержимое и добавляет iframe вида:

```text
chrome-extension://<dynamic-id>/restored-frame.html#<renderId>
```

Iframe самостоятельно получает payload у service worker. Plaintext не проходит
через page DOM или `window.postMessage`. В parent передаются только безопасные
layout messages, например `{ renderId, height }`.

Iframe показывает restored text, copy action и переключатель restored/raw. Raw
OBF-текст также отображается внутри iframe, поэтому page DOM не требуется
раскрывать обратно.

### Conversation mapping

История продукта отсутствует. Хранится только состояние, необходимое для
обратимости:

```text
ConversationMapping
├── schemaVersion
├── site
├── conversationId
├── tokenPrefix
├── nextSequence
├── symbols[]
└── updatedAt
```

Ключ — `<site>:<conversationId>`. Для нового chat до появления ID используется
`<site>:tab:<tabId>`. После navigation adapter отправляет `migrateConversation`,
и service worker атомарно переносит mapping.

Один identifier получает стабильный marker во всех turns conversation. Mapping
не синхронизируется через Chrome account.

`chrome.storage.local` хранит mappings и settings. Доступ выставляется в
`TRUSTED_CONTEXTS`, поэтому content scripts не читают raw mapping. Временные
render payloads хранятся в `chrome.storage.session`.

### TypeScript parsing core

Core не зависит от DOM и Chrome APIs. Он состоит из:

```text
core/
├── types.ts
├── occurrences.ts
├── text-utils.ts
├── oracle-lexer.ts
├── sql-parser.ts
├── xplan-parser.ts
├── sql-monitor-parser.ts
├── detector.ts
├── parser.ts
├── symbol-registry.ts
└── transform.ts
```

Expression/hint parsing реализован как отдельные exports `sql-parser.ts`, чтобы
не дублировать lexer traversal. Exact restore, marker diagnostics и validation
сосредоточены в `transform.ts` и `symbol-registry.ts`.

Oracle lexer сохраняет точные UTF-16 offsets и распознаёт keywords, quoted
identifiers, alternative quoting, literals, binds, comments, hints, operators и
database links.

Shallow SQL parser определяет schemas, tables, aliases, columns, CTE, functions,
query blocks и links. Он не пытается валидировать или выполнять SQL. Parser
работает по source spans; исходный текст не генерируется заново.

DBMS_XPLAN и SQL Monitor обрабатываются section-aware parsers. Для predicates,
projection и hints используется общий Oracle expression tokenizer.

Неизвестный чувствительный fragment заменяется целиком. Metrics в известных
табличных колонках сохраняются. Identity-like numeric fields (SQL ID, plan hash,
object/session IDs) маскируются.

### Site adapters

```text
SiteAdapter
├── id
├── matches(url)
├── getConversationId(url)
├── findComposer()
├── findSendControl()
├── writeComposerText(text)
├── isSendingGesture(event)
├── listAssistantMessages()
├── getMessageContent(element)
├── getMessageKey(element)
├── isStreaming()
└── watch(onChange)
```

`ChatGPTAdapter` и `ClaudeAdapter` имеют независимые selector sets и DOM fixture
tests. Adapter выбирает отображаемый editor среди desktop/mobile вариантов и
ищет Send внутри связанной формы. Adapter не найден или не уверен — extension
переходит в видимый fail-safe fallback и ничего не отправляет в страницу.

### Popup settings

Popup не содержит history. Он показывает:

- enabled/disabled для ChatGPT и Claude;
- send guard: `block`, `warn`, `off`;
- automatic response restoration;
- hide original OBF response;
- restore old visible messages on navigation;
- clear mapping for current conversation;
- clear all mappings;
- version и adapter diagnostic status.

### Permissions

Минимальный manifest:

```text
permissions:
  storage

host_permissions:
  https://chatgpt.com/*
  https://claude.ai/*
```

Content scripts объявляются статически, поэтому `scripting` не нужен.
`tabs` не требуется: sender tab metadata приходит service worker вместе с
runtime messages. Web-accessible frame resources ограничены двумя AI origins и
используют dynamic URL.

## Структура репозитория

```text
plan-obfuscator/
├── extension/
│   ├── manifest.json
│   ├── package.json
│   ├── tsconfig.json
│   ├── src/
│   │   ├── core/
│   │   ├── adapters/
│   │   ├── background/
│   │   ├── content/
│   │   ├── frames/
│   │   └── popup/
│   ├── tests/
│   └── scripts/
├── test-corpus/
│   ├── corpus.schema.json
│   ├── canonical/
│   ├── generated/oracle-21c/
│   └── user/
├── legacy/
│   └── python-app/
└── README.md
```

Extension build не импортирует `legacy`. Корневой README описывает только новый
продукт. Legacy README объясняет статус и прежний запуск.

## Ключевые решения и их обоснование

| Решение | Альтернативы | Обоснование |
|---|---|---|
| Pure TypeScript MV3 | Python localhost, Native Messaging | Один installable artifact и отсутствие фонового runtime. |
| Extension iframe composer | Штатный composer с последующей заменой | Plaintext не попадает в общий DOM страницы. |
| Extension iframe response | Прямая замена text nodes, Shadow DOM | Cross-origin frame не раскрывает restored text page scripts. |
| Floating explicit action | Continuous keystroke replacement | Не ломает caret, undo и IME; пользователь видит момент защиты. |
| Custom Oracle lexer + shallow parser | Generic JS parser, full ANTLR parser | Нужны spans и classification, а не SQL execution; полный контроль над Oracle 19c fallback. |
| Mapping per conversation | Global mapping, per-message mapping | Стабильность multi-turn без корреляции разных chats. |
| chrome.storage.local | SQLite, sync storage, prompt history | Хранится только небольшой mapping; данные не уходят в Chrome Sync. |
| Default guard block | Warn, off | Fail-safe против случайной отправки plaintext. |
| Unpacked ZIP first | Немедленная Web Store публикация | Adapter DOM необходимо стабилизировать до store review. |

## Отклонённые альтернативы и причины

### Python companion

Требует отдельного процесса, установки и IPC. Противоречит цели самодостаточного
extension.

### Side Panel copy/paste workflow

Безопасен, но создаёт второй интерфейс и дополнительные ручные действия.

### Plaintext в штатном composer

Page scripts способны прочитать DOM до обфускации. Не соответствует выбранной
границе защиты.

### Прямая запись restored text в page DOM

Страница может прочитать mutation. Используется extension-origin iframe.

### Shadow DOM как security boundary

Shadow DOM обеспечивает encapsulation, но не является достаточной границей для
plaintext. Используется cross-origin extension document.

### Generic JavaScript SQL parser

Доступные parsers не обеспечивают требуемую Oracle 19c/hints/XPLAN semantics.

### Full ANTLR PL/SQL parser

Большой generated bundle и высокая сложность visitor layer при том, что продукту
нужны spans и semantic categories, а не полная validation grammar.

## Риски и открытые вопросы

Блокирующих продуктовых вопросов нет.

Основные технические риски:

- DOM selectors ChatGPT/Claude меняются; mitigation — независимые adapters,
  выбор отображаемого editor, fixture tests актуальных desktop/mobile вариантов
  и всегда видимый fallback launcher.
- Framework-controlled composer может отвергать programmatic changes; adapter
  должен использовать native setter/contenteditable path и trusted-like input
  event sequence.
- Chrome service worker ephemeral; mapping и counters сохраняются после каждой
  операции.
- Concurrent tabs одной conversation могут обновлять mapping; service worker
  сериализует операции по conversation key.
- Large reports могут блокировать event handler; performance corpus содержит
  реальные 40k+ XPLAN и устанавливает budget.
- Mapping storage не зашифрован; это принято для локального доверенного профиля.
- Web-accessible iframe URL виден странице; restored payload выдаётся только
  extension context по opaque renderId и validated sender.

## Стратегия тестирования

### Критерий безопасности parser

Parser работает в privacy-first режиме:

- false negative (чувствительное значение осталось открытым) считается
  блокирующей ошибкой;
- false positive (неопасное значение было замаскировано) допустим как
  консервативный fallback;
- исключение — известные performance metrics в структурных колонках отчётов:
  они должны сохраняться, иначе AI теряет диагностическую ценность;
- неизвестная Oracle-подобная секция маскируется целиком, а не пропускается.

Одного exact round-trip недостаточно: он доказывает обратимость найденных spans,
но не доказывает полноту обнаружения. Поэтому golden corpus содержит независимые
ожидания `must_mask` и `must_keep`; тесты проверяют отсутствие всех `must_mask`
после обфускации и сохранение всех `must_keep`.

### Parser corpus

- Oracle 19c SELECT/DML, CTE, old outer join `(+)`, hints, quoted identifiers,
  national/q strings, binds и database links;
- malformed and partial SQL;
- DBMS_XPLAN basic/typical/allstats/advanced sections;
- query blocks, outline, predicates, projection, peeked binds, remote SQL;
- SQL Monitor TEXT global info, plan, parallel details, binds and unknown sections;
- literal/metric collisions;
- comments and free-form notes;
- Unicode identifiers and values;
- exact round-trip and damaged markers.

### Live Oracle matrix

Помимо одного smoke-запроса используется матрица синтетических запросов на
Docker Oracle:

- index access, joins, aggregation, sort/window, CTE/set operations и PIVOT;
- partition pruning, hierarchical query, JSON expressions и bind peeking;
- INSERT/UPDATE/DELETE/MERGE и distributed plans через временный DB link;
- serial и parallel hints;
- `DBMS_XPLAN` BASIC, TYPICAL, ALL и ALLSTATS LAST с ALIAS, OUTLINE,
  PREDICATE, PROJECTION, PARTITION, PARALLEL, IOSTATS и MEMSTATS;
- SQL Monitor TEXT уровней BASIC, TYPICAL и ALL;
- malformed/truncated variants, SQL*Plus line wrapping и CRLF mutations.

Oracle XE не создаёт полноценные PX operators даже после временного увеличения
`parallel_max_servers`. Реальный PX layout поэтому покрыт отдельным synthetic
fixture по документированному формату `TQ / IN-OUT / PQ Distrib`; для полного
live-PX smoke потребуется Oracle Enterprise Edition. Это явно считается
ограничением среды, а не доказанным live-сценарием.

Матрица выявила и зафиксировала regression tests для четырёх реальных дефектов:

- SQL из заголовка `EXPLAINED SQL STATEMENT` не извлекался;
- Peeked Binds без имени bind оставлял значение открытым;
- SQL Monitor bind table `Name | Position | Type | Value` не разбиралась;
- смешанный prompt с несколькими report types мог оставить SQL ID открытым и
  скрыть performance metrics консервативным fragment fallback.

Результаты live matrix нормализуются в committed corpus без пользовательских
данных. Для каждого артефакта сохраняется golden manifest с обязательными
секретами, обязательными открытыми метриками, ожидаемым kind и минимальными
структурными признаками. Live generation остаётся отдельным Docker smoke, а CI
работает по зафиксированному corpus и не требует Oracle.

### Generative invariants

Property/mutation tests должны проверять:

- parser не падает и не зависает на произвольном тексте;
- каждый span находится в границах source и точно совпадает с `original`;
- spans не пересекаются;
- obfuscate → exact restore возвращает исходные bytes;
- whitespace, CRLF, ширина plan table и отсутствие необязательных секций не
  раскрывают `must_mask` значения;
- повреждённые markers диагностируются и не восстанавливаются нечётко;
- время обработки больших отчётов ограничено regression budget.

### Единая организация test corpus

Corpus содержит только синтетические или заранее обезличенные данные. Отдельный
режим для production-секретов не предусмотрен: такие данные в репозиторий и
локальный corpus не помещаются.

```text
test-corpus/
├── corpus.schema.json
├── canonical/
│   ├── expectations.json
│   ├── sql/
│   ├── xplan/
│   ├── sql-monitor/
│   ├── outline/
│   └── predicates/
├── generated/
│   └── oracle-21c/
│       ├── expectations.json
│       └── cases...
└── user/
    └── README.md
```

Один loader рекурсивно находит все `.sql`, `.txt`, `.log` и `.out`. Файл не
может быть молча проигнорирован и не требует регистрации для базовых проверок.
Hardcoded списков путей в test files нет.

Для каждого файла автоматически выполняются detection, проверка spans,
непересечение замен, exact round-trip и сохранение структурированных metrics.
Все токены, содержащие `PRIVATE` или `SECRET`, автоматически считаются
обязательными для замены. SQL ID, plan hash, query blocks и identity fields
проверяются по структуре формата.

Необязательный `expectations.json` задаёт только дополнительные ожидания для
файлов, которым недостаточно naming convention: ожидаемый kind, дополнительные
значения `replace`/`keep`, минимальное число occurrences, разрешённые findings и
tags. Формат един для SQL, XPLAN, SQL Monitor, outline и predicates; разделения
на `artifacts` и `sqlCases` нет.

Каждый expectations-файл проверяется по JSON Schema Draft 2020-12. Loader
отклоняет неизвестные поля, дублирующиеся ссылки, отсутствующие файлы, выход
пути за suite directory, невалидный UTF-8 и несоответствие ожидаемому kind.
Поля `caseId` и `sourceCharacters` удаляются как неиспользуемые.

Текущие небольшие 19c fixtures переносятся в `canonical`, Docker matrix — в
`generated/oracle-21c`, а файлы, добавляемые пользователем через разработчика,
помещаются в `user`. `npm test` всегда использует один и тот же loader для всех
трёх источников.

### Golden compatibility

Python reference outputs используются один раз для создания neutral expected
fixtures. TypeScript tests не запускают Python и не зависят от legacy directory.

### Extension tests

- service worker message contract and storage migrations;
- conversation provisional-to-stable migration;
- concurrent symbol allocation;
- ChatGPT and Claude adapter DOM fixtures, включая актуальный mobile composer;
- composer protection states and send guard modes;
- fallback launcher и восстановление UI при неизвестной/изменённой разметке;
- response iframe isolation protocol;
- restored/raw display and copy;
- rerender recovery;
- manifest permission audit;
- bundle contains no remote executable code.

### Integration and performance

- extension build validation;
- load-unpacked smoke in Chrome;
- real ChatGPT and Claude manual smoke;
- real Docker Oracle corpus parsed by TypeScript core;
- parser and obfuscation budget measured on large reports;
- reproducible ZIP verified from clean install.

## План реализации

1. Переместить прежний Python product в `legacy/python-app` и создать neutral
   test corpus.
2. Создать TypeScript workspace, MV3 manifest, esbuild и Vitest pipeline.
3. Реализовать core types, Oracle lexer и occurrence finalization.
4. Реализовать shallow SQL parser, hints и expressions.
5. Реализовать XPLAN и SQL Monitor section parsers.
6. Реализовать symbol registry, transform, exact restore и validation.
7. Реализовать conversation storage и migrations.
8. Реализовать service worker message protocol и settings.
9. Реализовать ChatGPT/Claude adapters и floating integration shell.
10. Реализовать secure composer iframe и send guard.
11. Реализовать response observer и restored iframe.
12. Реализовать popup settings и mapping cleanup.
13. Перенести golden tests, добавить DOM adapter tests и live Oracle smoke.
14. Выполнить performance/security audit.
15. Собрать unpacked directory и reproducible release ZIP.

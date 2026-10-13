# Oracle parser corpus

Corpus является контрактом TypeScript parser и содержит только синтетические
или заранее обезличенные данные.

Структура:

- `canonical/` — небольшие ручные Oracle 19c и synthetic regression fixtures;
- `generated/oracle-21c/` — snapshot матрицы, реально полученной из Docker
  Oracle XE 21.3;
- `user/` — дополнительные обезличенные файлы пользователя;
- `corpus.schema.json` — единая схема дополнительных ожиданий.

Все `.sql`, `.txt`, `.log` и `.out` обнаруживаются рекурсивно одним loader.
Добавление файла не требует изменения test code. Автоматически проверяются kind,
source spans, отсутствие пересечений, exact round-trip и сохранение metrics.
Токены, содержащие `PRIVATE` или `SECRET`, обязаны исчезнуть после обфускации.
Файл без таких токенов должен быть описан в `expectations.json`; иначе тест
завершается ошибкой, чтобы слабый fixture не создавал ложное ощущение покрытия.

Необязательный `expectations.json` в каталоге suite добавляет нестандартные
значения для замены и сохранения, минимальное число occurrences, разрешённые
findings и tags. Файл строго проверяется по `corpus.schema.json`; неизвестные
поля и ссылки на отсутствующие файлы считаются ошибкой.

Запуск только corpus tests:

```powershell
npm run test:corpus
```

`canonical/xplan/xplan_parallel_synthetic.txt` воспроизводит documented PX/TQ
layout, который Oracle XE не способен сгенерировать как настоящий parallel
execution.

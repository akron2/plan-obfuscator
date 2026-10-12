# Synthetic live Oracle matrix

Этот corpus сгенерирован на Docker Oracle XE 21.3 из полностью синтетической
схемы. Пользовательских и production-данных в нём нет.

Состав:

- `DBMS_XPLAN` BASIC, TYPICAL, ALL и ALLSTATS LAST;
- index access, hash join, aggregation, partition pruning, window functions,
  hierarchy, JSON, CTE/UNION, PIVOT, bind peeking и parallel hint;
- EXPLAIN PLAN для INSERT, UPDATE, DELETE, MERGE и distributed SQL через
  временный loopback database link;
- SQL Monitor TEXT уровней BASIC, TYPICAL и ALL;
- исходные SQL каждого сценария;
- `manifest.json` с независимыми `mustMask`, `mustKeep` и structural thresholds.

Динамические SQL ID, plan hash, session, module, service, program и host
нормализованы в стабильные синтетические значения. Отчёты можно пересоздать:

```powershell
npm run smoke:oracle-matrix -- --container sqlexplorer-oracle21 --write-corpus
```

Генератор создаёт случайную временную схему `POB_<8 hex>`, выполняет матрицу и
удаляет только эту схему и случайный public database link после получения
отчётов. Для parallel-сценария он временно увеличивает `parallel_max_servers`,
а затем безусловно возвращает исходное значение. Oracle XE всё равно не создаёт
полноценные PX operators, поэтому real-PX output требует Enterprise Edition и
не считается покрытым этой матрицей.

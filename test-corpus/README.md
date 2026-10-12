# Neutral Oracle parser corpus

Этот corpus является контрактом TypeScript core и не зависит от legacy Python
package. Он содержит Oracle 19c SQL и текстовые diagnostic reports.

Expected assertions находятся в extension tests и проверяют:

- entity categories и canonical values;
- неизменность performance metrics;
- stable mapping;
- exact round-trip;
- conservative fallback;
- damaged marker detection.

`oracle-live-21c/` дополнительно содержит committed snapshot матрицы, реально
сгенерированной на Docker Oracle XE 21.3: 23 XPLAN/SQL Monitor отчёта и 19
исходных SQL/DDL/DML сценариев с golden `mustMask`/`mustKeep` ожиданиями.

`xplan/xplan_parallel_synthetic.txt` воспроизводит documented PX/TQ layout,
который Oracle XE не способен сгенерировать как настоящий parallel execution.

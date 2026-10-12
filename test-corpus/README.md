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

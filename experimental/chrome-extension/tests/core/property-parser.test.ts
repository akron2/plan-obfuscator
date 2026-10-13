import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { parseArtifact, parsePrompt } from "../../src/core/parser";
import { createConversationMapping } from "../../src/core/symbol-registry";
import { obfuscatePrompt, restoreExact } from "../../src/core/transform";
import { ArtifactKind } from "../../src/core/types";
import { corpusCases } from "../helpers/corpus";

const identifierSuffix = fc.stringMatching(/^[A-Z][A-Z0-9_]{0,15}$/);
const valueSuffix = fc.string({ minLength: 1, maxLength: 30 })
  .filter((value) => !value.includes("]'") && /\S/u.test(value));

function assertSpanInvariants(source: string): void {
  const parsed = parsePrompt(source);
  let previousEnd = 0;
  for (const occurrence of parsed.occurrences) {
    expect(occurrence.start).toBeGreaterThanOrEqual(previousEnd);
    expect(occurrence.end).toBeGreaterThan(occurrence.start);
    expect(occurrence.end).toBeLessThanOrEqual(source.length);
    expect(source.slice(occurrence.start, occurrence.end)).toBe(occurrence.original);
    previousEnd = occurrence.end;
  }
}

describe("parser generative invariants", () => {
  it("masks generated Oracle identifiers and literals with exact restoration", () => {
    fc.assert(
      fc.property(
        identifierSuffix,
        identifierSuffix,
        identifierSuffix,
        identifierSuffix,
        identifierSuffix,
        valueSuffix,
        fc.integer({ min: 70_000_000, max: 99_999_999 }),
        (schemaPart, tablePart, columnPart, indexPart, bindPart, valuePart, number) => {
          const schema = `SCH_PRIVATE_${schemaPart}`;
          const table = `TBL_SECRET_${tablePart}`;
          const column = `COL_PRIVATE_${columnPart}`;
          const index = `IDX_SECRET_${indexPart}`;
          const bind = `BIND_PRIVATE_${bindPart}`;
          const value = `VALUE_PRIVATE_${valuePart}`;
          const sql = `select /*+ INDEX(t "${index}") */ t."${column}"\n` +
            `from "${schema}"."${table}" t\n` +
            `where t."${column}" = q'[${value}]'\n` +
            `  and t.NUMERIC_PRIVATE > ${number}\n` +
            `  and t.BIND_COLUMN_PRIVATE = :${bind}`;
          assertSpanInvariants(sql);
          const result = obfuscatePrompt(
            sql,
            createConversationMapping("property", "generated-sql", "PRP001")
          );
          for (const secret of [schema, table, column, index, bind, value, String(number)]) {
            expect(result.text, `generated value leaked: ${secret}`).not.toContain(secret);
          }
          expect(restoreExact(result)).toBe(sql);
        }
      ),
      { numRuns: 300, seed: 20261012 }
    );
  });

  it("never throws and always returns valid non-overlapping spans for hostile text", () => {
    const dangerousChunk = fc.oneof(
      fc.string({ maxLength: 80 }),
      fc.constantFrom(
        "'", "\"", "/*", "*/", "--", "q'[", "]'", "nq'{", "}'", "(+)",
        "SELECT", "WITH", "SQL_ID", "Plan hash value:", "Predicate Information",
        "| Id | Operation | Name | Rows |", "\r\n", "\n", "\u0000", "Конфиденциально"
      )
    );
    fc.assert(
      fc.property(
        fc.array(dangerousChunk, { minLength: 0, maxLength: 60 }).map((items) => items.join("")),
        (source) => {
          assertSpanInvariants(source);
          const result = obfuscatePrompt(
            source,
            createConversationMapping("property", "hostile", "PRP002")
          );
          expect(restoreExact(result)).toBe(source);
        }
      ),
      { numRuns: 600, seed: 20261013 }
    );
  });

  it("keeps golden secrets protected after line-ending and spacing mutations", () => {
    const cases = corpusCases.filter((item) =>
      item.suite === "generated-oracle-21c" &&
      [ArtifactKind.Xplan, ArtifactKind.SqlMonitor].includes(item.expectedKind as ArtifactKind)
    );
    fc.assert(
      fc.property(
        fc.constantFrom(...cases),
        fc.boolean(),
        fc.integer({ min: 0, max: 3 }),
        (item, useCrLf, blankLines) => {
          const newline = useCrLf ? "\r\n" : "\n";
          const source = item.source
            .replace(/\r?\n/g, newline)
            .replace(/(Plan hash value[^\r\n]*)(\r?\n)/i, `$1${newline.repeat(blankLines + 1)}`);
          const result = obfuscatePrompt(
            source,
            createConversationMapping("property", item.id, "PRP003")
          );
          for (const secret of item.replace.filter((value) => source.includes(value))) {
            expect(result.text, `mutated report leaked: ${secret}`).not.toContain(secret);
          }
          for (const safeValue of item.keep) {
            expect(result.text, `mutated report changed safe field: ${safeValue}`).toContain(safeValue);
          }
          expect(restoreExact(result)).toBe(source);
        }
      ),
      { numRuns: 250, seed: 20261014 }
    );
  });

  it("parses the complete committed report matrix within a regression budget", () => {
    const sources = corpusCases
      .filter((item) => item.suite === "generated-oracle-21c" && item.expectedKind !== ArtifactKind.Sql)
      .map((item) => item.source);
    const started = performance.now();
    for (let iteration = 0; iteration < 20; iteration += 1) {
      for (const source of sources) parseArtifact(source);
    }
    const elapsed = performance.now() - started;
    expect(elapsed).toBeLessThan(5_000);
  }, 10_000);
});

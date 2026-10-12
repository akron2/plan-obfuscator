import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { cellBounds, normalizeWhitespace, trimmedBounds } from "../../src/core/text-utils";
import { linesWithOffsets } from "../../src/core/text-utils";
import { parseArtifact } from "../../src/core/parser";
import { KNOWN_FUNCTIONS, tokenizeOracle } from "../../src/core/oracle-lexer";
import { createConversationMapping } from "../../src/core/symbol-registry";
import { obfuscatePrompt, restoreExact } from "../../src/core/transform";
import type { ArtifactKind, ParsedArtifact } from "../../src/core/types";

interface GoldenCase {
  id: string;
  file: string;
  kind: `${ArtifactKind}`;
  mustMask: string[];
  mustKeep?: string[];
  minOccurrences: number;
  sourceCharacters: number;
}

interface GoldenManifest {
  schemaVersion: number;
  artifacts: GoldenCase[];
  sqlCases: GoldenCase[];
}

const corpusDirectory = resolve(import.meta.dirname, "../../../test-corpus/oracle-live-21c");
const manifest = JSON.parse(
  readFileSync(resolve(corpusDirectory, "manifest.json"), "utf8")
) as GoldenManifest;

function sourceFor(item: GoldenCase): string {
  return readFileSync(resolve(corpusDirectory, item.file), "utf8").trimEnd();
}

function assertValidSpans(source: string, parsed: ParsedArtifact): void {
  let previousEnd = 0;
  for (const occurrence of parsed.occurrences) {
    expect(occurrence.start).toBeGreaterThanOrEqual(previousEnd);
    expect(occurrence.end).toBeGreaterThan(occurrence.start);
    expect(occurrence.end).toBeLessThanOrEqual(source.length);
    expect(source.slice(occurrence.start, occurrence.end)).toBe(occurrence.original);
    previousEnd = occurrence.end;
  }
}

const sensitiveHeader = /(?:name|value|bind|object|\binst\b|instance|session|user|host|machine|process|program|module|action|service|server|sql id|dbop|client)/i;

function assertMetricCellsAreUnmasked(source: string, parsed: ParsedArtifact): number {
  const lines = linesWithOffsets(source);
  let checked = 0;
  for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
    const headerLine = lines[lineIndex]!;
    if (!headerLine.text.trimStart().startsWith("|")) continue;
    const headerBounds = cellBounds(headerLine.text);
    const headers = headerBounds.map(([start, end]) =>
      normalizeWhitespace(headerLine.text.slice(start, end)).toLowerCase()
    );
    if (headers.length < 2 || !headers.some((header) =>
      /^(?:id|operation|elapsed|time|rows|starts|cost|bytes|buffer|read|write|fetch|activity|execs)/i.test(header)
    )) continue;

    const safeColumns = headers
      .map((header, index) => ({ header, index }))
      .filter(({ header }) => header && !sensitiveHeader.test(header));
    for (const row of lines.slice(lineIndex + 1)) {
      if (/^\s*[-=]+\s*$/.test(row.text)) continue;
      if (!row.text.trimStart().startsWith("|")) break;
      const rowBounds = cellBounds(row.text);
      for (const { index } of safeColumns) {
        const bounds = rowBounds[index];
        if (!bounds) continue;
        const valueBounds = trimmedBounds(row.text, bounds[0], bounds[1]);
        if (!valueBounds) continue;
        const absoluteStart = row.offset + valueBounds[0];
        const absoluteEnd = row.offset + valueBounds[1];
        const value = source.slice(absoluteStart, absoluteEnd);
        if (!/[\dA-Z]/i.test(value)) continue;
        const overlap = parsed.occurrences.some(
          (item) => absoluteStart < item.end && item.start < absoluteEnd
        );
        expect(overlap, `safe metric cell was masked: ${value}`).toBe(false);
        checked += 1;
      }
    }
  }
  return checked;
}

describe("committed live Oracle matrix corpus", () => {
  expect(manifest.schemaVersion).toBe(1);

  it.each([...manifest.artifacts, ...manifest.sqlCases])(
    "$id masks every golden secret and round-trips exactly",
    (item) => {
      const source = sourceFor(item);
      const parsed = parseArtifact(source);
      expect(parsed.findings.filter((finding) => finding.severity === "error")).toEqual([]);
      expect(parsed.kind).toBe(item.kind);
      expect(parsed.occurrences.length).toBeGreaterThanOrEqual(item.minOccurrences);
      assertValidSpans(source, parsed);
      for (const secret of item.mustMask) {
        expect(source, `golden value missing from source: ${secret}`).toContain(secret);
      }

      const result = obfuscatePrompt(
        source,
        createConversationMapping("golden", item.id, "GOLD01")
      );
      for (const secret of item.mustMask) {
        expect(result.text, `sensitive value leaked: ${secret}`).not.toContain(secret);
      }
      for (const safeValue of item.mustKeep ?? []) {
        expect(result.text, `safe field changed: ${safeValue}`).toContain(safeValue);
      }
      for (const queryBlock of source.matchAll(/\b(?:SEL|SET|INS|UPD|DEL)\$[A-Z0-9_]+\b/gi)) {
        expect(result.text, `query block leaked: ${queryBlock[0]}`).not.toContain(queryBlock[0]);
      }
      expect(restoreExact(result)).toBe(source);
    }
  );

  it("does not mask structured metric cells across the report matrix", () => {
    let checkedCells = 0;
    for (const item of manifest.artifacts) {
      const source = sourceFor(item);
      checkedCells += assertMetricCellsAreUnmasked(source, parseArtifact(source));
    }
    expect(checkedCells).toBeGreaterThan(250);
  });

  it("fails closed when reports are truncated during copy and paste", () => {
    let variants = 0;
    for (const item of manifest.artifacts) {
      const fullSource = sourceFor(item);
      for (const ratio of [0.25, 0.5, 0.75]) {
        const source = fullSource.slice(0, Math.max(1, Math.floor(fullSource.length * ratio)));
        const presentSecrets = item.mustMask.filter((secret) => source.includes(secret));
        if (presentSecrets.length === 0) continue;
        const result = obfuscatePrompt(
          source,
          createConversationMapping("golden-truncated", `${item.id}-${ratio}`, "CUT001")
        );
        for (const secret of presentSecrets) {
          expect(result.text, `truncated report leaked: ${secret}`).not.toContain(secret);
        }
        expect(restoreExact(result)).toBe(source);
        variants += 1;
      }
    }
    expect(variants).toBeGreaterThan(40);
  });

  it("covers every literal and bind token in the direct SQL matrix", () => {
    let checkedTokens = 0;
    for (const item of manifest.sqlCases) {
      const source = sourceFor(item);
      const parsed = parseArtifact(source);
      for (const token of tokenizeOracle(source)) {
        const tokenOverlap = parsed.occurrences.some(
          (occurrence) => token.start < occurrence.end && occurrence.start < token.end
        );
        if (token.kind === "keyword") {
          expect(tokenOverlap, `Oracle keyword was masked: ${token.raw}`).toBe(false);
          continue;
        }
        if (token.kind === "identifier" && KNOWN_FUNCTIONS.has(token.upper)) {
          expect(tokenOverlap, `known Oracle function was masked: ${token.raw}`).toBe(false);
        }
        if (![
          "string",
          "national_string",
          "q_string",
          "number",
          "bind"
        ].includes(token.kind)) continue;
        const start = token.kind === "number" ? token.start : token.contentStart;
        const end = token.kind === "number" ? token.end : token.contentEnd;
        const covered = parsed.occurrences.some(
          (occurrence) => occurrence.start <= start && occurrence.end >= end
        );
        expect(covered, `uncovered ${token.kind} token: ${token.raw}`).toBe(true);
        checkedTokens += 1;
      }
    }
    expect(checkedTokens).toBeGreaterThan(30);
  });

  it("protects every golden value when two XPLAN reports are pasted together", () => {
    const first = manifest.artifacts.find((item) => item.id === "xplan-q1-index-basic")!;
    const second = manifest.artifacts.find((item) => item.id === "xplan-q2-join-all")!;
    const source = `${sourceFor(first)}\n\nПлан второго child cursor:\n\n${sourceFor(second)}`;
    const result = obfuscatePrompt(
      source,
      createConversationMapping("golden-multi", "two-xplans", "MUL001")
    );
    for (const secret of [...first.mustMask, ...second.mustMask]) {
      expect(result.text, `multi-report prompt leaked: ${secret}`).not.toContain(secret);
    }
    expect(restoreExact(result)).toBe(source);
  });

  it("protects a fenced SQL query pasted together with an XPLAN report", () => {
    const report = manifest.artifacts.find((item) => item.id === "xplan-q1-index-basic")!;
    const source = `Проверь отдельно запрос и его план:\n\n` +
      "```sql\nselect FENCED_COLUMN_PRIVATE from FENCED_TABLE_SECRET " +
      "where FENCED_COLUMN_PRIVATE = 'FENCED_VALUE_PRIVATE'\n```\n\n" +
      sourceFor(report);
    const result = obfuscatePrompt(
      source,
      createConversationMapping("golden-mixed", "sql-and-xplan", "MIX001")
    );
    for (const secret of [
      "FENCED_COLUMN_PRIVATE",
      "FENCED_TABLE_SECRET",
      "FENCED_VALUE_PRIVATE",
      ...report.mustMask
    ]) {
      expect(result.text, `mixed prompt leaked: ${secret}`).not.toContain(secret);
    }
    expect(result.text).toContain("Проверь отдельно запрос и его план");
    expect(restoreExact(result)).toBe(source);
  });

  it("protects mixed SQL Monitor and XPLAN reports without hiding their metrics", () => {
    const monitor = manifest.artifacts.find((item) => item.id === "monitor-q8-bind-all")!;
    const xplan = manifest.artifacts.find((item) => item.id === "xplan-q3-partition-window")!;
    const source = `${sourceFor(monitor)}\n\nДополнительный execution plan:\n\n${sourceFor(xplan)}`;
    const result = obfuscatePrompt(
      source,
      createConversationMapping("golden-mixed", "monitor-and-xplan", "MIX002")
    );
    for (const secret of [...monitor.mustMask, ...xplan.mustMask]) {
      expect(result.text, `mixed reports leaked: ${secret}`).not.toContain(secret);
    }
    for (const safeValue of monitor.mustKeep ?? []) {
      expect(result.text, `monitor metric was hidden: ${safeValue}`).toContain(safeValue);
    }
    expect(result.text).toContain("991K (0)");
    expect(restoreExact(result)).toBe(source);
  });

  it("protects two SQL Monitor histories pasted into one prompt", () => {
    const first = manifest.artifacts.find((item) => item.id === "monitor-q2-join-all")!;
    const second = manifest.artifacts.find((item) => item.id === "monitor-q8-bind-all")!;
    const source = `${sourceFor(first)}\n\nСледующее выполнение:\n\n${sourceFor(second)}`;
    const result = obfuscatePrompt(
      source,
      createConversationMapping("golden-multi", "two-monitors", "MUL002")
    );
    for (const secret of [...first.mustMask, ...second.mustMask]) {
      expect(result.text, `multi-monitor prompt leaked: ${secret}`).not.toContain(secret);
    }
    for (const safeValue of [...(first.mustKeep ?? []), ...(second.mustKeep ?? [])]) {
      expect(result.text, `multi-monitor metric was hidden: ${safeValue}`).toContain(safeValue);
    }
    expect(restoreExact(result)).toBe(source);
  });
});

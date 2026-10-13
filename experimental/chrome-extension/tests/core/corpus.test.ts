import { describe, expect, it } from "vitest";
import { KNOWN_FUNCTIONS, tokenizeOracle } from "../../src/core/oracle-lexer";
import { parseArtifact } from "../../src/core/parser";
import { createConversationMapping } from "../../src/core/symbol-registry";
import { cellBounds, linesWithOffsets, normalizeWhitespace, trimmedBounds } from "../../src/core/text-utils";
import { obfuscatePrompt, restoreExact } from "../../src/core/transform";
import { ArtifactKind, type ParsedArtifact } from "../../src/core/types";
import { corpusCase, corpusCases, type CorpusCase } from "../helpers/corpus";

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
    const headers = cellBounds(headerLine.text).map(([start, end]) =>
      normalizeWhitespace(headerLine.text.slice(start, end)).toLowerCase()
    );
    if (
      headers.length < 2 ||
      !headers.some((header) =>
        /^(?:id|operation|elapsed|time|rows|starts|cost|bytes|buffer|read|write|fetch|activity|execs)/i.test(header)
      )
    ) continue;

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

function identityValues(source: string): string[] {
  const values = [
    ...source.matchAll(/\bSQL(?:_| )ID\s*[:=]?\s+([a-z0-9]{8,})/gi),
    ...source.matchAll(/\b(?:SQL )?Plan Hash Value\s*[:=]\s*(\d{6,})/gi)
  ].map((match) => match[1] ?? "");
  return [...new Set(values.filter(Boolean))];
}

function reportCase(pathSuffix: string): CorpusCase {
  return corpusCase(`generated/oracle-21c/${pathSuffix}`);
}

describe("unified Oracle corpus", () => {
  it("discovers every corpus file through one loader", () => {
    expect(corpusCases.length).toBeGreaterThan(40);
    expect(new Set(corpusCases.map((item) => item.id)).size).toBe(corpusCases.length);
  });

  it.each(corpusCases)("$id satisfies its parser contract", (item) => {
    const parsed = parseArtifact(item.source);
    const unexpectedFindings = parsed.findings.filter(
      (finding) =>
        finding.severity === "error" ||
        (finding.severity === "warning" && !item.allowFindings.includes(finding.code))
    );
    expect(unexpectedFindings).toEqual([]);
    if (item.expectedKind) expect(parsed.kind).toBe(item.expectedKind);
    else expect(parsed.kind).not.toBe(ArtifactKind.Unknown);
    expect(parsed.occurrences.length).toBeGreaterThanOrEqual(item.minOccurrences);
    assertValidSpans(item.source, parsed);
    for (const value of item.replace) {
      expect(item.source, `expected replacement is absent from source: ${value}`).toContain(value);
    }

    const result = obfuscatePrompt(
      item.source,
      createConversationMapping("corpus", item.id, "CRP001")
    );
    for (const value of [...item.replace, ...identityValues(item.source)]) {
      expect(result.text, `sensitive value leaked: ${value}`).not.toContain(value);
    }
    for (const value of item.keep) {
      expect(result.text, `preserved value changed: ${value}`).toContain(value);
    }
    for (const queryBlock of item.source.matchAll(/\b(?:SEL|SET|INS|UPD|DEL)\$[A-Z0-9_]+\b/gi)) {
      expect(result.text, `query block leaked: ${queryBlock[0]}`).not.toContain(queryBlock[0]);
    }
    expect(restoreExact(result)).toBe(item.source);
  });

  it("does not mask structured metric cells", () => {
    let checkedCells = 0;
    for (const item of corpusCases) {
      const parsed = parseArtifact(item.source);
      if (![ArtifactKind.Xplan, ArtifactKind.SqlMonitor].includes(parsed.kind)) continue;
      checkedCells += assertMetricCellsAreUnmasked(item.source, parsed);
    }
    expect(checkedCells).toBeGreaterThan(250);
  });

  it("fails closed when reports are truncated during copy and paste", () => {
    let variants = 0;
    for (const item of corpusCases) {
      const parsed = parseArtifact(item.source);
      if (![ArtifactKind.Xplan, ArtifactKind.SqlMonitor].includes(parsed.kind)) continue;
      for (const ratio of [0.25, 0.5, 0.75]) {
        const source = item.source.slice(0, Math.max(1, Math.floor(item.source.length * ratio)));
        const presentValues = item.replace.filter((value) => source.includes(value));
        if (presentValues.length === 0) continue;
        const result = obfuscatePrompt(
          source,
          createConversationMapping("corpus-truncated", `${item.id}-${ratio}`, "CUT001")
        );
        for (const value of presentValues) {
          expect(result.text, `truncated report leaked: ${value}`).not.toContain(value);
        }
        expect(restoreExact(result)).toBe(source);
        variants += 1;
      }
    }
    expect(variants).toBeGreaterThan(40);
  });

  it("covers every literal and bind token in SQL corpus files", () => {
    let checkedTokens = 0;
    for (const item of corpusCases) {
      const parsed = parseArtifact(item.source);
      if (parsed.kind !== ArtifactKind.Sql) continue;
      for (const token of tokenizeOracle(item.source)) {
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
        if (!["string", "national_string", "q_string", "number", "bind"].includes(token.kind)) continue;
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

  it("protects two XPLAN reports pasted together", () => {
    const first = reportCase("xplan-q1-index-basic.txt");
    const second = reportCase("xplan-q2-join-all.txt");
    const source = `${first.source}\nПлан второго child cursor:\n${second.source}`;
    const result = obfuscatePrompt(source, createConversationMapping("corpus-multi", "two-xplans", "MUL001"));
    for (const value of [...first.replace, ...second.replace]) {
      expect(result.text, `multi-report prompt leaked: ${value}`).not.toContain(value);
    }
    expect(restoreExact(result)).toBe(source);
  });

  it("protects fenced SQL pasted together with XPLAN", () => {
    const report = reportCase("xplan-q1-index-basic.txt");
    const source = `Проверь отдельно запрос и его план:\n\n` +
      "```sql\nselect FENCED_COLUMN_PRIVATE from FENCED_TABLE_SECRET " +
      "where FENCED_COLUMN_PRIVATE = 'FENCED_VALUE_PRIVATE'\n```\n\n" + report.source;
    const result = obfuscatePrompt(source, createConversationMapping("corpus-mixed", "sql-and-xplan", "MIX001"));
    for (const value of ["FENCED_COLUMN_PRIVATE", "FENCED_TABLE_SECRET", "FENCED_VALUE_PRIVATE", ...report.replace]) {
      expect(result.text, `mixed prompt leaked: ${value}`).not.toContain(value);
    }
    expect(result.text).toContain("Проверь отдельно запрос и его план");
    expect(restoreExact(result)).toBe(source);
  });

  it("protects mixed SQL Monitor and XPLAN without hiding metrics", () => {
    const monitor = reportCase("monitor-q8-bind-all.txt");
    const xplan = reportCase("xplan-q3-partition-window.txt");
    const source = `${monitor.source}\nДополнительный execution plan:\n${xplan.source}`;
    const result = obfuscatePrompt(source, createConversationMapping("corpus-mixed", "monitor-and-xplan", "MIX002"));
    for (const value of [...monitor.replace, ...xplan.replace]) {
      expect(result.text, `mixed reports leaked: ${value}`).not.toContain(value);
    }
    for (const value of monitor.keep) expect(result.text).toContain(value);
    expect(result.text).toContain("991K (0)");
    expect(restoreExact(result)).toBe(source);
  });

  it("protects two SQL Monitor histories pasted together", () => {
    const first = reportCase("monitor-q2-join-all.txt");
    const second = reportCase("monitor-q8-bind-all.txt");
    const source = `${first.source}\nСледующее выполнение:\n${second.source}`;
    const result = obfuscatePrompt(source, createConversationMapping("corpus-multi", "two-monitors", "MUL002"));
    for (const value of [...first.replace, ...second.replace]) {
      expect(result.text, `multi-monitor prompt leaked: ${value}`).not.toContain(value);
    }
    for (const value of [...first.keep, ...second.keep]) expect(result.text).toContain(value);
    expect(restoreExact(result)).toBe(source);
  });
});

import {
  addOccurrence,
  emptyParsedArtifact,
  finalizeArtifact,
  makeOccurrence,
  shiftOccurrence
} from "./occurrences";
import { detectArtifactKind } from "./detector";
import { tokenizeOracle } from "./oracle-lexer";
import { parseSqlMonitor } from "./sql-monitor-parser";
import { parseExpressionFragment, parseHintFragment, parseSql } from "./sql-parser";
import { trimmedBounds } from "./text-utils";
import { ArtifactKind, EntityType, type ParsedArtifact } from "./types";
import { parseXplan } from "./xplan-parser";

function parseOutline(source: string): ParsedArtifact {
  const artifact = emptyParsedArtifact(ArtifactKind.Outline, source);
  artifact.occurrences.push(...parseHintFragment(source));
  return finalizeArtifact(artifact);
}

function parsePredicates(source: string): ParsedArtifact {
  const artifact = emptyParsedArtifact(ArtifactKind.Predicates, source);
  const matches = [...source.matchAll(/^\s*\d+\s*-\s*(?:access|filter)\(/gim)];
  if (matches.length === 0) {
    artifact.occurrences.push(...parseExpressionFragment(source));
  } else {
    for (let index = 0; index < matches.length; index += 1) {
      const match = matches[index]!;
      const expressionStart = (match.index ?? 0) + match[0].length - 1;
      const expressionEnd = matches[index + 1]?.index ?? source.length;
      artifact.occurrences.push(
        ...parseExpressionFragment(source.slice(expressionStart, expressionEnd), {
          baseOffset: expressionStart,
          parentSource: source,
          displayContext: true
        })
      );
    }
  }
  return finalizeArtifact(artifact);
}

function parseUnknown(source: string): ParsedArtifact {
  const artifact = emptyParsedArtifact(ArtifactKind.Unknown, source);
  const bounds = trimmedBounds(source, 0, source.length);
  if (bounds) {
    addOccurrence(
      artifact,
      makeOccurrence(
        source,
        bounds[0],
        bounds[1],
        EntityType.Fragment,
        "unknown_artifact_fallback",
        { confidence: 0.5 }
      )
    );
  }
  artifact.findings.push({
    severity: "warning",
    code: "unknown_artifact_replaced",
    message: "Unknown artifact format was conservatively replaced as one fragment."
  });
  return finalizeArtifact(artifact);
}

export function parseArtifact(
  source: string,
  requestedKind?: ArtifactKind | `${ArtifactKind}`
): ParsedArtifact {
  const kind = requestedKind ? (requestedKind as ArtifactKind) : detectArtifactKind(source);
  switch (kind) {
    case ArtifactKind.Sql:
      return parseSql(source);
    case ArtifactKind.Xplan:
      return parseXplan(source);
    case ArtifactKind.SqlMonitor:
      return parseSqlMonitor(source);
    case ArtifactKind.Outline:
      return parseOutline(source);
    case ArtifactKind.Predicates:
      return parsePredicates(source);
    default:
      return parseUnknown(source);
  }
}

function appendRegion(
  target: ParsedArtifact,
  parsed: ParsedArtifact,
  offset: number
): void {
  for (const occurrence of parsed.occurrences) {
    addOccurrence(target, shiftOccurrence(occurrence, offset, target.sourceText));
  }
  target.findings.push(
    ...parsed.findings.map((finding) => ({
      ...finding,
      ...(finding.start === undefined ? {} : { start: finding.start + offset }),
      ...(finding.end === undefined ? {} : { end: finding.end + offset })
    }))
  );
  target.extractedSql.push(...parsed.extractedSql);
}

function sqlRegionEnd(source: string): number {
  if (/^\s*(?:BEGIN|DECLARE)\b/i.test(source)) return source.length;
  let depth = 0;
  for (const token of tokenizeOracle(source)) {
    if (token.raw === "(") depth += 1;
    else if (token.raw === ")") depth = Math.max(0, depth - 1);
    else if (token.raw === ";" && depth === 0) return token.end;
  }
  return source.length;
}

interface ReportRegion {
  kind: ArtifactKind.Xplan | ArtifactKind.SqlMonitor;
  start: number;
  end: number;
}

function reportRegions(source: string): ReportRegion[] {
  const pattern = /^(?:[ \t]*SQL Monitoring Report[ \t]*|[ \t]*SQL_ID\s+[a-z0-9]+\s*,\s*child number\s+\d+[ \t]*|[ \t]*(?:EXPLAINED SQL STATEMENT|SQL STATEMENT)\s*:[ \t]*|[ \t]*Plan hash value\s*:\s*\d+[ \t]*)$/gim;
  const candidates = [...source.matchAll(pattern)].map((match) => {
    const label = match[0].trim().toUpperCase();
    const marker = label.startsWith("SQL MONITORING REPORT")
      ? "monitor"
      : label.startsWith("PLAN HASH VALUE")
        ? "plan_hash"
        : "xplan_header";
    return { start: match.index ?? 0, marker } as const;
  });
  const accepted: Array<{ start: number; kind: ReportRegion["kind"]; ownsPlanHash: boolean }> = [];
  for (const candidate of candidates) {
    const previous = accepted.at(-1);
    if (
      candidate.marker === "plan_hash" &&
      previous?.kind === ArtifactKind.Xplan &&
      !previous.ownsPlanHash
    ) {
      previous.ownsPlanHash = true;
      continue;
    }
    accepted.push({
      start: candidate.start,
      kind: candidate.marker === "monitor" ? ArtifactKind.SqlMonitor : ArtifactKind.Xplan,
      ownsPlanHash: candidate.marker === "plan_hash"
    });
  }
  return accepted.map((item, index) => ({
    kind: item.kind,
    start: item.start,
    end: accepted[index + 1]?.start ?? source.length
  }));
}

/**
 * Parses a complete chat prompt without treating ordinary prose as secret data.
 * Known reports may be embedded in prose; SQL snippets should preferably be in
 * Markdown fences. A bare SQL statement beginning on its own line is also
 * supported and is assumed to continue to the end of the prompt.
 */
export function parsePrompt(source: string): ParsedArtifact {
  const detected = detectArtifactKind(source);
  const aggregate = emptyParsedArtifact(detected, source);
  const covered: Array<[number, number]> = [];
  const reports = reportRegions(source);
  if (reports.length > 0) {
    for (const report of reports) {
      appendRegion(
        aggregate,
        parseArtifact(source.slice(report.start, report.end), report.kind),
        report.start
      );
      covered.push([report.start, report.end]);
    }
  } else if (detected !== ArtifactKind.Unknown) {
    appendRegion(aggregate, parseArtifact(source, detected), 0);
  }
  const fencePattern = /```(oracle|sql|plsql|text)?[ \t]*\r?\n([\s\S]*?)```/gi;
  for (const match of source.matchAll(fencePattern)) {
    const language = (match[1] ?? "").toLowerCase();
    const body = match[2] ?? "";
    const bodyStart = (match.index ?? 0) + match[0].indexOf(body);
    const kind = detectArtifactKind(body);
    if (kind === ArtifactKind.Unknown && !language) continue;
    const parsed = kind === ArtifactKind.Unknown && language !== "text"
      ? parseSql(body)
      : parseArtifact(body, kind);
    appendRegion(aggregate, parsed, bodyStart);
    covered.push([bodyStart, bodyStart + body.length]);
  }

  if (detected === ArtifactKind.Unknown && covered.length === 0) {
    const sqlStart = source.search(
      /(?:^|[\s:])(?:SELECT|WITH|INSERT|UPDATE|DELETE|MERGE|EXPLAIN|CREATE|ALTER|DROP|TRUNCATE|BEGIN|DECLARE)\b/im
    );
    if (sqlStart >= 0) {
      const candidate = source.slice(sqlStart);
      const keyword = candidate.search(/(?:SELECT|WITH|INSERT|UPDATE|DELETE|MERGE|EXPLAIN|CREATE|ALTER|DROP|TRUNCATE|BEGIN|DECLARE)\b/i);
      const absolute = sqlStart + Math.max(0, keyword);
      const region = source.slice(absolute);
      const end = sqlRegionEnd(region);
      appendRegion(aggregate, parseSql(region.slice(0, end)), absolute);
      covered.push([absolute, absolute + end]);
    }
  }

  if (detected === ArtifactKind.Unknown && covered.length === 0) {
    if (/\b(?:DBMS_XPLAN|SQL_ID|PLAN HASH VALUE|PREDICATE INFORMATION|SQL MONITOR(?:ING)? REPORT)\b/i.test(source)) {
      const fallback = parseUnknown(source);
      appendRegion(aggregate, fallback, 0);
      aggregate.findings.push({
        severity: "warning",
        code: "oracle_artifact_conservative_fallback",
        message: "Oracle-like content was not structurally recognized and was replaced as one fragment."
      });
    } else {
      aggregate.findings.push({
        severity: "info",
        code: "no_oracle_artifact_detected",
        message: "No supported Oracle artifact was detected; the prompt was left unchanged."
      });
    }
  }
  aggregate.metadata = { embeddedArtifacts: covered.length };
  return finalizeArtifact(aggregate);
}

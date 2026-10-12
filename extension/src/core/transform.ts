import { finalizeOccurrences } from "./occurrences";
import { parsePrompt } from "./parser";
import {
  getOrCreateSymbol,
  mappingMarkerIndex,
  MARKER_PATTERN,
  markerHasValidChecksum
} from "./symbol-registry";
import type {
  AppliedReplacement,
  ConversationMapping,
  ObfuscationResult,
  RestoreResult,
  TextOccurrence
} from "./types";

function applyOccurrences(
  source: string,
  occurrences: readonly TextOccurrence[],
  mapping: ConversationMapping
): { text: string; replacements: AppliedReplacement[] } {
  const replacements: AppliedReplacement[] = [];
  let text = "";
  let cursor = 0;
  for (const occurrence of finalizeOccurrences(occurrences)) {
    if (source.slice(occurrence.start, occurrence.end) !== occurrence.original) {
      throw new Error(`Stale source span at ${occurrence.start}:${occurrence.end}`);
    }
    if (occurrence.start < cursor) throw new Error("Parser produced overlapping occurrences");
    const symbol = getOrCreateSymbol(mapping, occurrence);
    text += source.slice(cursor, occurrence.start);
    const outputStart = text.length;
    text += symbol.marker;
    replacements.push({
      occurrence,
      symbol,
      outputStart,
      outputEnd: text.length
    });
    cursor = occurrence.end;
  }
  text += source.slice(cursor);
  return { text, replacements };
}

export function obfuscatePrompt(
  source: string,
  mapping: ConversationMapping
): ObfuscationResult {
  const parsed = parsePrompt(source);
  const applied = applyOccurrences(source, parsed.occurrences, mapping);
  const hasError = parsed.findings.some((item) => item.severity === "error");
  const hasWarning = parsed.findings.some((item) => item.severity === "warning");
  return {
    text: applied.text,
    artifact: {
      kind: parsed.kind,
      sourceText: source,
      obfuscatedText: applied.text,
      replacements: applied.replacements,
      findings: parsed.findings
    },
    mapping,
    replacementCount: applied.replacements.length,
    validationStatus: hasError ? "error" : hasWarning ? "warning" : "ok"
  };
}

export function restoreText(source: string, mapping: ConversationMapping): RestoreResult {
  const symbols = mappingMarkerIndex(mapping);
  const unknown = new Set<string>();
  let replacementCount = 0;
  MARKER_PATTERN.lastIndex = 0;
  const text = source.replace(MARKER_PATTERN, (marker) => {
    const normalized = marker.toUpperCase();
    const symbol = symbols.get(normalized);
    if (!symbol || !markerHasValidChecksum(normalized)) {
      unknown.add(marker);
      return marker;
    }
    replacementCount += 1;
    return symbol.preferredOriginal;
  });
  MARKER_PATTERN.lastIndex = 0;
  for (const suspicious of text.matchAll(/\bOBF_[A-Z0-9_]{4,80}\b/gi)) {
    unknown.add(suspicious[0]);
  }
  return { text, unknownMarkers: [...unknown], replacementCount };
}

/** Restores a directly obfuscated prompt byte-for-byte using its source manifest. */
export function restoreExact(result: ObfuscationResult): string {
  const source = result.text;
  let output = "";
  let cursor = 0;
  for (const replacement of result.artifact.replacements) {
    if (source.slice(replacement.outputStart, replacement.outputEnd) !== replacement.symbol.marker) {
      throw new Error("Obfuscated text no longer matches its exact restoration manifest");
    }
    output += source.slice(cursor, replacement.outputStart);
    output += replacement.occurrence.original;
    cursor = replacement.outputEnd;
  }
  output += source.slice(cursor);
  return output;
}

export function markerCount(source: string): number {
  MARKER_PATTERN.lastIndex = 0;
  const count = [...source.matchAll(MARKER_PATTERN)].length;
  MARKER_PATTERN.lastIndex = 0;
  return count;
}

import {
  ENTITY_CODES,
  type ConversationMapping,
  type SymbolRecord,
  type TextOccurrence
} from "./types";

export const MAPPING_SCHEMA_VERSION = 1;
export const MARKER_PATTERN = /\bOBF_[A-Z0-9]{4,12}_[A-Z0-9]{2,8}_[0-9A-Z]{4,8}_[0-9A-F]{2}\b/gi;

function checksum(value: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).toUpperCase().padStart(8, "0").slice(-2);
}

function randomPrefix(): string {
  const bytes = new Uint8Array(6);
  globalThis.crypto.getRandomValues(bytes);
  return [...bytes]
    .map((value) => value.toString(36).toUpperCase().padStart(2, "0"))
    .join("")
    .slice(0, 8);
}

function sequenceText(sequence: number): string {
  return sequence.toString(36).toUpperCase().padStart(4, "0");
}

function symbolKey(record: Pick<SymbolRecord, "entityType" | "canonical">): string {
  return `${record.entityType}\u0000${record.canonical}`;
}

export function createConversationMapping(
  site: string,
  conversationId: string,
  prefix = randomPrefix()
): ConversationMapping {
  return {
    schemaVersion: MAPPING_SCHEMA_VERSION,
    site,
    conversationId,
    tokenPrefix: prefix.toUpperCase(),
    nextSequence: 1,
    symbols: [],
    updatedAt: new Date().toISOString()
  };
}

export function validateMapping(mapping: ConversationMapping): void {
  if (mapping.schemaVersion !== MAPPING_SCHEMA_VERSION) {
    throw new Error(`Unsupported mapping schema: ${mapping.schemaVersion}`);
  }
  if (!/^[A-Z0-9]{4,12}$/.test(mapping.tokenPrefix)) {
    throw new Error("Invalid mapping token prefix");
  }
  const markers = new Set<string>();
  for (const symbol of mapping.symbols) {
    const marker = symbol.marker.toUpperCase();
    if (!MARKER_PATTERN.test(marker)) throw new Error(`Invalid marker: ${symbol.marker}`);
    MARKER_PATTERN.lastIndex = 0;
    if (markers.has(marker)) throw new Error(`Duplicate marker: ${marker}`);
    markers.add(marker);
  }
}

export function getOrCreateSymbol(
  mapping: ConversationMapping,
  occurrence: TextOccurrence
): SymbolRecord {
  const key = `${occurrence.entityType}\u0000${occurrence.canonical}`;
  const existing = mapping.symbols.find((item) => symbolKey(item) === key);
  if (existing) return existing;

  const sequence = mapping.nextSequence;
  const code = ENTITY_CODES[occurrence.entityType];
  const core = `OBF_${mapping.tokenPrefix}_${code}_${sequenceText(sequence)}`;
  const symbol: SymbolRecord = {
    entityType: occurrence.entityType,
    canonical: occurrence.canonical,
    preferredOriginal: occurrence.original,
    marker: `${core}_${checksum(core)}`,
    sequence
  };
  mapping.symbols.push(symbol);
  mapping.nextSequence += 1;
  mapping.updatedAt = new Date().toISOString();
  return symbol;
}

export function mappingMarkerIndex(mapping: ConversationMapping): Map<string, SymbolRecord> {
  return new Map(mapping.symbols.map((symbol) => [symbol.marker.toUpperCase(), symbol]));
}

export function markerHasValidChecksum(marker: string): boolean {
  const normalized = marker.toUpperCase();
  const separator = normalized.lastIndexOf("_");
  if (separator < 0) return false;
  return checksum(normalized.slice(0, separator)) === normalized.slice(separator + 1);
}

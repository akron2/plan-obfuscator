import {
  ENTITY_PRIORITY,
  EntityType,
  type ParsedArtifact,
  type TextOccurrence
} from "./types";

const IDENTIFIER_TYPES = new Set<EntityType>([
  EntityType.Schema,
  EntityType.Table,
  EntityType.View,
  EntityType.Column,
  EntityType.Index,
  EntityType.Constraint,
  EntityType.Partition,
  EntityType.Alias,
  EntityType.QueryBlock,
  EntityType.DbLink,
  EntityType.User,
  EntityType.Database,
  EntityType.Service,
  EntityType.Host,
  EntityType.Program,
  EntityType.Module,
  EntityType.Action,
  EntityType.SqlId,
  EntityType.PlanHash,
  EntityType.ExecutionId,
  EntityType.SessionId,
  EntityType.ObjectId,
  EntityType.Bind,
  EntityType.Function,
  EntityType.Identifier
]);

export function canonicalIdentifier(value: string, quoted = false): string {
  const unescaped = quoted ? value.replaceAll('""', '"') : value;
  return quoted ? `Q:${unescaped}` : `U:${unescaped.toUpperCase()}`;
}

export function canonicalValue(
  entityType: EntityType,
  value: string,
  quoted = false
): string {
  if (
    entityType === EntityType.StringLiteral ||
    entityType === EntityType.Comment ||
    entityType === EntityType.Fragment
  ) {
    return value;
  }
  if (
    entityType === EntityType.NumberLiteral ||
    entityType === EntityType.DatetimeLiteral
  ) {
    return value.toUpperCase();
  }
  return IDENTIFIER_TYPES.has(entityType)
    ? canonicalIdentifier(value, quoted)
    : value;
}

export interface OccurrenceOptions {
  canonical?: string;
  quoted?: boolean;
  quotingContext?: string;
  confidence?: number;
  priority?: number;
}

export function makeOccurrence(
  source: string,
  start: number,
  end: number,
  entityType: EntityType,
  provenance: string,
  options: OccurrenceOptions = {}
): TextOccurrence | null {
  if (start < 0 || end <= start || end > source.length) return null;
  const original = source.slice(start, end);
  if (!original.trim()) return null;
  return {
    start,
    end,
    entityType,
    original,
    canonical:
      options.canonical ?? canonicalValue(entityType, original, options.quoted ?? false),
    provenance,
    confidence: options.confidence ?? 1,
    quotingContext: options.quotingContext ?? "none",
    priority: options.priority ?? ENTITY_PRIORITY[entityType]
  };
}

export function shiftOccurrence(
  occurrence: TextOccurrence,
  offset: number,
  parentSource: string
): TextOccurrence {
  return {
    ...occurrence,
    start: occurrence.start + offset,
    end: occurrence.end + offset,
    original: parentSource.slice(occurrence.start + offset, occurrence.end + offset)
  };
}

export function retagOccurrence(
  occurrence: TextOccurrence,
  entityType: EntityType
): TextOccurrence {
  const quoted = occurrence.canonical.startsWith("Q:");
  return {
    ...occurrence,
    entityType,
    canonical: canonicalValue(entityType, occurrence.original, quoted),
    priority: ENTITY_PRIORITY[entityType]
  };
}

export function finalizeOccurrences(items: readonly TextOccurrence[]): TextOccurrence[] {
  const bestByRange = new Map<string, TextOccurrence>();
  for (const item of items) {
    const key = `${item.start}:${item.end}`;
    const current = bestByRange.get(key);
    if (!current || item.priority > current.priority) bestByRange.set(key, item);
  }

  const candidates = [...bestByRange.values()].sort(
    (left, right) =>
      left.start - right.start ||
      right.priority - left.priority ||
      right.end - right.start - (left.end - left.start)
  );
  const accepted: TextOccurrence[] = [];
  for (const candidate of candidates) {
    const overlaps = accepted.filter(
      (item) => candidate.start < item.end && item.start < candidate.end
    );
    if (overlaps.length === 0) {
      accepted.push(candidate);
      continue;
    }
    if (overlaps.every((item) => candidate.priority > item.priority)) {
      for (const overlap of overlaps) accepted.splice(accepted.indexOf(overlap), 1);
      accepted.push(candidate);
    }
  }
  return accepted.sort((left, right) => left.start - right.start || left.end - right.end);
}

export function emptyParsedArtifact(kind: ParsedArtifact["kind"], sourceText: string): ParsedArtifact {
  return {
    kind,
    sourceText,
    occurrences: [],
    findings: [],
    extractedSql: [],
    metadata: {}
  };
}

export function addOccurrence(
  artifact: ParsedArtifact,
  occurrence: TextOccurrence | null
): void {
  if (!occurrence) return;
  if (artifact.sourceText.slice(occurrence.start, occurrence.end) !== occurrence.original) {
    throw new Error(`Occurrence does not match source at ${occurrence.start}:${occurrence.end}`);
  }
  artifact.occurrences.push(occurrence);
}

export function finalizeArtifact(artifact: ParsedArtifact): ParsedArtifact {
  artifact.occurrences = finalizeOccurrences(artifact.occurrences);
  return artifact;
}

import {
  addOccurrence,
  canonicalValue,
  emptyParsedArtifact,
  finalizeArtifact,
  makeOccurrence,
  retagOccurrence,
  shiftOccurrence
} from "./occurrences";
import { parseExpressionFragment, parseHintFragment, parseSql } from "./sql-parser";
import { cellBounds, linesWithOffsets, trimmedBounds } from "./text-utils";
import {
  ArtifactKind,
  EntityType,
  type ParsedArtifact,
  type TextOccurrence
} from "./types";

const SECTION_NAMES = [
  "Query Block Name / Object Alias",
  "Outline Data",
  "Predicate Information",
  "Column Projection Information",
  "Peeked Binds",
  "Remote SQL Information",
  "Note",
  "Hint Report"
] as const;

interface SectionRange {
  name: (typeof SECTION_NAMES)[number];
  start: number;
  end: number;
}

function groupOffset(match: RegExpMatchArray, group: string): number {
  return (match.index ?? 0) + match[0].indexOf(group);
}

function addSqlRegion(artifact: ParsedArtifact, start: number, end: number): void {
  const region = artifact.sourceText.slice(start, end);
  const leading = region.length - region.trimStart().length;
  const sql = region.trim();
  if (!sql) return;
  const parsed = parseSql(sql);
  artifact.extractedSql.push(sql);
  for (const item of parsed.occurrences) {
    addOccurrence(artifact, shiftOccurrence(item, start + leading, artifact.sourceText));
  }
  artifact.findings.push(...parsed.findings);
}

function maskQualifiedName(
  artifact: ParsedArtifact,
  start: number,
  end: number,
  objectType: EntityType,
  provenance: string
): void {
  const value = artifact.sourceText.slice(start, end);
  const parts = [...value.matchAll(/"(?:""|[^"])+"|[\p{L}_$#][\p{L}\p{N}_$#]*/gu)];
  if (parts.length === 0) {
    addOccurrence(
      artifact,
      makeOccurrence(artifact.sourceText, start, end, objectType, provenance, {
        confidence: 0.7
      })
    );
    return;
  }
  for (let index = 0; index < parts.length; index += 1) {
    const part = parts[index]!;
    const raw = part[0];
    const quoted = raw.startsWith('"') && raw.endsWith('"');
    const clean = quoted ? raw.slice(1, -1) : raw;
    const partStart = start + (part.index ?? 0) + (quoted ? 1 : 0);
    const partEnd = partStart + clean.length;
    const type = index < parts.length - 1 ? EntityType.Schema : objectType;
    addOccurrence(
      artifact,
      makeOccurrence(artifact.sourceText, partStart, partEnd, type, provenance, {
        quoted,
        canonical: canonicalValue(type, clean, quoted),
        quotingContext: quoted ? "quoted_identifier" : "none"
      })
    );
  }
}

function parsePlanTables(artifact: ParsedArtifact): void {
  const lines = linesWithOffsets(artifact.sourceText);
  for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
    const headerLine = lines[lineIndex]!;
    if (!/\|\s*Id\s*\|/i.test(headerLine.text)) continue;
    const headerCells = cellBounds(headerLine.text);
    const headers = headerCells.map(([start, end]) =>
      headerLine.text.slice(start, end).trim().toLowerCase()
    );
    const operationIndex = headers.indexOf("operation");
    const nameIndex = headers.indexOf("name");
    if (operationIndex < 0 || nameIndex < 0) continue;
    const identityColumns = new Map<number, EntityType>();
    for (let index = 0; index < headers.length; index += 1) {
      const header = headers[index];
      if (header === "object id" || header === "object#") {
        identityColumns.set(index, EntityType.ObjectId);
      } else if (["inst", "instance", "instance id"].includes(header ?? "")) {
        identityColumns.set(index, EntityType.SessionId);
      }
    }

    for (let rowIndex = lineIndex + 1; rowIndex < lines.length; rowIndex += 1) {
      const row = lines[rowIndex]!;
      if (!row.text.trimStart().startsWith("|")) {
        if (/^\s*-+\s*$/.test(row.text)) continue;
        break;
      }
      const cells = cellBounds(row.text);
      if (cells.length <= Math.max(operationIndex, nameIndex)) continue;
      for (const [columnIndex, type] of identityColumns) {
        const cell = cells[columnIndex];
        if (!cell) continue;
        const bounds = trimmedBounds(row.text, cell[0], cell[1]);
        if (!bounds) continue;
        addOccurrence(
          artifact,
          makeOccurrence(
            artifact.sourceText,
            row.offset + bounds[0],
            row.offset + bounds[1],
            type,
            "xplan_plan_identity"
          )
        );
      }
      const operationCell = cells[operationIndex]!;
      const nameCell = cells[nameIndex]!;
      const operation = row.text
        .slice(operationCell[0], operationCell[1])
        .trim()
        .toUpperCase();
      const bounds = trimmedBounds(row.text, nameCell[0], nameCell[1]);
      if (!bounds) continue;
      let absoluteStart = row.offset + bounds[0];
      const absoluteEnd = row.offset + bounds[1];
      const rawName = artifact.sourceText.slice(absoluteStart, absoluteEnd);
      let type = EntityType.Identifier;
      if (rawName.startsWith(":")) {
        type = EntityType.QueryBlock;
        absoluteStart += 1;
      } else if (operation.includes("TABLE")) type = EntityType.Table;
      else if (operation.includes("VIEW")) type = EntityType.View;
      else if (operation.includes("PARTITION")) type = EntityType.Partition;
      else if (operation.includes("INDEX")) type = EntityType.Index;
      maskQualifiedName(
        artifact,
        absoluteStart,
        absoluteEnd,
        type,
        "xplan_name_column"
      );
    }
  }
}

function sectionRanges(source: string): SectionRange[] {
  const escaped = SECTION_NAMES.map((name) => name.replaceAll("/", "\\/")).join("|");
  const pattern = new RegExp(
    `^\\s*(${escaped})(?:\\s*\\([^\\r\\n]*\\))?\\s*:?\\s*$`,
    "gim"
  );
  const matches = [...source.matchAll(pattern)];
  return matches.map((match, index) => {
    let start = (match.index ?? 0) + match[0].length;
    const newline = source.slice(start).match(/^(?:\r\n|\n|\r)/);
    if (newline) start += newline[0].length;
    const separator = source.slice(start).match(/^-+\s*(?:\r\n|\n|\r)/);
    if (separator) start += separator[0].length;
    return {
      name: match[1] as SectionRange["name"],
      start,
      end: matches[index + 1]?.index ?? source.length
    };
  });
}

function parseQueryBlocks(artifact: ParsedArtifact, start: number, end: number): void {
  const fragment = artifact.sourceText.slice(start, end);
  for (const match of fragment.matchAll(/\b(?:SEL|SET|INS|UPD|DEL)\$[A-Z0-9_]+\b/gi)) {
    addOccurrence(
      artifact,
      makeOccurrence(
        artifact.sourceText,
        start + (match.index ?? 0),
        start + (match.index ?? 0) + match[0].length,
        EntityType.QueryBlock,
        "xplan_query_block"
      )
    );
  }
  for (const match of fragment.matchAll(/"((?:""|[^"])*)"/g)) {
    const clean = match[1] ?? "";
    const after = fragment[(match.index ?? 0) + match[0].length] ?? "";
    const type = after === "@" ? EntityType.Alias : EntityType.Identifier;
    const semanticQuoted = clean !== clean.toUpperCase() || !/^[A-Z][A-Z0-9_$#]*$/.test(clean);
    const localStart = (match.index ?? 0) + 1;
    addOccurrence(
      artifact,
      makeOccurrence(
        artifact.sourceText,
        start + localStart,
        start + localStart + clean.length,
        type,
        "xplan_query_block",
        {
          quoted: semanticQuoted,
          canonical: canonicalValue(type, clean, semanticQuoted),
          quotingContext: "quoted_identifier"
        }
      )
    );
  }
  for (const match of fragment.matchAll(
    /(?<!["\p{L}\p{N}_$#])([\p{L}_][\p{L}\p{N}_$#]*)@(?=(?:SEL|SET|INS|UPD|DEL)\$)/giu
  )) {
    const clean = match[1] ?? "";
    const localStart = (match.index ?? 0) + match[0].indexOf(clean);
    addOccurrence(
      artifact,
      makeOccurrence(
        artifact.sourceText,
        start + localStart,
        start + localStart + clean.length,
        EntityType.Alias,
        "xplan_query_block"
      )
    );
  }
}

function parsePredicateSection(artifact: ParsedArtifact, start: number, end: number): void {
  const fragment = artifact.sourceText.slice(start, end);
  const matches = [...fragment.matchAll(/^\s*\d+\s*-\s*(?:access|filter)\(/gim)];
  if (matches.length === 0) {
    artifact.occurrences.push(
      ...parseExpressionFragment(fragment, {
        baseOffset: start,
        parentSource: artifact.sourceText,
        displayContext: true
      })
    );
    return;
  }
  for (let index = 0; index < matches.length; index += 1) {
    const match = matches[index]!;
    const expressionStart = (match.index ?? 0) + match[0].length - 1;
    const expressionEnd = matches[index + 1]?.index ?? fragment.length;
    artifact.occurrences.push(
      ...parseExpressionFragment(fragment.slice(expressionStart, expressionEnd), {
        baseOffset: start + expressionStart,
        parentSource: artifact.sourceText,
        displayContext: true
      })
    );
  }
}

function parsePeekedBinds(artifact: ParsedArtifact, start: number, end: number): void {
  const fragment = artifact.sourceText.slice(start, end);
  for (const line of linesWithOffsets(fragment)) {
    const valueMatch = line.text.match(/\)\s*:\s*(.+?)\s*(?:\r?\n)?$/u);
    if (!valueMatch) continue;
    const prefix = line.text.slice(0, valueMatch.index ?? 0);
    const bindMatch = prefix.match(/:([\p{L}\p{N}_$#]+)/u);
    if (bindMatch) {
      const bind = bindMatch[1] ?? "";
      const bindStart = start + line.offset + (bindMatch.index ?? 0) + 1;
      addOccurrence(
        artifact,
        makeOccurrence(
          artifact.sourceText,
          bindStart,
          bindStart + bind.length,
          EntityType.Bind,
          "xplan_peeked_bind",
          { canonical: canonicalValue(EntityType.Bind, bind), quotingContext: "bind" }
        )
      );
    }
    const value = valueMatch[1] ?? "";
    if (value.toUpperCase() === "NULL") continue;
    const matchStart = valueMatch.index ?? 0;
    let valueStart = start + line.offset + matchStart + valueMatch[0].indexOf(value);
    let valueEnd = valueStart + value.length;
    let type = EntityType.StringLiteral;
    if (value.startsWith("'") && value.endsWith("'")) {
      valueStart += 1;
      valueEnd -= 1;
    } else if (/^[-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?$/.test(value)) {
      type = EntityType.NumberLiteral;
    }
    addOccurrence(
      artifact,
      makeOccurrence(
        artifact.sourceText,
        valueStart,
        valueEnd,
        type,
        "xplan_peeked_bind_value",
        { quotingContext: "bind_value" }
      )
    );
  }
}

function parseNotes(artifact: ParsedArtifact, start: number, end: number): void {
  const fragment = artifact.sourceText.slice(start, end);
  const safe = /(?:dynamic statistics|statistics feedback|cardinality feedback|adaptive plan|basic plan statistics|degree of parallelism|SQL (?:profile|plan baseline)|outline|rule based optimizer)/i;
  for (const line of linesWithOffsets(fragment)) {
    const clean = line.text.trim();
    if (!clean || /^[-*\s]+$/.test(clean)) continue;
    for (const match of line.text.matchAll(/SQL (?:profile|plan baseline)\s+"((?:""|[^"])*)"/gi)) {
      const value = match[1] ?? "";
      const localStart = (match.index ?? 0) + match[0].indexOf(value);
      addOccurrence(
        artifact,
        makeOccurrence(
          artifact.sourceText,
          start + line.offset + localStart,
          start + line.offset + localStart + value.length,
          EntityType.Identifier,
          "xplan_note_object"
        )
      );
    }
    if (safe.test(clean)) continue;
    let localStart = line.text.search(/\S/);
    if (/^[-*]\s/.test(line.text.slice(localStart))) localStart += 2;
    const localEnd = line.text.trimEnd().length;
    addOccurrence(
      artifact,
      makeOccurrence(
        artifact.sourceText,
        start + line.offset + localStart,
        start + line.offset + localEnd,
        EntityType.Fragment,
        "xplan_unknown_note",
        { confidence: 0.5, priority: 110 }
      )
    );
  }
}

function reconcileIdentifiers(artifact: ParsedArtifact): void {
  const semantic = new Map<string, Set<EntityType>>();
  for (const item of artifact.occurrences) {
    if (item.entityType === EntityType.Identifier) continue;
    const values = semantic.get(item.canonical) ?? new Set<EntityType>();
    values.add(item.entityType);
    semantic.set(item.canonical, values);
  }
  artifact.occurrences = artifact.occurrences.map((item) => {
    const candidates = [...(semantic.get(item.canonical) ?? [])].filter(
      (type) => type !== EntityType.QueryBlock
    );
    return item.entityType === EntityType.Identifier && candidates.length === 1
      ? retagOccurrence(item, candidates[0]!)
      : item;
  });
}

export function parseXplan(source: string): ParsedArtifact {
  const artifact = emptyParsedArtifact(ArtifactKind.Xplan, source);
  const sqlHeaderPattern = /^\s*SQL_ID\s+([a-z0-9]+)\s*,\s*child number\s+(\d+)\s*\r?\n-+\s*\r?\n([\s\S]*?)(?=\r?\n\s*\r?\n\s*Plan hash value\s*:|(?![\s\S]))/gim;
  for (const sqlHeader of source.matchAll(sqlHeaderPattern)) {
    const sqlId = sqlHeader[1] ?? "";
    const child = sqlHeader[2] ?? "";
    const sql = sqlHeader[3] ?? "";
    const sqlIdStart = groupOffset(sqlHeader, sqlId);
    const childStart = groupOffset(sqlHeader, child);
    const sqlStart = groupOffset(sqlHeader, sql);
    addOccurrence(
      artifact,
      makeOccurrence(source, sqlIdStart, sqlIdStart + sqlId.length, EntityType.SqlId, "xplan_header")
    );
    addOccurrence(
      artifact,
      makeOccurrence(
        source,
        childStart,
        childStart + child.length,
        EntityType.ExecutionId,
        "xplan_header"
      )
    );
    addSqlRegion(artifact, sqlStart, sqlStart + sql.length);
  }

  const explainedSqlPattern = /^\s*(?:EXPLAINED SQL STATEMENT|SQL STATEMENT)\s*:\s*\r?\n-+\s*\r?\n([\s\S]*?)(?=\r?\n\s*\r?\n\s*Plan hash value\s*:|(?![\s\S]))/gim;
  for (const explainedSql of source.matchAll(explainedSqlPattern)) {
    const sql = explainedSql[1] ?? "";
    const sqlStart = groupOffset(explainedSql, sql);
    addSqlRegion(artifact, sqlStart, sqlStart + sql.length);
  }

  for (const match of source.matchAll(/Plan hash value\s*[:=]\s*(\d+)/gi)) {
    const value = match[1] ?? "";
    const start = groupOffset(match, value);
    addOccurrence(
      artifact,
      makeOccurrence(source, start, start + value.length, EntityType.PlanHash, "xplan_plan_hash")
    );
  }
  parsePlanTables(artifact);

  for (const section of sectionRanges(source)) {
    const fragment = source.slice(section.start, section.end);
    if (section.name === "Query Block Name / Object Alias") {
      parseQueryBlocks(artifact, section.start, section.end);
    } else if (section.name === "Outline Data") {
      artifact.occurrences.push(
        ...parseHintFragment(fragment, section.start, artifact.sourceText)
      );
    } else if (section.name === "Predicate Information") {
      parsePredicateSection(artifact, section.start, section.end);
    } else if (section.name === "Column Projection Information") {
      artifact.occurrences.push(
        ...parseExpressionFragment(fragment, {
          baseOffset: section.start,
          parentSource: artifact.sourceText,
          maskNumbers: false,
          displayContext: true
        })
      );
    } else if (section.name === "Peeked Binds") {
      parsePeekedBinds(artifact, section.start, section.end);
    } else if (section.name === "Remote SQL Information") {
      addSqlRegion(artifact, section.start, section.end);
    } else if (section.name === "Note" || section.name === "Hint Report") {
      parseNotes(artifact, section.start, section.end);
    }
  }
  reconcileIdentifiers(artifact);
  return finalizeArtifact(artifact);
}

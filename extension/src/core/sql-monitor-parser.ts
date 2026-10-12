import {
  addOccurrence,
  canonicalValue,
  emptyParsedArtifact,
  finalizeArtifact,
  makeOccurrence,
  shiftOccurrence
} from "./occurrences";
import { parseSql } from "./sql-parser";
import { cellBounds, linesWithOffsets, normalizeWhitespace, trimmedBounds } from "./text-utils";
import { ArtifactKind, EntityType, type ParsedArtifact } from "./types";

const SENSITIVE_GLOBAL_FIELDS = new Map<string, EntityType>([
  ["instance id", EntityType.SessionId],
  ["session", EntityType.SessionId],
  ["sql id", EntityType.SqlId],
  ["sql execution id", EntityType.ExecutionId],
  ["module/action", EntityType.Module],
  ["service", EntityType.Service],
  ["program", EntityType.Program],
  ["database", EntityType.Database],
  ["db name", EntityType.Database],
  ["db unique name", EntityType.Database],
  ["container", EntityType.Database],
  ["host", EntityType.Host],
  ["user", EntityType.User],
  ["sql plan hash value", EntityType.PlanHash],
  ["plan hash value", EntityType.PlanHash],
  ["sql full plan hash value", EntityType.PlanHash],
  ["sql exec id", EntityType.ExecutionId],
  ["session id", EntityType.SessionId],
  ["session serial", EntityType.SessionId],
  ["dbop name", EntityType.Identifier],
  ["dbop execution id", EntityType.ExecutionId],
  ["pdb name", EntityType.Database],
  ["con name", EntityType.Database],
  ["client identifier", EntityType.Identifier],
  ["client info", EntityType.Identifier],
  ["machine", EntityType.Host],
  ["os user", EntityType.User],
  ["object id", EntityType.ObjectId],
  ["object#", EntityType.ObjectId],
  ["dbid", EntityType.Database],
  ["database id", EntityType.Database]
]);

const SAFE_GLOBAL_FIELDS = new Set([
  "status",
  "execution started",
  "sql execution start",
  "first refresh time",
  "last refresh time",
  "duration",
  "fetch calls",
  "dop",
  "degree of parallelism",
  "px servers allocated",
  "report level"
]);

const SENSITIVE_TABLE_COLUMNS = new Map<string, EntityType>([
  ["instance", EntityType.SessionId],
  ["instance id", EntityType.SessionId],
  ["session", EntityType.SessionId],
  ["session id", EntityType.SessionId],
  ["session serial", EntityType.SessionId],
  ["user", EntityType.User],
  ["username", EntityType.User],
  ["host", EntityType.Host],
  ["machine", EntityType.Host],
  ["process", EntityType.Program],
  ["program", EntityType.Program],
  ["module", EntityType.Module],
  ["action", EntityType.Action],
  ["service", EntityType.Service],
  ["server#", EntityType.SessionId]
]);

const SECTION_PATTERN = /^\s*(SQL Text|Global Information|Global Stats|SQL Plan Monitoring Details|Parallel Execution Details|Activity|Metrics|Bind Variables|Binds)(?:\s*\([^\r\n]*\))?\s*$/gim;
const GENERIC_SECTION_PATTERN = /^(?<heading>[\p{L}][^\r\n]{0,140})[ \t]*\r?\n(?<separator>[-=]{3,})[ \t]*$/gimu;

interface SectionRange {
  name: string;
  headingStart: number;
  start: number;
  end: number;
}

function sectionRanges(source: string): SectionRange[] {
  const matches = [...source.matchAll(SECTION_PATTERN)];
  return matches.map((match, index) => {
    let start = (match.index ?? 0) + match[0].length;
    const newline = source.slice(start).match(/^(?:\r\n|\n|\r)/);
    if (newline) start += newline[0].length;
    const separator = source.slice(start).match(/^-+\s*(?:\r\n|\n|\r)/);
    if (separator) start += separator[0].length;
    return {
      name: match[1] ?? "",
      headingStart: match.index ?? 0,
      start,
      end: matches[index + 1]?.index ?? source.length
    };
  });
}

function groupStart(match: RegExpMatchArray, value: string): number {
  return (match.index ?? 0) + match[0].indexOf(value);
}

function parseSqlText(artifact: ParsedArtifact, start: number, end: number): void {
  const region = artifact.sourceText.slice(start, end);
  const leading = region.length - region.trimStart().length;
  const sql = region.trim();
  if (!sql) return;
  const parsed = parseSql(sql);
  artifact.extractedSql.push(sql);
  for (const occurrence of parsed.occurrences) {
    addOccurrence(artifact, shiftOccurrence(occurrence, start + leading, artifact.sourceText));
  }
  artifact.findings.push(...parsed.findings);
}

function parseGlobalInformation(
  artifact: ParsedArtifact,
  start: number,
  end: number
): void {
  const region = artifact.sourceText.slice(start, end);
  for (const line of linesWithOffsets(region)) {
    const match = line.text.match(/^\s*([^:]+?)\s*:\s*(.*?)\s*(?:\r?\n)?$/);
    if (!match) continue;
    const label = normalizeWhitespace(match[1] ?? "").toLowerCase();
    let entityType = SENSITIVE_GLOBAL_FIELDS.get(label);
    if (!entityType && SAFE_GLOBAL_FIELDS.has(label)) continue;
    const value = match[2] ?? "";
    if (!value || value === "-") continue;
    if (!entityType) {
      entityType = EntityType.Fragment;
      artifact.findings.push({
        severity: "warning",
        code: "sql_monitor_unknown_global_field_masked",
        message: `Unknown Global Information field was masked: ${(match[1] ?? "").trim()}`,
        start: start + line.offset,
        end: start + line.offset + line.text.trimEnd().length
      });
    }
    const localStart = line.text.indexOf(value);
    addOccurrence(
      artifact,
      makeOccurrence(
        artifact.sourceText,
        start + line.offset + localStart,
        start + line.offset + localStart + value.length,
        entityType,
        "sql_monitor_global"
      )
    );
  }
}

function maskQualifiedPlanName(
  artifact: ParsedArtifact,
  start: number,
  end: number,
  finalType: EntityType
): void {
  const raw = artifact.sourceText.slice(start, end);
  const parts = [...raw.matchAll(/"(?:""|[^"])+"|[\p{L}_$#][\p{L}\p{N}_$#]*/gu)];
  if (parts.length === 0) {
    addOccurrence(
      artifact,
      makeOccurrence(artifact.sourceText, start, end, finalType, "sql_monitor_plan_name", {
        confidence: 0.7
      })
    );
    return;
  }
  for (let index = 0; index < parts.length; index += 1) {
    const part = parts[index]!;
    const quoted = part[0].startsWith('"') && part[0].endsWith('"');
    const clean = quoted ? part[0].slice(1, -1) : part[0];
    const partStart = start + (part.index ?? 0) + (quoted ? 1 : 0);
    const entityType = index < parts.length - 1 ? EntityType.Schema : finalType;
    addOccurrence(
      artifact,
      makeOccurrence(
        artifact.sourceText,
        partStart,
        partStart + clean.length,
        entityType,
        "sql_monitor_plan_name",
        {
          quoted,
          canonical: canonicalValue(entityType, clean, quoted),
          quotingContext: quoted ? "quoted_identifier" : "none"
        }
      )
    );
  }
}

function parsePlanTable(artifact: ParsedArtifact, section: SectionRange): void {
  const lines = linesWithOffsets(artifact.sourceText.slice(section.start, section.end));
  let headerIndex = -1;
  let headers: string[] = [];
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]!;
    if (!/\|\s*Id\s*\|/i.test(line.text)) continue;
    headers = cellBounds(line.text).map(([left, right]) =>
      normalizeWhitespace(line.text.slice(left, right)).toLowerCase()
    );
    if (headers.includes("operation") && headers.includes("name")) {
      headerIndex = index;
      break;
    }
  }
  if (headerIndex < 0) return;
  const operationIndex = headers.indexOf("operation");
  const nameIndex = headers.indexOf("name");
  const identityColumns = new Map<number, EntityType>();
  headers.forEach((header, index) => {
    if (header === "object id" || header === "object#") {
      identityColumns.set(index, EntityType.ObjectId);
    } else if (["inst", "instance", "instance id"].includes(header)) {
      identityColumns.set(index, EntityType.SessionId);
    }
  });

  for (const line of lines.slice(headerIndex + 1)) {
    if (!line.text.trimStart().startsWith("|")) continue;
    const bounds = cellBounds(line.text);
    if (bounds.length <= Math.max(operationIndex, nameIndex)) continue;
    for (const [columnIndex, type] of identityColumns) {
      const cell = bounds[columnIndex];
      if (!cell) continue;
      const valueBounds = trimmedBounds(line.text, cell[0], cell[1]);
      if (!valueBounds) continue;
      addOccurrence(
        artifact,
        makeOccurrence(
          artifact.sourceText,
          section.start + line.offset + valueBounds[0],
          section.start + line.offset + valueBounds[1],
          type,
          "sql_monitor_plan_identity"
        )
      );
    }
    const operationCell = bounds[operationIndex]!;
    const nameCell = bounds[nameIndex]!;
    const operation = line.text
      .slice(operationCell[0], operationCell[1])
      .trim()
      .toUpperCase();
    const valueBounds = trimmedBounds(line.text, nameCell[0], nameCell[1]);
    if (!valueBounds) continue;
    const absoluteStart = section.start + line.offset + valueBounds[0];
    const absoluteEnd = section.start + line.offset + valueBounds[1];
    const type = operation.includes("INDEX")
      ? EntityType.Index
      : operation.includes("VIEW")
        ? EntityType.View
        : operation.includes("TABLE")
          ? EntityType.Table
          : EntityType.Identifier;
    maskQualifiedPlanName(artifact, absoluteStart, absoluteEnd, type);
  }
}

function parseSensitiveTable(
  artifact: ParsedArtifact,
  start: number,
  end: number
): void {
  const lines = linesWithOffsets(artifact.sourceText.slice(start, end));
  let headerIndex = -1;
  let sensitiveIndexes = new Map<number, EntityType>();
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]!;
    if (!line.text.trimStart().startsWith("|")) continue;
    const headers = cellBounds(line.text).map(([left, right]) =>
      normalizeWhitespace(line.text.slice(left, right)).toLowerCase()
    );
    const found = new Map<number, EntityType>();
    headers.forEach((header, columnIndex) => {
      const type = SENSITIVE_TABLE_COLUMNS.get(header);
      if (type) found.set(columnIndex, type);
    });
    if (found.size > 0) {
      headerIndex = index;
      sensitiveIndexes = found;
      break;
    }
  }
  if (headerIndex < 0) return;
  for (const line of lines.slice(headerIndex + 1)) {
    if (!line.text.trimStart().startsWith("|")) continue;
    const bounds = cellBounds(line.text);
    for (const [columnIndex, entityType] of sensitiveIndexes) {
      const cell = bounds[columnIndex];
      if (!cell) continue;
      const valueBounds = trimmedBounds(line.text, cell[0], cell[1]);
      if (!valueBounds) continue;
      addOccurrence(
        artifact,
        makeOccurrence(
          artifact.sourceText,
          start + line.offset + valueBounds[0],
          start + line.offset + valueBounds[1],
          entityType,
          "sql_monitor_sensitive_table"
        )
      );
    }
  }
}

function parseBindSection(artifact: ParsedArtifact, start: number, end: number): void {
  const region = artifact.sourceText.slice(start, end);
  const lines = linesWithOffsets(region);
  for (let headerIndex = 0; headerIndex < lines.length; headerIndex += 1) {
    const header = lines[headerIndex]!;
    if (!header.text.trimStart().startsWith("|")) continue;
    const headers = cellBounds(header.text).map(([left, right]) =>
      normalizeWhitespace(header.text.slice(left, right)).toLowerCase()
    );
    const nameIndex = headers.indexOf("name");
    const valueIndex = headers.indexOf("value");
    if (nameIndex < 0 || valueIndex < 0) continue;
    for (const line of lines.slice(headerIndex + 1)) {
      if (/^\s*[-=]+\s*$/.test(line.text)) continue;
      if (!line.text.trimStart().startsWith("|")) break;
      const bounds = cellBounds(line.text);
      const nameCell = bounds[nameIndex];
      const valueCell = bounds[valueIndex];
      if (!nameCell || !valueCell) continue;
      const nameBounds = trimmedBounds(line.text, nameCell[0], nameCell[1]);
      if (nameBounds) {
        const rawName = line.text.slice(nameBounds[0], nameBounds[1]);
        const prefix = rawName.startsWith(":") ? 1 : 0;
        addOccurrence(
          artifact,
          makeOccurrence(
            artifact.sourceText,
            start + line.offset + nameBounds[0] + prefix,
            start + line.offset + nameBounds[1],
            EntityType.Bind,
            "sql_monitor_bind_table",
            { quotingContext: "bind" }
          )
        );
      }
      const valueBounds = trimmedBounds(line.text, valueCell[0], valueCell[1]);
      if (!valueBounds) continue;
      const rawValue = line.text.slice(valueBounds[0], valueBounds[1]);
      if (!rawValue || rawValue.toUpperCase() === "NULL") continue;
      let valueStart = start + line.offset + valueBounds[0];
      let valueEnd = start + line.offset + valueBounds[1];
      let type = EntityType.StringLiteral;
      let quotingContext = "bind_value";
      if (rawValue.startsWith("'") && rawValue.endsWith("'") && rawValue.length >= 2) {
        valueStart += 1;
        valueEnd -= 1;
        quotingContext = "string";
      } else if (/^[-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?$/.test(rawValue)) {
        type = EntityType.NumberLiteral;
      }
      addOccurrence(
        artifact,
        makeOccurrence(
          artifact.sourceText,
          valueStart,
          valueEnd,
          type,
          "sql_monitor_bind_table_value",
          { quotingContext }
        )
      );
    }
    break;
  }

  for (const match of region.matchAll(/(:[\p{L}\p{N}_$#]+).*?(?:=|:)\s*(.+?)\s*$/gimu)) {
    const bind = match[1] ?? "";
    const value = match[2] ?? "";
    const bindStart = start + groupStart(match, bind) + 1;
    addOccurrence(
      artifact,
      makeOccurrence(
        artifact.sourceText,
        bindStart,
        bindStart + bind.length - 1,
        EntityType.Bind,
        "sql_monitor_bind",
        {
          canonical: canonicalValue(EntityType.Bind, bind.slice(1)),
          quotingContext: "bind"
        }
      )
    );
    if (!value || value.toUpperCase() === "NULL") continue;
    let valueStart = start + groupStart(match, value);
    let valueEnd = valueStart + value.length;
    let type = EntityType.StringLiteral;
    let quotingContext = "bind_value";
    if (value.startsWith("'") && value.endsWith("'") && value.length >= 2) {
      valueStart += 1;
      valueEnd -= 1;
      quotingContext = "string";
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
        "sql_monitor_bind_value",
        { quotingContext }
      )
    );
  }
}

function parseUnknownSections(artifact: ParsedArtifact): void {
  const known = new Set([
    "sql text",
    "global information",
    "global stats",
    "sql plan monitoring details",
    "parallel execution details",
    "activity",
    "metrics",
    "bind variables",
    "binds"
  ]);
  const matches = [...artifact.sourceText.matchAll(GENERIC_SECTION_PATTERN)];
  for (let index = 0; index < matches.length; index += 1) {
    const match = matches[index]!;
    const heading = (match.groups?.heading ?? "")
      .replace(/\s*\([^\r\n]*\)\s*$/, "")
      .trim();
    if (known.has(heading.toLowerCase())) continue;
    let contentStart = (match.index ?? 0) + match[0].length;
    const newline = artifact.sourceText.slice(contentStart).match(/^(?:\r\n|\n|\r)/);
    if (newline) contentStart += newline[0].length;
    const contentEnd = matches[index + 1]?.index ?? artifact.sourceText.length;
    const bounds = trimmedBounds(artifact.sourceText, contentStart, contentEnd);
    if (!bounds) continue;
    addOccurrence(
      artifact,
      makeOccurrence(
        artifact.sourceText,
        bounds[0],
        bounds[1],
        EntityType.Fragment,
        "sql_monitor_unknown_section",
        { confidence: 0.4, priority: 110 }
      )
    );
    artifact.findings.push({
      severity: "warning",
      code: "sql_monitor_unknown_section_masked",
      message: `Unknown SQL Monitor section was masked: ${heading}`,
      start: match.index ?? 0,
      end: contentEnd
    });
  }
}

export function parseSqlMonitor(source: string): ParsedArtifact {
  const artifact = emptyParsedArtifact(ArtifactKind.SqlMonitor, source);
  const sections = sectionRanges(source);
  for (const section of sections) {
    const normalized = section.name.toLowerCase();
    if (normalized === "sql text") {
      parseSqlText(artifact, section.start, section.end);
    } else if (normalized === "global information") {
      parseGlobalInformation(artifact, section.start, section.end);
    } else if (normalized === "sql plan monitoring details") {
      parsePlanTable(artifact, section);
    } else if (normalized === "bind variables" || normalized === "binds") {
      parseBindSection(artifact, section.start, section.end);
    } else if (normalized === "parallel execution details" || normalized === "activity") {
      parseSensitiveTable(artifact, section.start, section.end);
    }
  }

  for (const match of source.matchAll(/Plan Hash Value\s*=\s*(\d+)/gi)) {
    const value = match[1] ?? "";
    const start = groupStart(match, value);
    addOccurrence(
      artifact,
      makeOccurrence(source, start, start + value.length, EntityType.PlanHash, "sql_monitor_plan_hash")
    );
  }
  parseUnknownSections(artifact);
  if (sections.length === 0) {
    artifact.findings.push({
      severity: "warning",
      code: "sql_monitor_sections_not_found",
      message: "SQL Monitor marker was detected but standard TEXT sections were not found."
    });
  }
  return finalizeArtifact(artifact);
}

import {
  addOccurrence,
  canonicalValue,
  emptyParsedArtifact,
  finalizeArtifact,
  finalizeOccurrences,
  makeOccurrence,
  retagOccurrence
} from "./occurrences";
import {
  KNOWN_FUNCTIONS,
  SPECIAL_IDENTIFIERS,
  tokenizeOracle
} from "./oracle-lexer";
import {
  ArtifactKind,
  EntityType,
  type OracleToken,
  type ParsedArtifact,
  type TextOccurrence
} from "./types";

const QUERY_BLOCK_RE = /^(?:SEL|SET|INS|UPD|DEL)\$[A-Z0-9_]+$/i;
const TABLE_INTRODUCERS = new Set(["FROM", "JOIN", "UPDATE", "INTO", "USING"]);
const FROM_TERMINATORS = new Set([
  "WHERE",
  "GROUP",
  "HAVING",
  "ORDER",
  "CONNECT",
  "START",
  "MODEL",
  "UNION",
  "INTERSECT",
  "MINUS",
  "RETURNING",
  "FETCH"
]);

const KNOWN_HINTS = new Set([
  "ALL_ROWS",
  "APPEND",
  "BEGIN_OUTLINE_DATA",
  "CARDINALITY",
  "CLUSTER",
  "DB_VERSION",
  "END_OUTLINE_DATA",
  "FIRST_ROWS",
  "FULL",
  "GATHER_PLAN_STATISTICS",
  "IGNORE_OPTIM_EMBEDDED_HINTS",
  "INDEX",
  "INDEX_ASC",
  "INDEX_DESC",
  "INDEX_RS_ASC",
  "LEADING",
  "MATERIALIZE",
  "MERGE",
  "MONITOR",
  "NO_MERGE",
  "NO_PARALLEL",
  "NO_UNNEST",
  "NLJ_BATCHING",
  "OPTIMIZER_FEATURES_ENABLE",
  "OPT_PARAM",
  "ORDERED",
  "OUTLINE",
  "OUTLINE_LEAF",
  "PARALLEL",
  "PQ_DISTRIBUTE",
  "PQ_FILTER",
  "PUSH_PRED",
  "RBO_OUTLINE",
  "SERIAL",
  "SWAP_JOIN_INPUTS",
  "UNNEST",
  "USE_HASH",
  "USE_MERGE",
  "USE_NL"
]);

function isIdentifierToken(token: OracleToken | undefined): token is OracleToken {
  return token?.kind === "identifier" || token?.kind === "quoted_identifier";
}

function isQuotedSemantic(token: OracleToken, displayContext = false): boolean {
  if (token.kind !== "quoted_identifier") return false;
  if (!displayContext) return true;
  return token.value !== token.value.toUpperCase() || !/^[A-Z][A-Z0-9_$#]*$/.test(token.value);
}

function occurrenceForToken(
  source: string,
  token: OracleToken,
  entityType: EntityType,
  provenance: string,
  displayContext = false
): TextOccurrence | null {
  const quoted = isQuotedSemantic(token, displayContext);
  const useContent = token.kind === "quoted_identifier" || token.kind === "bind";
  const start = useContent ? token.contentStart : token.start;
  const end = useContent ? token.contentEnd : token.end;
  return makeOccurrence(source, start, end, entityType, provenance, {
    quoted,
    canonical: canonicalValue(entityType, token.value, quoted),
    quotingContext:
      token.kind === "quoted_identifier"
        ? "quoted_identifier"
        : token.kind === "bind"
          ? "bind"
          : "none"
  });
}

function shiftLocalOccurrence(
  item: TextOccurrence,
  offset: number,
  parentSource: string
): TextOccurrence {
  return {
    ...item,
    start: item.start + offset,
    end: item.end + offset,
    original: parentSource.slice(item.start + offset, item.end + offset)
  };
}

export function parseHintFragment(
  source: string,
  baseOffset = 0,
  parentSource = source
): TextOccurrence[] {
  const occurrences: TextOccurrence[] = [];
  const allTokens = tokenizeOracle(source);
  for (const hint of allTokens.filter((item) => item.kind === "hint")) {
    occurrences.push(
      ...parseHintFragment(
        hint.value,
        baseOffset + hint.contentStart,
        parentSource
      )
    );
  }
  const tokens = allTokens.filter(
    (item) => item.kind !== "comment" && item.kind !== "hint"
  );
  for (let index = 0; index < tokens.length; index += 1) {
    const current = tokens[index]!;
    const previous = tokens[index - 1];
    const next = tokens[index + 1];
    let occurrence: TextOccurrence | null = null;

    if (current.kind === "quoted_identifier") {
      const type = previous?.raw === "@"
        ? EntityType.QueryBlock
        : next?.raw === "@"
          ? EntityType.Alias
          : EntityType.Identifier;
      occurrence = occurrenceForToken(source, current, type, "oracle_hint", true);
    } else if (current.kind === "identifier") {
      if (QUERY_BLOCK_RE.test(current.value)) {
        occurrence = occurrenceForToken(source, current, EntityType.QueryBlock, "oracle_hint");
      } else if (
        !KNOWN_HINTS.has(current.upper) &&
        !KNOWN_FUNCTIONS.has(current.upper) &&
        !["DEFAULT", "FIXED"].includes(current.upper)
      ) {
        occurrence = occurrenceForToken(source, current, EntityType.Identifier, "oracle_hint");
      }
    }
    if (occurrence) occurrences.push(shiftLocalOccurrence(occurrence, baseOffset, parentSource));
  }
  return finalizeOccurrences(occurrences);
}

export interface ExpressionOptions {
  baseOffset?: number;
  parentSource?: string;
  maskNumbers?: boolean;
  displayContext?: boolean;
}

export function parseExpressionFragment(
  source: string,
  options: ExpressionOptions = {}
): TextOccurrence[] {
  const baseOffset = options.baseOffset ?? 0;
  const parentSource = options.parentSource ?? source;
  const maskNumbers = options.maskNumbers ?? true;
  const displayContext = options.displayContext ?? true;
  const occurrences: TextOccurrence[] = [];
  const tokens = tokenizeOracle(source);

  for (let index = 0; index < tokens.length; index += 1) {
    const current = tokens[index]!;
    const previous = tokens[index - 1];
    const next = tokens[index + 1];
    let occurrence: TextOccurrence | null = null;
    if (current.kind === "quoted_identifier") {
      const type = previous?.raw === "@"
        ? EntityType.QueryBlock
        : next?.raw === "@" || next?.raw === "."
          ? EntityType.Alias
          : EntityType.Column;
      occurrence = occurrenceForToken(
        source,
        current,
        type,
        "oracle_expression",
        displayContext
      );
    } else if (
      current.kind === "string" ||
      current.kind === "national_string" ||
      current.kind === "q_string"
    ) {
      const datetime = ["DATE", "TIMESTAMP", "INTERVAL"].includes(previous?.upper ?? "");
      const type = datetime ? EntityType.DatetimeLiteral : EntityType.StringLiteral;
      occurrence = makeOccurrence(
        source,
        current.contentStart,
        current.contentEnd,
        type,
        "oracle_expression",
        {
          canonical: canonicalValue(type, current.value),
          quotingContext: current.kind
        }
      );
    } else if (current.kind === "number" && maskNumbers) {
      occurrence = occurrenceForToken(
        source,
        current,
        EntityType.NumberLiteral,
        "oracle_expression"
      );
    } else if (current.kind === "bind") {
      occurrence = occurrenceForToken(source, current, EntityType.Bind, "oracle_expression");
    } else if (current.kind === "identifier") {
      if (QUERY_BLOCK_RE.test(current.value)) {
        occurrence = occurrenceForToken(
          source,
          current,
          EntityType.QueryBlock,
          "oracle_expression"
        );
      } else if (next?.raw === ".") {
        occurrence = occurrenceForToken(source, current, EntityType.Alias, "oracle_expression");
      } else if (previous?.raw === ".") {
        occurrence = occurrenceForToken(source, current, EntityType.Column, "oracle_expression");
      } else if (next?.raw === "(" && !KNOWN_FUNCTIONS.has(current.upper)) {
        occurrence = occurrenceForToken(
          source,
          current,
          EntityType.Function,
          "oracle_expression"
        );
      }
    }
    if (occurrence) occurrences.push(shiftLocalOccurrence(occurrence, baseOffset, parentSource));
  }
  return finalizeOccurrences(occurrences);
}

interface ParsedObject {
  nextIndex: number;
  occurrenceIndexes: Set<number>;
  aliasCanonical?: string;
}

function parseTableObject(
  source: string,
  tokens: OracleToken[],
  startIndex: number,
  occurrences: TextOccurrence[],
  cteCanonicals: Set<string>
): ParsedObject {
  let index = startIndex;
  const used = new Set<number>();
  if (tokens[index]?.raw === "(") {
    let depth = 0;
    while (index < tokens.length) {
      if (tokens[index]?.raw === "(") depth += 1;
      if (tokens[index]?.raw === ")") {
        depth -= 1;
        if (depth === 0) {
          index += 1;
          break;
        }
      }
      index += 1;
    }
    if (tokens[index]?.upper === "AS") index += 1;
    const alias = tokens[index];
    if (isIdentifierToken(alias)) {
      const occurrence = occurrenceForToken(source, alias, EntityType.Alias, "sql_structure");
      if (occurrence) occurrences.push(occurrence);
      used.add(index);
      return occurrence
        ? { nextIndex: index + 1, occurrenceIndexes: used, aliasCanonical: occurrence.canonical }
        : { nextIndex: index + 1, occurrenceIndexes: used };
    }
    return { nextIndex: index, occurrenceIndexes: used };
  }

  const parts: Array<{ token: OracleToken; index: number }> = [];
  while (isIdentifierToken(tokens[index])) {
    parts.push({ token: tokens[index]!, index });
    index += 1;
    if (tokens[index]?.raw !== ".") break;
    index += 1;
  }
  if (parts.length === 0) return { nextIndex: startIndex, occurrenceIndexes: used };

  const tablePart = parts.at(-1)!;
  for (let partIndex = 0; partIndex < parts.length; partIndex += 1) {
    const part = parts[partIndex]!;
    const type = partIndex === parts.length - 1 ? EntityType.Table : EntityType.Schema;
    let occurrence = occurrenceForToken(source, part.token, type, "sql_structure");
    if (type === EntityType.Table && occurrence && cteCanonicals.has(occurrence.canonical)) {
      occurrence = retagOccurrence(occurrence, EntityType.Alias);
    }
    if (occurrence) occurrences.push(occurrence);
    used.add(part.index);
  }

  if (tokens[index]?.raw === "@") {
    const link = tokens[index + 1];
    if (isIdentifierToken(link)) {
      const occurrence = occurrenceForToken(source, link, EntityType.DbLink, "sql_structure");
      if (occurrence) occurrences.push(occurrence);
      used.add(index + 1);
      index += 2;
    }
  }

  if (tokens[index]?.upper === "AS") index += 1;
  const alias = tokens[index];
  let aliasCanonical: string | undefined;
  if (
    isIdentifierToken(alias) &&
    !FROM_TERMINATORS.has(alias.upper) &&
    !TABLE_INTRODUCERS.has(alias.upper)
  ) {
    const occurrence = occurrenceForToken(source, alias, EntityType.Alias, "sql_structure");
    if (occurrence) {
      occurrences.push(occurrence);
      aliasCanonical = occurrence.canonical;
    }
    used.add(index);
    index += 1;
  } else {
    aliasCanonical = canonicalValue(EntityType.Alias, tablePart.token.value, tablePart.token.kind === "quoted_identifier");
  }
  return aliasCanonical
    ? { nextIndex: index, occurrenceIndexes: used, aliasCanonical }
    : { nextIndex: index, occurrenceIndexes: used };
}

export function parseSql(source: string): ParsedArtifact {
  const artifact = emptyParsedArtifact(ArtifactKind.Sql, source);
  artifact.extractedSql.push(source);
  const tokens = tokenizeOracle(source);
  const assigned = new Set<number>();
  const aliases = new Set<string>();
  const cteCanonicals = new Set<string>();

  for (let index = 0; index < tokens.length; index += 1) {
    const current = tokens[index]!;
    const previous = tokens[index - 1];
    let occurrence: TextOccurrence | null = null;
    if (current.kind === "comment") {
      occurrence = makeOccurrence(
        source,
        current.contentStart,
        current.contentEnd,
        EntityType.Comment,
        "sql_comment",
        { quotingContext: "comment" }
      );
    } else if (current.kind === "hint") {
      for (const hintOccurrence of parseHintFragment(
        current.value,
        current.contentStart,
        source
      )) {
        artifact.occurrences.push(hintOccurrence);
      }
    } else if (
      current.kind === "string" ||
      current.kind === "national_string" ||
      current.kind === "q_string"
    ) {
      const datetime = ["DATE", "TIMESTAMP", "INTERVAL"].includes(previous?.upper ?? "");
      const type = datetime ? EntityType.DatetimeLiteral : EntityType.StringLiteral;
      occurrence = makeOccurrence(
        source,
        current.contentStart,
        current.contentEnd,
        type,
        "sql_lexer",
        { canonical: canonicalValue(type, current.value), quotingContext: current.kind }
      );
    } else if (current.kind === "number") {
      occurrence = occurrenceForToken(source, current, EntityType.NumberLiteral, "sql_lexer");
    } else if (current.kind === "bind") {
      occurrence = occurrenceForToken(source, current, EntityType.Bind, "sql_lexer");
    }
    addOccurrence(artifact, occurrence);
  }

  for (let index = 0; index < tokens.length - 2; index += 1) {
    if (tokens[index]?.upper !== "WITH") continue;
    let cursor = index + 1;
    while (cursor < tokens.length) {
      const name = tokens[cursor];
      if (!isIdentifierToken(name)) break;
      const afterName = tokens[cursor + 1]?.upper === "AS" ? cursor + 2 : cursor + 1;
      if (tokens[afterName]?.raw !== "(") break;
      const occurrence = occurrenceForToken(source, name, EntityType.Alias, "sql_cte");
      if (occurrence) {
        artifact.occurrences.push(occurrence);
        cteCanonicals.add(occurrence.canonical);
      }
      assigned.add(cursor);
      let depth = 0;
      cursor = afterName;
      while (cursor < tokens.length) {
        if (tokens[cursor]?.raw === "(") depth += 1;
        if (tokens[cursor]?.raw === ")") {
          depth -= 1;
          if (depth === 0) {
            cursor += 1;
            break;
          }
        }
        cursor += 1;
      }
      if (tokens[cursor]?.raw !== ",") break;
      cursor += 1;
    }
  }

  let inFrom = false;
  let depth = 0;
  for (let index = 0; index < tokens.length; index += 1) {
    const current = tokens[index]!;
    if (current.raw === "(") depth += 1;
    if (current.raw === ")") depth = Math.max(0, depth - 1);
    if (current.upper === "FROM" && current.kind === "keyword") inFrom = true;
    if (depth === 0 && FROM_TERMINATORS.has(current.upper)) inFrom = false;

    const startsObject =
      TABLE_INTRODUCERS.has(current.upper) || (inFrom && depth === 0 && current.raw === ",");
    if (!startsObject) continue;
    const parsed = parseTableObject(
      source,
      tokens,
      index + 1,
      artifact.occurrences,
      cteCanonicals
    );
    for (const usedIndex of parsed.occurrenceIndexes) assigned.add(usedIndex);
    if (parsed.aliasCanonical) aliases.add(parsed.aliasCanonical);
  }

  for (let index = 0; index < tokens.length; index += 1) {
    if (assigned.has(index)) continue;
    const current = tokens[index]!;
    if (!isIdentifierToken(current)) continue;
    const previous = tokens[index - 1];
    const next = tokens[index + 1];
    let type: EntityType | null = null;
    const aliasCanonical = canonicalValue(
      EntityType.Alias,
      current.value,
      current.kind === "quoted_identifier"
    );
    if (QUERY_BLOCK_RE.test(current.value)) type = EntityType.QueryBlock;
    else if (previous?.raw === "@") type = EntityType.DbLink;
    else if (next?.raw === ".") type = aliases.has(aliasCanonical) ? EntityType.Alias : EntityType.Schema;
    else if (previous?.raw === ".") type = EntityType.Column;
    else if (previous?.upper === "AS") type = EntityType.Alias;
    else if (next?.raw === "(") {
      if (!KNOWN_FUNCTIONS.has(current.upper)) type = EntityType.Function;
    } else if (!SPECIAL_IDENTIFIERS.has(current.upper)) type = EntityType.Column;
    if (type) {
      const occurrence = occurrenceForToken(source, current, type, "sql_shallow");
      if (occurrence) artifact.occurrences.push(occurrence);
    }
  }

  const semantic = new Map<string, Set<EntityType>>();
  for (const item of artifact.occurrences) {
    if (item.entityType !== EntityType.Identifier) {
      const choices = semantic.get(item.canonical) ?? new Set<EntityType>();
      choices.add(item.entityType);
      semantic.set(item.canonical, choices);
    }
  }
  artifact.occurrences = artifact.occurrences.map((item) => {
    const choices = semantic.get(item.canonical);
    return item.entityType === EntityType.Identifier && choices?.size === 1
      ? retagOccurrence(item, [...choices][0]!)
      : item;
  });
  return finalizeArtifact(artifact);
}

import type { OracleToken, OracleTokenKind } from "./types";

export const SQL_KEYWORDS = new Set(
  [
    "ACCESS",
    "ADD",
    "ALL",
    "ALTER",
    "AND",
    "ANY",
    "AS",
    "ASC",
    "BETWEEN",
    "BY",
    "CASE",
    "CALL",
    "CAST",
    "CHECK",
    "CONNECT",
    "CONSTRAINT",
    "CREATE",
    "CROSS",
    "CURRENT",
    "DATE",
    "DECLARE",
    "DEFAULT",
    "DELETE",
    "DESC",
    "DISTINCT",
    "DROP",
    "ELSE",
    "END",
    "ESCAPE",
    "EXCEPT",
    "EXISTS",
    "EXPLAIN",
    "FETCH",
    "FIRST",
    "FOLLOWING",
    "FOR",
    "FROM",
    "FULL",
    "GRANT",
    "GROUP",
    "HAVING",
    "IN",
    "INDEX",
    "INNER",
    "INSERT",
    "INTERSECT",
    "INTERVAL",
    "INTO",
    "IS",
    "JOIN",
    "KEEP",
    "LEFT",
    "LIKE",
    "LOCK",
    "MATCHED",
    "MERGE",
    "MINUS",
    "MODEL",
    "NATURAL",
    "NO",
    "NOT",
    "NULL",
    "NULLS",
    "OF",
    "ON",
    "ONLY",
    "OR",
    "ORDER",
    "OUTER",
    "OVER",
    "PARTITION",
    "PIVOT",
    "PRECEDING",
    "PRIOR",
    "PROCEDURE",
    "RANGE",
    "RECURSIVE",
    "RETURNING",
    "REVOKE",
    "RIGHT",
    "ROW",
    "ROWS",
    "SELECT",
    "SET",
    "START",
    "TABLE",
    "THEN",
    "TIMESTAMP",
    "TO",
    "TRIGGER",
    "TRUNCATE",
    "UNBOUNDED",
    "UNION",
    "UNIQUE",
    "UNPIVOT",
    "UPDATE",
    "USING",
    "VALUES",
    "VIEW",
    "WHEN",
    "WHERE",
    "WINDOW",
    "WITH"
  ].map((item) => item.toUpperCase())
);

export const KNOWN_FUNCTIONS = new Set(
  [
    "ABS",
    "ADD_MONTHS",
    "AVG",
    "BITAND",
    "CAST",
    "CEIL",
    "COALESCE",
    "COUNT",
    "CURRENT_DATE",
    "CURRENT_TIMESTAMP",
    "DECODE",
    "DENSE_RANK",
    "EXTRACT",
    "FLOOR",
    "GREATEST",
    "JSON_EXISTS",
    "JSON_QUERY",
    "JSON_TABLE",
    "JSON_VALUE",
    "LAG",
    "LEAD",
    "LEAST",
    "LISTAGG",
    "LOWER",
    "MAX",
    "MIN",
    "MOD",
    "MONTHS_BETWEEN",
    "NVL",
    "NVL2",
    "ORA_HASH",
    "RANK",
    "REGEXP_LIKE",
    "REGEXP_REPLACE",
    "REGEXP_SUBSTR",
    "ROUND",
    "ROW_NUMBER",
    "RTRIM",
    "SUBSTR",
    "SUM",
    "SYS_CONTEXT",
    "TO_CHAR",
    "TO_DATE",
    "TO_NUMBER",
    "TO_TIMESTAMP",
    "TRIM",
    "TRUNC",
    "UPPER"
  ].map((item) => item.toUpperCase())
);

export const SPECIAL_IDENTIFIERS = new Set([
  "ROWID",
  "ROWNUM",
  "LEVEL",
  "SYSDATE",
  "SYSTIMESTAMP",
  "TRUE",
  "FALSE"
]);

function isWordStart(character: string): boolean {
  return /[\p{L}_#$]/u.test(character);
}

function isWordPart(character: string): boolean {
  return /[\p{L}\p{N}_#$]/u.test(character);
}

function token(
  kind: OracleTokenKind,
  source: string,
  start: number,
  end: number,
  value: string,
  contentStart = start,
  contentEnd = end
): OracleToken {
  return {
    kind,
    start,
    end,
    raw: source.slice(start, end),
    value,
    upper: value.toUpperCase(),
    contentStart,
    contentEnd
  };
}

function consumeQuoted(
  source: string,
  start: number,
  quote: "'" | '"'
): number {
  let index = start + 1;
  while (index < source.length) {
    if (source[index] === quote) {
      if (source[index + 1] === quote) {
        index += 2;
        continue;
      }
      return index + 1;
    }
    index += 1;
  }
  return source.length;
}

function consumeNumber(source: string, start: number): number {
  let index = start;
  while (index < source.length && /\d/.test(source[index] ?? "")) index += 1;
  if (source[index] === "." && /\d/.test(source[index + 1] ?? "")) {
    index += 1;
    while (index < source.length && /\d/.test(source[index] ?? "")) index += 1;
  }
  if (/[eE]/.test(source[index] ?? "")) {
    const exponentStart = index;
    index += 1;
    if (/[+-]/.test(source[index] ?? "")) index += 1;
    const digitsStart = index;
    while (index < source.length && /\d/.test(source[index] ?? "")) index += 1;
    if (digitsStart === index) return exponentStart;
  }
  return index;
}

export function tokenizeOracle(source: string): OracleToken[] {
  const tokens: OracleToken[] = [];
  const pairs: Record<string, string> = { "[": "]", "{": "}", "(": ")", "<": ">" };
  const multiOperators = ["(+)", ">=", "<=", "<>", "!=", "^=", "~=", "||", ":=", "=>", ".."];
  let index = 0;

  while (index < source.length) {
    const character = source[index] ?? "";
    if (/\s/.test(character)) {
      index += 1;
      continue;
    }

    if (source.startsWith("--", index)) {
      const start = index;
      index += 2;
      const contentStart = index;
      while (index < source.length && !/[\r\n]/.test(source[index] ?? "")) index += 1;
      tokens.push(token("comment", source, start, index, source.slice(contentStart, index), contentStart, index));
      continue;
    }

    if (source.startsWith("/*", index)) {
      const start = index;
      const isHint = source.startsWith("/*+", index);
      index += isHint ? 3 : 2;
      const contentStart = index;
      const closing = source.indexOf("*/", index);
      const contentEnd = closing < 0 ? source.length : closing;
      index = closing < 0 ? source.length : closing + 2;
      tokens.push(
        token(
          isHint ? "hint" : "comment",
          source,
          start,
          index,
          source.slice(contentStart, contentEnd),
          contentStart,
          contentEnd
        )
      );
      continue;
    }

    const qPrefixLength =
      (character === "q" || character === "Q") && source[index + 1] === "'"
        ? 2
        : (character === "n" || character === "N") &&
            (source[index + 1] === "q" || source[index + 1] === "Q") &&
            source[index + 2] === "'"
          ? 3
          : 0;
    const qDelimiter = qPrefixLength > 0 ? source[index + qPrefixLength] ?? "" : "";
    if (qPrefixLength > 0 && qDelimiter && !/[\s\p{L}\p{N}']/u.test(qDelimiter)) {
      const start = index;
      const closer = pairs[qDelimiter] ?? qDelimiter;
      const contentStart = index + qPrefixLength + 1;
      const closing = source.indexOf(`${closer}'`, contentStart);
      const contentEnd = closing < 0 ? source.length : closing;
      index = closing < 0 ? source.length : closing + 2;
      tokens.push(
        token(
          "q_string",
          source,
          start,
          index,
          source.slice(contentStart, contentEnd),
          contentStart,
          contentEnd
        )
      );
      continue;
    }

    if ((character === "n" || character === "N") && source[index + 1] === "'") {
      const start = index;
      const quoteStart = index + 1;
      index = consumeQuoted(source, quoteStart, "'");
      const contentStart = quoteStart + 1;
      const contentEnd = source[index - 1] === "'" ? index - 1 : index;
      tokens.push(
        token(
          "national_string",
          source,
          start,
          index,
          source.slice(contentStart, contentEnd),
          contentStart,
          contentEnd
        )
      );
      continue;
    }

    if (character === "'") {
      const start = index;
      index = consumeQuoted(source, start, "'");
      const contentStart = start + 1;
      const contentEnd = source[index - 1] === "'" ? index - 1 : index;
      tokens.push(
        token(
          "string",
          source,
          start,
          index,
          source.slice(contentStart, contentEnd),
          contentStart,
          contentEnd
        )
      );
      continue;
    }

    if (character === '"') {
      const start = index;
      index = consumeQuoted(source, start, '"');
      const contentStart = start + 1;
      const contentEnd = source[index - 1] === '"' ? index - 1 : index;
      tokens.push(
        token(
          "quoted_identifier",
          source,
          start,
          index,
          source.slice(contentStart, contentEnd),
          contentStart,
          contentEnd
        )
      );
      continue;
    }

    if (character === ":" && /[\p{L}\p{N}_#$]/u.test(source[index + 1] ?? "")) {
      const start = index;
      index += 1;
      const contentStart = index;
      while (index < source.length && isWordPart(source[index] ?? "")) index += 1;
      tokens.push(
        token(
          "bind",
          source,
          start,
          index,
          source.slice(contentStart, index),
          contentStart,
          index
        )
      );
      continue;
    }

    if (/\d/.test(character)) {
      const start = index;
      index = consumeNumber(source, start);
      tokens.push(token("number", source, start, index, source.slice(start, index)));
      continue;
    }

    if (isWordStart(character)) {
      const start = index;
      index += 1;
      while (index < source.length && isWordPart(source[index] ?? "")) index += 1;
      const value = source.slice(start, index);
      const upper = value.toUpperCase();
      tokens.push(token(SQL_KEYWORDS.has(upper) ? "keyword" : "identifier", source, start, index, value));
      continue;
    }

    const multi = multiOperators.find((value) => source.startsWith(value, index));
    if (multi) {
      tokens.push(token("operator", source, index, index + multi.length, multi));
      index += multi.length;
      continue;
    }

    const kind: OracleTokenKind = ".,;()[]@".includes(character)
      ? "punctuation"
      : "operator";
    tokens.push(token(kind, source, index, index + 1, character));
    index += 1;
  }
  return tokens;
}

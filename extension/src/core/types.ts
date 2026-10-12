export enum ArtifactKind {
  Sql = "sql",
  Xplan = "xplan",
  SqlMonitor = "sql_monitor",
  Outline = "outline",
  Predicates = "predicates",
  Unknown = "unknown"
}

export enum EntityType {
  Schema = "schema",
  Table = "table",
  View = "view",
  Column = "column",
  Index = "index",
  Constraint = "constraint",
  Partition = "partition",
  Alias = "alias",
  QueryBlock = "query_block",
  DbLink = "db_link",
  User = "user",
  Database = "database",
  Service = "service",
  Host = "host",
  Program = "program",
  Module = "module",
  Action = "action",
  SqlId = "sql_id",
  PlanHash = "plan_hash",
  ExecutionId = "execution_id",
  SessionId = "session_id",
  ObjectId = "object_id",
  Bind = "bind",
  StringLiteral = "string_literal",
  NumberLiteral = "number_literal",
  DatetimeLiteral = "datetime_literal",
  Comment = "comment",
  Function = "function",
  Identifier = "identifier",
  Fragment = "fragment"
}

export const ENTITY_CODES: Readonly<Record<EntityType, string>> = {
  [EntityType.Schema]: "SCH",
  [EntityType.Table]: "TBL",
  [EntityType.View]: "VIW",
  [EntityType.Column]: "COL",
  [EntityType.Index]: "IDX",
  [EntityType.Constraint]: "CST",
  [EntityType.Partition]: "PRT",
  [EntityType.Alias]: "ALS",
  [EntityType.QueryBlock]: "QB",
  [EntityType.DbLink]: "DBL",
  [EntityType.User]: "USR",
  [EntityType.Database]: "DB",
  [EntityType.Service]: "SVC",
  [EntityType.Host]: "HST",
  [EntityType.Program]: "PRG",
  [EntityType.Module]: "MOD",
  [EntityType.Action]: "ACT",
  [EntityType.SqlId]: "SQLID",
  [EntityType.PlanHash]: "PHV",
  [EntityType.ExecutionId]: "EXE",
  [EntityType.SessionId]: "SID",
  [EntityType.ObjectId]: "OID",
  [EntityType.Bind]: "BND",
  [EntityType.StringLiteral]: "STR",
  [EntityType.NumberLiteral]: "NUM",
  [EntityType.DatetimeLiteral]: "DT",
  [EntityType.Comment]: "CMT",
  [EntityType.Function]: "FUN",
  [EntityType.Identifier]: "IDN",
  [EntityType.Fragment]: "FRG"
};

export const ENTITY_PRIORITY: Readonly<Record<EntityType, number>> = {
  [EntityType.StringLiteral]: 100,
  [EntityType.NumberLiteral]: 100,
  [EntityType.DatetimeLiteral]: 100,
  [EntityType.Comment]: 100,
  [EntityType.QueryBlock]: 95,
  [EntityType.SqlId]: 95,
  [EntityType.PlanHash]: 95,
  [EntityType.ExecutionId]: 95,
  [EntityType.SessionId]: 95,
  [EntityType.Bind]: 94,
  [EntityType.Schema]: 90,
  [EntityType.Table]: 85,
  [EntityType.View]: 85,
  [EntityType.Index]: 85,
  [EntityType.Column]: 80,
  [EntityType.Constraint]: 80,
  [EntityType.Partition]: 80,
  [EntityType.DbLink]: 80,
  [EntityType.Alias]: 75,
  [EntityType.User]: 75,
  [EntityType.Database]: 75,
  [EntityType.Service]: 75,
  [EntityType.Host]: 75,
  [EntityType.Program]: 75,
  [EntityType.Module]: 75,
  [EntityType.Action]: 75,
  [EntityType.ObjectId]: 75,
  [EntityType.Function]: 60,
  [EntityType.Identifier]: 50,
  [EntityType.Fragment]: 40
};

export interface TextOccurrence {
  start: number;
  end: number;
  entityType: EntityType;
  original: string;
  canonical: string;
  provenance: string;
  confidence: number;
  quotingContext: string;
  priority: number;
}

export interface ParseFinding {
  severity: "info" | "warning" | "error";
  code: string;
  message: string;
  start?: number;
  end?: number;
}

export interface ParsedArtifact {
  kind: ArtifactKind;
  sourceText: string;
  occurrences: TextOccurrence[];
  findings: ParseFinding[];
  extractedSql: string[];
  metadata: Record<string, unknown>;
}

export type OracleTokenKind =
  | "keyword"
  | "identifier"
  | "quoted_identifier"
  | "string"
  | "national_string"
  | "q_string"
  | "number"
  | "bind"
  | "comment"
  | "hint"
  | "operator"
  | "punctuation";

export interface OracleToken {
  kind: OracleTokenKind;
  start: number;
  end: number;
  raw: string;
  value: string;
  upper: string;
  contentStart: number;
  contentEnd: number;
}

export interface SymbolRecord {
  entityType: EntityType;
  canonical: string;
  preferredOriginal: string;
  marker: string;
  sequence: number;
}

export interface ConversationMapping {
  schemaVersion: number;
  site: string;
  conversationId: string;
  tokenPrefix: string;
  nextSequence: number;
  symbols: SymbolRecord[];
  updatedAt: string;
}

export interface AppliedReplacement {
  occurrence: TextOccurrence;
  symbol: SymbolRecord;
  outputStart: number;
  outputEnd: number;
}

export interface ObfuscatedArtifact {
  kind: ArtifactKind;
  sourceText: string;
  obfuscatedText: string;
  replacements: AppliedReplacement[];
  findings: ParseFinding[];
}

export interface ObfuscationResult {
  text: string;
  artifact: ObfuscatedArtifact;
  mapping: ConversationMapping;
  replacementCount: number;
  validationStatus: "ok" | "warning" | "error";
}

export interface RestoreResult {
  text: string;
  unknownMarkers: string[];
  replacementCount: number;
}

export type SendGuardMode = "block" | "warn" | "off";

export interface ExtensionSettings {
  enabledChatGpt: boolean;
  enabledClaude: boolean;
  sendGuard: SendGuardMode;
  autoRestore: boolean;
  hideOriginal: boolean;
  restoreVisibleMessages: boolean;
}

export const DEFAULT_SETTINGS: ExtensionSettings = {
  enabledChatGpt: true,
  enabledClaude: true,
  sendGuard: "block",
  autoRestore: true,
  hideOriginal: true,
  restoreVisibleMessages: true
};

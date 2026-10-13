import { ArtifactKind } from "./types";

const SQL_START_RE = /^\s*(?:(?:\/\*[\s\S]*?\*\/|--[^\r\n]*(?:\r?\n|$))\s*)*(?:SELECT|WITH|INSERT|UPDATE|DELETE|MERGE|EXPLAIN|CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|COMMENT|CALL|BEGIN|DECLARE)\b/i;

export function detectArtifactKind(source: string): ArtifactKind {
  const text = source.trim();
  if (!text) return ArtifactKind.Unknown;
  if (
    /^\s*SQL Monitoring Report\s*$/im.test(source) ||
    (/^\s*Global Information\s*$/im.test(source) &&
      /^\s*SQL Plan Monitoring Details/im.test(source))
  ) {
    return ArtifactKind.SqlMonitor;
  }
  if (
    /^\s*Plan hash value\s*[:=]/im.test(source) ||
    (/^\s*\|\s*Id\s*\|\s*Operation\s*\|/im.test(source) &&
      /^\s*Predicate Information/im.test(source))
  ) {
    return ArtifactKind.Xplan;
  }
  if (/BEGIN_OUTLINE_DATA/i.test(source) || /^\s*Outline Data\s*$/im.test(source)) {
    return ArtifactKind.Outline;
  }
  if (
    /^\s*Predicate Information/im.test(source) ||
    /^\s*\d+\s*-\s*(?:access|filter)\(/im.test(source)
  ) {
    return ArtifactKind.Predicates;
  }
  if (SQL_START_RE.test(source)) return ArtifactKind.Sql;
  return ArtifactKind.Unknown;
}

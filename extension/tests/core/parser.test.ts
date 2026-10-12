import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { detectArtifactKind } from "../../src/core/detector";
import { parseArtifact, parsePrompt } from "../../src/core/parser";
import { ArtifactKind, EntityType } from "../../src/core/types";

function corpus(path: string): string {
  return readFileSync(resolve(import.meta.dirname, "../../../test-corpus", path), "utf8");
}

function values(source: string, type: EntityType): string[] {
  return parseArtifact(source).occurrences
    .filter((item) => item.entityType === type)
    .map((item) => item.original);
}

describe("artifact detector and parsers", () => {
  it.each([
    ["sql/query.sql", ArtifactKind.Sql],
    ["xplan/xplan_19c.txt", ArtifactKind.Xplan],
    ["sql-monitor/sql_monitor_19c.txt", ArtifactKind.SqlMonitor],
    ["outline/outline.txt", ArtifactKind.Outline],
    ["predicates/predicates.txt", ArtifactKind.Predicates]
  ])("detects %s", (path, expected) => {
    expect(detectArtifactKind(corpus(path))).toBe(expected);
  });

  it("classifies schemas, tables, aliases, columns, literals, bind and link in SQL", () => {
    const source = corpus("sql/oracle-edge.sql");
    const artifact = parseArtifact(source);
    expect(artifact.kind).toBe(ArtifactKind.Sql);
    expect(values(source, EntityType.Schema)).toEqual(expect.arrayContaining(["Sales", "crm"]));
    expect(values(source, EntityType.Table)).toEqual(expect.arrayContaining(["Orders", "customers"]));
    expect(values(source, EntityType.DbLink)).toContain("prod_link");
    expect(values(source, EntityType.Bind)).toContain("region");
    expect(values(source, EntityType.StringLiteral)).toEqual(
      expect.arrayContaining(["private status", "Конфиденциально"])
    );
    expect(values(source, EntityType.DatetimeLiteral)).toContain("2026-01-01 00:00:00");
    expect(artifact.occurrences.every((item) => source.slice(item.start, item.end) === item.original)).toBe(true);
  });

  it("parses DBMS_XPLAN while preserving performance metrics", () => {
    const source = corpus("xplan/xplan_19c.txt");
    const artifact = parseArtifact(source);
    const originalValues = artifact.occurrences.map((item) => item.original);
    expect(originalValues).toEqual(
      expect.arrayContaining(["7ab12cd34ef56", "3829104756", "ORDERS", "IX_ORDERS_STATUS", "REGION", "SIBERIA", "SALES_PROFILE"])
    );
    expect(artifact.occurrences.some((item) => item.entityType === EntityType.QueryBlock)).toBe(true);
    expect(originalValues).not.toContain("00:00:12");
    expect(originalValues).not.toContain("87500");
    expect(artifact.extractedSql).toHaveLength(1);
  });

  it("parses SQL Monitor identity fields and leaves metrics untouched", () => {
    const source = corpus("sql-monitor/sql_monitor_19c.txt");
    const artifact = parseArtifact(source);
    const originalValues = artifact.occurrences.map((item) => item.original);
    expect(originalValues).toEqual(
      expect.arrayContaining([
        "REPORT_USER (52:61539)",
        "7ab12cd34ef56",
        "16777216",
        "report-worker@prod-db-01/monthly-close",
        "sales_prod",
        "ORDERS",
        "IX_ORDERS_STATUS",
        "REGION",
        "SIBERIA"
      ])
    );
    expect(originalValues).not.toContain("12.01");
    expect(originalValues).not.toContain("98765");
    expect(artifact.findings.filter((item) => item.severity === "error")).toHaveLength(0);
  });

  it("masks unknown SQL Monitor sections conservatively", () => {
    const source = `${corpus("sql-monitor/sql_monitor_19c.txt")}\nPrivate Extension\n-----\nsecret payload\n`;
    const artifact = parseArtifact(source);
    expect(artifact.occurrences.some((item) => item.original === "secret payload" && item.entityType === EntityType.Fragment)).toBe(true);
    expect(artifact.findings.some((item) => item.code === "sql_monitor_unknown_section_masked")).toBe(true);
  });

  it("keeps prose and parses supported fenced artifacts", () => {
    const source = `Проверь запрос, метку prose не трогай:\n\n\`\`\`sql\n${corpus("sql/query.sql")}\n\`\`\``;
    const artifact = parsePrompt(source);
    expect(artifact.occurrences.length).toBeGreaterThan(8);
    expect(artifact.occurrences.some((item) => item.original.includes("Проверь"))).toBe(false);
  });

  it("parses an inline SQL statement without masking surrounding prose", () => {
    const source = "Посмотри: SELECT s.secret_col FROM app.secret_table s; Потом объясни план.";
    const artifact = parsePrompt(source);
    expect(artifact.occurrences.map((item) => item.original)).toEqual(
      expect.arrayContaining(["secret_col", "app", "secret_table", "s"])
    );
    expect(artifact.occurrences.some((item) => item.original.includes("Потом"))).toBe(false);
  });

  it("fails closed for an explicitly parsed unknown artifact", () => {
    const artifact = parseArtifact("opaque secret format");
    expect(artifact.occurrences).toHaveLength(1);
    expect(artifact.occurrences[0]?.entityType).toBe(EntityType.Fragment);
  });

  it("fails closed for an unrecognized Oracle-like prompt", () => {
    const source = "DBMS_XPLAN private proprietary layout customer_schema.secret_object";
    const artifact = parsePrompt(source);
    expect(artifact.occurrences).toHaveLength(1);
    expect(artifact.occurrences[0]?.original).toBe(source);
    expect(artifact.findings.some((item) => item.code === "oracle_artifact_conservative_fallback")).toBe(true);
  });
});

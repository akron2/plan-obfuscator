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
    ["xplan/xplan_parallel_synthetic.txt", ArtifactKind.Xplan],
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

  it("parses parallel-plan table queues without masking PX metrics", () => {
    const source = corpus("xplan/xplan_parallel_synthetic.txt");
    const artifact = parseArtifact(source);
    const originals = artifact.occurrences.map((item) => item.original);
    expect(originals).toEqual(expect.arrayContaining([
      "p4r4ll3l00001",
      "7654321098",
      "EMP_PRIVATE",
      "DEPT_PRIVATE",
      "TQ10001",
      "TQ10000",
      "SEL$PARALLEL_PRIVATE"
    ]));
    expect(originals).not.toContain("Q1,01");
    expect(originals).not.toContain("P->S");
    expect(originals).not.toContain("PCWP");
    expect(originals).not.toContain("445");
  });

  it("extracts wrapped SQL from the EXPLAINED SQL STATEMENT header", () => {
    const source = `EXPLAINED SQL STATEMENT:\n` +
      `------------------------\n` +
      `select /*+ INDEX(o IX_PRIVATE) */ o.SECRET_COLUMN from LAB_SCHEMA.PRIVATE_TABLE o\n` +
      `where o.STATUS_SECRET = 'PRIVATE_VALUE'\n\n` +
      `Plan hash value: 1234567890\n\n` +
      `----------------------------------------------------------\n` +
      `| Id  | Operation                   | Name          |\n` +
      `----------------------------------------------------------\n` +
      `|   0 | SELECT STATEMENT            |               |\n` +
      `|   1 |  TABLE ACCESS BY INDEX ROWID| PRIVATE_TABLE |\n` +
      `----------------------------------------------------------\n`;
    const artifact = parseArtifact(source);
    const originals = artifact.occurrences.map((item) => item.original);
    expect(artifact.extractedSql).toHaveLength(1);
    expect(originals).toEqual(expect.arrayContaining([
      "IX_PRIVATE",
      "SECRET_COLUMN",
      "LAB_SCHEMA",
      "PRIVATE_TABLE",
      "STATUS_SECRET",
      "PRIVATE_VALUE"
    ]));
  });

  it("masks peeked bind values even when Oracle omits the bind name", () => {
    const source = `Plan hash value: 1234567890\n\n` +
      `------------------------------------------------\n` +
      `| Id  | Operation        | Name          |\n` +
      `------------------------------------------------\n` +
      `|   0 | SELECT STATEMENT |               |\n` +
      `|   1 | TABLE ACCESS FULL| PRIVATE_TABLE |\n` +
      `------------------------------------------------\n\n` +
      `Peeked Binds (identified by position):\n` +
      `--------------------------------------\n\n` +
      `   1 - (VARCHAR2(30), CSID=873): 'BIND_VALUE_PRIVATE'\n`;
    const artifact = parseArtifact(source);
    expect(artifact.occurrences.some((item) =>
      item.original === "BIND_VALUE_PRIVATE" && item.entityType === EntityType.StringLiteral
    )).toBe(true);
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

  it("masks bind names and values from SQL Monitor bind tables", () => {
    const source = `SQL Monitoring Report\n\n` +
      `Binds\n` +
      `==============================================================\n` +
      `|      Name       | Position |    Type     |       Value        |\n` +
      `==============================================================\n` +
      `| :REGION_PRIVATE |        1 | VARCHAR2(32)| SIBERIA_PRIVATE   |\n` +
      `==============================================================\n`;
    const artifact = parseArtifact(source);
    expect(artifact.occurrences.some((item) =>
      item.original === "REGION_PRIVATE" && item.entityType === EntityType.Bind
    )).toBe(true);
    expect(artifact.occurrences.some((item) =>
      item.original === "SIBERIA_PRIVATE" && item.entityType === EntityType.StringLiteral
    )).toBe(true);
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

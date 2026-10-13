import { describe, expect, it } from "vitest";
import {
  createConversationMapping,
  markerHasValidChecksum,
  validateMapping
} from "../../src/core/symbol-registry";
import { obfuscatePrompt, restoreText } from "../../src/core/transform";
import { corpusCase } from "../helpers/corpus";

function corpus(path: string): string {
  return corpusCase(`canonical/${path}`).source;
}

describe("reversible transformation", () => {
  it("reuses symbols within one conversation and separates conversations", () => {
    const source = "select o.order_id from sales.orders o where o.status = 'OPEN'";
    const mapping = createConversationMapping("chatgpt", "one", "AAA111");
    const first = obfuscatePrompt(source, mapping);
    const second = obfuscatePrompt(source, mapping);
    expect(second.text).toBe(first.text);
    expect(second.mapping.symbols).toHaveLength(first.mapping.symbols.length);

    const other = obfuscatePrompt(
      source,
      createConversationMapping("chatgpt", "two", "BBB222")
    );
    expect(other.text).not.toBe(first.text);
  });

  it("restores model output case-insensitively and reports damaged or unknown markers", () => {
    const mapping = createConversationMapping("claude", "one", "ABC123");
    const result = obfuscatePrompt("select secret_col from secret_table", mapping);
    const known = result.artifact.replacements[0]!.symbol.marker;
    const unknown = known.replace("ABC123", "ZZZ999");
    const damaged = "OBF_ABC123 COL BROKEN";
    const restored = restoreText(`Use ${known.toLowerCase()} but keep ${unknown} and ${damaged}.`, mapping);
    expect(restored.text).toContain(result.artifact.replacements[0]!.symbol.preferredOriginal);
    expect(restored.text).toContain(unknown);
    expect(restored.unknownMarkers).toContain(unknown);
    expect(restored.unknownMarkers).toContain("OBF_ABC123");
    expect(restored.replacementCount).toBe(1);
  });

  it("does not alter ordinary prose without an Oracle artifact", () => {
    const source = "Объясни, почему этот план может быть медленным.";
    const result = obfuscatePrompt(
      source,
      createConversationMapping("chatgpt", "plain", "ABC123")
    );
    expect(result.text).toBe(source);
    expect(result.replacementCount).toBe(0);
  });

  it("preserves performance metrics while replacing identity values", () => {
    const xplan = obfuscatePrompt(
      corpus("xplan/xplan_19c.txt"),
      createConversationMapping("test", "metrics-xplan", "MET001")
    );
    expect(xplan.text).toContain("00:00:12");
    expect(xplan.text).toContain("87500");
    expect(xplan.text).not.toContain("3829104756");
    expect(xplan.text).not.toContain("IX_ORDERS_STATUS");

    const monitor = obfuscatePrompt(
      corpus("sql-monitor/sql_monitor_19c.txt"),
      createConversationMapping("test", "metrics-monitor", "MET002")
    );
    expect(monitor.text).toContain("12.01");
    expect(monitor.text).toContain("98765");
    expect(monitor.text).not.toContain("REPORT_USER");
    expect(monitor.text).not.toContain("sales_prod");
  });

  it("validates persisted mapping structure and marker checksums", () => {
    const mapping = createConversationMapping("chatgpt", "validated", "ABC123");
    const result = obfuscatePrompt("select secret_col from secret_table", mapping);
    validateMapping(mapping);
    expect(result.artifact.replacements.every((item) => markerHasValidChecksum(item.symbol.marker))).toBe(true);
    expect(createConversationMapping("chatgpt", "random").tokenPrefix).toMatch(/^[A-Z0-9]{4,12}$/);

    expect(() => validateMapping({ ...mapping, schemaVersion: 999 })).toThrow("Unsupported mapping schema");
    expect(() => validateMapping({ ...mapping, tokenPrefix: "bad-prefix" })).toThrow("Invalid mapping token prefix");
    expect(() => validateMapping({
      ...mapping,
      symbols: [...mapping.symbols, { ...mapping.symbols[0]! }]
    })).toThrow("Duplicate marker");
  });
});

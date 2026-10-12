import { describe, expect, it } from "vitest";
import { tokenizeOracle } from "../../src/core/oracle-lexer";

describe("Oracle lexer", () => {
  it("keeps exact offsets for Oracle-specific token forms", () => {
    const source = `select /*+ INDEX(t \"IX T\") */ t.\"Mixed Name\", :bind,\n` +
      `q'[private '' value]', q'!bang!', nq'{юникод}', N'секрет' -- note\n` +
      `from app.orders@prod_link t where t.id = 1.25e+3 and t.legacy_id = x.id(+)`;
    const tokens = tokenizeOracle(source);
    for (const token of tokens) {
      expect(source.slice(token.start, token.end)).toBe(token.raw);
      expect(token.start).toBeLessThan(token.end);
    }
    expect(tokens.filter((token) => token.kind === "q_string")).toHaveLength(3);
    expect(tokens.some((token) => token.kind === "national_string" && token.value === "секрет")).toBe(true);
    expect(tokens.some((token) => token.kind === "bind" && token.value === "bind")).toBe(true);
    expect(tokens.some((token) => token.raw === "(+)" && token.kind === "operator")).toBe(true);
    expect(tokens.some((token) => token.kind === "number" && token.raw === "1.25e+3")).toBe(true);
  });

  it("does not swallow text after malformed literals", () => {
    const tokens = tokenizeOracle("select 'unterminated");
    expect(tokens.at(-1)?.kind).toBe("string");
    expect(tokens.at(-1)?.end).toBe("select 'unterminated".length);
  });
});

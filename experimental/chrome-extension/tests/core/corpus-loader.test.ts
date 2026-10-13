import {
  cpSync,
  mkdirSync,
  mkdtempSync,
  rmSync,
  writeFileSync
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { corpusRoot, loadCorpusFrom } from "../helpers/corpus";

const temporaryRoots: string[] = [];

function temporaryCorpus(): string {
  const root = mkdtempSync(join(tmpdir(), "plan-obfuscator-corpus-"));
  temporaryRoots.push(root);
  cpSync(resolve(corpusRoot, "corpus.schema.json"), resolve(root, "corpus.schema.json"));
  return root;
}

function expectations(cases: unknown[] = []): object {
  return {
    $schema: "../corpus.schema.json",
    schemaVersion: 1,
    suite: "test-suite",
    source: "test fixture",
    cases
  };
}

afterEach(() => {
  for (const root of temporaryRoots.splice(0)) rmSync(root, { recursive: true, force: true });
});

describe("corpus loader validation", () => {
  it("automatically discovers an unregistered file and PRIVATE/SECRET sentinels", () => {
    const root = temporaryCorpus();
    mkdirSync(resolve(root, "user"));
    writeFileSync(
      resolve(root, "user", "query.sql"),
      "select COLUMN_PRIVATE from TABLE_SECRET",
      "utf8"
    );
    const cases = loadCorpusFrom(root);
    expect(cases).toHaveLength(1);
    expect(cases[0]).toMatchObject({
      id: "user/query.sql",
      registered: false,
      replace: ["COLUMN_PRIVATE", "TABLE_SECRET"]
    });
  });

  it("rejects unknown expectation fields", () => {
    const root = temporaryCorpus();
    mkdirSync(resolve(root, "suite"));
    writeFileSync(resolve(root, "suite", "case.sql"), "select VALUE_PRIVATE from dual", "utf8");
    writeFileSync(
      resolve(root, "suite", "expectations.json"),
      JSON.stringify({ ...expectations(), unexpected: true }),
      "utf8"
    );
    expect(() => loadCorpusFrom(root)).toThrow("must NOT have additional properties");
  });

  it("rejects an unregistered file without PRIVATE/SECRET test values", () => {
    const root = temporaryCorpus();
    mkdirSync(resolve(root, "user"));
    writeFileSync(resolve(root, "user", "weak.sql"), "select ordinary_column from ordinary_table", "utf8");
    expect(() => loadCorpusFrom(root)).toThrow(
      "must contain PRIVATE/SECRET values or be added to expectations.json"
    );
  });

  it("rejects missing files and paths escaping the suite", () => {
    const root = temporaryCorpus();
    mkdirSync(resolve(root, "suite"));
    const baseCase = {
      file: "missing.sql",
      kind: "sql",
      replace: [],
      keep: [],
      minOccurrences: 1,
      allowFindings: [],
      tags: []
    };
    writeFileSync(
      resolve(root, "suite", "expectations.json"),
      JSON.stringify(expectations([baseCase])),
      "utf8"
    );
    expect(() => loadCorpusFrom(root)).toThrow("does not exist");

    writeFileSync(
      resolve(root, "suite", "expectations.json"),
      JSON.stringify(expectations([{ ...baseCase, file: "../outside.sql" }])),
      "utf8"
    );
    expect(() => loadCorpusFrom(root)).toThrow("escapes its suite");
  });

  it("rejects duplicate suite names and duplicate registrations", () => {
    const root = temporaryCorpus();
    for (const directory of ["one", "two"]) {
      mkdirSync(resolve(root, directory));
      writeFileSync(
        resolve(root, directory, "expectations.json"),
        JSON.stringify(expectations()),
        "utf8"
      );
    }
    expect(() => loadCorpusFrom(root)).toThrow("Duplicate corpus suite");

    rmSync(resolve(root, "two"), { recursive: true, force: true });
    writeFileSync(resolve(root, "one", "case.sql"), "select VALUE_PRIVATE from dual", "utf8");
    const duplicated = {
      file: "case.sql",
      kind: "sql",
      replace: [],
      keep: [],
      minOccurrences: 1,
      allowFindings: [],
      tags: []
    };
    writeFileSync(
      resolve(root, "one", "expectations.json"),
      JSON.stringify(expectations([duplicated, duplicated])),
      "utf8"
    );
    expect(() => loadCorpusFrom(root)).toThrow("registered more than once");
  });

  it("rejects invalid UTF-8 corpus files", () => {
    const root = temporaryCorpus();
    mkdirSync(resolve(root, "user"));
    writeFileSync(resolve(root, "user", "broken.sql"), Buffer.from([0xff, 0xfe, 0xfd]));
    expect(() => loadCorpusFrom(root)).toThrow("not valid UTF-8");
  });
});

import { readFileSync, readdirSync, statSync } from "node:fs";
import { isAbsolute, relative, resolve, sep } from "node:path";
import Ajv2020 from "ajv/dist/2020.js";
import type { ArtifactKind } from "../../src/core/types";

const SUPPORTED_EXTENSIONS = new Set([".sql", ".txt", ".log", ".out"]);
const MAX_FILE_BYTES = 10 * 1024 * 1024;

interface ExpectedCase {
  file: string;
  kind: `${ArtifactKind}`;
  replace: string[];
  keep: string[];
  minOccurrences: number;
  allowFindings: string[];
  tags: string[];
}

interface ExpectationsFile {
  $schema?: string;
  schemaVersion: 1;
  suite: string;
  source: string;
  cases: ExpectedCase[];
}

interface RegisteredExpectation {
  suite: string;
  suiteDirectory: string;
  expected: ExpectedCase;
}

export interface CorpusCase {
  id: string;
  suite: string;
  relativePath: string;
  absolutePath: string;
  source: string;
  expectedKind?: `${ArtifactKind}`;
  replace: string[];
  keep: string[];
  minOccurrences: number;
  allowFindings: string[];
  tags: string[];
  registered: boolean;
}

export const corpusRoot = resolve(import.meta.dirname, "../../../test-corpus");

function normalizedPath(path: string): string {
  return path.replaceAll("\\", "/");
}

function extensionOf(path: string): string {
  const match = path.match(/\.[^.\\/]+$/);
  return match?.[0].toLowerCase() ?? "";
}

function walkFiles(directory: string): string[] {
  const result: string[] = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })
    .sort((left, right) => left.name.localeCompare(right.name))) {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) result.push(...walkFiles(path));
    else if (entry.isFile()) result.push(path);
  }
  return result;
}

function staysInside(parent: string, child: string): boolean {
  const path = relative(parent, child);
  return path !== "" && !path.startsWith(`..${sep}`) && path !== ".." && !isAbsolute(path);
}

function decodeUtf8(root: string, path: string): string {
  const size = statSync(path).size;
  if (size > MAX_FILE_BYTES) {
    throw new Error(`Corpus file exceeds 10 MiB: ${normalizedPath(relative(root, path))}`);
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(readFileSync(path));
  } catch (error) {
    throw new Error(
      `Corpus file is not valid UTF-8: ${normalizedPath(relative(root, path))}`,
      { cause: error }
    );
  }
}

function sentinelValues(source: string): string[] {
  return [...new Set(
    [...source.matchAll(/[\p{L}\p{N}_$#]*(?:PRIVATE|SECRET)[\p{L}\p{N}_$#]*/giu)]
      .map((match) => match[0])
      .filter(Boolean)
  )];
}

export function loadCorpusFrom(root: string): CorpusCase[] {
  const resolvedRoot = resolve(root);
  const schemaPath = resolve(resolvedRoot, "corpus.schema.json");
  const schema = JSON.parse(readFileSync(schemaPath, "utf8")) as object;
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  const validate = ajv.compile<ExpectationsFile>(schema);
  const allFiles = walkFiles(resolvedRoot);
  const expectationPaths = allFiles.filter((path) => path.endsWith(`${sep}expectations.json`));
  const expectationsByFile = new Map<string, RegisteredExpectation>();
  const suiteDirectories: Array<{ suite: string; directory: string }> = [];
  const suites = new Set<string>();

  for (const path of expectationPaths) {
    const raw = JSON.parse(readFileSync(path, "utf8")) as unknown;
    if (!validate(raw)) {
      const details = (validate.errors ?? [])
        .map((error) => `${error.instancePath || "/"} ${error.message ?? "is invalid"}`)
        .join("; ");
      throw new Error(`${normalizedPath(relative(resolvedRoot, path))}: ${details}`);
    }
    const expectations = raw;
    if (suites.has(expectations.suite)) {
      throw new Error(`Duplicate corpus suite: ${expectations.suite}`);
    }
    suites.add(expectations.suite);
    const suiteDirectory = resolve(path, "..");
    suiteDirectories.push({ suite: expectations.suite, directory: suiteDirectory });
    for (const expected of expectations.cases) {
      const absolutePath = resolve(suiteDirectory, expected.file);
      if (!staysInside(suiteDirectory, absolutePath)) {
        throw new Error(`${expectations.suite}: corpus path escapes its suite: ${expected.file}`);
      }
      if (!SUPPORTED_EXTENSIONS.has(extensionOf(absolutePath))) {
        throw new Error(`${expectations.suite}: unsupported corpus extension: ${expected.file}`);
      }
      if (expectationsByFile.has(absolutePath)) {
        throw new Error(`Corpus file is registered more than once: ${expected.file}`);
      }
      expectationsByFile.set(absolutePath, {
        suite: expectations.suite,
        suiteDirectory,
        expected
      });
    }
  }

  const dataFiles = allFiles.filter((path) => SUPPORTED_EXTENSIONS.has(extensionOf(path)));
  const dataFileSet = new Set(dataFiles);
  for (const [path, registration] of expectationsByFile) {
    if (!dataFileSet.has(path)) {
      throw new Error(`${registration.suite}: registered corpus file does not exist: ${registration.expected.file}`);
    }
  }

  suiteDirectories.sort((left, right) => right.directory.length - left.directory.length);
  const cases: CorpusCase[] = [];
  const ids = new Set<string>();
  for (const absolutePath of dataFiles) {
    const registration = expectationsByFile.get(absolutePath);
    const containingSuite = suiteDirectories.find(({ directory }) => staysInside(directory, absolutePath));
    const topLevel = normalizedPath(relative(resolvedRoot, absolutePath)).split("/")[0] ?? "corpus";
    const suite = registration?.suite ?? containingSuite?.suite ?? topLevel;
    const suiteDirectory = registration?.suiteDirectory ?? containingSuite?.directory ?? resolve(resolvedRoot, topLevel);
    const casePath = normalizedPath(relative(suiteDirectory, absolutePath));
    const id = `${suite}/${casePath}`;
    if (ids.has(id)) throw new Error(`Duplicate corpus case ID: ${id}`);
    ids.add(id);
    const source = decodeUtf8(resolvedRoot, absolutePath);
    const expected = registration?.expected;
    const automaticReplace = sentinelValues(source);
    if (!expected && automaticReplace.length === 0) {
      throw new Error(
        `${normalizedPath(relative(resolvedRoot, absolutePath))}: unregistered corpus file must contain PRIVATE/SECRET values or be added to expectations.json`
      );
    }
    cases.push({
      id,
      suite,
      relativePath: normalizedPath(relative(resolvedRoot, absolutePath)),
      absolutePath,
      source,
      ...(expected ? { expectedKind: expected.kind } : {}),
      replace: [...new Set([...(expected?.replace ?? []), ...automaticReplace])],
      keep: expected?.keep ?? [],
      minOccurrences: expected?.minOccurrences ?? 1,
      allowFindings: expected?.allowFindings ?? [],
      tags: expected?.tags ?? [],
      registered: Boolean(expected)
    });
  }
  if (cases.length === 0) throw new Error("Corpus contains no test files");
  return cases.sort((left, right) => left.id.localeCompare(right.id));
}

export const corpusCases = loadCorpusFrom(corpusRoot);

export function corpusCase(pathSuffix: string): CorpusCase {
  const normalizedSuffix = normalizedPath(pathSuffix);
  const matches = corpusCases.filter((item) => item.relativePath.endsWith(normalizedSuffix));
  if (matches.length !== 1) {
    throw new Error(`Expected one corpus case ending with ${normalizedSuffix}, found ${matches.length}`);
  }
  return matches[0]!;
}

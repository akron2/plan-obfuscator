import { existsSync } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(root, "dist");
const explicitIndex = process.argv.indexOf("--chrome");
const explicit = explicitIndex >= 0 ? process.argv[explicitIndex + 1] : undefined;
const candidates = [
  explicit,
  process.env.PROGRAMFILES ? join(process.env.PROGRAMFILES, "Google", "Chrome", "Application", "chrome.exe") : undefined,
  process.env["PROGRAMFILES(X86)"] ? join(process.env["PROGRAMFILES(X86)"], "Google", "Chrome", "Application", "chrome.exe") : undefined,
  process.env.LOCALAPPDATA ? join(process.env.LOCALAPPDATA, "Google", "Chrome", "Application", "chrome.exe") : undefined,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium"
].filter(Boolean);
const chrome = candidates.find((path) => existsSync(path));
if (!chrome) throw new Error("Google Chrome not found; pass --chrome <path>");
if (!existsSync(join(dist, "manifest.json"))) throw new Error("Run npm run build before Chrome smoke");

const profile = await mkdtemp(join(tmpdir(), "plan-obfuscator-chrome-"));
try {
  const result = spawnSync(chrome, [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--disable-default-apps",
    `--disable-extensions-except=${dist}`,
    `--load-extension=${dist}`,
    `--user-data-dir=${profile}`,
    "--enable-logging=stderr",
    "--log-level=1",
    "--dump-dom",
    "data:text/html,<title>plan-obfuscator-smoke</title><p>ok</p>"
  ], {
    encoding: "utf8",
    timeout: 30_000,
    maxBuffer: 8 * 1024 * 1024
  });
  const output = `${result.stdout ?? ""}\n${result.stderr ?? ""}`;
  const extensionErrors = output.match(/Failed to load extension|Manifest is not valid|Extension error|Uncaught|SyntaxError/gi) ?? [];
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`Chrome exited ${result.status}: ${result.stderr}`);
  if (!output.includes("plan-obfuscator-smoke")) throw new Error("Chrome did not load the smoke document");
  if (extensionErrors.length > 0) throw new Error(`Chrome reported extension errors: ${extensionErrors.join(", ")}`);
  console.log(JSON.stringify({ chrome, manifestLoaded: true, extensionErrors: 0 }, null, 2));
} finally {
  const resolvedProfile = resolve(profile);
  const resolvedTemp = resolve(tmpdir());
  if (!resolvedProfile.startsWith(`${resolvedTemp}\\`) && !resolvedProfile.startsWith(`${resolvedTemp}/`)) {
    throw new Error(`Refusing to remove unexpected profile path: ${resolvedProfile}`);
  }
  await rm(resolvedProfile, { recursive: true, force: true });
}

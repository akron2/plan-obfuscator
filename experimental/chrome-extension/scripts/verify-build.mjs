import { readFile, readdir } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(root, "dist");
const manifest = JSON.parse(await readFile(join(dist, "manifest.json"), "utf8"));

const required = new Set([
  manifest.background.service_worker,
  manifest.action.default_popup,
  ...manifest.content_scripts.flatMap((entry) => [...entry.js, ...(entry.css ?? [])]),
  ...manifest.web_accessible_resources.flatMap((entry) => entry.resources)
]);
const files = new Set(await readdir(dist));
const missing = [...required].filter((name) => !files.has(name));
if (missing.length > 0) throw new Error(`Missing extension artifacts: ${missing.join(", ")}`);

if (JSON.stringify(manifest.permissions) !== JSON.stringify(["storage"])) {
  throw new Error(`Unexpected permissions: ${JSON.stringify(manifest.permissions)}`);
}
const expectedHosts = ["https://chatgpt.com/*", "https://claude.ai/*"];
if (JSON.stringify(manifest.host_permissions) !== JSON.stringify(expectedHosts)) {
  throw new Error(`Unexpected host permissions: ${JSON.stringify(manifest.host_permissions)}`);
}

for (const name of [...files].filter((item) => /\.(?:js|html)$/.test(item))) {
  const source = await readFile(join(dist, name), "utf8");
  if (
    /(?:import\s*\(|fetch\s*\(|new\s+Worker\s*\()\s*["'`]https?:\/\//i.test(source) ||
    /<(?:script|link)[^>]+(?:src|href)=["']https?:\/\//i.test(source)
  ) {
    throw new Error(`Remote executable resource found: ${name}`);
  }
  if (/\beval\s*\(|new\s+Function\s*\(/.test(source)) {
    throw new Error(`Dynamic code execution found: ${name}`);
  }
}
console.log(`Verified ${files.size} files for Manifest V3.`);

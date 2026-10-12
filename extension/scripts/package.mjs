import { createWriteStream } from "node:fs";
import { mkdir, readdir, readFile, stat } from "node:fs/promises";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import archiver from "archiver";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(root, "dist");
const release = resolve(root, "..", "release");
const manifest = JSON.parse(await readFile(join(dist, "manifest.json"), "utf8"));
await mkdir(release, { recursive: true });
const outputPath = join(release, `plan-obfuscator-v${manifest.version}.zip`);

async function listFiles(directory) {
  const result = [];
  for (const name of (await readdir(directory)).sort()) {
    const path = join(directory, name);
    if ((await stat(path)).isDirectory()) result.push(...await listFiles(path));
    else result.push(path);
  }
  return result;
}

const output = createWriteStream(outputPath);
const archive = archiver("zip", { zlib: { level: 9 } });
const completed = new Promise((resolvePromise, reject) => {
  output.on("close", resolvePromise);
  output.on("error", reject);
  archive.on("error", reject);
});
archive.pipe(output);
for (const path of await listFiles(dist)) {
  archive.append(await readFile(path), {
    name: relative(dist, path).replaceAll("\\", "/"),
    date: new Date("2000-01-01T00:00:00.000Z"),
    mode: 0o644
  });
}
await archive.finalize();
await completed;

console.log(outputPath);

import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const outdir = join(root, "dist");

if (dirname(outdir) !== root || outdir !== join(root, "dist")) {
  throw new Error(`Refusing to clean unexpected path: ${outdir}`);
}
await rm(outdir, { recursive: true, force: true });
await mkdir(outdir, { recursive: true });

await build({
  absWorkingDir: root,
  entryPoints: {
    background: "src/background/service-worker.ts",
    content: "src/content/content-script.ts",
    "composer-frame": "src/frames/composer-frame.ts",
    "restored-frame": "src/frames/restored-frame.ts",
    popup: "src/popup/popup.ts"
  },
  outdir,
  bundle: true,
  format: "esm",
  target: "chrome114",
  platform: "browser",
  sourcemap: false,
  legalComments: "none",
  charset: "utf8",
  minify: false,
  logLevel: "info"
});

const staticFiles = [
  ["manifest.json", "manifest.json"],
  ["src/content/content.css", "content.css"],
  ["src/frames/composer-frame.html", "composer-frame.html"],
  ["src/frames/composer-frame.css", "composer-frame.css"],
  ["src/frames/restored-frame.html", "restored-frame.html"],
  ["src/frames/restored-frame.css", "restored-frame.css"],
  ["src/popup/popup.html", "popup.html"],
  ["src/popup/popup.css", "popup.css"]
];
for (const [source, destination] of staticFiles) {
  await cp(join(root, source), join(outdir, destination));
}

const packageJson = JSON.parse(await readFile(join(root, "package.json"), "utf8"));
await writeFile(
  join(outdir, "BUILD_INFO.json"),
  `${JSON.stringify({ name: packageJson.name, version: packageJson.version }, null, 2)}\n`,
  "utf8"
);

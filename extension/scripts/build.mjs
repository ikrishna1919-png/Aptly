// "Build" for a no-bundler MV3 extension: this package is loadable unpacked
// as-is (plain ES modules, no transpile). This script validates the manifest
// and that every referenced file exists, then (without --check) copies the
// loadable files into dist/ so there's a clean artifact to zip for the store.
import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const check = process.argv.includes("--check");

async function exists(p) {
  try {
    await fs.access(path.join(root, p));
    return true;
  } catch {
    return false;
  }
}

const manifest = JSON.parse(await fs.readFile(path.join(root, "manifest.json"), "utf8"));
const required = [
  manifest.background.service_worker,
  manifest.action.default_popup,
  ...manifest.content_scripts.flatMap((c) => c.js),
  ...Object.values(manifest.icons),
];

let ok = true;
for (const f of required) {
  if (!(await exists(f))) {
    console.error(`MISSING: ${f}`);
    ok = false;
  }
}
if (!ok) {
  console.error("Manifest references missing files.");
  process.exit(1);
}
console.log(`manifest v${manifest.manifest_version} OK — ${required.length} referenced files present`);

if (!check) {
  const dist = path.join(root, "dist");
  await fs.rm(dist, { recursive: true, force: true });
  await fs.mkdir(dist, { recursive: true });
  for (const rel of ["manifest.json", "src", "public"]) {
    await fs.cp(path.join(root, rel), path.join(dist, rel), { recursive: true });
  }
  console.log("dist/ written — load unpacked from extension/dist or zip it for the store.");
}

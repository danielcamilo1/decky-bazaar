#!/usr/bin/env node
/**
 * Build the zip that Decky Loader installs from a URL or a manual copy.
 *
 * Layout Decky expects:
 *
 *   <Plugin Name>/
 *     dist/index.js
 *     main.py
 *     package.json
 *     plugin.json
 *     README.md
 *     LICENSE
 *
 * The folder inside the zip must be named after `plugin.json`'s `name`, since
 * that is the directory Decky serves the plugin's assets from.
 */
import { execFileSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const manifest = JSON.parse(readFileSync(join(root, "plugin.json"), "utf-8"));
const { version } = JSON.parse(readFileSync(join(root, "package.json"), "utf-8"));

if (!existsSync(join(root, "dist", "index.js"))) {
  console.error("dist/index.js is missing — run `pnpm run build` first.");
  process.exit(1);
}

const outDir = join(root, "out");
const stageDir = join(outDir, manifest.name);
const zipName = `${manifest.name.replace(/\s+/g, "-")}-v${version}.zip`;

rmSync(outDir, { recursive: true, force: true });
mkdirSync(stageDir, { recursive: true });

cpSync(join(root, "dist"), join(stageDir, "dist"), { recursive: true });
for (const file of ["main.py", "package.json", "plugin.json", "README.md", "LICENSE"]) {
  cpSync(join(root, file), join(stageDir, file));
}

// Bazaar reads a plugin's own assets out of its folder, and the panel image
// ships with it.
cpSync(join(root, "assets"), join(stageDir, "assets"), { recursive: true });

// The sourcemap is a few MB and only useful with a dev build of the loader.
rmSync(join(stageDir, "dist", "index.js.map"), { force: true });

execFileSync("zip", ["-r", "-X", zipName, manifest.name], { cwd: outDir, stdio: "inherit" });
rmSync(stageDir, { recursive: true, force: true });

console.log(`\nBuilt out/${zipName}`);

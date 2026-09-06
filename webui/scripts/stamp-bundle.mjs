// Fingerprints the production bundle right after `vite build`.
//
// Writes navin/web/dist/build-info.json, whose `bundle` field is a sha256 over
// every other file in the bundle. Each packaged target (AppImage, deb, rpm,
// pacman, DMG, MSI, NSIS) embeds that same directory through PyInstaller, so
// the release workflow can prove that what ships is byte for byte the bundle
// it built, linted and tested once, and not one a build machine rebuilt with
// its own dependency resolution.
//
// Run by `npm run build`; no arguments.

import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webuiDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(webuiDir, "..");
const distDir = join(repoRoot, "navin", "web", "dist");
const STAMP_NAME = "build-info.json";

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

/** Every file of the bundle as `relative/path`, POSIX separators, sorted. */
function bundleFiles(root) {
  const found = [];
  const walk = (dir, prefix) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
      if (entry.isDirectory()) {
        walk(join(dir, entry.name), relative);
      } else if (entry.isFile() && relative !== STAMP_NAME) {
        found.push(relative);
      }
    }
  };
  walk(root, "");
  return found.sort();
}

/**
 * One digest for the whole tree: sha256 over `path\0filedigest\n` lines.
 *
 * Paths are part of the digest, so a renamed asset changes it even when the
 * bytes are unchanged. The stamp itself is excluded, otherwise no digest
 * could ever be written.
 */
function bundleDigest(root, files) {
  const digest = createHash("sha256");
  for (const relative of files) {
    digest.update(`${relative}\0${sha256(readFileSync(join(root, relative)))}\n`);
  }
  return digest.digest("hex");
}

function gitOutput(args) {
  try {
    return execFileSync("git", args, { cwd: repoRoot, encoding: "utf8" }).trim();
  } catch {
    // A tarball release or a shallow container has no git; the stamp stays
    // useful without provenance.
    return "";
  }
}

function shippedVersion() {
  // Same source of truth as scripts/set-version.sh: the desktop app version.
  try {
    const conf = JSON.parse(
      readFileSync(join(repoRoot, "desktop", "src-tauri", "tauri.conf.json"), "utf8"),
    );
    return String(conf.version || "");
  } catch {
    return "";
  }
}

function lockfileDigest() {
  try {
    return sha256(readFileSync(join(webuiDir, "package-lock.json")));
  } catch {
    return "";
  }
}

let files;
try {
  files = bundleFiles(distDir);
} catch (error) {
  console.error(`stamp-bundle: no bundle at ${distDir} (${error.message})`);
  process.exit(1);
}
if (!files.includes("index.html")) {
  console.error(`stamp-bundle: ${distDir} has no index.html; the build did not produce a bundle`);
  process.exit(1);
}

const bundle = bundleDigest(distDir, files);
const stamp = {
  bundle,
  files: files.length,
  version: shippedVersion(),
  commit: gitOutput(["rev-parse", "HEAD"]),
  // Anything uncommitted means the bundle cannot be reproduced from the commit.
  dirty: gitOutput(["status", "--porcelain"]) !== "",
  lockfile: lockfileDigest(),
  packageManager: `npm@${process.env.npm_config_user_agent?.match(/npm\/(\S+)/)?.[1] || "unknown"}`,
  node: process.version,
  builtAt: new Date().toISOString(),
};

writeFileSync(join(distDir, STAMP_NAME), `${JSON.stringify(stamp, null, 2)}\n`, "utf8");
console.log(`stamp-bundle: ${files.length} files, bundle ${bundle.slice(0, 16)}`);

#!/usr/bin/env node
// Launcher for the navin-engine binary.
//
// The engine is a Rust executable, not JavaScript. This wrapper exists so a
// user can write `npx -y navin-engine mcp` in an MCP config without
// installing a Rust toolchain: it resolves the release binary for the current
// platform, caches it, and hands over the process.
//
// Everything it prints goes to stderr. Stdout belongs to the MCP protocol.

"use strict";

const { spawnSync, execFileSync } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { version } = require("../package.json");
const REPO = "https://github.com/navinspire-ai/navin-engine";

// Node's platform-arch pair to the Rust target triple of the release asset.
const TARGETS = {
  "linux-x64": "x86_64-unknown-linux-gnu",
  "linux-arm64": "aarch64-unknown-linux-gnu",
  "darwin-arm64": "aarch64-apple-darwin",
  "darwin-x64": "x86_64-apple-darwin",
  "win32-x64": "x86_64-pc-windows-msvc",
};

function note(message) {
  process.stderr.write(`navin-engine: ${message}\n`);
}

function fail(message) {
  note(message);
  note(
    `install it another way instead: cargo install --git ${REPO}, or download a binary from ${REPO}/releases and point NAVIN_ENGINE_BIN at it`
  );
  process.exit(1);
}

function cacheDir() {
  if (process.env.NAVIN_ENGINE_HOME) {
    return path.join(process.env.NAVIN_ENGINE_HOME, version);
  }
  const base =
    process.platform === "win32"
      ? process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local")
      : process.env.XDG_CACHE_HOME || path.join(os.homedir(), ".cache");
  return path.join(base, "navin-engine", version);
}

async function download(url) {
  const response = await fetch(url, { redirect: "follow" });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText} for ${url}`);
  }
  return Buffer.from(await response.arrayBuffer());
}

function extract(archive, into) {
  // bsdtar reads both .tar.gz and .zip, and ships with macOS, most Linux
  // distributions and Windows 10 1803 and later.
  try {
    execFileSync("tar", ["-xf", archive, "-C", into], { stdio: "ignore" });
    return;
  } catch (error) {
    if (process.platform !== "win32") {
      throw error;
    }
  }
  execFileSync(
    "powershell",
    [
      "-NoProfile",
      "-Command",
      `Expand-Archive -Force -Path '${archive}' -DestinationPath '${into}'`,
    ],
    { stdio: "ignore" }
  );
}

async function install(binary) {
  const key = `${process.platform}-${process.arch}`;
  const target = TARGETS[key];
  if (!target) {
    fail(`no prebuilt binary for ${key}`);
  }
  const archiveName =
    process.platform === "win32"
      ? `navin-engine-${target}.zip`
      : `navin-engine-${target}.tar.gz`;
  const base = `${REPO}/releases/download/v${version}/${archiveName}`;

  note(`fetching ${archiveName} (once, then cached)`);
  let archive;
  let expected;
  try {
    [archive, expected] = await Promise.all([
      download(base),
      download(`${base}.sha256`),
    ]);
  } catch (error) {
    fail(`download failed: ${error.message}`);
  }

  // A binary fetched over the network is verified before it is ever run.
  const actual = crypto.createHash("sha256").update(archive).digest("hex");
  const wanted = expected.toString("utf8").trim().split(/\s+/)[0];
  if (actual !== wanted) {
    fail(`checksum mismatch for ${archiveName}: expected ${wanted}, got ${actual}`);
  }

  const dir = path.dirname(binary);
  fs.mkdirSync(dir, { recursive: true });
  const staged = path.join(dir, archiveName);
  fs.writeFileSync(staged, archive);
  try {
    extract(staged, dir);
  } catch (error) {
    fail(`cannot extract ${archiveName}: ${error.message}`);
  }
  fs.rmSync(staged, { force: true });
  if (process.platform !== "win32") {
    fs.chmodSync(binary, 0o755);
  }
  if (!fs.existsSync(binary)) {
    fail(`${archiveName} did not contain the expected binary`);
  }
}

async function main() {
  const name = process.platform === "win32" ? "navin-engine.exe" : "navin-engine";

  // Same escape hatch as the rest of the engine's tooling: an explicit
  // binary always wins, which is what you want for a local build.
  const override = process.env.NAVIN_ENGINE_BIN;
  let binary = override && override.trim() ? override.trim() : path.join(cacheDir(), name);

  if (!fs.existsSync(binary)) {
    if (override) {
      fail(`NAVIN_ENGINE_BIN points at ${binary}, which does not exist`);
    }
    await install(binary);
  }

  const result = spawnSync(binary, process.argv.slice(2), { stdio: "inherit" });
  if (result.error) {
    fail(`cannot run ${binary}: ${result.error.message}`);
  }
  process.exit(result.status === null ? 1 : result.status);
}

main().catch((error) => fail(error.message));

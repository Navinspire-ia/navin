import { build } from "../webui/node_modules/esbuild/lib/main.js";
import { mkdir, readFile, readdir, writeFile, copyFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";

const root = path.dirname(fileURLToPath(import.meta.url));
const repo = path.dirname(root);
for (const browser of ["chrome", "edge", "firefox"]) {
  const output = path.join(root, "dist", browser);
  await mkdir(output, { recursive: true });
  await build({ entryPoints: [path.join(root, "src/main.tsx")], outfile: path.join(output, "app.js"), bundle: true,
    minify: true, format: "esm", platform: "browser", target: ["chrome120", "firefox140"], jsx: "automatic",
    nodePaths: [path.join(repo, "webui/node_modules")], define: { "process.env.NODE_ENV": '"production"' }, legalComments: "linked" });
  for (const name of ["index.html", "style.css", "privacy.html"]) await copyFile(path.join(root, name), path.join(output, name));
  const fonts = path.join(repo, "webui/node_modules/@fluentui/font-icons-mdl2/fonts");
  for (const name of await readdir(fonts)) {
    if (name.endsWith(".woff")) await copyFile(path.join(fonts, name), path.join(output, name));
  }
  for (const name of ["background.js", "capture.js", "overlay.js"]) await copyFile(path.join(root, "src", name), path.join(output, name));
  // Edge uses the same Chromium manifest and extension APIs as Chrome.
  const manifest = JSON.parse(await readFile(path.join(root, `manifest.${browser === "edge" ? "chrome" : browser}.json`), "utf8"));
  await writeFile(path.join(output, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
}
const localPython = path.join(repo, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
const python = process.env.NAVIN_BUILD_PYTHON || (existsSync(localPython) ? localPython : process.platform === "win32" ? "python" : "python3");
execFileSync(python, [path.join(root, "package.py")], { stdio: "inherit" });

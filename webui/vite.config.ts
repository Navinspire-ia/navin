import { defineConfig, loadEnv, type Plugin, type ViteDevServer } from "vite";
import react from "@vitejs/plugin-react";
import { execFile, spawn } from "node:child_process";
import type { IncomingHttpHeaders } from "node:http";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

/** A mid-session optimize-deps rewrite answers 504 on the old hashed URLs.
 *  Desktop webviews often miss Vite's HMR reload, so the chunk stays dead.
 *  Reload at most once per window: each 504 used to broadcast another
 *  full-reload, the tab requested the same stale URL, got 504 again, and
 *  the page blinked every second. */
const OPTIMIZE_DEP_RELOAD_MIN_MS = 15_000;

function recoverOutdatedOptimizeDep(): Plugin {
  let lastFullReloadAt = 0;
  return {
    name: "navin-recover-outdated-optimize-dep",
    configureServer(server: ViteDevServer) {
      server.middlewares.use((req, res, next) => {
        const url = req.url ?? "";
        if (!url.includes("/node_modules/.vite/deps/")) {
          next();
          return;
        }
        const origEnd = res.end.bind(res);
        res.end = ((...args: Parameters<typeof origEnd>) => {
          if (res.statusCode === 504) {
            const now = Date.now();
            if (now - lastFullReloadAt >= OPTIMIZE_DEP_RELOAD_MIN_MS) {
              lastFullReloadAt = now;
              server.ws.send({ type: "full-reload", path: "*" });
            }
          }
          return origEnd(...args);
        }) as typeof res.end;
        next();
      });
    },
  };
}

function careerWebSearch(): Plugin {
  return {
    name: "navin-career-web-search",
    configureServer(server: ViteDevServer) {
      server.middlewares.use((req, res, next) => {
        const raw = req.originalUrl || req.url || "";
        if (!raw.startsWith("/career-web-search")) {
          next();
          return;
        }
        const query = new URL(raw, "http://127.0.0.1").searchParams.get("q") || "";
        res.setHeader("Content-Type", "application/json; charset=utf-8");
        if (!query.trim()) {
          res.end("[]");
          return;
        }
        const python = path.resolve(__dirname, "../.venv/bin/python");
        const script = path.resolve(__dirname, "../navin/career/web_search_cli.py");
        execFile(
          python,
          [script, query],
          { timeout: 18000, env: { ...process.env, PYTHONPATH: path.resolve(__dirname, "..") } },
          (error, stdout) => {
            if (error || !stdout.trim()) {
              res.end("[]");
              return;
            }
            res.end(stdout);
          },
        );
      });
    },
  };
}

function navinFileBody(headers: IncomingHttpHeaders): string {
  const chunks: string[] = [];
  for (let index = 0; index < 64; index += 1) {
    const raw = headers[`x-navin-file-body-${index}`];
    if (raw == null) break;
    chunks.push(Array.isArray(raw) ? raw.join("") : raw);
  }
  if (!chunks.length) return "{}";
  try {
    return Buffer.from(chunks.join(""), "base64").toString("utf8") || "{}";
  } catch {
    return "{}";
  }
}

/**
 * Harvest / produce / pipeline (and the other desk writes) must not spawn a
 * Python child inside the Vite process. One image pack or site fetch is
 * enough to OOM Node on WSL and look like "the site died".
 * Snapshot polls go to the live gateway too unless NAVIN_VITE_DESK_FALLBACK=1
 * (old gateway missing /api/<desk>).
 */
const DESK_VITE_HEAVY_ACTIONS = new Set([
  "approve",
  "campaign",
  "collect",
  "content",
  "creative",
  "enrich",
  "generate",
  "harvest",
  "hunt",
  "improve",
  "launch",
  "optimize",
  "pipeline",
  "plan",
  "produce",
  "product",
  "research",
  "send",
  "tick",
  "understand",
  "vision",
  "write",
]);

function deskActionGoesToGateway(action: string): boolean {
  const act = (action || "snapshot").trim().toLowerCase();
  if (DESK_VITE_HEAVY_ACTIONS.has(act)) {
    return true;
  }
  return process.env.NAVIN_VITE_DESK_FALLBACK !== "1";
}

/** The live gateway may predate /api/career. Vite serves the same local store as the career tool. */
function careerDeskApi(): Plugin {
  return {
    name: "navin-career-desk-api",
    configureServer(server: ViteDevServer) {
      server.middlewares.use((req, res, next) => {
        const raw = req.originalUrl || req.url || "";
        const pathname = raw.split("?")[0] || "";
        if (pathname !== "/api/career") {
          next();
          return;
        }
        const action = new URL(raw, "http://127.0.0.1").searchParams.get("action") || "snapshot";
        if (deskActionGoesToGateway(action)) {
          next();
          return;
        }
        const body = navinFileBody(req.headers);
        const root = path.resolve(__dirname, "..");
        const python = path.resolve(root, ".venv/bin/python");
        const child = spawn(python, ["-m", "navin.career.desk_cli", action], {
          cwd: root,
          env: { ...process.env, PYTHONPATH: root },
        });
        const out: Buffer[] = [];
        const err: Buffer[] = [];
        const timer = setTimeout(() => {
          child.kill("SIGTERM");
        }, 240_000);
        child.stdout.on("data", (chunk) => out.push(chunk));
        child.stderr.on("data", (chunk) => err.push(chunk));
        child.on("error", (error) => {
          clearTimeout(timer);
          if (res.writableEnded) return;
          res.statusCode = 500;
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          res.end(JSON.stringify({ error: error.message || "career desk failed" }));
        });
        child.on("close", (code) => {
          clearTimeout(timer);
          if (res.writableEnded) return;
          const stdout = Buffer.concat(out).toString("utf8").trim();
          const stderr = Buffer.concat(err).toString("utf8").trim();
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          if (code === 0 && stdout) {
            res.statusCode = 200;
            res.end(stdout);
            return;
          }
          let parsed: { error?: string; status?: number } = {};
          try {
            parsed = stdout ? (JSON.parse(stdout) as { error?: string; status?: number }) : {};
          } catch {
            parsed = {};
          }
          res.statusCode = Number(parsed.status) || (code === 2 ? 400 : 500);
          res.end(
            JSON.stringify({
              error: parsed.error || stderr || "career desk failed",
            }),
          );
        });
        child.stdin.end(body);
      });
    },
  };
}

/** The live gateway on 8766 may predate /api/leads. Vite serves the desk. */
function leadsDeskApi(): Plugin {
  return {
    name: "navin-leads-desk-api",
    configureServer(server: ViteDevServer) {
      server.middlewares.use((req, res, next) => {
        const raw = req.originalUrl || req.url || "";
        const pathname = raw.split("?")[0] || "";
        if (pathname !== "/api/leads") {
          next();
          return;
        }
        const action = new URL(raw, "http://127.0.0.1").searchParams.get("action") || "snapshot";
        if (deskActionGoesToGateway(action)) {
          next();
          return;
        }
        const body = navinFileBody(req.headers);
        const root = path.resolve(__dirname, "..");
        const python = path.resolve(root, ".venv/bin/python");
        const child = spawn(python, ["-m", "navin.leads.desk_cli", action], {
          cwd: root,
          env: { ...process.env, PYTHONPATH: root, NAVIN_PROJECT: root },
        });
        const out: Buffer[] = [];
        const err: Buffer[] = [];
        const timer = setTimeout(() => {
          child.kill("SIGTERM");
        }, 240_000);
        child.stdout.on("data", (chunk) => out.push(chunk));
        child.stderr.on("data", (chunk) => err.push(chunk));
        child.on("error", (error) => {
          clearTimeout(timer);
          if (res.writableEnded) return;
          res.statusCode = 500;
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          res.end(JSON.stringify({ error: error.message || "leads desk failed" }));
        });
        child.on("close", (code) => {
          clearTimeout(timer);
          if (res.writableEnded) return;
          const stdout = Buffer.concat(out).toString("utf8").trim();
          const stderr = Buffer.concat(err).toString("utf8").trim();
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          if (code === 0 && stdout) {
            res.statusCode = 200;
            res.end(stdout);
            return;
          }
          let parsed: { error?: string; status?: number } = {};
          try {
            parsed = stdout ? (JSON.parse(stdout) as { error?: string; status?: number }) : {};
          } catch {
            parsed = {};
          }
          res.statusCode = Number(parsed.status) || (code === 2 ? 400 : 500);
          res.end(
            JSON.stringify({
              error: parsed.error || stderr || "leads desk failed",
            }),
          );
        });
        child.stdin.end(body);
      });
    },
  };
}

/** The live gateway may predate /api/trading. Vite serves the same local store as the trading tool. */
function tradingDeskApi(): Plugin {
  return {
    name: "navin-trading-desk-api",
    configureServer(server: ViteDevServer) {
      server.middlewares.use((req, res, next) => {
        const raw = req.originalUrl || req.url || "";
        const pathname = raw.split("?")[0] || "";
        if (pathname !== "/api/trading") {
          next();
          return;
        }
        const action = new URL(raw, "http://127.0.0.1").searchParams.get("action") || "snapshot";
        if (deskActionGoesToGateway(action)) {
          next();
          return;
        }
        const body = navinFileBody(req.headers);
        const root = path.resolve(__dirname, "..");
        const python = path.resolve(root, ".venv/bin/python");
        const child = spawn(python, ["-m", "navin.trading.desk_cli", action], {
          cwd: root,
          env: { ...process.env, PYTHONPATH: root },
        });
        const out: Buffer[] = [];
        const err: Buffer[] = [];
        const timer = setTimeout(() => {
          child.kill("SIGTERM");
        }, 240_000);
        child.stdout.on("data", (chunk) => out.push(chunk));
        child.stderr.on("data", (chunk) => err.push(chunk));
        child.on("error", (error) => {
          clearTimeout(timer);
          if (res.writableEnded) return;
          res.statusCode = 500;
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          res.end(JSON.stringify({ error: error.message || "trading desk failed" }));
        });
        child.on("close", (code) => {
          clearTimeout(timer);
          if (res.writableEnded) return;
          const stdout = Buffer.concat(out).toString("utf8").trim();
          const stderr = Buffer.concat(err).toString("utf8").trim();
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          if (code === 0 && stdout) {
            res.statusCode = 200;
            res.end(stdout);
            return;
          }
          let parsed: { error?: string; status?: number } = {};
          try {
            parsed = stdout ? (JSON.parse(stdout) as { error?: string; status?: number }) : {};
          } catch {
            parsed = {};
          }
          res.statusCode = Number(parsed.status) || (code === 2 ? 400 : 500);
          res.end(
            JSON.stringify({
              error: parsed.error || stderr || "trading desk failed",
            }),
          );
        });
        child.stdin.end(body);
      });
    },
  };
}

/** The live gateway may predate /api/marketing. Vite serves the same local store as the marketing tool. */
function marketingDeskApi(): Plugin {
  return {
    name: "navin-marketing-desk-api",
    configureServer(server: ViteDevServer) {
      server.middlewares.use((req, res, next) => {
        const raw = req.originalUrl || req.url || "";
        const pathname = raw.split("?")[0] || "";
        if (pathname !== "/api/marketing") {
          next();
          return;
        }
        const action = new URL(raw, "http://127.0.0.1").searchParams.get("action") || "snapshot";
        if (deskActionGoesToGateway(action)) {
          next();
          return;
        }
        if (action === "file") {
          const name = new URL(raw, "http://127.0.0.1").searchParams.get("name") || "";
          const root = path.resolve(__dirname, "..");
          const python = path.resolve(root, ".venv/bin/python");
          const lookup = spawn(
            python,
            [
              "-c",
              "from navin.marketing.assets import resolve_asset; from navin.marketing.store import MarketingStore; import sys; p=resolve_asset(MarketingStore(), sys.argv[1]); print(p or '')",
              name,
            ],
            { cwd: root, env: { ...process.env, PYTHONPATH: root } },
          );
          const out: Buffer[] = [];
          const lookupTimer = setTimeout(() => {
            lookup.kill("SIGTERM");
          }, 15_000);
          lookup.stdout.on("data", (chunk) => out.push(chunk));
          lookup.on("close", () => {
            clearTimeout(lookupTimer);
            if (res.writableEnded) return;
            const filePath = Buffer.concat(out).toString("utf8").trim();
            if (!filePath) {
              res.statusCode = 404;
              res.setHeader("Content-Type", "application/json; charset=utf-8");
              res.end(JSON.stringify({ error: "asset not found" }));
              return;
            }
            const ext = path.extname(filePath).toLowerCase();
            const mime =
              ext === ".png"
                ? "image/png"
                : ext === ".jpg" || ext === ".jpeg"
                  ? "image/jpeg"
                  : ext === ".webp"
                    ? "image/webp"
                    : ext === ".gif"
                      ? "image/gif"
                      : ext === ".svg"
                        ? "image/svg+xml"
                        : ext === ".mp4"
                          ? "video/mp4"
                          : ext === ".webm"
                            ? "video/webm"
                            : ext === ".mp3"
                              ? "audio/mpeg"
                              : ext === ".wav"
                                ? "audio/wav"
                                : "application/octet-stream";
            res.statusCode = 200;
            res.setHeader("Content-Type", mime);
            res.setHeader("Cache-Control", "public, max-age=3600");
            fs.createReadStream(filePath).pipe(res);
          });
          lookup.on("error", () => {
            clearTimeout(lookupTimer);
            if (res.writableEnded) return;
            res.statusCode = 500;
            res.end(JSON.stringify({ error: "asset lookup failed" }));
          });
          return;
        }
        const body = navinFileBody(req.headers);
        const root = path.resolve(__dirname, "..");
        const python = path.resolve(root, ".venv/bin/python");
        const child = spawn(python, ["-m", "navin.marketing.desk_cli", action], {
          cwd: root,
          env: { ...process.env, PYTHONPATH: root },
        });
        const out: Buffer[] = [];
        const err: Buffer[] = [];
        const timer = setTimeout(() => {
          child.kill("SIGTERM");
        }, 240_000);
        child.stdout.on("data", (chunk) => out.push(chunk));
        child.stderr.on("data", (chunk) => err.push(chunk));
        child.on("error", (error) => {
          clearTimeout(timer);
          if (res.writableEnded) return;
          res.statusCode = 500;
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          res.end(JSON.stringify({ error: error.message || "marketing desk failed" }));
        });
        child.on("close", (code) => {
          clearTimeout(timer);
          if (res.writableEnded) return;
          const stdout = Buffer.concat(out).toString("utf8").trim();
          const stderr = Buffer.concat(err).toString("utf8").trim();
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          if (code === 0 && stdout) {
            res.statusCode = 200;
            res.end(stdout);
            return;
          }
          let parsed: { error?: string; status?: number } = {};
          try {
            parsed = stdout ? (JSON.parse(stdout) as { error?: string; status?: number }) : {};
          } catch {
            parsed = {};
          }
          res.statusCode = Number(parsed.status) || (code === 2 ? 400 : 500);
          res.end(
            JSON.stringify({
              error: parsed.error || stderr || "marketing desk failed",
            }),
          );
        });
        child.stdin.end(body);
      });
    },
  };
}

/** The live gateway on 8766 may predate the LinkedIn Tenders MCP preset. */
function mcpPresetsApi(): Plugin {
  const paths = new Set([
    "/api/settings/mcp-presets",
    "/api/settings/mcp-presets/enable",
    "/api/settings/mcp-presets/remove",
    "/api/settings/mcp-presets/tools",
    "/api/settings/mcp-presets/custom",
    "/api/settings/mcp-presets/import",
    "/api/settings/mcp-presets/import-cursor",
  ]);
  const actionByPath: Record<string, string> = {
    "/api/settings/mcp-presets": "list",
    "/api/settings/mcp-presets/enable": "enable",
    "/api/settings/mcp-presets/remove": "remove",
    "/api/settings/mcp-presets/tools": "tools",
    "/api/settings/mcp-presets/custom": "custom",
    "/api/settings/mcp-presets/import": "import",
    "/api/settings/mcp-presets/import-cursor": "import-cursor",
  };
  return {
    name: "navin-mcp-presets-api",
    configureServer(server: ViteDevServer) {
      server.middlewares.use((req, res, next) => {
        const raw = req.originalUrl || req.url || "";
        const pathname = raw.split("?")[0] || "";
        if (!paths.has(pathname)) {
          next();
          return;
        }
        const parsed = new URL(raw, "http://127.0.0.1");
        const query: Record<string, string> = {};
        parsed.searchParams.forEach((value, key) => {
          query[key] = value;
        });
        let values: Record<string, unknown> = {};
        const header = req.headers["x-navin-mcp-values"];
        const headerText = Array.isArray(header) ? header.join("") : header;
        if (headerText) {
          try {
            const parsedValues = JSON.parse(headerText) as unknown;
            if (parsedValues && typeof parsedValues === "object") {
              values = parsedValues as Record<string, unknown>;
            }
          } catch {
            values = {};
          }
        }
        const root = path.resolve(__dirname, "..");
        const python = path.resolve(root, ".venv/bin/python");
        const child = spawn(python, ["-m", "navin.webui.mcp_presets_cli", actionByPath[pathname] || "list"], {
          cwd: root,
          env: { ...process.env, PYTHONPATH: root, NAVIN_PROJECT: root },
        });
        const out: Buffer[] = [];
        const err: Buffer[] = [];
        const timer = setTimeout(() => {
          child.kill("SIGTERM");
        }, 60_000);
        child.stdout.on("data", (chunk) => out.push(chunk));
        child.stderr.on("data", (chunk) => err.push(chunk));
        child.on("error", (error) => {
          clearTimeout(timer);
          if (res.writableEnded) return;
          res.statusCode = 500;
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          res.end(JSON.stringify({ error: error.message || "mcp presets failed" }));
        });
        child.on("close", (code) => {
          clearTimeout(timer);
          if (res.writableEnded) return;
          const stdout = Buffer.concat(out).toString("utf8").trim();
          const stderr = Buffer.concat(err).toString("utf8").trim();
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          if (code === 0 && stdout) {
            res.statusCode = 200;
            res.end(stdout);
            return;
          }
          let parsedBody: { error?: string; status?: number } = {};
          try {
            parsedBody = stdout ? (JSON.parse(stdout) as { error?: string; status?: number }) : {};
          } catch {
            parsedBody = {};
          }
          res.statusCode = Number(parsedBody.status) || (code === 2 ? 400 : 500);
          res.end(JSON.stringify({ error: parsedBody.error || stderr || "mcp presets failed" }));
        });
        child.stdin.end(JSON.stringify({ query, values }));
      });
    },
  };
}

/** The live gateway on 8766 may predate /api/tenders. Vite serves the desk. */
function tendersDeskApi(): Plugin {
  return {
    name: "navin-tenders-desk-api",
    configureServer(server: ViteDevServer) {
      server.middlewares.use((req, res, next) => {
        const raw = req.originalUrl || req.url || "";
        const pathname = raw.split("?")[0] || "";
        if (pathname !== "/api/tenders") {
          next();
          return;
        }
        const action = new URL(raw, "http://127.0.0.1").searchParams.get("action") || "snapshot";
        if (deskActionGoesToGateway(action)) {
          next();
          return;
        }
        const body = navinFileBody(req.headers);
        const root = path.resolve(__dirname, "..");
        const python = path.resolve(root, ".venv/bin/python");
        const child = spawn(python, ["-m", "navin.tenders.desk_cli", action], {
          cwd: root,
          env: { ...process.env, PYTHONPATH: root, NAVIN_PROJECT: root },
        });
        const out: Buffer[] = [];
        const err: Buffer[] = [];
        const timer = setTimeout(() => {
          child.kill("SIGTERM");
        }, 240_000);
        child.stdout.on("data", (chunk) => out.push(chunk));
        child.stderr.on("data", (chunk) => err.push(chunk));
        child.on("error", (error) => {
          clearTimeout(timer);
          if (res.writableEnded) return;
          res.statusCode = 500;
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          res.end(JSON.stringify({ error: error.message || "tenders desk failed" }));
        });
        child.on("close", (code) => {
          clearTimeout(timer);
          if (res.writableEnded) return;
          const stdout = Buffer.concat(out).toString("utf8").trim();
          const stderr = Buffer.concat(err).toString("utf8").trim();
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          if (code === 0 && stdout) {
            res.statusCode = 200;
            res.end(stdout);
            return;
          }
          let parsed: { error?: string; status?: number } = {};
          try {
            parsed = stdout ? (JSON.parse(stdout) as { error?: string; status?: number }) : {};
          } catch {
            parsed = {};
          }
          res.statusCode = Number(parsed.status) || (code === 2 ? 400 : 500);
          res.end(
            JSON.stringify({
              error: parsed.error || stderr || "tenders desk failed",
            }),
          );
        });
        child.stdin.end(body);
      });
    },
  };
}

export function webuiManualChunk(id: string): string | undefined {
  if (id.includes("node_modules/refractor/lang/")) {
    return;
  }
  // The whole unified/hast/refractor/react-syntax-highlighter ecosystem must
  // live in ONE chunk: these packages import each other in both directions
  // (hastscript <-> hast-util-parse-selector, refractor <-> hast helpers),
  // and splitting them across chunks creates circular chunk imports that
  // throw "Cannot access 'X' before initialization" (TDZ) at load time.
  if (
    id.includes("node_modules/react-syntax-highlighter")
    || id.includes("node_modules/refractor/core")
    || id.includes("node_modules/react-markdown")
    || id.includes("node_modules/remark-")
    || id.includes("node_modules/rehype-")
    || id.includes("node_modules/unified")
    || id.includes("node_modules/mdast-")
    || id.includes("node_modules/hast-")
    || id.includes("node_modules/micromark")
    || id.includes("node_modules/unist-")
  ) {
    return "markdown-vendor";
  }
  if (id.includes("node_modules/katex")) {
    return "katex";
  }
}

export function gatewayTarget(host: string | undefined, port: number): string {
  const value = (host || "127.0.0.1").trim();
  const connectHost =
    value === "0.0.0.0" ? "127.0.0.1"
    : value === "::" || value === "[::]" ? "::1"
    : value.replace(/^\[|\]$/g, "");
  const authority = connectHost.includes(":") ? `[${connectHost}]` : connectHost;
  return `http://${authority}:${port}`;
}

function defaultGatewayTarget(): string {
  // Keep Vite's /webui + /api proxy pointed at the same port the gateway
  // actually serves (channels.websocket.port), not a stale 8765 default.
  try {
    const cfgPath = path.join(os.homedir(), ".navin", "config.json");
    const raw = fs.readFileSync(cfgPath, "utf8");
    const websocket = (JSON.parse(raw) as {
      channels?: { websocket?: { host?: string; port?: number } };
    })?.channels?.websocket;
    const port = websocket?.port;
    if (typeof port === "number" && port > 0) {
      return gatewayTarget(websocket?.host, port);
    }
  } catch {
    // fall through
  }
  return "http://127.0.0.1:8765";
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.NAVIN_API_URL || defaultGatewayTarget();
  const hmrPath = "/__navin_vite_hmr";

  return {
    plugins: [react(), recoverOutdatedOptimizeDep(), careerWebSearch(), careerDeskApi(), tradingDeskApi(), tendersDeskApi(), leadsDeskApi(), marketingDeskApi(), mcpPresetsApi()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
      // One React copy. Serving the CJS jsx-runtime.js raw (no named `jsx`
      // export) is what throws "does not provide an export named 'jsx'".
      dedupe: ["react", "react-dom"],
    },
    optimizeDeps: {
      // Keep dev reloads stable for dependencies that can rewrite generated
      // optimizer chunk filenames while a browser tab is still running. Do not
      // exclude the markdown/remark/rehype chain: Vite's pre-bundling is needed
      // there for CommonJS interop such as style-to-js.
      // Pre-bundle React's CJS jsx-runtime so Radix / Fluent import { jsx }
      // instead of hitting /node_modules/react/jsx-runtime.js raw.
      // The syntax-highlighter chain MUST be pre-bundled, not excluded:
      // prism-async-light dynamically imports refractor/core and
      // refractor/lang/*.js, which are CommonJS. Served raw in dev they throw
      // "require is not defined" and code blocks silently lose highlighting.
      // Eager pre-bundling also keeps chunk names stable across the session
      // (no on-the-fly re-optimization reload when a language loads).
      include: [
        "react",
        "react/jsx-runtime",
        "react/jsx-dev-runtime",
        "react-dom",
        "react-dom/client",
        "@radix-ui/react-dialog",
        "@radix-ui/react-context",
        "react-syntax-highlighter/dist/esm/prism-async-light",
        "react-syntax-highlighter/dist/esm/create-element",
        "react-syntax-highlighter/dist/esm/styles/prism/one-dark",
        "react-syntax-highlighter/dist/esm/styles/prism/one-light",
        "refractor/core",
        "refractor/lang/*.js",
        // Lazy workbench chunks (Studio TipTap, Code xterm). If these are
        // discovered mid-session Vite rewrites the dep hash and the tab still
        // holding the old URL gets 504 Outdated Optimize Dep - a black
        // terminal or a stuck "Loading workspace".
        "@xterm/xterm",
        "@xterm/addon-fit",
        "@xterm/addon-webgl",
        "@tiptap/react",
        "@tiptap/starter-kit",
        "@tiptap/extension-highlight",
        "@tiptap/extension-code-block-lowlight",
        "@tiptap/extension-image",
        "@tiptap/extension-table",
        "@tiptap/extension-task-list",
        "@tiptap/extension-task-item",
        "@tiptap/extension-text-style",
        "lowlight",
        // Same trap as xterm/tiptap: Studio, Montage, Meeting and CRM lazy-load
        // Fluent icons + three/R3F. Discovering them mid-session rewrites the
        // dep hash and the tab still holding `@fluentui_font-icons-mdl2.js` /
        // `@react-three_fiber.js` / `@react-three_drei.js` gets
        // 504 Outdated Optimize Dep.
        "@fluentui/font-icons-mdl2",
        "@fluentui/react",
        "@react-three/fiber",
        "@react-three/drei",
        "three",
        "diff",
      ],
    },
    build: {
      outDir: path.resolve(__dirname, "../navin/web/dist"),
      emptyOutDir: true,
      sourcemap: false,
      rollupOptions: {
        output: {
          manualChunks: webuiManualChunk,
        },
      },
    },
    server: {
      // Listen on all interfaces: under WSL2 the Windows browser often cannot
      // reach 127.0.0.1 forwarding and must use the WSL IP directly.
      host: "0.0.0.0",
      port: 5173,
      strictPort: true,
      // WSL inotify hits ENOSPC quickly (many Vite/Node trees). Polling
      // keeps :5173 up instead of crashing after "ready".
      watch: {
        usePolling: Boolean(process.env.WSL_DISTRO_NAME)
          || process.env.CHOKIDAR_USEPOLLING === "1"
          || process.env.CHOKIDAR_USEPOLLING === "true",
      },
      // Publish (Cloudflare Quick Tunnel) rewrites the Host header to
      // *.trycloudflare.com. Vite blocks unknown hosts by default.
      allowedHosts: [".trycloudflare.com", ".nport.link", "localhost", "127.0.0.1"],
      // Keep Vite's HMR socket on a dedicated path so the Navin app socket can
      // use /__navin_ws without competing for the same upgrade route.
      // No hmr.host override: the client must connect back to whatever host
      // the page was loaded from (localhost, WSL IP, tunnel...). Hardcoding
      // 127.0.0.1 broke HMR when browsing via the WSL IP, leaving stale code
      // in the tab.
      hmr: {
        path: hmrPath,
      },
      // Crawl the lazy 3D / Fluent entries at boot so Vite does not discover
      // them on first open of Studio / Montage and then 504 the old URLs.
      warmup: {
        clientFiles: [
          "./src/lib/fluent-icons.ts",
          "./src/components/studio/tenders/TendersScene.tsx",
          "./src/components/studio/career/CareerScene.tsx",
          "./src/components/studio/trading/TradingScene.tsx",
          "./src/components/studio/leads/LeadsScene.tsx",
          "./src/components/studio/marketing/MarketingWorkspace.tsx",
          "./src/components/studio/MarketingQA.tsx",
          "./src/components/montage/TimelineSpatialPreview.tsx",
          "./src/components/crm/CrmPipelineScene.tsx",
          "./src/components/settings/SettingsView.tsx",
          "./src/components/thread/AgentActivityCluster.tsx",
        ],
      },
      proxy: {
        "/webui": { target, changeOrigin: true },
        "/api": { target, changeOrigin: true },
        "/remotive-api": {
          target: "https://remotive.com",
          changeOrigin: true,
          rewrite: (pathName) => pathName.replace(/^\/remotive-api/, "/api"),
        },
        "/ats-greenhouse": {
          target: "https://boards-api.greenhouse.io",
          changeOrigin: true,
          rewrite: (pathName) => pathName.replace(/^\/ats-greenhouse/, "/v1/boards"),
        },
        "/ats-lever": {
          target: "https://api.lever.co",
          changeOrigin: true,
          rewrite: (pathName) => pathName.replace(/^\/ats-lever/, "/v0/postings"),
        },
        "/ats-ashby": {
          target: "https://api.ashbyhq.com",
          changeOrigin: true,
          rewrite: (pathName) => pathName.replace(/^\/ats-ashby/, "/posting-api/job-board"),
        },
        "/ddg-html": {
          target: "https://html.duckduckgo.com",
          changeOrigin: true,
          rewrite: (pathName) => pathName.replace(/^\/ddg-html/, "/html"),
        },
        "/auth": { target, changeOrigin: true },
        // App WebSocket relay: browsers that cannot reach the gateway port
        // directly (e.g. Windows -> WSL2 localhost forwarding broken for a
        // port) still reach Vite, which forwards inside the same host. HMR
        // lives on its own path (hmrPath) so there is no upgrade conflict.
        "/__navin_ws": {
          target,
          changeOrigin: true,
          ws: true,
          rewrite: (p) => p.replace(/^\/__navin_ws/, "") || "/",
        },
      },
    },
  };
});

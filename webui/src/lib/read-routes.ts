// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Which gateway routes may be retried after a timeout.
 *
 * Every gateway call is a GET, the state-changing ones included, so "retry
 * GETs" would re-run a file save or a board move whose first attempt only
 * *looked* lost. Retrying is therefore an allow-list of pure reads: the
 * listings and status probes each panel loads on open. A stalled engine
 * (its first minute after a restart, mostly) is worth a second and third
 * attempt there, and nowhere else.
 */

const SESSION_READS = new Set([
  "automations",
  "board",
  "checkpoints",
  "checkpoints/diff",
  "context-usage",
  "crm",
  "file-preview",
  "file-search",
  "file-tree",
  "git-blame",
  "git-changes",
  "git-diff",
  "git-log",
  "github/checks",
  "github/issues",
  "github/issues/repo",
  "github/pr",
  "lsp",
  "metagraph",
  "project-brain",
  "project-rules",
  "resume-seed",
  "review-changes",
  "review-file",
  "search",
  "symbols",
  "webui-thread",
]);

/** CRM shares one route; the `action` query names the operation. */
const CRM_READ_ACTIONS = new Set([
  "audit",
  "calendar",
  "channels",
  "dashboard",
  "insights",
  "lines",
  "list",
  "members",
  "products",
  "settings",
  "timeline",
]);

const GLOBAL_READS = new Set([
  "/api/commands",
  "/api/document-templates",
  "/api/media-templates",
  "/api/sessions",
  "/api/settings",
  "/api/settings/cli-apps",
  "/api/settings/mcp-presets",
  "/api/settings/navin-features",
  "/api/settings/ollama",
  "/api/settings/omniroute",
  "/api/settings/pairing",
  "/api/settings/provider-models",
  "/api/settings/usage",
  "/api/webui/account",
  "/api/webui/app-templates",
  "/api/webui/automations",
  "/api/webui/diagnostics",
  "/api/webui/diagnostics/workspace",
  "/api/webui/exec-policy",
  "/api/webui/fs/list",
  "/api/webui/fs/roots",
  "/api/webui/git/status",
  "/api/webui/lsp-servers",
  "/api/webui/lsp-servers/search",
  "/api/webui/marketing-qa/assets",
  "/api/webui/marketing-qa/readiness",
  "/api/webui/marketing-qa/reports",
  "/api/webui/migration/scan",
  "/api/webui/montage/assets",
  "/api/webui/montage/jobs",
  "/api/webui/montage/probe",
  "/api/webui/montage/status",
  "/api/webui/montage/timelines",
  "/api/webui/onboarding",
  "/api/webui/openrouter/status",
  "/api/webui/plugins",
  "/api/webui/preview-discover",
  "/api/webui/preview-logs",
  "/api/webui/processes",
  "/api/webui/runtime/health",
  "/api/webui/skills",
  "/api/workspaces",
]);

/** `<collection>/<id>` detail reads; a verb in the id slot is a mutation. */
const DETAIL_PREFIXES = [
  "/api/webui/app-templates/",
  "/api/webui/automations/",
  "/api/webui/marketing-qa/reports/",
  "/api/webui/skills/",
  "/api/webui/montage/jobs/",
  "/api/webui/montage/timelines/",
];

const MUTATION_VERBS = new Set([
  "accept",
  "activate",
  "apply",
  "cancel",
  "clear",
  "connect-url",
  "convert",
  "create",
  "delete",
  "disable",
  "discover",
  "enable",
  "import",
  "import-workspace",
  "install",
  "invite",
  "kick",
  "kill",
  "logout",
  "mkdir",
  "move",
  "override",
  "pause",
  "put",
  "reject",
  "remove",
  "rename",
  "render",
  "reset",
  "restore",
  "resume",
  "run",
  "save",
  "setup",
  "start",
  "stop",
  "sync",
  "toggle",
  "trigger",
  "uninstall",
  "update",
  "update_settings",
  "upsert",
]);

function pathAndQuery(url: string): { path: string; query: URLSearchParams } {
  const withoutOrigin = url.replace(/^https?:\/\/[^/]+/, "");
  const mark = withoutOrigin.indexOf("?");
  const path = (mark === -1 ? withoutOrigin : withoutOrigin.slice(0, mark)).replace(/\/+$/, "");
  const query = new URLSearchParams(mark === -1 ? "" : withoutOrigin.slice(mark + 1));
  return { path, query };
}

/** True when a timed-out call to *url* can safely be sent again. */
export function isRetryableRead(url: string): boolean {
  const { path, query } = pathAndQuery(url);
  if (GLOBAL_READS.has(path)) return true;

  const session = /^\/api\/sessions\/[^/]+\/(.+)$/.exec(path);
  if (session) {
    const rest = session[1];
    if (rest === "crm") {
      return CRM_READ_ACTIONS.has(query.get("action") ?? "");
    }
    return SESSION_READS.has(rest);
  }

  for (const prefix of DETAIL_PREFIXES) {
    if (!path.startsWith(prefix)) continue;
    const tail = path.slice(prefix.length);
    if (!tail || tail.includes("/")) return false;
    return !MUTATION_VERBS.has(tail);
  }
  return false;
}

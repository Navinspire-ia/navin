/**
 * Panels that Code hosts in its own center area and that a URL can target.
 *
 * These used to be standalone shell modules with their own hash (`#/project`,
 * `#/templates`, `#/evolve`). They act on the project Code already has open,
 * so they moved inside it; the old hashes stay alive as rewrites.
 */
export type CodePanel = "project" | "templates" | "evolve";

const CODE_PANELS: readonly CodePanel[] = ["project", "templates", "evolve"];

/** `#/code?panel=<value>` - anything unknown opens plain Code. */
export function codePanelFromParam(
  value: string | null | undefined,
): CodePanel | null {
  const raw = (value || "").trim().toLowerCase();
  return (CODE_PANELS as readonly string[]).includes(raw)
    ? (raw as CodePanel)
    : null;
}

/** The legacy standalone hash a path stands for, if it is one of them. */
export function legacyCodePanelForPath(path: string): CodePanel | null {
  if (path === "/project" || path === "/home") return "project";
  if (path === "/templates") return "templates";
  if (path === "/evolve") return "evolve";
  return null;
}

/**
 * Where a legacy hash lands inside Code.
 *
 * Every query parameter is carried over rather than rebuilt, so a shared link
 * keeps its `?chat=` (and anything else it held) instead of dropping the
 * session it was taken from.
 */
export function rewriteToCodePanel(
  panel: CodePanel,
  query: string,
  fallbackChatKey: string | null,
): { hash: string; activeKey: string | null } {
  const params = new URLSearchParams(query);
  params.set("panel", panel);
  const qs = params.toString();
  return {
    hash: `#/code${qs ? `?${qs}` : ""}`,
    activeKey: params.get("chat")?.trim() || fallbackChatKey,
  };
}

/** In-app link to a Code panel that keeps the current chat.
 *
 * `#/evolve` used to drop `?chat=`: the rewrite only sees the new hash, so
 * the session (and its pending file edits) vanished when opening Evolve
 * from the review bar.
 */
export function codePanelHref(
  panel: CodePanel,
  chatKey?: string | null,
): string {
  const params = new URLSearchParams();
  const chat = chatKey?.trim();
  if (chat) params.set("chat", chat);
  params.set("panel", panel);
  return `#/code?${params.toString()}`;
}

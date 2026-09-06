/** Durable last module + chat route for restore after restart / cold boot. */

export const LAST_SHELL_ROUTE_KEY = "navin-webui.lastShellRoute";

/** Routes that mean "no real workspace open" - do not restore these. */
export function isEmptyShellHash(hash: string | null | undefined): boolean {
  const raw = (hash || "").trim();
  const normalized = raw.startsWith("#") ? raw.slice(1) : raw;
  return !normalized || normalized === "/" || normalized === "/new";
}

/**
 * Settings overlays are not a "work" destination. Persisting them as the last
 * route made every refresh of `#/new` (or an empty hash) bounce back into
 * Settings after the user left.
 */
export function isEphemeralShellHash(hash: string | null | undefined): boolean {
  if (!hash || isEmptyShellHash(hash)) return true;
  const raw = hash.trim();
  const normalized = raw.startsWith("#") ? raw.slice(1) : raw;
  const path = (normalized.split("?", 1)[0] || "").replace(/\/+$/, "") || "/";
  return (
    path === "/settings" ||
    path === "/tools" ||
    path === "/apps" ||
    path === "/automations" ||
    path === "/skills" ||
    path === "/templates"
  );
}

/** Same idea as {@link isEphemeralShellHash}, for in-memory ShellView ids. */
export function isEphemeralShellView(view: string | null | undefined): boolean {
  const v = (view || "").trim().toLowerCase();
  return (
    v === "settings" ||
    v === "tools" ||
    v === "apps" ||
    v === "automations" ||
    v === "skills" ||
    v === "templates"
  );
}

export function normalizeShellHash(hash: string): string {
  const trimmed = hash.trim();
  if (!trimmed) return "#/new";
  return trimmed.startsWith("#") ? trimmed : `#${trimmed}`;
}

/** True when two hashes are the same route, even if `:` is encoded as `%3A`. */
export function shellHashesEqual(left: string, right: string): boolean {
  const normalize = (hash: string) => {
    const raw = (hash.startsWith("#") ? hash.slice(1) : hash).trim();
    const [path, query = ""] = raw.split("?", 2);
    const params = new URLSearchParams(query);
    const keys = [...new Set(params.keys())].sort();
    const sorted = new URLSearchParams();
    for (const key of keys) {
      for (const value of params.getAll(key)) {
        sorted.append(key, value);
      }
    }
    const qs = sorted.toString();
    const pathNorm = path.startsWith("/") ? path : `/${path}`;
    return `${pathNorm}${qs ? `?${qs}` : ""}`;
  };
  return normalize(left) === normalize(right);
}

export function readLastShellRouteHash(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const stored = window.localStorage.getItem(LAST_SHELL_ROUTE_KEY);
    if (!stored || isEmptyShellHash(stored) || isEphemeralShellHash(stored)) {
      return null;
    }
    return normalizeShellHash(stored);
  } catch {
    return null;
  }
}

export function rememberLastShellRoute(hash: string | null | undefined): void {
  if (typeof window === "undefined") return;
  try {
    if (!hash || isEmptyShellHash(hash) || isEphemeralShellHash(hash)) return;
    window.localStorage.setItem(LAST_SHELL_ROUTE_KEY, normalizeShellHash(hash));
  } catch {
    // private mode / quota
  }
}

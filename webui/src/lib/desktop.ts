/**
 * One place that answers "are we inside the packaged desktop shell?" and
 * "which URL reaches the gateway?".
 *
 * The desktop shell is a Tauri window that starts the local Python gateway and
 * then navigates the webview to `http://127.0.0.1:<port>`. The page origin is
 * therefore the gateway itself, exactly like in a browser: relative URLs work,
 * and `window.location` is the right thing to derive a WebSocket URL from.
 *
 * What does differ is everything the *shell* owns: the Tauri IPC bridge, the
 * native save dialog, the OS folder picker, the link opener. Those used to be
 * detected four different ways (`__TAURI__.core.invoke`, `__TAURI_INTERNALS__`,
 * `__TAURI__.opener`, `window.navinHost`), so a surface could be "desktop" for
 * one helper and "browser" for the next. They all funnel through here now.
 */

/** Gateway port used when there is no page to derive one from (SSR, tests). */
const FALLBACK_GATEWAY_PORT = 8765;
/** Vite dev server port; `strictPort` keeps it fixed. */
export const VITE_DEV_PORT = "5173";
/** Path the Vite dev server proxies to the gateway WebSocket. */
const VITE_WS_PROXY_PREFIX = "/__navin_ws";
/** Hosts a server advertises when it binds every interface. Unusable as a peer. */
const WILDCARD_HOSTS = new Set(["0.0.0.0", "::", "[::]", ""]);

/** Custom-protocol hosts Tauri 2 uses on Windows/Linux. Not a real website. */
const TAURI_APP_HOSTS = new Set([
  "tauri.localhost",
  "ipc.localhost",
  "asset.localhost",
]);

/** macOS custom-protocol schemes. Windows/Linux splash uses http://tauri.localhost. */
const TAURI_CUSTOM_SCHEMES = new Set(["tauri", "ipc", "asset"]);

/** True for ``tauri.localhost`` and the other in-webview Tauri hosts. */
export function isTauriAppHost(hostname: string): boolean {
  return TAURI_APP_HOSTS.has(hostname.trim().toLowerCase());
}

/** True for ``tauri:`` / ``ipc:`` / ``asset:`` (macOS WebView, never Chrome). */
export function isTauriCustomScheme(protocol: string): boolean {
  return TAURI_CUSTOM_SCHEMES.has(protocol.replace(/:$/, "").toLowerCase());
}

/** Loopback + Tauri custom-protocol hosts. Same set as the Rust shell. */
export function isDesktopAppHttpHost(hostname: string): boolean {
  const host = hostname.trim().toLowerCase();
  return (
    host === "127.0.0.1"
    || host === "localhost"
    || host === "::1"
    || host === "[::1]"
    || isTauriAppHost(host)
  );
}

/** True when this URL must stay in the WebView (Linux, Windows, macOS). */
export function isDesktopAppUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    const scheme = parsed.protocol.replace(/:$/, "").toLowerCase();
    if (isTauriCustomScheme(scheme) || ["data", "blob", "about"].includes(scheme)) {
      return true;
    }
    if (scheme !== "http" && scheme !== "https") return false;
    return isDesktopAppHttpHost(parsed.hostname);
  } catch {
    return false;
  }
}

/** True when opening this URL in Chrome would show a blank ``tauri.localhost`` page. */
export function isInternalDesktopUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    if (isTauriCustomScheme(parsed.protocol)) return true;
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return false;
    return isTauriAppHost(parsed.hostname);
  } catch {
    return false;
  }
}

/**
 * Ports Navin itself listens on. A project Vite/Next on 5174/5176 is not this.
 * Dynamic gateway ports are covered by comparing to the current page origin.
 */
const NAVIN_EDITOR_PORTS = new Set(["8765", "8766", "18790", "18791"]);

function urlPort(parsed: URL): string {
  return parsed.port || (parsed.protocol === "https:" ? "443" : "80");
}

/**
 * True when this URL is the Navin IDE (splash, gateway, Vite editor), not a
 * project preview. ``window.open("http://127.0.0.1:5176")`` used to match
 * ``isDesktopAppUrl`` and the shell then loaded it into the main WebView,
 * covering the whole IDE. Project loopback must leave for the OS browser.
 */
export function isNavinShellUrl(
  url: string,
  currentHref: string = typeof window !== "undefined" ? window.location.href : "",
): boolean {
  try {
    const parsed = new URL(url);
    const scheme = parsed.protocol.replace(/:$/, "").toLowerCase();
    if (isTauriCustomScheme(parsed.protocol) || ["data", "blob", "about"].includes(scheme)) {
      return true;
    }
    if (scheme !== "http" && scheme !== "https") return false;
    if (isTauriAppHost(parsed.hostname)) return true;
    if (!isDesktopAppHttpHost(parsed.hostname)) return false;
    if (NAVIN_EDITOR_PORTS.has(urlPort(parsed))) return true;
    if (!currentHref) return false;
    const current = new URL(currentHref);
    if (!isDesktopAppHttpHost(current.hostname)) return false;
    return (
      current.hostname.toLowerCase() === parsed.hostname.toLowerCase()
      && urlPort(current) === urlPort(parsed)
    );
  } catch {
    return false;
  }
}

export type TauriInvoke = (
  cmd: string,
  args?: Record<string, unknown>,
) => Promise<unknown>;

interface TauriWindow {
  __TAURI__?: {
    core?: { invoke?: TauriInvoke };
    opener?: { openUrl?: (url: string) => Promise<unknown> };
  };
  __TAURI_INTERNALS__?: unknown;
  navinHost?: unknown;
}

function tauriWindow(): TauriWindow | null {
  if (typeof window === "undefined") return null;
  return window as unknown as TauriWindow;
}

/** The Tauri IPC entry point, or null outside the shell. */
export function tauriInvoke(): TauriInvoke | null {
  const invoke = tauriWindow()?.__TAURI__?.core?.invoke;
  return typeof invoke === "function" ? invoke : null;
}

/** The native link opener, or null when the capability is not granted. */
export function tauriOpenUrl(): ((url: string) => Promise<unknown>) | null {
  const openUrl = tauriWindow()?.__TAURI__?.opener?.openUrl;
  return typeof openUrl === "function" ? openUrl : null;
}

/**
 * True inside the packaged desktop shell.
 *
 * Any of the three markers is enough: `withGlobalTauri` exposes `__TAURI__`,
 * the IPC bootstrap always defines `__TAURI_INTERNALS__`, and `navinHost` is
 * the older embedded-host bridge. A shell that exposes only one of them is
 * still a shell, and treating it as a browser is what silently disabled native
 * downloads and pickers on some builds.
 */
export function isDesktopShell(): boolean {
  const win = tauriWindow();
  if (!win) return false;
  return (
    tauriInvoke() != null
    || win.__TAURI_INTERNALS__ != null
    || win.navinHost != null
  );
}

/** True when a Tauri command can be invoked (a strict subset of the above). */
export function tauriIpcAvailable(): boolean {
  return tauriInvoke() != null || tauriWindow()?.__TAURI_INTERNALS__ != null;
}

// -- Gateway URL resolution --------------------------------------------------

/** The parts of `window.location` this module needs, so it stays testable. */
export interface PageLocation {
  protocol: string;
  hostname: string;
  port: string;
  host: string;
}

export interface GatewayUrlOptions {
  /** WebSocket path the gateway registered, e.g. `/` or `/ws`. */
  wsPath: string;
  /** Short-lived WebSocket token from `/webui/bootstrap`. */
  token: string;
  /** `ws_url` the gateway advertises for itself, when it advertises one. */
  advertisedWsUrl?: string | null;
  /** Page location; `null` means there is no page (SSR, unit tests). */
  location?: PageLocation | null;
  /**
   * True only for a bundle served by the Vite dev server. A packaged build is
   * never a dev bundle, which is what keeps a gateway that happens to listen
   * on 5173 from being mistaken for the dev proxy.
   */
  devBundle?: boolean;
  /** Explicit runtime contract. When omitted it is derived from the same inputs. */
  profile?: RuntimeProfile;
}

export type RuntimeProfileName =
  | "browser-direct"
  | "browser-vite"
  | "desktop-http"
  | "desktop-bridge";

export type RuntimeWsTransport = "gateway-origin" | "vite-proxy" | "native-bridge";

/**
 * Stable runtime contract consumed by URL resolution.
 *
 * Keeping this as data rather than scattered environment checks makes every
 * supported launch mode testable without a browser or a Tauri process.
 */
export interface RuntimeProfile {
  name: RuntimeProfileName;
  wsTransport: RuntimeWsTransport;
  httpBase: "";
}

export interface RuntimeProfileOptions {
  advertisedWsUrl?: string | null;
  desktopShell?: boolean;
  devBundle?: boolean;
  location?: PageLocation | null;
}

export function resolveRuntimeProfile(options: RuntimeProfileOptions = {}): RuntimeProfile {
  if (options.advertisedWsUrl?.startsWith("navin-host://")) {
    return { name: "desktop-bridge", wsTransport: "native-bridge", httpBase: "" };
  }
  if (options.devBundle && options.location?.port === VITE_DEV_PORT) {
    return { name: "browser-vite", wsTransport: "vite-proxy", httpBase: "" };
  }
  if (options.desktopShell ?? isDesktopShell()) {
    return { name: "desktop-http", wsTransport: "gateway-origin", httpBase: "" };
  }
  return { name: "browser-direct", wsTransport: "gateway-origin", httpBase: "" };
}

/** Read the live page location, or null when there is no document. */
export function currentPageLocation(): PageLocation | null {
  if (typeof window === "undefined" || !window.location) return null;
  const { protocol, hostname, port, host } = window.location;
  return { protocol, hostname, port, host };
}

/** Wrap a bare IPv6 address in brackets so it can go in an authority. */
function authorityHost(hostname: string): string {
  if (hostname.includes(":") && !hostname.startsWith("[")) {
    return `[${hostname}]`;
  }
  return hostname;
}

function appendQueryToken(url: string, token: string): string {
  if (!token) return url;
  const join = url.includes("?") ? "&" : "?";
  return `${url}${join}token=${encodeURIComponent(token)}`;
}

function normalizeWsPath(wsPath: string): string {
  if (!wsPath) return "/";
  return wsPath.startsWith("/") ? wsPath : `/${wsPath}`;
}

/**
 * Rewrite a wildcard bind address to a host the page can actually reach.
 *
 * A gateway told to listen on every interface advertises `ws://0.0.0.0:8766`.
 * That is a bind address, not a destination: connecting to it fails on Windows
 * and macOS, and only works on Linux by accident. The page's own hostname is
 * the address that provably reaches this gateway - it just served the page.
 */
function repairWildcardHost(
  advertised: string,
  location: PageLocation | null,
): string {
  if (!location) return advertised;
  const match = /^(wss?:\/\/)(\[[^\]]*\]|[^/:?#]*)(:\d+)?(.*)$/i.exec(advertised);
  if (!match) return advertised;
  const [, scheme, host, port = "", rest = ""] = match;
  if (!WILDCARD_HOSTS.has(host.toLowerCase())) return advertised;
  return `${scheme}${authorityHost(location.hostname)}${port}${rest}`;
}

/**
 * Build the WebSocket URL for the app stream.
 *
 * Order of preference:
 * 1. The Vite dev proxy, and only for a real dev bundle served on :5173. The
 *    browser reached :5173 to load the page, while the gateway port may be
 *    unreachable from its host (Windows -> WSL2 forwarding).
 * 2. The address the gateway advertises for itself, with a wildcard bind
 *    address repaired into a reachable host.
 * 3. The page origin. In the desktop shell this is always the right answer:
 *    the webview was navigated to the gateway, so its origin is the gateway.
 */
export function resolveGatewayWsUrl(options: GatewayUrlOptions): string {
  const {
    wsPath,
    token,
    advertisedWsUrl,
    devBundle = false,
  } = options;
  const location =
    options.location === undefined ? currentPageLocation() : options.location;
  const path = normalizeWsPath(wsPath);
  const profile = options.profile ?? resolveRuntimeProfile({
    advertisedWsUrl,
    devBundle,
    location,
  });

  if (profile.wsTransport === "vite-proxy" && location) {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const host = authorityHost(location.hostname);
    const suffix = path === "/" ? "" : path;
    return appendQueryToken(
      `${scheme}://${host}:${VITE_DEV_PORT}${VITE_WS_PROXY_PREFIX}${suffix}`,
      token,
    );
  }

  if (advertisedWsUrl && /^(wss?|navin-host):\/\//i.test(advertisedWsUrl)) {
    return appendQueryToken(
      repairWildcardHost(advertisedWsUrl, location),
      token,
    );
  }

  if (!location) {
    return appendQueryToken(
      `ws://127.0.0.1:${FALLBACK_GATEWAY_PORT}${path}`,
      token,
    );
  }

  const scheme = location.protocol === "https:" ? "wss" : "ws";
  return appendQueryToken(`${scheme}://${location.host}${path}`, token);
}

/**
 * Base for gateway HTTP requests.
 *
 * Always the empty string: the page is served by the gateway in every runtime
 * (browser, Vite proxy, desktop shell), so a relative path is both correct and
 * the only form that survives a dynamic port. Exported so call sites have
 * somewhere to point instead of reinventing an origin from `window.location`.
 */
export function gatewayHttpBase(): string {
  return "";
}

/** Absolute gateway origin, for the rare caller that cannot use a relative URL. */
export function gatewayOrigin(location?: PageLocation | null): string {
  const page = location === undefined ? currentPageLocation() : location;
  if (!page) return `http://127.0.0.1:${FALLBACK_GATEWAY_PORT}`;
  return `${page.protocol}//${page.host}`;
}

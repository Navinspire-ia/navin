import i18n from "@/i18n";

export const DEFAULT_HTTP_TIMEOUT_MS = 20_000;

/**
 * Transport failures carry a message written for the person reading it.
 *
 * Panels show `error.message` next to whatever failed, so the text that ends
 * up on screen is decided here. "Request timed out after 20000ms" told a user
 * nothing they could act on and read like a browser fault; these say which
 * side is slow and that waiting usually fixes it. Code that needs to branch on
 * the kind of failure uses the `is*Error` helpers below, never the wording.
 */
export class RequestTimeoutError extends Error {
  readonly timeoutMs: number;
  readonly url: string;

  constructor(url: string, timeoutMs: number) {
    super(transportMessage("timeout", { seconds: Math.round(timeoutMs / 1000) }));
    this.name = "RequestTimeoutError";
    this.timeoutMs = timeoutMs;
    this.url = url;
  }
}

/** fetch's opaque TypeError, wrapped so the screen text names the engine. */
export class GatewayUnreachableError extends Error {
  readonly url: string;

  constructor(url: string, cause?: unknown) {
    super(transportMessage("unreachable"), { cause });
    this.name = "GatewayUnreachableError";
    this.url = url;
  }
}

type TransportKey = "timeout" | "unreachable" | "internal" | "htmlInsteadOfJson" | "nonJson" | "httpStatus";

const TRANSPORT_DEFAULTS: Record<TransportKey, string> = {
  timeout: "The Navin engine took too long to answer. Try again in a moment.",
  unreachable: "The Navin engine is not reachable right now. It is usually back within a few seconds.",
  internal: "The Navin engine hit an internal error. Try again; if it persists, restart Navin.",
  htmlInsteadOfJson:
    "The Navin engine answered with the app page instead of data. Restart Navin and try again.",
  nonJson: "The Navin engine returned an unexpected answer.",
  httpStatus: "The Navin engine answered with an error (HTTP {{status}}).",
};

/** Human wording for a transport condition, in the UI language. */
export function transportMessage(
  key: TransportKey,
  values: Record<string, string | number> = {},
): string {
  return i18n.t(`transport.${key}`, { defaultValue: TRANSPORT_DEFAULTS[key], ...values });
}

function errorName(error: unknown): string {
  return typeof error === "object" && error !== null && "name" in error
    ? String((error as { name?: unknown }).name ?? "")
    : "";
}

/** The request hit its deadline (ours, not the caller's abort). */
export function isTimeoutError(error: unknown): boolean {
  if (error instanceof RequestTimeoutError) return true;
  if (errorName(error) === "RequestTimeoutError") return true;
  // Older callers still raise plain Errors with the historical wording.
  return error instanceof Error && /timed out( after \d+ms)?$/i.test(error.message);
}

/** The engine could not be reached at all (connection refused, DNS, offline). */
export function isUnreachableError(error: unknown): boolean {
  if (error instanceof GatewayUnreachableError) return true;
  if (errorName(error) === "GatewayUnreachableError") return true;
  return (
    error instanceof TypeError &&
    /failed to fetch|network ?error|load failed|networkerror/i.test(error.message)
  );
}

/** Timeout or unreachable: the gateway, not the request, is the problem. */
export function isTransportError(error: unknown): boolean {
  return isTimeoutError(error) || isUnreachableError(error);
}

function linkAbort(controller: AbortController, signal: AbortSignal | null | undefined): () => void {
  if (!signal) return () => {};
  if (signal.aborted) {
    controller.abort(signal.reason);
    return () => {};
  }
  const forward = () => controller.abort(signal.reason);
  signal.addEventListener("abort", forward, { once: true });
  return () => signal.removeEventListener("abort", forward);
}

export async function fetchWithTimeout(
  input: RequestInfo | URL,
  init: RequestInit = {},
  timeoutMs: number = DEFAULT_HTTP_TIMEOUT_MS,
): Promise<Response> {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    return fetch(input, init);
  }

  const controller = typeof AbortController !== "undefined"
    ? new AbortController()
    : null;
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  // The caller's own signal used to be dropped when a deadline was set, so a
  // panel unmounting could not cancel its request. Both now abort the fetch.
  const unlink = controller ? linkAbort(controller, init.signal) : () => {};

  const request = fetch(input, {
    ...init,
    signal: controller?.signal ?? init.signal,
  });
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  const timeout = new Promise<Response>((_, reject) => {
    timeoutId = setTimeout(() => {
      reject(new RequestTimeoutError(url, timeoutMs));
      controller?.abort();
    }, timeoutMs);
  });

  try {
    return await Promise.race([request, timeout]);
  } finally {
    if (timeoutId !== undefined) clearTimeout(timeoutId);
    unlink();
  }
}

/**
 * Reading the checkpoint API's failures.
 *
 * Most gateway errors arrive as plain text and are shown as they are. The ones
 * the checkpoint API can predict - a WSL project with no reachable
 * distribution, a distribution that did not answer, a missing git - arrive as
 * JSON carrying a stable code, because the English sentence the gateway wrote
 * is a fallback and not what a French user should end up reading.
 */

export type CheckpointFailure = {
  /** Stable identifier for a known failure, or null for anything else. */
  code: string | null;
  /** Human-readable text: the gateway's English sentence, or the raw body. */
  message: string;
};

export function parseCheckpointError(error: unknown): CheckpointFailure {
  const raw =
    error instanceof Error ? error.message : String(error ?? "");
  const text = raw.trim();
  if (!text.startsWith("{")) return { code: null, message: text };
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { code: null, message: text };
  }
  if (typeof parsed !== "object" || parsed === null) {
    return { code: null, message: text };
  }
  const body = parsed as { error?: unknown; code?: unknown };
  return {
    code: typeof body.code === "string" && body.code ? body.code : null,
    message: typeof body.error === "string" && body.error ? body.error : text,
  };
}

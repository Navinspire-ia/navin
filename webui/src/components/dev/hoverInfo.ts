/**
 * Whether a hover payload is worth painting as a tooltip.
 *
 * The index fallback used to echo the identifier when it had no definition.
 * On a YAML key that looks like a word (`requests`, `resources`, `cpu`) the
 * tooltip then drew that same word on a grey bar just under the line, on
 * every mouse pass. Language servers do the same with markdown wrappers
 * (`requests` or a one-word fence), so those are unwrapped before comparing.
 * Silence is better than a mirror.
 */
export function unwrapHoverEcho(text: string): string {
  let value = text.trim();
  const fenced = /^```[^\n]*\r?\n?([\s\S]*?)\r?\n?```$/.exec(value);
  if (fenced) value = fenced[1].trim();
  if (value.startsWith("`") && value.endsWith("`") && value.length >= 2) {
    value = value.slice(1, -1).trim();
  }
  if (
    (value.startsWith("**") && value.endsWith("**") && value.length >= 4) ||
    (value.startsWith("__") && value.endsWith("__") && value.length >= 4)
  ) {
    value = value.slice(2, -2).trim();
  }
  return value;
}

export function isUsefulHoverText(
  text: string,
  symbol: string,
  sourceLine?: string,
): boolean {
  const trimmed = text.trim();
  if (!trimmed) return false;
  const echoed = unwrapHoverEcho(trimmed);
  if (echoed === symbol || trimmed === symbol) return false;
  if (sourceLine) {
    const line = sourceLine.trim();
    if (echoed === line || trimmed === line) return false;
    const key = line.replace(/:$/, "").trim();
    if (key && (echoed === key || trimmed === key)) return false;
  }
  return true;
}

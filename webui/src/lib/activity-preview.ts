import { shortenArgValue } from "@/lib/short-path";

/** Keys shown on a generic activity row, in priority order. */
export const GENERIC_ARG_PREVIEW_KEYS = [
  "target",
  "runner",
  "query",
  "glob",
  "pattern",
  "path",
  "file_path",
  "paths",
  "url",
  "name",
  "id",
  "title",
  "question",
  "message",
  "text",
  "command",
  "extra",
  "cwd",
  "suite",
  "test_target",
  "action",
] as const;

const MAX_ARG_ENTRIES = 3;

export function previewGenericToolArgs(args: string, shorten = true): string {
  const compactArgs = args.trim();
  if (!compactArgs) return "";
  try {
    return previewGenericArgsObject(JSON.parse(compactArgs) as unknown, shorten);
  } catch {
    const stripped = compactArgs.replace(/^["']|["']$/g, "");
    return maybeShorten(stripped, shorten);
  }
}

export function previewGenericArgsObject(argsObject: unknown, shorten = true): string {
  if (!argsObject || typeof argsObject !== "object" || Array.isArray(argsObject)) {
    return previewArgValue(argsObject, shorten) ?? "";
  }
  const record = argsObject as Record<string, unknown>;
  const entries: string[] = [];
  for (const key of GENERIC_ARG_PREVIEW_KEYS) {
    if (key === "action" && entries.length > 0) continue;
    const preview = previewArgValue(record[key], shorten);
    if (preview) entries.push(`${key}: ${preview}`);
    if (entries.length >= MAX_ARG_ENTRIES) return entries.join(" · ");
  }
  return entries.join(" · ");
}

export function toolEventOutputText(event: {
  result?: unknown;
  output?: unknown;
} | null | undefined): string {
  if (!event) return "";
  return stringifyToolPayload(event.result) || stringifyToolPayload(event.output);
}

export function firstOutputLine(text: string): string {
  return text
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((line) => line.trim())
    .find(Boolean) ?? "";
}

export function tailToolOutput(text: string, maxChars: number): string {
  const normalized = text.replace(/\r\n/g, "\n").trimEnd();
  if (normalized.length <= maxChars) return normalized;
  const tail = normalized.slice(-maxChars);
  const firstBreak = tail.indexOf("\n");
  return `…${firstBreak >= 0 ? tail.slice(firstBreak + 1) : tail}`;
}

function previewArgValue(value: unknown, shorten: boolean): string | null {
  if (typeof value === "string" && value.trim()) {
    return maybeShorten(value.trim(), shorten);
  }
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    const items = value
      .map((item) => previewArgValue(item, shorten))
      .filter((item): item is string => Boolean(item));
    if (!items.length) return null;
    const shown = items.slice(0, 2).join(", ");
    return items.length > 2 ? `${shown} +${items.length - 2}` : shown;
  }
  return null;
}

function stringifyToolPayload(value: unknown): string {
  if (typeof value === "string") return value.trim() ? value : "";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (!value || typeof value !== "object") return "";
  if (Array.isArray(value)) {
    return value.map((item) => stringifyToolPayload(item)).filter(Boolean).join("\n");
  }
  const record = value as Record<string, unknown>;
  for (const key of ["content", "output", "text", "result", "message"]) {
    const inner = stringifyToolPayload(record[key]);
    if (inner) return inner;
  }
  return "";
}

function maybeShorten(value: string, shorten: boolean): string {
  return shorten ? shortenArgValue(value) : value;
}

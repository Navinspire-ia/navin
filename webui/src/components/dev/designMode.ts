/**
 * Design mode for the preview browser: the probe injected in the previewed
 * page describes the element the user clicked, and this module turns that
 * description into what the workbench needs - a label for the popover, the
 * place to draw it, the source file to attach and the instruction the agent
 * receives.
 */

import type { ProjectFileMatch } from "@/lib/types";

export interface PickedElementSource {
  /** Absolute file, dev-server path (`/src/App.tsx`) or Vite `/@fs/` URL. */
  file: string;
  /** 1-based line when the framework knows it, otherwise 0. */
  line: number;
}

export interface PickedElement {
  tag: string;
  id: string;
  classes: string;
  testId: string;
  text: string;
  selector: string;
  /** Position inside the preview viewport, in CSS pixels. */
  rect: { x: number; y: number; width: number; height: number };
  viewport: { width: number; height: number };
  /** Component that rendered the element, when a framework exposes it. */
  component: string;
  /** Component ancestry, nearest first. */
  ancestors: string[];
  framework: string;
  source: PickedElementSource | null;
  attributes: Record<string, string>;
  url: string;
  title: string;
}

const MAX_TEXT = 200;
const MAX_CLASSES = 300;

function str(value: unknown, max = 500): string {
  return typeof value === "string" ? value.slice(0, max) : "";
}

function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? Math.round(value) : 0;
}

/** Validates an element description posted by the probe; `null` when malformed. */
export function parsePickedElement(raw: unknown): PickedElement | null {
  if (!raw || typeof raw !== "object") return null;
  const data = raw as Record<string, unknown>;
  const tag = str(data.tag, 40).toLowerCase();
  if (!tag) return null;
  const rect = (data.rect ?? {}) as Record<string, unknown>;
  const viewport = (data.viewport ?? {}) as Record<string, unknown>;
  const source = data.source as Record<string, unknown> | null | undefined;
  const attributes: Record<string, string> = {};
  if (data.attributes && typeof data.attributes === "object") {
    for (const [key, value] of Object.entries(data.attributes as Record<string, unknown>)) {
      if (typeof value === "string" && value) attributes[key.slice(0, 40)] = value.slice(0, 200);
    }
  }
  const ancestors = Array.isArray(data.ancestors)
    ? data.ancestors.filter((name): name is string => typeof name === "string" && name !== "").slice(0, 6)
    : [];
  return {
    tag,
    id: str(data.id, 120),
    classes: str(data.classes, MAX_CLASSES),
    testId: str(data.testId, 120),
    text: str(data.text, MAX_TEXT),
    selector: str(data.selector, 400),
    rect: { x: num(rect.x), y: num(rect.y), width: num(rect.width), height: num(rect.height) },
    viewport: { width: num(viewport.width), height: num(viewport.height) },
    component: str(data.component, 120),
    ancestors,
    framework: str(data.framework, 20),
    source:
      source && typeof source === "object" && typeof source.file === "string" && source.file
        ? { file: source.file.slice(0, 500), line: num(source.line) }
        : null,
    attributes,
    url: str(data.url, 500),
    title: str(data.title, 200),
  };
}

/** "ChatList · div", or the tag with its id / first class when no component is known. */
export function pickedElementLabel(element: PickedElement): string {
  if (element.component) return `${element.component} \u00b7 ${element.tag}`;
  if (element.id) return `${element.tag}#${element.id}`;
  const firstClass = element.classes.split(" ").find((name) => name && !name.includes(":"));
  return firstClass ? `${element.tag}.${firstClass}` : element.tag;
}

export interface PopoverPlacement {
  left: number;
  top: number;
  /** True when the popover sits above the element instead of below it. */
  above: boolean;
}

/**
 * Where to draw the prompt next to the picked element: below it, left-aligned,
 * unless that would leave the frame, in which case above, and always clamped
 * to the frame with a small margin.
 */
export function popoverPlacement(
  rect: PickedElement["rect"],
  frame: { width: number; height: number },
  popover: { width: number; height: number },
  gap = 8,
  margin = 8,
): PopoverPlacement {
  const maxLeft = Math.max(margin, frame.width - popover.width - margin);
  const left = Math.min(Math.max(margin, rect.x), maxLeft);
  const below = rect.y + rect.height + gap;
  const fitsBelow = below + popover.height + margin <= frame.height;
  const aboveTop = rect.y - gap - popover.height;
  const fitsAbove = aboveTop >= margin;
  if (fitsBelow || !fitsAbove) {
    const maxTop = Math.max(margin, frame.height - popover.height - margin);
    return { left, top: Math.min(below, maxTop), above: false };
  }
  return { left, top: aboveTop, above: true };
}

export interface SourceLookup {
  /** Project-relative path when the file is under the project root. */
  relative: string | null;
  /** Path to match against the end of project paths (`src/App.tsx`). */
  suffix: string;
  name: string;
}

/**
 * Normalizes what a framework reports as the source file. React 18 gives an
 * absolute path, React 19 and Vue give the dev-server URL of the module
 * (`http://localhost:5173/src/App.tsx?t=123` or `/@fs/abs/path.tsx`).
 */
export function sourceLookup(file: string, projectPath?: string | null): SourceLookup {
  let path = file.trim();
  try {
    if (/^https?:\/\//i.test(path)) path = new URL(path).pathname;
  } catch {
    // Not a URL: keep the raw text.
  }
  path = path.split("?")[0]?.split("#")[0] ?? "";
  if (path.startsWith("/@fs/")) path = path.slice("/@fs".length);
  path = path.replace(/\\/g, "/");
  const base = (projectPath ?? "").replace(/\\/g, "/").replace(/\/+$/, "");
  let relative: string | null = null;
  if (base && path.startsWith(`${base}/`)) relative = path.slice(base.length + 1);
  const suffix = relative ?? path.replace(/^\/+/, "");
  const name = suffix.split("/").pop() ?? suffix;
  return { relative, suffix, name };
}

/** Picks the project file whose path ends with the reported source path. */
export function matchSourceFile(
  lookup: SourceLookup,
  items: readonly ProjectFileMatch[],
): ProjectFileMatch | null {
  if (lookup.relative) {
    const exact = items.find((item) => item.kind === "file" && item.path === lookup.relative);
    if (exact) return exact;
    return { path: lookup.relative, name: lookup.name, kind: "file" };
  }
  if (!lookup.suffix) return null;
  const files = items.filter((item) => item.kind === "file");
  return (
    files.find((item) => item.path === lookup.suffix) ??
    files.find((item) => item.path.endsWith(`/${lookup.suffix}`)) ??
    null
  );
}

function quote(text: string): string {
  return `"${text.replace(/"/g, '\\"')}"`;
}

/**
 * The message the agent receives: the user's words first, then a compact,
 * factual description of the element so it can find the code without asking.
 */
export function designModePrompt(
  element: PickedElement,
  instruction: string,
  sourcePath: string | null,
): string {
  const lines: string[] = [];
  const request = instruction.trim();
  lines.push(request || "Improve this element.");
  lines.push("");
  const where = element.url ? ` on ${element.url}` : "";
  lines.push(`Element picked in the preview (design mode)${where}:`);
  const component = element.component
    ? `${element.component} <${element.tag}>`
    : `<${element.tag}>`;
  const ancestry = element.ancestors.filter((name) => name !== element.component).slice(0, 4);
  lines.push(
    `- Component: ${component}${ancestry.length ? `, inside ${ancestry.join(" > ")}` : ""}`,
  );
  if (sourcePath) {
    const at = element.source?.line ? ` (around line ${element.source.line})` : "";
    lines.push(`- Source: @${sourcePath}${at}`);
  } else if (element.source?.file) {
    lines.push(`- Source (as served by the dev server): ${element.source.file}`);
  }
  if (element.selector) lines.push(`- Selector: ${element.selector}`);
  if (element.testId) lines.push(`- data-testid: ${element.testId}`);
  if (element.text) lines.push(`- Text: ${quote(element.text)}`);
  if (element.classes) lines.push(`- Classes: ${element.classes}`);
  const attrs = Object.entries(element.attributes)
    .filter(([key]) => key !== "class")
    .map(([key, value]) => `${key}=${quote(value)}`);
  if (attrs.length) lines.push(`- Attributes: ${attrs.join(", ")}`);
  if (element.rect.width || element.rect.height) {
    const viewport =
      element.viewport.width && element.viewport.height
        ? ` in a ${element.viewport.width} x ${element.viewport.height} viewport`
        : "";
    lines.push(
      `- Box: ${element.rect.width} x ${element.rect.height} px at (${element.rect.x}, ${element.rect.y})${viewport}`,
    );
  }
  lines.push("");
  lines.push(
    "Locate this element in the source (component name, class names, text), make the change there, and keep the surrounding design consistent.",
  );
  return lines.join("\n");
}

// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Review annotations: the user selects a passage of an assistant answer or of
 * a file, attaches a quick action and/or a comment, and sends the whole batch
 * to the agent as one message.
 *
 * This module owns the model, the per-conversation draft store (persisted so a
 * reload keeps a review in progress), the text anchoring used to find a
 * passage again, and the wire format of the feedback message.
 */

export type AnnotationAction =
  | "clarify"
  | "example"
  | "alternative"
  | "keep"
  | "remove"
  | "expand"
  | "comment";

export interface AnnotationActionSpec {
  id: AnnotationAction;
  /** Single key that applies the action while the toolbar is open. */
  key: string;
  /** English label: shown in the UI by default and written to the agent. */
  label: string;
}

/** The quick actions offered on a selection, in toolbar order. */
export const ANNOTATION_ACTIONS: readonly AnnotationActionSpec[] = [
  { id: "clarify", key: "c", label: "Clarify" },
  { id: "example", key: "e", label: "Give an example" },
  { id: "alternative", key: "a", label: "Find an alternative" },
  { id: "keep", key: "k", label: "Good, keep this" },
  { id: "remove", key: "r", label: "Remove this" },
  { id: "expand", key: "x", label: "Expand" },
];

const COMMENT_LABEL = "Comment";

export function annotationActionLabel(action: AnnotationAction): string {
  return ANNOTATION_ACTIONS.find((spec) => spec.id === action)?.label ?? COMMENT_LABEL;
}

function actionFromLabel(label: string): AnnotationAction {
  const wanted = label.trim().toLowerCase();
  return ANNOTATION_ACTIONS.find((spec) => spec.label.toLowerCase() === wanted)?.id ?? "comment";
}

export type AnnotationSource =
  | {
      kind: "message";
      /** Message id at the time of the annotation (may change after a reload). */
      id: string;
      /** Content fingerprint: finds the message again when its id changed. */
      key: string;
    }
  | {
      kind: "file";
      /** Project-relative path. */
      path: string;
      /** 1-based line range, when the selection came from the source view. */
      fromLine?: number;
      toLine?: number;
    };

export interface Annotation {
  id: string;
  source: AnnotationSource;
  /** Exact selected text, as it reads in the document. */
  quote: string;
  /** Character offset of the quote in its document, used to order and re-find it. */
  offset: number;
  action: AnnotationAction;
  comment: string;
  createdAt: number;
}

export const ANNOTATION_QUOTE_MAX_CHARS = 4000;
const MAX_ANNOTATIONS_PER_SCOPE = 200;
const STORAGE_PREFIX = "navin.webui.annotations.v1:";

// ---------------------------------------------------------------------------
// Text anchoring
// ---------------------------------------------------------------------------

/** Cheap stable fingerprint of a message body (FNV-1a over its text). */
export function contentFingerprint(text: string): string {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return `${text.length.toString(36)}-${(hash >>> 0).toString(36)}`;
}

const WORD_CHAR = /[\p{L}\p{N}_]/u;

/**
 * Grows a selection to whole words and drops surrounding whitespace, so a
 * drag that starts mid-word still quotes something readable.
 */
export function snapToWords(
  text: string,
  start: number,
  end: number,
): { start: number; end: number } {
  let from = Math.max(0, Math.min(start, end));
  let to = Math.min(text.length, Math.max(start, end));
  while (from < to && /\s/.test(text[from])) from += 1;
  while (to > from && /\s/.test(text[to - 1])) to -= 1;
  if (from >= to) return { start: from, end: from };
  while (from > 0 && WORD_CHAR.test(text[from]) && WORD_CHAR.test(text[from - 1])) from -= 1;
  while (to < text.length && WORD_CHAR.test(text[to - 1]) && WORD_CHAR.test(text[to])) to += 1;
  return { start: from, end: to };
}

/**
 * Finds where `quote` sits in `text`. The stored offset wins when the text is
 * unchanged; otherwise the occurrence nearest to it is used. -1 when the
 * passage is gone.
 */
export function locateQuote(text: string, quote: string, offsetHint: number): number {
  if (!quote) return -1;
  if (offsetHint >= 0 && text.startsWith(quote, offsetHint)) return offsetHint;
  let best = -1;
  let bestDistance = Number.POSITIVE_INFINITY;
  let from = text.indexOf(quote);
  while (from !== -1) {
    const distance = Math.abs(from - offsetHint);
    if (distance < bestDistance) {
      best = from;
      bestDistance = distance;
    }
    from = text.indexOf(quote, from + 1);
  }
  return best;
}

// ---------------------------------------------------------------------------
// Feedback message
// ---------------------------------------------------------------------------

const REVIEW_HEADER_RE = /^\[Review\] (\d+) annotations?\b/;
const REVIEW_FOOTER =
  "Address each annotation in order. Leave every passage marked \"Good, keep this\" unchanged. "
  + "If this reviews a plan, reply with the revised plan only and do not start building.";

function sourceRank(source: AnnotationSource): string {
  return source.kind === "message" ? `m:${source.key}` : `f:${source.path}`;
}

function positionOf(annotation: Annotation): number {
  const { source } = annotation;
  if (source.kind === "file" && source.fromLine) return source.fromLine * 1_000_000 + annotation.offset;
  return annotation.offset;
}

/** Document order: sources in the order they were first annotated, then position. */
export function sortAnnotations(annotations: readonly Annotation[]): Annotation[] {
  const firstSeen = new Map<string, number>();
  for (const annotation of [...annotations].sort((a, b) => a.createdAt - b.createdAt)) {
    const rank = sourceRank(annotation.source);
    if (!firstSeen.has(rank)) firstSeen.set(rank, firstSeen.size);
  }
  return [...annotations].sort((a, b) => {
    const bySource = (firstSeen.get(sourceRank(a.source)) ?? 0) - (firstSeen.get(sourceRank(b.source)) ?? 0);
    if (bySource) return bySource;
    return positionOf(a) - positionOf(b) || a.createdAt - b.createdAt;
  });
}

export function annotationLocation(source: AnnotationSource): string {
  if (source.kind !== "file") return "";
  if (!source.fromLine) return `@${source.path}`;
  const range = source.toLine && source.toLine !== source.fromLine
    ? `L${source.fromLine}-${source.toLine}`
    : `L${source.fromLine}`;
  return `@${source.path} (${range})`;
}

function quoteLines(quote: string): string[] {
  return quote.replace(/\s+$/, "").split(/\r?\n/).map((line) => `> ${line}`);
}

/** The single user message that carries a batch of annotations to the agent. */
export function formatReviewMessage(annotations: readonly Annotation[]): string {
  const ordered = sortAnnotations(annotations);
  if (!ordered.length) return "";
  const hasMessage = ordered.some((annotation) => annotation.source.kind === "message");
  const hasFile = ordered.some((annotation) => annotation.source.kind === "file");
  const target = hasMessage && hasFile
    ? "on your answer and on files"
    : hasFile ? "on files" : "on your answer";
  const lines = [`[Review] ${ordered.length} annotation${ordered.length === 1 ? "" : "s"} ${target}`];
  ordered.forEach((annotation, index) => {
    const location = annotationLocation(annotation.source);
    lines.push("");
    lines.push(`${index + 1}. ${annotationActionLabel(annotation.action)}${location ? ` - ${location}` : ""}`);
    lines.push(...quoteLines(annotation.quote));
    const comment = annotation.comment.trim();
    if (comment) {
      const [first, ...rest] = comment.split(/\r?\n/);
      lines.push(`Note: ${first}`);
      for (const line of rest) lines.push(`  ${line}`);
    }
  });
  lines.push("", REVIEW_FOOTER);
  return lines.join("\n");
}

export interface ReviewItem {
  action: AnnotationAction;
  label: string;
  location: string;
  quote: string;
  comment: string;
}

/** Reads a feedback message back; `null` when the text is not one. */
export function parseReviewMessage(text: string): ReviewItem[] | null {
  const lines = text.replace(/^\s+/, "").split(/\r?\n/);
  if (!REVIEW_HEADER_RE.test(lines[0] ?? "")) return null;
  const items: ReviewItem[] = [];
  let current: (ReviewItem & { quoteLines: string[]; commentLines: string[] }) | null = null;
  let inComment = false;
  const flush = () => {
    if (!current) return;
    items.push({
      action: current.action,
      label: current.label,
      location: current.location,
      quote: current.quoteLines.join("\n"),
      comment: current.commentLines.join("\n"),
    });
    current = null;
  };
  for (const line of lines.slice(1)) {
    const head = /^(\d+)\. (.+)$/.exec(line);
    if (head && !inComment) {
      flush();
      const [label, ...where] = head[2].split(" - ");
      current = {
        action: actionFromLabel(label),
        label: label.trim(),
        location: where.join(" - ").trim(),
        quote: "",
        comment: "",
        quoteLines: [],
        commentLines: [],
      };
      continue;
    }
    if (!current) continue;
    if (inComment && line.startsWith("  ")) {
      current.commentLines.push(line.slice(2));
      continue;
    }
    inComment = false;
    if (line.startsWith("> ") || line === ">") {
      current.quoteLines.push(line.slice(2));
    } else if (line.startsWith("Note: ")) {
      current.commentLines.push(line.slice(6));
      inComment = true;
    } else if (line.trim() === "") {
      flush();
    }
  }
  flush();
  return items.length ? items : null;
}

// ---------------------------------------------------------------------------
// Draft store
// ---------------------------------------------------------------------------

const EMPTY: readonly Annotation[] = Object.freeze([]);
const drafts = new Map<string, readonly Annotation[]>();
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of [...listeners]) listener();
}

export function subscribeAnnotations(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function isAnnotation(value: unknown): value is Annotation {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<Annotation>;
  const source = item.source as Partial<AnnotationSource> | undefined;
  return typeof item.id === "string"
    && typeof item.quote === "string"
    && typeof item.offset === "number"
    && typeof item.action === "string"
    && typeof item.comment === "string"
    && typeof item.createdAt === "number"
    && !!source
    && (source.kind === "message" || source.kind === "file");
}

function load(scope: string): readonly Annotation[] {
  const raw = storage()?.getItem(STORAGE_PREFIX + scope);
  if (!raw) return EMPTY;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return EMPTY;
    const items = parsed.filter(isAnnotation).slice(0, MAX_ANNOTATIONS_PER_SCOPE);
    return items.length ? items : EMPTY;
  } catch {
    return EMPTY;
  }
}

function commit(scope: string, next: readonly Annotation[]): void {
  drafts.set(scope, next.length ? next : EMPTY);
  try {
    if (next.length) storage()?.setItem(STORAGE_PREFIX + scope, JSON.stringify(next));
    else storage()?.removeItem(STORAGE_PREFIX + scope);
  } catch {
    // Storage full or blocked: the review still works for this session.
  }
  emit();
}

/** Draft annotations of one conversation. The array is stable until it changes. */
export function getAnnotations(scope: string | null | undefined): readonly Annotation[] {
  if (!scope) return EMPTY;
  let current = drafts.get(scope);
  if (!current) {
    current = load(scope);
    drafts.set(scope, current);
  }
  return current;
}

let idCounter = 0;
function nextId(): string {
  idCounter += 1;
  return `a${Date.now().toString(36)}${idCounter.toString(36)}`;
}

export function addAnnotation(
  scope: string,
  input: Pick<Annotation, "source" | "quote" | "offset" | "action" | "comment">,
): Annotation | null {
  const quote = input.quote.slice(0, ANNOTATION_QUOTE_MAX_CHARS);
  if (!scope || !quote.trim()) return null;
  const current = getAnnotations(scope);
  if (current.length >= MAX_ANNOTATIONS_PER_SCOPE) return null;
  const annotation: Annotation = {
    id: nextId(),
    source: input.source,
    quote,
    offset: input.offset,
    action: input.action,
    comment: input.comment.trim(),
    createdAt: Date.now(),
  };
  commit(scope, [...current, annotation]);
  return annotation;
}

export function updateAnnotation(
  scope: string,
  id: string,
  patch: Partial<Pick<Annotation, "action" | "comment">>,
): void {
  const current = getAnnotations(scope);
  if (!current.some((annotation) => annotation.id === id)) return;
  commit(scope, current.map((annotation) => (annotation.id === id ? { ...annotation, ...patch } : annotation)));
}

export function removeAnnotation(scope: string, id: string): void {
  const current = getAnnotations(scope);
  const next = current.filter((annotation) => annotation.id !== id);
  if (next.length !== current.length) commit(scope, next);
}

export function clearAnnotations(scope: string): void {
  if (getAnnotations(scope).length) commit(scope, EMPTY);
}

/** True when the annotation was made on this message (same id, or same content). */
export function annotationIsOnMessage(annotation: Annotation, id: string, key: string): boolean {
  const { source } = annotation;
  return source.kind === "message" && (source.id === id || source.key === key);
}

// ---------------------------------------------------------------------------
// Selection requests: a surface asks, the dock of the conversation answers
// ---------------------------------------------------------------------------

export interface AnnotationRect {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export interface AnnotationRequest {
  scope: string;
  source: AnnotationSource;
  quote: string;
  offset: number;
  /** Where the selection sits on screen (viewport coordinates). */
  rect: AnnotationRect;
  nonce: number;
}

let activeScope: string | null = null;
let pending: AnnotationRequest | null = null;
let requestNonce = 0;

/** The conversation on screen: where an annotation made in the editor lands. */
export function setActiveAnnotationScope(scope: string | null): void {
  if (activeScope === scope) return;
  activeScope = scope;
  emit();
}

export function getActiveAnnotationScope(): string | null {
  return activeScope;
}

export function getAnnotationRequest(): AnnotationRequest | null {
  return pending;
}

/**
 * Opens the annotation toolbar on a selection. Returns false when no
 * conversation can receive it or the selection is empty.
 */
export function requestAnnotation(
  input: Omit<AnnotationRequest, "nonce" | "scope"> & { scope?: string | null },
): boolean {
  const scope = input.scope || activeScope;
  const quote = input.quote.slice(0, ANNOTATION_QUOTE_MAX_CHARS);
  if (!scope || !quote.trim()) return false;
  requestNonce += 1;
  pending = { ...input, scope, quote, nonce: requestNonce };
  emit();
  return true;
}

export function dismissAnnotationRequest(): void {
  if (!pending) return;
  pending = null;
  emit();
}

/** Test helper: forget every draft, request and scope held in memory. */
export function resetAnnotationsForTests(): void {
  drafts.clear();
  pending = null;
  activeScope = null;
  emit();
}

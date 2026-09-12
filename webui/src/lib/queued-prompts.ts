// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** Composer message queue: prompts typed while a turn is running.
 *
 * The queue lives in the composer but its rules are pure functions here so the
 * limits, the localStorage round-trip and the reordering stay testable.
 */
import type { SendOptions } from "@/hooks/useNavinStream";
import type { AttachmentKind } from "@/hooks/useAttachedImages";
import { isTimeoutError } from "@/lib/http";
import type {
  OutboundCliAppMention,
  OutboundFileMention,
  OutboundMcpPresetMention,
} from "@/lib/types";
import { collapseUserDisplayLabel } from "@/lib/pasted-content";

export const QUEUED_PROMPTS_STORAGE_PREFIX = "navin.webui.composerQueuedGuidance.v1:";
export const QUEUED_PROMPTS_LIMIT = 20;
/** Same ceiling as a message sent right away: ``MessageIngressLimits`` in
 * navin/webui/ingress_policy.py. Queueing must never refuse text the composer
 * would have accepted, so the queue carries no cap of its own; the server
 * limit is the only one. */
export const QUEUED_PROMPT_MAX_BYTES = 64 * 1024;
/** Mirrors ``MAX_ATTACHMENTS_PER_MESSAGE``; kept local so the queue rules stay
 * free of hook imports. */
export const QUEUED_PROMPT_MAX_IMAGES = 20;

function utf8Bytes(value: string): number {
  return new TextEncoder().encode(value).byteLength;
}

/** Trim to ``maxBytes`` UTF-8 bytes without splitting a code point. */
export function clampQueuedText(text: string, maxBytes = QUEUED_PROMPT_MAX_BYTES): string {
  if (utf8Bytes(text) <= maxBytes) return text;
  let low = 0;
  let high = text.length;
  while (low < high) {
    const mid = Math.ceil((low + high) / 2);
    if (utf8Bytes(text.slice(0, mid)) <= maxBytes) low = mid;
    else high = mid - 1;
  }
  return text.slice(0, low);
}

/** Turn attachments carried by a queued prompt. Restricted to the serializable
 * subset of ``SendOptions``: transport flags and workspace scope are resolved
 * when the prompt is actually sent. */
export type QueuedPromptOptions = Pick<
  SendOptions,
  | "cliApps"
  | "mcpPresets"
  | "fileMentions"
  | "documentTemplate"
  | "mediaTemplate"
  | "mediaTemplates"
>;

export interface QueuedPromptImage {
  dataUrl: string;
  name?: string;
  kind?: AttachmentKind;
}

export interface QueuedPrompt {
  id: string;
  text: string;
  images?: QueuedPromptImage[];
  options?: QueuedPromptOptions;
}

export function queuedPromptsStorageKey(key?: string | null): string | null {
  const clean = key?.trim();
  return clean ? `${QUEUED_PROMPTS_STORAGE_PREFIX}${clean}` : null;
}

function namedEntries<T extends { name: string }>(value: unknown): T[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (entry): entry is T =>
      !!entry
      && typeof entry === "object"
      && typeof (entry as { name?: unknown }).name === "string"
      && !!(entry as { name: string }).name,
  );
}

export function normalizeQueuedOptions(value: unknown): QueuedPromptOptions | undefined {
  if (!value || typeof value !== "object") return undefined;
  const record = value as QueuedPromptOptions;
  const cliApps = namedEntries<OutboundCliAppMention>(record.cliApps);
  const mcpPresets = namedEntries<OutboundMcpPresetMention>(record.mcpPresets);
  const fileMentions = Array.isArray(record.fileMentions)
    ? record.fileMentions.filter(
        (entry): entry is OutboundFileMention =>
          !!entry && typeof entry === "object" && typeof entry.path === "string" && !!entry.path,
      )
    : [];
  const template = record.documentTemplate;
  const documentTemplate =
    template
    && typeof template === "object"
    && typeof template.category === "string"
    && typeof template.name === "string"
      ? template
      : undefined;
  const mediaRaw = record.mediaTemplate;
  const mediaTemplate =
    mediaRaw
    && typeof mediaRaw === "object"
    && typeof mediaRaw.id === "string"
    && mediaRaw.id
      ? mediaRaw
      : undefined;
  const mediaTemplates = Array.isArray(record.mediaTemplates)
    ? record.mediaTemplates
        .filter(
          (entry): entry is NonNullable<QueuedPromptOptions["mediaTemplates"]>[number] =>
            !!entry && typeof entry === "object" && typeof entry.id === "string" && !!entry.id,
        )
        .slice(0, 6)
    : [];
  if (
    cliApps.length === 0
    && mcpPresets.length === 0
    && fileMentions.length === 0
    && !documentTemplate
    && !mediaTemplate
    && mediaTemplates.length === 0
  ) {
    return undefined;
  }
  return {
    ...(cliApps.length > 0 ? { cliApps } : {}),
    ...(mcpPresets.length > 0 ? { mcpPresets } : {}),
    ...(fileMentions.length > 0 ? { fileMentions } : {}),
    ...(documentTemplate ? { documentTemplate } : {}),
    ...(mediaTemplate ? { mediaTemplate } : {}),
    ...(mediaTemplates.length > 0 ? { mediaTemplates } : {}),
  };
}

export function normalizeQueuedPrompt(item: unknown, index: number): QueuedPrompt | null {
  if (!item || typeof item !== "object") return null;
  const record = item as Partial<QueuedPrompt>;
  if (typeof record.text !== "string") return null;
  const text = clampQueuedText(record.text.trim());
  const images = Array.isArray(record.images)
    ? record.images
        .flatMap((image) => {
          if (!image || typeof image !== "object") return [];
          const candidate = image as Partial<QueuedPromptImage>;
          if (typeof candidate.dataUrl !== "string" || !candidate.dataUrl.startsWith("data:")) {
            return [];
          }
          const kind = candidate.kind === "file" || candidate.kind === "image"
            ? candidate.kind
            : candidate.dataUrl.startsWith("data:image/")
              ? "image"
              : "file";
          return [{
            dataUrl: candidate.dataUrl,
            kind,
            ...(typeof candidate.name === "string" && candidate.name.trim()
              ? { name: candidate.name.trim() }
              : {}),
          }];
        })
        .slice(0, QUEUED_PROMPT_MAX_IMAGES)
    : [];
  if (!text && images.length === 0) return null;
  const id = typeof record.id === "string" && record.id.trim()
    ? record.id
    : `queued-prompt-restored-${index}`;
  const options = normalizeQueuedOptions(record.options);
  return {
    id,
    text,
    ...(images.length > 0 ? { images } : {}),
    ...(options ? { options } : {}),
  };
}

export function readQueuedPrompts(storageKey: string): QueuedPrompt[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(storageKey);
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map((item, index) => normalizeQueuedPrompt(item, index))
      .filter((item): item is QueuedPrompt => item != null)
      .slice(0, QUEUED_PROMPTS_LIMIT);
  } catch {
    return [];
  }
}

function serializeQueuedPrompts(prompts: QueuedPrompt[], withImages: boolean): string {
  return JSON.stringify(
    prompts.slice(0, QUEUED_PROMPTS_LIMIT).map((prompt) => ({
      id: prompt.id,
      text: clampQueuedText(prompt.text),
      ...(withImages && prompt.images?.length
        ? { images: prompt.images.slice(0, QUEUED_PROMPT_MAX_IMAGES) }
        : {}),
      ...(prompt.options ? { options: prompt.options } : {}),
    })),
  );
}

export function storeQueuedPrompts(storageKey: string, prompts: QueuedPrompt[]): void {
  if (typeof window === "undefined") return;
  if (prompts.length === 0) {
    try {
      window.localStorage.removeItem(storageKey);
    } catch {
      // Nothing to clean up if storage is unavailable.
    }
    return;
  }
  // A full queue of long prompts plus inlined images can exceed the origin's
  // localStorage quota. Degrade instead of dropping the whole queue: first the
  // image payloads, then the oldest prompts. The in-memory queue is unaffected.
  const attempts: QueuedPrompt[][] = [prompts];
  for (let keep = prompts.length - 1; keep >= 1; keep = Math.floor(keep / 2)) {
    attempts.push(prompts.slice(-keep));
  }
  for (const attempt of attempts) {
    for (const withImages of [true, false]) {
      try {
        window.localStorage.setItem(storageKey, serializeQueuedPrompts(attempt, withImages));
        return;
      } catch {
        // Try the next, smaller payload.
      }
    }
  }
}

export function queuedPromptLabel(prompt: QueuedPrompt, fallback = "File attachment"): string {
  const text = prompt.text.trim();
  if (text) return collapseUserDisplayLabel(text) || text;
  return prompt.images?.map((img) => img.name).filter(Boolean).join(", ") || fallback;
}

export function queuedPromptAttachmentCount(prompt: QueuedPrompt): number {
  const options = prompt.options;
  return (
    (prompt.images?.length ?? 0)
    + (options?.cliApps?.length ?? 0)
    + (options?.mcpPresets?.length ?? 0)
    + (options?.fileMentions?.length ?? 0)
    + (options?.documentTemplate ? 1 : 0)
    + (options?.mediaTemplates?.length
      ? options.mediaTemplates.length
      : options?.mediaTemplate
        ? 1
        : 0)
  );
}

/** Whether a prompt still carries something worth sending. */
export function queuedPromptIsSendable(prompt: QueuedPrompt): boolean {
  return !!prompt.text.trim() || !!prompt.images?.length;
}

/** Multitask dispatch carries plain text only. A prompt with images, files,
 * mentions or a template cannot ride a subagent spawn, so it stays queued
 * for the sequential flush (those extras would otherwise be dropped). */
export function queuedPromptIsMultitaskable(prompt: QueuedPrompt): boolean {
  return !!prompt.text.trim() && queuedPromptAttachmentCount(prompt) === 0;
}

/** Split the queue into prompts that can ride a spawn and those that stay. */
export function planMultitaskDispatch(queued: QueuedPrompt[]): {
  targets: QueuedPrompt[];
  remaining: QueuedPrompt[];
} {
  const targets = queued.filter(queuedPromptIsMultitaskable);
  const targetIds = new Set(targets.map((prompt) => prompt.id));
  return {
    targets,
    remaining: queued.filter((prompt) => !targetIds.has(prompt.id)),
  };
}

function multitaskErrorStatus(error: unknown): number | null {
  if (typeof error !== "object" || error === null || !("status" in error)) return null;
  const status = (error as { status?: unknown }).status;
  return typeof status === "number" && Number.isFinite(status) ? status : null;
}

function isTimeoutLikeMultitaskError(error: unknown): boolean {
  if (typeof error === "object" && error !== null && "name" in error) {
    if ((error as { name?: unknown }).name === "AbortError") return true;
  }
  return isTimeoutError(error);
}

/** Restore only when the server said it did not accept the spawn.
 *
 * 504 / 5xx / client timeout: the control may already be on the bus. Putting
 * the prompt back would also flush it as a sequential turn.
 * 400 / 401 / 404 / 409: nothing started, so the prompt must come back.
 */
export function shouldRestoreFailedMultitask(error: unknown): boolean {
  const status = multitaskErrorStatus(error);
  if (status === 400 || status === 401 || status === 404 || status === 409) return true;
  if (status !== null) return false;
  if (isTimeoutLikeMultitaskError(error)) return false;
  return true;
}

/** Prompts whose spawn rejected and that are safe to put back in the queue. */
export function failedMultitaskTargets(
  targets: QueuedPrompt[],
  results: readonly PromiseSettledResult<unknown>[],
): QueuedPrompt[] {
  const failed: QueuedPrompt[] = [];
  results.forEach((result, index) => {
    if (result.status !== "rejected") return;
    const prompt = targets[index];
    if (prompt && shouldRestoreFailedMultitask(result.reason)) failed.push(prompt);
  });
  return failed;
}

export function firstMultitaskError(
  results: readonly PromiseSettledResult<unknown>[],
): unknown {
  const rejected = results.find(
    (result) => result.status === "rejected" && shouldRestoreFailedMultitask(result.reason),
  );
  return rejected && rejected.status === "rejected" ? rejected.reason : null;
}

/** Put failed Multitask prompts back at the front, ahead of anything typed since. */
export function restoreFailedQueuedPrompts(
  current: QueuedPrompt[],
  failed: QueuedPrompt[],
): QueuedPrompt[] {
  if (failed.length === 0) return current;
  return [...failed, ...current];
}

/** Move ``dragId`` to the position currently held by ``targetId``. */
export function reorderQueuedPrompts(
  prompts: QueuedPrompt[],
  dragId: string,
  targetId: string,
): QueuedPrompt[] {
  if (dragId === targetId) return prompts;
  const from = prompts.findIndex((item) => item.id === dragId);
  const to = prompts.findIndex((item) => item.id === targetId);
  if (from === -1 || to === -1) return prompts;
  const next = [...prompts];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

/** Move a prompt by ``delta`` positions, clamped to the queue bounds. */
export function shiftQueuedPrompt(
  prompts: QueuedPrompt[],
  id: string,
  delta: number,
): QueuedPrompt[] {
  const from = prompts.findIndex((item) => item.id === id);
  if (from === -1) return prompts;
  const to = from + delta;
  if (to < 0 || to >= prompts.length) return prompts;
  const next = [...prompts];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

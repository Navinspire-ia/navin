// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { stripModeRoutingSlash } from "@/components/thread/ComposerModeMenu";
import { visibleUserChatText } from "@/lib/chat-visible-text";
import type { UIMessage } from "@/lib/types";

export interface PromptAnchor {
  answerPreview: string;
  id: string;
  label: string;
  preview: string;
  createdAt: number;
  index: number;
  /** No visible text (an image alone): label and preview are "Prompt N". */
  blank: boolean;
}

export function userPromptAnchors(messages: UIMessage[]): PromptAnchor[] {
  let index = 0;
  return messages.flatMap((message, messageIndex) => {
    if (message.role !== "user") return [];
    const shown = displayedPromptText(message.content);
    const anchor: PromptAnchor = {
      answerPreview: nextAssistantPreview(messages, messageIndex),
      id: message.id,
      label: promptLabel(shown, index),
      preview: promptPreview(shown, index),
      createdAt: message.createdAt,
      index,
      blank: !shown.trim(),
    };
    index += 1;
    return [anchor];
  });
}

/** A prompt the engine listed (``GET .../webui-prompts``), loaded or not. */
export interface ListedPrompt {
  index: number;
  text: string;
  created_at?: number | null;
}

/** A row of the prompt list: on screen (``id``) or in a page not loaded yet. */
export interface NavigatorPrompt {
  key: string;
  /** Position among all the chat's prompts. */
  index: number;
  /** Message id once the prompt is loaded; absent for an older page. */
  id?: string;
  label: string;
  preview: string;
  createdAt: number | null;
}

/**
 * Every prompt of the chat, oldest first. The thread is paged: the loaded
 * messages start at prompt ``userMessageOffset``, so the engine's list
 * supplies the prompts before it and the loaded messages (which also hold
 * prompts sent live, not in the list yet) supply the rest.
 */
export function navigatorPrompts(
  loaded: PromptAnchor[],
  listed: ListedPrompt[] | null,
  userMessageOffset: number,
): NavigatorPrompt[] {
  const offset = Math.max(0, userMessageOffset);
  const earlier = (listed ?? [])
    .filter((prompt) => prompt.index < offset)
    .map((prompt): NavigatorPrompt => {
      const shown = displayedPromptText(prompt.text);
      return {
        key: `listed-${prompt.index}`,
        index: prompt.index,
        label: promptLabel(shown, prompt.index),
        preview: promptPreview(shown, prompt.index),
        createdAt: typeof prompt.created_at === "number" ? prompt.created_at : null,
      };
    });
  const onScreen = loaded.map((anchor): NavigatorPrompt => {
    const index = offset + anchor.index;
    return {
      key: anchor.id,
      index,
      id: anchor.id,
      // A blank prompt is named by its number, counted over the whole chat.
      label: anchor.blank ? promptLabel("", index) : anchor.label,
      preview: anchor.blank ? promptPreview("", index) : anchor.preview,
      createdAt: anchor.createdAt,
    };
  });
  return [...earlier, ...onScreen];
}

/**
 * The prompt as the bubble shows it: hidden Team-room seed removed and the
 * mode-routing slash (/forge, /ask, /blueprint, …) the composer injects
 * stripped. A bare command with no arguments stays visible.
 */
export function displayedPromptText(content: string): string {
  const visible = visibleUserChatText(content);
  return stripModeRoutingSlash(visible) ?? visible;
}

export function promptLabel(content: string, index: number): string {
  const text = content.replace(/\s+/g, " ").trim();
  if (!text) return `Prompt ${index + 1}`;
  return truncatePreview(text, 80);
}

export function promptPreview(content: string, index: number): string {
  const text = compactPreview(content);
  if (!text) return `Prompt ${index + 1}`;
  return truncatePreview(text, 320);
}

function nextAssistantPreview(messages: UIMessage[], promptIndex: number): string {
  for (let index = promptIndex + 1; index < messages.length; index += 1) {
    const message = messages[index];
    if (message.role === "user") return "";
    if (message.role !== "assistant") continue;

    const preview = truncatePreview(compactPreview(plainAnswerText(message.content)), 240);
    if (preview) return preview;
  }

  return "";
}

/**
 * The rail card renders the answer as plain text, so drop the markdown
 * markers that would otherwise show up verbatim (`**bold**`, `code`, `# h1`,
 * fences, link syntax). Structure is kept; only the syntax goes.
 */
export function plainAnswerText(markdown: string): string {
  return markdown
    .replace(/^[ \t]*(```|~~~)[^\n]*$/gm, "")
    .replace(/^[ \t]{0,3}#{1,6}[ \t]+/gm, "")
    .replace(/^[ \t]{0,3}>[ \t]?/gm, "")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/(\*\*|__)(?=\S)([\s\S]*?\S)\1/g, "$2")
    .replace(/`([^`\n]+)`/g, "$1");
}

function compactPreview(content: string): string {
  return content.replace(/\n{3,}/g, "\n\n").trim();
}

function truncatePreview(text: string, maxLength: number): string {
  return text.length > maxLength ? `${text.slice(0, maxLength - 3)}...` : text;
}

export function jumpToPrompt(scrollEl: HTMLElement | null, promptId: string | undefined): void {
  if (!scrollEl || !promptId) return;
  const target = findPromptElement(scrollEl, promptId);
  if (!target) return;
  scrollEl.scrollTo({
    top: Math.max(0, promptTop(scrollEl, target) - 16),
    behavior: "smooth",
  });
}

export function findPromptElement(scrollEl: HTMLElement, promptId: string): HTMLElement | null {
  const escaped =
    typeof CSS !== "undefined" && typeof CSS.escape === "function"
      ? CSS.escape(promptId)
      : promptId.replace(/["\\]/g, "\\$&");
  const byMessage = scrollEl.querySelector<HTMLElement>(
    `[data-message-id="${escaped}"]`,
  );
  if (byMessage) return byMessage;
  const candidates = scrollEl.querySelectorAll<HTMLElement>("[data-user-prompt-id]");
  return Array.from(candidates).find(
    (candidate) => candidate.dataset.userPromptId === promptId,
  ) ?? null;
}

/**
 * Visual size / layout size. WebKitGTK zoom uses `transform: scale` on
 * `<html>`, so getBoundingClientRect is scaled while scrollTop is not.
 */
export function layoutScale(el: HTMLElement): number {
  const layout = el.offsetWidth || el.clientWidth;
  const visual = el.getBoundingClientRect().width;
  if (!layout || !visual) return 1;
  const scale = visual / layout;
  return Number.isFinite(scale) && scale > 0.01 ? scale : 1;
}

/** Convert a visual (viewport) Y delta into the scroller's layout pixels. */
export function layoutScrollTop(
  scrollTop: number,
  scrollRectTop: number,
  targetRectTop: number,
  scale: number,
): number {
  const safe = Number.isFinite(scale) && scale > 0.01 ? scale : 1;
  return (targetRectTop - scrollRectTop) / safe + scrollTop;
}

export function promptTop(scrollEl: HTMLElement, target: HTMLElement): number {
  const scrollRect = scrollEl.getBoundingClientRect();
  const targetRect = target.getBoundingClientRect();
  const hasLayoutRect = scrollRect.top !== 0 || targetRect.top !== 0;
  if (hasLayoutRect) {
    return layoutScrollTop(
      scrollEl.scrollTop,
      scrollRect.top,
      targetRect.top,
      layoutScale(scrollEl),
    );
  }
  return target.offsetTop;
}

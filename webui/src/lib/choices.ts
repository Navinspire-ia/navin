// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { UIMessage } from "./types";

export const OTHER_CHOICE_ID = "__other__";
export const CUSTOM_CHOICE_MAX_CHARS = 2000;

/** Whether Continue can fire for the current radio + optional typed answer. */
export function choiceCanContinue(selectedId: string, customText: string): boolean {
  if (!selectedId) return false;
  if (selectedId === OTHER_CHOICE_ID) return customText.trim().length > 0;
  return true;
}

/**
 * Payload for a choice card answer.
 *
 * A listed option never carries leftover text from the Other field. Skip
 * never carries a typed answer: it always means "take the recommended path".
 */
export function choiceAnswerPayload(
  optionId: string,
  skipped: boolean,
  customText: string,
): { optionId: string; skipped: boolean; customText: string } {
  if (skipped) return { optionId, skipped: true, customText: "" };
  if (optionId === OTHER_CHOICE_ID) {
    return {
      optionId: OTHER_CHOICE_ID,
      skipped: false,
      customText: customText.trim().slice(0, CUSTOM_CHOICE_MAX_CHARS),
    };
  }
  return { optionId, skipped: false, customText: "" };
}

export interface ChoiceOption {
  id: string;
  label: string;
  detail: string;
  recommended: boolean;
}

export interface PendingChoice {
  requestId: string;
  question: string;
  options: ChoiceOption[];
  allowSkip: boolean;
  recommendedId: string;
  expiresAt: number | null;
  receivedAt: number;
}

export interface ChoiceRequestFrame {
  request_id: string;
  question: string;
  options?: Array<{
    id?: string;
    label?: string;
    detail?: string;
    recommended?: boolean;
  }>;
  allow_skip?: boolean;
  recommended_id?: string;
  expires_at_ms?: number | null;
}

export function toPendingChoice(frame: ChoiceRequestFrame, now: number): PendingChoice {
  const options = (frame.options ?? [])
    .filter((entry) => entry && typeof entry.id === "string" && entry.id && entry.label)
    .map((entry) => ({
      id: String(entry.id),
      label: String(entry.label),
      detail: String(entry.detail ?? ""),
      recommended: Boolean(entry.recommended),
    }));
  return {
    requestId: frame.request_id,
    question: frame.question,
    options,
    allowSkip: frame.allow_skip !== false,
    recommendedId: frame.recommended_id ?? options.find((item) => item.recommended)?.id ?? "",
    expiresAt: typeof frame.expires_at_ms === "number" ? frame.expires_at_ms : null,
    receivedAt: now,
  };
}

export function addChoice(
  pending: readonly PendingChoice[],
  frame: ChoiceRequestFrame,
  now: number,
  settled: ReadonlySet<string> = new Set(),
): PendingChoice[] {
  const request = toPendingChoice(frame, now);
  if (!request.requestId || request.options.length < 2) return [...pending];
  if (settled.has(request.requestId)) return [...pending];
  if (request.expiresAt !== null && request.expiresAt <= now) return [...pending];
  const without = pending.filter((entry) => entry.requestId !== request.requestId);
  return [...without, request];
}

export function removeChoice(
  pending: readonly PendingChoice[],
  requestId: string,
): PendingChoice[] {
  return pending.filter((entry) => entry.requestId !== requestId);
}

export function dropExpiredChoices(
  pending: readonly PendingChoice[],
  now: number,
): PendingChoice[] {
  return pending.filter((entry) => entry.expiresAt === null || entry.expiresAt > now);
}

export interface ChoiceClosedFrame {
  request_id: string;
  option_id?: string;
  skipped?: boolean;
  question?: string;
  label?: string;
  custom_text?: string;
  timed_out?: boolean;
  stopped?: boolean;
  turn_id?: string;
  turn_seq?: number;
}

export function interactionEchoId(type: "choice" | "approval", requestId: string): string {
  return `${type}-${requestId}`;
}

/** The chat row that restates an answered card, built from what the user clicked. */
export function choiceEchoMessage(
  choice: PendingChoice,
  answer: { optionId: string; skipped: boolean; customText: string },
  now: number,
): UIMessage {
  const pickedId = answer.skipped ? choice.recommendedId : answer.optionId;
  const picked = choice.options.find((option) => option.id === pickedId);
  const customText = !answer.skipped && answer.optionId === OTHER_CHOICE_ID
    ? answer.customText.trim()
    : "";
  const label = customText ? "" : picked?.label ?? "";
  return {
    id: interactionEchoId("choice", choice.requestId),
    role: "tool",
    kind: "choice",
    content: customText || label,
    createdAt: now,
    interaction: {
      type: "choice",
      requestId: choice.requestId,
      question: choice.question,
      label,
      customText,
      skipped: answer.skipped,
    },
  };
}

/** Same row from the backend close frame: timeouts, other windows, reloads. */
export function choiceEchoFromClosedFrame(frame: ChoiceClosedFrame, now: number): UIMessage | null {
  const question = (frame.question ?? "").trim();
  if (!frame.request_id || !question) return null;
  const customText = (frame.custom_text ?? "").trim();
  const label = customText ? "" : (frame.label ?? "");
  return {
    id: interactionEchoId("choice", frame.request_id),
    role: "tool",
    kind: "choice",
    content: customText || label,
    createdAt: now,
    ...(frame.turn_id ? { turnId: frame.turn_id, turnPhase: "activity" as const } : {}),
    ...(typeof frame.turn_seq === "number" ? { turnSeq: frame.turn_seq } : {}),
    interaction: {
      type: "choice",
      requestId: frame.request_id,
      question,
      label,
      customText,
      skipped: Boolean(frame.skipped),
      timedOut: Boolean(frame.timed_out),
      stopped: Boolean(frame.stopped),
    },
  };
}

/**
 * Put an echo row in the chat, once. A later copy of the same request (the
 * backend close frame after the optimistic click) updates the row in place
 * and keeps its position and creation time.
 */
export function upsertInteractionEcho(messages: UIMessage[], echo: UIMessage): UIMessage[] {
  const index = messages.findIndex((message) => message.id === echo.id);
  if (index < 0) return [...messages, echo];
  const current = messages[index];
  const merged: UIMessage = {
    ...current,
    ...echo,
    createdAt: current.createdAt,
    interaction: current.interaction && echo.interaction
      ? { ...current.interaction, ...echo.interaction }
      : echo.interaction ?? current.interaction,
  };
  return [...messages.slice(0, index), merged, ...messages.slice(index + 1)];
}

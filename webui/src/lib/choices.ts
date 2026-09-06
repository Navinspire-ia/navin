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

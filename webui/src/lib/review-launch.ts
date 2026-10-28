/**
 * Review launcher for the Code rail: one click switches the chat to the
 * Review composer mode (gated change-set, correctness / regressions / tests /
 * performance / maintainability, false-positive filter, inline PR comments,
 * HTML report) and asks, in the user's Navin language, what to review. The
 * rail and the thread live in different trees, so the click travels as a
 * window event that the visible thread listens to.
 */
import type { UIMessage } from "@/lib/types";

export const REVIEW_LAUNCH_EVENT = "navin:review-launch";
export const REVIEW_INTRO_ID_PREFIX = "review-intro-";
export const REVIEW_INTRO_KEY = "thread.composer.mode.reviewIntro";
export const REVIEW_INTRO_FALLBACK =
  "**Review mode is on.** What do you want me to review: the current changes (working tree), a branch, a pull request, a commit range or a folder? "
  + "I read the change-set, check correctness, regressions, missing tests, error handling, performance, security and maintainability, "
  + "filter out false positives, then give a verdict (approve / request changes) with inline comments on the PR and an HTML report. "
  + "I can also apply the fixes once you agree. Nothing is modified without your approval.";

type Translate = (key: string, options: { defaultValue: string }) => string;

export function requestReviewLaunch(target: EventTarget = window): void {
  target.dispatchEvent(new CustomEvent(REVIEW_LAUNCH_EVENT));
}

export function onReviewLaunch(
  handler: () => void,
  target: EventTarget = window,
): () => void {
  const listener = () => handler();
  target.addEventListener(REVIEW_LAUNCH_EVENT, listener);
  return () => target.removeEventListener(REVIEW_LAUNCH_EVENT, listener);
}

export function buildReviewIntroMessage(t: Translate, now: number = Date.now()): UIMessage {
  return {
    id: `${REVIEW_INTRO_ID_PREFIX}${now}`,
    role: "assistant",
    content: t(REVIEW_INTRO_KEY, { defaultValue: REVIEW_INTRO_FALLBACK }),
    createdAt: now,
  };
}

/** Appends the intro once: a second click while it is still the last line is a no-op. */
export function appendReviewIntro(messages: UIMessage[], intro: UIMessage): UIMessage[] {
  const last = messages[messages.length - 1];
  if (last && last.id.startsWith(REVIEW_INTRO_ID_PREFIX)) return messages;
  return [...messages, intro];
}

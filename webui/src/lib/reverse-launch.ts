/**
 * Reverse launcher for the Code rail: one click switches the chat to the
 * Reverse composer mode and asks, in the user's Navin language, which app to
 * reverse engineer. The rail and the thread live in different trees, so the
 * click travels as a window event that the visible thread listens to.
 */
import type { UIMessage } from "@/lib/types";

export const REVERSE_LAUNCH_EVENT = "navin:reverse-launch";
export const REVERSE_INTRO_ID_PREFIX = "reverse-intro-";
export const REVERSE_INTRO_KEY = "thread.composer.mode.reverseIntro";
export const REVERSE_INTRO_FALLBACK =
  "**Reverse mode is on.** Which app do you want to reverse engineer? "
  + "Give me a path in this project, a binary, an APK, a web URL or a repository. "
  + "I start with a static analysis, and nothing dynamic runs without your approval.";

type Translate = (key: string, options: { defaultValue: string }) => string;

export function requestReverseLaunch(target: EventTarget = window): void {
  target.dispatchEvent(new CustomEvent(REVERSE_LAUNCH_EVENT));
}

export function onReverseLaunch(
  handler: () => void,
  target: EventTarget = window,
): () => void {
  const listener = () => handler();
  target.addEventListener(REVERSE_LAUNCH_EVENT, listener);
  return () => target.removeEventListener(REVERSE_LAUNCH_EVENT, listener);
}

export function buildReverseIntroMessage(t: Translate, now: number = Date.now()): UIMessage {
  return {
    id: `${REVERSE_INTRO_ID_PREFIX}${now}`,
    role: "assistant",
    content: t(REVERSE_INTRO_KEY, { defaultValue: REVERSE_INTRO_FALLBACK }),
    createdAt: now,
  };
}

/** Appends the intro once: a second click while it is still the last line is a no-op. */
export function appendReverseIntro(messages: UIMessage[], intro: UIMessage): UIMessage[] {
  const last = messages[messages.length - 1];
  if (last && last.id.startsWith(REVERSE_INTRO_ID_PREFIX)) return messages;
  return [...messages, intro];
}

/**
 * Cyber launcher for the Code rail: one click switches the chat to the
 * Security composer mode (/fortify: security_scan with FP filter and
 * exploitability proof, dependency / secrets / API / pentest / threat model,
 * code and PR review with inline comments, IaC / cloud / Kubernetes / Docker
 * posture, misconfiguration and privacy audit, remediation verified by tests
 * and CI and tracked on the board) and asks, in the user's Navin language,
 * what to audit. The rail and the thread live in different trees, so the
 * click travels as a window event that the visible thread listens to.
 */
import type { UIMessage } from "@/lib/types";

export const CYBER_LAUNCH_EVENT = "navin:cyber-launch";
export const CYBER_INTRO_ID_PREFIX = "cyber-intro-";
export const CYBER_INTRO_KEY = "thread.composer.mode.securityIntro";
export const CYBER_INTRO_FALLBACK =
  "**Security mode is on.** What do you want me to audit: this whole project, a folder, a file, a pull request or a diff? "
  + "I can scan the code (false-positive filter, source to sink proof, HTML report), check dependencies, secrets, APIs, run a penetration test and a threat model, "
  + "review code and PRs with inline comments, audit Terraform / IaC, cloud posture (AWS, Azure, GCP), Kubernetes and Docker misconfigurations and data protection, "
  + "then propose fixes and apply them in code, verified by tests and CI and tracked on the task board. Nothing is modified without your approval.";

type Translate = (key: string, options: { defaultValue: string }) => string;

export function requestCyberLaunch(target: EventTarget = window): void {
  target.dispatchEvent(new CustomEvent(CYBER_LAUNCH_EVENT));
}

export function onCyberLaunch(
  handler: () => void,
  target: EventTarget = window,
): () => void {
  const listener = () => handler();
  target.addEventListener(CYBER_LAUNCH_EVENT, listener);
  return () => target.removeEventListener(CYBER_LAUNCH_EVENT, listener);
}

export function buildCyberIntroMessage(t: Translate, now: number = Date.now()): UIMessage {
  return {
    id: `${CYBER_INTRO_ID_PREFIX}${now}`,
    role: "assistant",
    content: t(CYBER_INTRO_KEY, { defaultValue: CYBER_INTRO_FALLBACK }),
    createdAt: now,
  };
}

/** Appends the intro once: a second click while it is still the last line is a no-op. */
export function appendCyberIntro(messages: UIMessage[], intro: UIMessage): UIMessage[] {
  const last = messages[messages.length - 1];
  if (last && last.id.startsWith(CYBER_INTRO_ID_PREFIX)) return messages;
  return [...messages, intro];
}

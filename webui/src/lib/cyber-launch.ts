/**
 * Cyber launcher for the Code rail: one click switches the chat to the
 * Security composer mode (/fortify: security_scan with FP filter and
 * exploitability proof, dependency / secrets / API / pentest / threat model,
 * code and PR review with inline comments, IaC / cloud / Kubernetes / Docker
 * posture, misconfiguration and privacy audit, remediation verified by tests
 * and CI and tracked on the board). The switch is silent: the composer badge
 * and accent colour already say which mode is on, so no assistant line is
 * written in the journal. The rail and the thread live in different trees,
 * so the click travels as a window event that the visible thread listens to.
 */
export const CYBER_LAUNCH_EVENT = "navin:cyber-launch";

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

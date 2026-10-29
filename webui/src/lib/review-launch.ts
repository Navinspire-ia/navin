/**
 * Review launcher for the Code rail: one click switches the chat to the
 * Review composer mode (gated change-set, correctness / regressions / tests /
 * performance / maintainability, false-positive filter, inline PR comments,
 * HTML report). The switch is silent: the composer badge and accent colour
 * already say which mode is on, so no assistant line is written in the
 * journal. The rail and the thread live in different trees, so the click
 * travels as a window event that the visible thread listens to.
 */
export const REVIEW_LAUNCH_EVENT = "navin:review-launch";

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

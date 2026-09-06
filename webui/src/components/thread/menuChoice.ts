import type { PointerEvent as ReactPointerEvent } from "react";

/**
 * Composer menus (model, effort, mode) commit a pick on pointerdown so the
 * choice lands on the row under the cursor: WKWebView (macOS) often delivers
 * pointerup on another row once the menu reflows after the pick. The Radix
 * `onSelect` that follows (the click on pointerup, or Enter / Space from the
 * keyboard) then only has to close the menu. Calling `preventDefault` on that
 * select event would keep the menu open, so these helpers never do.
 */

/** A select event this close to a pointerdown commit is the same gesture. */
const POINTER_COMMIT_GRACE_MS = 600;

let lastPointerCommitAt = Number.NEGATIVE_INFINITY;

type PointerLike = Pick<ReactPointerEvent, "button" | "pointerType" | "preventDefault">;

/**
 * Mouse / pen only: a touch pointerdown is usually the start of a scroll and
 * must not pick the row; touch falls through to the regular click path.
 */
export function commitOnPointerDown(
  event: PointerLike,
  commit: () => void,
  now: () => number = Date.now,
): boolean {
  if (event.button !== 0) return false;
  if (event.pointerType !== "mouse" && event.pointerType !== "pen") return false;
  event.preventDefault();
  lastPointerCommitAt = now();
  commit();
  return true;
}

/**
 * Radix `onSelect` handler body. Skips the commit when a pointerdown already
 * committed this gesture (possibly on a different row), otherwise commits
 * (keyboard, touch). Either way the menu closes: the default is not prevented.
 */
export function commitOnSelect(
  commit: () => void,
  now: () => number = Date.now,
): boolean {
  if (now() - lastPointerCommitAt < POINTER_COMMIT_GRACE_MS) return false;
  commit();
  return true;
}

/** Test hook: forget any previous pointerdown commit. */
export function resetMenuChoiceForTests(): void {
  lastPointerCommitAt = Number.NEGATIVE_INFINITY;
}

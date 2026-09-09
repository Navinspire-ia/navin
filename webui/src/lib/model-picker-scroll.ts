// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/** Pixel cap for the chat model list, independent of Radix CSS variables. */
export const MODEL_PICKER_LIST_MIN_PX = 160;
export const MODEL_PICKER_LIST_MAX_PX = 360;
const BUDGET_NOTE_CHROME_PX = 152;
const SEARCH_FOOTER_CHROME_PX = 112;
const GAP_ABOVE_TRIGGER_PX = 20;

/**
 * Height of the scrollable model rows. `hostChrome=1` / native-host hides
 * scrollbars globally and `max-height: min(var(--radix-…), …)` often fails to
 * create a real scrollport, so the list is sized in px from the trigger.
 */
export function modelPickerListMaxPx(
  triggerTop: number,
  hasBudgetNote: boolean,
  viewportTop = 0,
): number {
  const chromePx = hasBudgetNote ? BUDGET_NOTE_CHROME_PX : SEARCH_FOOTER_CHROME_PX;
  const available = triggerTop - viewportTop - GAP_ABOVE_TRIGGER_PX - chromePx;
  return Math.max(
    MODEL_PICKER_LIST_MIN_PX,
    Math.min(MODEL_PICKER_LIST_MAX_PX, Math.floor(available)),
  );
}

export function bindMenuListWheel(node: HTMLElement): () => void {
  const onWheel = (event: WheelEvent) => {
    const max = Math.max(0, node.scrollHeight - node.clientHeight);
    // A list that is not actually a scrollport must not eat the event:
    // native-host + a Radix max-height that failed to apply looks like this,
    // and preventDefault would make Other / model lists feel frozen.
    if (max <= 0) return;
    event.stopPropagation();
    const scale = event.deltaMode === 1
      ? 16
      : event.deltaMode === 2
        ? node.clientHeight
        : 1;
    node.scrollTop = Math.max(0, Math.min(max, node.scrollTop + event.deltaY * scale));
    event.preventDefault();
  };
  node.addEventListener("wheel", onWheel, { capture: true, passive: false });
  return () => node.removeEventListener("wheel", onWheel, { capture: true });
}

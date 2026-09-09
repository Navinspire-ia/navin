// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import * as React from "react";

import { applyCssZoomToRadixPopper } from "@/lib/ui-zoom";

export function mergeRefs<T>(...refs: Array<React.Ref<T> | undefined>): React.RefCallback<T> {
  return (node) => {
    for (const ref of refs) {
      if (!ref) continue;
      if (typeof ref === "function") ref(node);
      else (ref as React.MutableRefObject<T | null>).current = node;
    }
  };
}

/**
 * Radix writes `transform` on the parent wrapper in a layout effect that
 * runs after this content's, so observe that wrapper rather than guessing
 * once at mount. Apply synchronously: a trailing rAF paints the unconverted
 * visual translate first, which is the zoomed-in Effort / model / session
 * menu appearing far down-right of its trigger.
 */
export function useCssZoomFloatingLayer(ref: { current: HTMLElement | null }): void {
  React.useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    const wrapper = node.closest("[data-radix-popper-content-wrapper]");
    if (!(wrapper instanceof HTMLElement)) return;
    const apply = () => applyCssZoomToRadixPopper(wrapper);
    apply();
    const observer = new MutationObserver(apply);
    observer.observe(wrapper, { attributes: true, attributeFilter: ["style"] });
    observer.observe(node, { attributes: true, attributeFilter: ["data-state"] });
    const resize = new ResizeObserver(apply);
    resize.observe(node);
    return () => {
      observer.disconnect();
      resize.disconnect();
    };
  });
}

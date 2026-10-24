// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { DirectionalHint, type ICalloutProps } from "@fluentui/react";

// Settings lives in overflow-y-auto. Fluent lists dismiss on the first
// scroll/resize unless these locks stay on (same as Career/Tenders).
// directionalHintFixed keeps the list below the field so it does not bounce.
// Kept out of lib/computer-setup so the chat thread never pulls Fluent in.
export const COMPUTER_MODEL_CALLOUT: ICalloutProps = {
  calloutMaxHeight: 280,
  directionalHint: DirectionalHint.bottomLeftEdge,
  directionalHintFixed: true,
  preventDismissOnScroll: true,
  preventDismissOnResize: true,
};

// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { IPanelStyles, IModalStyles } from "@fluentui/react";

/** Keep the header visible and give long forms a bounded, touch-scrollable body. */
export const DESK_PANEL_STYLES: Partial<IPanelStyles> = {
  main: { height: "100dvh", maxHeight: "100dvh", overflow: "hidden" },
  commands: { flexShrink: 0 },
  contentInner: { flex: "1 1 0%", minHeight: 0, minWidth: 0 },
  scrollableContent: { flex: "1 1 auto", minHeight: 0, overflowY: "auto", overscrollBehavior: "contain", scrollbarGutter: "stable" },
  content: { minWidth: 0, paddingBottom: 32, overflowWrap: "anywhere" },
};

export const DESK_MODAL_STYLES: Partial<IModalStyles> = {
  main: { minWidth: 0, maxWidth: "calc(100vw - 24px)", maxHeight: "calc(100dvh - 24px)", margin: 12 },
  scrollableContent: { maxHeight: "calc(100dvh - 24px)", overflowY: "auto", overscrollBehavior: "contain" },
};

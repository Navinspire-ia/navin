// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

export function pinElementToScrollStart(el: HTMLElement | null) {
  if (!el) return;
  const header = el.querySelector<HTMLElement>("[data-zone-header]");
  header?.focus({ preventScroll: true });
  el.scrollIntoView({ block: "start", inline: "nearest", behavior: "auto" });
}

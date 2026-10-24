// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { DirectionProvider } from "@radix-ui/react-direction";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { isRtlLocale, normalizeLocale } from "./config";

/**
 * Tells Radix (menus, popovers, tooltips, roving focus) which way the UI
 * reads, so "start"/"end" alignment and arrow-key navigation mirror with
 * the document in Arabic.
 */
export function DirectionRoot({ children }: { children: ReactNode }) {
  const { i18n } = useTranslation();
  const locale = normalizeLocale(i18n.resolvedLanguage ?? i18n.language);
  return (
    <DirectionProvider dir={isRtlLocale(locale) ? "rtl" : "ltr"}>
      {children}
    </DirectionProvider>
  );
}

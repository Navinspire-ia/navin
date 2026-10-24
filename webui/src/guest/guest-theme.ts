// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useState } from "react";

/** Day / night for the shared session page: the visitor's choice, else night. */
export type GuestTheme = "light" | "dark";

const STORAGE_KEY = "navin.guest.theme";

export function initialGuestTheme(): GuestTheme {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    // Private mode: follow the system.
  }
  // Night by default: a shared session opens dark until the visitor picks day.
  return "dark";
}

export function applyGuestTheme(theme: GuestTheme): void {
  const root = document.documentElement;
  root.classList.toggle("dark", theme === "dark");
  root.style.colorScheme = theme;
}

export function useGuestTheme(): { theme: GuestTheme; toggle: () => void } {
  const [theme, setTheme] = useState<GuestTheme>(() => initialGuestTheme());
  const toggle = useCallback(() => {
    setTheme((current) => {
      const next: GuestTheme = current === "dark" ? "light" : "dark";
      applyGuestTheme(next);
      try {
        window.localStorage.setItem(STORAGE_KEY, next);
      } catch {
        // Kept for this visit only.
      }
      return next;
    });
  }, []);
  return { theme, toggle };
}

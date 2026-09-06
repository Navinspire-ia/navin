import { useEffect, useState } from "react";

import {
  LOCAL_PREFS_CHANGED_EVENT,
  readLocalPreferences,
  type LocalDensity,
  type LocalPreferences,
} from "@/lib/local-preferences";

/** Every browser-local preference, live across tabs and the settings pane. */
export function useLocalPreferences(): LocalPreferences {
  const [prefs, setPrefs] = useState<LocalPreferences>(() => readLocalPreferences());

  useEffect(() => {
    // The change event carries the new value, but re-reading storage keeps
    // one normalization path for every source (event, other tab, refocus).
    const refresh = () => setPrefs(readLocalPreferences());
    window.addEventListener("storage", refresh);
    window.addEventListener("focus", refresh);
    window.addEventListener(LOCAL_PREFS_CHANGED_EVENT, refresh);
    return () => {
      window.removeEventListener("storage", refresh);
      window.removeEventListener("focus", refresh);
      window.removeEventListener(LOCAL_PREFS_CHANGED_EVENT, refresh);
    };
  }, []);

  return prefs;
}

export const CHAT_DENSITY_ATTRIBUTE = "data-chat-density";

/** Stamp the Density preference on <html> so the chat scale (globals.css
 *  `--chat-font-size` / `--chat-line-height`) follows it everywhere. */
export function applyChatDensity(density: LocalDensity, root: HTMLElement = document.documentElement): void {
  root.setAttribute(CHAT_DENSITY_ATTRIBUTE, density);
}

export function useChatDensityAttribute(): void {
  const { density } = useLocalPreferences();
  useEffect(() => {
    applyChatDensity(density);
  }, [density]);
}

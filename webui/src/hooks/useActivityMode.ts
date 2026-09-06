import { useEffect, useState } from "react";

import {
  LOCAL_PREFS_CHANGED_EVENT,
  normalizeActivityMode,
  readLocalPreferences,
  type LocalActivityMode,
  type LocalPreferences,
} from "@/lib/local-preferences";

/** The "Activity detail" preference, live across tabs and the settings pane. */
export function useActivityMode(): LocalActivityMode {
  const [mode, setMode] = useState<LocalActivityMode>(() => readLocalPreferences().activityMode);

  useEffect(() => {
    const refresh = () => setMode(readLocalPreferences().activityMode);
    const refreshFromLocalPreferenceEvent = (event: Event) => {
      const detail = (event as CustomEvent<Partial<LocalPreferences> | undefined>).detail;
      setMode(detail ? normalizeActivityMode(detail.activityMode) : readLocalPreferences().activityMode);
    };
    window.addEventListener("storage", refresh);
    window.addEventListener("focus", refresh);
    window.addEventListener(LOCAL_PREFS_CHANGED_EVENT, refreshFromLocalPreferenceEvent);
    return () => {
      window.removeEventListener("storage", refresh);
      window.removeEventListener("focus", refresh);
      window.removeEventListener(LOCAL_PREFS_CHANGED_EVENT, refreshFromLocalPreferenceEvent);
    };
  }, []);

  return mode;
}

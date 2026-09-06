export type LocalDensity = "comfortable" | "compact";
/**
 * How much of the agent's activity the chat shows by default.
 * - auto: the live turn is open step by step; once done it folds to a
 *   one-line digest, like every earlier turn.
 * - expanded: every turn stays open.
 * - compact: live turn open, Thinking reduced to its title, shorter journal.
 * - digest: every turn folded; the header line follows the live action.
 */
export type LocalActivityMode = "auto" | "expanded" | "compact" | "digest";
export type FileEditDisplayMode = "summary" | "diff" | "collapsed_diff";

export interface LocalPreferences {
  density: LocalDensity;
  activityMode: LocalActivityMode;
  codeWrap: boolean;
  brandLogos: boolean;
  fileEditDisplayMode: FileEditDisplayMode;
}

export const LOCAL_PREFS_STORAGE_KEY = "navin-webui.settings-preferences";
export const LOCAL_PREFS_CHANGED_EVENT = "navin-webui.local-preferences-changed";

export const DEFAULT_LOCAL_PREFS: LocalPreferences = {
  density: "comfortable",
  activityMode: "auto",
  codeWrap: true,
  brandLogos: false,
  // Diff by default, like Cursor: small edits render their red/green lines
  // inline, large ones auto-collapse behind a "View diff" toggle. "summary"
  // (counts only) stays available in Settings for users who prefer quiet.
  fileEditDisplayMode: "diff",
};

export function normalizeFileEditDisplayMode(value: unknown): FileEditDisplayMode {
  if (value === "diff" || value === "collapsed_diff" || value === "summary") {
    return value;
  }
  return DEFAULT_LOCAL_PREFS.fileEditDisplayMode;
}

export function normalizeActivityMode(value: unknown): LocalActivityMode {
  if (value === "expanded" || value === "compact" || value === "digest" || value === "auto") {
    return value;
  }
  return DEFAULT_LOCAL_PREFS.activityMode;
}

export function readLocalPreferences(): LocalPreferences {
  try {
    const raw = window.localStorage.getItem(LOCAL_PREFS_STORAGE_KEY);
    if (!raw) return DEFAULT_LOCAL_PREFS;
    const parsed = JSON.parse(raw) as Partial<LocalPreferences>;
    return {
      density: parsed.density === "compact" ? "compact" : "comfortable",
      activityMode: normalizeActivityMode(parsed.activityMode),
      codeWrap: parsed.codeWrap !== false,
      brandLogos: parsed.brandLogos === true,
      fileEditDisplayMode: normalizeFileEditDisplayMode(parsed.fileEditDisplayMode),
    };
  } catch {
    return DEFAULT_LOCAL_PREFS;
  }
}

export function writeLocalPreferences(preferences: LocalPreferences): void {
  try {
    window.localStorage.setItem(LOCAL_PREFS_STORAGE_KEY, JSON.stringify(preferences));
  } catch {
    // Browser-only preferences should never block settings.
  }
  window.dispatchEvent(new CustomEvent<LocalPreferences>(
    LOCAL_PREFS_CHANGED_EVENT,
    { detail: preferences },
  ));
}

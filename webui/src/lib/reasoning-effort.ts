/**
 * Labels for reasoning / thinking effort (Settings + composer picker).
 */

export const DEFAULT_REASONING_EFFORT_VALUES = ["", "low", "medium", "high"] as const;

export function reasoningEffortLabel(
  value: string,
  t: (key: string, fallback: string) => string,
): string {
  switch (value) {
    case "":
      return t("settings.values.reasoningAuto", "Auto");
    case "none":
      return t("settings.values.reasoningOff", "Off");
    case "minimal":
      return t("settings.values.reasoningMinimal", "Minimal");
    case "low":
      return t("settings.values.reasoningLow", "Low");
    case "medium":
      return t("settings.values.reasoningMedium", "Medium");
    case "high":
      return t("settings.values.reasoningHigh", "High");
    case "xhigh":
      return t("settings.values.reasoningXHigh", "X-High");
    case "max":
      return t("settings.values.reasoningMax", "Max");
    case "adaptive":
      return t("settings.values.reasoningAdaptive", "Adaptive");
    default:
      return value || t("settings.values.reasoningAuto", "Auto");
  }
}

/** Short label for the composer pill (Cursor-style). */
export function reasoningEffortShortLabel(
  value: string,
  t: (key: string, fallback: string) => string,
): string {
  // Only the empty default is "Auto". "adaptive" is a distinct Claude mode
  // (adaptive thinking) and must keep its own label - otherwise the Effort
  // menu shows Auto twice for ["", "adaptive", ...].
  if (value === "") {
    return t("thread.composer.effortAuto", "Auto");
  }
  return reasoningEffortLabel(value, t);
}

/** Labeled submodule rail (icons + names) when the Code chat is maximized. */
export const DEV_RAIL_LABEL_WIDTH = 216;
/** Icon-only rail: third Code layout, names hidden, tooltips remain. */
export const DEV_RAIL_ICON_WIDTH = 48;
export const RAIL_DENSITY_STORAGE_KEY = "navin.dev.railDensity";

export type DevRailDensity = "labels" | "icons";

export function readRailDensity(): DevRailDensity {
  try {
    return window.localStorage.getItem(RAIL_DENSITY_STORAGE_KEY) === "icons"
      ? "icons"
      : "labels";
  } catch {
    return "labels";
  }
}

export function persistRailDensity(next: DevRailDensity): void {
  try {
    window.localStorage.setItem(RAIL_DENSITY_STORAGE_KEY, next);
  } catch {
    // localStorage unavailable: the mode still applies for this session.
  }
}

export function railWidthFor(density: DevRailDensity): number {
  return density === "icons" ? DEV_RAIL_ICON_WIDTH : DEV_RAIL_LABEL_WIDTH;
}

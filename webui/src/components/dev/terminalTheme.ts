import type { CSSProperties } from "react";
import type { ITheme } from "@xterm/xterm";

/** Canvas + panel chrome share this hex so the tab bar never mismatches. */
export const TERMINAL_DARK_BG = "#111318";
export const TERMINAL_LIGHT_BG = "#f6f8fa";
export const TERMINAL_DARK_FG = "#e6e8ee";
export const TERMINAL_LIGHT_FG = "#1f2328";

export const TERMINAL_FONT =
  '"JetBrains Mono", "JetBrainsMono Nerd Font", "MesloLGS NF", "SFMono-Regular", "SF Mono", "Fira Code", "Cascadia Code", "Symbols Nerd Font Mono", monospace';

export const TERMINAL_DARK_THEME: ITheme = {
  background: TERMINAL_DARK_BG,
  foreground: TERMINAL_DARK_FG,
  cursor: "#9ecbff",
  cursorAccent: TERMINAL_DARK_BG,
  selectionBackground: "#2b4a7a80",
  selectionForeground: TERMINAL_DARK_FG,
  black: "#111318",
  red: "#ff7b72",
  green: "#3fb950",
  yellow: "#d29922",
  blue: "#58a6ff",
  magenta: "#bc8cff",
  cyan: "#39d353",
  white: "#e6e8ee",
  brightBlack: "#6e7681",
  brightRed: "#ffa198",
  brightGreen: "#56d364",
  brightYellow: "#e3b341",
  brightBlue: "#79c0ff",
  brightMagenta: "#d2a8ff",
  brightCyan: "#56d364",
  brightWhite: "#f0f6fc",
};

export const TERMINAL_LIGHT_THEME: ITheme = {
  background: TERMINAL_LIGHT_BG,
  foreground: TERMINAL_LIGHT_FG,
  cursor: TERMINAL_LIGHT_FG,
  cursorAccent: TERMINAL_LIGHT_BG,
  selectionBackground: "#0969da40",
  selectionForeground: TERMINAL_LIGHT_FG,
  black: "#1f2328",
  red: "#cf222e",
  green: "#116329",
  yellow: "#4d2d00",
  blue: "#0969da",
  magenta: "#8250df",
  cyan: "#1b7c83",
  white: "#6e7781",
  brightBlack: "#656d76",
  brightRed: "#a40e26",
  brightGreen: "#1a7f37",
  brightYellow: "#633c01",
  brightBlue: "#218bff",
  brightMagenta: "#a475f9",
  brightCyan: "#3192aa",
  brightWhite: TERMINAL_LIGHT_FG,
};

/** Read-only agent exec tabs: hide the caret, keep the same surfaces. */
export const TERMINAL_DARK_THEME_READONLY: ITheme = {
  ...TERMINAL_DARK_THEME,
  cursor: TERMINAL_DARK_BG,
  cursorAccent: TERMINAL_DARK_BG,
};

export const TERMINAL_LIGHT_THEME_READONLY: ITheme = {
  ...TERMINAL_LIGHT_THEME,
  cursor: TERMINAL_LIGHT_BG,
  cursorAccent: TERMINAL_LIGHT_BG,
};

export function terminalTheme(isDark: boolean, readonly = false): ITheme {
  if (readonly) {
    return isDark ? TERMINAL_DARK_THEME_READONLY : TERMINAL_LIGHT_THEME_READONLY;
  }
  return isDark ? TERMINAL_DARK_THEME : TERMINAL_LIGHT_THEME;
}

/** Keep the CSS chrome (viewport, padding, IME box) on the same hex as xterm. */
export function terminalSurfaceStyle(isDark: boolean): CSSProperties {
  return {
    "--navin-terminal-bg": isDark ? TERMINAL_DARK_BG : TERMINAL_LIGHT_BG,
    "--navin-terminal-fg": isDark ? TERMINAL_DARK_FG : TERMINAL_LIGHT_FG,
  } as CSSProperties;
}

export function paintTerminalViewport(container: HTMLElement | null, isDark: boolean): void {
  const viewport = container?.querySelector(".xterm-viewport");
  if (viewport instanceof HTMLElement) {
    viewport.style.backgroundColor = isDark ? TERMINAL_DARK_BG : TERMINAL_LIGHT_BG;
  }
}

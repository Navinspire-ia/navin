import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

type Theme = "light" | "dark";

/**
 * Bumped from "navin-webui.theme": the old value was written on every mount,
 * not on choice, so every existing install had a theme pinned in storage that
 * nobody had picked. Reading it would have made the new default unreachable.
 */
const STORAGE_KEY = "navin-webui.theme.v2";

/** Navin is dark unless asked otherwise, on every OS. Kept in step with the
 *  pre-paint script in index.html, which decides the same thing earlier. */
const DEFAULT_THEME: Theme = "dark";

/**
 * What a Chromium app window reads to tint its own title bar. Matches the body
 * fallbacks in index.html and the manifest, so the frame, the pre-paint page
 * and the installed splash are all the same colour.
 */
export const WINDOW_COLOR: Record<Theme, string> = {
  dark: "#141414",
  light: "#fcfcfc",
};

const ThemeContext = createContext<Theme>(DEFAULT_THEME);

/**
 * The theme to start in, given whatever is in storage.
 *
 * Deliberately ignores the OS preference: Navin looks the same on every machine
 * it is installed on, and only an explicit choice changes that.
 */
export function resolveInitialTheme(stored: string | null): Theme {
  return stored === "light" || stored === "dark" ? stored : DEFAULT_THEME;
}

function readStored(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "dark") root.classList.add("dark");
  else root.classList.remove("dark");
  // What a Chromium app window reads to tint its title bar. Without this the
  // frame keeps whatever colour it was given at load and stops matching the
  // app the moment the user toggles.
  root.style.colorScheme = theme;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", WINDOW_COLOR[theme]);
}

export function useTheme(): {
  theme: Theme;
  toggle: () => void;
  setTheme: (t: Theme) => void;
} {
  const [theme, setThemeState] = useState<Theme>(() => resolveInitialTheme(readStored()));

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // Only a choice is written down. Persisting on every mount is what turned a
  // default into a stored preference nobody had made, and left the next default
  // with no way to reach anyone.
  const remember = useCallback((t: Theme) => {
    try {
      localStorage.setItem(STORAGE_KEY, t);
    } catch {
      // Private mode, or storage full: the theme still applies for this window.
    }
  }, []);

  const setTheme = useCallback(
    (t: Theme) => {
      remember(t);
      setThemeState(t);
    },
    [remember],
  );
  const toggle = useCallback(
    () =>
      setThemeState((current) => {
        const next = current === "dark" ? "light" : "dark";
        remember(next);
        return next;
      }),
    [remember],
  );
  return { theme, toggle, setTheme };
}

export function ThemeProvider({ theme, children }: { theme: Theme; children: ReactNode }) {
  return createElement(ThemeContext.Provider, { value: theme }, children);
}

export function useThemeValue(): Theme {
  return useContext(ThemeContext);
}

// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Host side of the Navin canvas kit (navin/artifacts/canvas).
 *
 * A canvas artifact is HTML built by the engine from typed blocks. It carries
 * a marker meta tag and renders with its own inlined kit script. The panel
 * gives it two things before handing it to the sandboxed iframe:
 *
 * - a Content-Security-Policy of default-src 'none', inserted first in <head>,
 *   so the canvas can run its kit script but can never reach the network
 *   (CSP meta policies stack, so content cannot loosen it);
 * - the app theme as kit tokens (--nv-*), appended last in <head> so it wins
 *   over the kit defaults.
 *
 * Token values mirror webui/src/globals.css (:root and .dark). They are copied
 * rather than read from getComputedStyle because the theme class is applied in
 * an effect that runs after this component renders; canvas-theme.test.ts keeps
 * the two in step.
 */

export type CanvasThemeMode = "light" | "dark";

export const CANVAS_MARKER = '<meta name="navin-canvas" content="1">';

export const CANVAS_CSP =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; " +
  "img-src data:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'";

/** Kit token -> globals.css variable it mirrors. */
export const CANVAS_TOKEN_SOURCES: ReadonlyArray<readonly [string, string]> = [
  ["bg", "--background"],
  ["fg", "--foreground"],
  ["card", "--card"],
  ["muted", "--muted"],
  ["muted-fg", "--muted-foreground"],
  ["border", "--border"],
  ["accent", "--tone-explore"],
  ["good", "--tone-edit"],
  ["warn", "--tone-shell"],
  ["bad", "--tone-error"],
  ["info", "--tone-skill"],
];

/** HSL triplets from globals.css, per theme. */
export const CANVAS_THEME_VALUES: Record<CanvasThemeMode, Record<string, string>> = {
  light: {
    "--background": "0 0% 99%",
    "--foreground": "0 0% 10%",
    "--card": "0 0% 100%",
    "--muted": "0 0% 95%",
    "--muted-foreground": "0 0% 42%",
    "--border": "0 0% 88%",
    "--tone-explore": "214 76% 44%",
    "--tone-edit": "163 88% 25%",
    "--tone-shell": "20 88% 38%",
    "--tone-skill": "263 68% 48%",
    "--tone-error": "0 72% 42%",
  },
  dark: {
    "--background": "0 0% 8%",
    "--foreground": "0 0% 96%",
    "--card": "0 0% 11%",
    "--muted": "0 0% 14%",
    "--muted-foreground": "0 0% 62%",
    "--border": "0 0% 20%",
    "--tone-explore": "213 90% 70%",
    "--tone-edit": "160 62% 54%",
    "--tone-shell": "27 92% 62%",
    "--tone-skill": "255 88% 76%",
    "--tone-error": "0 88% 70%",
  },
};

export function isCanvasHtml(content: string | undefined | null): boolean {
  return Boolean(content && content.includes(CANVAS_MARKER));
}

/** The <style> block carrying the app theme as kit tokens. */
export function canvasThemeStyle(mode: CanvasThemeMode): string {
  const values = CANVAS_THEME_VALUES[mode];
  const decls = CANVAS_TOKEN_SOURCES.map(([token, source]) => `--nv-${token}: hsl(${values[source]});`);
  // Categorical series follow the four activity families, then neutral grey.
  decls.push(
    `--nv-s1: hsl(${values["--tone-explore"]});`,
    `--nv-s2: hsl(${values["--tone-shell"]});`,
    `--nv-s3: hsl(${values["--tone-skill"]});`,
    `--nv-s4: hsl(${values["--tone-edit"]});`,
    `--nv-s5: hsl(${values["--muted-foreground"]});`,
  );
  return `<style id="navin-host-theme">:root { color-scheme: ${mode}; ${decls.join(" ")} }</style>`;
}

/** Add the network lock and the host theme to a canvas document. */
export function prepareCanvasDocument(content: string, mode: CanvasThemeMode): string {
  const csp = `<meta http-equiv="Content-Security-Policy" content="${CANVAS_CSP}">`;
  const theme = canvasThemeStyle(mode);
  let doc = content;
  const head = /<head(\s[^>]*)?>/i.exec(doc);
  if (head) {
    const at = head.index + head[0].length;
    doc = `${doc.slice(0, at)}${csp}${doc.slice(at)}`;
  } else {
    doc = `${csp}${doc}`;
  }
  const close = doc.search(/<\/head>/i);
  return close >= 0 ? `${doc.slice(0, close)}${theme}${doc.slice(close)}` : `${doc}${theme}`;
}

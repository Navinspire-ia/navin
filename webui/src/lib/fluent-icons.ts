/**
 * Registers the Fluent (MDL2) icon set exactly once.
 *
 * Without this every `<Icon iconName="..." />` renders nothing and Fluent logs
 * `The icon "x" was used but not registered`. The fonts are served from
 * `public/fluent-icons/` rather than the Microsoft CDN: the desktop apps run
 * offline, and the default base URL would leave every icon blank there.
 *
 * Imported for its side effect by each module that uses Fluent components, so
 * registration happens when that lazy chunk loads - before its first render,
 * and without pulling the icon tables into the entry bundle.
 */
import { initializeIcons } from "@fluentui/font-icons-mdl2";

const FONT_BASE_URL = "/fluent-icons/";

let registered = false;

export function ensureFluentIcons(): void {
  if (registered) return;
  registered = true;
  initializeIcons(FONT_BASE_URL);
}

ensureFluentIcons();

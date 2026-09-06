/**
 * Language support for CodeEditor, resolved once per language.
 *
 * `loadLanguage` builds a new `LanguageSupport` on every call. A new instance
 * is a new value for CodeMirror's `language` facet, which re-parses the whole
 * document and leaves it unhighlighted until the parse catches up. When the
 * editor reconfigures on a timer - and the workbench polls git, review state
 * and the debugger - that repeated re-parse is what reads as flicker.
 *
 * Extensions are immutable descriptors, so a single instance per language is
 * safe to share across states, including both panes of a split view.
 */

import { type Extension } from "@codemirror/state";
import { type LanguageName, loadLanguage } from "@uiw/codemirror-extensions-langs";

const LANGUAGE_ALIASES: Record<string, LanguageName> = {
  bash: "bash",
  dockerfile: "bash",
  golang: "go",
  javascript: "js",
  json: "json",
  jsonl: "json",
  jsx: "jsx",
  markdown: "markdown",
  python: "python",
  scss: "scss",
  sh: "sh",
  shell: "sh",
  text: "textile",
  tsx: "tsx",
  typescript: "ts",
  yaml: "yaml",
  yml: "yml",
};

const cache = new Map<string, Extension | null>();

/**
 * Extension for a language name, or `null` for plain text.
 *
 * The same name always yields the same instance, so rebuilding the extension
 * list does not disturb an editor that is already showing that language.
 */
export function cachedLanguageSupport(language: string): Extension | null {
  const normalized = (language || "").toLowerCase();
  const name = LANGUAGE_ALIASES[normalized] ?? (normalized as LanguageName);
  const cached = cache.get(name);
  if (cached !== undefined) return cached;
  let support: Extension | null = null;
  try {
    support = loadLanguage(name);
  } catch {
    // Unknown language: plain text, and remembered as such so an unsupported
    // file does not retry the lookup on every rebuild.
  }
  cache.set(name, support);
  return support;
}

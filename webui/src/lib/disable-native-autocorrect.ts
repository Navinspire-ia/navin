// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Stop the OS from rewriting typed tokens (gpt -> got, etc.).
 *
 * macOS WKWebView and Safari apply system spelling correction and text
 * replacements inside inputs. That is not a Navin feature. Windows and
 * Linux are much quieter, but the same HTML flags keep them honest.
 */

const EDITABLE_SELECTOR = "input, textarea, [contenteditable]:not([contenteditable='false'])";

function isEditable(el: Element): boolean {
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA") return true;
  if (el.getAttribute("contenteditable") === "true") return true;
  return "isContentEditable" in el && Boolean((el as HTMLElement).isContentEditable);
}

export function disableNativeAutocorrectOn(el: Element): void {
  if (!isEditable(el)) return;
  el.setAttribute("spellcheck", "false");
  el.setAttribute("autocorrect", "off");
  el.setAttribute("autocapitalize", "off");
  const type = (el.getAttribute("type") || "").toLowerCase();
  const existing = (el.getAttribute("autocomplete") || "").trim().toLowerCase();
  if (type === "password") return;
  if (existing && existing !== "on" && existing !== "off") return;
  el.setAttribute("autocomplete", "off");
}

export function disableNativeAutocorrectTree(root: ParentNode): void {
  if (root instanceof Element) disableNativeAutocorrectOn(root);
  root.querySelectorAll(EDITABLE_SELECTOR).forEach(disableNativeAutocorrectOn);
}

/** Apply on focus so every dynamically created field is covered. */
export function installDisableNativeAutocorrect(doc: Document = document): () => void {
  doc.documentElement.setAttribute("spellcheck", "false");
  disableNativeAutocorrectTree(doc);
  const onFocus = (event: Event) => {
    const target = event.target;
    if (target instanceof Element) disableNativeAutocorrectOn(target);
  };
  doc.addEventListener("focusin", onFocus, true);
  return () => doc.removeEventListener("focusin", onFocus, true);
}

/**
 * Multi-cursor + rectangular (column) selection for CodeEditor.
 *
 * Cursor-parity bindings come from CodeMirror's default/search keymaps
 * (Mod-d, Mod-Alt-ArrowUp/Down). This module makes the prerequisites
 * explicit so we do not depend on opaque basicSetup defaults
 * (uiw leaves crosshairCursor off unless opted in).
 */

import { EditorState, type Extension } from "@codemirror/state";
import { crosshairCursor, rectangularSelection } from "@codemirror/view";

/** Extensions that enable multi-cursor editing and Alt-drag column select. */
export function multiCursorExtensions(): Extension[] {
  return [
    EditorState.allowMultipleSelections.of(true),
    rectangularSelection(),
    // Visual cue while Alt is held (pairs with rectangularSelection).
    crosshairCursor(),
  ];
}

/** basicSetup flags that must stay on for the same behavior. */
export const multiCursorBasicSetup = {
  allowMultipleSelections: true as const,
  rectangularSelection: true as const,
  crosshairCursor: true as const,
};

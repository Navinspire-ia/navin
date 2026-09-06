import { EditorSelection, EditorState } from "@codemirror/state";
import { selectNextOccurrence } from "@codemirror/search";
import { describe, expect, it } from "vitest";

import { multiCursorBasicSetup, multiCursorExtensions } from "./multiCursor";

describe("multiCursorExtensions", () => {
  it("enables the allowMultipleSelections facet", () => {
    const state = EditorState.create({
      doc: "a\nb\n",
      extensions: multiCursorExtensions(),
    });
    expect(state.facet(EditorState.allowMultipleSelections)).toBe(true);
  });

  it("keeps multiple cursors instead of collapsing to one", () => {
    const selection = EditorSelection.create([
      EditorSelection.cursor(0),
      EditorSelection.cursor(2),
    ]);
    const without = EditorState.create({
      doc: "aa\nbb\n",
      selection,
    });
    expect(without.selection.ranges).toHaveLength(1);

    const withMulti = EditorState.create({
      doc: "aa\nbb\n",
      selection,
      extensions: multiCursorExtensions(),
    });
    expect(withMulti.selection.ranges).toHaveLength(2);
  });

  it("selectNextOccurrence (Mod-d) adds another matching range", () => {
    let state = EditorState.create({
      doc: "foo bar foo baz foo\n",
      selection: EditorSelection.range(0, 3),
      extensions: multiCursorExtensions(),
    });
    const ok = selectNextOccurrence({
      state,
      dispatch(tr) {
        state = tr.state;
      },
    });
    expect(ok).toBe(true);
    expect(state.selection.ranges.length).toBe(2);
    expect(state.sliceDoc(state.selection.ranges[1]!.from, state.selection.ranges[1]!.to)).toBe(
      "foo",
    );
  });

  it("exports basicSetup flags including crosshairCursor", () => {
    expect(multiCursorBasicSetup.allowMultipleSelections).toBe(true);
    expect(multiCursorBasicSetup.rectangularSelection).toBe(true);
    expect(multiCursorBasicSetup.crosshairCursor).toBe(true);
  });
});

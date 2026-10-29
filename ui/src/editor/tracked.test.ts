import { EditorSelection, EditorState } from "@codemirror/state";
import { describe, expect, it } from "vitest";
import { hiddenDeletions, markEffect, trackedField } from "./tracked";

// One paragraph on one line, as papers written on Overleaf usually have them.
const BASE = "The rainfall is sensitive to the physics, and the timing error is shared by all runs.\nNext.\n";

function editor(text: string, cursor: number): EditorState {
  return EditorState.create({ doc: text, selection: EditorSelection.cursor(cursor), extensions: [trackedField] });
}

/** What the editor does a moment after typing stops. */
function remark(state: EditorState): EditorState {
  return state.update({ effects: markEffect(BASE, state.doc.toString()) }).state;
}

function shownDeletions(state: EditorState): number {
  let all = 0;
  state.field(trackedField).deleted.between(0, state.doc.length, () => {
    all++;
  });
  return all - hiddenDeletions(state).length;
}

describe("deletions in the editor", () => {
  it("shows deletions elsewhere in the paragraph being edited", () => {
    const text = BASE.replace("is sensitive", "issitive").replace("the timing error", "the error");
    const state = remark(editor(text, text.indexOf("issitive") + "issitive".length));
    // The deletion beside the cursor waits; the other one, on the same line, shows.
    expect(hiddenDeletions(state)).toHaveLength(1);
    expect(shownDeletions(state)).toBe(1);
  });

  it("does not show what is being backspaced until the cursor moves on", () => {
    const end = BASE.indexOf("sensitive") + "sensitive".length;
    let state = remark(editor(BASE, end));
    const backspace = (s: EditorState) => {
      const head = s.selection.main.head;
      return s.update({ changes: { from: head - 1, to: head }, selection: EditorSelection.cursor(head - 1) }).state;
    };
    state = remark(backspace(backspace(state)));
    expect(shownDeletions(state)).toBe(0);
    state = backspace(state); // before the marks are worked out again
    expect(shownDeletions(state)).toBe(0);
    state = remark(state);
    expect(shownDeletions(state)).toBe(0);
    state = state.update({ selection: EditorSelection.cursor(0) }).state;
    expect(shownDeletions(state)).toBe(1);
  });

  it("does not show a word being typed over until the cursor moves on", () => {
    const from = BASE.indexOf("sensitive");
    let state = remark(
      editor(BASE, from).update({ selection: EditorSelection.range(from, from + "sensitive".length) }).state,
    );
    for (const ch of "keen") {
      const sel = state.selection.main;
      state = state.update({ changes: { from: sel.from, to: sel.to, insert: ch }, selection: EditorSelection.cursor(sel.from + 1) })
        .state;
      expect(shownDeletions(state)).toBe(0);
      if (ch === "e") state = remark(state);
      expect(shownDeletions(state)).toBe(0);
    }
    state = state.update({ selection: EditorSelection.cursor(BASE.indexOf("Next")) }).state;
    expect(shownDeletions(state)).toBe(1);
  });
});

import { EditorSelection, EditorState } from "@codemirror/state";
import { describe, expect, it } from "vitest";
import { trackChanges, trackedField } from "./tracked";

// One paragraph on one line, as papers written on Overleaf usually have them.
const BASE = "The rainfall is sensitive to the physics, and the timing error is shared by all runs.\nNext.\n";

function editor(text: string, cursor = 0): EditorState {
  return EditorState.create({ doc: text, selection: EditorSelection.cursor(cursor), extensions: trackChanges(BASE, () => undefined) });
}

const type = (state: EditorState, text: string) => {
  const { from, to } = state.selection.main;
  return state.update({ changes: { from, to, insert: text }, selection: EditorSelection.cursor(from + text.length) }).state;
};

const backspace = (state: EditorState) => {
  const head = state.selection.main.head;
  return state.update({ changes: { from: head - 1, to: head }, selection: EditorSelection.cursor(head - 1) }).state;
};

function deletions(state: EditorState): number {
  let n = 0;
  state.field(trackedField).deleted.between(0, state.doc.length, () => {
    n++;
  });
  return n;
}

function insertedText(state: EditorState): string[] {
  const out: string[] = [];
  state.field(trackedField).inserted.between(0, state.doc.length, (from, to) => {
    out.push(state.sliceDoc(from, to));
  });
  return out;
}

describe("marks while typing", () => {
  it("shows a new word with its first letter, with no space and no wait", () => {
    let state = editor(BASE, BASE.indexOf("rainfall"));
    state = type(state, "h");
    expect(insertedText(state)).toEqual(["h"]);
    state = type(state, "e");
    expect(insertedText(state)).toEqual(["he"]);
  });

  it("marks a letter typed into a word at once, and only that letter", () => {
    const at = BASE.indexOf("rainfall") + "rain".length;
    const state = type(editor(BASE, at), "y");
    expect(insertedText(state)).toEqual(["y"]);
    expect(deletions(state)).toBe(0);
  });

  it("shows what is being backspaced as it goes", () => {
    const end = BASE.indexOf("sensitive") + "sensitive".length;
    let state = backspace(editor(BASE, end));
    expect(deletions(state)).toBe(1);
    state = backspace(state);
    expect(deletions(state)).toBe(1);
    expect(state.field(trackedField).count).toBe(1);
  });

  it("shows a word typed over with the first letter", () => {
    const from = BASE.indexOf("sensitive");
    const selected = editor(BASE).update({ selection: EditorSelection.range(from, from + "sensitive".length) }).state;
    const state = type(selected, "k");
    expect(deletions(state)).toBe(1);
    expect(insertedText(state)).toEqual(["k"]);
  });

  it("clears the marks when the text goes back to what it was", () => {
    let state = type(editor(BASE, 4), "x");
    expect(state.field(trackedField).count).toBe(1);
    state = backspace(state);
    expect(state.field(trackedField).count).toBe(0);
  });

  it("keeps up on a long paper that has changed throughout", () => {
    const lines = Array.from({ length: 4000 }, (_, i) => `Paragraph ${i} says something about deep convection and rainfall over the Amazon basin in the wet season, and much more besides.`);
    const baseline = `${lines.join("\n")}\n`;
    const current = `${lines.map((l, i) => (i % 8 === 0 ? l.replace("something", "a thing") : l)).join("\n")}\n`;
    let state = EditorState.create({ doc: current, selection: EditorSelection.cursor(100), extensions: trackChanges(baseline, () => undefined) });
    const started = performance.now();
    for (let i = 0; i < 30; i++) state = type(state, "x");
    const each = (performance.now() - started) / 30;
    // One frame is 16 ms; a keystroke must leave room for the rest of the editor's work.
    expect(each).toBeLessThan(12);
  });
});

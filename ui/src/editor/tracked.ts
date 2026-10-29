// Marks in the editor for what changed since the revision's baseline: inserted text tinted, deleted text
// shown struck through where it was. Recomputed a moment after typing stops. The deletion you are making
// or typing over is not shown while the cursor is at it: it would push the text about under the cursor.
// It appears once the cursor moves on.

import { RangeSetBuilder, StateEffect, StateField, type EditorState, type Extension } from "@codemirror/state";
import { Decoration, EditorView, ViewPlugin, WidgetType, type DecorationSet, type ViewUpdate } from "@codemirror/view";
import { trackedChanges } from "../trackchanges";

const LONG = 400;

interface Marks {
  inserted: DecorationSet;
  deleted: DecorationSet;
  /** The text edited since the marks were last worked out, which they do not cover yet. */
  edited: { from: number; to: number } | null;
}

const setMarks = StateEffect.define<Marks>();

class Deleted extends WidgetType {
  constructor(readonly text: string) {
    super();
  }

  eq(other: Deleted) {
    return other.text === this.text;
  }

  toDOM() {
    const el = document.createElement("del");
    el.className = "cm-tracked-del";
    el.textContent = this.text.length > LONG ? `${this.text.slice(0, LONG)}...` : this.text;
    el.title = "Deleted since the revision started";
    return el;
  }

  ignoreEvent() {
    return false;
  }
}

// Text typed at the end of an insertion joins it at once, before the marks are worked out again.
const inserted = Decoration.mark({
  class: "cm-tracked-ins",
  attributes: { title: "Added since the revision started" },
  inclusiveEnd: true,
});

function marks(baseline: string, text: string): { marks: Marks; count: number } {
  const added = new RangeSetBuilder<Decoration>();
  const removed = new RangeSetBuilder<Decoration>();
  const changes = trackedChanges(baseline, text);
  for (const c of changes) {
    if (c.deleted) removed.add(c.from, c.from, Decoration.widget({ widget: new Deleted(c.deleted), side: -1 }));
    if (c.to > c.from) added.add(c.from, c.to, inserted);
  }
  return { marks: { inserted: added.finish(), deleted: removed.finish(), edited: null }, count: changes.length };
}

export const trackedField = StateField.define<Marks>({
  create: () => ({ inserted: Decoration.none, deleted: Decoration.none, edited: null }),
  update(value, tr) {
    for (const e of tr.effects) if (e.is(setMarks)) return e.value;
    if (!tr.docChanged) return value;
    let edited = value.edited && { from: tr.changes.mapPos(value.edited.from, -1), to: tr.changes.mapPos(value.edited.to, 1) };
    tr.changes.iterChangedRanges((_fromA, _toA, from, to) => {
      edited = edited ? { from: Math.min(edited.from, from), to: Math.max(edited.to, to) } : { from, to };
    });
    return { inserted: value.inserted.map(tr.changes), deleted: value.deleted.map(tr.changes), edited };
  },
  provide: (f) => [
    EditorView.decorations.from(f, (value) => value.inserted),
    EditorView.decorations.compute([f, "selection"], (state) => {
      const hidden = new Set(hiddenDeletions(state));
      return state.field(f).deleted.update({ filter: (at) => !hidden.has(at) });
    }),
  ],
});

/** Where deletions are left out: the one the cursor is at, or at the start of the insertion the cursor is
 * typing, and any in text edited since the marks were last worked out. */
export function hiddenDeletions(state: EditorState): number[] {
  const { inserted, deleted, edited } = state.field(trackedField);
  const heads = state.selection.ranges.map((r) => r.head);
  const hidden: number[] = [];
  deleted.between(0, state.doc.length, (at) => {
    let hide = (edited !== null && at >= edited.from && at <= edited.to) || heads.includes(at);
    if (!hide) {
      inserted.between(at, at, (from, to) => {
        if (from === at && heads.some((head) => head >= from && head <= to)) hide = true;
      });
    }
    if (hide) hidden.push(at);
  });
  return hidden;
}

/** The marks for a text, as the editor sets them; for tests. */
export function markEffect(baseline: string, text: string) {
  return setMarks.of(marks(baseline, text).marks);
}

/** onCount hears how many changes there are each time the marks are recomputed. */
export function trackChanges(baseline: string, onCount: (n: number) => void): Extension {
  const plugin = ViewPlugin.fromClass(
    class {
      timer = 0;

      constructor(view: EditorView) {
        this.schedule(view, 0);
      }

      update(update: ViewUpdate) {
        if (update.docChanged) this.schedule(update.view, 300);
      }

      schedule(view: EditorView, ms: number) {
        window.clearTimeout(this.timer);
        this.timer = window.setTimeout(() => {
          const found = marks(baseline, view.state.doc.toString());
          view.dispatch({ effects: setMarks.of(found.marks) });
          onCount(found.count);
        }, ms);
      }

      destroy() {
        window.clearTimeout(this.timer);
      }
    },
  );
  return [trackedField, plugin];
}

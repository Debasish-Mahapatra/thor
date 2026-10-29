// Marks in the editor for what changed since the revision's baseline: inserted text tinted, deleted text
// shown struck through where it was. Recomputed a moment after typing stops. Deleted text is left out on
// the lines with the cursor, where it would push the text about while you type or delete.

import { RangeSetBuilder, StateEffect, StateField, type Extension } from "@codemirror/state";
import { Decoration, EditorView, ViewPlugin, WidgetType, type DecorationSet, type ViewUpdate } from "@codemirror/view";
import { trackedChanges } from "../trackchanges";

const LONG = 400;

interface Marks {
  inserted: DecorationSet;
  deleted: DecorationSet;
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

const inserted = Decoration.mark({ class: "cm-tracked-ins", attributes: { title: "Added since the revision started" } });

function marks(baseline: string, text: string): { marks: Marks; count: number } {
  const added = new RangeSetBuilder<Decoration>();
  const removed = new RangeSetBuilder<Decoration>();
  const changes = trackedChanges(baseline, text);
  for (const c of changes) {
    if (c.deleted) removed.add(c.from, c.from, Decoration.widget({ widget: new Deleted(c.deleted), side: -1 }));
    if (c.to > c.from) added.add(c.from, c.to, inserted);
  }
  return { marks: { inserted: added.finish(), deleted: removed.finish() }, count: changes.length };
}

const field = StateField.define<Marks>({
  create: () => ({ inserted: Decoration.none, deleted: Decoration.none }),
  update(value, tr) {
    for (const e of tr.effects) if (e.is(setMarks)) return e.value;
    return tr.docChanged
      ? { inserted: value.inserted.map(tr.changes), deleted: value.deleted.map(tr.changes) }
      : value;
  },
  provide: (f) => [
    EditorView.decorations.from(f, (value) => value.inserted),
    EditorView.decorations.compute([f, "selection"], (state) => {
      const editing = state.selection.ranges.map((r) => [state.doc.lineAt(r.from).from, state.doc.lineAt(r.to).to]);
      return state.field(f).deleted.update({ filter: (at) => !editing.some(([from, to]) => at >= from && at <= to) });
    }),
  ],
});

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
  return [field, plugin];
}

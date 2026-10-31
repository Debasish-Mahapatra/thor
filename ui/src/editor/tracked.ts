// Marks in the editor for what changed since the revision's baseline: inserted text tinted, deleted text
// shown struck through where it was. They are worked out with each keystroke, in the same update as the text, so
// they are never behind what you are typing. trackchanges.ts keeps that fast by redoing only the edited paragraph.

import { Facet, RangeSetBuilder, StateField, type EditorState, type Extension } from "@codemirror/state";
import { Decoration, EditorView, ViewPlugin, WidgetType, type DecorationSet } from "@codemirror/view";
import { trackedChanges } from "../trackchanges";

const LONG = 400;

interface Marks {
  inserted: DecorationSet;
  deleted: DecorationSet;
  count: number;
}

const NO_MARKS: Marks = { inserted: Decoration.none, deleted: Decoration.none, count: 0 };

const baselineOf = Facet.define<string, string | null>({ combine: (values) => values[0] ?? null });

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

function marksOf(state: EditorState): Marks {
  const baseline = state.facet(baselineOf);
  if (baseline === null) return NO_MARKS;
  const added = new RangeSetBuilder<Decoration>();
  const removed = new RangeSetBuilder<Decoration>();
  const changes = trackedChanges(baseline, state.doc.toString());
  for (const c of changes) {
    if (c.deleted) removed.add(c.from, c.from, Decoration.widget({ widget: new Deleted(c.deleted), side: -1 }));
    if (c.to > c.from) added.add(c.from, c.to, inserted);
  }
  return { inserted: added.finish(), deleted: removed.finish(), count: changes.length };
}

export const trackedField = StateField.define<Marks>({
  create: marksOf,
  update(value, tr) {
    return tr.docChanged || tr.startState.facet(baselineOf) !== tr.state.facet(baselineOf) ? marksOf(tr.state) : value;
  },
  provide: (f) => [EditorView.decorations.from(f, (value) => value.inserted), EditorView.decorations.from(f, (value) => value.deleted)],
});

/** The extension for a baseline; onCount hears how many changes there are, whenever that changes. */
export function trackChanges(baseline: string, onCount: (n: number) => void): Extension {
  let told = -1;
  const tell = (view: EditorView) => {
    const count = view.state.field(trackedField).count;
    if (count !== told) {
      told = count;
      onCount(count);
    }
  };
  return [
    baselineOf.of(baseline),
    trackedField,
    // React state cannot change during the editor's update, so the count follows just after it.
    ViewPlugin.define((view) => {
      window.setTimeout(() => tell(view), 0);
      return {
        update(update) {
          if (update.docChanged) window.setTimeout(() => tell(update.view), 0);
        },
      };
    }),
  ];
}

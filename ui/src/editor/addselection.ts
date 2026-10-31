// A small button under selected text that adds it to the agent's next message.

import { StateField, type EditorState } from "@codemirror/state";
import { showTooltip, type Tooltip } from "@codemirror/view";

function tooltipFor(state: EditorState, onAdd: () => void): readonly Tooltip[] {
  const range = state.selection.main;
  if (range.empty) return [];
  return [
    {
      pos: range.to,
      above: false,
      strictSide: false,
      arrow: false,
      create() {
        const dom = document.createElement("button");
        dom.className = "add-to-agent";
        dom.textContent = "Add to agent  ⌘L";
        dom.title = "Add this text to your next message to the agent. You can add several.";
        // Keeps the selection: a click here must not move the cursor first.
        dom.onmousedown = (e) => {
          e.preventDefault();
          onAdd();
        };
        return { dom };
      },
    },
  ];
}

export function addSelectionButton(onAdd: () => void) {
  return StateField.define<readonly Tooltip[]>({
    create: (state) => tooltipFor(state, onAdd),
    update(value, tr) {
      return tr.docChanged || tr.selection ? tooltipFor(tr.state, onAdd) : value;
    },
    provide: (f) => showTooltip.computeN([f], (state) => state.field(f)),
  });
}

// Spell check through macOS, so the words you taught your Mac apply. It reads only prose: never commands, their
// non-text arguments, math, comments or the preamble. Outside the app there is no system spell checker, and this does nothing.

import { RangeSetBuilder, StateEffect, StateField } from "@codemirror/state";
import { Decoration, EditorView, ViewPlugin, type DecorationSet, type ViewUpdate } from "@codemirror/view";
import { inShell, spellCheck, spellGuesses, spellLearn } from "../native";

// Commands whose argument is not prose.
const NOT_PROSE = /^\\(?:label|ref|eqref|pageref|autoref|[cC]ref|[a-zA-Z]*cite[a-zA-Z]*\*?|input|include|includegraphics\*?|bibliography[a-zA-Z]*|usepackage|documentclass|begin|end|url|href|hspace\*?|vspace\*?|setlength|addtolength|graphicspath|definecolor|color|textcolor|pagestyle|thispagestyle|newcommand|renewcommand|providecommand|nocite|figfile)\b/;
const MATH_ENV = /^(?:equation|align|gather|multline|displaymath|eqnarray|math|flalign|alignat|tikzpicture|verbatim|lstlisting|minted|tabular|array)\*?$/;
const WORD = /[A-Za-z\u00C0-\u024F][A-Za-z\u00C0-\u024F'’]*/y;

export interface Word {
  from: number;
  to: number;
  text: string;
}

/** The words of prose in a LaTeX text, with where they are. */
export function proseWords(text: string): Word[] {
  const start = text.indexOf("\\begin{document}");
  let i = start >= 0 ? start + "\\begin{document}".length : text.includes("\\documentclass") ? text.length : 0;
  const words: Word[] = [];
  while (i < text.length) {
    const c = text[i];
    if (c === "%") {
      const end = text.indexOf("\n", i);
      i = end < 0 ? text.length : end + 1;
    } else if (c === "$") {
      const double = text[i + 1] === "$";
      const close = text.indexOf(double ? "$$" : "$", i + (double ? 2 : 1));
      i = close < 0 ? text.length : close + (double ? 2 : 1);
    } else if (c === "\\") {
      if (text[i + 1] === "[" || text[i + 1] === "(") {
        const closing = text[i + 1] === "[" ? "\\]" : "\\)";
        const close = text.indexOf(closing, i + 2);
        i = close < 0 ? text.length : close + 2;
        continue;
      }
      const begin = /^\\begin\{([^}]*)\}/.exec(text.slice(i, i + 60));
      if (begin && MATH_ENV.test(begin[1])) {
        const closing = `\\end{${begin[1]}}`;
        const close = text.indexOf(closing, i);
        i = close < 0 ? text.length : close + closing.length;
        continue;
      }
      const command = /^\\[a-zA-Z@]+\*?/.exec(text.slice(i, i + 60));
      if (!command) {
        i += 2;
        continue;
      }
      const skip = NOT_PROSE.test(command[0]);
      i += command[0].length;
      if (skip) {
        // Its optional and required arguments.
        while (text[i] === "[" || text[i] === "{") {
          const open = text[i];
          const shut = open === "[" ? "]" : "}";
          let depth = 0;
          let j = i;
          for (; j < text.length; j++) {
            if (text[j] === "\\") j++;
            else if (text[j] === open) depth++;
            else if (text[j] === shut && --depth === 0) break;
          }
          i = j + 1;
        }
      }
    } else {
      WORD.lastIndex = i;
      const found = WORD.exec(text);
      if (found) {
        const word = found[0].replace(/['’]+$/, "");
        if (word.length > 1) words.push({ from: i, to: i + word.length, text: word });
        i += found[0].length;
      } else {
        i++;
      }
    }
  }
  return words;
}

const setWrong = StateEffect.define<DecorationSet>();
const wrongMark = Decoration.mark({ class: "cm-misspelled" });

const wrongField = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(value, tr) {
    for (const e of tr.effects) if (e.is(setWrong)) return e.value;
    return tr.docChanged ? value.map(tr.changes) : value;
  },
  provide: (f) => EditorView.decorations.from(f),
});

// Answers from the Mac are kept, so that typing asks only about new words.
const known = new Map<string, boolean>();

async function recheck(view: EditorView): Promise<void> {
  const text = view.state.doc.toString();
  const words = proseWords(text);
  const unknown = [...new Set(words.map((w) => w.text))].filter((w) => !known.has(w));
  if (unknown.length) {
    const wrong = new Set(await spellCheck(unknown));
    for (const w of unknown) known.set(w, wrong.has(w));
  }
  if (view.state.doc.toString() !== text) return; // the next check has this
  const builder = new RangeSetBuilder<Decoration>();
  for (const w of words) if (known.get(w.text)) builder.add(w.from, w.to, wrongMark);
  view.dispatch({ effects: setWrong.of(builder.finish()) });
}

function closeMenu(): void {
  document.querySelector(".spell-menu")?.remove();
}

function showMenu(view: EditorView, word: Word, x: number, y: number, guesses: string[]): void {
  closeMenu();
  const menu = document.createElement("div");
  menu.className = "spell-menu";
  menu.style.left = `${x}px`;
  menu.style.top = `${y}px`;
  const add = (label: string, act: () => void) => {
    const item = document.createElement("button");
    item.textContent = label;
    item.onclick = () => {
      closeMenu();
      act();
    };
    menu.appendChild(item);
  };
  for (const guess of guesses.slice(0, 6)) add(guess, () => view.dispatch({ changes: { from: word.from, to: word.to, insert: guess } }));
  add(`Learn "${word.text}"`, () => {
    known.set(word.text, false);
    void spellLearn(word.text).then(() => recheck(view));
  });
  document.body.appendChild(menu);
  window.setTimeout(() => document.addEventListener("mousedown", (e) => !menu.contains(e.target as Node) && closeMenu(), { once: true }), 0);
}

export const spellChecking = inShell()
  ? [
      wrongField,
      ViewPlugin.fromClass(
        class {
          timer = 0;

          constructor(view: EditorView) {
            this.schedule(view, 200);
          }

          update(update: ViewUpdate) {
            if (update.docChanged) this.schedule(update.view, 500);
          }

          schedule(view: EditorView, ms: number) {
            window.clearTimeout(this.timer);
            this.timer = window.setTimeout(() => void recheck(view), ms);
          }

          destroy() {
            window.clearTimeout(this.timer);
          }
        },
      ),
      EditorView.domEventHandlers({
        contextmenu(event, view) {
          const pos = view.posAtCoords({ x: event.clientX, y: event.clientY });
          if (pos === null) return false;
          let found: Word | null = null;
          view.state.field(wrongField).between(pos, pos, (from, to) => {
            found = { from, to, text: view.state.sliceDoc(from, to) };
          });
          if (!found) return false;
          event.preventDefault();
          const word: Word = found;
          void spellGuesses(word.text).then((guesses) => showMenu(view, word, event.clientX, event.clientY, guesses));
          return true;
        },
      }),
    ]
  : [];

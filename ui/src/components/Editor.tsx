import { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap } from "@codemirror/autocomplete";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { bracketMatching, foldGutter, foldKeymap, indentOnInput } from "@codemirror/language";
import { highlightSelectionMatches, searchKeymap } from "@codemirror/search";
import { Annotation, EditorState, Prec, Transaction } from "@codemirror/state";
import {
  drawSelection,
  dropCursor,
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
} from "@codemirror/view";
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { editorTheme, latexBrackets, latexHighlight, latexLanguage } from "../editor/latex";

// Marks text loaded from disk, which is not an edit.
const fromDisk = Annotation.define<boolean>();

export interface EditorHandle {
  goto(line: number): void;
  text(): string;
  /** Replaces the whole text, keeping the cursor where it was as far as the new text allows. */
  setText(text: string): void;
  cursorLine(): number;
  focus(): void;
}

interface Props {
  /** A new path makes a new editor, with its own undo history. */
  path: string;
  initialText: string;
  latex: boolean;
  onChange: () => void;
  onSave: () => void;
  onCompile: () => void;
  onSyncToPdf: (line: number) => void;
}

export const Editor = forwardRef<EditorHandle, Props>(function Editor(props, ref) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  // Handlers change on every render; the editor reads the latest through this ref.
  const handlers = useRef(props);
  handlers.current = props;

  useEffect(() => {
    const run = (action: () => void) => () => {
      action();
      return true;
    };
    const commands = Prec.highest(
      keymap.of([
        { key: "Mod-s", run: run(() => handlers.current.onSave()) },
        { key: "Mod-b", run: run(() => handlers.current.onCompile()) },
        { key: "Mod-Enter", run: run(() => handlers.current.onCompile()) },
        {
          key: "Mod-j",
          run: (v) => {
            handlers.current.onSyncToPdf(v.state.doc.lineAt(v.state.selection.main.head).number);
            return true;
          },
        },
      ]),
    );
    const state = EditorState.create({
      doc: props.initialText,
      extensions: [
        commands,
        lineNumbers(),
        highlightActiveLineGutter(),
        foldGutter(),
        history(),
        // No blinking cursor: nothing in the app flashes.
        drawSelection({ cursorBlinkRate: 0 }),
        dropCursor(),
        EditorState.allowMultipleSelections.of(true),
        indentOnInput(),
        bracketMatching(),
        closeBrackets(),
        autocompletion({ activateOnTyping: false }),
        highlightActiveLine(),
        highlightSelectionMatches(),
        keymap.of([
          ...closeBracketsKeymap,
          ...defaultKeymap,
          ...searchKeymap,
          ...historyKeymap,
          ...foldKeymap,
          ...completionKeymap,
          indentWithTab,
        ]),
        ...(props.latex ? [latexLanguage, latexBrackets, latexHighlight] : []),
        editorTheme,
        EditorView.lineWrapping,
        EditorView.contentAttributes.of({ spellcheck: "false", autocorrect: "off", autocapitalize: "off" }),
        EditorView.updateListener.of((update) => {
          if (update.docChanged && !update.transactions.some((tr) => tr.annotation(fromDisk))) {
            handlers.current.onChange();
          }
        }),
      ],
    });
    const editor = new EditorView({ state, parent: host.current! });
    view.current = editor;
    return () => {
      editor.destroy();
      view.current = null;
    };
    // The editor is made once per file; later text arrives through setText.
  }, [props.path]);

  useImperativeHandle(ref, () => ({
    goto(line: number) {
      const v = view.current;
      if (!v) return;
      const target = v.state.doc.line(Math.min(Math.max(1, line), v.state.doc.lines));
      v.dispatch({ selection: { anchor: target.from }, effects: EditorView.scrollIntoView(target.from, { y: "center" }) });
      v.focus();
    },
    text() {
      return view.current?.state.doc.toString() ?? "";
    },
    setText(text: string) {
      const v = view.current;
      if (!v) return;
      const head = Math.min(v.state.selection.main.head, text.length);
      v.dispatch({
        changes: { from: 0, to: v.state.doc.length, insert: text },
        selection: { anchor: head },
        annotations: [fromDisk.of(true), Transaction.addToHistory.of(false)],
      });
    },
    cursorLine() {
      const v = view.current;
      return v ? v.state.doc.lineAt(v.state.selection.main.head).number : 1;
    },
    focus() {
      view.current?.focus();
    },
  }));

  return <div className="cm-host" ref={host} />;
});

import { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap } from "@codemirror/autocomplete";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { bracketMatching, foldGutter, foldKeymap, indentOnInput } from "@codemirror/language";
import { highlightSelectionMatches, searchKeymap } from "@codemirror/search";
import { Annotation, Compartment, EditorState, Prec, Transaction, type Extension } from "@codemirror/state";
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
import type { AidData, StyleRule } from "../api";
import { addSelectionButton } from "../editor/addselection";
import { htmlToLatex } from "../editor/insert";
import { latexCompletions } from "../editor/completion";
import { editorTheme, latexBrackets, latexHighlight, latexLanguage } from "../editor/latex";
import { latexLinter } from "../editor/lint";
import { mathPreview } from "../editor/mathpreview";
import { spellChecking } from "../editor/spell";
import { trackChanges } from "../editor/tracked";
import { visualMode } from "../editor/visual";

// Marks text loaded from disk, which is not an edit.
const fromDisk = Annotation.define<boolean>();

export interface EditorHandle {
  goto(line: number): void;
  text(): string;
  /** Replaces the whole text, keeping the cursor where it was as far as the new text allows. */
  setText(text: string): void;
  cursorLine(): number;
  focus(): void;
  /** Replaces the whole text as an edit (undoable, saved like typing). */
  replaceAll(text: string): void;
  /** The selected text and its lines; null when nothing is selected. */
  selection(): { from: number; to: number; text: string } | null;
  /** Puts text at the cursor, as if typed. */
  insert(text: string): void;
  /** Puts a table or figure on lines of its own, in the document's body even if the cursor is in the preamble. */
  insertBlock(text: string): void;
}

export type EditorKeys = "default" | "vim" | "emacs";

/** Where a document's body starts: the line after \\begin{document}, or the top when it has none. */
function bodyStart(text: string): number {
  const begin = text.indexOf("\\begin{document}");
  if (begin < 0) return 0;
  const end = text.indexOf("\n", begin);
  return end < 0 ? text.length : end + 1;
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
  /** The revision's baseline of this file: what changed since is marked. Null shows no marks. */
  baseline?: string | null;
  onTrackedCount?: (count: number | null) => void;
  /** Called when the selected lines change; null when nothing is selected. */
  onSelectionLines?: (lines: { from: number; to: number } | null) => void;
  /** Adds the selected text to the agent's next message. */
  onAddSelection: () => void;
  aids: () => AidData | null;
  styleRules: () => StyleRule[];
  keys: EditorKeys;
  /** Shows LaTeX as it reads; whatever holds the cursor stays source. */
  visual: boolean;
  figureUrl: (name: string) => string;
  figureMacro: string;
}

export const Editor = forwardRef<EditorHandle, Props>(function Editor(props, ref) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const tracking = useRef(new Compartment());
  const keyMode = useRef(new Compartment());
  const visualArea = useRef(new Compartment());
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
        { key: "Mod-l", run: run(() => handlers.current.onAddSelection()) },
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
        keyMode.current.of([]),
        visualArea.current.of([]),
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
        autocompletion({ activateOnTyping: true, icons: false, override: props.latex ? [latexCompletions(() => handlers.current.aids())] : null }),
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
        ...(/\.(tex|ltx)$/.test(props.path)
          ? [latexLinter(() => handlers.current.styleRules()), mathPreview(() => handlers.current.aids()?.macros ?? {}), spellChecking]
          : []),
        addSelectionButton(() => handlers.current.onAddSelection()),
        EditorView.domEventHandlers({
          // Text from Word or a web page arrives as LaTeX while writing visually. In the source, a paste stays as pasted.
          paste(event, v) {
            if (!handlers.current.visual) return false;
            const html = event.clipboardData?.getData("text/html");
            const latex = html ? htmlToLatex(html) : null;
            if (!latex) return false;
            event.preventDefault();
            v.dispatch(v.state.replaceSelection(latex));
            return true;
          },
        }),
        tracking.current.of(tracked(props.baseline)),
        editorTheme,
        EditorView.lineWrapping,
        EditorView.contentAttributes.of({ spellcheck: "false", autocorrect: "off", autocapitalize: "off" }),
        EditorView.updateListener.of((update) => {
          if (update.docChanged && !update.transactions.some((tr) => tr.annotation(fromDisk))) {
            handlers.current.onChange();
          }
          if (update.selectionSet) {
            const { main } = update.state.selection;
            handlers.current.onSelectionLines?.(
              main.empty
                ? null
                : { from: update.state.doc.lineAt(main.from).number, to: update.state.doc.lineAt(main.to).number },
            );
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

  function tracked(baseline: string | null | undefined) {
    return baseline == null ? [] : trackChanges(baseline, (n) => handlers.current.onTrackedCount?.(n));
  }

  useEffect(() => {
    view.current?.dispatch({ effects: tracking.current.reconfigure(tracked(props.baseline)) });
    if (props.baseline == null) handlers.current.onTrackedCount?.(null);
  }, [props.baseline]);

  useEffect(() => {
    const on = props.visual && /\.(tex|ltx)$/.test(props.path);
    const v = view.current;
    // The preamble folds in this view, and a cursor must not sit hidden inside it.
    const start = v && on && v.state.selection.main.head === 0 ? bodyStart(v.state.doc.toString()) : 0;
    v?.dispatch({
      selection: start ? { anchor: start } : undefined,
      effects: visualArea.current.reconfigure(
        on
          ? visualMode({
              figureUrl: (name) => handlers.current.figureUrl(name),
              macros: () => handlers.current.aids()?.macros ?? {},
              figureMacro: props.figureMacro,
            })
          : [],
      ),
    });
  }, [props.visual, props.path, props.figureMacro]);

  // Vim and Emacs load only for those who use them.
  useEffect(() => {
    let live = true;
    const apply = (extension: Extension) => {
      if (live) view.current?.dispatch({ effects: keyMode.current.reconfigure(extension) });
    };
    if (props.keys === "vim") void import("@replit/codemirror-vim").then((m) => apply(m.vim({ status: true })));
    else if (props.keys === "emacs") void import("@replit/codemirror-emacs").then((m) => apply(m.emacs()));
    else apply([]);
    return () => {
      live = false;
    };
  }, [props.keys, props.path]);

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
    replaceAll(text: string) {
      const v = view.current;
      if (!v) return;
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: text } });
      v.focus();
    },
    insert(text: string) {
      const v = view.current;
      if (!v) return;
      v.dispatch(v.state.replaceSelection(text));
      v.focus();
    },
    insertBlock(text: string) {
      const v = view.current;
      if (!v) return;
      const doc = v.state.doc;
      const head = Math.max(v.state.selection.main.head, bodyStart(doc.toString()));
      const line = doc.lineAt(head);
      // At the start of an empty line it can go right there; otherwise on the next line.
      const at = line.length === 0 ? line.from : line.to;
      const insert = (line.length === 0 ? "" : "\n") + text.replace(/\n$/, "") + "\n";
      v.dispatch({
        changes: { from: at, insert },
        selection: { anchor: at + insert.length },
        effects: EditorView.scrollIntoView(at, { y: "center" }),
      });
      v.focus();
    },
    selection() {
      const v = view.current;
      if (!v || v.state.selection.main.empty) return null;
      const { from, to } = v.state.selection.main;
      return { from: v.state.doc.lineAt(from).number, to: v.state.doc.lineAt(to).number, text: v.state.sliceDoc(from, to) };
    },
  }));

  return <div className="cm-host" ref={host} />;
});

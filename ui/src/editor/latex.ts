import { HighlightStyle, StreamLanguage, syntaxHighlighting } from "@codemirror/language";
import { stex } from "@codemirror/legacy-modes/mode/stex";
import { EditorView } from "@codemirror/view";
import { tags as t } from "@lezer/highlight";

// CodeMirror's LaTeX mode: commands come out as tagName, environment names as atom, math as keyword.
export const latexLanguage = StreamLanguage.define(stex);

// In prose, quotes and apostrophes must not auto-close; only brackets do.
export const latexBrackets = latexLanguage.data.of({ closeBrackets: { brackets: ["(", "[", "{"] } });

export const latexHighlight = syntaxHighlighting(
  HighlightStyle.define([
    { tag: t.tagName, color: "var(--tok-command)" },
    { tag: t.atom, color: "var(--tok-arg)" },
    { tag: [t.keyword, t.number], color: "var(--tok-math)" },
    { tag: t.comment, color: "var(--tok-comment)", fontStyle: "italic" },
    { tag: t.bracket, color: "var(--tok-bracket)" },
    { tag: t.invalid, color: "var(--bad)" },
  ]),
);

// Colours come from the CSS variables, so the editor follows light and dark mode.
export const editorTheme = EditorView.theme({
  "&": { backgroundColor: "var(--editor-bg)", color: "var(--text)", fontSize: "13px" },
  ".cm-content": { fontFamily: "var(--mono)", caretColor: "var(--accent)", padding: "6px 0" },
  ".cm-scroller": { lineHeight: "1.55", fontFamily: "var(--mono)" },
  ".cm-gutters": { backgroundColor: "var(--editor-bg)", color: "var(--editor-gutter)", border: "none" },
  ".cm-lineNumbers .cm-gutterElement": { padding: "0 12px 0 14px" },
  ".cm-activeLine": { backgroundColor: "var(--editor-active)" },
  ".cm-activeLineGutter": { backgroundColor: "var(--editor-active)", color: "var(--text)" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--accent)", borderLeftWidth: "2px" },
  "&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection":
    { backgroundColor: "var(--editor-selection)" },
  // CodeMirror draws selections under the text, where the current line's background and the tracked-change marks hide them.
  ".cm-selectionLayer": { zIndex: "1 !important", pointerEvents: "none" },
  ".cm-selectionMatch": { backgroundColor: "var(--hover)" },
  "&.cm-focused .cm-matchingBracket": { backgroundColor: "var(--hover)", outline: "1px solid var(--line)" },
  ".cm-foldGutter .cm-gutterElement": { color: "var(--editor-gutter)" },
  ".cm-panels": { backgroundColor: "var(--raised)", color: "var(--text)", borderColor: "var(--line)" },
  ".cm-panels input, .cm-panels button": { fontSize: "12px" },
  ".cm-searchMatch": { backgroundColor: "var(--warn-soft)", outline: "1px solid var(--warn)" },
  ".cm-searchMatch-selected": { backgroundColor: "var(--sync)" },
});

// The visual editor: LaTeX shown as it reads, in the same editor. Headings look like headings, math is set, citations
// are chips, figures and tables are drawn. The text underneath stays LaTeX, and whatever holds the cursor shows as source,
// so nothing is out of reach and undo, autosave and track changes work as ever.

import { StateField, type Extension, type Range, type Text } from "@codemirror/state";
import { Decoration, EditorView, WidgetType, type DecorationSet } from "@codemirror/view";
import { renderMath } from "./mathpreview";

export type Span =
  | { kind: "wrapper"; from: number; to: number; innerFrom: number; innerTo: number; className: string; prefix?: string }
  | { kind: "math"; from: number; to: number; source: string; display: boolean }
  | { kind: "chip"; from: number; to: number; label: string; className: string }
  | { kind: "figure"; from: number; to: number; name: string }
  | { kind: "table"; from: number; to: number; rows: string[][]; headerRows: number }
  | { kind: "bullet"; from: number; to: number; label: string }
  | { kind: "hidden"; from: number; to: number }
  | { kind: "symbol"; from: number; to: number; text: string }
  | { kind: "preamble"; from: number; to: number; lines: number }
  | { kind: "comment"; from: number; to: number };

const WRAPPERS: Record<string, string> = {
  section: "cm-v-h1",
  subsection: "cm-v-h2",
  subsubsection: "cm-v-h3",
  paragraph: "cm-v-h4",
  title: "cm-v-title",
  author: "cm-v-author",
  textbf: "cm-v-bold",
  emph: "cm-v-italic",
  textit: "cm-v-italic",
  underline: "cm-v-underline",
  texttt: "cm-v-mono",
  caption: "cm-v-caption",
};
const MATH_ENV = /\\begin\{(equation|align|gather|multline|eqnarray|displaymath|flalign)(\*?)\}[\s\S]*?\\end\{\1\2\}/g;
const DISPLAY = /\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]/g;
const INLINE = /(?<!\\)\$(?:[^$\\\n]|\\.)+?\$|\\\([\s\S]+?\\\)/g;
const TABULAR = /\\begin\{(tabular\*?|tabularx)\}(?:\[[^\]]*\])?(?:\{[^{}]*\})?\{(?:[^{}]|\{[^{}]*\})*\}([\s\S]*?)\\end\{\1\}/g;
const INCLUDE = /\\includegraphics\*?(?:\[[^\]]*\])?\{([^{}#]+)\}/g;
const COMMAND = /\\(section|subsection|subsubsection|paragraph|title|author|textbf|emph|textit|underline|texttt|caption)(\*?)(?:\[[^\]]*\])?\{/g;
const CITE = /\\([a-zA-Z]*cite[a-zA-Z]*)\*?(?:\[[^\]]*\])*\{([^{}]*)\}/g;
const REF = /\\(eqref|ref|autoref|pageref|[cC]ref|label)\{([^{}]*)\}/g;
const LIST_ITEM = /^[ \t]*\\item\b(?:\[[^\]]*\])?/gm;
const LIST_ENV = /^[ \t]*\\(begin|end)\{(itemize|enumerate)\}[ \t]*$/gm;
const RULES = /\\(?:hline|toprule|midrule|bottomrule|cmidrule(?:\([^)]*\))?\{[^}]*\})/g;
const TEX_COMMENT = /(?<!\\)%[^\n]*/g;
const STRUCTURE = /^[ \t]*(?:\\(?:begin|end)\{(?:figure\*?|table\*?|center)\}(?:\[[^\]]*\])?|\\centering|\\maketitle|\\end\{abstract\})[ \t]*\n?/gm;
const ABSTRACT = /^[ \t]*\\begin\{abstract\}[ \t]*\n?/gm;
const SYMBOLS = /---|--|~|\\[%&#_$]/g;
const SYMBOL_TEXT: Record<string, string> = { "---": "—", "--": "–", "~": " " };

/** Blanks a range without moving what follows, so positions found in the result hold for the document. */
function mask(text: string, from: number, to: number): string {
  return text.slice(0, from) + text.slice(from, to).replace(/[^\n]/g, " ") + text.slice(to);
}

function closingBrace(text: string, open: number): number {
  let depth = 0;
  for (let i = open; i < text.length; i++) {
    const c = text[i];
    if (c === "\\") i++;
    else if (c === "{") depth++;
    else if (c === "}" && --depth === 0) return i;
  }
  return -1;
}

export function parseTable(body: string): { rows: string[][]; headerRows: number } {
  const lines = body.replace(TEX_COMMENT, "").split(/\\\\(?:\[[^\]]*\])?/);
  const rows: string[][] = [];
  let headerRows = 0;
  for (const line of lines) {
    const hadRule = /\\midrule|\\hline/.test(line);
    if (hadRule && rows.length > 0 && headerRows === 0) headerRows = rows.length;
    const clean = line.replace(RULES, "").trim();
    if (clean) rows.push(clean.split(/(?<!\\)&/).map((cell) => cell.trim()));
  }
  return { rows, headerRows };
}

/** Finds what can be drawn. Pure, so it can be tested and cached: selection only decides which of these show. */
export function parseVisual(doc: string, figureMacro = ""): Span[] {
  const spans: Span[] = [];
  const begin = doc.indexOf("\\begin{document}");
  const start = begin >= 0 ? begin + "\\begin{document}".length : 0;
  let text = " ".repeat(start) + doc.slice(start);
  for (const m of text.matchAll(TEX_COMMENT)) {
    spans.push({ kind: "comment", from: m.index, to: m.index + m[0].length });
    text = mask(text, m.index, m.index + m[0].length);
  }
  const claim = (pattern: RegExp, make: (m: RegExpMatchArray, from: number, to: number) => Span | null) => {
    for (const m of text.matchAll(pattern)) {
      const from = m.index!;
      const to = from + m[0].length;
      const span = make(m, from, to);
      if (span) spans.push(span);
      text = mask(text, from, to);
    }
  };
  claim(MATH_ENV, (m, from, to) => {
    const body = m[0].slice(m[0].indexOf("}") + 1, m[0].lastIndexOf("\\end")).replace(/\\label\{[^}]*\}/g, "").replace(/\\nonumber|\\notag/g, "").trim();
    const aligned = m[1] === "align" || m[1] === "flalign" || m[1] === "eqnarray" ? `\\begin{aligned}${body}\\end{aligned}` : m[1] === "gather" ? `\\begin{gathered}${body}\\end{gathered}` : body;
    return { kind: "math", from, to, source: aligned, display: true };
  });
  claim(DISPLAY, (m, from, to) => ({ kind: "math", from, to, source: m[0].slice(2, -2).trim(), display: true }));
  claim(TABULAR, (m, from, to) => ({ kind: "table", from, to, ...parseTable(m[2]) }));
  claim(INLINE, (m, from, to) => ({ kind: "math", from, to, source: m[0].startsWith("$") ? m[0].slice(1, -1) : m[0].slice(2, -2), display: false }));
  claim(INCLUDE, (m, from, to) => ({ kind: "figure", from, to, name: m[1].trim() }));
  if (figureMacro) {
    claim(new RegExp(`\\\\${figureMacro.replace(/[^a-zA-Z]/g, "")}\\{[^{}]*\\}\\{([^{}#]+)\\}`, "g"), (m, from, to) => ({ kind: "figure", from, to, name: m[1].trim() }));
  }
  claim(CITE, (m, from, to) => ({ kind: "chip", from, to, label: m[2].split(",").map((k) => k.trim()).join(", "), className: "cm-v-cite" }));
  claim(REF, (m, from, to) => (m[1] === "label" ? { kind: "chip", from, to, label: `#${m[2]}`, className: "cm-v-label" } : { kind: "chip", from, to, label: m[2], className: "cm-v-ref" }));
  for (const m of text.matchAll(STRUCTURE)) spans.push({ kind: "hidden", from: m.index, to: m.index + m[0].length });
  for (const m of text.matchAll(ABSTRACT)) spans.push({ kind: "bullet", from: m.index, to: m.index + m[0].length, label: "Abstract. " });
  for (const m of text.matchAll(SYMBOLS)) spans.push({ kind: "symbol", from: m.index, to: m.index + m[0].length, text: SYMBOL_TEXT[m[0]] ?? m[0].slice(1) });
  if (begin > 0) {
    const lineEnd = doc.indexOf("\n", begin);
    const end = lineEnd < 0 ? doc.length : lineEnd + 1;
    spans.push({ kind: "preamble", from: 0, to: end, lines: doc.slice(0, end).split("\n").length - 1 });
  }
  const counters: number[] = [];
  const events: { at: number; end: number; kind: "begin" | "end" | "item"; list?: string }[] = [];
  for (const m of text.matchAll(LIST_ENV)) events.push({ at: m.index, end: m.index + m[0].length, kind: m[1] as "begin" | "end", list: m[2] });
  for (const m of text.matchAll(LIST_ITEM)) events.push({ at: m.index, end: m.index + m[0].length, kind: "item" });
  events.sort((a, b) => a.at - b.at);
  const kinds: string[] = [];
  for (const e of events) {
    if (e.kind === "begin") {
      kinds.push(e.list!);
      counters.push(0);
      spans.push({ kind: "hidden", from: e.at, to: e.end });
    } else if (e.kind === "end") {
      kinds.pop();
      counters.pop();
      spans.push({ kind: "hidden", from: e.at, to: e.end });
    } else if (kinds.length) {
      counters[counters.length - 1]++;
      const label = kinds[kinds.length - 1] === "enumerate" ? `${counters[counters.length - 1]}.` : "•";
      spans.push({ kind: "bullet", from: e.at, to: e.end, label });
    }
  }
  for (const m of text.matchAll(COMMAND)) {
    const open = m.index + m[0].length - 1;
    const close = closingBrace(text, open);
    if (close > open) {
      spans.push({ kind: "wrapper", from: m.index, to: close + 1, innerFrom: open + 1, innerTo: close, className: WRAPPERS[m[1]], prefix: m[1] === "caption" ? "Caption: " : undefined });
    }
  }
  return spans;
}

const rendered = new Map<string, HTMLElement>();
const RENDERED_KEPT = 2000;

class MathWidget extends WidgetType {
  constructor(
    readonly source: string,
    readonly display: boolean,
    readonly macros: () => Record<string, string>,
  ) {
    super();
  }
  eq(other: MathWidget) {
    return other.source === this.source && other.display === this.display;
  }
  toDOM(view: EditorView) {
    const dom = document.createElement(this.display ? "div" : "span");
    dom.className = this.display ? "cm-v-math display" : "cm-v-math";
    const key = `${this.display}:${this.source}`;
    const done = rendered.get(key);
    if (done) {
      dom.append(done.cloneNode(true));
      return dom;
    }
    // Until KaTeX has loaded, the first time only, the source stands in.
    dom.textContent = this.source;
    void renderMath({ from: 0, to: 0, source: this.source, display: this.display }, this.macros()).then((math) => {
      if (rendered.size >= RENDERED_KEPT) rendered.clear();
      rendered.set(key, math);
      dom.replaceChildren(math.cloneNode(true));
      view.requestMeasure();
    });
    return dom;
  }
  ignoreEvent() {
    return false;
  }
}

class TextWidget extends WidgetType {
  constructor(
    readonly label: string,
    readonly className: string,
  ) {
    super();
  }
  eq(other: TextWidget) {
    return other.label === this.label && other.className === this.className;
  }
  toDOM() {
    const dom = document.createElement("span");
    dom.className = this.className;
    dom.textContent = this.label;
    return dom;
  }
  ignoreEvent() {
    return false;
  }
}

/** Closed unless the cursor is inside it, which a click on this does: the cursor starts at the top of a file. */
class PreambleWidget extends WidgetType {
  constructor(readonly lines: number) {
    super();
  }
  eq(other: PreambleWidget) {
    return other.lines === this.lines;
  }
  toDOM(view: EditorView) {
    const dom = document.createElement("div");
    dom.className = "cm-v-preamble";
    dom.textContent = `Preamble, ${this.lines} lines. Click to show it.`;
    dom.onmousedown = (event) => {
      event.preventDefault();
      view.dispatch({ selection: { anchor: 1 } });
      view.focus();
    };
    return dom;
  }
  ignoreEvent() {
    return true;
  }
}

class FigureWidget extends WidgetType {
  constructor(
    readonly name: string,
    readonly url: (name: string) => string,
  ) {
    super();
  }
  eq(other: FigureWidget) {
    return other.name === this.name;
  }
  toDOM(view: EditorView) {
    const dom = document.createElement("div");
    dom.className = "cm-v-figure";
    const image = document.createElement("img");
    image.alt = this.name;
    image.src = this.url(this.name);
    image.onload = () => view.requestMeasure();
    image.onerror = () => {
      dom.textContent = `Figure ${this.name} was not found.`;
      view.requestMeasure();
    };
    dom.append(image);
    return dom;
  }
  ignoreEvent() {
    return false;
  }
}

class TableWidget extends WidgetType {
  constructor(
    readonly rows: string[][],
    readonly headerRows: number,
  ) {
    super();
  }
  eq(other: TableWidget) {
    return JSON.stringify(other.rows) === JSON.stringify(this.rows);
  }
  toDOM() {
    const table = document.createElement("table");
    table.className = "cm-v-table";
    this.rows.forEach((row, index) => {
      const tr = table.insertRow();
      for (const cell of row) {
        const td = document.createElement(index < this.headerRows ? "th" : "td");
        td.textContent = cell.replace(/\\([%&#_$])/g, "$1");
        tr.append(td);
      }
    });
    return table;
  }
  ignoreEvent() {
    return false;
  }
}

export interface VisualOptions {
  figureUrl: (name: string) => string;
  macros: () => Record<string, string>;
  figureMacro: string;
}

const hide = Decoration.replace({});
const dimmed = Decoration.mark({ class: "cm-v-comment" });

function decorations(spans: Span[], doc: Text, heads: { from: number; to: number }[], options: VisualOptions): DecorationSet {
  const ranges: Range<Decoration>[] = [];
  const touched = (from: number, to: number) => heads.some((h) => h.from <= to && h.to >= from);
  for (const span of spans) {
    if (span.to > doc.length) continue;
    if (span.kind === "comment") {
      ranges.push(dimmed.range(span.from, span.to));
      continue;
    }
    if (span.kind === "preamble") {
      if (!heads.some((h) => h.from > span.from && h.from < span.to)) {
        ranges.push(Decoration.replace({ widget: new PreambleWidget(span.lines) }).range(span.from, span.to));
      }
      continue;
    }
    if (touched(span.from, span.to)) continue;
    switch (span.kind) {
      case "wrapper":
        if (span.innerTo > span.innerFrom) {
          const opening = span.prefix ? Decoration.replace({ widget: new TextWidget(span.prefix, "cm-v-prefix") }) : hide;
          ranges.push(opening.range(span.from, span.innerFrom));
          ranges.push(Decoration.mark({ class: span.className }).range(span.innerFrom, span.innerTo));
          ranges.push(hide.range(span.innerTo, span.to));
        }
        break;
      case "math":
        ranges.push(Decoration.replace({ widget: new MathWidget(span.source, span.display, options.macros) }).range(span.from, span.to));
        break;
      case "chip":
        ranges.push(Decoration.replace({ widget: new TextWidget(span.label, `cm-v-chip ${span.className}`) }).range(span.from, span.to));
        break;
      case "figure":
        ranges.push(Decoration.replace({ widget: new FigureWidget(span.name, options.figureUrl) }).range(span.from, span.to));
        break;
      case "table":
        ranges.push(Decoration.replace({ widget: new TableWidget(span.rows, span.headerRows) }).range(span.from, span.to));
        break;
      case "bullet":
        ranges.push(Decoration.replace({ widget: new TextWidget(span.label, "cm-v-bullet") }).range(span.from, span.to));
        break;
      case "hidden":
        ranges.push(hide.range(span.from, span.to));
        break;
      case "symbol":
        ranges.push(Decoration.replace({ widget: new TextWidget(span.text, "") }).range(span.from, span.to));
        break;
    }
  }
  return Decoration.set(ranges, true);
}

/** Off, this adds nothing at all to the editor; the toggle turns it on and off through a compartment. */
export function visualMode(options: VisualOptions): Extension {
  const field = StateField.define<{ spans: Span[]; set: DecorationSet }>({
    create(state) {
      const spans = parseVisual(state.doc.toString(), options.figureMacro);
      return { spans, set: decorations(spans, state.doc, [...state.selection.ranges], options) };
    },
    update(value, tr) {
      if (!tr.docChanged && !tr.selection) return value;
      const spans = tr.docChanged ? parseVisual(tr.state.doc.toString(), options.figureMacro) : value.spans;
      return { spans, set: decorations(spans, tr.state.doc, [...tr.state.selection.ranges], options) };
    },
    provide: (f) => EditorView.decorations.from(f, (value) => value.set),
  });
  return [field, EditorView.editorAttributes.of({ class: "cm-visual" })];
}

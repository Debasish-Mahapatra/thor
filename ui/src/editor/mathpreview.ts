// Hover over math to see it set. KaTeX loads only the first time, so it does not slow the app's start.

import { hoverTooltip } from "@codemirror/view";

const MATH = /\$\$([\s\S]+?)\$\$|(?<!\\)\$((?:[^$\\\n]|\\.)+?)\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\\begin\{(equation|align|gather|multline|displaymath|flalign)(\*?)\}([\s\S]*?)\\end\{\5\6\}/g;

export interface Found {
  from: number;
  to: number;
  source: string;
  display: boolean;
}

/** The math around a position, if there is any. */
export function mathAt(text: string, pos: number): Found | null {
  for (const m of text.matchAll(MATH)) {
    const from = m.index;
    if (from > pos) return null;
    const to = from + m[0].length;
    if (pos >= to) continue;
    if (m[5]) {
      const body = m[7].replace(/\\label\{[^}]*\}/g, "").replace(/\\nonumber|\\notag/g, "").trim();
      const wrapped = m[5] === "align" || m[5] === "flalign" ? `\\begin{aligned}${body}\\end{aligned}` : m[5] === "gather" ? `\\begin{gathered}${body}\\end{gathered}` : body;
      return { from, to, source: wrapped, display: true };
    }
    return { from, to, source: (m[1] ?? m[2] ?? m[3] ?? m[4]).trim(), display: m[2] === undefined && m[4] === undefined };
  }
  return null;
}

let katex: Promise<typeof import("katex").default> | null = null;

function loadKatex() {
  katex ??= Promise.all([import("katex"), import("katex/dist/katex.min.css")]).then(([module]) => module.default);
  return katex;
}

/** Renders into a new element; math KaTeX cannot read shows its message instead of failing. */
export async function renderMath(found: Found, macros: Record<string, string>): Promise<HTMLElement> {
  const render = await loadKatex();
  const dom = document.createElement("div");
  dom.className = "math-preview";
  const known: Record<string, string> = {};
  for (const [name, body] of Object.entries(macros)) known[`\\${name}`] = body;
  render.render(found.source, dom, { displayMode: found.display, throwOnError: false, macros: known, trust: false, strict: false });
  return dom;
}

export function mathPreview(macros: () => Record<string, string>) {
  return hoverTooltip(async (view, pos) => {
    const found = mathAt(view.state.doc.toString(), pos);
    if (!found) return null;
    const dom = await renderMath(found, macros());
    return { pos: found.from, end: found.to, above: true, create: () => ({ dom }) };
  });
}

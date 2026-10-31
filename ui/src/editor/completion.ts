// Completion for what a paper refers to: citation keys, labels, file names, environments and common commands.

import type { Completion, CompletionContext, CompletionResult } from "@codemirror/autocomplete";
import type { AidData } from "../api";

export type CompletionKind = "cite" | "ref" | "image" | "tex" | "bib" | "env" | "command";

const CITE = /\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{(?:[^{}]*,)?\s*([^,{}\s]*)$/;
const REF = /\\(?:ref|eqref|pageref|autoref|nameref|[cC]ref|[vV]ref)\*?\{(?:[^{}]*,)?\s*([^,{}\s]*)$/;
const IMAGE = /\\includegraphics\*?(?:\[[^\]]*\])?\{([^{}]*)$/;
const TEX = /\\(?:input|include|subfile)\{([^{}]*)$/;
const BIB = /\\(?:bibliography|addbibresource)(?:\[[^\]]*\])?\{(?:[^{}]*,)?([^,{}]*)$/;
const ENV = /\\(?:begin|end)\{([a-zA-Z*]*)$/;
const COMMAND = /\\([a-zA-Z]*)$/;

/** What the text before the cursor is asking for, and how many of its last characters the answer replaces. */
export function completionKind(before: string): { kind: CompletionKind; typed: number } | null {
  const rules: [RegExp, CompletionKind][] = [[CITE, "cite"], [REF, "ref"], [IMAGE, "image"], [TEX, "tex"], [BIB, "bib"], [ENV, "env"], [COMMAND, "command"]];
  for (const [rule, kind] of rules) {
    const found = rule.exec(before);
    if (found) return { kind, typed: found[1].length };
  }
  return null;
}

const ENVIRONMENTS = [
  "abstract", "align", "align*", "array", "center", "description", "document", "enumerate", "equation", "equation*", "figure", "figure*",
  "flushleft", "flushright", "frame", "gather", "itemize", "minipage", "multline", "quote", "split", "subequations", "table", "table*",
  "tabular", "tabularx", "thebibliography", "verbatim",
];

const COMMANDS = [
  "alpha", "appendix", "approx", "autoref", "begin", "beta", "bibliography", "bibliographystyle", "caption", "centering", "chapter", "cdot",
  "cite", "citep", "citet", "cref", "Delta", "delta", "documentclass", "emph", "end", "epsilon", "eqref", "frac", "gamma", "hline", "href",
  "include", "includegraphics", "infty", "input", "int", "item", "label", "lambda", "left", "leq", "geq", "maketitle", "mathbf", "mathrm",
  "midrule", "mu", "newcommand", "newpage", "noindent", "omega", "paragraph", "partial", "phi", "pi", "rho", "right", "ref", "section",
  "sigma", "sqrt", "subsection", "subsubsection", "sum", "tableofcontents", "tau", "textbf", "textit", "texttt", "textwidth", "theta", "times",
  "title", "toprule", "bottomrule", "usepackage", "url",
];

function optionsFor(kind: CompletionKind, data: AidData | null): Completion[] {
  switch (kind) {
    case "cite":
      return (data?.cites ?? []).map((c) => ({
        label: c.key,
        detail: [c.authors.split(" and ")[0]?.split(",")[0], c.year].filter(Boolean).join(", "),
        info: c.title || undefined,
        // A title or an author's name finds the key, too.
        filterText: `${c.key} ${c.authors} ${c.title}`,
        type: "text",
      }));
    case "ref":
      return (data?.labels ?? []).map((l) => ({ label: l.key, detail: l.near || `${l.file}:${l.line}`, type: "text" }));
    case "image":
      return (data?.images ?? []).map((f) => ({ label: f, type: "text" }));
    case "tex":
      return (data?.tex ?? []).map((f) => ({ label: f.replace(/\.tex$/, ""), type: "text" }));
    case "bib":
      return (data?.bibs ?? []).map((f) => ({ label: f.replace(/\.bib$/, ""), type: "text" }));
    case "env":
      return ENVIRONMENTS.map((label) => ({ label, type: "keyword" }));
    case "command":
      return [...COMMANDS, ...Object.keys(data?.macros ?? {})].map((label) => ({ label: `\\${label}`, type: "keyword" }));
  }
}

export function latexCompletions(data: () => AidData | null) {
  return (context: CompletionContext): CompletionResult | null => {
    const line = context.state.doc.lineAt(context.pos);
    const before = line.text.slice(0, context.pos - line.from);
    const found = completionKind(before);
    if (!found) return null;
    // A bare backslash opens the list only if asked; typing \% or \\ should not bring it up.
    if (found.kind === "command" && found.typed === 0 && !context.explicit) return null;
    const replaced = found.kind === "command" ? found.typed + 1 : found.typed;
    return { from: context.pos - replaced, options: optionsFor(found.kind, data()), validFor: found.kind === "command" ? /^\\[a-zA-Z]*$/ : /^[^,{}\s]*$/ };
  };
}

// Live warnings: braces and environments that do not match, and the paper's own style rules. Comments and verbatim
// text are skipped, and macro definitions in the preamble may open what they do not close, so environments are checked
// only in the document's body.

import { linter, type Diagnostic } from "@codemirror/lint";
import type { StyleRule } from "../api";

const VERBATIM = /^(?:verbatim\*?|lstlisting|minted|comment)$/;
const ENV = /^\\(begin|end)\s*\{([^}]*)\}/;

export interface Problem {
  from: number;
  to: number;
  message: string;
}

export function checkLatex(text: string): Problem[] {
  const problems: Problem[] = [];
  const braces: number[] = [];
  const envs: { name: string; at: number; end: number }[] = [];
  const body = text.indexOf("\\begin{document}");
  // A file with no \documentclass is a part of a paper, and all of it is body.
  const checkEnvsFrom = body >= 0 ? body : text.includes("\\documentclass") ? Infinity : 0;
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    if (c === "%") {
      const end = text.indexOf("\n", i);
      i = end < 0 ? text.length : end + 1;
    } else if (c === "\\") {
      if (text.startsWith("\\verb", i) && !/[a-zA-Z]/.test(text[i + 5] ?? "")) {
        const close = text.indexOf(text[i + 5] ?? "", i + 6);
        i = close < 0 ? i + 5 : close + 1;
        continue;
      }
      const env = ENV.exec(text.slice(i, i + 80));
      if (env) {
        const end = i + env[0].length;
        if (env[1] === "begin" && VERBATIM.test(env[2])) {
          const closing = `\\end{${env[2]}}`;
          const close = text.indexOf(closing, end);
          i = close < 0 ? text.length : close + closing.length;
          continue;
        }
        if (i >= checkEnvsFrom) {
          if (env[1] === "begin") {
            envs.push({ name: env[2], at: i, end });
          } else {
            const open = envs.pop();
            if (!open) {
              problems.push({ from: i, to: end, message: `\\end{${env[2]}} has no \\begin.` });
            } else if (open.name !== env[2]) {
              problems.push({ from: i, to: end, message: `\\end{${env[2]}} closes \\begin{${open.name}}.` });
            }
          }
        }
        i = end;
        continue;
      }
      i += 2;
    } else if (c === "{") {
      braces.push(i);
      i++;
    } else if (c === "}") {
      if (braces.pop() === undefined) problems.push({ from: i, to: i + 1, message: "This } closes nothing." });
      i++;
    } else {
      i++;
    }
  }
  for (const at of braces) problems.push({ from: at, to: at + 1, message: "This { is never closed." });
  for (const open of envs) problems.push({ from: open.at, to: open.end, message: `\\begin{${open.name}} is never closed.` });
  return problems.sort((a, b) => a.from - b.from);
}

export function checkStyle(text: string, rules: StyleRule[]): Problem[] {
  const problems: Problem[] = [];
  const start = Math.max(0, text.indexOf("\\begin{document}"));
  let at = start;
  for (const line of text.slice(start).split("\n")) {
    const comment = /(?<!\\)%/.exec(line);
    const clean = comment ? line.slice(0, comment.index) : line;
    for (const rule of rules) {
      let rx: RegExp;
      try {
        rx = new RegExp(rule.pattern, `g${rule.flags === "i" ? "i" : ""}u`);
      } catch {
        continue;
      }
      for (const found of clean.matchAll(rx)) {
        problems.push({ from: at + found.index, to: at + found.index + found[0].length, message: rule.message });
      }
    }
    at += line.length + 1;
  }
  return problems;
}

export function latexLinter(rules: () => StyleRule[]) {
  return linter(
    (view): Diagnostic[] => {
      const text = view.state.doc.toString();
      return [
        ...checkLatex(text).map((p): Diagnostic => ({ ...p, severity: "error" })),
        ...checkStyle(text, rules()).map((p): Diagnostic => ({ ...p, severity: "warning" })),
      ];
    },
    { delay: 300 },
  );
}

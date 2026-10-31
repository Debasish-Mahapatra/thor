import { describe, expect, it } from "vitest";
import { completionKind } from "./completion";

describe("completionKind", () => {
  it.each([
    ["\\cite{Pri", "cite", 3],
    ["\\citep[see][p. 3]{Price, Hol", "cite", 3],
    ["\\citeS{", "cite", 0],
    ["Section~\\ref{sec:", "ref", 4],
    ["\\cref{a,b", "ref", 1],
    ["\\includegraphics[width=3cm]{figs/o", "image", 6],
    ["\\input{par", "tex", 3],
    ["\\bibliography{ref", "bib", 3],
    ["\\begin{equa", "env", 4],
    ["\\end{", "env", 0],
    ["Some \\textb", "command", 5],
  ])("reads %s", (before, kind, typed) => {
    expect(completionKind(before)).toEqual({ kind, typed });
  });

  it("stays out of ordinary text and closed arguments", () => {
    expect(completionKind("Just some words")).toBeNull();
    expect(completionKind("\\cite{Price} and more")).toBeNull();
  });
});

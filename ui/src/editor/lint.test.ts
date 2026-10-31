import { describe, expect, it } from "vitest";
import { checkLatex, checkStyle } from "./lint";

const doc = (body: string) => `\\documentclass{article}\n\\begin{document}\n${body}\n\\end{document}\n`;
const messages = (text: string) => checkLatex(text).map((p) => p.message);

describe("checkLatex", () => {
  it("finds nothing in a good file", () => {
    expect(checkLatex(doc("A \\textbf{bold} word.\n\\begin{itemize}\\item x\\end{itemize}"))).toEqual([]);
  });

  it("finds an open brace, a stray brace and where they are", () => {
    const open = doc("A \\textbf{bold word.");
    expect(messages(open)).toEqual(["This { is never closed."]);
    expect(open[checkLatex(open)[0].from]).toBe("{");
    expect(messages(doc("A } here."))).toEqual(["This } closes nothing."]);
  });

  it("finds environments that do not match", () => {
    expect(messages(doc("\\begin{figure}x\\end{table}"))).toEqual(["\\end{table} closes \\begin{figure}."]);
    expect(messages(doc("\\begin{figure}x"))).toEqual(["\\begin{document} is never closed.", "\\end{document} closes \\begin{figure}."]);
    expect(messages("Part of a paper.\n\\end{figure}\n")).toEqual(["\\end{figure} has no \\begin."]);
  });

  it("is not fooled by comments, escapes or verbatim text", () => {
    expect(checkLatex(doc("100\\% % a comment with { and \\begin{x}\n\\{ and \\}"))).toEqual([]);
    expect(checkLatex(doc("\\begin{verbatim}\n{ \\begin{x}\n\\end{verbatim}\n\\verb|{|"))).toEqual([]);
  });

  it("lets a preamble's macros open what they do not close", () => {
    expect(checkLatex("\\documentclass{article}\n\\newcommand{\\st}{\\begin{center}}\n\\begin{document}\nx\n\\end{document}\n")).toEqual([]);
  });
});

describe("checkStyle", () => {
  const rules = [{ pattern: "\\b(\\w+)\\s+\\1\\b", message: "A word is repeated.", flags: "i" }, { pattern: "utilise", message: "Say use.", flags: "" }];

  it("marks where a rule is broken, in the body only", () => {
    const text = doc("We utilise the the data.");
    const found = checkStyle(text, rules);
    expect(found.map((p) => text.slice(p.from, p.to))).toEqual(["the the", "utilise"]);
  });

  it("skips comments and the preamble, and survives a broken rule", () => {
    expect(checkStyle("utilise\n\\begin{document}\n% utilise\n", rules)).toEqual([]);
    expect(checkStyle(doc("x"), [{ pattern: "(", message: "bad", flags: "" }])).toEqual([]);
  });
});

import { describe, expect, it } from "vitest";
import { figureLatex, htmlToLatex, tableLatex, usesBooktabs } from "./insert";
import { parseTable, parseVisual, type Span } from "./visual";

const body = (text: string) => `\\documentclass{article}\n\\usepackage{x}\n\\begin{document}\n${text}\n\\end{document}\n`;
const kinds = (spans: Span[], kind: Span["kind"]) => spans.filter((s) => s.kind === kind);

describe("what the visual editor draws", () => {
  it("reads nothing in the preamble, and folds it as one", () => {
    const doc = "\\newcommand{\\x}{$y$ \\textbf{z}}\n\\begin{document}\nHello\n\\end{document}";
    expect(parseVisual(doc)).toEqual([{ kind: "preamble", from: 0, to: doc.indexOf("Hello"), lines: 2 }]);
  });

  it("shows dashes, ties and escaped signs as they print", () => {
    const doc = body("Between 2014--2015, at 4~km, 12\\% more --- as \\_ said.");
    const symbols = kinds(parseVisual(doc), "symbol") as Extract<Span, { kind: "symbol" }>[];
    expect(symbols.map((s) => s.text)).toEqual(["–", " ", "%", "—", "_"]);
  });

  it("hides the lines that only arrange a figure", () => {
    const doc = body("\\begin{figure}[htbp]\n  \\centering\nText\n\\end{figure}");
    expect((kinds(parseVisual(doc), "hidden") as Extract<Span, { kind: "hidden" }>[]).length).toBe(3);
  });

  it("finds headings and inline styles with their inner text", () => {
    const doc = body("\\section{Results}\nSome \\textbf{bold {nested} text} and \\emph{more}.");
    const wrappers = kinds(parseVisual(doc), "wrapper") as Extract<Span, { kind: "wrapper" }>[];
    expect(wrappers.map((w) => doc.slice(w.innerFrom, w.innerTo))).toEqual(["Results", "bold {nested} text", "more"]);
  });

  it("does not read math or markup inside a comment", () => {
    const spans = parseVisual(body("% $x$ \\textbf{y} \\cite{z}\nA real \\% sign, $a$ here."));
    expect(kinds(spans, "comment")).toHaveLength(1);
    expect((kinds(spans, "math") as Extract<Span, { kind: "math" }>[]).map((m) => m.source)).toEqual(["a"]);
    expect(kinds(spans, "wrapper")).toHaveLength(0);
    expect(kinds(spans, "chip")).toHaveLength(0);
  });

  it("does not take an escaped dollar for math", () => {
    expect(kinds(parseVisual(body("It cost \\$5 and \\$6 in all.")), "math")).toHaveLength(0);
  });

  it("sets display math and aligned environments", () => {
    const spans = parseVisual(body("\\begin{align}\\label{e1}\na &= b \\\\\nc &= d\n\\end{align}\n\\[ x^2 \\]"));
    const math = kinds(spans, "math") as Extract<Span, { kind: "math" }>[];
    expect(math[0].source).toBe("\\begin{aligned}a &= b \\\\\nc &= d\\end{aligned}");
    expect(math[1]).toMatchObject({ source: "x^2", display: true });
    expect(kinds(spans, "chip")).toHaveLength(0);
  });

  it("makes chips of citations and references", () => {
    const chips = kinds(parseVisual(body("\\citep[see][p.~3]{a, b} and \\ref{fig:one} \\label{sec:x}")), "chip") as Extract<Span, { kind: "chip" }>[];
    expect(chips.map((c) => c.label)).toEqual(["a, b", "fig:one", "#sec:x"]);
  });

  it("finds figures by includegraphics and by the paper's macro", () => {
    const spans = parseVisual(body("\\includegraphics[width=2cm]{figs/a}\n\\figfile{width=\\textwidth}{b}"), "figfile");
    expect((kinds(spans, "figure") as Extract<Span, { kind: "figure" }>[]).map((f) => f.name)).toEqual(["figs/a", "b"]);
  });

  it("numbers enumerate and bullets itemize, nested too", () => {
    const doc = body("\\begin{enumerate}\n\\item one\n\\item two\n\\begin{itemize}\n  \\item inner\n\\end{itemize}\n\\item three\n\\end{enumerate}");
    const labels = (kinds(parseVisual(doc), "bullet") as Extract<Span, { kind: "bullet" }>[]).map((b) => b.label);
    expect(labels).toEqual(["1.", "2.", "•", "3."]);
  });

  it("does not move later positions when it blanks earlier ones", () => {
    const doc = body("$a$ then \\emph{word}");
    const wrapper = kinds(parseVisual(doc), "wrapper")[0] as Extract<Span, { kind: "wrapper" }>;
    expect(doc.slice(wrapper.innerFrom, wrapper.innerTo)).toBe("word");
  });
});

describe("tables", () => {
  it("reads rows, cells and the header", () => {
    expect(parseTable("\n\\toprule\nA & B \\\\\n\\midrule\n1 & 2\\% \\\\\n3 & 4 \\\\\n\\bottomrule\n")).toEqual({
      rows: [["A", "B"], ["1", "2\\%"], ["3", "4"]],
      headerRows: 1,
    });
  });

  it("writes booktabs or plain rules, and escapes what would break the compile", () => {
    const made = tableLatex({ cells: [["Run", "Share"], ["a & b", "12%"]], caption: "Runs", label: "tab:runs", booktabs: true });
    expect(made).toContain("\\toprule\n    Run & Share \\\\\n    \\midrule\n    a \\& b & 12\\% \\\\\n    \\bottomrule");
    expect(made).toContain("\\caption{Runs}\n  \\label{tab:runs}");
    expect(made).toContain("\\begin{tabular}{ll}");
    expect(tableLatex({ cells: [["x"]], caption: "", label: "", booktabs: false })).toContain("\\hline\n    x \\\\\n    \\hline");
  });

  it("fills short rows so the columns line up", () => {
    expect(tableLatex({ cells: [["a", "b", "c"], ["d"]], caption: "", label: "", booktabs: true })).toContain("d &  &  \\\\");
  });

  it("sees whether the paper loads booktabs", () => {
    expect(usesBooktabs("\\usepackage{graphicx,booktabs}\n")).toBe(true);
    expect(usesBooktabs("% \\usepackage{booktabs}\n")).toBe(false);
  });
});

describe("figures", () => {
  it("uses the paper's macro when it has one", () => {
    expect(figureLatex({ image: "figures/fig3.pdf", width: 0.8, caption: "C", label: "fig:c", macro: "figfile" })).toContain("\\figfile{width=0.8\\linewidth}{fig3}");
    expect(figureLatex({ image: "figures/fig3.pdf", width: 1, caption: "", label: "", macro: "" })).toContain("\\includegraphics[width=\\linewidth]{figures/fig3.pdf}");
  });
});

describe("a paste from Word", () => {
  it("keeps paragraphs and styles, and drops Word's markup", () => {
    const html = `<html xmlns:o="urn:schemas-microsoft-com:office:office"><head><style>p.MsoNormal{margin:0}</style></head><body>
<!--StartFragment--><p class=MsoNormal>The <b>first</b> para &amp; 50% of it.<o:p></o:p></p>
<p class=MsoNormal><i>Second</i> with CO<sub>2</sub> and x<sup>2</sup>.<o:p></o:p></p><!--EndFragment--></body></html>`;
    expect(htmlToLatex(html)).toBe("The \\textbf{first} para \\& 50\\% of it.\n\n\\emph{Second} with CO\\textsubscript{2} and x\\textsuperscript{2}.");
  });

  it("reads Word's inline styles", () => {
    expect(htmlToLatex(`<p><span style="font-weight:bold">B</span> <span style='font-style:italic'>I</span></p>`)).toBe("\\textbf{B} \\emph{I}");
  });

  it("turns lists and tables into LaTeX", () => {
    expect(htmlToLatex("<ul><li>one</li><li>two</li></ul>")).toBe("\\begin{itemize}\n  \\item one\n  \\item two\n\\end{itemize}");
    const table = htmlToLatex("<table><tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>")!;
    expect(table).toContain("\\begin{tabular}{ll}");
    expect(table).toContain("A & B \\\\");
  });

  it("escapes LaTeX's special characters in the text", () => {
    expect(htmlToLatex("<p>a_b $5 #1 {x}</p>")).toBe("a\\_b \\$5 \\#1 \\{x\\}");
  });

  it("leaves plain text to the normal paste", () => {
    expect(htmlToLatex("just text")).toBeNull();
  });
});

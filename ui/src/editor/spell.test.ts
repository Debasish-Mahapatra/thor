import { describe, expect, it } from "vitest";
import { proseWords } from "./spell";

const words = (text: string) => proseWords(text).map((w) => w.text);
const doc = (body: string) => `\\documentclass{article}\n\\usepackage{amsmath}\n\\begin{document}\n${body}\n\\end{document}\n`;

describe("proseWords", () => {
  it("takes prose, and text inside formatting commands", () => {
    expect(words(doc("A \\textbf{bold} word, isn't it."))).toEqual(["bold", "word", "isn't", "it"]);
  });

  it("skips the preamble, math and comments", () => {
    expect(words(doc("Rain $x + yy$ and $$zz$$ and \\[ww\\] and \\(vv\\). % notaword\nEnd."))).toEqual(["Rain", "and", "and", "and", "End"]);
    expect(words(doc("\\begin{equation}\nqq\n\\end{equation}\nDone"))).toEqual(["Done"]);
  });

  it("skips the arguments of commands that hold no prose", () => {
    expect(words(doc("See \\citep[p.~3]{Price1994} and \\ref{sec:intro}, \\label{xyz}\\includegraphics[width=3cm]{figs/one}."))).toEqual(["See", "and"]);
  });

  it("gives positions that find the word", () => {
    const text = doc("One \\emph{two}");
    for (const w of proseWords(text)) expect(text.slice(w.from, w.to)).toBe(w.text);
  });

  it("treats a file with no documentclass as a part of the body", () => {
    expect(words("Just text here")).toEqual(["Just", "text", "here"]);
  });
});

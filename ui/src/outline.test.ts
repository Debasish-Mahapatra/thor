import { describe, expect, it } from "vitest";
import { cleanTitle, outline, stripComment } from "./outline";
import { fileKind, joinPath, relativeTo } from "./paths";

const PAPER = String.raw`\documentclass{article}
\begin{document}
\begin{abstract}
Lightning matters.
\end{abstract}
\section{Introduction}\label{sec:intro}
% \section{Commented out}
\section*{Results: where \emph{Thor} strikes}
\subsection{Strikes by
  season}
\subsubsection{Ragnarok}
Text with 50\% of flashes % \section{in a comment}
\bibliography{references}
\clearpage
{\Large\bfseries Supplementary Information}\\[8pt]
\section{Supplementary Note 1}
\end{document}`;

describe("outline", () => {
  it("lists the abstract, sections and the Supplementary Information", () => {
    expect(outline(PAPER, "Supplementary Information")).toEqual([
      { title: "Abstract", line: 3, level: 1 },
      { title: "Introduction", line: 6, level: 1 },
      { title: "Results: where Thor strikes", line: 8, level: 1 },
      { title: "Strikes by season", line: 9, level: 2 },
      { title: "Ragnarok", line: 11, level: 3 },
      { title: "Supplementary Information", line: 15, level: 1 },
      { title: "Supplementary Note 1", line: 16, level: 1 },
    ]);
  });

  it("is empty for text without headings", () => {
    expect(outline("Just text.")).toEqual([]);
  });
});

describe("helpers", () => {
  it("strips comments but keeps escaped percent signs", () => {
    expect(stripComment(String.raw`50\% of flashes % a note`)).toBe(String.raw`50\% of flashes `);
  });

  it("cleans titles", () => {
    expect(cleanTitle(String.raw`Lightning~over \textbf{land} \label{x}`)).toBe("Lightning over land");
  });

  it("converts between project and LaTeX-folder paths", () => {
    expect(joinPath("manuscript", "main.tex")).toBe("manuscript/main.tex");
    expect(joinPath(".", "main.tex")).toBe("main.tex");
    expect(relativeTo("manuscript", "manuscript/sub/a.tex")).toBe("sub/a.tex");
    expect(relativeTo("manuscript", "scripts/a.py")).toBeNull();
    expect(fileKind("figures/fig1.PDF")).toBe("pdf");
    expect(fileKind("main.tex")).toBe("text");
    expect(fileKind("map.png")).toBe("image");
  });
});

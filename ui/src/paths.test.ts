import { describe, expect, it } from "vitest";
import { canBeMain, mainFields } from "./paths";

describe("main file settings", () => {
  it("splits a path into the LaTeX folder and the file", () => {
    expect(mainFields("paper.tex")).toEqual({ latex_dir: ".", main: "paper.tex" });
    expect(mainFields("tex/paper.tex")).toEqual({ latex_dir: "tex", main: "paper.tex" });
    expect(mainFields("a/b/paper.tex")).toEqual({ latex_dir: "a/b", main: "paper.tex" });
  });

  it("offers only .tex files outside the revision folders", () => {
    expect(canBeMain("paper.tex")).toBe(true);
    expect(canBeMain("tex/revision_notes.tex")).toBe(true);
    expect(canBeMain("mystyle.sty")).toBe(false);
    expect(canBeMain("revision1/supporting_files/main.tex")).toBe(false);
    expect(canBeMain("tex/revision12/main.tex")).toBe(false);
  });
});

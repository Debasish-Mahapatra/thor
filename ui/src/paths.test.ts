import { describe, expect, it } from "vitest";
import { mainFields } from "./paths";

describe("main file settings", () => {
  it("splits a path into the LaTeX folder and the file", () => {
    expect(mainFields("paper.tex")).toEqual({ latex_dir: ".", main: "paper.tex" });
    expect(mainFields("tex/paper.tex")).toEqual({ latex_dir: "tex", main: "paper.tex" });
    expect(mainFields("a/b/paper.tex")).toEqual({ latex_dir: "a/b", main: "paper.tex" });
  });
});

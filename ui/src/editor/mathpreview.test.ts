import { describe, expect, it } from "vitest";
import { mathAt } from "./mathpreview";

const at = (text: string, needle: string) => mathAt(text, text.indexOf(needle));

describe("mathAt", () => {
  it("finds inline and display math around a position", () => {
    expect(at("Rain $x^2$ falls", "x")).toMatchObject({ source: "x^2", display: false });
    expect(at("Rain \\[y\\] falls", "y")).toMatchObject({ source: "y", display: true });
    expect(at("Rain $$z$$ falls", "z")).toMatchObject({ source: "z", display: true });
    expect(at("Rain \\(w\\) falls", "w")).toMatchObject({ source: "w", display: false });
  });

  it("finds nothing in plain text, or after an escaped dollar", () => {
    expect(at("Rain $x$ falls", "falls")).toBeNull();
    expect(at("It cost \\$5 and more", "cost")).toBeNull();
  });

  it("takes labels out of environments and keeps alignment", () => {
    expect(at("\\begin{equation}\\label{e} E=mc^2 \\end{equation}", "E")).toMatchObject({ source: "E=mc^2", display: true });
    expect(at("\\begin{align*} a &= b \\end{align*}", "a &")).toMatchObject({ source: "\\begin{aligned}a &= b\\end{aligned}" });
  });
});

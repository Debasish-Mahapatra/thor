import { describe, expect, it } from "vitest";
import type { Check } from "./api";
import { ago, formatSize, groupChecks, sharedFix } from "./format";

const check = (id: string, group: string, required: boolean, ok: boolean): Check => ({
  id,
  name: id,
  group,
  needed_for: "",
  required,
  ok,
  version: null,
  path: null,
  detail: "",
  fix: null,
});

describe("groupChecks", () => {
  it("keeps the backend's order and counts missing required checks", () => {
    const groups = groupChecks([
      check("latexmk", "TeX", true, false),
      check("python", "Python", true, true),
      check("xelatex", "TeX", false, false),
      check("bibtex", "TeX", true, false),
    ]);
    expect(groups.map((g) => g.group)).toEqual(["TeX", "Python"]);
    expect(groups[0].checks.map((c) => c.id)).toEqual(["latexmk", "xelatex", "bibtex"]);
    expect(groups.map((g) => g.missingRequired)).toEqual([2, 0]);
  });
});

describe("sharedFix", () => {
  const withFix = (id: string, ok: boolean, fix: string | null): Check => ({ ...check(id, "TeX", true, ok), fix });

  it("returns the fix two or more unmet checks have in common", () => {
    expect(
      sharedFix([withFix("a", false, "Install MacTeX"), withFix("b", false, "Install MacTeX"), withFix("c", false, "other")]),
    ).toBe("Install MacTeX");
  });

  it("ignores met checks and single fixes", () => {
    expect(sharedFix([withFix("a", true, "Install MacTeX"), withFix("b", false, "Install MacTeX")])).toBeNull();
    expect(sharedFix([withFix("a", false, null), withFix("b", false, null)])).toBeNull();
  });
});

describe("formatSize", () => {
  it("uses B, KB and MB", () => {
    expect(formatSize(null)).toBe("");
    expect(formatSize(900)).toBe("900 B");
    expect(formatSize(4096)).toBe("4 KB");
    expect(formatSize(8_062_505)).toBe("7.7 MB");
  });
});

describe("ago", () => {
  it("names the largest whole unit", () => {
    expect(ago(1000, 1030)).toBe("just now");
    expect(ago(0, 3 * 3600 + 10)).toBe("3 hours ago");
    expect(ago(0, 86400)).toBe("1 day ago");
  });
});

import { describe, expect, it } from "vitest";
import type { CommentItem } from "./api";
import { NO_FILTER, matches, summary, where } from "./comments";

const base: CommentItem = {
  id: 1,
  kind: "highlight",
  author: "ada.l",
  person: "Ada",
  page: 3,
  line: 64,
  line_end: 66,
  element: null,
  marked: "north to south",
  note: "",
  insert: "",
  anchor: "",
  marks: [],
  replies: [],
  rects: [],
  context: "",
  baseline: { status: "moved", page: 3, line: 63 },
  question: "Marked with no note: what should change?",
  answer: null,
};

describe("comments", () => {
  it("says where a comment is", () => {
    expect(where(base)).toBe("page 3, line 64-66");
    expect(where({ ...base, line: null, element: "Supplementary Note 3" })).toBe("page 3, Supplementary Note 3");
    expect(where({ ...base, page: null })).toBe("email");
  });

  it("summarises what a comment asks for", () => {
    expect(summary({ ...base, kind: "replacement", marked: "plot", insert: "analysis" })).toBe(
      'Replace "plot" with "analysis"',
    );
    expect(summary({ ...base, kind: "deletion", marked: "very very" })).toBe('Delete "very very"');
    expect(summary({ ...base, kind: "insertion", insert: "and fishers", anchor: "herders" })).toBe(
      'Insert "and fishers" after "herders"',
    );
    expect(summary({ ...base, note: "Which region?" })).toBe("Which region?");
  });

  it("filters", () => {
    expect(matches(base, { ...NO_FILTER, unclear: true })).toBe(true);
    expect(matches({ ...base, answer: "outdoor construction" }, { ...NO_FILTER, unclear: true })).toBe(false);
    expect(matches(base, { ...NO_FILTER, moved: true })).toBe(true);
    expect(matches(base, { ...NO_FILTER, person: "Ada", search: "NORTH" })).toBe(true);
    expect(matches(base, { ...NO_FILTER, kind: "deletion" })).toBe(false);
  });
});

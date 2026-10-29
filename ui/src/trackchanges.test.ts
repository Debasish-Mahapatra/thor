import { describe, expect, it } from "vitest";
import { trackedChanges } from "./trackchanges";

const inserted = (text: string, c: { from: number; to: number }) => text.slice(c.from, c.to);

describe("tracked changes", () => {
  it("finds nothing in the same text", () => {
    expect(trackedChanges("A\nB\n", "A\nB\n")).toEqual([]);
  });

  it("marks an inserted word, not the whole line", () => {
    const now = "Lightning closely follows convection.\nNext line.\n";
    const changes = trackedChanges("Lightning follows convection.\nNext line.\n", now);
    expect(changes).toHaveLength(1);
    expect(inserted(now, changes[0]).trim()).toBe("closely");
    expect(changes[0].deleted).toBe("");
  });

  it("marks replaced words with what they replaced", () => {
    const now = "We count strikes with a new phrase here.";
    const changes = trackedChanges("We count strikes with the old phrase here.", now);
    expect(changes.map((c) => inserted(now, c))).toEqual(["a new"]);
    expect(changes.map((c) => c.deleted)).toEqual(["the old"]);
  });

  it("marks a new paragraph and a deleted one", () => {
    const base = "Intro.\n\nGone paragraph.\n\nEnd.\n";
    const now = "Intro.\n\nEnd.\n\nThis is a test to see track changes.\n";
    const changes = trackedChanges(base, now);
    const added = changes.filter((c) => c.to > c.from).map((c) => inserted(now, c)).join("");
    const removed = changes.map((c) => c.deleted).join("");
    expect(added).toContain("This is a test to see track changes.");
    expect(removed).toContain("Gone paragraph.");
    expect(added).not.toContain("Intro");
  });

  it("keeps positions right after earlier changes", () => {
    const base = "one two three\nfour five six\n";
    const now = "one 2 three\nfour five seven six\n";
    const changes = trackedChanges(base, now);
    expect(changes.map((c) => inserted(now, c).trim()).filter(Boolean)).toEqual(["2", "seven"]);
  });

  it("marks words in a long paper that changed throughout", () => {
    const base = Array.from({ length: 3000 }, (_, i) => `Line ${i} says something about lightning.\n`).join("");
    const now = base
      .split("\n")
      .map((line, i) => (i === 5 || i % 50 === 25 ? line.replace("something", "something else") : line))
      .join("\n");
    const changes = trackedChanges(base, now);
    expect(changes).toHaveLength(61);
    expect(changes.every((c) => now.slice(c.from, c.to).trim() === "else" && c.deleted === "")).toBe(true);
  });
});

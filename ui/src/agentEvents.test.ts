import { describe, expect, it } from "vitest";
import { currentActivity, groupEvents, usageText, type Item } from "./agentEvents";
import type { AgentEvent } from "./api";

let seq = 0;
const ev = (type: AgentEvent["type"], rest: Partial<AgentEvent> = {}): AgentEvent => ({ seq: ++seq, time: 0, type, ...rest });
const kinds = (items: Item[]) => items.map((i) => i.kind);

describe("groupEvents", () => {
  it("merges text and keeps the order around tool calls", () => {
    const items = groupEvents([
      ev("user", { text: "Hi" }),
      ev("text", { text: "One " }),
      ev("text", { text: "two\n" }),
      ev("activity", { id: "a", summary: "Reading main.tex" }),
      ev("text", { text: "three\n" }),
    ]);
    expect(kinds(items)).toEqual(["user", "text", "activity", "text"]);
    expect(items[1]).toMatchObject({ text: "One two\n" });
  });

  it("joins a tool call to its result", () => {
    const items = groupEvents([
      ev("activity", { id: "a", summary: "Reading" }),
      ev("activity", { id: "b", summary: "Editing" }),
      ev("activity_done", { id: "b", ok: false, detail: "No such file" }),
      ev("activity_done", { id: "a", ok: true }),
    ]);
    expect(items[0]).toMatchObject({ state: "ok" });
    expect(items[1]).toMatchObject({ state: "failed", detail: "No such file" });
  });

  it("treats an activity without an id as finished", () => {
    expect(groupEvents([ev("activity", { summary: "Applied 2 edits" })])[0]).toMatchObject({ state: "ok" });
  });

  it("adds up a turn's tokens and shows them at its end", () => {
    const items = groupEvents([
      ev("user", { text: "Hi" }),
      ev("usage", { input_tokens: 10, output_tokens: 2, cost_usd: 0.01 }),
      ev("text", { text: "Answer\n" }),
      ev("usage", { input_tokens: 5, output_tokens: 1, cost_usd: 0.02 }),
      ev("done"),
      ev("user", { text: "Again" }),
    ]);
    expect(kinds(items)).toEqual(["user", "text", "usage", "user"]);
    expect(items[2]).toMatchObject({ input: 15, output: 3 });
    expect((items[2] as { cost: number }).cost).toBeCloseTo(0.03);
  });

  it("keeps the cost unknown when no price is known", () => {
    const items = groupEvents([ev("usage", { input_tokens: 1, output_tokens: 1, cost_usd: null }), ev("done")]);
    expect(items[0]).toMatchObject({ cost: null });
  });

  it("marks answered questions", () => {
    const items = groupEvents([ev("approval", { id: "q", summary: "Run rm" }), ev("approval_done", { id: "q", allowed: false })]);
    expect(items[0]).toMatchObject({ answered: false });
  });

  it("leaves nothing running once the turn is done", () => {
    const items = groupEvents([ev("activity", { id: "a", summary: "Reading" }), ev("done")]);
    expect(items[0]).toMatchObject({ state: "ok" });
  });
});

describe("currentActivity", () => {
  it("names the last thing still running", () => {
    const items = groupEvents([
      ev("activity", { id: "a", summary: "Reading" }),
      ev("activity_done", { id: "a" }),
      ev("activity", { id: "b", summary: "Compiling" }),
    ]);
    expect(currentActivity(items)).toBe("Compiling");
    expect(currentActivity(groupEvents([ev("user", { text: "Hi" })]))).toBe("Working");
  });
});

describe("usageText", () => {
  it("says tokens, and the cost when known", () => {
    expect(usageText(1234, 5, null)).toBe("1,234 tokens in, 5 out");
    expect(usageText(10, 5, 0.256)).toBe("10 tokens in, 5 out, about $0.26");
    expect(usageText(10, 5, 0.001)).toBe("10 tokens in, 5 out, about $0.01 or less");
  });
});

// A chat's event list, grouped into what the transcript shows.

import type { AgentEvent } from "./api";

export type Item =
  | { kind: "user"; event: AgentEvent }
  | { kind: "text"; key: number; text: string }
  | { kind: "activity"; key: number; summary: string; state: "running" | "ok" | "failed"; detail: string }
  | { kind: "compile"; event: AgentEvent }
  | { kind: "changes"; event: AgentEvent }
  | { kind: "approval"; event: AgentEvent; answered: boolean | null }
  | { kind: "notice"; event: AgentEvent }
  | { kind: "error"; event: AgentEvent }
  | { kind: "usage"; key: number; input: number; output: number; cost: number | null };

/** Merges text chunks, joins each tool call to its result, and adds up a turn's tokens at its end. */
export function groupEvents(events: AgentEvent[]): Item[] {
  const items: Item[] = [];
  const activities = new Map<string, Extract<Item, { kind: "activity" }>>();
  const approvals = new Map<string, Extract<Item, { kind: "approval" }>>();
  // In an object, because TypeScript cannot follow a plain variable through the closure below.
  const turn: { usage: Extract<Item, { kind: "usage" }> | null } = { usage: null };
  const endTurn = () => {
    if (turn.usage) items.push(turn.usage);
    turn.usage = null;
  };
  for (const event of events) {
    switch (event.type) {
      case "user":
        endTurn();
        items.push({ kind: "user", event });
        break;
      case "text": {
        const last = items[items.length - 1];
        if (last?.kind === "text") last.text += event.text ?? "";
        else if (event.text) items.push({ kind: "text", key: event.seq, text: event.text });
        break;
      }
      case "activity": {
        // Without an id nothing will report back on it, so it is already finished.
        const item: Extract<Item, { kind: "activity" }> = {
          kind: "activity",
          key: event.seq,
          summary: event.summary ?? "",
          state: event.id ? "running" : "ok",
          detail: "",
        };
        items.push(item);
        if (event.id) activities.set(event.id, item);
        break;
      }
      case "activity_done": {
        const item = event.id ? activities.get(event.id) : undefined;
        if (item) {
          item.state = event.ok === false ? "failed" : "ok";
          item.detail = event.detail ?? "";
        }
        break;
      }
      case "compile":
        items.push({ kind: "compile", event });
        break;
      case "changes":
        items.push({ kind: "changes", event });
        break;
      case "approval": {
        const item: Extract<Item, { kind: "approval" }> = { kind: "approval", event, answered: null };
        items.push(item);
        if (event.id) approvals.set(event.id, item);
        break;
      }
      case "approval_done": {
        const item = event.id ? approvals.get(event.id) : undefined;
        if (item) item.answered = event.allowed ?? false;
        break;
      }
      case "notice":
        items.push({ kind: "notice", event });
        break;
      case "error":
        items.push({ kind: "error", event });
        break;
      case "usage":
        turn.usage = {
          kind: "usage",
          key: event.seq,
          input: (turn.usage?.input ?? 0) + (event.input_tokens ?? 0),
          output: (turn.usage?.output ?? 0) + (event.output_tokens ?? 0),
          cost:
            event.cost_usd === null || event.cost_usd === undefined
              ? (turn.usage?.cost ?? null)
              : (turn.usage?.cost ?? 0) + event.cost_usd,
        };
        break;
      case "done":
        endTurn();
        break;
    }
  }
  endTurn();
  // A run that ended without its result (Thor was closed) has nothing still running.
  const finished = events[events.length - 1]?.type === "done";
  if (finished) {
    for (const item of activities.values()) if (item.state === "running") item.state = "ok";
  }
  return items;
}

/** What the agent is doing now, in words, for the line above the message box. */
export function currentActivity(items: Item[]): string {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (item.kind === "activity" && item.state === "running") return item.summary;
  }
  return "Working";
}

export function usageText(input: number, output: number, cost: number | null): string {
  const tokens = `${input.toLocaleString()} tokens in, ${output.toLocaleString()} out`;
  return cost === null ? tokens : `${tokens}, about $${cost < 0.01 ? "0.01 or less" : cost.toFixed(2)}`;
}

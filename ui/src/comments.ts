import type { CommentItem } from "./api";

export const KIND_LABEL: Record<string, string> = {
  highlight: "Highlight",
  underline: "Underline",
  squiggly: "Squiggly",
  "strike-out": "Strike-out",
  deletion: "Deletion",
  replacement: "Replacement",
  insertion: "Insertion",
  note: "Note",
  "text box": "Text box",
  pen: "Pen marks",
  email: "Email",
};

/** "page 3, line 64-66", "page 31, Supplementary Note 3", "email". */
export function where(c: CommentItem): string {
  if (!c.page) return "email";
  if (c.line) return `page ${c.page}, line ${c.line}${c.line_end ? `-${c.line_end}` : ""}`;
  return c.element ? `page ${c.page}, ${c.element}` : `page ${c.page}`;
}

/** One line saying what the comment asks for. */
export function summary(c: CommentItem): string {
  const quote = (s: string) => `"${s.length > 70 ? `${s.slice(0, 67)}...` : s}"`;
  switch (c.kind) {
    case "deletion":
      return `Delete ${quote(c.marked)}`;
    case "replacement":
      return `Replace ${quote(c.marked)} with ${quote(c.insert)}`;
    case "insertion":
      return `Insert ${quote(c.insert)}${c.anchor ? ` after "${c.anchor}"` : ""}`;
    case "pen":
      return c.note || `${c.marks.map((m) => m.kind).join(", ")}: ${quote(c.marked)}`;
    default:
      return c.note || quote(c.marked);
  }
}

export interface CommentFilter {
  kind: string;
  person: string;
  unclear: boolean;
  moved: boolean;
  search: string;
}

export const NO_FILTER: CommentFilter = { kind: "", person: "", unclear: false, moved: false, search: "" };

export function matches(c: CommentItem, f: CommentFilter): boolean {
  if (f.kind && c.kind !== f.kind) return false;
  if (f.person && c.person !== f.person) return false;
  if (f.unclear && !(c.question && !c.answer)) return false;
  if (f.moved && c.baseline?.status !== "moved" && c.baseline?.status !== "not found") return false;
  if (f.search) {
    const text = `${c.marked} ${c.note} ${c.insert} ${c.context} ${c.replies.map((r) => r.note).join(" ")}`;
    if (!text.toLowerCase().includes(f.search.toLowerCase())) return false;
  }
  return true;
}

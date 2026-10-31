// What changed since the revision's baseline, for marking in the editor: lines are compared first, then
// the words of each changed block, so a reworded sentence shows the words that changed, not the paragraph.

export interface TrackedChange {
  /** The inserted text's place in the current text; from === to when text was only deleted. */
  from: number;
  to: number;
  /** Baseline text removed at `from` ("" when text was only inserted). */
  deleted: string;
}

type Op = "same" | "del" | "ins";

// Longest common subsequence tables above this size are not worth the wait; the block is then shown as
// replaced whole.
const MAX_CELLS = 4_000_000;

// Blank lines and spaces count for less, so they do not anchor the alignment in place of real text.
const weight = (token: string) => (token.trim() ? 2 : 1);

function diff(a: string[], b: string[]): Op[] | null {
  const n = a.length;
  const m = b.length;
  if (n * m > MAX_CELLS) return null;
  const width = m + 1;
  const table = new Uint32Array((n + 1) * width);
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      table[i * width + j] =
        a[i] === b[j]
          ? table[(i + 1) * width + j + 1] + weight(a[i])
          : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
    }
  }
  const ops: Op[] = [];
  let i = 0;
  let j = 0;
  while (i < n || j < m) {
    const skip = Math.max(i < n ? table[(i + 1) * width + j] : 0, j < m ? table[i * width + j + 1] : 0);
    if (i < n && j < m && a[i] === b[j] && table[i * width + j] >= skip) {
      ops.push("same");
      i++;
      j++;
    } else if (j < m && (i === n || table[i * width + j + 1] >= table[(i + 1) * width + j])) {
      ops.push("ins");
      j++;
    } else {
      ops.push("del");
      i++;
    }
  }
  return ops;
}

const lines = (text: string) => text.match(/[^\n]*\n|[^\n]+$/g) ?? [];
const words = (text: string) => text.match(/\s+|[\p{L}\p{N}\\@]+|[^\s\p{L}\p{N}\\@]/gu) ?? [];

/** Within one replaced word, only the letters that differ: "usefull" to "useful" shows one struck letter, not the
 * whole word struck and retyped, so the text barely moves while you type. Unrelated words stay whole. */
function narrowed(start: number, end: number, removed: string, text: string): TrackedChange {
  const added = text.slice(start, end);
  if (removed && added && !/\s/.test(removed + added)) {
    let head = 0;
    while (head < removed.length && head < added.length && removed[head] === added[head]) head++;
    let tail = 0;
    while (tail < removed.length - head && tail < added.length - head && removed[removed.length - 1 - tail] === added[added.length - 1 - tail]) tail++;
    if ((head + tail) * 2 >= Math.max(removed.length, added.length)) {
      return { from: start + head, to: end - tail, deleted: removed.slice(head, removed.length - tail) };
    }
  }
  return { from: start, to: end, deleted: removed };
}

function compareWords(deleted: string, inserted: string): TrackedChange[] {
  const a = words(deleted);
  const b = words(inserted);
  // The words around an edit are the same, so only the middle needs the table.
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head++;
  let tail = 0;
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++;
  const middle = diff(a.slice(head, a.length - tail), b.slice(head, b.length - tail));
  if (!middle) return [{ from: 0, to: inserted.length, deleted }];
  const ops: Op[] = [...same(head), ...middle, ...same(tail)];
  const out: TrackedChange[] = [];
  let i = 0;
  let j = 0;
  let pos = 0;
  let start = 0;
  let removed = "";
  const flush = () => {
    if (removed || pos > start) out.push(narrowed(start, pos, removed, inserted));
    removed = "";
  };
  for (const op of ops) {
    if (op === "same") {
      flush();
      pos += b[j].length;
      i++;
      j++;
      start = pos;
    } else if (op === "del") {
      removed += a[i++];
    } else {
      pos += b[j++].length;
    }
  }
  flush();
  return out;
}

// The marks are worked out on every keystroke, yet only the paragraph being edited has changed since the last time.
const compared = new Map<string, TrackedChange[]>();
const MAX_COMPARED = 5000;

/** Word-level changes between a deleted and an inserted block; `at` is where the inserted block starts. */
function wordChanges(deleted: string, inserted: string, at: number, out: TrackedChange[]): void {
  const key = `${deleted}\0${inserted}`;
  let found = compared.get(key);
  if (!found) {
    if (compared.size >= MAX_COMPARED) compared.clear();
    found = compareWords(deleted, inserted);
    compared.set(key, found);
  }
  for (const c of found) out.push({ from: c.from + at, to: c.to + at, deleted: c.deleted });
}

const same = (n: number): Op[] => Array<Op>(n).fill("same");

/** Patience diff of lines: lines that occur once in each text and keep their order anchor the alignment,
 * and the gaps between anchors are compared on their own. It stays fast and readable when a long paper has
 * changed throughout, where one table for the whole text would be too big. */
function lineOps(a: string[], b: string[]): Op[] {
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head++;
  let tail = 0;
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++;
  const midA = a.slice(head, a.length - tail);
  const midB = b.slice(head, b.length - tail);
  return [...same(head), ...middleOps(midA, midB), ...same(tail)];
}

function middleOps(a: string[], b: string[]): Op[] {
  if (!a.length || !b.length) return [...a.map((): Op => "del"), ...b.map((): Op => "ins")];
  const anchors = uniqueAnchors(a, b);
  if (!anchors.length) return diff(a, b) ?? [...a.map((): Op => "del"), ...b.map((): Op => "ins")];
  const ops: Op[] = [];
  let i = 0;
  let j = 0;
  for (const [ai, bj] of anchors) {
    ops.push(...lineOps(a.slice(i, ai), b.slice(j, bj)), "same");
    i = ai + 1;
    j = bj + 1;
  }
  ops.push(...lineOps(a.slice(i), b.slice(j)));
  return ops;
}

/** Lines found exactly once in each text, as [index in a, index in b], longest run in the same order. */
function uniqueAnchors(a: string[], b: string[]): [number, number][] {
  const seen = new Map<string, { inA: number; inB: number; i: number; j: number }>();
  a.forEach((line, i) => {
    const e = seen.get(line) ?? { inA: 0, inB: 0, i, j: -1 };
    e.inA++;
    seen.set(line, e);
  });
  b.forEach((line, j) => {
    const e = seen.get(line);
    if (e) {
      e.inB++;
      e.j = j;
    }
  });
  const pairs = [...seen.values()].filter((e) => e.inA === 1 && e.inB === 1).map((e): [number, number] => [e.i, e.j]);
  pairs.sort((x, y) => x[0] - y[0]);
  // Longest increasing run of b positions (patience sorting).
  const tops: number[] = [];
  const back: number[] = [];
  pairs.forEach(([, j], k) => {
    let lo = 0;
    let hi = tops.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (pairs[tops[mid]][1] < j) lo = mid + 1;
      else hi = mid;
    }
    back[k] = lo > 0 ? tops[lo - 1] : -1;
    tops[lo] = k;
  });
  const run: [number, number][] = [];
  for (let k = tops.length ? tops[tops.length - 1] : -1; k >= 0; k = back[k]) run.push(pairs[k]);
  return run.reverse();
}

/** Changes with at most one space or tab between them read as one: "~~is sensitive~~issitive", not
 * "~~is~~issitive ~~sensitive~~". */
function joined(changes: TrackedChange[], current: string): TrackedChange[] {
  const out: TrackedChange[] = [];
  for (const c of changes) {
    const prev = out[out.length - 1];
    const gap = prev ? current.slice(prev.to, c.from) : "";
    if (prev && /^[ \t]?$/.test(gap)) {
      prev.deleted += gap + c.deleted;
      prev.to = c.to;
    } else {
      out.push({ ...c });
    }
  }
  return out;
}

export function trackedChanges(baseline: string, current: string): TrackedChange[] {
  if (baseline === current) return [];
  const midA = lines(baseline);
  const midB = lines(current);
  let pos = 0;
  const out: TrackedChange[] = [];
  const ops = lineOps(midA, midB);
  let i = 0;
  let j = 0;
  let removed = "";
  let added = "";
  const flush = () => {
    if (removed && added) wordChanges(removed, added, pos, out);
    else if (added) out.push({ from: pos, to: pos + added.length, deleted: "" });
    else if (removed) out.push({ from: pos, to: pos, deleted: removed });
    pos += added.length;
    removed = "";
    added = "";
  };
  for (const op of ops) {
    if (op === "same") {
      flush();
      pos += midB[j].length;
      i++;
      j++;
    } else if (op === "del") {
      removed += midA[i++];
    } else {
      added += midB[j++];
    }
  }
  flush();
  return joined(out, current);
}

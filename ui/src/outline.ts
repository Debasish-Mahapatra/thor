// The document outline: sections from the source, plus the abstract and the start of the Supplementary
// Information, which papers often open with a plain heading rather than a \section.

export interface OutlineItem {
  title: string;
  line: number;
  level: 1 | 2 | 3;
}

const SECTION = /\\(part|chapter|section|subsection|subsubsection)\*?\s*(?:\[[^\]]*\])?\s*\{/;
const DEPTH: Record<string, number> = { part: 0, chapter: 1, section: 2, subsection: 3, subsubsection: 4 };

/** The line without its comment; an escaped \% is text. */
export function stripComment(line: string): string {
  for (let i = 0; i < line.length; i++) {
    if (line[i] === "\\") i++;
    else if (line[i] === "%") return line.slice(0, i);
  }
  return line;
}

/** The text of a brace group starting at open (the index of "{"), possibly running over later lines. */
function group(lines: string[], row: number, open: number): string {
  let depth = 0;
  let out = "";
  for (let r = row; r < lines.length && r < row + 5; r++) {
    const text = stripComment(lines[r]);
    for (let i = r === row ? open : 0; i < text.length; i++) {
      const c = text[i];
      if (c === "\\" && i + 1 < text.length) {
        out += c + text[++i];
        continue;
      }
      if (c === "{") {
        if (depth++ === 0) continue;
      } else if (c === "}" && --depth === 0) {
        return out;
      }
      out += c;
    }
    out += " ";
  }
  return out;
}

/** Readable heading text: drops \label and other commands, keeps their arguments. */
export function cleanTitle(raw: string): string {
  return raw
    .replace(/\\label\s*\{[^}]*\}/g, "")
    .replace(/\\(?:emph|textbf|textit|mathrm|text)\s*\{([^}]*)\}/g, "$1")
    .replace(/\\[a-zA-Z@]+\*?/g, "")
    .replace(/[{}$]/g, "")
    .replace(/~/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function outline(source: string, siMarker = ""): OutlineItem[] {
  const lines = source.split("\n");
  const found: { title: string; line: number; depth: number }[] = [];
  let afterBibliography = false;
  let siSeen = false;
  for (let i = 0; i < lines.length; i++) {
    const text = stripComment(lines[i]);
    const m = SECTION.exec(text);
    if (m) {
      const title = cleanTitle(group(lines, i, m.index + m[0].length - 1));
      if (title) found.push({ title, line: i + 1, depth: DEPTH[m[1]] });
      if (siMarker && title.startsWith(siMarker)) siSeen = true;
      continue;
    }
    if (/\\begin\s*\{abstract\}/.test(text)) found.push({ title: "Abstract", line: i + 1, depth: 2 });
    if (/\\(?:bibliography|printbibliography)\b/.test(text)) afterBibliography = true;
    if (siMarker && afterBibliography && !siSeen && text.includes(siMarker)) {
      siSeen = true;
      found.push({ title: siMarker, line: i + 1, depth: 2 });
    }
  }
  if (found.length === 0) return [];
  const top = Math.min(...found.map((f) => f.depth));
  return found.map((f) => ({ title: f.title, line: f.line, level: Math.min(3, f.depth - top + 1) as 1 | 2 | 3 }));
}

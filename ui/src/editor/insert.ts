// LaTeX the table and figure dialogs write, and what a paste from Word or a web page turns into.

const SPECIAL = /(?<!\\)([&%#])/g;

export function cellText(text: string): string {
  return text.trim().replace(SPECIAL, "\\$1");
}

export function usesBooktabs(preamble: string): boolean {
  return /^[^%\n]*\\usepackage(?:\[[^\]]*\])?\{[^}]*\bbooktabs\b[^}]*\}/m.test(preamble);
}

export interface TableChoice {
  cells: string[][];
  caption: string;
  label: string;
  booktabs: boolean;
}

export function tableLatex({ cells, caption, label, booktabs }: TableChoice): string {
  const columns = Math.max(1, ...cells.map((row) => row.length));
  const [top, middle, bottom] = booktabs ? ["\\toprule", "\\midrule", "\\bottomrule"] : ["\\hline", "\\hline", "\\hline"];
  const rows = cells.map((row, i) => {
    const line = `    ${Array.from({ length: columns }, (_, c) => cellText(row[c] ?? "")).join(" & ")} \\\\`;
    return i === 0 && cells.length > 1 ? `${line}\n    ${middle}` : line;
  });
  return [
    "\\begin{table}[htbp]",
    "  \\centering",
    ...(caption.trim() ? [`  \\caption{${caption.trim()}}`] : []),
    ...(label.trim() ? [`  \\label{${label.trim()}}`] : []),
    `  \\begin{tabular}{${"l".repeat(columns)}}`,
    `    ${top}`,
    ...rows,
    `    ${bottom}`,
    "  \\end{tabular}",
    "\\end{table}",
    "",
  ].join("\n");
}

export interface FigureChoice {
  image: string;
  width: number;
  caption: string;
  label: string;
  macro: string;
}

export function figureLatex({ image, width, caption, label, macro }: FigureChoice): string {
  const size = `width=${width === 1 ? "" : width}\\linewidth`;
  const include = macro ? `\\${macro}{${size}}{${image.replace(/\.[a-zA-Z]+$/, "").replace(/^.*\//, "")}}` : `\\includegraphics[${size}]{${image}}`;
  return [
    "\\begin{figure}[htbp]",
    "  \\centering",
    `  ${include}`,
    ...(caption.trim() ? [`  \\caption{${caption.trim()}}`] : []),
    ...(label.trim() ? [`  \\label{${label.trim()}}`] : []),
    "\\end{figure}",
    "",
  ].join("\n");
}

const TAG = /<!--[\s\S]*?-->|<(\/?)([a-zA-Z][\w:-]*)((?:[^>"']|"[^"]*"|'[^']*')*)>/g;
const ENTITIES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ", ndash: "–", mdash: "—", hellip: "…", lsquo: "‘", rsquo: "’", ldquo: "“", rdquo: "”" };
const DROPPED = new Set(["head", "style", "script", "title", "xml", "meta", "link"]);
const LATEX_TEXT: Record<string, string> = { "\\": "\\textbackslash{}", "&": "\\&", "%": "\\%", $: "\\$", "#": "\\#", _: "\\_", "{": "\\{", "}": "\\}", "~": "\\textasciitilde{}", "^": "\\textasciicircum{}" };

function entity(text: string): string {
  return text.replace(/&(#x[0-9a-f]+|#\d+|\w+);/gi, (whole, code: string) => {
    if (code[0] === "#") {
      const value = code[1].toLowerCase() === "x" ? parseInt(code.slice(2), 16) : parseInt(code.slice(1), 10);
      return String.fromCodePoint(value);
    }
    return ENTITIES[code.toLowerCase()] ?? whole;
  });
}

function latexText(text: string): string {
  return entity(text).replace(/\s+/g, " ").replace(/[\\&%$#_{}~^]/g, (c) => LATEX_TEXT[c]);
}

function styled(attributes: string, property: RegExp): boolean {
  const style = /style\s*=\s*(?:"([^"]*)"|'([^']*)')/i.exec(attributes);
  return !!style && property.test(style[1] ?? style[2] ?? "");
}

const INLINE_WRAP: Record<string, [string, string]> = {
  b: ["\\textbf{", "}"],
  strong: ["\\textbf{", "}"],
  i: ["\\emph{", "}"],
  em: ["\\emph{", "}"],
  u: ["\\underline{", "}"],
  sup: ["\\textsuperscript{", "}"],
  sub: ["\\textsubscript{", "}"],
};

/** A paste's HTML as LaTeX: paragraphs, bold, italic, super- and subscripts, lists and tables. Word's own markup, its
 *  conditional comments and style sheets, is dropped. Null when there is nothing but plain text in it. */
export function htmlToLatex(html: string): string | null {
  const fragment = /<!--StartFragment-->([\s\S]*?)<!--EndFragment-->/.exec(html)?.[1] ?? html;
  let out = "";
  let at = 0;
  let dropping: string | null = null;
  let rich = false;
  const lists: string[] = [];
  const closers: { tag: string; text: string }[] = [];
  let table: { rows: string[][]; cell: string | null } | null = null;
  const write = (text: string) => {
    if (table && table.cell !== null) table.cell += text;
    else if (!table) out += text;
  };
  const paragraph = () => {
    if (!out.endsWith("\n\n") && out) out = out.trimEnd() + "\n\n";
  };
  for (const m of fragment.matchAll(TAG)) {
    const before = fragment.slice(at, m.index);
    at = m.index + m[0].length;
    if (!dropping && before) write(latexText(before));
    const name = (m[2] ?? "").toLowerCase();
    if (!name) continue;
    if (dropping) {
      if (m[1] && name === dropping) dropping = null;
      continue;
    }
    if (DROPPED.has(name)) {
      if (!m[1] && !m[3].trimEnd().endsWith("/")) dropping = name;
      continue;
    }
    const closing = !!m[1];
    if (name === "p" || name === "div" || /^h[1-6]$/.test(name)) {
      if (table) write(" ");
      else if (lists.length === 0) paragraph();
      if (/^h[1-6]$/.test(name)) {
        rich = true;
        const heading = name === "h1" || name === "h2" ? "section" : name === "h3" ? "subsection" : "subsubsection";
        if (closing) {
          out = out.trimEnd() + "}\n\n";
        } else {
          out += `\\${heading}{`;
        }
      }
    } else if (name === "br") {
      write(table ? " " : "\\\\\n");
    } else if (name === "ul" || name === "ol") {
      rich = true;
      const env = name === "ul" ? "itemize" : "enumerate";
      if (closing) {
        out = `${out.trimEnd()}\n\\end{${lists.pop() ?? env}}\n\n`;
      } else {
        paragraph();
        lists.push(env);
        out += `\\begin{${env}}\n`;
      }
    } else if (name === "li") {
      if (!closing) out = `${out.trimEnd()}\n  \\item `;
    } else if (name === "table") {
      rich = true;
      if (closing) {
        if (table) out += tableLatex({ cells: table.rows, caption: "", label: "", booktabs: false }).replace(/^\\begin\{table\}\[htbp\]\n\s*\\centering\n|\\end\{table\}\n$/g, "");
        table = null;
      } else {
        paragraph();
        table = { rows: [], cell: null };
      }
    } else if (table && name === "tr" && !closing) {
      table.rows.push([]);
    } else if (table && (name === "td" || name === "th")) {
      if (closing && table.cell !== null) {
        const raw = table.cell.trim();
        table.rows[table.rows.length - 1]?.push(raw);
        table.cell = null;
      } else if (!closing) {
        table.cell = "";
      }
    } else if (INLINE_WRAP[name]) {
      rich = true;
      write(closing ? INLINE_WRAP[name][1] : INLINE_WRAP[name][0]);
    } else if (name === "span") {
      if (closing) {
        const open = closers.pop();
        if (open) write(open.text);
      } else {
        const attrs = m[3];
        let closeWith = "";
        if (styled(attrs, /font-weight\s*:\s*(bold|[6-9]00)/i)) {
          write("\\textbf{");
          closeWith += "}";
          rich = true;
        }
        if (styled(attrs, /font-style\s*:\s*italic/i)) {
          write("\\emph{");
          closeWith += "}";
          rich = true;
        }
        if (styled(attrs, /vertical-align\s*:\s*super/i)) {
          write("\\textsuperscript{");
          closeWith += "}";
          rich = true;
        }
        if (styled(attrs, /vertical-align\s*:\s*sub\b/i)) {
          write("\\textsubscript{");
          closeWith += "}";
          rich = true;
        }
        closers.push({ tag: name, text: closeWith });
      }
    }
  }
  if (!dropping && at < fragment.length) write(latexText(fragment.slice(at)));
  const result = out
    .replace(/\\(textbf|emph|underline|textsuperscript|textsubscript)\{\s*\}/g, "")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return rich || /<p\b/i.test(fragment) ? result : null;
}

// Paths in the interface are relative to the project folder; the build and SyncTeX speak relative to the
// LaTeX folder. These convert between the two.

export function joinPath(dir: string, file: string): string {
  return !dir || dir === "." ? file : `${dir}/${file}`;
}

/** path relative to dir, or null when it lies outside dir. */
export function relativeTo(dir: string, path: string): string | null {
  if (!dir || dir === ".") return path;
  return path.startsWith(`${dir}/`) ? path.slice(dir.length + 1) : null;
}

export function basename(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

/** The settings that name a main file, from its path in the project folder. */
export function mainFields(path: string): { latex_dir: string; main: string } {
  const slash = path.lastIndexOf("/");
  return slash < 0 ? { latex_dir: ".", main: path } : { latex_dir: path.slice(0, slash), main: path.slice(slash + 1) };
}

/** Revision folders hold copies the workflow writes, so a file there is never the paper's main file. */
export function canBeMain(path: string): boolean {
  return path.endsWith(".tex") && !/(^|\/)revision\d+\//.test(path);
}

const TEXT = new Set([
  "tex", "bib", "sty", "cls", "bst", "txt", "md", "py", "sh", "toml", "json", "yaml", "yml", "csv", "tsv", "log",
  "cfg", "ltx", "dtx", "ins", "r", "m", "jl", "ncl", "f90",
]);
const IMAGE = new Set(["png", "jpg", "jpeg", "gif", "svg", "webp"]);

export function fileKind(path: string): "text" | "pdf" | "image" | "other" {
  const ext = path.slice(path.lastIndexOf(".") + 1).toLowerCase();
  if (TEXT.has(ext)) return "text";
  if (ext === "pdf") return "pdf";
  if (IMAGE.has(ext)) return "image";
  return "other";
}

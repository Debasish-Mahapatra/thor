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

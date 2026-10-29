import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Listing, type TreeEntry } from "../api";
import { basename } from "../paths";

interface Props {
  projectId: string;
  /** The folder shown at the top level: the LaTeX folder. */
  root: string;
  /** Other project folders shown beside its contents, such as results/ and scripts/. */
  extra: string[];
  label: string;
  selected: string | null;
  onOpen: (entry: TreeEntry) => void;
  onError: (e: unknown) => void;
}

const KIND_LABEL: Record<TreeEntry["kind"], string> = {
  folder: "",
  tex: "TeX",
  bib: "Bib",
  pdf: "PDF",
  image: "Img",
  script: "Py",
  other: "",
};

export function FileTree({ projectId, root, extra, label, selected, onOpen, onError }: Props) {
  const [listings, setListings] = useState<Record<string, Listing>>({});
  const [open, setOpen] = useState<Set<string>>(new Set());
  const requested = useRef(new Set<string>());
  const rootPath = root === "." ? "" : root;

  const load = useCallback(
    async (path: string) => {
      if (requested.current.has(path)) return;
      requested.current.add(path);
      try {
        const listing = await api.tree(projectId, path);
        setListings((prev) => ({ ...prev, [path]: listing }));
      } catch (e) {
        requested.current.delete(path);
        onError(e);
      }
    },
    [projectId, onError],
  );

  useEffect(() => {
    requested.current = new Set();
    setListings({});
    setOpen(new Set());
    void load(rootPath);
  }, [projectId, rootPath, load]);

  const toggle = (path: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else {
        next.add(path);
        void load(path);
      }
      return next;
    });

  const row = (entry: TreeEntry, depth: number) => (
    <div key={entry.path}>
      <button
        className={`tree-row ${entry.dir ? "dir" : entry.kind} ${entry.path === selected ? "selected" : ""}`}
        style={{ paddingLeft: 14 + depth * 14 }}
        onClick={() => (entry.dir ? toggle(entry.path) : onOpen(entry))}
        title={entry.path}
        aria-expanded={entry.dir ? open.has(entry.path) : undefined}
      >
        <span className="twisty">{entry.dir ? (open.has(entry.path) ? "▾" : "▸") : ""}</span>
        <span className="tree-name">{entry.dir ? `${entry.name}/` : entry.name}</span>
        {!entry.dir && <span className="tree-meta">{KIND_LABEL[entry.kind]}</span>}
      </button>
      {entry.dir && open.has(entry.path) && folder(entry.path, depth + 1)}
    </div>
  );

  const folder = (path: string, depth: number) => {
    const listing = listings[path];
    if (!listing) return null;
    return (
      <>
        {listing.entries.map((e) => row(e, depth))}
        {listing.truncated && (
          <div className="tree-row muted" style={{ paddingLeft: 14 + depth * 14 }}>
            More files not shown
          </div>
        )}
      </>
    );
  };

  const extraEntries: TreeEntry[] = extra
    .filter((p) => p && p !== "." && p !== rootPath && !p.startsWith(`${rootPath}/`))
    .map((p) => ({ name: basename(p), path: p, dir: true, size: null, kind: "folder" }));

  return (
    <nav className="tree" aria-label="Project files">
      <div className="tree-row dir" style={{ paddingLeft: 14 }}>
        <span className="twisty">▾</span>
        <span className="tree-name">{label}</span>
      </div>
      {folder(rootPath, 1)}
      {extraEntries.map((e) => row(e, 1))}
    </nav>
  );
}

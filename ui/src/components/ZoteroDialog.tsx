import { useState, type FormEvent } from "react";
import { api, type ZoteroItem } from "../api";
import { Modal } from "./Modal";

interface Props {
  projectId: string;
  prepare: () => Promise<boolean>;
  /** The entry is in the bibliography; put the citation in the text. */
  onCite: (key: string, file: string | null) => void;
  onClose: () => void;
}

/** Search the user's own Zotero library, add a work to the paper's bibliography and cite it at the cursor. */
export function ZoteroDialog({ projectId, prepare, onCite, onClose }: Props) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<ZoteroItem[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");

  const guarded = async (action: () => Promise<void>) => {
    setBusy(true);
    setProblem("");
    try {
      await action();
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const search = (e: FormEvent) => {
    e.preventDefault();
    if (query.trim()) void guarded(async () => setItems((await api.zoteroSearch(projectId, query)).items));
  };

  const cite = (item: ZoteroItem) =>
    guarded(async () => {
      if (!(await prepare())) return;
      const added = await api.zoteroAdd(projectId, item.key);
      onCite(added.cite_key, added.file);
    });

  return (
    <Modal title="Cite from Zotero" onClose={onClose} wide>
      <form className="row" onSubmit={search}>
        <input autoFocus value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Title, author or year" aria-label="Search Zotero" />
        <button className="primary" disabled={busy}>
          Search
        </button>
      </form>
      {problem && <div className="flag small git-problem">{problem}</div>}
      {items?.length === 0 && <p className="muted">Nothing in your library matches.</p>}
      <ul className="zotero-items">
        {items?.map((item) => (
          <li key={item.key} className="row-between">
            <span className="small">
              <strong>{item.title}</strong>
              <div className="muted">
                {item.creators} {item.year && `(${item.year})`} {item.doi && `doi:${item.doi}`}
              </div>
            </span>
            <button disabled={busy} onClick={() => void cite(item)}>
              Cite
            </button>
          </li>
        ))}
      </ul>
    </Modal>
  );
}

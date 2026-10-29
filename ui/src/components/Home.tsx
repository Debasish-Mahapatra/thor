import { useCallback, useEffect, useState } from "react";
import { api, type RecentProject } from "../api";
import { APP_NAME } from "../brand";
import { ago } from "../format";
import { inShell, pickFolder } from "../native";

interface Props {
  onOpen: (path: string) => void;
  onError: (e: unknown) => void;
}

export function Home({ onOpen, onError }: Props) {
  const [recent, setRecent] = useState<RecentProject[]>([]);
  const [path, setPath] = useState("");

  const load = useCallback(() => api.recent().then((r) => setRecent(r.projects), onError), [onError]);
  useEffect(() => void load(), [load]);

  const choose = async () => {
    const folder = await pickFolder();
    if (folder) onOpen(folder);
  };

  const forget = async (id: string) => {
    try {
      await api.forget(id);
      await load();
    } catch (e) {
      onError(e);
    }
  };

  return (
    <div className="home">
      <section className="card">
        <h2>Projects</h2>
        <p className="muted">
          Open the folder of a paper. {APP_NAME} finds the main file, figures, bibliography and revision folders, and
          changes nothing until you save its settings.
        </p>
        {inShell() ? (
          <button className="primary" onClick={choose}>
            Open Folder...
          </button>
        ) : (
          <form
            className="path-form"
            onSubmit={(e) => {
              e.preventDefault();
              if (path.trim()) onOpen(path.trim());
            }}
          >
            <input
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="/Users/you/papers/thunder-over-asgard"
              aria-label="Folder path"
              spellCheck={false}
            />
            <button className="primary" type="submit" disabled={!path.trim()}>
              Open
            </button>
          </form>
        )}
        {recent.length > 0 && (
          <ul className="recent">
            {recent.map((p) => (
              <li key={p.id}>
                <button className="recent-open" disabled={!p.exists} onClick={() => onOpen(p.root)}>
                  <span className="recent-name">{p.name}</span>
                  <span className="muted small">{p.exists ? p.root : `${p.root} (folder not found)`}</span>
                </button>
                <span className="muted small">{ago(p.last_opened)}</span>
                <button className="link small" onClick={() => forget(p.id)} title="Remove from this list">
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

import { useEffect, useState } from "react";
import { api, type DiffLine, type Version } from "../api";
import { basename } from "../paths";
import { Modal } from "./Modal";

interface Props {
  projectId: string;
  path: string;
  onRestore: (text: string) => void;
  onClose: () => void;
  onError: (e: unknown) => void;
}

function when(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

/** Earlier versions of a file, each shown as the change restoring it would make. */
export function HistoryDialog({ projectId, path, onRestore, onClose, onError }: Props) {
  const [versions, setVersions] = useState<Version[] | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [version, setVersion] = useState<{ text: string; diff: DiffLine[] } | null>(null);

  useEffect(() => {
    api.history(projectId, path).then((r) => {
      setVersions(r.versions);
      setSelected(r.versions[0]?.id ?? null);
    }, onError);
  }, [projectId, path, onError]);

  useEffect(() => {
    if (selected === null) return;
    setVersion(null);
    api.version(projectId, selected).then(setVersion, onError);
  }, [projectId, selected, onError]);

  return (
    <Modal title={`History of ${basename(path)}`} onClose={onClose} wide>
      {versions?.length === 0 && <p className="muted">No earlier versions yet.</p>}
      {versions && versions.length > 0 && (
        <div className="history">
          <ul className="history-list">
            {versions.map((v) => (
              <li key={v.id}>
                <button className={v.id === selected ? "selected" : ""} onClick={() => setSelected(v.id)}>
                  <span>{when(v.time)}</span>
                  {v.label && <span className="chip">{v.label}</span>}
                </button>
              </li>
            ))}
          </ul>
          <div className="history-diff">
            {!version && <p className="muted">Reading...</p>}
            {version && version.diff.length === 0 && <p className="muted">This version is the same as the file now.</p>}
            {version && version.diff.length > 0 && (
              <>
                <p className="muted small">What restoring this version changes in the file as it is now:</p>
                <pre className="diff">
                  {version.diff.map((line, i) => (
                    <div key={i} className={`diff-${line.kind}`}>
                      {line.kind === "hunk" ? line.text : `${line.kind === "added" ? "+" : line.kind === "removed" ? "-" : " "} ${line.text}`}
                    </div>
                  ))}
                </pre>
              </>
            )}
          </div>
        </div>
      )}
      <div className="modal-foot">
        <span className="muted small">Restoring puts this text in the editor; the current text stays in the history.</span>
        <button
          className="primary"
          disabled={!version || version.diff.length === 0}
          onClick={() => version && onRestore(version.text)}
        >
          Restore this version
        </button>
      </div>
    </Modal>
  );
}

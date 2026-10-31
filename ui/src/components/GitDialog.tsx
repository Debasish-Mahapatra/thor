import { useCallback, useEffect, useState } from "react";
import { api, type GitStatus } from "../api";
import { Modal } from "./Modal";

interface Props {
  projectId: string;
  prepare: () => Promise<boolean>;
  /** A pull changed files, so what is open may be out of date. */
  onPulled: () => void;
  onClose: () => void;
  onError: (e: unknown) => void;
}

/** Commit, pull and push for the paper's own repository. Only this button commits, and as the user alone. */
export function GitDialog({ projectId, prepare, onPulled, onClose, onError }: Props) {
  const [status, setStatus] = useState<GitStatus | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState("");
  const [problem, setProblem] = useState("");

  const show = useCallback((next: GitStatus) => {
    setStatus(next);
    setChosen(new Set((next.files ?? []).map((f) => f.path)));
  }, []);

  useEffect(() => {
    void prepare().then(() => api.git(projectId)).then(show, onError);
  }, [projectId, prepare, show, onError]);

  const act = async (word: string, action: () => Promise<GitStatus>, after?: () => void) => {
    setBusy(word);
    setProblem("");
    try {
      await prepare();
      show(await action());
      after?.();
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  };

  const files = status?.files ?? [];
  const toggle = (path: string) =>
    setChosen((old) => {
      const next = new Set(old);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  return (
    <Modal title="Git" onClose={onClose} wide>
      {!status && <div className="muted">Reading the repository...</div>}
      {status && !status.repo && <div>{status.detail}</div>}
      {status?.repo && (
        <>
          <div className="row-between small">
            <span>
              Branch <strong>{status.branch}</strong>
              {status.upstream ? `, with ${status.upstream}` : ", with no remote branch"}
              {status.ahead ? `, ${status.ahead} to push` : ""}
              {status.behind ? `, ${status.behind} to pull` : ""}
            </span>
            <span className="row">
              <button disabled={!!busy || !status.upstream} onClick={() => void act("Pulling", () => api.gitPull(projectId), onPulled)}>
                Pull
              </button>
              <button disabled={!!busy || !status.upstream || !status.ahead} onClick={() => void act("Pushing", () => api.gitPush(projectId))}>
                Push
              </button>
            </span>
          </div>
          {status.last_commit && <div className="muted small">Last commit: {status.last_commit}</div>}
          {files.length === 0 && <p className="muted">No changes to commit.</p>}
          {files.length > 0 && (
            <>
              <ul className="git-files small">
                {files.map((f) => (
                  <li key={f.path}>
                    <label className="check">
                      <input type="checkbox" checked={chosen.has(f.path)} onChange={() => toggle(f.path)} />
                      <span className="mono">{f.path}</span>
                      <span className="muted">{f.state}</span>
                    </label>
                  </li>
                ))}
              </ul>
              <textarea rows={3} placeholder="Commit message" aria-label="Commit message" value={message} onChange={(e) => setMessage(e.target.value)} />
              <div className="row-between">
                <span className="muted small">
                  {status.author ? `As ${status.author} <${status.email}>.` : "Git does not know your name yet. Set user.name and user.email in Terminal."}
                </span>
                <button
                  className="primary"
                  disabled={!!busy || !message.trim() || chosen.size === 0}
                  onClick={() =>
                    void act("Committing", () => api.gitCommit(projectId, message, [...chosen]), () => setMessage(""))
                  }
                >
                  Commit {chosen.size} file{chosen.size === 1 ? "" : "s"}
                </button>
              </div>
            </>
          )}
          {busy && <div className="muted small">{busy}...</div>}
          {problem && <div className="flag small git-problem">{problem}</div>}
        </>
      )}
    </Modal>
  );
}

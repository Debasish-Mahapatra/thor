import { useCallback, useEffect, useMemo, useState } from "react";
import { api, fetchBytes, type FileChange, type Hunk, type WaitingChanges } from "../api";
import { trackedChanges } from "../trackchanges";
import { PdfViewer } from "./PdfViewer";

/** The new text with its insertions marked and the deleted words struck through where they were. */
function HunkText({ hunk }: { hunk: Hunk }) {
  const oldText = hunk.old.join("");
  const newText = hunk.new.join("");
  const pieces = useMemo(() => {
    const out: { kind: "same" | "ins" | "del"; text: string }[] = [];
    let at = 0;
    for (const change of trackedChanges(oldText, newText)) {
      if (change.from > at) out.push({ kind: "same", text: newText.slice(at, change.from) });
      if (change.deleted) out.push({ kind: "del", text: change.deleted });
      if (change.to > change.from) out.push({ kind: "ins", text: newText.slice(change.from, change.to) });
      at = change.to;
    }
    if (at < newText.length) out.push({ kind: "same", text: newText.slice(at) });
    return out;
  }, [oldText, newText]);
  return (
    <pre className="hunk-text">
      {pieces.map((piece, i) =>
        piece.kind === "same" ? (
          piece.text
        ) : piece.kind === "ins" ? (
          <ins key={i}>{piece.text}</ins>
        ) : (
          <del key={i}>{piece.text}</del>
        ),
      )}
    </pre>
  );
}

const KIND_WORDS: Record<FileChange["kind"], string> = {
  edit: "",
  new: "New file",
  deleted: "The agent deleted this file",
  binary: "Replaced (not text)",
};

interface Props {
  projectId: string;
  chatId: string | null;
  /** Saves what the editor holds, so an accepted edit is applied to the latest text. */
  prepare: () => Promise<boolean>;
  onWritten: (paths: string[]) => void;
  onCount: (count: number) => void;
  onError: (e: unknown) => void;
}

export function countOf(changes: WaitingChanges | null): number {
  return changes?.files.reduce((n, f) => n + Math.max(1, f.hunks.length), 0) ?? 0;
}

export function ChangesView({ projectId, chatId, prepare, onWritten, onCount, onError }: Props) {
  const [changes, setChanges] = useState<WaitingChanges | null>(null);
  const [busy, setBusy] = useState(false);
  const [pdf, setPdf] = useState<{ data: ArrayBuffer; version: string } | null>(null);
  const [showPdf, setShowPdf] = useState(false);

  const show = useCallback(
    (next: WaitingChanges) => {
      setChanges(next);
      onCount(countOf(next));
    },
    [onCount],
  );

  useEffect(() => {
    if (chatId) api.changes(projectId, chatId).then(show, onError);
    else show({ files: [], running: false, build: null });
  }, [projectId, chatId, show, onError]);

  const buildId = changes?.build?.pdf ? changes.build.id : null;
  useEffect(() => {
    if (!showPdf || !buildId || !chatId) return;
    fetchBytes(api.chatPdfUrl(projectId, chatId)).then((data) => setPdf({ data, version: buildId }), onError);
  }, [showPdf, buildId, projectId, chatId, onError]);

  const act = async (kind: "accept" | "reject", path?: string, hunk?: string) => {
    if (!chatId || busy) return;
    setBusy(true);
    try {
      if (!(await prepare())) return;
      const next = kind === "accept" ? await api.acceptChanges(projectId, chatId, path, hunk) : await api.rejectChanges(projectId, chatId, path, hunk);
      show(next);
      if (next.written?.length) onWritten(next.written);
    } catch (e) {
      onError(e);
      api.changes(projectId, chatId).then(show, () => undefined);
    } finally {
      setBusy(false);
    }
  };

  if (!chatId || !changes || changes.files.length === 0) {
    return (
      <div className="changes-view">
        <div className="empty">{changes?.running ? "The agent is working. Its edits appear here when it finishes." : "No edits are waiting. When the agent changes something, you review it here."}</div>
      </div>
    );
  }

  const total = countOf(changes);
  const flagged = changes.files.reduce((n, f) => n + f.hunks.filter((h) => h.flags.length > 0).length + (f.kind === "deleted" ? 1 : 0), 0);
  return (
    <div className="changes-view with-head">
      <div className="changes-head">
        <strong>
          {total} edit{total === 1 ? "" : "s"} in {changes.files.length} file{changes.files.length === 1 ? "" : "s"}
        </strong>
        {changes.build && <span className={changes.build.ok ? "good small" : "bad small"}>The agent's copy: {changes.build.summary}</span>}
        {flagged > 0 && (
          <span className="warn-text small" title="Thor flags edits that change a number, a citation, or a word that can alter a finding. It cannot see meaning, so an unflagged edit may still change a claim.">
            {flagged} to read closely
          </span>
        )}
        <span className="spacer" />
        {changes.build?.pdf && (
          <button onClick={() => setShowPdf((s) => !s)}>{showPdf ? "Hide its PDF" : "Show its PDF"}</button>
        )}
        <button onClick={() => void act("reject")} disabled={busy || changes.running}>
          Reject all
        </button>
        <button className="primary" onClick={() => void act("accept")} disabled={busy || changes.running}>
          {flagged > 0 ? "Accept all unflagged" : "Accept all"}
        </button>
      </div>
      {changes.running && <div className="agent-notice">The agent is still working, so edits cannot be accepted yet.</div>}
      <div className={`changes-body ${showPdf ? "split" : ""}`}>
        <div className="changes-list">
          {changes.files.map((file) => (
            <section className="change-file" key={file.path}>
              <div className="change-file-head">
                <span className="mono">{file.path}</span>
                <span className="muted small">
                  {file.additions > 0 && `+${file.additions} `}
                  {file.deletions > 0 && `-${file.deletions}`}
                </span>
                <span className="spacer" />
                <button className="link small" onClick={() => void act("reject", file.path)} disabled={busy || changes.running}>
                  Reject file
                </button>
                {!file.refused && (
                  <button className="link small" onClick={() => void act("accept", file.path)} disabled={busy || changes.running}>
                    {file.hunks.some((h) => h.flags.length > 0) ? "Accept unflagged" : "Accept file"}
                  </button>
                )}
              </div>
              {file.refused && <div className="agent-error">{file.refused}</div>}
              {KIND_WORDS[file.kind] && <div className="muted small change-note">{KIND_WORDS[file.kind]}</div>}
              {file.kind === "deleted" && !file.refused && (
                <div className="change-note">
                  <button className="link small" onClick={() => void act("accept", file.path, "delete")} disabled={busy || changes.running}>
                    Delete it
                  </button>
                </div>
              )}
              {file.hunks.map((hunk) => (
                <div className="hunk" key={hunk.id}>
                  {hunk.flags.length > 0 && (
                    <div className="hunk-flags">
                      {hunk.flags.map((flag) => (
                        <div key={flag}>{flag}</div>
                      ))}
                    </div>
                  )}
                  <div className="hunk-head">
                    <span className="muted small">Line {hunk.old_start}</span>
                    <span className="spacer" />
                    <button className="link small" onClick={() => void act("reject", file.path, hunk.id)} disabled={busy || changes.running}>
                      Reject
                    </button>
                    {!file.refused && (
                      <button className="link small" onClick={() => void act("accept", file.path, hunk.id)} disabled={busy || changes.running}>
                        Accept
                      </button>
                    )}
                  </div>
                  {hunk.before.length > 0 && <pre className="hunk-context">{hunk.before.slice(-1).join("")}</pre>}
                  <HunkText hunk={hunk} />
                  {hunk.after.length > 0 && <pre className="hunk-context">{hunk.after.slice(0, 1).join("")}</pre>}
                </div>
              ))}
            </section>
          ))}
        </div>
        {showPdf && (
          <PdfViewer
            title="The agent's copy"
            data={pdf?.data ?? null}
            version={pdf?.version ?? ""}
            highlight={null}
            placeholder="Loading the agent's PDF..."
          />
        )}
      </div>
    </div>
  );
}

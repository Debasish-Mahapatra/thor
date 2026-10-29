import { useCallback, useEffect, useMemo, useState } from "react";
import { api, fetchBytes, type CommentItem, type CommentSet, type CommentSetSummary, type SyncBox } from "../api";
import { KIND_LABEL, NO_FILTER, matches, summary, where, type CommentFilter } from "../comments";
import { inShell, pickPdf } from "../native";
import { Modal } from "./Modal";
import { PdfViewer } from "./PdfViewer";

interface Props {
  projectId: string;
  active: number | null;
  /** The latest build, where each comment's text is looked for. */
  build: { data: ArrayBuffer; version: string } | null;
  buildTitle: string;
  onError: (e: unknown) => void;
}

const boxes = (c: CommentItem): SyncBox[] =>
  c.page ? c.rects.map(([x0, y0, x1, y1]) => ({ page: c.page!, left: x0, top: y0, width: x1 - x0, height: y1 - y0 })) : [];

/** The Comments tab: co-authors' annotated PDFs, read into a list, beside the PDF they marked and the build now. */
export function CommentsView({ projectId, active, build, buildTitle, onError }: Props) {
  const [sets, setSets] = useState<CommentSetSummary[] | null>(null);
  const [name, setName] = useState<string | null>(null);
  const [data, setData] = useState<CommentSet | null>(null);
  const [annotated, setAnnotated] = useState<ArrayBuffer | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [now, setNow] = useState<{ id: number; boxes: SyncBox[] } | null>(null);
  const [filter, setFilter] = useState<CommentFilter>(NO_FILTER);
  const [importing, setImporting] = useState(false);
  const [merging, setMerging] = useState(false);
  // Both PDFs side by side need a wide window; otherwise one at a time.
  const [pdfs, setPdfs] = useState<"both" | "annotated" | "now">(() => (window.innerWidth >= 1400 ? "both" : "annotated"));

  const loadSets = useCallback(
    async (choose?: string) => {
      try {
        const listed = await api.commentSets(projectId);
        setSets(listed.sets);
        setName((current) => choose ?? (listed.sets.some((s) => s.name === current) ? current : (listed.sets[0]?.name ?? null)));
      } catch (e) {
        onError(e);
      }
    },
    [projectId, onError],
  );

  useEffect(() => {
    setSets(null);
    setName(null);
    if (active) void loadSets();
  }, [active, loadSets]);

  useEffect(() => {
    setData(null);
    setAnnotated(null);
    setSelected(null);
    setFilter(NO_FILTER);
    if (!name) return;
    api.commentSet(projectId, name).then(setData, onError);
    fetchBytes(api.annotatedPdfUrl(projectId, name)).then(setAnnotated, onError);
  }, [projectId, name, onError]);

  // Find the selected comment's text in the latest build.
  useEffect(() => {
    if (!name || selected === null || !build) return;
    let cancelled = false;
    api.commentNow(projectId, name, selected).then(
      (found) => !cancelled && setNow({ id: selected, boxes: found.boxes }),
      () => !cancelled && setNow({ id: selected, boxes: [] }),
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, name, selected, build?.version]);

  // The selected comment stays in the list, so answering it under "Needs an answer" does not make it vanish.
  const shown = useMemo(
    () => (data ? data.comments.filter((c) => c.id === selected || matches(c, filter)) : []),
    [data, filter, selected],
  );
  const current = data?.comments.find((c) => c.id === selected) ?? null;
  const kinds = useMemo(() => [...new Set(data?.comments.map((c) => c.kind))].sort(), [data]);
  const unanswered = data?.comments.filter((c) => c.question && !c.answer).length ?? 0;
  const moved = data?.comments.filter((c) => c.baseline?.status === "moved" || c.baseline?.status === "not found").length ?? 0;

  const changed = (next: CommentSet) => {
    setData(next);
    void loadSets(next.name);
  };

  const answer = async (id: number, text: string) => {
    if (!name) return;
    try {
      changed(await api.answerComment(projectId, name, id, text));
    } catch (e) {
      onError(e);
    }
  };

  const step = (by: 1 | -1) => {
    if (!shown.length) return;
    const index = shown.findIndex((c) => c.id === selected);
    const next = shown[Math.min(shown.length - 1, Math.max(0, index < 0 ? 0 : index + by))];
    setSelected(next.id);
    document.getElementById(`comment-${next.id}`)?.scrollIntoView({ block: "nearest" });
  };

  if (!active) {
    return (
      <div className="empty">
        Start a revision, or continue the latest one, from the revision menu in the toolbar. Co-authors' annotated
        PDFs are imported into the active revision.
      </div>
    );
  }

  const importDialog = importing && (
    <ImportDialog
      projectId={projectId}
      active={active}
      onDone={(set) => {
        setImporting(false);
        void loadSets(set.name);
      }}
      onClose={() => setImporting(false)}
    />
  );

  if (sets && sets.length === 0) {
    return (
      <div className="empty">
        <div>
          <p>No comments imported into revision {active} yet.</p>
          <button className="primary" onClick={() => setImporting(true)}>
            Import an annotated PDF...
          </button>
        </div>
        {importDialog}
      </div>
    );
  }

  const nowBoxes = current && now?.id === current.id ? now.boxes : null;
  const nowNote = !current?.page
    ? null
    : !build
      ? "Compile to see where this is in the paper now."
      : nowBoxes && nowBoxes.length === 0
        ? "Not found in the latest build: this text has changed since."
        : null;

  return (
    <div className={`comments ${pdfs === "both" ? "" : "single"}`}>
      <aside className="comment-side">
        <div className="comment-head">
          {sets && sets.length > 1 ? (
            <select value={name ?? ""} onChange={(e) => setName(e.target.value)} aria-label="Comment set">
              {sets.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.name} ({s.count})
                </option>
              ))}
            </select>
          ) : (
            <strong className="comment-title" title={data?.source_pdf}>
              {data?.source_pdf ?? ""}
            </strong>
          )}
          <span className="spacer" />
          <button onClick={() => setImporting(true)}>Import...</button>
        </div>
        {data && (
          <div className="comment-meta small">
            <span className="muted">
              {data.comments.length} comments from{" "}
              {Object.entries(data.persons)
                .map(([p, n]) => `${p} (${n})`)
                .join(", ")}
            </span>{" "}
            <button className="link small" onClick={() => setMerging(true)}>
              Merge names
            </button>
            <div className="muted">
              {data.source_pdf}, imported {data.imported}
              {data.baseline ? `. Checked against ${data.baseline}.` : ". No baseline PDF to check against."}
            </div>
          </div>
        )}
        <div className="pdf-choice small">
          <span className="muted">Show</span>
          {(["annotated", "now", "both"] as const).map((v) => (
            <button key={v} className={pdfs === v ? "on" : ""} onClick={() => setPdfs(v)}>
              {v === "annotated" ? "Annotated PDF" : v === "now" ? "Paper now" : "Both"}
            </button>
          ))}
        </div>
        {data && (
          <div className="comment-filters">
            <input
              type="search"
              placeholder="Search the comments"
              value={filter.search}
              onChange={(e) => setFilter({ ...filter, search: e.target.value })}
            />
            <div className="row">
              <select value={filter.kind} onChange={(e) => setFilter({ ...filter, kind: e.target.value })} aria-label="Kind">
                <option value="">All kinds</option>
                {kinds.map((k) => (
                  <option key={k} value={k}>
                    {KIND_LABEL[k] ?? k}
                  </option>
                ))}
              </select>
              {Object.keys(data.persons).length > 1 && (
                <select value={filter.person} onChange={(e) => setFilter({ ...filter, person: e.target.value })} aria-label="Person">
                  <option value="">Everyone</option>
                  {Object.keys(data.persons).map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              )}
            </div>
            <div className="row">
              <label className="check small">
                <input type="checkbox" checked={filter.unclear} onChange={(e) => setFilter({ ...filter, unclear: e.target.checked })} />
                Needs an answer ({unanswered})
              </label>
              <label className="check small">
                <input type="checkbox" checked={filter.moved} onChange={(e) => setFilter({ ...filter, moved: e.target.checked })} />
                Moved ({moved})
              </label>
            </div>
            {data.comments.some((c) => !matches(c, filter)) && (
              <div className="muted small">
                Showing {data.comments.filter((c) => matches(c, filter)).length} of {data.comments.length}.{" "}
                <button className="link small" onClick={() => setFilter(NO_FILTER)}>
                  Show all
                </button>
              </div>
            )}
          </div>
        )}
        <ul
          className="comment-list"
          tabIndex={0}
          aria-label="Comments"
          onKeyDown={(e) => {
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
              e.preventDefault();
              step(e.key === "ArrowDown" ? 1 : -1);
            }
          }}
        >
          {!data && <li className="muted small comment-pad">Reading the comments...</li>}
          {shown.map((c) => (
            <li key={c.id} id={`comment-${c.id}`} className={c.id === selected ? "selected" : ""}>
              <button className="comment-row" onClick={() => setSelected(c.id === selected ? null : c.id)}>
                <span className="comment-line">
                  <span className="comment-num">{c.id}</span>
                  <span className="comment-kind">{KIND_LABEL[c.kind] ?? c.kind}</span>
                  <span className="muted small">{where(c)}</span>
                  <span className="spacer" />
                  {c.question && !c.answer && <span className="chip warn">needs an answer</span>}
                  {c.baseline?.status === "moved" && <span className="chip">moved</span>}
                  {c.baseline?.status === "not found" && <span className="chip bad">not in baseline</span>}
                </span>
                <span className="comment-summary">{summary(c)}</span>
              </button>
              {c.id === selected && (
                <CommentDetail comment={c} baseline={data?.baseline ?? ""} nowNote={nowNote} onAnswer={answer} />
              )}
            </li>
          ))}
          {data && shown.length === 0 && <li className="muted small comment-pad">No comments match.</li>}
        </ul>
      </aside>
      {pdfs !== "now" && (
        <PdfViewer
          title={data ? `${data.source_pdf} (annotated)` : "Annotated PDF"}
          data={annotated}
          version={name ?? ""}
          highlight={current ? boxes(current) : null}
          placeholder="Opening the PDF..."
        />
      )}
      {pdfs !== "annotated" && (
        <PdfViewer
          title={`Now: ${buildTitle}`}
          data={build?.data ?? null}
          version={build?.version ?? ""}
          highlight={nowBoxes?.length ? nowBoxes : null}
        />
      )}
      {importDialog}
      {merging && data && (
        <MergeDialog
          data={data}
          onSave={async (aliases) => {
            try {
              changed(await api.setAliases(projectId, data.name, aliases));
              setMerging(false);
            } catch (e) {
              onError(e);
            }
          }}
          onClose={() => setMerging(false)}
        />
      )}
    </div>
  );
}

interface DetailProps {
  comment: CommentItem;
  baseline: string;
  nowNote: string | null;
  onAnswer: (id: number, text: string) => Promise<void>;
}

function CommentDetail({ comment: c, baseline, nowNote, onAnswer }: DetailProps) {
  const [draft, setDraft] = useState(c.answer ?? "");
  const [editing, setEditing] = useState(!c.answer);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDraft(c.answer ?? "");
    setEditing(!c.answer);
  }, [c.id, c.answer]);

  const save = async () => {
    setSaving(true);
    await onAnswer(c.id, draft);
    setSaving(false);
  };

  const status = c.baseline?.status;
  return (
    <div className="comment-detail small">
      <div className="muted">
        {c.person}
        {c.person !== c.author && ` (as ${c.author})`}
      </div>
      {c.marked && (
        <p>
          <span className="muted">Marked: </span>
          {c.kind === "deletion" || c.kind === "replacement" ? <del>{c.marked}</del> : <mark>{c.marked}</mark>}
        </p>
      )}
      {c.insert && (
        <p>
          <span className="muted">Insert: </span>
          <ins>{c.insert}</ins>
          {c.anchor && <span className="muted"> after "{c.anchor}"</span>}
        </p>
      )}
      {c.note && (
        <p>
          <span className="muted">Note: </span>
          {c.note}
        </p>
      )}
      {c.marks.length > 0 && (
        <ul className="comment-marks">
          {c.marks.map((m, i) => (
            <li key={i}>
              <span className="muted">{m.kind}</span> {m.text ? `"${m.text}"` : ""}
              {m.line ? <span className="muted"> (line {m.line})</span> : null}
            </li>
          ))}
        </ul>
      )}
      {c.replies.map((r, i) => (
        <p key={i}>
          <span className="muted">Reply from {r.author}: </span>
          {r.note}
        </p>
      ))}
      {status === "moved" && c.baseline && (
        <p className="muted">
          In {baseline || "the baseline"} this is on page {c.baseline.page}
          {c.baseline.line ? `, line ${c.baseline.line}` : ""}: the PDF that was annotated is not the baseline.
        </p>
      )}
      {status === "not found" && (
        <p className="warn-text">This text is not in {baseline || "the baseline"}; it was changed after the annotated PDF was made.</p>
      )}
      {nowNote && <p className="muted">{nowNote}</p>}
      {c.question && (
        <div className="comment-question">
          <p className="warn-text">{c.question}</p>
          {editing ? (
            <>
              <textarea
                rows={2}
                value={draft}
                placeholder="What the co-author meant, in your words"
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && e.metaKey && void save()}
              />
              <div className="row">
                <button className="primary" disabled={saving || !draft.trim()} onClick={() => void save()}>
                  {saving ? "Saving..." : "Save answer"}
                </button>
                {c.answer && <button onClick={() => setEditing(false)}>Cancel</button>}
              </div>
            </>
          ) : (
            <p>
              <span className="muted">Answer: </span>
              {c.answer}{" "}
              <button className="link small" onClick={() => setEditing(true)}>
                Change
              </button>
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function ImportDialog({ projectId, active, onDone, onClose }: { projectId: string; active: number; onDone: (set: CommentSet) => void; onClose: () => void }) {
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const choose = async () => {
    const picked = await pickPdf();
    if (picked) setPath(picked);
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      onDone(await api.importComments(projectId, path.trim(), email, name.trim()));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  const stem = path.trim().split("/").pop()?.replace(/\.pdf$/i, "") ?? "";
  return (
    <Modal title="Import an annotated PDF" onClose={onClose} wide>
      <div className="field">
        <label>Annotated PDF</label>
        <div className="row">
          <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/Users/you/Downloads/paper-ada.pdf" style={{ flex: 1 }} />
          {inShell() && <button onClick={() => void choose()}>Choose...</button>}
        </div>
      </div>
      <div className="field">
        <label>Name</label>
        <div>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder={stem || "ada"} />
          <span className="help">
            Saved in revision{active}/supporting_files/comments/{name.trim() || stem || "..."}/ with a copy of the PDF.
          </span>
        </div>
      </div>
      <div className="field">
        <label>Their email (optional)</label>
        <div>
          <textarea rows={6} value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Paste the email that came with the PDF if it has comments too." />
          <span className="help">Each paragraph becomes a comment; greetings and sign-offs are left out.</span>
        </div>
      </div>
      {error && <p className="warn-text">{error}</p>}
      <div className="modal-foot">
        <span className="muted small">
          {busy ? "Reading the annotations and checking them against the baseline..." : "Importing again under the same name replaces that set, keeping your answers and merged names."}
        </span>
        <button className="primary" disabled={busy || !path.trim()} onClick={() => void submit()}>
          Import
        </button>
      </div>
    </Modal>
  );
}

function MergeDialog({ data, onSave, onClose }: { data: CommentSet; onSave: (aliases: Record<string, string>) => Promise<void>; onClose: () => void }) {
  const authors = useMemo(() => [...new Set(data.comments.map((c) => c.author).filter(Boolean))].sort(), [data]);
  const [names, setNames] = useState<Record<string, string>>(() =>
    Object.fromEntries(authors.map((a) => [a, data.aliases[a] ?? a])),
  );

  return (
    <Modal title="Merge names" onClose={onClose}>
      <p className="muted small">
        A co-author who annotated on two computers shows up under two account names. Give both the same name to treat
        them as one person.
      </p>
      <datalist id="merge-names">
        {[...new Set(Object.values(names))].map((n) => (
          <option key={n} value={n} />
        ))}
      </datalist>
      {authors.map((a) => (
        <div key={a} className="field">
          <label className="mono">{a}</label>
          <div>
            <input list="merge-names" value={names[a] ?? ""} onChange={(e) => setNames({ ...names, [a]: e.target.value })} />
          </div>
        </div>
      ))}
      <div className="modal-foot">
        <button
          className="primary"
          onClick={() => void onSave(Object.fromEntries(authors.map((a) => [a, (names[a] ?? "").trim() || a])))}
        >
          Save
        </button>
      </div>
    </Modal>
  );
}

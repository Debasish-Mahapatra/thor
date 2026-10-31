import { useEffect, useState } from "react";
import { api, fetchBytes, type RevisionOverview } from "../api";
import { PdfViewer } from "./PdfViewer";

interface Props {
  projectId: string;
  overview: RevisionOverview | null;
  onUpdate: () => Promise<void>;
  onError: (e: unknown) => void;
}

/** The Tracked PDF tab: the active revision's tracked PDF beside the report of its last update. */
export function TrackedView({ projectId, overview, onUpdate, onError }: Props) {
  const [pdf, setPdf] = useState<ArrayBuffer | null>(null);
  const [missing, setMissing] = useState(false);
  const [updating, setUpdating] = useState(false);
  const active = overview?.active ?? null;
  const sync = overview?.sync;
  // The incremental tracked PDF, ready seconds after a compile; the revision's own file if there is none yet.
  const preview = sync?.preview?.ok ? sync.preview.id : null;
  const version = preview ?? `${active}-${sync?.finished ?? ""}`;

  useEffect(() => {
    if (!active) return;
    fetchBytes(preview ? api.trackedPreviewUrl(projectId) : api.revisionFileUrl(projectId, "revision_tracked.pdf")).then(
      (bytes) => {
        setPdf(bytes);
        setMissing(false);
      },
      () => setMissing(true),
    );
  }, [projectId, active, version, preview]);

  if (!active) {
    return (
      <div className="empty">
        Start a revision, or continue the latest one, from the revision menu in the toolbar. Its tracked PDF then
        appears here after each compile.
      </div>
    );
  }

  const update = async () => {
    setUpdating(true);
    try {
      await onUpdate();
    } catch (e) {
      onError(e);
    } finally {
      setUpdating(false);
    }
  };

  const tracked = sync?.revision === active ? sync.tracked : null;
  return (
    <div className="tracked">
      <aside className="tracked-report">
        <div className="row-between">
          <h2>Revision {active}</h2>
          <button onClick={update} disabled={updating || sync?.state === "running"}>
            {sync?.state === "running" ? "Updating..." : "Update now"}
          </button>
        </div>
        {overview?.baseline && <p className="muted small mono">Baseline: {overview.baseline}</p>}
        {sync?.revision === active && sync.steps.length > 0 ? (
          <ul className="steps">
            {sync.steps.map((step) => (
              <li key={step.name}>
                <span className={step.ok ? "good-text" : "warn-text"}>{step.ok ? "Done" : "Failed"}</span>
                <span>
                  <strong>{step.name}</strong>
                  {step.detail && <span className="muted small block">{step.detail}</span>}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">Not updated yet. Compile, or press Update now.</p>
        )}
        {tracked && (
          <>
            <h3>Tracked PDF</h3>
            <p className="small">{tracked.summary}</p>
            <p className="small muted">
              Figures: {Object.entries(tracked.figures)
                .map(([k, v]) => `${v} ${k}`)
                .join(", ")}
            </p>
            {tracked.tables.length > 0 && (
              <p className="small muted">Tables: {tracked.tables.join("; ")}</p>
            )}
            {tracked.counts.overfull > 0 && (
              <p className="small muted">
                {tracked.counts.overfull} overfull boxes, as is usual where markup widens lines.
              </p>
            )}
            {tracked.problems
              .filter((p) => p.kind !== "warning" && p.kind !== "overfull")
              .map((p, i) => (
                <p key={i} className="small warn-text">
                  {p.message}
                  {p.line ? ` (tracked.tex:${p.line})` : ""}
                </p>
              ))}
          </>
        )}
      </aside>
      <PdfViewer
        title={`revision${active}/revision_tracked.pdf`}
        data={missing ? null : pdf}
        version={version}
        highlight={null}
      />
    </div>
  );
}

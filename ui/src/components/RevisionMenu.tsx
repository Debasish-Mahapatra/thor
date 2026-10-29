import { useEffect, useRef, useState } from "react";
import type { RevisionOverview } from "../api";
import { IconChevron } from "../icons";

interface Props {
  overview: RevisionOverview | null;
  onStart: () => Promise<void>;
  onContinue: () => Promise<void>;
  onShowReport: () => void;
}

function clock(seconds: number): string {
  return new Date(seconds * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** The revision picker in the toolbar: which revision Thor keeps up to date, and starting the next one. */
export function RevisionMenu({ overview, onStart, onContinue, onShowReport }: Props) {
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState<"start" | "continue" | null>(null);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    const escape = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", escape);
    };
  }, [open]);

  const active = overview?.active ?? null;
  const latest = overview?.latest ?? null;
  const next = (latest ?? 0) + 1;
  const sync = overview?.sync;
  const label = active ? `Revision ${active}` : latest ? `Revision ${latest}` : "No revision";

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    try {
      await action();
      setOpen(false);
      setConfirm(null);
    } finally {
      setBusy(false);
    }
  };

  let status = null;
  if (active && sync?.revision === active) {
    if (sync.state === "running") status = <span className="muted small">updating...</span>;
    else if (sync.state === "done" && sync.finished) status = <span className="muted small">updated {clock(sync.finished)}</span>;
    else if (sync.state === "failed")
      status = (
        <button className="link small warn-text" onClick={onShowReport}>
          update failed
        </button>
      );
  }

  return (
    <div className="revision-menu" ref={box}>
      <button className="select-like" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {label}
        {!active && latest ? <span className="muted small"> · frozen</span> : null}
        <IconChevron up={false} />
      </button>
      {status}
      {open && (
        <div className="popover" role="dialog" aria-label="Revisions">
          {confirm === null && (
            <>
              <p className="small">
                {active
                  ? `After each successful compile, Thor updates revision ${active}: the source snapshot, the clean, split and tracked PDFs, and the change log.`
                  : latest
                    ? `Revision ${latest} is frozen: Thor writes nothing into it.`
                    : "No revision folder yet."}
              </p>
              {active && overview?.baseline && <p className="muted small mono">Baseline: {overview.baseline}</p>}
              <div className="popover-actions">
                <button className="primary" onClick={() => setConfirm("start")}>
                  Start revision {next}
                </button>
                {latest && active !== latest && <button onClick={() => setConfirm("continue")}>Continue revision {latest}</button>}
              </div>
            </>
          )}
          {confirm === "start" && (
            <>
              <p className="small">
                {latest
                  ? `Revision ${next} starts from revision ${latest}'s final source (revision${latest}/supporting_files), which becomes its frozen baseline. Revision ${latest} is not changed.`
                  : "Revision 1 starts from the source as it is now, which becomes its baseline."}{" "}
                From then on, each successful compile updates revision{next}/.
              </p>
              <div className="popover-actions">
                <button className="primary" disabled={busy} onClick={() => run(onStart)}>
                  {busy ? "Starting..." : `Start revision ${next}`}
                </button>
                <button className="ghost" onClick={() => setConfirm(null)}>
                  Cancel
                </button>
              </div>
            </>
          )}
          {confirm === "continue" && (
            <>
              <p className="small warn-text">
                After each successful compile, Thor will overwrite revision {latest}'s PDFs and its source snapshot. Do
                this only if revision {latest} has not been sent yet.
              </p>
              <div className="popover-actions">
                <button className="primary" disabled={busy} onClick={() => run(onContinue)}>
                  {busy ? "Continuing..." : `Continue revision ${latest}`}
                </button>
                <button className="ghost" onClick={() => setConfirm(null)}>
                  Cancel
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

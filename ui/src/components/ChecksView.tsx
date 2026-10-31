import { useCallback, useEffect, useState } from "react";
import {
  api,
  type RevisionCheck,
  type Metric,
  type NumberReport,
  type ReferenceReport,
  type ScriptJob,
  type ScriptScan,
  type SubmissionResult,
} from "../api";

const POLL_MS = 1000;
const STATUS_WORDS: Record<RevisionCheck["status"], string> = { ok: "Passed", warn: "Look at this", fail: "Failed", info: "Note" };
type Section = "checks" | "numbers" | "references" | "figures" | "submission";
const SECTIONS: { id: Section; label: string }[] = [
  { id: "checks", label: "Checks" },
  { id: "numbers", label: "Numbers" },
  { id: "references", label: "References" },
  { id: "figures", label: "Figures" },
  { id: "submission", label: "Submission" },
];

interface Props {
  projectId: string;
  prepare: () => Promise<boolean>;
  onJump: (file: string, line: number) => void;
  /** A script changed files the editor may have open. */
  onWritten: (paths: string[]) => void;
  onError: (e: unknown) => void;
}

function Place(props: { file?: string | null; line?: number | null; onJump: Props["onJump"] }) {
  const { file, line, onJump } = props;
  if (!file) return null;
  return (
    <button className="link small" onClick={() => onJump(file, line ?? 1)}>
      {file}
      {line ? `:${line}` : ""}
    </button>
  );
}

function Checks({ projectId, prepare, onJump, onError }: Props) {
  const [data, setData] = useState<{ checks: RevisionCheck[]; metrics: Metric[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const run = useCallback(async () => {
    setBusy(true);
    try {
      await prepare();
      setData(await api.verify(projectId));
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }, [projectId, prepare, onError]);
  useEffect(() => void run(), [run]);
  const shown = (value: number | null) => (value === null ? "—" : value.toLocaleString());
  return (
    <>
      <div className="row-between">
        <span className="muted small">A pass means these things hold. It does not mean the paper is right.</span>
        <button disabled={busy} onClick={() => void run()}>
          {busy ? "Checking..." : "Check again"}
        </button>
      </div>
      {data?.checks.map((check) => (
        <section key={check.id} className={`check-card ${check.status}`}>
          <div className="plan-item-head">
            <span className={`check-status ${check.status}`}>{STATUS_WORDS[check.status]}</span>
            <strong>{check.title}</strong>
          </div>
          <div className="small">{check.summary}</div>
          {check.details.length > 0 && (
            <ul className="check-details small">
              {check.details.map((d, i) => (
                <li key={i}>
                  {d.text} <Place file={d.file} line={d.line} onJump={onJump} />
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
      {data && (
        <section className="check-card">
          <strong>Before and after</strong>
          <table className="check-table small">
            <thead>
              <tr>
                <th />
                <th>At the baseline</th>
                <th>Now</th>
                <th>Journal's limit</th>
              </tr>
            </thead>
            <tbody>
              {data.metrics.map((m) => (
                <tr key={m.name} className={m.over ? "over" : ""}>
                  <th>{m.name}</th>
                  <td>{shown(m.before)}</td>
                  <td>
                    {shown(m.after)}
                    {m.over ? ", over the limit" : ""}
                  </td>
                  <td>{shown(m.limit)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="muted small">Figures, tables and words count what comes before the Supplementary Information. Set limits in the project's settings.</div>
        </section>
      )}
    </>
  );
}

function Numbers({ projectId, prepare, onJump, onError }: Props) {
  const [report, setReport] = useState<NumberReport | null>(null);
  const [onlyMissing, setOnlyMissing] = useState(true);
  useEffect(() => {
    void prepare().then(() => api.numbers(projectId)).then(setReport, onError);
  }, [projectId, prepare, onError]);
  if (!report) return <div className="muted">Searching the result files...</div>;
  const rows = report.numbers.filter((n) => !onlyMissing || n.sources.length === 0);
  return (
    <>
      <div className="row-between">
        <span className="small">
          {report.has_results
            ? `${report.found} of ${report.found + report.missing} numbers found in ${report.searched} result file${report.searched === 1 ? "" : "s"}. Finding a value does not prove it is the right one.`
            : "No result files to compare with. Set a results folder in the project's settings."}
        </span>
        <label className="check small">
          <input type="checkbox" checked={onlyMissing} onChange={(e) => setOnlyMissing(e.target.checked)} />
          Only those not found
        </label>
      </div>
      {report.skipped.length > 0 && <div className="flag small">Not searched: {report.skipped.join("; ")}</div>}
      <table className="check-table small">
        <thead>
          <tr>
            <th>Number</th>
            <th>In the text</th>
            <th>In the results</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((n, i) => (
            <tr key={i}>
              <th>{n.shown}</th>
              <td>
                <Place file={n.file} line={n.line} onJump={onJump} />
                <div className="muted">{n.context}</div>
              </td>
              <td>
                {n.sources.length === 0 && <span className="warn-text">Not found</span>}
                {n.sources.map((s, j) => (
                  <div key={j} className="mono">
                    {s.file}:{s.line}: {s.text}
                  </div>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && <div className="muted small">Nothing to show.</div>}
    </>
  );
}

function References({ projectId, prepare, onJump, onError }: Props) {
  const [report, setReport] = useState<ReferenceReport | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      await prepare();
      setReport(await api.checkReferences(projectId));
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <div className="row-between">
        <span className="muted small">
          Sends the titles and DOIs of the works the paper cites to Crossref. Nothing else leaves this Mac. Crossref says whether a work
          exists as written, not whether the paper is right to cite it.
        </span>
        <button className="primary" disabled={busy} onClick={() => void run()}>
          {busy ? "Asking Crossref..." : report ? "Check again" : "Check the references"}
        </button>
      </div>
      {report && (
        <>
          <div className="small">
            {report.counts["confirmed"]} confirmed, {report.counts["mismatch"]} differ, {report.counts["not found"]} not found
            {report.counts["not checked"] ? `, ${report.counts["not checked"]} could not be checked` : ""}.
          </div>
          {report.no_entry.length > 0 && <div className="flag small">Cited with no entry: {report.no_entry.join(", ")}</div>}
          <table className="check-table small">
            <thead>
              <tr>
                <th>Key</th>
                <th>Result</th>
                <th>Entry</th>
              </tr>
            </thead>
            <tbody>
              {report.rows.map((r) => (
                <tr key={r.key} className={r.status === "confirmed" ? "" : "over"}>
                  <th>{r.key}</th>
                  <td>
                    <strong>{r.status[0].toUpperCase() + r.status.slice(1)}.</strong> {r.detail}
                  </td>
                  <td>
                    {r.authors} ({r.year}) {r.title} <Place file={r.source} onJump={onJump} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {report.uncited.length > 0 && <div className="muted small">In the bibliography but not cited: {report.uncited.join(", ")}</div>}
        </>
      )}
    </>
  );
}

const JOB_WORDS: Record<ScriptJob["state"], string> = {
  idle: "",
  running: "Running",
  done: "Finished",
  failed: "Failed",
  stopped: "Stopped",
};

function Figures({ projectId, onWritten, onError }: Props) {
  const [scan, setScan] = useState<ScriptScan | null>(null);
  const [job, setJob] = useState<ScriptJob | null>(null);
  const reload = useCallback(
    () => api.scripts(projectId).then((found) => {
      setScan(found);
      setJob(found.job);
    }, onError),
    [projectId, onError],
  );
  useEffect(() => void reload(), [reload]);
  const running = job?.state === "running";
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      api.scriptJob(projectId).then((next) => {
        setJob(next);
        if (next.state !== "running") {
          void reload();
          if (next.made.length) onWritten(next.made);
        }
      }, onError);
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [running, projectId, reload, onWritten, onError]);
  const start = (path: string) => api.runScript(projectId, path).then(setJob, onError);
  if (!scan) return <div className="muted">Reading the scripts...</div>;
  return (
    <>
      {!scan.has_folder && <div className="flag small">No scripts folder is set. Set one in the project's settings to run figure scripts here.</div>}
      {job && job.state !== "idle" && (
        <section className={`check-card ${job.state === "failed" ? "fail" : ""}`}>
          <div className="row-between">
            <span className="small">
              <strong>{JOB_WORDS[job.state]}:</strong> {job.script}. {job.detail}
              {job.made.length > 0 ? ` Changed ${job.made.join(", ")}.` : job.state === "done" ? " It changed no figure." : ""}
            </span>
            {running && <button onClick={() => void api.stopScript(projectId).catch(onError)}>Stop</button>}
          </div>
          {job.log && <pre className="script-log small">{job.log}</pre>}
        </section>
      )}
      <table className="check-table small">
        <thead>
          <tr>
            <th>Figure</th>
            <th>Made by</th>
          </tr>
        </thead>
        <tbody>
          {scan.figures.map((f) => (
            <tr key={f.name}>
              <th>{f.name}</th>
              <td>
                {!f.file && <div className="warn-text">The file was not found.</div>}
                {f.scripts.length === 0 && <span className="muted">No script names this figure.</span>}
                {f.scripts.map((path) => (
                  <div key={path} className="row">
                    <span className="mono">{path}</span>
                    {f.stale.includes(path) && <span className="warn-text">The script is newer than the figure.</span>}
                    <button disabled={running} onClick={() => void start(path)}>
                      Run
                    </button>
                  </div>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {scan.scripts.length > 0 && (
        <details className="small">
          <summary>All scripts ({scan.scripts.length})</summary>
          {scan.scripts.map((s) => (
            <div key={s.path} className="row">
              <span className="mono">{s.command}</span>
              <span className="muted">{s.path}</span>
              <button disabled={running} onClick={() => void start(s.path)}>
                Run
              </button>
            </div>
          ))}
        </details>
      )}
      <div className="muted small">A script runs in its own folder, as it would in Terminal, and can change your files. Matching goes by the names in its text, so one that builds names from parts is not seen.</div>
    </>
  );
}

function Submission({ projectId, prepare, onError }: Props) {
  const [result, setResult] = useState<SubmissionResult | null>(null);
  const [busy, setBusy] = useState(false);
  const make = async () => {
    setBusy(true);
    try {
      await prepare();
      setResult(await api.makeSubmission(projectId));
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <div className="row-between">
        <span className="muted small">
          One .tex with its inputs and bibliography in place, the figures the last compile used beside it, and any local class and style
          files. It is compiled from scratch in an empty folder, and only saved if that works.
        </span>
        <button className="primary" disabled={busy} onClick={() => void make()}>
          {busy ? "Making it and compiling it..." : "Make the zip"}
        </button>
      </div>
      {result && (
        <section className="check-card ok">
          <div className="row-between">
            <span className="small">
              <strong>{result.path}</strong>: compiled to {result.pages} pages, {(result.size / 1_000_000).toFixed(1)} MB.
            </span>
            <button className="link small" onClick={() => void api.reveal(projectId, result.path).catch(onError)}>
              Show in Finder
            </button>
          </div>
          {result.notes.map((note) => (
            <div key={note} className="flag small">
              {note}
            </div>
          ))}
          <details className="small">
            <summary>{result.files.length} files</summary>
            <div className="mono">{result.files.join("\n")}</div>
          </details>
        </section>
      )}
    </>
  );
}

/** What to look at before a revision goes out. */
export function ChecksView(props: Props) {
  const [section, setSection] = useState<Section>("checks");
  return (
    <div className="check-view">
      <div className="changes-head">
        <div className="plan-choices" role="group" aria-label="Which checks">
          {SECTIONS.map((s) => (
            <button key={s.id} className={section === s.id ? "chosen" : ""} aria-pressed={section === s.id} onClick={() => setSection(s.id)}>
              {s.label}
            </button>
          ))}
        </div>
      </div>
      <div className="check-body">
        {section === "checks" && <Checks {...props} />}
        {section === "numbers" && <Numbers {...props} />}
        {section === "references" && <References {...props} />}
        {section === "figures" && <Figures {...props} />}
        {section === "submission" && <Submission {...props} />}
      </div>
    </div>
  );
}

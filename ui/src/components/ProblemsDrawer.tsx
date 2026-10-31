import { useEffect, useState } from "react";
import { api, type BuildResult, type Problem } from "../api";
import { IconChevron } from "../icons";

const KIND_LABEL: Record<Problem["kind"], string> = {
  error: "Error",
  undefined_reference: "Undefined reference",
  undefined_citation: "Undefined citation",
  overfull: "Overfull box",
  warning: "Warning",
};

interface Props {
  projectId: string;
  build: BuildResult | null;
  open: boolean;
  onToggle: () => void;
  onJump: (file: string, line: number) => void;
  /** Compiles without the files kept from earlier compiles. */
  onCompileClean: () => void;
  compiling: boolean;
  /** Asks the agent to fix what this build reported. */
  onFixWithAgent: () => void;
}

/** The bottom drawer: what the last build reported, and its log. */
export function ProblemsDrawer({ projectId, build, open, onToggle, onJump, onCompileClean, compiling, onFixWithAgent }: Props) {
  const [tab, setTab] = useState<"problems" | "log">("problems");
  const [log, setLog] = useState<string | null>(null);
  const counts = build?.counts;

  useEffect(() => setLog(null), [build?.id]);
  useEffect(() => {
    if (!open || tab !== "log" || !build || log !== null) return;
    const name = build.main.replace(/\.tex$/, "") + ".log";
    fetch(api.buildFileUrl(projectId, build.id, name), { credentials: "same-origin" })
      .then((r) => (r.ok ? r.text() : `The log is gone; compile again.`))
      .then(setLog, () => setLog("The log could not be read."));
  }, [open, tab, build, log, projectId]);

  const chip = (n: number | undefined, one: string, many: string, tone: string) =>
    n ? (
      <span className={`chip ${tone}`}>
        {n} {n === 1 ? one : many}
      </span>
    ) : null;

  return (
    <div className={`drawer ${open ? "open" : ""}`}>
      <div className="drawer-head">
        <button className={`tab ${tab === "problems" && open ? "active" : ""}`} onClick={() => (setTab("problems"), !open && onToggle())}>
          Problems
        </button>
        <button className={`tab ${tab === "log" && open ? "active" : ""}`} onClick={() => (setTab("log"), !open && onToggle())}>
          Log
        </button>
        {build && (
          <>
            {chip(counts?.error, "error", "errors", "bad")}
            {chip(counts?.undefined_reference, "undefined reference", "undefined references", "warn")}
            {chip(counts?.undefined_citation, "undefined citation", "undefined citations", "warn")}
            {chip(counts?.overfull, "overfull box", "overfull boxes", "")}
            {chip(counts?.warning, "warning", "warnings", "")}
            {build.problems.length === 0 && <span className="chip">No problems</span>}
          </>
        )}
        <span className="spacer" />
        {build && build.counts.error > 0 && (
          <button className="link small" onClick={onFixWithAgent} title="Asks the agent to fix these errors. You review its edits.">
            Fix with the agent
          </button>
        )}
        <button
          className="link small"
          onClick={onCompileClean}
          disabled={compiling}
          title="Compiles again without the .aux, .bbl and other files kept from earlier compiles. Slower; use it when a compile fails for no reason you can see."
        >
          Compile from scratch
        </button>
        <button className="icon" onClick={onToggle} aria-label={open ? "Hide the drawer" : "Show the drawer"}>
          <IconChevron up={!open} />
        </button>
      </div>
      {open && (
        <div className="drawer-body">
          {!build && <div className="empty">Compile to see problems here.</div>}
          {build && tab === "problems" &&
            (build.problems.length === 0 ? (
              <div className="empty">The last build reported no problems.</div>
            ) : (
              build.problems.map((p, i) => (
                <button
                  key={i}
                  className="problem"
                  disabled={!p.file || !p.line}
                  onClick={() => p.file && p.line && onJump(p.file, p.line)}
                  title={p.file && p.line ? "Go to this line" : undefined}
                >
                  <span className={`kind ${p.kind}`}>{KIND_LABEL[p.kind]}</span>
                  <span>
                    {p.message}
                    {p.detail && <pre>{p.detail}</pre>}
                  </span>
                  <span className="where">{p.file ? `${p.file}${p.line ? `:${p.line}` : ""}` : ""}</span>
                </button>
              ))
            ))}
          {build && tab === "log" && <pre className="log-view">{log ?? "Reading the log..."}</pre>}
        </div>
      )}
    </div>
  );
}

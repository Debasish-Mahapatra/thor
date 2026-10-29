import type { ReactNode } from "react";
import type { DepsReport } from "../api";
import { APP_NAME } from "../brand";
import { groupChecks, sharedFix } from "../format";

/** Text with `commands` in backticks, shown as selectable code. */
function Hint({ text }: { text: string }) {
  const parts: ReactNode[] = text.split("`").map((part, i) =>
    i % 2 ? (
      <code key={i} className="fix">
        {part}
      </code>
    ) : (
      part
    ),
  );
  return <div className="hint-text">{parts}</div>;
}

export function SystemCheck(props: { report: DepsReport | null; checking: boolean; onRecheck: () => void }) {
  const { report, checking } = props;
  return (
    <div className="system-check">
      <div className="row-between">
        <p className="muted">
          {report === null
            ? "Looking for LaTeX, Python packages and Claude Code."
            : report.summary.ok
              ? `Everything ${APP_NAME} needs is installed.`
              : `Missing: ${report.summary.missing.join(", ")}.`}
        </p>
        <button onClick={props.onRecheck} disabled={checking}>
          {checking ? "Checking..." : "Check again"}
        </button>
      </div>
      {report &&
        groupChecks(report.checks).map((group) => {
          const shared = sharedFix(group.checks);
          return (
            <section key={group.group} className="check-group">
              <h3>
                {group.group}
                {group.missingRequired > 0 && <span className="count warn">{group.missingRequired} missing</span>}
              </h3>
              {shared && <Hint text={shared} />}
              <table className="checks">
                <tbody>
                  {group.checks.map((c) => (
                    <tr key={c.id} className={c.ok ? "ok" : c.required ? "missing" : "optional"}>
                      <td className="state">{c.ok ? "Found" : c.required ? "Missing" : "Optional"}</td>
                      <td className="name">{c.name}</td>
                      <td>
                        <div>{c.ok ? c.version || c.detail : c.detail}</div>
                        {c.ok && c.version && c.detail && <div className="muted small">{c.detail}</div>}
                        {!c.ok && c.fix && c.fix !== shared && <Hint text={c.fix} />}
                        <div className="muted small">For {c.needed_for}</div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          );
        })}
    </div>
  );
}

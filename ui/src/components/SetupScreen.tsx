import type { SetupStatus, SetupStep } from "../api";
import { APP_NAME, BoltLogo } from "../brand";
import { formatSize } from "../format";

const STATE_LABEL: Record<SetupStep["state"], string> = {
  waiting: "Waiting",
  running: "Installing",
  done: "Done",
  failed: "Failed",
};

/** Shown while the first-run setup installs Claude Code and MacTeX. There is nothing to click unless a step fails. */
export function SetupScreen({ status, onRetry }: { status: SetupStatus; onRetry: () => void }) {
  const tex = status.tex;

  const detail = (step: SetupStep) => {
    if (step.id !== "mactex" || step.state !== "running") return step.message;
    if (tex.step === "downloading") {
      const share = tex.total ? Math.min(1, tex.downloaded / tex.total) : 0;
      return (
        <>
          <div>{tex.message}</div>
          <div className="row">
            <div className="bar">
              <div style={{ width: `${share * 100}%` }} />
            </div>
            <span className="muted small">
              {formatSize(tex.downloaded)}
              {tex.total ? ` of ${formatSize(tex.total)}` : ""}
            </span>
          </div>
        </>
      );
    }
    if (tex.step === "installing") {
      return (
        <>
          <div>{tex.message}</div>
          <div className="muted small">If you don't see the password dialog, it may be behind this window.</div>
        </>
      );
    }
    return tex.message || step.message;
  };

  return (
    <div className="setup">
      <h1>
        <BoltLogo size={28} />
        Setting up {APP_NAME}
      </h1>
      <p className="muted">
        {APP_NAME} is installing what it needs. macOS asks for your password once, to install MacTeX.
      </p>
      <ul className="setup-steps">
        {status.steps.map((step) => (
          <li key={step.id}>
            <strong>{step.name}</strong>
            <span className={`state-${step.state}`}>{STATE_LABEL[step.state]}</span>
            <div>{detail(step)}</div>
          </li>
        ))}
      </ul>
      {status.failed && !status.running && (
        <button className="primary" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

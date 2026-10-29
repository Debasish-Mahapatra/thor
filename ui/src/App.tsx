import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type DepsReport, type Project, type SetupStatus } from "./api";
import { APP_NAME, BoltLogo } from "./brand";
import { Home } from "./components/Home";
import { Modal } from "./components/Modal";
import { SetupScreen } from "./components/SetupScreen";
import { SettingsDialog } from "./components/SettingsDialog";
import { SystemCheck } from "./components/SystemCheck";
import { Workspace } from "./components/Workspace";
import { onShellCommand } from "./native";

// The open project lives in the URL hash, so a reload keeps it.
function projectIdFromHash(): string | null {
  const m = window.location.hash.match(/^#\/project\/([0-9a-f]+)$/);
  return m ? m[1] : null;
}

export function App() {
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deps, setDeps] = useState<DepsReport | null>(null);
  const [checking, setChecking] = useState(false);
  const [dialog, setDialog] = useState<"settings" | "system" | null>(null);
  const [setup, setSetup] = useState<SetupStatus | null>(null);

  const report = useCallback((e: unknown) => {
    if (e instanceof ApiError && e.status === 401) {
      setError(`This window lost its connection to ${APP_NAME}. Quit and reopen the app.`);
    } else {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const recheck = useCallback(async () => {
    setChecking(true);
    try {
      setDeps(await api.deps());
    } catch (e) {
      report(e);
    } finally {
      setChecking(false);
    }
  }, [report]);

  const show = useCallback((p: Project | null) => {
    setProject(p);
    setError(null);
    const hash = p ? `#/project/${p.id}` : "";
    if (window.location.hash !== hash) history.replaceState(null, "", hash || window.location.pathname);
  }, []);

  const openPath = useCallback(
    async (path: string, main?: string) => {
      try {
        show(await api.open(path, main));
      } catch (e) {
        report(e);
      }
    },
    [report, show],
  );

  useEffect(() => {
    void recheck();
    api.setup().then(setSetup, report);
    const id = projectIdFromHash();
    if (id) api.project(id).then(show, () => show(null));
  }, [recheck, report, show]);

  // While first-run setup runs, read its state once a second; when it ends, check the system again.
  const setupRunning = setup?.running ?? false;
  useEffect(() => {
    if (!setupRunning) return;
    const timer = window.setInterval(() => api.setup().then(setSetup, report), 1000);
    return () => {
      window.clearInterval(timer);
      void recheck();
    };
  }, [setupRunning, recheck, report]);

  const retrySetup = useCallback(() => api.retrySetup().then(setSetup, report), [report]);
  const inSetup = setup !== null && setup.enabled && (setup.running || setup.failed);

  useEffect(
    () =>
      onShellCommand((command) => {
        if (command.cmd === "openProject" && command.arg) void openPath(command.arg);
        if (command.cmd === "openSettings") setDialog("settings");
      }),
    [openPath],
  );

  const missing = deps?.summary.missing.length ?? 0;

  const banner = error && (
    <div className="banner error" role="alert">
      <span>{error}</span>
      <button className="link" onClick={() => setError(null)}>
        Dismiss
      </button>
    </div>
  );

  const dialogs = (
    <>
      {dialog === "settings" && <SettingsDialog onClose={() => setDialog(null)} onSaved={recheck} onError={report} />}
      {dialog === "system" && (
        <Modal title="System check" onClose={() => setDialog(null)} wide>
          <SystemCheck report={deps} checking={checking} onRecheck={recheck} />
        </Modal>
      )}
    </>
  );

  if (project && !inSetup) {
    return (
      <div className="app">
        {banner}
        <Workspace
          project={project}
          missing={missing}
          onProject={show}
          onHome={() => show(null)}
          onSettings={() => setDialog("settings")}
          onSystem={() => setDialog("system")}
          onError={report}
        />
        {dialogs}
      </div>
    );
  }

  return (
    <div className="app">
      <header className="topbar">
        <button className="wordmark" onClick={() => show(null)} title="All projects">
          <BoltLogo />
          {APP_NAME}
        </button>
        <div className="spacer" />
        <button
          className={`status-pill ${deps === null ? "" : missing ? "warn" : "good"}`}
          onClick={() => setDialog("system")}
        >
          {deps === null ? "Checking setup" : missing ? `${missing} missing` : "Setup complete"}
        </button>
        <button className="ghost" onClick={() => setDialog("settings")}>
          Settings
        </button>
      </header>

      {banner}

      <main className="content">
        {inSetup ? (
          <SetupScreen status={setup} onRetry={retrySetup} />
        ) : (
          <Home deps={deps} checking={checking} onRecheck={recheck} onOpen={openPath} onError={report} />
        )}
      </main>
      {dialogs}
    </div>
  );
}

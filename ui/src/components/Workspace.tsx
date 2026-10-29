import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  api,
  fetchBytes,
  type BuildResult,
  type OpenedFile,
  type Project,
  type RevisionOverview,
  type SyncBox,
  type TreeEntry,
  type WordCount,
} from "../api";
import { APP_NAME, BoltLogo } from "../brand";
import { IconGear, IconPlay, IconSidebar } from "../icons";
import { outline as makeOutline, type OutlineItem } from "../outline";
import { basename, fileKind, joinPath, relativeTo } from "../paths";
import { Editor, type EditorHandle } from "./Editor";
import { FileTree } from "./FileTree";
import { HistoryDialog } from "./HistoryDialog";
import { PdfViewer } from "./PdfViewer";
import { ProblemsDrawer } from "./ProblemsDrawer";
import { ProjectSettingsDialog } from "./ProjectSettingsDialog";
import { RevisionMenu } from "./RevisionMenu";
import { TrackedView } from "./TrackedView";

// The views of the window; those not built yet arrive with their milestones.
const TABS = [
  { id: "write", label: "Write" },
  { id: "comments", label: "Comments", later: "comment import (M3)" },
  { id: "plan", label: "Plan", later: "the revision plan (M5)" },
  { id: "changes", label: "Changes", later: "the agent's proposed edits (M4)" },
  { id: "tracked", label: "Tracked PDF" },
  { id: "reply", label: "Reply", later: "the reply document (M7)" },
];

const AUTOSAVE_MS = 1500;

type RightView = { kind: "build" } | { kind: "pdf"; path: string; data: ArrayBuffer } | { kind: "image"; path: string };

interface Props {
  project: Project;
  missing: number;
  onProject: (p: Project) => void;
  onHome: () => void;
  onSettings: () => void;
  onSystem: () => void;
  onError: (e: unknown) => void;
}

export function Workspace({ project, missing, onProject, onHome, onSettings, onSystem, onError }: Props) {
  const cfg = project.config;
  const mainPath = joinPath(cfg.latex_dir, cfg.main);
  const pdfTitle = cfg.main.replace(/\.tex$/, ".pdf");

  const [sidebar, setSidebar] = useState(true);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [projectDialog, setProjectDialog] = useState(false);
  const [file, setFile] = useState<OpenedFile | null>(null);
  const [dirty, setDirty] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [outline, setOutline] = useState<OutlineItem[]>([]);
  const [build, setBuild] = useState<BuildResult | null>(null);
  const [compiling, setCompiling] = useState(false);
  const [pdf, setPdf] = useState<{ data: ArrayBuffer; version: string } | null>(null);
  const [view, setView] = useState<RightView>({ kind: "build" });
  const [highlight, setHighlight] = useState<SyncBox[] | null>(null);
  const [words, setWords] = useState<WordCount | null>(null);
  const [tab, setTab] = useState<"write" | "tracked">("write");
  const [overview, setOverview] = useState<RevisionOverview | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);

  const editor = useRef<EditorHandle>(null);
  const fileRef = useRef<OpenedFile | null>(null);
  const hashRef = useRef<string | null>(null);
  const dirtyRef = useRef(false);
  const compilingRef = useRef(false);
  const saving = useRef<Promise<boolean> | null>(null);
  const pendingLine = useRef<number | null>(null);
  const timers = useRef<{ save?: number; outline?: number }>({});

  const markDirty = (value: boolean) => {
    dirtyRef.current = value;
    setDirty(value);
  };

  const save = useCallback(async (): Promise<boolean> => {
    if (saving.current) await saving.current;
    const current = fileRef.current;
    if (!current || !dirtyRef.current || !editor.current) return true;
    window.clearTimeout(timers.current.save);
    const text = editor.current.text();
    const attempt = (async () => {
      try {
        const saved = await api.saveFile(project.id, current.path, text, hashRef.current);
        hashRef.current = saved.hash;
        // Typing during the save leaves the file edited.
        markDirty(editor.current?.text() !== text);
        return true;
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) setConflict(true);
        else onError(e);
        return false;
      }
    })();
    saving.current = attempt;
    const ok = await attempt;
    saving.current = null;
    return ok;
  }, [project.id, onError]);

  const openFile = useCallback(
    async (path: string, line?: number) => {
      try {
        const kind = fileKind(path);
        if (kind === "pdf") {
          setView({ kind: "pdf", path, data: await fetchBytes(api.rawUrl(project.id, path)) });
          return;
        }
        if (kind === "image") {
          setView({ kind: "image", path });
          return;
        }
        if (kind !== "text") {
          onError(new Error(`${basename(path)} is not a file ${APP_NAME} can open.`));
          return;
        }
        if (fileRef.current?.path === path) {
          if (line) editor.current?.goto(line);
          return;
        }
        if (!(await save())) return;
        const opened = await api.file(project.id, path);
        pendingLine.current = line ?? null;
        hashRef.current = opened.hash;
        fileRef.current = opened;
        markDirty(false);
        setConflict(false);
        setFile(opened);
        setOutline(makeOutline(opened.text, cfg.si_marker));
      } catch (e) {
        onError(e);
      }
    },
    [project.id, cfg.si_marker, save, onError],
  );

  // The editor for a newly opened file mounts after the render; then jump to the requested line.
  useEffect(() => {
    const line = pendingLine.current;
    if (line && file) {
      pendingLine.current = null;
      requestAnimationFrame(() => editor.current?.goto(line));
    }
  }, [file]);

  const showBuild = useCallback(
    async (result: BuildResult) => {
      setBuild(result);
      setHighlight(null);
      if (result.pdf) {
        const name = result.main.replace(/\.tex$/, ".pdf");
        setPdf({ data: await fetchBytes(api.buildFileUrl(project.id, result.id, name)), version: result.id });
      }
    },
    [project.id],
  );

  const compile = useCallback(async () => {
    if (compilingRef.current) return;
    compilingRef.current = true;
    setCompiling(true);
    try {
      if (!(await save())) return;
      const result = await api.build(project.id);
      await showBuild(result);
      setView({ kind: "build" });
      if (result.revision_update) setOverview(await api.revision(project.id));
      if (result.counts.error > 0) setDrawerOpen(true);
      api.wordCount(project.id).then(setWords, () => undefined);
    } catch (e) {
      onError(e);
    } finally {
      compilingRef.current = false;
      setCompiling(false);
    }
  }, [project.id, save, showBuild, onError]);

  const saveAndCompile = useCallback(() => void compile(), [compile]);

  const syncToPdf = useCallback(
    async (line?: number) => {
      const current = fileRef.current;
      const rel = current && relativeTo(cfg.latex_dir, current.path);
      if (!rel) return;
      try {
        const { boxes } = await api.syncForward(project.id, rel, line ?? editor.current?.cursorLine() ?? 1);
        setView({ kind: "build" });
        setHighlight(boxes.length ? boxes : null);
      } catch (e) {
        onError(e);
      }
    },
    [project.id, cfg.latex_dir, onError],
  );

  const syncToSource = useCallback(
    async (page: number, x: number, y: number) => {
      try {
        const found = await api.syncBackward(project.id, page, x, y);
        await openFile(joinPath(cfg.latex_dir, found.file), found.line);
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 404)) onError(e);
      }
    },
    [project.id, cfg.latex_dir, openFile, onError],
  );

  const onEdit = () => {
    markDirty(true);
    window.clearTimeout(timers.current.save);
    timers.current.save = window.setTimeout(() => void save(), AUTOSAVE_MS);
    window.clearTimeout(timers.current.outline);
    timers.current.outline = window.setTimeout(() => {
      if (editor.current) setOutline(makeOutline(editor.current.text(), cfg.si_marker));
    }, 400);
  };

  const useDiskVersion = async () => {
    const current = fileRef.current;
    if (!current) return;
    try {
      const opened = await api.file(project.id, current.path);
      hashRef.current = opened.hash;
      editor.current?.setText(opened.text);
      markDirty(false);
      setConflict(false);
      setOutline(makeOutline(opened.text, cfg.si_marker));
    } catch (e) {
      onError(e);
    }
  };

  const keepMine = async () => {
    const current = fileRef.current;
    if (!current) return;
    try {
      hashRef.current = (await api.fileHash(project.id, current.path)).hash;
      setConflict(false);
      markDirty(true);
      await save();
    } catch (e) {
      onError(e);
    }
  };

  // Open the main file and the last build when the project opens.
  useEffect(() => {
    fileRef.current = null;
    setFile(null);
    setBuild(null);
    setPdf(null);
    setView({ kind: "build" });
    void openFile(mainPath);
    api.latestBuild(project.id).then((last) => last && showBuild(last), onError);
    api.wordCount(project.id).then(setWords, () => undefined);
    api.revision(project.id).then(setOverview, onError);
    // Only when the project or its main file changes.
  }, [project.id, mainPath]);

  // While the revision folder is being updated, read its state every 1.5 s.
  const updating = overview?.sync.state === "running";
  useEffect(() => {
    if (!updating) return;
    const timer = window.setInterval(() => api.revision(project.id).then(setOverview, () => undefined), 1500);
    return () => window.clearInterval(timer);
  }, [updating, project.id]);

  const startRevision = async () => {
    try {
      setOverview(await api.startRevision(project.id));
      onProject(await api.project(project.id));
    } catch (e) {
      onError(e);
    }
  };

  const continueRevision = async () => {
    try {
      setOverview(await api.continueRevision(project.id));
    } catch (e) {
      onError(e);
    }
  };

  const updateRevision = async () => {
    if (!(await save())) return;
    setOverview(await api.updateRevision(project.id));
  };

  // When the window comes back to the front, pick up changes other programs made to the open file.
  useEffect(() => {
    const onFocus = async () => {
      const current = fileRef.current;
      if (!current) return;
      try {
        const { hash } = await api.fileHash(project.id, current.path);
        if (!hash || hash === hashRef.current) return;
        if (dirtyRef.current) setConflict(true);
        else await useDiskVersion();
      } catch {
        // The next save reports any problem.
      }
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  });

  // ⌘B, ⌘↩ and ⌘S also work when the focus is outside the editor.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || !e.metaKey) return;
      if (e.key === "b" || e.key === "Enter" || e.key === "s") {
        e.preventDefault();
        saveAndCompile();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [saveAndCompile]);

  // Save before the window closes.
  useEffect(() => {
    const onHide = () => {
      if (dirtyRef.current) void save();
    };
    window.addEventListener("pagehide", onHide);
    return () => window.removeEventListener("pagehide", onHide);
  }, [save]);

  const changeEngine = async (engine: string) => {
    try {
      onProject(await api.saveConfig(project.id, { ...cfg, engine }));
    } catch (e) {
      onError(e);
    }
  };

  const status = compiling ? "Compiling..." : build ? build.summary : "Not compiled yet";
  const dot = compiling ? "busy" : build ? (build.ok ? "ok" : "failed") : "";
  const backToBuild = () => setView({ kind: "build" });

  return (
    <div className="workspace">
      <header className="toolbar">
        <button className="brand" onClick={onHome} title="All projects">
          <BoltLogo size={30} />
          <span>
            <span className="brand-name">{APP_NAME}</span>
            <span className="brand-sub">LaTeX editor for scientists</span>
          </span>
        </button>
        <button className="compile" onClick={saveAndCompile} disabled={compiling} title="Save and compile (⌘S, ⌘B)">
          <IconPlay /> Compile
        </button>
        <select value={cfg.engine} onChange={(e) => void changeEngine(e.target.value)} aria-label="Engine">
          <option value="pdflatex">pdfLaTeX</option>
          <option value="xelatex">XeLaTeX</option>
          <option value="lualatex">LuaLaTeX</option>
        </select>
        <RevisionMenu
          overview={overview}
          onStart={startRevision}
          onContinue={continueRevision}
          onShowReport={() => setTab("tracked")}
        />
        <div className="status" title={status}>
          <span className={`dot ${dot}`} />
          {status}
        </div>
        <span className="spacer" />
        {missing > 0 && (
          <button className="status-pill warn" onClick={onSystem}>
            {missing} missing
          </button>
        )}
        <button
          className={`icon ${sidebar ? "on" : ""}`}
          onClick={() => setSidebar((s) => !s)}
          title="Show or hide the sidebar"
          aria-label="Show or hide the sidebar"
        >
          <IconSidebar />
        </button>
        <button className="icon" onClick={onSettings} title="Settings" aria-label="Settings">
          <IconGear />
        </button>
      </header>

      <div className={`body ${sidebar ? "" : "no-sidebar"}`}>
        {sidebar && (
          <aside className="side">
            <section className="side-section">
              <div className="side-head">
                Project
                <button className="icon" onClick={() => setProjectDialog(true)} title="Project settings" aria-label="Project settings">
                  <IconGear />
                </button>
              </div>
              <FileTree
                projectId={project.id}
                root={cfg.latex_dir}
                extra={[cfg.results_dir, cfg.scripts_dir]}
                label={project.name}
                selected={view.kind === "build" ? (file?.path ?? null) : view.path}
                onOpen={(entry: TreeEntry) => void openFile(entry.path)}
                onError={onError}
              />
            </section>
            <section className="side-section">
              <div className="side-head">Document outline</div>
              <nav className="outline" aria-label="Document outline">
                {outline.length === 0 && <div className="muted small" style={{ padding: "2px 14px" }}>No headings.</div>}
                {outline.map((item) => (
                  <button key={`${item.line}-${item.title}`} className={`level-${item.level}`} onClick={() => editor.current?.goto(item.line)} title={item.title}>
                    {item.title}
                  </button>
                ))}
              </nav>
            </section>
          </aside>
        )}

        <div className="main">
          <nav className="tabs" aria-label="Views">
            {TABS.map((t) => (
              <button
                key={t.id}
                className={`tab ${t.id === tab ? "active" : ""}`}
                disabled={!!t.later}
                title={t.later ? `Arrives with ${t.later}` : undefined}
                onClick={() => (t.id === "write" || t.id === "tracked") && setTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </nav>
          {tab === "tracked" && (
            <TrackedView projectId={project.id} overview={overview} onUpdate={updateRevision} onError={onError} />
          )}
          <div className="panes" hidden={tab !== "write"}>
            <div className="pane">
              <div className="pane-head">
                <span className="pane-title">{file ? basename(file.path) : "No file open"}</span>
                {dirty && <span className="dirty">Edited</span>}
                <span className="spacer" />
                {file && (
                  <button className="link small" onClick={() => setHistoryOpen(true)}>
                    History
                  </button>
                )}
                {words?.ok && file?.path === mainPath && (
                  <span className="muted small" title={`${words.headers} in headings, ${words.captions} in captions (texcount, whole document)`}>
                    {words.text?.toLocaleString()} words
                  </span>
                )}
              </div>
              {conflict && file && (
                <div className="conflict">
                  <span>{basename(file.path)} changed on disk since you opened it.</span>
                  <button onClick={() => void useDiskVersion()}>Use the disk version</button>
                  <button onClick={() => void keepMine()}>Keep my version</button>
                </div>
              )}
              <div className="pane-body">
                {file ? (
                  <Editor
                    key={file.path}
                    ref={editor}
                    path={file.path}
                    initialText={file.text}
                    latex={/\.(tex|sty|cls|bib|ltx|dtx)$/.test(file.path)}
                    onChange={onEdit}
                    onSave={saveAndCompile}
                    onCompile={saveAndCompile}
                    onSyncToPdf={(line) => void syncToPdf(line)}
                  />
                ) : (
                  <div className="empty">Choose a file in the sidebar.</div>
                )}
              </div>
            </div>
            {view.kind === "image" ? (
              <div className="pane">
                <div className="pane-head">
                  <span className="pane-title">{view.path}</span>
                  <button className="link small" onClick={backToBuild}>
                    Back to {pdfTitle}
                  </button>
                </div>
                <div className="pane-body">
                  <div className="image-view">
                    <img src={api.rawUrl(project.id, view.path)} alt={view.path} />
                  </div>
                </div>
              </div>
            ) : (
              <PdfViewer
                title={view.kind === "pdf" ? view.path : pdfTitle}
                data={view.kind === "pdf" ? view.data : (pdf?.data ?? null)}
                version={view.kind === "pdf" ? view.path : (pdf?.version ?? "")}
                highlight={view.kind === "build" ? highlight : null}
                onDoubleClick={view.kind === "build" ? (p, x, y) => void syncToSource(p, x, y) : undefined}
                onSyncFromCursor={view.kind === "build" ? () => void syncToPdf() : undefined}
                extraHead={
                  view.kind === "pdf" ? (
                    <button className="link small" onClick={backToBuild}>
                      Back to {pdfTitle}
                    </button>
                  ) : undefined
                }
              />
            )}
          </div>
        </div>
      </div>

      <ProblemsDrawer
        projectId={project.id}
        build={build}
        open={drawerOpen}
        onToggle={() => setDrawerOpen((o) => !o)}
        onJump={(f, line) => void openFile(joinPath(cfg.latex_dir, f), line)}
      />

      {historyOpen && file && (
        <HistoryDialog
          projectId={project.id}
          path={file.path}
          onRestore={(text) => {
            editor.current?.replaceAll(text);
            setHistoryOpen(false);
          }}
          onClose={() => setHistoryOpen(false)}
          onError={onError}
        />
      )}

      {projectDialog && (
        <ProjectSettingsDialog
          project={project}
          onSaved={onProject}
          onClose={() => setProjectDialog(false)}
          onError={onError}
        />
      )}
    </div>
  );
}

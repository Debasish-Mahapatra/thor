import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import {
  ApiError,
  api,
  fetchBytes,
  type AidData,
  type BuildResult,
  type ContextItem,
  type EditorContext,
  type OpenedFile,
  type Project,
  type RevisionOverview,
  type StyleRule,
  type SyncBox,
  type TreeEntry,
  type WordCount,
} from "../api";
import { APP_NAME } from "../brand";
import { IconAgent, IconBack, IconChevron, IconEditorPane, IconGear, IconPdfPane, IconPlay, IconSidebar } from "../icons";
import { useLayoutValue } from "../layout";
import { outline as makeOutline, type OutlineItem } from "../outline";
import { basename, fileKind, joinPath, relativeTo } from "../paths";
import { AgentPanel, type AgentRequest } from "./AgentPanel";
import { ChangesView, countOf } from "./ChangesView";
import { CommentsView } from "./CommentsView";
import { Editor, type EditorHandle, type EditorKeys } from "./Editor";
import { FileTree } from "./FileTree";
import { HistoryDialog } from "./HistoryDialog";
import { PdfViewer } from "./PdfViewer";
import { ProblemsDrawer } from "./ProblemsDrawer";
import { ProjectSettingsDialog } from "./ProjectSettingsDialog";
import { RevisionMenu } from "./RevisionMenu";
import { SearchPanel } from "./SearchPanel";
import { Splitter } from "./Splitter";
import { SymbolPalette } from "./SymbolPalette";
import { TrackedView } from "./TrackedView";

type TabId = "write" | "comments" | "changes" | "tracked";

// The views of the window; those not built yet arrive with their milestones.
const TABS: { id: string; label: string; later?: string }[] = [
  { id: "write", label: "Write" },
  { id: "comments", label: "Comments" },
  { id: "plan", label: "Plan", later: "the revision plan (M5)" },
  { id: "changes", label: "Changes" },
  { id: "tracked", label: "Tracked PDF" },
  { id: "reply", label: "Reply", later: "the reply document (M7)" },
];

const AUTOSAVE_MS = 1500;

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

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

  const [sidebar, setSidebar] = useLayoutValue("sidebar", true);
  const [filesOpen, setFilesOpen] = useLayoutValue("filesOpen", true);
  const [searchOpen, setSearchOpen] = useLayoutValue("searchOpen", false);
  const [symbolsOpen, setSymbolsOpen] = useLayoutValue("symbolsOpen", false);
  const [editorKeys] = useLayoutValue<EditorKeys>("editorKeys", "default");
  const [contextItems, setContextItems] = useState<ContextItem[]>([]);
  const [searchFocus, setSearchFocus] = useState(0);
  const [outlineOpen, setOutlineOpen] = useLayoutValue("outlineOpen", true);
  const [showEditor, setShowEditor] = useLayoutValue("showEditor", true);
  const [showPdf, setShowPdf] = useLayoutValue("showPdf", true);
  const [sideWidth, setSideWidth] = useLayoutValue("sideWidth", 250);
  const [agentWidth, setAgentWidth] = useLayoutValue("agentWidth", 370);
  const [agentHeight, setAgentHeight] = useLayoutValue("agentHeight", 270);
  const [editorShare, setEditorShare] = useLayoutValue("editorShare", 0.5);
  const [agentDock, setAgentDock] = useLayoutValue<"right" | "bottom">("agentDock", "right");
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
  const [tab, setTab] = useState<TabId>("write");
  const [agentOpen, setAgentOpen] = useLayoutValue("agentOpen", true);
  const [agentChat, setAgentChat] = useState<string | null>(null);
  const [waitingEdits, setWaitingEdits] = useState(0);
  const [agentRequest, setAgentRequest] = useState<AgentRequest | null>(null);
  const [agentFocus, setAgentFocus] = useState(0);
  const [selectedLines, setSelectedLines] = useState<{ from: number; to: number } | null>(null);
  const [autoCompile, setAutoCompile] = useLayoutValue("autoCompile", false);
  const [compileWords, setCompileWords] = useState("");
  const [overview, setOverview] = useState<RevisionOverview | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  // While a revision is active: the open file's baseline, whether its changes are marked, and which PDF shows.
  const [baseline, setBaseline] = useState<string | null>(null);
  const [showChanges, setShowChanges] = useLayoutValue("showChanges", true);
  const [changeCount, setChangeCount] = useState<number | null>(null);
  const [pdfMode, setPdfMode] = useState<"clean" | "tracked">("clean");
  const [trackedPdf, setTrackedPdf] = useState<ArrayBuffer | null>(null);

  const editor = useRef<EditorHandle>(null);
  const panes = useRef<HTMLDivElement>(null);
  const aidData = useRef<AidData | null>(null);
  const styleRules = useRef<StyleRule[]>([]);
  const fileRef = useRef<OpenedFile | null>(null);
  const hashRef = useRef<string | null>(null);
  const dirtyRef = useRef(false);
  const compilingRef = useRef(false);
  const autoRef = useRef(autoCompile);
  autoRef.current = autoCompile;
  // An edit arrived during a compile in automatic mode, so another follows it.
  const compileAgain = useRef(false);
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
        // New labels and citation keys become completions.
        api.aids(project.id).then((data) => (aidData.current = data), () => undefined);
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

  const compile = useCallback(async (clean = false) => {
    if (compilingRef.current) return;
    compilingRef.current = true;
    setCompiling(true);
    try {
      if (!(await save())) return;
      const result = await api.build(project.id, clean);
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
      if (compileAgain.current) {
        compileAgain.current = false;
        window.setTimeout(() => void compileRef.current(), 0);
      }
    }
  }, [project.id, save, showBuild, onError]);
  const compileRef = useRef(compile);
  compileRef.current = compile;

  // A compile's stage in words, for the toolbar.
  useEffect(() => {
    if (!compiling) {
      setCompileWords("");
      return;
    }
    const timer = window.setInterval(() => {
      api.buildProgress(project.id).then((p) => setCompileWords(p.text), () => undefined);
    }, 400);
    return () => window.clearInterval(timer);
  }, [compiling, project.id]);

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
    timers.current.save = window.setTimeout(() => {
      void save().then((saved) => {
        if (!saved || !autoRef.current) return;
        if (compilingRef.current) compileAgain.current = true;
        else void compileRef.current();
      });
    }, AUTOSAVE_MS);
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
    api.aids(project.id).then((data) => (aidData.current = data), () => undefined);
    api.styleRules(project.id).then((found) => (styleRules.current = found.rules), () => undefined);
    // Only when the project or its main file changes.
  }, [project.id, mainPath]);

  const active = overview?.active ?? null;
  useEffect(() => {
    setBaseline(null);
    if (!active || !file) return;
    api.revisionBaseline(project.id, file.path).then(
      (found) => setBaseline(found.text),
      () => setBaseline(null),
    );
  }, [project.id, active, file?.path]);

  // The incremental tracked PDF, seconds after a compile; the revision's own file only if there is none yet. Until the
  // new one is ready the last one stays on screen.
  const trackedPreview = overview?.sync.preview?.ok ? overview.sync.preview.id : null;
  const trackedVersion = trackedPreview ?? `${active}-${overview?.sync.finished ?? ""}`;
  const syncRunning = overview?.sync.state === "running";
  useEffect(() => {
    if (pdfMode !== "tracked" || !active) return;
    if (syncRunning && !trackedPreview) return;
    const url = trackedPreview ? api.trackedPreviewUrl(project.id) : api.revisionFileUrl(project.id, "revision_tracked.pdf");
    fetchBytes(url).then(setTrackedPdf, () => undefined);
  }, [project.id, pdfMode, active, trackedVersion, syncRunning, trackedPreview]);
  const showTracked = pdfMode === "tracked" && !!active;

  // While the revision folder is being updated, read its state often: the tracked PDF is ready within seconds. Once
  // it is, only the revision's own files remain, which wait for a pause and need no one watching.
  const updating = syncRunning && !trackedPreview;
  useEffect(() => {
    if (!syncRunning) return;
    const timer = window.setInterval(() => api.revision(project.id).then(setOverview, () => undefined), updating ? 400 : 3000);
    return () => window.clearInterval(timer);
  }, [syncRunning, updating, project.id]);

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

  /** Adds what is selected in the editor to the agent's next message; false if nothing is selected. */
  const addSelection = useCallback((): boolean => {
    const selected = editor.current?.selection();
    const current = fileRef.current;
    if (!selected || !current || !selected.text.trim()) return false;
    setContextItems((items) =>
      items.some((i) => i.file === current.path && i.from === selected.from && i.to === selected.to && i.text === selected.text)
        ? items
        : [...items, { id: `${current.path}:${selected.from}:${Date.now()}`, file: current.path, ...selected }],
    );
    setAgentOpen(true);
    return true;
  }, [setAgentOpen]);

  // ⌘B, ⌘↩ and ⌘S also work when the focus is outside the editor.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || !e.metaKey) return;
      if (e.key === "b" || e.key === "Enter" || e.key === "s") {
        e.preventDefault();
        saveAndCompile();
      }
      if (e.key === "k") {
        e.preventDefault();
        addSelection();
        setAgentOpen(true);
        setAgentFocus((n) => n + 1);
      }
      if (e.key === "f" && e.shiftKey) {
        e.preventDefault();
        setSidebar(true);
        setSearchOpen(true);
        setSearchFocus((n) => n + 1);
      }
      // Shift turns the backslash into a bar on most keyboards.
      if (e.key === "\\" || e.key === "|") {
        e.preventDefault();
        if (e.shiftKey) setAgentOpen(!agentOpen);
        else setSidebar(!sidebar);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [saveAndCompile, agentOpen, setAgentOpen, sidebar, setSidebar, addSelection, setSearchOpen]);

  // The editor and the PDF can each be hidden, but not both: then there would be nothing to work in.
  const toggleEditor = () => {
    if (showEditor && !showPdf) setShowPdf(true);
    setShowEditor(!showEditor);
  };
  const togglePdf = () => {
    if (showPdf && !showEditor) setShowEditor(true);
    setShowPdf(!showPdf);
  };
  const bothPanes = showEditor && showPdf;

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

  // What the agent sees of the editor when a message goes out.
  const agentContext = useCallback((): EditorContext => {
    const current = fileRef.current;
    const failed = build && build.counts.error > 0;
    return {
      file: current?.path,
      line: editor.current?.cursorLine(),
      selection: editor.current?.selection() ?? null,
      selections: contextItems,
      build: build?.summary,
      problems: failed
        ? build.problems
            .filter((p) => p.kind === "error")
            .map((p) => `${p.file ?? ""}${p.line ? `:${p.line}` : ""} ${p.message}`.trim())
        : undefined,
    };
  }, [build, contextItems]);

  const refreshWaiting = useCallback(() => {
    if (!agentChat) {
      setWaitingEdits(0);
      return;
    }
    api.changes(project.id, agentChat).then((c) => setWaitingEdits(countOf(c)), () => undefined);
  }, [project.id, agentChat]);
  useEffect(refreshWaiting, [refreshWaiting]);

  const editsAccepted = useCallback(
    (paths: string[]) => {
      if (fileRef.current && paths.includes(fileRef.current.path)) void useDiskVersion();
      if (autoRef.current) void compileRef.current();
    },
    [project.id],
  );

  const fixWithAgent = () => {
    setAgentOpen(true);
    setAgentRequest({ text: "Fix the errors from the last compile. Fix each at its cause; do not hide it.", token: Date.now() });
  };

  const seconds = build && build.passes > 0 && build.seconds > 0 ? ` · ${build.seconds.toFixed(1)} s` : "";
  const status = compiling ? `${compileWords || "Compiling"}...` : build ? build.summary + seconds : "Not compiled yet";
  const dot = compiling ? "busy" : build ? (build.ok ? "ok" : "failed") : "";
  const backToBuild = () => setView({ kind: "build" });

  return (
    <div className="workspace">
      <header className="toolbar">
        <button className="back-to-projects" onClick={onHome} title="All projects">
          <IconBack />
          <span>{project.name}</span>
        </button>
        <button className="compile" onClick={saveAndCompile} disabled={compiling} title="Save and compile (⌘S, ⌘B)">
          <IconPlay /> Compile
        </button>
        <label className="check auto-compile" title="Compiles each time the editor saves, a moment after you stop typing">
          <input
            type="checkbox"
            checked={autoCompile}
            onChange={(e) => setAutoCompile(e.target.checked)}
          />
          Auto
        </label>
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
          onClick={() => setSidebar(!sidebar)}
          title="Show or hide the files and outline (⌘\)"
          aria-label="Show or hide the files and outline"
        >
          <IconSidebar />
        </button>
        <button className={`icon ${showEditor ? "on" : ""}`} onClick={toggleEditor} title="Show or hide the editor" aria-label="Show or hide the editor">
          <IconEditorPane />
        </button>
        <button className={`icon ${showPdf ? "on" : ""}`} onClick={togglePdf} title="Show or hide the PDF" aria-label="Show or hide the PDF">
          <IconPdfPane />
        </button>
        <button
          className={`icon ${agentOpen ? "on" : ""}`}
          onClick={() => setAgentOpen(!agentOpen)}
          title="Show or hide the agent (⌘⇧\). ⌘K goes to its message box."
          aria-label="Show or hide the agent"
        >
          <IconAgent />
        </button>
        <button className="icon" onClick={onSettings} title="Settings" aria-label="Settings">
          <IconGear />
        </button>
      </header>

      <div
        className={`body ${sidebar ? "" : "no-sidebar"} ${agentOpen ? `with-agent agent-${agentDock}` : ""}`}
        style={{ "--side-width": `${sideWidth}px`, "--agent-width": `${agentWidth}px`, "--agent-height": `${agentHeight}px` } as CSSProperties}
      >
        {sidebar && (
          <aside className="side">
            <div className="side-scroll">
              <section className="side-section">
                <div className="side-head">
                  <button className="side-toggle" aria-expanded={searchOpen} title="Search all files (⌘⇧F)" onClick={() => setSearchOpen(!searchOpen)}>
                    <IconChevron up={searchOpen} /> Search
                  </button>
                </div>
                <div hidden={!searchOpen}>
                  <SearchPanel
                    projectId={project.id}
                    focusToken={searchFocus}
                    prepare={save}
                    onOpen={(path, line) => void openFile(path, line)}
                    onReplaced={(paths) => {
                      if (fileRef.current && paths.includes(fileRef.current.path)) void useDiskVersion();
                    }}
                    onError={onError}
                  />
                </div>
              </section>
              <section className="side-section">
                <div className="side-head">
                  <button className="side-toggle" aria-expanded={filesOpen} onClick={() => setFilesOpen(!filesOpen)}>
                    <IconChevron up={filesOpen} /> Project
                  </button>
                  <button className="icon" onClick={() => setProjectDialog(true)} title="Project settings" aria-label="Project settings">
                    <IconGear />
                  </button>
                </div>
                <div hidden={!filesOpen}>
                  <FileTree
                    projectId={project.id}
                    root={cfg.latex_dir}
                    extra={[cfg.results_dir, cfg.scripts_dir]}
                    label={project.name}
                    selected={view.kind === "build" ? (file?.path ?? null) : view.path}
                    onOpen={(entry: TreeEntry) => void openFile(entry.path)}
                    onError={onError}
                  />
                </div>
              </section>
              <section className="side-section">
                <div className="side-head">
                  <button className="side-toggle" aria-expanded={outlineOpen} onClick={() => setOutlineOpen(!outlineOpen)}>
                    <IconChevron up={outlineOpen} /> Document outline
                  </button>
                </div>
                <nav className="outline" aria-label="Document outline" hidden={!outlineOpen}>
                  {outline.length === 0 && <div className="muted small" style={{ padding: "2px 14px" }}>No headings.</div>}
                  {outline.map((item) => (
                    <button key={`${item.line}-${item.title}`} className={`level-${item.level}`} onClick={() => editor.current?.goto(item.line)} title={item.title}>
                      {item.title}
                    </button>
                  ))}
                </nav>
              </section>
            </div>
            <Splitter axis="x" label="Drag to change the width of the files and outline" onMoved={(d) => setSideWidth(clamp(sideWidth + d, 160, 520))} />
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
                onClick={() => !t.later && setTab(t.id as TabId)}
              >
                {t.label}
                {t.id === "changes" && waitingEdits > 0 ? ` (${waitingEdits})` : ""}
              </button>
            ))}
          </nav>
          {tab === "comments" && (
            <CommentsView
              projectId={project.id}
              active={overview?.active ?? null}
              build={pdf}
              buildTitle={pdfTitle}
              onError={onError}
            />
          )}
          {tab === "changes" && (
            <ChangesView
              projectId={project.id}
              chatId={agentChat}
              prepare={save}
              onWritten={editsAccepted}
              onCount={setWaitingEdits}
              onError={onError}
            />
          )}
          {tab === "tracked" && (
            <TrackedView projectId={project.id} overview={overview} onUpdate={updateRevision} onError={onError} />
          )}
          <div
            className={`panes ${showEditor ? "" : "no-editor"} ${showPdf ? "" : "no-pdf"}`}
            hidden={tab !== "write"}
            ref={panes}
            style={{ gridTemplateColumns: bothPanes ? `minmax(0, ${editorShare}fr) minmax(0, ${1 - editorShare}fr)` : "minmax(0, 1fr)" }}
          >
            <div className="pane">
              <div className="pane-head">
                <span className="pane-title">{file ? basename(file.path) : "No file open"}</span>
                {dirty && <span className="dirty">Edited</span>}
                <span className="spacer" />
                {file && (
                  <button className="link small" onClick={() => setSymbolsOpen(!symbolsOpen)} title="Greek letters, operators, arrows and units">
                    Symbols
                  </button>
                )}
                {file && (
                  <button className="link small" onClick={() => setHistoryOpen(true)}>
                    History
                  </button>
                )}
                {baseline !== null && (
                  <label className="check small" title={`Marks what changed since revision ${active} started`}>
                    <input
                      type="checkbox"
                      checked={showChanges}
                      onChange={(e) => setShowChanges(e.target.checked)}
                    />
                    Changes{showChanges && changeCount !== null ? ` (${changeCount})` : ""}
                  </label>
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
              {symbolsOpen && file && <SymbolPalette onInsert={(text) => editor.current?.insert(text)} />}
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
                    baseline={showChanges ? baseline : null}
                    onTrackedCount={setChangeCount}
                    onSelectionLines={setSelectedLines}
                    onAddSelection={() => void addSelection()}
                    aids={() => aidData.current}
                    styleRules={() => styleRules.current}
                    keys={editorKeys}
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
                title={view.kind === "pdf" ? view.path : showTracked ? "revision_tracked.pdf" : pdfTitle}
                data={view.kind === "pdf" ? view.data : showTracked ? trackedPdf : (pdf?.data ?? null)}
                version={view.kind === "pdf" ? view.path : showTracked ? `tracked-${trackedVersion}` : (pdf?.version ?? "")}
                highlight={view.kind === "build" && !showTracked ? highlight : null}
                onDoubleClick={view.kind === "build" && !showTracked ? (p, x, y) => void syncToSource(p, x, y) : undefined}
                onSyncFromCursor={view.kind === "build" && !showTracked ? () => void syncToPdf() : undefined}
                placeholder={showTracked ? (updating || trackedPreview ? "Getting the tracked PDF ready..." : "The tracked PDF appears after the revision's first update.") : undefined}
                extraHead={
                  view.kind === "pdf" ? (
                    <button className="link small" onClick={backToBuild}>
                      Back to {pdfTitle}
                    </button>
                  ) : active ? (
                    <span className="segmented" role="group" aria-label="Which PDF">
                      <button className={showTracked ? "" : "on"} onClick={() => setPdfMode("clean")}>
                        Clean
                      </button>
                      <button
                        className={showTracked ? "on" : ""}
                        onClick={() => setPdfMode("tracked")}
                        title="The tracked PDF, with what changed since the revision started marked. It follows each compile within seconds."
                      >
                        Tracked
                      </button>
                      {showTracked && updating && <span className="muted small">updating...</span>}
                    </span>
                  ) : undefined
                }
              />
            )}
            {bothPanes && (
              <Splitter
                axis="x"
                label="Drag to change the sizes of the editor and the PDF"
                style={{ left: `calc(${editorShare * 100}% - 3px)` }}
                onMoved={(d) => setEditorShare(clamp(editorShare + d / (panes.current?.clientWidth || 1000), 0.15, 0.85))}
              />
            )}
          </div>
        </div>
        <AgentPanel
          hidden={!agentOpen}
          waiting={waitingEdits}
          items={contextItems}
          onRemoveItem={(id) => setContextItems((items) => items.filter((i) => i.id !== id))}
          onClearItems={() => setContextItems([])}
          onJumpToItem={(item) => void openFile(item.file, item.from)}
          dock={agentDock}
          onDock={setAgentDock}
          onHide={() => setAgentOpen(false)}
          splitter={
            agentDock === "right" ? (
              <Splitter axis="x" label="Drag to change the width of the agent" onMoved={(d) => setAgentWidth(clamp(agentWidth - d, 260, 800))} />
            ) : (
              <Splitter axis="y" label="Drag to change the height of the agent" onMoved={(d) => setAgentHeight(clamp(agentHeight - d, 140, 700))} />
            )
          }
          projectId={project.id}
          prepare={save}
          context={agentContext}
          openFile={file?.path ?? null}
          selectedLines={selectedLines}
          request={agentRequest}
          focusToken={agentFocus}
          onChatChanged={setAgentChat}
          onTurnDone={refreshWaiting}
          onReview={() => setTab("changes")}
          onSettings={onSettings}
          onError={onError}
        />
      </div>

      <ProblemsDrawer
        onFixWithAgent={fixWithAgent}
        projectId={project.id}
        build={build}
        open={drawerOpen}
        onToggle={() => setDrawerOpen((o) => !o)}
        onJump={(f, line) => void openFile(joinPath(cfg.latex_dir, f), line)}
        onCompileClean={() => void compile(true)}
        compiling={compiling}
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

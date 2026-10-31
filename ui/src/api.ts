// Typed calls to the backend. Shapes mirror backend/thor/*.py.

export interface Check {
  id: string;
  name: string;
  group: string;
  needed_for: string;
  required: boolean;
  ok: boolean;
  version: string | null;
  path: string | null;
  detail: string;
  fix: string | null;
}

export interface DepsReport {
  checks: Check[];
  summary: { ok: boolean; missing: string[] };
}

export interface Endpoint {
  id: string;
  name: string;
  base_url: string;
  model: string;
}

export interface CommandProvider {
  id: string;
  name: string;
  command: string;
  edits_files: boolean;
  sandboxed: boolean;
}

export interface AgentSettings {
  provider: string;
  model: string;
  codex_model: string;
  openai_model: string;
  ollama_model: string;
  lmstudio_model: string;
  local_keep_loaded: boolean;
  effort: string;
  budget_usd: number;
  claude_path: string;
  codex_path: string;
  endpoints: Endpoint[];
  commands: CommandProvider[];
}

export interface SettingsResponse {
  agent: AgentSettings;
  models: { id: string; name: string }[];
  efforts: string[];
  api_key_saved: boolean;
}

export interface Provider {
  id: string;
  kind: "cli" | "api" | "local" | "endpoint" | "command";
  name: string;
  billing: string;
  model: string;
  models: { id: string; name: string }[];
  ready: boolean;
  detail: string;
  fix?: string | null;
  path?: string | null;
  version?: string | null;
  has_key?: boolean;
  placeholder?: string;
  base_url?: string;
  command?: string;
  edits_files?: boolean;
}

/** A passage the user added to the next message from the editor. */
export interface ContextItem {
  id: string;
  file: string;
  from: number;
  to: number;
  text: string;
}

export interface EditorContext {
  file?: string;
  line?: number;
  selection?: { from: number; to: number; text: string } | null;
  selections?: ContextItem[];
  build?: string;
  problems?: string[];
}

export interface AidData {
  labels: { key: string; file: string; line: number; near: string }[];
  cites: { key: string; title: string; authors: string; year: string }[];
  images: string[];
  tex: string[];
  bibs: string[];
  macros: Record<string, string>;
}

export interface StyleRule {
  pattern: string;
  message: string;
  flags: string;
}

export interface SearchResults {
  files: { path: string; hits: { line: number; text: string; from: number; to: number }[] }[];
  matches: number;
  truncated: boolean;
}

export interface AgentEvent {
  seq: number;
  time: number;
  type: "user" | "text" | "activity" | "activity_done" | "approval" | "approval_done" | "notice" | "usage" | "compile"
    | "changes" | "error" | "done";
  text?: string;
  id?: string;
  summary?: string;
  detail?: string;
  ok?: boolean;
  allowed?: boolean;
  build?: string;
  context?: EditorContext | null;
  provider_name?: string;
  model?: string;
  input_tokens?: number;
  output_tokens?: number;
  cost_usd?: number | null;
  files?: number;
  hunks?: number;
  additions?: number;
  deletions?: number;
}

export interface ChatSummary {
  id: string;
  title: string;
  updated: number;
  provider: string | null;
  running: boolean;
}

export interface Chat {
  id: string;
  title: string;
  provider: string | null;
  model: string | null;
  events: AgentEvent[];
  running: boolean;
}

export interface Hunk {
  id: string;
  old_start: number;
  new_start: number;
  old: string[];
  new: string[];
  before: string[];
  after: string[];
  /** Why someone should read this one; it is left out of "accept all". */
  flags: string[];
}

export interface FileChange {
  path: string;
  kind: "edit" | "new" | "deleted" | "binary";
  hunks: Hunk[];
  additions: number;
  deletions: number;
  refused: string;
}

export interface WaitingChanges {
  files: FileChange[];
  running: boolean;
  build: { id: string; ok: boolean; summary: string; pdf: boolean } | null;
  written?: string[];
}

export interface ProjectConfig {
  name: string;
  latex_dir: string;
  main: string;
  revisions_dir: string;
  results_dir: string;
  scripts_dir: string;
  style_rules: string;
  standing_instructions: string;
  engine: string;
  toolchain: string;
  si_marker: string;
  line_numbers: boolean;
  figure_macro: string;
  figure_folders: string[];
  bib_style: string;
  bib_filecontents: boolean;
  bib_extra_lists: string[];
}

export interface Revision {
  number: number;
  path: string;
  pdfs: string[];
  missing: string[];
  supporting_files: boolean;
}

export interface Project {
  id: string;
  root: string;
  name: string;
  config: ProjectConfig;
  detected: ProjectConfig;
  config_saved: boolean;
  main_candidates: string[];
  notes: string[];
  revisions: Revision[];
}

export interface RecentProject {
  id: string;
  root: string;
  name: string;
  last_opened: number;
  exists: boolean;
}

export interface TreeEntry {
  name: string;
  path: string;
  dir: boolean;
  size: number | null;
  kind: "folder" | "tex" | "bib" | "pdf" | "image" | "script" | "other";
}

export interface Listing {
  path: string;
  entries: TreeEntry[];
  truncated: boolean;
}

export interface TexSetupStatus {
  step: "idle" | "finding" | "downloading" | "verifying" | "installing" | "done" | "failed" | "cancelled";
  message: string;
  downloaded: number;
  total: number | null;
  package: string | null;
  installed: boolean;
  running: boolean;
}

export interface SetupStep {
  id: "claude" | "mactex";
  name: string;
  state: "waiting" | "running" | "done" | "failed";
  message: string;
}

export interface SetupStatus {
  enabled: boolean;
  running: boolean;
  finished: boolean;
  failed: boolean;
  steps: SetupStep[];
  tex: TexSetupStatus;
}

export interface Problem {
  kind: "error" | "undefined_reference" | "undefined_citation" | "overfull" | "warning";
  message: string;
  /** Relative to the LaTeX folder; null for files outside the project. */
  file: string | null;
  line: number | null;
  detail: string;
}

export interface BuildResult {
  id: string;
  ok: boolean;
  exit_code: number;
  seconds: number;
  engine: string;
  main: string;
  pdf: boolean;
  pages: number;
  si_start: number | null;
  problems: Problem[];
  counts: Record<Problem["kind"], number>;
  summary: string;
  output_tail: string;
  passes: number;
  warm: boolean;
  /** True when this build queued an update of the active revision folder. */
  revision_update?: boolean;
}

export interface SyncStep {
  name: string;
  ok: boolean;
  detail: string;
}

export interface TrackedReport {
  ok: boolean;
  pages: number;
  problems: Problem[];
  counts: Record<Problem["kind"], number>;
  tables: string[];
  figures: Record<string, number>;
  additions: number;
  deletions: number;
  summary: string;
}

export interface SyncStatus {
  revision: number | null;
  state: "idle" | "running" | "done" | "failed";
  started: number | null;
  finished: number | null;
  steps: SyncStep[];
  tracked: TrackedReport | null;
  /** The tracked PDF from an incremental compile, which appears before the revision's own files are made. */
  preview: { id: string; ok: boolean; pages: number; seconds: number; summary: string } | null;
  summary: string;
}

export interface RevisionOverview {
  revisions: number[];
  latest: number | null;
  active: number | null;
  baseline: string | null;
  sync: SyncStatus;
}

export interface CommentItem {
  id: number;
  kind: string;
  author: string;
  person: string;
  page: number | null;
  line: number | null;
  line_end: number | null;
  element: string | null;
  marked: string;
  note: string;
  insert: string;
  anchor: string;
  marks: { kind: string; text: string; line: number | null; rect: number[] }[];
  replies: { author: string; note: string }[];
  rects: number[][];
  context: string;
  baseline: { status: "same" | "moved" | "not found" | "unchecked"; page: number | null; line: number | null } | null;
  question: string | null;
  answer: string | null;
}

export interface CommentSet {
  name: string;
  source_pdf: string;
  imported: string;
  baseline: string;
  aliases: Record<string, string>;
  comments: CommentItem[];
  persons: Record<string, number>;
}

export interface CommentSetSummary {
  name: string;
  source_pdf: string;
  imported: string;
  count: number;
  persons: Record<string, number>;
  questions: number;
}

export interface Version {
  id: number;
  hash: string;
  time: number;
  label: string | null;
}

export interface DiffLine {
  kind: "hunk" | "same" | "removed" | "added";
  text: string;
}

export interface OpenedFile {
  path: string;
  text: string;
  hash: string;
}

export interface SyncBox {
  page: number;
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface WordCount {
  ok: boolean;
  text?: number;
  headers?: number;
  captions?: number;
  detail?: string;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly data: unknown = null,
  ) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let message = res.statusText;
    let data: unknown = null;
    try {
      data = await res.json();
      const detail = (data as { detail?: unknown }).detail;
      message = typeof detail === "string" ? detail : JSON.stringify(detail);
    } catch {
      // Not JSON; keep the status text.
    }
    throw new ApiError(res.status, message, data);
  }
  return res.json() as Promise<T>;
}

export const api = {
  deps: () => request<DepsReport>("GET", "/api/deps"),
  settings: () => request<SettingsResponse>("GET", "/api/settings"),
  saveSettings: (agent: AgentSettings) => request<SettingsResponse>("PUT", "/api/settings", { agent }),
  saveApiKey: (key: string) => request<{ api_key_saved: boolean }>("PUT", "/api/settings/api-key", { key }),
  deleteApiKey: () => request<{ api_key_saved: boolean }>("DELETE", "/api/settings/api-key"),
  layout: () => request<Record<string, boolean | number | string>>("GET", "/api/settings/layout"),
  saveLayout: (changes: Record<string, boolean | number | string>) =>
    request<Record<string, boolean | number | string>>("PUT", "/api/settings/layout", changes),
  providers: () => request<{ current: string; providers: Provider[] }>("GET", "/api/agent/providers"),
  providerModels: (provider: string) =>
    request<{ models: { id: string; name: string }[] }>("GET", `/api/agent/providers/${encodeURIComponent(provider)}/models`),
  saveKey: (provider: string, key: string) =>
    request<{ saved: boolean }>("PUT", `/api/agent/keys/${encodeURIComponent(provider)}`, { key }),
  deleteKey: (provider: string) => request<{ saved: boolean }>("DELETE", `/api/agent/keys/${encodeURIComponent(provider)}`),
  chats: (id: string) => request<{ chats: ChatSummary[] }>("GET", `/api/projects/${id}/agent/chats`),
  newChat: (id: string) => request<Chat>("POST", `/api/projects/${id}/agent/chats`),
  chat: (id: string, chat: string) => request<Chat>("GET", `/api/projects/${id}/agent/chats/${chat}`),
  deleteChat: (id: string, chat: string) => request<{ ok: boolean }>("DELETE", `/api/projects/${id}/agent/chats/${chat}`),
  sendMessage: (id: string, chat: string, text: string, context: EditorContext | null, provider: string, model: string | null, fast = false) =>
    request<{ ok: boolean }>("POST", `/api/projects/${id}/agent/chats/${chat}/messages`, { text, context, provider, model, fast }),
  warmProvider: (provider: string) => request<{ ok: boolean }>("POST", `/api/agent/providers/${encodeURIComponent(provider)}/warm`),
  stopChat: (id: string, chat: string) => request<{ ok: boolean }>("POST", `/api/projects/${id}/agent/chats/${chat}/stop`),
  decide: (id: string, chat: string, approval: string, allow: boolean) =>
    request<{ ok: boolean }>("POST", `/api/projects/${id}/agent/chats/${chat}/approvals/${approval}`, { allow }),
  chatEventsUrl: (id: string, chat: string, after: number) => `/api/projects/${id}/agent/chats/${chat}/events?after=${after}`,
  changes: (id: string, chat: string) => request<WaitingChanges>("GET", `/api/projects/${id}/agent/chats/${chat}/changes`),
  acceptChanges: (id: string, chat: string, path?: string, hunk?: string) =>
    request<WaitingChanges>("POST", `/api/projects/${id}/agent/chats/${chat}/changes/accept`, { path, hunk }),
  rejectChanges: (id: string, chat: string, path?: string, hunk?: string) =>
    request<WaitingChanges>("POST", `/api/projects/${id}/agent/chats/${chat}/changes/reject`, { path, hunk }),
  chatPdfUrl: (id: string, chat: string) => `/api/projects/${id}/agent/chats/${chat}/pdf`,
  buildProgress: (id: string) => request<{ text: string }>("GET", `/api/projects/${id}/build/progress`),
  setup: () => request<SetupStatus>("GET", "/api/setup"),
  retrySetup: () => request<SetupStatus>("POST", "/api/setup/start"),
  recent: () => request<{ projects: RecentProject[] }>("GET", "/api/projects/recent"),
  open: (path: string, main?: string) => request<Project>("POST", "/api/projects/open", { path, main }),
  project: (id: string) => request<Project>("GET", `/api/projects/${id}`),
  forget: (id: string) => request<{ ok: boolean }>("DELETE", `/api/projects/${id}`),
  saveConfig: (id: string, config: ProjectConfig) => request<Project>("PUT", `/api/projects/${id}/config`, config),
  tree: (id: string, path: string) =>
    request<Listing>("GET", `/api/projects/${id}/tree?path=${encodeURIComponent(path)}`),
  file: (id: string, path: string) =>
    request<OpenedFile>("GET", `/api/projects/${id}/file?path=${encodeURIComponent(path)}`),
  fileHash: (id: string, path: string) =>
    request<{ path: string; hash: string | null }>("GET", `/api/projects/${id}/file/hash?path=${encodeURIComponent(path)}`),
  /** Throws ApiError 409 with data.current_hash when the file changed on disk. */
  saveFile: (id: string, path: string, text: string, baseHash: string | null) =>
    request<{ path: string; hash: string }>("PUT", `/api/projects/${id}/file`, { path, text, base_hash: baseHash }),
  rawUrl: (id: string, path: string) => `/api/projects/${id}/raw?path=${encodeURIComponent(path)}`,
  build: (id: string, clean = false) =>
    request<BuildResult>("POST", `/api/projects/${id}/build${clean ? "?clean=true" : ""}`),
  latestBuild: (id: string) => request<BuildResult | null>("GET", `/api/projects/${id}/build`),
  buildFileUrl: (id: string, buildId: string, name: string) =>
    `/api/projects/${id}/builds/${encodeURIComponent(buildId)}/${encodeURIComponent(name)}`,
  syncForward: (id: string, file: string, line: number, column = 0) =>
    request<{ boxes: SyncBox[] }>("POST", `/api/projects/${id}/synctex/forward`, { file, line, column }),
  syncBackward: (id: string, page: number, x: number, y: number) =>
    request<{ file: string; line: number }>("POST", `/api/projects/${id}/synctex/backward`, { page, x, y }),
  wordCount: (id: string) => request<WordCount>("GET", `/api/projects/${id}/wordcount`),
  revision: (id: string) => request<RevisionOverview>("GET", `/api/projects/${id}/revision`),
  startRevision: (id: string) => request<RevisionOverview>("POST", `/api/projects/${id}/revision/start`),
  continueRevision: (id: string) => request<RevisionOverview>("POST", `/api/projects/${id}/revision/continue`),
  updateRevision: (id: string) => request<RevisionOverview>("POST", `/api/projects/${id}/revision/update`),
  revisionFileUrl: (id: string, name: string) => `/api/projects/${id}/revision/files/${name}`,
  trackedPreviewUrl: (id: string) => `/api/projects/${id}/revision/tracked-preview`,
  aids: (id: string) => request<AidData>("GET", `/api/projects/${id}/aids`),
  styleRules: (id: string) => request<{ rules: StyleRule[] }>("GET", `/api/projects/${id}/style-rules`),
  search: (id: string, q: string, regex: boolean, caseSensitive: boolean, glob = "") =>
    request<SearchResults>("GET", `/api/projects/${id}/search?${new URLSearchParams({ q, regex: String(regex), case: String(caseSensitive), glob })}`),
  replace: (id: string, query: string, replacement: string, regex: boolean, caseSensitive: boolean, paths: string[]) =>
    request<{ changed: string[]; replacements: number }>("POST", `/api/projects/${id}/replace`, { query, replacement, regex, case: caseSensitive, paths }),
  revisionBaseline: (id: string, path: string) =>
    request<{ path: string; text: string }>("GET", `/api/projects/${id}/revision/baseline?path=${encodeURIComponent(path)}`),
  commentSets: (id: string) =>
    request<{ active: number | null; sets: CommentSetSummary[] }>("GET", `/api/projects/${id}/comments`),
  importComments: (id: string, path: string, email: string, name: string) =>
    request<CommentSet>("POST", `/api/projects/${id}/comments/import`, { path, email, name }),
  commentSet: (id: string, name: string) => request<CommentSet>("GET", `/api/projects/${id}/comments/${name}`),
  setAliases: (id: string, name: string, aliases: Record<string, string>) =>
    request<CommentSet>("PUT", `/api/projects/${id}/comments/${name}/aliases`, { aliases }),
  answerComment: (id: string, name: string, commentId: number, answer: string) =>
    request<CommentSet>("PUT", `/api/projects/${id}/comments/${name}/answer`, { id: commentId, answer }),
  annotatedPdfUrl: (id: string, name: string) => `/api/projects/${id}/comments/${name}/pdf`,
  commentNow: (id: string, name: string, commentId: number) =>
    request<{ boxes: SyncBox[]; build: string | null }>("GET", `/api/projects/${id}/comments/${name}/${commentId}/now`),
  history: (id: string, path: string) =>
    request<{ path: string; versions: Version[] }>("GET", `/api/projects/${id}/history?path=${encodeURIComponent(path)}`),
  version: (id: string, versionId: number) =>
    request<{ path: string; text: string; diff: DiffLine[] }>("GET", `/api/projects/${id}/history/${versionId}`),
};

/** The bytes of a file the backend serves, for PDF.js. */
export async function fetchBytes(url: string): Promise<ArrayBuffer> {
  const res = await fetch(url, { credentials: "same-origin", cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  return res.arrayBuffer();
}

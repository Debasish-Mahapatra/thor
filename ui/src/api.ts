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

export interface AgentSettings {
  auth: "claude_plan" | "api_key";
  model: string;
  effort: string;
  budget_usd: number;
  claude_path: string;
}

export interface SettingsResponse {
  agent: AgentSettings;
  models: { id: string; name: string }[];
  efforts: string[];
  api_key_saved: boolean;
}

export interface ClaudeStatus {
  path: string | null;
  version: string | null;
  logged_in: boolean | null;
  auth_method: string | null;
  email: string | null;
  plan: string | null;
  detail: string;
}

export interface AgentStatus {
  auth: AgentSettings["auth"];
  ready: boolean;
  claude: ClaudeStatus | null;
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
  summary: string;
}

export interface RevisionOverview {
  revisions: number[];
  latest: number | null;
  active: number | null;
  baseline: string | null;
  sync: SyncStatus;
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
  agentStatus: () => request<AgentStatus>("GET", "/api/agent/status"),
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
  build: (id: string) => request<BuildResult>("POST", `/api/projects/${id}/build`),
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

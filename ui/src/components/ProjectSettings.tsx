import { useEffect, useState, type ReactNode } from "react";
import { api, type Project, type ProjectConfig } from "../api";

interface Props {
  project: Project;
  onSaved: (project: Project) => void;
  onError: (e: unknown) => void;
}

type TextKey = {
  [K in keyof ProjectConfig]: ProjectConfig[K] extends string ? K : never;
}[keyof ProjectConfig];

function sameConfig(a: ProjectConfig, b: ProjectConfig): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** A list of names typed as "a, b"; parsed when the field loses focus. */
function ListInput(props: { value: string[]; onChange: (v: string[]) => void; id: string }) {
  const [text, setText] = useState(props.value.join(", "));
  useEffect(() => setText(props.value.join(", ")), [props.value]);
  return (
    <input
      id={props.id}
      value={text}
      spellCheck={false}
      onChange={(e) => setText(e.target.value)}
      onBlur={() =>
        props.onChange(
          text
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
        )
      }
    />
  );
}

export function ProjectSettings({ project, onSaved, onError }: Props) {
  const [draft, setDraft] = useState<ProjectConfig>(project.config);
  const [saving, setSaving] = useState(false);
  const [savedNote, setSavedNote] = useState(false);
  useEffect(() => {
    setDraft(project.config);
    setSavedNote(false);
  }, [project]);

  const set = <K extends keyof ProjectConfig>(key: K, value: ProjectConfig[K]) => {
    setDraft((d) => ({ ...d, [key]: value }));
    setSavedNote(false);
  };
  const dirty = !sameConfig(draft, project.config);
  const detected = project.detected;

  const hint = (key: keyof ProjectConfig): ReactNode => {
    const value = detected[key];
    if (JSON.stringify(value) === JSON.stringify(draft[key])) return null;
    const shown = Array.isArray(value) ? value.join(", ") : String(value);
    return <span className="hint">Detected: {shown || "(none)"}</span>;
  };

  const field = (key: keyof ProjectConfig, label: string, control: ReactNode, help?: string) => (
    <div className="field" key={key}>
      <label htmlFor={`cfg-${key}`}>{label}</label>
      <div>
        {control}
        {hint(key)}
        {help && <span className="help">{help}</span>}
      </div>
    </div>
  );

  const text = (key: TextKey, label: string, help?: string, placeholder?: string) =>
    field(
      key,
      label,
      <input
        id={`cfg-${key}`}
        value={draft[key]}
        placeholder={placeholder}
        spellCheck={false}
        onChange={(e) => set(key, e.target.value)}
      />,
      help,
    );

  const mainPath = draft.latex_dir === "." ? draft.main : `${draft.latex_dir}/${draft.main}`;
  const candidates = project.main_candidates.includes(mainPath)
    ? project.main_candidates
    : [mainPath, ...project.main_candidates];

  const save = async () => {
    setSaving(true);
    try {
      onSaved(await api.saveConfig(project.id, draft));
      setSavedNote(true);
    } catch (e) {
      onError(e);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="card settings-form">
      <div className="row-between">
        <h2>Project settings</h2>
        <div className="row">
          {dirty && (
            <button className="ghost" onClick={() => setDraft(project.config)}>
              Revert
            </button>
          )}
          <button className="primary" onClick={save} disabled={saving || (!dirty && project.config_saved)}>
            {saving ? "Saving..." : "Save to revision_app.toml"}
          </button>
        </div>
      </div>
      <p className="muted">
        {savedNote
          ? "Saved."
          : project.config_saved
            ? "Read from revision_app.toml in the project folder."
            : "Detected from the source. Nothing is written until you save."}
      </p>

      <h3>Project</h3>
      {text("name", "Name")}
      {field(
        "main",
        "Main file",
        <select
          id="cfg-main"
          value={mainPath}
          onChange={(e) => {
            const path = e.target.value;
            const slash = path.lastIndexOf("/");
            setDraft((d) => ({
              ...d,
              latex_dir: slash < 0 ? "." : path.slice(0, slash),
              main: path.slice(slash + 1),
            }));
          }}
        >
          {candidates.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>,
      )}
      {text("revisions_dir", "Revision folders", "Where revision1/, revision2/, ... live. A dot is the project folder.")}
      {text("results_dir", "Results", "Every number in the text must come from files here.")}
      {text("scripts_dir", "Scripts", "Scripts that make the figures and tables.")}
      {text("style_rules", "Style rules", "A file with your writing and style rules.", "style_rules.md")}
      {text("standing_instructions", "Instructions for the agent", "For example CLAUDE.md.")}

      <h3>Build</h3>
      {field(
        "engine",
        "Engine",
        <select id="cfg-engine" value={draft.engine} onChange={(e) => set("engine", e.target.value)}>
          <option value="pdflatex">pdfLaTeX</option>
          <option value="xelatex">XeLaTeX</option>
          <option value="lualatex">LuaLaTeX</option>
        </select>,
      )}
      {field(
        "toolchain",
        "TeX installation",
        <select id="cfg-toolchain" value={draft.toolchain} onChange={(e) => set("toolchain", e.target.value)}>
          <option value="local">This Mac (MacTeX)</option>
          <option value="remote">Remote machine over ssh</option>
        </select>,
      )}
      {text("si_marker", "Supplementary Information starts at", "The PDF is split at the first page starting with this text.")}
      {field(
        "line_numbers",
        "Line numbers",
        <label className="check">
          <input
            id="cfg-line_numbers"
            type="checkbox"
            checked={draft.line_numbers}
            onChange={(e) => set("line_numbers", e.target.checked)}
          />
          The source uses the lineno package
        </label>,
      )}

      <h3>Figures</h3>
      {text("figure_macro", "Include macro", "Without the backslash, e.g. figfile for \\figfile{opts}{name}.")}
      {field(
        "figure_folders",
        "Figure folders",
        <ListInput id="cfg-figure_folders" value={draft.figure_folders} onChange={(v) => set("figure_folders", v)} />,
        "Comma-separated, inside the LaTeX folder.",
      )}

      <h3>Bibliography</h3>
      {field(
        "bib_style",
        "Style",
        <select id="cfg-bib_style" value={draft.bib_style} onChange={(e) => set("bib_style", e.target.value)}>
          <option value="natbib">natbib</option>
          <option value="biblatex">biblatex</option>
          <option value="bibtex">plain BibTeX</option>
          <option value="">None</option>
        </select>,
      )}
      {field(
        "bib_filecontents",
        "Bibliography source",
        <label className="check">
          <input
            id="cfg-bib_filecontents"
            type="checkbox"
            checked={draft.bib_filecontents}
            onChange={(e) => set("bib_filecontents", e.target.checked)}
          />
          Written by a filecontents block in the main file
        </label>,
      )}
      {field(
        "bib_extra_lists",
        "Extra reference lists",
        <ListInput id="cfg-bib_extra_lists" value={draft.bib_extra_lists} onChange={(v) => set("bib_extra_lists", v)} />,
        "multibib lists from \\newcites, e.g. S.",
      )}
    </section>
  );
}

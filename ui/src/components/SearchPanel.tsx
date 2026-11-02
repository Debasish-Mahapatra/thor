import { useEffect, useRef, useState } from "react";
import { ApiError, api, type SearchResults } from "../api";
import { IconSearch } from "../icons";

interface Props {
  projectId: string;
  focusToken: number;
  /** Saves what the editor holds, so search and replace see the latest text. */
  prepare: () => Promise<boolean>;
  onOpen: (path: string, line: number) => void;
  onReplaced: (paths: string[]) => void;
  onError: (e: unknown) => void;
}

/** Search in the top bar. The panel stays mounted while it is closed, so its results are there when it opens again. */
export function SearchMenu({ focusToken, ...panel }: Props) {
  const [open, setOpen] = useState(false);
  const [focus, setFocus] = useState(0);
  const box = useRef<HTMLDivElement>(null);

  const show = () => {
    setOpen(true);
    setFocus((n) => n + 1);
  };

  useEffect(() => {
    if (focusToken) show();
  }, [focusToken]);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    const escape = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", escape);
    };
  }, [open]);

  return (
    <div className="search-menu" ref={box}>
      <button className={`icon ${open ? "on" : ""}`} onClick={() => (open ? setOpen(false) : show())} aria-expanded={open} title="Search all files (⌘⇧F)" aria-label="Search all files">
        <IconSearch />
      </button>
      <div className="popover" role="dialog" aria-label="Search all files" hidden={!open}>
        <SearchPanel {...panel} focusToken={focus} />
      </div>
    </div>
  );
}

function SearchPanel({ projectId, focusToken, prepare, onOpen, onReplaced, onError }: Props) {
  const [query, setQuery] = useState("");
  const [replacement, setReplacement] = useState("");
  const [regex, setRegex] = useState(false);
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [results, setResults] = useState<SearchResults | null>(null);
  const [problem, setProblem] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [note, setNote] = useState("");
  const box = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (focusToken) box.current?.focus();
  }, [focusToken]);

  const run = async () => {
    setConfirm(false);
    setNote("");
    setProblem("");
    if (!query) {
      setResults(null);
      return;
    }
    if (!(await prepare())) return;
    try {
      setResults(await api.search(projectId, query, regex, caseSensitive));
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) setProblem(e.message);
      else onError(e);
    }
  };

  const replace = async () => {
    if (!results) return;
    if (!(await prepare())) return;
    try {
      const done = await api.replace(projectId, query, replacement, regex, caseSensitive, results.files.map((f) => f.path));
      setNote(`Replaced ${done.replacements} in ${done.changed.length} file${done.changed.length === 1 ? "" : "s"}. Each file's History has the old text.`);
      setResults(null);
      setConfirm(false);
      onReplaced(done.changed);
    } catch (e) {
      onError(e);
    }
  };

  return (
    <div className="search-panel">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <input ref={box} value={query} placeholder="Search all files" spellCheck={false} onChange={(e) => setQuery(e.target.value)} />
        <input value={replacement} placeholder="Replace with" spellCheck={false} onChange={(e) => setReplacement(e.target.value)} />
        <div className="row small">
          <label className="check">
            <input type="checkbox" checked={regex} onChange={(e) => setRegex(e.target.checked)} /> Regex
          </label>
          <label className="check">
            <input type="checkbox" checked={caseSensitive} onChange={(e) => setCaseSensitive(e.target.checked)} /> Case
          </label>
          <span className="spacer" />
          <button type="submit">Search</button>
        </div>
      </form>
      {problem && <div className="warn-text small">{problem}</div>}
      {note && <div className="muted small">{note}</div>}
      {results && (
        <div className="search-results">
          <div className="row-between small">
            <span className="muted">
              {results.matches === 0 ? "No matches." : `${results.matches}${results.truncated ? "+" : ""} match${results.matches === 1 ? "" : "es"} in ${results.files.length} file${results.files.length === 1 ? "" : "s"}`}
            </span>
            {results.matches > 0 &&
              (confirm ? (
                <button className="link small" onClick={() => void replace()}>
                  Yes, replace all
                </button>
              ) : (
                <button className="link small" onClick={() => setConfirm(true)}>
                  Replace all
                </button>
              ))}
          </div>
          {results.files.map((file) => (
            <div key={file.path}>
              <div className="search-file mono">{file.path}</div>
              {file.hits.map((hit, i) => (
                <button key={`${hit.line}-${i}`} className="search-hit" onClick={() => onOpen(file.path, hit.line)} title={`Line ${hit.line}`}>
                  <span className="muted">{hit.line}</span> {hit.text.slice(Math.max(0, hit.from - 24), hit.from)}
                  <mark>{hit.text.slice(hit.from, hit.to)}</mark>
                  {hit.text.slice(hit.to, hit.to + 60)}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

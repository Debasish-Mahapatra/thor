"""The scripts that make the paper's figures: which makes which, and running them.

The user's click runs a script where it is, as they would in Terminal: a button they pressed for their own program.
The agent's run_script asks first, and runs it on the agent's copy under the sandbox with no network, so what it makes
waits in Changes with the agent's other edits. The results folder is linked into the copy, and the sandbox keeps it
read-only.
"""

from __future__ import annotations

import os
import re
import shlex
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .aids import source_files
from .agent import sandbox
from .agent.staging import Staging
from .agent.tools import ToolError
from .paths import CANCELLED, app_home, clone_file, run_tool, tool_env, which
from .projects import ProjectConfig, ProjectError, inside

INTERPRETERS = {".py": "python3", ".r": "Rscript", ".jl": "julia", ".sh": "bash", ".ncl": "ncl", ".gs": "grads -blc",
                ".m": "octave --no-gui --quiet"}
MAX_SCRIPT_BYTES = 2_000_000
SCRIPT_SECONDS = 3600
LOG_TAIL = 20_000
IMAGE = r"\.(?:pdf|png|eps|svg|jpe?g|tiff?)"
OUTPUT_NAME = re.compile(rf"""['"]([^'"\n]*{IMAGE})['"]""", re.I)
INCLUDE = re.compile(r"\\includegraphics\*?(?:\[[^\]]*\])?\{([^{}#]+)\}")
WATCHED_SUFFIXES = {".pdf", ".png", ".eps", ".svg", ".jpg", ".jpeg", ".tif", ".tiff"}


class ScriptError(Exception):
    """Told to the user as it is."""


def figures_used(latex_dir: Path, cfg: ProjectConfig) -> list[str]:
    """The figure names the source asks for, in order, by \\includegraphics and by the paper's own macro."""
    macro = (re.compile(rf"\\{re.escape(cfg.figure_macro)}\{{[^{{}}]*\}}\{{([^{{}}#]+)\}}") if cfg.figure_macro else None)
    names: list[str] = []
    for path in source_files(latex_dir, {".tex"}):
        text = re.sub(r"(?<!\\)%.*", "", path.read_text(errors="replace"))
        names.extend(INCLUDE.findall(text))
        if macro:
            names.extend(macro.findall(text))
    return list(dict.fromkeys(n.strip() for n in names))


def find_figure(latex_dir: Path, cfg: ProjectConfig, name: str) -> Path | None:
    """The file a figure name means, looked for the way graphicx would."""
    plain = Path(name)
    folders = [Path("."), *map(Path, cfg.figure_folders)]
    for folder in folders:
        for candidate in [plain.name if folder != Path(".") else name, *(f"{name}{s}" for s in (".pdf", ".png", ".jpg", ".jpeg", ".eps"))]:
            found = latex_dir / folder / candidate
            if found.is_file():
                return found
    return None


def stem_of(name: str) -> str:
    return Path(name).stem if Path(name).suffix.lower() in WATCHED_SUFFIXES else Path(name).name


def scan(root: Path, cfg: ProjectConfig) -> dict:
    """Which script makes which figure, judged by the names in the scripts' text. A script that builds its file names
    from parts is not seen, so a figure with no script may still have one."""
    latex_dir = inside(root, cfg.latex_dir)
    used = figures_used(latex_dir, cfg)
    scripts: list[dict] = []
    if cfg.scripts_dir:
        try:
            folder = inside(root, cfg.scripts_dir)
        except ProjectError:
            folder = None
        for path in sorted(folder.rglob("*")) if folder and folder.is_dir() else []:
            rel = path.relative_to(folder)
            if any(p.startswith(".") for p in rel.parts) or not path.is_file() or path.suffix.lower() not in INTERPRETERS:
                continue
            scripts.append({"path": path.relative_to(root).as_posix(), "name": path.name, "command": shlex.join(command_for(path, cfg)),
                            "outputs": _outputs(path), "makes": [], "mtime": path.stat().st_mtime})
    figures = []
    for name in used:
        stem = stem_of(name)
        pattern = re.compile(rf"(?<![\w-]){re.escape(stem)}(?![\w-])")
        makers = [s for s in scripts if any(stem_of(o) == stem for o in s["outputs"]) or _mentions(root / s["path"], pattern)]
        found = find_figure(latex_dir, cfg, name)
        for script in makers:
            script["makes"].append(name)
        mtime = found.stat().st_mtime if found else None
        figures.append({"name": name, "file": found.relative_to(root).as_posix() if found else None,
                        "scripts": [s["path"] for s in makers],
                        "stale": [s["path"] for s in makers if mtime is not None and s["mtime"] > mtime]})
    for script in scripts:
        del script["mtime"]
    return {"scripts": scripts, "figures": figures, "has_folder": bool(cfg.scripts_dir)}


def _read(path: Path) -> str:
    return path.read_text(errors="replace") if path.stat().st_size <= MAX_SCRIPT_BYTES else ""


def _outputs(path: Path) -> list[str]:
    return sorted(set(OUTPUT_NAME.findall(_read(path))))


def _mentions(path: Path, pattern: re.Pattern) -> bool:
    return bool(pattern.search(_read(path)))


def command_for(script: Path, cfg: ProjectConfig) -> list[str]:
    """The project's own command for this kind of script if it has one, or the usual one."""
    suffix = script.suffix.lower()
    for line in cfg.script_commands:
        kind, _, command = line.partition("=")
        if kind.strip().lower() == suffix and command.strip():
            words = shlex.split(command)
            break
    else:
        words = shlex.split(INTERPRETERS.get(suffix, ""))
    if not words:
        raise ScriptError(f"{APP_HINT} {suffix} scripts.")
    has_slot = any("{script}" in w for w in words)
    return [w.replace("{script}", script.name) for w in words] + ([] if has_slot else [script.name])


APP_HINT = "Set a command in the project's settings (script_commands, such as .py=python3) to run"


def _state(folders: list[Path]) -> dict[str, tuple[int, int]]:
    state = {}
    for folder in folders:
        for current, dirs, names in os.walk(folder):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in names:
                path = Path(current) / name
                if path.suffix.lower() in WATCHED_SUFFIXES:
                    info = path.stat()
                    state[str(path)] = (info.st_size, info.st_mtime_ns)
    return state


@dataclass
class Job:
    script: str = ""
    state: str = "idle"  # idle, running, done, failed or stopped
    started: float = 0
    finished: float = 0
    code: int | None = None
    log: str = ""
    made: list[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class ScriptRunner:
    """One script at a time for each paper: figure scripts tend to use all of a machine's memory."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def status(self, project_id: str) -> dict:
        return self._jobs.get(project_id, Job()).to_dict()

    def stop(self, project_id: str) -> None:
        cancel = self._cancel.get(project_id)
        if cancel:
            cancel.set()

    def start(self, root: Path, cfg: ProjectConfig, project_id: str, rel: str) -> dict:
        script = self._script(root, cfg, rel)
        args = command_for(script, cfg)
        with self._lock:
            if self._jobs.get(project_id, Job()).state == "running":
                raise ScriptError(f"{self._jobs[project_id].script} is still running.")
            job = Job(script=rel, state="running", started=time.time())
            self._jobs[project_id] = job
            self._cancel[project_id] = threading.Event()
        threading.Thread(target=self._run, args=(root, cfg, project_id, job, script, args), daemon=True).start()
        return job.to_dict()

    @staticmethod
    def _script(root: Path, cfg: ProjectConfig, rel: str) -> Path:
        if not cfg.scripts_dir:
            raise ScriptError("This paper has no scripts folder set. Set one in the project's settings.")
        folder = inside(root, cfg.scripts_dir).resolve()
        script = inside(root, rel).resolve()
        if folder not in script.parents or not script.is_file():
            raise ScriptError(f"{rel} is not a script in the scripts folder.")
        return script

    def _run(self, root: Path, cfg: ProjectConfig, project_id: str, job: Job, script: Path, args: list[str]) -> None:
        latex_dir = inside(root, cfg.latex_dir)
        watched = [latex_dir, *([inside(root, cfg.results_dir)] if cfg.results_dir else [])]
        before = _state(watched)
        if not which(args[0]) and not Path(args[0]).is_file():
            job.state, job.detail, job.finished = "failed", f"{args[0]} was not found. {APP_HINT} {script.suffix} scripts.", time.time()
            return
        code, output = run_tool(args, timeout=SCRIPT_SECONDS, cwd=script.parent, cancel=self._cancel[project_id])
        job.code, job.finished = code, time.time()
        job.log = output[-LOG_TAIL:]
        logs = app_home() / "script-logs" / project_id
        logs.mkdir(parents=True, exist_ok=True)
        (logs / f"{script.stem}.log").write_text(output)
        after = _state(watched)
        job.made = sorted(Path(p).relative_to(root).as_posix() for p, stamp in after.items() if before.get(p) != stamp)
        job.state = "stopped" if code == CANCELLED else "done" if code == 0 else "failed"
        if code not in (0, CANCELLED):
            job.detail = f"It stopped with exit code {code}."


def mirror_scripts(staging: Staging, root: Path, cfg: ProjectConfig) -> None:
    """The agent's copy gets the scripts, as clones it may change, and the results, as a link it can only read."""
    scripts_dir = inside(root, cfg.scripts_dir)
    target = staging.work_root / cfg.scripts_dir
    for current, dirs, names in os.walk(scripts_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in names:
            if name.startswith("."):
                continue
            source = Path(current) / name
            copy = target / source.relative_to(scripts_dir)
            copy.parent.mkdir(parents=True, exist_ok=True)
            clone_file(source, copy)
    if cfg.results_dir:
        link = staging.work_root / cfg.results_dir
        real = inside(root, cfg.results_dir)
        if real.is_dir() and not link.exists():
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(real, target_is_directory=True)


def in_copy(staging: Staging, root: Path, cfg: ProjectConfig, path: str) -> Path:
    """The agent's copy of a script. Raises ToolError for anything but a script in the scripts folder."""
    if not cfg.scripts_dir:
        raise ToolError("This paper has no scripts folder set, so no script can be run.")
    try:
        mirror_scripts(staging, root, cfg)
    except (OSError, ProjectError) as e:
        raise ToolError(f"The scripts could not be copied: {e}") from e
    folder = (staging.work_root / cfg.scripts_dir).resolve()
    for base in (staging.work, staging.work_root, folder):
        candidate = (base / path).resolve()
        if folder in candidate.parents and candidate.is_file():
            return candidate
    raise ToolError(f"{path} is not a script in the scripts folder ({cfg.scripts_dir}).")


def run_in_copy(staging: Staging, root: Path, cfg: ProjectConfig, script: Path, cancel: threading.Event) -> str:
    """What the model is told about the run."""
    try:
        args = command_for(script, cfg)
    except ScriptError as e:
        raise ToolError(str(e)) from e
    if not which(args[0]):
        raise ToolError(f"{args[0]} was not found on this Mac.")
    if not sandbox.available():
        raise ToolError("This Mac has no sandbox to run the script in, so it was not run.")
    temp = staging.work_root / ".thor-tmp"
    temp.mkdir(exist_ok=True)
    env = tool_env(TMPDIR=str(temp), MPLBACKEND="Agg")
    code, output = run_tool(sandbox.wrap(args, staging.work_root, network=False), timeout=SCRIPT_SECONDS, env=env,
                            cwd=script.parent, cancel=cancel)
    if code == CANCELLED:
        return "Stopped by the user."
    made = [c.path for c in staging.changes() if c.kind in ("binary", "new")]
    tail = output[-6000:]
    return (f"Exit code {code}.\nOutput:\n{tail or '(none)'}\nFiles new or changed in your copy, waiting for the user: "
            f"{', '.join(made) or 'none'}")

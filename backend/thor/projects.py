"""A project: a folder with a LaTeX manuscript, its settings file and its revision folders.

Opening a folder detects the settings from the source (main file, figure macro and folders, the marker
where the Supplementary Information starts, engine, bibliography). Detection never writes anything;
`revision_app.toml` is written only when the user saves the settings.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .paths import APP_NAME

CONFIG_NAME = "revision_app.toml"

DOCCLASS = re.compile(r"^[^%\n]*\\documentclass", re.M)
REVISION_DIR = re.compile(r"^revision(\d+)$")
NEWCOMMAND = re.compile(r"\\(?:re)?newcommand\*?\s*\{?\\([A-Za-z@]+)\}?\s*\[(\d)\]")
USEPACKAGE = re.compile(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")
FILECONTENTS = re.compile(r"\\begin\{filecontents\*?\}\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")
MAGIC_PROGRAM = re.compile(r"^\s*%\s*!TEX\s+(?:TS-)?program\s*=\s*(\w+)", re.M | re.I)
BIB_COMMAND = re.compile(r"\\(?:bibliography|printbibliography)\b")
SI_MARKERS = ["Supplementary Information", "Supplementary Materials", "Supplementary Material"]

# Folders never searched for the main file.
SKIP_DIRS = {"node_modules", "__pycache__", "old", "temp", "tmp", "build", "out", "supporting_files"}
HIDDEN_IN_TREE = {"__pycache__", "node_modules"}
IMAGE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".eps"}
MAX_TEX_BYTES = 5_000_000
ENGINES = ["pdflatex", "xelatex", "lualatex"]
TOOLCHAINS = ["local", "remote"]
DELIVERABLES = ["main_draft.pdf", "supplementary_draft.pdf", "revision_clean.pdf", "revision_tracked.pdf"]


class ProjectError(ValueError):
    pass


@dataclass
class ProjectConfig:
    name: str = ""
    latex_dir: str = "."
    main: str = "main.tex"
    revisions_dir: str = "."
    results_dir: str = ""
    scripts_dir: str = ""
    style_rules: str = ""
    standing_instructions: str = ""
    engine: str = "pdflatex"
    toolchain: str = "local"
    si_marker: str = ""
    line_numbers: bool = False
    figure_macro: str = ""
    figure_folders: list[str] = field(default_factory=list)
    bib_style: str = ""
    bib_filecontents: bool = False
    bib_extra_lists: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# (section, key, field, comment), in the order they are written to revision_app.toml.
LAYOUT = [
    ("project", "name", "name", f"Shown in {APP_NAME}."),
    ("project", "latex_dir", "latex_dir", "Folder holding the main file."),
    ("project", "main", "main", "Main file, inside latex_dir."),
    ("project", "revisions_dir", "revisions_dir", "Folder holding revision1/, revision2/, ..."),
    ("project", "results_dir", "results_dir", "Result files every number in the text must come from."),
    ("project", "scripts_dir", "scripts_dir", "Scripts that make the figures and tables."),
    ("project", "style_rules", "style_rules", "File with the writing and style rules the checks apply."),
    ("project", "standing_instructions", "standing_instructions", "Instructions given to the agent (e.g. CLAUDE.md)."),
    ("build", "engine", "engine", "pdflatex, xelatex or lualatex."),
    ("build", "toolchain", "toolchain", "local (MacTeX) or remote (ssh)."),
    ("build", "si_marker", "si_marker", "Text that opens the Supplementary Information; the PDF is split there."),
    ("build", "line_numbers", "line_numbers", "The source uses the lineno package (comment locations use it)."),
    ("figures", "macro", "figure_macro", r"Figure-include macro, e.g. figfile for \figfile{opts}{name}."),
    ("figures", "folders", "figure_folders", "Folders the macro looks in, inside latex_dir."),
    ("bibliography", "style", "bib_style", "natbib, biblatex or bibtex."),
    ("bibliography", "filecontents", "bib_filecontents", "The .bib file is written by a filecontents block in the main file."),
    ("bibliography", "extra_lists", "bib_extra_lists", r"Extra reference lists from multibib (\newcites)."),
]


def project_id(root: Path) -> str:
    return hashlib.sha1(str(root.resolve()).encode()).hexdigest()[:12]


def strip_comments(text: str) -> str:
    return re.sub(r"(?<!\\)%.*", "", text)


def read_tex(path: Path) -> str:
    if path.stat().st_size > MAX_TEX_BYTES:
        raise ProjectError(f"{path.name} is larger than {MAX_TEX_BYTES // 1_000_000} MB.")
    return path.read_text(encoding="utf-8", errors="replace")


def tex_files(root: Path, max_depth: int = 2) -> list[tuple[Path, int]]:
    found: list[tuple[Path, int]] = []

    def visit(folder: str, depth: int) -> None:
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return
        for e in entries:
            if e.name.startswith("."):
                continue
            if e.is_dir(follow_symlinks=False):
                if depth < max_depth and e.name not in SKIP_DIRS and not REVISION_DIR.match(e.name):
                    visit(e.path, depth + 1)
            elif e.name.endswith(".tex") and e.is_file(follow_symlinks=False):
                found.append((Path(e.path), depth))

    visit(str(root), 0)
    return found


def main_candidates(root: Path) -> list[str]:
    """Files with a \\documentclass, best guess first: main.tex, then shallower, then newer."""
    ranked = []
    for path, depth in tex_files(root):
        try:
            if DOCCLASS.search(read_tex(path)):
                ranked.append(((path.name != "main.tex", depth, -path.stat().st_mtime), path))
        except (OSError, ProjectError):
            continue
    ranked.sort(key=lambda r: r[0])
    return [p.relative_to(root).as_posix() for _, p in ranked]


def packages(source: str) -> set[str]:
    names: set[str] = set()
    for group in USEPACKAGE.findall(source):
        names.update(n.strip() for n in group.split(",") if n.strip())
    return names


def figure_macro(source: str) -> tuple[str, list[str]]:
    """The first two-argument macro whose body calls \\includegraphics, and the folders it looks in."""
    for m in NEWCOMMAND.finditer(source):
        body = source[m.end(): m.end() + 2000]
        following = NEWCOMMAND.search(body)
        if following:
            body = body[: following.start()]
        if "\\includegraphics" in body:
            folders: list[str] = []
            for folder in re.findall(r"\{([\w\-./]+)/#\d", body):
                if folder not in folders:
                    folders.append(folder)
            return m.group(1), folders
    return "", []


def graphicspath(source: str) -> list[str]:
    m = re.search(r"\\graphicspath\s*\{((?:\{[^{}]*\})+)\}", source)
    return [p.rstrip("/") for p in re.findall(r"\{([^{}]+)\}", m.group(1))] if m else []


def image_folders(latex_dir: Path) -> list[str]:
    folders = []
    for e in sorted(os.scandir(latex_dir), key=lambda e: e.name):
        if e.is_dir() and not e.name.startswith(".") and not REVISION_DIR.match(e.name):
            try:
                if any(Path(f.name).suffix.lower() in IMAGE_SUFFIXES for f in os.scandir(e.path)):
                    folders.append(e.name)
            except OSError:
                continue
    return folders


def si_marker(source: str) -> str:
    bib = BIB_COMMAND.search(source)
    tail = source[bib.end():] if bib else ""
    return next((marker for marker in SI_MARKERS if marker in tail), "")


def engine(raw: str, source: str) -> str:
    magic = MAGIC_PROGRAM.search(raw)
    if magic and magic.group(1).lower() in ENGINES:
        return magic.group(1).lower()
    return "xelatex" if packages(source) & {"fontspec", "unicode-math"} else "pdflatex"


def revision_dirs(folder: Path) -> list[Path]:
    try:
        return [Path(e.path) for e in os.scandir(folder) if e.is_dir() and REVISION_DIR.match(e.name)]
    except OSError:
        return []


def detect(root: Path, main: str | None = None) -> tuple[ProjectConfig, list[str], list[str]]:
    """Settings detected from the folder, the main-file candidates, and notes for the user."""
    candidates = main_candidates(root)
    notes: list[str] = []
    cfg = ProjectConfig(name=root.name)
    chosen = main or (candidates[0] if candidates else None)
    if not chosen:
        notes.append("No .tex file with \\documentclass was found within two folder levels.")
        return cfg, candidates, notes
    main_path = root / chosen
    cfg.latex_dir = main_path.parent.relative_to(root).as_posix()
    cfg.main = main_path.name
    if len(candidates) > 1:
        others = [c for c in candidates if c != chosen]
        notes.append(f"Main file {chosen}; {len(others)} other file(s) also have \\documentclass.")

    raw = read_tex(main_path)
    source = strip_comments(raw)
    used = packages(source)
    cfg.engine = engine(raw, source)
    cfg.line_numbers = "lineno" in used
    cfg.si_marker = si_marker(source)
    cfg.figure_macro, cfg.figure_folders = figure_macro(source)
    if not cfg.figure_folders:
        cfg.figure_folders = graphicspath(source) or image_folders(main_path.parent)
    bib_files = [n for n in FILECONTENTS.findall(source) if n.endswith(".bib")]
    cfg.bib_filecontents = bool(bib_files)
    cfg.bib_style = "biblatex" if "biblatex" in used else "natbib" if "natbib" in used else (
        "bibtex" if BIB_COMMAND.search(source) else "")
    cfg.bib_extra_lists = re.findall(r"\\newcites\s*\{(\w+)\}", source)

    latex_dir = main_path.parent
    in_root, in_latex = revision_dirs(root), revision_dirs(latex_dir)
    if len(in_root) >= len(in_latex) and in_root:
        cfg.revisions_dir = "."
    else:
        cfg.revisions_dir = cfg.latex_dir
        if not in_latex:
            notes.append(f"No revision folders yet; new ones will go in {cfg.latex_dir}/.")
    cfg.results_dir = "results" if (root / "results").is_dir() else ""
    cfg.scripts_dir = "scripts" if (root / "scripts").is_dir() else ""
    cfg.standing_instructions = "CLAUDE.md" if (root / "CLAUDE.md").is_file() else ""
    return cfg, candidates, notes


def read_config(path: Path, base: ProjectConfig) -> ProjectConfig:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ProjectError(f"{path.name} is not valid TOML: {e}") from e
    values = base.to_dict()
    for section, key, name, _ in LAYOUT:
        if key in data.get(section, {}):
            values[name] = data[section][key]
    cfg = ProjectConfig(**values)
    return cfg


def load_config(root: Path) -> ProjectConfig:
    """The settings in effect: revision_app.toml over what detection finds."""
    detected = detect(root)[0]
    path = root / CONFIG_NAME
    return read_config(path, detected) if path.is_file() else detected


def toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(v, ensure_ascii=False) for v in value) + "]"
    # A JSON string is a valid TOML basic string.
    return json.dumps(value, ensure_ascii=False)


def to_toml(cfg: ProjectConfig) -> str:
    lines = [f"# {APP_NAME} project settings. Paths are relative to this file's folder.", ""]
    current = None
    for section, key, name, comment in LAYOUT:
        if section != current:
            if current is not None:
                lines.append("")
            lines.append(f"[{section}]")
            current = section
        lines.append(f"# {comment}")
        lines.append(f"{key} = {toml_value(getattr(cfg, name))}")
    return "\n".join(lines) + "\n"


def inside(root: Path, rel: str) -> Path:
    """root/rel, refusing absolute paths and anything that climbs out of root."""
    if os.path.isabs(rel):
        raise ProjectError(f"{rel} must be relative to the project folder.")
    joined = os.path.normpath(os.path.join(root, rel))
    if joined != str(root) and not joined.startswith(str(root) + os.sep):
        raise ProjectError(f"{rel} is outside the project folder.")
    return Path(joined)


def validate(root: Path, cfg: ProjectConfig) -> None:
    known = {f.name: f.type for f in fields(ProjectConfig)}
    for name, value in cfg.to_dict().items():
        expected = known[name]
        if expected == "bool" and not isinstance(value, bool):
            raise ProjectError(f"{name} must be true or false.")
        if expected.startswith("list") and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
            raise ProjectError(f"{name} must be a list of names.")
        if expected == "str" and not isinstance(value, str):
            raise ProjectError(f"{name} must be text.")
    if not cfg.name.strip():
        raise ProjectError("The project name is empty.")
    if cfg.engine not in ENGINES:
        raise ProjectError(f"Engine must be one of {', '.join(ENGINES)}.")
    if cfg.toolchain not in TOOLCHAINS:
        raise ProjectError(f"Toolchain must be one of {', '.join(TOOLCHAINS)}.")
    latex_dir = inside(root, cfg.latex_dir)
    if not (latex_dir / cfg.main).is_file() or "/" in cfg.main:
        raise ProjectError(f"{cfg.latex_dir}/{cfg.main} does not exist.")
    for name in ("revisions_dir", "results_dir", "scripts_dir"):
        rel = getattr(cfg, name)
        if rel and not inside(root, rel).is_dir():
            raise ProjectError(f"{name}: the folder {rel} does not exist.")
    for name in ("style_rules", "standing_instructions"):
        rel = getattr(cfg, name)
        if rel and not inside(root, rel).is_file():
            raise ProjectError(f"{name}: the file {rel} does not exist.")


def save_config(root: Path, cfg: ProjectConfig) -> Path:
    validate(root, cfg)
    path = root / CONFIG_NAME
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(to_toml(cfg), encoding="utf-8")
    tmp.replace(path)
    return path


def revisions(root: Path, cfg: ProjectConfig) -> list[dict]:
    found = []
    for folder in revision_dirs(inside(root, cfg.revisions_dir)):
        pdfs = sorted(p.name for p in folder.glob("*.pdf"))
        found.append({
            "number": int(REVISION_DIR.match(folder.name).group(1)),
            "path": folder.relative_to(root).as_posix(),
            "pdfs": pdfs,
            "missing": [d for d in DELIVERABLES if d not in pdfs],
            "supporting_files": (folder / "supporting_files").is_dir(),
        })
    return sorted(found, key=lambda r: r["number"])


def open_project(root: Path, main: str | None = None) -> dict:
    root = Path(os.path.abspath(os.path.expanduser(str(root))))
    if not root.is_dir():
        raise ProjectError(f"{root} is not a folder.")
    detected, candidates, notes = detect(root, main)
    config_path = root / CONFIG_NAME
    config = read_config(config_path, detected) if config_path.is_file() else detected
    if config_path.is_file():
        try:
            validate(root, config)
        except ProjectError as e:
            notes.append(f"{CONFIG_NAME}: {e}")
    return {
        "id": project_id(root),
        "root": str(root),
        "name": config.name,
        "config": config.to_dict(),
        "detected": detected.to_dict(),
        "config_saved": config_path.is_file(),
        "main_candidates": candidates,
        "notes": notes,
        "revisions": revisions(root, config),
    }


def kind(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in {".tex", ".sty", ".cls"}:
        return "tex"
    if suffix == ".bib":
        return "bib"
    if suffix == ".pdf":
        return "pdf"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in {".py", ".sh"}:
        return "script"
    return "other"


def list_dir(root: Path, rel: str = "", limit: int = 5000) -> dict:
    folder = inside(root, rel or ".")
    if not folder.is_dir():
        raise ProjectError(f"{rel or '.'} is not a folder.")
    entries = []
    for e in os.scandir(folder):
        if e.name.startswith(".") or e.name in HIDDEN_IN_TREE:
            continue
        is_dir = e.is_dir()
        try:
            size = None if is_dir else e.stat().st_size
        except OSError:
            size = None
        entries.append({
            "name": e.name,
            "path": os.path.relpath(e.path, root),
            "dir": is_dir,
            "size": size,
            "kind": "folder" if is_dir else kind(e.name),
        })
    entries.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    return {"path": rel, "entries": entries[:limit], "truncated": len(entries) > limit}

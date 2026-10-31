"""The tools a model can call when Thor runs the conversation itself, and the ones Thor adds to Claude Code.

Every path is confined to the agent's copy of the paper, and reading may also reach the results and scripts
folders. Text that comes back from a tool is data for the model; the worst it can do is suggest an edit, which
the user then sees.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..files import is_text
from .staging import LATEXMK_RC

if TYPE_CHECKING:
    import httpx

MAX_READ_LINES = 1500
MAX_RESULT_CHARS = 60_000
MAX_SEARCH_HITS = 120
CROSSREF = "https://api.crossref.org/works"
USER_AGENT = "Thor-LaTeX-editor/0.1 (local desktop app)"


class ToolError(Exception):
    """Told to the model as a failed tool call, so it can correct itself."""


def _object(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


FILE_TOOLS = [
    {"name": "list_files", "description": "List the files and folders in a folder of the paper. Use '.' for the top.",
     "parameters": _object({"path": {"type": "string", "description": "Folder, relative to the paper."}}, ["path"])},
    {"name": "read_file", "description": "Read a text file. Lines are numbered; the numbers are not part of the file.",
     "parameters": _object({"path": {"type": "string"},
                            "start_line": {"type": "integer", "description": "First line to read, from 1."},
                            "line_count": {"type": "integer", "description": f"At most {MAX_READ_LINES}."}}, ["path"])},
    {"name": "search", "description": "Search the paper's text files with a regular expression. Returns file:line: text.",
     "parameters": _object({"pattern": {"type": "string"},
                            "glob": {"type": "string", "description": "Only files matching this, such as *.bib."}}, ["pattern"])},
    {"name": "edit_file", "description": "Replace exact text in a file. old_text must appear exactly once unless "
                                         "replace_all is true. Change as little as you can: do not rewrap lines you did "
                                         "not otherwise change. The user reviews each edit.",
     "parameters": _object({"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"},
                            "replace_all": {"type": "boolean"}}, ["path", "old_text", "new_text"])},
    {"name": "write_file", "description": "Create a new file, or replace a whole file when most of it changes.",
     "parameters": _object({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"])},
]
PAPER_TOOLS = [
    {"name": "compile", "description": "Compile your copy of the paper with LaTeX and report errors, undefined "
                                       "references and citations, overfull boxes and the page count.",
     "parameters": _object({}, [])},
    {"name": "find_references", "description": "Look up published works in Crossref by title, authors or topic. Never "
                                               "cite anything from memory alone: confirm it here first.",
     "parameters": _object({"query": {"type": "string"}, "rows": {"type": "integer", "description": "At most 10."}}, ["query"])},
    {"name": "get_bibtex", "description": "Get the BibTeX entry for a DOI from its publisher's record.",
     "parameters": _object({"doi": {"type": "string"}}, ["doi"])},
    {"name": "read_comments", "description": "Read the co-authors' comments imported into the active revision, with "
                                             "their pages, lines and any answers from the user.",
     "parameters": _object({}, [])},
]


@dataclass
class ToolBox:
    work: Path
    readable: list[Path] = field(default_factory=list)
    compile_fn: Callable[[], str] | None = None
    comments_fn: Callable[[], str] | None = None
    http: httpx.Client | None = None
    wrote: bool = False

    def call(self, name: str, args: dict) -> str:
        method = getattr(self, f"tool_{name}", None)
        if not method:
            raise ToolError(f"There is no tool called {name}.")
        try:
            result = method(**args)
        except TypeError as e:
            raise ToolError(f"Wrong arguments for {name}: {e}") from e
        except OSError as e:
            raise ToolError(f"{name} failed: {e.strerror or e}") from e
        return result if len(result) <= MAX_RESULT_CHARS else result[:MAX_RESULT_CHARS] + "\n[Cut off here: too long.]"

    def _path(self, rel: str, writing: bool = False) -> Path:
        target = Path(rel)
        target = (target if target.is_absolute() else self.work / target).resolve()
        work = self.work.resolve()
        if target == work or work in target.parents:
            if writing and target.name in LATEXMK_RC:
                raise ToolError("latexmk runs this file as code, so the agent may not change it. Tell the user what "
                                "should change.")
            if writing and any(part.startswith(".") for part in target.relative_to(work).parts):
                raise ToolError("Hidden files and folders cannot be written.")
            return target
        if not writing and any(target == r.resolve() or r.resolve() in target.parents for r in self.readable):
            return target
        raise ToolError(f"{rel} is outside the paper.")

    def tool_list_files(self, path: str = ".") -> str:
        folder = self._path(path)
        if not folder.is_dir():
            raise ToolError(f"{path} is not a folder.")
        rows = []
        for item in sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name)):
            if not item.name.startswith("."):
                rows.append(f"{item.name}/" if item.is_dir() else f"{item.name} ({item.stat().st_size:,} bytes)")
        return "\n".join(rows) or "The folder is empty."

    def tool_read_file(self, path: str, start_line: int = 1, line_count: int = MAX_READ_LINES) -> str:
        target = self._path(path)
        if not target.is_file():
            raise ToolError(f"{path} is not a file.")
        data = target.read_bytes()
        if b"\0" in data[:8192]:
            raise ToolError(f"{path} is not a text file.")
        lines = data.decode("utf-8", errors="replace").splitlines()
        first = max(1, start_line)
        count = max(1, min(line_count, MAX_READ_LINES))
        shown = lines[first - 1:first - 1 + count]
        body = "\n".join(f"{first + i}\t{line}" for i, line in enumerate(shown))
        if first - 1 + count < len(lines):
            body += f"\n[{len(lines)} lines in all; continue from line {first + count}.]"
        return body or "The file is empty."

    def tool_search(self, pattern: str, glob: str = "") -> str:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise ToolError(f"That is not a valid regular expression: {e}") from e
        hits: list[str] = []
        for path in sorted(self.work.rglob("*")):
            rel = path.relative_to(self.work)
            if any(part.startswith(".") for part in rel.parts) or not path.is_file() or not is_text(path):
                continue
            if glob and not fnmatch.fnmatch(path.name, glob) and not fnmatch.fnmatch(rel.as_posix(), glob):
                continue
            for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{rel.as_posix()}:{number}: {line[:300]}")
                    if len(hits) >= MAX_SEARCH_HITS:
                        return "\n".join(hits) + f"\n[Stopped at {MAX_SEARCH_HITS} matches; narrow the search.]"
        return "\n".join(hits) or "No matches."

    def tool_edit_file(self, path: str, old_text: str, new_text: str, replace_all: bool = False) -> str:
        target = self._path(path, writing=True)
        if not target.is_file():
            raise ToolError(f"{path} is not a file. Use write_file to create one.")
        if not old_text:
            raise ToolError("old_text is empty.")
        text = target.read_bytes().decode("utf-8", errors="replace")
        count = text.count(old_text)
        if count == 0:
            raise ToolError(f"old_text is not in {path}. Read the file again and copy the text exactly.")
        if count > 1 and not replace_all:
            raise ToolError(f"old_text appears {count} times in {path}. Add more of the surrounding text.")
        target.write_bytes(text.replace(old_text, new_text).encode())
        self.wrote = True
        return f"Edited {path} ({count} place{'s' if count != 1 else ''})."

    def tool_write_file(self, path: str, content: str) -> str:
        target = self._path(path, writing=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode())
        self.wrote = True
        return f"Wrote {path}."

    def tool_compile(self) -> str:
        if not self.compile_fn:
            raise ToolError("Compiling is not available here.")
        return self.compile_fn()

    def tool_read_comments(self) -> str:
        if not self.comments_fn:
            raise ToolError("Comments are not available here.")
        return self.comments_fn()

    def _client(self) -> httpx.Client:
        import httpx

        return self.http or httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT})

    def tool_find_references(self, query: str, rows: int = 5) -> str:
        import httpx

        try:
            response = self._client().get(CROSSREF, params={"query.bibliographic": query, "rows": max(1, min(rows, 10)),
                                                            "select": "DOI,title,author,issued,container-title"})
            response.raise_for_status()
            items = response.json()["message"]["items"]
        except (httpx.HTTPError, KeyError, json.JSONDecodeError) as e:
            raise ToolError(f"Crossref did not answer: {e}") from e
        rows_out = []
        for item in items:
            authors = ", ".join(a.get("family", a.get("name", "")) for a in item.get("author", [])[:6])
            year = ((item.get("issued") or {}).get("date-parts") or [[None]])[0][0]
            rows_out.append(f"{(item.get('title') or ['(no title)'])[0]}\n  {authors} ({year}), "
                            f"{(item.get('container-title') or [''])[0]}\n  doi:{item.get('DOI')}")
        return "\n".join(rows_out) or "Crossref found nothing."

    def tool_get_bibtex(self, doi: str) -> str:
        doi = doi.removeprefix("https://doi.org/").removeprefix("doi:").strip()
        if not re.fullmatch(r"10\.\d{4,9}/\S+", doi):
            raise ToolError(f"{doi} does not look like a DOI.")
        import httpx

        try:
            response = self._client().get(f"https://doi.org/{doi}", headers={"Accept": "application/x-bibtex"})
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise ToolError(f"doi.org did not answer for {doi}: {e}") from e
        return response.text


JSON_TYPES = {"string": str, "integer": int, "boolean": bool}


def check_args(schema: dict, args: object) -> str | None:
    """Why these arguments do not fit the tool's schema; None if they do. Streamed tool input can arrive cut off
    or mistyped, and such a call must not run."""
    if not isinstance(args, dict):
        return "The arguments are not an object."
    for name in schema.get("required", []):
        if name not in args:
            return f"The argument {name} is missing."
    properties = schema.get("properties", {})
    for name, value in args.items():
        if name not in properties:
            return f"There is no argument called {name}."
        wanted = JSON_TYPES.get(properties[name].get("type"))
        if wanted and (not isinstance(value, wanted) or (wanted is int and isinstance(value, bool))):
            return f"The argument {name} should be a {properties[name]['type']}."
    return None


def describe(name: str, args: dict) -> str:
    """A tool call in words, for the transcript."""
    path = args.get("path") or args.get("file_path") or ""
    short = Path(path).name if path else ""
    return {
        "list_files": f"Listing {short or 'the paper'}",
        "read_file": f"Reading {short}", "Read": f"Reading {short}",
        "search": f"Searching for {args.get('pattern', '')!r}", "Grep": f"Searching for {args.get('pattern', '')!r}",
        "Glob": f"Finding files like {args.get('pattern', '')}",
        "edit_file": f"Editing {short}", "Edit": f"Editing {short}", "MultiEdit": f"Editing {short}",
        "write_file": f"Writing {short}", "Write": f"Writing {short}",
        "compile": "Compiling its copy of the paper", "find_references": f"Looking up {args.get('query', '')!r} in Crossref",
        "get_bibtex": f"Getting the BibTeX for {args.get('doi', '')}", "read_comments": "Reading the co-authors' comments",
        "WebSearch": f"Searching the web for {args.get('query', '')!r}", "WebFetch": f"Reading {args.get('url', '')}",
        "Bash": f"Running {str(args.get('command', ''))[:120]}",
    }.get(name.removeprefix("mcp__thor__"), f"Using {name}")

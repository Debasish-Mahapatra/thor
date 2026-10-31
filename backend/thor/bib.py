"""Reading BibTeX: enough to list a paper's references and check them, not to format them."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

ENTRY_START = re.compile(r"@(\w+)\s*\{\s*([^,\s{}]+)\s*,")
FIELD_NAME = re.compile(r"\s*,?\s*([\w-]+)\s*=\s*")
FILECONTENTS = re.compile(r"\\begin\{filecontents\*?\}(?:\[[^\]]*\])?\{([^}]+\.bib)\}\n?(.*?)\\end\{filecontents\*?\}", re.S)
NOT_ENTRIES = {"comment", "string", "preamble"}


@dataclass
class BibEntry:
    key: str
    kind: str
    fields: dict[str, str] = field(default_factory=dict)
    # Where it came from, and the text as written.
    source: str = ""
    raw: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def title(self) -> str:
        return plain(self.fields.get("title", ""))

    @property
    def authors(self) -> str:
        return plain(self.fields.get("author", ""))

    @property
    def year(self) -> str:
        return self.fields.get("year", "")

    @property
    def doi(self) -> str:
        return self.fields.get("doi", "").removeprefix("https://doi.org/").removeprefix("doi:").strip()


def plain(text: str) -> str:
    """Field text without BibTeX's braces and line breaks."""
    return re.sub(r"\s+", " ", text.replace("{", "").replace("}", "")).strip()


def _closing(text: str, start: int, opening: str, closing: str) -> int:
    """The position after the bracket that closes the one at start; -1 if it never closes."""
    depth, i = 0, start
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == opening:
            depth += 1
        elif c == closing:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def parse(text: str, source: str = "") -> list[BibEntry]:
    entries = []
    at = 0
    while True:
        found = ENTRY_START.search(text, at)
        if not found:
            return entries
        brace = text.index("{", found.start())
        end = _closing(text, brace, "{", "}")
        if end < 0:
            at = found.end()
            continue
        kind = found.group(1).lower()
        at = end
        if kind in NOT_ENTRIES:
            continue
        entry = BibEntry(found.group(2), kind, source=source, raw=text[found.start():end])
        pos = found.end()
        while pos < end - 1:
            name = FIELD_NAME.match(text, pos)
            if not name:
                break
            pos = name.end()
            if text[pos:pos + 1] == "{":
                close = _closing(text, pos, "{", "}")
                value = text[pos + 1:close - 1] if close > 0 else ""
                pos = close if close > 0 else end
            elif text[pos:pos + 1] == '"':
                close = text.find('"', pos + 1)
                value = text[pos + 1:close] if close > 0 else ""
                pos = close + 1 if close > 0 else end
            else:
                bare = re.match(r"[^,}\s]+", text[pos:end])
                value = bare.group(0) if bare else ""
                pos += len(value)
            entry.fields[name.group(1).lower()] = value.strip()
        entries.append(entry)


def read_project(latex_dir: Path, tex_files: list[Path] | None = None) -> list[BibEntry]:
    """Every entry in the project's .bib files and filecontents bibliographies; the first with a key wins."""
    found: dict[str, BibEntry] = {}
    for path in sorted(latex_dir.rglob("*.bib")):
        if not any(p.startswith(".") for p in path.relative_to(latex_dir).parts):
            for entry in parse(path.read_text(errors="replace"), path.relative_to(latex_dir).as_posix()):
                found.setdefault(entry.key, entry)
    for path in tex_files if tex_files is not None else sorted(latex_dir.rglob("*.tex")):
        text = path.read_text(errors="replace")
        if "filecontents" in text:
            for name, body in FILECONTENTS.findall(text):
                for entry in parse(body, path.relative_to(latex_dir).as_posix()):
                    found.setdefault(entry.key, entry)
    return list(found.values())

"""The user's Zotero library, through the API Zotero itself serves on this Mac while it is running."""

from __future__ import annotations

import re
from pathlib import Path

from . import files
from .agent.tools import ToolError
from .aids import source_files
from .bib import FILECONTENTS, parse
from .history import History
from .projects import ProjectConfig, inside

BASE = "http://127.0.0.1:23119/api/users/0"
NOT_ITEMS = "-attachment || note"
ASK_SECONDS = 5
NOT_ALLOWED = ("Zotero is refusing other programs. In Zotero's Settings, under Advanced, turn on \"Allow other "
               "applications on this computer to communicate with Zotero\".")
NOT_RUNNING = "Zotero is not running. Open it and try again."
BIBLIOGRAPHY = re.compile(r"\\(?:bibliography|addbibresource)(?:\[[^\]]*\])?\{([^}]+)\}")
END_FILECONTENTS = re.compile(r"^\\end\{filecontents\*?\}", re.M)


class ZoteroError(Exception):
    """Told to the user as it is."""


def _get(path: str, params: dict, http=None) -> object:
    import httpx

    try:
        response = (http or httpx).get(f"{BASE}{path}", params=params, **({} if http else {"timeout": ASK_SECONDS}))
    except httpx.ConnectError as e:
        raise ZoteroError(NOT_RUNNING) from e
    except httpx.HTTPError as e:
        raise ZoteroError(f"Zotero did not answer: {e}") from e
    if response.status_code in (401, 403):
        raise ZoteroError(NOT_ALLOWED)
    if response.status_code != 200:
        raise ZoteroError(f"Zotero answered with an error ({response.status_code}): {response.text[:200]}")
    return response


def search(query: str, http=None) -> list[dict]:
    response = _get("/items", {"q": query, "qmode": "titleCreatorYear", "limit": 25, "format": "json",
                               "itemType": NOT_ITEMS}, http)
    found = []
    for item in response.json():
        data = item.get("data") or {}
        people = [c.get("lastName") or c.get("name") or "" for c in data.get("creators") or []]
        year = re.search(r"\d{4}", str(data.get("date") or ""))
        found.append({"key": item.get("key") or data.get("key"), "title": data.get("title") or "(no title)",
                      "creators": ", ".join(p for p in people[:6] if p), "year": year.group(0) if year else "",
                      "doi": data.get("DOI") or "", "kind": data.get("itemType") or ""})
    return found


def bibtex(key: str, http=None) -> str:
    if not re.fullmatch(r"[A-Za-z0-9]+", key):
        raise ZoteroError(f"{key} is not a Zotero item key.")
    return _get("/items", {"itemKey": key, "format": "bibtex"}, http).text.strip()


def search_text(query: str, http=None) -> str:
    """The agent's tool."""
    try:
        found = search(query, http)[:8]
        return "\n\n".join(bibtex(item["key"], http) for item in found) or "Nothing in the library matches."
    except ZoteroError as e:
        raise ToolError(str(e)) from e


def target_file(root: Path, cfg: ProjectConfig) -> tuple[Path, bool]:
    """Where a new entry goes: (file, whether it goes in a filecontents block there)."""
    latex_dir = inside(root, cfg.latex_dir)
    main = latex_dir / cfg.main
    text = main.read_text(errors="replace")
    if FILECONTENTS.search(text):
        return main, True
    for group in BIBLIOGRAPHY.findall(re.sub(r"(?<!\\)%.*", "", text)):
        first = group.split(",")[0].strip()
        found = latex_dir / (first if first.endswith(".bib") else f"{first}.bib")
        if found.is_file():
            return found, False
    bibs = sorted(p for p in latex_dir.glob("*.bib"))
    if bibs:
        return bibs[0], False
    raise ZoteroError(f"This paper has no .bib file to add to, and {cfg.main} writes none. Create one first.")


def add_to_project(root: Path, cfg: ProjectConfig, project_id: str, history: History, key: str, http=None) -> dict:
    """Adds an item's BibTeX to the paper's bibliography, unless it is there already. Returns the key to cite."""
    text = bibtex(key, http)
    entries = parse(text)
    if not entries:
        raise ZoteroError("Zotero gave no BibTeX for this item.")
    entry = entries[0]
    latex_dir = inside(root, cfg.latex_dir)
    existing = {}
    for path in [*sorted(latex_dir.glob("*.bib")), *source_files(latex_dir, {".tex"})]:
        source = path.read_text(errors="replace")
        for found in parse(source):
            existing.setdefault(found.key, found)
    # Zotero's export is the same each time, so the same work is recognized by its DOI or by its title.
    for found in existing.values():
        if (entry.doi and found.doi.lower() == entry.doi.lower()) or (not entry.doi and found.title and found.title == entry.title):
            return {"cite_key": found.key, "file": None, "added": False}
    cite_key = entry.key
    number = 2
    while cite_key in existing:
        cite_key = f"{entry.key}-{number}"
        number += 1
    raw = entry.raw.replace(f"{{{entry.key},", f"{{{cite_key},", 1)
    path, in_block = target_file(root, cfg)
    rel = path.relative_to(root).as_posix()
    current = path.read_text(errors="replace")
    if in_block:
        # The last block is the bibliography when a paper has more than one, as with multibib's lists it ends with.
        match = None
        for match in END_FILECONTENTS.finditer(current):
            pass
        updated = f"{current[:match.start()]}{raw}\n{current[match.start():]}"
    else:
        updated = f"{current.rstrip()}\n\n{raw}\n"
    history.record(project_id, rel, current.encode())
    files.write_text(root, rel, updated, files.digest(current.encode()))
    history.record(project_id, rel, updated.encode(), label=f"Added {cite_key} from Zotero")
    return {"cite_key": cite_key, "file": rel, "added": True}

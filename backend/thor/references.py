"""Checking the paper's references against Crossref, and reading legal open copies through Unpaywall.

Crossref says whether a work exists as the entry describes it. It knows nothing about whether the paper cites it for the
right reason. A book or a report is often missing from it, so "not found" means look, not wrong.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .aids import source_files
from .agent.tools import CROSSREF, USER_AGENT, ToolError, crossref_get
from .bib import BibEntry, read_project
from .paths import app_home
from .projects import ProjectConfig, inside

if TYPE_CHECKING:
    import httpx

CITE = re.compile(r"\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{([^}]*)\}")
SAME_TITLE = 0.9
LIKELY_TITLE = 0.75
# Whether a work exists does not change, but an entry's text can, and the key below holds that.
KEEP_SECONDS = 30 * 86400
UNPAYWALL = "https://api.unpaywall.org/v2"
MAX_PDF_BYTES = 30_000_000
MAX_TEXT_CHARS = 60_000
FIELDS = "DOI,title,author,issued,container-title"


def client() -> httpx.Client:
    import httpx

    return httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT})


def normal(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"\\[a-zA-Z]+|[{}$]", "", title).lower()).strip()


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normal(a), normal(b), autojunk=False).ratio()


def cited_keys(latex_dir: Path) -> set[str]:
    keys: set[str] = set()
    for path in source_files(latex_dir, {".tex"}):
        for group in CITE.findall(re.sub(r"(?<!\\)%.*", "", path.read_text(errors="replace"))):
            keys.update(k.strip() for k in group.split(",") if k.strip())
    return keys


def year_of(item: dict) -> str:
    parts = ((item.get("issued") or {}).get("date-parts") or [[None]])[0]
    return str(parts[0]) if parts and parts[0] else ""


def first_author(item: dict) -> str:
    authors = item.get("author") or []
    return str(authors[0].get("family") or authors[0].get("name") or "") if authors else ""


def compare(entry: BibEntry, item: dict, by_doi: bool) -> dict:
    """What differs between an entry and the record found for it."""
    title = (item.get("title") or [""])[0]
    problems = []
    closeness = similarity(entry.title, title)
    if closeness < SAME_TITLE:
        problems.append(f'Crossref has the title "{title}"')
    year = year_of(item)
    # A paper is often online one year and in an issue the next.
    if year and entry.year and abs(int(year) - int(entry.year)) > 1 if year.isdigit() and entry.year.isdigit() else False:
        problems.append(f"Crossref has the year {year}, and the entry says {entry.year}")
    family = first_author(item)
    if family and entry.authors and normal(family) not in normal(entry.authors):
        problems.append(f"Crossref's first author is {family}, who is not in the entry")
    doi = item.get("DOI") or ""
    row = {"found_title": title, "found_year": year, "found_author": family, "found_doi": doi}
    if problems:
        return {**row, "status": "mismatch", "detail": "; ".join(problems) + "."}
    if not by_doi and doi and entry.doi and entry.doi.lower() != doi.lower():
        return {**row, "status": "mismatch", "detail": f"The entry's DOI is {entry.doi}, and Crossref has {doi}."}
    return {**row, "status": "confirmed", "detail": f"Matches doi:{doi}." if doi else "Matches Crossref."}


def check_entry(entry: BibEntry, http) -> dict:
    import httpx

    row = {"key": entry.key, "title": entry.title, "authors": entry.authors, "year": entry.year, "doi": entry.doi,
           "source": entry.source}
    try:
        if entry.doi:
            response = crossref_get(http, f"{CROSSREF}/{entry.doi}")
            if response.status_code == 404:
                return {**row, "status": "not found", "detail": f"Crossref has no record of the DOI {entry.doi}."}
            response.raise_for_status()
            return {**row, **compare(entry, response.json()["message"], True)}
        if not entry.title:
            return {**row, "status": "not checked", "detail": "The entry has no title or DOI to look up."}
        response = crossref_get(http, CROSSREF, {"query.bibliographic": f"{entry.title} {entry.authors[:80]}", "rows": 5,
                                                 "select": FIELDS})
        response.raise_for_status()
        items = response.json()["message"]["items"]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        return {**row, "status": "not checked", "detail": f"Crossref did not answer: {e}"}
    best = max(items, key=lambda i: similarity(entry.title, (i.get("title") or [""])[0]), default=None)
    if not best or similarity(entry.title, (best.get("title") or [""])[0]) < LIKELY_TITLE:
        return {**row, "status": "not found", "detail": "Crossref has no such title. It may be a book or a report, which "
                                                        "it often lacks; if it is a journal paper, look closely."}
    return {**row, **compare(entry, best, False)}


def _cache_path(project_id: str) -> Path:
    folder = app_home() / "references"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{project_id}.json"


def check(root: Path, cfg: ProjectConfig, project_id: str, http=None) -> dict:
    latex_dir = inside(root, cfg.latex_dir)
    entries = read_project(latex_dir, source_files(latex_dir, {".tex"}))
    keys = cited_keys(latex_dir)
    cited = [e for e in entries if e.key in keys]
    path = _cache_path(project_id)
    try:
        cache = json.loads(path.read_text())
    except (OSError, ValueError):
        cache = {}
    now = time.time()

    def fingerprint(entry: BibEntry) -> str:
        return hashlib.sha1(entry.raw.encode()).hexdigest()

    def one(entry: BibEntry) -> dict:
        kept = cache.get(fingerprint(entry))
        if kept and now - kept["when"] < KEEP_SECONDS:
            return kept["row"]
        return check_entry(entry, http)

    own = None
    if http is None:
        http = own = client()
    try:
        # One at a time: Crossref turns away anyone asking in parallel.
        rows = [one(entry) for entry in cited]
    finally:
        if own:
            own.close()
    # A failure to reach Crossref says nothing about the entry, so it is asked again next time.
    fresh = {fingerprint(e): {"when": cache.get(fingerprint(e), {}).get("when", now), "row": r}
             for e, r in zip(cited, rows) if r["status"] != "not checked"}
    path.write_text(json.dumps(fresh))
    known = {e.key for e in entries}
    order = {"mismatch": 0, "not found": 1, "not checked": 2, "confirmed": 3}
    rows.sort(key=lambda r: (order[r["status"]], r["key"]))
    return {"rows": rows, "uncited": sorted(known - keys), "no_entry": sorted(keys - known),
            "counts": {s: sum(1 for r in rows if r["status"] == s) for s in order}}


def open_text(doi: str, email: str, http=None) -> str:
    """The text of a legal open copy, with where it came from. Raises ToolError with what stopped it."""
    import httpx
    import pymupdf

    if not email:
        raise ToolError("Unpaywall asks for an email address, and none is set. Tell the user they can add one in "
                        "Settings; it is sent only to Unpaywall.")
    own = None
    if http is None:
        http = own = client()
    try:
        response = http.get(f"{UNPAYWALL}/{doi}", params={"email": email})
        if response.status_code == 404:
            raise ToolError(f"Unpaywall does not know {doi}.")
        response.raise_for_status()
        best = response.json().get("best_oa_location") or {}
        url = best.get("url_for_pdf")
        if not url:
            raise ToolError(f"Unpaywall knows no open PDF of {doi}. Landing page, if any: {best.get('url') or 'none'}.")
        with http.stream("GET", url) as stream:
            stream.raise_for_status()
            data = bytearray()
            for chunk in stream.iter_bytes():
                data += chunk
                if len(data) > MAX_PDF_BYTES:
                    raise ToolError(f"The open copy at {url} is too large to read here.")
    except httpx.HTTPError as e:
        raise ToolError(f"Could not get an open copy of {doi}: {e}") from e
    finally:
        if own:
            own.close()
    try:
        document = pymupdf.open(stream=bytes(data), filetype="pdf")
    except (pymupdf.FileDataError, ValueError) as e:
        raise ToolError(f"What came from {url} is not a readable PDF.") from e
    text = "\n".join(page.get_text() for page in document)
    license_name = best.get("license") or "not stated"
    return f"Source: {url}\nLicense: {license_name}\n\n{text[:MAX_TEXT_CHARS]}"

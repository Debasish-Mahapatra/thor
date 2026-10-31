"""Where the paper's numbers come from: each number in the text, linked to the result files that hold it.

A number counts as found when a result file holds a value that rounds to it, as the text writes it: 3.14 is found in
a file that says 3.1416. Finding it proves only that the value exists somewhere, not that it is the right one, so the
report shows the lines and a person reads them.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .aids import source_files
from .agent.tools import ToolError
from .projects import ProjectConfig, ProjectError, inside

TEXT_SUFFIXES = {".csv", ".tsv", ".txt", ".dat", ".json", ".md", ".log", ".out", ".asc", ".tab", ".yaml", ".yml", ".tex", ".rst"}
# Large model output takes minutes to read in Python and is rarely where a printed number comes from.
MAX_FILE_BYTES = 20_000_000
MAX_TOTAL_BYTES = 60_000_000
MAX_PLACES = 5
MAX_SEARCH_HITS = 80
TOKEN = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\w])")
IN_TEXT = re.compile(r"(?<![\w.\\])[-−–]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.\\])[-−–]?\d+(?:\.\d+)?")
# LaTeX that holds digits which are not results. Each is blanked, keeping the line numbers.
NOT_TEXT = [re.compile(p, re.S) for p in (
    r"(?<!\\)%[^\n]*",
    r"\\(?:[a-zA-Z]*cite[a-zA-Z]*\*?|ref|eqref|autoref|pageref|[cC]ref|label|input|include|bibliography[A-Za-z]*|"
    r"bibliographystyle|url|usepackage|includegraphics|graphicspath|vspace\*?|hspace\*?|setcounter|addtocounter|"
    r"setlength|addtolength|begin|end)(?:\[[^\]]*\])*\{[^{}]*\}",
    r"\\href\{[^{}]*\}",
    r"\[[^\]\n]*(?:width|height|scale|angle|\d(?:pt|cm|mm|in|em|ex))[^\]\n]*\]",
    r"\{[lcrp|@{}\d. ]*\}(?=\s*\n)",
    r"\^\{?[-−]?\d+\}?",
    r"_\{?\d+\}?",
    r"\\[a-zA-Z]+\d*",
)]
# Words before a number that make it a label.
LABEL_BEFORE = re.compile(r"(?:Figures?|Figs?\.?|Tables?|Tabs?\.?|Sections?|Sects?\.?|Secs?\.?|Equations?|Eqs?\.?|Pages?|pp?\.|"
                          r"Lines?|Experiments?|Appendix|Chapter|Supplementary|No\.|Vol\.)[~\s]*(?:\\[a-zA-Z]+[~\s{]*)?[S]?$", re.I)
UNIT_AFTER = re.compile(r"\s*(?:\\,|~|\\ )?\s*(?:\\%|%|K\b|km|mm|cm|hPa|Pa\b|W\b|m\b|s\b|g\b|kg|degrees?\b|°|\\degree|\^\\circ|"
                        r"\$\^\\circ|ppm|ppb|days?\b|hours?\b|years?\b|percent|times\b|fold\b|GW|TW|Gt|Mt|Tg|mg|µg|ms|h\b)")
PERCENT = re.compile(r"\s*(?:\\,|~)?\s*(?:\\%|%|percent)")


@dataclass
class Number:
    file: str
    line: int
    # As the text writes it, and the digits used to compare it.
    shown: str
    digits: str
    context: str
    percent: bool = False
    sources: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def blank(text: str) -> str:
    def spaces(match: re.Match) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))

    for pattern in NOT_TEXT:
        text = pattern.sub(spaces, text)
    return text


def body_of(text: str) -> tuple[str, int]:
    """The text after the preamble, and how many lines came before it."""
    start = text.find("\\begin{document}")
    return (text[start:], text.count("\n", 0, start)) if start >= 0 else (text, 0)


def worth_checking(digits: str, has_unit: bool) -> bool:
    """A lone small whole number is too often a count or a word's worth of prose, and a year is a year."""
    if "." in digits or has_unit:
        return True
    value = int(digits)
    return value >= 100 and not 1800 <= value <= 2100


def in_text(text: str, name: str) -> list[Number]:
    body, skipped = body_of(text)
    cleaned = blank(body)
    found = []
    for number, (line, plain) in enumerate(zip(cleaned.split("\n"), body.split("\n")), skipped + 1):
        for match in IN_TEXT.finditer(line):
            after = line[match.end():match.end() + 16]
            if after[:1] in ("-", "/") and after[1:2].isdigit() and match.group(0).startswith("1"):
                continue
            if LABEL_BEFORE.search(line[max(0, match.start() - 30):match.start()]):
                continue
            digits = match.group(0).lstrip("-−–").replace(",", "")
            if not worth_checking(digits, bool(UNIT_AFTER.match(after))):
                continue
            found.append(Number(name, number, match.group(0), digits, plain.strip()[:200],
                                bool(PERCENT.match(after))))
    return found


def rounded(value: float, decimals: int) -> str:
    return f"{abs(value):.{decimals}f}"


def result_files(root: Path, cfg: ProjectConfig) -> tuple[list[Path], list[str]]:
    """The files that can be read, and in words those that cannot."""
    if not cfg.results_dir:
        return [], []
    try:
        folder = inside(root, cfg.results_dir)
    except ProjectError:
        return [], []
    if not folder.is_dir():
        return [], []
    readable, skipped, total = [], [], 0
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if any(part.startswith(".") for part in rel.parts) or not path.is_file():
            continue
        size = path.stat().st_size
        if path.suffix.lower() not in TEXT_SUFFIXES:
            skipped.append(f"{rel.as_posix()} (not a text file)")
        elif size > MAX_FILE_BYTES:
            skipped.append(f"{rel.as_posix()} ({size // 1_000_000} MB, too large to search)")
        elif total + size > MAX_TOTAL_BYTES:
            skipped.append(f"{rel.as_posix()} (the search stops at {MAX_TOTAL_BYTES // 1_000_000} MB)")
        else:
            readable.append(path)
            total += size
    return readable, skipped


def link(numbers: list[Number], paths: list[Path], root: Path) -> None:
    """Fills in where each number is found. Only the decimal places the paper uses are worked out for each value."""
    wanted: dict[tuple[int, float], dict[str, list[Number]]] = {}

    def add(digits: str, scale: float, number: Number) -> None:
        decimals = len(digits.partition(".")[2])
        wanted.setdefault((decimals, scale), {}).setdefault(digits, []).append(number)

    for number in numbers:
        add(number.digits, 1.0, number)
        if number.percent:
            # A share of 0.123 and a text saying 12.3%.
            add(number.digits, 100.0, number)
    for path in paths:
        rel = path.relative_to(root).as_posix()
        for line_number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            for token in TOKEN.findall(line):
                value = float(token)
                for (decimals, scale), shown in wanted.items():
                    for number in shown.get(rounded(value * scale, decimals), ()):
                        if len(number.sources) < MAX_PLACES and not any(s["file"] == rel and s["line"] == line_number
                                                                        for s in number.sources):
                            number.sources.append({"file": rel, "line": line_number, "text": line.strip()[:160]})


def report(root: Path, cfg: ProjectConfig) -> dict:
    latex_dir = inside(root, cfg.latex_dir)
    results = inside(root, cfg.results_dir) if cfg.results_dir else None
    found: list[Number] = []
    for path in source_files(latex_dir, {".tex"}):
        if results and results in path.parents:
            continue
        found.extend(in_text(path.read_text(errors="replace"), path.relative_to(root).as_posix()))
    paths, skipped = result_files(root, cfg)
    link(found, paths, root)
    return {"has_results": bool(paths), "searched": len(paths), "skipped": skipped,
            "found": sum(1 for n in found if n.sources), "missing": sum(1 for n in found if not n.sources),
            "numbers": [n.to_dict() for n in found]}


def search_results(root: Path, cfg: ProjectConfig, query: str) -> str:
    """The agent's tool: a number, by rounding as above, or else a regular expression."""
    paths, skipped = result_files(root, cfg)
    if not paths:
        raise ToolError("This paper has no result files to search. Its results folder is set in the project's settings."
                        + (f" Not searched: {'; '.join(skipped[:5])}" if skipped else ""))
    hits: list[str] = []
    if re.fullmatch(r"-?\d+(?:\.\d+)?", query.strip()):
        number = Number("", 0, query, query.strip().lstrip("-"), "")
        number_paths = paths
        for path in number_paths:
            link([number], [path], root)
        hits = [f"{s['file']}:{s['line']}: {s['text']}" for s in number.sources]
    else:
        try:
            pattern = re.compile(query)
        except re.error as e:
            raise ToolError(f"That is not a number or a valid regular expression: {e}") from e
        for path in paths:
            for line_number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                if pattern.search(line):
                    hits.append(f"{path.relative_to(root).as_posix()}:{line_number}: {line.strip()[:200]}")
                    if len(hits) >= MAX_SEARCH_HITS:
                        break
            if len(hits) >= MAX_SEARCH_HITS:
                break
    text = "\n".join(hits) or "Not found in the result files."
    return text + (f"\nNot searched: {'; '.join(skipped[:8])}" if skipped else "")


def changed(old_text: str, new_text: str) -> list[dict]:
    """The numbers the text has more or fewer of than the baseline did: a changed value shows up as one of each."""
    def counted(text: str) -> Counter:
        return Counter(n.digits for n in in_text(text, ""))

    old, new = counted(old_text), counted(new_text)
    return [{"value": value, "before": old[value], "after": new[value]} for value in sorted(set(old) | set(new))
            if old[value] != new[value]]

"""Writing rules the editor warns about and the verification report checks.

A paper's style file is usually prose for people and for the agent, so only two things in it become mechanical checks:
lines of the form `forbid: /regex/ message`, and a few rules recognized by name. Anything else stays with the agent.
"""

from __future__ import annotations

import re
from pathlib import Path

from .projects import ProjectConfig, inside

COMMON = [
    {"pattern": r"\b(\w+)\s+\1\b", "message": "A word is repeated.", "flags": "i"},
    {"pattern": r"\b(?:TODO|FIXME|XXX)\b", "message": "A note left in the text.", "flags": ""},
]
# Rules recognized in a style file's prose: words to look for, and the check they stand for.
NAMED = [
    (re.compile(r"em[- ]dash", re.I), {"pattern": r"—|(?<!-)---(?!-)", "message": "An em dash. Your style rules avoid them.", "flags": ""}),
    (re.compile(r"Supplementary Fig\.?", re.I), {"pattern": r"Supplementary Fig\.", "message": "\"Supplementary Fig.\": check this against your style rules.", "flags": ""}),
]
FORBID = re.compile(r"^\s*forbid:\s*/(.+)/(i?)\s*(.*)$")


def rules(root: Path, cfg: ProjectConfig) -> list[dict]:
    found = list(COMMON)
    if not cfg.style_rules:
        return found
    try:
        text = inside(root, cfg.style_rules).read_text(errors="replace")
    except (OSError, ValueError):
        return found
    for needle, rule in NAMED:
        if needle.search(text):
            found.append(rule)
    for line in text.splitlines():
        match = FORBID.match(line)
        if match:
            try:
                re.compile(match.group(1))
            except re.error:
                continue
            found.append({"pattern": match.group(1), "message": match.group(3).strip() or f"Your style rules forbid /{match.group(1)}/.", "flags": match.group(2)})
    return found


def strip_comment(line: str) -> str:
    return re.sub(r"(?<!\\)%.*", "", line)


def check(text: str, found_rules: list[dict]) -> list[dict]:
    """Where the rules are broken in a file's text, by line, skipping comments and the preamble."""
    out = []
    start = text.find("\\begin{document}")
    offset = text.count("\n", 0, start) + 1 if start >= 0 else 1
    body = text[start:] if start >= 0 else text
    compiled = [(re.compile(r["pattern"], re.I if r.get("flags") == "i" else 0), r["message"]) for r in found_rules]
    for number, line in enumerate(body.split("\n"), offset):
        clean = strip_comment(line)
        for rx, message in compiled:
            for match in rx.finditer(clean):
                out.append({"line": number, "text": match.group(0)[:80], "message": message})
    return out

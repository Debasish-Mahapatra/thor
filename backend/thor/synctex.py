"""Jumps between source and PDF with the synctex tool and the .synctex.gz file a build writes.

Positions are PDF points from the top-left corner of the page, the frame PDF.js draws in.
"""

from __future__ import annotations

import os
from pathlib import Path

from .paths import run_tool


def records(output: str, keys: tuple[str, ...]) -> list[dict[str, str]]:
    """synctex prints "Key:value" lines; a record starts again at each repeat of the first key."""
    found: list[dict[str, str]] = []
    for line in output.splitlines():
        key, sep, value = line.partition(":")
        if not sep or key not in keys:
            continue
        if key == keys[0] or not found:
            found.append({})
        found[-1][key] = value
    return found


def forward(pdf: Path, latex_dir: Path, file: str, line: int, column: int = 0) -> list[dict]:
    """Boxes on the PDF for a source line: page (1-based), left, top, width, height."""
    source = str(latex_dir / file)
    code, out = run_tool(["synctex", "view", "-i", f"{line}:{column}:{source}", "-o", str(pdf)], timeout=20,
                         cwd=latex_dir)
    if code != 0:
        return []
    boxes = []
    for r in records(out, ("Page", "x", "y", "h", "v", "W", "H")):
        try:
            h, v, w, height = float(r["h"]), float(r["v"]), float(r["W"]), float(r["H"])
            boxes.append({"page": int(r["Page"]), "left": h, "top": v - height, "width": w, "height": height})
        except (KeyError, ValueError):
            continue
    return boxes


def backward(pdf: Path, latex_dir: Path, page: int, x: float, y: float) -> dict | None:
    """The source file (relative to the LaTeX folder) and line at a point on a PDF page."""
    code, out = run_tool(["synctex", "edit", "-o", f"{page}:{x:.2f}:{y:.2f}:{pdf}"], timeout=20, cwd=latex_dir)
    if code != 0:
        return None
    for r in records(out, ("Input", "Line", "Column")):
        try:
            path = os.path.normpath(r["Input"])
            line = int(r["Line"])
        except (KeyError, ValueError):
            continue
        rel = os.path.relpath(path, latex_dir) if os.path.isabs(path) else path
        if rel.startswith(".."):
            continue
        return {"file": rel, "line": max(1, line)}
    return None

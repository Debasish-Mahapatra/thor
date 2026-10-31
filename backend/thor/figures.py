"""PNG figures that pdfTeX can embed without decoding them, for the editor's compiles only.

pdfTeX copies a plain PNG's data as it stands. If the PNG has an alpha channel, as every figure from
matplotlib has, it decodes the image, splits off the alpha and compresses both again, which is most of a
compile's time on a paper with large figures. Yet that alpha is usually opaque everywhere, and then the same
pixels without it look the same. These copies are made once, in the background, and kept in the project's
builds folder under the same relative names; the editor's compiles find them first through TEXINPUTS.

Figures with real transparency are left alone, and deliverables never see these copies.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from PIL import Image

from .spare import recorded_inputs

MANIFEST = "manifest.json"
# Below this, a figure costs pdfTeX too little to matter.
MIN_BYTES = 200_000

_lock = threading.Lock()
_running: set[str] = set()


def _manifest(shadow: Path) -> dict:
    try:
        return json.loads((shadow / MANIFEST).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(shadow: Path, manifest: dict) -> None:
    tmp = shadow / f"{MANIFEST}.tmp"
    tmp.write_text(json.dumps(manifest))
    tmp.replace(shadow / MANIFEST)


def _stamp(path: Path) -> list[int]:
    info = path.stat()
    return [info.st_size, info.st_mtime_ns]


def search_path(shadow: Path, *also: Path) -> str:
    """TEXINPUTS with these copies first. The empty last entry keeps TeX's default search."""
    parts = [str(shadow), ".", *map(str, also), os.environ.get("TEXINPUTS", "").strip(":")]
    return ":".join(p for p in parts if p) + ":"


def drop_stale(latex_dir: Path, shadow: Path) -> None:
    """Before a compile: a figure that changed must not be found through its old copy."""
    with _lock:
        manifest = _manifest(shadow)
        changed = False
        for rel, known in list(manifest.items()):
            source = latex_dir / rel
            if not source.is_file() or _stamp(source) != known["stamp"]:
                (shadow / rel).unlink(missing_ok=True)
                del manifest[rel]
                changed = True
        if changed:
            _save(shadow, manifest)


def needs_copy(image: Image.Image) -> bool:
    """True when pdfTeX has to decode this PNG but need not: an opaque alpha channel, or interlacing."""
    if image.mode in ("RGBA", "LA"):
        return image.getchannel("A").getextrema() == (255, 255)
    if "transparency" in image.info or image.mode not in ("RGB", "L", "P", "1"):
        return False
    return bool(image.info.get("interlace"))


def make_copy(source: Path, target: Path) -> bool:
    """Writes the fast copy of source if it needs one; True if it did."""
    with Image.open(source) as image:
        if image.format != "PNG" or not needs_copy(image):
            return False
        keep = {k: image.info[k] for k in ("dpi", "icc_profile") if k in image.info}
        mode = {"RGBA": "RGB", "LA": "L"}.get(image.mode, image.mode)
        plain = image.convert(mode)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.{os.getpid()}")
    try:
        plain.save(partial, "PNG", compress_level=6, **keep)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return True


def refresh(latex_dir: Path, shadow: Path, fls: Path) -> None:
    """After a compile: makes copies, in the background, of the figures it read that have none yet."""
    wanted = []
    known = _manifest(shadow)
    for path in recorded_inputs(fls, latex_dir):
        if path.suffix.lower() != ".png" or latex_dir not in path.parents:
            continue
        rel = path.relative_to(latex_dir).as_posix()
        try:
            if rel not in known and path.stat().st_size >= MIN_BYTES:
                wanted.append(rel)
        except OSError:
            continue
    if not wanted:
        return
    key = str(shadow)
    with _lock:
        if key in _running:
            return
        _running.add(key)

    def work() -> None:
        try:
            for rel in wanted:
                source = latex_dir / rel
                try:
                    stamp = _stamp(source)
                    made = make_copy(source, shadow / rel)
                # Pillow raises many kinds of errors on odd files; pdfTeX then simply reads the original.
                except Exception:  # noqa: BLE001
                    continue
                with _lock:
                    manifest = _manifest(shadow)
                    manifest[rel] = {"stamp": stamp, "copied": made}
                    shadow.mkdir(parents=True, exist_ok=True)
                    _save(shadow, manifest)
        finally:
            with _lock:
                _running.discard(key)

    threading.Thread(target=work, daemon=True).start()


def real_paths_in_fls(fls: Path, shadow: Path, latex_dir: Path) -> None:
    """Names the original figures in a compile's .fls, which the revision snapshot reads to find the source."""
    if not fls.is_file():
        return
    prefix = f"{shadow}/"
    text = fls.read_text(errors="replace")
    if prefix not in text:
        return
    lines = []
    for line in text.splitlines():
        if line.startswith("INPUT ") and line[6:].startswith(prefix):
            line = f"INPUT {latex_dir / line[6 + len(prefix):]}"
        lines.append(line)
    fls.write_text("\n".join(lines) + "\n")

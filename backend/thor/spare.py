"""A LaTeX process kept ready between the editor's compiles, with the preamble already read.

Reading a paper's packages takes most of a second on every compile. So after each compile a new LaTeX is
started on a frozen copy of the lines before \\begin{document}, and it then waits on TeX's \\read. A compile
wakes it, and it reads main.tex from the top with every character ignored until the \\begin{document} line.
The lines it skips are never tokenized, so nothing in them can upset it; line numbers, SyncTeX and \\jobname
are those of main.tex; and .aux is read after the edit, as in any pass.

The process is used only if the preamble and everything it read are unchanged, on the day it started
(\\today). Otherwise it is discarded and the compile runs as usual. If its stdin closes, because Thor quit,
TeX stops at the \\read with "End of file on the terminal".
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from pathlib import Path

from .paths import clone_file

READY = "THOR-SPARE-READY"
PREAMBLE_NAME = "thor-preamble.tex"
KEPT_SUFFIX = ".thor-kept"
# How long a compile waits for a spare that is still reading its preamble.
READY_TIMEOUT = 120
BEGIN_DOCUMENT = re.compile(rb"^[ \t]*\\begin[ \t]*\{document\}", re.M)
# Files a preamble can empty just by being read: multibib's \\newcites opens S.aux, \\makeindex the .idx, and so on.
EMPTIED_BY_PREAMBLE = {".aux", ".idx", ".glo", ".nlo", ".acn", ".bcf"}
# With -file-line-error, errors look like "./main.tex:3: ..."; some still start with "!".
ERROR_LINE = re.compile(r"^(?:!|\S*:\d+: )")


def as_it_was(path: Path) -> Path:
    """The file to read for what the last compile left: a waiting spare may have emptied the real one."""
    kept = path.with_name(path.name + KEPT_SUFFIX)
    return kept if kept.is_file() else path


def begin_line(data: bytes) -> int | None:
    """The 1-based line that starts with \\begin{document}; None if the preamble has no line of its own."""
    found = BEGIN_DOCUMENT.search(data)
    if not found:
        return None
    line = data.count(b"\n", 0, found.start()) + 1
    return line if line > 1 else None


def wake_command(main: str, line: int, setup: str, wide: list[int]) -> str:
    """What LaTeX is told to do on its command line. \\count255 is TeX's scratch register, and the counter is
    a macro, so no register is allocated and the document's own allocation numbers stay the same.

    pdfTeX reads bytes, but XeTeX and LuaTeX read Unicode, so wide lists the characters above 255 that the
    preamble contains."""
    ignore_wide = "".join(f"\\catcode{c}=9 " for c in wide)
    return (
        setup + r"\def\thorline{1}\begingroup\catcode13=13 "
        r"\gdef\thorresume{\begingroup\count255=0 "
        r"\loop\catcode\count255=9 \advance\count255 by1 \ifnum\count255<256 \repeat" + ignore_wide +
        r"\catcode13=13 \def^^M{\xdef\thorline{\the\numexpr\thorline+1}"
        rf"\ifnum\thorline={line} \endgroup\fi}}\input{{{main}}}}}"
        rf"\endgroup\input{{{PREAMBLE_NAME}}}\immediate\write16{{{READY}}}"
        r"\scrollmode\read16 to\thorgo\nonstopmode\thorresume"
    )


def recorded_inputs(fls: Path, base: Path) -> list[Path]:
    """The files a LaTeX run has read so far, from its .fls."""
    if not fls.is_file():
        return []
    paths = []
    for line in fls.read_text(errors="replace").splitlines():
        if line.startswith("INPUT "):
            paths.append(Path(os.path.normpath(base / line[6:].strip())))
    return list(dict.fromkeys(paths))


class Spare:
    def __init__(self, latex_dir: Path, main: str, work: Path, args: list[str], env: dict[str, str],
                 preamble: bytes, line: int) -> None:
        self.latex_dir = latex_dir
        self.work = work
        self.stem = Path(main).stem
        self.preamble = preamble
        self.line = line
        self.day = time.strftime("%Y%m%d")
        self.started_ns = time.time_ns()
        self.ready = threading.Event()
        self.output: list[str] = []
        self.errors: list[str] = []
        self.inputs: dict[Path, tuple[int, int]] = {}
        self._discarded = False
        # LaTeX empties the .log and .fls as it starts, and the preamble may empty others. Until this spare is woken,
        # those files as they were still count: latexmk wants them if it is discarded, and as_it_was reads them.
        self._kept = []
        targets = [work / f"{self.stem}{suffix}" for suffix in (".log", ".fls")]
        targets += [f for f in work.iterdir() if f.suffix in EMPTIED_BY_PREAMBLE and f.name != f"{self.stem}.aux"]
        for target in targets:
            if target.is_file():
                kept = target.with_name(target.name + KEPT_SUFFIX)
                clone_file(target, kept)
                self._kept.append((kept, target))
        self.process = subprocess.Popen(
            args, cwd=latex_dir, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace",
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        for line in self.process.stdout:
            self.output.append(line)
            if not self.ready.is_set():
                if line.startswith(READY):
                    self.inputs = self._input_state()
                    self.ready.set()
                elif ERROR_LINE.match(line):
                    self.errors.append(line.strip())
        self.ready.set()

    def _input_state(self) -> dict[Path, tuple[int, int]]:
        state = {}
        for path in recorded_inputs(self.work / f"{self.stem}.fls", self.latex_dir):
            # What LaTeX wrote itself (a filecontents bibliography) is newer than this process by nature.
            if self.work not in path.parents:
                try:
                    info = path.stat()
                except OSError:
                    continue
                state[path] = (info.st_size, info.st_mtime_ns)
        return state

    def usable(self, data: bytes) -> bool:
        """True when waking this process gives what a fresh LaTeX would: same preamble, same files read."""
        if self.process.poll() is not None or self.day != time.strftime("%Y%m%d"):
            return False
        if not data.startswith(self.preamble) or begin_line(data) != self.line:
            return False
        if not self.ready.wait(READY_TIMEOUT) or self.errors or self.process.poll() is not None:
            return False
        for path, known in self.inputs.items():
            try:
                info = path.stat()
            except OSError:
                return False
            if (info.st_size, info.st_mtime_ns) != known or info.st_mtime_ns > self.started_ns:
                return False
        return True

    def failed_in_preamble(self) -> bool:
        """The preamble copy did not work: it ended with an error or LaTeX stopped before the \\read."""
        return self.ready.is_set() and (bool(self.errors) or not self.inputs)

    def wake(self, timeout: float) -> tuple[int, str]:
        self._forget_kept()
        try:
            self.process.stdin.write("go\n")
            self.process.stdin.close()
            code = self.process.wait(timeout)
        except (OSError, ValueError):
            code = self.process.wait(timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            return -1, f"no answer within {timeout:g} s"
        self._discarded = True
        return code, "".join(self.output)

    def discard(self) -> None:
        if self._discarded:
            return
        self._discarded = True
        self.process.kill()
        self.process.wait()
        for kept, target in self._kept:
            if kept.is_file():
                os.replace(kept, target)
        self._kept = []

    def _forget_kept(self) -> None:
        for kept, _ in self._kept:
            kept.unlink(missing_ok=True)
        self._kept = []

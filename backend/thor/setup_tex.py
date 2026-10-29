"""Installs MacTeX from inside the app, as the first-run setup of a .dmg install will.

It uses a MacTeX package already on the Mac (in Homebrew's cache) or downloads the official one
from CTAN and checks its SHA-512. Either way it runs the package only if it is signed by MacTeX's
maintainer and notarized by Apple. Apple's installer then runs with administrator rights: macOS asks for
the password in its own dialog, and the app never sees it.
"""

from __future__ import annotations

import hashlib
import shlex
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .paths import APP_NAME, app_home, run_tool

MACTEX_URL = "https://mirror.ctan.org/systems/mac/mactex/MacTeX.pkg"
CHECKSUM_URL = MACTEX_URL + ".sha512"
# MacTeX is signed with Richard Koch's Developer ID; the team ID is what identifies it.
SIGNER = "Developer ID Installer: Richard Koch (RBGCY5RJWM)"
TEXBIN = Path("/Library/TeX/texbin")
CHUNK = 1 << 20
INSTALL_TIMEOUT = 3600


def search_dirs() -> list[Path]:
    # Not ~/Downloads: reading it would make macOS ask the user for permission.
    return [app_home() / "downloads", Path.home() / "Library" / "Caches" / "Homebrew" / "downloads"]


class Stopped(Exception):
    pass


@dataclass
class InstallState:
    # idle, finding, downloading, verifying, installing, done, failed, cancelled
    step: str = "idle"
    message: str = ""
    downloaded: int = 0
    total: int | None = None
    package: str | None = None


class TexInstaller:
    def __init__(
        self,
        downloads: Path | None = None,
        search: Callable[[], list[Path]] = search_dirs,
        texbin: Path = TEXBIN,
        run: Callable[..., tuple[int, str]] = run_tool,
        open_url: Callable[[str], object] = urllib.request.urlopen,
    ) -> None:
        self.downloads = downloads or app_home() / "downloads"
        self.search = search
        self.texbin = texbin
        self.run = run
        self.open_url = open_url
        self.state = InstallState()
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    def installed(self) -> bool:
        return (self.texbin / "latexmk").exists() and (self.texbin / "pdflatex").exists()

    def status(self) -> dict:
        with self._lock:
            return {**asdict(self.state), "installed": self.installed(), "running": self.running()}

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> dict:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("MacTeX is already being installed.")
            self._cancel.clear()
            self.state = InstallState(step="finding", message="Looking for a MacTeX package on this Mac.")
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self.status()

    def cancel(self) -> None:
        """Stops a download. Apple's installer, once it has started, is left to finish."""
        self._cancel.set()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _set(self, **changes: object) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(self.state, key, value)

    def _run(self) -> None:
        try:
            package = self._find_local()
            if package:
                self._set(package=str(package), message=f"Using {package.name} found in {package.parent}.")
            else:
                package = self._download()
            self._set(step="verifying", message="Checking that the package is MacTeX, signed and notarized.")
            problem = self.signature_problem(package)
            if problem:
                raise RuntimeError(problem)
            self._install(package)
        except Stopped:
            self._set(step="cancelled", message="Stopped. Nothing was installed.")
        except Exception as e:  # reported to the user; the thread must not die silently
            self._set(step="failed", message=str(e))

    def _find_local(self) -> Path | None:
        found = []
        for folder in self.search():
            if folder.is_dir():
                found += [p for p in folder.iterdir() if p.suffix == ".pkg" and "mactex" in p.name.lower()]
        for package in sorted(found, key=lambda p: p.stat().st_mtime, reverse=True):
            if self.signature_problem(package) is None:
                return package
        return None

    def signature_problem(self, package: Path) -> str | None:
        code, out = self.run(["/usr/sbin/pkgutil", "--check-signature", str(package)], timeout=300)
        if code != 0:
            return f"{package.name} is not signed: {out.splitlines()[-1] if out else 'pkgutil failed'}."
        if "signed by a developer certificate issued by Apple for distribution" not in out:
            return f"{package.name} is not signed with an Apple Developer ID."
        if "Notarization: trusted by the Apple notary service" not in out:
            return f"{package.name} is not notarized by Apple."
        if SIGNER not in out:
            return f"{package.name} is not signed by MacTeX's maintainer ({SIGNER})."
        return None

    def _download(self) -> Path:
        self.downloads.mkdir(parents=True, exist_ok=True)
        target = self.downloads / "MacTeX.pkg"
        partial = target.with_suffix(".pkg.part")
        self._set(step="downloading", message="Downloading MacTeX from CTAN.", downloaded=0, total=None)
        with self.open_url(CHECKSUM_URL) as response:
            expected = response.read().decode().split()[0].lower()
        digest = hashlib.sha512()
        with self.open_url(MACTEX_URL) as response, open(partial, "wb") as out:
            length = response.headers.get("Content-Length")
            self._set(total=int(length) if length else None)
            done = 0
            while chunk := response.read(CHUNK):
                if self._cancel.is_set():
                    out.close()
                    partial.unlink(missing_ok=True)
                    raise Stopped
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                self._set(downloaded=done)
        if digest.hexdigest() != expected:
            partial.unlink(missing_ok=True)
            raise RuntimeError("The download does not match CTAN's checksum, so it was deleted. Try again.")
        partial.replace(target)
        self._set(package=str(target))
        return target

    def _install(self, package: Path) -> None:
        self._set(step="installing",
                  message="Installing MacTeX: enter your password when macOS asks. This takes several minutes.")
        if self._cancel.is_set():
            raise Stopped
        command = f"/usr/sbin/installer -pkg {shlex.quote(str(package))} -target /"
        prompt = f"{APP_NAME} needs your password to install MacTeX."
        script = f"do shell script {applescript_string(command)} with prompt {applescript_string(prompt)} with administrator privileges"
        started = time.time()
        code, out = self.run(["/usr/bin/osascript", "-e", script], timeout=INSTALL_TIMEOUT)
        if code != 0:
            if "-128" in out or "User canceled" in out:
                raise Stopped
            raise RuntimeError(f"Apple's installer failed: {out.splitlines()[-1] if out else 'no output'}")
        if not self.installed():
            raise RuntimeError(f"The installer finished, but {self.texbin}/latexmk is missing.")
        minutes = max(1, round((time.time() - started) / 60))
        self._set(step="done", message=f"MacTeX is installed (took about {minutes} min).")


def applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'

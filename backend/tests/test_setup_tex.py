from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from thor import setup_tex
from thor.paths import APP_NAME
from thor.setup_tex import TexInstaller

GOOD_SIGNATURE = f"""Package "x.pkg":
   Status: signed by a developer certificate issued by Apple for distribution
   Notarization: trusted by the Apple notary service
   Certificate Chain:
    1. {setup_tex.SIGNER}
    2. Developer ID Certification Authority
"""


class FakeMac:
    """Stands in for pkgutil and osascript; installing creates the texbin tools."""

    def __init__(self, texbin: Path, signature: str = GOOD_SIGNATURE, install: tuple[int, str] = (0, "")) -> None:
        self.texbin = texbin
        self.signature = signature
        self.install = install
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], timeout: float = 15) -> tuple[int, str]:
        self.calls.append(args)
        if args[0].endswith("pkgutil"):
            return 0, self.signature
        if args[0].endswith("osascript"):
            if self.install[0] == 0:
                self.texbin.mkdir(parents=True, exist_ok=True)
                (self.texbin / "latexmk").touch()
                (self.texbin / "pdflatex").touch()
            return self.install
        raise AssertionError(args)


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


def installer(tmp_path: Path, mac: FakeMac, local: list[Path] | None = None, urls: dict | None = None) -> TexInstaller:
    search_dir = tmp_path / "Downloads"
    search_dir.mkdir(exist_ok=True)
    for p in local or []:
        (search_dir / p.name).write_bytes(b"pkg")
    return TexInstaller(
        downloads=tmp_path / "thor-downloads",
        search=lambda: [search_dir, tmp_path / "missing"],
        texbin=tmp_path / "texbin",
        run=mac,
        open_url=lambda url: FakeResponse((urls or {})[url]),
    )


def finish(inst: TexInstaller) -> dict:
    inst.start()
    inst.wait(10)
    return inst.status()


def test_installs_a_local_package(tmp_path: Path) -> None:
    mac = FakeMac(tmp_path / "texbin")
    inst = installer(tmp_path, mac, local=[Path("mactex-20260324.pkg")])
    status = finish(inst)
    assert status["step"] == "done" and status["installed"] is True
    assert status["package"].endswith("mactex-20260324.pkg")
    script = mac.calls[-1][2]
    assert "with administrator privileges" in script and "installer -pkg" in script
    assert f"{APP_NAME} needs your password to install MacTeX." in script


def test_downloads_and_checks_the_checksum(tmp_path: Path) -> None:
    data = b"MacTeX bytes" * 1000
    urls = {setup_tex.MACTEX_URL: data,
            setup_tex.CHECKSUM_URL: f"{hashlib.sha512(data).hexdigest()}  MacTeX.pkg\n".encode()}
    status = finish(installer(tmp_path, FakeMac(tmp_path / "texbin"), urls=urls))
    assert status["step"] == "done"
    assert status["downloaded"] == status["total"] == len(data)
    assert (tmp_path / "thor-downloads" / "MacTeX.pkg").read_bytes() == data


def test_bad_checksum_deletes_the_download(tmp_path: Path) -> None:
    urls = {setup_tex.MACTEX_URL: b"tampered", setup_tex.CHECKSUM_URL: b"00ff  MacTeX.pkg\n"}
    status = finish(installer(tmp_path, FakeMac(tmp_path / "texbin"), urls=urls))
    assert status["step"] == "failed" and "checksum" in status["message"]
    assert list((tmp_path / "thor-downloads").iterdir()) == []


@pytest.mark.parametrize(
    "signature, message",
    [
        ("Status: no signature", "Apple Developer ID"),
        (GOOD_SIGNATURE.replace("Notarization: trusted by the Apple notary service", ""), "not notarized"),
        (GOOD_SIGNATURE.replace(setup_tex.SIGNER, "Developer ID Installer: Someone Else (XXXX)"), "maintainer"),
    ],
)
def test_wrong_signature_is_refused(tmp_path: Path, signature: str, message: str) -> None:
    mac = FakeMac(tmp_path / "texbin", signature=signature)
    inst = installer(tmp_path, mac)
    assert message in inst.signature_problem(tmp_path / "x.pkg")
    # A local package with a wrong signature is skipped, never installed.
    urls = {setup_tex.MACTEX_URL: b"x", setup_tex.CHECKSUM_URL: hashlib.sha512(b"x").hexdigest().encode()}
    inst = installer(tmp_path, mac, local=[Path("MacTeX-fake.pkg")], urls=urls)
    status = finish(inst)
    assert status["step"] == "failed" and message in status["message"]
    assert not any(c[0].endswith("osascript") for c in mac.calls)


def test_cancelled_password_dialog(tmp_path: Path) -> None:
    mac = FakeMac(tmp_path / "texbin", install=(1, "execution error: User canceled. (-128)"))
    status = finish(installer(tmp_path, mac, local=[Path("MacTeX.pkg")]))
    assert status["step"] == "cancelled" and "Nothing was installed" in status["message"]
    assert status["installed"] is False


def test_installer_failure_is_reported(tmp_path: Path) -> None:
    mac = FakeMac(tmp_path / "texbin", install=(1, "installer: The install failed."))
    status = finish(installer(tmp_path, mac, local=[Path("MacTeX.pkg")]))
    assert status["step"] == "failed" and "The install failed." in status["message"]


def test_only_one_install_at_a_time(tmp_path: Path) -> None:
    inst = installer(tmp_path, FakeMac(tmp_path / "texbin"), local=[Path("MacTeX.pkg")])
    inst._thread = type("Alive", (), {"is_alive": lambda self: True})()
    with pytest.raises(RuntimeError, match="already"):
        inst.start()


def test_applescript_quoting() -> None:
    assert setup_tex.applescript_string('a "b" \\ c') == '"a \\"b\\" \\\\ c"'

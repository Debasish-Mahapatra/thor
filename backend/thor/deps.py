"""The start-up dependency check: what is installed, what each piece is for, and how to install what is missing."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from importlib import metadata

from . import claude_cli
from .agent import providers
from .paths import APP_NAME, run_tool, which
from .settings import AgentSettings, SecretStore

MACTEX_FIX = f"{APP_NAME} installs MacTeX when the app starts. From Terminal: `brew install --cask mactex-no-gui`."


@dataclass
class Check:
    id: str
    name: str
    group: str
    needed_for: str
    required: bool
    ok: bool
    version: str | None = None
    path: str | None = None
    detail: str = ""
    fix: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# (command, version flag, needed for, required)
TEX_TOOLS = [
    ("latexmk", "-v", "every build", True),
    ("pdflatex", "--version", "pdfLaTeX builds", True),
    ("bibtex", "--version", "bibliographies (natbib)", True),
    ("latexdiff", "--version", "tracked PDFs", True),
    ("synctex", "version", "jumping between source and PDF", True),
    ("texcount", "--version", "word counts", True),
    ("kpsewhich", "--version", "finding LaTeX packages", True),
    ("xelatex", "--version", "XeLaTeX builds", False),
    ("lualatex", "--version", "LuaLaTeX builds", False),
    ("biber", "--version", "biblatex bibliographies", False),
]

# Files the tracked PDF and the reply document rely on.
TEX_FILES = ["ulem.sty", "lineno.sty", "natbib.sty", "multibib.sty", "glyphtounicode.tex"]

PYTHON_PACKAGES = [
    ("PyMuPDF", "importing PDF comments, page text"),
    ("pypdf", "splitting PDFs"),
    ("Pillow", "figure checks"),
    ("claude-agent-sdk", "the agent with Claude Code"),
    ("anthropic", "the agent with an Anthropic API key"),
    ("httpx", "the agent with an OpenAI or compatible API"),
    ("fastapi", "the app itself"),
]


def first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:100]
    return ""


def check_tool(name: str, flag: str, needed_for: str, required: bool, group: str, fix: str | None) -> Check:
    path = which(name)
    if not path:
        return Check(name, name, group, needed_for, required, False, detail="Not found.", fix=fix)
    code, out = run_tool([path, flag])
    version = first_line(out) if code == 0 else None
    return Check(name, name, group, needed_for, required, True, version=version, path=path)


def check_tex_files() -> Check:
    path = which("kpsewhich")
    if not path:
        return Check("tex-files", "LaTeX packages", "TeX", "tracked PDFs, line numbers", True, False,
                     detail="kpsewhich is missing, so packages cannot be looked up.", fix=MACTEX_FIX)
    missing = [f for f in TEX_FILES if run_tool([path, f])[0] != 0]
    if missing:
        return Check("tex-files", "LaTeX packages", "TeX", "tracked PDFs, line numbers", True, False,
                     detail="Missing: " + ", ".join(missing), fix="Install them with `sudo tlmgr install "
                     + " ".join(f.rsplit(".", 1)[0] for f in missing) + "`.")
    return Check("tex-files", "LaTeX packages", "TeX", "tracked PDFs, line numbers", True, True,
                 detail=", ".join(TEX_FILES))


def check_python() -> list[Check]:
    v = sys.version_info
    checks = [Check("python", "Python", "Python", "the backend", True, v >= (3, 11),
                    version=f"{v.major}.{v.minor}.{v.micro}", path=sys.executable,
                    fix=None if v >= (3, 11) else "Run `make setup`, which creates the app's Python 3.12 venv.")]
    for package, needed_for in PYTHON_PACKAGES:
        try:
            version = metadata.version(package)
            checks.append(Check(package.lower(), package, "Python", needed_for, True, True, version=version))
        except metadata.PackageNotFoundError:
            checks.append(Check(package.lower(), package, "Python", needed_for, True, False, detail="Not installed.",
                                fix="Run `make setup` in the app's source folder."))
    return checks


def check_agent(agent: AgentSettings, secret: SecretStore) -> Check:
    found = providers.catalog(agent, secret)
    provider = next(p for p in found if p["id"] == agent.provider)
    usable = next((p for p in found if p["id"] == providers.effective(agent, found)), provider)
    if not provider["ready"] and usable["ready"]:
        # Nothing is missing while another model can do the work.
        return Check("agent", usable["name"], "Agent", "the agent", True, True, path=usable.get("path"),
                     version=usable.get("version"),
                     detail=f"{provider['name']} is not set up, so the agent uses {usable['name']}.")
    if agent.provider != "claude_code":
        return Check("agent", provider["name"], "Agent", "the agent", True, provider["ready"], path=provider.get("path"),
                     version=provider.get("version"), detail=provider["detail"],
                     fix=None if provider["ready"] else provider.get("fix") or "Fix this in Settings.")
    st = claude_cli.status(agent.claude_path or None)
    if not st.path:
        return Check("agent", "Claude Code", "Agent", "the agent (Claude plan)", True, False, detail=st.detail,
                     fix="Install Claude Code with `curl -fsSL https://claude.ai/install.sh | bash`, or set its location in Settings.")
    signed_in = st.logged_in is not False
    who = ", ".join(x for x in (st.email, st.plan and f"Claude {st.plan.capitalize()}") if x)
    return Check("agent", "Claude Code", "Agent", "the agent (Claude plan)", True, signed_in,
                 version=st.version, path=st.path,
                 detail=(f"Signed in ({who})." if who else "Signed in.") if st.logged_in else st.detail,
                 fix=None if signed_in else "Run `claude` in Terminal and sign in.")


def run_checks(agent: AgentSettings, secret: SecretStore) -> list[Check]:
    tex_jobs = [(n, f, w, r, "TeX", MACTEX_FIX) for n, f, w, r in TEX_TOOLS]
    other_jobs = [
        ("git", "--version", "commits (not used yet)", False, "Other", "Install the Xcode command-line tools with `xcode-select --install`."),
        ("ssh", "-V", "builds and scripts on a remote machine", False, "Other", None),
    ]
    with ThreadPoolExecutor(max_workers=8) as pool:
        tools = [pool.submit(check_tool, *job) for job in tex_jobs + other_jobs]
        tex_files = pool.submit(check_tex_files)
        agent_check = pool.submit(check_agent, agent, secret)
        results = [f.result() for f in tools[: len(tex_jobs)]] + [tex_files.result()]
        results += check_python()
        results.append(agent_check.result())
        results += [f.result() for f in tools[len(tex_jobs):]]
    return results


def summary(checks: list[Check]) -> dict:
    missing = [c.name for c in checks if c.required and not c.ok]
    return {"ok": not missing, "missing": missing}

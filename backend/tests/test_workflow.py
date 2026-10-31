"""The revision workflow around the agent: plan, reply, checks, git, Zotero, submission. Models are fake, as in
test_agent.py; the network is httpx's mock transport."""

from __future__ import annotations

import json
import subprocess
import time
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from .conftest import write_executable
from thor import build, documents, gitops, numbers, plan, projects, references, reply, scripts, submission, verify, zotero
from thor.agent import codex, textmode
from thor.agent.base import Turn
from thor.agent.tools import Submission, ToolBox, ToolError
from thor.first_run import FirstRun
from thor.history import History
from thor.server import create_app
from thor.settings import MemorySecret, SettingsStore
from thor.setup_tex import TexInstaller
from thor.store import Store

TOKEN = "s3cret"
BASE = "http://127.0.0.1:8123"
MAIN = r"""\documentclass{article}
\begin{document}
\begin{abstract}
A short abstract of seven words here.
\end{abstract}
Warming of 3.14 K was seen, about 12.3\% of the total, in 2015. See Figure 2 and \cite{Price1994}.
A second finding of 41.7 km stands alone. There were 3 runs.
\includegraphics[width=0.5\linewidth]{figs/fig1}
\end{document}
"""


@pytest.fixture(autouse=True)
def basic_tools(bin_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THOR_TOOL_PATH", f"{bin_dir}:/usr/bin:/bin")


def paper_at(tmp_path: Path) -> Path:
    root = tmp_path / "paper"
    (root / "tex" / "figs").mkdir(parents=True)
    (root / "results").mkdir()
    (root / "scripts").mkdir()
    (root / "tex" / "main.tex").write_text(MAIN)
    (root / "tex" / "figs" / "fig1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (root / "results" / "summary.csv").write_text("run,warming,share\na,3.1416,0.123\n")
    (root / "scripts" / "plot_fig1.py").write_text("import sys\nprint('made')\nopen('../tex/figs/fig1.png', 'w').write('new')\n")
    (root / "scripts" / "other.py").write_text("savefig('elsewhere.pdf')\n")
    return root


class IdleSetup(FirstRun):
    def __init__(self, tmp: Path) -> None:
        super().__init__(TexInstaller(downloads=tmp / "dl", search=lambda: [], texbin=tmp / "texbin"), enabled=False)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(TOKEN, MemorySecret(), ui_dir=None, store=Store(tmp_path / "test.db"), settings_store=SettingsStore(),
                     setup=IdleSetup(tmp_path), history=History(tmp_path / "history"))
    return TestClient(app, base_url=BASE, headers={"Authorization": f"Bearer {TOKEN}"})


def folder_with_comments(tmp_path: Path) -> Path:
    folder = tmp_path / "revision2"
    target = folder / "supporting_files" / "comments" / "alice"
    target.mkdir(parents=True)
    data = {"name": "alice", "source_pdf": "a.pdf", "imported": "today", "baseline": "", "aliases": {},
            "comments": [{"id": 1, "kind": "note", "author": "Alice", "page": 1, "line": 3, "marked": "warming", "note": "Which warming?",
                          "insert": "", "replies": []},
                         {"id": 2, "kind": "note", "author": "Alice", "page": 2, "line": None, "marked": "", "note": "Cite this.",
                          "insert": "", "replies": []}]}
    (target / "comments.json").write_text(json.dumps(data))
    return folder


def submitted(*refs: list[str], proposal: str = "Say surface warming.") -> dict:
    return {"summary": "Clarify.", "items": [{"comments": r, "location": "main.tex:5", "request": "Be clear.", "proposal": proposal,
                                              "previous": ""} for r in refs]}


def test_a_plan_leaving_out_a_comment_goes_back_once(tmp_path: Path) -> None:
    folder = folder_with_comments(tmp_path)
    check = plan.checker(folder)
    with pytest.raises(ToolError, match="alice:2"):
        check(submitted(["alice:1"]))
    check(submitted(["alice:1"]))


def test_a_plan_with_no_summary_goes_back(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="summary"):
        plan.checker(folder_with_comments(tmp_path))({"items": submitted(["alice:1", "alice:2"])["items"]})


def test_a_plan_cannot_name_a_comment_that_does_not_exist(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="bob:9"):
        plan.checker(folder_with_comments(tmp_path))(submitted(["bob:9"]))


def test_decisions_gate_approval(tmp_path: Path) -> None:
    folder = folder_with_comments(tmp_path)
    current = plan.save_draft(folder, submitted(["alice:1"], ["alice:2"]), None, "c", "m")
    assert [i["id"] for i in current["items"]] == ["D1", "D2"]
    with pytest.raises(plan.PlanError, match="Decide D1, D2"):
        plan.approve(folder)
    plan.decide(folder, "D1", "approve", "")
    plan.decide(folder, "D2", "change", "Use Price 1994.")
    with pytest.raises(plan.PlanError, match="Draft the plan again"):
        plan.approve(folder)
    plan.decide(folder, "D2", "explain", "It is common knowledge.")
    approved = plan.approve(folder)
    assert approved["status"] == "approved"
    prompt = plan.edit_prompt(approved)
    assert "D1" in prompt and "Leave the text alone for D2" in prompt
    with pytest.raises(plan.PlanError, match="Reopen"):
        plan.decide(folder, "D1", "change", "x")
    assert (plan.folder_of(folder) / "plan.md").is_file()


def test_a_new_draft_keeps_only_decisions_still_true(tmp_path: Path) -> None:
    folder = folder_with_comments(tmp_path)
    first = plan.save_draft(folder, submitted(["alice:1"], ["alice:2"]), None, "c", "m")
    plan.decide(folder, "D1", "approve", "")
    first = plan.decide(folder, "D2", "change", "Other words.")
    again = submitted(["alice:1"], ["alice:2"])
    again["items"][0]["previous"] = "D1"
    again["items"][1].update(previous="D2", proposal="Something else.")
    again["items"].append({"comments": [], "location": "x", "request": "New", "proposal": "New one", "previous": ""})
    second = plan.save_draft(folder, again, first, "c", "m")
    assert second["version"] == 2
    by_id = {i["id"]: i for i in second["items"]}
    assert by_id["D1"]["decision"] == "approve"
    assert by_id["D2"]["decision"] == "" and by_id["D2"]["earlier"][0]["note"] == "Other words."
    assert by_id["D3"]["request"] == "New"
    assert (plan.folder_of(folder) / "plan-v1.json").is_file()


def test_an_approved_item_the_model_reworded_is_not_approved_any_more(tmp_path: Path) -> None:
    folder = folder_with_comments(tmp_path)
    first = plan.save_draft(folder, submitted(["alice:1", "alice:2"]), None, "c", "m")
    first = plan.decide(folder, "D1", "approve", "")
    again = submitted(["alice:1", "alice:2"], proposal="Sneakily different.")
    again["items"][0]["previous"] = "D1"
    assert plan.save_draft(folder, again, first, "c", "m")["items"][0]["decision"] == ""


def turn(tmp_path: Path, box: ToolBox) -> Turn:
    return Turn(prompt="p", instructions="i", cwd=tmp_path, box=box, model="", effort="high", budget_usd=1, emit=lambda e: None,
                cancelled=__import__("threading").Event(), ask=lambda s, d: False)


def test_a_read_only_turn_offers_and_runs_no_writing_tool(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x")
    box = ToolBox(tmp_path, read_only=True, submission=Submission(plan.SUBMIT_PLAN, lambda a: None))
    names = {s["name"] for s in box.specs()}
    assert "submit_plan" in names and "read_file" in names
    assert not names & {"edit_file", "write_file", "compile", "run_script"}
    with pytest.raises(ToolError, match="only read"):
        box.call("write_file", {"path": "a.txt", "content": "y"})
    assert (tmp_path / "a.txt").read_text() == "x"
    box.call("submit_plan", {"summary": "s", "items": []})
    assert box.submission.received == {"summary": "s", "items": []}


def test_the_anthropic_api_is_offered_no_writing_tool_in_a_read_only_turn(tmp_path: Path) -> None:
    from thor.agent import anthropic_api

    box = ToolBox(tmp_path, read_only=True, submission=Submission(plan.SUBMIT_PLAN, lambda a: None))
    the_turn = turn(tmp_path, box)
    the_turn.model = "claude-opus-5-5"
    names = {t["name"] for t in anthropic_api.request_params(the_turn)["tools"]}
    assert "submit_plan" in names and not names & {"edit_file", "write_file", "compile", "run_script"}


def test_a_model_without_tools_hands_in_json(tmp_path: Path) -> None:
    box = ToolBox(tmp_path, read_only=True, submission=Submission(plan.SUBMIT_PLAN, lambda a: None))
    textmode.take_submission(turn(tmp_path, box), 'Here it is:\n```json\n{"summary": "s", "items": []}\n```\nDone.')
    assert box.submission.received["summary"] == "s"


def test_codex_reads_only_and_gets_a_strict_schema(tmp_path: Path) -> None:
    box = ToolBox(tmp_path, read_only=True, submission=Submission(plan.SUBMIT_PLAN, lambda a: None))
    the_turn = turn(tmp_path, box)
    the_turn.read_only = True
    args, _ = codex.command_line("codex", the_turn, tmp_path / "s.json")
    assert args[args.index("--sandbox") + 1] == "read-only" and "--output-schema" in args
    schema = codex.strict(plan.SUBMIT_PLAN["parameters"])
    item = schema["properties"]["items"]["items"]
    assert set(item["required"]) == set(item["properties"]) and item["additionalProperties"] is False


def test_only_a_provider_with_the_tool_is_told_about_it() -> None:
    from thor.agent import providers
    from thor.settings import AgentSettings

    agent = AgentSettings()
    for provider in ("codex", "command:mine"):
        assert "submit_replies" not in providers.how_to_compile(provider, agent, True, "submit_replies")
    assert "submit_replies" in providers.how_to_compile("anthropic_api", agent, True, "submit_replies")
    assert "submit_replies" not in reply.INSTRUCTIONS and "submit_plan" not in plan.INSTRUCTIONS


def open_paper(client: TestClient, tmp_path: Path) -> tuple[str, Path]:
    root = paper_at(tmp_path)
    opened = client.post("/api/projects/open", json={"path": str(root)}).json()
    ident = opened["id"]
    settings = {**opened["config"], "latex_dir": "tex", "main": "main.tex", "revisions_dir": ".", "results_dir": "results",
                "scripts_dir": "scripts"}
    saved = client.put(f"/api/projects/{ident}/config", json=settings)
    assert saved.status_code == 200, saved.text
    return ident, root


def use_command(client: TestClient, script: Path) -> None:
    got = client.get("/api/settings").json()
    agent = {**got["agent"], "provider": "command:m", "commands": [{"id": "m", "name": "Mine", "command": str(script),
                                                                    "edits_files": False, "sandboxed": False}]}
    assert client.put("/api/settings", json={"agent": agent}).status_code == 200


def wait_chat(client: TestClient, ident: str, chat: str) -> list[dict]:
    for _ in range(200):
        got = client.get(f"/api/projects/{ident}/agent/chats/{chat}").json()
        if not got["running"] and any(e["type"] == "done" for e in got["events"]):
            return got["events"]
        time.sleep(0.05)
    raise AssertionError("The turn did not finish.")


def test_the_plan_from_draft_to_the_first_edit(client: TestClient, tmp_path: Path, bin_dir: Path) -> None:
    ident, root = open_paper(client, tmp_path)
    assert client.post(f"/api/projects/{ident}/plan/draft", json={"brief": "x"}).status_code == 409
    assert client.post(f"/api/projects/{ident}/revision/start").status_code == 200
    answer = json.dumps(submitted([], proposal="Say surface warming."))
    log = tmp_path / "prompt.txt"
    script = write_executable(bin_dir / "model", f"""#!/bin/sh
cat > {log}
case "$(cat {log})" in
  *"approved this plan"*) printf '<<<<<<< SEARCH main.tex\\nWarming of\\n=======\\nSurface warming of\\n>>>>>>> REPLACE\\n' ;;
  *) printf '```json\\n%s\\n```\\n' '{answer}' ;;
esac
""")
    (root / "tex" / "main.tex").write_text(MAIN.replace("Warming of 3.14", "Warming of\n3.14"))
    use_command(client, script)
    before = (root / "tex" / "main.tex").read_text()
    chat = client.post(f"/api/projects/{ident}/plan/draft", json={"brief": "Be clearer."}).json()["chat"]
    events = wait_chat(client, ident, chat)
    assert any(e["type"] == "notice" and "Plan version 1" in e["text"] for e in events)
    got = client.get(f"/api/projects/{ident}/plan").json()
    assert got["plan"]["items"][0]["id"] == "D1" and got["blocked"] == "Decide D1 first."
    assert (root / "tex" / "main.tex").read_text() == before
    assert client.get(f"/api/projects/{ident}/agent/chats/{chat}/changes").json()["files"] == []
    assert client.post(f"/api/projects/{ident}/plan/approve", json={}).status_code == 409
    client.post(f"/api/projects/{ident}/plan/decide", json={"id": "D1", "decision": "approve"})
    assert client.post(f"/api/projects/{ident}/plan/approve", json={}).json()["edits"] is True
    wait_chat(client, ident, chat)
    changes = client.get(f"/api/projects/{ident}/agent/chats/{chat}/changes").json()["files"]
    assert changes and (root / "tex" / "main.tex").read_text() == before


def test_a_model_that_hands_in_nothing_is_told_so(client: TestClient, tmp_path: Path, bin_dir: Path) -> None:
    ident, _ = open_paper(client, tmp_path)
    client.post(f"/api/projects/{ident}/revision/start")
    use_command(client, write_executable(bin_dir / "model", "#!/bin/sh\ncat > /dev/null\necho I would rather chat.\n"))
    chat = client.post(f"/api/projects/{ident}/plan/draft", json={"brief": "x"}).json()["chat"]
    events = wait_chat(client, ident, chat)
    assert any(e["type"] == "error" and "without handing in a plan" in e["text"] for e in events)
    assert client.get(f"/api/projects/{ident}/plan").json()["plan"] is None


REPLIES = {"opening_formal": "Dear editor,", "opening_plain": "Hi all,", "closing_formal": "Sincerely,", "closing_plain": "Thanks.",
           "replies": [{"comments": ["alice:1", "alice:2"], "formal": "We have clarified this.", "plain": "Done."}]}


def test_the_reply_quotes_comments_itself_and_exports(tmp_path: Path) -> None:
    folder = folder_with_comments(tmp_path)
    data = reply.save_draft(folder, REPLIES, "c", "m")
    assert data["entries"][0]["quotes"][0]["words"] == "Which warming?"
    assert reply.unanswered(folder, data) == []
    reply.update(folder, "plain", "Hello,", "Bye.", {"R1": "Fixed it."})
    text = reply.text_of(reply.load(folder), "plain", "Reply")
    assert "Hello," in text and "Fixed it." in text and "> Which warming?" in text and "We have clarified" not in text
    made = reply.export(folder, "formal", "docx", "Reply")
    with zipfile.ZipFile(made) as z:
        assert "We have clarified this." in z.read("word/document.xml").decode()
        assert "[Content_Types].xml" in z.namelist()
    assert "Sincerely," in reply.export(folder, "formal", "md", "Reply").read_text()


def test_a_reply_leaving_out_a_comment_goes_back_once(tmp_path: Path) -> None:
    check = reply.checker(folder_with_comments(tmp_path))
    partial = {**REPLIES, "replies": [{"comments": ["alice:1"], "formal": "a", "plain": "b"}]}
    with pytest.raises(ToolError, match="alice:2"):
        check(partial)
    check(partial)


def test_latex_special_characters_are_escaped() -> None:
    latex = documents.to_latex([("text", r"50% of $x_1 & #2 {a} \b ~^")])
    assert r"50\% of \$x\_1 \& \#2 \{a\} \textbackslash{}b \textasciitilde{}\textasciicircum{}" in latex


def test_word_refuses_control_characters_so_they_are_removed(tmp_path: Path) -> None:
    target = tmp_path / "a.docx"
    documents.to_docx([("text", "bad\x0bchar & <tag>")], target)
    with zipfile.ZipFile(target) as z:
        body = z.read("word/document.xml").decode()
    assert "badchar &amp; &lt;tag&gt;" in body


def test_numbers_are_found_in_results_by_rounding(tmp_path: Path) -> None:
    root = paper_at(tmp_path)
    cfg = projects.ProjectConfig(latex_dir="tex", main="main.tex", results_dir="results")
    got = numbers.report(root, cfg)
    by_value = {n["digits"]: n for n in got["numbers"]}
    assert by_value["3.14"]["sources"][0] == {"file": "results/summary.csv", "line": 2, "text": "a,3.1416,0.123"}
    assert by_value["12.3"]["sources"], "12.3% is the share 0.123"
    assert by_value["41.7"]["sources"] == []
    assert not {"2015", "2", "3", "0.5", "1994"} & set(by_value)


def test_what_is_not_a_result_is_not_counted() -> None:
    text = r"""\documentclass{article}\setlength{\parindent}{12.5pt}
\begin{document}
% a comment says 99.9 K
\cite{Smith2019.5} \ref{fig:12.3} \vspace{3.5cm} Table 4.5 and km$^{-2}$ \includegraphics[width=0.75\textwidth]{a1.5}
Between 1990 and 2015. The value 7.25 stays.
\end{document}
"""
    assert [n.digits for n in numbers.in_text(text, "m.tex")] == ["7.25"]


def test_results_too_large_or_not_text_are_named(tmp_path: Path) -> None:
    root = paper_at(tmp_path)
    (root / "results" / "model.nc").write_bytes(b"\0\1")
    got = numbers.report(root, projects.ProjectConfig(latex_dir="tex", main="main.tex", results_dir="results"))
    assert got["skipped"] == ["model.nc (not a text file)"]


def test_the_agent_searches_results_by_number_and_by_pattern(tmp_path: Path) -> None:
    root = paper_at(tmp_path)
    cfg = projects.ProjectConfig(latex_dir="tex", main="main.tex", results_dir="results")
    assert "summary.csv:2" in numbers.search_results(root, cfg, "3.14")
    assert "summary.csv:1" in numbers.search_results(root, cfg, "warm.ng")
    assert "Not found" in numbers.search_results(root, cfg, "777.7")
    with pytest.raises(ToolError):
        numbers.search_results(root, projects.ProjectConfig(latex_dir="tex"), "1")


def test_changed_numbers_show_against_the_baseline() -> None:
    old = "\\begin{document}Value 3.14 K and 7.25 m.\\end{document}"
    new = "\\begin{document}Value 3.15 K and 7.25 m.\\end{document}"
    assert numbers.changed(old, new) == [{"value": "3.14", "before": 1, "after": 0}, {"value": "3.15", "before": 0, "after": 1}]


def test_scripts_are_matched_to_the_figures_they_make(tmp_path: Path) -> None:
    root = paper_at(tmp_path)
    cfg = projects.ProjectConfig(latex_dir="tex", main="main.tex", scripts_dir="scripts")
    got = scripts.scan(root, cfg)
    assert got["figures"][0]["name"] == "figs/fig1"
    assert got["figures"][0]["scripts"] == ["scripts/plot_fig1.py"]
    assert got["figures"][0]["file"] == "tex/figs/fig1.png"
    by_name = {s["name"]: s for s in got["scripts"]}
    assert by_name["plot_fig1.py"]["makes"] == ["figs/fig1"] and by_name["other.py"]["makes"] == []
    assert by_name["other.py"]["outputs"] == ["elsewhere.pdf"]


def test_a_project_can_say_how_to_run_a_kind_of_script(tmp_path: Path) -> None:
    script = tmp_path / "a.py"
    cfg = projects.ProjectConfig(script_commands=[".py=/opt/py/bin/python -u"])
    assert scripts.command_for(script, cfg) == ["/opt/py/bin/python", "-u", "a.py"]
    assert scripts.command_for(tmp_path / "a.m", projects.ProjectConfig(script_commands=[".m=matlab -batch \"run('{script}')\""])) == [
        "matlab", "-batch", "run('a.m')"]


def test_running_a_script_reports_what_it_changed(tmp_path: Path, bin_dir: Path) -> None:
    root = paper_at(tmp_path)
    write_executable(bin_dir / "python3", "#!/bin/sh\necho made\nprintf new > ../tex/figs/fig1.png\n")
    cfg = projects.ProjectConfig(latex_dir="tex", main="main.tex", scripts_dir="scripts")
    runner = scripts.ScriptRunner()
    runner.start(root, cfg, "p", "scripts/plot_fig1.py")
    for _ in range(100):
        job = runner.status("p")
        if job["state"] != "running":
            break
        time.sleep(0.05)
    assert job["state"] == "done" and job["log"] == "made" and job["made"] == ["tex/figs/fig1.png"]


@pytest.mark.parametrize("path", ["../outside.py", "tex/main.tex"])
def test_only_scripts_in_the_scripts_folder_can_run(tmp_path: Path, path: str) -> None:
    root = paper_at(tmp_path)
    cfg = projects.ProjectConfig(latex_dir="tex", main="main.tex", scripts_dir="scripts")
    with pytest.raises((scripts.ScriptError, projects.ProjectError)):
        scripts.ScriptRunner().start(root, cfg, "p", path)


def test_the_agents_script_runs_on_its_copy_and_cannot_touch_the_results(tmp_path: Path, bin_dir: Path) -> None:
    from thor.agent.staging import Staging

    root = paper_at(tmp_path)
    (root / "scripts" / "plot_fig1.py").write_text("x")
    write_executable(bin_dir / "python3", """#!/bin/sh
printf new > ../tex/figs/fig1.png
printf hacked > ../results/summary.csv 2>/dev/null && echo WROTE-RESULTS
printf hacked > %s/escaped.txt 2>/dev/null && echo WROTE-OUTSIDE
exit 0
""" % tmp_path)
    cfg = projects.ProjectConfig(latex_dir="tex", main="main.tex", scripts_dir="scripts", results_dir="results")
    staging = Staging(tmp_path / "chat", root, cfg)
    staging.create()
    script = scripts.in_copy(staging, root, cfg, "plot_fig1.py")
    said = scripts.run_in_copy(staging, root, cfg, script, __import__("threading").Event())
    assert "tex/figs/fig1.png" in said and "WROTE" not in said
    assert (root / "tex" / "figs" / "fig1.png").read_bytes() != b"new"
    assert (root / "results" / "summary.csv").read_text().startswith("run")
    assert not (tmp_path / "escaped.txt").exists()
    with pytest.raises(ToolError):
        scripts.in_copy(staging, root, cfg, "../../tex/main.tex")


def crossref(records: dict[str, dict], searches: list[dict] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/works":
            return httpx.Response(200, json={"message": {"items": searches or []}})
        record = records.get(path.removeprefix("/works/"))
        return httpx.Response(200, json={"message": record}) if record else httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def bib_paper(tmp_path: Path, bib: str) -> tuple[Path, projects.ProjectConfig]:
    root = tmp_path / "bibpaper"
    root.mkdir()
    (root / "main.tex").write_text("\\begin{document}\\cite{a,b,c,missing}\\end{document}\n")
    (root / "refs.bib").write_text(bib)
    return root, projects.ProjectConfig(main="main.tex")


def test_references_are_confirmed_or_questioned(tmp_path: Path) -> None:
    root, cfg = bib_paper(tmp_path, """
@article{a, title={Global lightning}, author={Price, Colin}, year={1994}, doi={10.1/a}}
@article{b, title={Wrong title entirely}, author={Price, Colin}, year={1994}, doi={10.1/b}}
@article{c, title={Nothing like it}, author={Nobody}, year={2000}}
@article{unused, title={Not cited}}
""")
    http = crossref({
        "10.1/a": {"DOI": "10.1/a", "title": ["Global Lightning"], "author": [{"family": "Price"}], "issued": {"date-parts": [[1994]]}},
        "10.1/b": {"DOI": "10.1/b", "title": ["A different paper"], "author": [{"family": "Stone"}], "issued": {"date-parts": [[2004]]}},
    })
    got = references.check(root, cfg, "p", http)
    by_key = {r["key"]: r for r in got["rows"]}
    assert by_key["a"]["status"] == "confirmed"
    assert by_key["b"]["status"] == "mismatch" and "2004" in by_key["b"]["detail"] and "Stone" in by_key["b"]["detail"]
    assert by_key["c"]["status"] == "not found"
    assert got["uncited"] == ["unused"] and got["no_entry"] == ["missing"]


def test_an_online_first_year_is_not_a_mismatch(tmp_path: Path) -> None:
    root, cfg = bib_paper(tmp_path, "@article{a, title={T}, author={Lee, A}, year={2020}, doi={10.1/a}}")
    http = crossref({"10.1/a": {"DOI": "10.1/a", "title": ["T"], "author": [{"family": "Lee"}], "issued": {"date-parts": [[2019]]}}})
    assert references.check(root, cfg, "p", http)["rows"][0]["status"] == "confirmed"


def test_a_network_failure_says_nothing_about_the_entry_and_is_not_kept(tmp_path: Path) -> None:
    root, cfg = bib_paper(tmp_path, "@article{a, title={T}, author={Lee, A}, year={2020}, doi={10.1/a}}")

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    first = references.check(root, cfg, "p", httpx.Client(transport=httpx.MockTransport(down)))
    assert first["rows"][0]["status"] == "not checked"
    http = crossref({"10.1/a": {"DOI": "10.1/a", "title": ["T"], "author": [{"family": "Lee"}], "issued": {"date-parts": [[2020]]}}})
    assert references.check(root, cfg, "p", http)["rows"][0]["status"] == "confirmed"


def test_crossref_turning_us_away_means_wait_and_ask_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, cfg = bib_paper(tmp_path, "@article{a, title={T}, author={Lee, A}, year={2020}, doi={10.1/a}}")
    calls = []
    waits = []
    monkeypatch.setattr("thor.agent.tools.time.sleep", waits.append)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, json={"message": {"DOI": "10.1/a", "title": ["T"], "author": [{"family": "Lee"}], "issued": {"date-parts": [[2020]]}}})

    assert references.check(root, cfg, "p", httpx.Client(transport=httpx.MockTransport(handler)))["rows"][0]["status"] == "confirmed"
    assert waits == [3.0] and len(calls) == 2


def test_open_copies_need_the_users_own_email_and_name_their_source(tmp_path: Path) -> None:
    import pymupdf

    with pytest.raises(ToolError, match="Settings"):
        references.open_text("10.1/a", "")
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "The findings of the paper")
    pdf = doc.tobytes()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "unpaywall" in request.url.host:
            seen["email"] = request.url.params["email"]
            return httpx.Response(200, json={"best_oa_location": {"url_for_pdf": "https://repo.example/a.pdf", "license": "cc-by"}})
        return httpx.Response(200, content=pdf)

    text = references.open_text("10.1/a", "me@example.org", httpx.Client(transport=httpx.MockTransport(handler)))
    assert seen["email"] == "me@example.org"
    assert "Source: https://repo.example/a.pdf" in text and "cc-by" in text and "findings" in text


def zotero_http(items: list[dict], bibtex: str, status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status)
        if request.url.params.get("format") == "bibtex":
            return httpx.Response(200, text=bibtex)
        return httpx.Response(200, json=items)

    return httpx.Client(transport=httpx.MockTransport(handler))


ZOTERO_ITEM = {"key": "ABC123", "data": {"title": "Lightning", "creators": [{"lastName": "Price"}], "date": "March 1994", "DOI": "10.1/z",
                                         "itemType": "journalArticle"}}
ZOTERO_BIB = "@article{price_lightning_1994,\n title = {Lightning},\n doi = {10.1/z}\n}"


def test_zotero_search_and_add_to_a_bib_file(tmp_path: Path) -> None:
    http = zotero_http([ZOTERO_ITEM], ZOTERO_BIB)
    assert zotero.search("price", http)[0] == {"key": "ABC123", "title": "Lightning", "creators": "Price", "year": "1994",
                                               "doi": "10.1/z", "kind": "journalArticle"}
    root, cfg = bib_paper(tmp_path, "@article{a, title={T}}\n")
    (root / "main.tex").write_text("\\bibliography{refs}\n")
    history = History(tmp_path / "history")
    added = zotero.add_to_project(root, cfg, "p", history, "ABC123", http)
    assert added == {"cite_key": "price_lightning_1994", "file": "refs.bib", "added": True}
    assert "price_lightning_1994" in (root / "refs.bib").read_text()
    again = zotero.add_to_project(root, cfg, "p", history, "ABC123", http)
    assert again == {"cite_key": "price_lightning_1994", "file": None, "added": False}


def test_zotero_adds_inside_a_filecontents_block(tmp_path: Path) -> None:
    root = tmp_path / "fc"
    root.mkdir()
    (root / "main.tex").write_text("\\begin{filecontents*}{r.bib}\n@article{a, title={T}}\n\\end{filecontents*}\n\\documentclass{article}\n")
    zotero.add_to_project(root, projects.ProjectConfig(main="main.tex"), "p", History(tmp_path / "h"), "ABC123", zotero_http([], ZOTERO_BIB))
    text = (root / "main.tex").read_text()
    assert text.index("price_lightning_1994") < text.index("\\end{filecontents*}") < text.index("\\documentclass")


def test_zotero_says_what_to_do_when_it_refuses(tmp_path: Path) -> None:
    with pytest.raises(zotero.ZoteroError, match="Allow other"):
        zotero.search("x", zotero_http([], "", 403))


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    root = tmp_path / "gitpaper"
    root.mkdir()
    for args in (["init", "-b", "main"], ["config", "user.name", "The Author"], ["config", "user.email", "author@example.org"]):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    (root / "main.tex").write_text("one\n")
    (root / "notes.txt").write_text("note\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "start"], check=True, capture_output=True)
    return root


def test_a_commit_takes_only_the_chosen_files_and_adds_nothing(repo: Path) -> None:
    (repo / "main.tex").write_text("two\n")
    (repo / "notes.txt").write_text("changed\n")
    state = gitops.status(repo)
    assert {f["path"]: f["state"] for f in state["files"]} == {"main.tex": "changed", "notes.txt": "changed"}
    after = gitops.commit(repo, "Fix the abstract", ["main.tex"])
    assert [f["path"] for f in after["files"]] == ["notes.txt"]
    body = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%an <%ae>%n%B"], capture_output=True, text=True).stdout
    assert body.strip() == "The Author <author@example.org>\nFix the abstract"


def test_a_commit_refuses_without_a_message_or_an_identity(repo: Path) -> None:
    (repo / "main.tex").write_text("two\n")
    with pytest.raises(gitops.GitError, match="message"):
        gitops.commit(repo, "  ", ["main.tex"])
    subprocess.run(["git", "-C", str(repo), "config", "--unset", "user.email"], check=True)
    with pytest.raises(gitops.GitError, match="who you are"):
        gitops.commit(repo, "x", ["main.tex"])


def test_status_outside_a_repository(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    assert gitops.status(tmp_path / "plain")["repo"] is False


def test_pull_and_push_with_a_remote(repo: Path, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(remote)], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-u", "origin", "main"], check=True, capture_output=True)
    (repo / "main.tex").write_text("two\n")
    gitops.commit(repo, "Second", ["main.tex"])
    assert gitops.status(repo)["ahead"] == 1
    assert gitops.push(repo)["ahead"] == 0
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(remote), str(other)], check=True, capture_output=True)
    (other / "new.txt").write_text("x")
    subprocess.run(["git", "-C", str(other), "add", "."], check=True)
    subprocess.run(["git", "-C", str(other), "-c", "user.name=O", "-c", "user.email=o@o", "commit", "-m", "Other"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(other), "push"], check=True, capture_output=True)
    assert gitops.pull(repo)["last_commit"].endswith("Other")
    (repo / "main.tex").write_text("three\n")
    gitops.commit(repo, "Mine", ["main.tex"])
    subprocess.run(["git", "-C", str(other), "pull"], check=True, capture_output=True)
    (other / "new.txt").write_text("y")
    subprocess.run(["git", "-C", str(other), "-c", "user.name=O", "-c", "user.email=o@o", "commit", "-am", "Other 2"], check=True, capture_output=True)


def test_the_git_routes_and_every_new_route_need_the_token(client: TestClient, tmp_path: Path) -> None:
    ident, _ = open_paper(client, tmp_path)
    assert client.get(f"/api/projects/{ident}/git").json()["repo"] is False
    bare = TestClient(client.app, base_url=BASE)
    for method, path in [("get", "plan"), ("post", "plan/draft"), ("post", "plan/decide"), ("post", "plan/approve"), ("post", "plan/reopen"),
                         ("get", "plan/pdf"), ("get", "reply"), ("post", "reply/draft"), ("put", "reply"), ("post", "reply/export"),
                         ("post", "reveal"), ("get", "verify"), ("get", "numbers"), ("post", "references/check"), ("get", "scripts"),
                         ("post", "scripts/run"), ("get", "scripts/job"), ("post", "scripts/stop"), ("get", "figure-preview?name=x"),
                         ("post", "submission"), ("get", "git"), ("post", "git/commit"), ("post", "git/pull"), ("post", "git/push"),
                         ("get", "zotero/search?q=x"), ("post", "zotero/add")]:
        assert getattr(bare, method)(f"/api/projects/{ident}/{path}").status_code == 401, path


def test_reveal_stays_inside_the_project(client: TestClient, tmp_path: Path) -> None:
    ident, _ = open_paper(client, tmp_path)
    assert client.post(f"/api/projects/{ident}/reveal", json={"path": "../../etc/passwd"}).status_code == 422


def test_figure_preview_draws_the_figure(client: TestClient, tmp_path: Path) -> None:
    import pymupdf

    ident, root = open_paper(client, tmp_path)
    doc = pymupdf.open()
    doc.new_page(width=200, height=100)
    doc.save(root / "tex" / "figs" / "plot.pdf")
    got = client.get(f"/api/projects/{ident}/figure-preview", params={"name": "figs/plot"})
    assert got.status_code == 200 and got.content[:4] == b"\x89PNG"
    assert client.get(f"/api/projects/{ident}/figure-preview", params={"name": "nothing"}).status_code == 404


def test_metrics_compare_with_the_baseline_and_the_journals_limits(tmp_path: Path) -> None:
    root = paper_at(tmp_path)
    (root / "tex" / "main.tex").write_text(MAIN + "")
    cfg = projects.ProjectConfig(latex_dir="tex", main="main.tex", max_figures=1, max_tables=0, max_abstract_words=3)
    folder = tmp_path / "paper" / "revision2"
    old = folder / "supporting_files" / "tracked_build" / "old"
    old.mkdir(parents=True)
    (old / "main.tex").write_text("\\begin{document}\\begin{abstract}Only three words\\end{abstract}\\end{document}\n")
    rows = {r["name"]: r for r in verify.metrics(root, cfg, "p", folder, None)}
    assert rows["Figures"]["after"] == 0 and rows["Figures"]["before"] == 0
    assert rows["Abstract words"] == {"name": "Abstract words", "before": 3, "after": 7, "limit": 3, "over": True}
    assert rows["References"]["after"] == 1 and rows["Tables"]["limit"] is None


def test_verification_says_what_it_could_not_check(client: TestClient, tmp_path: Path) -> None:
    ident, _ = open_paper(client, tmp_path)
    got = client.get(f"/api/projects/{ident}/verify").json()
    by_id = {c["id"]: c for c in got["checks"]}
    assert by_id["comments"]["status"] == "info"
    assert by_id["build"]["status"] == "warn" and "Compile first" in by_id["build"]["summary"]
    assert any("41.7" in d["text"] for d in by_id["numbers"]["details"])
    assert "Finding a value does not prove" in by_id["numbers"]["summary"]


def test_a_revision_does_not_inherit_the_last_ones_plan(tmp_path: Path) -> None:
    from thor.revisions import copy_source

    source = tmp_path / "sf"
    for name in ("plan", "reply", "submission"):
        (source / name).mkdir(parents=True)
        (source / name / "x.txt").write_text("x")
    (source / "main.tex").write_text("x")
    target = tmp_path / "old"
    copy_source(source, target, skip=("plan", "reply", "submission"))
    assert (target / "main.tex").is_file() and not (target / "plan").exists()

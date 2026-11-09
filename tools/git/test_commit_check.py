"""Test for the commit message check: what counts as a project, what passes, and what the hook and CI say.

    python tools/git/test_commit_check.py

Needs only git. Exit code 1 on any failure.
"""
import contextlib
import io
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import commit_check as cc  # noqa: E402

FILES = ["README.md", ".github/workflows/x.yml", "aero_modeling/sim_config.json", "aero_modeling/NIMITZ/Nimitz.ork",
         "aero_modeling/AIRMAIL/OpenRocket/AIRMAIL.ork", "tools/openrocket/or_ci.py", "postflight_analysis/a.py"]
PROJECTS = ["AIRMAIL", "NIMITZ", "postflight_analysis", "tools"]


def test_projects():
    assert cc.projects_in(FILES) == PROJECTS, cc.projects_in(FILES)
    assert cc.project_of("aero_modeling/sim_config.json") is None, "a file at the top of aero_modeling is no vehicle"
    assert cc.project_of(".github/workflows/x.yml") is None and cc.project_of("README.md") is None


def test_subjects():
    ok = ["NIMITZ: moved the fins", "nimitz: lower case is fine", "NIMITZ, tools: both", "AIRMAIL/NIMITZ: both",
          "tools:no space", "Merge branch 'main' of https://github.com/tamusrt/dynamics", 'Revert "NIMITZ: x"',
          "fixup! NIMITZ: x"]
    for line in ok:
        assert cc.problem(line, PROJECTS) is None, line
    bad = {"valentina change 1": "does not start", "Fix issues in RAS and OR given from Structures": "does not start",
           "SOL: no such folder": "'SOL' is not a project", "NIMITZ, design: one unknown": "'design' is not a project",
           "NIMITZ:": "nothing after", "": "empty", "aero_modeling: not a vehicle": "is not a project"}
    for line, why in bad.items():
        got = cc.problem(line, PROJECTS)
        assert got and why in got, (line, got)
    assert cc.subject("# comment\n\n  NIMITZ: x  \nbody\n") == "NIMITZ: x"


def test_suggestion():
    changed = ["aero_modeling/NIMITZ/a.ork", "aero_modeling/NIMITZ/b.eng", "tools/x.py", "README.md"]
    assert cc.suggestion(changed, PROJECTS) == "NIMITZ, tools", "most-touched first"
    assert cc.suggestion(["README.md"], PROJECTS) is None
    text = cc.explain("fin test", "it does not start with a project and a colon", PROJECTS, changed)
    assert "Projects: AIRMAIL, NIMITZ, postflight_analysis, tools" in text and 'Try "NIMITZ, tools: fin test"' in text, text
    assert "\n" not in text, "VS Code shows only the first line of a hook's output"
    assert 'Try "NIMITZ: moved fins"' in cc.explain("SOL: moved fins", "x", PROJECTS, changed[:1]), "keeps their text"
    assert f'Try "{cc.EXAMPLE}"' in cc.explain("x", "y", PROJECTS, ["README.md"]), "no project touched: the example"
    assert all(ord(ch) < 128 for ch in text), "a Windows console must be able to print it"


def run(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def test_hook_and_ci_on_a_real_repo():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        run(repo, "init", "-q", "-b", "main")
        run(repo, "config", "user.email", "t@example.com")
        run(repo, "config", "user.name", "t")
        run(repo, "config", "core.hooksPath", "no-hooks")   # these test commits are made without the hook

        def commit(path, message):
            (repo / path).parent.mkdir(parents=True, exist_ok=True)
            (repo / path).write_text(message, encoding="utf-8")
            run(repo, "add", "-A")
            run(repo, "commit", "-q", "-m", message)
            return run(repo, "rev-parse", "HEAD")

        first = commit("aero_modeling/AIRMAIL/a.ork", "AIRMAIL: first")
        # a new vehicle folder is a project in the commit that adds it
        (repo / "aero_modeling/NEWROCKET").mkdir(parents=True)
        (repo / "aero_modeling/NEWROCKET/n.ork").write_text("x", encoding="utf-8")
        run(repo, "add", "-A")
        msg = repo / "msg.txt"
        for text, want in (("NEWROCKET: added", 0), ("added the new rocket", 1), ("# only a comment\n", 1)):
            msg.write_text(text, encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                assert cc.cmd_hook(msg, repo) == want, (text, err.getvalue())
            if want:
                first = err.getvalue().splitlines()[0]
                assert "Projects: AIRMAIL, NEWROCKET" in first and 'Try "NEWROCKET: ' in first, err.getvalue()
        run(repo, "commit", "-q", "-m", "NEWROCKET: added")
        good = run(repo, "rev-parse", "HEAD")
        bad1 = commit("tools/x.py", "fixed stuff")
        bad2 = commit("aero_modeling/AIRMAIL/b.ork", "AIRMAIL: ok")

        def ci(before, after):
            summary = repo / "summary.md"
            summary.unlink(missing_ok=True)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cc.cmd_ci(before, after, summary, repo)
            return code, out.getvalue(), summary.read_text(encoding="utf-8")

        code, out, md = ci(good, bad2)
        assert code == 1 and out.count("::error") == 1 and bad1[:7] in out and "fixed stuff" in md, (out, md)
        assert "`tools: ...`" in md and "1 of 2 commit message(s)" in out, (out, md)
        code, out, md = ci(first, good)
        assert code == 0 and "All 1 commit message(s)" in md, (out, md)
        code, out, _ = ci(cc.ZERO, bad2)
        assert code == 0 and "1 of 1" in out, "a new branch: only its tip"
        code, out, _ = ci("f" * 40, bad1)
        assert code == 1 and "0 of 1" in out, "a force push whose old tip is gone: only the new tip"
        assert ci(good, cc.ZERO)[0] == 0, "a deleted branch has nothing to check"


def main() -> int:
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("commit messages: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())

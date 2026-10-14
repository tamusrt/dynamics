"""Regression test of or_ci.git_file_history, the walk that lists every committed version of a design.

    python tools/openrocket/tests/test_history.py

No OpenRocket needed: it builds small git repositories and checks which versions the walk finds.
"""

import random
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import or_ci  # noqa: E402


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def commit(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "--short", "HEAD")


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def new_repo(tmp: str) -> Path:
    root = Path(tmp)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "tester")
    git(root, "config", "commit.gpgsign", "false")
    return root


def design(n: int) -> str:
    """A version unlike every other, as two saves of a zipped .ork are: git cannot match them by content."""
    rng = random.Random(n)
    return "".join(f"{rng.random()}\n" for _ in range(40))


def test_a_pull_onto_an_old_copy_keeps_the_history_before_the_rename():
    """Someone edits the design on an old copy of main while main renames it; they pull, and git makes a merge
    whose FIRST parent is their old side. Every version from before the merge must still be found."""
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp)
        write(root, "OLD/OR/rocket.ork", design(1))
        v1 = commit(root, "first")
        write(root, "OLD/OR/rocket.ork", design(2))
        v2 = commit(root, "second")
        git(root, "branch", "teammate")  # their copy of main, taken here
        (root / "NEW/OpenRocket").mkdir(parents=True)
        git(root, "mv", "OLD/OR/rocket.ork", "NEW/OpenRocket/ROCKET.ork")
        v3 = commit(root, "rename")
        write(root, "NEW/OpenRocket/ROCKET.ork", design(4))
        v4 = commit(root, "motor swap")
        git(root, "checkout", "-q", "teammate")
        write(root, "OLD/OR/rocket.ork", design(5))
        commit(root, "their edit on the old name")
        # they pull: a merge with THEIR side first, which conflicts (renamed and edited on main, edited on theirs);
        # they settle it by keeping main's layout and file, as happened with AIRMAIL.ork
        git(root, "merge", "-q", "--no-commit", "-s", "ours", "main")
        git(root, "rm", "-q", "OLD/OR/rocket.ork")
        git(root, "checkout", "main", "--", "NEW")
        merge = commit(root, "merge main")
        write(root, "NEW/OpenRocket/ROCKET.ork", design(6))
        v6 = commit(root, "after the merge")

        first_parent_only = or_ci._first_parent_versions(root, "HEAD", "NEW/OpenRocket/ROCKET.ork", 100)
        assert len(first_parent_only) == 2, "the fixture must reproduce the problem: git alone finds only two versions"

        versions = or_ci.git_file_history(root, "NEW/OpenRocket/ROCKET.ork", 100)
        assert [v["short"] for v in versions] == [v1, v2, v3, v4, merge, v6], [(v["short"], v["message"]) for v in versions]
        assert versions[0]["path"] == "OLD/OR/rocket.ork" and versions[-1]["path"] == "NEW/OpenRocket/ROCKET.ork"
        assert all("parents" not in v for v in versions)
        assert all(or_ci._blob(root, v["sha"], v["path"]) for v in versions), "each version's file can be read at its commit"
    print("history through a merge onto an old copy: ok")


def test_a_plain_history_and_the_limit_are_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        root = new_repo(tmp)
        shorts = []
        for n in range(5):
            write(root, "R/r.ork", design(n))
            shorts.append(commit(root, f"v{n}"))
        assert [v["short"] for v in or_ci.git_file_history(root, "R/r.ork", 100)] == shorts
        assert [v["short"] for v in or_ci.git_file_history(root, "R/r.ork", 3)] == shorts[-3:], "the newest N"
    print("plain history and the limit: ok")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()

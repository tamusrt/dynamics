"""Commit messages start with the project they change: "NIMITZ: moved the fins 0.125 in aft", "tools: fix the site".

    python tools/git/commit_check.py hook <message file>              (.githooks/commit-msg runs this)

The projects are the folders git tracks inside aero_modeling/ and at the top of the repository (not aero_modeling
itself, and not hidden ones such as .github), read from the commit being checked, so a new vehicle folder is a project
as soon as it is committed. A commit that changes several names them all: "NIMITZ, tools: ...". Case does not matter.
Merge, revert and fixup commits are exempt.

The hook rejects the commit and lists the projects. Only the hook checks: nothing on GitHub does, so a commit made on
github.com or with --no-verify is not checked.
"""
import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

VEHICLES = "aero_modeling"
EXEMPT = re.compile(r'^(Merge |Revert "|fixup! |squash! |amend! )')
EXAMPLE = "NIMITZ: moved the fins 0.125 in aft"


def project_of(path: str):
    """The project a tracked file belongs to, or None for a file at the top (README.md) or in a hidden folder."""
    parts = path.split("/")
    if len(parts) < 2 or parts[0].startswith("."):
        return None
    if parts[0] == VEHICLES:
        return parts[1] if len(parts) > 2 else None   # aero_modeling/sim_config.json belongs to no vehicle
    return parts[0]


def projects_in(paths) -> list:
    return sorted({p for p in map(project_of, paths) if p}, key=str.lower)


def subject(message: str) -> str:
    """The first line that is not blank or a git comment."""
    for line in message.splitlines():
        if line.strip() and not line.startswith("#"):
            return line.strip()
    return ""


def problem(line: str, projects: list):
    """Why `line` is not a valid subject, or None when it is."""
    if not line:
        return "the message is empty"
    if EXEMPT.match(line):
        return None
    m = re.match(r"^([^:]+):(.*)$", line)
    if not m:
        return "it does not start with a project and a colon"
    names = [n.strip() for n in re.split(r"[,/&+]", m.group(1))]
    known = {p.lower() for p in projects}
    unknown = [n for n in names if n.lower() not in known]
    if unknown:
        return f"{', '.join(repr(n) for n in unknown)} is not a project"
    if not m.group(2).strip():
        return "there is nothing after the colon"
    return None


def suggestion(changed, projects: list):
    """'NIMITZ' or 'NIMITZ, tools' from the files a commit changes, most-touched first; None when they name none."""
    known = set(projects)
    counts = Counter(p for p in map(project_of, changed) if p in known)
    return ", ".join(p for p, _ in counts.most_common()) or None


def explain(line: str, why: str, projects: list, changed) -> str:
    """One line: VS Code's commit error shows only the first line a hook prints."""
    rest = re.sub(r"^[^:]*:\s*", "", line) if ":" in line else line
    tip = suggestion(changed, projects)
    example = f"{tip}: {rest or '<what you changed>'}" if tip else EXAMPLE
    return (f"Commit message needs a project first: {why}. Try \"{example}\". "
            f"Projects: {', '.join(projects)}.")


def git(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True).stdout


def cmd_hook(message_file: Path, repo=".") -> int:
    line = subject(message_file.read_text(encoding="utf-8", errors="replace"))
    projects = projects_in(git(repo, "ls-files").splitlines())   # the index: a folder added in this commit counts
    why = problem(line, projects)
    if why is None:
        return 0
    changed = git(repo, "diff", "--cached", "--name-only").splitlines()
    print(explain(line, why, projects, changed), file=sys.stderr)
    print("  (Your message is kept in .git/COMMIT_EDITMSG.)", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):   # a Windows console cannot print every character a message may hold
        stream.reconfigure(errors="replace")
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hook", help="check the message of the commit being made (commit-msg hook)")
    h.add_argument("message_file", type=Path)
    a = p.parse_args(argv)
    return cmd_hook(a.message_file)


if __name__ == "__main__":
    sys.exit(main())

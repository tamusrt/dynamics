"""Commit messages start with the project they change: "NIMITZ: moved the fins 0.125 in aft", "tools: fix the site".

    python tools/git/commit_check.py hook <message file>              (.githooks/commit-msg runs this)
    python tools/git/commit_check.py ci --before SHA --after SHA [--summary FILE]

The projects are the folders git tracks inside aero_modeling/ and at the top of the repository (not aero_modeling
itself, and not hidden ones such as .github), read from the commit being checked, so a new vehicle folder is a project
as soon as it is committed. A commit that changes several names them all: "NIMITZ, tools: ...". Case does not matter.
Merge, revert and fixup commits are exempt.

The hook rejects the commit and lists the projects. The CI check cannot reject a push, so it fails its own run instead,
which shows a red X on the commit and nothing else.
"""
import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

VEHICLES = "aero_modeling"
EXEMPT = re.compile(r'^(Merge |Revert "|fixup! |squash! |amend! )')
ZERO = "0" * 40
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
    rest = re.sub(r"^[^:]*:\s*", "", line) if ":" in line else line
    tip = suggestion(changed, projects)
    out = [f'Commit message must start with the project it changes, then a colon, like "{EXAMPLE}".',
           f'  "{line}": {why}.',
           f"  Projects: {', '.join(projects)}"]
    if tip:
        out.append(f"  Your changes are in {tip}, so try:  {tip}: {rest or '<what you changed>'}")
    return "\n".join(out)


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


def pushed_commits(repo, before: str, after: str) -> list:
    """The commits a push added, without merges. A new branch or a force push has no usable `before`: only its tip."""
    if not after or after == ZERO:
        return []
    known = before and before != ZERO and subprocess.run(["git", "cat-file", "-e", f"{before}^{{commit}}"],
                                                         cwd=repo, capture_output=True).returncode == 0
    span = [f"{before}..{after}"] if known else ["-1", after]
    return git(repo, "rev-list", "--no-merges", "--max-count=200", *span).split()


def cmd_ci(before: str, after: str, summary=None, repo=".") -> int:
    bad = []
    commits = pushed_commits(repo, before, after)
    projects = []
    for sha in commits:
        line = subject(git(repo, "log", "-1", "--format=%B", sha))
        projects = projects_in(git(repo, "ls-tree", "-r", "--name-only", sha).splitlines())
        why = problem(line, projects)
        if why is None:
            continue
        changed = git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha).splitlines()
        bad.append((sha[:7], line, why, suggestion(changed, projects)))
        print(f"::error title=Commit message needs a project::{sha[:7]} \"{line}\": {why}. "
              f"Projects: {', '.join(projects)}")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            if bad:
                f.write("## Commit messages without a project\n\n"
                        f'Start the message with the project it changes, like `{EXAMPLE}`.\n\n'
                        "| Commit | Message | Problem | Try |\n|---|---|---|---|\n")
                for sha, line, why, tip in bad:
                    cell = line.replace("|", "\\|")
                    f.write(f"| `{sha}` | {cell} | {why} | {f'`{tip}: ...`' if tip else ''} |\n")
                f.write(f"\nProjects: {', '.join(f'`{p}`' for p in projects)}\n\n"
                        "Turn on the hook that checks this before each commit: `git config core.hooksPath .githooks`\n")
            else:
                f.write(f"All {len(commits)} commit message(s) name a project.\n")
    print(f"{len(commits) - len(bad)} of {len(commits)} commit message(s) name a project.")
    return 1 if bad else 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):   # a Windows console cannot print every character a message may hold
        stream.reconfigure(errors="replace")
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hook", help="check the message of the commit being made (commit-msg hook)")
    h.add_argument("message_file", type=Path)
    c = sub.add_parser("ci", help="check every commit in a push")
    c.add_argument("--before", default="")
    c.add_argument("--after", required=True)
    c.add_argument("--summary", default=None, help="markdown file to append the result to ($GITHUB_STEP_SUMMARY)")
    a = p.parse_args(argv)
    if a.cmd == "hook":
        return cmd_hook(a.message_file)
    return cmd_ci(a.before, a.after, a.summary)


if __name__ == "__main__":
    sys.exit(main())

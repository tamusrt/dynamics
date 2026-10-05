"""Move or rename a file or folder of aero_modeling/ without breaking the site.

    python tools/whatif/move_files.py aero_modeling/IREC_2027/OR/old.ork aero_modeling/IREC_2027/OR/new.ork

It does ``git mv`` (so git remembers the move) and then changes every place in whatif_config.json and
sim_config.json that named the old path. Nothing is committed: look at the result with ``git status``, then commit.
Add --dry-run to see what would change.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def rewrite(value: str, old: str, new: str) -> str:
    """``value`` (a path from the config's folder) with the moved file or folder at its start replaced."""
    if value == old:
        return new
    if value.startswith(old.rstrip("/") + "/"):
        return new.rstrip("/") + value[len(old.rstrip("/")):]
    return value


def walk(node, old: str, new: str, changes: list[tuple[str, str]]):
    """The JSON value with every string (and every key) that starts with the old path replaced."""
    if isinstance(node, str):
        fixed = rewrite(node, old, new)
        if fixed != node:
            changes.append((node, fixed))
        return fixed
    if isinstance(node, list):
        return [walk(item, old, new, changes) for item in node]
    if isinstance(node, dict):
        return {walk(k, old, new, changes): walk(v, old, new, changes) for k, v in node.items()}
    return node


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("old", help="the file or folder to move (from the repository folder)")
    parser.add_argument("new", help="where it goes")
    parser.add_argument("--base", default="aero_modeling", help="the folder the configs are in")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    base = Path(args.base).resolve()
    old, new = Path(args.old).resolve(), Path(args.new).resolve()
    if not old.exists():
        print(f"{args.old} does not exist.")
        return 1
    if new.exists():
        print(f"{args.new} already exists; nothing was moved.")
        return 1
    try:
        old_rel, new_rel = old.relative_to(base).as_posix(), new.relative_to(base).as_posix()
    except ValueError:
        print(f"Both paths must be inside {args.base}/ (the configs name files from there).")
        return 1
    plans = []
    for name in ("whatif_config.json", "sim_config.json"):
        path = base / name
        if not path.is_file():
            continue
        changes: list[tuple[str, str]] = []
        data = walk(json.loads(path.read_text(encoding="utf-8")), old_rel, new_rel, changes)
        if changes:
            plans.append((path, data, changes))
    for path, _, changes in plans:
        for before, after in changes:
            print(f"{path.name}: {before} -> {after}")
    if not plans:
        print("No config names this path; only the file is moved.")
    if args.dry_run:
        return 0
    new.parent.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(["git", "mv", str(old), str(new)], cwd=old.parent, capture_output=True, text=True, check=False)
    if done.returncode:
        print("git could not move it:", (done.stderr or done.stdout).strip())
        return 1
    for path, data, _ in plans:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Moved. Nothing is committed yet: check 'git status', then commit and push.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

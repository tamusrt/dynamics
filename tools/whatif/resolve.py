"""Find the model files the site is built from, even when someone moved or renamed them.

build_site.py names its files in aero_modeling/whatif_config.json. When a file there has moved, this finds it
again so the site still builds, and it says what it did so the config can be corrected:

  1. the file is where the config says            -> used as it is
  2. only the capital letters differ              -> the name git stores is used
  3. git recorded a rename or move                -> the new place is used
  4. (design / RASAero file only) there is exactly one such file in the rocket's folders -> that one
  5. otherwise nothing is guessed                 -> the build stops with one clear message (the doctor lists all of them)

Which files may be guessed is deliberate. The aero table (a CSV) is never guessed: a wrong table gives wrong
physics without any sign of it, so it is only ever followed through a recorded rename.

The motor is chosen by ``motor_mode`` ("pinned", the default, or "newest"):
  * pinned: the motor the History tab flies (default_motor in sim_config.json), followed through renames; if
    it cannot be found, the newest motor curve in the folder is used, and a warning says so;
  * newest: the motor curve whose contents changed last (a pure rename or move does not count as a change).
"""

from __future__ import annotations

import fnmatch
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doctor import Repo, git  # noqa: E402  (same folder; only the standard library)

MOTOR_SUFFIXES = (".eng", ".rse")
DEFAULT_IGNORE = ("*OLD*", "*Box_Plot*")  # folders of old or comparison curves are never picked as "the newest motor"
MAX_CANDIDATES = 40


@dataclass
class Resolution:
    """A file the build will use and how it was found."""

    path: Path
    how: str
    auto: bool = False  # True when the build had to find it (the config should be corrected)
    old: str = ""  # the config's value when it was not right


@dataclass
class Notes:
    """Everything the build did on its own or wants said, for the page and the commit comment."""

    notices: list[dict] = field(default_factory=list)

    def add(self, level: str, text: str) -> None:
        if not any(n["text"] == text for n in self.notices):
            self.notices.append({"level": level, "text": text})


def config_value(base: Path, path: Path) -> str:
    """A path as the config writes it: from the config's own folder, with slashes."""
    return Path(os.path.relpath(path, base)).as_posix()


def changed_date(repo: Repo, path: Path) -> str:
    """The day the file's CONTENTS last changed (a pure rename or move is not a change), as YYYY-MM-DD."""
    stamp = content_time(repo, path)
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d") if stamp else ""


def content_time(repo: Repo, path: Path) -> float:
    """When the file's contents last changed: the newest commit that added or edited it, following renames.
    A file that is not committed (or edited since) counts from its file time."""
    try:
        rel = repo.rel(path)
        if repo.tracked is not None and rel in repo.tracked:
            dirty = git(repo.root, "diff", "HEAD", "--ignore-space-at-eol", "--quiet", "--", rel)
            if dirty is not None and dirty.returncode == 0:
                done = git(repo.root, "log", "--follow", "-M", "--name-status", "--format=@%ct", "--", rel)
                stamp = 0.0
                for line in (done.stdout if done is not None and done.returncode == 0 else "").splitlines():
                    if line.startswith("@"):
                        stamp = float(line[1:])
                    elif line and not line.startswith("R100"):  # a pure rename changes nothing in the file
                        return stamp
        return path.stat().st_mtime
    except (OSError, ValueError):
        return 0.0


def ignored(repo: Repo, path: Path, patterns: tuple[str, ...]) -> bool:
    """True when a folder or file name below the repository root matches one of the ignore patterns."""
    parts = repo.rel(path).split("/")
    return any(fnmatch.fnmatch(part, pattern) for part in parts[:-1] for pattern in patterns)


def candidates(repo: Repo, folder: Path, suffixes: tuple[str, ...], patterns: tuple[str, ...] = ()) -> list[Path]:
    """Every committed (or present) file with one of the suffixes below ``folder``, minus ignored folders."""
    found = []
    if not folder.is_dir():
        return found
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.name.lower().endswith(suffixes) and not ignored(repo, path, patterns):
            found.append(path)
    return found


def rocket_folder(base: Path, spec: dict) -> Path:
    """The rocket's own folder: the first folder of its design's path (aero_modeling/IREC_2027 for IREC_2027/OR/x.ork)."""
    first = Path(spec.get("ork", ".")).parts[:1]
    return base / first[0] if first else base


def resolve_file(
    repo: Repo, base: Path, value: str, role: str, search: Path | None = None, suffixes: tuple[str, ...] = ()
) -> Resolution | None:
    """Find the file the config names. ``search`` and ``suffixes`` allow step 4 (the only such file in a folder)."""
    path = base / value
    if path.is_file():
        wrong = repo.case_variant(repo.rel(path)) if repo.tracked is not None else None
        if wrong:
            fixed = repo.root / wrong
            return Resolution(fixed, f"capital letters corrected (the config says '{value}')", True, value)
        return Resolution(path, "set in the config")
    wrong = repo.case_variant(repo.rel(path))
    if wrong and (repo.root / wrong).is_file():
        return Resolution(repo.root / wrong, f"capital letters corrected (the config says '{value}')", True, value)
    now = repo.follow(repo.rel(path))
    if now and (repo.root / now).is_file():
        return Resolution(repo.root / now, f"followed a rename from {Path(value).name}", True, value)
    if search is not None and suffixes:
        only = candidates(repo, search, suffixes)
        if len(only) == 1:
            return Resolution(only[0], f"the only {role} file in {search.name}", True, value)
    return None


def resolve_folder(repo: Repo, base: Path, value: str, hint_file: Path | None = None) -> Resolution | None:
    """Find a folder of the config that no longer exists, from where its files went."""
    path = base / value
    if path.is_dir() and (repo.tracked is None or repo.tracked_under(repo.rel(path))):
        return Resolution(path, "set in the config")
    wrong = repo.case_variant(repo.rel(path))
    if wrong and (repo.root / wrong).is_dir():
        return Resolution(repo.root / wrong, f"capital letters corrected (the config says '{value}')", True, value)
    places = repo.follow_folder(repo.rel(path))
    if not places:
        return None
    pick = places[0][0]
    if hint_file is not None:  # the folder the History tab's motor now lives in is the one meant
        rel = repo.rel(hint_file)
        home = repo.follow(rel) or (rel if repo.is_tracked(rel) else None)
        if home and "/" in home and any(home.rsplit("/", 1)[0] == p for p, _ in places):
            pick = home.rsplit("/", 1)[0]
    return Resolution(repo.root / pick, f"followed its files, which moved to {Path(pick).name}", True, value)


def newest_motor(repo: Repo, folder: Path, patterns: tuple[str, ...] = DEFAULT_IGNORE) -> tuple[Path, str] | None:
    """The motor curve whose contents changed last, anywhere below the folder (old/comparison folders skipped).
    A motor is an .eng and an .rse of the same name: they are judged together and the .eng is used."""
    files = candidates(repo, folder, MOTOR_SUFFIXES, patterns)
    if not files:
        return None
    stems: dict[Path, list[Path]] = {}
    for path in files:
        stems.setdefault(path.with_suffix(""), []).append(path)
    best = max(stems, key=lambda s: (max(content_time(repo, p) for p in stems[s]), s.name))
    chosen = sorted(stems[best], key=lambda p: p.suffix.lower() != ".eng")[0]
    return chosen, f"newest of {len(stems)} in {folder.name}"


def resolve_motor(
    repo: Repo, base: Path, spec: dict, mode: str, history_file: Path | None, notes: Notes, ignore: tuple[str, ...]
) -> tuple[Resolution | None, str]:
    """The motor curve to fly, and a short text for the page's motor note. None when there is none at all."""
    if "motor" in spec:  # one motor named in the config beats everything else
        found = resolve_file(repo, base, spec["motor"], "motor")
        if found:
            return found, "set in the config" if not found.auto else found.how
        notes.add("warn", f"The motor '{spec['motor']}' named in the config is gone and git has no record of it moving.")
        if "motor_dir" not in spec:
            return None, ""
    folder = resolve_folder(repo, base, spec["motor_dir"], history_file) if "motor_dir" in spec else None
    if folder is None:
        return None, ""
    if folder.auto:
        notes.add("warn", f"The motor folder '{spec['motor_dir']}' moved: its files are now in "
                          f"'{config_value(base, folder.path)}'. Change motor_dir in whatif_config.json.")
    if mode != "newest" and history_file is not None:
        wanted = resolve_file(repo, history_file.parent, history_file.name, "motor") if history_file.is_file() else None
        if wanted is None:
            now = repo.follow(repo.rel(history_file))
            wanted = Resolution(repo.root / now, f"followed a rename from {history_file.name}", True, history_file.name) \
                if now and (repo.root / now).is_file() else None
        if wanted is not None and wanted.path.suffix.lower() in MOTOR_SUFFIXES:
            if wanted.auto:
                notes.add("warn", f"The History tab's motor was renamed: sim_config.json still says '{history_file.name}', "
                                  f"the file is now '{wanted.path.name}'. Change default_motor there.")
            return wanted, "same as the History tab" if not wanted.auto else wanted.how
        if history_file is not None:
            notes.add("warn", f"The History tab's motor ({history_file.name}) is gone and git has no record of it "
                              "moving, so the newest motor curve in the folder is used instead. That can change the "
                              "predicted apogee.")
    newest = newest_motor(repo, folder.path, ignore)
    if newest is None:
        return None, ""
    return Resolution(newest[0], newest[1], mode != "newest" or folder.auto, ""), newest[1]

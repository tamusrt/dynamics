"""Check the repository's model files before the site is built, and say what is wrong in plain words.

    python tools/whatif/doctor.py                    # check everything, print what it finds
    python tools/whatif/doctor.py --fix              # also show how to repair paths that moved
    python tools/whatif/doctor.py --fix --write      # make those repairs in the two config files
    python tools/whatif/doctor.py --install-hook     # check on every `git push` (warns, never blocks)

It reads the files only: it does not simulate anything and needs nothing but Python and git, so it takes
seconds. The site build (tools/whatif/build_site.py) stops with one message when a file it needs is missing;
this checks all of them at once and says which setting to change.

What it looks at:
  * the two config files (whatif_config.json, sim_config.json) are valid JSON, and every file they name is there;
  * the file's capital letters are the ones git stores (Windows does not notice a wrong one, GitHub does);
  * the file is committed and not hidden by .gitignore (GitHub only ever sees committed files);
  * the .ork opens, has a nose cone, a body tube and fins, and holds the saved simulation the config asks for;
  * the motor curve that will be flown reads correctly, and its .eng and .rse agree;
  * the History tab and the Predictions page fly the same motor;
  * no file is close to GitHub's size limit, and no file name has a character that breaks links.

A file that moved or was renamed is found again through git's rename history, and ``--fix`` shows the new
setting (``--write`` applies it, and you commit the change yourself). Nothing is ever edited without ``--write``.

Exit status: 1 when something the site build needs is wrong, else 0 (``--warn-only`` always gives 0).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import stat
import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _build_site():
    """build_site.py (imported late: it uses this module too)."""
    import build_site

    return build_site


def _resolve():
    """resolve.py (imported late: it uses this module too)."""
    import resolve

    return resolve


ERROR, WARN = "error", "warn"
MOTOR_SUFFIXES = (".eng", ".rse")
SIZE_WARN_BYTES = 25 * 1024 * 1024  # GitHub warns at 50 MB and refuses at 100 MB
SIZE_ERROR_BYTES = 95 * 1024 * 1024
REPO_WARN_MB = 500
PAIR_TOLERANCE = 0.02  # a motor's .eng and .rse should give the same burn time and total impulse to within this
MASS_TOLERANCE = 0.05  # an .rse's mass column may differ from "propellant leaves in step with the impulse" by this share of the propellant
BAD_NAME = re.compile(r"[#%?&+;'\"]")
HOOK_MARK = "# installed by tools/whatif/doctor.py"


class Finding(dict):
    """One thing that is wrong: level, code, where, message, hint and (when a move was found) fix."""


def finding(level: str, code: str, where: str, message: str, hint: str = "", fix: dict | None = None) -> Finding:
    out = Finding(level=level, code=code, where=where, message=message, hint=hint)
    if fix:
        out["fix"] = fix
    return out


def git(root: Path, *args: str) -> subprocess.CompletedProcess | None:
    """Run git in the repository; None when git is not there."""
    try:
        return subprocess.run(
            ["git", "-c", "core.quotepath=off", *args], cwd=root, capture_output=True, text=True, check=False
        )
    except OSError:
        return None


def tracked_files(root: Path) -> set[str] | None:
    """Every committed file as git stores its name, or None outside a repository."""
    done = git(root, "ls-files", "-z")
    if done is None or done.returncode != 0 or not isinstance(done.stdout, str):
        return None
    return {name for name in done.stdout.split("\0") if name}


class Repo:
    """What the checks need to know about the repository."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.tracked = tracked_files(self.root)
        self.lower = {}
        for name in self.tracked or ():
            self.lower.setdefault(name.lower(), name)
        self._renames: list[tuple[str, str]] | None = None

    def rel(self, path: Path) -> str:
        """The path as git writes it: from the repository root, with slashes."""
        full = Path(os.path.normpath(path)).resolve()
        try:
            return full.relative_to(self.root).as_posix()
        except ValueError:  # outside the repository
            return full.as_posix()

    def is_tracked(self, rel: str) -> bool:
        return self.tracked is None or rel in self.tracked

    def tracked_under(self, rel: str) -> list[str]:
        prefix = rel.rstrip("/") + "/"
        return sorted(n for n in (self.tracked or ()) if n.startswith(prefix))

    def case_variant(self, rel: str) -> str | None:
        """The name git stores when it differs from ``rel`` only in capital letters."""
        found = self.lower.get(rel.lower())
        if found and found != rel:
            return found
        if self.tracked and not found:
            prefix = rel.lower().rstrip("/") + "/"
            for name in self.tracked:  # a folder written with the wrong capitals
                if name.lower().startswith(prefix):
                    stored = name[: len(prefix) - 1]
                    return stored if stored != rel.rstrip("/") else None
        return None

    def ignored_by(self, rel: str) -> str:
        """The .gitignore rule that hides a file, or '' when none does."""
        done = git(self.root, "check-ignore", "-v", "--", rel)
        return done.stdout.strip() if done is not None and done.returncode == 0 else ""

    def renames(self) -> list[tuple[str, str]]:
        """Every rename in the history, oldest first, as (old path, new path)."""
        if self._renames is None:
            self._renames = []
            done = git(self.root, "log", "-M", "--name-status", "--diff-filter=R", "--format=", "--reverse")
            for line in (done.stdout if done is not None and done.returncode == 0 else "").splitlines():
                parts = line.split("\t")
                if len(parts) == 3 and parts[0].startswith("R"):
                    self._renames.append((parts[1], parts[2]))
        return self._renames

    def follow(self, rel: str) -> str | None:
        """Where a file that was renamed or moved is now, or None when git does not know."""
        here = rel
        for old, new in self.renames():
            if old == here:
                here = new
        return here if here != rel and self.is_tracked(here) else None

    def follow_folder(self, rel: str) -> list[tuple[str, int]]:
        """Where the files of a folder that no longer exists went: [(folder, how many files)], most first."""
        prefix = rel.rstrip("/") + "/"
        places: Counter = Counter()
        for old, _ in self.renames():
            if old.startswith(prefix):
                now = self.follow(old)
                if now:
                    places[now.rsplit("/", 1)[0] if "/" in now else ""] += 1
        return places.most_common()


def read_json(path: Path, repo: Repo, label: str, findings: list[Finding]) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        findings.append(finding(ERROR, "config-missing", label, f"{label} cannot be read ({err.strerror or err}).",
                                "It is the file the site build is configured from."))
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        findings.append(finding(
            ERROR, "json-typo", label,
            f"{label} has a typo at line {err.lineno}, column {err.colno}: {err.msg}.",
            "Look at that line and the one above it: a missing or extra comma or quote is the usual cause.",
        ))
        return None


def check_name(rel: str, findings: list[Finding]) -> None:
    """Warn about file names that break links or shell commands."""
    bad = []
    for part in rel.split("/"):
        if part != part.strip():
            bad.append(f"a space at the start or end of '{part}'")
        if BAD_NAME.search(part):
            bad.append(f"a special character in '{part}'")
        if not part.isascii():
            bad.append(f"a letter that is not plain English in '{part}'")
    if bad:
        findings.append(finding(WARN, "odd-name", rel, f"{rel} has " + "; ".join(bad) + ".",
                                "It works today, but such names can break links and commands."))


class Needs:
    """A file the config names: what it is, where, and whether the site build needs it."""

    def __init__(self, role: str, config_file: str, config_dir: Path, value: str, folder: bool = False, needed: bool = True):
        self.role, self.config_file, self.config_dir, self.value = role, config_file, config_dir, value
        self.folder, self.needed = folder, needed


def check_path(repo: Repo, need: Needs, findings: list[Finding], hint_motor: str | None = None) -> Path | None:
    """Check one file or folder named in a config. Returns its path when it is fine."""
    level = ERROR if need.needed else WARN
    path = (need.config_dir / need.value)
    rel = repo.rel(path)
    where = f"{need.config_file} ({need.role})"
    exists = path.is_dir() if need.folder else path.is_file()
    kind = "folder" if need.folder else "file"
    if exists:
        names = repo.tracked_under(rel) if need.folder else ([rel] if rel in (repo.tracked or ()) else [])
        if repo.tracked is not None and not names:
            wrong = repo.case_variant(rel)
            if wrong:
                findings.append(finding(
                    level, "wrong-capitals", where,
                    f"The {need.role} is written '{need.value}', but git stores it as '{wrong}'. Windows treats them as the "
                    "same; GitHub does not, so the site build will not find it.",
                    "Write it exactly as git stores it.",
                    fix={"file": need.config_file, "old": need.value,
                         "new": Path(os.path.relpath(wrong, repo.rel(need.config_dir))).as_posix()},
                ))
                return None
            rule = repo.ignored_by(rel)
            if rule:
                findings.append(finding(
                    level, "git-ignored", where,
                    f"The {need.role} '{need.value}' is on your computer but .gitignore hides it, so it is never pushed "
                    f"and the site build cannot see it ({rule}).",
                    "Rename it to match a rule that lets it through (an aero table must end in _aero.csv), or change .gitignore.",
                ))
            else:
                findings.append(finding(
                    level, "not-committed", where,
                    f"The {need.role} '{need.value}' is on your computer but has not been committed, so the site build "
                    "cannot see it.",
                    "Commit it by name with git add.",
                ))
            return None
        check_name(rel, findings)
        return path
    # not there: a wrong capital, a move, or gone
    wrong = repo.case_variant(rel)
    if wrong:
        findings.append(finding(
            level, "wrong-capitals", where,
            f"The {need.role} is written '{need.value}', but the {kind} is stored as '{wrong}'. The capital letters differ, "
            "which GitHub (Linux) treats as a different name.",
            "Write it exactly as git stores it.",
            fix={"file": need.config_file, "old": need.value,
                 "new": Path(os.path.relpath(wrong, repo.rel(need.config_dir))).as_posix()},
        ))
        return None
    if need.folder:
        places = repo.follow_folder(rel)
        pick = None
        if hint_motor:  # the folder the History tab's motor now lives in is the likely one
            home = repo.follow(hint_motor)
            home = home or (hint_motor if repo.is_tracked(hint_motor) else None)
            pick = home.rsplit("/", 1)[0] if home and "/" in home else None
        if places:
            best = pick if pick and any(pick == p for p, _ in places) else places[0][0]
            where_to = ", ".join(f"{p or '(top)'} ({n} file{'s' if n != 1 else ''})" for p, n in places[:4])
            findings.append(finding(
                level, "moved", where,
                f"The {need.role} '{need.value}' is gone. Its files moved to: {where_to}.",
                f"The likely new setting is '{Path(os.path.relpath(best, repo.rel(need.config_dir))).as_posix()}'. "
                + ("Several folders took files, so check that this is the one you want." if len(places) > 1 else ""),
                fix={"file": need.config_file, "old": need.value,
                     "new": Path(os.path.relpath(best, repo.rel(need.config_dir))).as_posix(), "ambiguous": len(places) > 1},
            ))
            return None
    else:
        now = repo.follow(rel)
        if now:
            findings.append(finding(
                level, "moved", where,
                f"The {need.role} '{need.value}' was moved or renamed in git to '{now}'.",
                "Point the setting at the new name.",
                fix={"file": need.config_file, "old": need.value,
                     "new": Path(os.path.relpath(now, repo.rel(need.config_dir))).as_posix()},
            ))
            return None
    findings.append(finding(
        level, "missing", where,
        f"The {need.role} '{need.value}' does not exist in the repository, and git has no record of it being moved.",
        f"Change the setting in {need.config_file} to where it is now, or put the {kind} back.",
    ))
    return None


# ---------------------------------------------------------------------------------------------- the .ork

def read_ork(path: Path) -> tuple[ElementTree.Element | None, str]:
    """The design's XML root and an error message ('' when it opened)."""
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                name = next((n for n in z.namelist() if n.endswith(".ork") or n.endswith(".xml")), z.namelist()[0])
                data = z.read(name)
        else:
            data = path.read_bytes()
        return ElementTree.fromstring(data), ""
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError, IndexError, KeyError) as err:
        return None, str(err) or type(err).__name__


def check_ork(path: Path, rel: str, sim: str | None, findings: list[Finding]) -> None:
    root, problem = read_ork(path)
    if root is None:
        findings.append(finding(ERROR, "ork-unreadable", rel, f"The OpenRocket file {rel} cannot be opened ({problem}).",
                                "Open it in OpenRocket and save it again; if it is a download, fetch it again."))
        return
    tags = Counter(el.tag for el in root.iter())
    missing = []
    if not tags["nosecone"]:
        missing.append("a nose cone")
    if not tags["bodytube"]:
        missing.append("a body tube")
    if not any(tag.endswith("finset") for tag in tags):
        missing.append("a fin set")
    if missing:
        findings.append(finding(ERROR, "ork-incomplete", rel, f"The design {rel} has no " + ", no ".join(missing) + ".",
                                "The page draws and flies the rocket from these parts; was the wrong file saved over it?"))
    names = [(el.findtext("name") or "").strip() for el in root.iter("simulation")]
    if not names:
        findings.append(finding(ERROR, "ork-no-sims", rel, f"{rel} has no saved simulations.",
                                "Run the simulations in OpenRocket and save the file: the launch conditions come from them."))
    elif sim is not None and sim not in names:
        findings.append(finding(
            ERROR, "ork-sim-missing", rel,
            f"The config asks for the simulation '{sim}', but {rel} holds: " + ", ".join(f"'{n}'" for n in names) + ".",
            "Was the simulation renamed in OpenRocket? Change 'sim' in whatif_config.json to match.",
        ))


# ---------------------------------------------------------------------------------------------- motors

def read_motor(path: Path) -> tuple[list[tuple[float, float]], float | None]:
    """A motor file's (time s, thrust N) points and, for .rse, the total impulse it declares (None if not given)."""
    if path.suffix.lower() == ".rse":
        root = ElementTree.parse(path).getroot()
        points = [(float(el.get("t")), float(el.get("f"))) for el in root.iter("eng-data")]
        declared = next((e.get("Itot") for e in root.iter("engine") if e.get("Itot")), None)
        return points, float(declared) if declared else None
    points, header = [], False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(";"):
            if header:
                break  # the end mark
            continue
        if not header:
            header = True  # name, diameter, length, delays, propellant weight, total weight, maker
            continue
        t, f = line.split()[:2]
        points.append((float(t), float(f)))
    return points, None


def motor_numbers(points: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Burn time (s), total impulse (N s) and peak thrust (N); the thrust is taken to start from zero."""
    if not points:  # a motor file with no thrust points at all: nothing burns
        return 0.0, 0.0, 0.0
    curve = [(0.0, 0.0)] + points if points[0][0] > 0 else points
    impulse = sum((b[0] - a[0]) * (a[1] + b[1]) / 2 for a, b in zip(curve, curve[1:]))
    return curve[-1][0], impulse, max(f for _, f in curve)


def check_motor(path: Path, rel: str, findings: list[Finding], level: str = ERROR) -> tuple[float, float] | None:
    """Check one motor file reads sensibly. Returns (burn time, impulse) when it does."""
    try:
        points, declared = read_motor(path)
    except (OSError, ValueError, TypeError, ElementTree.ParseError) as err:
        findings.append(finding(level, "motor-unreadable", rel, f"The motor file {rel} cannot be read ({err}).",
                                "Make it again with the thrust curve generator."))
        return None
    problems = []
    if len(points) < 3:
        problems.append("it has fewer than 3 thrust points")
    elif any(b[0] <= a[0] for a, b in zip(points, points[1:])):
        problems.append("the times do not always increase")
    if any((not math.isfinite(t)) or (not math.isfinite(f)) or f < 0 for t, f in points):
        problems.append("a thrust is negative or not a number")
    if problems:
        findings.append(finding(level, "motor-bad", rel, f"The motor file {rel} is not a usable thrust curve: "
                                + "; ".join(problems) + ".", "Make it again with the thrust curve generator."))
        return None
    burn, impulse, peak = motor_numbers(points)
    if burn < 0.2 or peak <= 0 or impulse <= 0:
        findings.append(finding(level, "motor-bad", rel, f"The motor file {rel} has no real burn (burn time {burn:.2f} s, "
                                f"total impulse {impulse:.0f} N s).", "Make it again with the thrust curve generator."))
        return None
    if declared and abs(impulse / declared - 1) > 0.05:
        findings.append(finding(WARN, "motor-impulse", rel, f"{rel} says its total impulse is {declared:.0f} N s, but its "
                                f"thrust points add up to {impulse:.0f} N s.", "Was the curve edited by hand?"))
    return burn, impulse


def check_pair(path: Path, rel: str, findings: list[Finding]) -> None:
    """A motor is usually an .eng and an .rse: they should describe the same burn."""
    other = next((path.with_suffix(s) for s in MOTOR_SUFFIXES if s != path.suffix.lower() and path.with_suffix(s).is_file()), None)
    if other is None:
        return
    try:
        a = motor_numbers(read_motor(path)[0])
        b = motor_numbers(read_motor(other)[0])
    except (OSError, ValueError, TypeError, IndexError, ElementTree.ParseError):
        return
    for name, x, y in (("burn time", a[0], b[0]), ("total impulse", a[1], b[1])):
        if y and abs(x / y - 1) > PAIR_TOLERANCE:
            findings.append(finding(
                WARN, "motor-pair", rel,
                f"{path.name} and {other.name} disagree on the {name} ({x:.2f} against {y:.2f}).",
                "The History tab and the Predictions page may read different ones. Make both with the generator in one go.",
            ))
            return


def read_designation(path: Path) -> str:
    """The name a motor file gives itself (what OpenRocket lists it as), or '' when it has none."""
    try:
        if path.suffix.lower() == ".rse":
            root = ElementTree.parse(path).getroot()
            return next((e.get("code") or "" for e in root.iter("engine")), "").strip()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith(";"):
                return line.split()[0]  # the first line that is not a comment names the motor
    except (OSError, ValueError, ElementTree.ParseError):
        pass
    return ""


def check_designations(path: Path, rel: str, findings: list[Finding]) -> None:
    """Two different motors in one folder should not share a name: OpenRocket treats the name as the motor's identity."""
    mine = read_designation(path)
    if not mine or not path.parent.is_dir():
        return
    clash = sorted({other.stem for other in path.parent.iterdir()
                    if other.suffix.lower() in MOTOR_SUFFIXES and other.stem != path.stem
                    and read_designation(other) == mine})
    if clash:
        findings.append(finding(
            WARN, "motor-same-name", rel,
            f"{path.stem} and {', '.join(clash)} are different motor curves but both call themselves '{mine}'. "
            "OpenRocket treats motors with the same name as one motor, so it can load the wrong curve.",
            "Give each curve its own name inside the file (the 'code' line of the .rse, the first line of the .eng).",
        ))


def check_mass_column(path: Path, rel: str, findings: list[Finding]) -> None:
    """An .rse that lists its own mass is flown with it. If that mass does not fall in step with the thrust, the
    result differs from the .eng (and from Jarvis), which burn propellant in proportion to the impulse delivered."""
    if path.suffix.lower() != ".rse":
        return
    try:
        root = ElementTree.parse(path).getroot()
        engine = next(iter(root.iter("engine")), None)
        if engine is None or engine.get("auto-calc-mass", "1") != "0":
            return  # OpenRocket works the mass out itself
        rows = [(float(e.get("t")), float(e.get("f")), float(e.get("m"))) for e in root.iter("eng-data")]
    except (OSError, ValueError, TypeError, ElementTree.ParseError):
        return
    if len(rows) < 3 or any(b[0] <= a[0] for a, b in zip(rows, rows[1:])):
        return
    burned = rows[0][2] - rows[-1][2]
    total = 0.0
    impulse = [0.0]
    for a, b in zip(rows, rows[1:]):
        total += (b[0] - a[0]) * (a[1] + b[1]) / 2
        impulse.append(total)
    if burned <= 0 or total <= 0:
        return
    worst, at = 0.0, 0.0
    for row, done in zip(rows, impulse):
        gap = abs((rows[0][2] - row[2]) / burned - done / total)
        if gap > worst:
            worst, at = gap, row[0]
    if worst > MASS_TOLERANCE:
        findings.append(finding(
            WARN, "motor-mass-column", rel,
            f"{path.name} lists its own mass for every moment, and that mass does not fall in step with the thrust "
            f"(by up to {worst * burned / 1000:.1f} kg at {at:.1f} s). OpenRocket flies the listed mass, so it predicts a "
            f"different apogee than the same curve as an .eng file does, and than Jarvis does.",
            "Ask whoever makes the curve which mass profile is right. The .eng gives the thrust-proportional one, so it matches Jarvis.",
        ))


# ---------------------------------------------------------------------------------------------- the checks

def strings(node: object):
    """Every string in a parsed JSON value."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from strings(value)


def check_sim_config(repo: Repo, sim_path: Path, sim: dict, needed: set[str], findings: list[Finding]) -> None:
    """Every design and motor the History tab's config names must exist (a problem only matters
    for a design the Predictions page uses; for the others it is a warning)."""
    label = repo.rel(sim_path)
    for ork, entry in (sim.get("files") or {}).items():
        important = repo.rel(sim_path.parent / ork) in needed
        check_path(repo, Needs("OpenRocket design", label, sim_path.parent, ork, needed=important), findings)
        for value in strings(entry):
            if value.lower().endswith((*MOTOR_SUFFIXES, ".ork")):
                check_path(repo, Needs("motor file", label, sim_path.parent, value, needed=important), findings)


def flown_motor(repo: Repo, config_dir: Path, spec: dict, defaults: dict | None = None) -> tuple[Path | None, str]:
    """The motor file the Predictions page flies for a rocket and how it is chosen: the very same choice build_site makes
    (resolve.resolve_motor), so the doctor checks the file the build will fly."""
    resolve = _resolve()
    defaults = defaults or {}
    mode = str(spec.get("motor_mode", defaults.get("motor_mode", "pinned"))).lower()
    ignore = tuple(defaults.get("motor_ignore", resolve.DEFAULT_IGNORE))
    history = _build_site().history_motor_file(config_dir, spec)
    found, how = resolve.resolve_motor(repo, config_dir, spec, mode, history, resolve.Notes(), ignore)
    return (found.path, how or "named in the config") if found is not None else (None, "none found")


def check_rocket(repo: Repo, config_path: Path, sim_path: Path, key: str, spec: dict, findings: list[Finding],
                 defaults: dict | None = None) -> set[str]:
    """Check one rocket of whatif_config.json. Returns the repository paths of its design."""
    label = repo.rel(config_path)
    base = config_path.parent
    for setting in ("ork", "aero"):
        if setting not in spec:
            findings.append(finding(ERROR, "setting-missing", f"{label} ({key})", f"Rocket '{key}' has no '{setting}' setting.",
                                    "Add it: the site build needs it."))
    if "motor" not in spec and "motor_dir" not in spec:
        findings.append(finding(ERROR, "setting-missing", f"{label} ({key})", f"Rocket '{key}' has neither 'motor' nor 'motor_dir'.",
                                "Add one: it says which thrust curve to fly."))
    ork_path = None
    for setting, role in (("ork", "OpenRocket design"), ("aero", "RASAero table (CSV)"), ("rasaero", "RASAero file (.CDX1)"),
                          ("rasaero_results", "RASAero results file"), ("motor", "motor file")):
        if setting in spec:
            found = check_path(repo, Needs(f"{role} of {key}", label, base, spec[setting]), findings)
            if setting == "ork":
                ork_path = found
    if "motor_dir" in spec:
        hist = _build_site().history_motor_file(base, spec)
        hint = repo.rel(hist) if hist is not None else None
        check_path(repo, Needs(f"motor folder of {key}", label, base, spec["motor_dir"], folder=True), findings, hint_motor=hint)
    if "edith_site" in spec:
        check_path(repo, Needs(f"EDITH site settings of {key}", label, base, spec["edith_site"]), findings)
    if ork_path is not None:
        check_ork(ork_path, repo.rel(ork_path), spec.get("sim"), findings)
    motor, how = flown_motor(repo, base, spec, defaults)
    if "motor_dir" in spec and "motor" not in spec:
        hist = _build_site().history_motor_file(base, spec)
        if hist is None:
            findings.append(finding(
                WARN, "history-motor-unknown", f"{label} ({key})",
                f"sim_config.json names no default_motor for {spec.get('ork')}, so the Predictions page flies the newest motor "
                f"in the folder ({motor.name if motor else 'none'}) and the History tab may fly another.",
                "Add the design with a default_motor to sim_config.json so both fly the same curve.",
            ))
        elif not hist.is_file() and motor is not None:
            findings.append(finding(
                WARN, "history-motor-missing", f"{label} ({key})",
                f"The History tab's motor ({hist.name}) is gone, so the Predictions page flies the newest in the folder "
                f"instead: {motor.name}. That can change the predicted apogee.",
                "Point default_motor in sim_config.json at the curve you mean (see the 'moved' message above).",
            ))
    if motor is not None and motor.is_file():
        mrel = repo.rel(motor)
        check_name(mrel, findings)
        check_motor(motor, mrel, findings)
        check_pair(motor, mrel, findings)
        check_designations(motor, mrel, findings)
        for same in (motor, *(motor.with_suffix(x) for x in MOTOR_SUFFIXES if x != motor.suffix.lower())):
            if same.is_file():
                check_mass_column(same, repo.rel(same), findings)
    return {repo.rel(base / spec["ork"])} if "ork" in spec else set()


def check_repository(repo: Repo, findings: list[Finding]) -> None:
    """Size and stray-file checks that do not depend on the configs."""
    for name in sorted(repo.tracked or ()):
        try:
            size = (repo.root / name).stat().st_size
        except OSError:
            continue
        if size >= SIZE_ERROR_BYTES:
            findings.append(finding(ERROR, "too-big", name, f"{name} is {size / 2**20:.0f} MB; GitHub refuses files over 100 MB.",
                                    "Keep it out of the repository."))
        elif size >= SIZE_WARN_BYTES and name.startswith("aero_modeling/"):  # the files the team keeps adding to
            findings.append(finding(WARN, "big-file", name, f"{name} is {size / 2**20:.0f} MB; GitHub warns at 50 MB.",
                                    "Each new version of it adds to the repository's size for good."))
    done = git(repo.root, "count-objects", "-v")
    if done is not None and done.returncode == 0:
        kib = sum(int(m.group(1)) for m in re.finditer(r"^size(?:-pack)?: (\d+)", done.stdout, re.M))
        if kib / 1024 > REPO_WARN_MB:
            findings.append(finding(WARN, "repo-big", ".git", f"The repository's history is {kib / 1024:.0f} MB.",
                                    "Large files committed again and again are the usual cause (the aero CSV)."))
    ignored = git(repo.root, "ls-files", "--others", "--ignored", "--exclude-standard", "--", "aero_modeling")
    for name in (ignored.stdout.splitlines() if ignored is not None and ignored.returncode == 0 else []):
        low = name.lower()
        if "aero" in low and low.endswith(".csv") and "/alpha/" not in low:
            findings.append(finding(WARN, "csv-ignored", name,
                                    f"{name} looks like an aero table, but .gitignore hides it: only names ending _aero.csv are kept.",
                                    "Rename it to end in _aero.csv, or it will never be pushed."))
    loose = git(repo.root, "ls-files", "--others", "--exclude-standard", "--", "aero_modeling")
    for name in (loose.stdout.splitlines() if loose is not None and loose.returncode == 0 else []):
        if name.lower().endswith((".ork", ".eng", ".rse", ".cdx1", "_aero.csv")):
            findings.append(finding(WARN, "untracked-model", name, f"{name} is on your computer but has not been committed.",
                                    "The site only sees committed files."))


def run_checks(root: Path, config_path: Path, sim_path: Path) -> tuple[list[Finding], Repo]:
    findings: list[Finding] = []
    repo = Repo(root)
    if repo.tracked is None:
        findings.append(finding(WARN, "no-git", ".", "This folder is not a git repository, so the committed-file checks were skipped."))
    config = read_json(config_path, repo, repo.rel(config_path) if config_path.exists() else str(config_path), findings)
    sim = read_json(sim_path, repo, repo.rel(sim_path) if sim_path.exists() else str(sim_path), findings) if sim_path.exists() else None
    needed: set[str] = set()
    if config is not None:
        rockets = config.get("rockets") or {}
        default = config.get("default")
        if not rockets:
            findings.append(finding(ERROR, "no-rockets", repo.rel(config_path), "whatif_config.json lists no rockets.", ""))
        elif default and default not in rockets:
            findings.append(finding(ERROR, "bad-default", repo.rel(config_path),
                                    f"The default rocket '{default}' is not one of the rockets: " + ", ".join(rockets) + ".",
                                    "Change 'default' to one of them."))
        for key, spec in rockets.items():
            needed |= check_rocket(repo, config_path, sim_path, key, spec, findings, config)
    if sim is not None:
        check_sim_config(repo, sim_path, sim, needed, findings)
    check_repository(repo, findings)
    seen, unique = set(), []
    for f in findings:
        key = (f["code"], f["where"], f["message"])
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique, repo


# ---------------------------------------------------------------------------------------------- output

def sort_key(f: Finding):
    return (f["level"] != ERROR, f["where"], f["code"])


def text_report(findings: list[Finding]) -> str:
    if not findings:
        return "doctor: everything the site build needs is in place."
    lines = []
    for f in sorted(findings, key=sort_key):
        lines.append(f"{'ERROR' if f['level'] == ERROR else 'WARN '}  {f['message']}")
        if f["hint"]:
            lines.append(f"       -> {f['hint']}")
    errors = sum(f["level"] == ERROR for f in findings)
    warns = len(findings) - errors
    lines.append(f"doctor: {errors} error{'s' if errors != 1 else ''}, {warns} warning{'s' if warns != 1 else ''}.")
    return "\n".join(lines)


def comment_text(findings: list[Finding]) -> str:
    """For the commit comment: each error in full, the warnings as one short line."""
    errors = [f for f in findings if f["level"] == ERROR]
    warns = [f for f in findings if f["level"] == WARN]
    out = []
    if errors:
        out.append(f"**FILES CHECK:** {len(errors)} thing{'s' if len(errors) != 1 else ''} the site build needs "
                   f"{'are' if len(errors) != 1 else 'is'} wrong.\n")
        out += [f"- {f['message']} {f['hint']}".rstrip() for f in errors]
    if warns:
        out.append(("\n" if out else "") + f"_Files check: {len(warns)} warning{'s' if len(warns) != 1 else ''} "
                    "(details in the run summary): " + "; ".join(sorted({w["code"] for w in warns})) + "._")
    return "\n".join(out) + ("\n" if out else "")


def escape_command(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def github_lines(findings: list[Finding]) -> list[str]:
    out = []
    for f in sorted(findings, key=sort_key):
        kind = "error" if f["level"] == ERROR else "warning"
        out.append(f"::{kind} title=Files check ({f['code']})::{escape_command((f['message'] + ' ' + f['hint']).strip())}")
    return out


def apply_fixes(root: Path, findings: list[Finding]) -> list[str]:
    """Write the fixes that were found into the config files. Returns what was changed."""
    done = []
    for f in findings:
        fix = f.get("fix")
        if not fix:
            continue
        path = root / fix["file"]
        text = path.read_text(encoding="utf-8")
        old, new = json.dumps(fix["old"], ensure_ascii=False), json.dumps(fix["new"], ensure_ascii=False)
        if old in text:
            path.write_text(text.replace(old, new), encoding="utf-8", newline="")
            done.append(f"{fix['file']}: {fix['old']} -> {fix['new']}")
    return done


def install_hook(root: Path) -> str:
    hooks = root / ".git" / "hooks"
    if not hooks.is_dir():
        return "This folder has no .git/hooks, so nothing was installed."
    target = hooks / "pre-push"
    if target.exists() and HOOK_MARK not in target.read_text(encoding="utf-8", errors="replace"):
        return f"{target} already exists and is not this check's, so it was left alone."
    target.write_text(
        "#!/bin/sh\n" + HOOK_MARK + " --install-hook: shows what is wrong before a push; it never stops the push\n"
        "# (set DOCTOR_STRICT=1 to stop a push when something the site build needs is wrong)\n"
        'py=$(command -v python3 || command -v python || command -v py)\n'
        '[ -z "$py" ] && exit 0\n'
        '"$py" tools/whatif/doctor.py\n'
        "status=$?\n"
        '[ "$DOCTOR_STRICT" = "1" ] && exit $status\n'
        "exit 0\n",
        encoding="utf-8", newline="\n",
    )
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return f"Installed {target}: every `git push` now shows what is wrong first (it never blocks the push)."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=".", help="the repository's folder (default: here)")
    parser.add_argument("--config", default="aero_modeling/whatif_config.json")
    parser.add_argument("--sim-config", default="aero_modeling/sim_config.json")
    parser.add_argument("--json", default="", help="also write the findings to this file")
    parser.add_argument("--markdown", default="", help="also write the short text for the commit comment (only when something is wrong)")
    parser.add_argument("--github", action="store_true", help="also print GitHub workflow annotations")
    parser.add_argument("--fix", action="store_true", help="show the repairs for paths that moved")
    parser.add_argument("--write", action="store_true", help="with --fix: make the repairs in the config files")
    parser.add_argument("--warn-only", action="store_true", help="always exit with 0")
    parser.add_argument("--install-hook", action="store_true", help="show this check before every git push")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if args.install_hook:
        print(install_hook(root))
        return 0
    findings, _ = run_checks(root, root / args.config, root / args.sim_config)
    print(text_report(findings))
    fixes = [f for f in findings if f.get("fix")]
    if args.fix:
        for f in fixes:
            fix = f["fix"]
            print(f"fix: in {fix['file']} change '{fix['old']}' to '{fix['new']}'" + ("  (check this one: several places fit)" if fix.get("ambiguous") else ""))
        if args.write and fixes:
            print("written:\n  " + "\n  ".join(apply_fixes(root, findings)) + "\nCheck the change with git diff, then commit it.")
        elif fixes:
            print("Nothing was changed. Add --write to make these changes.")
    elif fixes:
        print("Some of these can be repaired: run with --fix to see how.")
    if args.github:
        print("\n".join(github_lines(findings)))
    if args.json:
        Path(args.json).write_text(json.dumps(findings, indent=1), encoding="utf-8")
    if args.markdown and findings:
        Path(args.markdown).write_text(comment_text(findings), encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and findings:
        with open(summary, "a", encoding="utf-8") as out:
            out.write("### Files check\n\n" + "\n".join(f"- **{f['level']}** {f['message']} {f['hint']}" for f in sorted(findings, key=sort_key)) + "\n")
    errors = any(f["level"] == ERROR for f in findings)
    return 0 if args.warn_only or not errors else 1


if __name__ == "__main__":
    sys.exit(main())

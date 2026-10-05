"""Build the predictions page and 3D flight of every rocket in aero_modeling/whatif_config.json.

    python tools/whatif/build_site.py --config aero_modeling/whatif_config.json --site site

The page and flight (Jarvis and Vision) come from the flight_sim package (``pip install``
it from the flight_sim repo): it reads the OpenRocket design, takes the aerodynamics from the
RASAero CSV and flies the rocket. When the History site is already in ``--site``, the page
puts OpenRocket's numbers from it next to Jarvis's, and writes ``jarvis_by_commit.json`` (Jarvis
flown on every commit of the design) for the History tab to draw. See tools/whatif/README.md.

With ``--edith`` each rocket is then flown many times by EDITH (the team's Monte Carlo simulation, also in
flight_sim). It adds its own page at ``predictions/edith/`` and the
cloud of flights in Vision. It takes minutes, so its result is kept in ``--edith-cache`` until something it
depends on changes, and whatever goes wrong with it leaves the pages built before it exactly as they were.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aero_meta  # noqa: E402  (same folder; only the standard library)
import resolve  # noqa: E402
from doctor import Repo, git  # noqa: E402

EDITH_MINUTES = 10.0  # EDITH stops starting new rounds of flights after this long
EDITH_GRACE_S = 90  # on top of that, for the round it is in and for writing the pages

_FILES = ("ork", "aero")
_MOTOR_SUFFIXES = (".eng", ".rse")


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def _stamp(path: Path) -> float:
    """When a file last changed: its last commit, or its file time if it is untracked or edited since."""
    folder, name = path.parent, path.name
    try:
        tracked = _git(["ls-files", "--error-unmatch", name], folder).returncode == 0
        if tracked and _git(["diff", "HEAD", "--ignore-space-at-eol", "--quiet", "--", name], folder).returncode == 0:
            committed = _git(["log", "-1", "--format=%ct", "--", name], folder).stdout.strip()
            if committed:
                return float(committed)
    except OSError:  # no git on this machine
        pass
    return path.stat().st_mtime


def newest_thrust_curve(folder: Path) -> tuple[Path, str]:
    """The motor file the propulsion model wrote last, and how it was chosen.

    Looks at the .eng and .rse files directly in ``folder`` (not its subfolders). A motor is usually
    both files, so they are judged together by their newest change, and the .eng is used.
    """
    files = [p for p in folder.glob("*") if p.is_file() and p.suffix.lower() in _MOTOR_SUFFIXES]
    if not files:
        raise SystemExit(f"There is no .eng or .rse motor file in {folder}.")
    stems: dict[str, list[Path]] = {}
    for path in files:
        stems.setdefault(path.stem, []).append(path)
    newest = max(stems, key=lambda s: (max(_stamp(p) for p in stems[s]), s))
    chosen = sorted(stems[newest], key=lambda p: p.suffix.lower() != ".eng")[0]
    return chosen, f"newest of {len(stems)} in {folder.name}"


def history_motor_file(config_dir: Path, spec: dict) -> Path | None:
    """The motor file the History tab's OpenRocket runs use for this design, or None if unknown.

    Read from sim_config.json (next to the whatif config, or the file named by the rocket's
    ``openrocket_config`` setting): the design's ``default_motor``, a path relative to that file.
    """
    path = config_dir / spec.get("openrocket_config", "sim_config.json")
    try:
        files = json.loads(path.read_text(encoding="utf-8")).get("files", {})
    except (OSError, ValueError):
        return None
    motor = (files.get(Path(spec["ork"]).as_posix()) or {}).get("default_motor")
    return path.parent / motor if isinstance(motor, str) else None


def history_motor(config_dir: Path, spec: dict) -> str:
    """Name of the motor file the History tab flies for this design, or '' if unknown."""
    file = history_motor_file(config_dir, spec)
    return file.name if file else ""


_SEARCH = {"ork": (".ork",), "rasaero": (".cdx1",)}  # files that may be found as "the only one in the rocket's folder"


def _resolve_files(repo: Repo, base: Path, spec: dict, notes: resolve.Notes) -> dict[str, resolve.Resolution]:
    """Find the design, aero table, RASAero file and RASAero results the config names (see resolve.py)."""
    found: dict[str, resolve.Resolution] = {}
    folder = resolve.rocket_folder(base, spec)
    for role in (*_FILES, "rasaero", "rasaero_results"):
        if role not in spec:
            continue
        suffixes = _SEARCH.get(role, ())
        got = resolve.resolve_file(repo, base, spec[role], role, folder if suffixes else None, suffixes)
        if got is None:
            raise SystemExit(f"{role}: these files are missing: {base / spec[role]} (git has no record of it moving). "
                             "Run 'python tools/whatif/doctor.py' to see what changed.")
        found[role] = got
    return found


def _changed_in_last_commit(repo: Repo, path: Path) -> bool:
    """True when the newest commit changed this file (False without git or history)."""
    done = git(repo.root, "diff", "--name-only", "HEAD~1", "HEAD", "--", repo.rel(path))
    return bool(done is not None and done.returncode == 0 and isinstance(done.stdout, str) and done.stdout.strip())


def files_used(repo, base, key, spec, mode, found, motor, note, history_file, notes) -> dict:
    """What files_used.json says: the files the page was built from, how each was found, and what to mention."""
    def rel(path: Path) -> str:
        return resolve.config_value(base, path)

    def others(role: str, chosen: Path) -> list[dict]:
        if role == "ork":
            pool = resolve.candidates(repo, resolve.rocket_folder(base, spec), (".ork",))
        elif role == "rasaero":
            pool = resolve.candidates(repo, resolve.rocket_folder(base, spec), (".cdx1",))
        elif role == "aero":
            pool = resolve.candidates(repo, chosen.parent, ("_aero.csv",))
        elif role == "motor":
            pool = resolve.candidates(repo, base / spec["motor_dir"], resolve.MOTOR_SUFFIXES, resolve.DEFAULT_IGNORE) \
                if "motor_dir" in spec else []
        else:
            return []
        return [{"path": rel(p), "changed": resolve.changed_date(repo, p)} for p in pool[: resolve.MAX_CANDIDATES]]

    files = []
    labels = {"ork": "OpenRocket design", "aero": "Aero table (RASAero CSV)", "rasaero": "RASAero file",
              "rasaero_results": "RASAero results"}
    for role, r in found.items():
        files.append({"role": role, "label": labels[role], "setting": role, "path": rel(r.path), "how": r.how,
                      "auto": r.auto, "changed": resolve.changed_date(repo, r.path), "candidates": others(role, r.path)})
    files.append({"role": "motor", "label": "Motor thrust curve", "setting": "motor",
                  "path": rel(motor.path), "how": note or motor.how, "auto": motor.auto,
                  "changed": resolve.changed_date(repo, motor.path), "candidates": others("motor", motor.path)})
    table: dict = {}
    if "aero" in found and _changed_in_last_commit(repo, found["aero"].path):
        notes.add("warn", f"The aero table {found['aero'].path.name} was changed by the latest push. Merging is turned off for it, "
                          "so if two people updated it, check that the version on main is the one you meant.")
    if "aero" in found:
        table = aero_meta.check(found["aero"].path, found["rasaero"].path if "rasaero" in found else None, found["ork"].path)
        for reason in table["stale"]:
            notes.add("warn", f"The aero table may be out of date: {reason}. Open the local helper and press Update CSV.")
    return {"version": 1, "rocket": key, "motor_mode": mode,
            "history_motor": history_file.name if history_file else "", "files": files,
            "notices": notes.notices, "table": table}


def write_files_used(build: dict) -> None:
    """Put files_used.json next to the page (the page lists the files and any notice from it)."""
    build["out"].mkdir(parents=True, exist_ok=True)
    (build["out"] / "files_used.json").write_text(json.dumps(build["files_used"], indent=1) + "\n", encoding="utf-8")


def write_notes(builds: list[dict], target: Path) -> None:
    """One short line per auto-fix or warning, for the commit comment and Discord (errors are reported elsewhere)."""
    lines = [f"- {b['key']}: {n['text']}" for b in builds for n in b["files_used"]["notices"] if n["level"] != "error"]
    if lines:
        target.write_text("Build notes:\n" + "\n".join(lines) + "\n", encoding="utf-8")
    elif target.exists():
        target.unlink()


def plan(config_path: Path, site: Path, edith: bool = False, edith_cache: Path | None = None) -> list[dict]:
    """The builds the config asks for: where each goes and the commands that make it.

    With ``edith`` each build also has the command that runs EDITH on the page it makes. A rocket's own
    settings may name ``edith_site`` (a JSON file of the launch site's wind, weather and target, relative to the
    config), ``edith_minutes`` (how long EDITH may take) and ``edith_accepted`` (IREC recommendations the team
    has accepted, by check id, such as ``stability_static_max``: the EDITH page shows them greyed); without them
    EDITH uses its placeholder site.
    """
    config = json.loads(config_path.read_text(encoding="utf-8"))
    rockets = config["rockets"]
    default = config.get("default") or next(iter(rockets))
    if default not in rockets:
        raise SystemExit(f"{config_path}: the default rocket {default!r} is not listed under 'rockets'.")
    base = config_path.parent
    top = git(base, "rev-parse", "--show-toplevel")
    named = (top.stdout or "").strip() if top is not None and top.returncode == 0 else ""
    repo = Repo(Path(named) if named else base)
    ignore = tuple(config.get("motor_ignore", resolve.DEFAULT_IGNORE))
    builds = []
    for key, spec in rockets.items():
        missing = [k for k in _FILES if k not in spec]
        if missing or not ("motor" in spec or "motor_dir" in spec):
            needs = [*missing, *([] if "motor" in spec or "motor_dir" in spec else ["motor or motor_dir"])]
            raise SystemExit(f"{config_path}: rocket {key!r} is missing these settings: {', '.join(needs)}.")
        notes = resolve.Notes()
        found = _resolve_files(repo, base, spec, notes)
        paths = {k: r.path for k, r in found.items()}
        mode = str(spec.get("motor_mode", config.get("motor_mode", "pinned"))).lower()
        history_file = history_motor_file(base, spec)
        motor, note = resolve.resolve_motor(repo, base, spec, mode, history_file, notes, ignore)
        if motor is None:
            where = spec.get("motor") or spec.get("motor_dir")
            raise SystemExit(f"Rocket {key!r}: there is no motor file at {base / where} and git has no record of it moving. "
                             "Run 'python tools/whatif/doctor.py' to see what changed.")
        paths["motor"] = motor.path
        absent = [str(p) for p in paths.values() if not p.exists()]
        if absent:
            raise SystemExit(f"Rocket {key!r}: these files are missing: " + "; ".join(absent))
        for role, r in found.items():
            if r.auto:
                notes.add("warn", f"{Path(r.old).name or role} was not where the config says; {r.how}: "
                                  f"'{resolve.config_value(base, r.path)}'. Change '{role}' in whatif_config.json.")
        out = site / "predictions" if key == default else site / "predictions" / key.lower()
        cmd = [
            sys.executable, "-m", "flight_sim.whatif.build",
            "--ork", str(paths["ork"]), "--aero", str(paths["aero"]),
            "--motor", str(paths["motor"]), "--out", str(out),
            "--name", spec.get("name", key),
        ]  # fmt: skip
        if note:
            cmd += ["--motor-note", note]
        if "rasaero" in paths:
            cmd += ["--rasaero", str(paths["rasaero"])]
        if "rasaero_results" in paths:
            cmd += ["--rasaero-results", str(paths["rasaero_results"])]
        if "sim" in spec:
            cmd += ["--sim", spec["sim"]]
        if (site / "data.json").is_file():  # the History site was built first
            cmd += ["--history-site", str(site)]
            history_name = history_motor(base, spec)
            if history_name:
                cmd += ["--history-motor", history_name]
        by_commit = None
        if (site / "data.json").is_file():  # Jarvis on every commit, for the History tab's extra line
            by_commit = [
                sys.executable, "-m", "flight_sim.whatif.commits",
                "--ork", str(paths["ork"]), "--aero", str(paths["aero"]),
                "--motor", str(paths["motor"]), "--history-site", str(site),
                "--out", str(out / "by_commit.json"),
            ]  # fmt: skip
            if "rasaero" in paths:
                by_commit += ["--rasaero", str(paths["rasaero"])]
        edith_cmd = None
        minutes = float(spec.get("edith_minutes", EDITH_MINUTES))
        if edith:
            edith_cmd = [
                sys.executable, "-m", "flight_sim.whatif.edith_site",
                "--ork", str(paths["ork"]), "--aero", str(paths["aero"]),
                "--motor", str(paths["motor"]), "--out", str(out),
                "--name", spec.get("name", key), "--minutes", f"{minutes:g}",
            ]  # fmt: skip
            if "sim" in spec:
                edith_cmd += ["--sim", spec["sim"]]
            if "edith_site" in spec:
                if not (base / spec["edith_site"]).is_file():
                    raise SystemExit(f"Rocket {key!r}: the edith_site file {base / spec['edith_site']} is missing.")
                edith_cmd += ["--site", str(base / spec["edith_site"])]
            if edith_cache is not None:
                edith_cmd += ["--cache", str(edith_cache / key.lower())]
            accepted = [str(a).strip() for a in spec.get("edith_accepted", []) if str(a).strip()]
            if accepted:  # IREC recommendations the team accepts: greyed on the EDITH page, not a reason to rerun it
                edith_cmd += ["--accepted", ",".join(accepted)]
        used = files_used(repo, base, key, spec, mode, found, motor, note, history_file, notes)
        builds.append({
            "key": key, "out": out, "cmd": cmd, "by_commit": by_commit, "motor": paths["motor"], "note": note,
            "edith": edith_cmd, "edith_timeout_s": minutes * 60 * 1.5 + EDITH_GRACE_S, "files_used": used,
        })  # fmt: skip
    return builds


def merge_by_commit(builds: list[dict], site: Path) -> Path | None:
    """Put every rocket's by-commit numbers into one file the History page loads.

    Each build wrote ``by_commit.json`` next to its page; they are joined into
    ``site/jarvis_by_commit.json`` by the design's path, and the single files are removed.
    """
    designs: dict[str, dict] = {}
    for build in builds:
        single = build["out"] / "by_commit.json"
        if not single.is_file():
            continue
        try:
            data = json.loads(single.read_text(encoding="utf-8"))
            designs[data["design"]] = {k: data[k] for k in ("motor", "note", "skipped", "sims")}
        except (ValueError, KeyError):
            pass  # unreadable: the History tab simply has no Jarvis line for it
        single.unlink()
    if not designs:
        return None
    target = site / "jarvis_by_commit.json"
    target.write_text(json.dumps({"designs": designs}, separators=(",", ":")), encoding="utf-8")
    return target


def run_edith(build: dict) -> bool:
    """Run EDITH for one built rocket. True if its pages were made; a failure or a timeout is only reported.

    EDITH never changes a page unless it finishes, so after a failure the pages are as the build left them.
    """
    try:
        done = subprocess.run(build["edith"], check=False, timeout=build["edith_timeout_s"])
    except subprocess.TimeoutExpired:
        print(f"  EDITH for {build['key']} took longer than {build['edith_timeout_s'] / 60:.0f} minutes and was stopped; "
              "the pages are left without it.", flush=True)
        return False
    except OSError as error:
        print(f"  EDITH for {build['key']} could not start ({error}); the pages are left without it.", flush=True)
        return False
    if done.returncode:
        print(f"  EDITH for {build['key']} failed (exit {done.returncode}); the pages are left without it.", flush=True)
    return not done.returncode


def main(argv: list[str] | None = None) -> int:
    """Command line entry; returns the exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="aero_modeling/whatif_config.json")
    parser.add_argument("--site", default="site", help="the folder GitHub Pages publishes")
    parser.add_argument("--dry-run", action="store_true", help="print the commands only")
    parser.add_argument("--edith", action="store_true", help="also run EDITH (the Monte Carlo simulation) on each rocket")
    parser.add_argument("--notes", default=None, help="write one line per auto-fix or warning to this file")
    parser.add_argument("--edith-cache", default=None, help="folder that keeps EDITH's result between builds")
    args = parser.parse_args(argv)
    builds = plan(Path(args.config), Path(args.site), args.edith, Path(args.edith_cache) if args.edith_cache else None)
    if args.notes:
        write_notes(builds, Path(args.notes))
    for build in builds:
        for notice in build["files_used"]["notices"]:
            print(f"  [{notice['level']}] {notice['text']}", flush=True)
        print(f"{build['key']} -> {build['out']} (motor {build['motor'].name}, {build['note'] or 'named in the config'})", flush=True)
        if args.dry_run:
            print("  " + " ".join(build["cmd"]))
            if build["by_commit"]:
                print("  " + " ".join(build["by_commit"]))
            if build["edith"]:
                print("  " + " ".join(build["edith"]))
            continue
        subprocess.run(build["cmd"], check=True)
        write_files_used(build)
        if build["by_commit"]:  # a problem here must not stop the page, only the extra History line
            if subprocess.run(build["by_commit"], check=False).returncode:
                print(f"  (no Jarvis line by commit for {build['key']})", flush=True)
        if build["edith"]:  # last: it only adds to the pages above, and may fail without hurting them
            run_edith(build)
    merged = merge_by_commit(builds, Path(args.site))
    if merged:
        print(f"Jarvis by commit for the History tab: {merged}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

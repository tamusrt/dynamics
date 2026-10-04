"""Build the predictions page and 3D flight of every rocket in aero_modeling/whatif_config.json.

    python tools/whatif/build_site.py --config aero_modeling/whatif_config.json --site site

The page and flight (Jarvis and Vision) come from the flight_sim package (``pip install``
it from the flight_sim repo): it reads the OpenRocket design, takes the aerodynamics from the
RASAero CSV and flies the rocket. When the History site is already in ``--site``, the page
puts OpenRocket's numbers from it next to Jarvis's, and writes ``jarvis_by_commit.json`` (Jarvis
flown on every commit of the design) for the History tab to draw. See tools/whatif/README.md.

With ``--edith`` each rocket is then flown many times by EDITH (the team's Monte Carlo simulation, also in
flight_sim). It adds its own page at ``predictions/edith/``, a summary card on the Predictions page and the
cloud of flights in Vision. It takes minutes, so its result is kept in ``--edith-cache`` until something it
depends on changes, and whatever goes wrong with it leaves the pages built before it exactly as they were.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

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


def plan(config_path: Path, site: Path, edith: bool = False, edith_cache: Path | None = None) -> list[dict]:
    """The builds the config asks for: where each goes and the commands that make it.

    With ``edith`` each build also has the command that runs EDITH on the page it makes. A rocket's own
    settings may name ``edith_site`` (a JSON file of the launch site's wind, weather and target, relative to the
    config) and ``edith_minutes`` (how long EDITH may take); without them EDITH uses its placeholder site.
    """
    config = json.loads(config_path.read_text(encoding="utf-8"))
    rockets = config["rockets"]
    default = config.get("default") or next(iter(rockets))
    if default not in rockets:
        raise SystemExit(f"{config_path}: the default rocket {default!r} is not listed under 'rockets'.")
    base = config_path.parent
    builds = []
    for key, spec in rockets.items():
        missing = [k for k in _FILES if k not in spec]
        if missing or not ("motor" in spec or "motor_dir" in spec):
            needs = [*missing, *([] if "motor" in spec or "motor_dir" in spec else ["motor or motor_dir"])]
            raise SystemExit(f"{config_path}: rocket {key!r} is missing these settings: {', '.join(needs)}.")
        paths = {k: base / spec[k] for k in (*_FILES, "rasaero", "rasaero_results") if k in spec}
        note = ""
        if "motor" in spec:  # one motor named in the config beats everything else
            paths["motor"] = base / spec["motor"]
        elif "motor_dir" in spec:
            if not (base / spec["motor_dir"]).is_dir():
                raise SystemExit(f"Rocket {key!r}: the motor_dir {base / spec['motor_dir']} is not a folder.")
            # Jarvis flies the motor the History tab flies, so the two always compare like with like;
            # without one (or when its file is missing) it flies the newest in the folder
            same = history_motor_file(base, spec)
            if same is not None and same.suffix.lower() in _MOTOR_SUFFIXES and same.is_file():
                paths["motor"], note = same, "same as the History tab"
            else:
                paths["motor"], note = newest_thrust_curve(base / spec["motor_dir"])
        absent = [str(p) for p in paths.values() if not p.exists()]
        if absent:
            raise SystemExit(f"Rocket {key!r}: these files are missing: " + "; ".join(absent))
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
            motor = history_motor(base, spec)
            if motor:
                cmd += ["--history-motor", motor]
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
        builds.append({
            "key": key, "out": out, "cmd": cmd, "by_commit": by_commit, "motor": paths["motor"], "note": note,
            "edith": edith_cmd, "edith_timeout_s": minutes * 60 * 1.5 + EDITH_GRACE_S,
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
    parser.add_argument("--edith-cache", default=None, help="folder that keeps EDITH's result between builds")
    args = parser.parse_args(argv)
    builds = plan(Path(args.config), Path(args.site), args.edith, Path(args.edith_cache) if args.edith_cache else None)
    for build in builds:
        print(f"{build['key']} -> {build['out']} (motor {build['motor'].name}, {build['note'] or 'named in the config'})", flush=True)
        if args.dry_run:
            print("  " + " ".join(build["cmd"]))
            if build["by_commit"]:
                print("  " + " ".join(build["by_commit"]))
            if build["edith"]:
                print("  " + " ".join(build["edith"]))
            continue
        subprocess.run(build["cmd"], check=True)
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

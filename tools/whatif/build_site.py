"""Build the predictions page and 3D flight of every rocket in aero_modeling/whatif_config.json.

    python tools/whatif/build_site.py --config aero_modeling/whatif_config.json --site site

The page and flight come from the flight_sim package (``pip install`` it from the
flight_sim repo): it reads the OpenRocket design, takes the aerodynamics from the
RASAero CSV and flies the 6-DOF sim. See tools/whatif/README.md.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

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
        raise SystemExit(f"{folder}: no .eng or .rse thrust curve in it")
    stems: dict[str, list[Path]] = {}
    for path in files:
        stems.setdefault(path.stem, []).append(path)
    newest = max(stems, key=lambda s: (max(_stamp(p) for p in stems[s]), s))
    chosen = sorted(stems[newest], key=lambda p: p.suffix.lower() != ".eng")[0]
    return chosen, f"newest of {len(stems)} in {folder.name}"


def plan(config_path: Path, site: Path) -> list[dict]:
    """The builds the config asks for: where each goes and the command that makes it."""
    config = json.loads(config_path.read_text(encoding="utf-8"))
    rockets = config["rockets"]
    default = config.get("default") or next(iter(rockets))
    if default not in rockets:
        raise SystemExit(f"{config_path}: default rocket {default!r} is not in 'rockets'")
    base = config_path.parent
    builds = []
    for key, spec in rockets.items():
        missing = [k for k in _FILES if k not in spec]
        if missing or not ("motor" in spec or "motor_dir" in spec):
            needs = [*missing, *([] if "motor" in spec or "motor_dir" in spec else ["motor or motor_dir"])]
            raise SystemExit(f"{config_path}: rocket {key!r} needs {', '.join(needs)}")
        paths = {k: base / spec[k] for k in (*_FILES, "rasaero") if k in spec}
        note = ""
        if "motor_dir" in spec:
            if not (base / spec["motor_dir"]).is_dir():
                raise SystemExit(f"rocket {key!r}: motor_dir {base / spec['motor_dir']} is not a folder")
            paths["motor"], note = newest_thrust_curve(base / spec["motor_dir"])
        else:
            paths["motor"] = base / spec["motor"]
        absent = [str(p) for p in paths.values() if not p.exists()]
        if absent:
            raise SystemExit(f"rocket {key!r}: missing file(s): " + "; ".join(absent))
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
        if "sim" in spec:
            cmd += ["--sim", spec["sim"]]
        builds.append({"key": key, "out": out, "cmd": cmd, "motor": paths["motor"], "note": note})
    return builds


def main(argv: list[str] | None = None) -> int:
    """Command line entry; returns the exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="aero_modeling/whatif_config.json")
    parser.add_argument("--site", default="site", help="the folder GitHub Pages publishes")
    parser.add_argument("--dry-run", action="store_true", help="print the commands only")
    args = parser.parse_args(argv)
    for build in plan(Path(args.config), Path(args.site)):
        print(f"{build['key']} -> {build['out']} (motor {build['motor'].name}, {build['note'] or 'named in the config'})", flush=True)
        if args.dry_run:
            print("  " + " ".join(build["cmd"]))
            continue
        subprocess.run(build["cmd"], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

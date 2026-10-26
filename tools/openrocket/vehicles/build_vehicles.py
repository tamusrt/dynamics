"""Build the Vehicles page: one static HTML page showing the geometry of every OpenRocket design.

    python tools/openrocket/vehicles/build_vehicles.py --config aero_modeling/sim_config.json --out site/vehicles
           [--template <path>] [--commit <sha>] [--date <text>]

CI runs this after the simulation check: it parses each .ork listed under "files" in sim_config.json
(in that order, skipping ignored or missing files) with ork_geometry.parse, no Java needed, and fills
template.html's __GEOMETRY__ (a JSON object {label: geometry}) and __BUILT__ (a provenance string).
Writes <out>/index.html and <out>/geometry.json. Exit 2 when no design could be parsed.
"""
import argparse
import datetime as dt
import fnmatch
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ork_geometry  # noqa: E402


def is_ignored(cfg_rel: str, patterns) -> bool:
    """Same rule as or_ci: patterns are globs relative to the config folder."""
    return any(fnmatch.fnmatch(cfg_rel, p) or fnmatch.fnmatch("/" + cfg_rel, p) for p in patterns)


def unique_label(stem: str, taken) -> str:
    label, n = stem, 2
    while label in taken:
        label, n = f"{stem} ({n})", n + 1
    return label


def short_sha(commit, repo: Path) -> str:
    if commit:
        return commit[:7]
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo, capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "working tree"


def build(config: Path, out: Path, template: Path, commit=None, date=None) -> int:
    cfg = json.loads(config.read_text(encoding="utf-8"))
    designs = {}
    for cfg_rel in cfg.get("files", {}):
        path = config.parent / cfg_rel
        if is_ignored(cfg_rel, cfg.get("ignore", [])):
            print(f"warning: {cfg_rel} matches an ignore pattern, skipped")
            continue
        if not path.is_file():
            print(f"warning: {cfg_rel} not found, skipped")
            continue
        label = unique_label(path.stem, designs)
        designs[label] = geo = ork_geometry.parse(path)
        print(f"{label}: length {geo['length']:.3f} m, {len(geo['components'])} components")
    if not designs:
        print("no design could be built from the config", file=sys.stderr)
        return 2
    built = f"from the .ork files at {short_sha(commit, config.parent)}, {date or dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d')}"
    html = template.read_text(encoding="utf-8")
    embedded = json.dumps(designs, separators=(",", ":")).replace("</", "<\\/")   # safe inside <script>
    html = html.replace("__GEOMETRY__", embedded).replace("__BUILT__", built)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / "geometry.json").write_text(json.dumps(designs, indent=1), encoding="utf-8")
    print(f"Wrote {len(designs)} design(s) to {out / 'index.html'} ({built})")
    return 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):   # design names may be non-ASCII; Windows consoles default to cp1252
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    p = argparse.ArgumentParser(description="Build the static Vehicles page from the .ork files in sim_config.json.")
    p.add_argument("--config", required=True, help="Path to sim_config.json")
    p.add_argument("--out", required=True, help="Output folder (index.html and geometry.json are written there)")
    p.add_argument("--template", default=str(HERE / "template.html"), help="Page template (default: template.html next to this script)")
    p.add_argument("--commit", default=None, help="Commit sha for the provenance line (default: git HEAD, else 'working tree')")
    p.add_argument("--date", default=None, help="Date text for the provenance line (default: today, UTC)")
    a = p.parse_args(argv)
    return build(Path(a.config).resolve(), Path(a.out), Path(a.template), a.commit, a.date)


if __name__ == "__main__":
    sys.exit(main())

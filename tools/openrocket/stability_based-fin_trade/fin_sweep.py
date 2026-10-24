#!/usr/bin/env python3
"""Sweep fin geometry with ork_batch.py, simulate with batch_simulate.py, cull by stability (% of length).

    python fin_sweep.py --base AIRMAIL.ork --height 5.5:8.5:0.5 --minimum-stability 10 --worst-stability 8
"""

import argparse
import csv
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # the ORK Tools folder
sys.path.insert(0, str(ROOT / "batch-generation"))
from orkbatch.orkio import OrkDocument  # noqa: E402
from orkbatch.params import parse_numeric_text  # noqa: E402

FIN_ARGS = {"root": "rootchord", "tip": "tipchord", "height": "height", "sweep": "sweeplength"}
M_TO_IN = 39.3701


def num(el, tag):
    return parse_numeric_text(el.findtext(tag))[0]


def length_and_diameter(ork):
    """Total length and max diameter (m) of the airframe: nose cones, body tubes and transitions."""
    length, radius = 0.0, 0.0
    for stage in OrkDocument(ork).root.findall("rocket/subcomponents/stage"):
        for c in stage.find("subcomponents"):
            if c.tag in ("nosecone", "bodytube", "transition"):
                length += num(c, "length") or 0.0
                radius = max([radius] + [num(c, t) or 0.0 for t in ("radius", "aftradius", "foreradius")])
    return length, 2 * radius


def is_no_wind(sim):
    cond = sim.find("conditions")
    if (cond.findtext("windmodeltype") or "Average") == "MultiLevel":
        return all(float(w.get("speed")) == 0 for w in cond.iter("windlevel"))
    avg = cond.find("wind[@model='average']")
    return float(avg.findtext("speed") if avg is not None else cond.findtext("windaverage")) == 0


def no_wind_index(ork):
    sims = OrkDocument(ork).root.find("simulations")
    hits = [i for i, s in enumerate(sims if sims is not None else []) if is_no_wind(s)]
    if not hits:
        sys.exit(f"error: {Path(ork).name} has no no-wind simulation")
    return hits[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="reference .ork")
    for a in FIN_ARGS:
        ap.add_argument(f"--{a}", help="start:stop:step in inches")
    ap.add_argument("--ork-folder", help="output folder (default: fin_sweep_<timestamp>)")
    ap.add_argument("--maximum-stability", type=float, help="cull if no-wind stability ever exceeds this %%")
    ap.add_argument("--minimum-stability", type=float, help="cull if no-wind stability ever drops below this %%")
    ap.add_argument("--worst-stability", type=float, help="cull if any sim's stability ever drops below this %%")
    ap.add_argument("--motor", help="thrust curve (.eng/.rse) to fly in every simulation")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--jar", default=str(ROOT / "batch_sim" / "OpenRocket-24.12.jar"))
    args = ap.parse_args()

    varies = [f"fins.{tag}={getattr(args, a)}" for a, tag in FIN_ARGS.items() if getattr(args, a)]
    if not varies:
        ap.error("give at least one of --root/--tip/--height/--sweep")
    folder = Path(args.ork_folder or f"fin_sweep_{time.strftime('%Y%m%d_%H%M%S')}").resolve()
    sim_csv = folder / "sim_results.csv"

    gen = [sys.executable, str(ROOT / "batch-generation" / "ork_batch.py"), "gen", "-b", args.base,
           "-o", str(folder), "-y", "-q"]
    for v in varies:
        gen += ["-v", v]
    subprocess.run(gen, check=True)
    sim = [sys.executable, str(ROOT / "batch_sim" / "batch_simulate.py"), "--input-dir", str(folder),
           "--jar", args.jar, "--output-csv", str(sim_csv), "--workers", str(args.workers)]
    if args.motor:
        sim += ["--motor", args.motor]
    subprocess.run(sim, check=True)

    length, dia = length_and_diameter(args.base)
    pct = lambda cal: float(cal) * dia / length * 100 if cal != "" else float("nan")  # noqa: E731
    print(f"\nLength {length * M_TO_IN:.2f} in, diameter {dia * M_TO_IN:.2f} in")

    by_file = {}
    with open(sim_csv, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            by_file.setdefault(r["source_file"], []).append(r)

    out, kept, failed = [], 0, 0
    for path, rows in by_file.items():
        nw = no_wind_index(path)
        res = [{"file": Path(path).name, "simulation_name": r["simulation_name"],
                "no_wind": r["simulation_index"] == str(nw),
                "first_stability_pct": pct(r["first_stability_cal"]),
                "min_stability_pct": pct(r["min_stability_cal"]),
                "max_stability_pct": pct(r["max_stability_cal"]),
                "max_aoa_deg": r["max_aoa_deg"], "apogee_ft": r["apogee_ft"], "status": r["status"]}
               for r in rows]
        nwr = next((r for r in res if r["no_wind"]), None)
        if nwr is None or nwr["status"] != "OK":
            failed += 1
            continue
        if args.maximum_stability is not None and nwr["max_stability_pct"] > args.maximum_stability:
            continue
        if args.minimum_stability is not None and nwr["min_stability_pct"] < args.minimum_stability:
            continue
        if args.worst_stability is not None and any(r["min_stability_pct"] < args.worst_stability for r in res):
            continue
        kept += 1
        out += res

    with open(folder / "sweep_results.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["file", "simulation_name", "no_wind", "first_stability_pct",
                                          "min_stability_pct", "max_stability_pct", "max_aoa_deg", "apogee_ft",
                                          "status"])
        w.writeheader()
        w.writerows({k: round(v, 2) if isinstance(v, float) else v for k, v in r.items()} for r in out)
    print(f"{kept} rocket(s) kept, {len(by_file) - kept - failed} culled, {failed} no-wind sim failed "
          f"-> {folder / 'sweep_results.csv'}")


if __name__ == "__main__":
    main()

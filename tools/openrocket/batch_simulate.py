#!/usr/bin/env python3
r"""
batch_simulate.py
------------------
Batch-run OpenRocket flight simulations on a folder full of .ork files,
headlessly (no GUI, no manual "import" + "simulate" clicking), and write a
summary CSV with one row per simulation found in each file.

WHY THIS EXISTS
    OpenRocket has no official command-line/batch mode. The standard
    community workaround is to embed OpenRocket's own .jar in a JVM via
    JPype and drive its Java API from Python. This script does that using
    the `orlab` package (an actively maintained descendant of the older,
    now-archived `orhelper` package). If `orlab` isn't installed, it falls
    back to `orhelper` automatically.

WHAT IT ASSUMES
    - Each .ork file already has one or more simulations configured in it
      (i.e. you already picked a motor / configuration for it in the
      OpenRocket GUI at some point). The script runs whatever simulations
      are already saved in the file -- it does not invent new ones.
    - You have Java and the OpenRocket .jar available (see README.md).

BEFORE RUNNING ON YOUR FULL BATCH
    Run with --probe on a single test file first (see README.md, "First
    run / sanity check"). The Python wrapper packages for OpenRocket are
    small, community-maintained projects, and method names have shifted
    between versions in the past. --probe prints the live object structure
    so you (or Claude) can quickly patch the two or three accessor
    functions below (_get_motor_config_name, _get_apogee_time) if your
    installed version differs.

USAGE
    python batch_simulate.py --input-dir "C:\path\to\ork_files" --output-csv results.csv
    python batch_simulate.py --input-dir ./ork_files --output-csv results.csv --recursive
    python batch_simulate.py --input-dir ./ork_files --probe --limit 1
"""

import argparse
import csv
import math
import sys
import time
import traceback
from pathlib import Path

# ----------------------------------------------------------------------------
# Unit conversion helpers (OpenRocket's API returns everything in SI units --
# meters, m/s, m/s^2, seconds -- regardless of what units the GUI displays)
# ----------------------------------------------------------------------------
M_TO_FT = 3.28084
MS_TO_MPH = 2.23694
MS2_TO_G = 1.0 / 9.80665


def _import_orkit():
    """
    Import whichever OpenRocket Python wrapper is installed. Prefers `orlab`
    (actively maintained as of 2026) and falls back to the older `orhelper`.
    Returns (module, which_name).
    """
    try:
        import orlab as orkit  # type: ignore
        return orkit, "orlab"
    except ImportError:
        pass
    try:
        import orhelper as orkit  # type: ignore
        return orkit, "orhelper"
    except ImportError:
        pass
    print(
        "ERROR: Neither 'orlab' nor 'orhelper' is installed.\n"
        "Install one of them first, e.g.:\n"
        "    pip install orlab\n"
        "or:\n"
        "    pip install orhelper\n"
        "See README.md for full setup instructions (Java + OpenRocket.jar are also required).",
        file=sys.stderr,
    )
    sys.exit(1)


def _find_ork_files(input_dir: Path, recursive: bool):
    pattern = "**/*.ork" if recursive else "*.ork"
    return sorted(input_dir.glob(pattern))


def _safe_call(obj, *method_names, default=None):
    """
    Try a list of candidate method/attribute names on `obj` in order and
    return the first one that works. Used because different versions of
    the OpenRocket Java API / the Python wrappers have used slightly
    different accessor names for the same data over the years.
    """
    for name in method_names:
        try:
            attr = getattr(obj, name, None)
            if attr is None:
                continue
            value = attr() if callable(attr) else attr
            if value is not None:
                return value
        except Exception:
            continue
    return default


def _get_motor_config_name(sim):
    """
    Best-effort extraction of a human-readable motor/configuration name for
    a simulation. Falls back to the simulation's own name, then "".
    """
    # Try a few plausible paths through the OpenRocket object graph.
    config = _safe_call(sim, "getFlightConfiguration", "getConfiguration")
    if config is not None:
        name = _safe_call(
            config,
            "getMotorConfigurationDescription",
            "getName",
            "toString",
        )
        if name:
            return str(name)
    return str(_safe_call(sim, "getName", default="") or "")


def _get_apogee_time(events, FlightEvent):
    """
    Pull the time of the APOGEE event out of whatever shape `get_events`
    returns (dict-of-lists is typical for orhelper/orlab).
    """
    try:
        apogee_key = getattr(FlightEvent, "APOGEE", None)
        if apogee_key is None or events is None:
            return None
        times = events.get(apogee_key)
        if times:
            return float(times[0])
    except Exception:
        pass
    return None


def probe_one_file(ork_path: Path, jar_path):
    """
    Diagnostic mode: load one file, run its first simulation, and dump the
    live shape of the objects involved so it's easy to see actual method
    names if this script's assumptions don't match your installed version.
    """
    orkit, which = _import_orkit()
    print(f"Using wrapper package: {which}")

    instance_ctx = orkit.OpenRocketInstance(jar_path) if jar_path else orkit.OpenRocketInstance()
    with instance_ctx as instance:
        helper = orkit.Helper(instance)
        doc = helper.load_doc(str(ork_path))
        sim_count = _safe_call(doc, "getSimulationCount", default=0)
        print(f"File: {ork_path}")
        print(f"Simulation count reported: {sim_count}")
        if not sim_count:
            print("No simulations found in this file -- nothing to probe.")
            return

        sim = doc.getSimulation(0)
        print("\n--- dir(sim) ---")
        print([a for a in dir(sim) if not a.startswith("_")])

        helper.run_simulation(sim)

        FlightDataType = getattr(orkit, "FlightDataType")
        data = helper.get_timeseries(
            sim,
            [
                FlightDataType.TYPE_TIME,
                FlightDataType.TYPE_ALTITUDE,
                FlightDataType.TYPE_VELOCITY_Z,
                FlightDataType.TYPE_ACCELERATION_TOTAL,
            ],
        )
        print("\n--- timeseries keys / lengths ---")
        for k, v in data.items():
            print(f"  {k}: {len(v)} samples, max={max(v) if len(v) else None}")

        events = helper.get_events(sim)
        print("\n--- events ---")
        print(events)

        print("\nMotor config name guess:", _get_motor_config_name(sim))


def run_batch(input_dir: Path, output_csv: Path, recursive: bool, jar_path, limit=None, verbose=False):
    orkit, which = _import_orkit()
    print(f"Using wrapper package: {which}")

    ork_files = _find_ork_files(input_dir, recursive)
    if limit:
        ork_files = ork_files[:limit]

    if not ork_files:
        print(f"No .ork files found in {input_dir} (recursive={recursive}).")
        return

    print(f"Found {len(ork_files)} .ork file(s). Starting JVM (this can take ~10-20s)...")

    fieldnames = [
        "source_file",
        "simulation_index",
        "simulation_name",
        "motor_configuration",
        "apogee_m",
        "apogee_ft",
        "max_velocity_ms",
        "max_velocity_mph",
        "max_acceleration_ms2",
        "max_acceleration_g",
        "time_to_apogee_s",
        "flight_time_s",
        "status",
        "notes",
    ]

    start = time.time()
    processed_sims = 0
    failed_files = 0

    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        f.flush()

        instance_ctx = orkit.OpenRocketInstance(jar_path) if jar_path else orkit.OpenRocketInstance()
        with instance_ctx as instance:
            helper = orkit.Helper(instance)
            FlightDataType = getattr(orkit, "FlightDataType")
            FlightEvent = getattr(orkit, "FlightEvent")

            for idx, ork_path in enumerate(ork_files, start=1):
                print(f"[{idx}/{len(ork_files)}] {ork_path.name}")
                try:
                    doc = helper.load_doc(str(ork_path))
                except Exception as e:
                    print(f"    FAILED to load: {e}")
                    writer.writerow({
                        "source_file": str(ork_path),
                        "simulation_index": "",
                        "simulation_name": "",
                        "motor_configuration": "",
                        "apogee_m": "", "apogee_ft": "",
                        "max_velocity_ms": "", "max_velocity_mph": "",
                        "max_acceleration_ms2": "", "max_acceleration_g": "",
                        "time_to_apogee_s": "", "flight_time_s": "",
                        "status": "LOAD_ERROR",
                        "notes": str(e).replace("\n", " ")[:300],
                    })
                    f.flush()
                    failed_files += 1
                    continue

                sim_count = _safe_call(doc, "getSimulationCount", default=0) or 0
                if sim_count == 0:
                    print("    No simulations saved in this file -- skipping.")
                    writer.writerow({
                        "source_file": str(ork_path),
                        "simulation_index": "",
                        "simulation_name": "",
                        "motor_configuration": "",
                        "apogee_m": "", "apogee_ft": "",
                        "max_velocity_ms": "", "max_velocity_mph": "",
                        "max_acceleration_ms2": "", "max_acceleration_g": "",
                        "time_to_apogee_s": "", "flight_time_s": "",
                        "status": "NO_SIMULATIONS",
                        "notes": "File has no configured simulation -- open it in the GUI once "
                                 "and set up/select a motor & simulation, then re-export the .ork.",
                    })
                    f.flush()
                    continue

                for sim_idx in range(sim_count):
                    try:
                        sim = doc.getSimulation(sim_idx)
                        sim_name = str(_safe_call(sim, "getName", default=f"sim_{sim_idx}"))
                        motor_cfg = _get_motor_config_name(sim)

                        helper.run_simulation(sim)

                        data = helper.get_timeseries(
                            sim,
                            [
                                FlightDataType.TYPE_TIME,
                                FlightDataType.TYPE_ALTITUDE,
                                FlightDataType.TYPE_VELOCITY_Z,
                                FlightDataType.TYPE_ACCELERATION_TOTAL,
                            ],
                        )

                        times = list(data[FlightDataType.TYPE_TIME])
                        altitudes = list(data[FlightDataType.TYPE_ALTITUDE])
                        velocities = list(data[FlightDataType.TYPE_VELOCITY_Z])
                        accels = list(data[FlightDataType.TYPE_ACCELERATION_TOTAL])

                        apogee_m = max(altitudes) if altitudes else float("nan")
                        max_v = max(velocities) if velocities else float("nan")
                        max_a = max(accels) if accels else float("nan")
                        flight_time = times[-1] if times else float("nan")

                        events = helper.get_events(sim)
                        t_apogee = _get_apogee_time(events, FlightEvent)

                        notes = ""
                        if math.isnan(apogee_m):
                            notes = "No altitude data returned -- simulation may have failed silently."

                        writer.writerow({
                            "source_file": str(ork_path),
                            "simulation_index": sim_idx,
                            "simulation_name": sim_name,
                            "motor_configuration": motor_cfg,
                            "apogee_m": round(apogee_m, 2) if not math.isnan(apogee_m) else "",
                            "apogee_ft": round(apogee_m * M_TO_FT, 1) if not math.isnan(apogee_m) else "",
                            "max_velocity_ms": round(max_v, 2) if not math.isnan(max_v) else "",
                            "max_velocity_mph": round(max_v * MS_TO_MPH, 1) if not math.isnan(max_v) else "",
                            "max_acceleration_ms2": round(max_a, 2) if not math.isnan(max_a) else "",
                            "max_acceleration_g": round(max_a * MS2_TO_G, 2) if not math.isnan(max_a) else "",
                            "time_to_apogee_s": round(t_apogee, 2) if t_apogee is not None else "",
                            "flight_time_s": round(flight_time, 2) if not math.isnan(flight_time) else "",
                            "status": "OK",
                            "notes": notes,
                        })
                        f.flush()
                        processed_sims += 1
                        print(f"    sim {sim_idx} ({sim_name}): apogee={apogee_m:.1f} m, max_v={max_v:.1f} m/s")

                    except Exception as e:
                        failed_files += 1
                        if verbose:
                            traceback.print_exc()
                        print(f"    sim {sim_idx} FAILED: {e}")
                        writer.writerow({
                            "source_file": str(ork_path),
                            "simulation_index": sim_idx,
                            "simulation_name": "",
                            "motor_configuration": "",
                            "apogee_m": "", "apogee_ft": "",
                            "max_velocity_ms": "", "max_velocity_mph": "",
                            "max_acceleration_ms2": "", "max_acceleration_g": "",
                            "time_to_apogee_s": "", "flight_time_s": "",
                            "status": "SIM_ERROR",
                            "notes": str(e).replace("\n", " ")[:300],
                        })
                        f.flush()

    elapsed = time.time() - start
    print(
        f"\nDone in {elapsed:.1f}s. "
        f"{processed_sims} simulation(s) processed, {failed_files} error(s). "
        f"Results written to {output_csv}"
    )


def main():
    parser = argparse.ArgumentParser(description="Batch-run OpenRocket simulations on a folder of .ork files.")
    parser.add_argument("--input-dir", required=True, help="Folder containing .ork files")
    parser.add_argument("--output-csv", default="simulation_results.csv", help="Path to write the summary CSV")
    parser.add_argument("--recursive", action="store_true", help="Also search subfolders for .ork files")
    parser.add_argument("--jar", default=None, help="Path to OpenRocket.jar (optional if orlab can auto-locate it)")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N files (useful for testing)")
    parser.add_argument("--probe", action="store_true", help="Diagnostic mode: inspect one file's objects instead of running the full batch")
    parser.add_argument("--verbose", action="store_true", help="Print full tracebacks on simulation errors")
    args = parser.parse_args()

    input_dir = Path(args.input_dir).expanduser().resolve()
    if not input_dir.is_dir():
        print(f"ERROR: {input_dir} is not a directory.", file=sys.stderr)
        sys.exit(1)

    if args.probe:
        ork_files = _find_ork_files(input_dir, args.recursive)
        if not ork_files:
            print("No .ork files found to probe.")
            sys.exit(1)
        probe_one_file(ork_files[0], args.jar)
        return

    run_batch(
        input_dir=input_dir,
        output_csv=Path(args.output_csv).expanduser().resolve(),
        recursive=args.recursive,
        jar_path=args.jar,
        limit=args.limit,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()

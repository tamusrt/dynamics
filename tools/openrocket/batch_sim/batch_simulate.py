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
    so you can quickly patch the two or three accessor
    functions below (_get_motor_config_name, _get_apogee_time) if your
    installed version differs.

USAGE
    python batch_simulate.py --input-dir "C:\path\to\ork_files" --output-csv results.csv
    python batch_simulate.py --input-dir ./ork_files --output-csv results.csv --recursive
    python batch_simulate.py --input-dir ./ork_files --probe --limit 1
    python batch_simulate.py --input-dir ./ork_files --output-csv results.csv --workers 8

PARALLELISM
    Files are simulated in parallel across worker processes (--workers).
    JPype allows only one JVM per process, so this uses processes rather
    than threads; each worker boots its own JVM once and then handles many
    files. The CSV is still written in input-file order.
"""

import argparse
import csv
import math
import multiprocessing
import os
import signal
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
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


FIELDNAMES = [
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
    "min_stability_cal",
    "max_stability_cal",
    "status",
    "notes",
]


def _error_row(ork_path, sim_idx, status, notes):
    return {
        "source_file": str(ork_path),
        "simulation_index": sim_idx,
        "simulation_name": "",
        "motor_configuration": "",
        "apogee_m": "", "apogee_ft": "",
        "max_velocity_ms": "", "max_velocity_mph": "",
        "max_acceleration_ms2": "", "max_acceleration_g": "",
        "time_to_apogee_s": "", "flight_time_s": "",
        "min_stability_cal": "", "max_stability_cal": "",
        "status": status,
        "notes": notes,
    }


def _round_or_blank(value, ndigits, scale=1.0):
    return round(float(value) * scale, ndigits) if not math.isnan(value) else ""


def simulate_file(helper, FlightDataType, FlightEvent, ork_path: Path, verbose=False):
    """
    Load one .ork file and run every simulation saved in it.
    Returns (rows, log_lines, ok_count, error_count). Never raises.
    Log lines are returned rather than printed so parallel workers don't
    interleave their output.
    """
    rows, log = [], []
    ok_count = err_count = 0

    try:
        doc = helper.load_doc(str(ork_path))
    except Exception as e:
        log.append(f"    FAILED to load: {e}")
        rows.append(_error_row(ork_path, "", "LOAD_ERROR", str(e).replace("\n", " ")[:300]))
        return rows, log, 0, 1

    sim_count = _safe_call(doc, "getSimulationCount", default=0) or 0
    if sim_count == 0:
        log.append("    No simulations saved in this file -- skipping.")
        rows.append(_error_row(
            ork_path, "", "NO_SIMULATIONS",
            "File has no configured simulation -- open it in the GUI once "
            "and set up/select a motor & simulation, then re-export the .ork.",
        ))
        return rows, log, 0, 0

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
                    FlightDataType.TYPE_STABILITY,
                ],
            )

            times = list(data[FlightDataType.TYPE_TIME])
            altitudes = list(data[FlightDataType.TYPE_ALTITUDE])
            velocities = list(data[FlightDataType.TYPE_VELOCITY_Z])
            accels = list(data[FlightDataType.TYPE_ACCELERATION_TOTAL])

            events = helper.get_events(sim)
            t_apogee = _get_apogee_time(events, FlightEvent)

            # OpenRocket's "Stability margin calibers" series. Only
            # ascent samples (launch -> apogee) are used: after
            # recovery deployment OpenRocket reports stability for
            # the separated sections, which is meaningless (e.g.
            # -20 cal). NaN samples (not computed) are dropped too.
            stabilities = [
                s for t, s in zip(times, data[FlightDataType.TYPE_STABILITY])
                if math.isfinite(s) and (t_apogee is None or t <= t_apogee)
            ]

            apogee_m = max(altitudes) if altitudes else float("nan")
            max_v = max(velocities) if velocities else float("nan")
            max_a = max(accels) if accels else float("nan")
            flight_time = times[-1] if times else float("nan")
            min_stab = min(stabilities) if stabilities else float("nan")
            max_stab = max(stabilities) if stabilities else float("nan")

            notes = ""
            if math.isnan(apogee_m):
                notes = "No altitude data returned -- simulation may have failed silently."

            rows.append({
                "source_file": str(ork_path),
                "simulation_index": sim_idx,
                "simulation_name": sim_name,
                "motor_configuration": motor_cfg,
                "apogee_m": _round_or_blank(apogee_m, 2),
                "apogee_ft": _round_or_blank(apogee_m, 1, M_TO_FT),
                "max_velocity_ms": _round_or_blank(max_v, 2),
                "max_velocity_mph": _round_or_blank(max_v, 1, MS_TO_MPH),
                "max_acceleration_ms2": _round_or_blank(max_a, 2),
                "max_acceleration_g": _round_or_blank(max_a, 2, MS2_TO_G),
                "time_to_apogee_s": round(t_apogee, 2) if t_apogee is not None else "",
                "flight_time_s": _round_or_blank(flight_time, 2),
                "min_stability_cal": _round_or_blank(min_stab, 2),
                "max_stability_cal": _round_or_blank(max_stab, 2),
                "status": "OK",
                "notes": notes,
            })
            ok_count += 1
            log.append(
                f"    sim {sim_idx} ({sim_name}): apogee={apogee_m:.1f} m, max_v={max_v:.1f} m/s, "
                f"stability={min_stab:.2f}-{max_stab:.2f} cal"
            )

        except Exception as e:
            err_count += 1
            if verbose:
                log.append(traceback.format_exc().rstrip())
            log.append(f"    sim {sim_idx} FAILED: {e}")
            rows.append(_error_row(ork_path, sim_idx, "SIM_ERROR", str(e).replace("\n", " ")[:300]))

    return rows, log, ok_count, err_count


# --- worker-process side: module-level so Windows' spawn can pickle them ---

_WORKER = {}  # per-process state, filled once by _worker_init


def _worker_init(jar_path):
    """Boots one JVM + OpenRocket per worker process (JPype allows only one
    JVM per process, so parallelism has to be multi-process)."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # the parent handles Ctrl+C
    try:
        orkit, _ = _import_orkit()
        instance = orkit.OpenRocketInstance(jar_path)
        instance.__enter__()  # never exited: the JVM lives as long as the process
        _WORKER.update(
            helper=orkit.Helper(instance),
            FlightDataType=getattr(orkit, "FlightDataType"),
            FlightEvent=getattr(orkit, "FlightEvent"),
        )
    except BaseException:
        _WORKER["init_error"] = traceback.format_exc()


def _worker_simulate_file(ork_path_str, verbose):
    ork_path = Path(ork_path_str)
    if "init_error" in _WORKER:
        log = [f"    Worker failed to start OpenRocket:\n{_WORKER['init_error']}"]
        return [_error_row(ork_path, "", "WORKER_ERROR", "Worker JVM failed to start")], log, 0, 1
    return simulate_file(
        _WORKER["helper"], _WORKER["FlightDataType"], _WORKER["FlightEvent"], ork_path, verbose
    )


def _default_workers():
    # Each worker holds its own JVM (a few hundred MB of RAM), so cap the default.
    return max(1, min(8, (os.cpu_count() or 2) - 1))


def run_batch(input_dir: Path, output_csv: Path, recursive: bool, jar_path, limit=None,
              verbose=False, workers=None):
    orkit, which = _import_orkit()
    print(f"Using wrapper package: {which}")

    ork_files = _find_ork_files(input_dir, recursive)
    if limit:
        ork_files = ork_files[:limit]

    if not ork_files:
        print(f"No .ork files found in {input_dir} (recursive={recursive}).")
        return

    workers = max(1, min(workers or _default_workers(), len(ork_files)))
    # Resolve the jar once here (validates it, and pins an absolute path so
    # every worker uses the same jar).
    jar_path = os.path.abspath(orkit.OpenRocketInstance(jar_path).jar_path)

    print(f"Found {len(ork_files)} .ork file(s). Starting {workers} worker JVM(s) "
          f"(this can take ~10-20s)...")

    start = time.time()
    processed_sims = 0
    failed = 0

    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        f.flush()

        def emit(idx, ork_path, result):
            nonlocal processed_sims, failed
            rows, log, ok_count, err_count = result
            print(f"[{idx + 1}/{len(ork_files)}] {ork_path.name}")
            for line in log:
                print(line)
            writer.writerows(rows)
            f.flush()
            processed_sims += ok_count
            failed += err_count

        if workers == 1:
            _worker_init(jar_path)
            for idx, ork_path in enumerate(ork_files):
                emit(idx, ork_path, _worker_simulate_file(str(ork_path), verbose))
        else:
            # Results are written in input-file order (same CSV as a serial
            # run): finished files are buffered until all earlier ones are in.
            pending = {}
            next_idx = 0
            executor = ProcessPoolExecutor(
                max_workers=workers,
                mp_context=multiprocessing.get_context("spawn"),
                initializer=_worker_init,
                initargs=(jar_path,),
            )
            try:
                futures = {
                    executor.submit(_worker_simulate_file, str(p), verbose): i
                    for i, p in enumerate(ork_files)
                }
                for fut in as_completed(futures):
                    i = futures[fut]
                    try:
                        pending[i] = fut.result()
                    except Exception as e:  # worker process died (JVM crash / OOM)
                        pending[i] = (
                            [_error_row(ork_files[i], "", "WORKER_ERROR", str(e)[:300])],
                            [f"    Worker crashed: {e}"], 0, 1,
                        )
                    while next_idx in pending:
                        emit(next_idx, ork_files[next_idx], pending.pop(next_idx))
                        next_idx += 1
            except KeyboardInterrupt:
                print("\nInterrupted -- stopping workers. Partial results are in the CSV.")
                executor.shutdown(wait=False, cancel_futures=True)
                raise
            executor.shutdown(wait=True)

    elapsed = time.time() - start
    print(
        f"\nDone in {elapsed:.1f}s. "
        f"{processed_sims} simulation(s) processed, {failed} error(s). "
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
    parser.add_argument("--workers", type=int, default=None,
                        help=f"Number of parallel worker processes, each with its own JVM "
                             f"(default: {_default_workers()}; 1 = run serially)")
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
        workers=args.workers,
    )


if __name__ == "__main__":
    main()

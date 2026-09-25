# OpenRocket automation

Everything that drives OpenRocket headlessly (through `orlab`, no GUI), for every design under `aero_modeling/`:

- **CI check, history site and changelog:** `or_ci.py` and `ork_diff.py`, run by `.github/workflows/openrocket-sim.yml` on every push. Config in `aero_modeling/sim_config.json`. Docs: [CI.md](CI.md).
- **Batch trade studies:** `batch_simulate.py` (run every `.ork` in a folder), `openrocket_variant_generator.py` (generate geometry variants) and `analyze_trade_study.py` (summarise the results CSV). Documented below.

Both share one setup: Java 17+, `pip install -r requirements.txt` here (or the repo's root `requirements.txt`), and `python -m orlab fetch 24.12` for the OpenRocket jar.

---

# OpenRocket Batch Simulator

Runs OpenRocket's flight simulation on every `.ork` file in a folder, without opening the OpenRocket GUI or clicking "import"/"simulate" by hand, and writes one summary CSV row per simulation (apogee, max velocity, max acceleration, time to apogee, flight time).

**Scope note:** this only automates OpenRocket. RASAero II has no scripting or command-line interface at all — the only way to drive it programmatically is simulating mouse/keyboard actions on its GUI, which is slow and brittle. Per your answer, this build leaves RASAero II out; ask if you want that tackled separately later (it would be a very different, GUI-automation-based tool).

## How it works

OpenRocket doesn't ship an official CLI or batch mode. The standard community approach — used here — starts OpenRocket's own engine inside a small embedded Java process and drives it from Python via the `orlab` package (an actively maintained descendant of the older `orhelper` project). This script:

1. Starts one JVM and loads the OpenRocket engine once.
2. Loops over every `.ork` file in your folder.
3. For each file, runs **whatever simulation(s) are already saved in that file** (i.e. whatever motor/configuration you last set up for it in the GUI) — it doesn't invent a simulation from scratch.
4. Pulls apogee, max velocity, max acceleration, time-to-apogee, and flight time out of each run.
5. Writes it all to a CSV, one row per simulation, flushing after every row so a crash partway through a big batch doesn't lose what's already done.

## Setup (one-time)

### 1. Install Java

You need a JDK, not just a JRE. OpenRocket's recent versions (23.09, 24.12) need **Java 17 or newer**.

- Download **Eclipse Temurin 17** (or 21): https://adoptium.net/
- Windows: run the installer, make sure "Set JAVA_HOME" and "Add to PATH" are checked.
- Verify: open a new terminal and run `java -version` — it should print 17 or higher.

### 2. Get the OpenRocket engine jar

You need the raw `.jar`, not just the installer/exe (the installer bundles its own private JRE that Python can't reach into).

- Releases page: https://github.com/openrocket/openrocket/releases (grab the `.jar` asset from the latest release, e.g. 24.12)
- Or, once `orlab` is installed (step 3), it can fetch and verify one for you:
  ```
  python -m orlab fetch
  ```
  Note the path it downloads to — you'll pass it to this script with `--jar`.

### 3. Install Python packages

From this folder:

```
pip install -r requirements.txt
```

If `orlab` fails to install on your system (it's a newer, smaller package), open `requirements.txt`, comment out `orlab` and uncomment `orhelper==0.1.3` instead, then re-run. The script auto-detects whichever one is present.

## First run / sanity check

**Don't point this at hundreds of files on the first try.** Run it in probe mode against a single file first, to confirm the Python wrapper's API matches what this script expects on your installed versions:

```
python batch_simulate.py --input-dir "C:\path\to\ork_files" --jar "C:\path\to\OpenRocket.jar" --probe
```

This loads one `.ork` file, runs its first simulation, and prints the live object structure (available methods, timeseries data, event list). If it errors out or the printed data looks wrong, paste the output back to Claude (or share it in the conversation this script came from) — it's a quick fix to adjust the two or three accessor calls in `batch_simulate.py` (`_get_motor_config_name`, `_get_apogee_time`) to match your exact installed version. This is worth doing up front: `orlab`/`orhelper` are small, community-maintained wrappers, and method names have shifted across releases before.

Once probe mode looks sane, test on a small handful of real files:

```
python batch_simulate.py --input-dir "C:\path\to\ork_files" --jar "C:\path\to\OpenRocket.jar" --output-csv test_results.csv --limit 5
```

Check `test_results.csv` against what you'd expect (or against a couple of files you've already simulated by hand in the GUI) before running the full batch.

## Running the full batch

```
python batch_simulate.py --input-dir "C:\path\to\ork_files" --jar "C:\path\to\OpenRocket.jar" --output-csv results.csv --recursive
```

- `--recursive` also searches subfolders for `.ork` files; omit it to only scan the top-level folder.
- `--jar` can be omitted if `orlab` can locate an OpenRocket jar on its own (e.g. after `python -m orlab fetch`); if you get a "jar not found" error, pass `--jar` explicitly.
- `--verbose` prints full error tracebacks for any simulation that fails, instead of just a one-line summary.
- Progress prints to the terminal as it goes, and the CSV is written incrementally, so you can open it in Excel/Sheets while the batch is still running (or safely Ctrl+C and keep the partial results).

## Output columns

| Column | Meaning |
|---|---|
| `source_file` | Path to the `.ork` file |
| `simulation_index` / `simulation_name` | Which saved simulation in that file (a file can contain more than one) |
| `motor_configuration` | Best-effort label for the motor/configuration used |
| `apogee_m` / `apogee_ft` | Max altitude reached |
| `max_velocity_ms` / `max_velocity_mph` | Max velocity during flight |
| `max_acceleration_ms2` / `max_acceleration_g` | Max acceleration during flight |
| `time_to_apogee_s` | Time from launch to apogee |
| `flight_time_s` | Total simulated flight time |
| `status` | `OK`, `LOAD_ERROR`, `NO_SIMULATIONS`, or `SIM_ERROR` |
| `notes` | Error detail or other notes, when relevant |

A row with `status = NO_SIMULATIONS` means that `.ork` file has a rocket design but no simulation was ever set up/saved for it in the GUI — there's nothing for this script to run until you open it once, configure a motor/simulation, and save.

## Troubleshooting

- **`orlab`/`orhelper` import error** — re-check step 3; make sure you activated the same Python environment you installed into.
- **"jar not found" / `JVMNotFoundException` / class-not-found errors** — pass `--jar` with the exact path to the OpenRocket `.jar` file; confirm `java -version` shows 17+.
- **JVM runs out of memory on a very large batch** — set `JAVA_TOOL_OPTIONS=-Xmx4g` (or higher) as an environment variable before running the script to give the embedded JVM more heap.
- **A specific file always fails** — open just that one file in the OpenRocket GUI and confirm it opens cleanly and has a simulation configured; a corrupted or design-only file will show up as `LOAD_ERROR` or `NO_SIMULATIONS` here rather than crashing the whole batch.
- **Numbers look off vs. the GUI** — the API returns SI units (meters, m/s, m/s², seconds) regardless of the GUI's display unit settings; this script converts to `ft`/`mph`/`g` in the extra columns for convenience, but double check `apogee_m` against the GUI's own SI toggle if something looks wrong.

## Known limitations

- This only runs simulations already saved inside each `.ork` file — it does not pick motors or build new simulations for you.
- `orlab` is a relatively young, small project; it's the best currently-maintained option, but validate its output against a few manually-run simulations before trusting a large batch to it.
- RASAero II is intentionally out of scope for this build (see the scope note at the top).

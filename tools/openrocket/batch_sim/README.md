# OpenRocket Batch Simulator

A small toolkit for running OpenRocket trade studies without clicking through the GUI for every design:

| Script | What it does |
|---|---|
| `openrocket_variant_generator.py` | GUI (tkinter, no extra installs). Load a base `.ork`, sweep nose cone trim and transition length, fore diameter, and position, and write one `.ork` per combination |
| `batch_simulate.py` | Runs every simulation saved in every `.ork` in a folder, with no GUI, and writes one summary CSV row per simulation (apogee, max velocity, max acceleration, time to apogee, flight time, stability) |
| `analyze_trade_study.py` | Parses the design traits from each filename, ranks the results, and plots how each trait affects apogee (or another metric) relative to the baseline |

**Scope note:** this only automates OpenRocket. RASAero II has no scripting or command-line interface, so it is out of scope.

## Typical workflow

```
# 1. Generate variants from a base design (opens a GUI)
python openrocket_variant_generator.py

# 2. Simulate them all
python batch_simulate.py --input-dir ork_variants --jar "C:\path\to\OpenRocket-24.12.jar" --output-csv results.csv

# 3. Rank and plot
python analyze_trade_study.py --results-csv results.csv --output-dir trade_study_analysis
```

Your base `.ork` must already have at least one simulation (a motor configuration) saved in it. The variants copy it, and `batch_simulate.py` only runs simulations that already exist in each file.

Setup and details for each step are below.

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

This loads one `.ork` file, runs its first simulation, and prints the live object structure (available methods, timeseries data, event list). If it errors out or the printed data looks wrong, adjust the two or three accessor calls in `batch_simulate.py` (`_get_motor_config_name`, `_get_apogee_time`) to match your exact installed version. This is worth doing up front: `orlab`/`orhelper` are small, community-maintained wrappers, and method names have shifted across releases before.

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

- `--motor PATH` flies the given `.eng`/`.rse` thrust curve in every simulation instead of the motor saved in each `.ork`. Use it when the files fail with `empty MotorInstance`: headless OpenRocket only loads its built-in motors, not the custom thrust curves the GUI finds through your preferences.

- `--workers N` sets how many parallel worker processes to run, each with its own JVM. The default is based on your CPU count. `--workers 1` runs serially.

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
| `min_stability_cal` / `max_stability_cal` | Min / max stability margin (calibers) from OpenRocket's own stability time series, over the ascent only (launch to apogee). Post-apogee values are excluded because after recovery deployment OpenRocket reports stability of the separated sections (nonsense like -20 cal) |
| `first_stability_cal` | First non-NaN stability margin (calibers) of the ascent |
| `max_aoa_deg` | Max angle of attack (degrees), launch to apogee |
| `status` | `OK`, `LOAD_ERROR`, `NO_SIMULATIONS`, or `SIM_ERROR` |
| `notes` | Error detail or other notes, when relevant |

A row with `status = NO_SIMULATIONS` means that `.ork` file has a rocket design but no simulation was ever set up/saved for it in the GUI — there's nothing for this script to run until you open it once, configure a motor/simulation, and save.

## Generating variants (`openrocket_variant_generator.py`)

```
python openrocket_variant_generator.py
```

1. **Base .ork file:** load your design. The tool reads its actual component list (nose cone, body tubes, any existing transition).
2. **Insertion points:** tick one or more junctions between adjacent body components where a new transition should go.
3. **Parameters:** enter comma-separated sweep values for nose cone trim (in; `0` = untrimmed), transition length (in), and transition fore diameter (in). The aft diameter is matched automatically to the next component. Optional checkboxes:
   - also generate trim-only variants (no transition)
   - include an unmodified copy of the base file (the baseline for analysis)
   - preserve transition-side and/or nose-trim internal volume by lengthening body tubes to compensate
4. **Output:** choose a folder (default `./ork_variants`) and click **Generate Batch**.

Files are named `<Base>_<X>Trim_<F>-<A>_<L>Trans_<Section>.ork`, and only the segments that changed are included. For example, `MyRocket_2Trim_4.5-6_10Trans_AV.ork` has a 2" nose trim and a 10" transition from 4.5" to 6" diameter, placed after the `AV` section. See the script's docstring for how mass and volume are handled.

## Analyzing results (`analyze_trade_study.py`)

Requires `pandas` and `matplotlib` (included in `requirements.txt`).

```
python analyze_trade_study.py --results-csv results.csv --output-dir trade_study_analysis
```

| Option | Default | Meaning |
|---|---|---|
| `--metric` | `apogee_ft` | Column to rank and plot (`apogee_m`, `max_velocity_mph`, `max_acceleration_g`, `time_to_apogee_s`, `flight_time_s`, ...) |
| `--agg` | `mean` | How to combine multiple simulations per file (`mean`, `max`, `min`) |
| `--baseline-name` | auto | Filename stem of the unmodified design. Defaults to the most common leading filename segment (e.g. `MyRocket`) |
| `--scale` | `auto` | `delta` plots change vs. baseline, `absolute` plots raw values, `auto` uses delta when a baseline file is found |

Outputs in the output folder:
- `parsed_traits_debug.csv`: what was parsed from each filename. **Check this first.** A non-empty `unparsed_segments` value means that filename didn't match the naming convention above.
- `overall_ranking.csv`, `per_file_ranking_<agg>.csv`, `sorted_by_<trait>.csv`: ranked tables
- `ranking_overall.png`, `trait_<trait>.png`: charts

Parsing depends on the generator's naming convention. If you name files another way, adjust the regexes used by `parse_traits`.

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

# OpenRocket simulation check (CI)

Every push that changes an OpenRocket design under `aero_modeling/` gets simulated automatically, before and after the change, and the result is posted back on the commit. No pull request is needed: the workflow triggers on `push` to any branch.

## What happens on a push

1. GitHub Actions boots an Ubuntu runner with Java 17, installs `orlab`, and downloads OpenRocket 24.12 (cached after the first run).
2. `or_ci.py compare` works out which `.ork` files changed between the previous commit and this one (plus any `.ork` whose motor file changed, or every file if `sim_config.json` changed).
3. For each of those files it runs **every simulation saved in the file**, twice: the previous version of the file (pulled from git) and the new one, in the same JVM with the same pinned random seed. Each simulation uses its own saved launch conditions and flight configuration.
4. A before/after table with the delta for every metric is posted as a **comment on the commit**, shown in the workflow's **job summary**, and saved as an **artifact** (`report.md`, `results.csv`, `results.json`).
5. Limits from `sim_config.json` (for example stability off the rod ≥ 1.5 cal) are checked on the new version and flagged with ❌. The check only turns red if `fail_on_limits` is true in the config or the script is run with `--strict`. Default is informational.

Files under `OLD/`, `PROGRESS REPORT*/` and anything else listed in `ignore` are skipped.

## Motors: the one thing you must configure

An `.ork` only stores a *reference* to its motor (manufacturer, designation, digest). OpenRocket resolves that against its motor database, and headless there is only the commercial thrust-curve database bundled in the jar. A team-made motor (`IGNIS_2027.rse`, the Invictus injector curve) therefore loads as an empty motor and the flight goes to 0 m.

`aero_modeling/sim_config.json` maps each simulation to its motor file:

```json
"files": {
  "IREC_2027/2027_OR.ork": {
    "default_motor": "IREC_2027/Thrust Curves/IGNIS_2027.rse",
    "simulations": {
      "Simulation 3": {"motor": "IREC_2027/Thrust Curves/IGNIS_2027.rse", "limits": {"max_mach": {"max": 2.0}}}
    }
  }
}
```

Per simulation, and per motor mount within it, the motor is resolved in this order, and the report states which rule fired:

1. `simulations[<simulation name>].motors[<mount name>]`, or `.motor` when the simulation has a single mount
2. `default_motors[<mount name>]` / `default_motor` for the file
3. the motor the file resolves on its own (commercial motors need nothing)
4. auto-lookup: a `.rse`/`.eng` whose designation matches the one saved in the `.ork` for that mount, searched in the `.ork`'s folder and subfolders (`IREC_2027/Thrust Curves/`) and then in the project folder above it (`LUMINA/Engine Files/` next to `LUMINA/OpenRocket/`)
5. **unresolved**, flagged with ⚠️ in the report

A hybrid modelled as two motors, an ox-tank curve in one body tube and a combustion-chamber curve in another, is two mounts. `SOL_4_30.ork` is the example in the config:

```json
"SOL_INVICTUS/OpenRocket/SOL_4_30.ork": {
  "default_motors": {"Ox Tank": "SOL_INVICTUS/OpenRocket/OXTank.eng",
                     "Combustion Chamber": "SOL_INVICTUS/OpenRocket/CC.eng"}
}
```

**Several curves for one design.** `motor_variants` runs every simulation in the file once per listed motor, reported as `Seymour_10 [85%]`, `Seymour_10 [95%]`, and so on, each with its own row, limits check and history charts. It can sit at file level or inside one simulation's entry, and a per-mount form (`{"85%": {"Tank": "..."}}`) works for multi-mount designs:

```json
"LUMINA/OpenRocket/Lumina.ork": {
  "motor_variants": {"85%": "LUMINA/Engine Files/85per_Liq.eng",
                     "95%": "LUMINA/Engine Files/95per_Liq.eng"}
}
```

So for a new design flying `IGNIS_2027.rse`: if the motor's designation in the file matches the `.rse` (`2027_Engine` today), nothing needs adding. If not, add the file to the config. Simulation and mount names match case- and whitespace-insensitively. Two simulations that share a flight configuration share a motor, so mapping them to different files will not work as expected. If a base commit predates a motor file, the current file is used for the "before" run and the report says so.

## Running it locally

Same script, same config. From the repo root, with the `.venv` active:

```
# simulate everything configured (or specific files)
python design/openrocket_batch_sim/or_ci.py run --config aero_modeling/sim_config.json
python design/openrocket_batch_sim/or_ci.py run --config aero_modeling/sim_config.json aero_modeling/IREC_2027/2027_OR.ork

# before/after your uncommitted or unpushed changes
python design/openrocket_batch_sim/or_ci.py compare --config aero_modeling/sim_config.json --base origin/main
python design/openrocket_batch_sim/or_ci.py compare --config aero_modeling/sim_config.json --base HEAD~3 --all
```

`compare` includes uncommitted working-tree changes, so you can check a design before committing. Pass `--jar C:\Users\<you>\.cache\orlab-jars\OpenRocket-24.12.jar` if orlab cannot find the jar on its own. Outputs land in `or_ci_results/` (git-ignored).

## Reading the report

**Units.** `"units"` in `sim_config.json` selects how reports read: `metric` (default: m, m/s, kPa) or `imperial` (ft, ft/s, psi). Calibers, Mach and seconds are the same in both. Limits in the config are written in the selected units. The CSV, JSON and history cache always hold SI values, so switching the flag changes only the rendered reports and never invalidates cached results.

**Tracked metrics** (summary table, history charts, and the default limits):

| Metric | Definition |
|---|---|
| Apogee | Highest altitude above the launch site (m or ft) |
| Max Mach | Peak Mach number |
| Max dynamic pressure | Peak ½·ρ·v² (kPa or psi), with ρ from OpenRocket's air pressure and temperature along the flight |
| Stability off rod | Barrowman margin (calibers) at launch-rod departure |
| Min / max stability | Smallest and largest margin between rod departure and apogee, **counting only samples with airspeed ≥ 30 m/s**. OpenRocket's margin diverges as airspeed goes to zero near apogee (it reads −20 cal on the IREC file), which is not a real stability event. orlab's unfiltered values are kept in the CSV as `*_raw`. |

Max velocity, acceleration, rod-exit speed, time to apogee, deployment speed, descent rate, flight time and landing distance are in the collapsible detail tables, the CSV and the JSON, and can be used in `limits`.

- **Δ apogee** is new minus old, with percent. An unchanged design produces identical numbers, so any delta is real.
- **Stability off rod** is OpenRocket's Barrowman margin when leaving the rod. With the file's own wind turbulence it moves by up to half a caliber between runs, and OpenRocket 24.12 does not tie that draw to the random seed. The check therefore runs with `deterministic_wind` on (default): turbulence intensity is set to 0 while the saved average wind, rod, site and atmosphere are kept. Set it to false in the config if you want the saved turbulence, accepting the noise.
- **"new"** in the Δ column means the file did not exist at the base commit.
- **Motor** shows the designation that actually flew and the rule that picked it (`config`, `config-default`, `file`, `auto`, `unresolved`).

## Performance history charts

Below the before/after table, the commit comment and job summary carry a **Performance history** section for each changed design: one Mermaid bar chart per tracked metric showing the change from the previous committed version (green up, red down; bar height is the size of the change), and a table with date, author, commit message and every tracked metric per version. GitHub draws Mermaid charts inline, so nothing is committed back to the repo and nothing is published outside it.

How it's built: `or_ci.py history` walks each design's git history along the first-parent line, following renames (the IREC file's `2027_OR_9_15` → `2027_OR` rename is tracked), simulates every version with the same seed and wind settings as the check, and caches each result by the file's git blob id in `.or_ci_cache/` (restored between workflow runs with `actions/cache`). Only versions never seen before are simulated, so the step costs a few seconds after the first backfill. The last 30 versions are charted; the whole history is in the artifact's `history.csv`.

Locally, the same command draws the chart for your uncommitted working copy as a final "working" bar:

```
python design/openrocket_batch_sim/or_ci.py history --config aero_modeling/sim_config.json --cache .or_ci_cache aero_modeling/IREC_2027/2027_OR.ork
```

Paste `or_ci_results/history/history.md` into any GitHub issue, PR, or a Markdown preview that supports Mermaid to see the charts.

## GitHub Pages site

On every push to `main` the workflow also rebuilds the full history for **every** configured design and publishes it as a static site through GitHub Pages (Actions deployment, so nothing is committed back). The URL is shown on the workflow run under the `deploy-pages` job and in the repo's Settings → Pages. The page is a sidebar tree of designs → simulations (each with its latest value for the chosen metric, coloured by its last change) and one large chart on the right. Tick any number of simulations to overlay them on the same axes, across designs too: the SOL_4_30 wind cases, or Lumina's 85% and 95% curves. Buttons switch the metric, a toggle shows deltas vs the previous version instead of absolute values, hovering a point shows the commit, author, message and change, and clicking it opens the commit on GitHub. A table below lists the latest values for the selection. The selection, metric and delta toggle live in the URL hash, so a view can be linked from Discord or an issue. Everything renders in the config's units.

One-time setup, already done via the API: Settings → Pages → Source = **GitHub Actions**. Pages requires a public repo on GitHub's free plan.

To build the site locally (open `or_ci_results/site/index.html` in a browser):

```
python design/openrocket_batch_sim/or_ci.py history --config aero_modeling/sim_config.json --cache .or_ci_cache --site or_ci_results/site
```

## Manual runs

The workflow can also be started from the Actions tab (**Run workflow**) with a custom base commit or with **all** ticked to simulate every configured design, for example after a motor curve update.

## Limits

- GitHub's free plan gives private repos 2,000 Actions minutes a month. A typical run is 2 to 3 minutes (JVM boot plus about 1.5 s per simulation), so budget is not a concern unless designs are pushed dozens of times a day.
- With `deterministic_wind` off, OpenRocket 24.12's turbulence adds randomness the seed does not control, so numbers differ between runs and even between two loads of the same file.
- Only OpenRocket. RASAero has no scripting interface.

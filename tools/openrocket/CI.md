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
python tools/openrocket/or_ci.py run --config aero_modeling/sim_config.json
python tools/openrocket/or_ci.py run --config aero_modeling/sim_config.json aero_modeling/IREC_2027/2027_OR.ork

# before/after your uncommitted or unpushed changes
python tools/openrocket/or_ci.py compare --config aero_modeling/sim_config.json --base origin/main
python tools/openrocket/or_ci.py compare --config aero_modeling/sim_config.json --base HEAD~3 --all
```

`compare` includes uncommitted working-tree changes, so you can check a design before committing. Pass `--jar C:\Users\<you>\.cache\orlab-jars\OpenRocket-24.12.jar` if orlab cannot find the jar on its own. Outputs land in `or_ci_results/` (git-ignored).

## Reading the report

**Units.** `"units"` in `sim_config.json` selects how reports read: `metric` (default: m, m/s, kPa) or `imperial` (ft, ft/s, psi). Calibers, Mach and seconds are the same in both. Limits in the config are written in the selected units. The CSV, JSON and history cache always hold SI values, so switching the flag changes only the rendered reports and never invalidates cached results.

**Stability form.** `"stability_units"` selects how the three stability margins are reported: `cal` (calibers, the OpenRocket convention, (CP−CG)/reference diameter) or `pct` (percentage of overall rocket length, (CP−CG)/length × 100). Both forms are always computed and stored, so `limits` can name either key (`stability_off_rod_cal` or `stability_off_rod_pct`), and the Pages site has a dropdown to switch. The reference diameter and rocket length used are recorded per simulation in the CSV.

**Tracked metrics** (summary table, history charts, and the default limits):

| Metric | Definition |
|---|---|
| Apogee | Highest altitude above the launch site (m or ft) |
| Max Mach | Peak Mach number |
| Max dynamic pressure | Peak ½·ρ·v² (kPa or psi), with ρ from OpenRocket's air pressure and temperature along the flight |
| Stability off rod | Barrowman margin at launch-rod departure, in calibers or % of length per `stability_units` |
| Min / max stability | Smallest and largest margin between rod departure and apogee, **counting only samples with airspeed ≥ 30 m/s**. OpenRocket's margin diverges as airspeed goes to zero near apogee (it reads −20 cal on the IREC file), which is not a real stability event. orlab's unfiltered values are kept in the CSV as `*_raw`. |

Max velocity, acceleration, rod-exit speed, time to apogee, deployment speed, descent rate, flight time and landing distance are in the collapsible detail tables, the CSV and the JSON, and can be used in `limits`.

- **Δ apogee** is new minus old, with percent. An unchanged design produces identical numbers, so any delta is real.
- **Stability off rod** is OpenRocket's Barrowman margin when leaving the rod. With the file's own wind turbulence it moves by up to half a caliber between runs, and OpenRocket 24.12 does not tie that draw to the random seed. The check therefore runs with `deterministic_wind` on (default): turbulence intensity is set to 0 while the saved average wind, rod, site and atmosphere are kept. Set it to false in the config if you want the saved turbulence, accepting the noise.
- **"new"** in the Δ column means the file did not exist at the base commit.
- **Motor** shows the designation that actually flew and the rule that picked it (`config`, `config-default`, `file`, `auto`, `unresolved`).

## Design changelog

An `.ork` is a zip around XML, and git shows it as an opaque binary. The check unpacks both versions and reports exactly what changed, in words, at the top of the commit comment under **What changed**, and as a **Changelog** tab on the site (newest first per design, with the performance change of each ticked simulation as coloured chips, and every changed field in a collapsible table).

`ork_diff.py` does this with the standard library only, no OpenRocket. Components are matched by the UUID OpenRocket stores for each one, so a rename or a move to another parent is reported as such rather than as a delete and an add. Cosmetic fields, internal ids, stored flight results and differences below display precision are ignored; a motor swap is reported as one change. Lengths read in inches or millimetres and masses in pounds or kilograms per the `units` setting. It also runs on its own:

```
python tools/openrocket/ork_diff.py old.ork new.ork --units imperial --table
```

### Optional: a Copilot-written summary

A 98-field commit becomes about 17 bullet lines; a model can turn those into three sentences. When the repository has a secret named **`COPILOT_GITHUB_TOKEN`**, the workflow installs [GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/automate-with-actions), hands it the structured diff and the simulated before/after numbers, and adds its paragraph as a quote above the bullets. Without the secret those steps are skipped and nothing else changes. (GitHub Models, the keyless route, was retired by GitHub on 2026-07-30, which is why a token is needed.)

- **The token**: a fine-grained personal access token with the **Copilot Requests** permission, created by someone whose account has GitHub Copilot (GitHub Education has offered it free to verified students; check what your plan includes). Narrations draw on that person's Copilot usage allowance, so check the plan's limits. Add it under Settings → Secrets and variables → Actions → New repository secret.
- **What the model sees**: only the diff, the commit line and the performance numbers, never the repository. It runs in an empty scratch folder with the file-write tool as its only permission, no shell, and its output is stripped of headings and code fences and capped in length.
- **What it is for**: reading convenience. The exact diff is always shown underneath and is the record; the prompt forbids guessing at intent or inventing numbers.
- **Cost control**: each summary is cached by the before/after blob pair, so it is written once. A push narrates its own change plus at most three older un-narrated entries of the same designs (`--narrate-backlog`), so a backfill never floods the account.

## Performance history charts

Below the before/after table, the commit comment and job summary carry a **Performance history** section for each changed design: one Mermaid bar chart per tracked metric showing the change from the previous committed version (green up, red down; bar height is the size of the change), and a table with date, author, commit message and every tracked metric per version. GitHub draws Mermaid charts inline, so nothing is committed back to the repo and nothing is published outside it.

How it's built: `or_ci.py history` walks each design's git history along the first-parent line, following renames (the IREC file's `2027_OR_9_15` → `2027_OR` rename is tracked), simulates every version with the same seed and wind settings as the check, and caches each result by the file's git blob id in `.or_ci_cache/` (restored between workflow runs with `actions/cache`). Only versions never seen before are simulated, so the step costs a few seconds after the first backfill. The last 30 versions are charted; the whole history is in the artifact's `history.csv`.

Locally, the same command draws the chart for your uncommitted working copy as a final "working" bar:

```
python tools/openrocket/or_ci.py history --config aero_modeling/sim_config.json --cache .or_ci_cache aero_modeling/IREC_2027/2027_OR.ork
```

Paste `or_ci_results/history/history.md` into any GitHub issue, PR, or a Markdown preview that supports Mermaid to see the charts.

## GitHub Pages site

On every push to `main` the workflow also rebuilds the full history for **every** configured design and publishes it as a static site through GitHub Pages (Actions deployment, so nothing is committed back). The URL is shown on the workflow run under the `deploy-pages` job and in the repo's Settings → Pages. The page is a sidebar tree of designs → simulations (each with its latest value for the chosen metric, coloured by its last change) and one large chart on the right. Tick any number of simulations to overlay them on the same axes, across designs too: the SOL_4_30 wind cases, or Lumina's 85% and 95% curves. Buttons switch the metric, and a three-way **view** switch chooses how it's drawn: *Absolute* and *Δ line* plot each simulation over commit date, while *Δ bars* puts commits on the x-axis with one bar per commit, green for an increase and red for a decrease from the previous version (outlined in each simulation's colour when several are ticked). Hovering shows the commit, author, message and change, and clicking opens the commit on GitHub. A table below lists the latest values for the selection. The selection, metric and delta toggle live in the URL hash, so a view can be linked from Discord or an issue. Everything renders in the config's units.

### Flight plots tab

The site's second tab plots any OpenRocket flight variable against any other for the latest committed version of each ticked simulation: stability versus altitude, drag coefficient versus Mach, CP and CG versus time, and so on. About 56 variables are available per flight (everything OpenRocket records, plus dynamic pressure and stability as a percentage of length), with OpenRocket's own names and units, converted by the units dropdown.

- **Fidelity.** Every sample of the whole flight is kept at OpenRocket's native time step (about 0.05 s; finer around events), with six significant digits. `SERIES_DESCENT_STRIDE` in `or_ci.py` can thin the descent again if the data files ever grow too large; cached series made with another stride are re-simulated automatically.
- **Controls.** One X variable from the dropdown and any number of Y variables, ticked in the panel to the right of the plot (a filter box narrows the list; the list stays open while you tick). The top of the panel lists the plotted ones under *Left axes* and *Right axes*: clicking a variable there moves it to the other side, × removes it. Variables that share a unit share an axis; each further unit gets its own axis, up to three per side (six in all), and a seventh unit is refused until something is removed. Line style distinguishes variables, colour distinguishes simulations. One-click presets, *ascent only*, and *previous version* (the commit before, as a faint dotted line). Dotted vertical lines mark rod exit, burnout, apogee and deployment, labelled along the top (hover for the time); they are independent of the Y axes. The legend groups traces by simulation only when several are ticked, and adds the design name only when the ticked simulations span several designs. A *Clear* button in the panel unticks every Y variable. The whole view lives in the URL as `fx=time&fy=altitude,velocity_total:r` (`:r` = right axis).
- **Export CSV** downloads exactly what is plotted: one row per sample with columns `simulation, version, time (s), <X>, <Y…>` in the display units, every ticked simulation stacked (and the previous version too when shown), cut at apogee when *ascent only* is ticked. Blank cells are values OpenRocket does not define at that instant (stability before liftoff, for example).
- **How it's produced.** `history --site` captures the time series for the two newest versions of each design (cached by blob like everything else) and writes one data file per simulation under `site/flights/`, loaded only when that simulation is ticked. They are plain scripts rather than `fetch`ed JSON so the page also works opened from disk.
- **What it can't do.** It plots what CI simulated. To see a different wind, rail or motor, change the simulation in the `.ork` (or add one) and push.

One-time setup, already done via the API: Settings → Pages → Source = **GitHub Actions**. Pages requires a public repo on GitHub's free plan.

To build the site locally (open `or_ci_results/site/index.html` in a browser):

```
python tools/openrocket/or_ci.py history --config aero_modeling/sim_config.json --cache .or_ci_cache --site or_ci_results/site
```

## Tests

The site page has a regression test that needs no OpenRocket, Java or network:

```
python tools/openrocket/tests/test_site.py
```

`test_site.py` builds a site from a synthetic history (two designs, a failed version, undefined samples, a commit message with a script tag) through `or_ci.write_site`, then `site_page_test.js` runs the page's own JavaScript in a stub DOM. Every call the page makes to Plotly is checked structurally (x, y and customdata of equal length, axes that exist, no unfilled placeholders), and the tests assert values against the flight data files: unit conversion, axis assignment and the three-per-side limit, event lines, legend grouping, the stability toggle, the URL hash, presets and the CSV export, plus the History views and the Changelog. With `npm install` run in `tools/openrocket/` (jsdom and the same Plotly build the page loads), the six-axis flight layout and the history charts are also rendered through the real Plotly.

The workflow runs it as the `page-test` job on every push that touches `or_ci.py` or the tests, and the Pages deployment waits for it, so a broken page never reaches the site (the simulation report is not held up). Run it locally before pushing any change to the page template; a change in behaviour that is intended needs its assertion updated in the same commit.

## Manual runs

The workflow can also be started from the Actions tab (**Run workflow**) with a custom base commit or with **all** ticked to simulate every configured design, for example after a motor curve update.

## Limits

- GitHub's free plan gives private repos 2,000 Actions minutes a month. A typical run is 2 to 3 minutes (JVM boot plus about 1.5 s per simulation), so budget is not a concern unless designs are pushed dozens of times a day.
- With `deterministic_wind` off, OpenRocket 24.12's turbulence adds randomness the seed does not control, so numbers differ between runs and even between two loads of the same file.
- Only OpenRocket. RASAero has no scripting interface.

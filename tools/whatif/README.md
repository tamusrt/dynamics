# Predictions (SRT14 flight predictions)

Predictions is part of the same GitHub Pages site as the OpenRocket History. It has two tabs on the top bar:

- **Predictions** shows how the committed rocket flies, up and back down, and puts it next to OpenRocket.
- **Vision** is the 3D flight. It opens inside the page, so the other tabs (History, Flight plots, Changelog) still work.

Both tabs are always on the top bar. If the page has not been built yet, the tabs say so instead of going missing.
The same pages are at `https://tamusrt.github.io/dynamics/predictions/` and `.../predictions/viewer/`.
To fly a different rocket, change it in OpenRocket and commit.

## Names

- **Jarvis** is our flight simulation (the `flight_sim` repo).
  - *Jarvis* is its quick model. It runs in the browser.
  - *Jarvis, detailed* is the full simulation. It is the one Vision plays back.
- **Vision** is the 3D view of the detailed flight.
- **OpenRocket** is the History tab's own run of the same rocket.

## What the page shows

- A chart of altitude, speed, Mach, acceleration, stability or drag. Tick the conditions on the left, and switch the
  three lines (Jarvis, Jarvis detailed, OpenRocket) on or off. The *Jarvis minus OpenRocket* view shows the gap.
- **Apogee in every kind of weather.** Jarvis flies the rocket as it is now through every set of conditions, up to the
  highest point only. There is no way down and no 3D, so it is quick. The first condition you tick is the one the others
  are compared with.
- Tables for the climb, the way down, and more numbers, with a grey row for the gap between Jarvis and OpenRocket.

## When it rebuilds

It rebuilds by itself when you push a changed `.ork`, `.eng`, `.rse`, CSV or `.CDX1` under `aero_modeling/`, or a change
to `aero_modeling/whatif_config.json` or `tools/whatif/`. The work is done by `.github/workflows/openrocket-sim.yml`.
On `main` it:

1. builds the History site into `site/`,
2. checks out the `flight_sim` repo and installs it,
3. runs `python tools/whatif/build_site.py --config aero_modeling/whatif_config.json --site site`
   (the Predictions page, Vision, and the dashed Jarvis lines for the History tab, see below),
4. opens the built site in a real browser and clicks through it (see "Checks on every push"),
5. if Predictions or Vision is broken, puts the last good ones back and adds the Error bar (see "When a push breaks
   something"),
6. uploads `site/` and deploys it.

If the Predictions build fails, the History site still deploys. Look for "Build the predictions page" in the run log.

A change to `flight_sim` (the model itself) does not start this by itself. After pushing it, run the workflow by hand:
Actions, "OpenRocket simulation check", Run workflow, branch `main`.

## Jarvis on the History tab (by commit)

The History tab draws OpenRocket's numbers for every committed version of a design. The Action also flies each of those
versions with Jarvis and draws it as a **dashed line** in the same colour, next to the solid OpenRocket line. Hover a
dashed point to see both numbers and the gap. The **Jarvis** button above the chart turns the dashed lines off and on
(`jarvis=0` in the address). They show in the Absolute and Δ line views, not in the bars.

Each point flies that version's own `.ork`, read back from git, with the RASAero table and `.CDX1` as they were at that
commit (the nearest ones when there were none yet) and today's motor. So the line shows the design changing and nothing
else. The numbers go to `site/jarvis_by_commit.json`, which the History page loads when it opens. A version that cannot
be read is left out (the note under the chart says how many). If Jarvis fails here, History is not affected: it just has no
dashed lines. The code is `flight_sim.whatif.commits`, also usable on its own:
`python -m flight_sim.whatif.commits --ork ... --aero ... --motor ... --history-site site --out by_commit.json`.

The dashed lines only appear on a site that was built on `main`, because they need the full git history and the History
site.

## Where the numbers come from

| Input | File (SRT14) | Used for |
| --- | --- | --- |
| OpenRocket design | `IREC_2027/OR/2027_OR.ork` | size, mass, launch conditions |
| RASAero table | `IREC_2027/RASA/ignis_2027_aero.csv` | drag, lift and centre of pressure |
| RASAero rocket | `IREC_2027/RASA/rasaero.CDX1` | the rocket the CSV was made for |
| Motor | the file History flies (`default_motor` in `sim_config.json`), else the newest `.eng` or `.rse` in `IREC_2027/Thrust Curves/` | thrust, propellant mass, total mass |

**OpenRocket's lines** are the History tab's numbers: the newest good flight of each simulation, read from the History
site that was just built. So Predictions and History never disagree. On a laptop with no History site, the results saved
inside the `.ork` are used, and the page says which it used.

**Same motor.** Jarvis flies the motor the History tab flies: the `default_motor` for the design in `sim_config.json`.
The page says so ("same as the History tab"). If that file is not set or is missing, Jarvis uses the newest file in
`motor_dir`, and the page warns you when the two are different files. To make Jarvis fly one fixed file instead, put
`"motor": "<file>"` in the rocket's entry in `whatif_config.json`. That choice wins over everything else.

**Stability** is counted the way History counts it: from the moment the rocket leaves the rail until apogee, only while it
is faster than 30 m/s.

**Geometry.** Every change in the `.ork` is picked up. The RASAero table only changes when you remake the CSV (Update CSV,
below). Until then the page corrects the table for the difference, lists the differences, and warns you.

**Way down.** After apogee the rocket falls under a stand-in recovery: the original Sol Invictus parachute set-up.
OpenRocket is left out of that part because its design file has no parachutes.

### Limits

- The corrections are first-order. Trust the trends, not the last percent.
- RASAero drag below Mach 1 is lower than OpenRocket's, so Jarvis reaches a higher apogee. Compare designs with each
  other, not with OpenRocket.
- The wind is constant, and the aero table is power-off.

## Update CSV (bottom of the Predictions tab)

Use this when the rocket has changed. A small helper program on the Windows computer that has RASAero presses the keys
for you. The page shows each command in its own box with a Copy button.

**First time only.** In a terminal run `pip install pyautogui`, then install flight_sim into the same Python:
`pip install -e C:\Users\yosep\flight_sim`.

1. **Start the helper.** Open the dynamics folder, type `cmd` in the address bar, and run
   `python tools\whatif\local_helper.py`. Leave the window open. The page shows when it can see the helper.
2. **Save the rocket in RASAero.** Open the rocket in RASAero II and save it over `rasaero.CDX1`.
   The **Open the RASAero folder** and **Open the dynamics folder** buttons open the right folders for you.
3. **Make the 31 RASAero files.** Click inside RASAero so it is the last window you used, tick the box, and press the
   button. Then do not touch the keyboard or mouse. A mouse in a screen corner stops it, and so does **Cancel**.
4. **Update the CSV and rebuild.** This turns the 31 files into the CSV, rebuilds the page and Vision, and runs the
   detailed flight again. If the rebuild fails, the old CSV is put back.

To publish, run the commands the page shows (`git add` the CSV and the `.CDX1`, commit, push). The Action rebuilds the live page.

The helper only listens on 127.0.0.1. It only accepts requests from `https://tamusrt.github.io` (`pages_origin` in the
config) and from itself. The open-folder buttons only open two places: the folder of the `.CDX1` and the dynamics
folder. Chrome may ask to allow the page to reach devices on your network. Click Allow. If the live page stays blocked,
use `http://127.0.0.1:8765/predictions/` instead, which the helper serves.

## Setting up the Action (one time)

- **Pages source** must be "GitHub Actions" (Settings, Pages). It already is if the History site is live.
- **flight_sim.** The workflow checks out `tamusrt/flight_sim` at `main`. Until the code is merged there, set the repository
  variable `FLIGHT_SIM_REF` (Settings, Secrets and variables, Actions, Variables) to the branch that has it, for example
  `senai_rebased_local`. Set `FLIGHT_SIM_REPO` to use a fork. If flight_sim is private, add a secret `FLIGHT_SIM_TOKEN`:
  a fine-grained personal access token with read-only "Contents" access to it.
- A wrong ref or token shows up as a skipped step, not a failed deploy.
- The motor choice reads git history, so the checkout must be full (`fetch-depth: 0`, already set in the workflow).

## Adding another rocket

Add an entry under `rockets` in `aero_modeling/whatif_config.json`. It needs `ork`, `aero`, and `motor_dir` or `motor`.
`rasaero`, `sim` and `alpha_dir` are optional (`alpha_dir` is needed for Update CSV). Paths are relative to that file.
`openrocket_config` names the History config to read the motor from (default `sim_config.json`).
The `default` rocket is at `/predictions/`. The others are at `/predictions/<key in lowercase>/`.

## Checks on every push

Every push to any branch that touches `tools/whatif/`, `tools/openrocket/`, `aero_modeling/` or the workflow runs
three kinds of check. A failed one is red on the commit.

- **Page tests** (`tools/openrocket/tests/test_site.py`) run the History page's code against made-up data.
- **Build tests** (`tools/whatif/tests/test_build_site.py`, `test_local_helper.py`) check the Predictions build and the
  Update CSV helper.
- **Browser test** (`tools/whatif/tests/browser_test.py`) opens the site in a real Chromium and clicks through it:
  the History chart and its five tabs; the dashed Jarvis lines and the Jarvis button; the Predictions tab (its table,
  chart and weather table, and that Jarvis's apogee is not wildly far from OpenRocket's); units chosen in the page
  reaching Predictions; the Vision 3D scene being drawn and not blank; the Error bar showing when something broke; and
  no script errors or missing files anywhere.
  On every push it runs on a made-up History site. On `main` it runs on the real site before it is published, and
  if the Predictions build worked, the Predictions page and Vision must be there. The pictures it takes of each tab are
  kept in the run's artifacts (`browser-test-pictures-...`), so you can see what it saw.

## When a push breaks something

The site never shows a broken Predictions page or a broken Vision. If a push breaks either one, for any reason (the
build fails, `flight_sim` cannot be checked out, a page is missing or gives a 404, a script stops, the 3D scene is
blank), the workflow:

1. downloads the Predictions page and Vision that are live now and puts them in the new site in place of the broken ones,
   so people still see the last good overview and 3D model;
2. shows a large red **Error** bar at the top of every tab of the History page, saying what broke, which push it was, and
   that it is the last good version being shown (with the date), with a link to the run;
3. adds the same bar to the top of the old page when it is opened on its own (inside the History page it hides itself,
   so there is only one bar);
4. writes the problem in red on the run's summary, as an error on the run, and on the commit's comment;
5. still publishes the site, so History keeps updating.

The bar goes away by itself on the first push that builds cleanly. If there is no earlier page to put back (the very
first publish), the new page is kept as it is, or the tab says it is missing, and the bar says so.

Two things are not covered, on purpose. A problem in the History tab itself fails the run and nothing is published, so
the site that is live now stays exactly as it is. The dashed Jarvis lines going missing only puts a line in the Error bar.

The work is done by `tools/whatif/site_status.py` (tested by `tests/test_site_status.py`). The site that is live is found
at `https://<owner>.github.io/<repo>`. If yours is somewhere else, set the repository variable `LIVE_SITE_URL`.

## Build and test on your computer

```
pip install -e <path to flight_sim>
python tools/whatif/build_site.py --config aero_modeling/whatif_config.json --site site
python tools/whatif/tests/test_build_site.py
python tools/whatif/tests/test_local_helper.py
python tools/whatif/tests/test_site_status.py
pip install playwright
python -m playwright install chromium
python tools/whatif/tests/browser_test.py --synthetic
python tools/whatif/tests/browser_test.py --site site --require-predictions --shots pictures
```

Open `site/predictions/index.html`. It loads Plotly from cdn.plot.ly. `--dry-run` prints the commands and the motor chosen.
To read OpenRocket's numbers from a History site, build the History site into `site/` first (the Action does).
For a single rocket by hand, `flight_sim.whatif.build` takes `--history-site <folder>` and `--history-motor <file name>`.

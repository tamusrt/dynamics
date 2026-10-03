# Predictions page (SRT14 flight predictions)

This is a page on the same GitHub Pages site as the OpenRocket history:

- `https://tamusrt.github.io/dynamics/predictions/` shows the committed rocket's flight, up and back down.
- `https://tamusrt.github.io/dynamics/predictions/viewer/` is the 3D flight (the flight_sim 6-DOF viewer).

The History page has a **Predictions** tab that links to it. It appears once the page has been deployed.
The page has no controls that change the rocket. To see a different design, change it in OpenRocket and commit.

## When it rebuilds

It rebuilds by itself when you push a changed `.ork`, `.eng`, `.rse`, CSV or `.CDX1` under `aero_modeling/`, or a change
to `aero_modeling/whatif_config.json` or `tools/whatif/`. The work is done by `.github/workflows/openrocket-sim.yml`.
On `main` it:

1. checks out the `flight_sim` repo (it holds the `.ork` reader, the page and the 6-DOF sim) and installs it,
2. runs `python tools/whatif/build_site.py --config aero_modeling/whatif_config.json --site site`,
3. uploads `site/` and deploys it, with the existing steps.

If the predictions build fails, the History site still deploys. Look for "Build the predictions page" in the run log.

## Where the numbers come from

| Input | File (SRT14) | Used for |
| --- | --- | --- |
| OpenRocket design | `IREC_2027/OR/2027_OR.ork` | size, mass, launch conditions, OpenRocket's own saved results |
| RASAero table | `IREC_2027/RASA/ignis_2027_aero.csv` | drag, lift and centre of pressure against Mach and angle of attack |
| RASAero rocket | `IREC_2027/RASA/rasaero.CDX1` | the rocket the CSV was made for, so the page can correct for a newer `.ork` |
| Motor | newest `.eng` or `.rse` in `IREC_2027/Thrust Curves/` | thrust curve, propellant mass, total mass |

**Motor.** The page uses the newest motor file in `motor_dir` (the one committed last) and says which one it used.
An `.eng` and an `.rse` with the same name count as one file, and the `.eng` is read. A file you edited and have not
committed counts as newest when you build on your computer. Only files directly in that folder are checked, not subfolders.
If the motor's masses differ from the motor in the saved OpenRocket runs, the page warns you. The OpenRocket column is
then for the other motor. To use one fixed file, put `"motor": "<file>"` in the config instead of `motor_dir`.

**Geometry.** Every change in the `.ork` is picked up. The RASAero table only changes when you remake the CSV (Update CSV,
below). Until then the page corrects the table for the difference, lists the differences, and warns you.

## What the page does

A flat in-browser model (up, downrange, pitch) flies the `.ork` on the RASAero table. Where the `.ork` differs from the
rocket RASAero was run for, it corrects the table: lift and centre of pressure from Barrowman's equations, and the drag of
the fins, body, nose and boat-tail from simple formulas. Mass, CG and inertia come from the parts in the `.ork`.
The 6-DOF column is the full sim on the table as it is, so it describes the RASAero rocket. The OpenRocket column is what
the `.ork` stores. The gaps between the columns show how far to trust the model.

**Descent.** After apogee the rocket falls as a point mass in the same wind. The `.ork` has no parachutes, so the page uses
the original Sol Invictus recovery as a stand-in: a 120 in reefed main, out 3 s after apogee, reef cut at 2,000 ft, on a single
separation. It is the same recovery in the model, the 6-DOF sim and the 3D flight. The numbers are `RECOVERY` in
`flight_sim/__main__.py`, with the body drag set from SRT14's own size (`original_descent` in `flight_sim/ork_profile.py`).
OpenRocket's landing figures are left out, because its saved runs have no parachutes either.

### Limits

- The corrections are first-order. Trust trends, not the last percent. Big differences are flagged: remake the CSV.
- RASAero drag below Mach 1 is 20 to 40 percent lower than OpenRocket's, so the apogee sits above OpenRocket's
  (about 7 percent for SRT14). Compare designs with each other, not with OpenRocket.
- The descent is a stand-in until SRT14 has its own recovery. The wind is constant. The aero table is power-off.

## Update CSV (bottom of the page)

The page cannot press keys in RASAero, so a small helper runs on your computer, and the page's buttons talk to it.
It only works on a Windows computer that has RASAero II.

**First time only.** Run `pip install pyautogui`. Then install flight_sim into the same Python, from the branch that has
`flight_sim.whatif`: `pip install -e C:\Users\yosep\flight_sim`.

**Every time:**

1. Open RASAero II with the current rocket. Save it over `aero_modeling/IREC_2027/RASA/rasaero.CDX1`.
2. Open a terminal in the dynamics folder and run `python tools\whatif\local_helper.py`. Leave the window open.
   Use the Python that has flight_sim and pyautogui, or pass `--python`.
3. Click inside RASAero, so it is the window you used last. Then open the page (the live one, or
   `http://127.0.0.1:8765/predictions/`, which the helper serves) and open **Update CSV** at the bottom.
4. Tick the box and press **Step 1: Create the 31 RASAero files**. After 5 seconds the helper switches to RASAero and
   checks it is in front (its title must contain the `rasaero_window` text in the config). Then it runs Run Test for alpha 0
   to 30. Do not touch the keyboard or mouse. A mouse in a screen corner stops it, and so does **Cancel**.
5. Press **Step 2: Update the CSV and rebuild this page**. It turns the 31 files into the CSV, rebuilds the page and the 3D
   flight, reruns the 6-DOF sim and shows the new page. If the rebuild fails, the old CSV is put back.
6. To publish, run the commands the page shows: `git add` the CSV and the `.CDX1`, commit, push. The Action then rebuilds the live page.

The helper only listens on 127.0.0.1. It only accepts requests from `https://tamusrt.github.io` (`pages_origin` in the
config) and from itself. Chrome may ask to allow the page to reach devices on your network. Click Allow. If the live page
stays blocked, use `http://127.0.0.1:8765/predictions/` instead.

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
The `default` rocket is at `/predictions/`. The others are at `/predictions/<key in lowercase>/`.

## Build and test on your computer

```
pip install -e <path to flight_sim>
python tools/whatif/build_site.py --config aero_modeling/whatif_config.json --site site
python tools/whatif/tests/test_build_site.py
python tools/whatif/tests/test_local_helper.py
```

Open `site/predictions/index.html`. It loads Plotly from cdn.plot.ly. `--dry-run` prints the commands and the motor chosen.

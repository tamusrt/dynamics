# Predictions page (SRT14 flight predictions)

A static page on the same GitHub Pages site as the OpenRocket history, in the same style:

- `https://tamusrt.github.io/dynamics/predictions/` , the committed design flown on the team's RASAero table.
- `https://tamusrt.github.io/dynamics/predictions/viewer/` , the 3D flight (the flight_sim 6-DOF viewer).

The History page has a **Predictions** tab that links to it (shown once the page has been deployed).
There are no controls that change the rocket: to see a different design, change it in OpenRocket and commit.

## What rebuilds it

`.github/workflows/openrocket-sim.yml` runs on every push that touches an `.ork`, `.eng`, `.rse`, a CSV or `.CDX1` under
`aero_modeling/`, `aero_modeling/whatif_config.json` or `tools/whatif/`. On `main` it then:

1. checks out the `flight_sim` repo (it holds the `.ork` reader, the page template and the 6-DOF sim) and `pip install`s it,
2. runs `python tools/whatif/build_site.py --config aero_modeling/whatif_config.json --site site`,
3. the existing steps upload `site/` and deploy it.

If the predictions build fails the History site still deploys; look for "Build the predictions page" in the run log.

## Where each input comes from

| Input | File (SRT14) | Used for |
| --- | --- | --- |
| OpenRocket design | `IREC_2027/OR/2027_OR.ork` | geometry, masses, launch conditions, OpenRocket's own saved results |
| RASAero table | `IREC_2027/RASA/ignis_2027_aero.csv` | Cd, CN-alpha and CP against Mach and angle of attack |
| RASAero geometry | `IREC_2027/RASA/rasaero.CDX1` | the geometry the CSV was made for, so the page can correct for a newer `.ork` |
| Motor | newest `.eng` or `.rse` in `IREC_2027/Thrust Curves/` | thrust curve, propellant and total mass |

**Motor.** `motor_dir` in the config is the folder the propulsion model writes to. The most recently committed motor in it
(an `.eng` and `.rse` of the same name count as one; the `.eng` is read) is used automatically, with its own propellant and
total mass, and the page says which file it used. A file that is edited or new on your computer and not yet committed counts as
newest when you build locally. Only files directly in that folder are looked at, not subfolders. The page warns when the
motor file's masses differ from the motor the saved OpenRocket simulations flew, because then the OpenRocket column is for the
other motor until the simulations are rerun and the `.ork` saved. Use `"motor": "<file>"` instead of `motor_dir` to pin one file.

**Geometry.** Every geometry change in the `.ork` is picked up automatically. The RASAero table only changes when the CSV is
remade (below); until then the page corrects the table for the difference, shows the differences in a table, and says so.

## What the page does

A planar in-browser model (up, downrange, pitch; 10 ms steps) flies the `.ork` on the RASAero table. Where the `.ork` differs
from the geometry RASAero was run for, it corrects the table: Barrowman normal force and centre of pressure, and drag of the fins,
body friction, nose and boat-tail from simple formulas, as a difference on RASAero's value. Mass, CG and inertia come from the
component masses in the `.ork`. The 6-DOF column is the full sim on the table as it is (so it describes the RASAero geometry),
and the OpenRocket column is what the `.ork` stores; the gaps between the columns show how far to trust the model.

### Limits

- The geometry corrections are first-order; trust trends, not the last percent. Large differences are flagged: remake the CSV.
- RASAero drag below Mach 1 is 20 to 40 percent lower than OpenRocket's, so predictions sit above OpenRocket's apogee
  (about 7 percent for SRT14). Compare designs against each other, not against OpenRocket.
- Recovery is not modelled (flight stops at apogee). Wind is constant. The aero table is power-off.

## Update CSV (bottom of the page)

The page cannot press keys in RASAero, so a small helper runs on your computer and the page's buttons talk to it.

**One-time:** `pip install pyautogui`, and install the `flight_sim` folder (from the branch that has `flight_sim.whatif`) into the
same Python: `pip install -e C:\Users\yosep\flight_sim`.

**Each time:**

1. Open RASAero II with the current rocket and save it over `aero_modeling/IREC_2027/RASA/rasaero.CDX1`.
2. Start the helper in the dynamics folder: `python tools\whatif\local_helper.py` (use the Python that has flight_sim and pyautogui,
   or pass `--python`). Leave the window open.
3. Click RASAero (so it is the window you touched last), then open the page (the live one, or `http://127.0.0.1:8765/predictions/`,
   which the helper serves) and open **Update CSV** at the bottom.
4. Tick the box and press **Create the 31 RASAero files**. After 5 seconds the helper presses Alt+Tab, checks the window in front
   is RASAero (its title must contain the `rasaero_window` text in the config), then runs RASAero's Run Test for alpha 0 to 30.
   Do not touch the keyboard or mouse; a mouse in a screen corner stops it, and so does **Cancel**.
5. Press **Update CSV, rebuild model and show the new page**. It turns the 31 files into the CSV, rebuilds the page and the 3D
   flight, reruns the 6-DOF sim, and shows the result (served by the helper). If the rebuild fails the old CSV is put back.
6. To publish, run the commands the page lists (`git add` the CSV and the `.CDX1`, commit, push). The Action rebuilds the live page.

The helper listens on 127.0.0.1 only and accepts requests only from `https://tamusrt.github.io` (`pages_origin` in the config)
and itself. Chrome may ask to allow the page to reach devices on your network: allow it. If a browser blocks the live page from
reaching the helper, use `http://127.0.0.1:8765/predictions/` instead.

## One-time setup of the Action

- **Pages source** must be "GitHub Actions" (Settings -> Pages). It already is if the History site is live.
- **flight_sim access.** The workflow checks out `tamusrt/flight_sim` at `main`. Until the code is merged there, set the
  repository variable `FLIGHT_SIM_REF` (Settings -> Secrets and variables -> Actions -> Variables) to the branch that has it,
  e.g. `senai_rebased_local`. `FLIGHT_SIM_REPO` points at a fork. If the flight_sim repo is private, add a secret
  `FLIGHT_SIM_TOKEN`: a fine-grained personal access token with read-only "Contents" access to it.
- The checkout step is `continue-on-error`: a wrong ref or token shows up as a skipped step, not a failed deploy.
- The motor choice reads git history, so the checkout must be full (`fetch-depth: 0`, already set in the workflow).

## Adding another rocket

Add an entry under `rockets` in `aero_modeling/whatif_config.json` with `ork`, `aero`, and `motor_dir` or `motor` (required);
`rasaero`, `sim` and `alpha_dir` (optional; `alpha_dir` is needed for Update CSV), paths relative to that file. `default` is at
`/predictions/`; the others at `/predictions/<key lowercased>/`.

## Build and test locally

```
pip install -e <path to flight_sim>
python tools/whatif/build_site.py --config aero_modeling/whatif_config.json --site site
python tools/whatif/tests/test_build_site.py
python tools/whatif/tests/test_local_helper.py
```

Open `site/predictions/index.html` (it loads Plotly from cdn.plot.ly). `--dry-run` prints the commands and the motor chosen.

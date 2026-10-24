# Stability-based fin trade

`fin_sweep.py` runs a fin-geometry sweep from start to finish:

1. It generates the variants with `../batch-generation/ork_batch.py`.
2. It simulates every variant with `../batch_sim/batch_simulate.py`.
3. It converts stability from calibers to % of rocket length and drops rockets that fall outside your stability limits.

All length inputs are in inches. Apogee is reported in feet.

## Requirements

* The same setup `batch_sim` needs: Java 17+, `orlab` (or `orhelper`), and `../batch_sim/OpenRocket-24.12.jar`. See `../batch_sim/README.md`.
* The reference `.ork` must have its simulations saved in it, and one of them must have **no wind** (see below).
* Pass the trade's thrust curve with `--motor`. Headless OpenRocket only loads its built-in motors, not the custom thrust curves the GUI finds, so without `--motor` a custom motor fails every simulation with `empty MotorInstance`.
* Keep this folder inside `ORK Tools`. The script finds the other two tools by their path relative to this folder.

## Usage

```powershell
python fin_sweep.py --base path\to\REF.ork --motor path\to\motor.eng --height 5.5:8.5:0.5 `
    --maximum-stability 25 --minimum-stability 10 --worst-stability 9
```

| Option | Default | Meaning |
|---|---|---|
| `--base` | required | Reference `.ork` |
| `--height` `--root` `--tip` `--sweep` | – | Fin span, root chord, tip chord and sweep length as `start:stop:step` in inches. At least one is required. When you give several, every combination is generated. Parameters you leave out keep the reference value. Any `ork_batch.py` value spec also works, e.g. `6,7,8`. |
| `--ork-folder` | `fin_sweep_<YYYYmmdd_HHMMSS>` | Folder the variants and results are written to |
| `--maximum-stability` | – | Drop a rocket if its no-wind sim's stability is ever **above** this % |
| `--minimum-stability` | – | Drop a rocket if its no-wind sim's stability is ever **below** this % |
| `--worst-stability` | – | Drop a rocket if **any** of its sims' stability is ever below this % |
| `--motor` | – | Thrust curve (`.eng`/`.rse`) flown in every simulation in place of the motor saved in the `.ork` |
| `--workers` | 8 | Parallel simulation workers (each one runs its own JVM) |
| `--jar` | `../batch_sim/OpenRocket-24.12.jar` | OpenRocket jar |

## How it works

**Stability %.** OpenRocket reports stability in calibers. The script converts it with:

```
stability % = calibers × diameter / length × 100
```

Length and diameter are read once from the reference file. Length is the sum of the nose cone, body tube and transition lengths. Diameter is the largest of their diameters. A fin sweep doesn't change either one. The script prints both values at the end of a run.

**Stability window.** All stability values (first, min and max) and the max AoA come from the ascent only, launch to apogee. After deployment OpenRocket reports meaningless values for the separated sections. "First stability" is the first sample that isn't NaN.

**The no-wind sim** is found from each file's wind settings, not from its name. A sim counts as no-wind if the wind model it actually uses is zero:
* an Average wind model with a speed of 0, or
* a MultiLevel wind model with every level at 0.

A sim whose average wind is 0 but which uses a non-zero MultiLevel profile does **not** count. If a file has no no-wind sim, the script stops with an error.

## Outputs

Everything is written to `--ork-folder`:

| File | Contents |
|---|---|
| `*.ork` | The generated variants, with stored simulation results stripped |
| `variants_manifest.csv`, `batch_config.json` | Written by `ork_batch.py` |
| `sim_results.csv` | The raw `batch_simulate.py` output, one row per simulation, in calibers |
| `sweep_results.csv` | One row per simulation for each rocket that passed the filters |

`sweep_results.csv` columns:

| Column | Meaning |
|---|---|
| `file` | Variant filename (the swept values are in the name, e.g. `AIRMAIL_height-7in.ork`) |
| `simulation_name` | Name of the simulation |
| `no_wind` | `True` for the rocket's no-wind sim |
| `first_stability_pct` | First non-NaN stability, % |
| `min_stability_pct` / `max_stability_pct` | Lowest / highest stability, % |
| `max_aoa_deg` | Max angle of attack, degrees |
| `apogee_ft` | Apogee, ft |
| `status` | `OK` or the `batch_simulate.py` error status |

The final line reports how many rockets were kept, culled, or had a failed no-wind sim. A rocket whose no-wind sim failed is left out of the results but is not counted as culled. `--worst-stability` checks only the sims that ran; a sim that failed doesn't cause a cull.

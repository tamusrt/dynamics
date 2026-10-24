# ork-batch-generation

A command-line tool for **batch-generating variants of OpenRocket `.ork` files** from one or more
reference designs. It replaces the separate one-off generators used before:

| Old tool | What it did | Now |
|---|---|---|
| `pdr_fin_refinement/Batch Creation/fin_batch_gui.py` | Sweep fin-set parameters (GUI) | `generate -v "fins.rootchord=10:16:1" ...` |
| `openrocket_batch_sim/openrocket_variant_generator.py` | Nose-cone trim + inserted transition sweeps (GUI) | `generate -v "nosecone.trim=..."` (trim only; transition insertion was not carried over) |

It can sweep **any parameter of any component** (fins, nose cone, body tubes, transitions, mass
components, bulkheads, parachutes, couplers, launch lugs, ...). It does this by editing the XML inside the
`.ork`. It never builds a rocket from scratch: every output starts as a copy of a reference file you give it.

* Only the Python standard library is used. Nothing to `pip install`.
* It only does variation batches. For a one-off change, edit the reference file in OpenRocket.
* Every run is a CLI command. A JSON config file can stand in for a long command line.
* There's also a point-and-click front end, `ork_batch_gui.py`. See [GUI](#gui).
* Outputs work straight away with `batch_sim/batch_simulate.py`. Generated files were
  checked by simulating them in OpenRocket 24.12.

---

## Contents

1. [Requirements](#requirements)
2. [Quick start](#quick-start)
3. [GUI](#gui)
4. [Commands](#commands)
5. [Core concepts](#core-concepts)
   - [Targets: `SELECTOR.PARAM`](#targets-selectorparam)
   - [Selectors](#selectors)
   - [Parameters](#parameters)
   - [Operations](#operations-----)
   - [Value specs](#value-specs)
   - [Units](#units)
   - [Labels, link groups and how combinations are built](#labels-link-groups-and-how-combinations-are-built)
   - [File naming](#file-naming)
6. [Nose volume, mass overrides, simulations](#nose-volume-mass-overrides-simulations)
   - [Nose-cone trim and volume preservation](#nose-cone-trim-and-volume-preservation)
   - [Mass overrides](#mass-overrides)
   - [Simulations](#simulations)
7. [Config files](#config-files)
8. [Outputs](#outputs)
9. [Validation and skipped variants](#validation-and-skipped-variants)
10. [Recipes](#recipes)
11. [Full pipeline: generate → simulate → analyze](#full-pipeline-generate--simulate--analyze)
12. [Shell quoting (PowerShell / cmd / bash)](#shell-quoting-powershell--cmd--bash)
13. [Option reference](#option-reference)
14. [Limitations and gotchas](#limitations-and-gotchas)
15. [Troubleshooting](#troubleshooting)
16. [Code layout](#code-layout)

---

## Requirements

* Python **3.9 or newer** (tested on 3.12). Nothing else is needed.
* Run it from this folder (`python ork_batch.py ...`) or by its full path from anywhere:
  `python "C:\...\ork-batch-generation\ork_batch.py" ...`

---

## Quick start

```powershell
cd "C:\Users\ncsam\Downloads\SRT-14 Goose Projects\ork-batch-generation"

# 1. See what's in the reference file: components, ready-to-use selectors, current values
python ork_batch.py inspect ..\pdr_fin_refinement\AIRMAIL.ork

# 2. Preview a sweep without writing anything (-n = --dry-run)
python ork_batch.py generate -b ..\pdr_fin_refinement\AIRMAIL.ork `
    -v "fins.rootchord=10:16:1" -v "fins.height=6,7,8" -n

# 3. Generate it
python ork_batch.py generate -b ..\pdr_fin_refinement\AIRMAIL.ork `
    -v "fins.rootchord=10:16:1" -v "fins.height=6,7,8" -o out\fin_test

# 4. Check what actually changed in one output
python ork_batch.py diff ..\pdr_fin_refinement\AIRMAIL.ork out\fin_test\AIRMAIL_root-12in_height-7in.ork
```

Step 3 writes 21 `.ork` files (7 root chords × 3 heights), a `variants_manifest.csv` and a
`batch_config.json` into `out\fin_test`.

---

## GUI

```powershell
python ork_batch_gui.py                       # or double-click ork_batch_gui.bat
python ork_batch_gui.py examples\nosecone_trim.json
python ork_batch_gui.py ..\pdr_fin_refinement\AIRMAIL.ork
```

The GUI is a tkinter window over the same `orkbatch` library. **Generate** runs `ork_batch.py generate`
underneath, so results match the CLI exactly.

* **Left side.** Add reference `.ork` files (or a whole folder). Pick one to browse its component tree.
  Selecting a component lists its parameters with current values in your units.
  * Double-click a parameter to sweep it.
  * Right-click a parameter or component to sweep it, sweep that parameter on *all* components of that
    type, or copy its selector.
* **Sweeps tab.** The list of sweeps: add, edit, duplicate, remove and reorder.
  The edit dialog suggests selectors and parameters from the loaded file and shows the reference value.
  It has a range builder (start/stop/step or count) and checks your input live
  (`OK: 7 values: 10, 11, ... 16 in`). The tab also shows a running variant count.
* **Options tab.** Output folder, filename prefix and name template, units, grid/zip combination, and the
  behavior switches (clear mass overrides, preserve nose volume, keep simulations, allow invalid, dedupe,
  manifest, overwrite).
* **Preview (F5).** Plans and validates every variant without writing anything. Shows each filename,
  its values and whether it would be written, skipped as invalid or skipped as a duplicate.
* **Generate (F6).** Writes the batch with a progress bar and a live log. Overwrite and large-batch
  questions appear as dialogs. **Cancel** stops after the current file, and the manifest still lists
  what was written.
* **File menu.** New / Open / Save config, in the same JSON format the CLI uses, so a GUI study can be
  re-run with `ork_batch.py generate -c`.
* **Tools menu.** Compare two `.ork` files (diff), Show equivalent command line, Open output folder.
* **Help menu.** Syntax cheat sheet, README.

---

## Commands

| Command | Purpose |
|---|---|
| `inspect FILE` | List every component with its selector(s) and current parameter values. **Start here.** |
| `params` | Reference list of known parameter names, their kinds/units, aliases and virtual parameters. |
| `generate` (alias `gen`) | Make a batch of variants from one or more reference files. |
| `diff A B` | Show every parameter that differs between two `.ork` files, in your units. |
| `init-config [PATH]` | Write an example JSON config to start from. |

Run `python ork_batch.py <command> -h` for that command's options.

### `inspect`

```
python ork_batch.py inspect REF.ork [--unit in|mm|cm|m|ft] [--mass-unit kg|g|lb|oz]
                                    [--brief] [--type TAG] [--select SELECTOR] [--json]
```

Example output (trimmed):

```
AIRMAIL.ork   (zip container, 3 simulation(s), lengths in in, masses in kg, angles in deg)
Sustainer  <stage>    select: stage  |  @Sustainer
  Nose Cone  <nosecone>    select: nosecone  |  @Nose Cone
      length=30 in   thickness=0.0787402 in   shape=haack   shapeparameter=0   aftradius=3 in ...
  Nose Cone Straight  <bodytube>    select: bodytube[0]  |  @Nose Cone Straight
      length=12 in   thickness=0.0413386 in   radius=3 in (auto)   diameter=6 in (auto)
  Combustion Chamber  <bodytube>    select: bodytube[2]  |  @Combustion Chamber
    Trapezoidal Fin Set  <trapezoidfinset>    select: trapezoidfinset  |  @Trapezoidal Fin Set
        fincount=4  thickness=0.3 in  crosssection=airfoil  rootchord=11.6 in  tipchord=4 in
        sweeplength=12.5 in  height=8.95 in  sweepangle=54.3973 deg ...
```

* `select:` lists selectors you can paste straight into `--vary`.
* `(auto)` means OpenRocket sizes that value automatically from its neighbor. See [gotchas](#limitations-and-gotchas).
* `--brief` prints only the component tree. `--type fins` / `--select "@Fiberglass Tube/*"` filter the list.
  `--json` prints machine-readable output.

### `generate`

```
python ork_batch.py generate -b REF.ork [REF2.ork ...] [-c config.json]
       -v "TARGET OP SPEC [as LABEL] [link GROUP]" [-v ...]
       [-o OUT_DIR] [--prefix P | --name-template T] [--unit U] [--mass-unit M]
       [--combine grid|zip] [--preserve-nose-volume]
       [--clear-mass-override] [--keep-sims] [--allow-invalid] [--no-dedupe]
       [-n/--dry-run [--show N|all]] [--overwrite] [-y] [-q] [--verbose]
```

* `-v/--vary` is a swept parameter. The swept parameters together define the batch. Everything you
  don't sweep stays as it is in the reference file.
* `-b` accepts several files, globs (`"refs\*.ork"`) and folders. Every reference file gets the full batch.

### `diff`

```
python ork_batch.py diff A.ork B.ork [--unit mm]
```

Components are paired by `<id>`, then by type + name, then by position among components of the same type.
Numeric lengths, angles and masses are shown in your units with the delta. Other values are shown raw.

---

## Core concepts

### Targets: `SELECTOR.PARAM`

Every edit names a **target**: which component(s), then a `.`, then which parameter.

```
fins.rootchord
nosecone.length
bodytube[1].length
@Fiberglass Tube.diameter
@Fiberglass Tube/masscomponent[0].mass
```

The parameter is whatever follows the **last** `.`. So component names that contain dots still work.

### Selectors

Selectors are case-insensitive.

| Selector | Matches |
|---|---|
| `nosecone` | The only `<nosecone>`. **Error if there are several**, so you never edit the wrong one by accident. |
| `bodytube[2]` | The third body tube in document order (0-based). `[-1]` is the last one. |
| `bodytube[*]` | Every body tube (the edit applies to all of them). |
| `fins`, `fins[1]` | Any fin set type: trapezoid, elliptical, freeform or tube. Also `fin`, `finset`. |
| `nose`, `tube`, `body` | Shorthand for `nosecone`, `bodytube`, `bodytube`. |
| `*`, `*[3]` | Any component. |
| `@Fiberglass Tube` | Component by name. Quote it as `@"name"` if the name contains `/ [ ] ' "`. |
| `@RECO Bulkhead[1]` | The second component with that name. |
| `#585ea21c` | The component whose `<id>` starts with this. |
| `@Fiberglass Tube/masscomponent[0]` | `/` scopes the search: the first mass component **inside** Fiberglass Tube. Indices count within the scope. |
| `stage[1]/fins` | The fin set in the second stage. |

Use any XML tag as a type: `nosecone bodytube transition trapezoidfinset ellipticalfinset freeformfinset
tubefinset launchlug railbutton innertube tubecoupler centeringring bulkhead engineblock masscomponent
shockcord parachute streamer stage podset`.

If a selector is ambiguous or matches nothing, the run stops **before writing anything**. The error lists
the candidates:

```
error: AIRMAIL.ork:
  - bodytube.length=1,2: Selector 'bodytube' matches 3 components of type 'bodytube':
    [0] Nose Cone Straight <bodytube>; [1] Fiberglass Tube <bodytube>; [2] Combustion Chamber <bodytube>.
    Add an index like [0] or [*], or select by @name.
```

**Prefer `@name` selectors when sweeping several reference files.** An index like `bodytube[1]` can
point at different parts in different designs.

### Parameters

Any leaf value that `inspect` shows can be a target. The parameter name is the XML tag (`rootchord`,
`length`, `aftradius`, `thickness`, `cant`, `mass`, ...). The parameter's **kind** decides how your
values are converted:

| Kind | Examples | Your values are in |
|---|---|---|
| length | `length thickness radius aftradius rootchord tipchord height sweeplength filletradius axialoffset packedlength ...` | `--unit` (default `in`) or a suffix |
| angle | `cant angleoffset rotation radialdirection sweepangle` | degrees (`rad` suffix accepted) |
| mass | `mass overridemass` | `--mass-unit` (default `kg`) or a suffix |
| int | `fincount instancecount linecount` | whole numbers |
| number | `shapeparameter cd overridecd material@density` | unitless / SI |
| choice | `crosssection` (square, rounded, airfoil), `shape` (conical, ogive, ellipsoid, power, parabolic, haack), `finish` | text |
| bool | `shapeclipped isflipped ...` | `true` / `false` |
| raw | anything else | written as typed |

Run `python ork_batch.py params` for the full list.

**Virtual parameters** are computed from real tags:

| Name | Meaning |
|---|---|
| `diameter`, `forediameter`, `aftdiameter`, `outerdiameter`, `innerdiameter` | 2 × the matching `*radius` tag. Used only when no real tag of that name exists. A parachute's `<diameter>` is a real tag and is edited directly. |
| `sweepangle` | Fin leading-edge sweep angle. Written as `sweeplength = height × tan(angle)`, OpenRocket's own definition. It is applied **after** all other edits in a variant, so it uses that variant's (possibly new) height. |
| `trim` | Nose-cone trim. `nosecone.trim=2` is exactly `nosecone.length-=2`, with default label `trim`. |
| `TAG@ATTR` | An XML attribute, e.g. `material@density` (kg/m³) or `axialoffset@method` (`top`, `middle`, `bottom`, `absolute`). |

**Aliases** (shorthand for parameter names): `root`→rootchord, `tip`→tipchord, `span`→height,
`sweep`→sweeplength, `n`/`count`→fincount, `thick`→thickness, `fillet`→filletradius, `pos`→axialoffset,
`xsec`→crosssection, `sweepang`→sweepangle, `dia`→diameter, `od`→outerdiameter.

**Twin tags.** OpenRocket 24 writes some values twice: `axialoffset` with `position`, `angleoffset` with
`rotation`, and on fin sets `fincount` with `instancecount`. Writing one also writes its twin, so they stay
consistent.

### Operations (`=`, `+=`, `-=`, `*=`)

| Op | Meaning | Example |
|---|---|---|
| `=` | Set an absolute value | `fins.rootchord=12` |
| `+=` | Add to the reference value | `@Fiberglass Tube.length+=0:6:2` |
| `-=` | Subtract from the reference value | `nosecone.length-=1:20:1` (same as `nosecone.trim=1:20:1`) |
| `*=` | Multiply the reference value (unitless factor) | `bodytube[*].length*=0.9,1,1.1` |

Relative ops act on the value **as it is at that point in the variant**. Sweeps are applied in the order
given. Each variant starts from a fresh copy of the reference file,
so nothing accumulates between variants.

### Value specs

| Spec | Meaning |
|---|---|
| `0.3` | A single value (in the default unit). It still counts as a sweep with one step. |
| `7.62mm`, `0.3in`, `2.5lb`, `0.1rad` | A value with an explicit unit |
| `10,12,14` | A list |
| `square,airfoil` | A list of choices |
| `10:16:1` | Inclusive range start:stop:step → 10, 11, …, 16 |
| `16:10:-2` | Ranges can count down |
| `10:16:n4` | 4 evenly spaced points from 10 to 16 → 10, 12, 14, 16 |
| `250mm:400mm:25mm` | Units work inside ranges |

### Units

* `--unit` sets the default length unit for values you type **and** for display (filenames, manifest,
  `inspect`, `diff`). Choices: `in` (default), `mm`, `cm`, `m`, `ft`.
* `--mass-unit` does the same for masses: `kg` (default), `g`, `lb`, `oz`.
* Angles are always degrees. Any number can carry its own unit suffix, which overrides the default.
* Inside the `.ork`, OpenRocket stores everything in SI (m, kg) and angles in degrees. The tool converts for you.

### Labels, link groups and how combinations are built

Each `--vary` entry has a **label**, used in filenames, the manifest and `--name-template`:

* The default label is a short name: `rootchord`→`root`, `tipchord`→`tip`, `sweeplength`→`sweep`,
  `fincount`→`n`, `trim`, and so on. These match the old fin GUI, so filenames come out the same.
  Relative ops add a suffix (`lengthplus`, `lengthminus`, `lengthx`).
* Labels that would collide get a selector prefix automatically (`bodytube1_length`).
* Set your own with `as NAME`: `-v "fins.height=6:9:1 as span"`.

**Combination rules:**

* By default every vary entry is crossed with every other one: a **grid** (Cartesian product).
  3 root chords × 4 heights = 12 variants.
* Entries with the same `link GROUP` advance **together**. They are zipped, so each must have the same
  number of values. A link group as a whole is then crossed with everything else.

  ```
  -v "fins.height=6,7,8 link g" -v "fins.sweeplength=4,5,6 link g" -v "fins.n=3,4"
  # -> (6,4),(7,5),(8,6) x n in {3,4} = 6 variants
  ```
* `--combine zip` zips **all** unlinked vary entries together instead of crossing them. Explicit link
  groups still behave as above.

### File naming

* By default: `{prefix}_{label}-{value}{unit}_...`, e.g. `Airmail_root-10in_tip-4in_height-5in_sweep-4in.ork`.
  This is the same as the old fin GUI.
* `prefix` defaults to the reference file's name. With `--prefix X` and several reference files, the
  prefix becomes `X_<reference name>`.
* `--name-template` gives full control. It is a Python format string with fields `{prefix}`, `{base}`,
  `{i}` (1-based index that restarts for each reference file, so pair it with `{base}` when using several; `{i:04d}` zero-pads) and every vary label:

  ```
  --name-template "{base}_{trim}Trim"                                       -> SolInvictus_3Trim.ork
  --name-template "{base}_run{i:04d}"                                       -> SolInvictus_run0001.ork ...
  ```
* Characters that aren't allowed in filenames become `_`. Duplicate names get `_2`, `_3`, … appended.

---

## Nose volume, mass overrides, simulations

### Nose-cone trim and volume preservation

```
-v "nosecone.trim=1:20:1"                 # = nosecone.length -= 1..20 in
--preserve-nose-volume                    # optional
--clear-mass-override                     # usually wanted, see "Mass overrides"
```

With `--preserve-nose-volume`, whenever a nose cone ends up with **less internal volume** than in the
reference (from a trim, a shape change or a radius change), the **body tube directly behind it** gets
longer by `lost volume / (π r²)`. The volume comes from numerically integrating the nose cone's real
profile: conical, ogive (with shape parameter), ellipsoid, power, parabolic or Haack, using the file's own
`<shape>`/`<shapeparameter>`. A cylinder approximation is not used. If the next component isn't a plain
body tube, the step is skipped and a note goes in the manifest. Internal parts inside the nose cone
(payload mass, bulkheads, …) stay where they are, the same as when you edit the length in OpenRocket's GUI.

### Mass overrides

Many reference designs carry `<overridemass>` on parts (e.g. AIRMAIL's *Combustion Chamber* = 0 kg, *Tail*
= 3.24 kg). If you resize a part whose mass is overridden, **its mass will not change** unless you clear the
override.

* `--clear-mass-override` removes `<overridemass>` from every component the variant modifies. That
  includes components touched by nose-volume preservation. OpenRocket then
  recomputes the mass from material and geometry. This is how the old nose-cone generator behaved, so use
  it to reproduce those studies.
* It is **off by default**, so a plain fin sweep leaves your overrides alone.
* A component whose `overridemass` you sweep in the same run is never cleared.
* To sweep an override: `-v "@Tail.overridemass=2:3:0.5 as tailmass"`, with values in `--mass-unit`.
  The tag is created if missing.

### Simulations

* Each output keeps the reference file's **simulation setups** (motor, launch conditions, wind…), so
  `batch_simulate.py` can run them straight away.
* Stored **flight-data results are removed** and each simulation is marked `outdated`, because they belong
  to the old geometry. They also make up most of the file size: 2.8 MB → about 4 KB here.
  `--keep-sims` keeps them.
* If the reference has no simulations, the outputs won't either. The run ends with a note saying so.

---

## Config files

Anything you can say on the command line can go in a JSON file. Start one with:

```
python ork_batch.py init-config my_study.json
python ork_batch.py generate -c my_study.json -n      # preview
python ork_batch.py generate -c my_study.json         # run
```

```json
{
  "_comment": "keys starting with _ are ignored",
  "base": ["../pdr_fin_refinement/AIRMAIL.ork"],
  "out_dir": "output/my_study",
  "prefix": "Airmail",
  "name_template": null,
  "unit": "in",
  "mass_unit": "kg",
  "combine": "grid",
  "vary": [
    "fins.rootchord=10:16:1",
    "fins.tipchord=4:8:1 as tip",
    {"target": "fins.height", "values": [6, 7, 8], "label": "span"},
    {"target": "fins.sweeplength", "range": [4, 12, 2], "unit": "in"},
    {"target": "nosecone.length", "op": "-=", "linspace": [0, 4, 3], "label": "trim"},
    {"target": "fins.cant", "spec": "0:1:0.5", "link": "g1"}
  ],
  "preserve_nose_volume": false,
  "clear_mass_override": false,
  "keep_sims": false,
  "allow_invalid": false,
  "overwrite": false,
  "dedupe": true,
  "manifest": true
}
```

* `vary` items can be **strings** (same syntax as the CLI) or **objects** with:
  `target`, `op` (`=`, `+=`, `-=`, `*=`, or `set`/`add`/`sub`/`scale`), **one of** `values` (list or
  scalar), `value`, `range` `[start, stop, step]`, `linspace` `[start, stop, count]` or `spec` (CLI spec
  string), plus optional `unit`, `label` (or `as`) and `link` (or `group`).
* `base` and `out_dir` paths are relative to **the config file's folder**.
* **Precedence:** command-line flags override the config's scalar settings. `-b` and `-v` are
  **added** to the config's lists. A base config plus extra `-v` on the command line works as you'd expect.
* Every run writes `batch_config.json` into the output folder, with absolute paths. `generate -c
  that/batch_config.json` reproduces the batch exactly.

Ready-made examples are in [`examples/`](examples):

| File | What it does |
|---|---|
| `fin_sweep.json` | The `pdr_fin_refinement` Batch3 fin grid (root × tip × height × sweep, 1575 files). Old GUI's filenames. |
| `nosecone_trim.json` | `nosecone_trade_study_length`: `SolInvictus_{1..20}Trim.ork`. Output matches the existing files exactly. |
| `multi_component.json` | Two reference files, fins + nose shape (linked shape/parameter) + tube length + payload mass. |

Example outputs go to `ork-batch-generation/output/<study>` unless you pass `-o`.

---

## Outputs

In `--out-dir` (default `./ork_variants`):

| File | Contents |
|---|---|
| `<name>.ork` | One per variant. Same container format as the reference (zip / gzip / plain XML). Decals and other zip entries are kept. |
| `variants_manifest.csv` | One row per planned variant: `file, base, status`, each vary label (in display units), then the **final value of every targeted parameter** (`Component.param (unit)`), then `problems, notes`. |
| `batch_config.json` | The fully resolved settings, for re-running. |

`status` is one of `written`, `skipped` (failed validation), `duplicate of X.ork` (output identical to an
earlier variant, so not written again) or `written-invalid` (with `--allow-invalid`). `--no-dedupe`
writes duplicates anyway. `--no-manifest` skips both side files.

Before writing, the tool also:

* resolves every target against every reference file and stops with a full list of errors if anything is wrong,
* asks before overwriting existing files (`--overwrite` or `-y` skips the prompt; in a non-interactive
  shell it refuses unless you pass one of them),
* asks before writing more than 1000 files (`-y` skips the prompt).

---

## Validation and skipped variants

After the edits, each modified component is checked:

* `length`, `rootchord`, `height`, `radius`, `outerradius` must be > 0
* `tipchord`, `thickness`, `filletradius`, tabs, other radii, masses, packed sizes must be ≥ 0
* fin thickness > 0, fin count 1–8, sweep angle within ±89°
* nose cone length ≥ 5 mm, body-tube wall thickness ≤ radius
* `crosssection` / `shape` values must be valid choices

Failing variants are **skipped** and listed (`SKIP name.ork: reason`) and kept in the manifest with their
reason. `--dry-run` shows the problems column so you can tune ranges first.
`--allow-invalid` writes them anyway.

---

## Recipes

All examples use PowerShell line continuation (`` ` ``). In cmd use `^`, in bash use `\`.

**Fin planform grid**
```powershell
python ork_batch.py gen -b ..\pdr_fin_refinement\AIRMAIL.ork --prefix Airmail `
  -v fins.root=10:16:1 -v fins.tip=4:8:1 -v fins.height=5:9:1 `
  -v fins.sweep=4:12:1 -o out\fins
```

**Sweep angle at several spans (sweep length follows the span)**
```powershell
python ork_batch.py gen -b ref.ork -v "fins.sweepangle=30:60:5 as sa" -v "fins.span=6:9:1"
```

**Fin count × cross-section (categorical)**
```powershell
python ork_batch.py gen -b ref.ork -v fins.n=3,4 -v fins.xsec=square,rounded,airfoil
```

**Paired values (not a grid) with a link group**
```powershell
python ork_batch.py gen -b ref.ork -v "fins.root=10,12,14 link p" -v "fins.tip=3,4,5 link p"
```

**Nose-cone length study (the old trim study)**
```powershell
python ork_batch.py gen -b "..\nosecone_trade_study_length\OpenRocket Files\SolInvictus.ork" `
  -v "nosecone.trim=1:20:1" --clear-mass-override --name-template "{base}_{trim}Trim" -o out\trim
```

**Nose shape × fineness, keeping internal volume**
```powershell
python ork_batch.py gen -b ref.ork -v "nosecone.shape=haack,ogive,conical" -v "nosecone.length=24:36:4" `
  --preserve-nose-volume --clear-mass-override
```

**Boat-tail study on the existing tail transition**
```powershell
python ork_batch.py gen -b ref.ork -v "@Tail.length=2:8:1" -v "@Tail.aftdiameter=3:5:0.5" --clear-mass-override
```

**Stretch / shrink all body tubes together**
```powershell
python ork_batch.py gen -b ref.ork -v "bodytube[*].length*=0.9:1.1:n5 as scale"
```

**Payload mass and CG position**
```powershell
python ork_batch.py gen -b ref.ork --mass-unit lb -v "@Payload.mass=3:6:1" -v "@Payload.axialoffset+=-4:4:2 as dx"
```

**Material density (e.g. carbon vs. fiberglass fins)**
```powershell
python ork_batch.py gen -b ref.ork -v "fins.material@density=1780,1850 as rho" --clear-mass-override
```

**Same study on several designs at once**
```powershell
python ork_batch.py gen -b ..\pdr_fin_refinement\AIRMAIL.ork ..\pdr_fin_refinement\optimal.ork -v fins.span=7:9:1
python ork_batch.py gen -b "candidates\*.ork" -v fins.span=7:9:1      # glob
python ork_batch.py gen -b candidates -v fins.span=7:9:1              # whole folder
```

**Combine a saved config with extra sweeps from the command line**
```powershell
python ork_batch.py gen -c examples\fin_sweep.json -v "fins.n=3,4" -o out\fins_with_count
```

---

## Full pipeline: generate → simulate → analyze

```powershell
$root = "C:\Users\ncsam\Downloads\SRT-14 Goose Projects"

python "$root\ork-batch-generation\ork_batch.py" gen -c "$root\ork-batch-generation\examples\fin_sweep.json" `
    -o "$root\pdr_fin_refinement\Batch4" -y

python "$root\openrocket_batch_sim\batch_simulate.py" --input-dir "$root\pdr_fin_refinement\Batch4" `
    --jar "$root\openrocket_batch_sim\OpenRocket-24.12.jar" --output-csv "$root\pdr_fin_refinement\results4.csv" --workers 16
```

`batch_simulate.py` only picks up `*.ork`, so the manifest and config files in the folder don't get in
the way. To link results back to parameters, join `results.csv` (`source_file`) with
`variants_manifest.csv` (`file`) on the filename. `analyze_trade_study.py` parses `_<x>Trim` filename
segments, so use `--name-template "{base}_{trim}Trim"` (as `examples/nosecone_trim.json` does) when you
want to feed it.

---

## Shell quoting (PowerShell / cmd / bash)

* **Quote anything that starts with `@`** in PowerShell, where `@` means splatting:
  `-v "@Payload.mass=3:6:1"`. It's also a good habit in other shells.
* Quote anything with spaces (`"@Fiberglass Tube.length=30"`, `as`/`link` suffixes) and `[*]` in bash.
* In **cmd.exe**, use double quotes only. Single quotes aren't quotes there.
* For a component name containing `"`, use a config file instead of fighting the shell.

---

## Option reference

`generate` options:

| Option | Default | Description |
|---|---|---|
| `-c, --config FILE` | – | JSON config (CLI overrides / extends it) |
| `--unit` | `in` | Default length unit for input and display |
| `--mass-unit` | `kg` | Default mass unit |
| `--[no-]preserve-nose-volume` | off | Lengthen the tube behind the nose when the nose loses volume |
| `--[no-]clear-mass-override` | off | Remove `<overridemass>` from modified parts |
| `--[no-]keep-sims` | off | Keep stored simulation results |
| `--[no-]allow-invalid` | off | Write variants that fail validation |
| `--[no-]overwrite` | off | Overwrite existing outputs without asking |
| `-y, --yes` | – | Yes to all prompts |
| `-q, --quiet` | – | Print only skips, errors and the summary |
| `--verbose` | – | Print per-file notes (volume compensation, cleared overrides…) |
| `-b, --base ORK…` | – | Reference file(s), globs or folders, repeatable |
| `-v, --vary SPEC` | – | Swept edit: `TARGET OP SPEC [as LABEL] [link GROUP]`, repeatable |
| `-o, --out-dir` | `ork_variants` | Output folder (created if needed) |
| `--prefix` | reference name | Filename prefix |
| `--name-template` | – | Filename format string |
| `--combine grid\|zip` | `grid` | How unlinked vary entries combine |
| `--[no-]dedupe` | on | Skip outputs identical to an earlier one |
| `--[no-]manifest` | on | Write `variants_manifest.csv` + `batch_config.json` |
| `-n, --dry-run` | – | Plan + validate + print a table, write nothing |
| `--show N\|all` | 25 | Rows to print in a dry run |

---

## Limitations and gotchas

* **"auto" values become fixed.** OpenRocket can size some values automatically, such as a body-tube
  radius that follows its neighbor or a bulkhead's outer radius (`inspect` shows `(auto)`). Editing such a
  value writes a plain number and switches auto off for that part. Values you don't touch stay auto.
  A relative op (`+=`, `*=`) on a pure `auto` value with no number stored is refused; use `=`.
* **Diameters and neighbors.** Changing one tube's diameter doesn't move its neighbors. OpenRocket
  re-derives neighbors only for parts set to auto. Use `bodytube[*].diameter=...` or select several parts.
* **Freeform fin point lists** (`<finpoints>`) aren't editable. Their scalar parameters (thickness, count,
  cant, position, fillet, …) are.
* **No components are added or removed.** To sweep a part, it has to exist in the reference file.
* Selectors with an index (`bodytube[2]`) depend on component order. Prefer `@name` across different designs.
* Nose-volume preservation lengthens the tube behind the nose and doesn't move parts inside the nose.
  Internal masses keep their offsets.
* The tool doesn't run simulations. Use `batch_simulate.py`, as shown above.

---

## Troubleshooting

| Message | Fix |
|---|---|
| `Selector 'bodytube' matches 3 components` | Add `[i]`, `[*]` or use `@name`. The candidates are listed in the message. |
| `has no parameter 'X'. Available: …` | Check the spelling against `inspect`/`params`. Fin-only params don't exist on other parts. |
| `has no numeric value ('auto') to apply '+='` | Use an absolute `=` value. |
| `Linked/zipped entries need the same number of values` | Give every entry in a link group (or every entry with `--combine zip`) the same number of values. |
| `N output file(s) already exist` | Add `--overwrite`/`-y` or choose a new `-o`. |
| Everything `SKIP`ped | Run with `-n`, read the problems column, then adjust ranges. |
| PowerShell: `The splatting operator '@' cannot be used…` | Quote the argument: `-v "@Name.param=1,2"`. |
| `config …: <key> no longer supported` | Fixed edits (`set`), sampling, filters (`where`) and transition insertion were removed. Put only sweeps under `"vary"`. Make one-off changes in the reference `.ork`. |
| Mass doesn't change after resizing | That part has a mass override. Add `--clear-mass-override`. |

---

## Code layout

```
ork-batch-generation/
├─ ork_batch.py            entry point (python ork_batch.py ...)
├─ ork_batch_gui.py        tkinter GUI (ork_batch_gui.bat launches it without a console)
├─ orkbatch/
│  ├─ cli.py               argument parsing, config merging, commands
│  ├─ engine.py            per-file checks, planning, building variants, manifest
│  ├─ sweep.py             --vary parsing, value specs, combinations, naming
│  ├─ selectors.py         component lookup (types, indices, @names, #ids, scoping)
│  ├─ params.py            parameter kinds, read/write accessors, virtual params, validation
│  ├─ geometry.py          nose-cone volume math
│  ├─ units.py             unit conversion and number formatting
│  └─ orkio.py             .ork container load/save (zip / gzip / xml), simulation stripping
└─ examples/               ready-to-run configs reproducing earlier studies
```

You can also use the package from Python:

```python
import sys; sys.path.insert(0, r"C:\...\ork-batch-generation")
from orkbatch.cli import main
main(["generate", "-b", "ref.ork", "-v", "fins.root=10:14:2", "-o", "out", "-y"])
```

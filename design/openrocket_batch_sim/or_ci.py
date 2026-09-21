#!/usr/bin/env python3
r"""
or_ci.py
--------
Run every simulation saved in OpenRocket (.ork) files headlessly and compare
the flight performance before and after a commit. Built for GitHub Actions
(.github/workflows/openrocket-sim.yml) but works identically on a laptop.

    python or_ci.py run     --config aero_modeling/sim_config.json [files...]
    python or_ci.py compare --config aero_modeling/sim_config.json --base HEAD~1 [files...]
    python or_ci.py compare --config aero_modeling/sim_config.json --base origin/main --all
    python or_ci.py history --config aero_modeling/sim_config.json --cache .or_ci_cache [files...]

WHAT "HISTORY" MEANS
    Every committed version of each .ork (first-parent history, renames
    followed) is simulated the same way, cached by git blob id so only
    versions never seen before cost anything, and rendered as Mermaid bar
    charts (apogee and stability per commit) that GitHub draws inline in
    commit comments and job summaries. No branch is written to.

WHAT "RUN" MEANS
    For each .ork file, every simulation saved in it is run with its own
    saved conditions (launch site, rod, wind, atmosphere, flight
    configuration). Nothing is invented. The only thing the file cannot
    supply is a custom motor: OpenRocket's headless engine only knows the
    motors bundled in its jar, so a design flying a team-made .rse/.eng
    loads with an empty motor and flies to 0 m. Motors are resolved per
    SIMULATION and per MOTOR MOUNT (a hybrid modelled as an ox-tank motor
    plus a combustion-chamber motor has two mounts), in this order:
      1. sim_config.json -> files[<ork>].simulations[<sim name>].motors[<mount>]
         (or .motor when the simulation has a single mount)
      2. sim_config.json -> files[<ork>].default_motors[<mount>] (or .default_motor)
      3. the motor the file resolved on its own (commercial motors)
      4. auto-lookup: a .rse/.eng in the .ork's folder whose designation
         matches the designation stored in the .ork for that mount
      5. UNRESOLVED (reported loudly; the simulation is still run)

WHAT "COMPARE" MEANS
    The .ork files changed between --base and the working tree (plus any
    whose motor file changed) are simulated twice in the same JVM: the
    base version (extracted with `git show`) and the current one, with the
    same pinned random seed and (by default, "deterministic_wind") wind
    turbulence zeroed, so an unchanged design gives bit-identical numbers
    and the deltas reflect the design change, not the gust draw. OpenRocket
    24.12 draws turbulence entropy outside the seed, so the seed alone is
    not enough. A markdown report is written (and appended to
    $GITHUB_STEP_SUMMARY when --summary is given). Limits from the config
    are checked on the current version; violations fail the run only with
    --strict or "fail_on_limits": true in the config.

CONFIG (aero_modeling/sim_config.json; every path relative to that file)
    {
      "seed": 20260915,
      "fail_on_limits": false,
      "ignore": ["**/OLD/**"],
      "limits": {"stability_off_rod_cal": {"min": 1.5}},
      "files": {
        "IREC_2027/2027_OR_9_15.ork": {
          "default_motor": "IREC_2027/IGNIS_2027.rse",
          "simulations": {
            "Simulation 3": {"motor": "IREC_2027/IGNIS_2027.rse",
                             "limits": {"max_mach": {"max": 2.0}}}
          }
        }
      }
    }
"""

import argparse
import csv
import fnmatch
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ork_diff  # noqa: E402  (structural diff of two .ork files; stdlib only)

DEFAULT_SEED = 20260915

# The six TRACKED metrics: summary table columns, history charts, limits by default.
# (metric key, label, unit, decimals)
PRIMARY_METRICS = [
    ("apogee", "Apogee", "m", 0),
    ("max_mach", "Max Mach", "", 2),
    ("max_dynamic_pressure_kpa", "Max dynamic pressure", "kPa", 1),
    ("stability_off_rod_cal", "Stability off rod", "cal", 2),
    ("min_stability_cal", "Min stability", "cal", 2),
    ("max_stability_cal", "Max stability", "cal", 2),
]
# Secondary metrics: kept in the detail tables, CSV and JSON.
SECONDARY_METRICS = [
    ("max_velocity", "Max velocity", "m/s", 1),
    ("max_acceleration", "Max acceleration", "m/s²", 1),
    ("velocity_off_rod", "Rod exit speed", "m/s", 1),
    ("time_to_apogee", "Time to apogee", "s", 1),
    ("velocity_at_deployment", "Deployment speed", "m/s", 1),
    ("descent_rate", "Descent rate", "m/s", 1),
    ("flight_time", "Flight time", "s", 0),
    ("landing_distance", "Landing distance", "m", 0),
    ("min_stability_cal_raw", "Min stability, orlab raw (to apogee)", "cal", 2),
    ("max_stability_cal_raw", "Max stability, orlab raw (to apogee)", "cal", 2),
    # the same three stability margins as a percentage of overall rocket length
    ("stability_off_rod_pct", "Stability off rod", "% L", 1),
    ("min_stability_pct", "Min stability", "% L", 1),
    ("max_stability_pct", "Max stability", "% L", 1),
    ("reference_diameter_m", "Reference diameter", "m", 4),
    ("rocket_length_m", "Rocket length", "m", 3),
]
CHART_TITLES = {
    "apogee": "Apogee",
    "max_mach": "Max Mach",
    "max_dynamic_pressure_kpa": "Max-Q",
    "stability_off_rod_cal": "Off-the-rail stability",
    "min_stability_cal": "Minimum stability",
    "max_stability_cal": "Maximum stability",
}
# stability margin in calibers (CP-CG)/D or as a percentage of rocket length (CP-CG)/L*100;
# both are stored, "stability_units" in the config picks which one the reports show
STABILITY_PAIRS = {"stability_off_rod_cal": "stability_off_rod_pct", "min_stability_cal": "min_stability_pct",
                   "max_stability_cal": "max_stability_pct"}
STABILITY_UNITS = ("cal", "pct")
for _cal, _pct in STABILITY_PAIRS.items():
    CHART_TITLES[_pct] = CHART_TITLES[_cal]
METRICS = PRIMARY_METRICS + SECONDARY_METRICS
METRIC_KEYS = [m[0] for m in METRICS]
PRIMARY_KEYS = [m[0] for m in PRIMARY_METRICS]

# Display units. Everything is computed and stored (JSON, CSV, cache) in SI; the config's
# "units" flag ("metric" default | "imperial") only changes how reports and limits read.
M_TO_FT = 3.280839895
KPA_TO_PSI = 0.1450377377
IMPERIAL = {  # metric key -> (unit, factor from SI, decimals)
    "apogee": ("ft", M_TO_FT, 0),
    "max_dynamic_pressure_kpa": ("psi", KPA_TO_PSI, 1),
    "max_velocity": ("ft/s", M_TO_FT, 1),
    "max_acceleration": ("ft/s²", M_TO_FT, 1),
    "velocity_off_rod": ("ft/s", M_TO_FT, 1),
    "velocity_at_deployment": ("ft/s", M_TO_FT, 1),
    "descent_rate": ("ft/s", M_TO_FT, 1),
    "landing_distance": ("ft", M_TO_FT, 0),
}
UNIT_SYSTEMS = ("metric", "imperial")


def metric_specs(units: str = "metric"):
    """[(key, label, unit, decimals, factor_from_SI)] for every metric in the requested unit system."""
    out = []
    for key, label, unit, dec in METRICS:
        factor = 1.0
        if units == "imperial" and key in IMPERIAL:
            unit, factor, dec = IMPERIAL[key]
        out.append((key, label, unit, dec, factor))
    return out


def primary_specs(units: str = "metric", stability: str = "cal"):
    """The six tracked metrics, with the stability margins in calibers or % of length."""
    keys = [STABILITY_PAIRS.get(k, k) if stability == "pct" else k for k in PRIMARY_KEYS]
    by_key = {s[0]: s for s in metric_specs(units)}
    return [by_key[k] for k in keys]


def spec_for(key: str, units: str = "metric"):
    for s in metric_specs(units):
        if s[0] == key:
            return s
    return (key, key, "", 3, 1.0)

# Stability window: from launch-rod departure to apogee, but only while the rocket is
# moving faster than this. OpenRocket's margin diverges as airspeed -> 0 near apogee
# (orlab's raw min comes out at -20 cal on the IREC design), which is not a real
# stability event.
STABILITY_MIN_SPEED_MS = 30.0
R_AIR = 287.05  # J/(kg K)


# ----------------------------------------------------------------------------
# git / filesystem snapshots
# ----------------------------------------------------------------------------
def _git(repo: Path, *args, check=True, binary=False):
    r = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=not binary)
    if check and r.returncode != 0:
        err = r.stderr.decode(errors="replace") if binary else r.stderr
        raise RuntimeError(f"git {' '.join(args)} failed: {err.strip()}")
    return r


def repo_root(start: Path) -> Path:
    r = _git(start if start.is_dir() else start.parent, "rev-parse", "--show-toplevel")
    return Path(r.stdout.strip())


def rel_posix(path: Path, root: Path) -> str:
    return PurePosixPath(path.resolve().relative_to(root.resolve())).as_posix()


class Snapshot:
    """Where design/motor files are read from: the working tree (ref=None) or a git ref."""

    def __init__(self, root: Path, ref=None):
        self.root = root
        self.ref = ref
        self._tmp = None
        self._cache = {}

    @property
    def label(self) -> str:
        return "working tree" if self.ref is None else self.ref

    def exists(self, rel: str) -> bool:
        if self.ref is None:
            return (self.root / rel).is_file()
        return _git(self.root, "cat-file", "-e", f"{self.ref}:{rel}", check=False).returncode == 0

    def path(self, rel: str):
        """Filesystem path holding this file's content in the snapshot (None if absent)."""
        if self.ref is None:
            p = self.root / rel
            return p if p.is_file() else None
        if rel in self._cache:
            return self._cache[rel]
        if not self.exists(rel):
            self._cache[rel] = None
            return None
        if self._tmp is None:
            self._tmp = Path(tempfile.mkdtemp(prefix="or_ci_base_"))
        out = self._tmp / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(_git(self.root, "show", f"{self.ref}:{rel}", binary=True).stdout)
        self._cache[rel] = out
        return out

    def walk(self, dir_rel: str):
        """Repo-relative paths of every file under dir_rel (recursive)."""
        if self.ref is None:
            d = self.root / dir_rel if dir_rel else self.root
            if not d.is_dir():
                return []
            return sorted(rel_posix(p, self.root) for p in d.rglob("*") if p.is_file())
        r = _git(self.root, "ls-tree", "-r", "--name-only", self.ref, "--", dir_rel or ".", check=False)
        return sorted(n for n in r.stdout.split("\n") if n)


# ----------------------------------------------------------------------------
# config
# ----------------------------------------------------------------------------
class Config:
    def __init__(self, path: Path):
        self.path = path.resolve()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.dir = self.path.parent
        self.seed = int(raw.get("seed", DEFAULT_SEED))
        self.units = str(raw.get("units", "metric")).strip().lower()
        if self.units not in UNIT_SYSTEMS:
            raise SystemExit(f"sim_config.json: units must be one of {UNIT_SYSTEMS}, got {self.units!r}")
        self.stability_units = str(raw.get("stability_units", "cal")).strip().lower()
        if self.stability_units not in STABILITY_UNITS:
            raise SystemExit(f"sim_config.json: stability_units must be one of {STABILITY_UNITS}, got {self.stability_units!r}")
        self.deterministic_wind = bool(raw.get("deterministic_wind", True))
        self.fail_on_limits = bool(raw.get("fail_on_limits", False))
        self.ignore = list(raw.get("ignore", []))
        self.limits = dict(raw.get("limits", {}))
        self.files = {self._norm(k): v for k, v in raw.get("files", {}).items()}

    @staticmethod
    def _norm(rel: str) -> str:
        return PurePosixPath(rel.replace("\\", "/")).as_posix()

    def to_repo_rel(self, cfg_rel: str, root: Path) -> str:
        """config-relative path -> repo-relative posix path."""
        return rel_posix(self.dir / cfg_rel, root)

    def is_ignored(self, cfg_rel: str) -> bool:
        cfg_rel = self._norm(cfg_rel)
        for pat in self.ignore:
            if fnmatch.fnmatch(cfg_rel, pat) or fnmatch.fnmatch("/" + cfg_rel, pat):
                return True
        return False

    def file_cfg(self, cfg_rel: str) -> dict:
        return self.files.get(self._norm(cfg_rel), {})

    @staticmethod
    def sim_cfg(file_cfg: dict, sim_name: str) -> dict:
        sims = file_cfg.get("simulations", {})
        if sim_name in sims:
            return sims[sim_name]
        key = " ".join(sim_name.split()).lower()
        for k, v in sims.items():
            if " ".join(k.split()).lower() == key:
                return v
        return {}

    def limits_for(self, file_cfg: dict, sim_cfg: dict) -> dict:
        merged = {}
        for layer in (self.limits, file_cfg.get("limits", {}), sim_cfg.get("limits", {})):
            for metric, spec in layer.items():
                merged.setdefault(metric, {}).update(spec)
        return merged


# ----------------------------------------------------------------------------
# .ork / motor-file parsing (no JVM needed)
# ----------------------------------------------------------------------------
def parse_ork(path: Path) -> dict:
    """{'sims': [(name, configid)],
        'mounts': [(mount component name, {configid: (manufacturer, designation)})]}
    A hybrid modelled as two motors (ox tank + combustion chamber) has two mounts."""
    with zipfile.ZipFile(path) as zf:
        name = "rocket.ork" if "rocket.ork" in zf.namelist() else zf.namelist()[0]
        root = ET.fromstring(zf.read(name))
    mounts = []
    for comp in root.iter():
        mm = comp.find("motormount")
        if mm is None:
            continue
        cfgs = {}
        for m in mm.findall("motor"):
            cid = m.get("configid")
            if cid:
                cfgs[cid] = ((m.findtext("manufacturer") or "").strip(), (m.findtext("designation") or "").strip())
        mounts.append(((comp.findtext("name") or ""), cfgs))  # exact name, trailing spaces included
    sims = []
    for s in root.iter("simulation"):
        sims.append(((s.findtext("name") or "").strip(), (s.findtext("conditions/configid") or "").strip()))
    return {"sims": sims, "mounts": mounts}


def mounts_for(info: dict, configid: str):
    """[(mount name, designation)] carrying a motor in this flight configuration."""
    return [(name, cfgs[configid][1]) for name, cfgs in info["mounts"] if configid in cfgs]


def find_mount(helper, rocket, name: str):
    """The motor-mount component object for an XML name (exact, then whitespace/case-insensitive)."""
    comps = helper.get_all_components(rocket)
    for c in comps:
        if str(c.getName()) == name:
            return c
    key = " ".join(name.split()).lower()
    loose = [c for c in comps if " ".join(str(c.getName()).split()).lower() == key]
    if len(loose) == 1:
        return loose[0]
    raise ValueError(f"motor mount {name!r} not found (or ambiguous) in the loaded rocket")


def config_motor_entries(fcfg: dict):
    """Every motor path mentioned in a file's config (for change detection)."""
    out = []
    for level in [fcfg] + list(fcfg.get("simulations", {}).values()):
        for key in ("motor", "default_motor"):
            if level.get(key):
                out.append(level[key])
        for key in ("motors", "default_motors"):
            out.extend(v for v in (level.get(key) or {}).values() if v)
    return [e["path"] if isinstance(e, dict) else e for e in out]


def motor_file_designations(path: Path):
    """Designations defined in a .rse / .eng thrust-curve file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if path.suffix.lower() == ".rse":
        return re.findall(r'\bcode="([^"]*)"', text)
    if path.suffix.lower() == ".eng":
        out = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(";"):
                continue
            parts = line.split()
            if len(parts) >= 7:  # header: name dia len delays propW totW mfg
                out.append(parts[0])
        return out
    return []


def _mount_lookup(table: dict, mount: str):
    if not table:
        return None
    if mount in table:
        return table[mount]
    key = " ".join(mount.split()).lower()
    for k, v in table.items():
        if " ".join(k.split()).lower() == key:
            return v
    return None


def resolve_motor(cfg: Config, snap: Snapshot, root: Path, ork_rel: str, fcfg: dict, scfg: dict,
                  mount: str, n_mounts: int, file_desig: str, file_resolved: bool):
    """Motor for ONE mount of one simulation.
    -> (source, repo-relative motor path or None, designation hint or None)"""
    candidates = [("config", _mount_lookup(scfg.get("motors"), mount)),
                  ("config-default", _mount_lookup(fcfg.get("default_motors"), mount))]
    if n_mounts <= 1:  # the single-mount shorthand keys
        candidates += [("config", scfg.get("motor")), ("config-default", fcfg.get("default_motor"))]
    for source, entry in candidates:
        if entry:
            rel = cfg.to_repo_rel(entry["path"] if isinstance(entry, dict) else entry, root)
            desig = entry.get("designation") if isinstance(entry, dict) else None
            return source, rel, desig
    if file_resolved:
        return "file", None, None
    if file_desig:
        # Search the .ork's own folder (and subfolders such as "Thrust Curves/") first, then the
        # project folder above it (for layouts like LUMINA/OpenRocket/x.ork + LUMINA/Engine Files/),
        # never above the config folder; ignored paths are skipped.
        cfg_root_rel = rel_posix(cfg.dir, root)
        cfg_root_rel = "" if cfg_root_rel == "." else cfg_root_rel
        folder = PurePosixPath(ork_rel).parent
        search = []
        for _ in range(2):
            f = folder.as_posix()
            f = "" if f == "." else f
            if f == cfg_root_rel or not f.startswith(cfg_root_rel):
                break
            search.append(f)
            folder = folder.parent
        for f in search:
            for rel in snap.walk(f):
                if not rel.lower().endswith((".rse", ".eng")):
                    continue
                if cfg.is_ignored(rel_posix(root / rel, cfg.dir)):
                    continue
                p = snap.path(rel)
                if p and any(d.strip().lower() == file_desig.lower() for d in motor_file_designations(p)):
                    return "auto", rel, file_desig
    return "unresolved", None, file_desig or None


# ----------------------------------------------------------------------------
# simulation
# ----------------------------------------------------------------------------
def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else math.nan


def derived_metrics(helper, sim, summary: dict) -> dict:
    """Max dynamic pressure (kPa) and a speed-windowed min/max stability, from the time series."""
    import numpy as np
    from orlab import FlightDataType as F
    out = {"max_dynamic_pressure_kpa": math.nan, "min_stability_cal": math.nan, "max_stability_cal": math.nan}
    try:
        ts = helper.get_timeseries(sim, [F.TYPE_TIME, F.TYPE_AIR_PRESSURE, F.TYPE_AIR_TEMPERATURE,
                                         F.TYPE_VELOCITY_TOTAL, F.TYPE_STABILITY])
        t = np.asarray(ts[F.TYPE_TIME], float)
        p = np.asarray(ts[F.TYPE_AIR_PRESSURE], float)
        T = np.asarray(ts[F.TYPE_AIR_TEMPERATURE], float)
        v = np.asarray(ts[F.TYPE_VELOCITY_TOTAL], float)
        stab = np.asarray(ts[F.TYPE_STABILITY], float)
        with np.errstate(invalid="ignore", divide="ignore"):
            q = 0.5 * (p / (R_AIR * T)) * v * v
        if q.size and not np.isnan(q).all():
            out["max_dynamic_pressure_kpa"] = float(np.nanmax(q)) / 1000.0
        events = helper.get_events(sim)
        from orlab import FlightEvent as E
        t_rod = events.get(E.LAUNCHROD, [None])[0]
        t_apo = events.get(E.APOGEE, [None])[0]
        if t_apo is None and t.size:
            t_apo = float(t[-1])
        if t_rod is not None and t_apo is not None:
            mask = (t >= t_rod) & (t <= t_apo) & (v >= STABILITY_MIN_SPEED_MS) & ~np.isnan(stab)
            if mask.any():
                out["min_stability_cal"] = float(stab[mask].min())
                out["max_stability_cal"] = float(stab[mask].max())
    except Exception:
        pass
    return out


# Flight variables that are constant or bookkeeping; left out of the website's flight plots.
_SERIES_SKIP = {"computation_time", "time_step", "reference_area", "reference_length"}
SERIES_DESCENT_STRIDE = 10   # ascent keeps every sample; after apogee every Nth (plus event samples)


def capture_flight_series(helper, sim, length_m: float, ref_d: float) -> dict:
    """Every OpenRocket flight variable of the run just made, for the website's flight plots.
    Full resolution from launch to apogee; thinned under parachute, where nothing changes fast.
    -> {'vars': {key: {'label', 'unit'}}, 'cols': {key: [floats|None]}, 'events': {NAME: [t, ...]}}"""
    import numpy as np
    from orlab import FlightDataType as F
    cols, meta = {}, {}
    for t in F:
        key = t.name[5:].lower() if t.name.startswith("TYPE_") else t.name.lower()
        if key in _SERIES_SKIP:
            continue
        try:
            jt = helper.translate_flight_data_type(t)
            a = np.asarray(helper.get_timeseries(sim, [t])[t], dtype=float)
        except Exception:
            continue
        if a.size == 0 or np.isnan(a).all():
            continue
        try:
            unit = str(jt.getUnitGroup().getSIUnit().getUnit()).replace("\u200b", "").strip()
        except Exception:
            unit = ""
        cols[key] = a
        meta[key] = {"label": str(jt.getName()), "unit": unit}
    if "time" not in cols:
        return {}
    if all(k in cols for k in ("air_pressure", "air_temperature", "velocity_total")):
        with np.errstate(invalid="ignore", divide="ignore"):
            cols["dynamic_pressure"] = 0.5 * (cols["air_pressure"] / (R_AIR * cols["air_temperature"])) * cols["velocity_total"] ** 2
        meta["dynamic_pressure"] = {"label": "Dynamic pressure", "unit": "Pa"}
    if "stability" in cols:
        meta["stability"] = {"label": "Stability margin", "unit": "cal"}
        if length_m and ref_d and not (math.isnan(length_m) or math.isnan(ref_d)):
            cols["stability_pct_length"] = cols["stability"] * ref_d / length_m * 100.0
            meta["stability_pct_length"] = {"label": "Stability margin, % of length", "unit": "% L"}
    events = {}
    try:
        for ev, times in helper.get_events(sim).items():
            events[getattr(ev, "name", str(ev))] = [round(float(x), 4) for x in times]
    except Exception:
        pass
    t = cols["time"]
    t_apogee = events.get("APOGEE", [float(t[-1])])[0]
    keep = set(np.where(t <= t_apogee)[0].tolist())
    keep.update(np.where(t > t_apogee)[0][::SERIES_DESCENT_STRIDE].tolist())
    for times in events.values():                      # never drop the sample an event sits on
        for te in times:
            keep.add(int(np.argmin(np.abs(t - te))))
    keep.add(len(t) - 1)
    idx = np.array(sorted(keep))

    def clean(a):
        return [None if (x != x or x in (float("inf"), float("-inf"))) else float(f"{x:.6g}") for x in a[idx].tolist()]

    return {"vars": meta, "cols": {k: clean(v) for k, v in cols.items()}, "events": events}


def check_limits(metrics: dict, limits: dict, units: str = "metric"):
    """Limits are written in the config's display units; metrics are SI."""
    out = []
    for metric, spec in limits.items():
        val = metrics.get(metric, math.nan)
        if math.isnan(val):
            continue
        _, _, unit, dec, factor = spec_for(metric, units)
        shown = val * factor
        u = f" {unit}" if unit else ""
        if "min" in spec and shown < float(spec["min"]):
            out.append(f"{metric} = {shown:.{dec}f}{u} < min {spec['min']}")
        if "max" in spec and shown > float(spec["max"]):
            out.append(f"{metric} = {shown:.{dec}f}{u} > max {spec['max']}")
    return out


def run_ork(helper, cfg: Config, snap: Snapshot, root: Path, ork_rel: str, seed: int, log=print, fallback=None,
            cfg_key: str = None, capture_series: bool = False):
    """Run every simulation in one .ork (as found in `snap`). Returns a list of record dicts.
    `fallback` is a Snapshot to take motor files from when `snap` lacks them (a base commit
    that predates the thrust-curve file). `cfg_key` overrides the config-relative path used
    to look the file up in sim_config.json (a historical version under an older filename)."""
    records = []
    path = snap.path(ork_rel)
    base_rec = {"file": ork_rel, "snapshot": snap.label}
    if path is None:
        return [{**base_rec, "status": "MISSING", "sim_name": "", "sim_index": -1, "metrics": {}, "notes": "file absent"}]
    try:
        info = parse_ork(path)
        doc = helper.load_doc(str(path))
    except Exception as e:
        return [{**base_rec, "status": "LOAD_ERROR", "sim_name": "", "sim_index": -1, "metrics": {},
                 "notes": str(e)[:300]}]
    n = int(doc.getSimulationCount())
    if n == 0:
        return [{**base_rec, "status": "NO_SIMULATIONS", "sim_name": "", "sim_index": -1, "metrics": {},
                 "notes": "no simulation saved in the file; add one in the GUI"}]
    cfg_rel = cfg_key or rel_posix(root / ork_rel, cfg.dir)
    fcfg = cfg.file_cfg(cfg_rel)
    # motor_variants: {"label": <motor path> | {"<mount>": <motor path>}} at file or simulation
    # level -> every simulation runs once per variant, reported as "<sim> [label]"
    for i in range(n):
        sim = doc.getSimulation(i)
        sim_name = str(sim.getName())
        configid = info["sims"][i][1] if i < len(info["sims"]) else ""
        scfg = cfg.sim_cfg(fcfg, sim_name)
        variants = scfg.get("motor_variants") or fcfg.get("motor_variants") or {None: None}
        for vlabel, voverride in variants.items():
            records.append(_run_one(helper, cfg, snap, root, ork_rel, base_rec, info, fcfg, scfg, sim, i,
                                    sim_name, configid, vlabel, voverride, seed, log, fallback, capture_series))
    return records


def _run_one(helper, cfg, snap, root, ork_rel, base_rec, info, fcfg, scfg, sim, i, sim_name, configid,
             vlabel, voverride, seed, log, fallback, capture_series=False):
    """Resolve motors, run one simulation (one motor variant), return its record.
    With capture_series the record also carries the full flight time series under 'series'."""
    label = sim_name if vlabel is None else f"{sim_name} [{vlabel}]"
    rec = {**base_rec, "sim_index": i, "sim_name": label, "status": "OK", "metrics": {}, "notes": "",
           "motor_source": "", "motor_file": "", "motor": "", "motors": [], "warnings": "", "violations": []}
    try:
        mounts = mounts_for(info, configid) or [(None, "")]
        notes = []
        for mount, file_desig in mounts:
            mount_obj = find_mount(helper, sim.getRocket(), mount) if mount else None
            try:
                own = helper.get_motor(sim, mount=mount_obj)
            except Exception:
                own = None
            override = _mount_lookup(voverride, mount or "") if isinstance(voverride, dict) else voverride
            if override:
                source, motor_rel, desig = f"variant {vlabel}", cfg.to_repo_rel(override, root), None
            else:
                source, motor_rel, desig = resolve_motor(cfg, snap, root, ork_rel, fcfg, scfg, mount or "",
                                                         len(mounts), file_desig, own is not None)
            entry = {"mount": mount or "", "source": source, "file": motor_rel or "", "designation": own or ""}
            if motor_rel:
                mpath = snap.path(motor_rel)
                if mpath is None and fallback is not None and fallback.path(motor_rel) is not None:
                    mpath = fallback.path(motor_rel)
                    notes.append(f"motor {motor_rel} absent in {snap.label}; used {fallback.label} copy")
                if mpath is None:
                    raise FileNotFoundError(f"motor file {motor_rel} not found in {snap.label}")
                if desig is None and len(motor_file_designations(mpath)) > 1:
                    desig = file_desig or None
                helper.set_motor(sim, str(mpath), mount=mount_obj, designation=desig)
                entry["designation"] = helper.get_motor(sim, mount=mount_obj) or ""
            if source == "unresolved":
                notes.append(f"MOTOR UNRESOLVED on mount {mount or '(default)'!r} "
                             f"(file wants {file_desig or '?'}): add it to sim_config.json; results are meaningless")
            rec["motors"].append(entry)
        rec["motor"] = " + ".join(m["designation"] or "none" for m in rec["motors"])
        rec["motor_source"] = ", ".join(sorted({m["source"] for m in rec["motors"]}))
        rec["motor_file"] = ", ".join(m["file"] for m in rec["motors"] if m["file"])
        rec["notes"] = "; ".join(notes)
        if cfg.deterministic_wind:
            # OpenRocket 24.12 draws turbulence entropy outside the seed: two loads of the
            # same file differ by ~0.1-0.5 cal in stability off the rod. Zero turbulence
            # (average wind kept) makes before/after bit-identical for an unchanged design.
            sim.getOptions().setWindTurbulenceIntensity(0.0)
        sim.getOptions().setRandomSeed(int(seed))
        t0 = time.time()
        helper.run_simulation(sim, randomize_seed=False)
        summary = helper.get_summary(sim).to_dict()
        summary["min_stability_cal_raw"] = summary.get("min_stability_cal")
        summary["max_stability_cal_raw"] = summary.get("max_stability_cal")
        summary.update(derived_metrics(helper, sim, summary))
        # stability as % of rocket length: calibers x (reference diameter / overall length) x 100
        try:
            fc = sim.getActiveConfiguration()
            length_m, ref_d = float(fc.getLength()), float(fc.getReferenceLength())
        except Exception:
            length_m = ref_d = math.nan
        summary["rocket_length_m"], summary["reference_diameter_m"] = length_m, ref_d
        for cal_key, pct_key in STABILITY_PAIRS.items():
            v = _num(summary.get(cal_key))
            summary[pct_key] = v * ref_d / length_m * 100.0 if length_m and not math.isnan(v) else math.nan
        rec["metrics"] = {k: _num(summary.get(k)) for k in METRIC_KEYS}
        rec["warnings"] = "; ".join(str(w) for w in (summary.get("warnings") or ()))[:400]
        rec["violations"] = check_limits(rec["metrics"], cfg.limits_for(fcfg, scfg), cfg.units)
        if capture_series and "unresolved" not in rec["motor_source"]:
            rec["series"] = capture_flight_series(helper, sim, length_m, ref_d)
        log(f"  [{snap.label}] {ork_rel} :: {label} ({rec['motor'] or 'no motor'}, {rec['motor_source']}) "
            f"apogee={rec['metrics']['apogee']:.0f} m  {time.time()-t0:.1f}s")
    except Exception as e:
        rec["status"] = "SIM_ERROR"
        rec["notes"] = f"{type(e).__name__}: {str(e)[:300]}"
        log(f"  [{snap.label}] {ork_rel} :: {label} FAILED: {rec['notes']}")
    return rec


def open_jvm(jar):
    import orlab
    if jar is None:
        try:
            jar = str(orlab.fetch_jar())  # cached copy or verified download of orlab's default version
        except Exception as e:
            raise SystemExit(f"no OpenRocket jar: pass --jar or run `python -m orlab fetch` ({e})")
    return orlab, jar


# ----------------------------------------------------------------------------
# reporting
# ----------------------------------------------------------------------------
def fmt(v, dec):
    return "–" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.{dec}f}"


def fmt_delta(a, b, dec):
    if a is None or b is None or math.isnan(a) or math.isnan(b):
        return "–"
    d = b - a
    pct = f" ({d / a * 100:+.1f}%)" if a not in (0.0,) and abs(a) > 1e-9 else ""
    return f"{d:+.{dec}f}{pct}"


def write_csv(records, path: Path):
    fields = ["snapshot", "file", "sim_index", "sim_name", "status", "motor", "motor_source", "motor_file"] + \
             METRIC_KEYS + ["violations", "warnings", "notes"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in records:
            w.writerow({**r, **r.get("metrics", {}), "violations": "; ".join(r.get("violations", []))})


def pair_key(r):
    return (r["file"], r["sim_name"] or f"#{r['sim_index']}")


def render_report(head_recs, base_recs, base_label, head_label, changed_motor_files, deleted, strict,
                  deterministic_wind=True, units="metric", stability="cal", changes=None) -> tuple:
    """-> (markdown, n_violations, n_unresolved). `changes`: {file: {'record', 'narration'}} from ork_diff."""
    P = primary_specs(units, stability)
    ALL = metric_specs(units)
    ap_key, _, ap_unit, ap_dec, ap_f = spec_for("apogee", units)
    st_key, _, st_unit, st_dec, _ = next(s for s in P if s[0].startswith("stability_off_rod"))
    lines = [f"## OpenRocket simulation check", ""]
    if base_label:
        lines.append(f"**{base_label}** → **{head_label}**")
    else:
        lines.append(f"**{head_label}** (no base commit to compare against)")
    lines.append("")
    shown_changes = {f: c for f, c in (changes or {}).items() if not c["record"]["empty"]}
    if shown_changes:
        lines.append("### What changed")
        for f, c in shown_changes.items():
            lines.append(f"**{PurePosixPath(f).name}** \u00b7 {c['record']['headline']}")
            if c.get("narration"):
                lines.append("")
                lines.append("> " + c["narration"].replace("\n", "\n> "))
                lines.append("")
            bullets = c["record"]["lines"][units]
            lines.extend("- " + b for b in bullets[:8])
            if len(bullets) > 8:
                lines.append(f"- \u2026 and {len(bullets) - 8} more (full list below)")
            lines.append("")
    for f, c in (changes or {}).items():
        if c["record"]["empty"]:
            lines.append(f"**{PurePosixPath(f).name}**: no design changes in the file (only stored results or metadata).")
            lines.append("")

    base_by = {pair_key(r): r for r in (base_recs or [])}
    n_viol = n_unres = 0
    summary_rows = []
    detail = []
    for r in head_recs:
        b = base_by.get(pair_key(r))
        sim = r["sim_name"] or f"#{r['sim_index']}"
        flags = []
        if r["status"] != "OK":
            flags.append(f"❌ {r['status']}: {r.get('notes', '')}")
        if "unresolved" in (r.get("motor_source") or ""):
            n_unres += 1
            flags.append("⚠️ motor unresolved")
        if r.get("notes") and r["status"] == "OK" and "UNRESOLVED" not in r["notes"]:
            flags.append("ℹ️ " + r["notes"])
        if r.get("violations"):
            n_viol += len(r["violations"])
            flags.extend("❌ " + v for v in r["violations"])
        m = r["metrics"] or {}
        ap = m.get("apogee", math.nan) * ap_f
        ap_b = b["metrics"].get("apogee", math.nan) * ap_f if b and b.get("metrics") else None
        cells = [fmt(m.get(k, math.nan) * f, dec) for k, _, _, dec, f in P[1:]]
        summary_rows.append(
            f"| `{r['file']}` | {sim} | {r.get('motor') or '–'} ({r.get('motor_source', '')}) | "
            f"{fmt(ap, ap_dec)} | {fmt_delta(ap_b, ap, ap_dec) if ap_b is not None else ('new' if base_label else '–')} | "
            + " | ".join(cells) + f" | {'; '.join(flags) if flags else '✅'} |")
        # detail table
        detail.append(f"<details><summary><code>{r['file']}</code> · {sim} · motor {r.get('motor') or '–'}"
                      f" ({r.get('motor_source', '')}{', ' + r['motor_file'] if r.get('motor_file') else ''})</summary>")
        detail.append("")
        if b is None and base_label:
            detail.append("_New in this range (no base version)._")
        detail.append("| Metric | Before | After | Δ |" if base_label else "| Metric | Value |")
        detail.append("|---|---:|---:|---:|" if base_label else "|---|---:|")
        for key, label, unit, dec, f in ALL:
            hv = (r["metrics"].get(key, math.nan) if r["metrics"] else math.nan) * f
            u = f" ({unit})" if unit else ""
            if base_label:
                bv = (b["metrics"].get(key, math.nan) if b and b.get("metrics") else math.nan) * f
                detail.append(f"| {label}{u} | {fmt(bv, dec)} | {fmt(hv, dec)} | {fmt_delta(bv, hv, dec)} |")
            else:
                detail.append(f"| {label}{u} | {fmt(hv, dec)} |")
        if r.get("warnings"):
            detail.append("")
            detail.append(f"OpenRocket warnings: {r['warnings']}")
        detail.append("")
        detail.append("</details>")
        detail.append("")

    # Digest first: one short line per simulation. Chat integrations (the Discord GitHub bot)
    # show only the first few hundred characters of a commit comment and cannot draw tables.
    for r in head_recs:
        b = base_by.get(pair_key(r))
        m = r["metrics"] or {}
        sim = r["sim_name"] or f"#{r['sim_index']}"
        ap = m.get("apogee", math.nan) * ap_f
        ap_b = b["metrics"].get("apogee", math.nan) * ap_f if b and b.get("metrics") else None
        if r["status"] != "OK":
            status = f"❌ {r['status']}"
        elif "unresolved" in (r.get("motor_source") or ""):
            status = "⚠️ motor unresolved"
        elif r.get("violations"):
            status = "❌ " + "; ".join(r["violations"])
        else:
            status = "✅"
        if ap_b is None:
            d = ", new" if base_label else ""
        elif not (math.isnan(ap) or math.isnan(ap_b)) and abs(ap - ap_b) < 0.5:
            d = ", unchanged"
        else:
            d = f", Δ {fmt_delta(ap_b, ap, ap_dec)}"
        lines.append(f"- **{PurePosixPath(r['file']).name}** · {sim} · {status} · apogee {fmt(ap, ap_dec)} {ap_unit}{d}"
                     + f" · Mach {fmt(m.get('max_mach', math.nan), 2)}"
                     + f" · rail {fmt(m.get(st_key, math.nan), st_dec)} {st_unit}")
    lines.append("")
    heads = [f"{label} ({unit})" if unit else label for _, label, unit, _, _ in P[1:]]
    lines.append(f"| File | Simulation | Motor | Apogee ({ap_unit}) | Δ apogee | " + " | ".join(heads) + " | Status |")
    lines.append("|---|---|---|---:|---:|" + "---:|" * len(heads) + "---|")
    lines.extend(summary_rows)
    lines.append("")
    wind = ("wind turbulence set to 0 for reproducibility (average wind kept)" if deterministic_wind
            else "wind turbulence as saved in the file (results vary run to run)")
    lines.append(f"Same random seed before and after; {wind}.")
    if changed_motor_files:
        lines.append(f"Motor files changed in this range: {', '.join(f'`{m}`' for m in changed_motor_files)}.")
    for d in deleted:
        lines.append(f"Deleted: `{d}`")
    lines.append("")
    lines.extend(detail)
    for f, c in shown_changes.items():
        rows = c["record"]["rows"][units]
        lines.append(f"<details><summary>All changed fields \u00b7 <code>{f}</code> ({len(rows)})</summary>")
        lines.append("")
        lines.append("| Component | Field | Before | After |")
        lines.append("|---|---|---|---|")
        lines.extend(f"| {w} | {what} | {a} | {b} |" for w, what, a, b in rows[:400])
        lines.append("")
        lines.append("</details>")
        lines.append("")
    if n_viol or n_unres:
        lines.append(f"**{n_viol} limit violation(s), {n_unres} unresolved motor(s).**"
                     + (" This check is configured to fail on limits." if strict else
                        " Informational only (set `fail_on_limits` in sim_config.json to make this fail)."))
    return "\n".join(lines) + "\n", n_viol, n_unres


# ----------------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------
# design changelog: what changed in the .ork, in words (ork_diff), optionally narrated by an AI
# ----------------------------------------------------------------------------
def change_record(old_path, new_path) -> dict:
    """What changed between two versions of a design, pre-rendered in both unit systems."""
    d = ork_diff.diff_files(old_path, new_path)
    return {"empty": ork_diff.is_empty(d), "headline": ork_diff.headline(d),
            "lines": {u: ork_diff.describe(d, u) for u in UNIT_SYSTEMS},
            "rows": {u: [list(r) for r in ork_diff.rows(d, u)] for u in UNIT_SYSTEMS}}


def narration_key(old_blob, new_blob) -> str:
    return f"{(old_blob or 'new')[:12]}-{(new_blob or 'none')[:12]}"


def read_narration(cache_dir, key: str):
    f = Path(cache_dir) / f"narration-{key}.md" if cache_dir else None
    return f.read_text(encoding="utf-8").strip() or None if f and f.is_file() else None


def clean_narration(text: str) -> str:
    """Model output -> one tidy paragraph block: no code fences, no headings, bounded length."""
    lines = [ln for ln in text.strip().splitlines() if not ln.strip().startswith("```")]
    lines = [ln.lstrip("# ").rstrip() if ln.lstrip().startswith("#") else ln.rstrip() for ln in lines]
    out = "\n".join(lines).strip()
    return out[:1500].rsplit(" ", 1)[0] + "\u2026" if len(out) > 1500 else out


def performance_lines(before: list, after: list, units: str, stability: str) -> list:
    """'<sim>: apogee A -> B (delta) ...' for each simulation present in both record lists."""
    by = {r["sim_name"]: r for r in before if r.get("status") == "OK"}
    out = []
    for r in after:
        b = by.get(r["sim_name"])
        if r.get("status") != "OK" or b is None:
            continue
        parts = []
        for key, label, unit, dec, f in primary_specs(units, stability):
            x, y = (b["metrics"] or {}).get(key, math.nan) * f, (r["metrics"] or {}).get(key, math.nan) * f
            if not (math.isnan(x) or math.isnan(y)):
                parts.append(f"{label} {fmt(x, dec)} -> {fmt(y, dec)}{(' ' + unit) if unit else ''} ({fmt_delta(x, y, dec)})")
        out.append(f"{r['sim_name']}: " + "; ".join(parts))
    return out


def narration_prompt(file: str, record: dict, units: str, perf: list, commit_line: str, out_name: str) -> str:
    rows = record["rows"][units]
    table = "\n".join(f"- {w} | {what} | {a} -> {b}" for w, what, a, b in rows[:160])
    if len(rows) > 160:
        table += f"\n- ... {len(rows) - 160} more field changes not shown"
    return f"""You are writing one entry of an engineering changelog for a rocket design file, for the team that flies it.

Design file: {file}
Commit: {commit_line}
Summary of the structural diff: {record['headline']}

Changes, already grouped per component:
{chr(10).join('- ' + ln.replace('**', '') for ln in record['lines'][units])}

Every changed field (component path | field | before -> after):
{table}

Simulated flight performance before -> after this change:
{chr(10).join('- ' + ln for ln in perf) if perf else '- (not available)'}

Write the entry as 2 to 5 plain sentences, at most 90 words, no heading, no bullet list, no code fence.
Lead with the most consequential change. Group related edits by subsystem (nose and payload, recovery,
avionics, airframe, fins, propulsion, simulation settings). A component removed and another of the same kind
and similar name added in the same place is a replacement or resize, so say that instead of listing both.
State the effect on performance using only the numbers above. Do not guess at intent, do not invent numbers,
and do not mention anything that is not in the data above.

Save the entry, and nothing else, to the file {out_name} in the current directory. Do not run any commands.
"""


def write_pending_prompt(pending_dir: Path, key: str, prompt: str):
    pending_dir.mkdir(parents=True, exist_ok=True)
    (pending_dir / f"{key}.prompt.md").write_text(prompt, encoding="utf-8")


def discover_orks(cfg: Config, root: Path):
    out = []
    for p in sorted(cfg.dir.rglob("*.ork")):
        cfg_rel = rel_posix(p, cfg.dir)
        if not cfg.is_ignored(cfg_rel):
            out.append(rel_posix(p, root))
    return out


def resolve_base(root: Path, ref: str):
    """A usable base commit, or None. Falls back to HEAD~1 for empty / all-zero / unknown refs."""
    candidates = []
    if ref and set(ref) != {"0"}:
        candidates.append(ref)
    candidates.append("HEAD~1")
    for c in candidates:
        r = _git(root, "rev-parse", "--verify", "--quiet", f"{c}^{{commit}}", check=False)
        if r.returncode == 0:
            return r.stdout.strip()
    return None


def changed_files(root: Path, base: str):
    r = _git(root, "diff", "--name-only", "--diff-filter=ACMR", base, "HEAD", "--")
    head = [p for p in r.stdout.split("\n") if p]
    # uncommitted changes in the working tree count too (local use)
    r2 = _git(root, "diff", "--name-only", "HEAD", "--")
    head += [p for p in r2.stdout.split("\n") if p]
    r3 = _git(root, "diff", "--name-only", "--diff-filter=D", base, "HEAD", "--")
    deleted = [p for p in r3.stdout.split("\n") if p]
    return sorted(set(head)), deleted


def cmd_run(args):
    cfg = Config(Path(args.config))
    root = repo_root(cfg.dir)
    snap = Snapshot(root)
    files = [rel_posix(Path(f), root) for f in args.files] or discover_orks(cfg, root)
    if not files:
        raise SystemExit("no .ork files to run")
    orlab, jar = open_jvm(args.jar)
    print(f"Running {len(files)} file(s) with OpenRocket jar {jar}")
    records = []
    with orlab.OpenRocketInstance(jar, log_level="ERROR") as inst:
        helper = orlab.Helper(inst)
        for f in files:
            records.extend(run_ork(helper, cfg, snap, root, f, cfg.seed))
    out = Path(args.results)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    write_csv(records, out / "results.csv")
    report, n_viol, n_unres = render_report(records, None, None, snap.label, [], [], args.strict or cfg.fail_on_limits,
                                            cfg.deterministic_wind, cfg.units, cfg.stability_units)
    (out / "report.md").write_text(report, encoding="utf-8")
    _emit(report, args)
    print(f"\nWrote {out / 'results.csv'}, {out / 'results.json'}, {out / 'report.md'}")
    return 2 if (args.strict or cfg.fail_on_limits) and (n_viol or n_unres) else 0


def cmd_compare(args):
    cfg = Config(Path(args.config))
    root = repo_root(cfg.dir)
    head = Snapshot(root)
    base_sha = resolve_base(root, args.base)
    base = Snapshot(root, base_sha) if base_sha else None
    head_sha = _git(root, "rev-parse", "--short", "HEAD").stdout.strip()
    base_label = _git(root, "rev-parse", "--short", base_sha).stdout.strip() if base_sha else None
    if base_sha and args.base and base_sha != _git(root, "rev-parse", args.base, check=False).stdout.strip():
        print(f"NOTE: base {args.base!r} not usable; comparing against HEAD~1 ({base_label}) instead")

    all_orks = discover_orks(cfg, root)
    deleted = []
    if args.files:
        targets = [rel_posix(Path(f), root) for f in args.files]
        motor_changed = []
    elif args.all or base is None:
        targets, motor_changed = all_orks, []
    else:
        changed, deleted_all = changed_files(root, base_sha)
        deleted = [d for d in deleted_all if d.lower().endswith(".ork")]
        changed_orks = {c for c in changed if c.lower().endswith(".ork") and c in all_orks}
        motor_changed = [c for c in changed if c.lower().endswith((".rse", ".eng"))]
        cfg_rel = rel_posix(cfg.path, root)
        # an .ork is affected if a motor file in its folder (or its configured motor) changed
        for ork in all_orks:
            folder = PurePosixPath(ork).parent.as_posix()
            fcfg = cfg.file_cfg(rel_posix(root / ork, cfg.dir))
            cfg_motors = {cfg.to_repo_rel(m, root) for m in config_motor_entries(fcfg)}
            if any(PurePosixPath(m).parent.as_posix() == folder or m in cfg_motors for m in motor_changed):
                changed_orks.add(ork)
        if cfg_rel in changed:
            changed_orks |= set(all_orks)
        targets = sorted(changed_orks)
    Path(args.results).mkdir(parents=True, exist_ok=True)
    (Path(args.results) / "targets.txt").write_text("\n".join(targets) + ("\n" if targets else ""), encoding="utf-8")
    if not targets and not deleted:
        msg = "No .ork files changed in this range; nothing to simulate."
        print(msg)
        _emit(f"## OpenRocket simulation check\n\n{msg}\n", args)
        (Path(args.results) / "report.md").write_text(msg + "\n", encoding="utf-8")
        return 0

    orlab, jar = open_jvm(args.jar)
    print(f"Comparing {len(targets)} file(s): base={base_label or 'none'} head={head_sha}; jar {jar}")
    head_recs, base_recs = [], []
    with orlab.OpenRocketInstance(jar, log_level="ERROR") as inst:
        helper = orlab.Helper(inst)
        for f in targets:
            if base is not None and base.exists(f):
                base_recs.extend(run_ork(helper, cfg, base, root, f, cfg.seed, fallback=head))
            head_recs.extend(run_ork(helper, cfg, head, root, f, cfg.seed))
    strict = args.strict or cfg.fail_on_limits
    out = Path(args.results)
    out.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache).resolve() if getattr(args, "cache", None) else None
    commit_line = _git(root, "log", "-1", "--format=%h %an: %s", check=False).stdout.strip()
    changes = {}
    for f in targets:
        if base is None or not base.exists(f) or head.path(f) is None:
            continue
        old_blob, new_blob = _blob(root, base_sha, f), _hash_file(head.path(f))
        if old_blob == new_blob:                     # re-simulated for another reason (config or motor change)
            continue
        try:
            record = change_record(base.path(f), head.path(f))
        except Exception as e:                       # a diff problem must never sink the simulation check
            print(f"  could not diff {f}: {e}")
            continue
        key = narration_key(old_blob, new_blob)
        changes[f] = {"record": record, "key": key, "narration": read_narration(cache_dir, key)}
        if not record["empty"] and changes[f]["narration"] is None:
            perf = performance_lines([r for r in base_recs if r["file"] == f], [r for r in head_recs if r["file"] == f],
                                     cfg.units, cfg.stability_units)
            write_pending_prompt(out / "pending", key, narration_prompt(f, record, cfg.units, perf, commit_line, f"{key}.out.md"))
    render = {"base_label": base_label, "head_label": head_sha, "motor_changed": motor_changed, "deleted": deleted,
              "strict": strict, "deterministic_wind": cfg.deterministic_wind, "units": cfg.units,
              "stability": cfg.stability_units}
    report, n_viol, n_unres = render_report(head_recs, base_recs if base else None, base_label,
                                            head_sha, motor_changed, deleted, strict, cfg.deterministic_wind, cfg.units,
                                            cfg.stability_units, changes)
    (out / "report.md").write_text(report, encoding="utf-8")
    (out / "results.json").write_text(json.dumps({"base": base_sha, "head": head_sha, "before": base_recs,
                                                  "after": head_recs, "changes": changes, "render": render},
                                                 indent=2), encoding="utf-8")
    write_csv(base_recs + head_recs, out / "results.csv")
    _emit(report, args)
    print(f"\nWrote {out / 'report.md'}, {out / 'results.csv'}, {out / 'results.json'}")
    if strict and (n_viol or n_unres):
        print(f"FAIL: {n_viol} limit violation(s), {n_unres} unresolved motor(s)")
        return 2
    return 0


def cmd_narrate(args):
    """Collect `<key>.out.md` files written by the narrator (Copilot CLI in CI), store them in the cache,
    and re-render report.md with them. Safe to run when there is nothing to merge."""
    out = Path(args.results)
    cache_dir = Path(args.cache).resolve() if args.cache else None
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
    fresh = {}
    for d in [out / "pending"] + [Path(x) for x in (args.pending or [])]:
        for f in sorted(d.glob("*.out.md")) if d.is_dir() else []:
            text = clean_narration(f.read_text(encoding="utf-8", errors="replace"))
            if text:
                fresh[f.name[:-len(".out.md")]] = text
                if cache_dir:
                    (cache_dir / f"narration-{f.name[:-len('.out.md')]}.md").write_text(text + "\n", encoding="utf-8")
    print(f"narrate: {len(fresh)} new narration(s)")
    res_file = out / "results.json"
    if not res_file.is_file():
        return 0
    data = json.loads(res_file.read_text(encoding="utf-8"))
    if "render" not in data:
        return 0
    for c in data.get("changes", {}).values():
        c["narration"] = fresh.get(c["key"]) or c.get("narration") or read_narration(cache_dir, c["key"])
    r = data["render"]
    report, _, _ = render_report(data["after"], data["before"] if r["base_label"] else None, r["base_label"], r["head_label"],
                                 r["motor_changed"], r["deleted"], r["strict"], r["deterministic_wind"], r["units"],
                                 r["stability"], data.get("changes"))
    (out / "report.md").write_text(report, encoding="utf-8")
    res_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _emit(report, args)
    return 0


def _emit(report: str, args):
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write(report)
    if not args.quiet:
        print("\n" + report)


# ----------------------------------------------------------------------------
# history: every committed version of a design, as Mermaid bar charts
# ----------------------------------------------------------------------------
def git_file_history(root: Path, rel: str, max_commits: int):
    """Commits (oldest first) that touched `rel`, following renames along the first-parent line.
    Each entry: sha, short, time, author, message, path (the file's name AT that commit)."""
    r = _git(root, "log", "--first-parent", "--follow", f"--max-count={max_commits}",
             "--format=__C__%H%x1f%ct%x1f%h%x1f%an%x1f%s", "--name-only", "--", rel, check=False)
    entries, cur = [], None
    for line in r.stdout.splitlines():
        if line.startswith("__C__"):
            sha, ct, short, author, msg = line[5:].split("\x1f", 4)
            cur = {"sha": sha, "short": short, "time": int(ct), "author": author, "message": msg, "path": None}
            entries.append(cur)
        elif line.strip() and cur is not None and cur["path"] is None:
            cur["path"] = line.strip()
    return [e for e in entries if e["path"]][::-1]


def _blob(root: Path, sha: str, rel: str):
    r = _git(root, "rev-parse", f"{sha}:{rel}", check=False)
    return r.stdout.strip() if r.returncode == 0 else None


def _hash_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha1(b"blob %d\0" % path.stat().st_size)
    h.update(path.read_bytes())
    return h.hexdigest()  # == git's blob id for the working-tree content


def _mermaid_label(s: str) -> str:
    return '"' + s.replace('"', "'") + '"'


def render_history_md(series: dict, max_points: int, units: str = "metric", stability: str = "cal") -> str:
    """series: {(file, sim): [{'label', 'short', 'date', 'author', 'message', 'status', 'metrics', 'note'} ...]}"""
    P = primary_specs(units, stability)
    _, _, _, ap_dec, ap_f = spec_for("apogee", units)
    out = ["## Performance history", ""]
    for (file, sim), rows in series.items():
        ok = [r for r in rows if r["status"] == "OK" and not math.isnan(r["metrics"].get("apogee", math.nan))]
        out.append(f"### `{file}` · {sim}")
        out.append("")
        if not ok:
            out.append("_No successful simulation in this file's history._")
            out.append("")
            continue
        shown = ok[-(max_points + 1):]  # one extra so the first charted version has a "previous"
        if len(shown) < 2:
            out.append("_Only one version so far; deltas start with the next commit._")
            out.append("")
        else:
            out.append("Each bar is the change from the previous committed version: 🟩 increase, 🟥 decrease ")
            out.append("")
            labels = ", ".join(_mermaid_label(r["label"]) for r in shown[1:])
            for key, label, unit, dec, f in P:
                title = CHART_TITLES.get(key, label)
                vals = [r["metrics"].get(key, math.nan) * f for r in shown]
                if all(math.isnan(v) for v in vals):
                    continue
                deltas = [0.0 if (math.isnan(a) or math.isnan(b)) else b - a for a, b in zip(vals, vals[1:])]
                # Mermaid colours per SERIES, not per bar: increases go in series 1 (green),
                # decreases in series 2 (red), each as a magnitude; the other series is 0 there.
                # Overlapping bar series share the x slot, so it reads as one coloured bar.
                up = [d if d > 0 else 0.0 for d in deltas]
                down = [-d if d < 0 else 0.0 for d in deltas]
                top = max(up + down) if any(up + down) else 1.0
                y1 = round(top * 1.15, dec) or 1.0
                fmt_series = lambda s: ", ".join(f"{v:.{dec}f}" for v in s)  # noqa: E731
                u = f" ({unit})" if unit else ""
                out.append("```mermaid")
                out.append('%%{init: {"themeVariables": {"xyChart": {"plotColorPalette": "#2da44e, #cf222e"}}}}%%')
                out.append("xychart-beta")
                out.append(f'    title "{title}"')
                out.append(f"    x-axis [{labels}]")
                out.append(f'    y-axis "|Δ|{u}" 0 --> {y1:g}')
                out.append(f"    bar [{fmt_series(up)}]")
                out.append(f"    bar [{fmt_series(down)}]")
                out.append("```")
                out.append("")
        heads = [f"{label} ({unit})" if unit else label for _, label, unit, _, _ in P]
        out.append("| Version | Date | Author | Commit | " + " | ".join(heads) + " | Δ apogee | Note |")
        out.append("|---|---|---|---|" + "---:|" * len(heads) + "---:|---|")
        prev = None
        for r in rows[-max_points:]:
            m = r["metrics"] or {}
            ap = m.get("apogee", math.nan) * ap_f
            msg = r["message"][:60] + ("…" if len(r["message"]) > 60 else "")
            delta = fmt_delta(prev, ap, ap_dec) if prev is not None and r["status"] == "OK" else "–"
            cells = " | ".join(fmt(m.get(k, math.nan) * f, dec) for k, _, _, dec, f in P)
            out.append(f"| `{r['short']}` | {r['date']} | {r['author']} | {msg} | {cells} | {delta} | "
                       f"{r['note'] or ('' if r['status'] == 'OK' else r['status'])} |")
            if r["status"] == "OK" and not math.isnan(ap):
                prev = ap
        out.append("")
    return "\n".join(out) + "\n"


def cmd_history(args):
    import datetime as dt
    cfg = Config(Path(args.config))
    root = repo_root(cfg.dir)
    head = Snapshot(root)
    files = [rel_posix(Path(f), root) for f in args.files] or discover_orks(cfg, root)
    files = [f for f in files if f]
    if not files:
        print("no .ork files to build history for")
        return 0
    cache_dir = Path(args.cache).resolve() if args.cache else None
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
    cache_salt = f"s{cfg.seed}-w{int(cfg.deterministic_wind)}-m3"  # bump the suffix when metrics change

    def salt_for(f):  # the file's config entry (motors, variants) changes the results too
        import hashlib
        entry = json.dumps(cfg.file_cfg(rel_posix(root / f, cfg.dir)), sort_keys=True)
        return cache_salt + "-" + hashlib.sha1(entry.encode()).hexdigest()[:8]

    # plan: (file, version entry, blob) for every version; look up the cache first
    plan, cached, files_versions, want_series = [], {}, {}, set()
    for f in files:
        versions = git_file_history(root, f, args.max_commits)
        for v in versions:
            v["blob"] = _blob(root, v["sha"], v["path"])
        # uncommitted working-tree version, if it differs from HEAD
        wt = head.path(f)
        if wt is not None:
            wt_blob = _hash_file(wt)
            if not versions or versions[-1]["blob"] != wt_blob:
                versions.append({"sha": None, "short": "working", "time": int(time.time()), "author": "",
                                 "message": "(uncommitted working tree)", "path": f, "blob": wt_blob})
        # flight plots on the site need the full time series of the newest two versions
        for v in (versions[-2:] if args.site else []):
            want_series.add((f, v["blob"]))
        for v in versions:
            key = (f, v["blob"])
            hit = cache_dir / f"{v['blob']}-{salt_for(f)}.json" if (cache_dir and v["blob"]) else None
            if hit and hit.is_file():
                try:
                    recs = json.loads(hit.read_text(encoding="utf-8"))
                    has_series = all("series" in r for r in recs if r.get("status") == "OK"
                                     and "unresolved" not in (r.get("motor_source") or ""))
                    if key not in want_series or has_series:
                        cached[key] = recs
                        continue
                except Exception:
                    pass
            plan.append((f, v))
        files_versions[f] = versions

    todo = plan
    print(f"History: {sum(len(v) for v in files_versions.values())} version(s) across {len(files)} file(s); "
          f"{len(todo)} to simulate, {len(cached)} from cache")
    results = dict(cached)
    if todo:
        orlab, jar = open_jvm(args.jar)
        with orlab.OpenRocketInstance(jar, log_level="ERROR") as inst:
            helper = orlab.Helper(inst)
            for f, v in todo:
                snap = head if v["sha"] is None else Snapshot(root, v["sha"])
                cfg_key = rel_posix(root / f, cfg.dir)  # config is keyed by the CURRENT name
                recs = run_ork(helper, cfg, snap, root, v["path"], cfg.seed, fallback=head, cfg_key=cfg_key,
                               capture_series=(f, v["blob"]) in want_series)
                results[(f, v["blob"])] = recs
                if cache_dir and v["blob"]:
                    (cache_dir / f"{v['blob']}-{salt_for(f)}.json").write_text(json.dumps(recs), encoding="utf-8")

    # assemble series per (file, sim name)
    series, csv_rows = {}, []
    for f, versions in files_versions.items():
        for v in versions:
            date = dt.datetime.fromtimestamp(v["time"]).strftime("%Y-%m-%d")
            for rec in results.get((f, v["blob"]), []):
                sim = rec.get("sim_name") or f"#{rec.get('sim_index')}"
                row = {"label": f"{date[5:]} {v['short']}", "short": v["short"], "date": date, "author": v["author"],
                       "message": v["message"], "status": rec["status"], "metrics": rec.get("metrics") or {},
                       "note": ("motor unresolved" if "unresolved" in (rec.get("motor_source") or "") else ""),
                       "flight": rec.get("series") if (f, v["blob"]) in want_series else None}
                series.setdefault((f, sim), []).append(row)
                csv_rows.append({"file": f, "path_at_commit": v["path"], "sim_name": sim, "commit": v["sha"] or "",
                                 "short": v["short"], "date": date, "author": v["author"], "message": v["message"],
                                 "status": rec["status"], "motor": rec.get("motor", ""),
                                 "motor_source": rec.get("motor_source", ""), **(rec.get("metrics") or {})})
    out = Path(args.results)
    out.mkdir(parents=True, exist_ok=True)

    # design changelog: one entry per version whose file content differs from the version before it
    changelog, backlog = {}, 0
    pending_dir = Path(args.pending[0]) if args.pending else out / "pending"
    for f, versions in files_versions.items():
        entries = []
        for prev, v in zip(versions, versions[1:]):
            if not prev["blob"] or not v["blob"] or prev["blob"] == v["blob"]:
                continue
            key = narration_key(prev["blob"], v["blob"])
            cached_diff = cache_dir / f"diff-{key}.json" if cache_dir else None
            try:
                if cached_diff and cached_diff.is_file():
                    record = json.loads(cached_diff.read_text(encoding="utf-8"))
                else:
                    snaps = [head if x["sha"] is None else Snapshot(root, x["sha"]) for x in (prev, v)]
                    record = change_record(snaps[0].path(prev["path"]), snaps[1].path(v["path"]))
                    if cached_diff:
                        cached_diff.write_text(json.dumps(record), encoding="utf-8")
            except Exception as e:
                print(f"  could not diff {f} {prev['short']}..{v['short']}: {e}")
                continue
            narration = read_narration(cache_dir, key)
            date = dt.datetime.fromtimestamp(v["time"]).strftime("%Y-%m-%d")
            entries.append({"short": v["short"], "sha": v["sha"] or "", "date": date, "author": v["author"],
                            "message": v["message"], "prev": prev["short"], "key": key, "narration": narration,
                            "headline": record["headline"], "empty": record["empty"], "lines": record["lines"],
                            "rows": {u: record["rows"][u][:300] for u in UNIT_SYSTEMS}})
            if narration is None and not record["empty"]:
                entries[-1]["_prompt"] = (prev, v, record)
        # prompts for the newest few un-narrated entries only, so a backfill never floods the narrator
        for e in reversed(entries):
            pv = e.pop("_prompt", None)
            if pv and backlog < args.narrate_backlog:
                prev, v, record = pv
                perf = performance_lines(results.get((f, prev["blob"]), []), results.get((f, v["blob"]), []),
                                         cfg.units, cfg.stability_units)
                write_pending_prompt(pending_dir, e["key"], narration_prompt(
                    f, record, cfg.units, perf, f"{v['short']} {v['author']}: {v['message']}", f"{e['key']}.out.md"))
                backlog += 1
        changelog[f] = list(reversed(entries))
    fields = ["file", "path_at_commit", "sim_name", "commit", "short", "date", "author", "message", "status",
              "motor", "motor_source"] + METRIC_KEYS
    with open(out / "history.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(csv_rows)
    md = render_history_md(series, args.max_points, cfg.units, cfg.stability_units)
    (out / "history.md").write_text(md, encoding="utf-8")
    _emit(md, args)
    print(f"Wrote {out / 'history.md'}, {out / 'history.csv'}")
    if args.site:
        repo_url = args.repo_url or _guess_repo_url(root)
        write_site(series, Path(args.site), cfg.units, repo_url, files_versions, cfg.stability_units, changelog)
    return 0


# ----------------------------------------------------------------------------
# static site (GitHub Pages): interactive charts of the same history
# ----------------------------------------------------------------------------
def _guess_repo_url(root: Path):
    r = _git(root, "remote", "get-url", "origin", check=False)
    url = r.stdout.strip()
    if not url:
        return ""
    m = re.match(r"(?:git@github\.com:|https://github\.com/)([^/]+/[^/]+?)(?:\.git)?$", url)
    return f"https://github.com/{m.group(1)}" if m else ""


SITE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenRocket performance history</title>
<script src="https://cdn.plot.ly/plotly-basic-2.35.2.min.js" charset="utf-8"></script>
<style>
  :root { color-scheme: light dark; --bg:#fff; --fg:#1f2328; --muted:#59636e; --card:#f6f8fa; --line:#d0d7de;
          --up:#1a7f37; --down:#cf222e; --flat:#8c959f; --accent:#0969da; --hover:#eaeef2; }
  @media (prefers-color-scheme: dark) { :root { --bg:#0d1117; --fg:#e6edf3; --muted:#9198a1; --card:#161b22; --line:#30363d;
          --up:#3fb950; --down:#f85149; --flat:#6e7681; --accent:#58a6ff; --hover:#21262d; } }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:14px/1.45 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  header { padding:12px 16px; border-bottom:1px solid var(--line); display:flex; flex-wrap:wrap; gap:6px 16px; align-items:baseline; }
  header h1 { font-size:18px; margin:0; } header .sub { color:var(--muted); font-size:13px; }
  .layout { display:grid; grid-template-columns:300px 1fr; min-height:calc(100vh - 50px); }
  nav { border-right:1px solid var(--line); background:var(--card); padding:10px 8px; overflow:auto; }
  main { padding:14px 16px 32px; min-width:0; }
  .design { margin-bottom:6px; }
  .design > .head { display:flex; align-items:center; gap:6px; padding:5px 6px; border-radius:6px; cursor:pointer; font-weight:600; }
  .design > .head:hover { background:var(--hover); }
  .design > .head .caret { width:14px; color:var(--muted); font-size:11px; transition:transform .12s; }
  .design.closed > .head .caret { transform:rotate(-90deg); }
  .design.closed > .sims { display:none; }
  .design > .head .name { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .design > .head .mini { color:var(--muted); font-size:11px; font-weight:400; }
  .sims { padding-left:18px; }
  .sim { display:flex; align-items:center; gap:7px; padding:4px 6px; border-radius:6px; cursor:pointer; }
  .sim:hover { background:var(--hover); }
  .sim input { margin:0; accent-color:var(--accent); }
  .sim .swatch { width:10px; height:10px; border-radius:3px; background:var(--line); flex:none; }
  .sim .label { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .sim .val { color:var(--muted); font-size:12px; font-variant-numeric:tabular-nums; }
  .sim .val.up { color:var(--up); } .sim .val.down { color:var(--down); }
  .navtools { display:flex; gap:6px; padding:2px 6px 10px; }
  .navtools button, .row button, .row label { font:inherit; font-size:12px; }
  select { font:inherit; font-size:13px; padding:3px 6px; border-radius:6px; border:1px solid var(--line); background:var(--bg); color:var(--fg); }
  button { font:inherit; padding:4px 10px; border-radius:6px; border:1px solid var(--line); background:var(--bg); color:var(--fg); cursor:pointer; }
  button.on { background:var(--accent); color:#fff; border-color:var(--accent); }
  .row { display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin-bottom:10px; }
  .row .spacer { flex:1; }
  .toggle { color:var(--muted); display:flex; align-items:center; gap:5px; }
  .clfile { font-size:15px; margin:22px 0 6px; border-bottom:1px solid var(--line); padding-bottom:4px; }
  .entry { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 14px; margin:10px 0; }
  .entry .ehead { color:var(--muted); font-size:12.5px; } .entry .ehl { font-weight:600; margin:4px 0; }
  .entry ul { margin:6px 0 6px 18px; padding:0; } .entry li { margin:2px 0; }
  .entry blockquote { margin:8px 0; padding:6px 12px; border-left:3px solid var(--accent); background:var(--bg); border-radius:0 6px 6px 0; }
  .entry details { margin-top:6px; } .entry summary { cursor:pointer; color:var(--muted); font-size:12.5px; }
  .entry table { margin-top:6px; font-size:12px; } .entry td, .entry th { text-align:left; white-space:normal; }
  .chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:4px; }
  .chip { font-size:12px; border:1px solid var(--line); border-radius:12px; padding:2px 9px; background:var(--bg); }
  .chip .up { color:var(--up); } .chip .down { color:var(--down); }
  .tabs { display:flex; gap:4px; }
  .tabs button { font-size:13px; padding:4px 12px; }
  .row select { max-width:260px; }
  .row .lbl { color:var(--muted); font-size:12px; margin-left:6px; }
  .chartbox.tall { height:520px; }
  .chartbox { position:relative; height:440px; background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px; }
  .empty { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; color:var(--muted); pointer-events:none; }
  .empty[hidden] { display:none; }   /* an author display rule would otherwise beat the hidden attribute */
  table { border-collapse:collapse; width:100%; margin-top:14px; font-size:13px; }
  th, td { text-align:right; padding:5px 8px; border-bottom:1px solid var(--line); white-space:nowrap; font-variant-numeric:tabular-nums; }
  th:first-child, td:first-child { text-align:left; }
  th { color:var(--muted); font-weight:600; }
  td .sw { display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:6px; vertical-align:middle; }
  td .d { color:var(--muted); font-size:11px; margin-left:4px; } td .d.up { color:var(--up); } td .d.down { color:var(--down); }
  .legend { color:var(--muted); font-size:12px; margin-top:14px; }
  a { color:var(--accent); }
  @media (max-width: 760px) { .layout { grid-template-columns:1fr; } nav { border-right:0; border-bottom:1px solid var(--line); max-height:45vh; } .chartbox { height:340px; } }
</style>
</head>
<body>
<header>
  <h1>OpenRocket performance</h1>
  <div class="tabs"><button id="tab-history">History</button><button id="tab-flight">Flight plots</button><button id="tab-changelog">Changelog</button></div>
  <span class="sub" id="sub"></span>
  <label class="sub" style="margin-left:auto">units <select id="units"><option value="metric">metric (m, m/s, kPa)</option><option value="imperial">imperial (ft, ft/s, psi)</option></select></label>
  <label class="sub">stability <select id="stab"><option value="cal">calibers</option><option value="pct">% of body length</option></select></label>
</header>
<div class="layout">
  <nav id="nav"></nav>
  <main>
   <section id="view-flight" hidden>
    <div class="row" id="fpresets"></div>
    <div class="row" id="fcontrols"></div>
    <div class="chartbox tall"><div id="fplot" style="position:absolute;inset:10px"></div><div class="empty" id="fempty" hidden>Select simulations in the list.</div></div>
    <p class="legend" id="finfo"></p>
    <p class="legend">Any flight variable against any other, from the latest committed version of each ticked simulation: full resolution through apogee, thinned under parachute. Triangles mark launch-rod exit, burnout, apogee and deployment. A second Y variable gets its own right-hand axis when its unit differs. Drag to zoom, double-click to reset, scroll to zoom, click legend entries to hide traces, and use the camera button to save a PNG. <b>Previous version</b> overlays the commit before as a faint dashed line. Simulated headlessly with OpenRocket 24.12, wind turbulence off, fixed seed; to change conditions, change the simulation in the <code>.ork</code> and push.</p>
   </section>
   <section id="view-changelog" hidden>
    <div id="clog"></div>
    <p class="legend">What changed in each design file, commit by commit, newest first, for the designs ticked on the left (all designs when none are). The bullet list and the field table are an exact structural diff of the <code>.ork</code> (components are matched by OpenRocket's internal ids, so renames and moves are recognised). A quoted paragraph, when present, is a summary written by GitHub Copilot from that same diff; the diff is the record. The coloured chips show how each ticked simulation moved at that commit.</p>
   </section>
   <section id="view-history">
    <div class="row" id="metrics"></div>
    <div class="chartbox"><div id="hplot" style="position:absolute;inset:10px"></div><div class="empty" id="empty" hidden>Select simulations in the list.</div></div>
    <div id="latest"></div>
    <p class="legend">Three views: <b>Absolute</b> and <b>Δ line</b> plot each simulation over commit date; <b>Δ bars</b> puts commits on the x-axis with one bar per commit, green for an increase and red for a decrease from the previous version (outlined in the simulation's colour when several are ticked). Hover for the commit, author, message and change; click a point or bar to open the commit on GitHub; drag to zoom, double-click to reset. Tick several simulations to compare them (wind cases, engine curves, designs). Views are linkable: the URL updates as you select. Simulated headlessly with OpenRocket 24.12, wind turbulence off, fixed seed. Generated by <code>or_ci.py history --site</code>.</p>
   </section>
  </main>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const PALETTE = ['#0969da','#e16f24','#1a7f37','#8250df','#cf222e','#0598a3','#bf8700','#d1478e','#57606a','#3fb950'];
const fmt = (v, dec) => (v == null || Number.isNaN(v)) ? '–' : Number(v).toFixed(dec);
const short = f => f.split('/').pop();
const sub = document.getElementById('sub');
sub.textContent = `${DATA.generated}` + (DATA.repo ? ' · ' : '');
if (DATA.repo) { const a = document.createElement('a'); a.href = DATA.repo; a.textContent = DATA.repo.replace('https://github.com/', ''); sub.appendChild(a); }

// ---- state (mirrored in the URL hash) ----
const ALL = [];  // {id, file, sim, rows}
for (const [file, sims] of Object.entries(DATA.designs)) for (const [sim, rows] of Object.entries(sims)) ALL.push({ id: `${file}|${sim}`, file, sim, rows });
const VIEWS = [['abs', 'Absolute'], ['dline', 'Δ line'], ['dbar', 'Δ bars']];
const state = { metric: DATA.metrics[0].key, view: 'abs', sel: new Set(), units: DATA.default_units || 'metric', stab: DATA.default_stability || 'cal',
                tab: 'history', fx: 'altitude', fy: 'stability', fy2: '', fapo: true, fprev: false };
const FVARS = DATA.flight_vars || {};
const unitSel = document.getElementById('units');
unitSel.onchange = () => { state.units = unitSel.value; update(); };
const stabSel = document.getElementById('stab');
// flight variables that exist in both stability forms: calibers <-> % of length
const STAB_FLIGHT = { stability: 'stability_pct_length', stability_pct_length: 'stability' };
function stabKey(k) { const o = STAB_FLIGHT[k]; if (!o || !FVARS[o]) return k; return (state.stab === 'pct') === (k === 'stability_pct_length') ? k : o; }
function applyStab() {
  const m = DATA.metrics.find(x => x.key === state.metric); if (m && m.stab && m.stab !== state.stab && m.pair) state.metric = m.pair;
  state.fx = stabKey(state.fx); state.fy = stabKey(state.fy); if (state.fy2) state.fy2 = stabKey(state.fy2);
}
stabSel.onchange = () => { state.stab = stabSel.value; applyStab(); update(); };
// the metrics shown for the current stability form (calibers vs % of length)
function visibleMetrics() { return DATA.metrics.filter(m => !m.stab || m.stab === state.stab); }
// metric spec in the current unit system: {key, label, unit, dec, factor}
function specOf(key) { const m = DATA.metrics.find(x => x.key === key); const u = (m.units && m.units[state.units]) || m.units.metric; return { key: m.key, label: m.label, unit: u.unit, dec: u.dec, factor: u.factor }; }
function val(r, key) { const v = r.m[key]; return v == null ? null : v * specOf(key).factor; }
function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get('metric') && DATA.metrics.some(m => m.key === p.get('metric'))) state.metric = p.get('metric');
  state.view = VIEWS.some(v => v[0] === p.get('view')) ? p.get('view') : (p.get('delta') === '1' ? 'dline' : 'abs');
  if (p.get('units') === 'metric' || p.get('units') === 'imperial') state.units = p.get('units');
  if (p.get('stab') === 'cal' || p.get('stab') === 'pct') state.stab = p.get('stab');
  state.tab = ['flight', 'changelog'].includes(p.get('tab')) ? p.get('tab') : 'history';
  if (FVARS[p.get('fx')]) state.fx = p.get('fx');
  if (FVARS[p.get('fy')]) state.fy = p.get('fy');
  state.fy2 = FVARS[p.get('fy2')] ? p.get('fy2') : '';
  if (p.has('apo')) state.fapo = p.get('apo') !== '0';
  state.fprev = p.get('prev') === '1';
  applyStab();
  const s = p.get('sel');
  // names may contain '%' ("Seymour_10 [85%]"); a link re-encoded by a chat app must not crash the page
  const safeDecode = x => { try { return decodeURIComponent(x); } catch (e) { return x; } };
  if (s) { const ids = s.split(','); state.sel = new Set(ids.map(safeDecode).concat(ids).filter(id => ALL.some(a => a.id === id))); }
  if (!state.sel.size) { const first = ALL[0] && ALL[0].file; ALL.filter(a => a.file === first).forEach(a => state.sel.add(a.id)); }
}
function writeHash() {
  const p = new URLSearchParams();
  p.set('tab', state.tab); p.set('units', state.units); p.set('stab', state.stab);
  if (state.tab === 'flight') { p.set('fx', state.fx); p.set('fy', state.fy); if (state.fy2) p.set('fy2', state.fy2); p.set('apo', state.fapo ? '1' : '0'); if (state.fprev) p.set('prev', '1'); }
  else { p.set('metric', state.metric); p.set('view', state.view); }
  p.set('sel', [...state.sel].map(encodeURIComponent).join(','));
  history.replaceState(null, '', '#' + p.toString());
}
const colorOf = new Map();
function assignColors() { colorOf.clear(); let i = 0; for (const a of ALL) if (state.sel.has(a.id)) colorOf.set(a.id, PALETTE[i++ % PALETTE.length]); }

// ---- sidebar tree ----
const nav = document.getElementById('nav');
function buildNav() {
  nav.innerHTML = '';
  const tools = document.createElement('div'); tools.className = 'navtools';
  const bNone = document.createElement('button'); bNone.textContent = 'Clear'; bNone.onclick = () => { state.sel.clear(); update(); };
  tools.appendChild(bNone); nav.appendChild(tools);
  for (const [file, sims] of Object.entries(DATA.designs)) {
    const d = document.createElement('div'); d.className = 'design';
    const head = document.createElement('div'); head.className = 'head';
    const caret = document.createElement('span'); caret.className = 'caret'; caret.textContent = '▼';
    const name = document.createElement('span'); name.className = 'name'; name.textContent = short(file); name.title = file;
    const mini = document.createElement('span'); mini.className = 'mini'; mini.textContent = `${Object.keys(sims).length}`;
    const all = document.createElement('button'); all.textContent = 'all'; all.title = 'Select every simulation of this design';
    all.onclick = e => { e.stopPropagation(); const ids = ALL.filter(a => a.file === file).map(a => a.id); const every = ids.every(id => state.sel.has(id)); ids.forEach(id => every ? state.sel.delete(id) : state.sel.add(id)); update(); };
    head.append(caret, name, mini, all); head.onclick = () => d.classList.toggle('closed');
    const list = document.createElement('div'); list.className = 'sims';
    for (const [sim, rows] of Object.entries(sims)) {
      const id = `${file}|${sim}`;
      const row = document.createElement('label'); row.className = 'sim'; row.dataset.id = id;
      const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = state.sel.has(id);
      cb.onchange = () => { cb.checked ? state.sel.add(id) : state.sel.delete(id); update(); };
      const sw = document.createElement('span'); sw.className = 'swatch';
      const lab = document.createElement('span'); lab.className = 'label'; lab.textContent = sim; lab.title = sim;
      const val = document.createElement('span'); val.className = 'val';
      row.append(cb, sw, lab, val); list.appendChild(row);
    }
    d.append(head, list); nav.appendChild(d);
  }
}
function refreshNav() {
  const spec = specOf(state.metric);
  for (const row of nav.querySelectorAll('.sim')) {
    const a = ALL.find(x => x.id === row.dataset.id);
    row.querySelector('input').checked = state.sel.has(a.id);
    row.querySelector('.swatch').style.background = colorOf.get(a.id) || css('--line');
    const ok = a.rows.filter(r => r.ok && r.m[state.metric] != null);
    const v = ok.length ? val(ok[ok.length - 1], state.metric) : null, p = ok.length > 1 ? val(ok[ok.length - 2], state.metric) : null;
    const el = row.querySelector('.val'); el.textContent = fmt(v, spec.dec) + (spec.unit ? ' ' + spec.unit : '');
    el.className = 'val' + (p == null || v == null || Math.abs(v - p) < Math.pow(10, -spec.dec) / 2 ? '' : v > p ? ' up' : ' down');
  }
}

// ---- metric buttons ----
const mrow = document.getElementById('metrics');
function buildMetrics() {
  mrow.innerHTML = '';
  for (const m of visibleMetrics()) { const b = document.createElement('button'); b.textContent = m.label; b.className = m.key === state.metric ? 'on' : ''; b.onclick = () => { state.metric = m.key; update(); }; mrow.appendChild(b); }
  const sp = document.createElement('span'); sp.className = 'spacer'; mrow.appendChild(sp);
  const lab = document.createElement('span'); lab.className = 'toggle'; lab.textContent = 'view'; mrow.appendChild(lab);
  for (const [key, text] of VIEWS) { const b = document.createElement('button'); b.textContent = text; b.className = key === state.view ? 'on' : '';
    b.title = key === 'abs' ? 'Value of each version over commit date' : key === 'dline' ? 'Change from the previous version over commit date' : 'Change from the previous version, one bar per commit (green up, red down)';
    b.onclick = () => { state.view = key; update(); }; mrow.appendChild(b); }
}

// ---- history chart (Plotly) ----
const hplot = document.getElementById('hplot'), empty = document.getElementById('empty');
function simLabel(a) { return (Object.keys(DATA.designs).length > 1 ? short(a.file) + ' \u00b7 ' : '') + a.sim; }
const isDark = () => window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
function baseLayout(xTitle, yTitle) {
  const grid = isDark() ? '#30363d' : '#d0d7de';
  return { paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font: { color: css('--fg'), size: 12 },
           margin: { l: 60, r: 20, t: 30, b: 60 }, hovermode: 'closest', dragmode: 'zoom', legend: { orientation: 'h', y: 1.06, x: 0 },
           xaxis: { title: { text: xTitle }, gridcolor: grid, zeroline: false, automargin: true },
           yaxis: { title: { text: yTitle }, gridcolor: grid, zeroline: true, zerolinecolor: grid, automargin: true } };
}
const plotConfig = name => ({ responsive: true, displaylogo: false, scrollZoom: true, modeBarButtonsToRemove: ['lasso2d', 'select2d'],
                              toImageButtonOptions: { format: 'png', filename: name, scale: 2 } });
function openCommit(div) {   // click a point or bar -> the commit on GitHub
  if (!div.on || !DATA.repo) return;
  div.removeAllListeners && div.removeAllListeners('plotly_click');
  div.on('plotly_click', ev => { const sha = ev.points && ev.points[0] && ev.points[0].customdata && ev.points[0].customdata.sha; if (sha) window.open(`${DATA.repo}/commit/${sha}`, '_blank'); });
}
function seriesOf(a, key) {   // successful versions with this metric, in display units, with per-point delta
  const ok = a.rows.filter(r => r.ok && r.m[key] != null);
  return ok.map((r, i) => { const v = val(r, key); return { r, v, d: i ? v - val(ok[i - 1], key) : null }; });
}
const dirColor = (d, tol) => d == null || Math.abs(d) < tol ? css('--flat') : d > 0 ? css('--up') : css('--down');

// one bar per commit, height and colour = change from the previous version of that simulation
function drawBars(spec, unit) {
  const tol = Math.pow(10, -spec.dec) / 2, commits = new Map(), series = [];
  for (const a of ALL) {
    if (!state.sel.has(a.id)) continue;
    const pts = new Map();
    for (const p of seriesOf(a, state.metric)) if (p.d != null) { pts.set(p.r.short, p); commits.set(p.r.short, p.r); }
    series.push({ a, pts });
  }
  const order = [...commits.values()].sort((x, y) => x.t - y.t);
  empty.hidden = order.length > 0;
  empty.textContent = series.length ? 'Only one version so far: nothing to take a difference of.' : 'Select simulations in the list.';
  if (!order.length) { Plotly.purge(hplot); return; }
  const labels = order.map(r => `${r.date.slice(5)} ${r.short}`);
  const traces = series.map(({ a, pts }) => {
    const rows = order.map(r => pts.get(r.short) || null);
    return { type: 'bar', name: simLabel(a), x: labels, y: rows.map(p => p ? p.d : null), customdata: rows.map((p, i) => ({ sha: order[i].sha, p })),
             marker: { color: rows.map(p => dirColor(p ? p.d : null, tol)), line: { color: colorOf.get(a.id), width: series.length > 1 ? 2 : 0 } },
             hovertemplate: rows.map((p, i) => p ? `<b>${order[i].short}</b> \u00b7 ${order[i].date} \u00b7 ${order[i].author}<br>${simLabel(a)}: \u0394 ${p.d >= 0 ? '+' : ''}${fmt(p.d, spec.dec)}${unit}`
                                            + (p.v - p.d ? ` (${(p.d / (p.v - p.d) * 100).toFixed(1)}%)` : '') + `<br>now ${fmt(p.v, spec.dec)}${unit}<br>${order[i].message}<extra></extra>` : '') };
  });
  const layout = baseLayout('commit', '\u0394 ' + spec.label + (spec.unit ? ` (${spec.unit})` : '')); layout.barmode = 'group'; layout.showlegend = series.length > 1;
  Plotly.react(hplot, traces, layout, plotConfig(`delta_${state.metric}`)); openCommit(hplot);
}

// value (or change) of each selected simulation over commit date
function draw() {
  const spec = specOf(state.metric), unit = spec.unit ? ' ' + spec.unit : '';
  if (state.view === 'dbar') return drawBars(spec, unit);
  const delta = state.view === 'dline', tol = Math.pow(10, -spec.dec) / 2, traces = [];
  for (const a of ALL) {
    if (!state.sel.has(a.id)) continue;
    const pts = seriesOf(a, state.metric); if (!pts.length) continue;
    const col = colorOf.get(a.id);
    traces.push({ type: 'scatter', mode: 'lines+markers', name: simLabel(a),
                  x: pts.map(p => new Date(p.r.t * 1000).toISOString()), y: pts.map(p => delta ? (p.d ?? 0) : p.v), customdata: pts.map(p => ({ sha: p.r.sha })),
                  line: { color: col, width: 2 }, marker: { size: 9, color: pts.map(p => dirColor(p.d, tol)), line: { color: col, width: 1.5 } },
                  hovertemplate: pts.map(p => `<b>${p.r.short}</b> \u00b7 ${p.r.date} \u00b7 ${p.r.author}<br>${simLabel(a)}: ${fmt(p.v, spec.dec)}${unit}`
                                   + (p.d != null ? `<br>\u0394 vs previous: ${p.d >= 0 ? '+' : ''}${fmt(p.d, spec.dec)}${unit}` + (p.v - p.d ? ` (${(p.d / (p.v - p.d) * 100).toFixed(1)}%)` : '') : '')
                                   + `<br>${p.r.message}<extra></extra>`) });
  }
  empty.hidden = traces.length > 0; empty.textContent = 'Select simulations in the list.';
  if (!traces.length) { Plotly.purge(hplot); return; }
  const layout = baseLayout('commit date', (delta ? '\u0394 ' : '') + spec.label + (spec.unit ? ` (${spec.unit})` : ''));
  layout.xaxis.type = 'date'; layout.showlegend = traces.length > 1;
  Plotly.react(hplot, traces, layout, plotConfig(`${delta ? 'delta_' : ''}${state.metric}`)); openCommit(hplot);
}

// ---- latest-values table ----
function drawTable() {
  const box = document.getElementById('latest');
  const rows = ALL.filter(a => state.sel.has(a.id));
  if (!rows.length) { box.innerHTML = ''; return; }
  const specs = visibleMetrics().map(m => specOf(m.key));
  let h = '<table><thead><tr><th>Simulation</th>' + specs.map(m => `<th>${m.label}${m.unit ? ' (' + m.unit + ')' : ''}</th>`).join('') + '<th>Latest version</th></tr></thead><tbody>';
  for (const a of rows) {
    const ok = a.rows.filter(r => r.ok); const last = ok[ok.length - 1], prev = ok[ok.length - 2];
    h += `<tr><td><span class="sw" style="background:${colorOf.get(a.id)}"></span>${Object.keys(DATA.designs).length > 1 ? short(a.file) + ' · ' : ''}${a.sim}</td>`;
    for (const m of specs) {
      const v = last ? val(last, m.key) : null, p = prev ? val(prev, m.key) : null;
      let d = '';
      if (v != null && p != null && Math.abs(v - p) >= Math.pow(10, -m.dec) / 2) d = `<span class="d ${v > p ? 'up' : 'down'}">${v > p ? '+' : ''}${fmt(v - p, m.dec)}</span>`;
      h += `<td>${fmt(v, m.dec)}${d}</td>`;
    }
    h += `<td>${last ? (DATA.repo && last.sha ? `<a href="${DATA.repo}/commit/${last.sha}" target="_blank">${last.short}</a>` : last.short) + ' · ' + last.date : '–'}</td></tr>`;
  }
  box.innerHTML = h + '</tbody></table>';
}

// ---- flight plots: any variable against any other, latest version of each ticked simulation ----
const FLIGHTS = {};            // id -> {versions: [{short, sha, date, message, cols, events}, ...]} newest first
const flightRequested = new Set();
window.__flight = (id, payload) => { FLIGHTS[id] = payload; if (state.tab === 'flight') update(); };
function ensureFlight(id) {   // data files are scripts, not fetch(), so the page also works from file://
  if (FLIGHTS[id] || flightRequested.has(id) || !(DATA.flights || {})[id]) return;
  flightRequested.add(id);
  const s = document.createElement('script'); s.src = DATA.flights[id]; document.head.appendChild(s);
}
const UNIT_METRIC = { 'Pa': ['kPa', 1e-3], 'rad': ['°', 57.29578], 'rad/s': ['°/s', 57.29578] };
const UNIT_IMPERIAL = { 'm': ['ft', 3.28084], 'm/s': ['ft/s', 3.28084], 'm/s²': ['ft/s²', 3.28084], 'N': ['lbf', 0.2248089], 'kg': ['lb', 2.2046226],
  'Pa': ['psi', 1.450377e-4], 'kg/m³': ['lb/ft³', 0.062428], 'm²': ['ft²', 10.76391], 'kg·m²': ['lb·ft²', 23.73036],
  'rad': ['°', 57.29578], 'rad/s': ['°/s', 57.29578] };
function fvar(key) { const m = FVARS[key]; const c = (state.units === 'imperial' ? UNIT_IMPERIAL : UNIT_METRIC)[m.unit];
  return { key, label: m.label, unit: c ? c[0] : m.unit, factor: c ? c[1] : 1 }; }
const axisText = v => v.label + (v.unit ? ` (${v.unit})` : '');
const PRESETS = [
  ['Stability vs altitude', 'altitude', 'stability', '', true], ['Velocity & Mach vs time', 'time', 'velocity_total', 'mach_number', true],
  ['Cd vs Mach', 'mach_number', 'drag_coeff', '', true], ['Thrust & mass vs time', 'time', 'thrust_force', 'mass', true],
  ['CP & CG vs time', 'time', 'cp_location', 'cg_location', true], ['Dynamic pressure vs altitude', 'altitude', 'dynamic_pressure', '', true],
  ['AoA vs time', 'time', 'aoa', '', true], ['Altitude vs time (whole flight)', 'time', 'altitude', '', false]];
const MARKED_EVENTS = [['LAUNCHROD', 'rod exit'], ['BURNOUT', 'burnout'], ['APOGEE', 'apogee'], ['RECOVERY_DEVICE_DEPLOYMENT', 'deployment']];

function buildFlightControls() {
  const pre = document.getElementById('fpresets'); pre.innerHTML = '';
  for (const [name, x, y0, y2, apo] of PRESETS) {
    // the stability preset follows the header's calibers / % of length choice
    const y = (y0 === 'stability' && state.stab === 'pct' && FVARS.stability_pct_length) ? 'stability_pct_length' : y0;
    if (!FVARS[x] || !FVARS[y] || (y2 && !FVARS[y2])) continue;
    const b = document.createElement('button'); b.textContent = name;
    b.className = (state.fx === x && state.fy === y && state.fy2 === y2) ? 'on' : '';
    b.onclick = () => { state.fx = x; state.fy = y; state.fy2 = y2; state.fapo = apo; update(); }; pre.appendChild(b);
  }
  const row = document.getElementById('fcontrols'); row.innerHTML = '';
  const keys = Object.keys(FVARS).sort((a, b) => FVARS[a].label.localeCompare(FVARS[b].label));
  const addSelect = (text, value, allowNone, onpick) => {
    const l = document.createElement('span'); l.className = 'lbl'; l.textContent = text; row.appendChild(l);
    const s = document.createElement('select');
    if (allowNone) { const o = document.createElement('option'); o.value = ''; o.textContent = '(none)'; s.appendChild(o); }
    for (const k of keys) { const o = document.createElement('option'); o.value = k; o.textContent = axisText(fvar(k)); s.appendChild(o); }
    s.value = value; s.onchange = () => { onpick(s.value); update(); }; row.appendChild(s);
  };
  addSelect('X', state.fx, false, v => state.fx = v);
  addSelect('Y', state.fy, false, v => state.fy = v);
  addSelect('Y2', state.fy2, true, v => state.fy2 = v);
  const sp = document.createElement('span'); sp.className = 'spacer'; row.appendChild(sp);
  const addCheck = (text, checked, onpick) => { const l = document.createElement('label'); l.className = 'toggle';
    const c = document.createElement('input'); c.type = 'checkbox'; c.checked = checked; c.onchange = () => { onpick(c.checked); update(); };
    l.append(c, document.createTextNode(text)); row.appendChild(l); };
  addCheck('ascent only', state.fapo, v => state.fapo = v);
  addCheck('previous version', state.fprev, v => state.fprev = v);
}

const fplot = document.getElementById('fplot'), fempty = document.getElementById('fempty'), finfo = document.getElementById('finfo');
function flightPoints(ver, xk, yk, xf, yf) {   // -> {x:[], y:[], t:[]} in display units, ascent-only when asked
  const t = ver.cols.time, xs = ver.cols[xk], ys = ver.cols[yk]; const out = { x: [], y: [], t: [] };
  if (!t || !xs || !ys) return out;
  const tApo = (ver.events.APOGEE || [Infinity])[0];
  for (let i = 0; i < t.length; i++) { if (state.fapo && t[i] > tApo) break; if (xs[i] == null || ys[i] == null) continue; out.x.push(xs[i] * xf); out.y.push(ys[i] * yf); out.t.push(t[i]); }
  return out;
}
function drawFlight() {
  const sel = ALL.filter(a => state.sel.has(a.id)); sel.forEach(a => ensureFlight(a.id));
  const X = fvar(state.fx), Y = fvar(state.fy), Y2 = state.fy2 ? fvar(state.fy2) : null;
  const twoAxes = Y2 && Y2.unit !== Y.unit;
  const traces = [], notes = [];
  const hover = (label, V) => `${label}<br>${V.label}: %{y:.5g}${V.unit ? ' ' + V.unit : ''}<br>${X.label}: %{x:.5g}${X.unit ? ' ' + X.unit : ''}<br>t = %{customdata:.2f} s<extra></extra>`;
  for (const a of sel) {
    const fl = FLIGHTS[a.id], col = colorOf.get(a.id);
    if (!fl) { notes.push(`${simLabel(a)}: ${(DATA.flights || {})[a.id] ? 'loading\u2026' : 'no flight data (simulation failed or motor unresolved)'}`); continue; }
    const vers = state.fprev ? fl.versions.slice(0, 2) : fl.versions.slice(0, 1);
    notes.push(`${simLabel(a)}: ${vers.map(v => `${v.short} (${v.date})`).join(' vs ')}`);
    vers.forEach((ver, vi) => {
      [[Y, 'y'], [Y2, twoAxes ? 'y2' : 'y']].forEach(([V, axis], yi) => {
        if (!V) return;
        const p = flightPoints(ver, X.key, V.key, X.factor, V.factor); if (!p.x.length) return;
        const label = `${simLabel(a)} \u00b7 ${V.label}${vi ? ' (previous ' + ver.short + ')' : ''}`;
        traces.push({ type: 'scatter', mode: 'lines', name: label, x: p.x, y: p.y, customdata: p.t, yaxis: axis,
                      line: { color: col, width: vi ? 1.5 : 2, dash: vi ? 'dot' : (yi ? 'dash' : 'solid') }, opacity: vi ? 0.55 : 1,
                      hovertemplate: hover(label, V) });
      });
      if (vi) return;   // event markers on the latest version's first Y only
      const base = flightPoints(ver, X.key, Y.key, X.factor, Y.factor);
      const m = { x: [], y: [], t: [], text: [] };
      for (const [ev, text] of MARKED_EVENTS) { const te = (ver.events[ev] || [])[0]; if (te == null || !base.t.length) continue;
        let best = 0; for (let i = 1; i < base.t.length; i++) if (Math.abs(base.t[i] - te) < Math.abs(base.t[best] - te)) best = i;
        if (Math.abs(base.t[best] - te) < 1.0) { m.x.push(base.x[best]); m.y.push(base.y[best]); m.t.push(te); m.text.push(text); } }
      if (m.x.length) traces.push({ type: 'scatter', mode: 'markers', name: `${simLabel(a)} \u00b7 events`, x: m.x, y: m.y, customdata: m.t, text: m.text, yaxis: 'y',
                                    marker: { symbol: 'triangle-up', size: 12, color: css('--bg'), line: { color: col, width: 2 } }, showlegend: false,
                                    hovertemplate: `<b>%{text}</b><br>${simLabel(a)}<br>${Y.label}: %{y:.5g}${Y.unit ? ' ' + Y.unit : ''}<br>${X.label}: %{x:.5g}${X.unit ? ' ' + X.unit : ''}<br>t = %{customdata:.2f} s<extra></extra>` });
    });
  }
  finfo.textContent = notes.join('   |   ');
  fempty.hidden = traces.length > 0; fempty.textContent = sel.length ? 'Loading flight data\u2026' : 'Select simulations in the list.';
  if (!traces.length) { if (window.Plotly) Plotly.purge(fplot); return; }
  const dark = isDark(), fg = css('--fg'), grid = dark ? '#30363d' : '#d0d7de';
  const layout = {
    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', font: { color: fg, size: 12 },
    margin: { l: 60, r: twoAxes ? 60 : 20, t: 30, b: 50 }, hovermode: 'closest', dragmode: 'zoom',
    legend: { orientation: 'h', y: 1.06, x: 0 },
    xaxis: { title: { text: axisText(X) }, gridcolor: grid, zeroline: false, automargin: true },
    yaxis: { title: { text: axisText(Y) + (Y2 && !twoAxes ? '  /  ' + axisText(Y2) : '') }, gridcolor: grid, zeroline: false, automargin: true },
  };
  if (twoAxes) layout.yaxis2 = { title: { text: axisText(Y2) + '  (dashed)' }, overlaying: 'y', side: 'right', showgrid: false, zeroline: false, automargin: true };
  const config = { responsive: true, displaylogo: false, scrollZoom: true, modeBarButtonsToRemove: ['lasso2d', 'select2d'],
                   toImageButtonOptions: { format: 'png', filename: `${state.fy}_vs_${state.fx}`, scale: 2 } };
  Plotly.react(fplot, traces, layout, config);
}

// ---- changelog: what changed in each design file, commit by commit ----
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const bold = s => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
function changeChips(file, entry) {   // performance change of each ticked simulation at this commit
  const chips = [];
  for (const a of ALL) {
    if (a.file !== file || !state.sel.has(a.id)) continue;
    const ok = a.rows.filter(r => r.ok), i = ok.findIndex(r => r.short === entry.short);
    if (i < 1) continue;
    const parts = [];
    for (const key of ['apogee', state.stab === 'pct' ? 'stability_off_rod_pct' : 'stability_off_rod_cal']) {
      const sp = specOf(key), v = val(ok[i], key), p = val(ok[i - 1], key); if (v == null || p == null) continue;
      const d = v - p, flat = Math.abs(d) < Math.pow(10, -sp.dec) / 2;
      parts.push(`<span class="${flat ? '' : d > 0 ? 'up' : 'down'}">${sp.label} ${flat ? 'unchanged' : (d > 0 ? '+' : '') + fmt(d, sp.dec) + (sp.unit ? ' ' + sp.unit : '')}</span>`);
    }
    if (parts.length) chips.push(`<span class="chip"><b>${esc(a.sim)}</b> ${parts.join(' · ')}</span>`);
  }
  return chips.join('');
}
function drawChangelog() {
  const box = document.getElementById('clog'), log = DATA.changelog || {};
  const ticked = new Set(ALL.filter(a => state.sel.has(a.id)).map(a => a.file));
  const files = Object.keys(log).filter(f => !ticked.size || ticked.has(f));
  let h = '';
  for (const f of files) {
    h += `<h2 class="clfile">${esc(f)}</h2>`;
    if (!log[f].length) { h += '<p class="legend">Only one version so far.</p>'; continue; }
    for (const e of log[f]) {
      const link = DATA.repo && e.sha ? `<a href="${DATA.repo}/commit/${e.sha}" target="_blank">${esc(e.short)}</a>` : esc(e.short);
      const rows = (e.rows[state.units] || []);
      h += `<div class="entry"><div class="ehead">${esc(e.date)} · ${link} · ${esc(e.author)} · <i>${esc(e.message)}</i></div>`
        + `<div class="ehl">${esc(e.headline)}</div>`
        + (e.narration ? `<blockquote title="Written by GitHub Copilot from the structured diff below">${esc(e.narration)}</blockquote>` : '')
        + (e.empty ? '' : '<ul>' + (e.lines[state.units] || []).map(l => `<li>${bold(l)}</li>`).join('') + '</ul>')
        + `<div class="chips">${changeChips(f, e)}</div>`
        + (rows.length ? `<details><summary>All changed fields (${rows.length})</summary><table><thead><tr><th>Component</th><th>Field</th><th>Before</th><th>After</th></tr></thead><tbody>`
            + rows.map(r => `<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td><td>${esc(r[2])}</td><td>${esc(r[3])}</td></tr>`).join('') + '</tbody></table></details>' : '')
        + '</div>';
    }
  }
  box.innerHTML = h || '<p class="legend">No changelog yet: it starts with the second committed version of a design.</p>';
}

const TABS = { history: 'tab-history', flight: 'tab-flight', changelog: 'tab-changelog' };
for (const [name, id] of Object.entries(TABS)) document.getElementById(id).onclick = () => { state.tab = name; update(); };
function update() {
  unitSel.value = state.units; stabSel.value = state.stab; assignColors(); writeHash(); refreshNav();
  for (const [name, id] of Object.entries(TABS)) {
    document.getElementById(id).className = state.tab === name ? 'on' : '';
    document.getElementById('view-' + name).hidden = state.tab !== name;
  }
  if (state.tab !== 'flight') Plotly.purge(fplot);
  if (state.tab !== 'history') Plotly.purge(hplot);
  if (state.tab === 'flight') { buildFlightControls(); drawFlight(); }
  else if (state.tab === 'changelog') drawChangelog();
  else { buildMetrics(); draw(); drawTable(); }
}
readHash(); buildNav(); update();
window.addEventListener('hashchange', () => { readHash(); update(); });
</script>
</body>
</html>
"""


def write_site(series: dict, site_dir: Path, units: str, repo_url: str, files_versions: dict, stability: str = "cal",
               changelog: dict = None):
    """A self-contained index.html (+ data.json) with interactive charts of every design's history.
    Values are emitted in SI with both unit systems' specs, and both stability forms (calibers and
    % of length); the page converts and switches client-side."""
    import datetime as dt
    import hashlib
    import shutil
    site_keys = PRIMARY_KEYS + list(STABILITY_PAIRS.values())
    designs, flights, flight_vars = {}, {}, {}
    site_dir.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(site_dir / "flights", ignore_errors=True)   # stale simulations must not linger
    for (file, sim), rows in series.items():
        out_rows = []
        # map short sha -> full sha via files_versions
        shas = {v["short"]: v["sha"] for v in files_versions.get(file, [])}
        times = {v["short"]: v["time"] for v in files_versions.get(file, [])}
        for r in rows:
            m = r["metrics"] or {}
            out_rows.append({
                "short": r["short"], "sha": shas.get(r["short"]) or "", "t": times.get(r["short"], 0),
                "date": r["date"], "author": r["author"],
                "message": r["message"], "ok": r["status"] == "OK" and not r["note"],
                "m": {k: (None if math.isnan(m.get(k, math.nan)) else round(m[k], 4)) for k in site_keys},
            })
        designs.setdefault(file, {})[sim] = out_rows
        # flight time series of the newest versions -> flights/<hash>.js (a script, so it loads from file:// too)
        vers = [{"short": r["short"], "sha": shas.get(r["short"]) or "", "date": r["date"], "message": r["message"],
                 "cols": r["flight"]["cols"], "events": r["flight"]["events"]}
                for r in reversed(rows) if r.get("flight") and r["status"] == "OK"][:2]
        if vers:
            sim_id = f"{file}|{sim}"
            name = "flights/" + hashlib.sha1(sim_id.encode("utf-8")).hexdigest()[:16] + ".js"
            flights[sim_id] = name
            for r in rows:
                if r.get("flight"):
                    flight_vars.update(r["flight"]["vars"])
            (site_dir / "flights").mkdir(parents=True, exist_ok=True)
            (site_dir / name).write_text("window.__flight(" + json.dumps(sim_id) + ", " + json.dumps({"versions": vers}, separators=(",", ":")) + ");\n",
                                         encoding="utf-8")
    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "default_units": units, "default_stability": stability, "repo": repo_url,
        "metrics": [{"key": k, "label": CHART_TITLES.get(k, label),
                     "stab": ("pct" if k in STABILITY_PAIRS.values() else "cal" if k in STABILITY_PAIRS else None),
                     "pair": STABILITY_PAIRS.get(k) or next((c for c, p in STABILITY_PAIRS.items() if p == k), None),
                     "units": {"metric": {"unit": mu, "dec": md, "factor": 1.0},
                               "imperial": {"unit": iu, "dec": idec, "factor": f}}}
                    for (k, label, mu, md, _), (_, _, iu, idec, f)
                    in zip([spec_for(k, "metric") for k in site_keys], [spec_for(k, "imperial") for k in site_keys])],
        "designs": designs,
        "flights": flights,            # simulation id -> data script with the full flight time series
        "flight_vars": flight_vars,    # variable key -> {label, SI unit}
        "changelog": changelog or {},  # design file -> [entry, ...] newest first (ork_diff + optional narration)
    }
    site_dir.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload).replace("</", "<\\/")
    (site_dir / "index.html").write_text(SITE_HTML.replace("__DATA__", data), encoding="utf-8")
    (site_dir / "data.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Wrote site to {site_dir / 'index.html'}")


def main():
    for stream in (sys.stdout, sys.stderr):  # the report has arrows/emoji; Windows consoles default to cp1252
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    p = argparse.ArgumentParser(description="Run / compare the saved simulations in OpenRocket files.")
    sub = p.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", required=True, help="Path to sim_config.json")
    common.add_argument("--jar", default=None, help="OpenRocket jar (default: orlab's cached/fetched default version)")
    common.add_argument("--results", default="or_ci_results", help="Output folder for report.md / results.csv / results.json")
    common.add_argument("--summary", default=None, help="Markdown file to APPEND the report to (e.g. $GITHUB_STEP_SUMMARY)")
    common.add_argument("--strict", action="store_true", help="Exit 2 on limit violations or unresolved motors")
    common.add_argument("--quiet", action="store_true", help="Don't print the report to stdout")
    common.add_argument("files", nargs="*", help=".ork files (default: all non-ignored files under the config folder / changed files)")
    r = sub.add_parser("run", parents=[common], help="Simulate the current files")
    r.set_defaults(fn=cmd_run)
    c = sub.add_parser("compare", parents=[common], help="Simulate before/after a base commit")
    c.add_argument("--base", default="", help="Base commit/ref (default: HEAD~1; all-zero SHAs fall back too)")
    c.add_argument("--all", action="store_true", help="Compare every file, not just the changed ones")
    c.add_argument("--cache", default=None, help="Cache folder holding AI narrations of design changes (narration-<key>.md)")
    c.set_defaults(fn=cmd_compare)
    h = sub.add_parser("history", parents=[common], help="Simulate every committed version; Mermaid bar charts")
    h.add_argument("--cache", default=None, help="Folder caching per-version results by git blob id (skips re-simulation)")
    h.add_argument("--max-commits", type=int, default=200, help="How far back to walk per file")
    h.add_argument("--max-points", type=int, default=30, help="Versions per chart (the most recent ones)")
    h.add_argument("--site", default=None, help="Also write a static site (index.html + data.json) to this folder, for GitHub Pages")
    h.add_argument("--repo-url", default=None, help="GitHub repo URL for commit links in the site (default: from git remote origin)")
    h.add_argument("--pending", action="append", default=None,
                   help="Folder to write narration prompts into (default <results>/pending)")
    h.add_argument("--narrate-backlog", type=int, default=3,
                   help="At most this many un-narrated changelog entries get a prompt per run (newest first)")
    h.set_defaults(fn=cmd_history)
    n = sub.add_parser("narrate", parents=[common], help="Merge AI-written change summaries (<key>.out.md) into the report")
    n.add_argument("--cache", default=None, help="Cache folder to store narrations in")
    n.add_argument("--pending", action="append", default=None, help="Extra folder(s) to scan for <key>.out.md")
    n.set_defaults(fn=cmd_narrate)
    args = p.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()

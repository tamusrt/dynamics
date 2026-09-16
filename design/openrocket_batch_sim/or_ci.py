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

DEFAULT_SEED = 20260915

# (metric key in orlab FlightSummary, label, unit, decimals)
METRICS = [
    ("apogee", "Apogee", "m", 0),
    ("max_velocity", "Max velocity", "m/s", 1),
    ("max_mach", "Max Mach", "", 2),
    ("max_acceleration", "Max acceleration", "m/s²", 1),
    ("velocity_off_rod", "Rod exit speed", "m/s", 1),
    ("stability_off_rod_cal", "Stability off rod", "cal", 2),
    ("min_stability_cal", "Min stability (rod→apogee)", "cal", 2),
    ("time_to_apogee", "Time to apogee", "s", 1),
    ("velocity_at_deployment", "Deployment speed", "m/s", 1),
    ("descent_rate", "Descent rate", "m/s", 1),
    ("flight_time", "Flight time", "s", 0),
    ("landing_distance", "Landing distance", "m", 0),
]
METRIC_KEYS = [m[0] for m in METRICS]


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
        folder = PurePosixPath(ork_rel).parent.as_posix()
        folder = "" if folder == "." else folder
        # the .ork's folder and its subfolders (e.g. "Thrust Curves/"), skipping ignored ones
        for rel in snap.walk(folder):
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


def check_limits(metrics: dict, limits: dict):
    out = []
    for metric, spec in limits.items():
        val = metrics.get(metric, math.nan)
        if math.isnan(val):
            continue
        if "min" in spec and val < float(spec["min"]):
            out.append(f"{metric} = {val:.3g} < min {spec['min']}")
        if "max" in spec and val > float(spec["max"]):
            out.append(f"{metric} = {val:.3g} > max {spec['max']}")
    return out


def run_ork(helper, cfg: Config, snap: Snapshot, root: Path, ork_rel: str, seed: int, log=print, fallback=None):
    """Run every simulation in one .ork (as found in `snap`). Returns a list of record dicts.
    `fallback` is a Snapshot to take motor files from when `snap` lacks them (a base commit
    that predates the thrust-curve file)."""
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
    cfg_rel = rel_posix(root / ork_rel, cfg.dir)
    fcfg = cfg.file_cfg(cfg_rel)
    for i in range(n):
        sim = doc.getSimulation(i)
        sim_name = str(sim.getName())
        configid = info["sims"][i][1] if i < len(info["sims"]) else ""
        rec = {**base_rec, "sim_index": i, "sim_name": sim_name, "status": "OK", "metrics": {}, "notes": "",
               "motor_source": "", "motor_file": "", "motor": "", "motors": [], "warnings": "", "violations": []}
        try:
            scfg = cfg.sim_cfg(fcfg, sim_name)
            mounts = mounts_for(info, configid) or [(None, "")]
            notes = []
            for mount, file_desig in mounts:
                mount_obj = find_mount(helper, sim.getRocket(), mount) if mount else None
                try:
                    own = helper.get_motor(sim, mount=mount_obj)
                except Exception:
                    own = None
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
            rec["metrics"] = {k: _num(summary.get(k)) for k in METRIC_KEYS}
            rec["warnings"] = "; ".join(str(w) for w in (summary.get("warnings") or ()))[:400]
            rec["violations"] = check_limits(rec["metrics"], cfg.limits_for(fcfg, scfg))
            log(f"  [{snap.label}] {ork_rel} :: {sim_name} ({rec['motor'] or 'no motor'}, {source}) "
                f"apogee={rec['metrics']['apogee']:.0f} m  {time.time()-t0:.1f}s")
        except Exception as e:
            rec["status"] = "SIM_ERROR"
            rec["notes"] = f"{type(e).__name__}: {str(e)[:300]}"
            log(f"  [{snap.label}] {ork_rel} :: {sim_name} FAILED: {rec['notes']}")
        records.append(rec)
    return records


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
                  deterministic_wind=True) -> tuple:
    """-> (markdown, n_violations, n_unresolved)"""
    lines = [f"## OpenRocket simulation check", ""]
    wind = ("wind turbulence set to 0 for reproducibility (average wind kept)" if deterministic_wind
            else "wind turbulence as saved in the file (results vary run to run)")
    if base_label:
        lines.append(f"Comparing **{base_label}** (before) → **{head_label}** (after); same random seed, {wind}.")
    else:
        lines.append(f"Simulated **{head_label}** (no base commit to compare against); {wind}.")
    if changed_motor_files:
        lines.append(f"Motor files changed in this range: {', '.join(f'`{m}`' for m in changed_motor_files)}.")
    for d in deleted:
        lines.append(f"Deleted: `{d}`")
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
        ap = r["metrics"].get("apogee", math.nan) if r["metrics"] else math.nan
        ap_b = b["metrics"].get("apogee", math.nan) if b and b.get("metrics") else None
        stab = r["metrics"].get("stability_off_rod_cal", math.nan) if r["metrics"] else math.nan
        summary_rows.append(
            f"| `{r['file']}` | {sim} | {r.get('motor') or '–'} ({r.get('motor_source', '')}) | "
            f"{fmt(ap, 0)} | {fmt_delta(ap_b, ap, 0) if ap_b is not None else ('new' if base_label else '–')} | "
            f"{fmt(stab, 2)} | {'; '.join(flags) if flags else '✅'} |")
        # detail table
        detail.append(f"<details><summary><code>{r['file']}</code> · {sim} · motor {r.get('motor') or '–'}"
                      f" ({r.get('motor_source', '')}{', ' + r['motor_file'] if r.get('motor_file') else ''})</summary>")
        detail.append("")
        if b is None and base_label:
            detail.append("_New in this range (no base version)._")
        detail.append("| Metric | Before | After | Δ |" if base_label else "| Metric | Value |")
        detail.append("|---|---:|---:|---:|" if base_label else "|---|---:|")
        for key, label, unit, dec in METRICS:
            hv = r["metrics"].get(key, math.nan) if r["metrics"] else math.nan
            u = f" {unit}" if unit else ""
            if base_label:
                bv = b["metrics"].get(key, math.nan) if b and b.get("metrics") else math.nan
                detail.append(f"| {label}{u} | {fmt(bv, dec)} | {fmt(hv, dec)} | {fmt_delta(bv, hv, dec)} |")
            else:
                detail.append(f"| {label}{u} | {fmt(hv, dec)} |")
        if r.get("warnings"):
            detail.append("")
            detail.append(f"OpenRocket warnings: {r['warnings']}")
        detail.append("")
        detail.append("</details>")
        detail.append("")

    lines.append("| File | Simulation | Motor | Apogee (m) | Δ apogee | Stability off rod (cal) | Status |")
    lines.append("|---|---|---|---:|---:|---:|---|")
    lines.extend(summary_rows)
    lines.append("")
    lines.extend(detail)
    if n_viol or n_unres:
        lines.append(f"**{n_viol} limit violation(s), {n_unres} unresolved motor(s).**"
                     + (" This check is configured to fail on limits." if strict else
                        " Informational only (set `fail_on_limits` in sim_config.json to make this fail)."))
    return "\n".join(lines) + "\n", n_viol, n_unres


# ----------------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------------
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
                                            cfg.deterministic_wind)
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
    if not targets and not deleted:
        msg = "No .ork files changed in this range; nothing to simulate."
        print(msg)
        _emit(f"## OpenRocket simulation check\n\n{msg}\n", args)
        Path(args.results).mkdir(parents=True, exist_ok=True)
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
    report, n_viol, n_unres = render_report(head_recs, base_recs if base else None, base_label,
                                            head_sha, motor_changed, deleted, strict, cfg.deterministic_wind)
    out = Path(args.results)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(report, encoding="utf-8")
    (out / "results.json").write_text(json.dumps({"base": base_sha, "head": head_sha, "before": base_recs,
                                                  "after": head_recs}, indent=2), encoding="utf-8")
    write_csv(base_recs + head_recs, out / "results.csv")
    _emit(report, args)
    print(f"\nWrote {out / 'report.md'}, {out / 'results.csv'}, {out / 'results.json'}")
    if strict and (n_viol or n_unres):
        print(f"FAIL: {n_viol} limit violation(s), {n_unres} unresolved motor(s)")
        return 2
    return 0


def _emit(report: str, args):
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write(report)
    if not args.quiet:
        print("\n" + report)


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
    c.set_defaults(fn=cmd_compare)
    args = p.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()

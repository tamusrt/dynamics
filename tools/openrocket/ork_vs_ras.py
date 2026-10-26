#!/usr/bin/env python3
"""
ork_vs_ras.py - is a RASAero II model (.CDX1) still the same rocket as its OpenRocket model (.ork)?

The two are kept in step by hand, so a change made in one is easy to forget in the other. This
reads both files and checks what both of them describe: the outer shape (nose, body sections,
transitions/boattail, overall length), the fins, the launch mass and CG, and the launch site.
No OpenRocket, no RASAero, standard library only.

    python ork_vs_ras.py rocket.ork rocket.CDX1 [--units imperial] [--ras-sim NAME] [--table] [--strict]

    result = compare_files(ork_path, cdx_path)   # structured, SI values
    lines = describe(result, units="imperial")   # human-readable sentences

How the two models are lined up:
- Shape: both become an outer profile measured from the nose tip, and touching body tubes of the
  same diameter are merged. RASAero is usually built with fewer, longer tubes than OpenRocket
  (one tube for "plumbing + Ox Tank"), and where a tube is split is not a difference in the rocket.
- Fins: paired in nose-to-tail order and compared by absolute leading-edge station, not by the
  tube they hang off.
- Mass and CG: per OpenRocket simulation, from the first data point of its stored results (no JVM),
  so they are only as current as the last time the file was simulated; a stale one is flagged.
  RASAero has a launch weight and CG per simulation row, often for older motors, so each OpenRocket
  simulation is compared with the row named with --ras-sim, or else the closest one. Both are the fully
  loaded launch values: RASAero does not compute its CG, it is typed in from OpenRocket's at t=0 for that
  motor, so a difference means one of the two was not updated.
- Launch site differences are notes, not mismatches: the OpenRocket file usually holds several
  simulations with different wind and rod angle, and RASAero holds one site.
- RASAero files are read as English units (in, lb, ft, °F, mph, inHg), the RASAero II default. Fin
  <Location> is read as the distance from the aft end of its body tube to the root leading edge.
"""

import argparse
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ork_diff  # noqa: E402  (load_ork_xml, git_version, format_value)

IN, LB, FT, MPH, INHG = 0.0254, 0.45359237, 0.3048, 0.44704, 3386.389

# what counts as "the same": lengths to 1 mm (RASAero is typed to 0.01 in), mass to 1 %, CG to 0.5 in
TOL_LENGTH = 0.001
TOL_MASS_REL = 0.01
TOL_CG = 0.0127
# OpenRocket simulation statuses whose stored data is the saved design's ("loaded": read from the file as saved)
FRESH = ("uptodate", "loaded")
# launch site: (ork_diff field tag for display, tolerance in SI)
SITE = {"launchaltitude": 5.0, "launchrodlength": 0.05, "launchrodangle": 0.1, "windaverage": 0.1,
        "basetemperature": 0.5, "basepressure": 200.0}

# ork <crosssection> -> nearest RASAero <AirfoilSection>; anything else is shown, never failed
SECTIONS = {"square": "square", "rounded": "rounded", "airfoil": "subsonic naca"}


# ----------------------------------------------------------------------------
# reading: both files become {"segments", "fins", "masses", "site", "finish", "notes"} in SI
# ----------------------------------------------------------------------------
def _num(text):
    """'0.0762' or 'auto 0.0762' -> 0.0762; 'auto' alone -> None."""
    t = (text or "").replace("auto", "").strip()
    return float(t) if t else None


def _shape(name: str, param=None) -> str:
    """Nose/transition shape in one vocabulary for both programs."""
    n = (name or "").strip().lower()
    p = float(param) if param not in (None, "") else None
    if n == "haack":
        return "Von Karman" if not p else "LV-Haack" if abs(p - 1 / 3) < 0.01 else f"Haack C={p:g}"
    if n == "ogive":
        return "tangent ogive" if p is None or abs(p - 1) < 1e-6 else f"secant ogive {p:g}"
    if n in ("power", "parabolic"):
        return f"{n} {p:g}" if p is not None else n
    return {"conical": "conical", "ellipsoid": "elliptical", "von karman ogive": "Von Karman",
            "lv-haack": "LV-Haack", "tangent ogive": "tangent ogive", "elliptical": "elliptical"}.get(n, name)


def _fin_le(method: str, off: float, root: float, x0: float, x1: float) -> float:
    """Absolute station of a fin's root leading edge from OpenRocket's position relative to its tube."""
    return {"top": x0 + off, "bottom": x1 + off - root, "middle": (x0 + x1) / 2 + off - root / 2,
            "absolute": off, "after": x1 + off}.get(method, x0 + off)


def read_ork(root: ET.Element) -> dict:
    segs, fins, notes = [], [], []
    x = 0.0
    stages = list(root.find("rocket/subcomponents"))
    if len(stages) > 1:
        notes.append(f"OpenRocket model has {len(stages)} stages; they are laid end to end and boosters are not compared")
    for stage in stages:
        stage_x0 = x
        for el in stage.find("subcomponents") if stage.find("subcomponents") is not None else []:
            if el.tag not in ("nosecone", "bodytube", "transition"):
                continue
            off_el = el.find("axialoffset")
            method = off_el.get("method", "after") if off_el is not None else "after"
            off = float(off_el.text) if off_el is not None else 0.0
            length = float(el.findtext("length"))
            x0 = {"after": x + off, "top": stage_x0 + off, "absolute": off}.get(method, x + off)
            prev_d = segs[-1]["d1"] if segs else 0.0
            name = (el.findtext("name") or el.tag).strip()
            if el.tag == "nosecone":
                d0, d1 = 0.0, 2 * (_num(el.findtext("aftradius")) or prev_d / 2)
                kind, shape = "nose", _shape(el.findtext("shape"), el.findtext("shapeparameter"))
            elif el.tag == "bodytube":
                d0 = d1 = 2 * (_num(el.findtext("radius")) or prev_d / 2)
                kind, shape = "tube", None
            else:
                d0 = 2 * (_num(el.findtext("foreradius")) or prev_d / 2)
                d1 = 2 * (_num(el.findtext("aftradius")) or d0 / 2)
                kind, shape = "transition", _shape(el.findtext("shape"), el.findtext("shapeparameter"))
            segs.append({"kind": kind, "name": name, "x0": x0, "x1": x0 + length, "d0": d0, "d1": d1,
                         "shape": shape, "finish": (el.findtext("finish") or "").strip()})
            x = x0 + length
            sub = el.find("subcomponents")
            for f in sub if sub is not None else []:
                if f.tag == "trapezoidfinset":
                    fo = f.find("axialoffset")
                    root_c = float(f.findtext("rootchord"))
                    fins.append({"name": (f.findtext("name") or "").strip(), "parent": name,
                                 "count": int(f.findtext("fincount")), "root": root_c,
                                 "tip": float(f.findtext("tipchord")), "span": float(f.findtext("height")),
                                 "sweep": float(f.findtext("sweeplength")), "thickness": float(f.findtext("thickness")),
                                 "le": _fin_le(fo.get("method", "top") if fo is not None else "top",
                                               float(fo.text) if fo is not None else 0.0, root_c, x0, x0 + length),
                                 "section": SECTIONS.get((f.findtext("crosssection") or "").strip(),
                                                         (f.findtext("crosssection") or "").strip())})
                elif f.tag.endswith("finset"):
                    notes.append(f"{f.tag} “{(f.findtext('name') or '').strip()}” on {name} is not compared "
                                 "(RASAero only models trapezoidal fins)")

    motors = {}
    for m in root.iter("motor"):
        motors.setdefault(m.get("configid"), []).append((m.findtext("designation") or "").strip())
    masses = []
    for s in root.iter("simulation"):
        cond = s.find("conditions")
        db = s.find(".//databranch")
        if db is None or db.find("datapoint") is None:
            continue
        cols = db.get("types", "").split(",")
        first = db.find("datapoint").text.split(",")
        get = lambda col: float(first[cols.index(col)]) if col in cols else math.nan  # noqa: E731
        cid = cond.findtext("configid") if cond is not None else None
        masses.append({"name": (s.findtext("name") or "").strip(), "status": s.get("status", ""),
                       "motor": " + ".join(motors.get(cid, [])), "mass": get("Mass"), "cg": get("CG location")})

    site = []
    for s in root.iter("simulation"):
        c = s.find("conditions")
        if c is None:
            continue
        row = {k: _num(c.findtext(k)) for k in ("launchaltitude", "launchrodlength", "launchrodangle", "windaverage")}
        atm = c.find("atmosphere")
        if atm is not None and atm.get("model") != "isa":
            row["basetemperature"] = _num(atm.findtext("basetemperature"))
            row["basepressure"] = _num(atm.findtext("basepressure"))
        site.append(row)

    finishes = sorted({s["finish"] for s in segs if s["finish"]})
    return {"segments": segs, "fins": fins, "masses": masses, "site": site, "finish": ", ".join(finishes), "notes": notes}


def read_ras(root: ET.Element) -> dict:
    rd = root.find("RocketDesign")
    segs, fins, notes = [], [], []
    f = lambda el, tag: float(el.findtext(tag) or 0)  # noqa: E731
    for el in rd:
        if el.tag not in ("NoseCone", "BodyTube", "BoatTail", "Transition"):
            continue
        x0, length, d = f(el, "Location") * IN, f(el, "Length") * IN, f(el, "Diameter") * IN
        seg = {"name": f"{el.tag} @ {f(el, 'Location'):g} in", "x0": x0, "x1": x0 + length, "shape": None}
        if el.tag == "NoseCone":
            seg.update(kind="nose", d0=0.0, d1=d, shape=_shape(el.findtext("Shape")))
        elif el.tag == "BodyTube":
            seg.update(kind="tube", d0=d, d1=d)
            for fin in el.findall("Fin"):
                root_c = f(fin, "Chord") * IN
                fins.append({"name": f"fins on {seg['name']}", "parent": seg["name"], "count": int(f(fin, "Count")),
                             "root": root_c, "tip": f(fin, "TipChord") * IN, "span": f(fin, "Span") * IN,
                             "sweep": f(fin, "SweepDistance") * IN, "thickness": f(fin, "Thickness") * IN,
                             "le": seg["x1"] - f(fin, "Location") * IN,
                             "section": (fin.findtext("AirfoilSection") or "").strip().lower()})
        else:
            seg.update(kind="transition", d0=d, d1=f(el, "RearDiameter") * IN, shape="conical")
        segs.append(seg)
    if (rd.findtext("UseBooster1") or "").lower() == "true":
        notes.append("RASAero model uses boosters; only the sustainer is compared")

    masses = [{"name": (s.findtext("SustainerEngine") or "").strip() or f"row {i} (no motor)", "status": "",
               "motor": (s.findtext("SustainerEngine") or "").split("(")[0].strip(),
               "mass": f(s, "SustainerLaunchWt") * LB, "cg": f(s, "SustainerCG") * IN}
              for i, s in enumerate(root.iter("Simulation"), 1)]
    ls = root.find("LaunchSite")
    site = [{"launchaltitude": f(ls, "Altitude") * FT, "launchrodlength": f(ls, "RodLength") * FT,
             "launchrodangle": f(ls, "RodAngle"), "windaverage": f(ls, "WindSpeed") * MPH,
             "basetemperature": (f(ls, "Temperature") - 32) * 5 / 9 + 273.15,
             "basepressure": f(ls, "Pressure") * INHG}] if ls is not None else []
    return {"segments": segs, "fins": fins, "masses": masses, "site": site,
            "finish": (rd.findtext("Surface") or "").strip(), "notes": notes}


# ----------------------------------------------------------------------------
# comparing
# ----------------------------------------------------------------------------
def merge_tubes(segs: list) -> list:
    """Touching body tubes of the same diameter become one section."""
    out = []
    for s in segs:
        p = out[-1] if out else None
        if p and s["kind"] == p["kind"] == "tube" and abs(s["d0"] - p["d0"]) < TOL_LENGTH and abs(s["x0"] - p["x1"]) < TOL_LENGTH:
            p["x1"], p["last"] = s["x1"], s["name"]
        else:
            out.append(dict(s))
    return out


def _check(section, what, field, ork, ras, tol, soft=False) -> dict:
    """One compared quantity. field is an ork_diff field tag, which picks the display units."""
    if isinstance(ork, str) or isinstance(ras, str):
        same = (ork or "").lower() == (ras or "").lower()
    else:
        same = ork is not None and ras is not None and abs(ork - ras) <= tol
    return {"section": section, "what": what, "field": field, "ork": ork, "ras": ras,
            "status": "ok" if same else "note" if soft else "mismatch"}


def compare(ork: dict, ras: dict, ras_sim: str = None, ork_sim: str = None) -> dict:
    checks, notes = [], ork["notes"] + ras["notes"]

    # --- shape
    o, r = merge_tubes(ork["segments"]), merge_tubes(ras["segments"])
    checks.append(_check("Shape", "overall length", "length", o[-1]["x1"] if o else None, r[-1]["x1"] if r else None, TOL_LENGTH))
    if [s["kind"] for s in o] == [s["kind"] for s in r]:
        for a, b in zip(o, r):
            label = f"{a['kind']} “{a['name']}”" + (f" to “{a['last']}”" if a.get("last") else "")
            checks.append(_check("Shape", f"{label} length", "length", a["x1"] - a["x0"], b["x1"] - b["x0"], TOL_LENGTH))
            if a["kind"] == "tube":
                checks.append(_check("Shape", f"{label} diameter", "length", a["d0"], b["d0"], TOL_LENGTH))
            else:
                if a["kind"] == "transition":
                    checks.append(_check("Shape", f"{label} fore diameter", "length", a["d0"], b["d0"], TOL_LENGTH))
                checks.append(_check("Shape", f"{label} aft diameter", "length", a["d1"], b["d1"], TOL_LENGTH))
                checks.append(_check("Shape", f"{label} shape", "shape", a["shape"], b["shape"], 0,
                                     soft=a["kind"] == "transition"))
    else:
        seq = lambda segs: " → ".join(f"{s['kind']} {s['d1'] / IN:.2f} in" for s in segs)  # noqa: E731
        checks.append(_check("Shape", "section breakdown", "text", seq(o), seq(r), 0))
    checks.append(_check("Shape", "surface finish", "text", ork["finish"], ras["finish"], 0, soft=True))

    # --- fins, nose to tail
    of, rf = sorted(ork["fins"], key=lambda f: f["le"]), sorted(ras["fins"], key=lambda f: f["le"])
    if len(of) != len(rf):
        checks.append(_check("Fins", "number of fin sets", "count", float(len(of)), float(len(rf)), 0))
    for a, b in zip(of, rf):
        label = f"“{a['name']}”"
        checks.append(_check("Fins", f"{label} fin count", "fincount", float(a["count"]), float(b["count"]), 0))
        for key, what in (("root", "root chord"), ("tip", "tip chord"), ("span", "span"), ("sweep", "sweep length"),
                          ("thickness", "thickness"), ("le", "leading-edge station")):
            checks.append(_check("Fins", f"{label} {what}", "length", a[key], b[key], TOL_LENGTH))
        checks.append(_check("Fins", f"{label} cross-section", "text", a["section"], b["section"], 0, soft=True))

    # --- launch mass and CG: every OpenRocket simulation against one RASAero row (these checks carry "sim")
    sims = [m for m in ork["masses"] if not ork_sim or m["name"] == ork_sim]
    if not ork["masses"]:
        notes.append("OpenRocket file has no stored simulation data, so mass and CG were not compared")
    named = [m for m in ras["masses"] if ras_sim and ras_sim.lower() in m["name"].lower()]
    if ras_sim and not named:
        notes.append(f"no RASAero simulation matches --ras-sim {ras_sim!r}; each was compared with the closest one instead")
    rows = {}
    for ref in sims if ras["masses"] else []:
        score = lambda m: abs(m["mass"] - ref["mass"]) / (TOL_MASS_REL * ref["mass"]) + abs(m["cg"] - ref["cg"]) / TOL_CG  # noqa: E731
        row = rows[ref["name"]] = (named or sorted(ras["masses"], key=score))[0]
        stale = ref["status"] not in FRESH
        what = f"(vs RASAero “{row['name']}”" + (f"; OpenRocket's stored results are {ref['status'] or 'missing'}" if stale else "") + ")"
        for c in (_check("Mass & CG", f"launch mass {what}", "mass", ref["mass"], row["mass"], TOL_MASS_REL * ref["mass"]),
                  _check("Mass & CG", f"fully loaded CG from nose tip {what}", "length", ref["cg"], row["cg"], TOL_CG)):
            checks.append({**c, "sim": ref["name"]})
        if stale:
            notes.append(f"OpenRocket simulation “{ref['name']}” is {ref['status'] or 'not run'}: its mass and CG may not be "
                         "the current design's. Re-run it in OpenRocket and save.")

    # --- launch site: the RASAero value should be one of the OpenRocket simulations' values
    if ork["site"] and ras["site"]:
        for key, tol in SITE.items():
            vals = [s[key] for s in ork["site"] if s.get(key) is not None]
            rv = ras["site"][0].get(key)
            if not vals or rv is None:
                continue
            near = min(vals, key=lambda v: abs(v - rv))
            checks.append(_check("Launch site", ork_diff.field_label(key), key, near, rv, tol, soft=True))

    return {"checks": checks, "notes": notes, "ras_rows": rows, "ras_masses": ras["masses"],
            "ork_sections": o, "ras_sections": r}


def load_cdx(path) -> ET.Element:
    try:
        return ET.parse(path).getroot()
    except ET.ParseError as e:
        raise SystemExit(f"{path}: not a readable RASAero file (broken XML: {e}). Open it in RASAero II and save it again.")


def compare_files(ork_path, cdx_path, ras_sim=None, ork_sim=None) -> dict:
    return compare(read_ork(ork_diff.load_ork_xml(ork_path)), read_ras(load_cdx(cdx_path)), ras_sim, ork_sim)


def mismatches(result: dict, sim=False) -> list:
    """What disagrees. sim=False: the whole design's (shape, fins); None: everything; a name: that simulation's."""
    return [c for c in result["checks"] if c["status"] == "mismatch" and (sim is None or c.get("sim") == (sim or None))]


def sim_names(result: dict) -> list:
    return list(dict.fromkeys(c["sim"] for c in result["checks"] if c.get("sim")))


# ----------------------------------------------------------------------------
# words
# ----------------------------------------------------------------------------
def _val(c: dict, v, units: str) -> str:
    if v is None:
        return "(missing)"
    if isinstance(v, str):
        return v or "(none)"
    return ork_diff.format_value(c["field"], repr(v), units)


def _line(c: dict, units: str) -> str:
    out = f"{c['what']}: OpenRocket {_val(c, c['ork'], units)}, RASAero {_val(c, c['ras'], units)}"
    if c["field"] in ("length", "mass") and not isinstance(c["ork"], str) and None not in (c["ork"], c["ras"]):
        out += f" (RASAero − OpenRocket = {_val(c, c['ras'] - c['ork'], units)})"
    return out


def mismatch_lines(result: dict, units: str = "metric", sim=False) -> list:
    """Only what disagrees, one sentence each (the site's (i) hover text); sim as in mismatches()."""
    return [_line(c, units) for c in mismatches(result, sim)]


def headline(result: dict) -> str:
    bad = len(mismatches(result, None))
    soft = sum(1 for c in result["checks"] if c["status"] == "note")
    if not bad:
        return "consistent" + (f" ({soft} note{'s' * (soft != 1)})" if soft else "")
    return f"{bad} mismatch{'es' * (bad != 1)}" + (f", {soft} note{'s' * (soft != 1)}" if soft else "")


def describe(result: dict, units: str = "metric") -> list:
    """Mismatches first (the design's, then each simulation's), then notes; matching quantities are left to --table."""
    lines = mismatch_lines(result, units)
    lines += [f"simulation “{s}”: {line}" for s in sim_names(result) for line in mismatch_lines(result, units, s)]
    lines += ["(note) " + _line(c, units) for c in result["checks"] if c["status"] == "note"]
    lines += ["(note) " + n for n in result["notes"]]
    return lines


def main():
    p = argparse.ArgumentParser(description="Check that a RASAero II model matches its OpenRocket model.",
                                epilog="Example:  ork_vs_ras.py aero_modeling/X/OpenRocket/X.ork aero_modeling/X/RASAero/X.CDX1 --units imperial --table",
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("ork", type=Path)
    p.add_argument("cdx", type=Path)
    p.add_argument("--units", choices=["metric", "imperial"], default="metric")
    p.add_argument("--ork-sim", metavar="NAME", help="Compare mass and CG for this OpenRocket simulation only (default: every one)")
    p.add_argument("--ras-sim", metavar="TEXT", help="RASAero simulation row to compare, by motor name (default: the closest)")
    p.add_argument("--table", action="store_true", help="Also print every compared quantity, matching or not")
    p.add_argument("--strict", action="store_true", help="Exit with status 1 if anything mismatches")
    a = p.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    res = compare_files(a.ork, a.cdx, a.ras_sim, a.ork_sim)
    print(headline(res))
    for line in describe(res, a.units):
        print(" -", line)
    if a.table:
        print()
        for c in res["checks"]:
            where = f"{c['section']} “{c['sim']}”" if c.get("sim") else c["section"]
            print(f"   [{c['status']:>8}] {where} | {c['what']} | {_val(c, c['ork'], a.units)} | {_val(c, c['ras'], a.units)}")
        if len(res["ras_masses"]) > 1:
            print("\n   RASAero simulation rows:")
            for m in res["ras_masses"]:
                print(f"     {m['name']}: {ork_diff.format_value('mass', repr(m['mass']), a.units)}, "
                      f"CG {ork_diff.format_value('length', repr(m['cg']), a.units)}")
    if a.strict and mismatches(res, None):
        sys.exit(1)


if __name__ == "__main__":
    main()

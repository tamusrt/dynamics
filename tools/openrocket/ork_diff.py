#!/usr/bin/env python3
"""
ork_diff.py - what changed between two OpenRocket (.ork) files, in words.

An .ork is a zip around one XML file, and every component in it carries a stable UUID, so
two versions can be compared exactly: which components were added, removed, moved or edited,
which fields changed from what to what, and which simulations or launch conditions changed.
No OpenRocket, no Java, standard library only.

    python ork_diff.py old.ork new.ork [--units imperial]

    diff = diff_files(old_path, new_path)      # structured, SI values
    lines = describe(diff, units="imperial")   # human-readable sentences

`diff` is plain data (dicts and lists), so it can be cached as JSON, rendered by the CI
report and the website, or handed to a language model to narrate.
"""

import argparse
import math
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

# Leaf fields that are cosmetic, duplicate another field, or are internal ids; never reported.
IGNORED_FIELDS = {"id", "color", "linestyle", "position", "decal", "appearance", "insideappearance", "preset", "configid"}

# xml tag -> (label, kind). kind picks the unit conversion in format_value().
FIELDS = {
    "length": ("length", "length"), "radius": ("outer radius", "length"), "outerradius": ("outer radius", "length"),
    "innerradius": ("inner radius", "length"), "thickness": ("wall thickness", "length"),
    "aftradius": ("aft radius", "length"), "foreradius": ("fore radius", "length"),
    "aftshoulderradius": ("aft shoulder radius", "length"), "aftshoulderlength": ("aft shoulder length", "length"),
    "aftshoulderthickness": ("aft shoulder thickness", "length"), "foreshoulderradius": ("fore shoulder radius", "length"),
    "foreshoulderlength": ("fore shoulder length", "length"), "foreshoulderthickness": ("fore shoulder thickness", "length"),
    "rootchord": ("root chord", "length"), "tipchord": ("tip chord", "length"), "sweeplength": ("sweep length", "length"),
    "height": ("span", "length"), "filletradius": ("fillet radius", "length"), "tabheight": ("tab height", "length"),
    "tablength": ("tab length", "length"), "axialoffset": ("position", "length"), "radialposition": ("radial position", "length"),
    "packedlength": ("packed length", "length"), "packedradius": ("packed radius", "length"),
    "diameter": ("diameter", "length"), "deployaltitude": ("deployment altitude", "distance"),
    "cordlength": ("cord length", "length"), "linelength": ("line length", "length"), "overhang": ("motor overhang", "length"),
    "mass": ("mass", "mass"), "overridemass": ("mass override", "mass"), "overridecg": ("CG override", "length"),
    "overridecd": ("Cd override", "number"), "cd": ("Cd", "number"), "fincount": ("fin count", "count"),
    "instancecount": ("instance count", "count"), "linecount": ("line count", "count"), "cant": ("cant angle", "degrees"),
    "rotation": ("rotation", "degrees"), "shapeparameter": ("shape parameter", "number"), "shape": ("shape", "text"),
    "finish": ("surface finish", "text"), "material": ("material", "text"), "crosssection": ("fin cross-section", "text"),
    "deployevent": ("deployment event", "text"), "deploydelay": ("deployment delay", "seconds"),
    "masscomponenttype": ("mass type", "text"), "comment": ("comment", "text"),
    # simulation conditions
    "launchrodlength": ("launch rod length", "distance"), "launchrodangle": ("launch rod angle", "degrees"),
    "launchroddirection": ("launch rod direction", "degrees"), "launchintowind": ("launch into wind", "text"),
    "windaverage": ("average wind", "speed"), "windturbulence": ("wind turbulence", "number"),
    "winddirection": ("wind direction", "radians"), "launchaltitude": ("launch altitude", "distance"),
    "launchlatitude": ("launch latitude", "number"), "launchlongitude": ("launch longitude", "number"),
    "timestep": ("time step", "seconds"), "maxtime": ("max simulation time", "seconds"),
    "configid": ("flight configuration", "text"), "windmodeltype": ("wind model", "text"),
    "basetemperature": ("base temperature", "kelvin"), "basepressure": ("base pressure", "pascal"),
}
KIND_NAMES = {"nosecone": "nose cone", "bodytube": "body tube", "transition": "transition", "trapezoidfinset": "fin set",
              "ellipticalfinset": "fin set", "freeformfinset": "fin set", "tubefinset": "tube fin set",
              "masscomponent": "mass component", "parachute": "parachute", "streamer": "streamer", "shockcord": "shock cord",
              "innertube": "inner tube", "tubecoupler": "tube coupler", "centeringring": "centering ring",
              "bulkhead": "bulkhead", "engineblock": "engine block", "launchlug": "launch lug", "railbutton": "rail button",
              "stage": "stage", "boosterset": "booster set", "podset": "pod set"}


# ----------------------------------------------------------------------------
# reading
# ----------------------------------------------------------------------------
def load_ork_xml(path) -> ET.Element:
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        return ET.fromstring(zf.read("rocket.ork" if "rocket.ork" in names else names[0]))


def _leaf_value(el: ET.Element) -> str:
    """A leaf's text plus the attributes that change its meaning (material density, offset method)."""
    text = (el.text or "").strip()
    extra = [f"{k}={v}" for k, v in sorted(el.attrib.items()) if k in ("density", "method", "type") and el.tag != "material"]
    if el.tag == "material" and el.get("density"):
        extra = [f"density={el.get('density')}"]
    return text + (" [" + ", ".join(extra) + "]" if extra else "")


def _fields_of(el: ET.Element) -> dict:
    out = {}
    for ch in el:
        if ch.tag in IGNORED_FIELDS or ch.tag in ("name", "subcomponents"):
            continue
        if len(ch) == 0:
            out[ch.tag] = _leaf_value(ch)
        elif ch.tag == "motormount":
            for m in ch.findall("motor"):
                out[f"motor[{(m.get('configid') or '')[:8]}]"] = " ".join(
                    x for x in ((m.findtext("manufacturer") or "").strip(), (m.findtext("designation") or "").strip()) if x)
            for leaf in ch:
                if len(leaf) == 0:
                    out[f"mount.{leaf.tag}"] = _leaf_value(leaf)
        elif ch.tag == "atmosphere":
            for leaf in ch:
                if len(leaf) == 0:
                    out[leaf.tag] = _leaf_value(leaf)
    return out


def read_components(root: ET.Element) -> dict:
    """{key: {'kind','name','path','parent','fields'}} for every component, keyed by its UUID."""
    comps = {}

    def walk(container, parent_key, parent_path):
        for el in container:
            name = (el.findtext("name") or "").strip()
            key = (el.findtext("id") or "").strip() or f"{el.tag}:{parent_path}/{name}"
            path = f"{parent_path} / {name}" if parent_path else name
            comps[key] = {"kind": el.tag, "name": name, "path": path, "parent": parent_key, "fields": _fields_of(el)}
            sub = el.find("subcomponents")
            if sub is not None:
                walk(sub, key, path)

    top = root.find("rocket/subcomponents")
    if top is not None:
        walk(top, None, "")
    return comps


def read_simulations(root: ET.Element) -> dict:
    sims = {}
    for s in root.iter("simulation"):
        name = (s.findtext("name") or "").strip()
        cond = s.find("conditions")
        sims[name] = _fields_of(cond) if cond is not None else {}
    return sims


# ----------------------------------------------------------------------------
# diff
# ----------------------------------------------------------------------------
def _same(a: str, b: str) -> bool:
    if a == b:
        return True
    try:                                   # 0.07619999999999999 vs 0.0762 is not a change
        x, y = float(a.split(" [")[0]), float(b.split(" [")[0])
        return math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-12) and a.split(" [")[1:] == b.split(" [")[1:]
    except (ValueError, IndexError):
        return False


def _field_changes(old: dict, new: dict) -> list:
    out = []
    for tag in sorted(set(old) | set(new)):
        a, b = old.get(tag), new.get(tag)
        if a is None or b is None or not _same(a, b):
            # a change too small to show at display precision is not worth a line
            if a is not None and b is not None and format_value(tag, a, "imperial") == format_value(tag, b, "imperial") \
                    and format_value(tag, a, "metric") == format_value(tag, b, "metric"):
                continue
            out.append({"field": tag, "old": a, "new": b})
    # a motor swap usually arrives as "motor[configA] removed" + "motor[configB] added": report it as one change
    gone = [c for c in out if c["field"].startswith("motor[") and c["new"] is None]
    came = [c for c in out if c["field"].startswith("motor[") and c["old"] is None]
    if len(gone) == 1 and len(came) == 1:
        out = [c for c in out if c not in (gone[0], came[0])]
        if gone[0]["old"] != came[0]["new"]:
            out.append({"field": "motor[]", "old": gone[0]["old"], "new": came[0]["new"]})
    return out


def diff_roots(old_root: ET.Element, new_root: ET.Element) -> dict:
    old_c, new_c = read_components(old_root), read_components(new_root)
    components = []
    for key, c in new_c.items():
        if key not in old_c:
            components.append({"change": "added", **{k: c[k] for k in ("kind", "name", "path")}, "fields": c["fields"]})
            continue
        o = old_c[key]
        changes = _field_changes(o["fields"], c["fields"])
        renamed = o["name"] != c["name"]
        moved = (o["parent"] != c["parent"]) and not renamed
        if changes or renamed or moved:
            components.append({"change": "modified", "kind": c["kind"], "name": c["name"], "path": c["path"],
                               "old_name": o["name"] if renamed else None,
                               "moved_from": o["path"].rsplit(" / ", 1)[0] if o["parent"] != c["parent"] else None,
                               "changes": changes})
    for key, o in old_c.items():
        if key not in new_c:
            components.append({"change": "removed", **{k: o[k] for k in ("kind", "name", "path")}, "fields": o["fields"]})

    old_s, new_s = read_simulations(old_root), read_simulations(new_root)
    simulations = []
    for name in new_s:
        if name not in old_s:
            simulations.append({"change": "added", "name": name, "fields": new_s[name]})
        else:
            ch = _field_changes(old_s[name], new_s[name])
            if ch:
                simulations.append({"change": "modified", "name": name, "changes": ch})
    simulations += [{"change": "removed", "name": n, "fields": old_s[n]} for n in old_s if n not in new_s]
    return {"components": components, "simulations": simulations}


def diff_files(old_path, new_path) -> dict:
    return diff_roots(load_ork_xml(old_path), load_ork_xml(new_path))


def is_empty(diff: dict) -> bool:
    return not diff["components"] and not diff["simulations"]


# ----------------------------------------------------------------------------
# words
# ----------------------------------------------------------------------------
def format_value(field: str, raw, units: str = "metric") -> str:
    """One stored value as a person would write it, in the chosen unit system."""
    if raw is None:
        return "(none)"
    label, kind = FIELDS.get(field, (field, "text"))
    text, _, extra = raw.partition(" [")
    extra = (" [" + extra) if extra else ""
    if field == "material" and extra:
        try:
            return f"{text} ({float(extra[10:-1]):g} kg/m³)"
        except ValueError:
            return raw
    if text == "auto" or text.startswith("auto "):
        return "auto" + (f" ({format_value(field, text[5:], units)})" if len(text) > 5 else "")
    try:
        v = float(text)
    except ValueError:
        return raw
    imp = units == "imperial"
    if kind == "length":
        return (f"{v / 0.0254:.2f} in" if imp else f"{v * 1000:.1f} mm") + _method(extra)
    if kind == "distance":
        return f"{v * 3.28084:.1f} ft" if imp else f"{v:.2f} m"
    if kind == "mass":
        return f"{v * 2.2046226:.2f} lb" if imp else f"{v:.3f} kg"
    if kind == "speed":
        return f"{v * 3.28084:.1f} ft/s" if imp else f"{v:.1f} m/s"
    if kind == "degrees":
        return f"{v:g}°"
    if kind == "radians":
        return f"{math.degrees(v):.0f}°"
    if kind == "seconds":
        return f"{v:g} s"
    if kind == "kelvin":
        return f"{(v - 273.15) * 9 / 5 + 32:.0f} °F" if imp else f"{v - 273.15:.1f} °C"
    if kind == "pascal":
        return f"{v * 1.450377e-4:.2f} psi" if imp else f"{v / 1000:.1f} kPa"
    if kind == "count":
        return f"{v:g}"
    return f"{v:g}" + extra


def _method(extra: str) -> str:
    return f" from {extra[9:-1]}" if extra.startswith(" [method=") else ""


def field_label(field: str) -> str:
    if field.startswith("motor["):
        return "motor"
    if field.startswith("mount."):
        return "motor mount " + field[6:]
    return FIELDS.get(field, (field, ""))[0]


def _kind(c: dict) -> str:
    return KIND_NAMES.get(c["kind"], c["kind"])


def _facts(fields: dict, units: str) -> str:
    """The two or three numbers that identify a component when it is added or removed."""
    keys = [k for k in ("mass", "overridemass", "length", "rootchord", "height", "diameter", "axialoffset") if k in fields][:3]
    return ", ".join(f"{field_label(k)} {format_value(k, fields[k], units)}" for k in keys)


def describe(diff: dict, units: str = "metric", max_fields: int = 4) -> list:
    """Short sentences, most structural first: removed, added, then edits, then simulations."""
    lines = []
    order = {"removed": 0, "added": 1, "modified": 2}
    for c in sorted(diff["components"], key=lambda c: order[c["change"]]):
        where = c["path"].rsplit(" / ", 1)[0] if " / " in c["path"] else ""
        if c["change"] in ("added", "removed"):
            facts = _facts(c["fields"], units)
            lines.append(f"{c['change'].capitalize()} {_kind(c)} **{c['name']}**" + (f" in {where}" if where else "")
                         + (f" ({facts})" if facts else ""))
            continue
        parts = []
        if c.get("old_name"):
            parts.append(f"renamed from “{c['old_name']}”")
        if c.get("moved_from") is not None:
            parts.append(f"moved from {c['moved_from'] or 'top level'} to {where or 'top level'}")
        shown = c["changes"][:max_fields]
        for ch in shown:
            f = ch["field"]
            if ch["old"] is None:
                parts.append(f"{field_label(f)} set to {format_value(f, ch['new'], units)}")
            elif ch["new"] is None:
                parts.append(f"{field_label(f)} removed (was {format_value(f, ch['old'], units)})")
            else:
                parts.append(f"{field_label(f)} {format_value(f, ch['old'], units)} → {format_value(f, ch['new'], units)}")
        if len(c["changes"]) > max_fields:
            parts.append(f"+{len(c['changes']) - max_fields} more")
        lines.append(f"**{c['name']}** ({_kind(c)}): " + "; ".join(parts))
    for s in diff["simulations"]:
        if s["change"] == "added":
            lines.append(f"Simulation **{s['name']}** added")
        elif s["change"] == "removed":
            lines.append(f"Simulation **{s['name']}** removed")
        else:
            parts = [f"{field_label(ch['field'])} {format_value(ch['field'], ch['old'], units)} → {format_value(ch['field'], ch['new'], units)}"
                     for ch in s["changes"][:max_fields]]
            if len(s["changes"]) > max_fields:
                parts.append(f"+{len(s['changes']) - max_fields} more")
            lines.append(f"Simulation **{s['name']}**: " + "; ".join(parts))
    return lines


def headline(diff: dict) -> str:
    n = {k: sum(1 for c in diff["components"] if c["change"] == k) for k in ("added", "removed", "modified")}
    bits = [f"{n[k]} {k}" for k in ("modified", "added", "removed") if n[k]]
    out = (", ".join(bits) + " component" + ("s" if sum(n.values()) != 1 else "")) if bits else ""
    if diff["simulations"]:
        out += ("; " if out else "") + f"{len(diff['simulations'])} simulation change" + ("s" if len(diff["simulations"]) != 1 else "")
    return out or "no design changes (only stored results or metadata)"


def rows(diff: dict, units: str = "metric") -> list:
    """Every field change as (where, what, before, after) strings, for a raw table."""
    out = []
    for c in diff["components"]:
        if c["change"] == "modified":
            if c.get("old_name"):
                out.append((c["path"], "name", c["old_name"], c["name"]))
            if c.get("moved_from") is not None:
                out.append((c["path"], "parent", c["moved_from"] or "top level", c["path"].rsplit(" / ", 1)[0]))
            out += [(c["path"], field_label(ch["field"]), format_value(ch["field"], ch["old"], units),
                     format_value(ch["field"], ch["new"], units)) for ch in c["changes"]]
        else:
            before = c["change"] == "removed"
            for f, v in c["fields"].items():
                val = format_value(f, v, units)
                out.append((c["path"], field_label(f), val if before else "–", "–" if before else val))
    for s in diff["simulations"]:
        where = f"simulation “{s['name']}”"
        if s["change"] == "modified":
            out += [(where, field_label(ch["field"]), format_value(ch["field"], ch["old"], units),
                     format_value(ch["field"], ch["new"], units)) for ch in s["changes"]]
        else:
            out.append((where, s["change"], "", ""))
    return out


def main():
    p = argparse.ArgumentParser(description="What changed between two OpenRocket files.")
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("--units", choices=["metric", "imperial"], default="metric")
    p.add_argument("--table", action="store_true", help="Also print every changed field")
    a = p.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    d = diff_files(Path(a.old), Path(a.new))
    print(headline(d))
    for line in describe(d, a.units):
        print(" -", line.replace("**", ""))
    if a.table:
        for where, what, before, after in rows(d, a.units):
            print(f"   {where} | {what} | {before} -> {after}")


if __name__ == "__main__":
    main()

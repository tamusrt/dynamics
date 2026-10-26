"""Geometry of an OpenRocket design, resolved to absolute axial positions, as JSON.

    python ork_geometry.py design.ork [out.json]

Output (all lengths in metres, x measured from the nose tip, aft positive):
{
  "name", "file", "or_version", "length", "max_radius",
  "components": [ { "id", "kind", "name", "parent", "depth", "external": bool,
                    "x0", "x1", "length", "r_fore", "r_aft", "offset": {"method", "value"},
                    "profile": [[x, r], ...]          (nosecone / transition outer line, x absolute),
                    "fins": {"count", "root", "tip", "sweep", "span", "thickness", "cant", "points": [[x_rel, y], ...]},
                    "radius": r, "inner_radius": r      (inner tubes, couplers, rings, bulkheads, masses),
                    "mass": kg | null, "fields": {raw leaf fields of the element} } ... ]
}
Positioning follows OpenRocket: body components (nose cone, body tubes, transitions) stack nose to tail;
other parts sit relative to their parent by method top / bottom / middle / absolute; "auto" radii inherit
from the neighbouring body component (or the parent's inner radius for internal parts).
"""
import json
import math
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

BODY = {"nosecone", "bodytube", "transition"}
KINDS = {"nosecone": "nose cone", "bodytube": "body tube", "transition": "transition", "trapezoidfinset": "fin set",
         "ellipticalfinset": "fin set", "freeformfinset": "fin set", "tubefinset": "tube fin set",
         "masscomponent": "mass component", "parachute": "parachute", "streamer": "streamer", "shockcord": "shock cord",
         "innertube": "inner tube", "tubecoupler": "tube coupler", "centeringring": "centering ring",
         "bulkhead": "bulkhead", "engineblock": "engine block", "launchlug": "launch lug", "railbutton": "rail button",
         "stage": "stage", "boosterset": "booster set", "podset": "pod set", "parallelstage": "booster"}
FINS = {"trapezoidfinset", "ellipticalfinset", "freeformfinset"}


def num(el, tag, default=None):
    e = el.find(tag)
    if e is None or e.text is None:
        return default
    t = e.text.strip()
    if t.startswith("auto"):   # OpenRocket 24.12 writes "auto 0.0762": auto, with the value it resolved to
        return "auto"
    if t == "":
        return default
    try:
        return float(t)
    except ValueError:
        return t


def leaf_fields(el):
    out = {}
    for c in el:
        if c.tag in ("subcomponents", "finpoints", "motormount", "appearance", "insideappearance", "material"):
            if c.tag == "material":
                out["material"] = (c.text or "").strip()
            continue
        if len(c) == 0 and c.text is not None and c.text.strip():
            out[c.tag] = c.text.strip() + ("".join(f" ({k}={v})" for k, v in c.attrib.items()) if c.attrib else "")
    return out


def nose_profile(shape, param, L, R, n=40):
    """Outer radius at n+1 stations from tip (x=0) to base (x=L) for OpenRocket's nose cone shapes."""
    pts = []
    for i in range(n + 1):
        x = L * i / n
        t = x / L if L else 1.0
        if shape == "conical":
            r = R * t
        elif shape == "ogive":
            k = param if param not in (None, 0) else 1.0      # 1 = tangent ogive
            rho = (R * R + L * L) / (2 * R) if R else 1.0
            # OpenRocket blends secant ogives by k; the tangent ogive is the common case
            r = math.sqrt(max(rho * rho - (L - x) ** 2, 0)) + R - rho if R else 0.0
            if k < 1:  # secant: scale towards the conical line
                r = k * r + (1 - k) * R * t
        elif shape == "ellipsoid":
            r = R * math.sqrt(max(1 - (1 - t) ** 2, 0))
        elif shape == "power":
            p = param if param else 0.5
            r = R * t ** p
        elif shape == "parabolic":
            k = param if param is not None else 1.0
            r = R * (2 * t - k * t * t) / (2 - k) if (2 - k) else R * t
        elif shape == "haack":
            C = param or 0.0
            th = math.acos(max(-1.0, min(1.0, 1 - 2 * t)))
            r = R / math.sqrt(math.pi) * math.sqrt(max(th - math.sin(2 * th) / 2 + C * math.sin(th) ** 3, 0))
        else:
            r = R * t
        pts.append([x, r])
    return pts


def parse(path: Path) -> dict:
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        root = ET.fromstring(zf.read("rocket.ork" if "rocket.ork" in names else names[0]))
    rocket = root.find("rocket")
    comps = []
    cursor = {"x": 0.0, "r": 0.0}   # where the next body component starts, and the radius it inherits

    def walk(el, parent, depth, body_order):
        for c in el:
            if c.tag == "subcomponents":
                walk(c, parent, depth, body_order)
                continue
            if c.tag not in KINDS:
                continue
            rec = {"id": c.findtext("id") or f"{c.tag}-{len(comps)}", "kind": c.tag, "label": KINDS[c.tag], "name": (c.findtext("name") or c.tag).strip(),
                   "parent": parent["id"] if parent else None, "depth": depth, "external": c.tag in BODY or c.tag in FINS,
                   "fields": leaf_fields(c)}
            m = num(c, "overridemass")
            rec["mass"] = m if isinstance(m, float) else (num(c, "mass") if isinstance(num(c, "mass"), float) else None)
            off = c.find("axialoffset")
            if off is None:
                off = c.find("position")
            method = (off.get("method") or off.get("type") or "after") if off is not None else "after"
            value = float(off.text) if off is not None and off.text and off.text.strip() not in ("", "auto") else 0.0
            rec["offset"] = {"method": method, "value": value}
            if c.tag == "stage":
                rec.update(x0=cursor["x"], x1=None, length=None)
                comps.append(rec)
                walk(c, rec, depth + 1, body_order)
                rec["x1"] = cursor["x"]; rec["length"] = rec["x1"] - rec["x0"]
                continue
            if c.tag in BODY:
                L = num(c, "length", 0.0) or 0.0
                if c.tag == "nosecone":
                    r_aft = num(c, "aftradius"); r_fore = 0.0
                    if r_aft == "auto":
                        r_aft = cursor["r"] or 0.0
                    flipped = (c.findtext("isflipped") or "false").strip() == "true"
                    prof = nose_profile((c.findtext("shape") or "ogive").strip(), num(c, "shapeparameter"), L, r_aft)
                    if flipped:
                        prof = [[L - x, r] for x, r in reversed(prof)]; r_fore, r_aft = r_aft, 0.0
                elif c.tag == "bodytube":
                    r = num(c, "radius")
                    if r == "auto":
                        r = cursor["r"]
                    r_fore = r_aft = r or 0.0
                    prof = [[0.0, r_fore], [L, r_aft]]
                else:  # transition
                    r_fore, r_aft = num(c, "foreradius"), num(c, "aftradius")
                    if r_fore == "auto":
                        r_fore = cursor["r"]
                    if r_aft == "auto":
                        r_aft = r_fore   # resolved after the pass when a tube follows
                    shape = (c.findtext("shape") or "conical").strip()
                    base = nose_profile(shape, num(c, "shapeparameter"), L, 1.0)
                    if r_aft >= r_fore:   # widening: nose-shaped growth from fore to aft
                        prof = [[x, r_fore + (r_aft - r_fore) * r] for x, r in base]
                    else:                 # narrowing: mirror the shape
                        prof = [[L - x, r_aft + (r_fore - r_aft) * r] for x, r in reversed(base)]
                x0 = cursor["x"] + (value if method == "after" else 0.0)
                rec.update(x0=x0, x1=x0 + L, length=L, r_fore=r_fore, r_aft=r_aft, profile=[[x0 + x, r] for x, r in prof],
                           thickness=num(c, "thickness"))
                if c.tag == "nosecone":
                    rec["shape"] = (c.findtext("shape") or "").strip(); rec["shape_parameter"] = num(c, "shapeparameter")
                    rec["shoulder_length"] = num(c, "aftshoulderlength", 0.0); rec["shoulder_radius"] = num(c, "aftshoulderradius", 0.0)
                if c.tag == "transition":
                    rec["shape"] = (c.findtext("shape") or "").strip()
                cursor["x"] = rec["x1"]; cursor["r"] = r_aft if isinstance(r_aft, float) else cursor["r"]
                comps.append(rec)
                walk(c, rec, depth + 1, body_order)
                continue
            # everything else sits inside / on its parent
            if c.tag in FINS:
                if c.tag == "freeformfinset":
                    pts = [[float(p.get("x")), float(p.get("y"))] for p in c.find("finpoints")]
                    L = max(x for x, _ in pts) - min(x for x, _ in pts)
                    fins = {"count": int(num(c, "fincount", 3) or 3), "root": L, "tip": None, "sweep": None,
                            "span": max(y for _, y in pts), "points": pts}
                elif c.tag == "ellipticalfinset":
                    L = num(c, "rootchord", 0.0) or 0.0; H = num(c, "height", 0.0) or 0.0
                    pts = [[L / 2 + L / 2 * math.cos(math.pi * (1 - i / 20)), H * math.sin(math.pi * i / 20)] for i in range(21)]
                    fins = {"count": int(num(c, "fincount", 3) or 3), "root": L, "tip": 0.0, "sweep": L / 2, "span": H, "points": [[0, 0]] + pts + [[L, 0]]}
                else:
                    L = num(c, "rootchord", 0.0) or 0.0; T = num(c, "tipchord", 0.0) or 0.0
                    S = num(c, "sweeplength", 0.0) or 0.0; H = num(c, "height", 0.0) or 0.0
                    fins = {"count": int(num(c, "fincount", 3) or 3), "root": L, "tip": T, "sweep": S, "span": H,
                            "points": [[0, 0], [S, H], [S + T, H], [L, 0]]}
                fins["thickness"] = num(c, "thickness"); fins["cant"] = num(c, "cant", 0.0)
                fins["tab_length"] = num(c, "tablength", 0.0); fins["tab_height"] = num(c, "tabheight", 0.0)
                rec["fins"] = fins
            elif c.tag == "tubefinset":
                L = num(c, "length", 0.0) or 0.0
                rec["fins"] = {"count": int(num(c, "fincount", 6) or 6), "root": L, "span": 2 * (num(c, "outerradius", 0.0) if isinstance(num(c, "outerradius"), float) else (parent.get("r_aft") or 0.0)), "points": []}
            elif c.tag in ("masscomponent", "parachute", "streamer", "shockcord"):
                L = num(c, "packedlength", 0.0) or 0.0
                rec["radius"] = num(c, "packedradius", 0.0) or 0.0
            elif c.tag == "railbutton":
                L = num(c, "outerdiameter", 0.0) or 0.0
            else:  # innertube, tubecoupler, centeringring, bulkhead, engineblock, launchlug
                L = num(c, "length", 0.0) or 0.0
                ro = num(c, "outerradius")
                fl = lambda v: v if isinstance(v, float) else 0.0   # "auto" or missing -> 0
                pr = (fl(parent.get("r_aft")) or fl(parent.get("radius"))) - fl(parent.get("thickness")) if parent else 0.0
                rec["radius"] = pr if ro == "auto" or not isinstance(ro, float) else ro
                ri = num(c, "innerradius")
                rec["inner_radius"] = (rec["radius"] - fl(num(c, "thickness", 0.0))) if not isinstance(ri, float) else ri
                if c.tag in ("centeringring", "bulkhead", "engineblock") and ri == "auto":
                    rec["inner_radius"] = None
            px0, px1 = (parent["x0"], parent["x1"]) if parent and parent.get("x1") is not None else (0.0, cursor["x"])
            if method == "top":
                x0 = px0 + value
            elif method == "bottom":
                x0 = px1 + value - L
            elif method == "middle":
                x0 = (px0 + px1) / 2 + value - L / 2
            elif method == "absolute":
                x0 = value
            else:  # after
                x0 = px1 + value
            rec.update(x0=x0, x1=x0 + L, length=L)
            rec["radial_position"] = num(c, "radialposition", 0.0)
            comps.append(rec)
            walk(c, rec, depth + 1, body_order)

    walk(rocket, None, 0, [])
    # a transition whose aft radius was "auto" takes the radius of the body component after it
    bodies = [c for c in comps if c["kind"] in BODY]
    for a, b in zip(bodies, bodies[1:]):
        if a["kind"] == "transition" and str(a["fields"].get("aftradius", "")).startswith("auto") and b["kind"] == "bodytube":
            a["r_aft"] = b["r_fore"]; a["profile"] = [[x, r if x < a["x1"] else b["r_fore"]] for x, r in a["profile"]]
        if a["kind"] == "nosecone" and str(a["fields"].get("aftradius", "")).startswith("auto") and b["kind"] == "bodytube":
            scale = b["r_fore"] / (a["r_aft"] or 1.0); a["r_aft"] = b["r_fore"]; a["profile"] = [[x, r * scale] for x, r in a["profile"]]
    length = max((c["x1"] for c in bodies), default=0.0)
    max_r = max((max(c["r_fore"], c["r_aft"]) for c in bodies), default=0.0)
    stage_name = rocket.findtext("name") or path.stem
    return {"name": (stage_name or path.stem).strip(), "file": path.name, "or_version": root.get("version"),
            "length": length, "max_radius": max_r, "components": comps}


if __name__ == "__main__":
    src = Path(sys.argv[1])
    g = parse(src)
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_suffix(".geometry.json")
    out.write_text(json.dumps(g, indent=1), encoding="utf-8")
    print(f"{g['name']}: length {g['length']:.4f} m, max radius {g['max_radius']:.4f} m, {len(g['components'])} components -> {out}")
    for c in g["components"]:
        if c["kind"] in BODY or "fins" in c:
            print(f"  {'  ' * c['depth']}{c['kind']:16s} {c['name'][:30]:30s} x {c['x0']:.3f}..{c['x1']:.3f}" + (f"  r {c['r_fore']:.4f}->{c['r_aft']:.4f}" if 'r_fore' in c else ''))

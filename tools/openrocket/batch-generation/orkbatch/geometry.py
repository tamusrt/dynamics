"""Nose-cone volume preservation (keeps internal volume when a nose cone is
trimmed or reshaped). Ported from openrocket_batch_sim/openrocket_variant_generator.py.
All values SI (m, m^3).
"""

import math

from .params import BODY_TAGS, parse_numeric_text
from .selectors import comp_name

M_TO_IN = 1.0 / 0.0254
KNOWN_NOSE_SHAPES = {"conical", "ogive", "ellipsoid", "elliptical", "power", "parabolic", "haack"}


class GeometryError(Exception):
    pass


def num(el, tag):
    return parse_numeric_text(el.findtext(tag))[0]


def radius_of(el, side):
    """Effective radius (m) of a body component's fore or aft end (None if unknown)."""
    if el.tag == "bodytube":
        return num(el, "radius")
    if el.tag == "nosecone":
        return num(el, "aftradius") if side == "aft" else None
    if el.tag == "transition":
        return num(el, "foreradius" if side == "fore" else "aftradius")
    return None


def body_context(index, el):
    """(subcomponents element, [body components in that stage], position of el) for a body component."""
    sc = index.parent.get(el)
    if el.tag not in BODY_TAGS or sc is None or getattr(index.parent.get(sc), "tag", None) != "stage":
        raise GeometryError(f"'{comp_name(el)}' <{el.tag}> isn't a nose cone / body tube / transition "
                            f"sitting directly in a stage")
    comps = [c for c in sc if c.tag in BODY_TAGS]
    return sc, comps, comps.index(el)


# ------------------------------------------------------------ nose cone volume

def nose_profile_radius(shape, p, L, R, x):
    """Radius at axial position x (from tip) of an OpenRocket nose-cone shape."""
    if L <= 0 or R <= 0:
        return 0.0
    u = min(max(x, 0.0), L) / L
    shape = (shape or "conical").lower()
    if shape == "conical":
        return R * u
    if shape in ("ellipsoid", "elliptical"):
        return R * math.sqrt(max(0.0, 2.0 * u - u * u))
    if shape == "power":
        return R * (u ** max(p if p is not None else 0.5, 1e-6))
    if shape == "parabolic":
        k = p if p is not None else 0.5
        denom = (2.0 - k) or 1e-9
        return R * max(0.0, (2.0 * u - k * u * u) / denom)
    if shape == "haack":
        c = p if p is not None else 0.0
        th = math.acos(max(-1.0, min(1.0, 1.0 - 2.0 * u)))
        return (R / math.sqrt(math.pi)) * math.sqrt(max(0.0, th - math.sin(2 * th) / 2 + c * math.sin(th) ** 3))
    if shape == "ogive":
        # circular-arc ogive through (0,0) and (L,R); p = rho / rho_tangent
        p = p if p is not None else 1.0
        K = L * L + R * R
        rho = max(max(p, 0.0) * K / (2.0 * R), math.sqrt(K) / 2.0)
        y_c = (R - L * math.sqrt(max(0.0, 4.0 * rho * rho / K - 1.0))) / 2.0
        x_c = (K - 2.0 * R * y_c) / (2.0 * L)
        return y_c + math.sqrt(max(0.0, rho * rho - (x - x_c) ** 2))
    return R * u


def nose_volume(shape, p, L, R, n=4000):
    """Solid-of-revolution volume (m^3) by Simpson's rule."""
    if L <= 0 or R <= 0:
        return 0.0
    n += n % 2
    h = L / n
    total = 0.0
    for i in range(n + 1):
        r = nose_profile_radius(shape, p, L, R, i * h)
        w = 1 if i in (0, n) else (4 if i % 2 else 2)
        total += w * math.pi * r * r
    return total * h / 3.0


def nose_state(nc):
    return {"shape": (nc.findtext("shape") or "conical").strip().lower(),
            "param": num(nc, "shapeparameter"), "length": num(nc, "length"),
            "radius": num(nc, "aftradius")}


def lengthen_bodytube(tube, extra_m):
    L = num(tube, "length")
    if tube.tag != "bodytube" or L is None:
        return False
    tube.find("length").text = repr(L + extra_m)
    return True


def preserve_nose_volume(index, nc, before):
    """If nose cone `nc` lost internal volume vs. state `before`, lengthen the body
    tube right behind it to add the volume back. Returns (notes, modified elements)."""
    after = nose_state(nc)
    if not before["length"] or not before["radius"] or not after["length"] or not after["radius"]:
        return [f"'{comp_name(nc)}': couldn't read nose length/radius -- volume preservation skipped"], []
    notes = []
    if after["shape"] not in KNOWN_NOSE_SHAPES:
        notes.append(f"nose shape '{after['shape']}' unknown -- treated as conical for volume math")
    deficit = (nose_volume(before["shape"], before["param"], before["length"], before["radius"])
               - nose_volume(after["shape"], after["param"], after["length"], after["radius"]))
    if deficit <= 1e-12:
        return notes, []
    _, comps, i = body_context(index, nc)
    donor = comps[i + 1] if i + 1 < len(comps) else None
    if donor is None or donor.tag != "bodytube":
        notes.append(f"component after '{comp_name(nc)}' isn't a body tube -- nose volume preservation skipped")
        return notes, []
    r = radius_of(donor, "fore")
    if not r:
        notes.append(f"couldn't read radius of '{comp_name(donor)}' -- nose volume preservation skipped")
        return notes, []
    extra = deficit / (math.pi * r * r)
    lengthen_bodytube(donor, extra)
    notes.append(f"nose volume preserved: '{comp_name(donor)}' +{extra * M_TO_IN:.3f} in "
                 f"({deficit * M_TO_IN ** 3:.2f} in^3)")
    return notes, [donor]

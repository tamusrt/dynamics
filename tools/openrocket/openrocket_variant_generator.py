#!/usr/bin/env python3
r"""
openrocket_variant_generator.py
---------------------------------
A GUI tool for generating batches of modified OpenRocket (.ork) files from a
single base design, for trade studies like the SolInvictus transition/nosecone
sweep.

WHAT IT DOES
    Load a base .ork file. It reads the file's actual component list (nose
    cone, body tubes, any existing transition) and lets you:
      - Insert a new transition between any two adjacent body components,
        sweeping its length and fore-side diameter (aft-side diameter is
        auto-matched to whatever the following component's radius already
        is, so the transition always mates cleanly).
      - Trim the nose cone's length by any number of amounts.
      - Combine both, or generate trim-only / transition-only variants.
    It then writes one .ork file per combination, using the SAME filename
    convention the companion analysis script (analyze_trade_study.py)
    already expects: "<base>_<X>Trim_<F>-<A>_<L>Trans_<Section>.ork" with
    only the segments that actually changed included.

WHY A GUI, AND WHY STDLIB-ONLY
    This uses only Python's built-in tkinter, zipfile, and xml.etree modules
    -- no pip installs, no extra dependencies to fight with. If `python
    openrocket_variant_generator.py` runs at all, this will run.

HOW GEOMETRY EDITS WORK (read this if something looks physically off)
    - .ork files are a zip containing one XML file. This tool parses the
      <rocket> design tree, edits copies of it, and writes each variant back
      out as its own .ork. It does NOT touch materials/masses of components
      it isn't modifying.
    - Nose cone trim: shortens the nose cone's <length> by the trim amount
      and CLEARS any mass override on the nose cone shell, so OpenRocket
      recomputes its mass from material density and the new (shorter)
      geometry -- i.e. a trimmed nose cone actually gets lighter, which is
      the physically correct behavior for a trade study. Internal mass
      components inside the nose cone (payload, bulkheads, etc.) are left
      exactly where they were, matching how OpenRocket's own GUI behaves
      when you just edit a parent component's length field.
    - Optional "preserve nose-trim volume": if enabled, also computes how
      much internal volume the nose cone itself lost by being shortened --
      using its ACTUAL shape function (conical/ogive/ellipsoid/power/
      parabolic/haack, read from the file's own <shape>/<shapeparameter>,
      via numerical integration of the real profile -- not a cylinder
      approximation) -- and lengthens the body tube immediately after the
      nose cone to add that volume back. Off by default, since a trimmed
      nose cone losing a bit of internal volume near the tip is often fine
      to just accept in a trade study; this is a separate switch from the
      transition-side compensation below.
    - New transition: fore radius = whatever you set (the swept variable);
      aft radius = auto-read from the component immediately following the
      insertion point, so it always fits; material/finish/thickness are
      copied from that same following component as a reasonable default.
      No mass override is set, so OpenRocket computes the transition's mass
      from its own material and geometry.
    - Everything forward of the transition -- the nose cone's base and every
      body tube between it and the transition -- is resized to the SAME
      fore diameter, so the forward section reads as one continuous body
      rather than leaving a step where an unchanged-diameter tube runs into
      a narrower (or wider) transition. Mass overrides on anything resized
      are cleared the same way as nose cone trim, for the same reason. If
      there's already another transition somewhere in that forward section,
      it gets flattened to a constant (non-tapered) diameter too, and this
      is called out explicitly in the per-file log.
    - Optional "preserve transition-side volume": if enabled, EVERY body
      tube forward of the transition (never the nose cone) is individually
      lengthened enough to make up for the internal volume IT ITSELF lost by
      being resized to the new (typically narrower) diameter -- each tube is
      judged against its own original radius/length, not just the one
      immediately before the transition. On top of that, the tube
      immediately before the new transition additionally absorbs the
      transition's own taper volume loss (the frustum-vs-cylinder deficit).
      If that immediately-preceding component isn't a plain body tube (e.g.
      the transition sits right after the nose cone, or after another
      flattened transition), the transition's own taper loss can't be
      compensated and this is called out in the per-file log -- but every
      other forward tube still gets its own self-preservation applied.
    - Every generated file keeps whatever simulation(s) the base file has
      (motor, launch conditions, etc.) so batch_simulate.py has something to
      run immediately -- but strips out the old flight-data RESULTS from
      those simulations, since they were computed for different geometry and
      would be stale/wrong. If the base file has zero saved simulations, the
      generated files will too (add one in OpenRocket first if so).

USAGE
    python openrocket_variant_generator.py
(everything else happens in the window that opens)
"""

import copy
import math
import queue
import re
import sys
import threading
import traceback
import uuid
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext
except ImportError:
    print("ERROR: tkinter is not available in this Python install. On Debian/Ubuntu try:\n"
          "    sudo apt install python3-tk\nOn Windows, tkinter ships with the standard "
          "python.org installer -- reinstall Python and make sure it's not a stripped-down build.",
          file=sys.stderr)
    sys.exit(1)


IN_TO_M = 0.0254
M_TO_IN = 1.0 / 0.0254

# The tags in a stage's <subcomponents> that represent physical, stackable
# body components (as opposed to internal fittings like fins/parachutes that
# live nested a level deeper).
BODY_TAGS = ("nosecone", "bodytube", "transition")


# ----------------------------------------------------------------------------
# Pure .ork read/edit logic -- no GUI code below this point, so it can be
# tested and reused independently of tkinter.
# ----------------------------------------------------------------------------

class OrkLoadError(Exception):
    pass


def load_ork_root(path: Path) -> ET.Element:
    """Read a .ork file (a zip containing one XML file) and return the parsed root element."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            target = "rocket.ork" if "rocket.ork" in names else (names[0] if names else None)
            if target is None:
                raise OrkLoadError(f"{path} is an empty zip archive -- not a valid .ork file.")
            data = zf.read(target)
    except zipfile.BadZipFile as e:
        raise OrkLoadError(f"{path} doesn't look like a valid .ork file (not a zip archive): {e}")

    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise OrkLoadError(f"Couldn't parse the XML inside {path}: {e}")

    if root.tag != "openrocket":
        raise OrkLoadError(f"{path} doesn't look like an OpenRocket file (root tag is <{root.tag}>, expected <openrocket>).")

    return root


def get_stages(root: ET.Element):
    """Return the list of <stage> elements in document order."""
    rocket = root.find("rocket")
    if rocket is None:
        raise OrkLoadError("No <rocket> element found in this file.")
    subcomponents = rocket.find("subcomponents")
    if subcomponents is None:
        raise OrkLoadError("Rocket has no <subcomponents> -- nothing to work with.")
    stages = subcomponents.findall("stage")
    if not stages:
        raise OrkLoadError("No <stage> found in this rocket.")
    return rocket, stages


def get_stage_body_components(stage: ET.Element):
    """
    Return (subcomponents_element, [list of direct-child body components])
    for a stage, in document order. Only nosecone/bodytube/transition count
    as "body components" for insertion-point purposes.
    """
    sc = stage.find("subcomponents")
    if sc is None:
        return None, []
    comps = [child for child in list(sc) if child.tag in BODY_TAGS]
    return sc, comps


def comp_label(el: ET.Element) -> str:
    name = (el.findtext("name") or "").strip()
    return name if name else f"(unnamed {el.tag})"


def sanitize_label(label: str) -> str:
    """Turn a component name into something safe to put in a filename."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", label)
    return cleaned if cleaned else "Section"


def _parse_auto_value(text):
    """Parse a field that may be 'auto', '0.0762', or 'auto 0.0762' -> float or None."""
    if text is None:
        return None
    parts = text.strip().split()
    if not parts:
        return None
    try:
        return float(parts[-1])
    except ValueError:
        return None


def get_radius_m(el: ET.Element, side: str):
    """
    Effective radius (meters) of a body component's fore or aft end.
    side is 'fore' or 'aft'. Nose cones have no meaningful fore radius
    (the tip is a point) so side='fore' returns None for a nosecone.
    """
    tag = el.tag
    if tag == "bodytube":
        return _parse_auto_value(el.findtext("radius"))
    if tag == "nosecone":
        return _parse_auto_value(el.findtext("aftradius")) if side == "aft" else None
    if tag == "transition":
        return _parse_auto_value(el.findtext("foreradius" if side == "fore" else "aftradius"))
    return None


def find_nosecone(stage: ET.Element):
    _, comps = get_stage_body_components(stage)
    for el in comps:
        if el.tag == "nosecone":
            return el
    return None


def get_junctions(components):
    """
    Return the list of valid insertion points as (index_after_which, label)
    pairs, where index_after_which is the index (into `components`) of the
    component the new transition would be placed immediately after.
    """
    junctions = []
    for i in range(len(components) - 1):
        label = sanitize_label(comp_label(components[i]))
        junctions.append((i, label))
    return junctions


_KNOWN_NOSECONE_SHAPES = {"conical", "ogive", "ellipsoid", "elliptical", "power", "parabolic", "haack"}


def _nosecone_profile_radius_m(shape: str, shapeparam, length_m: float, base_radius_m: float, x_m: float) -> float:
    """
    Radius (meters) of a nose-cone-shaped profile at axial position x_m
    (measured from the tip, 0 <= x_m <= length_m), for the shape families
    OpenRocket supports. base_radius_m is the radius at x_m == length_m.
    shapeparam is that shape's dimensionless parameter (None where not
    applicable -- conical/ellipsoid ignore it).

    These are the standard nose-cone-design equations (conical, tangent/
    secant ogive, ellipsoid, power series, parabolic series, Haack series),
    used here purely to get an accurate internal volume via numerical
    integration -- not to redraw the actual OpenRocket geometry.
    """
    L = length_m
    R = base_radius_m
    if L <= 0 or R <= 0:
        return 0.0
    x = min(max(x_m, 0.0), L)
    u = x / L
    shape = (shape or "conical").lower()

    if shape == "conical":
        return R * u

    if shape in ("ellipsoid", "elliptical"):
        val = max(0.0, 2.0 * u - u * u)
        return R * math.sqrt(val)

    if shape == "power":
        n = shapeparam if shapeparam is not None else 0.5
        n = max(n, 1e-6)
        return R * (u ** n)

    if shape == "parabolic":
        k = shapeparam if shapeparam is not None else 0.5
        denom = 2.0 - k
        if abs(denom) < 1e-9:
            denom = 1e-9
        val = (2.0 * u - k * u * u) / denom
        return R * max(0.0, val)

    if shape == "haack":
        c = shapeparam if shapeparam is not None else 0.0
        theta = math.acos(max(-1.0, min(1.0, 1.0 - 2.0 * u)))
        val = theta - math.sin(2.0 * theta) / 2.0 + c * (math.sin(theta) ** 3)
        val = max(0.0, val)
        return (R / math.sqrt(math.pi)) * math.sqrt(val)

    if shape == "ogive":
        # General circular-arc (secant) ogive, derived from: the generating
        # circle of radius rho passes through the tip (0,0) and the base
        # edge (L,R). shapeparam is rho expressed as a fraction of the
        # tangent-ogive radius (shapeparam=1.0 -> the standard tangent
        # ogive OpenRocket uses by default); smaller values pull the curve
        # in tighter (down to a degenerate cone at the minimum valid rho).
        p = shapeparam if shapeparam is not None else 1.0
        K = L * L + R * R
        rho_tangent = K / (2.0 * R)
        rho_min = math.sqrt(K) / 2.0
        rho = max(p, 0.0) * rho_tangent
        if rho < rho_min:
            rho = rho_min
        disc = max(0.0, (4.0 * rho * rho / K) - 1.0)
        y_c = (R - L * math.sqrt(disc)) / 2.0
        x_c = (K - 2.0 * R * y_c) / (2.0 * L)
        val = max(0.0, rho * rho - (x - x_c) ** 2)
        return y_c + math.sqrt(val)

    # Unrecognized shape -- fall back to a linear (conical) profile so the
    # math stays well-defined; callers should flag this to the user.
    return R * u


def nosecone_volume_m3(shape: str, shapeparam, length_m: float, base_radius_m: float, n: int = 4000) -> float:
    """
    Internal volume (m^3) of a solid-of-revolution nose cone of the given
    shape/length/base-radius, via Simpson's rule numerical integration of
    pi * r(x)^2 dx. n (even) is the number of integration slices -- 4000 is
    far more than enough for these smooth profiles.
    """
    if length_m <= 0 or base_radius_m <= 0:
        return 0.0
    if n % 2 == 1:
        n += 1
    h = length_m / n
    total = 0.0
    for i in range(n + 1):
        x = i * h
        r = _nosecone_profile_radius_m(shape, shapeparam, length_m, base_radius_m, x)
        f = math.pi * r * r
        if i == 0 or i == n:
            w = 1.0
        elif i % 2 == 1:
            w = 4.0
        else:
            w = 2.0
        total += w * f
    return total * h / 3.0


def _lengthen_bodytube(donor: ET.Element, extra_length_m: float) -> bool:
    """
    Adds extra_length_m to a plain bodytube's <length> and clears its mass
    override so OpenRocket recomputes mass from the new geometry. Returns
    True on success, False if donor isn't a bodytube with usable fields.
    """
    if donor is None or donor.tag != "bodytube":
        return False
    length_el = donor.find("length")
    if length_el is None or length_el.text is None:
        return False
    try:
        current_len_m = float(length_el.text)
    except ValueError:
        return False
    length_el.text = repr(current_len_m + extra_length_m)
    override_el = donor.find("overridemass")
    if override_el is not None:
        donor.remove(override_el)
    return True


def apply_nosecone_trim(stage: ET.Element, trim_in: float, compensate_volume: bool = False):
    """
    Mutates (a copy of) the stage: shortens the nose cone length by
    trim_in inches and clears its mass override so OpenRocket recomputes
    mass from the new geometry.

    If compensate_volume is True, also computes how much internal volume
    the nose cone itself lost by being shortened -- using its ACTUAL shape
    function (conical/ogive/ellipsoid/power/parabolic/haack, whichever this
    nose cone is, read from its <shape>/<shapeparameter>), not just a crude
    cylinder approximation -- and lengthens the body tube immediately after
    the nose cone to make that volume back up.

    Returns a list of note/warning strings (empty if nothing to report).
    """
    nc = find_nosecone(stage)
    if nc is None:
        return ["No <nosecone> found -- trim not applied."]
    length_el = nc.find("length")
    if length_el is None or length_el.text is None:
        return ["Nose cone has no <length> field -- trim not applied."]
    try:
        current_len_m = float(length_el.text)
    except ValueError:
        return ["Nose cone <length> isn't a plain number -- trim not applied."]

    new_len_m = current_len_m - trim_in * IN_TO_M
    if new_len_m <= 0.005:  # under 5mm left is not a usable nose cone
        return [f"Trim of {trim_in} in would leave the nose cone at "
                f"{new_len_m * M_TO_IN:.2f} in -- skipped, too short."]

    notes = []
    deficit_m3 = 0.0
    if compensate_volume:
        base_radius_m = get_radius_m(nc, "aft")
        if base_radius_m and base_radius_m > 0:
            shape = (nc.findtext("shape") or "conical").lower()
            if shape not in _KNOWN_NOSECONE_SHAPES:
                notes.append(f"Nose cone shape '{shape}' isn't one this tool has an exact volume formula for -- "
                             f"approximated as conical for the volume-compensation math.")
            param_text = nc.findtext("shapeparameter")
            try:
                shapeparam = float(param_text) if param_text not in (None, "") else None
            except ValueError:
                shapeparam = None
            vol_before = nosecone_volume_m3(shape, shapeparam, current_len_m, base_radius_m)
            vol_after = nosecone_volume_m3(shape, shapeparam, new_len_m, base_radius_m)
            deficit_m3 = max(0.0, vol_before - vol_after)
        else:
            notes.append("Couldn't determine the nose cone's base radius -- nose-trim volume compensation skipped.")

    length_el.text = repr(new_len_m)
    override_el = nc.find("overridemass")
    if override_el is not None:
        nc.remove(override_el)

    if compensate_volume and deficit_m3 > 0:
        _, comps = get_stage_body_components(stage)
        try:
            nc_index = comps.index(nc)
        except ValueError:
            nc_index = 0
        donor = comps[nc_index + 1] if nc_index + 1 < len(comps) else None

        if donor is None:
            notes.append("No body tube found after the nose cone -- nose-trim volume compensation skipped.")
        elif donor.tag != "bodytube":
            notes.append(f"Component immediately after the nose cone ('{comp_label(donor)}') isn't a plain "
                         f"body tube -- nose-trim volume compensation skipped for this variant.")
        else:
            donor_radius_m = get_radius_m(donor, "fore") or get_radius_m(donor, "aft")
            if not donor_radius_m or donor_radius_m <= 0:
                notes.append("Couldn't determine the donor tube's radius -- nose-trim volume compensation skipped.")
            else:
                extra_length_m = deficit_m3 / (math.pi * donor_radius_m ** 2)
                if _lengthen_bodytube(donor, extra_length_m):
                    notes.append(f"nose-trim compensation: +{extra_length_m * M_TO_IN:.3f} in added to "
                                 f"'{comp_label(donor)}' to preserve the {deficit_m3 * (M_TO_IN ** 3):.2f} in^3 "
                                 f"of internal volume trimmed off the nose tip.")
                else:
                    notes.append("Couldn't lengthen the donor tube -- nose-trim volume compensation skipped.")

    return notes


def resize_forward_components(stage: ET.Element, junction_index: int, new_radius_m: float):
    """
    Mutates (a copy of) the stage: sets every body component from the nose
    cone through the one immediately before the transition (indices 0
    through junction_index, inclusive) to new_radius_m, so the whole forward
    section becomes a single constant, physically continuous diameter that
    meets the transition's fore face flush -- instead of leaving a step
    where an unchanged-diameter tube runs into a narrower (or wider)
    transition. Clears mass overrides on anything it resizes so OpenRocket
    recomputes mass from the new geometry. Returns a list of note strings
    (e.g. flagging an already-existing transition in the forward section).
    """
    _, comps = get_stage_body_components(stage)
    notes = []
    for i in range(0, junction_index + 1):
        comp = comps[i]
        override_el = comp.find("overridemass")

        if comp.tag == "nosecone":
            aft_el = comp.find("aftradius")
            if aft_el is not None:
                aft_el.text = repr(new_radius_m)
        elif comp.tag == "bodytube":
            radius_el = comp.find("radius")
            if radius_el is not None:
                radius_el.text = repr(new_radius_m)
        elif comp.tag == "transition":
            fore_el = comp.find("foreradius")
            aft_el = comp.find("aftradius")
            if fore_el is not None:
                fore_el.text = repr(new_radius_m)
            if aft_el is not None:
                aft_el.text = repr(new_radius_m)
            notes.append(
                f"'{comp_label(comp)}' is an existing transition forward of the insertion point -- both its "
                f"radii were set to {new_radius_m * M_TO_IN:.3f} in to keep the forward section a constant "
                f"diameter (it's no longer tapered in this variant)."
            )

        if override_el is not None:
            comp.remove(override_el)

    return notes


def frustum_deficit_volume_m3(length_m, fore_radius_m, aft_radius_m):
    """
    Volume "lost" by a transition compared to a straight cylinder at the aft
    (normal/unchanged) radius over the same length -- i.e. how much smaller
    the tapered frustum is than the constant-diameter tube it replaced.
    Returns 0.0 (never negative) if the transition doesn't actually narrow
    anything relative to the aft radius (fore >= aft -- no volume lost).
    """
    cylinder_v = math.pi * aft_radius_m ** 2 * length_m
    frustum_v = (math.pi * length_m / 3.0) * (fore_radius_m ** 2 + fore_radius_m * aft_radius_m + aft_radius_m ** 2)
    return max(0.0, cylinder_v - frustum_v)


def apply_transition_geometry(stage: ET.Element, junction_index: int, new_fore_radius_m: float,
                               compensate_volume: bool = False,
                               transition_length_m: float = None, transition_aft_radius_m: float = None):
    """
    Resizes every body component forward of the insertion point (the nose
    cone through the component immediately before the new transition) to
    new_fore_radius_m -- see resize_forward_components -- so the forward
    section is one continuous diameter meeting the transition flush.

    If compensate_volume is True, ALL of those forward body tubes (never the
    nose cone) are lengthened to preserve volume -- not just the one right
    before the transition. Each tube is resized to the same new (typically
    narrower) diameter, so each one individually loses internal volume
    relative to ITS OWN original radius/length; each gets its own
    self-preserving length increase for that. On top of that, the tube
    immediately before the new transition additionally absorbs the
    transition's own taper volume loss (frustum_deficit_volume_m3).

    Returns a list of note/warning strings.
    """
    _, comps = get_stage_body_components(stage)
    notes = []

    # Capture each forward body tube's ORIGINAL radius/length before any
    # resizing happens -- resizing overwrites these fields in place, and we
    # need the "before" values to know how much volume each one lost.
    original_geom = {}
    if compensate_volume:
        for i in range(0, junction_index + 1):
            comp = comps[i]
            if comp.tag == "bodytube":
                r = get_radius_m(comp, "aft")
                try:
                    l = float(comp.findtext("length"))
                except (TypeError, ValueError):
                    l = None
                if r and l is not None:
                    original_geom[comp.findtext("id")] = (r, l)

    notes.extend(resize_forward_components(stage, junction_index, new_fore_radius_m))

    if not compensate_volume:
        return notes

    _, comps_after = get_stage_body_components(stage)
    preceding_comp = comps_after[junction_index] if junction_index < len(comps_after) else None

    transition_deficit_m3 = 0.0
    if transition_length_m is not None and transition_aft_radius_m is not None:
        transition_deficit_m3 = frustum_deficit_volume_m3(transition_length_m, new_fore_radius_m,
                                                            transition_aft_radius_m)

    transition_deficit_applied = False
    for i in range(0, junction_index + 1):
        comp = comps_after[i]
        if comp.tag != "bodytube":
            continue
        comp_id = comp.findtext("id")
        if comp_id not in original_geom or new_fore_radius_m <= 0:
            continue
        orig_radius_m, orig_length_m = original_geom[comp_id]

        extra_own_m = 0.0
        if orig_radius_m > new_fore_radius_m:
            ratio = orig_radius_m / new_fore_radius_m
            extra_own_m = orig_length_m * (ratio * ratio - 1.0)

        extra_transition_m = 0.0
        if i == junction_index and transition_deficit_m3 > 0:
            extra_transition_m = transition_deficit_m3 / (math.pi * new_fore_radius_m ** 2)
            transition_deficit_applied = True

        total_extra_m = extra_own_m + extra_transition_m
        if total_extra_m <= 1e-9:
            continue

        if not _lengthen_bodytube(comp, total_extra_m):
            notes.append(f"Couldn't lengthen '{comp_label(comp)}' for volume compensation -- skipped.")
            continue

        pieces = []
        if extra_own_m > 1e-9:
            pieces.append(f"{extra_own_m * M_TO_IN:.3f} in to preserve its own volume at the new "
                          f"{new_fore_radius_m * 2 * M_TO_IN:.3f} in diameter")
        if extra_transition_m > 1e-9:
            pieces.append(f"{extra_transition_m * M_TO_IN:.3f} in to absorb the new transition's own taper "
                          f"volume loss")
        notes.append(f"'{comp_label(comp)}' lengthened by " + " and ".join(pieces) + ".")

    if transition_deficit_m3 > 0 and not transition_deficit_applied and preceding_comp is not None:
        notes.append(f"Component immediately before the new transition ('{comp_label(preceding_comp)}') isn't a "
                     f"plain body tube (or has no usable length data) -- the transition's own taper volume loss "
                     f"could not be compensated by lengthening it.")

    return notes


def create_transition_element(length_m, fore_radius_m, aft_radius_m, template_el, shape="conical", name="Transition"):
    """
    Build a new <transition> element. template_el (if given) is the
    neighboring component whose material/finish/thickness get copied as
    sane defaults.
    """
    t = ET.Element("transition")

    def add(tag, text):
        e = ET.SubElement(t, tag)
        e.text = text
        return e

    add("name", name)
    add("id", str(uuid.uuid4()))
    add("overridesubcomponentsmass", "false")

    finish = "smooth"
    material_el = None
    thickness_m = 0.002
    if template_el is not None:
        finish = template_el.findtext("finish") or finish
        material_el = template_el.find("material")
        thickness_text = template_el.findtext("thickness")
        if thickness_text:
            try:
                thickness_m = float(thickness_text)
            except ValueError:
                pass

    add("finish", finish)
    if material_el is not None:
        t.append(copy.deepcopy(material_el))
    else:
        m = ET.SubElement(t, "material")
        m.set("type", "bulk")
        m.set("density", "1850.0")
        m.set("group", "Composites")
        m.text = "Fiberglass"

    add("length", repr(length_m))
    add("thickness", repr(thickness_m))
    add("shape", shape)
    add("shapeclipped", "false")
    add("foreradius", repr(fore_radius_m))
    add("aftradius", repr(aft_radius_m))
    add("foreshoulderradius", "0.0")
    add("foreshoulderlength", "0.0")
    add("foreshoulderthickness", "0.0")
    add("foreshouldercapped", "false")
    add("aftshoulderradius", "0.0")
    add("aftshoulderlength", "0.0")
    add("aftshoulderthickness", "0.0")
    add("aftshouldercapped", "false")

    return t


def strip_simulations(sims_el):
    """
    Return a deep copy of <simulations> with each <simulation>'s stored
    <flightdata> (the stale results blob) removed and status reset, so the
    simulation is still configured (motor, launch conditions) but flagged
    for a fresh run. Returns None if sims_el is None.
    """
    if sims_el is None:
        return None
    new_sims = copy.deepcopy(sims_el)
    for sim in new_sims.findall("simulation"):
        sim.set("status", "outdated")
        fd = sim.find("flightdata")
        if fd is not None:
            sim.remove(fd)
    return new_sims


def format_num(x: float) -> str:
    """Compact numeral for filenames: 2 not 2.0, 4.5 stays 4.5."""
    if float(x).is_integer():
        return str(int(x))
    return f"{x:g}"


def build_variant_filename(base_stem, trim_in, transition_variant, aft_diam_in_lookup):
    """
    transition_variant is None for "no transition", or a dict with keys
    junction_label, length_in, fore_diam_in, aft_diam_in.
    """
    parts = [base_stem]
    if trim_in and trim_in > 0:
        parts.append(f"{format_num(trim_in)}Trim")
    if transition_variant is not None:
        parts.append(f"{format_num(transition_variant['fore_diam_in'])}-{format_num(transition_variant['aft_diam_in'])}")
        parts.append(f"{format_num(transition_variant['length_in'])}Trans")
        parts.append(transition_variant["junction_label"])
    return "_".join(parts) + ".ork"


def build_variant(root: ET.Element, stage_index: int, trim_in: float, transition_variant, base_stem: str,
                   compensate_volume: bool = False, compensate_nose_trim: bool = False):
    """
    Build one variant's full <openrocket> root element (a fresh deep copy
    with edits applied) plus a list of warning strings.

    compensate_volume: when True and a transition is inserted, lengthens
    EVERY body tube forward of the transition (never the nose cone) to make
    up for the internal volume each one individually loses by being resized
    to the new (typically narrower) diameter, plus the transition's own
    taper volume loss on top of that for the tube immediately before it --
    see apply_transition_geometry.

    compensate_nose_trim: when True and the nose cone is trimmed, lengthens
    the body tube immediately after the nose cone to make up for the
    internal volume the nose cone itself lost by being shortened (computed
    from the nose cone's actual shape function) -- see apply_nosecone_trim.
    """
    warnings = []
    new_root = copy.deepcopy(root)
    rocket, stages = get_stages(new_root)
    stage = stages[stage_index]

    if trim_in and trim_in > 0:
        warnings.extend(apply_nosecone_trim(stage, trim_in, compensate_volume=compensate_nose_trim))

    if transition_variant is not None:
        sc, comps = get_stage_body_components(stage)
        idx = transition_variant["junction_index"]
        if idx >= len(comps) - 1:
            warnings.append("Selected junction no longer exists after trimming -- transition not inserted.")
        else:
            preceding = comps[idx]
            following = comps[idx + 1]
            fore_radius_m = transition_variant["fore_diam_in"] / 2.0 * IN_TO_M
            aft_radius_m = transition_variant["aft_diam_in"] / 2.0 * IN_TO_M
            length_m = transition_variant["length_in"] * IN_TO_M
            name = f"Transition ({transition_variant['length_in']:g}in x {transition_variant['fore_diam_in']:g}in)"
            new_transition = create_transition_element(length_m, fore_radius_m, aft_radius_m, following, name=name)
            # Insert into the real subcomponents list right after `preceding`.
            insert_pos = list(sc).index(preceding) + 1
            sc.insert(insert_pos, new_transition)

            # Shrink/grow everything forward of the transition (nose cone
            # through `preceding`) to the same fore diameter, so the forward
            # section is a single continuous diameter meeting the transition
            # flush instead of leaving a step -- and, if requested, restore
            # each forward tube's own lost volume plus the transition's own
            # taper loss. See apply_transition_geometry for the breakdown.
            warnings.extend(apply_transition_geometry(
                stage, idx, fore_radius_m,
                compensate_volume=compensate_volume,
                transition_length_m=length_m,
                transition_aft_radius_m=aft_radius_m,
            ))

    sims_el = new_root.find("simulations")
    if sims_el is not None:
        new_sims = strip_simulations(sims_el)
        new_root.remove(sims_el)
        new_root.append(new_sims)

    return new_root, warnings


def write_ork(root: ET.Element, out_path: Path):
    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("rocket.ork", xml_bytes)


def parse_number_list(text: str):
    """Parse a comma/space-separated list of numbers, e.g. '0, 1, 2.5' -> [0.0, 1.0, 2.5]. Empty -> []."""
    if not text or not text.strip():
        return []
    values = []
    for token in re.split(r"[,\s]+", text.strip()):
        if not token:
            continue
        values.append(float(token))
    return values


def plan_batch(trims, junction_choices, lengths, diameters, include_no_transition, include_baseline):
    """
    Build the full list of (trim_in, transition_variant_or_None) combinations
    to generate. junction_choices is a list of (index, label) tuples the
    user selected. Returns a list of dicts describing each planned variant.
    """
    if not trims:
        trims = [0.0]

    transition_variants = [None] if (include_no_transition or not junction_choices) else []
    for (jidx, jlabel) in junction_choices:
        for length_in in (lengths or []):
            for diam_in in (diameters or []):
                transition_variants.append({
                    "junction_index": jidx,
                    "junction_label": jlabel,
                    "length_in": length_in,
                    "fore_diam_in": diam_in,
                    "aft_diam_in": None,  # filled in by caller once aft radius is known
                })

    plan = []
    for trim_in in trims:
        for tv in transition_variants:
            if trim_in == 0.0 and tv is None and not include_baseline:
                continue
            plan.append({"trim_in": trim_in, "transition": tv})
    return plan


# ----------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------

class VariantGeneratorApp:
    def __init__(self, root_win):
        self.root_win = root_win
        root_win.title("OpenRocket Variant Generator")
        root_win.geometry("880x720")

        self.ork_path = None
        self.ork_root = None
        self.stage_index = 0
        self.components = []
        self.junction_vars = []  # list of (index, label, tk.BooleanVar)

        self.log_queue = queue.Queue()
        self.worker_thread = None
        self.cancel_event = threading.Event()

        self._build_ui()

    # ---- UI construction -----------------------------------------------

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        file_frame = ttk.LabelFrame(self.root_win, text="1. Base .ork file")
        file_frame.pack(fill="x", **pad)
        self.file_label = ttk.Label(file_frame, text="No file loaded.")
        self.file_label.pack(side="left", padx=8, pady=8)
        ttk.Button(file_frame, text="Load .ork file...", command=self.on_load_file).pack(side="right", padx=8, pady=8)

        junction_frame = ttk.LabelFrame(self.root_win, text="2. Where to insert a new transition (pick any number)")
        junction_frame.pack(fill="both", expand=False, **pad)
        self.junction_canvas_frame = ttk.Frame(junction_frame)
        self.junction_canvas_frame.pack(fill="both", expand=True, padx=8, pady=8)
        self.junction_placeholder = ttk.Label(self.junction_canvas_frame, text="Load a file to see insertion points.")
        self.junction_placeholder.pack(anchor="w")

        params_frame = ttk.LabelFrame(self.root_win, text="3. Parameters to sweep")
        params_frame.pack(fill="x", **pad)

        ttk.Label(params_frame, text="Nose cone trim amounts (in), comma-separated (0 = untrimmed):").grid(
            row=0, column=0, sticky="w", padx=8, pady=4)
        self.trim_entry = ttk.Entry(params_frame, width=40)
        self.trim_entry.insert(0, "0")
        self.trim_entry.grid(row=0, column=1, sticky="w", padx=8, pady=4)

        ttk.Label(params_frame, text="Transition length(s) (in), comma-separated:").grid(
            row=1, column=0, sticky="w", padx=8, pady=4)
        self.length_entry = ttk.Entry(params_frame, width=40)
        self.length_entry.insert(0, "3")
        self.length_entry.grid(row=1, column=1, sticky="w", padx=8, pady=4)

        ttk.Label(params_frame, text="Transition fore diameter(s) (in), comma-separated:").grid(
            row=2, column=0, sticky="w", padx=8, pady=4)
        self.diam_entry = ttk.Entry(params_frame, width=40)
        self.diam_entry.insert(0, "4")
        self.diam_entry.grid(row=2, column=1, sticky="w", padx=8, pady=4)

        self.include_no_transition_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(params_frame, text="Also generate trim-only variants (no transition inserted)",
                         variable=self.include_no_transition_var).grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=4)

        self.include_baseline_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(params_frame, text="Include a completely unmodified copy of the base file",
                         variable=self.include_baseline_var).grid(row=4, column=0, columnspan=2, sticky="w", padx=8, pady=4)

        self.compensate_volume_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            params_frame,
            text="Preserve transition-side volume (lengthen every forward body tube to compensate)",
            variable=self.compensate_volume_var,
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=8, pady=4)

        self.compensate_nose_trim_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            params_frame,
            text="Preserve nose-trim volume (lengthen the tube after the nose to compensate)",
            variable=self.compensate_nose_trim_var,
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=8, pady=4)

        for entry in (self.trim_entry, self.length_entry, self.diam_entry):
            entry.bind("<KeyRelease>", lambda e: self.update_preview())
        self.include_no_transition_var.trace_add("write", lambda *a: self.update_preview())
        self.include_baseline_var.trace_add("write", lambda *a: self.update_preview())

        out_frame = ttk.LabelFrame(self.root_win, text="4. Output")
        out_frame.pack(fill="x", **pad)
        self.out_dir = tk.StringVar(value=str(Path.cwd() / "ork_variants"))
        ttk.Entry(out_frame, textvariable=self.out_dir, width=60).pack(side="left", padx=8, pady=8, fill="x", expand=True)
        ttk.Button(out_frame, text="Choose folder...", command=self.on_choose_out_dir).pack(side="left", padx=8, pady=8)

        action_frame = ttk.Frame(self.root_win)
        action_frame.pack(fill="x", **pad)
        self.preview_label = ttk.Label(action_frame, text="Load a file to begin.")
        self.preview_label.pack(side="left", padx=8)
        self.generate_button = ttk.Button(action_frame, text="Generate Batch", command=self.on_generate, state="disabled")
        self.generate_button.pack(side="right", padx=8)
        self.cancel_button = ttk.Button(action_frame, text="Cancel", command=self.on_cancel, state="disabled")
        self.cancel_button.pack(side="right", padx=8)

        self.progress = ttk.Progressbar(self.root_win, mode="determinate")
        self.progress.pack(fill="x", padx=8, pady=(0, 4))

        log_frame = ttk.LabelFrame(self.root_win, text="Log")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, state="disabled")
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)

        self.root_win.after(150, self._poll_log_queue)

    # ---- Logging ----------------------------------------------------------

    def log(self, msg):
        self.log_queue.put(msg)

    def _poll_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", msg + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.root_win.after(150, self._poll_log_queue)

    # ---- File loading -------------------------------------------------

    def on_load_file(self):
        path = filedialog.askopenfilename(title="Select a base .ork file", filetypes=[("OpenRocket files", "*.ork"), ("All files", "*.*")])
        if not path:
            return
        try:
            root = load_ork_root(Path(path))
            rocket, stages = get_stages(root)
        except OrkLoadError as e:
            messagebox.showerror("Couldn't load file", str(e))
            return

        self.ork_path = Path(path)
        self.ork_root = root
        self.stage_index = 0
        _, comps = get_stage_body_components(stages[0])
        self.components = comps

        self.file_label.configure(text=f"{self.ork_path.name}  ({len(comps)} body component(s) in stage '{comp_label(stages[0])}')")
        self._populate_junctions(comps)
        self.generate_button.configure(state="normal")
        self.update_preview()
        self.log(f"Loaded {self.ork_path.name}: " + " -> ".join(comp_label(c) for c in comps))

    def _populate_junctions(self, comps):
        for child in list(self.junction_canvas_frame.children.values()):
            child.destroy()
        self.junction_vars = []

        if len(comps) < 2:
            ttk.Label(self.junction_canvas_frame, text="This rocket only has one body component -- no junctions to insert a transition at.").pack(anchor="w")
            return

        junctions = get_junctions(comps)
        for (idx, label) in junctions:
            var = tk.BooleanVar(value=False)
            text = f"After \"{comp_label(comps[idx])}\", before \"{comp_label(comps[idx + 1])}\"  (section label: {label})"
            cb = ttk.Checkbutton(self.junction_canvas_frame, text=text, variable=var, command=self.update_preview)
            cb.pack(anchor="w")
            self.junction_vars.append((idx, label, var))

    # ---- Output dir ------------------------------------------------------

    def on_choose_out_dir(self):
        path = filedialog.askdirectory(title="Choose output folder")
        if path:
            self.out_dir.set(path)
            self.update_preview()

    # ---- Preview / planning ------------------------------------------

    def _selected_junctions(self):
        return [(idx, label) for (idx, label, var) in self.junction_vars if var.get()]

    def _current_plan(self):
        if self.ork_root is None:
            return [], ["No file loaded."]
        errors = []
        try:
            trims = parse_number_list(self.trim_entry.get())
        except ValueError:
            trims, errors = [], errors + ["Nose cone trim list has a non-numeric value."]
        try:
            lengths = parse_number_list(self.length_entry.get())
        except ValueError:
            lengths, errors = [], errors + ["Transition length list has a non-numeric value."]
        try:
            diameters = parse_number_list(self.diam_entry.get())
        except ValueError:
            diameters, errors = [], errors + ["Transition diameter list has a non-numeric value."]

        junctions = self._selected_junctions()
        if not junctions and not self.include_no_transition_var.get():
            errors.append("Pick at least one junction, or enable trim-only variants.")
        if junctions and (not lengths or not diameters):
            errors.append("Selected a junction but transition length/diameter list is empty.")

        plan = plan_batch(trims, junctions, lengths, diameters,
                           self.include_no_transition_var.get(), self.include_baseline_var.get())
        return plan, errors

    def update_preview(self):
        plan, errors = self._current_plan()
        if errors:
            self.preview_label.configure(text="; ".join(errors))
        else:
            self.preview_label.configure(text=f"Will generate {len(plan)} file(s).")

    # ---- Generation ------------------------------------------------------

    def on_generate(self):
        plan, errors = self._current_plan()
        if errors:
            messagebox.showerror("Fix these first", "\n".join(errors))
            return
        if not plan:
            messagebox.showinfo("Nothing to do", "The current settings produce zero files.")
            return
        if len(plan) > 500:
            if not messagebox.askyesno("Large batch", f"This will generate {len(plan)} files. Continue?"):
                return

        out_dir = Path(self.out_dir.get()).expanduser()
        self.cancel_event.clear()
        self.progress.configure(maximum=len(plan), value=0)
        self.generate_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.log(f"--- Generating {len(plan)} file(s) into {out_dir} ---")

        self.worker_thread = threading.Thread(target=self._run_generation, args=(plan, out_dir), daemon=True)
        self.worker_thread.start()
        self.root_win.after(200, self._poll_worker)

    def on_cancel(self):
        self.cancel_event.set()
        self.log("Cancel requested -- finishing current file, then stopping.")

    def _poll_worker(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self.root_win.after(200, self._poll_worker)
        else:
            self.generate_button.configure(state="normal")
            self.cancel_button.configure(state="disabled")

    def _run_generation(self, plan, out_dir):
        base_stem = self.ork_path.stem
        written = 0
        skipped = 0

        for i, item in enumerate(plan, start=1):
            if self.cancel_event.is_set():
                self.log(f"Cancelled after {written} file(s).")
                break

            trim_in = item["trim_in"]
            tv = item["transition"]
            tv_full = None
            if tv is not None:
                idx = tv["junction_index"]
                following = self.components[idx + 1]
                aft_radius_m = get_radius_m(following, "aft") or get_radius_m(following, "fore")
                if aft_radius_m is None:
                    self.log(f"  [{i}/{len(plan)}] SKIPPED -- couldn't determine aft radius for junction '{tv['junction_label']}'.")
                    skipped += 1
                    self.root_win.after(0, self.progress.configure, {"value": i})
                    continue
                tv_full = dict(tv)
                tv_full["aft_diam_in"] = aft_radius_m * 2.0 * M_TO_IN

            filename = build_variant_filename(base_stem, trim_in, tv_full, None)
            try:
                new_root, warnings = build_variant(self.ork_root, self.stage_index, trim_in, tv_full, base_stem,
                                                    compensate_volume=self.compensate_volume_var.get(),
                                                    compensate_nose_trim=self.compensate_nose_trim_var.get())
                out_path = out_dir / filename
                write_ork(new_root, out_path)
                written += 1
                msg = f"  [{i}/{len(plan)}] wrote {filename}"
                if warnings:
                    msg += "  (" + "; ".join(warnings) + ")"
                self.log(msg)
            except Exception as e:
                skipped += 1
                self.log(f"  [{i}/{len(plan)}] ERROR on {filename}: {e}")
                self.log("    " + traceback.format_exc().replace("\n", "\n    "))

            self.root_win.after(0, self.progress.configure, {"value": i})

        self.log(f"--- Done: {written} file(s) written, {skipped} skipped. Output: {out_dir} ---")


def main():
    root_win = tk.Tk()
    VariantGeneratorApp(root_win)
    root_win.mainloop()


if __name__ == "__main__":
    main()

# import no less than 20 functions...
from __future__ import annotations
import sympy as sp
import math
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import savgol_filter, stft
from scipy.ndimage import uniform_filter1d
from dataclasses import dataclass, field
from typing import List, Tuple
from scipy import integrate as sci_integrate
from scipy.integrate import quad_vec
import pandas as pd
from filterpy.kalman import KalmanFilter
import hybrid_engine_cg as eng


radius = 3


import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np
from scipy import integrate as sci_integrate

# Tags that are purely motor/engine related and are skipped outright, along
# with everything nested inside them.
_IGNORED_TAGS = {"motor", "simulations", "simulation"}

# Tags that behave like axisymmetric "tube" sections (outer radius profile,
# optional inner radius from thickness) that we integrate over.
_TUBE_LIKE = {"nosecone", "transition", "bodytube", "innertube", "tubecoupler"}

# Tags that are short annular/disk hardware with explicit inner & outer radius.
_DISK_LIKE = {"bulkhead", "centeringring"}

# Tags that we treat as simple point masses (their own physical extent is
# small enough, or not meaningfully axisymmetric, that self-inertia is
# negligible next to the parallel-axis term).
_POINT_LIKE = {"masscomponent", "parachute", "shockcord", "railbutton"}

_FIN_TAGS = {"trapezoidfinset", "freeformfinset", "ellipticalfinset"}


def _num(text: Optional[str], default: float = 0.0) -> float:
    """Parse a numeric field that may be prefixed with OpenRocket's 'auto'
    marker (e.g. 'auto 0.0254' or a bare 'auto' with no numeric fallback)."""
    if text is None:
        return default
    text = text.strip()
    if not text:
        return default
    parts = text.split()
    for tok in reversed(parts):
        try:
            return float(tok)
        except ValueError:
            continue
    return default


def _has_number(text: Optional[str]) -> bool:
    if text is None:
        return False
    for tok in text.split():
        try:
            float(tok)
            return True
        except ValueError:
            continue
    return False


def _find_num(elem, tag, default=0.0):
    child = elem.find(tag)
    return _num(child.text if child is not None else None, default)


# --------------------------------------------------------------------------
# nose/transition shape profiles
# --------------------------------------------------------------------------

def _shape_fraction(shape: str, xu, param: float):
    """Normalized shape curve: xu in [0,1] (axial) -> y in [0,1] (radial),
    for the standard OpenRocket nose/transition shapes."""
    shape = (shape or "conical").lower()
    xu = np.clip(np.asarray(xu, dtype=float), 0.0, 1.0)
    if shape in ("conical", "cone"):
        return xu
    if shape in ("ellipsoid", "elliptical"):
        return np.sqrt(np.clip(1 - (1 - xu) ** 2, 0.0, None))
    if shape in ("haack", "vonkarman", "von karman", "von_karman"):
        theta = np.arccos(np.clip(1 - 2 * xu, -1.0, 1.0))
        c = param if param is not None else 0.0
        val = theta - np.sin(2 * theta) / 2 + c * np.sin(theta) ** 3
        return np.sqrt(np.clip(val, 0.0, None)) / math.sqrt(math.pi)
    if shape in ("power", "powerseries", "power series"):
        n = param if param else 0.5
        return xu ** n
    if shape in ("parabolic", "parabola"):
        k = param if param is not None else 0.5
        k = min(max(k, 0.0), 1.0)
        return (2 * xu - k * xu ** 2) / (2 - k)
    if shape in ("ogive",):
        # Tangent ogive in normalized (R=1, L=1) form.
        rho = 1.0
        y = np.sqrt(np.clip(rho ** 2 - (1 - xu) ** 2, 0.0, None)) + 1 - rho
        return y
    # Unknown shape: fall back to conical rather than crash.
    return xu


def _outer_radius_fn(shape: str, param: float, r_fore: float, r_aft: float, length: float) -> Callable:
    """Returns r_outer(xi) for xi in [0, length]."""
    d_r = r_aft - r_fore
    if length <= 0:
        return lambda xi: np.full(np.shape(np.asarray(xi, dtype=float)), r_aft)

    def r(xi):
        xu = np.clip(np.asarray(xi, dtype=float), 0.0, length) / length
        frac = _shape_fraction(shape, xu, param)
        return r_fore + d_r * frac

    return r


# --------------------------------------------------------------------------
# generic mass-weighted axial moments for an axisymmetric section
# --------------------------------------------------------------------------

def _section_moments(length: float, r_outer: Callable, r_inner: Callable,
                      mass: float, offset: float):
    """m0, m1, m2, mr about the nose datum (x=0), for a section spanning
    local xi in [0, length] whose annular cross-section (r_outer^2 -
    r_inner^2) sets the *shape* of the mass distribution, but whose total is
    forced to equal `mass` (so overridden masses are still distributed
    realistically along the component's own geometry)."""
    if length <= 0 or mass <= 0:
        xi_c = length / 2 if length > 0 else 0.0
        station = offset + xi_c
        return mass, mass * station, mass * station ** 2, 0.0

    w = lambda xi: max(float(r_outer(xi)) ** 2 - float(r_inner(xi)) ** 2, 0.0)
    w0, _ = sci_integrate.quad(w, 0, length)

    if w0 <= 0:
        # Degenerate cross-section (e.g. a literal point) -> treat as a
        # uniform rod so it isn't dropped entirely.
        xi_c = length / 2
        station = offset + xi_c
        return mass, mass * station, mass * station ** 2, 0.0

    station = lambda xi: offset + xi
    w1, _ = sci_integrate.quad(lambda xi: station(xi) * w(xi), 0, length)
    w2, _ = sci_integrate.quad(lambda xi: station(xi) ** 2 * w(xi), 0, length)
    wr, _ = sci_integrate.quad(
        lambda xi: (float(r_outer(xi)) ** 2 + float(r_inner(xi)) ** 2) * w(xi), 0, length
    )
    scale = mass / w0
    return mass, w1 * scale, w2 * scale, wr * scale


# --------------------------------------------------------------------------
# data classes
# --------------------------------------------------------------------------

@dataclass
class BodyPart:
    """Any axisymmetric or point-like mass contribution: nose cones,
    transitions, tubes, bulkheads, rings, mass components, hardware, ..."""
    name: str
    kind: str
    offset: float          # axial station of the *front* of this part (m)
    length: float          # axial extent (m)
    mass: float            # kg
    r_outer: Callable = field(repr=False)
    r_inner: Callable = field(repr=False)
    _moments_cache: tuple | None = field(default=None, repr=False, compare=False, init=False)

    def moments(self):
        if self._moments_cache is None:
            self._moments_cache = _section_moments(
                self.length, self.r_outer, self.r_inner, self.mass, self.offset
            )
        return self._moments_cache

    @property
    def cg(self) -> float:
        m0, m1, _, _ = self.moments()
        return m1 / m0 if m0 else self.offset

    def iyy_about(self, rocket_cg: float) -> float:
        m0, m1, m2, mr = self.moments()
        if m0 == 0:
            return 0.0
        return (m2 - 2 * rocket_cg * m1 + rocket_cg ** 2 * m0) + 0.25 * mr


@dataclass
class FinSet:
    name: str
    mass: float             # kg, TOTAL for the whole set (all fins combined)
    root_chord: float
    tip_chord: float
    span: float              # "height" in OpenRocket terms
    sweep: float              # sweeplength
    offset: float             # axial station of root-chord leading edge
    body_radius: float        # local body outer radius at the fin location
    fin_count: int = 1

    def __post_init__(self):
        r, t, sweep = self.root_chord, self.tip_chord, self.sweep
        # Axial centroid of the (swept) trapezoid, measured from the leading
        # edge of the root chord, then offset into rocket-station coords.
        # For an unswept trapezoid the centroid-of-chord-squared formula is
        # (r^2 + r*t + t^2) / (3*(r+t)); a sweep just shifts the tip chord
        # aft, so we blend that shift in proportionally by chord-weighted area.
        if (r + t) > 0:
            axial_unswept = (r ** 2 + r * t + t ** 2) / (3 * (r + t))
            # weight the sweep contribution by how "tip-heavy" the shape is
            sweep_shift = sweep * (2 * t + r) / (3 * (r + t)) if self.span > 0 else 0.0
        else:
            axial_unswept, sweep_shift = 0.0, 0.0
        self._local_cg_axial = self.offset + axial_unswept + sweep_shift
        self._local_cg_radial = self.body_radius + self.span * (
            (2 * t + r) / (3 * (r + t)) if (r + t) > 0 else 0.5
        )

    @property
    def area(self) -> float:
        return 0.5 * (self.root_chord + self.tip_chord) * self.span

    @property
    def cg(self) -> float:
        return self._local_cg_axial

    def iyy_cm(self) -> float:
        chord_avg = (self.root_chord + self.tip_chord) / 2
        axial = (1 / 12) * self.mass * chord_avg ** 2
        spanwise = (1 / 12) * self.mass * self.span ** 2
        return axial + spanwise

    def iyy_about(self, rocket_cg: float) -> float:
        d_axial = self._local_cg_axial - rocket_cg
        d_radial = self._local_cg_radial
        d_squared = d_axial ** 2 + d_radial ** 2
        return self.iyy_cm() + self.mass * d_squared


# --------------------------------------------------------------------------
# the parser
# --------------------------------------------------------------------------
class Rocket:
    def __init__(self, name: str, parts: List[BodyPart], fins: List[FinSet],
                 engine: Optional[eng.Engine] = None):
        self.name = name
        self.parts: List[BodyPart] = parts
        self.fins: List[FinSet] = fins
        self.engine: Optional[eng.Engine] = engine   

    @classmethod
    def from_file(cls, path: str, engine: Optional[Engine] = None) -> "Rocket":
        tree = ET.parse(path)
        root = tree.getroot()
        rocket_el = root.find("rocket")
        if rocket_el is None:
            raise ValueError("No <rocket> element found -- is this an OpenRocket .ork/.xml file?")
        name = (rocket_el.findtext("name") or "Rocket").strip()

        parts: List[BodyPart] = []
        fins: List[FinSet] = []

        sub = rocket_el.find("subcomponents")
        if sub is not None:
            for stage in sub.findall("stage"):
                _walk_children(stage, parent_front=0.0, parent_length=0.0,
                                current_radius=0.0, parts=parts, fins=fins)
        if engine is None:
            return cls(name, parts, fins)
        return cls(name, parts, fins, engine=engine)   

    # -- static (structural-only) quantities, unchanged --------------
    @property
    def mass(self) -> float:
        return sum(p.mass for p in self.parts) + sum(f.mass for f in self.fins)

    @property
    def cg(self) -> float:
        total = self.mass
        if total == 0:
            return 0.0
        moment = sum(p.mass * p.cg for p in self.parts) + sum(f.mass * f.cg for f in self.fins)
        return moment / total

    @property
    def iyy(self) -> float:
        cg = self.cg
        return (sum(p.iyy_about(cg) for p in self.parts)
                + sum(f.iyy_about(cg) for f in self.fins))

    def mass_at(self, t: float = 0.0) -> float:
        structural = sum(p.mass for p in self.parts) + sum(f.mass for f in self.fins)
        engine_mass = self.engine.mass_at(t) if self.engine is not None else 0.0
        return structural + engine_mass

    def cg_at(self, t: float = 0.0) -> float:
        structural_mass = sum(p.mass for p in self.parts) + sum(f.mass for f in self.fins)
        structural_moment = (sum(p.mass * p.cg for p in self.parts)
                              + sum(f.mass * f.cg for f in self.fins))
        if self.engine is not None:
            em = self.engine.mass_at(t)
            ecg = self.engine.cg_at(t)
            total_mass = structural_mass + em
            total_moment = structural_moment + em * ecg
        else:
            total_mass, total_moment = structural_mass, structural_moment
        return total_moment / total_mass if total_mass else 0.0

    def iyy_at(self, t: float = 0.0, cg: float | None = None) -> float:
        """Pitch-axis Iyy (kg*m^2) about the instantaneous cg, engine included."""
        if cg is None:
            cg = self.cg_at(t)
        iyy = (sum(p.iyy_about(cg) for p in self.parts)
            + sum(f.iyy_about(cg) for f in self.fins))
        if self.engine is not None:
            iyy += self.engine.iyy_at(t, cg)
        return iyy


def _resolve_offset(elem, parent_front, parent_length, this_length, stack_cursor):
    off_el = elem.find("axialoffset")
    if off_el is None:
        # No explicit offset -> OpenRocket stacks it immediately after the
        # previous sibling (or at the parent's front, if it's the first).
        return stack_cursor
    method = (off_el.get("method") or "top").lower()
    val = _num(off_el.text)
    if method == "top":
        return parent_front + val
    if method == "bottom":
        return parent_front + parent_length - this_length + val
    if method == "middle":
        return parent_front + (parent_length - this_length) / 2 + val
    if method == "absolute":
        return val
    return parent_front + val


def _walk_children(container, parent_front, parent_length, current_radius, parts, fins):
    sub = container.find("subcomponents")
    if sub is None:
        return
    stack_cursor = parent_front
    for child in sub:
        tag = child.tag
        if tag in _IGNORED_TAGS:
            continue

        name = (child.findtext("name") or tag).strip()
        length = _find_num(child, "length", 0.0)
        front = _resolve_offset(child, parent_front, parent_length, length, stack_cursor)
        stack_cursor = front + length

        instance_count = int(_find_num(child, "instancecount", 1)) or 1
        instance_sep = _find_num(child, "instanceseparation", 0.0)

        mass_override = None
        if child.find("overridemass") is not None:
            mass_override = _find_num(child, "overridemass")

        density = None
        mat_el = child.find("material")
        if mat_el is not None:
            density = float(mat_el.get("density", "0") or 0.0)

        child_radius = current_radius  # radius context passed down to fins/children

        if tag in ("nosecone", "transition"):
            shape = child.findtext("shape") or "conical"
            shapeparam = _find_num(child, "shapeparameter", 0.0)
            if tag == "nosecone":
                r_fore = 0.0
                r_aft = _num(child.findtext("aftradius"), current_radius)
            else:
                fore_txt = child.findtext("foreradius")
                r_fore = _num(fore_txt, current_radius) if _has_number(fore_txt) else current_radius
                r_aft = _num(child.findtext("aftradius"), current_radius)
            r_outer = _outer_radius_fn(shape, shapeparam, r_fore, r_aft, length)
            thickness_txt = child.findtext("thickness")
            if thickness_txt and thickness_txt.strip().lower() == "filled":
                r_inner = lambda xi: 0.0
            elif _has_number(thickness_txt):
                t = _num(thickness_txt)
                r_inner = (lambda xi, _t=t, _ro=r_outer: max(_ro(xi) - _t, 0.0))
            else:
                r_inner = lambda xi: 0.0
            mass = mass_override if mass_override is not None else _tube_mass(
                length, r_outer, r_inner, density)
            parts.append(BodyPart(name, tag, front, length, mass, r_outer, r_inner))
            child_radius = r_aft

        elif tag == "bodytube":
            radius_txt = child.findtext("radius")
            r = _num(radius_txt, current_radius) if _has_number(radius_txt) else current_radius
            r_outer = (lambda xi, _r=r: _r)
            thickness_txt = child.findtext("thickness")
            if thickness_txt and thickness_txt.strip().lower() == "filled":
                r_inner = lambda xi: 0.0
            elif _has_number(thickness_txt):
                t = _num(thickness_txt)
                r_inner = (lambda xi, _t=t, _r=r: max(_r - _t, 0.0))
            else:
                r_inner = lambda xi: 0.0
            mass = mass_override if mass_override is not None else _tube_mass(
                length, r_outer, r_inner, density)
            parts.append(BodyPart(name, tag, front, length, mass, r_outer, r_inner))
            child_radius = r

        elif tag in ("innertube", "tubecoupler"):
            radius_txt = child.findtext("outerradius")
            r = _num(radius_txt, current_radius) if _has_number(radius_txt) else current_radius
            r_outer = (lambda xi, _r=r: _r)
            thickness_txt = child.findtext("thickness")
            if _has_number(thickness_txt):
                t = _num(thickness_txt)
                r_inner = (lambda xi, _t=t, _r=r: max(_r - _t, 0.0))
            else:
                r_inner = lambda xi: 0.0
            mass = mass_override if mass_override is not None else _tube_mass(
                length, r_outer, r_inner, density)
            parts.append(BodyPart(name, tag, front, length, mass, r_outer, r_inner))
            child_radius = r

        elif tag in _DISK_LIKE:
            r_out_txt = child.findtext("outerradius")
            r_out = _num(r_out_txt, current_radius) if _has_number(r_out_txt) else current_radius
            r_in = _find_num(child, "innerradius", 0.0)
            r_outer = (lambda xi, _r=r_out: _r)
            r_inner = (lambda xi, _r=r_in: _r)
            mass = mass_override if mass_override is not None else _tube_mass(
                length, r_outer, r_inner, density)
            parts.append(BodyPart(name, tag, front, length, mass, r_outer, r_inner))

        elif tag in _FIN_TAGS:
            root = _find_num(child, "rootchord", 0.0)
            tip = _find_num(child, "tipchord", 0.0)
            span = _find_num(child, "height", 0.0)
            sweep = _find_num(child, "sweeplength", 0.0)
            fin_count = int(_find_num(child, "fincount", 1)) or 1
            thickness = _find_num(child, "thickness", 0.0)
            area = 0.5 * (root + tip) * span
            mass = mass_override
            if mass is None:
                mass = (density or 0.0) * area * thickness * fin_count
            fins.append(FinSet(name, mass, root, tip, span, sweep, front,
                                body_radius=current_radius, fin_count=fin_count))

        elif tag in _POINT_LIKE:
            if tag == "masscomponent":
                mass = _find_num(child, "mass", 0.0)
            elif tag == "parachute":
                if mass_override is not None:
                    mass = mass_override
                else:
                    diameter = _find_num(child, "diameter", 0.0)
                    surf_density = 0.0
                    m = child.find("material")
                    if m is not None:
                        surf_density = float(m.get("density", "0") or 0.0)
                    mass = surf_density * math.pi * (diameter / 2) ** 2
            elif tag == "shockcord":
                if mass_override is not None:
                    mass = mass_override
                else:
                    cordlength = _find_num(child, "cordlength", 0.0)
                    line_density = 0.0
                    m = child.find("material")
                    if m is not None:
                        line_density = float(m.get("density", "0") or 0.0)
                    mass = line_density * cordlength
            elif tag == "railbutton":
                mass = mass_override if mass_override is not None else 0.0
            else:
                mass = mass_override if mass_override is not None else 0.0

            if instance_count > 1:
                per = mass / instance_count
                for k in range(instance_count):
                    parts.append(BodyPart(f"{name} #{k+1}", tag,
                                           front + k * instance_sep, 0.0, per,
                                           lambda xi: 0.0, lambda xi: 0.0))
            else:
                parts.append(BodyPart(name, tag, front, 0.0, mass,
                                       lambda xi: 0.0, lambda xi: 0.0))

        # Recurse into this component's own subcomponents (payloads, rings,
        # fins mounted on a tube, nested couplers, ...).
        _walk_children(child, front, length, child_radius, parts, fins)


def _tube_mass(length, r_outer, r_inner, density):
    if not density or length <= 0:
        return 0.0
    area = lambda xi: max(float(r_outer(xi)) ** 2 - float(r_inner(xi)) ** 2, 0.0)
    vol_over_pi, _ = sci_integrate.quad(area, 0, length)
    return density * math.pi * vol_over_pi

def total_cg(rocket: "Rocket", times) -> tuple:
    """Returns (masses, cgs) as numpy arrays over the given time array."""
    times = np.asarray(times, dtype=float)
    masses = np.array([rocket.mass_at(t) for t in times])
    cgs = rocket.cg_at(times)
    return masses, cgs


def total_iyy(rocket: "Rocket", times, cgs=None):
    """Returns Iyy (kg*m^2) as a numpy array over the given time array.
    If `cgs` is provided (e.g. from total_cg), reuses it instead of
    recomputing cg at every timestep."""
    times = np.asarray(times, dtype=float)
    if cgs is None:
        iyys = np.array([rocket.iyy_at(t) for t in times])
    else:
        cgs = np.asarray(cgs, dtype=float)
        iyys = np.array([rocket.iyy_at(t, cg=c) for t, c in zip(times, cgs)])
    return iyys


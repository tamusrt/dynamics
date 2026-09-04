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
radius = 3

G_FT = 32.174   # ft/s^2, and the lbm-ft/(lbf-s^2) unit conversion
IN_PER_FT = 12

"""
rocket_iyy.py
=============
Parse an OpenRocket design file (.ork XML, e.g. exported/renamed as .xml)
and compute the rocket's pitch-axis mass moment of inertia (Iyy) about its
own center of gravity, plus total mass and CG station.

This intentionally IGNORES the motor/engine: no thrust curve, no propellant
mass depletion. Everything is computed as one static "loaded, motor-less"
configuration built entirely from the geometry, materials, and mass
overrides recorded in the file. If you want burn-time-varying Iyy back,
you'd re-introduce something like the original `Engine` class and add its
mass/cg contribution on top of `Rocket.iyy` / `Rocket.cg` below.

Usage
-----
    from rocket_iyy import Rocket

    rocket = Rocket.from_file("morph.xml")
    print(rocket.mass)        # kg
    print(rocket.cg)          # m from nose tip
    print(rocket.iyy)         # kg*m^2, about the pitch axis through rocket.cg

    for c in rocket.components:
        print(c.name, c.mass, c.cg, c.iyy_about(rocket.cg))

All units are whatever the file uses -- OpenRocket stores geometry in SI
(meters, kg) internally regardless of the display unit configured in the
app, so this module works entirely in meters / kilograms.
"""



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

# hard coded engine component for hybrids
@dataclass
class EngineComponent:
    name: str
    dry_mass: float # lbs
    offset: float  # lbs
    length: float #inches
    prop_mass: float = 0.0   # to hold depletion (plumbing has none)
    radius: float = None

    def cg_offset(self) -> float:
        # assuming uniform density
        return self.offset + self.length / 2

@dataclass
class Engine:
    tank:      EngineComponent   # oxidizer
    plumbing:  EngineComponent   # injector, valves, lines
    grain:     EngineComponent   # fuel grain + casing
    length: float #inches 
    offset: float #inches
    thrusts:   np.ndarray = None
    times:     np.ndarray = None

    def __post_init__(self):
        self._curve_ready = False
        if self.thrusts is not None and self.times is not None:
            self._process_curve()

    def set_curve(self, thrusts, times):
        self.thrusts = thrusts
        self.times = times
        self._process_curve()

    def _process_curve(self):
        assert len(self.thrusts) == len(self.times)
        cumulative = np.zeros(len(self.times))
        cumulative[1:] = np.cumsum(0.5 * (self.thrusts[:-1] + self.thrusts[1:]) * np.diff(self.times))
        self.total_impulse = cumulative[-1]
        self._frac_expended = cumulative / self.total_impulse
        self.burn_time = self.times[-1]
        self._curve_ready = True

    def _check_ready(self):
        if not self._curve_ready:
            raise RuntimeError("Thrust curve not set — call set_curve(thrusts, times) first")

    def _frac_at(self, t):
        t = np.asarray(t, dtype=float)
        frac = np.interp(t, self.times, self._frac_expended, left=0.0, right=1.0)
        return np.where(t >= self.burn_time, 1.0, frac)

    def thrust_at(self, t):
        self._check_ready()
        t = np.asarray(t, dtype=float)
        return np.interp(t, self.times, self.thrusts, left=0.0, right=0.0)

    def mass_at(self, t):
        self._check_ready()
        frac = self._frac_at(t)
        # oxidizer depletes with the thrust curve; grain regression can use
        # the same frac, or a separate curve if you're tracking O/F ratio
        tank_mass = self.tank.dry_mass + self.tank.prop_mass * (1 - frac)
        grain_mass = self.grain.dry_mass + self.grain.prop_mass * (1 - frac)
        plumbing_mass = self.plumbing.dry_mass
        return tank_mass + grain_mass + plumbing_mass

    def cg_at(self, t):
        self._check_ready()
        frac = self._frac_at(t)

        tank_mass = self.tank.dry_mass + self.tank.prop_mass * (1 - frac)
        grain_mass = self.grain.dry_mass + self.grain.prop_mass * (1 - frac)
        plumbing_mass = self.plumbing.dry_mass

        total = tank_mass + grain_mass + plumbing_mass
        moment = (tank_mass * self.tank.cg_offset()
                  + grain_mass * self.grain.cg_offset()
                  + plumbing_mass * self.plumbing.cg_offset())
        # cg_offset() is measured aft of the engine's own forward face
        return self.offset + moment / total
    
    @staticmethod
    def _rod_iyy(m: float, L: float) -> float:
        return (1.0 / 12.0) * m * L ** 2 if L > 0 else 0.0

    def iyy_at(self, t, rocket_cg: float) -> float:
        """Engine's contribution to pitch Iyy about rocket_cg, at time t."""
        self._check_ready()
        frac = self._frac_at(t)
        tank_mass = self.tank.dry_mass + self.tank.prop_mass * (1 - frac)
        grain_mass = self.grain.dry_mass + self.grain.prop_mass * (1 - frac)
        plumbing_mass = self.plumbing.dry_mass

        total = 0.0
        for comp, m in ((self.tank, tank_mass),
                         (self.grain, grain_mass),
                         (self.plumbing, plumbing_mass)):
            cg_local = self.offset + comp.cg_offset()
            d = cg_local - rocket_cg
            total += self._rod_iyy(m, comp.length) + m * d ** 2
        return total
# --------------------------------------------------------------------------
# small parsing helpers
# --------------------------------------------------------------------------

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
                 engine: Optional[Engine] = None):
        self.name = name
        self.parts: List[BodyPart] = parts
        self.fins: List[FinSet] = fins
        self.engine: Optional[Engine] = engine   # <-- actually assign it now

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

        return cls(name, parts, fins, engine=engine if engine is not None else engine1)

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

    # -- NEW: time-varying quantities including the engine -----------
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

# --------------------------------------------------------------------------
# recursive tree walker
# --------------------------------------------------------------------------

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
    cgs = np.array([rocket.cg_at(t) for t in times])
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

engine1 = Engine(EngineComponent(name="ox_tank", dry_mass=0.35, prop_mass=1.2, offset=0.0, length=0.45),
    EngineComponent(name="plumbing", dry_mass=0.15, offset=0.45, length=0.08),
    EngineComponent(name="fuel_grain", dry_mass=0.4, prop_mass=0.1, offset=0.53, length=0.30), length=50, offset=130)

# --------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    print("HELLO?")
    path = rf"G:\Shared drives\TAMU-SRT\srt_general\9_flight_data\Morpheus\04232025\morph.xml"
    rocket = Rocket.from_file(path)
    print(rocket.summary())
    print()
    print(f"{'component':35s}{'mass (kg)':>12s}{'cg (m)':>12s}{'Iyy@cg (kg*m^2)':>18s}")
    cg = rocket.cg
    rows = [(p.name, p.mass, p.cg, p.iyy_about(cg)) for p in rocket.parts if p.mass > 1e-9]
    rows += [(f.name + " (fins)", f.mass, f.cg, f.iyy_about(cg)) for f in rocket.fins]
    rows.sort(key=lambda r: r[2])
    for n, m, c, i in rows:
        print(f"{n[:34]:35s}{m:12.4f}{c:12.4f}{i:18.5f}")

# math Functions 

def angle(v_up, v_dr, v_cr, tilt):
    '''flight angle and angle of attack'''
    speed = np.sqrt(v_up**2 + v_dr**2 + v_cr**2)
    ratio = np.divide(v_up, speed, out=np.zeros_like(speed, dtype=float), where=speed > 1e-9)
    ang = np.degrees(np.arccos(np.clip(ratio, -1, 1)))
    aoa = tilt - ang
    return ang, aoa

def magnitude(*args):
    args = np.array(args)
    return np.sqrt(sum(args**2))

def v_mach(temperature, v):
    rankine = temperature + 459.67
    sound = np.sqrt(1.4*1716*(rankine))
    return sound, v/sound

def velocity(x, y, z, t):
    vx = np.diff(x)/np.diff(t)
    vy = np.diff(y)/np.diff(t)
    vz = np.diff(z)/np.diff(t)
    return np.insert(magnitude(vx,vy,vz), 0, 0)

def pad_to(arr, n):
    '''right-pad with zeros to length n'''
    arr = np.asarray(arr, float)
    return arr[:n] if arr.size >= n else np.concatenate([arr, np.zeros(n - arr.size)])

def acceleration(v_up, v_dr, v_cr, apogee, t, window_length=71):
    '''accelerations are computed to apogee, samples past it are zero padding'''
    smooth = savgol_filter(np.stack([v_cr, v_dr, v_up])[:, :apogee],
                           window_length=window_length, polyorder=3, axis=-1)
    accelx, accely, accelz = (pad_to(np.gradient(v, t[:apogee]), t.shape[0])
                              for v in smooth)

    total = magnitude(accelx, accely, accelz)
    
    return accelx, accely, accelz, total

def ndcheck_no_gyro(in_a, in_dr, in_cr, t, mass, thrust, apogee, gravity=G_FT, eps=1e-8):

    # single derivative for velocity (less noisy than double-diff)
    
    vdr = np.gradient(in_dr[:apogee], t[:apogee])
    vcr = np.gradient(in_cr[:apogee], t[:apogee])
    va  = np.gradient(in_a[:apogee], t[:apogee])
    v_vec = np.stack([vdr, vcr, va], axis=-1)          # (N,3)
    v_mag = np.linalg.norm(v_vec, axis=-1, keepdims=True)
    x_hat = v_vec / np.maximum(v_mag, eps)              # avoid div by zero at apex/launch
    # acceleration
    adr = np.gradient(vdr, t[:apogee])
    acr = np.gradient(vcr, t[:apogee])
    aa  = np.gradient(va, t[:apogee])
    a_vec = np.stack([adr, acr, aa], axis=-1)           # (N,3)

    g_vec = np.array([0, 0, -gravity])
    fnet_vec = mass[:apogee, None]*a_vec - mass[:apogee, None]*g_vec  # (N,3)

    # normal direction: component of accel perpendicular to velocity
    a_dot_x = np.sum(a_vec * x_hat, axis=-1, keepdims=True)
    a_perp = a_vec - a_dot_x * x_hat
    a_perp_mag = np.linalg.norm(a_perp, axis=-1, keepdims=True)
    z_hat = a_perp / np.maximum(a_perp_mag, eps)

    faxial = np.sum(fnet_vec * x_hat, axis=-1)
    fn     = np.sum(fnet_vec * z_hat, axis=-1)
    fd     = thrust[:apogee] - faxial   # drag opposes the velocity vector

    return fn, fd

def ndcheck_with_aoa(in_a, in_dr, in_cr, t, mass, thrust, aoa, gravity=G_FT, eps=1e-8):
    """
    aoa: array of angle-of-attack values (radians), same length as t
    """
    aoa = aoa/180 * np.pi
    vdr = np.gradient(in_dr, t)
    vcr = np.gradient(in_cr, t)
    va  = np.gradient(in_a, t)
    v_vec = np.stack([vdr, vcr, va], axis=-1)
    v_mag = np.linalg.norm(v_vec, axis=-1, keepdims=True)
    x_hat_v = v_vec / np.maximum(v_mag, eps)

    adr = np.gradient(vdr, t)
    acr = np.gradient(vcr, t)
    aa  = np.gradient(va, t)
    a_vec = np.stack([adr, acr, aa], axis=-1)

    g_vec = np.array([0, 0, -gravity])
    fnet_vec = mass[:, None]*a_vec - mass[:, None]*g_vec

    # perpendicular direction (maneuver-plane normal), as before
    a_dot_x = np.sum(a_vec * x_hat_v, axis=-1, keepdims=True)
    a_perp = a_vec - a_dot_x * x_hat_v
    a_perp_mag = np.linalg.norm(a_perp, axis=-1, keepdims=True)
    n_hat = a_perp / np.maximum(a_perp_mag, eps)

    # rotate by AoA within the maneuver plane
    cos_a = np.cos(aoa)[:, None]
    sin_a = np.sin(aoa)[:, None]
    x_hat_body =  cos_a * x_hat_v + sin_a * n_hat
    z_hat_body = -sin_a * x_hat_v + cos_a * n_hat

    faxial = np.sum(fnet_vec * x_hat_body, axis=-1)
    fn     = np.sum(fnet_vec * z_hat_body, axis=-1)
    fd     = thrust - faxial            # drag opposes the velocity vector

    return fn, fd

def theta(v_dr,v_cr):
    '''azimuth of the velocity vector in the horizontal plane, degrees in (-180, 180]'''
    return np.nan_to_num(np.degrees(np.arctan2(v_dr, v_cr)))

def axial_unit(tilt, theta):
    '''body axial unit vector stacked as (..., 3), from tilt off vertical
    and horizontal azimuth'''
    st, ct = np.sin(np.radians(tilt)), np.cos(np.radians(tilt))
    return np.stack([st*np.cos(np.radians(theta)), st*np.sin(np.radians(theta)),
                     np.broadcast_to(ct, np.shape(st))], axis=-1)

def specific_force(accelx, accely, accelz):
    '''acceleration stacked as (..., 3) with gravity added back into z'''
    return np.stack([accelx, accely, accelz + G_FT], axis=-1)

def thrust(weight, theta, accel_x, accel_y, accel_z, Fdx, Fdy, Fdz):
    '''thrust implied by measured acceleration and a known aerodynamic force vector.
    this is the algebraic inverse of fd(), so it is an independent estimate
    only when Fd comes from an independent aerodynamic model'''
    mass = np.asarray(weight, float)/G_FT
    ft = mass[..., None] * specific_force(accel_x, accel_y, accel_z) \
         + np.stack([Fdx, Fdy, Fdz], axis=-1)
    return np.linalg.norm(ft, axis=-1)

def fd(thrust, tilt, theta, weight, accelx, accely, accelz):
    '''total aerodynamic force acting upon the rocket, based on in flight data with
    thrust being predicted. the returned vector points opposite the aerodynamic
    force; resolve it with wind_axes() for drag or with the body axis for axial force'''

    # finding force provided by thrust in respective directions, subtracting total force in that direction
    mass = np.asarray(weight, float)/G_FT
    f = np.asarray(thrust, float)[..., None] * axial_unit(tilt, theta) \
        - mass[..., None] * specific_force(accelx, accely, accelz)

    return f[..., 0], f[..., 1], f[..., 2], np.linalg.norm(f, axis=-1)

def wind_axes(fdx, fdy, fdz, v_cr, v_dr, v_up, eps=1e-9):
    '''resolve the aerodynamic force into drag along the relative wind and lift
    perpendicular to it. still air is assumed, so the relative wind is the
    vehicle velocity; subtract a measured wind vector from it when one is available'''
    f = np.stack([fdx, fdy, fdz], axis=-1)
    v = np.stack([v_cr, v_dr, v_up], axis=-1)
    vhat = v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), eps)
    drag = np.sum(f * vhat, axis=-1)
    lift = np.linalg.norm(f - drag[..., None] * vhat, axis=-1)
    return drag, lift

def fn1(tilt, theta, weight, ax, ay, az):
    '''finding the normal force acting on the rocket, based on accelerometer data'''
    # removing acceleration due to gravity, subtracting by including the angle 
    accel = specific_force(ax, ay, az)
    # finding axial unit vector, concerning how force is being distributed 
    axial = axial_unit(tilt, theta)
    # dot product of acceleration for axial acceleration
    a_axial = np.sum(accel * axial, axis=-1, keepdims=True)
    # subtract component, multuply by mass 
    Fn = (accel - a_axial * axial) * (np.asarray(weight, float)/G_FT)[..., None]
    return Fn[..., 0], Fn[..., 1], Fn[..., 2], np.linalg.norm(Fn, axis=-1)

def fn2(weight, g_accelx, g_accelz):
    '''finding the normal force acting on the rocket, assuming that the gyroscope is the acceleration'''
    return weight/G_FT * magnitude(g_accelx, g_accelz)

def density(pressure, temperature):
    '''density using ideal gas law, constants used reflect atm and F'''
    return (pressure * 2116.22)/(53.35*((temperature + 459.67)))

def dynamic_pressure(density, velocity):
    '''q in lbf/ft^2 from density in lbm/ft^3 and velocity in ft/s'''
    return 0.5 * density * velocity**2 / G_FT

def ref_area(diameter):
    '''diameter in feet'''
    return np.pi * (diameter/2)**2

def cd(fd, density, velocity, diameter=2*radius/IN_PER_FT):
    '''diameter in feet'''
    denom = dynamic_pressure(density, velocity) * ref_area(diameter)
    return np.divide(fd, denom, out=np.full_like(np.asarray(fd, float), np.nan),
                     where=np.abs(denom) > 1e-9)

def rolling_slope(xv, yv, window, min_var):
    '''rolling least squares slope dy/dx, nan safe and valid for a non-monotonic x'''
    good = np.isfinite(xv) & np.isfinite(yv)
    xs, ys = np.where(good, xv, 0.0), np.where(good, yv, 0.0)
    w = good.astype(float)
    def mean(a):
        s = uniform_filter1d(a, window, mode='nearest')
        c = uniform_filter1d(w, window, mode='nearest')
        return np.divide(s, c, out=np.full_like(s, np.nan), where=c > 0)
    mx, my = mean(xs), mean(ys)
    var, cov = mean(xs*xs) - mx*mx, mean(xs*ys) - mx*my
    ok = var > min_var
    return np.where(ok, cov / np.where(ok, var, 1.0), np.nan)

def cna(fn, density, velocity, aoa, diameter=2*radius/IN_PER_FT, window=201, min_spread_deg=0.25):
    '''normal force coefficent, and its slope with respect to angle of attack.
    aoa oscillates, so the slope is fitted by rolling least squares against a
    non-monotonic coordinate'''
    denom = dynamic_pressure(density, velocity) * ref_area(diameter)
    cn = np.divide(fn, denom, out=np.full_like(np.asarray(fn, float), np.nan),
                   where=np.abs(denom) > 1e-9)
    # derivative of normal coefficent, per radian
    cna = rolling_slope(np.radians(aoa), cn, window, np.radians(min_spread_deg)**2)
    return cn, cna

def frequency(aoa, sample_rate, target_time, f_min=0.2):
    '''natrual frequency, from short fourier transform functions to isolate the atrual frequency of aoa oscillations'''
    sig = np.nan_to_num(np.asarray(aoa, float) - np.nanmean(aoa)) # oscillation about the trim aoa
    f, t, Zxx = stft(sig, fs=sample_rate, window='hann', nperseg=256) # finding frequency, time bucket, and array values 
    keep = f >= f_min # the dc bin holds the trim offset, not an oscillation
    f, Zxx = f[keep], Zxx[keep, :]
    t_index = np.argmin(np.abs(t[:, np.newaxis] - target_time), axis=0) # index, finding what each time value is closest to for bin sorting
    power = np.abs(Zxx[:, t_index]) # powers at that value
    frequency_n = f[np.argmax(power, axis=0)] # dominant frequency
    return frequency_n

def stability1(frequency_n, inertia_yy, velocity, density, aoa, fn, diameter=2*radius/IN_PER_FT):
    '''static margin in calibers from the pitch oscillation frequency, where the
    corrective moment coefficient C1 = omega^2 Iyy = q A d CNalpha SM'''
    omega = 2 * np.pi * frequency_n                       # rad/s
    iyy_slug_ft2 = inertia_yy / (G_FT * IN_PER_FT**2)     # lbm-in^2 -> slug-ft^2
    m_corrective = omega**2 * iyy_slug_ft2                # lbf-ft
    cn_a = cna(fn, density, velocity, aoa, diameter=diameter)[1]
    denom = dynamic_pressure(density, velocity) * ref_area(diameter) * diameter * cn_a
    sm = np.divide(m_corrective, denom, out=np.full_like(m_corrective, np.nan),
                   where=np.abs(denom) > 1e-12)
    return sm 

def stability(time, inertia_yy, gyro_y, fn, diameter=2*radius/IN_PER_FT, window=71, regression=1001):
    '''static margin in calibers from pitch angular acceleration, Iyy qdot = Fn d SM.
    gyro_y is smoothed before differentiating, and the margin is taken as a rolling
    regression of the restoring moment on Fn d, which stays defined where both
    oscillate through zero'''
    gyro_smooth = savgol_filter(np.radians(gyro_y), window_length=window, polyorder=3)
    q_accel = np.gradient(gyro_smooth, time)              # rad/s^2
    iyy_slug_ft2 = inertia_yy / (G_FT * IN_PER_FT**2)     # lbm-in^2 -> slug-ft^2
    moment = iyy_slug_ft2 * q_accel                       # lbf-ft
    arm = np.asarray(fn, float) * diameter                # lbf-ft per caliber
    num = uniform_filter1d(moment * arm, regression, mode='nearest')
    den = uniform_filter1d(arm * arm, regression, mode='nearest')
    sm = np.divide(num, den, out=np.full_like(num, np.nan), where=np.abs(den) > 1e-12)
    return sm

### math functions ^
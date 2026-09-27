from dataclasses import dataclass
from typing import Optional
import math
import numpy as np

def cumulative(y: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Cumulative trapezoidal integral of y dx, same length as y, starting at 0."""
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    out = np.zeros_like(y)
    out[1:] = np.cumsum(0.5 * (y[:-1] + y[1:]) * np.diff(x))
    return out

@dataclass
class EngineComponent:
    "Used to house the properties of the casing/structural/hardware components, namely oxidizer, plumbing, and fuel components"
    name: str
    dry_mass: float            # lbm, hardware only
    offset: float               # in, distance from engine reference to forward face
    length: float                 # in
    radius: Optional[float] = None   # in, used for volume/CG defaults

    def cg_offset(self) -> float:
        """CG of the dry hardware, assuming uniform density"""
        return self.offset + self.length / 2

    def volume(self) -> float:
        """Internal volume assuming a simple cylinder, in^3.
        S"""
        if self.radius is None:
            raise ValueError(f"{self.name}: radius not set, cannot compute volume")
        return math.pi * self.radius**2 * self.length
    
@dataclass
class FuelGrain:
    """
    Cylindrical hybrid fuel grain, port burns outward radially.
    Mass comes from integrating the fuel mass-flow-rate sensor; port
    radius is back-solved from that mass plus grain geometry/density.
    """

    casing: EngineComponent           # dry hardware: grain liner/casing
    outer_radius_in: float
    initial_port_radius_in: float
    length_in: float
    fuel_density_lbm_in3: float
    times_s: np.ndarray
    mdot_lbm_s: np.ndarray               # fuel mass flow rate sensor

    def __post_init__(self):
        self.times_s = np.asarray(self.times_s, dtype=float)
        self.mdot_lbm_s = np.asarray(self.mdot_lbm_s, dtype=float)
        assert len(self.times_s) == len(self.mdot_lbm_s)
        self._cum_mass_lost = cumulative(self.mdot_lbm_s, self.times_s)
        self.initial_fuel_mass_lbm = (
            self.fuel_density_lbm_in3 * math.pi * self.length_in
            * (self.outer_radius_in**2 - self.initial_port_radius_in**2)
        )

    def mass_at(self, t):
        cum_lost = np.interp(t, self.times_s, self._cum_mass_lost)
        return np.maximum(self.initial_fuel_mass_lbm - cum_lost, 0.0)

    def port_radius_at(self, t):
        """Regressed port radius, back-solved from remaining fuel mass."""
        m = self.mass_at(t)
        r2 = self.outer_radius_in**2 - m / (self.fuel_density_lbm_in3 * math.pi * self.length_in)
        return np.sqrt(np.maximum(r2, 0.0))

    def cg_at(self, t) -> float:
        # Purely radial regression keeps the fuel's axial centroid fixed
        # at the grain midpoint, regardless of how much has burned.
        return self.casing.offset + self.length_in / 2

    def total_mass_at(self, t) -> float:
        return self.mass_at(t) + self.casing.dry_mass


@dataclass
class SolidGrain:
    """
    Cylindrical solid propellant grain (BATES-style), burns outward
    radially from a central core -- geometrically identical to the hybrid
    FuelGrain's burn pattern. Mass comes from integrating a thrust curve
    (mass flow assumed proportional to thrust, i.e. constant Isp over the
    burn) rather than a direct mdot sensor, since solid motors are
    characterized by thrust data, not a flow sensor. Port radius is
    back-solved from that mass exactly as in the hybrid case.
    """

    casing: EngineComponent            # dry hardware: grain liner/casing
    outer_radius_in: float
    initial_port_radius_in: float
    length_in: float
    propellant_density_lbm_in3: float
    times_s: np.ndarray
    thrust_lbf: np.ndarray             # thrust curve, NOT a mass-flow sensor

    def __post_init__(self):
        self.times_s = np.asarray(self.times_s, dtype=float)
        self.thrust_lbf = np.asarray(self.thrust_lbf, dtype=float)
        assert len(self.times_s) == len(self.thrust_lbf)

        self.initial_fuel_mass_lbm = (
            self.propellant_density_lbm_in3 * math.pi * self.length_in
            * (self.outer_radius_in**2 - self.initial_port_radius_in**2)
        )

        # mass flow assumed proportional to thrust -> cumulative impulse,
        # normalized to [0, 1], IS the burn fraction. total_impulse cancels
        # the (unknown) Isp, so it never needs to be known explicitly.
        cumulative_impulse = cumulative(self.thrust_lbf, self.times_s)
        total_impulse = cumulative_impulse[-1]
        self._frac_burned = np.divide(cumulative_impulse, total_impulse,
                                       out=np.zeros_like(cumulative_impulse),
                                       where=total_impulse > 0)

    def mass_at(self, t):
        frac_burned = np.interp(t, self.times_s, self._frac_burned, left=0.0, right=1.0)
        return self.initial_fuel_mass_lbm * (1 - frac_burned)

    def port_radius_at(self, t):
        """Regressed port radius, back-solved from remaining propellant
        mass -- identical relation to the hybrid FuelGrain, since both
        burn radially outward from a central core."""
        m = self.mass_at(t)
        r2 = self.outer_radius_in**2 - m / (self.propellant_density_lbm_in3 * math.pi * self.length_in)
        return np.sqrt(np.maximum(r2, 0.0))

    def cg_at(self, t) -> float:
        # Purely radial regression keeps the propellant's axial centroid
        # fixed at the grain midpoint, regardless of how much has burned --
        # same assumption as the hybrid case.
        return self.casing.offset + self.length_in / 2

    def total_mass_at(self, t) -> float:
        return self.mass_at(t) + self.casing.dry_mass
    
class SolidMotor:
    """CG/Iyy model for a solid rocket motor: one propellant grain that
    depletes over the burn, inside a casing, plus fixed hardware (nozzle,
    igniter, closures) that never loses mass. All quantities are imperial
    throughout: mass in lbm, length/cg/offset in inches, iyy in lbm-in^2 --
    matching flight_analysis_functions.py's convention (see stability()'s
    lbm-in^2 -> slug-ft^2 conversion, which is the point where this would
    ever need to leave imperial).

    Mirrors HybridEngine: _component_states() is the single source of truth
    for "mass + cg of each component at time t", used by both cg_at() and
    iyy_at() (and a dry-state variant for burnout) so they can't disagree."""

    def __init__(self, grain, hardware, offset_in: float = 0.0):
        """
        grain: a SolidGrain/FuelGrain-like component -- total_mass_at(t),
            mass_at(t) (propellant remaining), cg_at(t) (WHOLE assembly's
            cg -- casing + propellant already combined, not propellant-only),
            casing.dry_mass, length_in.
        hardware: fixed-mass hardware (nozzle/igniter/closures) -- dry_mass,
            cg_offset(), length (assumed inches; see _component_length_in).
        offset_in: motor's mounting offset from the rocket nose, inches.
        """
        self.grain = grain
        self.hardware = hardware
        self.offset_in = offset_in
        self._ready = grain is not None and hardware is not None
        print("times_s monotonic?", np.all(np.diff(grain.times_s) > 0))
        print("total_impulse:", grain._frac_burned[-1] if hasattr(grain, '_frac_burned') else None)
        print(grain.times_s[-5:])
    def _check_ready(self):
        if not self._ready:
            raise RuntimeError("SolidMotor is missing its grain or hardware component")

    @staticmethod
    def _component_length_in(comp):
        """Length of a motor component, in inches -- resolves the naming
        mismatch between SolidGrain/FuelGrain (length_in) and EngineComponent
        (length), given both are already imperial."""
        return comp.length_in if hasattr(comp, 'length_in') else comp.length

    @staticmethod
    def _rod_iyy(m, length_in):
        """Iyy of a slender rod/cylinder about its own cg, in lbm-in^2.
        m: lbm, length_in: inches."""
        return m * length_in**2 / 12.0

    # -- shared mass/cg state, consumed by cg_at(), iyy_at(), mass_at() ----
    def _component_states(self, t_arr):
        """(component, mass, cg) triples at time t_arr, vectorized, cg in
        the motor's local coordinates (not yet shifted by offset_in).
        grain.cg_at() already returns the whole grain assembly's cg -- no
        separate casing/propellant mass-weighting needed, since SolidGrain
        fixes the propellant centroid at the grain midpoint under the
        radial-burn assumption and folds the casing in there too."""
        grain_mass = self.grain.total_mass_at(t_arr)
        grain_cg = np.full_like(grain_mass, self.grain.cg_at(t_arr))

        hardware_mass = np.full_like(grain_mass, self.hardware.dry_mass)
        hardware_cg = np.full_like(grain_mass, self.hardware.cg_offset())

        return (
            (self.grain, grain_mass, grain_cg),
            (self.hardware, hardware_mass, hardware_cg),
        )

    def _dry_component_states(self):
        """(component, mass, cg) with propellant fully depleted -- the
        burnout endpoint. grain.cg_at() is constant under the radial-burn
        assumption, so any t gives the correct dry-state cg; 0.0 is used
        here just as an arbitrary valid input."""
        return (
            (self.grain, self.grain.casing.dry_mass, self.grain.cg_at(0.0)),
            (self.hardware, self.hardware.dry_mass, self.hardware.cg_offset()),
        )

    # -- mass ----------------------------------------------------------------
    def mass_at(self, t):
        self._check_ready()
        t_arr = np.atleast_1d(np.asarray(t, dtype=float))
        total = sum(m for _, m, _ in self._component_states(t_arr))
        scalar_in = np.isscalar(t) or np.asarray(t).ndim == 0
        return float(total[0]) if scalar_in else total

    def mass_dry(self):
        self._check_ready()
        return sum(m for _, m, _ in self._dry_component_states())

    # -- cg --------------------------------------------------------------
    def cg_at(self, t):
        """CG location (inches), measured from the rocket nose, at time t.
        t may be scalar or array; the return matches its shape."""
        self._check_ready()
        t_arr = np.atleast_1d(np.asarray(t, dtype=float))
        states = self._component_states(t_arr)
        total = sum(m for _, m, _ in states)
        moment = sum(m * cg for _, m, cg in states)
        cg = self.offset_in + np.divide(moment, total, out=np.zeros_like(total), where=total > 0)

        scalar_in = np.isscalar(t) or np.asarray(t).ndim == 0
        return float(cg[0]) if scalar_in else cg

    def cg_dry(self):
        """CG location (inches) once propellant is fully depleted."""
        self._check_ready()
        states = self._dry_component_states()
        total = sum(m for _, m, _ in states)
        moment = sum(m * cg for _, m, cg in states)
        return self.offset_in + (moment / total if total else 0.0)

    # -- iyy -------------------------------------------------------------
    def _iyy_from_states(self, states, rocket_cg):
        """Parallel-axis sum, in lbm-in^2: each component's own rod iyy about
        its own cg, shifted out to rocket_cg by m*d**2. rocket_cg, self.offset_in
        and every component's cg/length are all inches; mass is lbm throughout."""
        total = 0.0
        for comp, m, cg in states:
            cg_global = self.offset_in + cg
            d = cg_global - rocket_cg
            total = total + self._rod_iyy(m, self._component_length_in(comp)) + m * d**2
        return total

    def iyy_at(self, t, rocket_cg):
        """Motor's contribution to pitch Iyy (lbm-in^2) about rocket_cg, at
        time t. rocket_cg is the rest-of-rocket cg, computed once by the
        caller -- only the motor's own mass distribution moves as
        propellant burns."""
        self._check_ready()
        t_arr = np.atleast_1d(np.asarray(t, dtype=float))
        iyy = self._iyy_from_states(self._component_states(t_arr), rocket_cg)

        scalar_in = np.isscalar(t) or np.asarray(t).ndim == 0
        return float(iyy[0]) if scalar_in else iyy

    def iyy_dry(self, rocket_cg):
        """Motor's contribution to pitch Iyy (lbm-in^2) at burnout
        (propellant fully depleted) -- same parallel-axis sum as
        iyy_at(), dry state instead."""
        self._check_ready()
        return self._iyy_from_states(self._dry_component_states(), rocket_cg)
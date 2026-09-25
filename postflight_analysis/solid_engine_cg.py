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
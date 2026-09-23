"""
Hybrid rocket engine center of mass model.

Notes:

1. oxidizer tank is tracked as a two-phase liquid/vapor system,
   but *not* assumed to be in saturation equilibrium as a whole. 
     - the VAPOR/ullage space is assumed saturated at the measured tank
       pressure (small mass, large surface area -> equilibrates fast),
       giving v_v = v_g(P(t)) from the N2O dome.
     - the LIQUID is assumed to sit at a fixed (measured or assumed)
       bulk temperature `liquid_temp_F`, and its specific volume is
       looked up as compressed/subcooled liquid at that temperature and
       the current tank pressure, v_l(T_liquid, P(t)) -- not read off
       the saturation dome at the tank pressure.
   Remaining mass is determined by mass flow rate sensor, as a function of time
   The liquid/vapor mass split is solved through the volume balance V_tank = m_l*v_l + m_v*v_v, 
   not a saturation-quality equation. These will be assigned different areas of the tank, liquid at the bottom, vapor everywhere else
   
   ***NOTE FROM CLAUDE: (A natural next step, not implemented here, is to let
   `liquid_temp_F` drift over time via its own energy balance instead
   of holding it fixed -- ask if you want that added.)

2. Fuel grain mass comes from integrating fuel mass-flow-rate
   sensor, and is converted to an actual regressed port radius using
   the grain's geometry and density -- so you get a real r(t) burn-back
   profile instead of a linear fraction.

3. Saturated N2O specific volumes (v_f, v_g) are looked up via CoolProp
   (`pip install CoolProp`), which uses a real NIST-grade equation of
   state for N2O. 



ASSUMPTIONS:
 - treating the tank and grain as simple cylinders (constant cross-section, no special consideration for the shape of the spiral).
 - Tank: liquid pools at the `offset` (forward) end, vapor ullage fills
   the rest toward the aft end. Flip `cg_at` if your tank sits the
   other way (e.g. injector/liquid draw from the aft end).
 - Ox tank liquid is held at a single fixed bulk temperature
   (`liquid_temp_F`) for the whole burn -- it does NOT track the
   saturation temperature implied by the pressure sensor. Only the
   vapor space is assumed saturated at the measured pressure. If your
   actual liquid temperature drifts a lot over the burn (long
   soak/coast periods, big ullage swings), a fixed value will be
   least accurate near the end of the burn.
 - Grain regresses radially only (burns from a central port outward),
   so its axial CG stays at the geometric midpoint. If you also get
   axial burn-back (e.g. exposed grain ends), you need a 2-D burn-back
   model instead -- ping me and we can extend this.
 - Mass-flow-rate and pressure sensor arrays share a time base, or at
   least can be linearly interpolated onto one; `times_s` for the tank
   and `times_s` for the grain do NOT need to match each other.
 - Chamber/grain pressure isn't used for mass accounting here since you
   already have a direct fuel mass-flow-rate sensor, which is more
   direct than back-calculating mass from a regression-rate
   correlation (e.g. r_dot = a*Gox^n) and a pressure trace. If you'd
   rather cross-check the flow sensor against a regression law using
   grain pressure, that's a separate, additive check -- say the word
   and we can bolt it on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from os import path
from typing import Optional
import numpy as np
from pathlib import Path
import pint
import pandas as pd

import matplotlib.pyplot as plt

try:
    from CoolProp.CoolProp import PropsSI
    _HAS_COOLPROP = True
except ImportError:
    _HAS_COOLPROP = False


IN_TO_M = 0.0254
LBM_TO_KG = 0.45359237
PSI_TO_PA = 6894.757293168



def cumulative(y: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Cumulative trapezoidal integral of y dx, same length as y, starting at 0."""
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    out = np.zeros_like(y)
    out[1:] = np.cumsum(0.5 * (y[:-1] + y[1:]) * np.diff(x))
    return out



class N2OSaturation:
    """
    Defining the liquid/vapor properties within the oxidizer tank with
    N2O saturation-dome property lookups. Called through N2OSaturation.function

    `vf_vg(P)` is kept around for reference / for anyone who wants to
    run the simpler full-equilibrium model as a comparison, but the
    tank model below only uses `vg(P)` (saturated vapor, assumed valid
    for the ullage) plus `subcooled_liquid_v` (NOT assumed saturated).
    """

    @classmethod
    def vf_vg(cls, pressure_pa: float) -> tuple[float, float]:
        """Return (v_f, v_g) in m^3/kg at the given saturation pressure (Pa)."""
        vf = 1.0 / PropsSI("D", "P", pressure_pa, "Q", 0, "N2O")
        vg = 1.0 / PropsSI("D", "P", pressure_pa, "Q", 1, "N2O")
        return vf, vg

    @classmethod
    def vg(cls, pressure_pa: float) -> float:
        """Saturated vapor specific volume (m^3/kg) at the given pressure -- used for the ullage."""

        return 1.0 / PropsSI("D", "P", pressure_pa, "Q", 1, "N2O")

    @classmethod
    def p_sat(cls, temperature_k: float) -> float:
        """Pressure needed to boil, when sitting at a given temperature (K)."""
        return PropsSI("P", "T", temperature_k, "Q", 0, "N2O")
       
    @classmethod
    def subcooled_liquid_v(cls, temperature_k: float, pressure_pa: float) -> float:
        """
        Specific volume ((m^3/kg) of liquid N2O, at given temperature (K) and with given surrounding pressure

        If `pressure_pa` is at/below the saturation
        pressure for that temperature, the liquid would actually be
        boiling there -- our fixed-liquid-temperature assumption has
        broken down at that instant, so we clamp to the saturated-
        liquid state at that temperature as the least-bad fallback
        (rather than raising, or letting CoolProp fail on an invalid
        two-phase T,P query).
        """
        p_sat = cls.p_sat(temperature_k)
        p_eff = max(pressure_pa, p_sat)
        
            # Right at/near p_sat, T&P aren't independent (that's the
            # definition of saturation) and CoolProp's single-phase
            # solver can't resolve it -- fall back to the saturated
            # liquid state at this temperature, which is the correct
            # limit anyway as p_eff -> p_sat.
        if p_eff <= p_sat * (1 + 1e-4):
            return 1.0 / PropsSI("D", "T", temperature_k, "Q", 0, "N2O")
        # Fallback: liquid is nearly incompressible, so approximate with
        # the saturated-liquid specific volume at this temperature.
        p_at_t = float(np.interp(temperature_k, cls._T, cls._P))
        return float(np.interp(p_at_t, cls._P, cls._VF))


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
class OxidizerTank:
    """
    Self-pressurizing N2O tank tracked as liquid + vapor, WITHOUT
    assuming the bulk liquid is in saturation equilibrium with the
    measured tank pressure.

    `mass_at(t)` comes from integrating the ox mass-flow-rate sensor.
    `phase_split_at(t)` treats the ullage as saturated vapor at the
    measured pressure, and the liquid as sitting at a fixed bulk
    temperature `liquid_temp_F` (subcooled relative to what the
    pressure would imply on the saturation dome), then solves the
    liquid/vapor mass split directly from the tank volume balance.
    `cg_at(t)` uses those masses/volumes to locate the combined
    liquid+vapor centroid inside the tank.
    """

    casing: EngineComponent            # dry hardware: tank wall, bulkheads, etc.
    volume_in3: float                    # fixed internal volume
    initial_ox_mass_lbm: float             # total N2O loaded (liquid + vapor)
    liquid_temp_F: float                     # assumed/measured bulk liquid temperature, held fixed
    times_s: np.ndarray
    pressure_psi: np.ndarray               # tank pressure sensor
    mdot_lbm_s: float                   # ox mass flow rate sensor
    cross_section_area_in2: Optional[float] = None  # defaults from casing.radius

    def __post_init__(self):
        self.times_s = np.asarray(self.times_s, dtype=float)
        self.pressure_psi = np.asarray(self.pressure_psi, dtype=float)
        self.mdot_lbm_s = np.asarray(self.mdot_lbm_s, dtype=float)
        assert len(self.times_s) == len(self.pressure_psi) == len(self.mdot_lbm_s), (
            "times_s, pressure_psi and mdot_lbm_s must all be the same length"
        )
        self._cum_mass_lost = cumulative(self.mdot_lbm_s, self.times_s)
        self.liquid_temp_K = (self.liquid_temp_F - 32.0) * 5.0 / 9.0 + 273.15

        if self.cross_section_area_in2 is None:
            if self.casing.radius is None:
                raise ValueError("Need either cross_section_area_in2 or casing.radius")
            self.cross_section_area_in2 = math.pi * self.casing.radius**2

    def pressure_at(self, t):
        return np.interp(t, self.times_s, self.pressure_psi)

    def mass_at(self, t):
        """Total remaining oxidizer mass (liquid + vapor) in tank, lbm."""
        cum_lost = np.interp(t, self.times_s, self._cum_mass_lost)
        return np.maximum(self.initial_ox_mass_lbm - cum_lost, 0.0)

    def _liquid_vapor_v_at(self, t):
        """Helper: (v_l, v_v) in m^3/kg at time t, per the non-equilibrium model."""
        t_arr = np.atleast_1d(np.asarray(t, dtype=float))
        p_pa = np.atleast_1d(self.pressure_at(t_arr)) * PSI_TO_PA

        v_l = np.empty_like(p_pa)
        v_v = np.empty_like(p_pa)
        for i, p in enumerate(p_pa):
            v_l[i] = N2OSaturation.subcooled_liquid_v(self.liquid_temp_K, p)
            v_v[i] = N2OSaturation.vg(p)
        return v_l, v_v

    def phase_split_at(self, t):
        """
        Returns (liquid_mass_lbm, vapor_mass_lbm, vapor_fraction) at
        time t.

        Non-equilibrium model: the vapor/ullage is assumed saturated at
        the measured pressure (v_v = v_g(P)); the liquid is assumed to
        sit at the fixed bulk temperature `liquid_temp_F`, subcooled
        relative to the saturation dome at the measured pressure
        (v_l = v_liquid(T_liquid, P) -- NOT read off the dome at P).

        The mass split is then solved directly from the tank volume
        balance, not from a saturation-quality equation:

            V_tank = m_l * v_l + m_v * v_v
            m_total = m_l + m_v   (from the integrated flow sensor)

            => m_l = (V_tank - m_total * v_v) / (v_l - v_v)
               m_v = m_total - m_l

        `vapor_fraction` (m_v / m_total) is returned in place of
        "quality" -- it's a similar concept but is no longer a
        thermodynamic quality since the liquid isn't saturated.
        Clipped to [0, m_total] to guard against sensor noise pushing
        the solved split slightly outside physical bounds.
        """
        t_arr = np.atleast_1d(np.asarray(t, dtype=float))
        m_total_lbm = np.atleast_1d(self.mass_at(t_arr))
        v_l, v_v = self._liquid_vapor_v_at(t_arr)

        m_total_kg = m_total_lbm * LBM_TO_KG
        v_tank_m3 = self.volume_in3 * IN_TO_M**3

        m_l_kg = (v_tank_m3 - m_total_kg * v_v) / (v_l - v_v)
        m_l_kg = np.clip(m_l_kg, 0.0, m_total_kg)
        m_v_kg = m_total_kg - m_l_kg

        liquid_mass = m_l_kg / LBM_TO_KG
        vapor_mass = m_v_kg / LBM_TO_KG
        vapor_fraction = np.divide(
            vapor_mass, m_total_lbm,
            out=np.zeros_like(m_total_lbm),
            where=m_total_lbm > 0,
        )

        scalar_in = np.isscalar(t) or np.asarray(t).ndim == 0
        if scalar_in:
            return float(liquid_mass[0]), float(vapor_mass[0]), float(vapor_fraction[0])
        return liquid_mass, vapor_mass, vapor_fraction

    def cg_at(self, t) -> float:
        """
        CG offset of the tank *contents only* (liquid + vapor), in the
        same reference frame as casing.offset. Liquid is assumed to
        pool at the casing.offset (forward) end with vapor filling the
        ullage toward the aft end -- flip liquid/vapor centroids below
        if your tank is oriented the other way.
        """
        # AFTER

        t_arr = np.atleast_1d(np.asarray(t, dtype=float))
        liquid_mass, vapor_mass, _ = self.phase_split_at(t_arr)
        v_l, v_v = self._liquid_vapor_v_at(t_arr)          # per-timestep now, not just [0]

        liquid_vol_in3 = liquid_mass * LBM_TO_KG * v_l / IN_TO_M**3
        vapor_vol_in3 = vapor_mass * LBM_TO_KG * v_v / IN_TO_M**3
        h_liquid = liquid_vol_in3 / self.cross_section_area_in2
        h_vapor = vapor_vol_in3 / self.cross_section_area_in2

        total_mass = liquid_mass + vapor_mass
        liquid_centroid = self.casing.offset + h_liquid / 2
        vapor_centroid = self.casing.offset + h_liquid + h_vapor / 2
        empty_cg = self.casing.offset + self.casing.length / 2

        cg = np.divide(                                      # <-- array-safe, replaces if/else
            liquid_mass * liquid_centroid + vapor_mass * vapor_centroid,
            total_mass,
            out=np.full_like(total_mass, empty_cg),
            where=total_mass > 0,
        )

        scalar_in = np.isscalar(t) or np.asarray(t).ndim == 0
        return float(cg[0]) if scalar_in else cg

    def total_mass_at(self, t) -> float:
        """Tank contents + dry casing mass."""
        return self.mass_at(t) + self.casing.dry_mass

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
class Engine:
    "All together now folks!~"
    tank: OxidizerTank
    plumbing: EngineComponent           # injector, valves, lines -- treated as dry, static
    grain: FuelGrain
    length_in: float
    offset_in: float
    thrusts: Optional[np.ndarray] = None
    times_s: Optional[np.ndarray] = None

    def __post_init__(self):
        self._curve_ready = False
        if self.thrusts is not None and self.times_s is not None:
            self.set_curve(self.thrusts, self.times_s)

    def set_curve(self, thrusts, times_s):
        self.thrusts = np.asarray(thrusts, dtype=float)
        self.times_s = np.asarray(times_s, dtype=float)
        assert len(self.thrusts) == len(self.times_s)
        cumulative = cumulative(self.thrusts, self.times_s)
        self.total_impulse = cumulative[-1]
        self.burn_time = self.times_s[-1]
        self._curve_ready = True

    def thrust_at(self, t):
        if not self._curve_ready:
            raise RuntimeError("Thrust curve not set -- call set_curve(thrusts, times_s) first")
        return np.interp(t, self.times_s, self.thrusts, left=0.0, right=0.0)

    def mass_at(self, t) -> float:
        return (
            self.tank.total_mass_at(t)
            + self.grain.total_mass_at(t)
            + self.plumbing.dry_mass
        )

    def cg_at(self, t):
        t_arr = np.atleast_1d(np.asarray(t, dtype=float))

        tank_mass = self.tank.total_mass_at(t_arr)
        ox_mass = self.tank.mass_at(t_arr)
        tank_dry_cg = self.tank.casing.cg_offset()
        tank_cg = np.divide(                                   # <-- array-safe, replaces if/else
            self.tank.casing.dry_mass * tank_dry_cg + ox_mass * self.tank.cg_at(t_arr),
            tank_mass,
            out=np.full_like(tank_mass, tank_dry_cg),
            where=tank_mass > 0,
        )

        grain_mass = self.grain.total_mass_at(t_arr)
        fuel_mass = self.grain.mass_at(t_arr)
        grain_dry_cg = self.grain.casing.cg_offset()
        grain_cg = np.divide(                                  # <-- array-safe, replaces if/else
            self.grain.casing.dry_mass * grain_dry_cg + fuel_mass * self.grain.cg_at(t_arr),
            grain_mass,
            out=np.full_like(grain_mass, grain_dry_cg),
            where=grain_mass > 0,
        )
        plumbing_mass = self.plumbing.dry_mass
        total = tank_mass + grain_mass + plumbing_mass
        moment = (
            tank_mass * tank_cg
            + grain_mass * grain_cg
            + plumbing_mass * self.plumbing.cg_offset()
        )
        cg = self.offset_in + moment / total

        scalar_in = np.isscalar(t) or np.asarray(t).ndim == 0
        return float(cg[0]) if scalar_in else cg               # <-- scalar in, scalar out

@dataclass
class EngineComponent2:
    name: str
    dry_mass: float # lbs
    offset: float  # lbs
    length: float #inches
    prop_mass: float = 0.0   # to hold depletion (plumbing has none)
    radius: float = None
    volume: float = None 
    mass_flow_rate = float = None

    def cg_offset(self) -> float:
        # assuming uniform density
        return self.offset + self.length / 2

@dataclass
class Engine2:
    tank:      EngineComponent   # oxidizer
    plumbing:  EngineComponent   # injector, valves, lines
    grain:     EngineComponent   # fuel grain + casing
    length: float #inches 
    offset: float #inches
    thrusts:   np.ndarray = None
    times:     np.ndarray = None
    pressure_tank: np.ndarray = None
    pressure_grain: np.ndarray = None
    mass_flow_rate = float = None



    def set_mass_flow_rate(self, mass_flow_rate: float):
        self.mass_flow_rate = mass_flow_rate

    def mass(self):
        if self.mass_flow_rate is not None and self.length is not None:
            return (self.tank.dry_mass + self.tank.prop_mass) - self.mass_flow_rate * self.times

    def specific_volume(self):
        if self.pressure_tank is not None and self.mass_at is not None:
            self.specific_volume = self.tank.volume / self.mass_at
            return self.specific_volume
        # If volume is not set, calculate it based on other parameters
        return self.length * self.radius**2 * math.pi

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


def main():
    path1 = Path(r"G:\Shared drives\TAMU-SRT\srt_general\9_flight_data\Morpheus\04232025_lone_star_cup\SPEC_thrust.csv")
    df = pd.read_csv(path1)

    sol_ignis = Engine2(EngineComponent2(name="ox_tank", dry_mass=8.0, prop_mass=40, offset=10.0, length=24.0),
                        EngineComponent2(name="plumbing", dry_mass=2.0, offset=10.0, length=2.0),
                        EngineComponent2(name="w34", dry_mass=3.0, prop_mass=0.1, offset=36.0, length=12.0), length=38, offset=0.0)

    time = df['Time'].to_numpy()
    thrust = df['Thrust (N)'].to_numpy()
    thrust = thrust/4.448    

    sol_ignis.set_curve(thrust, time)
    print(sol_ignis.cg_at(time))

    path = Path(r"G:\Shared drives\TAMU-SRT\srt_13\7_ground_support_engineering\5_Testing_Operations\9_Tests\4_Test_Completed\IGNIS SET-5\set-5 full data.csv")
    df = pd.read_csv(path)

    t_ns = df['timestamp (ns)'].to_numpy()
    t = (t_ns - t_ns[0]) * 1e-9  # convert to seconds
    ox_pressure = df['sensors/run_tank_pressure.value'].to_numpy()
    ox_mdot = np.full_like(t, 1.45)                      # lbm/s                     # psi, blow-down shape
    ox_pressure = np.clip(ox_pressure, 300, None)

    fuel_mdot = np.full_like(t, 1.45)              # O/F ~5

    tank_casing = EngineComponent(name="ox_tank_casing", dry_mass=8.0, offset=10.0, length=24.0, radius=2.0)
    tank = OxidizerTank(
        casing=tank_casing,
        volume_in3=math.pi * 2.0**2 * 30.0,
        initial_ox_mass_lbm=40.0,
        liquid_temp_F=65.0,  # assumed/measured fill temperature, held fixed for the burn
        times_s=t, pressure_psi=ox_pressure, mdot_lbm_s=ox_mdot,
    )

    grain_casing = EngineComponent(name="grain_casing", dry_mass=3.0, offset=36.0, length=12.0, radius=1.5)
    grain = FuelGrain(
        casing=grain_casing,
        outer_radius_in=1.4, initial_port_radius_in=0.4, length_in=12.0,
        fuel_density_lbm_in3=0.0417,  # ~HTPB, lbm/in^3
        times_s=t, mdot_lbm_s=fuel_mdot,
    )

    plumbing = EngineComponent(name="plumbing", dry_mass=2.0, offset=34.0, length=2.0)

    engine = Engine(tank=tank, plumbing=plumbing, grain=grain, length_in=50.0, offset_in=0.0)
    print(engine)
    plt.plot(t, engine.cg_at(t))
    plt.show()
    return engine.cg_at(t)

if __name__ == "__main__":
    main()
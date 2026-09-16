
from dataclasses import dataclass
import numpy as np
from pathlib import Path
import pint
import pandas as pd

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
            try:
                return 1.0 / PropsSI("D", "T", temperature_k, "P", p_eff, "N2O")
            except ValueError:
                return 1.0 / PropsSI("D", "T", temperature_k, "Q", 0, "N2O")
        # Fallback: liquid is nearly incompressible, so approximate with
        # the saturated-liquid specific volume at this temperature.
        p_at_t = float(np.interp(temperature_k, cls._T, cls._P))
        return float(np.interp(p_at_t, cls._P, cls._VF))


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
    pressures: np.ndarray = None
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
    
sol_ignis = Engine(EngineComponent(name="ox_tank", dry_mass=0.35, prop_mass=1.2, offset=0.0, length=0.45),
    EngineComponent(name="plumbing", dry_mass=0.15, offset=0.45, length=0.08),
    EngineComponent(name="fuel_grain", dry_mass=0.4, prop_mass=0.1, offset=0.53, length=0.30), length=50, offset=130)

thrusts_array = np.array([1000, 1500, 2000])  # example thrust values
times_array = np.array([0, 1, 2])  # example time values

sol_ignis.set_curve(thrusts_array, times_array) # call function .set_curve so it'll run the above functions
print(sol_ignis.cg_at(times_array)) 
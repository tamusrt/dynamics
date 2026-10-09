# Hybrid rocket engine mass-distribution / CG model.

# produces:

#    mdot_lbm_s     N2O mass flow rate out of the tank   
#    mdot_lbm_s     fuel mass flow rate (FuelGrain)      

# by finding:
#    total N2O mass = initial mass - integral(mdot) dt
#    liquid/vapor = solved from the constant tank volume: V = m_l*v_l + m_v*v_v
#    liquid CG = from the liquid column height inside the tank

# ASSUMPTIONS:
# - The ullage vapor is always saturated at the measured tank pressure.
# - Tank and grain are constant-cross-section cylinders.
# - Liquid and vapor are stratified. `liquid_end` says the liquid
#   pools at the end towards aft. *****The default is "fwd" as a left over 
# - The grain regresses radially only (axial CG fixed at its midpoint).
# - Flow and pressure arrays share a time base.

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Union
from pathlib import Path
import numpy as np
import pandas as pd
from CoolProp.CoolProp import PropsSI


# conversions (im not dealing with pints rn)
IN_TO_M = 0.0254
IN3_TO_M3 = IN_TO_M ** 3
LBM_TO_KG = 0.45359237
PSI_TO_PA = 6894.757293168

# Pressure window handed to CoolProp's saturation calls (cutting out pre-test zero readings and spikes).
P_MIN_PA = 1.5e5
P_MAX_PA = 7.2e6          # just below the N2O critical pressure (7.245 MPa)

TimeSeries = Union[None, float, np.ndarray, Callable[[np.ndarray], np.ndarray]]

_trapz = np.trapezoid if hasattr(np, "trapezoid") else np.trapz

DRY_TO_PROP_WARN = 40.0     # hardware mass / propellant mass above this is unusual
DRY_TO_PROP_HARD = 85.0    # ...above this it basically has to be a typo


class EngineConfigError(ValueError):
    """Physically impossible / wrong engine inputs."""


def _flag(msg: str, hard: bool, strict: bool) -> None:
    """Raise for impossible inputs when `strict`, otherwise just warn."""
    if hard and strict:
        raise EngineConfigError(msg + "  (pass strict=False to downgrade this to a warning)")
    warnings.warn(msg)


def cumulative(y: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Cumulative trapezoidal integral of y dx, same length as y, starting at 0."""
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    out = np.zeros_like(y)
    out[1:] = np.cumsum(0.5 * (y[:-1] + y[1:]) * np.diff(x))
    return out


def _resolve(spec: TimeSeries, times: np.ndarray, t_arr: np.ndarray) -> Optional[np.ndarray]:
    """Evaluate None / scalar / array-on-`times` / callable at the times `t_arr`."""
    if spec is None:
        return None
    if callable(spec):
        return np.broadcast_to(np.asarray(spec(t_arr), dtype=float), t_arr.shape).copy()
    arr = np.asarray(spec, dtype=float)
    if arr.ndim == 0:
        return np.full(t_arr.shape, float(arr))
    if len(arr) != len(times):
        raise ValueError(f"array has {len(arr)} samples but times_s has {len(times)}")
    return np.interp(t_arr, times, arr)


def _is_scalar(t) -> bool:
    return np.isscalar(t) or np.asarray(t).ndim == 0

# NEED TO FIX IN THE LOAD_DATA FILE: SUPPOSED TO CORRECT THE CHANGE BETWEEN PRESSURE VALUES AND 
def sensortime_mdot(t_sensor_s, t_model_s, mdot_model, ignition_idx: float = 0.0) -> np.ndarray:
    """
    correct the time steps and starts to match whats on the pressure

        t_sensor_s    your logged time array (s), e.g. from the pressure CSV
        t_model_s     the model's time array (starts at 0 at ignition)
        mdot_model    the model's mdot array (lbm/s)
        ignition_idx  when the burn starts on the SENSOR clock

    Returns an array the same length as `t_sensor_s`: the model curve
    shifted to start at ignition_idx, and 0 before ignition and after the
    model's burn ends.
    """
    t_rel = np.asarray(t_sensor_s, dtype=float) - ignition_idx
    return np.interp(t_rel, np.asarray(t_model_s, dtype=float),
                     np.asarray(mdot_model, dtype=float), left=0.0, right=0.0)


# build the flow arrays: burn window and fuel mdot
def burn_window_from_thrust(t_s, thrust, frac: float = 0.05) -> tuple[float, float]:
    """
    (t_ignition, t_burnout) from a thrust trace: first and last time it
    exceeds `frac` * peak thrust. Use it to gate the mdot arrays so they are
    zero before ignition and after burnout (a constant mdot over the whole
    log drains the tank for the entire recording).
    """
    thrust = np.asarray(thrust, dtype=float)
    idx = np.flatnonzero(thrust > frac * np.nanmax(thrust))
    if idx.size == 0:
        raise ValueError("thrust never exceeds the threshold")
    t_s = np.asarray(t_s, dtype=float)
    return float(t_s[idx[0]]), float(t_s[idx[-1]])


def fuel_mdot_from_regression(
    t_s, ox_mdot_lbm_s, a: float, n: float,
    port_radius0_in: float, length_in: float,
    fuel_density_lbm_in3: float, outer_radius_in: float,
) -> np.ndarray:
    """
    Fuel mdot(t) found through hybrid regression law, drifting o/f during burn:

        G_ox   = mdot_ox / (pi * r_port^2)          [lbm/(in^2 s)]
        r_dot  = a * G_ox^n                          [in/s]
        mdot_f = rho_f * pi * L * d(r_port^2)/dt     [lbm/s]

    port radius is integrated step by step, so mdot_f follows the
    growing port. `a` has units of (in/s)/(lbm/(in^2 s))^n
    """
    t = np.asarray(t_s, dtype=float)
    mo = np.asarray(ox_mdot_lbm_s, dtype=float)
    out = np.zeros_like(t)
    r = port_radius0_in
    for k in range(len(t) - 1):
        mo_k = 0.5 * (mo[k] + mo[k + 1])
        if mo_k <= 0.0 or r >= outer_radius_in:
            continue
        dt = t[k + 1] - t[k]
        r_dot = a * (mo_k / (math.pi * r ** 2)) ** n
        r_new = min(r + r_dot * dt, outer_radius_in)
        out[k] = fuel_density_lbm_in3 * math.pi * length_in * (r_new ** 2 - r ** 2) / dt
        r = r_new
    out[-1] = out[-2] if len(out) > 1 else 0.0
    return out


def fit_regression_a(t_s, ox_mdot_lbm_s, n: float, fuel_burned_lbm: float, **grain) -> float:
    """
    Find the coefficient `a` (for a given exponent `n`) that makes the
    regression model burn exactly `fuel_burned_lbm` over this ox flow --
    e.g. the grain mass lost between pre- and post-burn weigh-ins.
    `grain` = port_radius0_in, length_in, fuel_density_lbm_in3, outer_radius_in.
    """
    def burned(a):
        return float(_trapz(fuel_mdot_from_regression(t_s, ox_mdot_lbm_s, a, n, **grain), t_s))
    lo, hi = 1e-8, 1e2
    if burned(hi) < fuel_burned_lbm:
        raise ValueError("even a very large `a` cannot burn that much fuel -- check the grain geometry")
    for _ in range(80):                       # geometric bisection; burned(a) increases with a
        mid = math.sqrt(lo * hi)
        lo, hi = (mid, hi) if burned(mid) < fuel_burned_lbm else (lo, mid)
    return math.sqrt(lo * hi)


def fuel_mdot_from_thrust(
    t_s, thrust_lbf, ox_mdot_lbm_s,
    isp_s: Optional[float] = None, total_prop_burned_lbm: Optional[float] = None,
) -> np.ndarray:
    """
    Fuel mdot from the thrust trace: mdot_total = F / Isp, so
    mdot_fuel = mdot_total - mdot_ox. Give either `isp_s`, or the total
    propellant burned (Isp is then total impulse / that mass). This is a
    difference of two measured quantities, so it is noisy where mdot_ox is
    small; smooth the thrust first.
    """
    t = np.asarray(t_s, dtype=float)
    F = np.asarray(thrust_lbf, dtype=float)
    if isp_s is None:
        if total_prop_burned_lbm is None:
            raise ValueError("give isp_s or total_prop_burned_lbm")
        isp_s = float(_trapz(F, t)) / total_prop_burned_lbm
    return np.maximum(F / isp_s - np.asarray(ox_mdot_lbm_s, dtype=float), 0.0)


def _burn_gate(t_s, ignition_idx=None, burnout_idx=None) -> np.ndarray:
    """1.0 inside [t_ignition, t_burnout], 0.0 outside."""
    t = np.asarray(t_s, dtype=float)
    t0 = t[0] if ignition_idx is None else ignition_idx
    t1 = t[-1] if burnout_idx is None else burnout_idx
    return ((t >= t0) & (t <= t1)).astype(float)


def ox_mdot_array(t_s, method: str = "constant", ox_mdot_lbm_s: Optional[float] = None,
                  ignition_idx: Optional[float] = None, burnout_idx: Optional[float] = None,
                  measured: Optional[np.ndarray] = None, *,
                  p_tank_psi=None, p_chamber_psi=None, injector: Optional["DyerInjector"] = None,
                  liquid_temp_K=None, strict: bool = True) -> np.ndarray:
    """
    N2O mdot(t) on `t_s` (lbm/s), zero outside the burn window.

        "dyer"      Dyer two-phase injector model driven by measured tank and
                    chamber pressure -> mdot varies with time. Needs `injector`,
                    `p_tank_psi`, `p_chamber_psi` (psia). `ox_mdot_lbm_s`
                    (constant) or `measured` (array) is the BACKUP, used on any
                    burn sample where Dyer can't be evaluated.
        "constant"  ox_mdot_lbm_s while burning.
        "measured"  your own array (flow meter / tank-mass derivative).
    """
    t = np.asarray(t_s, dtype=float)
    gate = _burn_gate(t, ignition_idx, burnout_idx)

    if method == "dyer":
        if measured is not None:
            if len(measured) != len(t):
                raise ValueError("backup `measured` must be the same length as times_s")
            backup = np.asarray(measured, dtype=float)
        elif ox_mdot_lbm_s is not None:
            backup = np.full_like(t, float(ox_mdot_lbm_s))
        else:
            backup = np.zeros_like(t)
        missing = [n for n, v in (("injector (cd + area)", injector), ("p_tank_psi", p_tank_psi),
                                  ("p_chamber_psi", p_chamber_psi)) if v is None]
        if missing:
            _flag("ox_method='dyer' needs " + ", ".join(missing) + "; without them every sample "
                  "would use the constant backup mdot (a flat flow).", True, strict)
            return backup * gate
        d = dyer_mdot_array(t, p_tank_psi, p_chamber_psi, injector, liquid_temp_K, mask=gate > 0)
        burning = gate > 0
        use = np.isfinite(d) & burning
        n_burn, n_bak = int(burning.sum()), int((burning & ~use).sum())
        if n_bak:
            warnings.warn(
                f"Dyer could not be evaluated on {n_bak}/{n_burn} burn samples "
                f"({100 * n_bak / n_burn:.0f}%; P_tank <= P_chamber or bad pressure data) -- "
                + ("using the backup mdot there." if backup.any() else
                   "NO backup mdot was given, so flow is 0 there."))
        return np.where(use, d, backup) * gate

    if method == "constant":
        if ox_mdot_lbm_s is None:
            raise ValueError("ox_method='constant' needs flow['ox_mdot'] (lbm/s)")
        return float(ox_mdot_lbm_s) * gate
    if method == "measured":
        if measured is None or len(measured) != len(t):
            raise ValueError("ox_method='measured' needs an array the same length as times_s")
        return np.asarray(measured, dtype=float) * gate
    if method == "blowdown":
        raise NotImplementedError(
            "ox_method='blowdown' (predicting tank pressure from thermodynamics) is not "
            "implemented. Use 'dyer' with your measured tank + chamber pressure.")
    if method == "column":
        raise NotImplementedError("ox_method='column' is not implemented; use 'dyer'.")
    raise ValueError(f"unknown ox_method {method!r}")


def fuel_mdot_array(t_s, ox_mdot_lbm_s, grain_cfg: Mapping, flow_cfg: Mapping,
                    ignition_idx: Optional[float] = None, burnout_idx: Optional[float] = None,
                    thrust_lbf: Optional[np.ndarray] = None) -> np.ndarray:
    """Fuel mdot(t) from flow_cfg['fuel_method']: "regression" | "thrust" | "constant"."""
    t = np.asarray(t_s, dtype=float)
    mo = np.asarray(ox_mdot_lbm_s, dtype=float)
    gate = _burn_gate(t, ignition_idx, burnout_idx)
    method = flow_cfg.get("fuel_method", "regression")

    if method == "constant":
        return float(flow_cfg["fuel_mdot"]) * gate

    if method == "thrust":
        if thrust_lbf is None:
            raise ValueError("fuel_method='thrust' needs thrust_lbf")
        return fuel_mdot_from_thrust(
            t, thrust_lbf, mo, isp_s=flow_cfg.get("isp_s"),
            total_prop_burned_lbm=flow_cfg.get("total_prop_burned_lbm")) * gate

    if method == "regression":
        g = dict(port_radius0_in=grain_cfg["initial_port_radius_in"], length_in=grain_cfg["length_in"],
                 fuel_density_lbm_in3=grain_cfg["fuel_density_lbm_in3"], outer_radius_in=grain_cfg["outer_radius_in"])
        n = float(flow_cfg.get("n", 0.5))
        idx = np.flatnonzero(gate * mo > 0)          # only integrate over the burn
        out = np.zeros_like(t)
        if idx.size < 2:
            return out
        sl = slice(idx[0], idx[-1] + 1)
        a = flow_cfg.get("a")
        if a is None:
            if flow_cfg.get("fuel_burned_lbm") is None:
                raise ValueError("regression needs flow['a'] or flow['fuel_burned_lbm'] to fit it")
            a = fit_regression_a(t[sl], mo[sl], n, float(flow_cfg["fuel_burned_lbm"]), **g)
        out[sl] = fuel_mdot_from_regression(t[sl], mo[sl], float(a), n, **g)
        return out

    raise ValueError(f"unknown fuel_method {method!r}")


# N2O saturation properties for within the tank
class N2OSaturation:
    """
    N2O saturation-dome lookups (the "steam table" for nitrous). The tank
    uses `vg(P)` for the ullage, `subcooled_liquid_v(T, P)` for the liquid
    and `t_sat(P)` for the equilibrium liquid temperature.
    """

    _P_LO = None
    _P_HI = None

    @classmethod
    def _clamp_p(cls, p) -> float:
        """Keep pressure inside the N2O liquid-vapor dome (triple point .. critical point)."""
        if cls._P_LO is None:
            cls._P_LO = PropsSI("ptriple", "N2O") * 1.001
            cls._P_HI = PropsSI("pcrit", "N2O") * 0.999
        p = float(p)
        if not np.isfinite(p):
            return float("nan")
        return min(max(p, cls._P_LO), cls._P_HI)

    @classmethod
    def vf_vg(cls, pressure_pa):
        p = cls._clamp_p(pressure_pa)
        return (1.0 / PropsSI("D", "P", p, "Q", 0, "N2O"),
                1.0 / PropsSI("D", "P", p, "Q", 1, "N2O"))

    @classmethod
    def vg(cls, pressure_pa):
        p = cls._clamp_p(pressure_pa)
        return 1.0 / PropsSI("D", "P", p, "Q", 1, "N2O")
    
    @classmethod
    def p_sat(cls, temperature_k: float) -> float:
        """Saturation pressure (Pa) at temperature (K)."""
        return PropsSI("P", "T", temperature_k, "Q", 0, "N2O")
    
    @classmethod
    def t_sat(cls, pressure_pa):
        p = cls._clamp_p(pressure_pa)
        return PropsSI("T", "P", p, "Q", 0, "N2O")

    @classmethod
    def subcooled_liquid_v(cls, temperature_k: float, pressure_pa: float) -> float:
        """
        Specific volume (m^3/kg) of liquid N2O at (T, P). At or below the
        saturation pressure for that T the liquid would be boiling, so we
        clamp to the saturated-liquid state at T (the correct limit as
        P -> Psat).
        """
        p_sat = cls.p_sat(temperature_k)
        p_eff = max(pressure_pa, p_sat)
        if p_eff <= p_sat * (1 + 1e-4):
            return 1.0 / PropsSI("D", "T", temperature_k, "Q", 0, "N2O")
        try:
            return 1.0 / PropsSI("D", "T", temperature_k, "P", p_eff, "N2O")
        except ValueError:
            return 1.0 / PropsSI("D", "T", temperature_k, "Q", 0, "N2O")


    @classmethod
    def liquid_h_s(cls, temperature_k: float, pressure_pa: float) -> tuple[float, float]:
        """(h [J/kg], s [J/kg/K]) of liquid N2O at (T, P); saturated liquid if P <= Psat(T)."""
        p_sat = cls.p_sat(temperature_k)
        if pressure_pa > p_sat * (1 + 1e-4):
            try:
                return (PropsSI("H", "T", temperature_k, "P", pressure_pa, "N2O"),
                        PropsSI("S", "T", temperature_k, "P", pressure_pa, "N2O"))
            except ValueError:
                pass
        return (PropsSI("H", "T", temperature_k, "Q", 0, "N2O"),
                PropsSI("S", "T", temperature_k, "Q", 0, "N2O"))

    @classmethod
    def isentropic_state(cls, entropy: float, pressure_pa: float) -> tuple[float, float]:
        """(h [J/kg], rho [kg/m^3]) after isentropic expansion to `pressure_pa` (two-phase OK)."""
        try:
            return (PropsSI("H", "P", pressure_pa, "S", entropy, "N2O"),
                    PropsSI("D", "P", pressure_pa, "S", entropy, "N2O"))
        except ValueError:
            pass
        # CoolProp's (P, S) inversion often fails inside the dome, so build the
        # two-phase state from the saturated liquid (f) and vapor (g) at P2.
        p = float(min(max(pressure_pa, P_MIN_PA), P_MAX_PA))
        sf, sg = PropsSI("S", "P", p, "Q", 0, "N2O"), PropsSI("S", "P", p, "Q", 1, "N2O")
        hf, hg = PropsSI("H", "P", p, "Q", 0, "N2O"), PropsSI("H", "P", p, "Q", 1, "N2O")
        vf, vg = 1.0 / PropsSI("D", "P", p, "Q", 0, "N2O"), 1.0 / PropsSI("D", "P", p, "Q", 1, "N2O")
        x = float(np.clip((entropy - sf) / (sg - sf), 0.0, 1.0))   # quality from s2 = s1
        return hf + x * (hg - hf), 1.0 / (vf + x * (vg - vf))

# Dyer injector model (two-phase N2O through an orifice plate)
@dataclass
class DyerInjector:
    """Total injector discharge: cd * area_in2 (all holes combined)."""
    cd: float
    area_in2: float

    def __post_init__(self):
        if not (0.05 <= self.cd <= 1.0):
            raise ValueError(f"injector Cd={self.cd} is outside the plausible 0.05-1.0 range")
        if self.area_in2 <= 0:
            raise ValueError("injector area_in2 must be > 0")

    @classmethod
    def from_holes(cls, n_holes: int, hole_dia_in: float, cd: float) -> "DyerInjector":
        return cls(cd=cd, area_in2=n_holes * math.pi * (hole_dia_in / 2.0) ** 2)

    @classmethod
    def from_cfg(cls, d: Mapping) -> "DyerInjector":
        """{"cd":.., "area_in2":..}  or  {"cd":.., "n_holes":.., "hole_dia_in":..}"""
        if "area_in2" in d:
            return cls(cd=float(d["cd"]), area_in2=float(d["area_in2"]))
        return cls.from_holes(int(d["n_holes"]), float(d["hole_dia_in"]), float(d["cd"]))

    @property
    def cda_m2(self) -> float:
        return self.cd * self.area_in2 * IN_TO_M ** 2

def dyer_mdot_point(p1_pa: float, p2_pa: float, t1_k: Optional[float], cda_m2: float) -> dict:
    """
    Dyer et al. (2007) mass flow, SI units (kg/s).

        m = k/(1+k) * m_SPI + 1/(1+k) * m_HEM ,   k = sqrt((P1-P2)/(Pv-P2))

        m_SPI = CdA sqrt(2 rho1 (P1-P2))                (incompressible, no flashing)
        m_HEM = CdA rho2 sqrt(2 (h1-h2))                (equilibrium flash, s2 = s1 at P2)

    Upstream state is liquid at min(t1_k, Tsat(P1)) -- a tank cannot sit below
    the saturation pressure of its own liquid. Pv = Psat(T1). If Pv <= P2 the
    flow cannot flash and the result is pure SPI.
    """
    #p1 = float(np.clip(p1_pa, P_MIN_PA, P_MAX_PA))
    #p2 = float(max(p2_pa, P_MIN_PA))
    p1 = float(p1_pa)
    p2 = float(p2_pa)
    t_eq = N2OSaturation.t_sat(p1)
    T1 = t_eq if t1_k is None or not np.isfinite(t1_k) else float(t1_k)   # was: min(float(t1_k), t_eq)

    pv = N2OSaturation.p_sat(T1)
    dp = p1 - p2
    rho1 = 1.0 / N2OSaturation.subcooled_liquid_v(T1, p1)
    m_spi = cda_m2 * math.sqrt(2.0 * rho1 * dp)
    if pv <= p2:
        return {"m": m_spi, "spi": m_spi, "hem": float("nan"), "kappa": float("inf")}
    h1, s1 = N2OSaturation.liquid_h_s(T1, p1)
    h2, rho2 = N2OSaturation.isentropic_state(s1, p2)
    m_hem = cda_m2 * rho2 * math.sqrt(max(2.0 * (h1 - h2), 0.0))
    k = math.sqrt(dp / (pv - p2))
    return {"m": (k * m_spi + m_hem) / (1.0 + k), "spi": m_spi, "hem": m_hem, "kappa": k}

def dyer_mdot_array(t_s, p_tank_psi, p_chamber_psi, injector: DyerInjector,
                    liquid_temp_K=None, mask=None, max_points: int = 1500,
                    return_parts: bool = False):
    """
    Time-varying Dyer N2O mdot (lbm/s) from measured tank + chamber pressure (psia).

    NaN where the model cannot be evaluated (non-finite pressure, P_tank <= P_chamber,
    or outside `mask`) so the caller can substitute a backup. Evaluated on at most
    `max_points` samples inside the burn and interpolated, because every point costs
    several property calls. With return_parts=True returns a dict of arrays
    {"mdot", "spi", "hem", "kappa"} for plotting/debugging.
    """
    t = np.asarray(t_s, dtype=float)
    p1 = np.asarray(p_tank_psi, dtype=float) * PSI_TO_PA
    p2 = np.asarray(p_chamber_psi, dtype=float) * PSI_TO_PA
    if not (len(p1) == len(p2) == len(t)):
        raise ValueError("t_s, p_tank_psi and p_chamber_psi must be the same length")
    T1 = None if liquid_temp_K is None else np.broadcast_to(np.asarray(liquid_temp_K, float), t.shape)

    ok = np.isfinite(p1) & np.isfinite(p2) & (p2 > 0) & (p1 - p2 > 1e-3 * np.abs(p1))
    if mask is not None:
        ok &= np.asarray(mask, dtype=bool)
    idx = np.flatnonzero(ok)
    out = {k: np.full_like(t, np.nan) for k in ("mdot", "spi", "hem", "kappa")}
    if idx.size:
        sel = idx if idx.size <= max_points else idx[np.unique(np.linspace(0, idx.size - 1, max_points).astype(int))]
        pts = [dyer_mdot_point(p1[i], p2[i], None if T1 is None else T1[i], injector.cda_m2) for i in sel]
        for key, src in (("mdot", "m"), ("spi", "spi"), ("hem", "hem"), ("kappa", "kappa")):
            v = np.array([pt[src] for pt in pts])
            if v.size == 0:
                continue
            finite = np.isfinite(v)
            out[key][idx] = np.interp(t[idx], t[sel][finite], v[finite]) if finite.any() else np.nan
    for key in ("mdot", "spi", "hem"):
        out[key] = out[key] / LBM_TO_KG                       # kg/s -> lbm/s (kappa is dimensionless)
    return out if return_parts else out["mdot"]
# end of dyer 


# hardware components, not including the propellant 
@dataclass
class EngineComponent:
    name: str
    dry_mass: float            # lbm, hardware only
    offset: float              # in, distance from engine reference to forward face
    length: float              # in
    radius: Optional[float] = None   # in, used for volume/CG defaults

    def cg_offset(self) -> float:
        """CG of the dry hardware, assuming uniform density along its length."""
        return self.offset + self.length / 2

    def volume(self) -> float:
        """Internal volume assuming a simple cylinder, in^3."""
        if self.radius is None:
            raise ValueError(f"{self.name}: radius not set, cannot compute volume")
        return math.pi * self.radius ** 2 * self.length


# putting together the oxidizer tank 
@dataclass
class OxidizerTank:
    """
    Constant-volume N2O tank tracked as liquid + vapor, driven by
    time-dependent arrays.

    Required arrays (same length): times_s, pressure_psi, mdot_lbm_s.

    Optional:
      liquid_temp_F           number | array | callable | None (equilibrium)
      mass_history_lbm        exact total-mass array; used instead of
                              integrating mdot (avoids integration drift)
      liquid_mass_history_lbm liquid mass used directly, skipping the
                              volume balance (see `use_blowdown_split`)
      final_mass_lbm          rescale the integrated mdot so the mass lands
                              on this end value (post-burn weigh-in anchor)
      liquid_end              "fwd" or "aft": end of the tank the liquid
                              pools at
    """

    casing: EngineComponent
    volume_in3: float                      # fixed internal volume
    initial_ox_mass_lbm: float             # total N2O loaded (liquid + vapor)
    liquid_temp_F: TimeSeries
    times_s: np.ndarray
    pressure_psi: np.ndarray
    mdot_lbm_s: np.ndarray
    cross_section_area_in2: Optional[float] = None   # defaults from casing.radius
    mass_history_lbm: Optional[np.ndarray] = None
    liquid_mass_history_lbm: Optional[np.ndarray] = None
    final_mass_lbm: Optional[float] = None
    liquid_end: str = "fwd"
    strict: bool = True        # raise on impossible inputs instead of warning

    def __post_init__(self):
        self.times_s = np.asarray(self.times_s, dtype=float)
        self.pressure_psi = np.asarray(self.pressure_psi, dtype=float)
        self.mdot_lbm_s = np.asarray(self.mdot_lbm_s, dtype=float)
        n = len(self.times_s)
        assert len(self.pressure_psi) == len(self.mdot_lbm_s) == n, (
            "times_s, pressure_psi and mdot_lbm_s must all be the same length"
        )
        if self.liquid_end not in ("fwd", "aft"):
            raise ValueError("liquid_end must be 'fwd' or 'aft'")

        if self.cross_section_area_in2 is None:
            if self.casing.radius is None:
                raise ValueError("Need either cross_section_area_in2 or casing.radius")
            self.cross_section_area_in2 = math.pi * self.casing.radius ** 2

        cum = cumulative(self.mdot_lbm_s, self.times_s)
        self.mdot_scale = 1.0
        if self.final_mass_lbm is not None and cum[-1] > 0:
            self.mdot_scale = (self.initial_ox_mass_lbm - self.final_mass_lbm) / cum[-1]
            cum = cum * self.mdot_scale
            if not 0.85 <= self.mdot_scale <= 1.15:
                warnings.warn(
                    f"{self.casing.name}: the post-burn weigh-in rescaled the ox mdot by "
                    f"{self.mdot_scale:.2f}x. If mdot came from Dyer, the injector Cd/area (or the "
                    f"pressure data) is probably off by about that factor.")
        self._cum_mass_lost = cum
        if self.mass_history_lbm is None and cum[-1] > self.initial_ox_mass_lbm * 1.001:
            _flag(
                f"{self.casing.name}: the mdot array removes {cum[-1]:.1f} lbm of N2O but only "
                f"{self.initial_ox_mass_lbm:.1f} lbm was loaded -- check mdot units/time base "
                f"(mass would be clipped at 0 and the CG frozen).", True, self.strict)

        for name in ("mass_history_lbm", "liquid_mass_history_lbm"):
            val = getattr(self, name)
            if val is not None:
                val = np.asarray(val, dtype=float)
                if len(val) != n:
                    raise ValueError(f"{name} must have the same length as times_s")
                setattr(self, name, val)

        self._input_check()

    # ---- time-series helpers --------------------------------------------
    def pressure_at(self, t):
        return np.interp(t, self.times_s, self.pressure_psi)

    def mdot_at(self, t):
        """N2O mass flow rate out of the tank at time t, lbm/s."""
        return np.interp(t, self.times_s, self.mdot_lbm_s) * self.mdot_scale

    def mass_at(self, t):
        """Total remaining N2O mass (liquid + vapor), lbm."""
        if self.mass_history_lbm is not None:
            return np.maximum(np.interp(t, self.times_s, self.mass_history_lbm), 0.0)
        cum_lost = np.interp(t, self.times_s, self._cum_mass_lost)
        return np.maximum(self.initial_ox_mass_lbm - cum_lost, 0.0)

    # ---- thermodynamic state --------------------------------------------
    def _liquid_temp_K_at(self, t_arr, p_pa):
        spec = _resolve(self.liquid_temp_F, self.times_s, t_arr)
        if spec is None:                       # equilibrium
            return np.array([N2OSaturation.t_sat(p) for p in p_pa])
        return (spec - 32.0) * 5.0 / 9.0 + 273.15

    def _state_at(self, t_arr):
        """Mass split, specific volumes and liquid temperature at each time."""
        m_tot_lbm = np.atleast_1d(self.mass_at(t_arr)).astype(float)
        p_pa = np.atleast_1d(self.pressure_at(t_arr)) * PSI_TO_PA
        T_K = self._liquid_temp_K_at(t_arr, p_pa)
        v_l = np.array([N2OSaturation.subcooled_liquid_v(T, p) for T, p in zip(T_K, p_pa)])
        v_v = np.array([N2OSaturation.vg(p) for p in p_pa])

        if self.liquid_mass_history_lbm is not None:
            m_l_lbm = np.interp(t_arr, self.times_s, self.liquid_mass_history_lbm)
            m_l_lbm = np.clip(m_l_lbm, 0.0, m_tot_lbm)
        else:
            m_tot_kg = m_tot_lbm * LBM_TO_KG
            m_l_kg = (self.volume_in3 * IN3_TO_M3 - m_tot_kg * v_v) / (v_l - v_v)
            m_l_lbm = np.clip(m_l_kg, 0.0, m_tot_kg) / LBM_TO_KG
        return {"m_tot": m_tot_lbm, "m_l": m_l_lbm, "m_v": m_tot_lbm - m_l_lbm,
                "v_l": v_l, "v_v": v_v, "T_K": T_K}

    def phase_split_at(self, t):
        """
        (liquid_mass_lbm, vapor_mass_lbm, vapor_fraction) at time t.

        Volume balance (constant V):
            V = m_l*v_l + m_v*v_v ,   m_total = m_l + m_v
            => m_l = (V - m_total*v_v) / (v_l - v_v)
        with v_v = v_g(P(t)) and v_l = v_liquid(T_liq, P(t)).
        """
        t_arr = np.atleast_1d(np.asarray(t, dtype=float))
        s = self._state_at(t_arr)
        vf = np.divide(s["m_v"], s["m_tot"], out=np.zeros_like(s["m_tot"]), where=s["m_tot"] > 0)
        if _is_scalar(t):
            return float(s["m_l"][0]), float(s["m_v"][0]), float(vf[0])
        return s["m_l"], s["m_v"], vf

    def liquid_temp_K_at(self, t):
        """Liquid temperature actually used at time t (K)."""
        t_arr = np.atleast_1d(np.asarray(t, dtype=float))
        out = self._state_at(t_arr)["T_K"]
        return float(out[0]) if _is_scalar(t) else out

    # ---- geometry -------------------------------------------------------
    def _column_length_in(self) -> float:
        return self.volume_in3 / self.cross_section_area_in2

    def heights_at(self, t):
        """(liquid_height_in, ullage_height_in); they sum to V/A."""
        t_arr = np.atleast_1d(np.asarray(t, dtype=float))
        s = self._state_at(t_arr)
        L = self._column_length_in()
        h_l = np.minimum(s["m_l"] * LBM_TO_KG * s["v_l"] / IN3_TO_M3 / self.cross_section_area_in2, L)
        h_v = L - h_l
        if _is_scalar(t):
            return float(h_l[0]), float(h_v[0])
        return h_l, h_v

    def cg_at(self, t):
        """
        CG of the tank CONTENTS (liquid + vapor), same frame as
        casing.offset. The liquid pools at `liquid_end`; the vapor
        ullage fills the rest of the tank.
        """
        t_arr = np.atleast_1d(np.asarray(t, dtype=float))
        s = self._state_at(t_arr)
        lo = self.casing.offset
        L = self._column_length_in()
        hi = lo + L
        h_l = np.minimum(s["m_l"] * LBM_TO_KG * s["v_l"] / IN3_TO_M3 / self.cross_section_area_in2, L)

        if self.liquid_end == "aft":
            liq_c = hi - h_l / 2
            vap_c = lo + (L - h_l) / 2
        else:
            liq_c = lo + h_l / 2
            vap_c = lo + h_l + (L - h_l) / 2

        cg = np.divide(
            s["m_l"] * liq_c + s["m_v"] * vap_c, s["m_tot"],
            out=np.full_like(s["m_tot"], 0.5 * (lo + hi)), where=s["m_tot"] > 0,
        )
        return float(cg[0]) if _is_scalar(t) else cg

    def total_mass_at(self, t):
        """Tank contents + dry casing mass."""
        return self.mass_at(t) + self.casing.dry_mass

    # ---- sanity checks --------------------------------------------------
    def _input_check(self):
        """Impossible tank inputs. Raises when `strict`, else warns. Works with or without CoolProp."""
        L = self._column_length_in()
        if L > self.casing.length * (1 + 1e-3):
            _flag(
                f"{self.casing.name}: volume_in3 / area = {L:.1f} in but the casing is only "
                f"{self.casing.length:.1f} in long -- check volume_in3, radius and length. "
                f"(The ox column would overlap the plumbing/grain stations.)", True, self.strict)
        t0 = self.times_s[:1]
        p0 = float(self.pressure_at(t0[0]) * PSI_TO_PA)
        T_K = self._liquid_temp_K_at(t0, np.array([p0]))[0]
        v_l = N2OSaturation.subcooled_liquid_v(T_K, p0)
        need_in3 = self.initial_ox_mass_lbm * LBM_TO_KG * v_l / IN3_TO_M3
        if need_in3 > self.volume_in3 * (1 + 1e-3):
            max_lbm = self.volume_in3 * IN3_TO_M3 / v_l / LBM_TO_KG
            _flag(
                f"{self.casing.name}: {self.initial_ox_mass_lbm:.2f} lbm of liquid N2O needs "
                f"~{need_in3:.0f} in^3 but the tank volume is {self.volume_in3:.0f} in^3 "
                f"(overfilled; it holds at most ~{max_lbm:.1f} lbm of liquid at this temperature).",
                True, self.strict)


# fuel grain, including the radial regression array
@dataclass
class FuelGrain:
    """
    Cylindrical hybrid fuel grain, port burns outward radially. Mass comes
    from integrating the fuel mdot array; port radius is back-solved from
    that mass plus grain geometry/density.
    """

    casing: EngineComponent           # dry hardware: grain liner/casing
    outer_radius_in: float
    initial_port_radius_in: float
    length_in: float
    fuel_density_lbm_in3: float
    times_s: np.ndarray
    mdot_lbm_s: np.ndarray            # fuel mass flow rate, time-dependent
    strict: bool = True

    def __post_init__(self):
        self.times_s = np.asarray(self.times_s, dtype=float)
        self.mdot_lbm_s = np.asarray(self.mdot_lbm_s, dtype=float)
        assert len(self.times_s) == len(self.mdot_lbm_s)
        self._cum_mass_lost =cumulative(self.mdot_lbm_s, self.times_s)
        self.initial_fuel_mass_lbm = (
            self.fuel_density_lbm_in3 * math.pi * self.length_in
            * (self.outer_radius_in ** 2 - self.initial_port_radius_in ** 2)
        )
        if self._cum_mass_lost[-1] > self.initial_fuel_mass_lbm * 1.001:
            _flag(f"{self.casing.name}: the fuel mdot array removes {self._cum_mass_lost[-1]:.2f} lbm "
                  f"but the grain only holds {self.initial_fuel_mass_lbm:.2f} lbm -- check the fuel "
                  f"mdot/time base or the grain geometry.", True, self.strict)
        if self.casing.radius is not None and self.outer_radius_in > self.casing.radius * (1 + 1e-6):
            warnings.warn(f"{self.casing.name}: outer_radius_in={self.outer_radius_in} exceeds "
                          f"casing radius {self.casing.radius}.")

    def mdot_at(self, t):
        """Fuel mass flow rate at time t, lbm/s."""
        return np.interp(t, self.times_s, self.mdot_lbm_s)

    def mass_at(self, t):
        cum_lost = np.interp(t, self.times_s, self._cum_mass_lost)
        return np.maximum(self.initial_fuel_mass_lbm - cum_lost, 0.0)

    def port_radius_at(self, t):
        """Regressed port radius, back-solved from remaining fuel mass."""
        m = self.mass_at(t)
        r2 = self.outer_radius_in ** 2 - m / (self.fuel_density_lbm_in3 * math.pi * self.length_in)
        return np.sqrt(np.maximum(r2, 0.0))

    def cg_at(self, t) -> float:
        # Purely radial regression keeps the fuel's axial centroid fixed
        # at the grain midpoint, regardless of how much has burned.
        return self.casing.offset + self.length_in / 2

    def total_mass_at(self, t):
        return self.mass_at(t) + self.casing.dry_mass


# ALL TOGETHER NOW FOLKS!!!
@dataclass
class Engine:
    tank: OxidizerTank
    plumbing: EngineComponent           # injector, valves, lines -- dry, static
    grain: FuelGrain
    length_in: float
    offset_in: float
    thrusts: Optional[np.ndarray] = None
    times_s: Optional[np.ndarray] = None
    strict: bool = True

    def __post_init__(self):
        self._curve_ready = False
        self._validate()
        if self.thrusts is not None and self.times_s is not None:
            self.set_curve(self.thrusts, self.times_s)

    def _validate(self):
        """Cross-component checks, so one bad number can't silently dominate the CG."""
        tank, grain, pl = self.tank, self.grain, self.plumbing
        dry = tank.casing.dry_mass + grain.casing.dry_mass + pl.dry_mass
        prop = tank.initial_ox_mass_lbm + grain.initial_fuel_mass_lbm
        if prop > 0:
            ratio = dry / prop
            msg = (f"engine hardware is {dry:.1f} lbm vs {prop:.1f} lbm of propellant "
                   f"(ratio {ratio:.0f}:1); per-part dry masses: tank {tank.casing.dry_mass:g}, "
                   f"grain {grain.casing.dry_mass:g}, plumbing {pl.dry_mass:g}. The dry mass will "
                   f"swamp the propellant and the CG will barely move -- typo?")
            if ratio > DRY_TO_PROP_HARD:
                _flag(msg, True, self.strict)
            elif ratio > DRY_TO_PROP_WARN:
                warnings.warn(msg)

        spans = sorted((c.offset, c.offset + c.length, nm) for nm, c in
                       (("tank", tank.casing), ("plumbing", pl), ("grain", grain.casing)))
        for (s0, e0, n0), (s1, e1, n1) in zip(spans, spans[1:]):
            if s1 < e0 - 1e-6:
                warnings.warn(f"{n0} ({s0:g}-{e0:g} in) overlaps {n1} ({s1:g}-{e1:g} in).")
        if spans[0][0] < -1e-6 or spans[-1][1] > self.length_in + 1e-6:
            warnings.warn(f"components span {spans[0][0]:g}-{max(e for _, e, _ in spans):g} in "
                          f"but engine length_in is {self.length_in:g}.")

        for nm, a, b in (("start", tank.times_s[0], grain.times_s[0]), ("end", tank.times_s[-1], grain.times_s[-1])):
            if abs(a - b) > 1e-6:
                warnings.warn(f"tank and grain time bases differ at the {nm} ({a:g} vs {b:g} s); "
                              f"np.interp clamps outside the array, so one will freeze.")

        try:
            ts = np.array([tank.times_s[0], tank.times_s[-1]])
            swing = abs(float(np.diff(self.cg_at(ts))[0]))
            if prop > 0 and swing < 1e-3 * self.length_in:
                warnings.warn(f"engine CG moves only {swing:.4f} in over the whole log despite "
                              f"{prop:.1f} lbm of propellant -- check mdot arrays, gating and dry masses.")
        except Exception:                       # never let a diagnostic break construction
            pass

    def set_curve(self, thrusts, times_s):
        self.thrusts = np.asarray(thrusts, dtype=float)
        self.times_s = np.asarray(times_s, dtype=float)
        assert len(self.thrusts) == len(self.times_s)
        cum = cumulative(self.thrusts, self.times_s)   # (was shadowing the function name)
        self.total_impulse = cum[-1]
        self.burn_time = self.times_s[-1]
        self._curve_ready = True

    def thrust_at(self, t):
        if not self._curve_ready:
            raise RuntimeError("Thrust curve not set -- call set_curve(thrusts, times_s) first")
        return np.interp(t, self.times_s, self.thrusts, left=0.0, right=0.0)

    # ---- flow -----------------------------------------------------------
    def mdot_at(self, t):
        """(mdot_ox, mdot_fuel) in lbm/s from the two flow arrays."""
        return self.tank.mdot_at(t), self.grain.mdot_at(t)

    def of_ratio_at(self, t):
        ox, fu = (np.atleast_1d(np.asarray(x, dtype=float)) for x in self.mdot_at(t))
        return np.divide(ox, fu, out=np.full_like(ox, np.nan), where=fu > 0)

    # ---- mass / CG / Iyy ------------------------------------------------
    def mass_at(self, t) -> float:
        return (
            self.tank.total_mass_at(t)
            + self.grain.total_mass_at(t)
            + self.plumbing.dry_mass
        )

    def _component_states(self, t):
        """
        (mass, cg) for tank, grain, plumbing at time t, in the same local
        frame as casing.cg_offset(). Shared by cg_at and iyy_at.
        """
        t_arr = np.atleast_1d(np.asarray(t, dtype=float))

        tank_mass = np.atleast_1d(self.tank.total_mass_at(t_arr))
        ox_mass = np.atleast_1d(self.tank.mass_at(t_arr))
        tank_dry_cg = self.tank.casing.cg_offset()
        tank_cg = np.divide(
            self.tank.casing.dry_mass * tank_dry_cg + ox_mass * self.tank.cg_at(t_arr),
            tank_mass, out=np.full_like(tank_mass, tank_dry_cg), where=tank_mass > 0,
        )

        grain_mass = np.atleast_1d(self.grain.total_mass_at(t_arr))
        fuel_mass = np.atleast_1d(self.grain.mass_at(t_arr))
        grain_dry_cg = self.grain.casing.cg_offset()
        grain_cg = np.divide(
            self.grain.casing.dry_mass * grain_dry_cg + fuel_mass * self.grain.cg_at(t_arr),
            grain_mass, out=np.full_like(grain_mass, grain_dry_cg), where=grain_mass > 0,
        )

        plumbing_mass = np.full_like(t_arr, self.plumbing.dry_mass)
        plumbing_cg = np.full_like(t_arr, self.plumbing.cg_offset())
        return (tank_mass, tank_cg), (grain_mass, grain_cg), (plumbing_mass, plumbing_cg)

    def cg_at(self, t):
        (tm, tc), (gm, gc), (pm, pc) = self._component_states(t)
        total = tm + gm + pm
        cg = self.offset_in + (tm * tc + gm * gc + pm * pc) / total
        return float(cg[0]) if _is_scalar(t) else cg

    @staticmethod
    def _rod_iyy(mass, length):
        """Uniform rod about its own center, lbm*in^2."""
        return mass * length ** 2 / 12.0

    def iyy_at(self, t, rocket_cg_in: float):
        """
        Engine's pitch/yaw Iyy about `rocket_cg_in` (same frame as
        offset_in). Each component is a uniform rod over its casing length,
        shifted by the parallel-axis theorem. It does not resolve the
        separate liquid and vapor columns inside the tank.
        """
        t_arr = np.atleast_1d(np.asarray(t, dtype=float))
        (tm, tc), (gm, gc), (pm, pc) = self._component_states(t_arr)
        iyy = (
            self._rod_iyy(tm, self.tank.casing.length) + tm * (self.offset_in + tc - rocket_cg_in) ** 2
            + self._rod_iyy(gm, self.grain.length_in) + gm * (self.offset_in + gc - rocket_cg_in) ** 2
            + self._rod_iyy(pm, self.plumbing.length) + pm * (self.offset_in + pc - rocket_cg_in) ** 2
        )
        return float(iyy[0]) if _is_scalar(t) else iyy

    def summary(self, t) -> dict:
        """Arrays of the main time-dependent quantities, handy for plots/CSV."""
        t_arr = np.atleast_1d(np.asarray(t, dtype=float))
        liq, vap, _ = self.tank.phase_split_at(t_arr)
        mdot_ox, mdot_f = self.mdot_at(t_arr)
        return {
            "t_s": t_arr,
            "mass_lbm": np.atleast_1d(self.mass_at(t_arr)),
            "cg_in": np.atleast_1d(self.cg_at(t_arr)),
            "mdot_ox_lbm_s": np.atleast_1d(mdot_ox),
            "mdot_fuel_lbm_s": np.atleast_1d(mdot_f),
            "of_ratio": self.of_ratio_at(t_arr),
            "ox_liquid_lbm": np.atleast_1d(liq),
            "ox_vapor_lbm": np.atleast_1d(vap),
            "fuel_lbm": np.atleast_1d(self.grain.mass_at(t_arr)),
            "ox_liquid_temp_K": np.atleast_1d(self.tank.liquid_temp_K_at(t_arr)),
        }


# put together the engine the above functions 
def build_engine(
    cfg: Mapping, times_s, pressure_psi=None,
    ignition_idx: Optional[float] = None, burnout_idx: Optional[float] = None,
    thrust_lbf=None, ox_mdot_measured=None, chamber_pressure_psi=None,
    liquid_end: str = "aft", strict: bool = True,
) -> Engine:
    """
    calling the engine based off the inputs in the dictionary

    if cfg has no "flow" subdictionary to take the values from, cfg["ox_mdot"] / cfg["fuel_mdot"] are
    used as constants
    
    mass/geometry problems raise EngineConfigError (or warn if strict=False)
    """
    t = np.asarray(times_s, dtype=float)
    
    flow = dict(cfg.get("flow") or {})
    if not flow: # if there is no sub dictionary call as a constant under the flow
        flow = {"ox_method": "constant", "ox_mdot": cfg["ox_mdot"],
                "fuel_method": "constant", "fuel_mdot": cfg["fuel_mdot"]}
    else:
        for top, key in (("ox_mdot", "ox_mdot"), ("fuel_mdot", "fuel_mdot")):
            if top in cfg and key in flow and cfg[top] != flow[key]:
                warnings.warn(f"cfg['{top}']={cfg[top]} is ignored; using flow['{key}']={flow[key]}.")

    if (ignition_idx is None or burnout_idx is None) and thrust_lbf is not None: # use the thrust curve value if theres no start or end to set data
        ti, tb = burn_window_from_thrust(t, thrust_lbf)
        ignition_idx = ti if ignition_idx is None else ignition_idx
        burnout_idx = tb if burnout_idx is None else burnout_idx

    tk, gr, pl = cfg["tank"], cfg["grain"], cfg["plumbing"]
    method = flow.get("ox_method", "constant")
    if method == "dyer" and pressure_psi is None:
        raise ValueError("ox_method='dyer' needs the measured tank pressure_psi (psia)")
    injector = DyerInjector.from_cfg(flow["injector"]) if flow.get("injector") else None
    t1_f = _resolve(tk.get("liquid_temp_F"), t, t)            # None -> equilibrium (Tsat(P))
    t1_k = None if t1_f is None else (t1_f - 32.0) * 5.0 / 9.0 + 273.15

    ox = ox_mdot_array(t, method, flow.get("ox_mdot"), ignition_idx, burnout_idx,
                       measured=ox_mdot_measured, p_tank_psi=pressure_psi,
                       p_chamber_psi=chamber_pressure_psi, injector=injector,
                       liquid_temp_K=t1_k, strict=strict)
    T_tank = np.array([N2OSaturation.t_sat(p * PSI_TO_PA) for p in pressure_psi])   # liquid temp from tank pressure
    mdot = ox_mdot_array(t, "dyer", injector=injector,
                       p_tank_psi=pressure_psi,      # P1 = injector-side pressure
                       p_chamber_psi=chamber_pressure_psi,
                       liquid_temp_K=T_tank, strict=strict)
    fu = fuel_mdot_array(t, ox, cfg["grain"], flow, ignition_idx, burnout_idx, thrust_lbf)

    if pressure_psi is None:
        T_K = (float(tk["liquid_temp_F"]) - 32.0) * 5.0 / 9.0 + 273.15
        p_psi = N2OSaturation.p_sat(T_K) / PSI_TO_PA
        warnings.warn(f"no pressure_psi given; assuming constant saturation pressure {p_psi:.0f} psi.")
        pressure_psi = np.full_like(t, p_psi)
    # calling physical components  
    tank = OxidizerTank(
        casing=EngineComponent("ox_tank", tk["dry_mass"], tk["offset"], tk["length"], tk.get("radius")),
        volume_in3=tk["volume_in3"], initial_ox_mass_lbm=tk["initial_ox_mass_lbm"],
        liquid_temp_F=tk.get("liquid_temp_F"), times_s=t, pressure_psi=pressure_psi, mdot_lbm_s=ox,
        final_mass_lbm=flow.get("ox_final_mass_lbm"), liquid_end=liquid_end, strict=strict)
    grain = FuelGrain(
        casing=EngineComponent("grain", gr["dry_mass"], gr["offset"], gr["length"], gr.get("radius")),
        outer_radius_in=gr["outer_radius_in"], initial_port_radius_in=gr["initial_port_radius_in"],
        length_in=gr["length_in"], fuel_density_lbm_in3=gr["fuel_density_lbm_in3"],
        times_s=t, mdot_lbm_s=fu, strict=strict)
    plumbing = EngineComponent("plumbing", pl["dry_mass"], pl["offset"], pl["length"])
    eng_cfg = cfg["engine"]
    return Engine(tank=tank, plumbing=plumbing, grain=grain, length_in=eng_cfg["length_in"],
                  offset_in=eng_cfg["offset_in"], strict=strict)


# old version as comparison for results 
@dataclass
class EngineComponent2:
    name: str
    dry_mass: float  # lbs
    offset: float    # in
    length: float    # in
    prop_mass: float = 0.0   # to hold depletion (plumbing has none)
    radius: float = None
    volume: float = None
    mass_flow_rate: Optional[float] = None

    def cg_offset(self) -> float:
        return self.offset + self.length / 2
@dataclass
class Engine2:
    tank: EngineComponent2     # oxidizer
    plumbing: EngineComponent2  # injector, valves, lines
    grain: EngineComponent2    # fuel grain + casing
    length: float  # inches
    offset: float  # inches
    thrusts: np.ndarray = None
    times: np.ndarray = None
    pressure_tank: np.ndarray = None
    pressure_grain: np.ndarray = None
    mass_flow_rate: Optional[float] = None

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
            raise RuntimeError("Thrust curve not set -- call set_curve(thrusts, times) first")

    def _frac_at(self, t):
        t = np.asarray(t, dtype=float)
        frac = np.interp(t, self.times, self._frac_expended, left=0.0, right=1.0)
        return np.where(t >= self.burn_time, 1.0, frac)

    def mass_at(self, t):
        self._check_ready()
        frac = self._frac_at(t)
        return (self.tank.dry_mass + self.tank.prop_mass * (1 - frac)
                + self.grain.dry_mass + self.grain.prop_mass * (1 - frac)
                + self.plumbing.dry_mass)

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
        return self.offset + moment / total

sol_ignis ={
    "ox_mdot": 1.75,
    "fuel_mdot": 1.10,
    "flow": {
        "ox_method": "dyer",         
        "ox_mdot": 0.9,                 
        "injector": {"cd": 0.65, "n_holes": 12, "hole_dia_in": 0.0625},
        "fuel_method": "regression",     
        "n": 0.6,
        "fuel_burned_lbm": 1.8,          
    },
    "tank": {
        "dry_mass": 30, "offset": 10.0, "length": 60.0, "radius": 3.0,
        "volume_in3": math.pi * 3.0**2 * 60.0,
        "initial_ox_mass_lbm": 40.0,
        "liquid_temp_F": 60,
    },
    "grain": {
        "dry_mass": 3.0, "offset": 36.0, "length": 12.0, "radius": 1.5,
        "outer_radius_in": 1.4, "initial_port_radius_in": 0.45,
        "length_in": 12.0, "fuel_density_lbm_in3": 0.0417,
    },
    "plumbing": {"dry_mass": 2.0, "offset": 34.0, "length": 2.0},
    "engine": {"length_in": 50.0, "offset_in": 0.0},
    }
if __name__ == "__main__":

    t = np.linspace(0, 8, 161)
    burn = t < 7.5
    ox_mdot = np.where(burn, 0.95 - 0.04 * t, 0.0)                # lbm/s, decaying
    fuel_mdot = np.where(burn, 0.19 + 0.004 * t, 0.0)             # lbm/s, O/F drifts
    ox_pressure = np.clip(750 - 40 * t + 2 * t ** 2, 300, None)   # psi

    sets = pd.read_csv('interp_set-5 full data - set-5 full data.csv', usecols=[0,2,4])
    # --- 2. hardware
    engine = build_engine(sol_ignis, sets['time'], pressure_psi=sets['run_tank_pressure'], chamber_pressure_psi=sets['chamber_pressure'])
    print(engine.cg_at(sets['time']))
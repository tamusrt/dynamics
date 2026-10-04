"""
Hybrid rocket engine mass-distribution / CG model.

All arrays on common steps:

    times_s        seconds
    pressure_psi   N2O tank pressure
    mdot_lbm_s     N2O mass flow rate out of the tank   <- time-dependent
    mdot_lbm_s     fuel mass flow rate (FuelGrain)       <- time-dependent

    total N2O mass = initial mass - integral(mdot) dt
    liquid/vapor = solved from the constant tank volume: V = m_l*v_l + m_v*v_v
    liquid CG = from the liquid column height inside the tank


The fuel mdot for a hybrid differs as it is from a solid. Give `FuelGrain` your own fuel mdot array (sensor, or a
regression-rate model).

The ullage vapor is always saturated at the measured tank pressure.

Unit convention: masses lbm, lengths in, pressures psi, time s. SI
conversions happen internally wherever CoolProp is called. Positions are
measured along the engine axis, increasing toward the AFT end.

ASSUMPTIONS:
 - Tank and grain are constant-cross-section cylinders.
 - Liquid and vapor are stratified. `liquid_end` says the liquid
   pools at the end towards aft. *****The default is "fwd" as a left over 
 - The grain regresses radially only (axial CG fixed at its midpoint).
 - Flow and pressure arrays share a time base.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Union
from pathlib import Path
import numpy as np
import pandas as pd

try:
    from CoolProp.CoolProp import PropsSI
    _HAS_COOLPROP = True
except ImportError:
    _HAS_COOLPROP = False


# Unit conversions
IN_TO_M = 0.0254
IN3_TO_M3 = IN_TO_M ** 3
LBM_TO_KG = 0.45359237
PSI_TO_PA = 6894.757293168

# Pressure window handed to CoolProp's saturation calls (pre-test zero
# readings and over-range spikes would otherwise raise).
P_MIN_PA = 1.5e5
P_MAX_PA = 7.2e6          # just below the N2O critical pressure (7.245 MPa)

TimeSeries = Union[None, float, np.ndarray, Callable[[np.ndarray], np.ndarray]]


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


def mdot_on_sensor_time(t_sensor_s, t_model_s, mdot_model, t_ignition_s: float = 0.0) -> np.ndarray:
    """
    Put a model mdot curve on YOUR sensor time base, so it can sit next to
    your measured pressure array.

        t_sensor_s    your logged time array (s), e.g. from the pressure CSV
        t_model_s     the model's time array (starts at 0 at ignition)
        mdot_model    the model's mdot array (lbm/s)
        t_ignition_s  when the burn starts on the SENSOR clock

    Returns an array the same length as `t_sensor_s`: the model curve
    shifted to start at t_ignition_s, and 0 before ignition and after the
    model's burn ends.
    """
    t_rel = np.asarray(t_sensor_s, dtype=float) - t_ignition_s
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
        return float(np.trapz(fuel_mdot_from_regression(t_s, ox_mdot_lbm_s, a, n, **grain), t_s)) \
            if hasattr(np, "trapz") else float(np.trapezoid(
                fuel_mdot_from_regression(t_s, ox_mdot_lbm_s, a, n, **grain), t_s))
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
        _trapz = np.trapezoid if hasattr(np, "trapezoid") else np.trapz
        isp_s = float(_trapz(F, t)) / total_prop_burned_lbm
    return np.maximum(F / isp_s - np.asarray(ox_mdot_lbm_s, dtype=float), 0.0)


# N2O saturation properties
class N2OSaturation:
    """
    N2O saturation-dome lookups (the "steam table" for nitrous). The tank
    uses `vg(P)` for the ullage, `subcooled_liquid_v(T, P)` for the liquid
    and `t_sat(P)` for the equilibrium liquid temperature.
    """

    # Verify against NIST WebBook / ESDU 91022 before trusting it.
    _T = np.array([220, 230, 240, 250, 260, 270, 280, 290, 300, 305])
    _P = np.array([1.0e6, 1.4e6, 1.9e6, 2.5e6, 3.2e6, 4.0e6, 5.0e6, 6.2e6, 7.3e6, 7.9e6])
    _VF = np.array([0.00089, 0.00092, 0.00095, 0.00099, 0.00103, 0.00108, 0.00115, 0.00124, 0.00138, 0.00151])
    _VG = np.array([0.0430, 0.0310, 0.0225, 0.0165, 0.0122, 0.0090, 0.0065, 0.0045, 0.0028, 0.0019])

    @classmethod
    def vf_vg(cls, pressure_pa: float) -> tuple[float, float]:
        """(v_f, v_g) in m^3/kg at the given saturation pressure (Pa)."""
        if _HAS_COOLPROP:
            return (1.0 / PropsSI("D", "P", pressure_pa, "Q", 0, "N2O"),
                    1.0 / PropsSI("D", "P", pressure_pa, "Q", 1, "N2O"))
        return (float(np.interp(pressure_pa, cls._P, cls._VF)),
                float(np.interp(pressure_pa, cls._P, cls._VG)))

    @classmethod
    def vg(cls, pressure_pa: float) -> float:
        """Saturated-vapor specific volume (m^3/kg) at pressure -- used for the ullage."""
        if _HAS_COOLPROP:
            return 1.0 / PropsSI("D", "P", pressure_pa, "Q", 1, "N2O")
        return float(np.interp(pressure_pa, cls._P, cls._VG))

    @classmethod
    def p_sat(cls, temperature_k: float) -> float:
        """Saturation pressure (Pa) at temperature (K)."""
        if _HAS_COOLPROP:
            return PropsSI("P", "T", temperature_k, "Q", 0, "N2O")
        return float(np.interp(temperature_k, cls._T, cls._P))

    @classmethod
    def t_sat(cls, pressure_pa: float) -> float:
        """Saturation temperature (K) at pressure (Pa)."""
        if _HAS_COOLPROP:
            return PropsSI("T", "P", pressure_pa, "Q", 0, "N2O")
        return float(np.interp(pressure_pa, cls._P, cls._T))

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
        if _HAS_COOLPROP:
            if p_eff <= p_sat * (1 + 1e-4):
                return 1.0 / PropsSI("D", "T", temperature_k, "Q", 0, "N2O")
            try:
                return 1.0 / PropsSI("D", "T", temperature_k, "P", p_eff, "N2O")
            except ValueError:
                return 1.0 / PropsSI("D", "T", temperature_k, "Q", 0, "N2O")
        p_at_t = float(np.interp(temperature_k, cls._T, cls._P))
        return float(np.interp(p_at_t, cls._P, cls._VF))


# --------------------------------------------------------------------------
# Dry hardware (casings, plumbing) -- pure geometry, no propellant state
# --------------------------------------------------------------------------
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


# --------------------------------------------------------------------------
# Oxidizer tank -- constant-volume, two-phase N2O
# --------------------------------------------------------------------------
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
        if self.final_mass_lbm is not None and cum[-1] > 0:
            cum = cum * (self.initial_ox_mass_lbm - self.final_mass_lbm) / cum[-1]
        self._cum_mass_lost = cum
        if self.mass_history_lbm is None and cum[-1] > self.initial_ox_mass_lbm * 1.001:
            warnings.warn(
                f"{self.casing.name}: the mdot array removes {cum[-1]:.1f} lbm of N2O but only "
                f"{self.initial_ox_mass_lbm:.1f} lbm was loaded -- check mdot units/time base "
                f"(mass is clipped at 0)."
            )

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
        return np.interp(t, self.times_s, self.mdot_lbm_s)

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
        p_pa = np.clip(np.atleast_1d(self.pressure_at(t_arr)) * PSI_TO_PA, P_MIN_PA, P_MAX_PA)
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
        """Warn about physically impossible inputs."""
        L = self._column_length_in()
        if L > self.casing.length * (1 + 1e-3):
            warnings.warn(
                f"{self.casing.name}: volume_in3 / area = {L:.1f} in but the casing is only "
                f"{self.casing.length:.1f} in long -- check volume_in3, radius and length."
            )
        if not _HAS_COOLPROP:
            return
        t0 = self.times_s[:1]
        p0 = float(np.clip(self.pressure_at(t0[0]) * PSI_TO_PA, P_MIN_PA, P_MAX_PA))
        T_K = self._liquid_temp_K_at(t0, np.array([p0]))[0]
        v_l = N2OSaturation.subcooled_liquid_v(T_K, p0)
        need_in3 = self.initial_ox_mass_lbm * LBM_TO_KG * v_l / IN3_TO_M3
        if need_in3 > self.volume_in3 * (1 + 1e-3):
            warnings.warn(
                f"{self.casing.name}: {self.initial_ox_mass_lbm:.2f} lbm of liquid N2O needs "
                f"~{need_in3:.0f} in^3 but the tank volume is {self.volume_in3:.0f} in^3 (overfilled)."
            )


# --------------------------------------------------------------------------
# Fuel grain -- radial regression from a mass-flow-rate array
# --------------------------------------------------------------------------
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

    def __post_init__(self):
        self.times_s = np.asarray(self.times_s, dtype=float)
        self.mdot_lbm_s = np.asarray(self.mdot_lbm_s, dtype=float)
        assert len(self.times_s) == len(self.mdot_lbm_s)
        self._cum_mass_lost =cumulative(self.mdot_lbm_s, self.times_s)
        self.initial_fuel_mass_lbm = (
            self.fuel_density_lbm_in3 * math.pi * self.length_in
            * (self.outer_radius_in ** 2 - self.initial_port_radius_in ** 2)
        )

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


# --------------------------------------------------------------------------
# Whole-engine assembly
# --------------------------------------------------------------------------
@dataclass
class Engine:
    tank: OxidizerTank
    plumbing: EngineComponent           # injector, valves, lines -- dry, static
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


# old version as comparison 
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


if __name__ == "__main__":

    t = np.linspace(0, 8, 161)
    burn = t < 7.5
    ox_mdot = np.where(burn, 0.95 - 0.04 * t, 0.0)                # lbm/s, decaying
    fuel_mdot = np.where(burn, 0.19 + 0.004 * t, 0.0)             # lbm/s, O/F drifts
    ox_pressure = np.clip(750 - 40 * t + 2 * t ** 2, 300, None)   # psi

    # --- 2. hardware
    tank_casing = EngineComponent("ox_tank_casing", dry_mass=8.0, offset=10.0, length=24.0, radius=2.0)
    grain_casing = EngineComponent("grain_casing", dry_mass=3.0, offset=36.0, length=12.0, radius=1.5)
    plumbing = EngineComponent("plumbing", dry_mass=2.0, offset=34.0, length=2.0)

    # --- 3. tank + grain take the arrays directly
    tank = OxidizerTank(
        casing=tank_casing,
        volume_in3=tank_casing.volume(),          # constant volume
        initial_ox_mass_lbm=8.0,
        liquid_temp_F=65.0,                       # or None for equilibrium, or an array
        times_s=t, pressure_psi=ox_pressure, mdot_lbm_s=ox_mdot,
        liquid_end="aft",
    )
    grain = FuelGrain(
        casing=grain_casing,
        outer_radius_in=1.4, initial_port_radius_in=0.4, length_in=12.0,
        fuel_density_lbm_in3=0.0417,              # ~HTPB
        times_s=t, mdot_lbm_s=fuel_mdot,
    )
    engine = Engine(tank=tank, plumbing=plumbing, grain=grain, length_in=50.0, offset_in=0.0)
    times = np.arange(0, 5 + 0.001, 0.001)
    print(engine.cg_at(times))
    # --- 4. query at any time (scalar or array)
    for tt in (0.0, 2.0, 4.0, 6.0, 7.9):
        liq, vap, vf = tank.phase_split_at(tt)
        mo, mf = engine.mdot_at(tt)
        print(
            f"t={tt:4.1f}s  mdot_ox={mo:5.3f} mdot_f={mf:5.3f} lbm/s  "
            f"liq={liq:5.2f} vap={vap:5.2f} lbm  port_r={grain.port_radius_at(tt):.3f} in  "
            f"m={engine.mass_at(tt):6.2f} lbm  cg={engine.cg_at(tt):.2f} in"
        )

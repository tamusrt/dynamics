# =============================================================================
# LUMINA N2O / ETHANOL BLOWDOWN MODEL
# =============================================================================
#
# PURPOSE
# -------
# This is a zero-dimensional, quasi-steady engineering model of the Lumina
# propulsion system. It is intended to predict first-order blowdown behavior
# and to support injector/feed-system sizing, thrust-curve generation, and
# comparison against cold-flow and hotfire data.
#
# The simulation has two oxidizer regimes:
#
#   1. TWO-PHASE / LIQUID-FED REGIME
#      The N2O tank contains saturated liquid + vapor, but the tank outlet is
#      assumed to draw liquid. Tank temperature is solved from mass, volume,
#      and internal-energy conservation. Injector flow is calculated with the
#      Dyer/NHNE flashing-flow model.
#
#   2. VAPOR REGIME
#      Once liquid quality reaches x = 1, all remaining N2O is treated as a
#      single-phase vapor. Oxidizer flow then uses a compressible-gas orifice
#      relation through a lumped equivalent CdA for the complete ox flow path.
#
# IMPORTANT MODEL ASSUMPTIONS / LIMITATIONS
# -----------------------------------------
# - Tank states are spatially uniform (0-D); stratification and slosh are not
#   modeled.
# - Liquid/vapor N2O is assumed to remain in homogeneous thermodynamic
#   equilibrium while both phases coexist.
# - Tank-wall/environment heat transfer is not currently modeled; the tank
#   energy balance is adiabatic except for discharged enthalpy and P*dV work.
# - Feedlines and injectors are represented with lumped effective CdA values.
# - Feed-system and chamber states are solved quasi-steadily; fluid inertia,
#   valve-opening transients, manifold filling, and combustion instability are
#   not modeled.
# - c*, c* efficiency, and Cf are currently treated as constants, so results
#   become less trustworthy during large O/F excursions, especially in the
#   late vapor tail.
# - The vapor-flow model is lower fidelity than the liquid-fed Dyer model and
#   combines ox feedline + injector losses into one equivalent CdA.
#
# Use this model for engineering estimates and trade studies, then calibrate it
# against test data before using its outputs for design decisions.
# =============================================================================

import numpy as np
import matplotlib.pyplot as plt

from CoolProp.CoolProp import PropsSI
from scipy.optimize import brentq


# ============================================================
# CONSTANTS / UNIT CONVERSIONS
# ============================================================

PA_PER_PSI = 6894.757
N_PER_LBF = 4.44822
M_PER_IN = 0.0254

# CoolProp uses the fluid name below for all N2O property calls.  R_N2O is
# precomputed once because the vapor-flow model needs the specific gas constant
# R = R_universal / molar_mass.
FLUID = "NitrousOxide"
R_UNIVERSAL = PropsSI("GAS_CONSTANT", FLUID)
MOLAR_MASS_N2O = PropsSI("MOLAR_MASS", FLUID)
R_N2O = R_UNIVERSAL / MOLAR_MASS_N2O

N2O_T_MIN = 183.0
N2O_T_MAX = 309.4


# ============================================================
# INPUTS
# ============================================================

# Simulation
DT = 0.2                    # s
BURN_TIME = 20.0            # s

# N2O tank
T0 = 291.14                 # K
m_ox0 = 14.16               # kg
V_ox0 = 0.0188              # m^3
ox_phase = "two_phase"      # Starts liquid-fed, then switches to vapor blowdown

# Fuel
m_fuel0 = 5.72              # kg
rho_fuel = 789.0            # kg/m^3, approximate ethanol density

# Feedline effective flow areas (CdA).
# CdA = discharge coefficient * physical flow area.  Treating a group of
# valves, fittings, and tubing as one effective CdA lets the model reproduce
# the total measured pressure loss without explicitly modeling every fitting.
CdA_ox_feed = 4.5e-5        # m^2
CdA_fuel_feed = 1.821412e-5 # m^2

# Injector effective flow areas (CdA).  These are the primary parameters used
# when trading injector pressure drop / stiffness and physical orifice area.
CdA_ox_inj = 4.2239e-5      # m^2
CdA_fuel_inj = 1.5348e-5    # m^2

# Engine
throat_diameter = 0.0296    # m
A_throat = np.pi * throat_diameter**2 / 4.0

cstar = 1407.8              # m/s, replace with RPA value as needed
cstar_eff = 0.85
Cf = 1.4067                 # replace with RPA/nozzle value as needed

# Plunger geometry
D_plunger = 2.62 * M_PER_IN
A_plunger = np.pi * D_plunger**2 / 4.0

# Equivalent oxidizer flow-path CdA used only in the vapor-flow model.
# For two incompressible-style restrictions in series, pressure losses add as
#   dP = mdot^2/(2*rho*CdA_feed^2) + mdot^2/(2*rho*CdA_inj^2)
# which gives the equivalent relation below.  Using it for compressible vapor
# is an engineering approximation; the vapor model does not separately solve
# feedline and injector pressures.
CdA_ox_gas = 1.0 / np.sqrt(1.0 / CdA_ox_feed**2 + 1.0 / CdA_ox_inj**2)


# ============================================================
# N2O PROPERTY / TANK HELPERS
# ============================================================

def get_sat_props(T):
    """Return saturated N2O properties at temperature T.

    CoolProp quality Q = 0 selects saturated liquid and Q = 1 selects
    saturated vapor.  During the two-phase tank regime, pressure is therefore
    the saturation pressure associated with the solved tank temperature.
    """
    P = PropsSI("P", "T", T, "Q", 0, FLUID)
    rho_l = PropsSI("D", "T", T, "Q", 0, FLUID)
    rho_v = PropsSI("D", "T", T, "Q", 1, FLUID)
    u_l = PropsSI("U", "T", T, "Q", 0, FLUID)
    u_v = PropsSI("U", "T", T, "Q", 1, FLUID)
    h_l = PropsSI("H", "T", T, "Q", 0, FLUID)
    s_l = PropsSI("S", "T", T, "Q", 0, FLUID)
    return P, rho_l, rho_v, u_l, u_v, h_l, s_l


def get_quality(m, V, rho_l, rho_v):
    """Return N2O vapor mass quality from mixture specific volume.

    Quality x is the vapor mass fraction:

        x = m_vapor / m_total

    For a saturated liquid/vapor mixture, specific volume is

        v = (1 - x)*v_l + x*v_v

    Solving that relation for x gives the expression below.  x = 0 is all
    saturated liquid and x = 1 is all saturated vapor.
    """
    v = V / m
    v_l = 1.0 / rho_l
    v_v = 1.0 / rho_v
    return (v - v_l) / (v_v - v_l)


# ============================================================
# COMPRESSIBLE N2O VAPOR FLOW MODEL
# ============================================================

def ox_vapor_flow(Ptank, Ttank, Pc, cp, cv):
    """Return single-phase N2O vapor mass flow through the lumped gas CdA.

    This model is used only after liquid N2O is depleted.  It treats the
    remaining oxidizer as a compressible gas flowing from tank pressure to
    chamber pressure.  The pressure ratio determines whether the restriction
    is choked (mass flow no longer depends on downstream pressure) or unchoked.
    """
    if Ptank <= Pc:
        return 0.0

    # gamma = cp/cv controls the isentropic expansion behavior.  The critical
    # pressure ratio is the downstream/upstream ratio at which sonic choking
    # first occurs for this ideal-gas-style compressible-flow relation.
    gamma = cp / cv
    pressure_ratio = Pc / Ptank
    critical_ratio = (2.0 / (gamma + 1.0)) ** (gamma / (gamma - 1.0))

    # Choked flow: once Pc/Ptank is below the critical ratio, lowering Pc
    # further does not increase the predicted mass flow.
    if pressure_ratio <= critical_ratio:
        choked_factor = (2.0 / (gamma + 1.0)) ** ((gamma + 1.0) / (2.0 * (gamma - 1.0)))
        return CdA_ox_gas * Ptank * np.sqrt(gamma / (R_N2O * Ttank)) * choked_factor

    # Unchoked flow: chamber pressure is high enough that downstream pressure
    # still influences the mass flow through the restriction.
    flow_term = 2.0 * gamma / (R_N2O * Ttank * (gamma - 1.0))
    pressure_term = (
        pressure_ratio ** (2.0 / gamma)
        - pressure_ratio ** ((gamma + 1.0) / gamma)
    )
    return CdA_ox_gas * Ptank * np.sqrt(flow_term * pressure_term)


# ============================================================
# DYER / NHNE N2O INJECTOR MODEL
# ============================================================

def dyer_mass_flow(P1, P2, Pv, rho1, h1, s1):
    """
    Corrected Dyer / NHNE N2O injector model.

    P1   = injector inlet pressure [Pa]
    P2   = chamber pressure [Pa]
    Pv   = saturation pressure corresponding to tank T [Pa]
    rho1 = saturated liquid density at tank T [kg/m^3]
    h1   = saturated liquid enthalpy at tank T [J/kg]
    s1   = saturated liquid entropy at tank T [J/kg-K]
    """
    if P1 <= P2:
        return 0.0, 0.0, 0.0, 0.0

    # SPI = Single-Phase Incompressible.  This is the familiar liquid-orifice
    # equation.  It represents the limiting case where liquid N2O remains
    # metastable through the injector and does NOT significantly flash before
    # leaving the restriction:
    #
    #   mdot_SPI = CdA * sqrt(2*rho*(P1 - P2))
    #
    # SPI usually predicts more flow than a model that allows strong flashing.
    mdot_spi = CdA_ox_inj * np.sqrt(2.0 * rho1 * (P1 - P2))

    # HEM = Homogeneous Equilibrium Model.  This is the opposite limiting case:
    # N2O is assumed to flash fast enough to remain in thermodynamic equilibrium
    # during the pressure drop.  The expansion is treated as isentropic from
    # injector inlet entropy s1 down to chamber pressure P2.  The resulting
    # enthalpy drop is converted to velocity through sqrt(2*dh).
    try:
        h2 = PropsSI("H", "P", P2, "S", s1, FLUID)
        rho2 = PropsSI("D", "P", P2, "S", s1, FLUID)
        dh = h1 - h2
        mdot_hem = 0.0 if dh <= 0.0 else CdA_ox_inj * rho2 * np.sqrt(2.0 * dh)
    except ValueError:
        mdot_hem = 0.0

    # Dyer/NHNE does not assume that real injector flow is perfectly SPI or
    # perfectly HEM.  kappa weights the two limits based on how much pressure
    # drop exists relative to the available flashing pressure drop.  Large
    # kappa pushes the result toward SPI; small kappa gives HEM more weight.
    if Pv <= P2:
        kappa = 1.0e6
    else:
        kappa = np.sqrt((P1 - P2) / (Pv - P2))

    # Final Dyer mass flow is the weighted blend of the two limiting models.
    mdot_dyer = (kappa * mdot_spi + mdot_hem) / (1.0 + kappa)
    return mdot_dyer, mdot_spi, mdot_hem, kappa


# ============================================================
# FEED SYSTEM MODEL
# ============================================================

def ox_flow(Ptank, Pc, rho_ox, h_ox, s_ox):
    """Solve liquid-fed N2O flow through the feed system and injector.

    This requires an inner root solve because the unknown mass flow appears in
    two coupled places:

      1. mdot determines feedline pressure loss.
      2. Feedline pressure loss determines injector inlet pressure.
      3. Injector inlet pressure determines the Dyer-predicted mdot.

    The correct solution is the mdot for which the guessed flow and Dyer flow
    are equal.
    """
    if Ptank <= Pc:
        return 0.0

    def ox_flow_residual(mdot):
        # Treat the liquid-fed oxidizer plumbing as one incompressible hydraulic
        # restriction.  Higher mdot causes feedline dP to rise with mdot^2.
        dP_feed = mdot**2 / (2.0 * rho_ox * CdA_ox_feed**2)
        Pman_ox = Ptank - dP_feed

        if Pman_ox <= Pc:
            mdot_model = 0.0
        else:
            mdot_model, _, _, _ = dyer_mass_flow(Pman_ox, Pc, Ptank, rho_ox, h_ox, s_ox)

        return mdot - mdot_model

    # Upper bound: all available pressure drop consumed by the feedline.
    mdot_high = CdA_ox_feed * np.sqrt(2.0 * rho_ox * (Ptank - Pc))
    return brentq(ox_flow_residual, 0.0, mdot_high)


def fuel_flow(Pfuel, Pc):
    """Return incompressible ethanol flow through feedline + injector.

    The same fuel mass flow passes through both restrictions, so their pressure
    drops add:

        dP_total = mdot^2/(2*rho*CdA_feed^2)
                 + mdot^2/(2*rho*CdA_inj^2)

    Solving this equation for mdot gives the compact expression below.
    """
    dP_total = Pfuel - Pc
    if dP_total <= 0.0:
        return 0.0

    resistance = 1.0 / CdA_fuel_feed**2 + 1.0 / CdA_fuel_inj**2
    return np.sqrt(2.0 * rho_fuel * dP_total / resistance)


# ============================================================
# CHAMBER PRESSURE / THRUST MODEL
# ============================================================

def chamber_residual(Pc, Ptank, rho_ox, h_ox, s_ox):
    """Residual used to find the self-consistent liquid-fed chamber pressure.

    Pc affects both propellant flow rates, while total mass flow sets the
    chamber pressure through c*:

        Pc = mdot_total * (cstar * cstar_eff) / A_throat

    The root occurs when guessed Pc equals the Pc implied by the resulting
    propellant mass flow.
    """
    mdot_ox = ox_flow(Ptank, Pc, rho_ox, h_ox, s_ox)
    mdot_f = fuel_flow(Ptank, Pc)
    Pc_from_engine = (mdot_ox + mdot_f) * (cstar * cstar_eff) / A_throat
    return Pc - Pc_from_engine


def solve_chamber_pressure(Ptank, rho_ox, h_ox, s_ox):
    """Root-solve the coupled liquid-fed chamber pressure."""
    Pc_low = 1000.0
    Pc_high = 0.999 * Ptank
    return brentq(chamber_residual, Pc_low, Pc_high, args=(Ptank, rho_ox, h_ox, s_ox))


def chamber_residual_vapor(Pc, Ptank, Ttank, cp_ox, cv_ox):
    """Same chamber-pressure balance as above, but with vapor N2O flow."""
    mdot_ox = ox_vapor_flow(Ptank, Ttank, Pc, cp_ox, cv_ox)
    mdot_f = fuel_flow(Ptank, Pc)
    Pc_from_engine = (mdot_ox + mdot_f) * (cstar * cstar_eff) / A_throat
    return Pc - Pc_from_engine


def solve_chamber_pressure_vapor(Ptank, Ttank, cp_ox, cv_ox):
    """Root-solve chamber pressure after the oxidizer becomes vapor-only."""
    Pc_low = 1000.0
    Pc_high = 0.999 * Ptank
    return brentq(chamber_residual_vapor, Pc_low, Pc_high, args=(Ptank, Ttank, cp_ox, cv_ox))


def get_thrust(Pc):
    """Calculate thrust from F = Cf * Pc * A_throat."""
    return Cf * Pc * A_throat


# ============================================================
# TANK STATE SOLVERS
# ============================================================

def tank_energy_residual(T, m, U, V):
    """Energy residual used to solve the two-phase tank temperature.

    For a trial temperature, saturation properties determine liquid/vapor
    density and therefore the quality required to fill the known tank volume.
    That quality determines mixture specific internal energy:

        u_mix = (1 - x)*u_l + x*u_v

    The correct temperature is the one for which m*u_mix equals the tank's
    conserved total internal energy U.
    """
    _, rho_l, rho_v, u_l, u_v, _, _ = get_sat_props(T)
    x = get_quality(m, V, rho_l, rho_v)
    u_mix = (1.0 - x) * u_l + x * u_v
    return m * u_mix - U


def solve_tank_state(m, U, V, T_previous):
    """Solve saturated two-phase N2O tank state from m, U, and V.

    Temperature is the unknown.  Once T is found, saturation pressure and
    vapor quality follow directly from CoolProp and the mixture volume.  The
    first root bracket stays near the previous timestep for speed; the wider
    bracket is a fallback if the local bracket does not contain a root.
    """
    T_low = max(N2O_T_MIN, T_previous - 10.0)
    T_high = min(N2O_T_MAX, T_previous + 2.0)

    try:
        T = brentq(tank_energy_residual, T_low, T_high, args=(m, U, V))
    except ValueError:
        T = brentq(tank_energy_residual, N2O_T_MIN, N2O_T_MAX, args=(m, U, V))

    P, rho_l, rho_v, _, _, h_l, _ = get_sat_props(T)
    x = get_quality(m, V, rho_l, rho_v)
    return T, P, x, rho_l, h_l


def solve_vapor_tank_state(m, U, V):
    """Solve the single-phase N2O vapor state after liquid depletion.

    In the vapor regime, quality is no longer a thermodynamic state variable.
    The conserved mass, total volume, and internal energy give density rho=m/V
    and specific internal energy u=U/m.  CoolProp then returns the corresponding
    real-fluid vapor state.
    """
    rho = m / V
    u = U / m

    T = PropsSI("T", "Dmass", rho, "Umass", u, FLUID)
    P = PropsSI("P", "Dmass", rho, "Umass", u, FLUID)
    h = PropsSI("Hmass", "Dmass", rho, "Umass", u, FLUID)
    s = PropsSI("Smass", "Dmass", rho, "Umass", u, FLUID)
    cp = PropsSI("Cpmass", "Dmass", rho, "Umass", u, FLUID)
    cv = PropsSI("Cvmass", "Dmass", rho, "Umass", u, FLUID)

    return T, P, rho, h, s, cp, cv


# ============================================================
# MAIN SIMULATION
# ============================================================

def main():
    # --------------------------------------------------------
    # Initial N2O state
    # --------------------------------------------------------
    # The tank starts as a saturated liquid/vapor mixture.  Initial quality is
    # determined from mass and volume, then used to calculate mixture internal
    # energy.  U_ox is subsequently advanced by the tank energy balance.
    P0, rho_l0, rho_v0, u_l0, u_v0, h_l0, s_l0 = get_sat_props(T0)
    x0 = get_quality(m_ox0, V_ox0, rho_l0, rho_v0)
    u0 = (1.0 - x0) * u_l0 + x0 * u_v0
    U_ox0 = m_ox0 * u0

    print("Initial N2O state")
    print(f"Tank pressure: {P0 / PA_PER_PSI:.1f} psi")
    print(f"Temperature:   {T0:.2f} K")
    print(f"Quality:       {x0:.5f}")
    print(f"N2O mass:      {m_ox0:.3f} kg")

    # --------------------------------------------------------
    # Initial engine operating point
    # --------------------------------------------------------
    P, rho_l, rho_v, _, _, h_l, s_l = get_sat_props(T0)
    Pc = solve_chamber_pressure(P, rho_l, h_l, s_l)
    mdot_ox = ox_flow(P, Pc, rho_l, h_l, s_l)
    mdot_f = fuel_flow(P, Pc)
    F = get_thrust(Pc)

    ox_feed_dp_initial = mdot_ox**2 / (2.0 * rho_l * CdA_ox_feed**2)
    fuel_feed_dp_initial = mdot_f**2 / (2.0 * rho_fuel * CdA_fuel_feed**2)

    print()
    print("Initial engine operating point")
    print(f"Tank pressure:    {P / PA_PER_PSI:.1f} psi")
    print(f"Chamber pressure: {Pc / PA_PER_PSI:.1f} psi")
    print(f"Ox flow:          {mdot_ox:.3f} kg/s")
    print(f"Fuel flow:        {mdot_f:.3f} kg/s")
    print(f"O/F:              {mdot_ox / mdot_f:.3f}")
    print(f"Thrust:           {F / N_PER_LBF:.1f} lbf")
    print(f"Ox feed dP:       {ox_feed_dp_initial / PA_PER_PSI:.2f} psi")
    print(f"Fuel feed dP:     {fuel_feed_dp_initial / PA_PER_PSI:.2f} psi")

    # --------------------------------------------------------
    # Initial simulation state
    # --------------------------------------------------------
    # The plunger couples the two propellant volumes.  Fuel volume decreases as
    # fuel is consumed, and the N2O volume increases by the same amount.  V_total
    # is therefore constant for this simplified concentric/plunger geometry.
    V_fuel0 = m_fuel0 / rho_fuel
    V_total = V_ox0 + V_fuel0

    m_ox = m_ox0
    m_fuel = m_fuel0
    U_ox = U_ox0
    T = T0
    V_ox = V_ox0
    phase = ox_phase
    depletion_state = None

    # Histories
    times = []
    tank_pressures = []
    chamber_pressures = []
    thrusts = []
    mdot_ox_history = []
    mdot_fuel_history = []
    of_history = []
    temperatures = []
    qualities = []
    plunger_positions = []
    V_ox_history = []
    V_fuel_history = []
    m_ox_history = []
    m_ox_liquid_history = []
    m_ox_vapor_history = []
    ox_feed_dp_history = []
    fuel_feed_dp_history = []
    ox_inj_dp_history = []
    fuel_inj_dp_history = []
    ox_stiffness_history = []
    fuel_stiffness_history = []
    ox_manifold_pressure_history = []
    fuel_manifold_pressure_history = []
    dyer_mdot_history = []
    spi_mdot_history = []
    hem_mdot_history = []
    kappa_history = []

    t = 0.0

    while t <= BURN_TIME + 1.0e-12:
        # ----------------------------------------------------
        # Basic depletion checks
        # ----------------------------------------------------
        if m_ox <= 1.0e-6:
            print("N2O depleted.")
            break

        if m_fuel <= 1.0e-6:
            print("Fuel depleted.")
            break

        dt_step = DT

        # ----------------------------------------------------
        # Current oxidizer tank state
        # ----------------------------------------------------
        # In the two-phase regime, T defines the saturation pressure and phase
        # properties.  Quality is recomputed from the current N2O mass + volume.
        # In the vapor regime, CoolProp solves the single-phase state directly
        # from density and specific internal energy.
        if phase == "two_phase":
            P, rho_l, rho_v, _, _, h_l, s_l = get_sat_props(T)
            x = get_quality(m_ox, V_ox, rho_l, rho_v)

            # Resolve the liquid-depletion event more finely without
            # forcing the entire simulation to use the smaller timestep.
            if x > 0.95:
                dt_step = min(dt_step, 0.01)
        else:
            T, P, rho_ox, h_ox, s_ox, cp_ox, cv_ox = solve_vapor_tank_state(m_ox, U_ox, V_ox)
            # x = 1 here is bookkeeping meaning "all remaining N2O is vapor."
            # Thermodynamic quality is not formally defined for superheated gas.
            x = 1.0

        # ----------------------------------------------------
        # Engine operating point
        # ----------------------------------------------------
        # For the current tank state, solve Pc such that the feed-system mass
        # flows and c*-based chamber relation are simultaneously satisfied.
        # The oxidizer model changes at liquid depletion; the fuel model does not.
        if phase == "two_phase":
            Pc = solve_chamber_pressure(P, rho_l, h_l, s_l)
            mdot_ox = ox_flow(P, Pc, rho_l, h_l, s_l)
            h_out_ox = h_l
        else:
            Pc = solve_chamber_pressure_vapor(P, T, cp_ox, cv_ox)
            mdot_ox = ox_vapor_flow(P, T, Pc, cp_ox, cv_ox)
            h_out_ox = h_ox

        mdot_f = fuel_flow(P, Pc)
        of_ratio = mdot_ox / mdot_f if mdot_f > 0.0 else np.nan
        F = get_thrust(Pc)

        # ----------------------------------------------------
        # Flow / injector diagnostics
        # ----------------------------------------------------
        # These calculations do not change the simulation state; they expose
        # useful design quantities such as manifold pressure, injector dP, and
        # injector stiffness.  Here stiffness is defined as dP_injector / Pc.
        if phase == "two_phase":
            ox_feed_dp = mdot_ox**2 / (2.0 * rho_l * CdA_ox_feed**2)
            Pman_ox = P - ox_feed_dp
            mdot_dyer_check, mdot_spi, mdot_hem, kappa = dyer_mass_flow(
                Pman_ox, Pc, P, rho_l, h_l, s_l
            )
            ox_inj_dp = Pman_ox - Pc
            ox_stiffness = ox_inj_dp / Pc
        else:
            # The vapor model lumps the ox feedline and injector into a
            # single equivalent gas CdA, so these quantities are undefined.
            ox_feed_dp = np.nan
            Pman_ox = np.nan
            ox_inj_dp = np.nan
            ox_stiffness = np.nan
            mdot_dyer_check = np.nan
            mdot_spi = np.nan
            mdot_hem = np.nan
            kappa = np.nan

        # Fuel feedline and injector dP can be separated directly because both
        # are modeled as incompressible restrictions with known CdA values.
        fuel_feed_dp = mdot_f**2 / (2.0 * rho_fuel * CdA_fuel_feed**2)
        fuel_inj_dp = mdot_f**2 / (2.0 * rho_fuel * CdA_fuel_inj**2)
        Pman_fuel = P - fuel_feed_dp
        fuel_stiffness = fuel_inj_dp / Pc

        # ----------------------------------------------------
        # Current propellant / plunger state
        # ----------------------------------------------------
        # Fuel is treated as constant-density liquid, so its remaining volume is
        # simply m/rho.  N2O phase masses follow directly from vapor quality.
        V_fuel = m_fuel / rho_fuel
        plunger_travel = (V_ox - V_ox0) / A_plunger

        if phase == "two_phase":
            m_vapor = x * m_ox
            m_liquid = (1.0 - x) * m_ox
        else:
            m_vapor = m_ox
            m_liquid = 0.0

        # ----------------------------------------------------
        # Save current state
        # ----------------------------------------------------
        times.append(t)
        tank_pressures.append(P)
        chamber_pressures.append(Pc)
        thrusts.append(F)
        mdot_ox_history.append(mdot_ox)
        mdot_fuel_history.append(mdot_f)
        of_history.append(of_ratio)
        temperatures.append(T)
        qualities.append(x)
        plunger_positions.append(plunger_travel)
        V_ox_history.append(V_ox)
        V_fuel_history.append(V_fuel)
        m_ox_history.append(m_ox)
        m_ox_liquid_history.append(m_liquid)
        m_ox_vapor_history.append(m_vapor)
        ox_feed_dp_history.append(ox_feed_dp)
        fuel_feed_dp_history.append(fuel_feed_dp)
        ox_inj_dp_history.append(ox_inj_dp)
        fuel_inj_dp_history.append(fuel_inj_dp)
        ox_stiffness_history.append(ox_stiffness)
        fuel_stiffness_history.append(fuel_stiffness)
        ox_manifold_pressure_history.append(Pman_ox)
        fuel_manifold_pressure_history.append(Pman_fuel)
        dyer_mdot_history.append(mdot_dyer_check)
        spi_mdot_history.append(mdot_spi)
        hem_mdot_history.append(mdot_hem)
        kappa_history.append(kappa)

        if t >= BURN_TIME - 1.0e-12:
            break

        dt_step = min(dt_step, BURN_TIME - t)

        # ----------------------------------------------------
        # Candidate state update
        # ----------------------------------------------------
        # Advance mass with mdot*dt.  Moving the plunger changes N2O volume as
        # fuel leaves.  The N2O energy balance is
        #
        #   U_new = U_old - mdot_ox*h_out*dt - P*dV
        #
        # where the first term removes flow enthalpy and the second accounts for
        # expansion work done by the N2O as its volume grows.
        m_ox_trial = m_ox - mdot_ox * dt_step
        m_fuel_trial = m_fuel - mdot_f * dt_step

        # Avoid passing nonphysical negative masses into CoolProp.
        if m_ox_trial <= 0.0:
            print("N2O depleted during timestep.")
            break

        if m_fuel_trial <= 0.0:
            print("Fuel depleted during timestep.")
            break

        V_ox_trial = V_total - m_fuel_trial / rho_fuel
        dV_ox_trial = V_ox_trial - V_ox
        U_ox_trial = U_ox - mdot_ox * h_out_ox * dt_step - P * dV_ox_trial

        # ----------------------------------------------------
        # Accept state or locate the liquid-depletion event
        # ----------------------------------------------------
        # First solve the candidate two-phase state.  If x < 1, liquid remains
        # and the full timestep is accepted.  If x >= 1, the timestep crossed
        # the saturated-vapor boundary and must be shortened to the exact event.
        if phase == "two_phase":
            T_try, P_try, x_try, rho_l_try, h_l_try = solve_tank_state(
                m_ox_trial, U_ox_trial, V_ox_trial, T
            )

            if x_try < 1.0:
                m_ox = m_ox_trial
                m_fuel = m_fuel_trial
                V_ox = V_ox_trial
                U_ox = U_ox_trial
                T = T_try
                P = P_try
                x = x_try
                rho_l = rho_l_try
                h_l = h_l_try
                t += dt_step

            else:
                # Locate x = 1 inside this timestep.  frac is the fraction of
                # dt_step elapsed when the last liquid disappears.  Root-solving
                # this event prevents an artificial jump in tank T or P when the
                # code switches from the two-phase solver to the vapor solver.
                def depletion_residual(frac):
                    dt_sub = frac * dt_step
                    m_ox_sub = m_ox - mdot_ox * dt_sub
                    m_fuel_sub = m_fuel - mdot_f * dt_sub
                    V_ox_sub = V_total - m_fuel_sub / rho_fuel
                    dV_ox_sub = V_ox_sub - V_ox
                    U_ox_sub = U_ox - mdot_ox * h_out_ox * dt_sub - P * dV_ox_sub
                    _, _, x_sub, _, _ = solve_tank_state(m_ox_sub, U_ox_sub, V_ox_sub, T)
                    return x_sub - 1.0

                frac_depletion = brentq(depletion_residual, 0.0, 1.0)
                dt_depletion = frac_depletion * dt_step

                m_ox -= mdot_ox * dt_depletion
                m_fuel -= mdot_f * dt_depletion

                V_ox_new = V_total - m_fuel / rho_fuel
                dV_ox = V_ox_new - V_ox
                U_ox = U_ox - mdot_ox * h_out_ox * dt_depletion - P * dV_ox
                V_ox = V_ox_new

                # Re-solve the shortened step exactly on the saturated-vapor
                # boundary.  This state is the common physical boundary shared
                # by both the two-phase and single-phase property descriptions.
                T, P, x, rho_l, h_l = solve_tank_state(m_ox, U_ox, V_ox, T)
                t += dt_depletion

                depletion_state = {
                    "time": t,
                    "P": P,
                    "T": T,
                    "m_ox": m_ox,
                    "m_fuel": m_fuel,
                }

                print(f"Liquid N2O depleted at t = {t:.3f} s")

                # Verify numerical continuity when switching solvers.  Because
                # mass, volume, and internal energy are unchanged by the switch,
                # both property descriptions should return nearly identical T/P.
                T_transition_sat = T
                P_transition_sat = P

                phase = "vapor"
                x = 1.0
                T, P, rho_ox, h_ox, s_ox, cp_ox, cv_ox = solve_vapor_tank_state(m_ox, U_ox, V_ox)

                print(
                    f"Transition continuity: dT = {T - T_transition_sat:+.6f} K, "
                    f"dP = {(P - P_transition_sat) / PA_PER_PSI:+.6f} psi"
                )

        else:
            # Vapor-only blowdown uses the same mass/volume/energy update, then
            # directly solves the new single-phase state from rho and u.
            m_ox = m_ox_trial
            m_fuel = m_fuel_trial
            V_ox = V_ox_trial
            U_ox = U_ox_trial
            T, P, rho_ox, h_ox, s_ox, cp_ox, cv_ox = solve_vapor_tank_state(m_ox, U_ox, V_ox)
            t += dt_step

    # ========================================================
    # POST-PROCESSING
    # ========================================================
    times = np.array(times)
    tank_pressures = np.array(tank_pressures)
    chamber_pressures = np.array(chamber_pressures)
    thrusts = np.array(thrusts)
    mdot_ox_history = np.array(mdot_ox_history)
    mdot_fuel_history = np.array(mdot_fuel_history)
    of_history = np.array(of_history)
    temperatures = np.array(temperatures)
    qualities = np.array(qualities)
    plunger_positions = np.array(plunger_positions)
    V_ox_history = np.array(V_ox_history)
    V_fuel_history = np.array(V_fuel_history)
    m_ox_history = np.array(m_ox_history)
    m_ox_liquid_history = np.array(m_ox_liquid_history)
    m_ox_vapor_history = np.array(m_ox_vapor_history)
    ox_feed_dp_history = np.array(ox_feed_dp_history)
    fuel_feed_dp_history = np.array(fuel_feed_dp_history)
    ox_inj_dp_history = np.array(ox_inj_dp_history)
    fuel_inj_dp_history = np.array(fuel_inj_dp_history)
    ox_stiffness_history = np.array(ox_stiffness_history)
    fuel_stiffness_history = np.array(fuel_stiffness_history)
    ox_manifold_pressure_history = np.array(ox_manifold_pressure_history)
    fuel_manifold_pressure_history = np.array(fuel_manifold_pressure_history)
    dyer_mdot_history = np.array(dyer_mdot_history)
    spi_mdot_history = np.array(spi_mdot_history)
    hem_mdot_history = np.array(hem_mdot_history)
    kappa_history = np.array(kappa_history)

    # Integrating thrust over time gives total impulse.
    total_impulse_Ns = np.trapz(thrusts, times)
    total_impulse_lbfs = total_impulse_Ns / N_PER_LBF
    actual_burn_time = times[-1] - times[0]
    average_thrust = total_impulse_Ns / actual_burn_time

    # ========================================================
    # CONSOLE OUTPUT
    # ========================================================
    print()
    print("===== PERFORMANCE SUMMARY =====")
    print(f"Burn time:          {times[-1]:.2f} s")
    print(f"Initial thrust:     {thrusts[0] / N_PER_LBF:.1f} lbf")
    print(f"Final thrust:       {thrusts[-1] / N_PER_LBF:.1f} lbf")
    print(f"Average thrust:     {average_thrust / N_PER_LBF:.1f} lbf")
    print(f"Total impulse:      {total_impulse_lbfs:.1f} lbf-s")

    print()
    print(f"Initial Ptank:      {tank_pressures[0] / PA_PER_PSI:.1f} psi")
    print(f"Final Ptank:        {tank_pressures[-1] / PA_PER_PSI:.1f} psi")
    print(f"Initial Pc:         {chamber_pressures[0] / PA_PER_PSI:.1f} psi")
    print(f"Final Pc:           {chamber_pressures[-1] / PA_PER_PSI:.1f} psi")

    print()
    print(f"Initial O/F:        {of_history[0]:.3f}")
    print(f"Final O/F:          {of_history[-1]:.3f}")
    print(f"N2O consumed:       {m_ox0 - m_ox:.3f} kg")
    print(f"Fuel consumed:      {m_fuel0 - m_fuel:.3f} kg")

    print()
    print("===== INITIAL INJECTOR PERFORMANCE =====")
    print(f"Ox manifold pressure:   {ox_manifold_pressure_history[0] / PA_PER_PSI:.1f} psi")
    print(f"Ox injector dP:         {ox_inj_dp_history[0] / PA_PER_PSI:.1f} psi")
    print(f"Ox injector stiffness:  {100.0 * ox_stiffness_history[0]:.1f} %")
    print()
    print(f"Fuel manifold pressure: {fuel_manifold_pressure_history[0] / PA_PER_PSI:.1f} psi")
    print(f"Fuel injector dP:       {fuel_inj_dp_history[0] / PA_PER_PSI:.1f} psi")
    print(f"Fuel injector stiffness:{100.0 * fuel_stiffness_history[0]:.1f} %")

    if depletion_state is not None:
        print()
        print("===== LIQUID N2O DEPLETION =====")
        print(f"Time:               {depletion_state['time']:.3f} s")
        print(f"Tank pressure:      {depletion_state['P'] / PA_PER_PSI:.1f} psi")
        print(f"Tank temperature:   {depletion_state['T']:.2f} K")
        print(f"N2O remaining:      {depletion_state['m_ox']:.3f} kg")
        print(f"Fuel remaining:     {depletion_state['m_fuel']:.3f} kg")
    else:
        print()
        print("Liquid N2O depletion did not occur during the simulated interval.")

    V_fuel_final = m_fuel / rho_fuel
    print()
    print(f"Volume check:       {V_ox + V_fuel_final:.8f} m^3 (target {V_total:.8f} m^3)")

    # ========================================================
    # PLOTS
    # ========================================================
    plt.figure()
    plt.plot(times, thrusts / N_PER_LBF, label="Thrust")
    if depletion_state is not None:
        plt.axvline(depletion_state["time"], linestyle="--", label="Liquid N2O depleted")
    plt.xlabel("Time [s]")
    plt.ylabel("Thrust [lbf]")
    plt.legend()
    plt.grid()
    plt.show()

    plt.figure()
    plt.plot(times, tank_pressures / PA_PER_PSI, label="Tank")
    plt.plot(times, ox_manifold_pressure_history / PA_PER_PSI, label="Ox manifold")
    plt.plot(times, fuel_manifold_pressure_history / PA_PER_PSI, label="Fuel manifold")
    plt.plot(times, chamber_pressures / PA_PER_PSI, label="Chamber")
    plt.xlabel("Time [s]")
    plt.ylabel("Pressure [psi]")
    plt.legend()
    plt.grid()
    plt.show()

    plt.figure()
    plt.plot(times, temperatures)
    plt.xlabel("Time [s]")
    plt.ylabel("N2O Temperature [K]")
    plt.grid()
    plt.show()

    plt.figure()
    plt.plot(times, of_history)
    plt.xlabel("Time [s]")
    plt.ylabel("O/F")
    plt.grid()
    plt.show()

    plt.figure()
    plt.plot(times, plunger_positions / M_PER_IN)
    plt.xlabel("Time [s]")
    plt.ylabel("Plunger Travel [in]")
    plt.grid()
    plt.show()

    plt.figure()
    plt.plot(times, m_ox_liquid_history, label="Liquid N2O")
    plt.plot(times, m_ox_vapor_history, label="Vapor N2O")
    plt.xlabel("Time [s]")
    plt.ylabel("N2O Mass [kg]")
    plt.legend()
    plt.grid()
    plt.show()

    plt.figure()
    plt.plot(times, ox_feed_dp_history / PA_PER_PSI, label="Ox feedline")
    plt.plot(times, fuel_feed_dp_history / PA_PER_PSI, label="Fuel feedline")
    plt.xlabel("Time [s]")
    plt.ylabel("Feedline Pressure Drop [psi]")
    plt.legend()
    plt.grid()
    plt.show()

    plt.figure()
    plt.plot(times, ox_inj_dp_history / PA_PER_PSI, label="Ox injector")
    plt.plot(times, fuel_inj_dp_history / PA_PER_PSI, label="Fuel injector")
    plt.xlabel("Time [s]")
    plt.ylabel("Injector Pressure Drop [psi]")
    plt.legend()
    plt.grid()
    plt.show()

    plt.figure()
    plt.plot(times, 100.0 * ox_stiffness_history, label="Ox injector")
    plt.plot(times, 100.0 * fuel_stiffness_history, label="Fuel injector")
    plt.xlabel("Time [s]")
    plt.ylabel("Injector Stiffness [% of Pc]")
    plt.legend()
    plt.grid()
    plt.show()

    plt.figure()
    plt.plot(times, spi_mdot_history, label="SPI")
    plt.plot(times, hem_mdot_history, label="HEM")
    plt.plot(times, dyer_mdot_history, label="Dyer")
    plt.xlabel("Time [s]")
    plt.ylabel("N2O Mass Flow [kg/s]")
    plt.legend()
    plt.grid()
    plt.show()

    plt.figure()
    plt.plot(times, kappa_history)
    plt.xlabel("Time [s]")
    plt.ylabel("Dyer Kappa")
    plt.grid()
    plt.show()

    # ========================================================
    # CSV OUTPUT
    # ========================================================
    np.savetxt(
        "lumina_thrust_curve.csv",
        np.column_stack(
            (
                times,
                thrusts,
                chamber_pressures,
                tank_pressures,
                mdot_ox_history,
                mdot_fuel_history,
                of_history,
                ox_manifold_pressure_history,
                fuel_manifold_pressure_history,
                ox_inj_dp_history,
                fuel_inj_dp_history,
                ox_stiffness_history,
                fuel_stiffness_history,
            )
        ),
        delimiter=",",
        header=(
            "time_s,thrust_N,Pc_Pa,Ptank_Pa,mdot_ox_kg_s,mdot_fuel_kg_s,OF,"
            "Pman_ox_Pa,Pman_fuel_Pa,dp_inj_ox_Pa,dp_inj_fuel_Pa,"
            "stiffness_ox,stiffness_fuel"
        ),
        comments="",
    )


if __name__ == "__main__":
    main()

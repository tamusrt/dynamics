# =============================================================================
# LUMINA N2O / ETHANOL BLOWDOWN & ENGINE PERFORMANCE MODEL
# =============================================================================
#
# PURPOSE
# -------
# This program is a zero-dimensional engineering model developed to predict the
# approximate transient behavior of the Lumina N2O / ethanol liquid rocket
# propulsion system during blowdown operation.
#
# The model is intended primarily for:
#
#   - Preliminary engine performance prediction
#   - Thrust curve estimation
#   - Propellant ṁ estimation
#   - Tank pressure and temperature prediction
#   - O/F prediction
#   - Injector sizing and injector stiffness trades
#   - Feed system ΔP trades
#   - Estimation of liquid N2O depletion and subsequent vapor blowdown
#   - Comparison against cold-flow and hotfire test data
#   - Providing approximate inputs for vehicle trajectory and dynamics models
#
# The program is intended to be updated/calibrated using experimental data as
# Lumina testing progresses.
#
#
# MODEL OVERVIEW
# --------------
# The propulsion system is represented as a coupled lumped-parameter model.
# At each timestep, the program solves the oxidizer tank thermodynamic state,
# feed-system mass flow, chamber pressure, thrust, and propellant inventory.
#
# Major modeled components are:
#
#   1. N2O Tank Thermodynamics
#      - Nitrous oxide properties are obtained using CoolProp.
#      - During the liquid-containing portion of the burn, the tank is assumed
#        to remain in homogeneous thermodynamic equilibrium between saturated
#        liquid and saturated vapor.
#      - Tank temperature and vapor quality are solved from conservation of
#        mass, volume, and internal energy.
#      - Energy removed with discharged propellant is included.
#      - Expansion work (P*dV) is included.
#      - The model transitions to a single-phase vapor calculation after
#        liquid N2O depletion.
#
#   2. N2O Injector Flow
#      - Liquid/two-phase N2O injector flow is modeled using the Dyer / NHNE
#        non-equilibrium flashing-flow model.
#      - The Dyer formulation blends an incompressible SPI solution and a
#        homogeneous-equilibrium (HEM) solution.
#      - Injector geometry is represented using an effective CdA.
#
#   3. Fuel Flow
#      - Ethanol is treated as an incompressible liquid with constant density.
#      - Fuel feedline and injector pressure losses are modeled as lumped
#        hydraulic restrictions using effective CdA values.
#
#   4. Feed-System Losses
#      - Feedlines, valves, fittings, and other upstream restrictions are
#        represented by equivalent CdA values.
#
#   5. Chamber Pressure
#      - Chamber pressure is solved quasi-steadily at each timestep from:
#
#            Pc = mdot_total * c* / At
#
#        including the specified c* efficiency.
#      - Feed-system flow and chamber pressure are therefore solved as a
#        coupled system.
#
#   6. Thrust
#      - Thrust is calculated from:
#
#            F = Cf * Pc * At
#
#   7. Vapor-Phase N2O Flow
#      - After liquid N2O depletion, the remaining oxidizer is modeled as
#        single-phase vapor.
#      - Vapor discharge is currently approximated using a compressible-gas
#        orifice model with a lumped equivalent oxidizer flow-path CdA.
#
#   8. Injector Diagnostics
#      - Injector pressure drop, manifold pressure, and injector stiffness are
#        calculated during the liquid-fed portion of the burn.
#      - Injector stiffness is defined as:
#
#            stiffness = DeltaP_injector / Pc
#
#
# PRIMARY ASSUMPTIONS
# -------------------
# This model intentionally makes several simplifying assumptions:
#
#   - Zero-dimensional / lumped-parameter system representation
#   - Quasi-steady feed-system and chamber response at each timestep
#   - Homogeneous thermodynamic equilibrium inside the N2O tank while liquid
#     and vapor coexist
#   - Uniform tank pressure and temperature
#   - No explicit spatial modeling of tank stratification or slosh
#   - Constant ethanol density
#   - Lumped feedline and valve losses represented by CdA
#   - No transient fluid momentum in the feedlines
#   - No injector manifold volume dynamics
#   - No explicit combustion chamber filling or ignition transient
#   - No combustion instability modeling
#   - No detailed atomization, spray, mixing, or droplet combustion model
#   - No explicit heat transfer model between tank walls and N2O
#
#
# IMPORTANT LIMITATIONS
# ---------------------
#
# In particular:
#
#   - Dyer/NHNE predictions depend strongly on injector geometry, upstream
#     state, discharge coefficient, and the applicability of the model to the
#     actual injector.
#
#   - CdA values are effective parameters and may include behavior from
#     multiple real components. They should be updated when experimental
#     pressure-drop and mass-flow data become available.
#
#   - The current vapor-phase oxidizer model is lower fidelity than the
#     liquid/two-phase portion of the simulation.
#
#   - The current model does not explicitly resolve separate vapor-phase
#     oxidizer feedline and injector pressure drops after liquid depletion.
#
#   - c*, c* efficiency, and Cf may currently be treated as constants.
#     Therefore, predicted performance during large excursions in mixture
#     ratio, particularly the late vapor tail, is probably decently wrong
# 
#
#
# KNOWN ISSUES
# ---------------------
# 
#    - There is an issue with the pressures that in some cases will show a sharp spike/rise near gas phase. 
#      I havent't troubleshooted this yet, but it is non-physical and can safely be ignored.
#
#
#
# Mikey Carlino
# 09/27/2026


import numpy as np
import matplotlib.pyplot as plt

from CoolProp.CoolProp import PropsSI
from scipy.optimize import brentq

# ============================================================
# INPUTS
# ============================================================

# --- Simulation ---
dt = 0.2              # s
burn_time = 20        # sim time, s

# --- N2O tank ---
T0 = 291.14                   # K, initial N2O temperature
m_ox0 = 14.16              # kg, initial N2O mass
V_ox0 = 0.0188             # m^3, N2O tank internal volume
ox_phase = "two_phase"    #two_phase = liquid then gas blowndown

# --- Fuel ---
m_fuel0 = 5.72             # kg
rho_fuel = 789.0           # kg/m^3, approximate ethanol density

# --- Feedline effective flow areas (CdA) ---
CdA_ox_feed = 4.5e-05       # m^2
CdA_fuel_feed = 1.821412e-05     # m^2 

# --- Injector effective flow areas (CdA) ---
CdA_ox_inj = 4.2239e-5      # m^2 
CdA_fuel_inj = 1.5348e-5    # m^2 

# --- Engine ---
throat_diameter = 0.0296    # m
At = np.pi * throat_diameter**2 / 4

cstar = 1407.8           # m/s - replace with RPA value
cstar_eff = 0.85        
Cf = 1.4067              # replace with RPA/nozzle value

D_tank = 2.62 * 0.0254   # example: inches -> m
A_plunger = np.pi * D_tank**2 / 4

FLUID = "NitrousOxide"

# Equivalent oxidizer flow-path CdA for vapor flow
CdA_ox_gas = 1.0 / np.sqrt(
    1.0 / CdA_ox_feed**2
    +
    1.0 / CdA_ox_inj**2
)

def get_sat_props(T):

    P = PropsSI("P", "T", T, "Q", 0, FLUID)

    rho_l = PropsSI("D", "T", T, "Q", 0, FLUID)
    rho_v = PropsSI("D", "T", T, "Q", 1, FLUID)

    u_l = PropsSI("U", "T", T, "Q", 0, FLUID)
    u_v = PropsSI("U", "T", T, "Q", 1, FLUID)

    h_l = PropsSI("H", "T", T, "Q", 0, FLUID)
    s_l = PropsSI("S", "T", T, "Q", 0, FLUID)

    return P, rho_l, rho_v, u_l, u_v, h_l, s_l

def get_quality(T, m, V):
    P, rho_l, rho_v, u_l, u_v, h_l, s_l = get_sat_props(T)

    v = V / m
    v_l = 1.0 / rho_l
    v_v = 1.0 / rho_v

    x = (v - v_l) / (v_v - v_l)

    return x

P0, rho_l0, rho_v0, u_l0, u_v0, h_l0, s_l0 = get_sat_props(T0)
x0 = get_quality(T0, m_ox0, V_ox0)
u0 = (1.0 - x0) * u_l0 + x0 * u_v0
U_ox0 = m_ox0 * u0

print("Initial N2O state")
print(f"Tank pressure: {P0 / 6894.757:.1f} psi")
print(f"Temperature:   {T0:.2f} K")
print(f"Quality:       {x0:.5f}")
print(f"N2O mass:      {m_ox0:.3f} kg")

# ============================================================
# COMPRESSIBLE N2O VAPOR FLOW MODEL
# ============================================================

def ox_vapor_flow(Ptank, Ttank, Pc, cp, cv):

    if Ptank <= Pc:
        return 0.0

    gamma = cp / cv

    # Specific gas constant for N2O
    R_universal = PropsSI("GAS_CONSTANT", FLUID)
    molar_mass = PropsSI("MOLAR_MASS", FLUID)

    R = R_universal / molar_mass

    pressure_ratio = Pc / Ptank

    critical_ratio = (
        2.0 / (gamma + 1.0)
    )**(
        gamma / (gamma - 1.0)
    )

    # --------------------------------------------------------
    # CHOKED FLOW
    # --------------------------------------------------------

    if pressure_ratio <= critical_ratio:

        mdot = (
            CdA_ox_gas
            * Ptank
            * np.sqrt(
                gamma
                /
                (R * Ttank)
            )
            * (
                2.0 / (gamma + 1.0)
            )**(
                (gamma + 1.0)
                /
                (2.0 * (gamma - 1.0))
            )
        )

    # --------------------------------------------------------
    # UNCHOKED FLOW
    # --------------------------------------------------------

    else:

        mdot = (
            CdA_ox_gas
            * Ptank
            * np.sqrt(
                (
                    2.0 * gamma
                    /
                    (R * Ttank * (gamma - 1.0))
                )
                *
                (
                    pressure_ratio**(2.0 / gamma)
                    -
                    pressure_ratio**(
                        (gamma + 1.0) / gamma
                    )
                )
            )
        )

    return mdot

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

    # ========================================================
    # SPI
    # ========================================================

    mdot_spi = (
        CdA_ox_inj
        * np.sqrt(
            2.0
            * rho1
            * (P1 - P2)
        )
    )

    # ========================================================
    # HEM
    # ========================================================

    try:

        h2 = PropsSI(
            "H",
            "P", P2,
            "S", s1,
            FLUID
        )

        rho2 = PropsSI(
            "D",
            "P", P2,
            "S", s1,
            FLUID
        )

        dh = h1 - h2

        if dh <= 0.0:
            mdot_hem = 0.0

        else:
            mdot_hem = (
                CdA_ox_inj
                * rho2
                * np.sqrt(2.0 * dh)
            )

    except ValueError:

        mdot_hem = 0.0

    # ========================================================
    # DYER KAPPA
    # ========================================================

    if Pv <= P2:
        kappa = 1.0e6

    else:
        kappa = np.sqrt(
            (P1 - P2)
            /
            (Pv - P2)
        )

    # ========================================================
    # DYER BLEND
    # ========================================================

    mdot_dyer = (
        kappa * mdot_spi
        + mdot_hem
    ) / (1.0 + kappa)

    return mdot_dyer, mdot_spi, mdot_hem, kappa

# ============================================================
# TRANSIENT FEED SYSTEM MODEL
# ============================================================


def ox_flow(Ptank, Pc, rho_ox, h_ox, s_ox):

    if Ptank <= Pc:
        return 0.0

    def ox_flow_residual(mdot):

        # Feedline pressure loss
        dP_feed = (
            mdot**2
            /
            (
                2.0
                * rho_ox
                * CdA_ox_feed**2
            )
        )

        # Injector inlet pressure
        P1 = Ptank - dP_feed

        if P1 <= Pc:
            mdot_model = 0.0

        else:
            mdot_model, _, _, _ = dyer_mass_flow(
                P1,
                Pc,
                Ptank,
                rho_ox,
                h_ox,
                s_ox
            )

        return mdot - mdot_model

    # Maximum possible flow if all available pressure drop
    # were consumed by the feedline
    mdot_high = (
        CdA_ox_feed
        * np.sqrt(
            2.0
            * rho_ox
            * (Ptank - Pc)
        )
    )

    mdot_ox = brentq(
        ox_flow_residual,
        0.0,
        mdot_high
    )

    return mdot_ox


def fuel_flow(Pfuel, Pc):

    dP_total = Pfuel - Pc

    if dP_total <= 0:
        return 0.0

    # Feedline and injector are two restrictions in series:
    #
    # dP_total =
    # mdot^2/(2*rho*CdA_feed^2)
    # +
    # mdot^2/(2*rho*CdA_inj^2)

    resistance_term = (
        1.0 / CdA_fuel_feed**2
        +
        1.0 / CdA_fuel_inj**2
    )

    mdot_f = np.sqrt(
        2.0
        * rho_fuel
        * dP_total
        / resistance_term
    )

    return mdot_f

# ============================================================
# CHAMBER PRESSURE SOLVER
# ============================================================

def chamber_residual(
    Pc,
    Ptank,
    rho_ox,
    h_ox,
    s_ox
):

    mdot_ox = ox_flow(
        Ptank,
        Pc,
        rho_ox,
        h_ox,
        s_ox
    )

    mdot_f = fuel_flow(
        Ptank,
        Pc
    )

    mdot_total = mdot_ox + mdot_f

    Pc_from_engine = (
        mdot_total
        * (cstar * cstar_eff)
        / At
    )

    return Pc - Pc_from_engine


def solve_chamber_pressure(
    Ptank,
    rho_ox,
    h_ox,
    s_ox
):

    Pc_low = 1000.0
    Pc_high = 0.999 * Ptank

    return brentq(
        chamber_residual,
        Pc_low,
        Pc_high,
        args=(
            Ptank,
            rho_ox,
            h_ox,
            s_ox
        )
    )

def get_thrust(Pc):
    return Cf * Pc * At

# ============================================================
# VAPOR-PHASE CHAMBER PRESSURE SOLVER
# ============================================================

def chamber_residual_vapor(
    Pc,
    Ptank,
    Ttank,
    cp_ox,
    cv_ox
):

    mdot_ox = ox_vapor_flow(
        Ptank,
        Ttank,
        Pc,
        cp_ox,
        cv_ox
    )

    mdot_f = fuel_flow(
        Ptank,
        Pc
    )

    mdot_total = mdot_ox + mdot_f

    Pc_from_engine = (
        mdot_total
        * (cstar * cstar_eff)
        / At
    )

    return Pc - Pc_from_engine


def solve_chamber_pressure_vapor(
    Ptank,
    Ttank,
    cp_ox,
    cv_ox
):

    Pc_low = 1000.0
    Pc_high = 0.999 * Ptank

    return brentq(
        chamber_residual_vapor,
        Pc_low,
        Pc_high,
        args=(
            Ptank,
            Ttank,
            cp_ox,
            cv_ox
        )
    )

# ============================================================
# TANK STATE SOLVER
# ============================================================

def tank_energy_residual(T, m, U, V):

    P, rho_l, rho_v, u_l, u_v, h_l, _ = get_sat_props(T)

    v = V / m
    v_l = 1.0 / rho_l
    v_v = 1.0 / rho_v

    x = (v - v_l) / (v_v - v_l)

    u_mix = (1.0 - x) * u_l + x * u_v

    U_predicted = m * u_mix

    return U_predicted - U

def solve_tank_state(m, U, V, T_previous):

    T_low = max(183.0, T_previous - 10.0)
    T_high = min(309.4, T_previous + 2.0)

    try:
        T = brentq(
            tank_energy_residual,
            T_low,
            T_high,
            args=(m, U, V)
        )

    except ValueError:
        # Wider fallback bracket
        T = brentq(
            tank_energy_residual,
            183.0,
            309.4,
            args=(m, U, V)
        )

    P, rho_l, rho_v, u_l, u_v, h_l, _ = get_sat_props(T)

    x = get_quality(T, m, V)

    return T, P, x, rho_l, h_l

# ============================================================
# SINGLE-PHASE N2O VAPOR TANK SOLVER
# ============================================================

def solve_vapor_tank_state(m, U, V):

    rho = m / V
    u = U / m

    T = PropsSI(
        "T",
        "Dmass", rho,
        "Umass", u,
        FLUID
    )

    P = PropsSI(
        "P",
        "Dmass", rho,
        "Umass", u,
        FLUID
    )

    h = PropsSI(
        "Hmass",
        "Dmass", rho,
        "Umass", u,
        FLUID
    )

    s = PropsSI(
        "Smass",
        "Dmass", rho,
        "Umass", u,
        FLUID
    )

    cp = PropsSI(
        "Cpmass",
        "Dmass", rho,
        "Umass", u,
        FLUID
    )

    cv = PropsSI(
        "Cvmass",
        "Dmass", rho,
        "Umass", u,
        FLUID
    )

    return T, P, rho, h, s, cp, cv

# ============================================================
# INITIAL ENGINE OPERATING POINT CHECK
# ============================================================

P, rho_l, rho_v, u_l, u_v, h_l, s_l = get_sat_props(T0)


Pc = solve_chamber_pressure(
    P,
    rho_l,
    h_l,
    s_l
)

mdot_ox = ox_flow(
    P,
    Pc,
    rho_l,
    h_l,
    s_l
)

h_out_ox = h_l

mdot_f = fuel_flow(P, Pc)

F = get_thrust(Pc)

print()
print("Initial engine operating point")
print(f"Tank pressure:   {P / 6894.757:.1f} psi")
print(f"Chamber pressure:{Pc / 6894.757:.1f} psi")
print(f"Ox flow:         {mdot_ox:.3f} kg/s")
print(f"Fuel flow:       {mdot_f:.3f} kg/s")
print(f"O/F:             {mdot_ox / mdot_f:.3f}")
print(f"Thrust:          {F / 4.44822:.1f} lbf")

ox_feed_dp_initial = (
    mdot_ox**2
    /
    (
        2.0
        * rho_l
        * CdA_ox_feed**2
    )
)

fuel_feed_dp_initial = (
    mdot_f**2
    /
    (
        2.0
        * rho_fuel
        * CdA_fuel_feed**2
    )
)

print(f"Ox feed ΔP:      {ox_feed_dp_initial / 6894.757:.2f} psi")
print(f"Fuel feed ΔP:    {fuel_feed_dp_initial / 6894.757:.2f} psi")

V_fuel0 = m_fuel0 / rho_fuel
V_total = V_ox0 + V_fuel0

m_ox = m_ox0
m_fuel = m_fuel0
U_ox = U_ox0
T = T0
V_ox = V_ox0

times = []
tank_pressures = []
chamber_pressures = []
thrusts = []
mdot_ox_history = []
mdot_fuel_history = []
MR_history = []
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
dyer_mdot_history = []
spi_mdot_history = []
hem_mdot_history = []
kappa_history = []
ox_injector_pressure_history = []

# Injector diagnostics
ox_inj_dp_history = []
fuel_inj_dp_history = []
ox_stiffness_history = []
fuel_stiffness_history = []
ox_manifold_pressure_history = []
fuel_manifold_pressure_history = []

liquid_depletion_time = None

t = 0.0

while t <= burn_time + 1.0e-12:

    # ============================================================
    # BASIC DEPLETION CHECKS
    # ============================================================

    if m_ox <= 1.0e-6:
        print("N2O depleted.")
        break

    if m_fuel <= 1.0e-6:
        print("Fuel depleted.")
        break

    dt_step = dt

    # ============================================================
    # DETERMINE CURRENT OXIDIZER TANK STATE
    # ============================================================

    if ox_phase == "two_phase":

        P, rho_l, rho_v, u_l, u_v, h_l, s_l = get_sat_props(T)
        x = get_quality(T, m_ox, V_ox)

        # Resolve the end of the liquid phase more finely without
        # slowing down the whole simulation.
        if x > 0.95:
            dt_step = min(dt_step, 0.01)

    else:

        (
            T,
            P,
            rho_ox,
            h_ox,
            s_ox,
            cp_ox,
            cv_ox
        ) = solve_vapor_tank_state(
            m_ox,
            U_ox,
            V_ox
        )

        x = 1.0

    # ============================================================
    # ENGINE OPERATING POINT
    # ============================================================

    if ox_phase == "two_phase":

        Pc = solve_chamber_pressure(
            P,
            rho_l,
            h_l,
            s_l
        )

        mdot_ox = ox_flow(
            P,
            Pc,
            rho_l,
            h_l,
            s_l
        )

        # N2O leaves the tank as liquid during this regime.
        h_out_ox = h_l

    else:

        Pc = solve_chamber_pressure_vapor(
            P,
            T,
            cp_ox,
            cv_ox
        )

        mdot_ox = ox_vapor_flow(
            P,
            T,
            Pc,
            cp_ox,
            cv_ox
        )

        # N2O leaves the tank as vapor during this regime.
        h_out_ox = h_ox

    mdot_f = fuel_flow(P, Pc)

    # Protect against division by zero late in the tail.
    if mdot_f > 0.0:
        MR = mdot_ox / mdot_f
    else:
        MR = np.nan

    F = get_thrust(Pc)

    # ============================================================
    # FLOW DIAGNOSTICS
    # ============================================================

    if ox_phase == "two_phase":

        ox_feed_dp = (
            mdot_ox**2
            /
            (
                2.0
                * rho_l
                * CdA_ox_feed**2
            )
        )

        P1_ox = P - ox_feed_dp

        mdot_dyer_check, mdot_spi, mdot_hem, kappa = dyer_mass_flow(
            P1_ox,
            Pc,
            P,
            rho_l,
            h_l,
            s_l
        )

        # Oxidizer injector diagnostics.  P1_ox is the modeled
        # oxidizer manifold / injector-inlet pressure.
        Pman_ox = P1_ox
        ox_inj_dp = Pman_ox - Pc
        ox_stiffness = ox_inj_dp / Pc

    else:

        # The current vapor model lumps feedline + injector into
        # CdA_ox_gas, so a separate vapor feedline delta-P and
        # manifold pressure are not defined in this version.
        ox_feed_dp = np.nan
        P1_ox = np.nan
        Pman_ox = np.nan
        ox_inj_dp = np.nan
        ox_stiffness = np.nan

        # Dyer / SPI / HEM no longer apply after liquid depletion.
        mdot_dyer_check = np.nan
        mdot_spi = np.nan
        mdot_hem = np.nan
        kappa = np.nan

    fuel_feed_dp = (
        mdot_f**2
        /
        (
            2.0
            * rho_fuel
            * CdA_fuel_feed**2
        )
    )

    # Fuel injector diagnostics.  The fuel side is treated as
    # incompressible through both the feed system and injector.
    fuel_inj_dp = (
        mdot_f**2
        /
        (
            2.0
            * rho_fuel
            * CdA_fuel_inj**2
        )
    )

    Pman_fuel = P - fuel_feed_dp
    fuel_stiffness = fuel_inj_dp / Pc

    # ============================================================
    # CURRENT PROPELLANT / PLUNGER STATE
    # ============================================================

    V_fuel = m_fuel / rho_fuel

    plunger_travel = (
        V_ox - V_ox0
    ) / A_plunger

    if ox_phase == "two_phase":
        m_vapor = x * m_ox
        m_liquid = (1.0 - x) * m_ox
    else:
        m_vapor = m_ox
        m_liquid = 0.0

    # ============================================================
    # SAVE CURRENT STATE
    # ============================================================

    dyer_mdot_history.append(mdot_dyer_check)
    spi_mdot_history.append(mdot_spi)
    hem_mdot_history.append(mdot_hem)
    kappa_history.append(kappa)
    ox_injector_pressure_history.append(P1_ox)
    ox_feed_dp_history.append(ox_feed_dp)
    fuel_feed_dp_history.append(fuel_feed_dp)

    ox_inj_dp_history.append(ox_inj_dp)
    fuel_inj_dp_history.append(fuel_inj_dp)
    ox_stiffness_history.append(ox_stiffness)
    fuel_stiffness_history.append(fuel_stiffness)
    ox_manifold_pressure_history.append(Pman_ox)
    fuel_manifold_pressure_history.append(Pman_fuel)

    m_ox_history.append(m_ox)
    m_ox_liquid_history.append(m_liquid)
    m_ox_vapor_history.append(m_vapor)

    V_ox_history.append(V_ox)
    V_fuel_history.append(V_fuel)

    times.append(t)
    tank_pressures.append(P)
    chamber_pressures.append(Pc)
    thrusts.append(F)

    mdot_ox_history.append(mdot_ox)
    mdot_fuel_history.append(mdot_f)

    MR_history.append(MR)
    temperatures.append(T)
    qualities.append(x)
    plunger_positions.append(plunger_travel)

    # Current state at the requested final time has now been saved.
    if t >= burn_time - 1.0e-12:
        break

    # Do not integrate past the requested burn time.
    dt_step = min(dt_step, burn_time - t)

    # ============================================================
    # CANDIDATE STATE UPDATE
    # ============================================================

    m_ox_trial = m_ox - mdot_ox * dt_step
    m_fuel_trial = m_fuel - mdot_f * dt_step

    # Avoid sending nonphysical negative masses into CoolProp.
    if m_ox_trial <= 0.0:
        print("N2O depleted during timestep.")
        break

    if m_fuel_trial <= 0.0:
        print("Fuel depleted during timestep.")
        break

    V_ox_trial = V_total - m_fuel_trial / rho_fuel
    dV_ox_trial = V_ox_trial - V_ox

    U_ox_trial = (
        U_ox
        - mdot_ox * h_out_ox * dt_step
        - P * dV_ox_trial
    )

    # ============================================================
    # ACCEPT STATE OR LOCATE LIQUID-DEPLETION EVENT
    # ============================================================

    if ox_phase == "two_phase":

        (
            T_try,
            P_try,
            x_try,
            rho_l_try,
            h_l_try
        ) = solve_tank_state(
            m_ox_trial,
            U_ox_trial,
            V_ox_trial,
            T
        )

        if x_try < 1.0:

            # The full timestep remains inside the two-phase region.
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

            # ----------------------------------------------------
            # Liquid depletion occurred somewhere inside this
            # timestep. Locate x = 1 so that the vapor model starts
            # from the saturated-vapor boundary instead of forcing
            # a two-phase state to become single-phase vapor.
            # ----------------------------------------------------

            def depletion_residual(frac):

                dt_sub = frac * dt_step

                m_ox_sub = m_ox - mdot_ox * dt_sub
                m_fuel_sub = m_fuel - mdot_f * dt_sub

                V_ox_sub = V_total - m_fuel_sub / rho_fuel
                dV_ox_sub = V_ox_sub - V_ox

                U_ox_sub = (
                    U_ox
                    - mdot_ox * h_out_ox * dt_sub
                    - P * dV_ox_sub
                )

                _, _, x_sub, _, _ = solve_tank_state(
                    m_ox_sub,
                    U_ox_sub,
                    V_ox_sub,
                    T
                )

                return x_sub - 1.0

            frac_depletion = brentq(
                depletion_residual,
                0.0,
                1.0
            )

            dt_depletion = frac_depletion * dt_step

            m_ox = m_ox - mdot_ox * dt_depletion
            m_fuel = m_fuel - mdot_f * dt_depletion

            V_ox_new = V_total - m_fuel / rho_fuel
            dV_ox = V_ox_new - V_ox

            U_ox = (
                U_ox
                - mdot_ox * h_out_ox * dt_depletion
                - P * dV_ox
            )

            V_ox = V_ox_new

            # Solve exactly on the saturated-vapor boundary first.
            (
                T,
                P,
                x,
                rho_l,
                h_l
            ) = solve_tank_state(
                m_ox,
                U_ox,
                V_ox,
                T
            )

            t += dt_depletion
            liquid_depletion_time = t

            print(
                f"Liquid N2O depleted at t = "
                f"{liquid_depletion_time:.3f} s"
            )

            # Save the saturated-vapor boundary state so we can
            # verify that the single-phase solver is continuous.
            T_transition_sat = T
            P_transition_sat = P

            # Now switch models. The same conserved m, U, and V are
            # used, so T and P should remain continuous to numerical
            # precision across the transition.
            ox_phase = "vapor"
            x = 1.0

            (
                T,
                P,
                rho_ox,
                h_ox,
                s_ox,
                cp_ox,
                cv_ox
            ) = solve_vapor_tank_state(
                m_ox,
                U_ox,
                V_ox
            )

            print(
                f"Transition continuity: "
                f"dT = {T - T_transition_sat:+.6f} K, "
                f"dP = {(P - P_transition_sat) / 6894.757:+.6f} psi"
            )

    else:

        # Single-phase vapor blowdown.
        m_ox = m_ox_trial
        m_fuel = m_fuel_trial
        V_ox = V_ox_trial
        U_ox = U_ox_trial

        (
            T,
            P,
            rho_ox,
            h_ox,
            s_ox,
            cp_ox,
            cv_ox
        ) = solve_vapor_tank_state(
            m_ox,
            U_ox,
            V_ox
        )

        t += dt_step


times = np.array(times)

tank_pressures = np.array(tank_pressures)
chamber_pressures = np.array(chamber_pressures)
thrusts = np.array(thrusts)

mdot_ox_history = np.array(mdot_ox_history)
mdot_fuel_history = np.array(mdot_fuel_history)

MR_history = np.array(MR_history)
temperatures = np.array(temperatures)
qualities = np.array(qualities)

total_impulse_Ns = np.trapz(thrusts, times)
total_impulse_lbfs = total_impulse_Ns / 4.44822

m_ox_history = np.array(m_ox_history)
m_ox_liquid_history = np.array(m_ox_liquid_history)
m_ox_vapor_history = np.array(m_ox_vapor_history)

ox_feed_dp_history = np.array(ox_feed_dp_history)
fuel_feed_dp_history = np.array(fuel_feed_dp_history)

plunger_positions = np.array(plunger_positions)

V_ox_history = np.array(V_ox_history)
V_fuel_history = np.array(V_fuel_history)

dyer_mdot_history = np.array(dyer_mdot_history)
spi_mdot_history = np.array(spi_mdot_history)
hem_mdot_history = np.array(hem_mdot_history)
kappa_history = np.array(kappa_history)
ox_injector_pressure_history = np.array(
    ox_injector_pressure_history
)

ox_inj_dp_history = np.array(ox_inj_dp_history)
fuel_inj_dp_history = np.array(fuel_inj_dp_history)
ox_stiffness_history = np.array(ox_stiffness_history)
fuel_stiffness_history = np.array(fuel_stiffness_history)
ox_manifold_pressure_history = np.array(ox_manifold_pressure_history)
fuel_manifold_pressure_history = np.array(fuel_manifold_pressure_history)

print()
print("Engine Performance")
print(f"Total impulse: {total_impulse_Ns:.1f} N-s")
print(f"Total impulse: {total_impulse_lbfs:.1f} lbf-s")

actual_burn_time = times[-1] - times[0]
average_thrust = total_impulse_Ns / actual_burn_time
print(f"Average thrust: {average_thrust:.1f} N")
print(f"Average thrust: {average_thrust / 4.44822:.1f} lbf")

plt.figure()
plt.plot(times, thrusts / 4.44822, label="Thrust")

if liquid_depletion_time is not None:
    plt.axvline(
        liquid_depletion_time,
        linestyle="--",
        label="Liquid N2O depleted"
    )

plt.xlabel("Time [s]")
plt.ylabel("Thrust [lbf]")
plt.legend()
plt.grid()
plt.show()

plt.figure()
plt.plot(times, tank_pressures / 6894.757, label="Tank")
plt.plot(
    times,
    ox_manifold_pressure_history / 6894.757,
    label="Ox manifold"
)
plt.plot(
    times,
    fuel_manifold_pressure_history / 6894.757,
    label="Fuel manifold"
)
plt.plot(times, chamber_pressures / 6894.757, label="Chamber")
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
plt.plot(times, MR_history)
plt.xlabel("Time [s]")
plt.ylabel("O/F")
plt.grid()
plt.show()

plt.figure()
plt.plot(
    times,
    np.array(plunger_positions) / 0.0254
)
plt.xlabel("Time [s]")
plt.ylabel("Plunger Travel [in]")
plt.grid()
plt.show()

V_fuel_final = m_fuel / rho_fuel

print(
    "Volume check:",
    V_ox + V_fuel_final,
    V_total
)

plt.figure()
plt.plot(times, m_ox_liquid_history, label="Liquid N2O")
plt.plot(times, m_ox_vapor_history, label="Vapor N2O")
plt.xlabel("Time [s]")
plt.ylabel("N2O Mass [kg]")
plt.legend()
plt.grid()
plt.show()

plt.figure()
plt.plot(
    times,
    ox_feed_dp_history / 6894.757,
    label="Ox feedline"
)
plt.plot(
    times,
    fuel_feed_dp_history / 6894.757,
    label="Fuel feedline"
)
plt.xlabel("Time [s]")
plt.ylabel("Feedline Pressure Drop [psi]")
plt.legend()
plt.grid()
plt.show()

# Injector pressure-drop history.  Ox values stop at liquid
# depletion because the current vapor model lumps the oxidizer
# feedline and injector into one equivalent gas CdA.
plt.figure()
plt.plot(
    times,
    ox_inj_dp_history / 6894.757,
    label="Ox injector"
)
plt.plot(
    times,
    fuel_inj_dp_history / 6894.757,
    label="Fuel injector"
)
plt.xlabel("Time [s]")
plt.ylabel("Injector Pressure Drop [psi]")
plt.legend()
plt.grid()
plt.show()

# Injector stiffness is defined here as injector delta-P / Pc.
plt.figure()
plt.plot(
    times,
    100.0 * ox_stiffness_history,
    label="Ox injector"
)
plt.plot(
    times,
    100.0 * fuel_stiffness_history,
    label="Fuel injector"
)
plt.xlabel("Time [s]")
plt.ylabel("Injector Stiffness [% of Pc]")
plt.legend()
plt.grid()
plt.show()

plt.figure()
plt.plot(
    times,
    spi_mdot_history,
    label="SPI"
)
plt.plot(
    times,
    hem_mdot_history,
    label="HEM"
)
plt.plot(
    times,
    dyer_mdot_history,
    label="Dyer"
)
plt.xlabel("Time [s]")
plt.ylabel("N2O Mass Flow [kg/s]")
plt.legend()
plt.grid()
plt.show()

plt.figure()
plt.plot(
    times,
    kappa_history
)

plt.xlabel("Time [s]")
plt.ylabel("Dyer Kappa")
plt.grid()
plt.show()

print()
print("===== PERFORMANCE SUMMARY =====")
print(f"Burn time:          {times[-1]:.2f} s")
print(f"Initial thrust:     {thrusts[0] / 4.44822:.1f} lbf")
print(f"Final thrust:       {thrusts[-1] / 4.44822:.1f} lbf")
print(f"Average thrust:     {average_thrust / 4.44822:.1f} lbf")
print(f"Total impulse:      {total_impulse_lbfs:.1f} lbf-s")

print()
print(f"Initial Ptank:      {tank_pressures[0] / 6894.757:.1f} psi")
print(f"Final Ptank:        {tank_pressures[-1] / 6894.757:.1f} psi")
print(f"Initial Pc:         {chamber_pressures[0] / 6894.757:.1f} psi")
print(f"Final Pc:           {chamber_pressures[-1] / 6894.757:.1f} psi")

print()
print(f"Initial O/F:        {MR_history[0]:.3f}")
print(f"Final O/F:          {MR_history[-1]:.3f}")
print(f"N2O consumed:       {m_ox0 - m_ox:.3f} kg")
print(f"Fuel consumed:      {m_fuel0 - m_fuel:.3f} kg")

print()
print("===== INITIAL INJECTOR PERFORMANCE =====")
print(f"Ox manifold pressure:   {ox_manifold_pressure_history[0] / 6894.757:.1f} psi")
print(f"Ox injector dP:         {ox_inj_dp_history[0] / 6894.757:.1f} psi")
print(f"Ox injector stiffness:  {100.0 * ox_stiffness_history[0]:.1f} %")
print()
print(f"Fuel manifold pressure: {fuel_manifold_pressure_history[0] / 6894.757:.1f} psi")
print(f"Fuel injector dP:       {fuel_inj_dp_history[0] / 6894.757:.1f} psi")
print(f"Fuel injector stiffness:{100.0 * fuel_stiffness_history[0]:.1f} %")

np.savetxt(
    "lumina_thrust_curve.csv",
    np.column_stack((
        times,
        thrusts,
        chamber_pressures,
        tank_pressures,
        mdot_ox_history,
        mdot_fuel_history,
        MR_history,
        ox_manifold_pressure_history,
        fuel_manifold_pressure_history,
        ox_inj_dp_history,
        fuel_inj_dp_history,
        ox_stiffness_history,
        fuel_stiffness_history
    )),
    delimiter=",",
    header=(
        "time_s,thrust_N,Pc_Pa,Ptank_Pa,mdot_ox_kg_s,mdot_fuel_kg_s,OF,"
        "Pman_ox_Pa,Pman_fuel_Pa,dp_inj_ox_Pa,dp_inj_fuel_Pa,"
        "stiffness_ox,stiffness_fuel"
    ),
    comments=""
)

print()
print("===== LIQUID N2O DEPLETION =====")
print(f"Time:               {liquid_depletion_time:.3f} s")
print(f"Tank pressure:      {P / 6894.757:.1f} psi")
print(f"Tank temperature:   {T:.2f} K")
print(f"N2O remaining:      {m_ox:.3f} kg")
print(f"Fuel remaining:     {m_fuel:.3f} kg")

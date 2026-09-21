import numpy as np
import matplotlib.pyplot as plt


# ============================================================
# INPUTS
# ============================================================

# Time
dt = 0.1                   # Time interval [s]
burn_time = 10.0           # Total burn time [s]


# ----------------------------
# Top Plumbing
# ----------------------------

top_plumbing_mass = 1.08     # Empty mass [kg]
top_plumbing_length = 4.923  # Length [in]


# ----------------------------
# Oxidizer Tank
# ----------------------------

oxidizer_tank_mass = 32.25042 - 18.1437    # Empty tank mass [kg]
oxidizer_tank_length = 57.56              # Length [in]
oxidizer_mass_initial = 18.1437            # Initial oxidizer mass [kg]


# ----------------------------
# Plumbing between Ox Tank
# and Combustion Chamber
# ----------------------------

inter_plumbing_mass = 1.91   # Empty mass [kg]
inter_plumbing_length = 14   # Length [in]


# ----------------------------
# Combustion Chamber
# ----------------------------

combustion_chamber_mass = 7.574993 - 3.99161     # Empty mass [kg]
combustion_chamber_length = 24.5                  # Length [in]
fuel_mass_initial = 3.99161                       # Initial fuel mass [kg]


# ============================================================
# COMPONENT LOCATIONS
# ============================================================

# Everything is measured from the tip/top of the engine.

top_plumbing_start = 0.0

oxidizer_tank_start = (
    top_plumbing_start
    + top_plumbing_length
)

inter_plumbing_start = (
    oxidizer_tank_start
    + oxidizer_tank_length
)

combustion_chamber_start = (
    inter_plumbing_start
    + inter_plumbing_length
)


# Each component's dry CG is at its center

top_plumbing_cg = (
    top_plumbing_start
    + top_plumbing_length / 2
)

oxidizer_tank_cg = (
    oxidizer_tank_start
    + oxidizer_tank_length / 2
)

inter_plumbing_cg = (
    inter_plumbing_start
    + inter_plumbing_length / 2
)

combustion_chamber_cg = (
    combustion_chamber_start
    + combustion_chamber_length / 2
)


# Bottom of oxidizer tank
oxidizer_tank_bottom = (
    oxidizer_tank_start
    + oxidizer_tank_length
)


# ============================================================
# TIME ARRAY
# ============================================================

time = np.arange(0, burn_time + dt, dt)

# Make sure the final point is exactly burn_time
if time[-1] > burn_time:
    time[-1] = burn_time


# ============================================================
# CREATE OUTPUT ARRAYS
# ============================================================

# Whole-engine arrays
cg_array = np.zeros(len(time))
mass_array = np.zeros(len(time))

# Oxidizer tank + oxidizer arrays
oxidizer_combined_cg_array = np.zeros(len(time))
oxidizer_mass_array = np.zeros(len(time))
oxidizer_combined_mass_array = np.zeros(len(time))


# ============================================================
# CALCULATE CG AND MASS AT EACH TIME
# ============================================================

for i, t in enumerate(time):

    # Fraction of burn completed
    burn_fraction = t / burn_time


    # --------------------------------------------------------
    # REMAINING PROPELLANT
    # --------------------------------------------------------

    # Linear depletion

    oxidizer_mass = (
        oxidizer_mass_initial
        * (1 - burn_fraction)
    )

    fuel_mass = (
        fuel_mass_initial
        * (1 - burn_fraction)
    )


    # --------------------------------------------------------
    # OXIDIZER CG
    # --------------------------------------------------------

    # Initially the oxidizer fills the tank,
    # so its CG is at the center of the tank.

    # As it drains, its CG moves toward the bottom.

    oxidizer_cg = (
        oxidizer_tank_cg
        + burn_fraction
        * (
            oxidizer_tank_bottom
            - oxidizer_tank_cg
        )
    )


    # --------------------------------------------------------
    # OXIDIZER TANK + OXIDIZER CG
    # --------------------------------------------------------

    # Measure distance from the TOP of the oxidizer tank.

    # Dry tank CG measured from top of oxidizer tank
    oxidizer_tank_dry_cg_from_top = (
        oxidizer_tank_length / 2
    )

    # Oxidizer CG measured from top of oxidizer tank
    oxidizer_cg_from_top = (
        oxidizer_cg - oxidizer_tank_start
    )

    # Combined mass of empty tank + remaining oxidizer
    oxidizer_combined_mass = (
        oxidizer_tank_mass
        + oxidizer_mass
    )

    # Combined CG of tank + remaining oxidizer
    oxidizer_combined_cg = (
        oxidizer_tank_mass
        * oxidizer_tank_dry_cg_from_top
        + oxidizer_mass
        * oxidizer_cg_from_top
    ) / oxidizer_combined_mass


    # Store oxidizer tank results
    oxidizer_combined_cg_array[i] = oxidizer_combined_cg
    oxidizer_mass_array[i] = oxidizer_mass
    oxidizer_combined_mass_array[i] = oxidizer_combined_mass


    # --------------------------------------------------------
    # FUEL CG
    # --------------------------------------------------------

    # Fuel CG stays at the center of the combustion chamber.

    fuel_cg = combustion_chamber_cg


    # --------------------------------------------------------
    # TOTAL MASS
    # --------------------------------------------------------

    total_mass = (
        top_plumbing_mass
        + oxidizer_tank_mass
        + inter_plumbing_mass
        + combustion_chamber_mass
        + oxidizer_mass
        + fuel_mass
    )


    # --------------------------------------------------------
    # TOTAL MASS MOMENT
    # --------------------------------------------------------

    total_moment = (

        top_plumbing_mass
        * top_plumbing_cg

        + oxidizer_tank_mass
        * oxidizer_tank_cg

        + inter_plumbing_mass
        * inter_plumbing_cg

        + combustion_chamber_mass
        * combustion_chamber_cg

        + oxidizer_mass
        * oxidizer_cg

        + fuel_mass
        * fuel_cg
    )


    # --------------------------------------------------------
    # WHOLE ENGINE CG
    # --------------------------------------------------------

    cg = total_moment / total_mass


    # Store whole-engine results
    cg_array[i] = cg
    mass_array[i] = total_mass


# ============================================================
# RESULTS
# ============================================================

print("Time array [s]:")
print(time)

print("\nWhole Engine CG array [in]:")
print(cg_array)

print("\nWhole Engine Mass array [kg]:")
print(mass_array)

print("\nOxidizer Tank + Oxidizer CG array [in from TOP of tank]:")
print(oxidizer_combined_cg_array)

print("\nOxidizer mass array [kg]:")
print(oxidizer_mass_array)

print("\nOxidizer Tank + Oxidizer combined mass array [kg]:")
print(oxidizer_combined_mass_array)


# ============================================================
# OXIDIZER TANK + OXIDIZER CG PLOT
# ============================================================

plt.figure(figsize=(9, 5))

plt.plot(
    time,
    oxidizer_combined_cg_array,
    label="Oxidizer Tank + Oxidizer CG"
)

plt.xlabel("Time [s]")
plt.ylabel("CG Distance from Top of Oxidizer Tank [in]")
plt.title("Oxidizer Tank + Oxidizer CG vs. Time")
plt.grid(True)
plt.legend()

plt.tight_layout()
plt.show()


# ============================================================
# WHOLE ENGINE CG PLOT
# ============================================================

plt.figure(figsize=(9, 5))

plt.plot(
    time,
    cg_array,
    label="Whole Engine CG"
)

plt.xlabel("Time [s]")
plt.ylabel("CG from Top of Engine [in]")
plt.title("Whole Engine CG vs. Time")
plt.grid(True)
plt.legend()

plt.tight_layout()
plt.show()
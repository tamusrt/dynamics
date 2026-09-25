import numpy as np
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# ============================================================
# FILE INPUTS
# ============================================================



# ============================================================
# UNIT CONVERSIONS
# ============================================================

IN_TO_MM = 25.4
KG_TO_G = 1000.0
G0 = 9.80665


# ============================================================
# READ ENGINE INPUT FILE
# ============================================================

def read_input_file(filename):

    values = {}

    with open(filename, "r") as file:

        for line in file:

            line = line.strip()

            # Ignore blank lines
            if not line:
                continue

            # Ignore comments
            if line.startswith("#"):
                continue

            # Require key=value format
            if "=" not in line:
                continue

            key, value = line.split("=", 1)

            key = key.strip()
            value = value.strip()

            # Remove inline comments
            if "#" in value:
                value = value.split("#", 1)[0].strip()

            # Strings
            if key in [
                "rse_code",
                "rse_mfg",
                "rse_Type",
                "rse_delays",
                "rse_comments"
            ]:
                values[key] = value

            # Expressions / numbers
            else:
                try:
                    values[key] = float(value)
                except ValueError:
                    # If it isn't numeric, treat as string
                    values[key] = value

    return values


input_filename = input("Enter engine input filename: ").strip()
INPUT_FILE = os.path.join(SCRIPT_DIR, input_filename)
inputs = read_input_file(INPUT_FILE)


# ============================================================
# GET INPUT VARIABLES
# ============================================================

# ------------------------------------------------------------
# Custom CG / mass depletion time
# ------------------------------------------------------------

cg_mass_burn_time = inputs["cg_mass_burn_time"]


# ------------------------------------------------------------
# RSE metadata
# ------------------------------------------------------------

rse_code = inputs["rse_code"]
rse_mfg = inputs["rse_mfg"]
rse_Type = inputs["rse_Type"]
rse_delays = inputs["rse_delays"]

rse_dia_mm = inputs["rse_dia_mm"]
rse_throatDia_mm = inputs["rse_throatDia_mm"]
rse_exitDia_mm = inputs["rse_exitDia_mm"]

rse_comments = inputs["rse_comments"]


# ------------------------------------------------------------
# Component properties
# ------------------------------------------------------------

top_plumbing_mass = inputs["top_plumbing_mass"]
top_plumbing_length = inputs["top_plumbing_length"]

oxidizer_tank_mass = inputs["oxidizer_tank_mass"]
oxidizer_tank_length = inputs["oxidizer_tank_length"]
oxidizer_mass_initial = inputs["oxidizer_mass_initial"]

inter_plumbing_mass = inputs["inter_plumbing_mass"]
inter_plumbing_length = inputs["inter_plumbing_length"]

combustion_chamber_mass = inputs["combustion_chamber_mass"]
combustion_chamber_length = inputs["combustion_chamber_length"]
fuel_mass_initial = inputs["fuel_mass_initial"]

# ============================================================
# READ THRUST CURVE
# ============================================================

def read_thrust_curve(filename):

    time_values = []
    thrust_values = []

    with open(filename, "r") as file:

        for line in file:

            line = line.strip()

            # Skip blank lines
            if not line:
                continue

            # Skip comments
            if line.startswith("#"):
                continue

            parts = line.split()

            # We need at least two columns:
            # time   thrust
            if len(parts) < 2:
                continue

            try:
                t = float(parts[0])
                thrust = float(parts[1])
            except ValueError:
                # This skips the "CC 152 ..." header
                continue

            time_values.append(t)
            thrust_values.append(thrust)

    if len(time_values) == 0:
        raise ValueError("No thrust data found in thrust curve file.")

    return (
        np.array(time_values, dtype=float),
        np.array(thrust_values, dtype=float)
    )


thrust_filename = input("Enter thrust curve filename: ").strip()
THRUST_FILE = os.path.join(SCRIPT_DIR, thrust_filename)
time, thrust_array_N = read_thrust_curve(THRUST_FILE)


# ============================================================
# VALIDATE THRUST CURVE
# ============================================================

if len(time) < 2:
    raise ValueError("Thrust curve must contain at least two points.")

if np.any(np.diff(time) <= 0):
    raise ValueError(
        "Thrust-curve times must be strictly increasing."
    )


# ============================================================
# DETERMINE THRUST-CURVE TIME STEP
# ============================================================

time_steps = np.diff(time)



# ============================================================
# IMPORTANT:
#
# The thrust curve determines the RSE time points.
#
# The custom cg_mass_burn_time determines when propellant
# depletion stops.
# ============================================================

if cg_mass_burn_time > time[-1]:

    raise ValueError(
        "cg_mass_burn_time is longer than the thrust curve."
    )


# ============================================================
# COMPONENT LOCATIONS
# ============================================================

# Everything is measured from the top/tip of the engine.
# Locations are calculated in inches.

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


# Overall engine length

engine_length_in = (
    combustion_chamber_start
    + combustion_chamber_length
)


# ============================================================
# COMPONENT CG LOCATIONS
# ============================================================

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
# FIND THE CG/MASS STOP POINT
# ============================================================

# Find the thrust-curve time point closest to the requested
# CG/mass burn time.

cg_mass_stop_index = np.searchsorted(
    time,
    cg_mass_burn_time,
    side="right"
) - 1


if cg_mass_stop_index < 0:
    raise ValueError(
        "cg_mass_burn_time occurs before the first thrust point."
    )


cg_mass_stop_time = time[cg_mass_stop_index]




# ============================================================
# OUTPUT ARRAYS
# ============================================================

cg_array_mm = np.zeros(len(time))
mass_array_g = np.zeros(len(time))


# ============================================================
# CALCULATE CG AND MASS
# ============================================================

for i, t in enumerate(time):

    # --------------------------------------------------------
    # BEFORE / DURING CUSTOM CG-MASS BURN
    # --------------------------------------------------------

    if t <= cg_mass_stop_time:

        burn_fraction = t / cg_mass_stop_time

        # ----------------------------------------------------
        # REMAINING PROPELLANT
        # ----------------------------------------------------

        oxidizer_mass = (
            oxidizer_mass_initial
            * (1 - burn_fraction)
        )

        fuel_mass = (
            fuel_mass_initial
            * (1 - burn_fraction)
        )

        # ----------------------------------------------------
        # OXIDIZER CG
        # ----------------------------------------------------

        oxidizer_cg = (
            oxidizer_tank_cg
            + burn_fraction
            * (
                oxidizer_tank_bottom
                - oxidizer_tank_cg
            )
        )

        # ----------------------------------------------------
        # FUEL CG
        # ----------------------------------------------------

        fuel_cg = combustion_chamber_cg

        # ----------------------------------------------------
        # TOTAL MASS
        # ----------------------------------------------------

        total_mass_kg = (

            top_plumbing_mass

            + oxidizer_tank_mass

            + inter_plumbing_mass

            + combustion_chamber_mass

            + oxidizer_mass

            + fuel_mass
        )

        # ----------------------------------------------------
        # TOTAL MASS MOMENT
        # ----------------------------------------------------

        total_moment = (

            top_plumbing_mass
            * top_plumbing_cg

            +

            oxidizer_tank_mass
            * oxidizer_tank_cg

            +

            inter_plumbing_mass
            * inter_plumbing_cg

            +

            combustion_chamber_mass
            * combustion_chamber_cg

            +

            oxidizer_mass
            * oxidizer_cg

            +

            fuel_mass
            * fuel_cg
        )

        # ----------------------------------------------------
        # WHOLE ENGINE CG
        # ----------------------------------------------------

        cg_in = (
            total_moment
            / total_mass_kg
        )

        # Store values

        cg_array_mm[i] = cg_in * IN_TO_MM
        mass_array_g[i] = total_mass_kg * KG_TO_G


    # --------------------------------------------------------
    # AFTER CUSTOM CG-MASS BURN
    # --------------------------------------------------------
    #
    # DO NOT continue depleting propellant.
    #
    # Hold CG and mass constant at the last calculated value.
    # --------------------------------------------------------

    else:

        cg_array_mm[i] = cg_array_mm[cg_mass_stop_index]

        mass_array_g[i] = mass_array_g[cg_mass_stop_index]


# ============================================================
# WET / EMPTY VALUES
# ============================================================

wet_cg_mm = cg_array_mm[0]
wet_mass_g = mass_array_g[0]

# "Empty" here means the mass after the CUSTOM
# CG/mass burn period, NOT necessarily the final
# thrust-curve time.

empty_cg_mm = cg_array_mm[cg_mass_stop_index]
empty_mass_g = mass_array_g[cg_mass_stop_index]


propellant_mass_g = (
    wet_mass_g
    - empty_mass_g
)


mass_frac = (
    empty_mass_g
    / wet_mass_g
)


# ============================================================
# THRUST CURVE CALCULATIONS
# ============================================================

# Total impulse from actual thrust curve

rse_Itot = np.trapezoid(
    thrust_array_N,
    time
)


# Average thrust over the complete thrust-curve duration

rse_avgThrust = (
    rse_Itot
    / (time[-1] - time[0])
)


# Peak thrust

rse_peakThrust = np.max(
    thrust_array_N
)


# Actual thrust-curve duration

rse_burn_time = (
    time[-1] - time[0]
)


# Specific impulse

rse_Isp = (
    rse_Itot
    /
    (
        (propellant_mass_g / KG_TO_G)
        * G0
    )
)


# Engine length

rse_len_mm = (
    engine_length_in
    * IN_TO_MM
)


# ============================================================
# PRINT RESULTS
# ============================================================



# ============================================================
# BUILD RSE TEXT
# ============================================================

def build_rse_text():

    data_lines = []

    for t_i, cg_i, m_i, f_i in zip(
        time,
        cg_array_mm,
        mass_array_g,
        thrust_array_N
    ):

        data_lines.append(
            f'\t\t<eng-data '
            f'cg="{cg_i:.6f}" '
            f'f="{f_i:.6f}" '
            f'm="{m_i:.6f}" '
            f't="{t_i:.6f}"/>'
        )

    data_block = "\n".join(data_lines)


    rse_text = f"""<engine-database>
\t<engine-list>
\t\t<engine
\t\t\tIsp="{rse_Isp:.6f}"
\t\t\tItot="{rse_Itot:.6f}"
\t\t\tType="{rse_Type}"
\t\t\tauto-calc-cg="0"
\t\t\tauto-calc-mass="0"
\t\t\tavgThrust="{rse_avgThrust:.6f}"
\t\t\tburn-time="{rse_burn_time:.6f}"
\t\t\tcode="{rse_code}"
\t\t\tdelays="{rse_delays}"
\t\t\tdia="{rse_dia_mm:.6f}"
\t\t\texitDia="{rse_exitDia_mm:.6f}"
\t\t\tinitWt="{rse_initWt_g:.6f}"
\t\t\tlen="{rse_len_mm:.6f}"
\t\t\tmassFrac="{rse_massFrac:.6f}"
\t\t\tmfg="{rse_mfg}"
\t\t\tpeakThrust="{rse_peakThrust:.6f}"
\t\t\tpropWt="{rse_propWt_g:.6f}"
\t\t\tthroatDia="{rse_throatDia_mm:.6f}">
\t\t\t<comments>
\t\t\t\t{rse_comments}
\t\t\t</comments>
\t\t\t<data>
{data_block}
\t\t\t</data>
\t\t</engine>
\t</engine-list>
</engine-database>
"""

    return rse_text


# ============================================================
# RSE MASS METADATA
# ============================================================

# These are based on the mass model's actual wet/empty values.

rse_initWt_g = wet_mass_g
rse_propWt_g = propellant_mass_g
rse_massFrac = mass_frac


# ============================================================
# OUTPUT DIRECTORY
# ============================================================

RSE_OUTPUT_DIR = (
    r"C:\Users\nagah\Desktop\SRT_GitHub"
    r"\dynamics\aero_modeling\IREC_2027\Thrust Curves"
)


# ============================================================
# SAVE RSE
# ============================================================

if __name__ == "__main__":

    print("\n==============================================")
    print("               RSE FILE EXPORT")
    print("==============================================")

    try:

        user_input = input(
            f'Press ENTER to save "{rse_code}.rse" or type "n" to skip: '
        )

    except EOFError:

        user_input = ""


    if user_input.strip().lower() != "n":

        os.makedirs(
            RSE_OUTPUT_DIR,
            exist_ok=True
        )

        rse_filename = os.path.join(
            RSE_OUTPUT_DIR,
            f"{rse_code}.rse"
        )

        with open(
            rse_filename,
            "w",
            encoding="utf-8"
        ) as file:

            file.write(
                build_rse_text()
            )

        print(f"Saved RSE file to: {rse_filename}")

    else:
        pass
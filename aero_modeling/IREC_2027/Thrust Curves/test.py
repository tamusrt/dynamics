import numpy as np


# ============================================================
# INPUTS
# ============================================================

# Time
dt = 0.05                  # Time interval [s]  (changed from 0.1 -> 0.05)
burn_time = 10.0           # Total burn time [s]


# ---------------------------- 
# Thrust 
# ---------------------------- 

initial_thrust = 3600.0    # [N]  <-- constant thrust value used for every time instance


# ---------------------------- 
# Top Plumbing 
# ---------------------------- 

top_plumbing_mass = 1.08       # [kg] 
top_plumbing_length = 4.923    # [in] 


# ---------------------------- 
# Oxidizer Tank 
# ---------------------------- 

oxidizer_tank_mass = 32.25042 - 18.1437    # [kg] 
oxidizer_tank_length = 57.56               # [in] 
oxidizer_mass_initial = 18.1437            # [kg] 


# ---------------------------- 
# Plumbing between Ox Tank 
# and Combustion Chamber 
# ---------------------------- 

inter_plumbing_mass = 2.81227     # [kg] 
inter_plumbing_length = 14.0   # [in] 


# ---------------------------- 
# Combustion Chamber 
# ---------------------------- 

combustion_chamber_mass = 7.574993 - 3.99161    # [kg] 
combustion_chamber_length = 24.5                 # [in] 
fuel_mass_initial = 3.99161                      # [kg] 


# ============================================================
# ENGINE / .rse METADATA (edit these to match your motor)
# ============================================================
# Values marked "computed below" are filled in automatically from the
# mass/thrust arrays after they are calculated. Values marked
# "USER-EDITABLE" are physical/geometric properties that this CG/mass
# program has no way of deriving, so sensible placeholders are provided
# and you should replace them with your real motor's numbers.

rse_code        = "O3600"      # USER-EDITABLE: motor designator/code
rse_mfg         = "TAMUSRT"    # USER-EDITABLE: manufacturer
rse_Type        = "Hybrid"     # USER-EDITABLE: motor type
rse_delays      = "0"          # USER-EDITABLE: ejection delay(s)

rse_dia_mm      = 152.400000   # USER-EDITABLE: motor diameter [mm]
rse_throatDia_mm = 38.303200   # USER-EDITABLE: nozzle throat diameter [mm]
rse_exitDia_mm  = 77.314608    # USER-EDITABLE: nozzle exit diameter [mm]

# div/fix/step values control how OpenRocket-style sims scale the curve;
# 10/1/-1 are the standard "use as-is" defaults used in the sample file.
rse_FDiv, rse_FFix, rse_FStep   = 10, 1, -1.
rse_cgDiv, rse_cgFix, rse_cgStep = 10, 1, -1.
rse_mDiv, rse_mFix, rse_mStep   = 10, 1, -1.
rse_tDiv, rse_tFix, rse_tStep   = 10, 1, -1.

rse_comments = "IGNIS-2027-python"

# The following are computed further down, once the time/mass/thrust
# arrays exist:
#   rse_burn_time, rse_len_mm, rse_initWt_g, rse_propWt_g,
#   rse_massFrac, rse_Itot, rse_avgThrust, rse_peakThrust, rse_Isp


# ============================================================
# UNIT CONVERSIONS
# ============================================================

IN_TO_MM = 25.4
KG_TO_G = 1000.0
G0 = 9.80665   # standard gravity, used for Isp calculation


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

# Overall engine length (tip to bottom of combustion chamber), used to
# fill in the .rse "len" attribute.
engine_length_in = (
    combustion_chamber_start
    + combustion_chamber_length
)


# ============================================================
# COMPONENT CG LOCATIONS
# ============================================================

# Assume each dry component has uniform mass distribution,
# so its CG is at its geometric center.

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

# Make sure final point is exactly burn_time
if time[-1] > burn_time:
    time[-1] = burn_time


# ============================================================
# OUTPUT ARRAYS
# ============================================================

# Whole-engine CG at each time step [mm]
cg_array_mm = np.zeros(len(time))

# Whole-engine mass at each time step [g]
mass_array_g = np.zeros(len(time))

# Thrust at each time step [N] -- every entry is set to initial_thrust
thrust_array_N = np.full(len(time), initial_thrust)


# ============================================================
# CALCULATE CG AND MASS AT EACH TIME
# ============================================================

for i, t in enumerate(time):

    # --------------------------------------------------------
    # BURN FRACTION
    # --------------------------------------------------------

    burn_fraction = t / burn_time


    # --------------------------------------------------------
    # REMAINING PROPELLANT
    # --------------------------------------------------------

    # Linear depletion assumption

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

    # Initially the oxidizer fills the tank, so its CG
    # is at the center of the tank.
    #
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
    # FUEL CG
    # --------------------------------------------------------

    # Fuel CG stays at the center of the combustion chamber.

    fuel_cg = combustion_chamber_cg


    # --------------------------------------------------------
    # TOTAL ENGINE MASS
    # --------------------------------------------------------

    total_mass_kg = (
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


    # --------------------------------------------------------
    # WHOLE-ENGINE CG
    # --------------------------------------------------------

    cg_in = (
        total_moment
        / total_mass_kg
    )


    # --------------------------------------------------------
    # CONVERT OUTPUTS
    # --------------------------------------------------------

    cg_array_mm[i] = cg_in * IN_TO_MM

    mass_array_g[i] = total_mass_kg * KG_TO_G


# ============================================================
# OUTPUT
# ============================================================

print("Time array [s]:")
print(time)

print("\nWhole Engine CG Array [mm]:")
print(cg_array_mm)

print("\nWhole Engine Mass Array [g]:")
print(mass_array_g)

print("\nThrust Array [N]:")
print(thrust_array_N)

# ============================================================
# WET / EMPTY ENGINE RESULTS
# ============================================================

# Wet = beginning of burn (t = 0)
wet_cg_mm = cg_array_mm[0]
wet_mass_g = mass_array_g[0]

# Empty = end of burn (t = burn_time)
empty_cg_mm = cg_array_mm[-1]
empty_mass_g = mass_array_g[-1]


print("\n==============================================")
print("         WET / EMPTY ENGINE RESULTS")
print("==============================================")

print(f"Wet CG:      {wet_cg_mm:.3f} mm")
print(f"Empty CG:    {empty_cg_mm:.3f} mm")

print(f"Wet Mass:    {wet_mass_g:.3f} g")
print(f"Empty Mass:  {empty_mass_g:.3f} g")


# ============================================================
# FINISH FILLING IN THE .rse METADATA
# ============================================================

rse_burn_time  = burn_time
rse_len_mm     = engine_length_in * IN_TO_MM
rse_initWt_g   = wet_mass_g
rse_propWt_g   = wet_mass_g - empty_mass_g
rse_massFrac   = rse_propWt_g / rse_initWt_g

_trapz = getattr(np, "trapezoid", None) or np.trapz
rse_Itot        = _trapz(thrust_array_N, time)              # total impulse [N*s]
rse_avgThrust   = float(np.mean(thrust_array_N))           # [N]
rse_peakThrust  = float(np.max(thrust_array_N))             # [N]

# Isp = Itot / (propellant weight in kg * g0)
rse_Isp = rse_Itot / ((rse_propWt_g / KG_TO_G) * G0)


# ============================================================
# BUILD THE .rse (RASP Engine) FILE
# ============================================================

def build_rse_text():
    data_lines = []
    for t_i, cg_i, m_i, f_i in zip(time, cg_array_mm, mass_array_g, thrust_array_N):
        data_lines.append(
            f'\t\t<eng-data cg="{cg_i:.6f}" f="{f_i:.6f}" '
            f'm="{m_i:.6f}" t="{t_i:.6f}"/>'
        )
    data_block = "\n".join(data_lines)

    rse_text = f"""<engine-database>
\t<engine-list>
\t<engine FDiv="{rse_FDiv}" FFix="{rse_FFix}" FStep="{rse_FStep}" Isp="{rse_Isp:.6f}" Itot="{rse_Itot:.6f}" Type="{rse_Type}" auto-calc-cg="0" auto-calc-mass="0" avgThrust="{rse_avgThrust:.6f}" burn-time="{rse_burn_time:.6f}" cgDiv="{rse_cgDiv}" cgFix="{rse_cgFix}" cgStep="{rse_cgStep}" code="{rse_code}" delays="{rse_delays}" dia="{rse_dia_mm:.6f}" exitDia="{rse_exitDia_mm:.6f}" initWt="{rse_initWt_g:.0f}" len="{rse_len_mm:.6f}" mDiv="{rse_mDiv}" mFix="{rse_mFix}" mStep="{rse_mStep}" massFrac="{rse_massFrac:.6f}" mfg="{rse_mfg}" peakThrust="{rse_peakThrust:.6f}" propWt="{rse_propWt_g:.6f}" tDiv="{rse_tDiv}" tFix="{rse_tFix}" tStep="{rse_tStep}" throatDia="{rse_throatDia_mm:.6f}">
\t<comments>
\t\t{rse_comments}
\t</comments>
\t<data>
{data_block}
\t</data>
\t</engine>
 \t</engine-list>
</engine-database>
"""
    return rse_text


# ============================================================
# INTERACTIVE .rse DOWNLOAD PROMPT
# ============================================================
# When you run this script from a terminal, it will pause here.
# Press Enter to write the .rse file to disk (in the current folder).
# Type "n" (then Enter) to skip saving.

if __name__ == "__main__":
    try:
        user_input = input(
            f'\nPress Enter to save "{rse_code}.rse" to the current folder '
            f'(or type "n" to skip): '
        )
    except EOFError:
        # No interactive terminal available (e.g. piped input) - default to saving.
        user_input = ""

    if user_input.strip().lower() != "n":
        rse_filename = f"{rse_code}.rse"
        with open(rse_filename, "w") as f:
            f.write(build_rse_text())
        print(f"Saved thrust curve to: {rse_filename}")
    else:
        print("Skipped saving .rse file.")
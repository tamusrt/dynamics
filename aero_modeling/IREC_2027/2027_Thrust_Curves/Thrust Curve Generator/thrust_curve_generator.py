
"""Generate RSE and RASP .eng files from an engine information file and a thrust curve."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent

IN_TO_MM = 25.4
KG_TO_G = 1000.0
STANDARD_GRAVITY_M_S2 = 9.80665


# ============================================================
# ENGINE INFORMATION INPUT FILE READER
# ============================================================

# Every setting required by the program must be defined in
# the engine information input file.

REQUIRED_SETTINGS = {
    # Engine identity
    "engine_code": str,
    "manufacturer_name": str,
    "motor_type": str,
    "motor_delay_setting": str,
    "engine_comments": str,

    # Geometry (mm)
    "engine_outer_diameter_mm": float,
    "nozzle_throat_diameter_mm": float,
    "nozzle_exit_diameter_mm": float,

    # CG / mass depletion
    "cg_mass_burn_time_s": float,

    # Output
    "output_subfolder": str,
    "rse_output_filename": str,
    "eng_output_filename": str,

    # Oxidizer tank
    "oxidizer_tank_dry_mass_kg": float,
    "oxidizer_tank_length_in": float,
    "initial_oxidizer_mass_kg": float,

    # Middle plumbing
    "middle_plumbing_mass_kg": float,
    "middle_plumbing_length_in": float,

    # Combustion chamber
    "combustion_chamber_dry_mass_kg": float,
    "combustion_chamber_length_in": float,
    "initial_fuel_mass_kg": float,
}


def resolve_path(text: str, relative_to: Path) -> Path:
    """Resolve an absolute path or a path relative to a directory."""
    path = Path(text).expanduser()
    return path if path.is_absolute() else relative_to / path


def read_engine_info(filename: Path) -> dict:
    """Read and validate the engine information input file."""

    if not filename.is_file():
        raise FileNotFoundError(
            f"Engine information input file was not found:\n{filename}"
        )

    # Read setting=value pairs. Ignore blank lines and comments.
    raw_settings = {}

    with filename.open("r", encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()

            if not line or line.startswith("#"):
                continue

            if "=" not in line:
                raise ValueError(
                    f"Invalid line {line_number} in engine input file:\n"
                    f"{line}\n"
                    "Expected setting_name = value."
                )

            key, value = line.split("=", maxsplit=1)
            key = key.strip()
            value = value.strip()

            if not key:
                raise ValueError(
                    f"Missing setting name on line {line_number}."
                )

            if key in raw_settings:
                raise ValueError(
                    f"Duplicate setting '{key}' on line {line_number}."
                )

            if not value:
                raise ValueError(
                    f"Setting '{key}' has no value on line {line_number}."
                )

            raw_settings[key] = value

    # Check for missing required settings.
    missing = [
        key for key in REQUIRED_SETTINGS
        if key not in raw_settings
    ]

    if missing:
        raise ValueError(
            "The engine information file is missing these settings:\n"
            + "\n".join(f"  - {key}" for key in missing)
        )

    # Convert numeric settings and retain text settings as strings.
    settings = {}

    for key, value_type in REQUIRED_SETTINGS.items():
        value = raw_settings[key]

        try:
            if value_type is float:
                settings[key] = float(value)
                if not np.isfinite(settings[key]):
                    raise ValueError("Value must be finite.")
            else:
                settings[key] = value
        except ValueError as exc:
            raise ValueError(
                f"Invalid value for '{key}': {value!r}"
            ) from exc

    # Basic validation.
    positive_settings = [
        "engine_outer_diameter_mm",
        "nozzle_throat_diameter_mm",
        "nozzle_exit_diameter_mm",
        "cg_mass_burn_time_s",
        "oxidizer_tank_length_in",
        "middle_plumbing_length_in",
        "combustion_chamber_length_in",
    ]

    for key in positive_settings:
        if settings[key] <= 0:
            raise ValueError(
                f"'{key}' must be greater than zero."
            )

    nonnegative_settings = [
        "oxidizer_tank_dry_mass_kg",
        "initial_oxidizer_mass_kg",
        "middle_plumbing_mass_kg",
        "combustion_chamber_dry_mass_kg",
        "initial_fuel_mass_kg",
    ]

    for key in nonnegative_settings:
        if settings[key] < 0:
            raise ValueError(
                f"'{key}' cannot be negative."
            )

    if not settings["engine_code"].strip():
        raise ValueError("'engine_code' cannot be blank.")

    if not settings["manufacturer_name"].strip():
        raise ValueError("'manufacturer_name' cannot be blank.")

    # Normalize Windows and Unix-style relative folder separators.
    subfolder = settings["output_subfolder"].strip()
    subfolder_parts = [
        part for part in subfolder.replace("\\", "/").split("/")
        if part and part != "."
    ]

    if not subfolder_parts or ".." in subfolder_parts:
        raise ValueError(
            "'output_subfolder' must be a non-empty relative path "
            "inside the dynamics folder and cannot contain '..'."
        )

    settings["output_subfolder"] = Path(*subfolder_parts)

    # Keep the original output filenames, without extensions.
    for key in ("rse_output_filename", "eng_output_filename"):
        name = settings[key].strip()

        if not name or Path(name).name != name:
            raise ValueError(
                f"'{key}' must be a filename only, without a folder path."
            )

        settings[key] = name

    return settings


# ============================================================
# THRUST CURVE READER
# ============================================================

def read_thrust_curve(filename: Path):
    """Read the required 'Time (s)' and 'Thrust (N)' columns."""

    ext = filename.suffix.lower()

    if ext in {".xlsx", ".xls"}:
        data = pd.read_excel(filename)
    elif ext == ".csv":
        data = pd.read_csv(filename)
    else:
        raise ValueError(
            "Thrust curve must be an Excel (.xlsx/.xls) "
            "or CSV (.csv) file."
        )

    required = ["Time (s)", "Thrust (N)"]

    for col in required:
        if col not in data.columns:
            raise ValueError(
                f'Thrust input is missing required column "{col}".'
            )

    data = data[required].copy()

    for col in required:
        data[col] = pd.to_numeric(data[col], errors="coerce")

    data = data.dropna()

    if len(data) < 2:
        raise ValueError(
            "Thrust input must contain at least two valid data points."
        )

    times_s = data["Time (s)"].to_numpy(dtype=float)
    thrust_n = data["Thrust (N)"].to_numpy(dtype=float)

    if np.any(np.diff(times_s) <= 0):
        raise ValueError(
            "Thrust-curve times must be strictly increasing."
        )

    positive = np.flatnonzero(thrust_n > 0)

    if len(positive) == 0:
        raise ValueError(
            "No positive thrust was found in the input file."
        )

    after_positive_zero = np.flatnonzero(
        thrust_n[positive[0]:] <= 0
    )

    if len(after_positive_zero) == 0:
        raise ValueError(
            "No zero-thrust point was found after positive thrust."
        )

    burn_end = positive[0] + after_positive_zero[0]

    times_s = times_s[:burn_end + 1]
    thrust_n = thrust_n[:burn_end + 1]

    if len(times_s) < 2:
        raise ValueError(
            "Extracted thrust curve must contain at least two points."
        )

    return times_s, thrust_n


# ============================================================
# MASS AND CENTER OF GRAVITY
# ============================================================

def calculate_mass_and_cg(settings: dict, times_s: np.ndarray):
    """Calculate linear propellant depletion until the configured stop."""

    tank_dry = settings["oxidizer_tank_dry_mass_kg"]
    tank_len = settings["oxidizer_tank_length_in"]
    oxidizer_initial = settings["initial_oxidizer_mass_kg"]

    middle_mass = settings["middle_plumbing_mass_kg"]
    middle_len = settings["middle_plumbing_length_in"]

    chamber_dry = settings["combustion_chamber_dry_mass_kg"]
    chamber_len = settings["combustion_chamber_length_in"]
    fuel_initial = settings["initial_fuel_mass_kg"]

    # Component positions, measured from the front of the engine.
    tank_start = 0.0
    middle_start = tank_start + tank_len
    chamber_start = middle_start + middle_len
    engine_length_in = chamber_start + chamber_len

    tank_cg = tank_start + tank_len / 2
    middle_cg = middle_start + middle_len / 2
    chamber_cg = chamber_start + chamber_len / 2
    tank_bottom = tank_start + tank_len

    cg_stop_s = settings["cg_mass_burn_time_s"]

    elapsed_s = times_s - times_s[0]
    depletion_fraction = np.clip(
        elapsed_s / cg_stop_s, 0.0, 1.0
    )

    cg_mm = np.zeros(len(times_s))
    mass_g = np.zeros(len(times_s))

    for i, fraction in enumerate(depletion_fraction):
        oxidizer_remaining = oxidizer_initial * (1.0 - fraction)
        fuel_remaining = fuel_initial * (1.0 - fraction)

        # Oxidizer CG moves toward the bottom of the tank as it depletes.
        oxidizer_cg = tank_cg + fraction * (
            tank_bottom - tank_cg
        )

        total_mass_kg = (
            tank_dry + middle_mass + chamber_dry
            + oxidizer_remaining + fuel_remaining
        )

        moment_kg_in = (
            tank_dry * tank_cg
            + middle_mass * middle_cg
            + chamber_dry * chamber_cg
            + oxidizer_remaining * oxidizer_cg
            + fuel_remaining * chamber_cg
        )

        cg_mm[i] = (
            moment_kg_in / total_mass_kg
        ) * IN_TO_MM

        mass_g[i] = total_mass_kg * KG_TO_G

    wet_mass_g = float(mass_g[0])

    # Select the last curve sample at or before the configured stop time.
    stop_index = int(
        np.searchsorted(elapsed_s, cg_stop_s, side="right") - 1
    )
    stop_index = max(0, min(stop_index, len(times_s) - 1))

    empty_mass_g = float(mass_g[stop_index])
    propellant_mass_g = wet_mass_g - empty_mass_g

    if propellant_mass_g <= 0:
        raise ValueError(
            "Calculated depleted propellant mass is zero. Check that "
            "cg_mass_burn_time_s is within the thrust-curve duration "
            "and that the initial propellant masses are positive."
        )

    # Hold the final calculated mass and CG constant after the stop time.
    # If the stop time lies between samples, use the exact stop-time value.
    if elapsed_s[-1] > cg_stop_s:
        cg_mm[stop_index + 1:] = cg_mm[stop_index]
        mass_g[stop_index + 1:] = mass_g[stop_index]

    return (
        cg_mm,
        mass_g,
        engine_length_in,
        wet_mass_g,
        empty_mass_g,
        propellant_mass_g,
        stop_index,
    )


# ============================================================
# RSE OUTPUT
# ============================================================

def build_rse_text(
    settings,
    times_s,
    thrust_n,
    cg_mm,
    mass_g,
    engine_length_in,
    wet_mass_g,
    propellant_mass_g,
):
    total_impulse_ns = float(np.trapezoid(thrust_n, times_s))
    curve_duration_s = float(times_s[-1] - times_s[0])

    if curve_duration_s <= 0:
        raise ValueError(
            "Thrust-curve duration must be greater than zero."
        )

    avg_thrust_n = total_impulse_ns / curve_duration_s
    peak_thrust_n = float(np.max(thrust_n))

    isp_s = total_impulse_ns / (
        (propellant_mass_g / KG_TO_G) * STANDARD_GRAVITY_M_S2
    )

    mass_fraction = (
        (wet_mass_g - propellant_mass_g) / wet_mass_g
    )

    rows = [
        f'\t\t<eng-data cg="{cg:.6f}" f="{force:.6f}" '
        f'm="{mass:.6f}" t="{time:.6f}"/>'
        for time, force, cg, mass in zip(
            times_s, thrust_n, cg_mm, mass_g
        )
    ]

    return f"""<engine-database>
\t<engine-list>
\t\t<engine
\t\t\tIsp="{isp_s:.6f}"
\t\t\tItot="{total_impulse_ns:.6f}"
\t\t\tType="{settings['motor_type']}"
\t\t\tauto-calc-cg="0"
\t\t\tauto-calc-mass="0"
\t\t\tavgThrust="{avg_thrust_n:.6f}"
\t\t\tburn-time="{curve_duration_s:.6f}"
\t\t\tcode="{settings['engine_code']}"
\t\t\tdelays="{settings['motor_delay_setting']}"
\t\t\tdia="{settings['engine_outer_diameter_mm']:.6f}"
\t\t\texitDia="{settings['nozzle_exit_diameter_mm']:.6f}"
\t\t\tinitWt="{wet_mass_g:.6f}"
\t\t\tlen="{engine_length_in * IN_TO_MM:.6f}"
\t\t\tmassFrac="{mass_fraction:.6f}"
\t\t\tmfg="{settings['manufacturer_name']}"
\t\t\tpeakThrust="{peak_thrust_n:.6f}"
\t\t\tpropWt="{propellant_mass_g:.6f}"
\t\t\tthroatDia="{settings['nozzle_throat_diameter_mm']:.6f}">
\t\t\t<comments>{settings['engine_comments']}</comments>
\t\t\t<data>
{chr(10).join(rows)}
\t\t\t</data>
\t\t</engine>
\t</engine-list>
</engine-database>
"""


# ============================================================
# RASP .ENG OUTPUT
# ============================================================

def build_eng_text(
    settings,
    times_s,
    thrust_n,
    engine_length_in,
    wet_mass_g,
    propellant_mass_g,
):
    header = (
        f"{settings['engine_code']} "
        f"{settings['engine_outer_diameter_mm']:.6f} "
        f"{engine_length_in * IN_TO_MM:.6f} "
        f"{settings['motor_delay_setting']} "
        f"{propellant_mass_g / KG_TO_G:.6f} "
        f"{wet_mass_g / KG_TO_G:.6f} "
        f"{settings['manufacturer_name']}"
    )

    rows = []

    for time_s, force_n in zip(times_s, thrust_n):
        if np.isclose(time_s, 0.0) and np.isclose(force_n, 0.0):
            continue

        rows.append(f"   {time_s:.6f} {force_n:.6f}")

    comments = [
        f"; {settings['engine_code']}",
        f"; Manufacturer: {settings['manufacturer_name']}",
        f"; Type: {settings['motor_type']}",
        "; RSE source data",
    ]

    if settings["engine_comments"]:
        comments.append(f"; {settings['engine_comments']}")

    return "\n".join(comments + [header] + rows + [";", ""])


# ============================================================
# MAIN PROGRAM
# ============================================================

def main():
    # Preserve the requirement to run from the dynamics folder.
    current_folder = Path.cwd()

    if current_folder.name.lower() != "dynamics":
        sys.exit(
            "ERROR: This program must be run from the dynamics folder.\n"
            f"Current folder: {current_folder}\n"
            "Please open the dynamics folder in VS Code and run again."
        )

    dynamics_github_folder = current_folder

    print("\n========== ENGINE INFORMATION INPUT ==========")
    print("Enter the engine information filename.")
    print("Example: ENGINE-2027-python.txt")
    print("A relative filename is resolved from the Python script folder.")

    engine_info_entry = input(
        "\nEngine information input filename: "
    ).strip().strip('"').strip("'")

    if not engine_info_entry:
        sys.exit("ERROR: No engine information filename was entered.")

    engine_info_path = resolve_path(engine_info_entry, SCRIPT_DIR)

    try:
        settings = read_engine_info(engine_info_path)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(f"\nERROR: {exc}")

    settings["dynamics_github_folder"] = dynamics_github_folder

    print("\nEngine information loaded successfully.")
    print(f"Input file: {engine_info_path}")
    print(f"Engine code: {settings['engine_code']}")
    print(f"Manufacturer: {settings['manufacturer_name']}")
    print(f"Motor type: {settings['motor_type']}")

    # Ask for the thrust curve independently of the engine settings.
    thrust_entry = input(
        "\nEnter Excel/CSV thrust curve filename: "
    ).strip().strip('"').strip("'")

    if not thrust_entry:
        sys.exit("ERROR: No thrust-curve filename was entered.")

    thrust_path = resolve_path(thrust_entry, SCRIPT_DIR)

    if not thrust_path.is_file():
        sys.exit(
            f"ERROR: Thrust curve file was not found:\n{thrust_path}"
        )

    try:
        times_s, thrust_n = read_thrust_curve(thrust_path)

        (
            cg_mm,
            mass_g,
            engine_length_in,
            wet_mass_g,
            empty_mass_g,
            propellant_mass_g,
            stop_index,
        ) = calculate_mass_and_cg(settings, times_s)

    except (ValueError, OSError, ImportError) as exc:
        sys.exit(f"\nERROR: {exc}")

    output_dir = (
        settings["dynamics_github_folder"]
        / settings["output_subfolder"]
    )

    rse_name = settings["rse_output_filename"]
    eng_name = settings["eng_output_filename"]

    print("\n================ ENGINE FILE EXPORT ================")
    print(f"Engine code: {settings['engine_code']}")
    print(f"Thrust-curve duration: {times_s[-1] - times_s[0]:.3f} s")
    print(
        "CG/mass depletion stop: "
        f"{settings['cg_mass_burn_time_s']:.3f} s"
    )
    print(f"Engine length: {engine_length_in:.3f} in")
    print(f"Wet mass: {wet_mass_g / KG_TO_G:.3f} kg")
    print(f"Mass at stop index: {empty_mass_g / KG_TO_G:.3f} kg")
    print(f"Propellant mass depleted: {propellant_mass_g / KG_TO_G:.3f} kg")
    print(f"Output folder: {output_dir}")

    answer = input(
        f'\nSave "{rse_name}.rse" and "{eng_name}.eng"? '
        "Press ENTER to save or type n to cancel: "
    ).strip().lower()

    if answer == "n":
        print("File export skipped.")
        return

    rse_path = output_dir / f"{rse_name}.rse"
    eng_path = output_dir / f"{eng_name}.eng"

    try:
        output_dir.mkdir(parents=True, exist_ok=True)

        rse_path.write_text(
            build_rse_text(
                settings,
                times_s,
                thrust_n,
                cg_mm,
                mass_g,
                engine_length_in,
                wet_mass_g,
                propellant_mass_g,
            ),
            encoding="utf-8",
        )

        eng_path.write_text(
            build_eng_text(
                settings,
                times_s,
                thrust_n,
                engine_length_in,
                wet_mass_g,
                propellant_mass_g,
            ),
            encoding="utf-8",
        )

    except OSError as exc:
        sys.exit(f"\nERROR: Could not save output files: {exc}")

    print(f"\nSaved RSE file: {rse_path}")
    print(f"Saved RASP .eng file: {eng_path}")
    print("================== EXPORT COMPLETE =================")


if __name__ == "__main__":
    main()

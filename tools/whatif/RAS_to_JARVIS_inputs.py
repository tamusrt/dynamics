"""Create the JARVIS aero table CSV straight from RASAero II, asking for the inputs.

    python RAS_to_JARVIS_inputs.py

Self-contained: it drives RASAero's "Run Test" for angles of attack 0 to 30 degrees (key presses from
brute_force_aero.py by Luke Adams, Sarah Kinney, Nacho Durante), then converts the alphaN.txt exports to
the flight sim aero table. The alphaN.txt files go to a temporary folder and are thrown away.
Windows only (pyautogui; pip install pyautogui).

Before running: open RASAero II with the rocket loaded and make it the window you touched last. The
script presses Alt+Tab and types into whatever that reaches. Moving the mouse into a screen corner stops
pyautogui.

The table is in the missile frame: the normal force lies in the plane of phi 0, so the vehicle file
needs ``frame = "missile"``.

RASAero stops at 30 degrees. A vehicle tumbling after apogee flies at any angle, so each Mach number is
carried on to 180 degrees with Jorgensen's crossflow model (NASA TR R-474), fitted to the last RASAero
rows so the two join::

    CN(a) = CNa |sin a cos a| + K Cdc(M sin a) sin^2 a

The axial force falls with cos a to zero side-on, then turns round as the base meets the flow. These
rows are rough, perhaps 25 percent: enough for a tumble, not for the ascent.
"""

from __future__ import annotations

import csv
import math
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_NAME = "aero.csv"
WINDOW = "RASAero"
COUNTDOWN_S = 5

# --- RASAero export to aero table CSV ---

ALPHAS_DEG = range(31)

# Angles added past the RASAero ones, every 10 degrees to 180
EXTENDED_STEP_DEG = 10
CA_BASE = 0.9  # Axial force with the base meeting the flow, as a flat plate
BLEND_DEG = 30.0  # Angle over which the fit to the last RASAero row fades
ETA = 0.7  # Crossflow efficiency of a long body (Jorgensen)
FIT_FROM_DEG = 20.0  # Tables reaching this far fix the crossflow on their own

# RASAero writes a header line per Mach number, ending in the Reynolds number,
# the only field with no decimal point. Below Mach 1 it has 12 fields, and
# fields 4, 5, 7 and 8 are the drag parts that fall as the Reynolds number
# rises. After it come, in order:
#   +1: Mach, CN alpha (0-4 deg), CP (0-4 deg)
#   +2: Mach, Alpha, CN potential, CN viscous
#   +3: Mach, Alpha, CN total, CP total (inches from the nose tip)
#   +4: Mach, Alpha, CL, CD, CN, CA (power off)
#   +5: Mach, Alpha, CL, CD, CN, CA (power on)
# CA is along the body axis; CD is along the flight path, so Cx uses CA. At
# alpha 0, CP total is the CP's limit as alpha goes to zero, which xCP needs;
# the +1 line averages over 0 to 4 deg.
SUBSONIC_HEADER_FIELDS = 12
VISCOUS_FIELDS = (4, 5, 7, 8)
TOTAL_OFFSET, POWER_OFF_OFFSET, POWER_ON_OFFSET = 3, 4, 5
CA_FIELD = 5

# RASAero also writes rows between the grid's Mach numbers; keep the grid
MACH_STEP = 0.01

COLUMNS = ("Mach", "Alpha", "Phi", "Cx", "Cy", "Cz", "CMx", "xCP")
EXTRA_COLUMNS = ("CxPowerOn", "ViscousDrag")


def read_alpha_file(path: Path, alpha: int) -> list[tuple[float, ...]]:
    """Read one alphaN.txt export.

    Args:
        path (Path): RASAero export at one angle of attack.
        alpha (int): Its angle of attack in degrees.

    Returns:
        list[tuple[float, ...]]: Mach, alpha, CA power off, CA power on, CN,
            CP in inches and viscous drag (NaN above Mach 1) per grid Mach.
    """
    lines = path.read_text().splitlines()
    rows: list[tuple[float, ...]] = []
    for index, line in enumerate(lines[: len(lines) - POWER_ON_OFFSET]):
        fields = line.split()
        if len(fields) < 5 or "." in fields[-1]:
            continue
        try:
            mach = float(fields[0])
            _, _, cn, cp = (float(v) for v in lines[index + TOTAL_OFFSET].split())
            ca_off = float(lines[index + POWER_OFF_OFFSET].split()[CA_FIELD])
            ca_on = float(lines[index + POWER_ON_OFFSET].split()[CA_FIELD])
        except (ValueError, IndexError):
            continue
        if abs(mach / MACH_STEP - round(mach / MACH_STEP)) > 1e-6:
            continue
        viscous = (
            sum(float(fields[k]) for k in VISCOUS_FIELDS)
            if len(fields) == SUBSONIC_HEADER_FIELDS
            else float("nan")
        )
        rows.append((mach, float(alpha), ca_off, ca_on, cn, cp, viscous))
    return rows


def crossflow_drag(crossflow_mach: float) -> float:
    """Drag coefficient of a long cylinder across the flow (Jorgensen's curve)."""
    m = abs(crossflow_mach)
    if m <= 0.4:
        return 1.2
    if m <= 1.0:
        return 1.2 + (m - 0.4) / 0.6 * 0.6
    if m <= 2.0:
        return 1.8 - (m - 1.0) * 0.3
    return 1.5


def extend_to_180(  # pylint: disable=too-many-locals
    rows: list[tuple[float, ...]], reference_length_in: float
) -> list[tuple[float, ...]]:
    """Carry one Mach number's rows, sorted by alpha, on to 180 degrees.

    The linear lift acts at the small-angle CP and the crossflow at a point
    fitted from the last row; the CP is their blend.

    Args:
        rows (list[tuple[float, ...]]): Rows of ``read_alpha_file`` at one Mach.
        reference_length_in (float): Body diameter, for the least crossflow
            the body alone gives side-on.

    Returns:
        list[tuple[float, ...]]: The added rows, none when the last is 180.
    """
    mach, alpha_end_deg = rows[-1][0], rows[-1][1]
    added = range(
        (int(alpha_end_deg) // EXTENDED_STEP_DEG + 1) * EXTENDED_STEP_DEG,
        181,
        EXTENDED_STEP_DEG,
    )
    if not added:
        return []
    _, _, ca_off_end, ca_on_end, cn_end, cp_end, viscous = rows[-1]
    end = math.radians(alpha_end_deg)
    # Small-angle slope and CP from the first row above 0, up to 4 degrees
    small = next((r for r in rows if 0.0 < r[1] <= 4.0), rows[1])
    a0 = math.radians(small[1])
    slope = small[4] / (math.sin(a0) * math.cos(a0)) if small[4] > 0 else 0.0
    cp0 = small[5] if small[4] > 0 else cp_end
    lin_end = slope * abs(math.sin(end) * math.cos(end))
    cross_end = max(cn_end - lin_end, 0.0)
    k = cross_end / (math.sin(end) ** 2 * crossflow_drag(mach * math.sin(end)))
    # A table stopping short says too little about the crossflow: never less
    # than the body's side area, its length taken as the CP over 0.85
    if alpha_end_deg < FIT_FROM_DEG and cp0 > 0:
        k = max(k, ETA * 4.0 / math.pi * (cp0 / 0.85) / reference_length_in)
    cp_cross = 0.65 * cp0
    if cross_end > 1e-6:
        cp_cross = (cn_end * cp_end - lin_end * cp0) / cross_end
        cp_cross = min(max(cp_cross, 0.4 * cp0), 1.1 * cp0)
    model_end = lin_end + cross_end
    fit = cn_end / model_end if model_end > 1e-9 else 1.0
    ca_bases = (max(CA_BASE, rows[0][2]), max(CA_BASE, rows[0][3]))

    extended: list[tuple[float, ...]] = []
    for deg in added:
        a = math.radians(deg)
        lin = slope * abs(math.sin(a) * math.cos(a))
        cross = k * crossflow_drag(mach * math.sin(a)) * math.sin(a) ** 2
        scale = fit + (1.0 - fit) * min((deg - alpha_end_deg) / BLEND_DEG, 1.0)
        cp = (
            (lin * cp0 + cross * cp_cross) / (lin + cross)
            if lin + cross > 1e-9
            else cp_cross
        )
        ca_off, ca_on = (
            (ca * math.cos(a) / math.cos(end) for ca in (ca_off_end, ca_on_end))
            if deg <= 90
            else (base * math.cos(a) for base in ca_bases)
        )
        extended.append(
            (mach, float(deg), ca_off, ca_on, (lin + cross) * scale, cp, viscous)
        )
    return extended


def convert(  # pylint: disable=too-many-locals
    folder: Path, output: Path, reference_point_in: float, reference_length_in: float
) -> int:
    """Write the aero table CSV from every alphaN.txt in a folder.

    Args:
        folder (Path): Folder holding alpha0.txt to alpha30.txt.
        output (Path): CSV to write.
        reference_point_in (float): Reference point of the aero table, inches.
        reference_length_in (float): Reference length of the aero table, inches.

    Returns:
        int: Number of Mach and alpha points written.
    """
    rows = []
    for alpha_deg in ALPHAS_DEG:
        path = folder / f"alpha{alpha_deg}.txt"
        if not path.exists():
            # a table missing angles would be silently wrong, so stop
            raise SystemExit(f"{path.name} was not written: RASAero did not finish the sweep.")
        rows += read_alpha_file(path, alpha_deg)
    if not rows:
        raise SystemExit("The alphaN.txt files held no Mach rows: was the right rocket loaded in RASAero?")
    rows.sort(key=lambda row: (row[0], row[1]))
    by_mach: dict[float, list[tuple[float, ...]]] = {}
    for row in rows:
        by_mach.setdefault(row[0], []).append(row)
    rows = [
        row
        for mach_rows in by_mach.values()
        for row in mach_rows + extend_to_180(mach_rows, reference_length_in)
    ]

    with output.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(COLUMNS + EXTRA_COLUMNS)
        for mach, alpha, ca_off, ca_on, cn, cp, viscous in rows:
            x_cp = -(cp - reference_point_in) / reference_length_in
            for phi in (0, 360):
                values = (mach, alpha, phi, -ca_off, 0, -cn, 0, x_cp, -ca_on, viscous)
                # Adding 0.0 turns -0.0 into 0.0; no viscous drag is left blank
                writer.writerow("" if math.isnan(v) else f"{v + 0.0:g}" for v in values)
    return len(rows)


# --- RASAero "Run Test" sweep ---

NOZZLE_EXIT_DIAMETER_IN = 0  # already accounted for in the model


def foreground_title() -> str:
    """Title of the window in front (Windows only; run_sweep checks the platform first)."""
    import ctypes  # noqa: PLC0415  (Windows only)

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    handle = user32.GetForegroundWindow()
    buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(handle, buffer, 512)
    return buffer.value


def run_sweep(out_dir: Path, window: str, countdown: int, nozzle_in: float = NOZZLE_EXIT_DIAMETER_IN) -> int:
    """Switch to RASAero and dump alpha0.txt to alpha30.txt into ``out_dir``; returns an exit code."""
    if sys.platform != "win32":
        print("This only works on Windows, where RASAero runs.", flush=True)
        return 2
    try:
        import pyautogui  # noqa: PLC0415  (only this script needs it)
    except ImportError:
        print("pyautogui is not installed. Run: pip install pyautogui", flush=True)
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    folder = out_dir.resolve().as_posix()
    for left in range(countdown, 0, -1):
        print(f"Switching to RASAero in {left} s. Do not touch the keyboard or mouse.", flush=True)
        time.sleep(1)

    # alt-tab to rasaero
    pyautogui.hotkey("alt", "tab")
    time.sleep(0.8)
    title = foreground_title()
    if window.lower() not in title.lower():
        print(
            f"Stopped. The window in front was {title!r}, not RASAero. "
            "Click inside RASAero, then run this tool again.",
            flush=True,
        )
        return 3
    print(f"Typing into: {title}", flush=True)

    # select Tools -> Run test
    pyautogui.hotkey("alt", "t")
    pyautogui.typewrite("enter")
    time.sleep(0.25)
    # set pause in between keys to a small value
    pyautogui.PAUSE = 0.01
    for alpha in ALPHAS_DEG:
        # enter dump path
        pyautogui.hotkey("ctrl", "a")
        pyautogui.typewrite(f"{folder}/alpha{alpha}.txt")
        # set alpha
        pyautogui.typewrite(["tab", "tab", "tab", "tab"])
        pyautogui.hotkey("ctrl", "a")
        pyautogui.typewrite(str(alpha))
        # set nozzle diameter [in]
        pyautogui.typewrite(["tab"])
        pyautogui.hotkey("ctrl", "a")
        pyautogui.typewrite(f"{nozzle_in:g}")
        # run test
        pyautogui.typewrite(["tab", "tab", "\n"])
        time.sleep(0.3)
        # tab back to file select
        pyautogui.typewrite(["tab", "tab", "tab", "tab"])
        print(f"Finished {alpha}", flush=True)
    return 0


# --- Prompts ---


def ask_float(question: str, minimum: float | None = None) -> float:
    """Ask until the answer is a number (at least ``minimum`` when given)."""
    while True:
        try:
            value = float(input(question).strip())
        except ValueError:
            print("Enter a number.")
            continue
        if minimum is None or value >= minimum:
            return value
        print(f"Enter {minimum:g} or more.")


def output_path(answer: str) -> Path:
    """Where the CSV goes: Downloads when blank, a folder gets the default name, else the file named."""
    path = Path(answer.strip().strip('"')).expanduser() if answer.strip() else Path.home() / "Downloads"
    return path if path.suffix.lower() == ".csv" else path / DEFAULT_NAME


def main() -> int:
    """Ask, sweep, convert; returns the exit code."""
    reference_point_in = ask_float("Reference point [in]: ")
    reference_length_in = ask_float("Reference length [in, body diameter]: ", minimum=0.001)
    nozzle_in = ask_float("Nozzle exit diameter [in] (0 if already in the model): ", minimum=0)
    output = output_path(input("Output file or folder (blank = Downloads): "))
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        code = run_sweep(Path(tmp), WINDOW, COUNTDOWN_S, nozzle_in=nozzle_in)
        if code:
            return code
        count = convert(Path(tmp), output, reference_point_in, reference_length_in)
    print(f"Wrote {count} points to {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

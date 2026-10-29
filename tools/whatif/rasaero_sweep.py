"""Run RASAero II's "Run Test" for angles of attack 0 to 30 degrees and write alpha0.txt ... alpha30.txt.

    python tools/whatif/rasaero_sweep.py --out aero_modeling/IREC_2027/JARVIS/alpha

This is brute_force_aero.py (Luke Adams, Sarah Kinney, Nacho Durante) with the output folder as an
argument, a countdown, and a check that the window it switched to really is RASAero before it types.
The key presses are the original ones. Windows only (pyautogui; pip install pyautogui).

Before running: open RASAero II with the rocket loaded, save it over the .CDX1 the page reads, and make
RASAero the window you touched last. The script presses Alt+Tab and types into whatever that reaches.
Moving the mouse into a screen corner stops pyautogui.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ALPHAS = range(0, 31)
NOZZLE_EXIT_DIAMETER_IN = 0  # already accounted for in the model


def foreground_title() -> str:
    """Title of the window in front (Windows only; run_sweep checks the platform first)."""
    import ctypes  # noqa: PLC0415  (Windows only)

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    handle = user32.GetForegroundWindow()
    buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(handle, buffer, 512)
    return buffer.value


def run_sweep(out_dir: Path, window: str, countdown: int, expect: str | None = None,
              nozzle_in: float = NOZZLE_EXIT_DIAMETER_IN) -> int:
    """Switch to RASAero and dump alpha0.txt to alpha30.txt into ``out_dir``; returns an exit code."""
    if sys.platform != "win32":
        print("Step 3 only works on Windows, where RASAero runs.", flush=True)
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
            "Click inside RASAero, then click back on the page and press the Create button again.",
            flush=True,
        )
        return 3
    print(f"Typing into: {title}", flush=True)
    # RASAero may show the open file in its title: if that names another .CDX1, it would measure the wrong rocket
    if expect and ".cdx1" in title.lower() and expect.lower() not in title.lower():
        print(f"Stopped before typing anything. RASAero's title says another file is open ({title}), not {expect}. "
              f"In RASAero open {expect} (File, Open), click back on the page and press the Create button again.", flush=True)
        return 5

    # select Tools -> Run test
    pyautogui.hotkey("alt", "t")
    pyautogui.typewrite("enter")
    time.sleep(0.25)
    # set pause in between keys to a small value
    pyautogui.PAUSE = 0.01
    for alpha in ALPHAS:
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


def main(argv: list[str] | None = None) -> int:
    """Command line entry; returns the exit code."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", maxsplit=1)[0])
    parser.add_argument("--out", type=Path, required=True, help="folder for alpha0.txt ... alpha30.txt")
    parser.add_argument("--window", default="RASAero", help="text the RASAero window title contains")
    parser.add_argument("--countdown", type=int, default=5, help="seconds before switching windows")
    parser.add_argument("--expect", default=None, help="the .CDX1 RASAero should have open (checked against its window title)")
    parser.add_argument("--nozzle", type=float, default=None,
                        help=f"nozzle exit diameter [in] typed into Run Test (asked for when left out; blank = {NOZZLE_EXIT_DIAMETER_IN})")
    args = parser.parse_args(argv)
    nozzle = args.nozzle
    while nozzle is None:
        # only ask at a terminal: the Jarvis helper runs this with no keyboard attached and gets the default
        text = input(f"Nozzle exit diameter [in] (blank = {NOZZLE_EXIT_DIAMETER_IN}): ").strip() if sys.stdin and sys.stdin.isatty() else ""
        try:
            nozzle = float(text or NOZZLE_EXIT_DIAMETER_IN)
        except ValueError:
            print(f"{text!r} is not a number.")
    if nozzle < 0:
        parser.error("--nozzle cannot be negative")
    return run_sweep(args.out, args.window, args.countdown, args.expect, nozzle)


if __name__ == "__main__":
    sys.exit(main())

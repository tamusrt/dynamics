"""Run RASAero II's "Run Test" for angles of attack 0 to 30 degrees and write alpha0.txt ... alpha30.txt.

    python tools/whatif/rasaero_sweep.py --out aero_modeling/IREC_2027/RASA/alpha

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


def foreground_title() -> str | None:
    """Title of the window in front (Windows only), or None when it cannot be read."""
    if sys.platform != "win32":
        return None
    import ctypes  # noqa: PLC0415  (Windows only)

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    handle = user32.GetForegroundWindow()
    buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(handle, buffer, 512)
    return buffer.value


def rasaero_windows(window: str) -> dict[int, str]:
    """Visible top-level windows whose title contains ``window`` (Windows only): handle -> title."""
    import ctypes  # noqa: PLC0415  (Windows only)
    from ctypes import wintypes  # noqa: PLC0415

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    found: dict[int, str] = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(handle, _):
        if user32.IsWindowVisible(handle):
            buffer = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(handle, buffer, 512)
            if window.lower() in buffer.value.lower():
                found[int(handle)] = buffer.value
        return True

    user32.EnumWindows(visit, 0)
    return found


def open_in_rasaero(cdx: Path, window: str, pyautogui, wait_s: float = 45.0) -> tuple[str | None, int | None]:
    """Open ``cdx`` the way a double-click does, wait for its new RASAero window and bring it to the front.

    Returns (None, the window) when RASAero with the file is the window in front, or (what went wrong, None).
    """
    import ctypes  # noqa: PLC0415  (Windows only)
    import os  # noqa: PLC0415

    before = set(rasaero_windows(window))
    print(f"Opening {cdx.name} in RASAero", flush=True)
    try:
        os.startfile(str(cdx.resolve()))  # type: ignore[attr-defined]  # noqa: S606
    except OSError as exc:
        return f"Windows could not open {cdx.name} ({exc}). Is RASAero II the program that opens .CDX1 files?", None
    deadline = time.time() + wait_s
    new: list[int] = []
    while time.time() < deadline and not new:
        time.sleep(0.5)
        new = [h for h in rasaero_windows(window) if h not in before]
    if not new:
        return f"No new RASAero window appeared within {wait_s:.0f} s after opening {cdx.name}.", None
    time.sleep(4.0)  # let RASAero finish loading the rocket
    handle = max(new)
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    pyautogui.press("alt")  # Windows only lets a program bring a window forward right after a key press
    user32.ShowWindow(handle, 9)  # SW_RESTORE
    user32.SetForegroundWindow(handle)
    time.sleep(0.8)
    if user32.GetForegroundWindow() != handle:
        return "RASAero opened the file but could not be brought to the front.", None
    print(f"RASAero has {cdx.name} open", flush=True)
    return None, handle


def close_rasaero(handle: int) -> None:
    """End the RASAero that was opened for the sweep (it saved nothing, so nothing is lost)."""
    import ctypes  # noqa: PLC0415  (Windows only)
    from ctypes import wintypes  # noqa: PLC0415

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32  # type: ignore[attr-defined]
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    process = kernel32.OpenProcess(0x0001, False, pid.value)  # PROCESS_TERMINATE
    if process:
        kernel32.TerminateProcess(process, 0)
        kernel32.CloseHandle(process)
        print("Closed the RASAero window that was opened for this", flush=True)


def run_sweep(out_dir: Path, window: str, countdown: int, open_file: Path | None = None) -> int:
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

    opened = None
    if open_file is not None:  # open the file afresh, so RASAero measures exactly what is on disk
        problem, opened = open_in_rasaero(open_file, window, pyautogui)
        if problem:
            print(f"Stopped before typing anything. {problem} If this keeps happening, stop the helper and start it with "
                  f"--no-open (python tools/whatif/local_helper.py --no-open), open {open_file.name} in RASAero yourself, "
                  "click inside it, then click back on the page and press the Create button.", flush=True)
            return 4
    else:
        # alt-tab to rasaero
        pyautogui.hotkey("alt", "tab")
        time.sleep(0.8)
    title = foreground_title()
    if title is None or window.lower() not in title.lower():
        print(
            f"Stopped. The window in front was {title!r}, not RASAero. "
            "Click inside RASAero, then click back on the page and press the Create button again.",
            flush=True,
        )
        return 3

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
        pyautogui.typewrite(str(NOZZLE_EXIT_DIAMETER_IN))
        # run test
        pyautogui.typewrite(["tab", "tab", "\n"])
        time.sleep(0.3)
        # tab back to file select
        pyautogui.typewrite(["tab", "tab", "tab", "tab"])
        print(f"Finished {alpha}", flush=True)
    if opened is not None:
        time.sleep(1.5)  # let RASAero finish writing the last file
        close_rasaero(opened)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Command line entry; returns the exit code."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", maxsplit=1)[0])
    parser.add_argument("--out", type=Path, required=True, help="folder for alpha0.txt ... alpha30.txt")
    parser.add_argument("--window", default="RASAero", help="text the RASAero window title contains")
    parser.add_argument("--countdown", type=int, default=5, help="seconds before switching windows")
    parser.add_argument("--open", type=Path, default=None, help="open this .CDX1 in a new RASAero window first, and close it after")
    args = parser.parse_args(argv)
    return run_sweep(args.out, args.window, args.countdown, args.open)


if __name__ == "__main__":
    sys.exit(main())

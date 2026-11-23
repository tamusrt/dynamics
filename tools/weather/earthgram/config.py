"""Where Earth-GRAM 2016 is installed, and checks that it is usable.

Set EARTHGRAM_HOME to the install folder (the one holding "PC Executables",
IOfiles, NCEPdata and RRAdata). Default: C:\\EarthGRAM2016Version2.0
"""
import os
from pathlib import Path

DEFAULT_HOME = r"C:\EarthGRAM2016Version2.0"
EXE_RELPATH = Path("PC Executables") / "GRAM2016.exe"


class GramNotFound(RuntimeError):
    pass


def gram_home() -> Path:
    return Path(os.environ.get("EARTHGRAM_HOME", DEFAULT_HOME))


def gram_exe(home: Path | None = None) -> Path:
    return (home or gram_home()) / EXE_RELPATH


def data_dirs(home: Path | None = None) -> dict[str, Path]:
    home = home or gram_home()
    return {
        "atmpath": home / "IOfiles",
        "NCEPpath": home / "NCEPdata" / "FixedBin",
        "rrapath": home / "RRAdata",
    }


def check_install(home: Path | None = None) -> Path:
    """Raise GramNotFound with a clear reason if GRAM can't run from `home`."""
    home = home or gram_home()
    exe = gram_exe(home)
    if not exe.is_file():
        raise GramNotFound(f"GRAM2016.exe not found at {exe} (set EARTHGRAM_HOME)")
    for d in data_dirs(home).values():
        if not d.is_dir():
            raise GramNotFound(f"GRAM data folder missing: {d}")
    return home


def is_installed() -> bool:
    try:
        check_install()
        return True
    except GramNotFound:
        return False

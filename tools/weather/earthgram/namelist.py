"""Build the GRAM2016 namelist (settings file) from Python inputs.

GRAM reads this file by POSITION (each line is `key = value`, and the key is
ignored), so the order of FIELDS below must match Init::namelist() in
Source/Init.cpp exactly. Do not reorder.
"""
import math
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

from . import config

# WGS-84 flattening, for geodetic -> geocentric latitude (GRAM's phi1 is geocentric).
_WGS84_F = 1 / 298.257223563


def geodetic_to_geocentric(lat_deg: float) -> float:
    lat = math.radians(lat_deg)
    return math.degrees(math.atan((1 - _WGS84_F) ** 2 * math.tan(lat)))


@dataclass
class GramInputs:
    """One GRAM run. Defaults are for a vertical profile above a launch site."""
    # file paths (None = the GRAM install's folder, filled in by `render`;
    # output names are relative to the run folder)
    atmpath: str | None = None
    NCEPpath: str | None = None
    trapath: str = "null"
    prtpath: str = "null"
    nprpath: str = "special.txt"
    conpath: str = "null"
    rrapath: str | None = None
    rralist: str = "rrasites.txt"
    profile: str = "null"
    # start point
    h1: float = 0.0          # km above MSL
    phi1: float = 0.0        # GEOCENTRIC latitude, deg N
    thet1: float = 0.0       # longitude, deg E
    # solar / geomagnetic (only matter above ~90 km)
    f10: float = 150.0
    f10b: float = 150.0
    ap: float = 16.3
    s10: float = 0.0
    s10b: float = 0.0
    xm10: float = 0.0
    xm10b: float = 0.0
    y10: float = 0.0
    y10b: float = 0.0
    dstdtc: float = 0.0
    # UTC start time
    mn: int = 1
    ida: int = 1
    iyr: int = 2026
    ihro: int = 0
    mino: int = 0
    seco: float = 0.0
    # path increments: a vertical column by default
    dphi: float = 0.0
    dthet: float = 0.0
    dhgt: float = 0.25       # km
    nmax: int = 1
    delt: float = 0.0
    iopt: int = 0
    iaux: int = 0
    iprt: int = 0
    inpr: int = 1
    icon: int = 0
    NCEPyr: int = 9715       # 1997-2015 climatology (the only one installed)
    NCEPhr: int = 0          # 0 = pick the 00/06/12/18 UT slot from ihro; 5 = all-day
    nr1: int = 1234
    mc: int = 0
    rpscale: float = 1.0
    ruscale: float = 1.0
    rwscale: float = 1.0
    iurra: int = 1           # use Range Reference Atmosphere data when a site is near
    iyrrra: int = 3          # 2013 RRAs
    sitelim: float = 2.5
    sitenear: float = 0.5
    initpert: int = 0
    rdinit: float = 0.0
    rtinit: float = 0.0
    ruinit: float = 0.0
    rvinit: float = 0.0
    rwinit: float = 0.0
    patchy: float = 0.0
    itherm: int = 1
    z0in: float = -1
    ibltest: int = 0

    def set_time(self, when_utc: datetime) -> None:
        if when_utc.tzinfo is None:
            raise ValueError("time must be timezone-aware")
        t = when_utc.astimezone(timezone.utc)
        self.iyr, self.mn, self.ida = t.year, t.month, t.day
        self.ihro, self.mino = t.hour, t.minute
        self.seco = t.second + t.microsecond / 1e6

    def validate(self) -> None:
        if not 1 <= self.nr1 <= 900_000_000:
            raise ValueError("seed (nr1) must be between 1 and 9e8")
        if self.nmax < 1 or self.nmax > 50_000:
            raise ValueError("nmax must be 1..50000")
        for s in ("rpscale", "ruscale", "rwscale"):
            if not 0.1 <= getattr(self, s) <= 2.0:
                raise ValueError(f"{s} must be 0.1..2.0")
        if self.mc < 0:
            raise ValueError("mc must be >= 0")


def _fmt(v) -> str:
    return repr(v) if isinstance(v, float) else str(v)


def dir_arg(path) -> str:
    """A folder as GRAM wants it: with a trailing backslash (it appends file names)."""
    return str(path).rstrip("\\/") + "\\"


def render(inp: GramInputs, home: Path | None = None) -> str:
    """Namelist text for `inp`, with unset data folders taken from the GRAM install."""
    inp.validate()
    defaults = {k: dir_arg(d) for k, d in config.data_dirs(home).items()}
    lines = [" $namein_e10"]
    for f in fields(inp):
        v = getattr(inp, f.name)
        v = _fmt(defaults[f.name] if v is None else v)
        # GRAM reads each value with `>>`, which stops at whitespace
        if " " in v:
            raise ValueError(f"GRAM can't read {f.name} containing a space: {v!r}")
        lines.append(f"  {f.name} = {v}")
    lines.append(" $End")
    return "\n".join(lines) + "\n"

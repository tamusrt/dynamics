"""Weather at a launch site for a given day, from Earth-GRAM 2016.

    from earthgram import LaunchSite, profile, monte_carlo, launch_window
    site = LaunchSite("West TX", lat=31.024, lon=-103.6616, elev_m=900)
    prof = profile(site, "2026-06-15 13:00", tz="America/Chicago")
    prof.mean          # mean + standard deviation per altitude
    prof.sigma(-3)     # mean - 3 sigma profile
    mc = monte_carlo(site, "2026-06-15 13:00", tz="America/Chicago", n=500)
    mc.runs            # one row per run per altitude
    mc.percentiles([5, 50, 95])

What the date means: below ~30 km GRAM uses NCEP monthly climatology (1997-2015)
for the 00/06/12/18 UT slot nearest the requested time. Any day in the same month
at the same slot gives the same lower-atmosphere statistics. The exact day and the
solar inputs only matter higher up.

Units: SI. Altitude in m above MSL, temp in K, p in Pa, rho in kg/m^3, winds in m/s
(u toward east, v toward north), dir_from in deg clockwise from north (the
direction the wind blows from).
"""
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

from .namelist import GramInputs, geodetic_to_geocentric
from .runner import run

STATE = ["temp", "p", "rho", "u", "v"]


@dataclass(frozen=True)
class LaunchSite:
    name: str
    lat: float      # geodetic deg N
    lon: float      # deg E (west negative)
    elev_m: float   # ground height above MSL


def _utc(when, tz: str | None) -> pd.Timestamp:
    t = pd.Timestamp(when)
    if t.tzinfo is None:
        if tz is None:
            raise ValueError("give a timezone-aware time or tz= (e.g. tz='America/Chicago' or 'UTC')")
        t = t.tz_localize(tz)
    return t.tz_convert("UTC")


def _inputs(site: LaunchSite, t_utc: pd.Timestamp, top_m: float, step_m: float, gram: dict) -> GramInputs:
    if top_m <= site.elev_m:
        raise ValueError("top_m must be above the site elevation")
    if step_m <= 0:
        raise ValueError("step_m must be positive")
    nmax = int(np.floor((top_m - site.elev_m) / step_m + 1e-9)) + 1
    inp = GramInputs(
        h1=site.elev_m / 1000.0,
        phi1=geodetic_to_geocentric(site.lat),
        thet1=site.lon,
        dhgt=step_m / 1000.0,
        nmax=nmax,
    )
    inp.set_time(t_utc.to_pydatetime())
    return replace(inp, **gram)  # TypeError on an unknown GRAM input


@dataclass
class Profile:
    """Mean atmosphere and its standard deviations above a site at one time.

    `mean` has one row per altitude: the mean state, its standard deviations,
    GRAM's mean wind SPEED (`speed`, the average scalar speed, so larger than the
    length of the mean wind vector) and the mean wind's direction (`dir_from`).
    """
    site: LaunchSite
    time_utc: pd.Timestamp
    mean: pd.DataFrame = field(repr=False)

    def sigma(self, k: float) -> pd.DataFrame:
        """mean + k*sd for each quantity (temp, p, rho, u, v, speed), each shifted independently.

        This is a quick bounding case, not a physically consistent atmosphere; use
        monte_carlo() for dispersion.
        """
        m = self.mean
        out = m[["alt_m"]].copy()
        for c in (*STATE, "speed"):
            out[c] = m[c] + k * m[f"{c}_sd"]
        out["speed"] = out["speed"].clip(lower=0.0)
        out["dir_from"] = m["dir_from"]
        return out

    def at(self, alt_m: float) -> pd.Series:
        """Mean profile linearly interpolated to one altitude (m above MSL)."""
        m = self.mean
        if not m["alt_m"].min() <= alt_m <= m["alt_m"].max():
            raise ValueError(f"{alt_m} m is outside the profile")
        return pd.Series({c: np.interp(alt_m, m["alt_m"], m[c]) for c in m.columns if c != "dir_from"})


@dataclass
class MonteCarlo(Profile):
    """`n` perturbed atmospheres (correlated with height) plus the mean they scatter around.

    `runs` has one row per run per altitude, ordered run by run.
    """
    runs: pd.DataFrame = field(repr=False)

    @property
    def n(self) -> int:
        return len(self.runs) // len(self.mean)

    def values(self, col: str) -> np.ndarray:
        """`col` of every run as an (n runs, n altitudes) array."""
        return self.runs[col].to_numpy().reshape(self.n, len(self.mean))

    def percentiles(self, q=(5, 50, 95), cols=(*STATE, "speed")) -> pd.DataFrame:
        """One row per altitude; columns like temp_p5, temp_p50, temp_p95, speed_p95."""
        out = {"alt_m": self.mean["alt_m"].to_numpy()}
        for c in cols:
            for p, row in zip(q, np.percentile(self.values(c), q, axis=0)):
                out[f"{c}_p{p:g}"] = row
        return pd.DataFrame(out)

    def run_profile(self, i: int) -> pd.DataFrame:
        if not 0 <= i < self.n:
            raise IndexError(f"run {i} of {self.n}")
        nlev = len(self.mean)
        return self.runs.iloc[i * nlev:(i + 1) * nlev].drop(columns="run").reset_index(drop=True)


def profile(site: LaunchSite, when, tz: str | None = None, *, top_m: float = 30_000,
            step_m: float = 250, keep_dir: Path | None = None, **gram) -> Profile:
    """Mean and standard deviation profile above `site` at `when` (local time with tz=, or aware)."""
    t = _utc(when, tz)
    inp = _inputs(site, t, top_m, step_m, {"mc": 0, **gram})
    mean, _ = run(inp, keep_dir=keep_dir)
    return Profile(site, t, mean)


def monte_carlo(site: LaunchSite, when, tz: str | None = None, *, n: int = 100, seed: int = 1234,
                top_m: float = 30_000, step_m: float = 250, keep_dir: Path | None = None,
                **gram) -> MonteCarlo:
    """`n` GRAM Monte Carlo atmospheres. Same seed -> same atmospheres."""
    if n < 1:
        raise ValueError("n must be >= 1")
    t = _utc(when, tz)
    inp = _inputs(site, t, top_m, step_m, {"mc": n, "nr1": seed, **gram})
    return MonteCarlo(site, t, *run(inp, keep_dir=keep_dir))


def launch_window(site: LaunchSite, date, tz: str, hours=range(9, 18), *, top_m: float = 30_000,
                  step_m: float = 250, **gram) -> pd.DataFrame:
    """Mean profiles for each local hour on `date`, stacked with a `local_time` column.

    GRAM's lower atmosphere changes only between 6-hour UT slots, so neighbouring
    hours often come back identical. The hours run in parallel (each GRAM run has
    its own folder).
    """
    day = pd.Timestamp(date).normalize()
    times = [(day + pd.Timedelta(hours=h)).tz_localize(tz) for h in hours]

    def one(local):
        return profile(site, local, top_m=top_m, step_m=step_m, **gram).mean.assign(local_time=local)

    with ThreadPoolExecutor(max_workers=min(len(times), os.cpu_count() or 1) or 1) as pool:
        frames = list(pool.map(one, times))
    out = pd.concat(frames, ignore_index=True)
    return out[["local_time", *out.columns[:-1]]]

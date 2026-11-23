"""Read GRAM's special.txt (one row per height per Monte Carlo run) into SI DataFrames."""
from pathlib import Path

import numpy as np
import pandas as pd

# special.txt header -> our column name. Units are already SI except heights (km)
# and the density/pressure standard deviations (% of mean).
COLUMNS = {
    "Time": "time_s",
    "Hgtkm": "alt_km",
    "GeocenLat": "lat_gc",
    "Lon(East)": "lon",
    "DensMean": "rho_mean",     # kg/m^3
    "PresMean": "p_mean",       # Pa
    "Tmean": "temp_mean",          # K
    "EWmean": "u_mean",         # m/s, toward east
    "NSmean": "v_mean",         # m/s, toward north
    "DensPert": "rho",          # perturbed (one random draw) values
    "PresPert": "p",
    "Tpert": "temp",
    "EWpert": "u",
    "NSpert": "v",
    "SDden%": "rho_sd_pct",
    "SDprs%": "p_sd_pct",
    "SDtemK": "temp_sd",
    "SDuwnd": "u_sd",
    "SDvwnd": "v_sd",
    "SDwwnd": "w_sd",
    "Wpert": "w",
    "SpdAvg": "speed_mean",
    "SpdStd": "speed_sd",
    "SOSmean": "sos_mean",
    "SOSpert": "sos",
    "Sev": "severity",
}


def wind_dir_from(u, v):
    """Meteorological direction the wind blows FROM, deg clockwise from north."""
    return np.mod(np.degrees(np.arctan2(-u, -v)), 360.0)


# per-altitude statistics (identical in every run) -> name in the `mean` table
MEAN_COLS = {
    "alt_m": "alt_m",
    "temp_mean": "temp", "p_mean": "p", "rho_mean": "rho", "u_mean": "u", "v_mean": "v",
    "temp_sd": "temp_sd", "p_sd": "p_sd", "rho_sd": "rho_sd", "u_sd": "u_sd", "v_sd": "v_sd",
    "speed_mean": "speed", "speed_sd": "speed_sd", "sos_mean": "sos",
}
RUN_COLS = ["run", "alt_m", "temp", "p", "rho", "u", "v", "w", "w_sd", "sos"]


def read_special(path: Path, nmax: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Parse special.txt into (mean, runs).

    mean: one row per altitude - GRAM's mean state and standard deviations, plus its
          mean wind speed (`speed`, the average scalar speed) and the mean wind's
          direction (`dir_from`).
    runs: one row per run per altitude, run-major - the perturbed state of each
          Monte Carlo run, with that run's wind speed and direction. w_sd is here
          because GRAM derives it from each run's perturbed wind.
    `nmax` = heights per run, used to number the runs.
    """
    df = pd.read_csv(path, sep=r"\s+")
    missing = set(COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"{path}: unexpected special.txt header, missing {sorted(missing)}")
    df = df.rename(columns=COLUMNS)
    if len(df) == 0 or len(df) % nmax:
        raise ValueError(f"{path}: {len(df)} rows is not a whole number of {nmax}-level runs")
    df["run"] = np.arange(len(df)) // nmax
    df["alt_m"] = df["alt_km"] * 1000.0
    df["rho_sd"] = df["rho_mean"] * df["rho_sd_pct"] / 100.0
    df["p_sd"] = df["p_mean"] * df["p_sd_pct"] / 100.0

    stats = df[list(MEAN_COLS)].to_numpy().reshape(-1, nmax, len(MEAN_COLS))
    if not (stats == stats[0]).all():
        raise ValueError(f"{path}: GRAM's mean/sd columns differ between runs")
    mean = df.iloc[:nmax][list(MEAN_COLS)].rename(columns=MEAN_COLS).reset_index(drop=True)
    mean["dir_from"] = wind_dir_from(mean["u"], mean["v"])

    runs = df[RUN_COLS].reset_index(drop=True)
    runs.insert(7, "speed", np.hypot(runs["u"], runs["v"]))
    runs.insert(8, "dir_from", wind_dir_from(runs["u"], runs["v"]))
    return mean, runs

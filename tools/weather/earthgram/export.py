"""Write GRAM profiles out for other tools."""
from pathlib import Path

import pandas as pd

M_TO_FT = 1 / 0.3048
MS_TO_MPH = 1 / 0.44704


def openrocket_wind_csv(profile_df: pd.DataFrame, path: Path) -> Path:
    """Multi-level wind table for OpenRocket's wind-profile CSV import.

    `profile_df` is Profile.mean, Profile.sigma(k) or MonteCarlo.run_profile(i).
    Columns: altitude (m above MSL), speed (m/s), direction (deg, blowing FROM),
    standard deviation (m/s; 0 when the frame has none). Choose these units in
    OpenRocket's import dialog. Altitudes are above MSL: if your OpenRocket site
    altitude is set, check whether the importer expects AGL.
    """
    out = pd.DataFrame({
        "altitude": profile_df["alt_m"],
        "speed": profile_df["speed"],
        "direction": profile_df["dir_from"],
        "stddev": profile_df.get("speed_sd", 0.0),
    })
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False, float_format="%.3f")
    return path


def summary_table(profile_df: pd.DataFrame, every_m: float = 1000) -> pd.DataFrame:
    """Rows roughly every `every_m` metres, with altitude also in ft and speed in mph, for printing."""
    alts = profile_df["alt_m"]
    keep = ((alts - alts.iloc[0]) % every_m).round(6) == 0
    t = profile_df[keep].copy()
    t.insert(1, "alt_ft", t["alt_m"] * M_TO_FT)
    if "speed" in t:
        t["speed_mph"] = t["speed"] * MS_TO_MPH
    return t

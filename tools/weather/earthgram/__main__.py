"""Command line: weather above a launch site for one local time.

    python -m earthgram --lat 31.024 --lon -103.6616 --elev 900 --time "2026-06-15 13:00" --tz America/Chicago
    python -m earthgram ... --mc 500 --out results/

Run from tools/weather (or put tools/weather on PYTHONPATH).
"""
import argparse
from pathlib import Path

from . import LaunchSite, monte_carlo, openrocket_wind_csv, profile, summary_table


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="earthgram", description=__doc__.splitlines()[0])
    ap.add_argument("--lat", type=float, required=True, help="geodetic latitude, deg N")
    ap.add_argument("--lon", type=float, required=True, help="longitude, deg E (west negative)")
    ap.add_argument("--elev", type=float, required=True, help="site elevation, m above MSL")
    ap.add_argument("--time", required=True, help='local time, e.g. "2026-06-15 13:00"')
    ap.add_argument("--tz", default="UTC", help="IANA timezone of --time")
    ap.add_argument("--top", type=float, default=30_000, help="top altitude, m MSL")
    ap.add_argument("--step", type=float, default=250, help="altitude step, m")
    ap.add_argument("--mc", type=int, default=0, help="number of Monte Carlo runs (0 = mean only)")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--out", type=Path, help="folder for CSV outputs")
    a = ap.parse_args(argv)

    site = LaunchSite("site", a.lat, a.lon, a.elev)
    if a.mc:
        res = monte_carlo(site, a.time, a.tz, n=a.mc, seed=a.seed, top_m=a.top, step_m=a.step)
    else:
        res = profile(site, a.time, a.tz, top_m=a.top, step_m=a.step)

    print(f"Earth-GRAM 2016 at {a.lat}, {a.lon} ({a.elev:.0f} m), {res.time_utc:%Y-%m-%d %H:%M} UTC")
    # column -> (format, unit)
    cols = {"alt_m": ("{:.0f}", "m"), "alt_ft": ("{:.0f}", "ft"), "temp": ("{:.1f}", "K"),
            "temp_sd": ("{:.2f}", "K"), "p": ("{:.0f}", "Pa"), "rho": ("{:.4f}", "kg/m^3"),
            "speed": ("{:.2f}", "m/s"), "speed_sd": ("{:.2f}", "m/s"), "speed_mph": ("{:.1f}", "mph"),
            "dir_from": ("{:.0f}", "deg, wind blows FROM")}
    table = summary_table(res.mean)[list(cols)]
    print(table.to_string(index=False, formatters={c: f.format for c, (f, _) in cols.items()}))
    print("units: " + ", ".join(f"{c} {u}" for c, (_, u) in cols.items()))

    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
        res.mean.to_csv(a.out / "mean_profile.csv", index=False)
        openrocket_wind_csv(res.mean, a.out / "openrocket_wind.csv")
        if a.mc:
            res.runs.to_csv(a.out / "monte_carlo_runs.csv", index=False)
            res.percentiles().to_csv(a.out / "percentiles.csv", index=False)
        print(f"\nwrote CSVs to {a.out.resolve()}")


if __name__ == "__main__":
    main()

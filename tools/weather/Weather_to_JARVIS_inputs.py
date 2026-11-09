"""Weather inputs for the flight sim at a launch date, hour and place, in one of two modes.

    python tools/weather/Weather_to_JARVIS_inputs.py                     (asks for everything)
    python tools/weather/Weather_to_JARVIS_inputs.py jarvis --site "41.3, -112.9, 1310" --date 2027-06-20 --hour 7
    python tools/weather/Weather_to_JARVIS_inputs.py monte-carlo --site "32.99, -106.97" --date 2026-11-10 --hour 7

jarvis       site_<date>.toml: launch_latitude, launch_elevation, [atmosphere] and [wind] as single values,
             the most likely weather for that hour.
monte-carlo  site_<date>_monte_carlo.toml: mean and standard deviation of the same inputs.

Where the numbers come from, by how far away the date is:

    past (archive)          jarvis: the recorded hour.     monte-carlo: mean = the recorded hour, std = climate
    next ~2 weeks           jarvis: the forecast.          monte-carlo: the 51-member ECMWF ensemble at that hour
    further out             jarvis: the climate mean.      monte-carlo: the climate mean and std
    "climate" = the same place and hour over +-WINDOW_DAYS days in each of the last CLIMATE_YEARS years (ERA5),
    each moved along its variable's straight-line trend over those years to the launch year. A trend is
    used only where it stands out from the year-to-year noise (|t| >= TREND_T); otherwise it is left flat.

The elevation is looked up (90 m terrain model) when not given, and the weather is corrected to it.
Weather comes from Open-Meteo (free, no key; standard library only). Needs internet.
The rail, engine and gravity are not weather: they stay as they are in your sim.toml and vehicle file.
ERA5 smooths the weather, so the climate spreads run a little low, most for wind.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
ENSEMBLE_MODEL = "ecmwf_ifs025"  # 51 members; reaches about 14 days ahead and keeps about 3 days back
ARCHIVE_LAG_DAYS = 7  # the archive trails real time by about 5 days
FORECAST_DAYS = 15  # the forecast reaches 16 days ahead
CLIMATE_YEARS = 30  # past years behind the statistics; enough for a trend to stand out from year-to-year noise
FETCH_THREADS = 4  # parallel archive requests; more than this and the free tier refuses them
RETRIES = 6  # waits of 1, 2, 4 ... s when Open-Meteo says it is busy
TREND_T = 2.0  # a variable's trend is used only when its slope is at least this many standard errors
WINDOW_DAYS = 7  # days either side of the date, each year
HOUR_SPAN = 1  # hours either side of the launch hour
HOURLY = (
    "temperature_2m",
    "relative_humidity_2m",
    "surface_pressure",
    "wind_speed_100m",  # UniformWind has one wind for every altitude: the 100 m one is closest to the flight
    "wind_direction_100m",
)
SEA_LEVEL_PA = 101325.0
CACHE_DIR = Path.home() / ".cache" / "weather_to_jarvis"  # finished past years never change, so they are kept

# (name, TOML unit) of each sampled input, in the order of a sample tuple
VARIABLES = (
    ("launch_temperature", "degC"),
    ("barometric_pressure", "Pa"),
    ("relative_humidity", "0 to 1"),
    ("wind_east", "m/s"),
    ("wind_north", "m/s"),
)

Sample = tuple[float, float, float, float, float]


def standard_pressure(altitude_m: float) -> float:
    """Pressure of the 1976 standard atmosphere in Pa below 11 km."""
    return SEA_LEVEL_PA * (1.0 - 2.25577e-5 * altitude_m) ** 5.25588


def get_json(url: str, params: dict, cache: bool = False) -> dict:
    """One Open-Meteo request; with ``cache`` the answer is kept on disk and reused (finished past years only)."""
    query = f"{url}?{urlencode(params)}"
    cached = CACHE_DIR / f"{hashlib.sha1(query.encode()).hexdigest()}.json"
    if cache and cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    for attempt in range(RETRIES + 1):
        try:
            with urlopen(query, timeout=60) as response:
                data = json.load(response)
            break
        except HTTPError as error:
            if error.code == 429 and attempt < RETRIES:  # busy or over the rate limit: wait and try again
                time.sleep(2**attempt)
                continue
            raise SystemExit(f"Open-Meteo refused {url}: {error.read().decode(errors='replace')}") from error
        except (URLError, TimeoutError) as error:
            raise SystemExit(f"Could not reach Open-Meteo ({error}). Check the internet connection.") from error
    if cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(data), encoding="utf-8")
    return data


def ground_elevation(lat: float, lon: float) -> float:
    """Terrain height in m from Open-Meteo's 90 m elevation model."""
    return float(get_json(ELEVATION_URL, {"latitude": lat, "longitude": lon})["elevation"][0])


def weather_params(lat: float, lon: float, elevation_m: float, start: date, end: date) -> dict:
    return {
        "latitude": lat,
        "longitude": lon,
        "elevation": f"{elevation_m:.1f}",  # Open-Meteo corrects temperature and pressure to this height
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": ",".join(HOURLY),
        "wind_speed_unit": "ms",
        "timezone": "auto",
    }


def to_sample(values: list, elevation_m: float) -> Sample | None:
    """Open-Meteo's hourly values as a sample, or None when any is missing.

    Pressure is the site pressure reduced to sea level along the standard atmosphere, as the sim wants it;
    wind is the east and north components of the wind's velocity.
    """
    if any(value is None for value in values):
        return None
    temperature_c, humidity_percent, surface_hpa, speed, from_deg = values
    return (
        temperature_c,
        surface_hpa * 100.0 * SEA_LEVEL_PA / standard_pressure(elevation_m),
        humidity_percent / 100.0,
        -speed * math.sin(math.radians(from_deg)),  # the wind blows away from where it comes from
        -speed * math.cos(math.radians(from_deg)),
    )


def fetch(
    url: str, lat: float, lon: float, elevation_m: float, start: date, end: date, cache: bool = False
) -> list[tuple[datetime, Sample]]:
    """Hourly weather at a place, as (local time, sample) for every hour with all values present."""
    data = get_json(url, weather_params(lat, lon, elevation_m, start, end), cache)
    hourly = data["hourly"]
    rows = []
    for index, stamp in enumerate(hourly["time"]):
        sample = to_sample([hourly[name][index] for name in HOURLY], float(data["elevation"]))
        if sample is not None:
            rows.append((datetime.fromisoformat(stamp), sample))
    return rows


def ensemble_samples(lat: float, lon: float, elevation_m: float, day: date, hour: int) -> list[Sample]:
    """One sample per ensemble member at that hour; empty when the date is outside the ensemble's reach."""
    data = get_json(ENSEMBLE_URL, weather_params(lat, lon, elevation_m, day, day) | {"models": ENSEMBLE_MODEL})
    hourly = data["hourly"]
    index = [datetime.fromisoformat(stamp).hour for stamp in hourly["time"]].index(hour)
    members = [""] + sorted({key[len(HOURLY[0]):] for key in hourly if key.startswith(f"{HOURLY[0]}_member")})
    samples = []
    for member in members:
        values = [hourly.get(name + member, [None] * (index + 1))[index] for name in HOURLY]
        sample = to_sample(values, float(data["elevation"]))
        if sample is not None:
            samples.append(sample)
    return samples


def trend_to_year(dated: list[tuple[int, Sample]], target_year: int) -> tuple[list[Sample], tuple[float, ...]]:
    """Shift each sample along its variable's year-on-year trend to ``target_year``.

    The trend is a straight line through the yearly means, kept only where it stands out from the
    year-to-year noise (|slope| >= TREND_T times its standard error); otherwise that variable is left flat.

    Returns:
        tuple: The shifted samples and the slope used per variable, per year.
    """
    years = sorted({year for year, _ in dated})
    slopes = []
    for column in range(len(VARIABLES)):
        means = [statistics.fmean(s[column] for y, s in dated if y == year) for year in years]
        slope = 0.0
        if len(years) >= 3:
            fit = statistics.linear_regression(years, means)
            residual = math.sqrt(sum((m - fit.intercept - fit.slope * y) ** 2 for y, m in zip(years, means)) / (len(years) - 2))
            spread = math.sqrt(sum((y - statistics.fmean(years)) ** 2 for y in years))
            if residual == 0.0 or abs(fit.slope) >= TREND_T * residual / spread:
                slope = fit.slope
        slopes.append(slope)
    shifted = [tuple(v + b * (target_year - year) for v, b in zip(sample, slopes)) for year, sample in dated]
    return shifted, tuple(slopes)  # type: ignore[return-value]


def climate_samples(lat: float, lon: float, elevation_m: float, day: date, hour: int) -> tuple[list[Sample], str]:
    """Samples of the same place and hour around the date in each of the last CLIMATE_YEARS years,
    moved along each variable's trend to the launch year, and a note on the trends used."""
    def one_year(year: int) -> list[tuple[int, Sample]]:
        try:
            centre = day.replace(year=year)
        except ValueError:  # 29 February
            centre = day.replace(year=year, day=28)
        rows = fetch(
            ARCHIVE_URL, lat, lon, elevation_m, centre - timedelta(WINDOW_DAYS), centre + timedelta(WINDOW_DAYS), cache=True
        )
        return [(year, sample) for stamp, sample in rows if abs(stamp.hour - hour) <= HOUR_SPAN]

    print(f"Reading {CLIMATE_YEARS} years of history...", flush=True)
    years = range(date.today().year - CLIMATE_YEARS, date.today().year)
    with ThreadPoolExecutor(FETCH_THREADS) as pool:
        dated = [row for rows in pool.map(one_year, years) for row in rows]
    if len(dated) < 2:
        raise SystemExit("Open-Meteo returned too little history to take statistics from.")
    samples, slopes = trend_to_year(dated, day.year)
    trends = ", ".join(f"{name} {10 * b:+.3g} {unit}/decade" for (name, unit), b in zip(VARIABLES, slopes) if b) or "none"
    note = f"last {CLIMATE_YEARS} years, +-{WINDOW_DAYS} days, +-{HOUR_SPAN} h, trended to {day.year} (trends: {trends})"
    print(f"Trends kept: {trends}")
    return samples, note


def recorded_sample(lat: float, lon: float, elevation_m: float, day: date, hour: int) -> tuple[Sample, str] | None:
    """The archived or forecast weather for that hour, or None when the date is beyond the forecast."""
    today = date.today()
    if day > today + timedelta(FORECAST_DAYS):
        return None
    url, source = (ARCHIVE_URL, "archive") if day <= today - timedelta(ARCHIVE_LAG_DAYS) else (FORECAST_URL, "forecast")
    for stamp, sample in fetch(url, lat, lon, elevation_m, day, day):
        if stamp.hour == hour:
            return sample, source
    raise SystemExit(f"Open-Meteo has no data for {day} at {hour}:00.")


def mean_sample(samples: list[Sample]) -> Sample:
    return tuple(statistics.fmean(column) for column in zip(*samples))  # type: ignore[return-value]


def std_sample(samples: list[Sample]) -> Sample:
    return tuple(statistics.stdev(column) for column in zip(*samples))  # type: ignore[return-value]


def jarvis_inputs(lat: float, lon: float, elevation_m: float, day: date, hour: int) -> tuple[Sample, str]:
    """The single most likely weather for that hour, and where it came from."""
    recorded = recorded_sample(lat, lon, elevation_m, day, hour)
    if recorded is not None:
        return recorded
    climate, note = climate_samples(lat, lon, elevation_m, day, hour)
    return mean_sample(climate), f"climate mean ({note})"


def monte_carlo_inputs(lat: float, lon: float, elevation_m: float, day: date, hour: int) -> tuple[Sample, Sample, int, str]:
    """Mean, standard deviation, sample count and source for that hour."""
    today = date.today()
    if today - timedelta(ARCHIVE_LAG_DAYS) < day <= today + timedelta(FORECAST_DAYS):
        members = ensemble_samples(lat, lon, elevation_m, day, hour)
        if len(members) >= 2:
            return mean_sample(members), std_sample(members), len(members), f"{ENSEMBLE_MODEL} ensemble forecast"
    climate, climate_note = climate_samples(lat, lon, elevation_m, day, hour)
    recorded = recorded_sample(lat, lon, elevation_m, day, hour)
    if recorded is not None:
        # shortcut: the recorded hour has no spread of its own, so the climate's stands in; upgrade with ERA5 ensemble spread
        return recorded[0], std_sample(climate), len(climate), f"mean: {recorded[1]}; std: climate ({climate_note})"
    return mean_sample(climate), std_sample(climate), len(climate), f"climate ({climate_note})"


def site_toml(lat: float, elevation_m: float, sample: Sample, source: str, day: date, hour: int) -> str:
    """The sim's site.toml text for one sample."""
    temperature_c, pressure_pa, humidity, east, north = sample
    speed = math.hypot(east, north)
    from_deg = math.degrees(math.atan2(-east, -north)) % 360.0
    return f'''# Launch site weather for {day} at {hour}:00 local time; {source}, from Open-Meteo.

launch_latitude = "{lat:g} deg"
launch_elevation = "{elevation_m:.1f} m"

[atmosphere]
type = "flight_sim.environment.atmosphere.StandardAtmosphere1976"
launch_temperature = "{temperature_c:.1f} degC"
barometric_pressure = "{pressure_pa:.0f} Pa"
relative_humidity = {min(max(humidity, 0.0), 1.0):.3f}

[gravity]
type = "flight_sim.environment.gravity.WGS84Gravity"

[wind]
type = "flight_sim.environment.wind.UniformWind"
speed = "{speed:.1f} m/s"
from_azimuth = "{from_deg:.0f} deg"
'''


def monte_carlo_toml(
    lat: float, lon: float, elevation_m: float, mean: Sample, std: Sample, count: int, source: str, day: date, hour: int
) -> str:
    """Mean and standard deviation of the site inputs as TOML."""
    lines = [
        f"# Monte Carlo statistics of the site inputs for {day} at {hour}:00 local time, from Open-Meteo.",
        f"# Source: {source}. Wind is the 100 m wind as east and north components.",
        "",
        f'launch_latitude = "{lat:g} deg"',
        f'launch_longitude = "{lon:g} deg"',
        f'launch_elevation = "{elevation_m:.1f} m"',
        f"samples = {count}",
    ]
    for (name, unit), mu, sigma in zip(VARIABLES, mean, std):
        lines += ["", f"[{name}]  # {unit}", f"mean = {mu:.6g}", f"std = {sigma:.6g}"]
    return "\n".join(lines) + "\n"


def parse_site(text: str) -> tuple[float, float, float | None] | None:
    """'lat, lon[, elevation_m]' as numbers, or None when it is not that."""
    try:
        numbers = [float(part) for part in text.split(",")]
    except ValueError:
        return None
    if len(numbers) in (2, 3) and -90 <= numbers[0] <= 90 and -180 <= numbers[1] <= 180:
        return numbers[0], numbers[1], numbers[2] if len(numbers) == 3 else None
    return None


def ask(prompt: str, parse, error: str, given: str | None = None):
    """Parse ``given``, else ask until the answer parses."""
    text = given
    while True:
        if text is None:
            text = input(prompt)
        value = parse(text.strip())
        if value is not None:
            return value
        print(error)
        text = None


def parse_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def main() -> int:
    """Read the arguments (asking for any left out), fetch, write; returns the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", nargs="?", choices=("jarvis", "monte-carlo"))
    parser.add_argument("--site", help="'lat, lon[, elevation_m]', south and west negative")
    parser.add_argument("--date", help="launch date, YYYY-MM-DD")
    parser.add_argument("--hour", help="launch hour, local time, 0 to 23")
    parser.add_argument("--out", help="output folder (default Downloads)")
    args = parser.parse_args()

    mode = ask("Mode (jarvis / monte-carlo): ", lambda t: t if t in ("jarvis", "monte-carlo") else None,
               "Enter jarvis or monte-carlo.", args.mode)
    lat, lon, elevation_m = ask("Site 'lat, lon[, elevation_m]': ", parse_site,
                                "Enter 'lat, lon' or 'lat, lon, elevation_m' (south and west are negative).", args.site)
    day = ask("Launch date (YYYY-MM-DD): ", parse_date, "Enter the date as YYYY-MM-DD.", args.date)
    hour = ask("Launch hour, local time, 0 to 23: ", lambda t: int(t) if t.isdigit() and int(t) <= 23 else None,
               "Enter a whole number from 0 to 23.", args.hour)
    answer = args.out if args.out is not None else input("Output folder (blank = Downloads): ")
    answer = answer.strip().strip('"')
    folder = Path(answer).expanduser() if answer else Path.home() / "Downloads"
    folder.mkdir(parents=True, exist_ok=True)

    if elevation_m is None:
        elevation_m = ground_elevation(lat, lon)
        print(f"No elevation given: using the terrain model's, {elevation_m:.0f} m. A surveyed value is better.")

    if mode == "jarvis":
        sample, source = jarvis_inputs(lat, lon, elevation_m, day, hour)
        path = folder / f"site_{day}.toml"
        path.write_text(site_toml(lat, elevation_m, sample, source, day, hour), encoding="utf-8")
    else:
        mean, std, count, source = monte_carlo_inputs(lat, lon, elevation_m, day, hour)
        path = folder / f"site_{day}_monte_carlo.toml"
        path.write_text(monte_carlo_toml(lat, lon, elevation_m, mean, std, count, source, day, hour), encoding="utf-8")
    print(f"Wrote {path} ({source})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

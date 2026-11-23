# earthgram — launch-site weather from NASA Earth-GRAM 2016

Mean atmosphere, standard deviations and Monte Carlo atmospheres above a launch
site at a given local time, by driving `GRAM2016.exe` from Python.

## Setup

- Earth-GRAM 2016 v2.0 installed (default `C:\EarthGRAM2016Version2.0`, or set
  `EARTHGRAM_HOME`). Its path must not contain spaces: GRAM can't read them.
- Python 3.10+ with numpy and pandas. Windows only (GRAM ships as a PC .exe).

## Use

```python
import sys; sys.path.insert(0, "tools/weather")
import earthgram as eg

site = eg.LaunchSite("West TX", lat=31.024, lon=-103.6616, elev_m=900)

prof = eg.profile(site, "2026-06-15 13:00", tz="America/Chicago", top_m=30_000, step_m=250)
prof.mean           # alt_m, temp, p, rho, u, v (+ _sd for each), speed, speed_sd, dir_from, sos
prof.sigma(-3)      # mean - 3 sd for each quantity (a quick bounding case)
prof.at(3000)       # mean, interpolated to 3000 m MSL

mc = eg.monte_carlo(site, "2026-06-15 13:00", tz="America/Chicago", n=500, seed=1234)
mc.runs             # run, alt_m, temp, p, rho, u, v, speed, dir_from, w, w_sd, sos
mc.run_profile(17)  # one atmosphere, e.g. for one dispersion sim
mc.percentiles([5, 50, 95])   # alt_m, temp_p5, temp_p50, temp_p95, ...

eg.launch_window(site, "2026-06-15", "America/Chicago", hours=range(9, 18))
eg.openrocket_wind_csv(prof.mean, "wind.csv")
```

Command line (from `tools/weather`):

```bash
python -m earthgram --lat 31.024 --lon -103.6616 --elev 900 --time "2026-06-15 13:00" --tz America/Chicago --mc 500 --out results
```

500 Monte Carlo atmospheres from 900 m to 30 km take about 7 s.

## What the numbers mean

- **Units:** SI. Altitude in m above MSL; u is toward east, v toward north; `dir_from`
  is the direction the wind blows from, in degrees clockwise from north.
- **Time and date:** below about 30 km GRAM uses NCEP monthly climatology for
  1997–2015, at the 00/06/12/18 UT slot nearest the time asked for. Any day of the
  same month at the same slot gives the same answer; time of day and month do
  change it. The exact date only matters higher up.
- **Monte Carlo:** perturbations are correlated with height, so each run is one
  plausible atmosphere. Their spread matches GRAM's standard deviations (the tests
  check this). `sigma(k)` shifts every quantity independently and is not a
  physically consistent atmosphere.
- **Latitude:** you give geodetic latitude, and it is converted to the geocentric
  latitude GRAM expects.
- **Site data (RRA):** Range Reference Atmosphere data blends in when a site is
  within 2.5° (`sitelim`). For West Texas the nearest sites (El Paso, White Sands)
  are just outside that, so the global climatology is used.
- **Other GRAM inputs:** pass any GRAM namelist key as a keyword, for example
  `ruscale=1.5` or `f10=200`. See `namelist.GramInputs`.

## OpenRocket wind table

`openrocket_wind_csv` writes `altitude, speed, direction, stddev` in m, m/s and
degrees. I haven't checked this file against OpenRocket's multi-level wind import.
Check the units and whether altitude is MSL or AGL in the import dialog.

## Tests

```bash
python tools/weather/earthgram/tests/test_earthgram.py
```

Offline tests use a stored GRAM output. The live tests run GRAM and are skipped
when it isn't installed.

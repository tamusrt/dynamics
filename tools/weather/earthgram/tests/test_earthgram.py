"""Tests for the earthgram package.

    python tools/weather/earthgram/tests/test_earthgram.py

The offline tests use a stored GRAM output (data/special_mc3_n5.txt: 3 Monte Carlo
runs x 5 heights at 31.024 N, -103.6616 E, 900 m, 2026-06-15 18:00 UTC). The live
tests run GRAM2016.exe and are skipped when it isn't installed (see config.py).
Exit code 1 on any failure.
"""
import math
import os
import re
import sys
import tempfile
import traceback
from dataclasses import fields
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))  # tools/weather
import earthgram as eg  # noqa: E402
from earthgram import api, config, namelist, parse  # noqa: E402

FIXTURE = HERE / "data" / "special_mc3_n5.txt"
SITE = eg.LaunchSite("West TX", lat=31.024, lon=-103.6616, elev_m=900)
T_FIXTURE = pd.Timestamp("2026-06-15 18:00", tz="UTC")

# Key order of MyTest/NameRef_2016.txt, which Init::namelist() reads by position.
TEMPLATE_KEYS = (
    "atmpath NCEPpath trapath prtpath nprpath conpath rrapath rralist profile h1 phi1 thet1 "
    "f10 f10b ap s10 s10b xm10 xm10b y10 y10b dstdtc mn ida iyr ihro mino seco dphi dthet dhgt "
    "nmax delt iopt iaux iprt inpr icon NCEPyr NCEPhr nr1 mc rpscale ruscale rwscale iurra iyrrra "
    "sitelim sitenear initpert rdinit rtinit ruinit rvinit rwinit patchy itherm z0in ibltest"
).split()


def raises(exc, fn, *args, **kwargs) -> bool:
    try:
        fn(*args, **kwargs)
    except exc:
        return True
    return False


def fixture_mc():
    return api.MonteCarlo(SITE, T_FIXTURE, *parse.read_special(FIXTURE, 5))


def fixture_profile():
    return api.Profile(SITE, T_FIXTURE, parse.read_special(FIXTURE, 5)[0])


# ---------------------------------------------------------------- offline

def test_field_order_matches_template():
    assert [f.name for f in fields(eg.GramInputs)] == TEMPLATE_KEYS


def test_field_order_matches_source_when_available():
    src = config.gram_home() / "Source" / "Init.cpp"
    if not src.is_file():
        return "skip"
    text = src.read_text(errors="replace")
    body = text[text.index("namelist >> dummy;"):]
    read = re.findall(r"namelist >> dummy >> dummy >> (\w+);", body)[: len(TEMPLATE_KEYS)]
    # the C++ variable names differ from the template keys in two places
    alias = {"rrap": "rrapath", "iyrra": "iyrrra"}
    assert [alias.get(r, r) for r in read] == TEMPLATE_KEYS, read


def test_render():
    inp = eg.GramInputs(h1=0.9, phi1=30.85, thet1=-103.66, nmax=10)
    text = namelist.render(inp, Path(r"C:\GRAM"))
    lines = text.splitlines()
    assert lines[0] == " $namein_e10" and lines[-1] == " $End"
    assert len(lines) == len(TEMPLATE_KEYS) + 2
    kv = dict(re.match(r"\s+(\w+) = (\S+)$", l).groups() for l in lines[1:-1])
    assert kv["atmpath"] == "C:\\GRAM\\IOfiles\\"
    assert kv["NCEPpath"] == "C:\\GRAM\\NCEPdata\\FixedBin\\"
    assert kv["nmax"] == "10" and kv["h1"] == "0.9" and kv["inpr"] == "1"
    assert kv["mc"] == "0" and kv["NCEPhr"] == "0"
    # rendering fills in install paths without changing the inputs
    assert inp.atmpath is None and inp.NCEPpath is None and inp.rrapath is None
    assert "C:\\Other\\IOfiles\\" in namelist.render(inp, Path(r"C:\Other"))


def test_render_rejects_bad_inputs():
    for bad in ({"nr1": 0}, {"nmax": 0}, {"rpscale": 3.0}, {"mc": -1}, {"profile": "my file.txt"}):
        assert raises(ValueError, namelist.render, eg.GramInputs(**bad), Path(r"C:\GRAM")), bad
    assert raises(ValueError, namelist.render, eg.GramInputs(), Path(r"C:\My GRAM"))


def test_geocentric_latitude():
    # GRAM's own output lists geocentric 31.024 as geodetic 31.194
    assert abs(namelist.geodetic_to_geocentric(31.194) - 31.024) < 0.005
    assert namelist.geodetic_to_geocentric(0.0) == 0.0
    assert abs(namelist.geodetic_to_geocentric(90.0) - 90.0) < 1e-9


def test_inputs_from_site_and_local_time():
    t = api._utc("2026-06-15 13:00", "America/Chicago")
    assert t == T_FIXTURE
    inp = api._inputs(SITE, t, top_m=1900, step_m=250, gram={"mc": 4})
    assert inp.nmax == 5 and inp.h1 == 0.9 and abs(inp.dhgt - 0.25) < 1e-12
    assert (inp.iyr, inp.mn, inp.ida, inp.ihro, inp.mino) == (2026, 6, 15, 18, 0)
    assert inp.mc == 4 and abs(inp.phi1 - 30.855) < 0.01
    assert api._inputs(SITE, t, 30_000, 250, {}).nmax == 117  # 900..29900 m


def test_inputs_reject_bad_requests():
    t = api._utc("2026-06-15 18:00", "UTC")
    assert raises(ValueError, api._inputs, SITE, t, 500, 250, {})
    assert raises(ValueError, api._inputs, SITE, t, 5000, 0, {})
    assert raises(TypeError, api._inputs, SITE, t, 5000, 250, {"not_a_key": 1})
    assert raises(ValueError, api._utc, "2026-06-15 13:00", None)


def test_wind_direction_convention():
    d = parse.wind_dir_from(np.array([0.0, -5.0, 5.0, 0.0]), np.array([-5.0, 0.0, 0.0, 5.0]))
    # blowing toward south = from north (0); toward west = from east (90); etc.
    assert np.allclose(d, [0.0, 90.0, 270.0, 180.0])


def test_parse_fixture():
    mean, runs = parse.read_special(FIXTURE, 5)
    assert len(mean) == 5 and np.allclose(mean["alt_m"], [900, 1150, 1400, 1650, 1900])
    assert mean["p"].between(7e4, 1e5).all() and mean["rho"].between(0.8, 1.2).all()
    assert mean["temp"].between(280, 320).all() and (mean["rho_sd"] > 0).all()
    assert len(runs) == 15 and list(runs["run"].unique()) == [0, 1, 2]
    assert np.allclose(runs["alt_m"], np.tile(mean["alt_m"], 3))
    assert np.allclose(runs["speed"], np.hypot(runs["u"], runs["v"]))
    # the runs scatter around the mean
    assert runs.pivot(index="alt_m", columns="run", values="temp").nunique(axis=1).max() > 1
    assert raises(ValueError, parse.read_special, FIXTURE, 4)


def test_parse_rejects_inconsistent_means():
    lines = FIXTURE.read_text().splitlines()
    cols = lines[1].split()
    cols[4] = "9.9999e-01"  # run 0's mean density at the first height
    lines[1] = "  ".join(cols)
    with tempfile.TemporaryDirectory() as d:
        bad = Path(d) / "special.txt"
        bad.write_text("\n".join(lines) + "\n")
        assert raises(ValueError, parse.read_special, bad, 5)


def test_profile_mean_and_sigma():
    p = fixture_profile()
    m = p.mean
    assert len(m) == 5 and (np.diff(m["p"]) < 0).all() and (np.diff(m["rho"]) < 0).all()
    for k in (-3, 0, 2):
        s = p.sigma(k)
        assert np.allclose(s["temp"], m["temp"] + k * m["temp_sd"])
        assert np.allclose(s["p"], m["p"] + k * m["p_sd"])
        assert (s["speed"] >= 0).all()
    mid = p.at(1025.0)
    assert abs(mid["temp"] - (m["temp"][0] + m["temp"][1]) / 2) < 1e-9
    assert raises(ValueError, p.at, 5000)


def test_monte_carlo_views():
    mc = fixture_mc()
    assert mc.n == 3 and len(mc.runs) == 15 and mc.values("temp").shape == (3, 5)
    r2 = mc.run_profile(2)
    assert len(r2) == 5 and np.allclose(r2["temp"], mc.values("temp")[2])
    assert raises(IndexError, mc.run_profile, 3)
    q = mc.percentiles([5, 50, 95], cols=("temp", "speed"))
    assert list(q.columns) == ["alt_m", "temp_p5", "temp_p50", "temp_p95", "speed_p5", "speed_p50", "speed_p95"]
    assert len(q) == 5 and (q["temp_p5"] <= q["temp_p50"]).all() and (q["temp_p50"] <= q["temp_p95"]).all()
    # same numbers as pandas' own quantile
    ref = mc.runs.groupby("alt_m")["temp"].quantile(0.95).to_numpy()
    assert np.allclose(q["temp_p95"], ref)


def test_openrocket_export():
    with tempfile.TemporaryDirectory() as d:
        w = pd.read_csv(eg.openrocket_wind_csv(fixture_profile().mean, Path(d) / "w.csv"))
        assert list(w.columns) == ["altitude", "speed", "direction", "stddev"] and len(w) == 5
        assert w["direction"].between(0, 360).all() and (w["stddev"] > 0).all()
        w0 = pd.read_csv(eg.openrocket_wind_csv(fixture_mc().run_profile(0), Path(d) / "r.csv"))
        assert (w0["stddev"] == 0).all()


def test_missing_install_is_reported():
    with mock.patch.dict(os.environ, {"EARTHGRAM_HOME": r"C:\definitely\not\here"}):
        assert not eg.is_installed()
        assert raises(eg.GramNotFound, eg.profile, SITE, "2026-06-15 18:00", "UTC")


# ---------------------------------------------------------------- live (needs GRAM)

def live(fn):
    fn.live = True
    return fn


@live
def test_live_profile_is_physical():
    m = eg.profile(SITE, "2026-06-15 13:00", "America/Chicago", top_m=15_000, step_m=500).mean
    assert m["alt_m"].iloc[0] == 900 and m["alt_m"].iloc[-1] == 14_900
    assert 88_000 < m["p"].iloc[0] < 94_000          # ~91 kPa at 900 m
    assert 290 < m["temp"].iloc[0] < 320             # June afternoon, West Texas
    assert (np.diff(m["p"]) < 0).all() and (np.diff(m["rho"]) < 0).all()
    trop = m[m["alt_m"] <= 11_000]
    assert (np.diff(trop["temp"]) < 0).all()         # cooling through the troposphere
    assert (m["temp_sd"] > 0).all() and (m["speed_sd"] > 0).all()


@live
def test_live_time_of_day_and_date():
    a = eg.profile(SITE, "2026-06-15 07:00", "America/Chicago", top_m=2000).mean  # 12 UT slot
    b = eg.profile(SITE, "2026-06-15 13:00", "America/Chicago", top_m=2000).mean  # 18 UT slot
    c = eg.profile(SITE, "2026-06-03 13:00", "America/Chicago", top_m=2000).mean
    d = eg.profile(SITE, "2026-01-15 13:00", "America/Chicago", top_m=2000).mean
    assert b["temp"].iloc[0] - a["temp"].iloc[0] > 3  # afternoon warmer than morning
    assert np.allclose(b["temp"], c["temp"])          # same month and slot -> same climatology
    assert b["temp"].iloc[0] - d["temp"].iloc[0] > 10 # June warmer than January


@live
def test_live_launch_window():
    w = eg.launch_window(SITE, "2026-06-15", "America/Chicago", hours=[7, 13], top_m=2000)
    assert list(w.columns[:2]) == ["local_time", "alt_m"] and len(w) == 2 * 5
    first = w[w["alt_m"] == 900].set_index("local_time")["temp"]
    assert first.iloc[1] - first.iloc[0] > 3  # same answer as profile() at those times


@live
def test_live_monte_carlo_matches_gram_statistics():
    mc = eg.monte_carlo(SITE, "2026-06-15 13:00", "America/Chicago", n=300, seed=42, top_m=5000, step_m=500)
    assert mc.n == 300
    m, r = mc.mean, mc.runs
    for alt in (900, 2900, 4900):
        row, s = m[m["alt_m"] == alt].iloc[0], r[r["alt_m"] == alt]
        for c in ("temp", "u", "v"):
            assert abs(s[c].mean() - row[c]) < 4 * row[f"{c}_sd"] / math.sqrt(300) + 0.05, (alt, c)
            assert 0.8 < s[c].std() / row[f"{c}_sd"] < 1.2, (alt, c, s[c].std(), row[f"{c}_sd"])


@live
def test_live_seed_reproducible_and_matches_fixture():
    kw = dict(n=3, top_m=1900, step_m=250)
    a = eg.monte_carlo(SITE, "2026-06-15 13:00", "America/Chicago", seed=1234, **kw)
    b = eg.monte_carlo(SITE, "2026-06-15 13:00", "America/Chicago", seed=1235, **kw)
    ref = fixture_mc()
    assert np.allclose(a.mean, ref.mean) and np.allclose(a.runs, ref.runs)
    assert not np.allclose(a.runs, b.runs)


@live
def test_live_bad_input_raises():
    assert raises(eg.GramError, eg.profile, eg.LaunchSite("pit", 31.0, -103.7, -500),
                  "2026-06-15 18:00", "UTC", top_m=1000)


def main() -> int:
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    installed = eg.is_installed()
    failed = skipped = 0
    for name, fn in tests:
        if getattr(fn, "live", False) and not installed:
            print(f"SKIP {name} (GRAM not installed)")
            skipped += 1
            continue
        try:
            if fn() == "skip":
                print(f"SKIP {name}")
                skipped += 1
            else:
                print(f"ok   {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed - skipped} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

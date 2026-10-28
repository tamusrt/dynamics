"""Regression test for the history site: build it from synthetic data, then check the page.

    python tools/openrocket/tests/test_site.py

No OpenRocket, Java or network needed. It calls or_ci.write_site with a made-up history (two designs,
three simulations, failed versions, undefined samples, a changelog with a hostile commit message),
checks the files it writes, then runs tests/site_page_test.js on the result, which executes the page's
own JavaScript and asserts on what it hands to Plotly. Exit code 1 on any failure.
"""
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import or_ci  # noqa: E402

# ----------------------------------------------------------------- synthetic flight
VARS = {
    "time": ("Time", "s"), "altitude": ("Altitude", "m"), "velocity_total": ("Total velocity", "m/s"),
    "acceleration_total": ("Total acceleration", "m/s²"), "mass": ("Mass", "kg"), "thrust_force": ("Thrust", "N"),
    "cp_location": ("CP location", "m"), "cg_location": ("CG location", "m"), "stability": ("Stability margin", "cal"),
    "stability_pct_length": ("Stability margin, % of length", "% L"), "mach_number": ("Mach number", ""),
    "aoa": ("Angle of attack", "rad"), "drag_coeff": ("Drag coefficient", ""), "dynamic_pressure": ("Dynamic pressure", "Pa"),
    "air_pressure": ("Air pressure", "Pa"), "roll_damping_coeff": ("Roll damping coefficient", ""),
}


def flight(scale: float, dt: float = 0.1) -> dict:
    """A plausible ascent to apogee at t = 40 s, then a slow descent; some values undefined early on."""
    t = [round(i * dt, 4) for i in range(int(60 / dt) + 1)]
    alt = [scale * (80 * x - x * x) if x <= 40 else scale * (1600 - 20 * (x - 40)) for x in t]
    vel = [scale * (80 - 2 * x) if x <= 40 else -20.0 * scale for x in t]
    cols = {
        "time": t, "altitude": alt, "velocity_total": vel, "acceleration_total": [-2.0 * scale] * len(t),
        "mass": [50 - 0.5 * min(x, 10) for x in t], "thrust_force": [1000.0 if x < 10 else 0.0 for x in t],
        "cp_location": [1.8] * len(t), "cg_location": [1.5 + 0.01 * min(x, 10) for x in t],
        "stability": [None if x < 0.5 else 2 + 0.01 * x for x in t],
        "mach_number": [abs(v) / 340 for v in vel], "aoa": [0.001 * math.sin(x) for x in t],
        "drag_coeff": [0.4 + 0.1 * math.sin(x / 5) for x in t], "dynamic_pressure": [0.5 * 1.2 * v * v for v in vel],
        "air_pressure": [101325 * math.exp(-a / 8000) for a in alt], "roll_damping_coeff": [0.0] * len(t),
    }
    cols["stability_pct_length"] = [None if s is None else s * 10 for s in cols["stability"]]
    events = {"LAUNCH": [0.0], "LAUNCHROD": [0.5], "BURNOUT": [10.0], "APOGEE": [40.0],
              "RECOVERY_DEVICE_DEPLOYMENT": [41.0], "GROUND_HIT": [60.0]}
    return {"vars": {k: {"label": l, "unit": u} for k, (l, u) in VARS.items()}, "cols": cols, "events": events,
            "stride": or_ci.SERIES_DESCENT_STRIDE}


def metrics(apogee: float) -> dict:
    m = {"apogee": apogee, "max_mach": apogee / 4000, "max_dynamic_pressure_kpa": apogee / 50,
         "stability_off_rod_cal": 1.8, "min_stability_cal": 1.6, "max_stability_cal": 2.4}
    for cal, pct in or_ci.STABILITY_PAIRS.items():
        m[pct] = m[cal] * 10
    return m


# ----------------------------------------------------------------- synthetic history
FILE_A, FILE_B = "aero_modeling/A/a.ork", "aero_modeling/B/b.ork"
DAY = 86400


def version(i: int, msg: str) -> dict:
    return {"short": f"{i:07x}", "sha": f"{i:07x}" + "0" * 33, "time": 1_800_000_000 + i * DAY, "author": "tester",
            "message": msg, "path": FILE_A, "blob": f"blob{i}"}


MOTOR_A = "aero_modeling/A/curves/a.rse"


def row(v: dict, apogee, fl=None, status="OK", note="", motor_file="") -> dict:
    return {"short": v["short"], "date": "2027-01-%02d" % (1 + int(v["short"], 16)), "author": v["author"], "message": v["message"],
            "status": status, "note": note, "metrics": metrics(apogee) if apogee is not None else {}, "flight": fl,
            "motor": "A-rse" if motor_file else "", "motor_file": motor_file}


def build(site: Path):
    va = [version(1, "first"), version(2, "longer nose"), version(3, "<script>alert(1)</script> heavier payload"),
          dict(version(6, "new thrust curve"), blob="blob3", kind="motor")]   # a curve commit: same design, new motor
    vb = [dict(version(4, "initial"), path=FILE_B), dict(version(5, "broke the motor reference"), path=FILE_B)]
    series = {
        (FILE_A, "best"): [row(va[0], 3000), row(va[1], 3200, flight(1.0)), row(va[2], 3100, flight(1.02), motor_file=MOTOR_A),
                           row(va[3], 3150, flight(1.03), motor_file=MOTOR_A)],
        (FILE_A, "worst"): [row(va[0], 2500), row(va[1], 2450, flight(0.9)), row(va[2], 2450, flight(0.9), motor_file=MOTOR_A),
                            row(va[3], 2470, flight(0.91), motor_file=MOTOR_A)],
        (FILE_B, "Simulation 1"): [row(vb[0], 1800, flight(0.6)), row(vb[1], None, None, status="FAILED", note="motor unresolved")],
    }
    files_versions = {FILE_A: va, FILE_B: vb}
    entry = lambda v, prev, headline: {  # noqa: E731  (what cmd_history assembles from ork_diff + narration)
        "sha": v["sha"], "short": v["short"], "date": "2027-01-%02d" % (1 + int(v["short"], 16)), "author": v["author"],
        "message": v["message"], "prev": prev["short"], "key": f"{prev['blob']}-{v['blob']}", "narration": None,
        "headline": headline, "empty": False,
        "lines": {u: [f"**Nose cone** length 0.30 → 0.35 {u}"] for u in or_ci.UNIT_SYSTEMS},
        "rows": {u: [["Nose cone", "length", "0.30", "0.35"]] for u in or_ci.UNIT_SYSTEMS}}
    motor_entry = dict(entry(va[3], va[2], "Thrust curve changed: a.rse"), kind="motor",
                       lines={u: ["**a.rse**: total impulse 37141 \u2192 37273 N\u00b7s; burn time 12.80 \u2192 12.60 s"] for u in or_ci.UNIT_SYSTEMS},
                       rows={u: [["a.rse", "total impulse", "37141 N\u00b7s", "37273 N\u00b7s"], ["a.rse", "burn time", "12.80 s", "12.60 s"]] for u in or_ci.UNIT_SYSTEMS})
    changelog = {FILE_A: [motor_entry, entry(va[2], va[1], "1 component changed"), entry(va[1], va[0], "Nose cone lengthened")], FILE_B: [entry(vb[1], vb[0], "Motor changed")]}
    mass = lambda u: [f"launch mass (vs RASAero “CUST”): OpenRocket 158.68 {u}, RASAero 152.00 {u}"]  # noqa: E731
    rasaero = [  # what rasaero_checks gives: A differs in shape and in one simulation's mass, B only in one simulation's
        {"ork": FILE_A, "rasaero": "aero_modeling/A/RASAero/A.CDX1",
         "lines": {"metric": ["overall length: OpenRocket 5199.1 mm, RASAero 5237.2 mm"],
                   "imperial": ["overall length: OpenRocket 204.69 in, RASAero 206.19 in"]},
         "sims": {"best": {"metric": mass("kg"), "imperial": mass("lb")}}},
        {"ork": FILE_B, "rasaero": "aero_modeling/B/RASAero/B.CDX1", "lines": {"metric": [], "imperial": []},
         "sims": {"Simulation 1": {"metric": mass("kg"), "imperial": mass("lb")}}}]
    or_ci.write_site(series, site, "imperial", "https://github.com/example/dynamics", files_versions, "pct", changelog, rasaero)


def test_current_sims():
    """A simulation missing from the newest loadable version drops out; a newest version that failed to load is skipped."""
    fv = {"a.ork": [{"blob": "1"}, {"blob": "2"}, {"blob": "3"}], "b.ork": [{"blob": "4"}]}
    results = {("a.ork", "1"): [{"sim_name": "old"}, {"sim_name": "best"}],
               ("a.ork", "2"): [{"sim_name": "best"}, {"sim_name": "new"}],
               ("a.ork", "3"): [],                      # newest version failed to load
               ("b.ork", "4"): []}                      # never loaded: keep whatever history there is
    series = {("a.ork", "old"): [1], ("a.ork", "best"): [1, 2], ("a.ork", "new"): [2], ("b.ork", "x"): [1]}
    kept = or_ci.current_sims(series, fv, results)
    assert set(kept) == {("a.ork", "best"), ("a.ork", "new"), ("b.ork", "x")}, kept
    print("current_sims: ok")


def test_variant_sims_keep_their_own_label():
    """A motor_variants simulation ('S [85%]') already names its curve; it gets no second label. Multi-mount motors join."""
    v = version(1, "first")
    series = {("v.ork", "S [85%]"): [row(v, 1000, motor_file="L/85per_Liq.eng")],
              ("h.ork", "best"): [dict(row(v, 1000), motor_file="H/OXTank.eng, H/CC.eng")]}
    with tempfile.TemporaryDirectory() as tmp:
        or_ci.write_site(series, Path(tmp), "imperial", "", {"v.ork": [v], "h.ork": [v]}, "pct", {})
        motors = json.loads((Path(tmp) / "data.json").read_text(encoding="utf-8"))["motors"]
    assert motors == {"h.ork": {"best": "OXTank.eng + CC.eng"}}, motors
    print("motor labels: ok")


def main() -> int:
    test_current_sims()
    test_variant_sims_keep_their_own_label()
    tmp = Path(tempfile.mkdtemp(prefix="or_site_test_"))
    site = tmp / "site"
    try:
        build(site)
        data = json.loads((site / "data.json").read_text(encoding="utf-8"))
        assert (site / "index.html").is_file() and (site / ".nojekyll").is_file()
        assert set(data["flights"]) == {f"{FILE_A}|best", f"{FILE_A}|worst", f"{FILE_B}|Simulation 1"}, data["flights"]
        for name in data["flights"].values():
            assert (site / name).is_file(), name
        assert "stability_pct_length" in data["flight_vars"] and data["default_units"] == "imperial" and data["default_stability"] == "pct"
        rows_b = data["designs"][FILE_B]["Simulation 1"]
        assert [r["ok"] for r in rows_b] == [True, False], "a failed version must be marked not ok"
        assert len(data["changelog"][FILE_A]) == 3 and data["changelog"][FILE_A][0]["kind"] == "motor"
        assert [r["kind"] for r in data["designs"][FILE_A]["best"]] == ["design", "design", "design", "motor"], "a curve commit is a version of its own"
        assert not (site / "vehicles").exists(), "the Vehicles page is built by its own job, not by write_site"
        assert data["motors"] == {FILE_A: {"best": "a.rse", "worst": "a.rse"}},             f"each simulation is labelled with the motor file its newest version flew, and only then: {data['motors']}"
        page = (site / "index.html").read_text(encoding="utf-8")
        for icon in ("favicon.ico", "favicon-32x32.png", "favicon-16x16.png", "apple-touch-icon.png", "site.webmanifest"):
            assert (site / icon).is_file() and f'href="{icon}"' in page, f"{icon} is linked and next to the page"
        manifest = json.loads((site / "site.webmanifest").read_text(encoding="utf-8"))
        for entry in manifest["icons"]:  # relative: the site is served from /<repo>/, not the domain root
            assert not entry["src"].startswith("/") and (site / entry["src"]).is_file(), entry
        print(f"write_site: ok ({len(data['flights'])} flight files, {len(data['flight_vars'])} variables)")
        node = shutil.which("node")
        if not node:
            print("node is not installed: the page test did not run", file=sys.stderr)
            return 1
        return subprocess.call([node, str(HERE / "site_page_test.js"), str(site)])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

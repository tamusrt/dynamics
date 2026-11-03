"""Test for the Vehicles page: check ork_geometry on the real designs, build the page, check what it wrote.

    python tools/openrocket/tests/test_vehicles.py

No OpenRocket, Java or network needed. Parses every .ork listed in aero_modeling/sim_config.json and
asserts the geometry is sane, builds the page with build_vehicles.main into a temp folder (with the real
template when tools/openrocket/vehicles/template.html exists, else a stand-in) and checks the output,
then runs tests/vehicles_page_test.js on it when that file and node exist. Exit code 1 on any failure.
"""
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
VEHICLES = HERE.parent / "vehicles"
CONFIG = REPO / "aero_modeling" / "sim_config.json"
sys.path.insert(0, str(VEHICLES))
import build_vehicles  # noqa: E402
import ork_geometry  # noqa: E402

STAND_IN = ('<!doctype html><html><head><title>Vehicles</title></head><body><p>Built __BUILT__</p>\n'
            '<script type="application/json" id="geo">__GEOMETRY__</script></body></html>\n')


def listed_orks():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    paths = [CONFIG.parent / rel for rel in cfg["files"] if not build_vehicles.is_ignored(rel, cfg.get("ignore", []))]
    assert paths, "sim_config.json lists no .ork files"
    return paths


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and not math.isnan(v)


def check_geometry(path: Path):
    g = ork_geometry.parse(path)
    name = path.stem
    assert g["length"] > 1.0, f"{name}: length {g['length']} m is not > 1 m"
    assert g["max_radius"] > 0.03, f"{name}: max radius {g['max_radius']} m is not > 3 cm"
    kinds = [c["kind"] for c in g["components"]]
    assert "nosecone" in kinds, f"{name}: no nose cone"
    assert any(k in ork_geometry.FINS for k in kinds), f"{name}: no fin set"
    for c in g["components"]:
        where = f"{name} / {c['kind']} '{c['name']}'"
        for key in ("x0", "x1", "length"):
            assert is_num(c.get(key)), f"{where}: {key} = {c.get(key)!r}"
        assert c["x0"] <= c["x1"] + 1e-9, f"{where}: x0 {c['x0']} > x1 {c['x1']}"
        for key in ("radius", "r_fore", "r_aft"):
            # ork_geometry leaves a mass component's "auto" packed radius as the string "auto" (Lumina, SOL_4_30 have some)
            if key in c and c[key] is not None and c[key] != "auto":
                assert is_num(c[key]), f"{where}: {key} = {c[key]!r}"
        if c["mass_source"] == "material":
            assert is_num(c["mass"]) and c["mass"] >= 0, f"{where}: material mass {c['mass']!r} is not a number >= 0"
            # AIRMAIL's "Nose Cone -> Body Coupler" is saved with thickness 0.0: OpenRocket gives it zero mass too
            zero_wall = c["fields"].get("thickness", "") in ("0", "0.0")
            assert c["mass"] > 0 or zero_wall, f"{where}: material mass is 0 although the file gives it a wall"
        # Nimitz's motor tubes are saved with thickness 0.0 and carry only an override: zero computed mass is right
        if c["kind"] == "bodytube" and is_num(c.get("thickness")) and c["thickness"] > 0:
            try:
                float(c["fields"].get("material_density", ""))
            except ValueError:
                continue
            assert is_num(c["mass_computed"]) and c["mass_computed"] > 0, f"{where}: mass_computed {c['mass_computed']!r} is not > 0"
    print(f"geometry: {name} ok ({len(g['components'])} components, {g['length']:.3f} m)")


def check_build(tmp: Path, orks) -> Path:
    template = VEHICLES / "template.html"
    if not template.is_file():
        template = tmp / "template.html"
        template.write_text(STAND_IN, encoding="utf-8")
        print("template.html does not exist yet: built with a stand-in template")
    out = tmp / "vehicles"
    rc = build_vehicles.main(["--config", str(CONFIG), "--out", str(out), "--template", str(template),
                              "--commit", "0123456789abcdef", "--date", "test day"])
    assert rc == 0, f"build_vehicles.main returned {rc}"
    page = out / "index.html"
    assert page.is_file() and (out / "geometry.json").is_file(), "index.html and geometry.json are written"
    html = page.read_text(encoding="utf-8")
    assert "__GEOMETRY__" not in html and "__BUILT__" not in html, "placeholders were replaced"
    assert "from the .ork files at 0123456, test day" in html, "provenance line is on the page"
    m = re.search(r'<script type="application/json" id="geo">(.*?)</script>', html, re.S)
    assert m, "the page has the geometry script block"
    labels = list(json.loads(m.group(1).replace("<\\/", "</")))
    assert labels == [p.stem for p in orks], f"labels {labels} should be the file stems in config order"
    assert list(json.loads((out / "geometry.json").read_text(encoding="utf-8"))) == labels
    print(f"build: ok ({', '.join(labels)})")
    return page


def check_against_openrocket():
    """Nimitz parts the parser once got wrong, against the values OpenRocket 24.12 itself gives for the file:
    an "auto" radius saved with its value, an "ogive" with shape parameter 0 (a cone), an ogive transition
    shaped by its real radius change, and an auto bulkhead inside a coupler (the coupler's bore)."""
    ork = CONFIG.parent / "NIMITZ" / "Nimitz.ork"
    if not ork.is_file():
        print("openrocket values: skipped (no Nimitz.ork)")
        return
    comps = ork_geometry.parse(ork)["components"]

    def part(name, x0):
        return next(c for c in comps if c["name"] == name and abs(c["x0"] - x0) < 1e-3)

    def near(got, want, tol, what):
        assert is_num(got) and abs(got - want) <= tol, f"Nimitz {what}: {got!r}, OpenRocket gives {want}"

    near(part("Nose tip (Aluminum)", 0.0)["mass"], 0.1195, 5e-4, "solid ogive-0 nose tip mass (kg)")
    hollow = part("Nose Cone (hollow)", 0.1778)
    near(hollow["r_fore"], 0.01542, 1e-5, "hollow nose cone fore radius (m)")
    near(hollow["r_aft"], 0.02699, 1e-5, "hollow nose cone aft radius, saved as auto (m)")
    near(part("Nosecone Straight Section", 0.4413)["r_fore"], 0.02699, 1e-5, "straight section radius, saved as auto (m)")
    near(part("Bulkhead", 0.2591)["radius"], 0.01821, 5e-5, "bulkhead radius inside the ogive transition (m)")
    near(part("Bulkhead", 0.5547)["radius"], 0.02281, 5e-5, "bulkhead radius inside the motor coupler (m)")
    print("openrocket values: ok (Nimitz)")


def main() -> int:
    orks = listed_orks()
    for path in orks:
        check_geometry(path)
    check_against_openrocket()
    tmp = Path(tempfile.mkdtemp(prefix="or_vehicles_test_"))
    try:
        page = check_build(tmp, orks)
        js = HERE / "vehicles_page_test.js"
        node = shutil.which("node")
        if js.is_file() and node:
            sys.stdout.flush()   # keep the node output after ours when piped (CI logs)
            rc = subprocess.call([node, str(js), str(page)])
            if rc != 0:
                return rc
        else:
            print(f"page test skipped ({'no vehicles_page_test.js yet' if not js.is_file() else 'node is not installed'})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("vehicles: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())

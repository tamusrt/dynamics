"""doctor.py: files that moved, went missing or are hidden are found and named, and moves can be repaired."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import doctor  # noqa: E402

ORK = """<openrocket><rocket><subcomponents><stage><subcomponents>
<nosecone><length>0.762</length></nosecone><bodytube><length>1</length><subcomponents>
<trapezoidfinset><fincount>4</fincount></trapezoidfinset></subcomponents></bodytube>
</subcomponents></stage></subcomponents><simulations>
<simulation><name>average</name></simulation></simulations></rocket></openrocket>"""

ENG = "; test\nM 100 1000 0 1 2 TEST\n 0.1 100\n 0.5 200\n 1.0 100\n 1.5 0\n;\n"
RSE = ('<engine-database><engine-list><engine Itot="200" code="M"><data>'
       '<eng-data t="0" f="0"/><eng-data t="0.1" f="100"/><eng-data t="0.5" f="200"/>'
       '<eng-data t="1.0" f="100"/><eng-data t="1.5" f="0"/></data></engine></engine-list></engine-database>')


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True, capture_output=True)


def _repo(root: Path) -> None:
    """A small repository laid out like dynamics: aero_modeling/ with two configs, a design, a motor and a table."""
    base = root / "aero_modeling"
    for folder in ("R/OR", "R/Curves", "R/JARVIS", "R/RASA"):
        (base / folder).mkdir(parents=True)
    with zipfile.ZipFile(base / "R/OR/d.ork", "w") as archive:
        archive.writestr("rocket.ork", ORK)
    (base / "R/Curves/m.eng").write_text(ENG, encoding="utf-8")
    (base / "R/Curves/m.rse").write_text(RSE, encoding="utf-8")
    (base / "R/JARVIS/r_aero.csv").write_text("Mach,Alpha\n", encoding="utf-8")
    (base / "R/RASA/r.CDX1").write_text("<x/>", encoding="utf-8")
    (base / "R/RASA/res.json").write_text("{}", encoding="utf-8")
    (base / "whatif_config.json").write_text(json.dumps({"default": "A", "rockets": {"A": {
        "ork": "R/OR/d.ork", "aero": "R/JARVIS/r_aero.csv", "rasaero": "R/RASA/r.CDX1", "rasaero_results": "R/RASA/res.json",
        "motor_dir": "R/Curves", "sim": "average"}}}, indent=2), encoding="utf-8")
    (base / "sim_config.json").write_text(json.dumps({"files": {"R/OR/d.ork": {"default_motor": "R/Curves/m.rse"}}}, indent=2), encoding="utf-8")
    (root / ".gitignore").write_text("*.csv\n!aero_modeling/**/*_aero.csv\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "start")


def _run(root: Path):
    findings, _ = doctor.run_checks(root, root / "aero_modeling/whatif_config.json", root / "aero_modeling/sim_config.json")
    return findings


def _codes(findings, level="error") -> set[str]:
    return {f["code"] for f in findings if f["level"] == level}


def test_a_complete_repository_has_no_errors():
    with tempfile.TemporaryDirectory() as tmp:
        _repo(Path(tmp))
        assert _codes(_run(Path(tmp))) == set(), _run(Path(tmp))


def test_a_moved_motor_folder_is_found_and_repaired():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _repo(root)
        _git(root, "mv", "aero_modeling/R/Curves", "aero_modeling/R/Motors")
        _git(root, "commit", "-q", "-m", "organise")
        found = _run(root)
        assert "moved" in _codes(found)
        assert any(f.get("fix", {}).get("new") == "R/Motors" for f in found)
        assert doctor.apply_fixes(root, found)
        assert _codes(_run(root)) == set(), _run(root)


def test_a_renamed_motor_file_is_followed_through_two_renames():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _repo(root)
        for old, new in (("m", "m2"), ("m2", "m3")):
            for suffix in (".eng", ".rse"):
                _git(root, "mv", f"aero_modeling/R/Curves/{old}{suffix}", f"aero_modeling/R/Curves/{new}{suffix}")
            _git(root, "commit", "-q", "-m", f"rename {new}")
        found = _run(root)
        fixes = [f["fix"] for f in found if f.get("fix") and f["fix"]["file"].endswith("sim_config.json")]
        assert fixes and fixes[0]["new"] == "R/Curves/m3.rse", fixes


def test_a_wrong_capital_is_named():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _repo(root)
        config = root / "aero_modeling/whatif_config.json"
        config.write_text(config.read_text(encoding="utf-8").replace("R/Curves", "r/curves"), encoding="utf-8")
        assert "wrong-capitals" in _codes(_run(root)), _run(root)


def test_a_typo_in_the_config_gives_the_line():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _repo(root)
        (root / "aero_modeling/whatif_config.json").write_text('{\n  "a": 1,\n}\n', encoding="utf-8")
        found = [f for f in _run(root) if f["code"] == "json-typo"]
        assert found and ("line 2" in found[0]["message"] or "line 3" in found[0]["message"]), found  # Python versions differ


def test_a_table_hidden_by_gitignore_is_named():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _repo(root)
        (root / "aero_modeling/R/JARVIS/other.csv").write_text("x\n", encoding="utf-8")
        config = root / "aero_modeling/whatif_config.json"
        config.write_text(config.read_text(encoding="utf-8").replace("r_aero.csv", "other.csv"), encoding="utf-8")
        assert "git-ignored" in _codes(_run(root)), _run(root)


def test_a_missing_simulation_and_a_broken_motor_are_errors():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _repo(root)
        config = root / "aero_modeling/whatif_config.json"
        config.write_text(config.read_text(encoding="utf-8").replace('"average"', '"worst case"'), encoding="utf-8")
        (root / "aero_modeling/R/Curves/m.rse").write_text(RSE.replace('t="0.5"', 't="0.05"'), encoding="utf-8")
        _git(root, "add", ".")
        _git(root, "commit", "-q", "-m", "edit")
        assert {"ork-sim-missing", "motor-bad"} <= _codes(_run(root)), _run(root)


def test_a_missing_history_motor_is_a_warning_not_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _repo(root)
        sim = root / "aero_modeling/sim_config.json"
        sim.write_text(sim.read_text(encoding="utf-8").replace("m.rse", "gone.rse"), encoding="utf-8")
        found = _run(root)
        assert "history-motor-missing" in _codes(found, "warn"), found


def test_two_curves_with_one_name_and_a_mass_column_out_of_step_are_warned():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _repo(root)
        folder = root / "aero_modeling/R/Curves"
        (folder / "other.eng").write_text(ENG, encoding="utf-8")  # a second curve that calls itself "M" too
        # the same thrust, but a mass that falls evenly in time while the thrust is front-loaded
        rows = [(0, 0, 10000), (0.1, 100, 9000), (0.5, 200, 5000), (1.0, 100, 2000), (1.5, 0, 0)]  # grams, as in an .rse
        data = "".join(f'<eng-data t="{t}" f="{f}" m="{m}"/>' for t, f, m in rows)
        text = (RSE.split("<data>")[0].replace("<engine Itot", '<engine auto-calc-mass="0" Itot')
                + "<data>" + data + "</data></engine></engine-list></engine-database>")
        (folder / "m.rse").write_text(text, encoding="utf-8")
        _git(root, "add", ".")
        _git(root, "commit", "-q", "-m", "edit")
        warned = _codes(_run(root), "warn")
        assert {"motor-same-name", "motor-mass-column"} <= warned, _run(root)


def test_a_mass_column_that_follows_the_thrust_is_not_warned():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _repo(root)
        folder = root / "aero_modeling/R/Curves"
        rows = [(0, 0, 10000), (0.1, 100, 9879), (0.5, 200, 8424), (1.0, 100, 6606), (1.5, 0, 6000)]  # grams, as in an .rse
        data = "".join(f'<eng-data t="{t}" f="{f}" m="{m}"/>' for t, f, m in rows)
        text = (RSE.split("<data>")[0].replace("<engine Itot", '<engine auto-calc-mass="0" Itot')
                + "<data>" + data + "</data></engine></engine-list></engine-database>")
        (folder / "m.rse").write_text(text, encoding="utf-8")
        _git(root, "add", ".")
        _git(root, "commit", "-q", "-m", "edit")
        assert "motor-mass-column" not in _codes(_run(root), "warn"), _run(root)


def test_a_motor_with_no_points_is_an_error_not_a_crash():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _repo(root)
        folder = root / "aero_modeling/R/Curves"
        (folder / "m.rse").write_text('<engine-database><engine-list><engine Itot="200" code="M"><data></data></engine></engine-list></engine-database>', encoding="utf-8")
        _git(root, "add", ".")
        _git(root, "commit", "-q", "-m", "edit")
        assert "motor-bad" in _codes(_run(root)), _run(root)


def test_the_doctor_checks_the_motor_the_build_flies_when_the_history_motor_is_gone():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _repo(root)
        folder = root / "aero_modeling/R/Curves"
        (folder / "OLD").mkdir()
        (folder / "OLD/z.eng").write_text(ENG, encoding="utf-8")  # an old curve is never "the newest motor"
        (folder / "OLD/z.eng").write_text(ENG.replace("1000", "1001"), encoding="utf-8")
        sim = root / "aero_modeling/sim_config.json"
        sim.write_text(sim.read_text(encoding="utf-8").replace("m.rse", "gone.rse"), encoding="utf-8")
        _git(root, "add", ".")
        _git(root, "commit", "-q", "-m", "edit")
        spec = json.loads((root / "aero_modeling/whatif_config.json").read_text(encoding="utf-8"))["rockets"]["A"]
        path, _ = doctor.flown_motor(doctor.Repo(root), root / "aero_modeling", spec)
        assert path is not None and path.parent.name == "Curves", path


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

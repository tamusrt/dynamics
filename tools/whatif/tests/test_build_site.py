"""Checks of the predictions build planner. Run: python tools/whatif/tests/test_build_site.py"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import build_site  # noqa: E402


def _config(root: Path, rockets: dict, default: str | None = None) -> Path:
    for spec in rockets.values():
        for key in ("ork", "aero", "motor", "rasaero"):
            if key in spec:
                (root / spec[key]).parent.mkdir(parents=True, exist_ok=True)
                (root / spec[key]).write_text("x")
    path = root / "whatif_config.json"
    path.write_text(json.dumps({"default": default, "rockets": rockets}))
    return path


def test_default_rocket_goes_to_the_site_root_of_the_page():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        spec = {"ork": "a.ork", "aero": "a.csv", "motor": "a.eng", "rasaero": "a.CDX1", "sim": "average"}
        other = {"ork": "b.ork", "aero": "b.csv", "motor": "b.eng"}
        builds = build_site.plan(_config(root, {"SRT14": spec, "Other": other}, "SRT14"), root / "site")
        assert builds[0]["out"] == root / "site" / "predictions"
        assert builds[1]["out"] == root / "site" / "predictions" / "other"
        assert "--rasaero" in builds[0]["cmd"] and "--rasaero" not in builds[1]["cmd"]
        assert builds[0]["cmd"][builds[0]["cmd"].index("--sim") + 1] == "average"


def test_the_history_site_is_used_when_it_is_already_built():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        spec = {"ork": "IREC/OR/a.ork", "aero": "a.csv", "motor": "a.eng"}
        path = _config(root, {"R": spec}, "R")
        site = root / "site"
        (root / "sim_config.json").write_text(json.dumps({"files": {"IREC/OR/a.ork": {"default_motor": "IREC/Thrust Curves/IGNIS.rse"}}}))
        cmd = build_site.plan(path, site)[0]["cmd"]
        assert "--history-site" not in cmd, "no History site yet"
        site.mkdir()
        (site / "data.json").write_text("{}")
        cmd = build_site.plan(path, site)[0]["cmd"]
        assert cmd[cmd.index("--history-site") + 1] == str(site)
        assert cmd[cmd.index("--history-motor") + 1] == "IGNIS.rse"
        (root / "sim_config.json").write_text("{}")  # no default motor named: nothing to compare
        assert "--history-motor" not in build_site.plan(path, site)[0]["cmd"]
        (root / "sim_config.json").unlink()
        assert "--history-motor" not in build_site.plan(path, site)[0]["cmd"]


def test_missing_file_is_named():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _config(root, {"R": {"ork": "a.ork", "aero": "a.csv", "motor": "a.eng"}}, "R")
        (root / "a.csv").unlink()
        try:
            build_site.plan(path, root / "site")
        except SystemExit as error:
            assert "a.csv" in str(error)
        else:
            raise AssertionError("a missing file should stop the build")


def test_unknown_default_is_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _config(root, {"R": {"ork": "a.ork", "aero": "a.csv", "motor": "a.eng"}}, "Nope")
        try:
            build_site.plan(path, root / "site")
        except SystemExit as error:
            assert "Nope" in str(error)
        else:
            raise AssertionError("an unknown default should stop the build")


def _git(root: Path, *args: str, when: int | None = None) -> None:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    if when:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"{when} +0000"
    subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True)


def _commit(root: Path, name: str, when: int) -> None:
    (root / name).write_text(name + str(when))
    _git(root, "add", name)
    _git(root, "commit", "-m", name, when=when)


def _motors(root: Path) -> Path:
    folder = root / "Thrust Curves"
    folder.mkdir()
    (folder / "sub").mkdir()
    _git(root, "init", "-q")
    _commit(folder, "OLD.eng", 1_500_000_000)
    _commit(folder, "OLD.rse", 1_500_000_000)
    _commit(folder, "NEW.rse", 1_600_000_000)
    _commit(folder, "NEW.eng", 1_600_000_100)
    (folder / "sub" / "NEWEST.eng").write_text("a subfolder is not searched")
    return folder


def test_newest_committed_thrust_curve_is_chosen_and_eng_preferred():
    with tempfile.TemporaryDirectory() as tmp:
        folder = _motors(Path(tmp))
        chosen, note = build_site.newest_thrust_curve(folder)
        assert chosen.name == "NEW.eng" and note == "newest of 2 in Thrust Curves"
        (folder / "NEW.eng").unlink()
        assert build_site.newest_thrust_curve(folder)[0].name == "NEW.rse"


def test_an_untracked_or_edited_motor_file_counts_as_newer():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        folder = _motors(root)
        (folder / "OLD.rse").write_text("edited just now")
        assert build_site.newest_thrust_curve(folder)[0].name == "OLD.eng"
        _git(root, "checkout", "--", "Thrust Curves/OLD.rse")
        assert build_site.newest_thrust_curve(folder)[0].name == "NEW.eng"
        time.sleep(0.05)
        (folder / "FRESH.rse").write_text("not committed yet")
        assert build_site.newest_thrust_curve(folder)[0].name == "FRESH.rse"


def test_without_git_the_file_time_decides():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        for name, age in (("A.eng", 300), ("B.eng", 100), ("C.rse", 200)):
            (folder / name).write_text("x")
            stamp = time.time() - age
            os.utime(folder / name, (stamp, stamp))
        assert build_site.newest_thrust_curve(folder)[0].name == "B.eng"
        assert build_site.newest_thrust_curve(folder)[1] == "newest of 3 in " + folder.name


def test_folder_without_a_motor_is_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        try:
            build_site.newest_thrust_curve(Path(tmp))
        except SystemExit as error:
            assert "no .eng or .rse" in str(error)
        else:
            raise AssertionError("an empty folder should stop the build")


def test_config_with_motor_dir_passes_the_newest_motor_and_the_note():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name in ("a.ork", "a.csv"):
            (root / name).write_text("x")
        folder = root / "motors"
        folder.mkdir()
        (folder / "m.rse").write_text("x")
        spec = {"ork": "a.ork", "aero": "a.csv", "motor_dir": "motors"}
        path = root / "whatif_config.json"
        path.write_text(json.dumps({"rockets": {"R": spec}}))
        cmd = build_site.plan(path, root / "site")[0]["cmd"]
        assert cmd[cmd.index("--motor") + 1] == str(folder / "m.rse")
        assert cmd[cmd.index("--motor-note") + 1] == "newest of 1 in motors"


def _motor_config(root: Path, spec_extra: dict | None = None, history: dict | None = None) -> Path:
    for name in ("a.ork", "a.csv"):
        (root / name).write_text("x")
    folder = root / "motors"
    folder.mkdir()
    (folder / "m.rse").write_text("x")  # the one the propulsion model wrote last
    (folder / "h.rse").write_text("x")  # the one the History tab flies
    spec = {"ork": "a.ork", "aero": "a.csv", "motor_dir": "motors", **(spec_extra or {})}
    (root / "sim_config.json").write_text(json.dumps({"files": {"a.ork": history or {"default_motor": "motors/h.rse"}}}))
    path = root / "whatif_config.json"
    path.write_text(json.dumps({"rockets": {"R": spec}}))
    return path


def _flown(cmd: list[str]) -> str:
    return Path(cmd[cmd.index("--motor") + 1]).name


def test_jarvis_flies_the_motor_the_history_tab_flies():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build = build_site.plan(_motor_config(root), root / "site")[0]
        assert _flown(build["cmd"]) == "h.rse" and build["note"] == "same as the History tab"
        assert build["cmd"][build["cmd"].index("--motor-note") + 1] == "same as the History tab"


def test_without_a_usable_history_motor_the_newest_is_flown():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _motor_config(root, history={"default_motor": "motors/gone.rse"})  # file is not there
        assert _flown(build_site.plan(path, root / "site")[0]["cmd"]) in ("m.rse", "h.rse")
        assert build_site.plan(path, root / "site")[0]["note"].startswith("newest of 2")
        (root / "sim_config.json").write_text(json.dumps({"files": {"a.ork": {"default_motor": "motors/notes.txt"}}}))
        (root / "motors" / "notes.txt").write_text("not a motor")
        assert build_site.plan(path, root / "site")[0]["note"].startswith("newest of 2")
        (root / "sim_config.json").write_text("{}")  # History names no motor
        assert build_site.plan(path, root / "site")[0]["note"].startswith("newest of 2")


def test_a_motor_named_in_the_config_beats_the_history_motor():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _motor_config(root, {"motor": "motors/m.rse"})
        assert _flown(build_site.plan(path, root / "site")[0]["cmd"]) == "m.rse"


def test_jarvis_by_commit_is_planned_only_when_the_history_site_is_built():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        spec = {"ork": "IREC/OR/a.ork", "aero": "a.csv", "motor": "a.eng", "rasaero": "a.CDX1"}
        path = _config(root, {"R": spec}, "R")
        site = root / "site"
        assert build_site.plan(path, site)[0]["by_commit"] is None
        site.mkdir()
        (site / "data.json").write_text("{}")
        cmd = build_site.plan(path, site)[0]["by_commit"]
        assert cmd[1:3] == ["-m", "flight_sim.whatif.commits"]
        assert cmd[cmd.index("--history-site") + 1] == str(site)
        assert cmd[cmd.index("--out") + 1] == str(site / "predictions" / "by_commit.json")
        assert "--rasaero" in cmd


def test_the_by_commit_files_of_all_rockets_are_joined_for_the_history_page():
    with tempfile.TemporaryDirectory() as tmp:
        site = Path(tmp)
        builds = []
        for key, design in (("A", "x/a.ork"), ("B", "x/b.ork"), ("C", "x/c.ork")):
            out = site / "predictions" / key.lower()
            out.mkdir(parents=True)
            builds.append({"out": out})
            body = {"design": design, "motor": "m.rse", "note": "n", "skipped": 0, "sims": {"s": {"abc": {"apogee": 1.0}}}}
            (out / "by_commit.json").write_text(json.dumps(body) if key != "C" else "{not json")
        target = build_site.merge_by_commit(builds, site)
        merged = json.loads(target.read_text())
        assert set(merged["designs"]) == {"x/a.ork", "x/b.ork"}
        assert merged["designs"]["x/a.ork"]["sims"]["s"]["abc"]["apogee"] == 1.0
        assert not any((b["out"] / "by_commit.json").exists() for b in builds), "the single files are removed"
        assert build_site.merge_by_commit(builds, site) is None, "nothing left to join"


def test_edith_is_planned_only_when_asked_and_uses_the_rockets_settings():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        spec = {"ork": "a.ork", "aero": "a.csv", "motor": "a.eng", "sim": "average", "edith_site": "site.json", "edith_minutes": 4}
        (root / "site.json").write_text("{}")
        path = _config(root, {"R": spec}, "R")
        assert build_site.plan(path, root / "site")[0]["edith"] is None, "off unless asked for"
        build = build_site.plan(path, root / "site", True, root / "cache")[0]
        cmd = build["edith"]
        assert cmd[1:3] == ["-m", "flight_sim.whatif.edith_site"]
        assert cmd[cmd.index("--out") + 1] == str(root / "site" / "predictions")
        assert cmd[cmd.index("--sim") + 1] == "average" and cmd[cmd.index("--minutes") + 1] == "4"
        assert cmd[cmd.index("--site") + 1] == str(root / "site.json")
        assert cmd[cmd.index("--cache") + 1] == str(root / "cache" / "r")
        assert build["edith_timeout_s"] == 4 * 60 * 1.5 + build_site.EDITH_GRACE_S
        plain = build_site.plan(_config(root, {"R": {"ork": "a.ork", "aero": "a.csv", "motor": "a.eng"}}, "R"), root / "site", True)[0]["edith"]
        assert "--site" not in plain and "--cache" not in plain and "--sim" not in plain
        assert plain[plain.index("--minutes") + 1] == "10"
        assert "--accepted" not in plain and "--accepted" not in cmd, "no accepted list, no flag"
        spec["edith_accepted"] = ["stability_static_max", " main_altitude ", ""]
        cmd = build_site.plan(_config(root, {"R": spec}, "R"), root / "site", True)[0]["edith"]
        assert cmd[cmd.index("--accepted") + 1] == "stability_static_max,main_altitude"


def test_a_missing_edith_settings_file_is_named():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _config(root, {"R": {"ork": "a.ork", "aero": "a.csv", "motor": "a.eng", "edith_site": "nope.json"}}, "R")
        try:
            build_site.plan(path, root / "site", True)
        except SystemExit as stop:
            assert "nope.json" in str(stop)
        else:
            raise AssertionError("a missing settings file must stop the build with its name")


def test_an_edith_failure_or_timeout_is_reported_and_never_raised():
    py = sys.executable
    build = {"key": "R", "edith_timeout_s": 30, "edith": [py, "-c", "raise SystemExit(0)"]}
    assert build_site.run_edith(build) is True
    assert build_site.run_edith({**build, "edith": [py, "-c", "raise SystemExit(3)"]}) is False
    assert build_site.run_edith({**build, "edith": [py, "-c", "import time; time.sleep(30)"], "edith_timeout_s": 0.5}) is False
    assert build_site.run_edith({**build, "edith": ["/no/such/program"]}) is False


def test_main_runs_edith_after_the_page_and_a_failure_does_not_change_the_exit_code():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = _config(root, {"R": {"ork": "a.ork", "aero": "a.csv", "motor": "a.eng"}}, "R")
        calls: list[str] = []
        real = subprocess.run

        def fake(cmd, **kw):
            if cmd[0] != "git":  # looking up the repository is not a build step
                calls.append(cmd[2] if len(cmd) > 2 and cmd[1] == "-m" else cmd[0])
            return subprocess.CompletedProcess(cmd, 1 if "edith_site" in cmd[2] else 0)

        subprocess.run = fake
        try:
            code = build_site.main(["--config", str(path), "--site", str(root / "site"), "--edith"])
            with_edith = list(calls)
            calls.clear()
            build_site.main(["--config", str(path), "--site", str(root / "site")])
        finally:
            subprocess.run = real
        assert code == 0, "EDITH failing must not fail the build"
        assert with_edith == ["flight_sim.whatif.build", "flight_sim.whatif.edith_site"], "EDITH runs after the page"
        assert calls == ["flight_sim.whatif.build"], "without --edith it is not run"


def test_rasaero_results_are_passed_on_when_named():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        spec = {"ork": "a.ork", "aero": "a.csv", "motor": "a.eng", "rasaero_results": "RASA/results.json"}
        (root / "RASA").mkdir()
        (root / "RASA" / "results.json").write_text("{}")
        cmd = build_site.plan(_config(root, {"R": spec}, "R"), root / "site")[0]["cmd"]
        assert cmd[cmd.index("--rasaero-results") + 1] == str(root / "RASA" / "results.json")
        plain = build_site.plan(_config(root, {"R": {"ork": "a.ork", "aero": "a.csv", "motor": "a.eng"}}, "R"), root / "site")[0]["cmd"]
        assert "--rasaero-results" not in plain
        (root / "RASA" / "results.json").unlink()
        try:
            build_site.plan(_config(root, {"R": spec}, "R"), root / "site")
        except SystemExit as stop:
            assert "results.json" in str(stop), "a missing results file is named"
        else:
            raise AssertionError("a missing results file must stop the build")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

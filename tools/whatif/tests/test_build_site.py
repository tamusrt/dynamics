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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

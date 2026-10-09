"""resolve.py / build_site.plan: files that were moved or renamed are found again and said out loud."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aero_meta  # noqa: E402
import build_site  # noqa: E402


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True, capture_output=True)


def _repo(root: Path) -> Path:
    base = root / "aero_modeling"
    for folder in ("R/OR", "R/Curves", "R/JARVIS", "R/RASA"):
        (base / folder).mkdir(parents=True)
    for name in ("R/OR/d.ork", "R/JARVIS/r_aero.csv", "R/RASA/r.CDX1", "R/Curves/m.eng", "R/Curves/n.eng"):
        (base / name).write_text(name, encoding="utf-8")
    (base / "whatif_config.json").write_text(json.dumps({"default": "A", "rockets": {"A": {
        "ork": "R/OR/d.ork", "aero": "R/JARVIS/r_aero.csv", "rasaero": "R/RASA/r.CDX1", "motor_dir": "R/Curves"}}}), encoding="utf-8")
    (base / "sim_config.json").write_text(json.dumps({"files": {"R/OR/d.ork": {"default_motor": "R/Curves/m.eng"}}}), encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "start")
    return base / "whatif_config.json"


def _plan(config: Path) -> dict:
    return build_site.plan(config, config.parent.parent / "site")[0]


def _texts(build: dict) -> str:
    return " | ".join(n["text"] for n in build["files_used"]["notices"])


def test_a_clean_repository_has_no_notices():
    with tempfile.TemporaryDirectory() as tmp:
        build = _plan(_repo(Path(tmp)))
        assert build["files_used"]["notices"] == [] and build["note"] == "pinned: the History tab's motor"


def test_a_renamed_design_and_a_moved_motor_folder_are_followed():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _repo(root)
        _git(root, "mv", "aero_modeling/R/OR/d.ork", "aero_modeling/R/OR/design2.ork")
        _git(root, "mv", "aero_modeling/R/Curves", "aero_modeling/R/Motors")
        _git(root, "commit", "-q", "-m", "organise")
        build = _plan(config)
        cmd = build["cmd"]
        assert cmd[cmd.index("--ork") + 1].endswith("design2.ork"), cmd
        assert Path(cmd[cmd.index("--motor") + 1]).parent.name == "Motors"
        assert "design2.ork" in _texts(build) and "Motors" in _texts(build)


def test_a_missing_pinned_motor_falls_back_to_the_newest_with_a_warning():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _repo(root)
        _git(root, "rm", "-q", "aero_modeling/R/Curves/m.eng")
        _git(root, "commit", "-q", "-m", "drop")
        build = _plan(config)
        assert build["note"].startswith("newest of 1") and "newest motor curve" in _texts(build)


def test_newest_mode_and_old_folders_are_ignored():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _repo(root)
        (root / "aero_modeling/R/Curves/OLD_stuff").mkdir()
        (root / "aero_modeling/R/Curves/OLD_stuff/z.eng").write_text("z", encoding="utf-8")
        data = json.loads(config.read_text(encoding="utf-8"))
        data["rockets"]["A"]["motor_mode"] = "newest"
        config.write_text(json.dumps(data), encoding="utf-8")
        assert Path(_plan(config)["cmd"][_plan(config)["cmd"].index("--motor") + 1]).name != "z.eng"


def test_an_unfindable_design_stops_with_one_message():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _repo(root)
        (root / "aero_modeling/R/JARVIS/r_aero.csv").unlink()  # the table is never guessed
        try:
            _plan(config)
        except SystemExit as stop:
            assert "r_aero.csv" in str(stop)
        else:
            raise AssertionError("a missing aero table must stop the build")


def test_files_used_and_notes_are_written():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        build = _plan(_repo(root))
        build["out"] = root / "out"
        build_site.write_files_used(build)
        data = json.loads((root / "out/files_used.json").read_text(encoding="utf-8"))
        assert data["version"] == 1 and {f["role"] for f in data["files"]} >= {"ork", "aero", "motor"}
        build["files_used"]["notices"].append({"level": "warn", "text": "x"})
        build_site.write_notes([build], root / "n.md")
        assert "- A: x" in (root / "n.md").read_text(encoding="utf-8")


def test_meta_record_makes_a_changed_design_stale():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cdx = root / "r.CDX1"
        cdx.write_text("<R><RocketDesign><Surface>Rough Camouflage Paint</Surface><NoseCone><Diameter>6</Diameter></NoseCone>"
                       "</RocketDesign><LaunchSite><Angle>5</Angle></LaunchSite></R>", encoding="utf-8")
        table = root / "r_aero.csv"
        table.write_text("Mach,Alpha,Phi,Cz,CMy\n0.3,2,0,-0.4,-1.4\n", encoding="utf-8")
        aero_meta.write_meta(table, cdx)
        assert aero_meta.check(table, cdx)["stale"] == []
        cdx.write_text(cdx.read_text(encoding="utf-8").replace("<Angle>5", "<Angle>7"), encoding="utf-8")
        assert aero_meta.check(table, cdx)["stale"] == [], "launch-site edits do not make the table stale"
        cdx.write_text(cdx.read_text(encoding="utf-8").replace("Diameter>6", "Diameter>7"), encoding="utf-8")
        assert aero_meta.check(table, cdx)["stale"], "a design edit does"


def test_move_files_updates_the_configs_and_git_remembers():
    import move_files

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _repo(root)
        old, new = root / "aero_modeling/R/Curves", root / "aero_modeling/R/Motors"
        assert move_files.main(["--base", str(config.parent), str(old), str(new)]) == 0
        data = json.loads(config.read_text(encoding="utf-8"))
        assert data["rockets"]["A"]["motor_dir"] == "R/Motors"
        assert "R/Motors/m.eng" in (config.parent / "sim_config.json").read_text(encoding="utf-8")
        _git(root, "commit", "-q", "-m", "move")
        assert _plan(config)["files_used"]["notices"] == []


def test_a_table_changed_by_the_latest_push_gets_a_warning():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _repo(root)
        assert "latest push" not in _texts(_plan(config))
        (root / "aero_modeling/R/JARVIS/r_aero.csv").write_text("changed", encoding="utf-8")
        _git(root, "commit", "-q", "-am", "new table")
        assert "latest push" in _texts(_plan(config))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

"""fix_cdx.py: the RASAero file follows the .ork, and its surface finish is always rough camouflage paint."""

from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fix_cdx  # noqa: E402

ORK = """<openrocket><rocket><subcomponents><stage><subcomponents>
<nosecone><length>0.762</length><aftradius>0.0762</aftradius></nosecone>
<bodytube><length>0.3048</length><radius>0.0762</radius><subcomponents>
<trapezoidfinset><fincount>4</fincount><rootchord>0.2</rootchord><tipchord>0.1</tipchord><height>0.15</height>
<sweeplength>0.1</sweeplength><thickness>0.003</thickness><axialoffset method="bottom">0</axialoffset></trapezoidfinset>
</subcomponents></bodytube></subcomponents></stage></subcomponents></rocket></openrocket>"""

CDX = """﻿<RASAeroDocument>
  <RocketDesign>
    <NoseCone><Length>30</Length><Diameter>6</Diameter><Location>0</Location></NoseCone>
    <BodyTube><Length>12</Length><Location>30</Location>
      <Fin><Count>4</Count><Chord>7.874</Chord><TipChord>3.937</TipChord><Span>5.9055</Span>
      <SweepDistance>3.937</SweepDistance><Thickness>0.1181</Thickness><Location>0</Location></Fin>
    </BodyTube>
    <Surface>{surface}</Surface>
    <ModifiedBarrowman>False</ModifiedBarrowman>
  </RocketDesign>
  <LaunchSite></LaunchSite>
</RASAeroDocument>"""


def _files(folder: Path, surface: str) -> tuple[Path, Path]:
    ork = folder / "a.ork"
    with zipfile.ZipFile(ork, "w") as archive:
        archive.writestr("rocket.ork", ORK)
    cdx = folder / "r.CDX1"
    cdx.write_bytes(CDX.format(surface=surface).encode("utf-8"))
    return ork, cdx


def _surface(cdx: Path) -> str:
    text = cdx.read_text(encoding="utf-8-sig")
    return text.split("<Surface>")[1].split("</Surface>")[0]


def test_a_smooth_finish_is_changed_to_rough_camouflage_paint():
    with tempfile.TemporaryDirectory() as tmp:
        ork, cdx = _files(Path(tmp), "Smooth Paint")
        changes = fix_cdx.fix(ork, cdx)
        assert "surface finish: Smooth Paint -> Rough Camouflage Paint" in changes
        assert _surface(cdx) == "Rough Camouflage Paint"
        assert (Path(tmp) / "r.CDX1.before-fix").exists()


def test_the_right_finish_is_left_alone_and_a_second_run_changes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        ork, cdx = _files(Path(tmp), "Rough Camouflage Paint")
        fix_cdx.fix(ork, cdx)  # the shape may need correcting once
        assert fix_cdx.fix(ork, cdx) == []
        assert _surface(cdx) == "Rough Camouflage Paint"


def test_a_dry_run_reports_the_finish_without_writing():
    with tempfile.TemporaryDirectory() as tmp:
        ork, cdx = _files(Path(tmp), "Smooth Paint")
        before = cdx.read_bytes()
        changes = fix_cdx.fix(ork, cdx, dry_run=True)
        assert any(line.startswith("surface finish") for line in changes)
        assert cdx.read_bytes() == before


def test_a_file_without_a_surface_element_is_not_given_one():
    with tempfile.TemporaryDirectory() as tmp:
        ork, cdx = _files(Path(tmp), "x")
        cdx.write_bytes(cdx.read_bytes().replace(b"<Surface>x</Surface>", b""))
        assert not any(line.startswith("surface") for line in fix_cdx.fix(ork, cdx))
        assert b"<Surface>" not in cdx.read_bytes()


def test_rogers_modified_barrowman_is_turned_on():
    with tempfile.TemporaryDirectory() as tmp:
        ork, cdx = _files(Path(tmp), "Rough Camouflage Paint")
        assert "Rogers Modified Barrowman: False -> True" in fix_cdx.fix(ork, cdx)
        assert b"<ModifiedBarrowman>True</ModifiedBarrowman>" in cdx.read_bytes()
        assert fix_cdx.fix(ork, cdx) == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

"""Make the RASAero .CDX1 match the OpenRocket .ork before RASAero measures it.

    python tools/whatif/fix_cdx.py --ork aero_modeling/IREC_2027/OR/2027_OR.ork \
        --cdx aero_modeling/IREC_2027/RASA/rasaero.CDX1 [--dry-run]

OpenRocket's RASAero export does not always copy the shape exactly (fin tip, span and sweep, and
the tube lengths have come out wrong). This rewrites, from the .ork:
  nose cone   length and diameter
  body tubes  each tube's length (when both files have the same number of tubes; otherwise the
              difference in total length goes into the last tube), and every part's Location
  fins        count, root chord, tip chord, span, sweep distance, thickness, and position
              (RASAero's "Distance from the base of the tube": from the aft end of the fins' tube
              to the fins' leading edge; 0 puts the whole fin behind the tube, the root chord
              puts its trailing edge at the tube's end)
  boat tail   length and rear diameter (RASAero takes the front diameter from the body tube)
Everything else in the file (surface, launch site, simulations) is kept as it is.
The old file is kept next to it as <name>.before-fix. Prints one line per value it changed.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

IN = 0.0254
BODY = ("nosecone", "bodytube", "transition")


def _kids(element: ET.Element) -> list[ET.Element]:
    sub = element.find("subcomponents")
    return list(sub) if sub is not None else []


def _num(element: ET.Element, tag: str, default: float = 0.0) -> float:
    text = element.findtext(tag)
    try:
        return float(text) if text not in (None, "", "auto") else default
    except ValueError:
        return default


def read_ork_shape(path: Path) -> dict:
    """Nose, tubes, fins and boat tail of the .ork, in inches."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        root = ET.fromstring(archive.read(next((n for n in names if n.endswith(".ork")), names[0])))
    shape: dict = {"tubes": []}
    radius = 0.0
    station = 0.0  # from the nose tip, in metres
    for stage in root.iter("stage"):
        for part in _kids(stage):
            if part.tag not in BODY:
                continue
            length_m = _num(part, "length")
            start, station = station, station + length_m
            length = length_m / IN
            if part.tag == "nosecone":
                radius = _num(part, "aftradius")
                shape["nose"] = {"Length": length, "Diameter": 2 * radius / IN}
            elif part.tag == "bodytube":
                radius = _num(part, "radius", radius)
                shape["tubes"].append(length)
            else:
                # not the front diameter: RASAero takes it from the body tube (its box is greyed out), and
                # OpenRocket's export writes the rear diameter there, which RASAero ignores
                shape["tail"] = {"Length": length, "RearDiameter": 2 * _num(part, "aftradius") / IN}
            for child in _kids(part):
                if child.tag == "trapezoidfinset":
                    lead = _fin_leading_edge(child, start, length_m)
                    shape["fins"] = {
                        "Location": (start + length_m - lead) / IN,
                        "Count": _num(child, "fincount", 4), "Chord": _num(child, "rootchord") / IN,
                        "TipChord": _num(child, "tipchord") / IN, "Span": _num(child, "height") / IN,
                        "SweepDistance": _num(child, "sweeplength") / IN, "Thickness": _num(child, "thickness") / IN,
                    }
    if "nose" not in shape or "fins" not in shape or not shape["tubes"]:
        raise ValueError(f"{path}: needs a nose cone, body tubes and a trapezoidal fin set")
    return shape


def _fin_leading_edge(fins: ET.Element, tube_start: float, tube_length: float) -> float:
    """Where the fins' root leading edge is, from the nose tip (OpenRocket's axial offset rules)."""
    root = _num(fins, "rootchord")
    node = fins.find("axialoffset")
    if node is None:
        node = fins.find("position")
    if node is None:
        return tube_start
    value = float(node.text or 0.0)
    method = node.attrib.get("method", node.attrib.get("type", "top"))
    if method == "absolute":
        return value
    if method == "bottom":
        return tube_start + tube_length - root + value
    if method == "middle":
        return tube_start + 0.5 * (tube_length - root) + value
    return tube_start + value


def _fmt(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _set(element: ET.Element, tag: str, value: float, where: str, changes: list[str]) -> None:
    node = element.find(tag)
    if node is None:
        return
    old = _num(element, tag)
    if abs(old - value) > 5e-4:
        label = {"Location": "distance from the base of the tube"}.get(tag, tag)
        changes.append(f"{where} {label}: {_fmt(old)} -> {_fmt(value)} in" if tag != "Count" else f"{where} fin count: {_fmt(old)} -> {_fmt(value)}")
        node.text = _fmt(value)


def fix(ork: Path, cdx: Path, dry_run: bool = False) -> list[str]:
    """Rewrite ``cdx`` to the shape of ``ork``; returns what changed (empty: it already matched)."""
    shape = read_ork_shape(ork)
    raw = cdx.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    root = ET.fromstring(raw.decode("utf-8-sig"))
    design = root.find("RocketDesign")
    if design is None:
        raise ValueError(f"{cdx}: no RocketDesign in the file")
    changes: list[str] = []
    nose = design.find("NoseCone")
    if nose is not None:
        for tag, value in shape["nose"].items():
            _set(nose, tag, value, "nose cone", changes)
    tubes = design.findall("BodyTube")
    want = list(shape["tubes"])
    if len(tubes) != len(want):  # put the whole difference in the last tube (the one with the fins)
        have = [_num(t, "Length") for t in tubes]
        want = have[:-1] + [have[-1] + sum(shape["tubes"]) - sum(have)]
    for i, (tube, length) in enumerate(zip(tubes, want), 1):
        _set(tube, "Length", length, f"body tube {i}", changes)
    fin = design.find(".//Fin")
    if fin is not None:
        for tag, value in shape["fins"].items():
            _set(fin, tag, value, "fins", changes)
    tail = design.find("BoatTail")
    if tail is not None and "tail" in shape:
        for tag, value in shape["tail"].items():
            _set(tail, tag, value, "boat tail", changes)
    # every part starts where the one before it ends
    station = 0.0
    for part in design:
        if part.tag in ("NoseCone", "BodyTube", "BoatTail") and part.find("Location") is not None:
            if abs(_num(part, "Location") - station) > 5e-4:
                part.find("Location").text = _fmt(station)
            station += _num(part, "Length")
    if changes and not dry_run:
        shutil.copyfile(cdx, cdx.with_name(cdx.name + ".before-fix"))
        # RASAero writes empty elements as <Tag></Tag>; keep that form rather than XML's <Tag />
        text = re.sub(r"<([A-Za-z0-9_]+) />", r"<\1></\1>", ET.tostring(root, encoding="unicode"))
        cdx.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
    return changes


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--ork", required=True, type=Path)
    p.add_argument("--cdx", required=True, type=Path)
    p.add_argument("--dry-run", action="store_true", help="only say what would change")
    a = p.parse_args(argv)
    changes = fix(a.ork, a.cdx, a.dry_run)
    for line in changes:
        print(line, flush=True)
    print(f"{a.cdx.name} {'would be' if a.dry_run else 'was'} corrected ({len(changes)} values)." if changes
          else f"{a.cdx.name} already matches {a.ork.name}.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

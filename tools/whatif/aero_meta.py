"""A small record of how an aero table was made, so a table that no longer fits its model can be noticed.

The RASAero table (``*_aero.csv``) is made by hand with the Update CSV button from the rocket's RASAero file
(``rasaero.CDX1``). Next to it Update CSV now keeps ``<name>.meta.json``. It says which version of the rocket
design the table was made from, how RASAero was set up, and two numbers read from the table itself.
Later this file is compared with what is committed now:

  * the rocket-design part of the .CDX1 changed (shape, surface finish, Rogers or classic method, turbulence)
    -> the table is out of date. Launch-site settings (rod angle, wind, temperature) are NOT part of the
    comparison: they do not change the aerodynamics, so editing them does not make a table stale;
  * the .CDX1 no longer matches the OpenRocket design -> the shape was edited in one and not the other;
  * the table's own numbers differ from the recorded ones -> the CSV was replaced or edited by hand.

Only the standard library is used, so the helper, the build and the checks can all use it.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_SURFACE = "Rough Camouflage Paint"
SETTINGS = ("Surface", "ModifiedBarrowman", "Turbulence")
VERSION = 1
IN = 0.0254
NUMBERS_TOLERANCE = 0.01  # the table's recorded numbers may differ by 1% (rounding) before it counts as changed


def meta_path(csv_path: Path) -> Path:
    """``ignis_2027_aero.csv`` -> ``ignis_2027_aero.meta.json`` (next to it)."""
    return csv_path.with_name(csv_path.stem + ".meta.json")


def _design(cdx: Path) -> ET.Element | None:
    try:
        text = cdx.read_bytes().decode("utf-8-sig")
        return ET.fromstring(text).find("RocketDesign")
    except (OSError, ET.ParseError, UnicodeDecodeError):
        return None


def _canonical(element: ET.Element) -> str:
    """The element and everything in it as text with no formatting, leaving out free-text comments."""
    if element.tag == "Comments":
        return ""
    inner = "".join(_canonical(child) for child in element)
    return f"<{element.tag}>{(element.text or '').strip()}{inner}</{element.tag}>"


def design_hash(cdx: Path) -> str:
    """A fingerprint of the rocket-design part of the .CDX1 (not the launch site, recovery or simulations)."""
    design = _design(cdx)
    if design is None:
        return ""
    return hashlib.sha256(_canonical(design).encode("utf-8")).hexdigest()


def settings_of(cdx: Path) -> dict[str, str]:
    """RASAero's surface finish, method and turbulence setting as written in the file."""
    design = _design(cdx)
    if design is None:
        return {}
    return {name: (design.findtext(name) or "").strip() for name in SETTINGS if design.find(name) is not None}


def reference_length_m(cdx: Path) -> float | None:
    """The body diameter in metres (RASAero's reference length), from the nose cone."""
    design = _design(cdx)
    try:
        return float(design.findtext("NoseCone/Diameter")) * IN if design is not None else None
    except (TypeError, ValueError):
        return None


def table_numbers(csv_path: Path, length_m: float | None, alpha: float = 2.0, mach_from: float = 0.3) -> dict[str, float]:
    """The table's normal-force slope (per radian) and centre of pressure (m from the nose) at the first Mach
    from 0.3 and an angle of attack of 2 degrees, read the way the Predictions page reads them
    (CN = -Cz, centre of pressure = CMy * length / Cz; roll angle 0)."""
    rows: dict[float, dict[str, float]] = {}
    try:
        with csv_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                try:
                    if float(row["Phi"]) != 0.0 or float(row["Alpha"]) != alpha or float(row["Mach"]) < mach_from:
                        continue
                    rows[float(row["Mach"])] = {"cz": float(row["Cz"]), "cmy": float(row["CMy"])}
                except (KeyError, ValueError):
                    continue
    except OSError:
        return {}
    if not rows:
        return {}
    first = rows[min(rows)]
    out = {"cna_m03": -first["cz"] / math.radians(alpha)}
    if length_m and first["cz"]:
        out["xcp_m03"] = first["cmy"] * length_m / first["cz"]
    return {k: round(v, 4) for k, v in out.items() if math.isfinite(v)}


def write_meta(csv_path: Path, cdx: Path) -> Path:
    """Write the record next to the table. Called by Update CSV right after it has made the table."""
    record = {
        "version": VERSION,
        "made": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "cdx_file": cdx.name,
        "design_hash": design_hash(cdx),
        "settings": settings_of(cdx),
        **table_numbers(csv_path, reference_length_m(cdx)),
    }
    target = meta_path(csv_path)
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    temp.replace(target)
    return target


def read_meta(csv_path: Path) -> dict | None:
    try:
        record = json.loads(meta_path(csv_path).read_text(encoding="utf-8"))
        return record if isinstance(record, dict) else None
    except (OSError, ValueError):
        return None


def ork_differences(ork: Path | None, cdx: Path | None) -> list[str]:
    """What the RASAero file gets wrong about the OpenRocket design (what fix_cdx.py would correct), as text."""
    if ork is None or cdx is None or not ork.is_file() or not cdx.is_file():
        return []
    try:
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import fix_cdx  # same folder

        with tempfile.TemporaryDirectory() as folder:
            copy = Path(folder) / cdx.name
            shutil.copyfile(cdx, copy)
            return list(fix_cdx.fix(ork, copy))
    except Exception:  # noqa: BLE001  (an unreadable design is reported by the doctor, not here)
        return []


def check(csv_path: Path, cdx: Path | None, ork: Path | None = None) -> dict:
    """Does the table still fit its model? Returns what the page and the messages need:
    ``{"meta_found", "stale": [reasons], "settings", "cna_m03", "xcp_m03"}``."""
    meta = read_meta(csv_path)
    length = reference_length_m(cdx) if cdx is not None else None
    now = table_numbers(csv_path, length)
    out: dict = {"meta_found": meta is not None, "stale": [], "settings": (meta or {}).get("settings", {}), **now}
    reasons: list[str] = out["stale"]
    if meta is not None and cdx is not None and cdx.is_file():
        if meta.get("design_hash") and meta["design_hash"] != design_hash(cdx):
            reasons.append(f"the rocket design in {cdx.name} was changed after the table was made")
        for key in ("cna_m03", "xcp_m03"):
            if key in meta and key in now and abs(now[key] - meta[key]) > NUMBERS_TOLERANCE * max(abs(meta[key]), 1e-9):
                reasons.append("the table file was replaced or edited after it was made")
                break
        surface = settings_of(cdx).get("Surface", "")
        if surface and surface != REQUIRED_SURFACE:
            reasons.append(f"{cdx.name} uses '{surface}' instead of {REQUIRED_SURFACE}")
    if meta is not None:
        used = (meta.get("settings") or {}).get("Surface", "")
        if used and used != REQUIRED_SURFACE:
            reasons.append(f"the table was made with '{used}' paint, not {REQUIRED_SURFACE}")
    differences = ork_differences(ork, cdx)  # also without a record: the shape was edited in one file and not the other
    if differences:
        reasons.append("the RASAero file does not match the OpenRocket design (" + "; ".join(differences[:3]) + ")")
    return out


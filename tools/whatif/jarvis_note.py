"""Write a short Jarvis note for the commit comment the Discord GitHub bot shows.

    python tools/whatif/jarvis_note.py --site site --live-url https://tamusrt.github.io/dynamics \
        --flightsim "Vision@abc1234" --out jarvis_note.md

Reads the predictions page the build just made (site/predictions/index.html) and writes a few
lines: Jarvis's apogee in every saved condition (next to OpenRocket's), links to JARVIS predictions
and VISION, and whether the RASAero table still matches the rocket (if not, run Update CSV).
Writes nothing if there is no predictions page.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

FT = 0.3048
IN = 0.0254
# the shape the RASAero table was made for, against the .ork: (key, name, is a count)
GEOM = [
    ("finCount", "fin count", True), ("finRoot", "fin root chord", False), ("finTip", "fin tip chord", False),
    ("finSpan", "fin span", False), ("finSweep", "fin sweep", False), ("finThick", "fin thickness", False),
    ("noseLength", "nose length", False), ("bodyLength", "body tubes", False),
    ("tailLength", "boat-tail length", False), ("tailAft", "boat-tail aft radius", False),
]


def read_data(page: Path) -> dict | None:
    """The DATA object the predictions page was built with."""
    if not page.is_file():
        return None
    text = page.read_text(encoding="utf-8")
    start = text.find("const DATA = ")
    if start < 0:
        return None
    data, _ = json.JSONDecoder().raw_decode(text, start + len("const DATA = "))
    return data if isinstance(data, dict) else None


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "sim"


def design_of(base: dict) -> dict:
    f = base["fins"]
    return {"finCount": f["count"], "finRoot": f["root"], "finTip": f["tip"], "finSpan": f["span"],
            "finSweep": f["sweep"], "finThick": f["thick"], "noseLength": base["noseLength"],
            "bodyLength": base["bodyLength"], "tailLength": base["tailLength"], "tailAft": base["tailAftRadius"]}


def rasaero_diffs(data: dict) -> list[str]:
    ref = data.get("refChange")
    if not ref:
        return []
    design, out = design_of(data["base"]), []
    for key, name, count in GEOM:
        r, d = ref.get(key), design.get(key)
        if r is None or d is None or abs(d - r) < (0.5 if count else 1e-4):
            continue
        out.append(f"{name} {r:.0f} → {d:.0f}" if count else f"{name} {r / IN:.2f} → {d / IN:.2f} in")
    return out


def note(data: dict, live_url: str, flightsim: str) -> str:
    live = live_url.rstrip("/")
    six, ors, viewers = data["sixdof"], data.get("openrocket") or {}, data.get("viewers") or {}
    default = data.get("defaultSim")
    lines = ["**JARVIS simulation**", f"{data.get('name', 'rocket')}" + (f" · flight_sim `{flightsim}`" if flightsim else "")]
    for sim, run in six.items():
        o = (ors.get(sim) or {}).get("m", {}).get("apogee")
        vs = f" · OpenRocket {o / FT:,.0f} ft ({(run['apogee'] / o - 1) * 100:+.1f}%)" if o else ""
        lines.append(f"- {sim}: apogee **{run['apogee'] / FT:,.0f} ft**, Mach {run['machMax']:.2f}{vs}")
    view = viewers.get(default) or f"viewer/{slug(default or 'sim')}/index.html"
    lines.append(f"[JARVIS predictions]({live}/predictions/) · [VISION]({live}/predictions/{view})")
    diffs = rasaero_diffs(data)
    if diffs:
        lines.append("⚠️ **RASAero table is out of date** (" + "; ".join(diffs) + "). "
                     "Rebuild it with Update CSV at the bottom of JARVIS predictions.")
    else:
        lines.append("✅ RASAero table matches the rocket.")
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--site", default="site")
    p.add_argument("--live-url", required=True)
    p.add_argument("--flightsim", default="")
    p.add_argument("--out", default="jarvis_note.md")
    a = p.parse_args()
    data = read_data(Path(a.site) / "predictions" / "index.html")
    if data and data.get("sixdof"):
        Path(a.out).write_text(note(data, a.live_url, a.flightsim), encoding="utf-8")


if __name__ == "__main__":
    main()

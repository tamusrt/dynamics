"""Write a short Jarvis note for the commit comment the Discord GitHub bot shows.

    python tools/whatif/jarvis_note.py --site site --live-url https://tamusrt.github.io/dynamics \
        --flightsim "Vision@abc1234" --out jarvis_note.md

Reads the predictions page the build just made (site/predictions/index.html) and writes a few
lines: a link to VISION, Jarvis's apogee in every saved condition (next to OpenRocket's), and
warnings when the stability margin falls below 1.0 caliber, when the RASAero table does not match
the rocket (run Update CSV), or when OpenRocket's saved runs used a different motor.
Writes jarvis_note.md (the commit comment) and jarvis_note.json (the Discord message, for
discord_post.py). Writes nothing if there is no predictions page.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

FT = 0.3048
IN = 0.0254
LB = 0.45359237
MIN_MARGIN = 1.0  # calibers, the team minimum
# the shape the RASAero table was made for, against the .ork: (key, name, is a count)
GEOM = [
    ("finCount", "fin count", True), ("finRoot", "fin root chord", False), ("finTip", "fin tip chord", False),
    ("finSpan", "fin span", False), ("finSweep", "fin sweep", False), ("finThick", "fin thickness", False),
    ("noseLength", "nose length", False), ("bodyLength", "body tubes", False),
    ("tailLength", "boat-tail length", False), ("tailAft", "boat-tail aft radius", False),
    ("finFromBase", "fin distance from the base of the tube", False),
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
            "bodyLength": base["bodyLength"], "tailLength": base["tailLength"], "tailAft": base["tailAftRadius"],
            "finFromBase": base["bodyStart"] + base["bodyLength"] - (f["teX"] - f["root"])}


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


def stem(name: str | None) -> str:
    return re.sub(r"\.[^.]*$", "", name or "")


def motor_problem(data: dict) -> str | None:
    """Why OpenRocket's numbers are for a different motor from the one Jarvis flies, if they are."""
    mf, files = data.get("motorFile") or {}, data.get("files") or {}
    sources = {(o or {}).get("source") for o in (data.get("openrocket") or {}).values()}
    motor = files.get("motor", "the motor file")
    if "history" in sources and data.get("openrocketMotor") and stem(data["openrocketMotor"]) != stem(motor):
        return f"OpenRocket (History tab) flies `{data['openrocketMotor']}`, Jarvis flies `{motor}`."
    if "file" in sources and mf.get("propKg") and mf.get("savedPropKg") is not None:
        what = []
        if abs(mf["propKg"] - mf["savedPropKg"]) > 0.5:
            what.append(f"propellant {mf['propKg'] / LB:.1f} lb vs {mf['savedPropKg'] / LB:.1f} lb")
        if abs(mf.get("totalKg", 0) - mf.get("savedTotalKg", 0)) > 1.0:
            what.append(f"total motor mass {mf['totalKg'] / LB:.1f} lb vs {mf['savedTotalKg'] / LB:.1f} lb")
        if what:
            return (f"`{motor}` and the motor in OpenRocket's saved runs differ: " + "; ".join(what)
                    + f". Rerun the OpenRocket simulations with `{motor}`.")
    return None


def note(data: dict, live_url: str, flightsim: str, picture: bool = False, stamp: str = "") -> dict:
    """The message: a title, the VISION link, the text, and whether anything needs attention.

    The commit comment starts with a short summary (the alerts on one line, the apogees on the next):
    the Discord GitHub bot shows only the first few hundred characters of a comment, and no pictures.
    """
    live = live_url.rstrip("/")
    six, ors = data["sixdof"], data.get("openrocket") or {}
    vision = f"{live}/#tab=vision"
    name = data.get("name", "rocket")
    low = {sim: run["marginLo"] for sim, run in six.items() if run.get("marginLo") is not None and run["marginLo"] < MIN_MARGIN}
    diffs, motor = rasaero_diffs(data), motor_problem(data)
    short, warn = [], []
    if low:
        worst = min(low, key=low.get)
        short.append(f"⚠️ **Stability {low[worst]:.2f} cal** ({worst})")
        warn.append("⚠️ **Stability below 1.0 cal**: " + ", ".join(f"{sim} {m:.2f} cal" for sim, m in low.items()) + ".")
    if diffs:
        short.append("⚠️ **RASAero table out of date**")
        warn.append("⚠️ **RASAero table is out of date** (" + "; ".join(diffs) + "). "
                    "Rebuild it with Update CSV at the bottom of JARVIS predictions.")
    if motor:
        short.append("⚠️ **OpenRocket used a different motor**")
        warn.append("⚠️ **Different motor in OpenRocket**: " + motor)
    alerts = " · ".join(short) or "✅ All checks pass"
    apogees = "Apogee: " + " · ".join(f"{sim} **{run['apogee'] / FT:,.0f}**" for sim, run in six.items()) + " ft"
    head = [f"**JARVIS simulation** · {name} · **[▶ Open VISION]({vision})**", alerts, apogees]
    details = [""]
    for sim, run in six.items():
        o = (ors.get(sim) or {}).get("m", {}).get("apogee")
        vs = f" · OpenRocket {o / FT:,.0f} ft ({(run['apogee'] / o - 1) * 100:+.1f}%)" if o else ""
        details.append(f"- {sim}: apogee {run['apogee'] / FT:,.0f} ft, Mach {run['machMax']:.2f}{vs}")
    details += [""] + (warn or ["✅ Stability above 1.0 cal, RASAero table matches the rocket, same motor as OpenRocket."])
    details.append(f"[JARVIS predictions]({live}/#tab=predictions) · [VISION]({vision})"
                   + (f" · flight_sim `{flightsim}`" if flightsim else ""))
    image = f"{live}/predictions/vision.png" + (f"?v={stamp}" if stamp else "")
    markdown = "\n".join(head + details + ([f"\n[![The whole flight in VISION]({image})]({vision})"] if picture else [])) + "\n"
    # the Discord embed has its own title (linking to VISION), so it starts at the alerts
    return {"title": f"JARVIS simulation · {name}", "url": vision, "description": "\n".join(head[1:] + details),
            "warnings": len(warn), "markdown": markdown}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--site", default="site")
    p.add_argument("--live-url", required=True)
    p.add_argument("--flightsim", default="")
    p.add_argument("--out", default="jarvis_note.md", help="the commit comment; the Discord message goes next to it as .json")
    p.add_argument("--picture", action="store_true", help="the VISION picture was taken (site/predictions/vision.png)")
    p.add_argument("--stamp", default="", help="added to the picture's address so Discord and GitHub fetch the new one")
    a = p.parse_args()
    data = read_data(Path(a.site) / "predictions" / "index.html")
    if data and data.get("sixdof"):
        msg = note(data, a.live_url, a.flightsim, a.picture, a.stamp)
        out = Path(a.out)
        out.write_text(msg.pop("markdown"), encoding="utf-8")
        out.with_suffix(".json").write_text(json.dumps(msg, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()

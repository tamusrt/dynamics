"""Run Missile DATCOM and turn its output into the flight sim's aero table CSV.

One script for the whole job, so it works like the RASAero converter but starts
from a DATCOM input file instead of RASAero text files:

    1. Asks for DATCOM input files one at a time ("First input file [for005.dat]",
       then "Next input file, or 'done' to finish"), found next to this script
       or in the current folder. Each file can hold any number of cases, each
       with its own Mach list (up to 20) and alpha list (up to 20).
    2. Runs each file through datcom.exe as written (a PLOT card is added to
       any case without one). DATCOM only reads for005.dat in its own folder,
       so that file is replaced for each run and put back afterwards, byte for
       byte. Each run's input and plot file are kept in datcom_runs/.
       (MAKE_GRID = True instead builds one complete grid from each file's
       rocket; see "THE GRID" below.)
    3. TILES every case of every file into ONE CSV, with the same columns as the
       RASAero one:

           Mach, Alpha, Phi, Cx, Cy, Cz, CMx, CMy, CMz

       The table's Machs and alphas are all the ones run anywhere. Where the
       tiles don't line up, the gaps are filled (fill_holes) and shown on a
       coverage map; where two tiles overlap, the one given later wins. Each
       point keeps the altitude it was run at; the sim's table has no altitude
       axis, so altitude only rides along with Mach.
    4. Prints the lines for rockets/<name>.py, including frame="missile".

Run it with:   python Make_DATCOM_Input.py                  (asks for the files)
           or: python Make_DATCOM_Input.py low_mach.dat high_mach.dat ...
Run it from the flight_sim repo (uv run python ...) and it also loads the CSV
with the sim's own aero_table_from_csv as a final check.

Nothing here reads for006.dat's results. If DATCOM writes no for003.dat, the
lines in for006.dat that say ERROR or WARNING are printed on your screen so you
can see why; nothing is copied or sent anywhere.

THE GRID
    The sim looks up its table over Mach, total angle of attack (Alpha) and
    aerodynamic roll angle (Phi), and outside the table it extends each value
    in a straight line. So one table must cover the whole flight: Mach 0 to
    past max Mach, alpha 0 to 180 deg (the RASAero tables do), and roll angles
    too if the fins are canted. DATCOM allows 20 Machs and 20 alphas per case at
    one roll angle, so the grid is split into as few cases as possible:

        cases = (Machs / 20, rounded up) x (alphas / 20, rounded up) x roll angles

    With the default lists that is 2 cases with no cant, and 14 with cant
    (roll angles 0-90 every 15 deg; 4 equal fins repeat every 90 deg, so those
    are copied around the circle). DATCOM runs them all in one go.

WHAT THE SIM DOES WITH THE CSV (flight_sim/utilities/data_loader.py and dcm.py)
    * Alpha is the total angle of attack and Phi the aerodynamic roll angle,
      in degrees. Phi = atan2(v_y, v_z) of the airspeed in body axes, so it is
      measured from body +Z toward body +Y.
    * frame="missile": the six numbers are along the body frame rolled by Phi so
      the crossflow lies along +Z, and the sim turns them into body axes after
      the lookup.
    * Every Mach x Alpha x Phi point must be there; a hole becomes NaN.

HOW DATCOM'S NUMBERS BECOME THE SIM'S
    1. DATCOM gives moments about its XCG; they are moved to REFPOINT.
    2. DATCOM's axes are body axes rolled with the rocket by PHI: x forward,
       y right, z down. At PHI = 0 they are exactly the sim's missile frame, so
           Cx = -CA, Cy = CY, Cz = -CN, CMx = CLL, CMy = CM, CMz = CLN
       which matches the RASAero converter where they overlap.
    3. At other PHI the roll is undone to get the missile frame. Which way
       DATCOM counts PHI is measured from the data (find_phi_sign).
    4. With N identical, evenly spaced fins the missile-frame numbers repeat
       every 360/N deg, so runs over one fin spacing fill the circle.
"""

import csv
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# --------------------------------------------------------------------------- #
# SETTINGS: change these for your run
# --------------------------------------------------------------------------- #

HERE = Path(__file__).resolve().parent

# File offered at the prompt when you just press Enter
DEFAULT_INPUT = "for005.dat"

# DATCOM program. None = look for datcom.exe next to the input file and this
# script, then ask.
DATCOM_EXE = None
# Give up on a DATCOM run after this many seconds
TIMEOUT_S = 600

# False: run each input file exactly as written (your own Mach/alpha sweeps),
# and tile them all into the one CSV.
# True: ignore each file's sweeps, copy the rocket from it and run the full grid
# below instead (one file is then enough for a complete table).
MAKE_GRID = False

# Mach numbers (DATCOM cannot do Mach 0; the lowest Mach is copied to a Mach 0
# row). Close spacing through transonic, where things change fastest.
MACHS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9,
         0.95, 1.0, 1.05, 1.1, 1.2, 1.3, 1.4, 1.5, 1.75, 2.0]

# Total angles of attack in degrees, 0 to 180 like the RASAero tables. Close
# spacing at small angles, where the rocket spends its powered flight.
ALPHAS = [0, 1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50, 60, 70, 80, 90,
          100, 110, 120, 130, 140, 150, 160, 170, 180]

# Altitude in FEET for every Mach (DATCOM uses it for air density and Reynolds
# number, which sets skin friction). One number = the same for all Machs; a
# list must have one value per Mach in MACHS; "base" = take the Mach/ALT pairs
# from the input file and interpolate (held at the end values outside them).
ALT_FT = "base"

# Fin cant in degrees, the same on every fin (0 = no cant). DATCOM: a positive
# deflection gives a negative (counterclockwise seen from behind) roll moment.
CANT_DEG = 0.0

# Roll angles PHI to run, degrees. No cant: 0 alone is enough. With cant, one
# fin spacing (0-90 for 4 fins), end included; the rest is filled in.
PHIS = [0.0] if CANT_DEG == 0 else [0.0, 15.0, 30.0, 45.0, 60.0, 75.0, 90.0]

# Reference values the flight sim uses, in INCHES, same meaning as in the
# RASAero converter: REFPOINT is the moment reference point measured aft of the
# nose tip, L_ref is the reference length (body diameter). DATCOM's XCG can stay
# whatever it is; the moments are moved from XCG to REFPOINT.
REFPOINT = 50.0
L_ref = 6.0

# Only keep Mach numbers up to this (None keeps everything)
MAX_MACH = None

# Copy the lowest Mach DATCOM ran to a Mach 0 row, so the sim never
# extrapolates below it at liftoff (the RASAero tables start at Mach 0 too).
ADD_MACH_ZERO = True

# Fill the rest of the roll circle from runs covering one fin spacing. Only
# valid if every fin is the same and they are evenly spaced.
FILL_BY_SYMMETRY = True

# Which way DATCOM's PHI turns compared with the sim's Phi: +1 same, -1
# opposite, None = measure it from the runs (needs a PHI that is not 0 or 180).
PHI_SIGN = None

# How close two numbers must be to count as the same point
TOL = 1e-6

# --------------------------------------------------------------------------- #

NUM_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[EeDd][-+]?\d+)?")
TO_INCHES = {"IN": 1.0, "FT": 12.0, "CM": 1 / 2.54, "M": 100 / 2.54}
NEEDED = ("ALPHA", "CN", "CM", "CA", "CY", "CLN", "CLL")
SIM_COLUMNS = ("Cx", "Cy", "Cz", "CMx", "CMy", "CMz")
DONE_WORDS = ("done", "q", "quit", "exit")

# Cards left out when the grid is built: they add output nobody reads here or
# change what PLOT writes. PLOT and NEXT CASE are put back on every case.
DROP_CARDS = ("PLOT", "PRESSURES", "WRITE", "FORMAT", "DAMP", "BUILD", "PART",
              "TRIM", "DUMP", "SAVE", "CASEID", "NEXT")


# =========================================================================== #
# Part 1: building the DATCOM input
# =========================================================================== #

def chunks(values, size=20):
    """Split a list into as few pieces of at most `size` as possible, nearly
    equal in length. DATCOM needs at least 2 alphas per case, so a lone
    leftover value is avoided by spreading the values evenly."""
    n = math.ceil(len(values) / size)
    step = math.ceil(len(values) / n)
    return [values[i:i + step] for i in range(0, len(values), step)]


def base_case_text(path, number):
    """The text of one case of a DATCOM file (cases end at NEXT CASE)."""
    cases, current = [], []
    for line in Path(path).read_text(errors="replace").splitlines():
        if re.match(r"^\s*NEXT\s+CASE\b", line, re.I):
            cases.append(current)
            current = []
        else:
            current.append(line)
    if any(l.strip() for l in current):
        cases.append(current)
    if not 1 <= number <= len(cases):
        raise SystemExit(f"{path} has {len(cases)} case(s); BASE_CASE = {number} is not one.")
    return cases[number - 1]


def geometry_lines(lines):
    """Everything in the base case except $FLTCON, $DEFLCT and the control cards
    in DROP_CARDS. Namelists are copied as written, over as many lines as they
    take. Returns (the DIM card or None, the other lines)."""
    text = "\n".join(lines)
    # a namelist runs from "$NAME" to the next "$"
    text = re.sub(r"\$(FLTCON|DEFLCT)\b.*?\$", "", text, flags=re.I | re.S)
    out, dim, inside = [], None, False
    for line in text.splitlines():
        s = line.strip()
        word = s.split()[0].upper() if s else ""
        if not inside and word == "DIM":
            dim = line
        elif inside or s.startswith("$"):
            out.append(line)
        elif s and word not in DROP_CARDS:
            out.append(line)
        # track whether we are inside a namelist that continues on the next line
        if s:
            inside = (inside or s.startswith("$")) and line.count("$") % 2 == (0 if inside else 1)
    return dim, out


def base_altitudes(lines):
    """Altitude for each Mach in MACHS from the base case's own MACH/ALT pairs.

    Straight-line interpolation between the base case's Machs; below the lowest
    or above the highest, the end altitude is held. No ALT in the base case
    gives 0 ft for every Mach."""
    blocks = dict(parse_namelists("\n".join(lines)))
    flt = blocks.get("FLTCON", {})
    pairs = sorted((m, a) for m, a in zip(flt.get("MACH", []), flt.get("ALT", []))
                   if isinstance(m, float) and isinstance(a, float))
    if not pairs:
        print("Base case has no ALT; using 0 ft for every Mach.")
        return [0.0] * len(MACHS)
    out = []
    for m in MACHS:
        if m <= pairs[0][0]:
            out.append(pairs[0][1])
        elif m >= pairs[-1][0]:
            out.append(pairs[-1][1])
        else:
            (m0, a0), (m1, a1) = next((p, q) for p, q in zip(pairs, pairs[1:]) if p[0] <= m <= q[0])
            out.append(round(a0 + (m - m0) / (m1 - m0) * (a1 - a0), 1))
    return out


def namelist_list(key, values, per_line=5):
    """KEY=v1,v2,... split over lines DATCOM accepts, KEY(6)=... continuing."""
    parts = []
    for i in range(0, len(values), per_line):
        tag = key if i == 0 else f"{key}({i + 1})"
        parts.append(f"{tag}=" + ",".join(f"{v:g}." if float(v).is_integer() else f"{v:g}"
                                          for v in values[i:i + per_line]))
    return parts


def fltcon(machs, alts, alphas, phi):
    """The $FLTCON namelist for one case."""
    items = [f"NMACH={len(machs)}.", f"NALPHA={len(alphas)}."]
    items += namelist_list("MACH", machs)
    items += namelist_list("ALT", alts)
    items += namelist_list("ALPHA", alphas)
    if phi:
        items.append(f"PHI={phi:g}.")   # with PHI set, ALPHA is total angle of attack
    lines = [" $FLTCON " + items[0] + ","]
    lines += ["         " + it + "," for it in items[1:]]
    lines[-1] = lines[-1].rstrip(",") + "$"
    return lines


def build_grid(lines):
    """The full-grid DATCOM input for the rocket in one case's lines.
    Returns (text, number of cases, one-line description)."""
    if ALT_FT == "base":
        alts = base_altitudes(lines)
    elif isinstance(ALT_FT, (list, tuple)):
        alts = list(ALT_FT)
    else:
        alts = [float(ALT_FT)] * len(MACHS)
    if len(alts) != len(MACHS):
        raise SystemExit(f"ALT_FT has {len(alts)} values but MACHS has {len(MACHS)}.")
    dim, geom = geometry_lines(lines)
    npanel = re.search(r"NPANEL\s*=\s*([\d.]+)", "\n".join(geom), re.I)
    npanel = int(float(npanel.group(1))) if npanel else 4

    mach_sets = chunks(sorted(zip(MACHS, alts)))
    alpha_sets = chunks(sorted(float(a) for a in ALPHAS))
    text, n = [], 0
    for phi in PHIS:
        for ms in mach_sets:
            for al in alpha_sets:
                n += 1
                text.append(f" CASEID PHI {phi:g}, CANT {CANT_DEG:g}, "
                            f"MACH {ms[0][0]:g}-{ms[-1][0]:g}, ALPHA {al[0]:g}-{al[-1]:g}")
                if dim:
                    text.append(dim)
                text += fltcon([m for m, _ in ms], [a for _, a in ms], al, phi)
                text += geom
                if CANT_DEG:
                    text.append(" $DEFLCT DELTA1=" + ",".join([f"{float(CANT_DEG)!r}"] * npanel) + ",$")
                text.append(" PLOT")
                text.append(" NEXT CASE")
    about = (f"{n} case(s): {len(PHIS)} roll angle(s) x {len(mach_sets)} Mach set(s) x "
             f"{len(alpha_sets)} alpha set(s), Mach {min(MACHS):g}-{max(MACHS):g}, "
             f"alpha {min(ALPHAS):g}-{max(ALPHAS):g}, cant {CANT_DEG:g} deg")
    return "\n".join(text) + "\n", n, about


def as_written(text):
    """The input file run as it is, with a PLOT card added to any case that has
    none (without PLOT, DATCOM writes nothing to for003.dat for that case)."""
    out = []
    for case in split_cases(text):
        body = "\n".join(case).rstrip()
        if not re.search(r"^\s*PLOT\b", body, re.I | re.M):
            body = re.sub(r"^(\s*NEXT\s+CASE\b)", r" PLOT\n\1", body, count=1, flags=re.I | re.M) \
                if re.search(r"^\s*NEXT\s+CASE\b", body, re.I | re.M) else body + "\n PLOT"
        out.append(body)
    return "\n".join(out) + "\n"


def split_cases(text):
    """The input's cases as lists of lines; each case ends with its NEXT CASE
    line, and text after the last NEXT CASE is one more case if it has namelists."""
    cases, cur = [], []
    for line in text.splitlines():
        cur.append(line)
        if re.match(r"^\s*NEXT\s+CASE\b", line, re.I):
            cases.append(cur)
            cur = []
    if parse_namelists("\n".join(cur)):
        cases.append(cur)
    return cases


# =========================================================================== #
# Part 2: converting DATCOM's output (for005.dat + for003.dat) to the CSV
# =========================================================================== #

def fmt(x):
    """Compact number format, same as the RASAero converter: 0, -0.449931, -2.34154."""
    if abs(x) < 1e-12:
        return "0"
    return f"{x:g}"


# --------------------------------------------------------------------------- #
# Reading for005.dat
# --------------------------------------------------------------------------- #

def parse_namelists(text):
    """Read DATCOM namelist blocks like  $FLTCON NMACH=3., MACH=0.5,0.8,1.2, $
    into [(NAME, {KEY: [values]})]. A key with an index, like MACH(6)=..., fills
    the list starting at that position (that is how long lists are split over
    lines). Text values (TNOSE=HAACK) are kept as upper-case strings."""
    blocks = []
    key_re = re.compile(r"([A-Z][A-Z0-9]*)\s*(?:\(\s*(\d+)\s*\))?\s*=", re.I)
    for m in re.finditer(r"\$([A-Z][A-Z0-9]*)\b(.*?)\$", text, re.I | re.S):
        name, body = m.group(1).upper(), m.group(2)
        vals = {}
        keys = list(key_re.finditer(body))
        for k, km in enumerate(keys):
            key = km.group(1).upper()
            start = int(km.group(2)) - 1 if km.group(2) else 0
            end = keys[k + 1].start() if k + 1 < len(keys) else len(body)
            parsed = []
            for v in body[km.end():end].replace("\n", ",").split(","):
                v = v.strip()
                if not v:
                    continue
                try:
                    parsed.append(float(v.replace("D", "E").replace("d", "e")))
                except ValueError:
                    parsed.append(v.strip(".").upper())
            arr = vals.setdefault(key, [])
            if len(arr) < start:
                arr.extend([None] * (start - len(arr)))
            arr[start:start + len(parsed)] = parsed
        blocks.append((name, vals))
    return blocks


def numbers(vals, key, count=None):
    """The numeric values of one namelist key, cut to `count` if given."""
    out = [v for v in vals.get(key, []) if isinstance(v, float)]
    return out[:int(count)] if count else out


def read_for005(path):
    """Split for005.dat into cases (each ends at a NEXT CASE card) and pull out
    what the converter needs from each one.

    DATCOM forgets the previous case's inputs unless that case had a SAVE card,
    so namelists are only carried forward after a SAVE. "geometry" is every
    namelist except FLTCON (flight conditions), so two cases with the same
    geometry are the same rocket and can go in one table.
    Returns (cases, units)."""
    lines = Path(path).read_text(errors="replace").splitlines()

    units = "FT"   # DATCOM's default when there is no DIM card
    for line in lines:
        m = re.match(r"^\s*DIM\s+(\w+)", line, re.I)
        if m:
            units = m.group(1).upper()
            break

    segments, current = [], []
    for line in lines:
        if line.lstrip().startswith("*"):          # comment card
            continue
        if re.match(r"^\s*NEXT\s+CASE\b", line, re.I):
            segments.append("\n".join(current))
            current = []
        else:
            current.append(line)
    if "".join(current).strip():
        segments.append("\n".join(current))       # last case without NEXT CASE

    cases, carried = [], {}
    for number, seg in enumerate(segments, start=1):
        nl = {k: dict(v) for k, v in carried.items()}
        for name, vals in parse_namelists(seg):
            nl.setdefault(name, {}).update(vals)
        flt = nl.get("FLTCON", {})
        nmach, nalpha = numbers(flt, "NMACH"), numbers(flt, "NALPHA")
        fins = [v for k, v in sorted(nl.items()) if k.startswith("FINSET")]
        cards = {l.strip().split()[0].upper() for l in seg.splitlines()
                 if l.strip() and l.strip()[0].isalpha()}
        caseid = [re.sub(r"^\s*CASEID\b\s*", "", l, flags=re.I).strip()
                  for l in seg.splitlines() if re.match(r"^\s*CASEID\b", l, re.I)]
        cases.append(dict(
            number=number,
            name=caseid[0] if caseid else "",
            machs=numbers(flt, "MACH", nmach[0] if nmach else None),
            # ALT goes with MACH one for one (DATCOM's default is sea level)
            alts=(numbers(flt, "ALT", nmach[0] if nmach else None) or [])
            + [0.0] * len(numbers(flt, "MACH", nmach[0] if nmach else None)),
            alphas=numbers(flt, "ALPHA", nalpha[0] if nalpha else None),
            phi=(numbers(flt, "PHI") or [0.0])[0],
            beta=(numbers(flt, "BETA") or [0.0])[0],
            plot="PLOT" in cards,
            refq=nl.get("REFQ", {}),
            npanel=int(numbers(fins[0], "NPANEL")[0]) if fins and numbers(fins[0], "NPANEL") else None,
            geometry=repr(sorted((k, sorted(v.items())) for k, v in nl.items() if k != "FLTCON")),
        ))
        carried = nl if "SAVE" in cards else {}
    return cases, units


# --------------------------------------------------------------------------- #
# Reading for003.dat
# --------------------------------------------------------------------------- #

def read_for003(path):
    """Read the PLOT file into blocks, one per ZONE, in file order:
    {"title": ..., "mach": Mach from the title, "rows": [{ALPHA: .., CN: .., ...}]}.

    Numbers are found with a regex rather than split on spaces, because
    fixed-width Fortran output can run a negative number into the one before it
    (8.6014-10.7919). The header can name more columns than the rows hold (a
    real run listed ...,DELTA,CL,CD over 8-number rows), so only the first 7 are
    required."""
    names, blocks = None, []
    for line in Path(path).read_text(errors="replace").splitlines():
        s = line.strip()
        if not s:
            continue
        up = s.upper()
        if up.startswith("VARIABLES"):
            names = [n.strip().strip('"').upper()
                     for n in s.split("=", 1)[1].replace(",", " ").split()]
            continue
        if up.startswith("ZONE"):
            title = re.search(r'T\s*=\s*"([^"]*)"', s, re.I)
            title = title.group(1) if title else s
            mach = re.search(r"MACH\s*=\s*([-+.\dEe]+)", title, re.I)
            blocks.append(dict(title=title.strip(),
                               mach=float(mach.group(1)) if mach else None, rows=[]))
            continue
        if names is None or not blocks:
            continue
        vals = [float(v.replace("D", "E")) for v in NUM_RE.findall(s)]
        row = dict(zip(names, vals))
        if all(k in row for k in NEEDED):
            blocks[-1]["rows"].append(row)
    return blocks


def match_blocks_to_cases(blocks, cases):
    """Work out which case and Mach each for003 block belongs to.

    DATCOM writes one block per Mach for every case with a PLOT card, case after
    case, each case's Machs from low to high. So each case takes the next NMACH
    blocks, and each block is matched to the case's Mach with the same value.
    Trimmed blocks (from TRIM) are skipped: the sim wants the untrimmed rocket.
    Returns [(block, case, mach)]."""
    usable = [b for b in blocks if "TRIMMED" not in b["title"].upper()]
    if len(usable) != len(blocks):
        print(f"Note: skipped {len(blocks) - len(usable)} trimmed block(s) in for003.dat")

    for label, chosen in (("cases with a PLOT card", [c for c in cases if c["plot"]]),
                          ("all cases", cases)):
        if sum(len(c["machs"]) for c in chosen) != len(usable):
            continue
        out, i = [], 0
        for c in chosen:
            group = usable[i:i + len(c["machs"])]
            i += len(c["machs"])
            left = list(c["machs"])
            for b in group:
                hit = left[0]
                if b["mach"] is not None:
                    hit = next((m for m in left if abs(b["mach"] - m) < 0.006), None)
                    if hit is None:
                        raise SystemExit(
                            f"for003.dat has a Mach {b['mach']} block where case "
                            f"{c['number']} ({label}) has no such Mach.\n"
                            f"Are this for003.dat and for005.dat from the same run?")
                left.remove(hit)
                out.append((b, c, hit))
        return out

    with_plot = sum(len(c["machs"]) for c in cases if c["plot"])
    raise SystemExit(
        f"for003.dat has {len(usable)} blocks, but for005.dat expects {with_plot} "
        f"(one per Mach in each case with a PLOT card).\n"
        f"Common causes: the files are from different runs, a case stopped early "
        f"(check the end of for006.dat yourself for where), or BUILD is on (it adds "
        f"extra blocks; turn it off for this).")


# --------------------------------------------------------------------------- #
# Turning DATCOM coefficients into the sim's columns
# --------------------------------------------------------------------------- #

def move_moments(row, xcg, xref, lref, latref):
    """Move DATCOM's moments from XCG to the sim's reference point.

    x is measured aft of the nose (DATCOM's XCG is "+aft"). A normal force at a
    point aft of the reference pitches the nose down, so moving the reference
    point from XCG to XREF adds CN * (XREF - XCG) / LREF to CM. Side force does
    the same to yaw, using LATREF. Roll is about the body axis, so moving the
    point along the axis does not change it, and forces do not change.
    Check: a pure CN = 1 at x = 150 in, XCG = 134, XREF = 50, LREF = 6 gives
    CM = -16/6 about XCG, plus 1 * (50 - 134)/6 = -100/6, which is CM about 50."""
    d = xref - xcg
    row = dict(row)
    row["CM"] = row["CM"] + row["CN"] * d / lref
    row["CLN"] = row["CLN"] + row["CY"] * d / latref
    return row


def datcom_axes(r):
    """DATCOM coefficients along DATCOM's own (rolled body) axes, in the sim's
    column names: x forward, y right, z down."""
    return dict(Cx=-r["CA"], Cy=r["CY"], Cz=-r["CN"],
                CMx=r["CLL"], CMy=r["CM"], CMz=r["CLN"])


def body_to_missile(c, phi_deg):
    """Undo a roll of phi: body-axis components to missile-frame components.

    The inverse of the sim's missile_to_body(phi) = r3f.dcm(phi, "x"), which is
    v_body = [[1,0,0],[0,cos,sin],[0,-sin,cos]] @ v_missile. Axial force and roll
    moment lie along the roll axis, so they do not change."""
    a = math.radians(phi_deg)
    ca, sa = math.cos(a), math.sin(a)
    out = dict(c)
    out["Cy"] = ca * c["Cy"] - sa * c["Cz"]
    out["Cz"] = sa * c["Cy"] + ca * c["Cz"]
    out["CMy"] = ca * c["CMy"] - sa * c["CMz"]
    out["CMz"] = sa * c["CMy"] + ca * c["CMz"]
    return out


def find_phi_sign(rows):
    """Measure which way DATCOM's PHI turns compared with the sim's Phi.

    In the missile frame the crossflow is along +Z by definition, so the normal
    force lands almost entirely on Cz and Cy is about zero (exactly zero without
    fin cant, small with it). Undo the roll both ways and keep the one that
    leaves the least on Cy. Only rows with PHI not 0 or 180 and some alpha can
    tell the two apart. rows: [(phi, alpha, datcom-axes coefficients)]."""
    useful = [(p, a, c) for p, a, c in rows
              if abs(a) > 1.0 and abs(math.sin(math.radians(p))) > 0.1]
    if not useful:
        return None, None
    err = {s: sum(body_to_missile(c, s * p)["Cy"] ** 2 for p, _, c in useful) for s in (1, -1)}
    size = sum(c["Cy"] ** 2 + c["Cz"] ** 2 for _, _, c in useful) or 1.0
    sign = min(err, key=err.get)
    return sign, err[sign] / size


def fill_by_symmetry(table, period):
    """Copy runs to every fin spacing around the circle.

    With N identical, evenly spaced fins, the rocket at roll angle Phi + 360/N
    looks exactly like it does at Phi, and the missile frame turns with the
    crossflow, so all six missile-frame numbers are the same. A real run at a
    point always wins over a copy. If runs exist at both 0 and one fin spacing,
    they are compared as a check on the symmetry."""
    zero = {(m, a): c for (m, a, p), c in table.items() if abs(p) < TOL}
    end = {(m, a): c for (m, a, p), c in table.items() if abs(p - period) < TOL}
    both = [k for k in zero if k in end and k[1] > 1.0]
    if both:
        diff = max(abs(zero[k][n] - end[k][n]) for k in both for n in SIM_COLUMNS)
        big = max(abs(zero[k][n]) for k in both for n in SIM_COLUMNS) or 1.0
        print(f"  Symmetry check: Phi={period:g} vs Phi=0 differ by at most "
              f"{diff:.3g} ({diff / big:.1%} of the largest coefficient)")
        if diff / big > 0.05:
            print("  WARNING: poor match. Are the fins identical and evenly spaced? "
                  "Consider running the full 0-360 sweep and FILL_BY_SYMMETRY = False.")
    filled = dict(table)
    copies = int(round(360.0 / period))
    for (m, a, p), c in table.items():
        for k in range(1, copies):
            q = round((p + k * period) % 360.0, 6)
            filled.setdefault((m, a, 0.0 if abs(q - 360.0) < TOL else q), c)
    return filled


def check_refs(used, to_in):
    """Reference values must be the same in every case used, and match the sim.
    Returns (xcg, lref, latref) in inches and SREF in DATCOM units."""
    refs = {(c["refq"].get("XCG", [0.0])[0], c["refq"].get("LREF", [None])[0],
             c["refq"].get("LATREF", [None])[0], c["refq"].get("SREF", [None])[0])
            for c in used}
    if len(refs) > 1:
        raise SystemExit(f"Cases use different $REFQ values {refs}; they must all match.")
    xcg, lref, latref, sref = refs.pop()
    if lref is None:
        raise SystemExit("Set LREF in $REFQ (otherwise DATCOM picks its own).")
    xcg_in, lref_in = xcg * to_in, lref * to_in
    latref_in = (latref if latref is not None else lref) * to_in
    if abs(lref_in - L_ref) > 1e-3 or abs(latref_in - L_ref) > 1e-3:
        raise SystemExit(f"DATCOM reference lengths (LREF {lref_in:g} in, LATREF "
                         f"{latref_in:g} in) must both equal L_ref = {L_ref:g} in.")
    if any(c["beta"] and not c["phi"] for c in used):
        print("  WARNING: a case uses BETA without PHI; its ALPHA is then body-axis alpha, "
              "not total alpha. Use PHI instead for this table.")
    return xcg_in, lref_in, latref_in, sref


def save_csv(table, path):
    """Write the CSV, coping with Windows locking it. Excel (or anything else
    with the file open) stops it from being overwritten, so: try for a few
    seconds, then ask you to close it and press Enter, or type another name.
    Returns the path actually written."""
    path = Path(path)
    while True:
        for _ in range(6):
            try:
                write_table(table, path)
                return path
            except PermissionError:
                time.sleep(0.5)
        print(f"\n  Can't write {path.name}: another program has it open (Excel, most likely).")
        s = ask("  Close it and press Enter to try again, or type a different file name", "")
        if s:
            path = path.with_name(s if s.lower().endswith(".csv") else s + ".csv")


def write_table(table, path):
    """Write the CSV sorted by Mach, then Alpha, then Phi."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Mach", "Alpha", "Phi", *SIM_COLUMNS])
        for key in sorted(table):
            w.writerow([fmt(v) for v in key] + [fmt(table[key][n]) for n in SIM_COLUMNS])


def sim_check(path, sref_m2):
    """Load the CSV with the sim's own loader, if this runs inside the repo."""
    try:
        from flight_sim.units import scalar, vector  # pylint: disable=import-outside-toplevel
        from flight_sim.utilities.data_loader import aero_table_from_csv  # pylint: disable=import-outside-toplevel
    except ImportError:
        return
    import numpy as np  # pylint: disable=import-outside-toplevel
    t = aero_table_from_csv(str(path), reference_area=scalar(sref_m2 or 1.0, "m**2"),
                            reference_length=scalar(L_ref * 0.0254, "m"),
                            reference_point=vector((-REFPOINT * 0.0254, 0.0, 0.0), "m"),
                            frame="missile")
    bad = int(np.isnan(t.values).sum())
    print(f"  Sim loader check: {t.values.shape[:3]} grid, "
          f"{'no gaps' if not bad else f'{bad} NaN values (gaps!)'}")


# --------------------------------------------------------------------------- #

def pick_rocket(runs):
    """All the tiles must be the same rocket. Cases whose geometry (every
    namelist except FLTCON) differs from the rest are different rockets, e.g.
    another fin design or another cant; ask which rocket to build the table for
    and leave the others out. Returns the geometry to keep."""
    rockets = {}
    for run in runs:
        for c in run["cases"]:
            rockets.setdefault(c["geometry"], []).append(f"{run['label']} case {c['number']}"
                                                          + (f" ({c['name']})" if c["name"] else ""))
    if len(rockets) == 1:
        return next(iter(rockets))
    keys = list(rockets)
    print(f"\nThese cases describe {len(rockets)} different rockets (different fins, cant or "
          f"body), and one table can only hold one:")
    for i, k in enumerate(keys, 1):
        shown = rockets[k][:4] + (["..."] if len(rockets[k]) > 4 else [])
        print(f"  {i}: {', '.join(shown)}")
    while True:
        s = ask(f"Which rocket goes in the CSV (1-{len(keys)})", "1")
        if s.isdigit() and 1 <= int(s) <= len(keys):
            return keys[int(s) - 1]
        print(f"  Type a number from 1 to {len(keys)}.")


def tile(runs, xcg_in, lref_in, latref_in, phi_sign):
    """Put every run's numbers into one {(Mach, Alpha, Phi): coefficients} table.

    Runs are laid down in the order they were given, and within a run case by
    case, so where two tiles cover the same point the later one wins. That way
    a run added at the end can patch or refine part of the table. Returns the
    table and the set of points that came straight from DATCOM."""
    table, origin, dropped, overlaps, differ = {}, {}, 0, 0, []
    for run in runs:
        for block, case, mach in run["matched"]:
            if MAX_MACH is not None and mach > MAX_MACH + TOL:
                continue
            alt = dict(zip(case["machs"], case["alts"])).get(mach, 0.0)
            if len(block["rows"]) != len(case["alphas"]):
                print(f"  WARNING: {run['label']} case {case['number']} Mach {mach}: "
                      f"{len(block['rows'])} rows but {len(case['alphas'])} alphas in its input")
            for row in block["rows"]:
                alpha, phi = row["ALPHA"], case["phi"]
                if abs(alpha) > TOL and all(row[k] == 0 for k in NEEDED[1:]):
                    dropped += 1        # DATCOM writes all zeros where it had no answer
                    continue
                if alpha < 0:           # (-a, PHI) is the same flight as (a, PHI + 180)
                    alpha, phi = -alpha, phi + 180.0
                sim_phi = round((phi_sign * phi) % 360.0, 6)
                c = datcom_axes(move_moments(row, xcg_in, REFPOINT, lref_in, latref_in))
                key = (round(mach, 6), round(alpha, 6), sim_phi)
                new = body_to_missile(c, sim_phi)
                if key in table:
                    overlaps += 1
                    if max(abs(new[n] - table[key][n]) for n in SIM_COLUMNS) > 1e-3:
                        differ.append((key, origin[key], (run["label"], case["number"], alt)))
                table[key] = new
                origin[key] = (run["label"], case["number"], alt)
    if dropped:
        print(f"  Dropped {dropped} all-zero row(s) (DATCOM had no answer there)")
    if overlaps:
        print(f"  {overlaps} point(s) were run more than once; the later run was kept.")
    if differ:
        (m, a, p), was, now = differ[0]
        print(f"  {len(differ)} of those gave different numbers, e.g. Mach {m:g}, alpha {a:g}: "
              f"{was[0]} case {was[1]} at {was[2]:g} ft vs {now[0]} case {now[1]} at "
              f"{now[2]:g} ft. Usually that is the altitude; the later one is in the CSV.")
    return table, set(table)


def _interp(table, key, lo, hi, t):
    c0, c1 = table[lo], table[hi]
    table[key] = {n: c0[n] + t * (c1[n] - c0[n]) for n in SIM_COLUMNS}


def fill_holes(table):
    """Complete the grid, as the sim's loader needs every Mach x Alpha x Phi point.

    Tiles rarely line up exactly (one run at alphas 0-4 for Mach 0.3-0.9,
    another at 0-10 for Mach 1.0-2.0, ...), so the missing points are filled in
    three steps, and each step only uses numbers DATCOM actually produced or
    the step before filled:
      1. along alpha: straight line between the nearest run alphas at the same
         Mach and Phi;
      2. along Mach: straight line between the nearest Machs that have the point;
      3. anything left lies beyond the alphas run at that Mach. It is copied
         from the nearest Mach that was run at that alpha, which keeps the
         shape of the alpha curve (normal force keeps rising with alpha);
      4. only if no Mach at all has that alpha, the value at the nearest alpha
         at the same Mach is held.
    Steps 3 and 4 are guesses and are reported, but they never run away the
    way extrapolating would.
    Returns the table, its axes and how many points each step filled."""
    machs = sorted({m for m, _, _ in table})
    alphas = sorted({a for _, a, _ in table})
    phis = sorted({p for _, _, p in table})
    counts, guessed = [0, 0, 0, 0], set()
    for m in machs:                                       # 1. along alpha
        for p in phis:
            have = [a for a in alphas if (m, a, p) in table]
            for a in alphas:
                if (m, a, p) in table:
                    continue
                lo = max((x for x in have if x < a), default=None)
                hi = min((x for x in have if x > a), default=None)
                if lo is not None and hi is not None:
                    _interp(table, (m, a, p), (m, lo, p), (m, hi, p), (a - lo) / (hi - lo))
                    counts[0] += 1
    for a in alphas:                                      # 2. along Mach
        for p in phis:
            have = [m for m in machs if (m, a, p) in table]
            for m in machs:
                if (m, a, p) in table:
                    continue
                lo = max((x for x in have if x < m), default=None)
                hi = min((x for x in have if x > m), default=None)
                if lo is not None and hi is not None:
                    _interp(table, (m, a, p), (lo, a, p), (hi, a, p), (m - lo) / (hi - lo))
                    counts[1] += 1
    for a in alphas:                                      # 3. copy the nearest Mach
        for p in phis:
            have = [m for m in machs if (m, a, p) in table]
            if not have:
                continue
            for m in machs:
                if (m, a, p) not in table:
                    near = min(have, key=lambda x: abs(x - m))
                    table[(m, a, p)] = dict(table[(near, a, p)])
                    guessed.add((m, a, p))
                    counts[2] += 1
    for m in machs:                                       # 4. hold the nearest alpha
        for p in phis:
            have = [a for a in alphas if (m, a, p) in table]
            if not have:
                raise SystemExit(f"Nothing at all at Mach {m:g}, Phi {p:g}; can't fill it.")
            for a in alphas:
                if (m, a, p) not in table:
                    near = min(have, key=lambda x: abs(x - a))
                    table[(m, a, p)] = dict(table[(m, near, p)])
                    guessed.add((m, a, p))
                    counts[3] += 1
    return table, machs, alphas, phis, counts, guessed


def coverage(run_points, guessed, machs, alphas, phi=0.0):
    """Print which Mach/alpha points DATCOM ran (x), which were interpolated
    between runs (.) and which are guesses beyond the runs (?), at one roll
    angle, so you can see where another tile would help."""
    if len(alphas) > 40:
        return
    width = max(len(f"{a:g}") for a in alphas)
    print(f"  Coverage at Phi {phi:g}: x = from DATCOM, . = between runs, ? = guess"
          f"\n  {'alpha':>6} " + " ".join(f"{a:>{width}g}" for a in alphas))
    for m in machs:
        if m == 0.0 and ADD_MACH_ZERO:
            continue
        marks = ["x" if (m, a, phi) in run_points else "?" if (m, a, phi) in guessed else "."
                 for a in alphas]
        print(f"  M{m:<5g} " + " ".join(f"{s:>{width}}" for s in marks))


def convert(runs, out_path):
    """Tile every run into ONE CSV for the sim.

    runs: [{"label": name shown in messages, "for005": path, "for003": path}],
    in the order they should be laid down (later wins where they overlap).
    Mach, alpha and roll angle lists can be different in every case and every
    run; the union of them becomes the table's grid, and the gaps are filled
    (fill_holes). Altitude is not an axis of the sim's table, so it is not one
    here either: each point simply keeps the altitude it was run at."""
    out_path = Path(out_path)
    units_seen = set()
    for run in runs:
        run["cases"], units = read_for005(run["for005"])
        run["units"] = units
        units_seen.add(units)
        blocks = read_for003(run["for003"])
        if not blocks:
            raise SystemExit(f"No data in {run['for003']}. Did the cases have a PLOT card?")
        run["matched"] = match_blocks_to_cases(blocks, run["cases"])
        print(f"{run['label']}: {len(run['cases'])} case(s), {len(blocks)} Mach block(s)")
    if len(units_seen) > 1:
        raise SystemExit(f"The runs use different DIM units {units_seen}; use one for all.")
    to_in = TO_INCHES.get(units_seen.pop(), 1.0)

    keep = pick_rocket(runs)
    for run in runs:
        run["matched"] = [t for t in run["matched"] if t[1]["geometry"] == keep]
    used = [c for run in runs for c in run["cases"] if c["geometry"] == keep]
    xcg_in, lref_in, latref_in, sref = check_refs(used, to_in)
    sref_m2 = sref * (to_in * 0.0254) ** 2 if sref is not None else None

    phi_sign = PHI_SIGN
    if phi_sign is None:
        rows = [(c["phi"], r["ALPHA"], datcom_axes(r))
                for run in runs for b, c, _ in run["matched"] for r in b["rows"]]
        phi_sign, misfit = find_phi_sign(rows)
        if phi_sign is None:
            phi_sign = 1    # every run is at PHI 0 or 180, where it makes no difference
        else:
            print(f"DATCOM PHI vs sim Phi: {'same' if phi_sign > 0 else 'opposite'} direction "
                  f"(leftover side force {misfit:.2%})")
            if misfit > 0.05:
                print("  WARNING: neither direction fits well; check the PHI runs.")

    print(f"\nTiling {len(used)} case(s) into one table:")
    table, run_points = tile(runs, xcg_in, lref_in, latref_in, phi_sign)
    if FILL_BY_SYMMETRY and len({p for _, _, p in table}) > 1:
        npanel = next((c["npanel"] for c in used if c["npanel"]), None)
        if not npanel:
            raise SystemExit("FILL_BY_SYMMETRY needs NPANEL in $FINSET1.")
        table = fill_by_symmetry(table, 360.0 / npanel)
        run_points = set(table)
    for (m, a, p), c in list(table.items()):        # close the circle
        if abs(p) < TOL:
            table[(m, a, 360.0)] = c
            run_points.add((m, a, 360.0))
    if ADD_MACH_ZERO:
        low = min(m for m, _, _ in table)
        if low > TOL:
            for (m, a, p), c in list(table.items()):
                if m == low:
                    table[(0.0, a, p)] = c
    table, machs, alphas, phis, counts, guessed = fill_holes(table)
    if counts[0] or counts[1]:
        print(f"  Filled {counts[0]} point(s) between run alphas and {counts[1]} between run Machs")
    if counts[2] or counts[3]:
        print(f"  WARNING: {counts[2] + counts[3]} point(s) lie beyond the alphas run at their "
              f"Mach ({counts[2]} copied from the nearest Mach run at that alpha, {counts[3]} "
              f"holding the nearest alpha). These are guesses: add a run there to replace them.")
    coverage(run_points, guessed, machs, alphas)

    out_path = save_csv(table, out_path)
    print(f"\n  {len(table)} rows ({len(machs)} Mach x {len(alphas)} alpha x {len(phis)} phi), "
          f"Mach {machs[0]:g}-{machs[-1]:g}, alpha {alphas[0]:g}-{alphas[-1]:g}")
    print(f"  Saved to {out_path}")
    if alphas[-1] < 179:
        print(f"  Note: alpha stops at {alphas[-1]:g} deg; above that the sim extrapolates "
              f"in a straight line.")
    spin = [(m, table[(m, 0.0, 0.0)]["CMx"]) for m in machs if (m, 0.0, 0.0) in table]
    if any(abs(v) > 1e-9 for _, v in spin):
        print("  Roll moment at zero alpha (from fin cant), CMx by Mach:")
        print("    " + ", ".join(f"M{m:g}: {v:+.4g}" for m, v in spin))
    sim_check(out_path, sref_m2)

    print("\nFor the rocket file:")
    if sref_m2 is not None:
        print(f'    reference_area=scalar({sref_m2:.10g}, "m**2"),  # DATCOM SREF')
    print(f'    reference_length=scalar({L_ref * 0.0254:.6g}, "m"),')
    print(f'    reference_point=vector(({-REFPOINT * 0.0254:.6g}, 0.0, 0.0), "m"),  '
          f'# REFPOINT: {REFPOINT:g} in aft of nose')
    print('    frame="missile",')
    return out_path

# =========================================================================== #
# Part 3: finding the file and DATCOM, and running it
# =========================================================================== #

def ask(prompt, default=""):
    """Ask a question; Enter (or no keyboard) takes the default."""
    try:
        s = input(f"{prompt} [{default}]: " if default else f"{prompt}: ").strip()
    except EOFError:
        s = ""
    return s or default


def find_file(name):
    """A file name typed at the prompt: as given, then with .dat added; next to
    this script first (VS Code's Run button starts in another folder), then the
    current folder. Returns a Path or None."""
    name = name.strip().strip('"').strip("'")
    if not name:
        return None
    names = [name] + ([name + ".dat"] if not os.path.splitext(name)[1] else [])
    for nm in names:
        p = Path(nm)
        for t in ([p] if p.is_absolute() else [HERE / p, Path.cwd() / p]):
            if t.is_file():
                return t.resolve()
    return None


def is_datcom_input(path):
    """True for a DATCOM input file (namelists like $FLTCON or $FINSET1)."""
    try:
        text = Path(path).read_text(errors="replace")[:400000]
    except OSError:
        return False
    return bool(re.search(r"\$\s*(FLTCON|REFQ|AXIBOD|ELLBOD|FINSET\d|DEFLCT)\b", text, re.I))


def ask_input_files(argv):
    """The DATCOM input files to run and tile together: from the command line,
    or asked for one at a time until 'done'. The first prompt offers
    for005.dat; after that Enter means done."""
    def check(path, name):
        if not path:
            print(f"  Could not find '{name}' in {HERE}. Check the name and try again.")
        elif not is_datcom_input(path):
            print(f"  {path.name} doesn't look like a DATCOM input file (no $FLTCON, "
                  f"$AXIBOD, $FINSET... namelists).")
        else:
            return True
        return False

    if argv:
        paths = [find_file(a) for a in argv]
        if not all(check(p, a) for p, a in zip(paths, argv)):
            raise SystemExit("Nothing was run.")
        return paths
    dats = sorted(p.name for p in HERE.glob("*.dat")
                  if not re.match(r"for0\d\d", p.name, re.I) or p.name.lower() == "for005.dat")
    if dats:
        print(f"DATCOM input files in {HERE}:\n  " + ", ".join(dats[:30]))
    print("Give one input file at a time; all of them are run and tiled into ONE CSV.\n"
          "Where two files cover the same point, the one given later wins.")
    paths = []
    while True:
        first = not paths
        s = ask("\nFirst input file" if first else "Next input file, or 'done' to finish",
                DEFAULT_INPUT if first else "done")
        if s.lower() in DONE_WORDS:
            if paths:
                return paths
            raise SystemExit("Stopped.")
        path = find_file(s)
        if check(path, s):
            if path in paths:
                print(f"  {path.name} is already in the list.")
            else:
                paths.append(path)


def find_datcom_exe(start_dir):
    """DATCOM_EXE if set, else a program with 'datcom' or 'misdat' in its name
    next to the input file or this script, else ask."""
    def usable(p):
        return p.is_file() and (os.name == "nt" or os.access(p, os.X_OK))

    if DATCOM_EXE:
        p = Path(DATCOM_EXE)
        if usable(p):
            return p.resolve()
        print(f"  DATCOM_EXE = {DATCOM_EXE} was not found.")
    for folder in dict.fromkeys([Path(start_dir), HERE]):
        for p in sorted(folder.iterdir()):
            low = p.name.lower()
            if ("datcom" in low or "misdat" in low) and usable(p) and \
                    (low.endswith(".exe") or (os.name != "nt" and "." not in p.name)):
                return p.resolve()
    while True:
        t = ask("Where is the DATCOM program (e.g. D:\\datcom\\datcom.exe; Enter to stop)")
        if not t:
            raise SystemExit("No DATCOM program, so nothing was run.")
        p = Path(t.strip('"').strip("'"))
        if p.is_dir():
            p = p / "datcom.exe"
        if usable(p):
            return p.resolve()
        print(f"  {p} is not a program file.")


def _retry(action, path, tries=10, wait=0.5):
    """Do a file action, retrying for a few seconds if Windows says the file is
    in use. If it stays locked, stop with a message saying what usually holds
    it, instead of a traceback."""
    for i in range(tries):
        try:
            return action()
        except PermissionError:
            if i == tries - 1:
                raise SystemExit(
                    f"\n{path} is in use by another program, so it can't be written.\n"
                    f"Usually that is DATCOM itself, still running or stuck from an earlier "
                    f"run (for example one started by the fin comparison script, which uses "
                    f"the same for005.dat).\nClose that script if it's running, end any "
                    f"datcom.exe in Task Manager, then run this again.") from None
            time.sleep(wait)
    return None


def _tail(path, n=6):
    """The last few non-blank lines of a text file, indented, for messages."""
    try:
        lines = [l.rstrip() for l in Path(path).read_text(errors="replace").splitlines() if l.strip()]
    except OSError:
        return ""
    return "\n".join("    | " + l[:150] for l in lines[-n:])


def _flagged_lines(work):
    """Lines in DATCOM's main output that say ERROR, WARNING and so on, for the
    screen only. The error-code legend DATCOM prints at the top of every run
    ("A - UNKNOWN VARIABLE NAME" ...) is not an error and is skipped."""
    out6 = next((p for p in work.iterdir() if p.name.lower() in ("for006.dat", "for006", "fort.6")), None)
    if not out6:
        return "DATCOM wrote no main output either, so it probably did not read the input."
    legend = re.compile(r"^\s*(CONERR\b|ERROR CODES\b|[A-F] - )")
    found = [l.strip() for l in out6.read_text(errors="replace").splitlines()
             if re.search(r"\b(ERROR|WARNING|ILLEGAL|UNKNOWN|INVALID|NOT AVAILABLE|NOT VALID|"
                          r"IGNORED)\b", l, re.I) and not legend.match(l)]
    found = list(dict.fromkeys(found))[:6]
    if not found:
        return f"{out6.name} flags no errors or warnings."
    return f"{out6.name} says:\n" + "\n".join("    | " + l[:150] for l in found)


PLOT_NAMES = ("for003.dat", "for003", "fort.3")


def run_datcom(exe, input_text, save_dir):
    """Run DATCOM once, in its own folder, on input_text, and save the input
    used, the plot file and DATCOM's screen output into save_dir.

    DATCOM only reads the file called for005.dat next to it. If that file
    already holds exactly input_text (you picked your own for005.dat and every
    case has its PLOT card), it is not touched at all. Otherwise it is replaced
    for the run and put back exactly as it was afterwards (also if the run
    fails or you press Ctrl+C). An old for003.dat is removed first so
    it can never be mistaken for this run's output.
    Returns the saved plot file's path; stops with the reason if there is none."""
    work = exe.parent
    target = work / "for005.dat"
    save_dir.mkdir(parents=True, exist_ok=True)
    original = target.read_bytes() if target.is_file() else None
    same = original is not None and \
        original.decode(errors="replace").replace("\r\n", "\n") == input_text.replace("\r\n", "\n")
    if same:
        original = None             # nothing to replace, nothing to put back
        print("for005.dat already holds this input; running it untouched.")
    elif original is not None:
        (save_dir / "for005_before_this_run.dat").write_bytes(original)
    for p in work.iterdir():
        if p.name.lower() in PLOT_NAMES:
            _retry(p.unlink, p)
    log = save_dir / "datcom_console.txt"
    t0 = time.time()
    try:
        # native line endings, as when the file is written by hand
        if not same:
            _retry(lambda: target.write_text(input_text), target)
        print(f"Running {exe.name} in {work} (up to {TIMEOUT_S} s)...")
        for attempt in ("captured", "plain"):
            try:
                if attempt == "captured":
                    with open(log, "w") as lf:
                        # Enter presses on its keyboard, in case it waits at the end
                        subprocess.run([str(exe)], cwd=work, input="\n" * 40, text=True,
                                       stdout=lf, stderr=subprocess.STDOUT, timeout=TIMEOUT_S)
                else:
                    print("No plot file yet; running it once more the plain way, as by hand "
                          "(press Enter if it waits)...")
                    sys.stdout.flush()
                    subprocess.run([str(exe)], cwd=work, timeout=TIMEOUT_S)
            except subprocess.TimeoutExpired:
                raise SystemExit(f"DATCOM did not finish within {TIMEOUT_S} s (raise TIMEOUT_S). "
                                 f"Last lines it printed:\n{_tail(log)}") from None
            except OSError as e:
                raise SystemExit(f"Could not start {exe}: {e.strerror or e}") from None
            plot = next((p for p in work.iterdir() if p.name.lower() in PLOT_NAMES
                         and p.stat().st_mtime >= t0 - 3 and p.stat().st_size > 0), None)
            if plot:
                break
        shutil.copyfile(target, save_dir / "for005.dat")
        if not plot:
            raise SystemExit(f"DATCOM ran but wrote no plot file (for003.dat).\n"
                             f"{_flagged_lines(work)}\nLast lines it printed:\n{_tail(log)}")
        saved = save_dir / "for003.dat"
        shutil.copyfile(plot, saved)
        print(f"DATCOM finished in {time.time() - t0:.1f} s.")
        return saved
    finally:
        try:
            if original is not None:
                target.write_bytes(original)
            elif not same and target.is_file():
                target.unlink()
        except OSError as e:
            print(f"WARNING: could not put the original for005.dat back ({e.strerror or e}). "
                  f"A copy is in {save_dir / 'for005_before_this_run.dat'}.")


# =========================================================================== #

def main(argv=None):
    """Ask for the input files, run DATCOM on each, tile them into one CSV."""
    argv = sys.argv[1:] if argv is None else argv
    paths = ask_input_files(argv)
    name = ask("\nCSV file name", "DATCOM" if paths[0].stem.lower() == "for005" else paths[0].stem)
    out_name = name if name.lower().endswith(".csv") else name + ".csv"
    exe = find_datcom_exe(paths[0].parent)

    runs = []
    for i, path in enumerate(paths, 1):
        print(f"\n[{i}/{len(paths)}] {path.name}")
        cases = split_cases(path.read_text(errors="replace"))
        if not cases:
            raise SystemExit(f"{path.name} has no cases in it.")
        if MAKE_GRID:
            run_text, _, about = build_grid(cases[0])
            print(f"  Full grid from its first case: {about}.")
        else:
            run_text = as_written(path.read_text(errors="replace"))
            print(f"  {len(cases)} case(s), run as written.")
        save_dir = HERE / "datcom_runs" / f"{i}_{path.stem}"
        plot = run_datcom(exe, run_text, save_dir)
        runs.append(dict(label=path.name, for005=save_dir / "for005.dat", for003=plot))

    print()
    out = convert(runs, HERE / out_name)
    print(f"\nDone. One CSV: {out}")
    print(f"Each run's input and plot file are in {HERE / 'datcom_runs'}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")

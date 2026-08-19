#!/usr/bin/env python3
"""
Parse Missile DATCOM for006.dat into TWO Excel-ready text layouts:

1) for006_by_altitude.txt
   Side-by-side altitude blocks for visual comparison in Excel.

2) for006_long.txt
   One normal "tidy" table with one row per Mach/AoA/altitude combination.
   Better for PivotTables, filtering, plotting, XLOOKUP, Python/MATLAB, etc.

Usage:
    py parse_for006_by_altitude.py for006.dat
"""

from pathlib import Path
import argparse
import re
import sys

NUM = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?"


def fnum(s):
    return float(s.replace("D", "E").replace("d", "e"))


def parse_for006(filename):
    lines = Path(filename).read_text(errors="replace").splitlines()
    rows = []

    for i, line in enumerate(lines):
        if "STATIC AERODYNAMICS FOR BODY-FIN SET" not in line:
            continue

        page_end = min(i + 90, len(lines))
        mach = None
        altitude = None
        body_header = None

        for j in range(i, page_end):
            m = re.search(r"MACH NO\s*=\s*(" + NUM + r")", lines[j])
            if m:
                mach = fnum(m.group(1))

            a = re.search(r"ALTITUDE\s*=\s*(" + NUM + r")\s*FT", lines[j])
            if a:
                altitude = fnum(a.group(1))

            if (
                "ALPHA" in lines[j]
                and "CN" in lines[j]
                and "CM" in lines[j]
                and "CA" in lines[j]
                and "CY" in lines[j]
                and "CLN" in lines[j]
                and "CLL" in lines[j]
            ):
                body_header = j
                break

        # Skip derivative-only pages.
        if mach is None or altitude is None or body_header is None:
            continue

        body = {}
        j = body_header + 1

        # Main body-axis table:
        # ALPHA CN CM CA CY CLN CLL
        while j < page_end:
            txt = lines[j].strip()

            if "ALPHA" in txt and "CL/CD" in txt and re.search(r"\bCD\b", txt):
                break

            vals = re.findall(NUM, txt)
            if len(vals) == 7:
                try:
                    alpha, cn, cm, ca, cy, cln, cll = map(fnum, vals)
                    body[round(alpha, 8)] = {
                        "ALPHA": alpha,
                        "CN": cn,
                        "CM": cm,
                        "CA": ca,
                        "CY": cy,
                        "CLN": cln,
                        "CLL": cll,
                    }
                except ValueError:
                    pass
            j += 1

        # Wind-axis table:
        # ALPHA CL CD CL/CD X-C.P.
        wind_header = None
        for k in range(j, page_end):
            txt = lines[k]
            if "ALPHA" in txt and "CL/CD" in txt and re.search(r"\bCD\b", txt):
                wind_header = k
                break

        if wind_header is None:
            continue

        wind = {}
        k = wind_header + 1
        while k < page_end:
            txt = lines[k].strip()

            if "X-C.P. MEAS." in txt or "***** THE USAF" in txt:
                break

            vals = re.findall(NUM, txt)
            if len(vals) == 5:
                try:
                    alpha, cl, cd, clcd, xcp = map(fnum, vals)
                    wind[round(alpha, 8)] = {
                        "CL": cl,
                        "CD": cd,
                        "CL/CD": clcd,
                        "XCP": xcp,
                    }
                except ValueError:
                    pass
            k += 1

        for alpha_key in sorted(set(body) & set(wind)):
            rec = {
                "MACH": mach,
                "ALTITUDE_FT": altitude,
            }
            rec.update(body[alpha_key])
            rec.update(wind[alpha_key])
            rows.append(rec)

    # Remove accidental duplicate pages if DATCOM repeats anything.
    unique = {}
    for r in rows:
        key = (r["MACH"], r["ALPHA"], r["ALTITUDE_FT"])
        unique[key] = r

    rows = list(unique.values())
    rows.sort(key=lambda r: (r["ALTITUDE_FT"], r["MACH"], r["ALPHA"]))
    return rows


COLUMNS = [
    "MACH", "ALPHA",
    "CN", "CM", "CA", "CY", "CLN", "CLL",
    "CL", "CD", "CL/CD", "XCP"
]


def fmt_row(r):
    return [
        f'{r["MACH"]:.4f}',
        f'{r["ALPHA"]:.4f}',
        f'{r["CN"]:.6f}',
        f'{r["CM"]:.6f}',
        f'{r["CA"]:.6f}',
        f'{r["CY"]:.6f}',
        f'{r["CLN"]:.6f}',
        f'{r["CLL"]:.6f}',
        f'{r["CL"]:.6f}',
        f'{r["CD"]:.6f}',
        f'{r["CL/CD"]:.6f}',
        f'{r["XCP"]:.6f}',
    ]


def write_long(rows, output):
    cols = ["MACH", "ALPHA", "ALTITUDE_FT"] + COLUMNS[2:]

    with open(output, "w", newline="") as f:
        f.write("\t".join(cols) + "\n")
        for r in sorted(rows, key=lambda x: (x["MACH"], x["ALPHA"], x["ALTITUDE_FT"])):
            vals = [
                f'{r["MACH"]:.4f}',
                f'{r["ALPHA"]:.4f}',
                f'{r["ALTITUDE_FT"]:.1f}',
            ] + fmt_row(r)[2:]
            f.write("\t".join(vals) + "\n")


def write_side_by_side(rows, output):
    altitudes = sorted({r["ALTITUDE_FT"] for r in rows})

    grouped = {}
    for alt in altitudes:
        alt_rows = [r for r in rows if r["ALTITUDE_FT"] == alt]
        alt_rows.sort(key=lambda r: (r["MACH"], r["ALPHA"]))
        grouped[alt] = alt_rows

    max_rows = max(len(v) for v in grouped.values())
    block_width = len(COLUMNS)

    with open(output, "w", newline="") as f:
        # Row 1: altitude titles, one title per horizontal table.
        title_cells = []
        for n, alt in enumerate(altitudes):
            title_cells += [f"ALTITUDE = {alt:g} FT"] + [""] * (block_width - 1)
            if n != len(altitudes) - 1:
                title_cells += [""]  # spacer column
        f.write("\t".join(title_cells) + "\n")

        # Row 2: repeated column headers for every altitude block.
        header_cells = []
        for n, alt in enumerate(altitudes):
            header_cells += COLUMNS
            if n != len(altitudes) - 1:
                header_cells += [""]
        f.write("\t".join(header_cells) + "\n")

        # Data rows: identical Mach/AoA ordering in every altitude block.
        for idx in range(max_rows):
            cells = []
            for n, alt in enumerate(altitudes):
                block = grouped[alt]
                if idx < len(block):
                    cells += fmt_row(block[idx])
                else:
                    cells += [""] * block_width
                if n != len(altitudes) - 1:
                    cells += [""]
            f.write("\t".join(cells) + "\n")


def main():
    ap = argparse.ArgumentParser(
        description="Parse Missile DATCOM for006.dat into Excel-ready altitude tables."
    )
    ap.add_argument("input", help="Path to for006.dat")
    args = ap.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        sys.exit(f"ERROR: File not found: {input_path}")

    rows = parse_for006(input_path)
    if not rows:
        sys.exit("ERROR: No final body-fin static aerodynamic tables found.")

    wide_path = input_path.with_name("for006_by_altitude.txt")
    long_path = input_path.with_name("for006_long.txt")

    write_side_by_side(rows, wide_path)
    write_long(rows, long_path)

    machs = sorted({r["MACH"] for r in rows})
    alphas = sorted({r["ALPHA"] for r in rows})
    alts = sorted({r["ALTITUDE_FT"] for r in rows})

    print(f"Parsed {len(rows)} unique Mach/AoA/altitude points")
    print(f"Mach points: {len(machs)}")
    print(f"AoA points: {len(alphas)}")
    print(f"Altitude points: {len(alts)}")
    print()
    print(f"Side-by-side Excel layout: {wide_path.name}")
    print(f"Long/tidy data layout:      {long_path.name}")


if __name__ == "__main__":
    main()

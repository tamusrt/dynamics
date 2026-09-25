"""
Missile DATCOM 1997 - for006.dat parser
Parses all output tables into a unified DataFrame keyed by
(Case, Mach, Altitude, Beta, Alpha).

All reference quantities (LREF, XCG, SREF) are read directly from the
for006.dat header lines — no hardcoded vehicle constants.
"""

import re
import math
import pandas as pd
from collections import defaultdict


def _to_float(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return math.nan


def _is_numeric_or_nan(s):
    if s == "NaN":
        return True
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


def _parse_data_rows(lines, start_idx, n_headers):
    i = start_idx
    while i < len(lines) and not lines[i].strip():
        i += 1
    rows = []
    while i < len(lines):
        parts = lines[i].strip().split()
        if not parts:
            break
        if not _is_numeric_or_nan(parts[0]):
            break
        if len(parts) == n_headers:
            rows.append([_to_float(p) for p in parts])
        i += 1
    return rows, i


_HEADER_RENAME = {
    "CL/CD": "CL_CD",
    "X-C.P.": "XCP",
}

_TABLE_SIGNATURES = [
    frozenset(["ALPHA", "CN", "CM", "CA", "CY", "CLN", "CLL"]),
    frozenset(["ALPHA", "CL", "CD", "CL/CD", "X-C.P."]),
    frozenset(["ALPHA", "CNA", "CMA", "CYB", "CLNB", "CLLB"]),
    frozenset(["ALPHA", "CNQ", "CMQ", "CAQ", "CNAD", "CMAD"]),
    frozenset(["ALPHA", "CYR", "CLNR", "CLLR", "CYP", "CLNP", "CLLP"]),
]


def _parse_ref_quantities(lines):
    refs = {"lref": None, "xcg": None, "sref": None}
    for line in lines:
        if refs["lref"] is None:
            m = re.search(r'REF LENGTH\s*=\s*([0-9Ee\.\+\-]+)', line)
            if m:
                refs["lref"] = float(m.group(1))
        if refs["xcg"] is None:
            m = re.search(r'MOMENT CENTER\s*=\s*([0-9Ee\.\+\-]+)', line)
            if m:
                refs["xcg"] = float(m.group(1))
        if refs["sref"] is None:
            m = re.search(r'REF AREA\s*=\s*([0-9Ee\.\+\-]+)', line)
            if m:
                refs["sref"] = float(m.group(1))
        if all(v is not None for v in refs.values()):
            break
    return refs


def parse_for006(filepath):
    """
    Parse a Missile DATCOM for006.dat output file.
    Returns (df, refs) where:
      df   : DataFrame keyed by (Case, Mach, Altitude_ft, Beta, ALPHA)
      refs : dict with lref, xcg, sref read from file headers
    """
    with open(filepath, "r") as f:
        lines = f.readlines()

    refs = _parse_ref_quantities(lines)

    data         = defaultdict(dict)
    current_mach = None
    current_alt  = None
    current_beta = 0.0   # default — DATCOM prints 0.0 if not set
    current_case = None

    i = 0
    while i < len(lines):
        raw      = lines[i]
        stripped = raw.strip()

        m = re.search(r'CASE\s+(\d+)', raw)
        if m:
            current_case = int(m.group(1))

        m = re.search(r'MACH NO\s*=\s*([0-9Ee\.\+\-]+)', raw)
        if m:
            current_mach = float(m.group(1))

        m = re.search(r'ALTITUDE\s*=\s*([0-9Ee\.\+\-]+)', raw)
        if m:
            current_alt = float(m.group(1))

        # DATCOM prints "SIDESLIP =       5.00 DEG"
        m = re.search(r'SIDESLIP\s*=\s*([0-9Ee\.\+\-]+)', raw)
        if m:
            current_beta = float(m.group(1))

        tokens = stripped.split()
        if tokens and tokens[0] == "ALPHA" and len(tokens) >= 2:
            token_set   = frozenset(tokens)
            matched_sig = None
            for sig in _TABLE_SIGNATURES:
                if sig == token_set or sig.issubset(token_set):
                    matched_sig = sig
                    break

            if matched_sig is not None:
                headers      = [_HEADER_RENAME.get(t, t) for t in tokens]
                rows, next_i = _parse_data_rows(lines, i + 1, len(headers))

                for row_vals in rows:
                    alpha = row_vals[0]
                    if math.isnan(alpha):
                        continue
                    key      = (current_case, current_mach, current_alt,
                                current_beta, alpha)
                    row_dict = dict(zip(headers[1:], row_vals[1:]))
                    data[key].update(row_dict)

                i = next_i
                continue

        i += 1

    if not data:
        return pd.DataFrame(), refs

    rows = []
    for (case, mach, alt, beta, alpha), coeffs in data.items():
        row = {"Case": case, "Mach": mach, "Altitude_ft": alt,
               "Beta": beta, "ALPHA": alpha}
        row.update(coeffs)
        rows.append(row)

    df = pd.DataFrame(rows)

    coeff_cols = [c for c in df.columns
                  if c not in ["Case", "Mach", "Altitude_ft", "Beta", "ALPHA"]]
    df = df.dropna(subset=coeff_cols, how="all").reset_index(drop=True)
    df = df.sort_values(["Case", "Mach", "Beta", "ALPHA"]).reset_index(drop=True)

    meta_cols  = ["Case", "Mach", "Altitude_ft", "Beta", "ALPHA"]
    coeff_cols = sorted([c for c in df.columns if c not in meta_cols])
    df = df[meta_cols + coeff_cols]

    df.attrs["lref"] = refs["lref"]
    df.attrs["xcg"]  = refs["xcg"]
    df.attrs["sref"] = refs["sref"]

    return df, refs


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "for006.dat"
    df, refs = parse_for006(path)
    print(f"Reference quantities:  LREF={refs['lref']} in  "
          f"XCG={refs['xcg']} in  SREF={refs['sref']} in²")
    print(f"Parsed {len(df)} rows, {len(df.columns)} columns")
    print(f"Mach values: {sorted(df['Mach'].unique())}")
    print(f"Beta values: {sorted(df['Beta'].unique())}")
    print(f"Cases:       {sorted(df['Case'].unique())}")
    print()
    print(df.to_string(index=False))

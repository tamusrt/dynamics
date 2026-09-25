"""Compare DATCOM coefficient sweeps with RASAero Run Test output.

RASAero Run Test text files contain one Mach block per group: a drag row
(5, 12, or 14 columns depending on Mach regime) followed by five aero rows. The final
two six-column rows are CL, CD, CN, CA for power-off and power-on.

DATCOM cases can be any number (not just two): either point --cases at one
for006.dat per case, or drop a single combined for006.dat (with N sequential
cases) beside this script / pass it via --cases. Each case's legend label is
either supplied with --labels, or you'll be prompted for it interactively.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import matplotlib.pyplot as plt
except ImportError:
    plt = None

from datcom_parser import parse_for006

# Color palette cycled across however many DATCOM cases are plotted.
CASE_COLORS = [
    "#2474b5", "#e07a26", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]


def _numeric_row(line: str) -> list[float] | None:
    parts = line.split()
    if len(parts) < 2:
        return None
    try:
        return [float(value.replace("D", "E")) for value in parts]
    except ValueError:
        return None


def parse_rasaero_run_test(path: str | Path, alpha: float | None = None) -> pd.DataFrame:
    """Parse RASAero's plain-text Tools/Run Test aerodynamic output."""
    numeric = []
    for line in Path(path).read_text(errors="replace").splitlines():
        row = _numeric_row(line)
        if row is not None:
            numeric.append(row)

    records = []
    i = 0
    while i < len(numeric):
        row = numeric[i]
        if len(row) not in (5, 12, 14):
            i += 1
            continue
        mach = row[0]
        block = [row]
        j = i + 1
        while j < len(numeric) and len(block) < 6:
            candidate = numeric[j]
            if abs(candidate[0] - mach) > 1e-6:
                break
            block.append(candidate)
            j += 1
        if len(block) == 6 and [len(r) for r in block[1:]] == [3, 4, 4, 6, 6]:
            off, on = block[4], block[5]
            records.append({
                "Mach": mach, "Alpha": off[1],
                "CD_total_power_off": off[3], "CL_power_off": off[2],
                "CN_power_off": off[4], "CA_power_off": off[5],
                "CD_total_power_on": on[3], "CL_power_on": on[2],
                "CN_power_on": on[4], "CA_power_on": on[5],
                "CD_zero_alpha_power_off": row[2],
                "CD_zero_alpha_power_on": row[3],
                "Reynolds": row[-1], "CN_0_to_4": block[1][1],
                "CP_0_to_4": block[1][2], "CP_total": block[3][3],
            })
            i = j
        else:
            i += 1

    df = pd.DataFrame.from_records(records)
    if df.empty:
        raise ValueError(
            f"No complete RASAero Run Test Mach blocks parsed from {path}. "
            "Expected a 5-, 12-, or 14-value line followed by 3, 4, 4, 6, and 6-value lines."
        )
    if alpha is not None:
        df = df[np.isclose(df["Alpha"], alpha, atol=0.05)].copy()
    return df.sort_values("Mach").reset_index(drop=True)


def _datcom_alpha_zero(path: str | Path) -> pd.DataFrame:
    df, _ = parse_for006(str(path))
    if df.empty:
        raise ValueError(f"No DATCOM coefficient rows parsed from {path}")
    if "Beta" in df:
        df = df[df["Beta"].abs() < 0.05]
    alpha_values = sorted(df["ALPHA"].dropna().unique())
    if not alpha_values:
        raise ValueError(f"No alpha values in DATCOM output {path}")
    alpha0 = min(alpha_values, key=abs)
    return df[np.isclose(df["ALPHA"], alpha0, atol=0.05)].sort_values("Mach")


def _datcom_all_cases_alpha_zero(path: str | Path) -> list[pd.DataFrame]:
    """Read every sequential DATCOM case out of one combined for006.dat file."""
    frame = _datcom_alpha_zero(path)
    cases = sorted(frame["Case"].dropna().unique())
    if not cases:
        raise ValueError(f"No DATCOM cases found in {path}")
    return [frame[frame["Case"] == case].copy() for case in cases]


def gather_cases(args, default_data_path) -> tuple[list[pd.DataFrame], list[str]]:
    """Return (frames, default_labels) for however many DATCOM cases were given."""
    if args.cases:
        paths = [Path(p) for p in args.cases]
        missing = [p for p in paths if not p.is_file()]
        if missing:
            raise FileNotFoundError(
                "DATCOM case file(s) not found: " + ", ".join(str(p) for p in missing)
            )
        frames = [_datcom_alpha_zero(p) for p in paths]
        default_labels = [p.stem for p in paths]
    else:
        combined_path = default_data_path("for006.dat")
        if not combined_path.is_file():
            raise FileNotFoundError(f"Missing regular DATCOM output: {combined_path}")
        frames = _datcom_all_cases_alpha_zero(combined_path)
        default_labels = [f"Case {i + 1}" for i in range(len(frames))]
    return frames, default_labels


def prompt_case_label(index: int, default: str) -> str:
    """Ask the user what to call this case's legend entry; Enter keeps the default."""
    try:
        response = input(f"Case {index} (Default Name: {default}): ").strip()
    except EOFError:
        response = ""
    return response or default


def resolve_labels(args, default_labels: list[str]) -> list[str]:
    n = len(default_labels)
    if args.labels:
        if len(args.labels) != n:
            raise ValueError(
                f"--labels expects {n} value(s) for {n} DATCOM case(s), got {len(args.labels)}."
            )
        return list(args.labels)
    if args.no_prompt:
        return default_labels
    print(f"Found {n} DATCOM case(s). Type a name and press Enter, or just press Enter to keep the default.")
    return [prompt_case_label(i, default) for i, default in enumerate(default_labels, start=1)]


def _save_svg(output: Path, cases: list[tuple[pd.DataFrame, str, str]],
              rasaero: pd.DataFrame, alpha: float) -> None:
    """Dependency-light SVG chart fallback for environments without matplotlib."""
    width, height = 1280, 560
    panels = [(55, "Drag coefficient", "CD"), (665, "Lift coefficient", "CL")]
    off_color, on_color = "#303030", "#858585"

    drag_traces = [(f["Mach"], f.get("CD"), f"{label} CD", color) for f, label, color in cases]
    drag_traces += [
        (rasaero["Mach"], rasaero["CD_total_power_off"], "RASAero power-off CD", off_color),
        (rasaero["Mach"], rasaero["CD_total_power_on"], "RASAero power-on CD", on_color),
    ]
    lift_traces = [
        (f["Mach"], f.get("CL", f.get("CN")), f"{label} lift", color) for f, label, color in cases
    ]
    lift_traces += [
        (rasaero["Mach"], rasaero["CL_power_off"], "RASAero power-off CL", off_color),
        (rasaero["Mach"], rasaero["CL_power_on"], "RASAero power-on CL", on_color),
    ]
    panel_series_list = [drag_traces, lift_traces]

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
           '<rect width="100%" height="100%" fill="white"/>',
           f'<text x="640" y="28" text-anchor="middle" font-family="Arial" font-size="19">RASAero Run Test alpha={alpha:g}° vs DATCOM alpha=0°</text>']
    for panel_i, (x0, title, ylabel) in enumerate(panels):
        x1, y0, y1 = x0 + 535, 70, 440
        panel_series = panel_series_list[panel_i]
        values = []
        for xs, ys, _, _color in panel_series:
            if ys is not None:
                values.extend(float(y) for xx, y in zip(xs, ys) if pd.notna(y) and 0.70 <= float(xx) <= 1.50)
        lo, hi = (min(values), max(values)) if values else (0.0, 1.0)
        pad = max((hi - lo) * 0.08, 0.01)
        lo, hi = lo - pad, hi + pad
        def xp(m): return x0 + (float(m) - 0.70) / 0.80 * (x1 - x0)
        def yp(v): return y1 - (float(v) - lo) / (hi - lo) * (y1 - y0)
        svg.append(f'<text x="{(x0+x1)/2}" y="55" text-anchor="middle" font-family="Arial" font-size="16">{title}</text>')
        svg.append(f'<path d="M{x0},{y0} V{y1} H{x1}" fill="none" stroke="#333"/>')
        for tick in range(5):
            mach = 0.70 + tick * 0.20
            tx = xp(mach)
            svg.append(f'<path d="M{tx},{y1} v5" stroke="#333"/><text x="{tx}" y="{y1+22}" text-anchor="middle" font-family="Arial" font-size="11">{mach:.1f}</text>')
        svg.append(f'<text x="{(x0+x1)/2}" y="{y1+47}" text-anchor="middle" font-family="Arial" font-size="13">Mach</text>')
        svg.append(f'<text x="{x0-34}" y="{(y0+y1)/2}" transform="rotate(-90 {x0-34} {(y0+y1)/2})" text-anchor="middle" font-family="Arial" font-size="13">{ylabel}</text>')
        for trace_i, (xs, ys, label, color) in enumerate(panel_series):
            if ys is None: continue
            points = [(xp(xv), yp(yv)) for xv, yv in zip(xs, ys) if pd.notna(yv) and 0.70 <= float(xv) <= 1.50]
            if len(points) < 1: continue
            dash = ' stroke-dasharray="7 5"' if "power-on" in label else ""
            coord = " ".join(f"{px:.1f},{py:.1f}" for px, py in points)
            svg.append(f'<polyline points="{coord}" fill="none" stroke="{color}" stroke-width="2"{dash}/>')
            lx, ly = x0 + 12, y0 + 18 + trace_i * 18
            svg.append(f'<line x1="{lx}" y1="{ly}" x2="{lx+22}" y2="{ly}" stroke="{color}" stroke-width="2"{dash}/>')
            svg.append(f'<text x="{lx+28}" y="{ly+4}" font-family="Arial" font-size="11">{label}</text>')
    svg.append("</svg>")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(svg), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--cases", nargs="+",
        help="One for006.dat per DATCOM case (any number). Omit to read every "
             "sequential case out of a single for006.dat beside this script.",
    )
    parser.add_argument(
        "--labels", nargs="+",
        help="Legend label for each case, in the same order as --cases (or the "
             "order cases appear in the combined for006.dat). Skips the prompt.",
    )
    parser.add_argument(
        "--no-prompt", action="store_true",
        help="Don't ask interactively for labels; use file-derived/'Case N' defaults.",
    )
    parser.add_argument("rasaero_run_test", nargs="?", help="RASAero Run Test .txt file")
    parser.add_argument("--alpha", type=float, default=0.0, help="RASAero test alpha in degrees")
    parser.add_argument("--output", help="PNG output path")
    parser.add_argument("--csv", help="parsed RASAero CSV path")
    parser.add_argument("--show", action="store_true", help="show plot window after saving")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent

    def default_data_path(filename: str, fallback: Path | None = None) -> Path:
        beside_script = here / filename
        if beside_script.exists():
            return beside_script
        if fallback is not None and fallback.exists():
            return fallback
        return beside_script

    rasaero_path = Path(args.rasaero_run_test) if args.rasaero_run_test else default_data_path("zeroalphatest.txt", here.parent / "zeroalphatest.txt")

    frames, default_labels = gather_cases(args, default_data_path)
    labels = resolve_labels(args, default_labels)
    cases = list(zip(frames, labels, [CASE_COLORS[i % len(CASE_COLORS)] for i in range(len(frames))]))

    if not rasaero_path.is_file():
        raise FileNotFoundError(f"Missing RASAero Run Test text file: {rasaero_path}")
    rasaero = parse_rasaero_run_test(rasaero_path, alpha=args.alpha)
    if rasaero.empty:
        raise ValueError(f"No RASAero rows at alpha={args.alpha:g}° in {rasaero_path}")

    if plt is None:
        output = Path(args.output) if args.output else here / "comparison_lift_drag.svg"
        if output.suffix.lower() != ".svg":
            output = output.with_suffix(".svg")
        _save_svg(output, cases, rasaero, args.alpha)
        csv_path = Path(args.csv) if args.csv else output.with_suffix(".csv")
        rasaero.to_csv(csv_path, index=False)
        print(f"RASAero parsed rows at alpha={args.alpha:g}°: {len(rasaero)}")
        print(f"RASAero Mach range: {rasaero['Mach'].min():.2f} to {rasaero['Mach'].max():.2f}")
        for frame, label, _color in cases:
            print(f"DATCOM alpha-zero rows: {label}={len(frame)}")
        print(f"Saved plot: {output}")
        print(f"Saved parsed RASAero rows: {csv_path}")
        return

    plt.rcParams.update({
        "figure.dpi": 140, "axes.grid": True, "grid.alpha": 0.28,
        "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
    })
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2))
    for frame, label, color in cases:
        if "CD" in frame:
            axes[0].plot(frame["Mach"], frame["CD"], "o-", ms=3, lw=1.5,
                         label=f"{label} CD", color=color)
        lift_col = "CL" if "CL" in frame else "CN"
        if lift_col in frame:
            axes[1].plot(frame["Mach"], frame[lift_col], "o-", ms=3, lw=1.5,
                         label=f"{label} {lift_col}", color=color)

    axes[0].plot(rasaero["Mach"], rasaero["CD_total_power_off"], "-",
                 label="RASAero power-off CD", color="#303030")
    axes[0].plot(rasaero["Mach"], rasaero["CD_total_power_on"], "--",
                 label="RASAero power-on CD", color="#777777")
    axes[1].plot(rasaero["Mach"], rasaero["CL_power_off"], "-",
                 label="RASAero power-off CL", color="#303030")
    axes[1].plot(rasaero["Mach"], rasaero["CL_power_on"], "--",
                 label="RASAero power-on CL", color="#777777")
    axes[0].set(title="Drag coefficient", xlabel="Mach", ylabel="CD")
    axes[1].set(title="Lift coefficient", xlabel="Mach", ylabel="CL")
    for ax in axes:
        ax.set_xlim(0.70, 1.50)
        ax.legend(fontsize=8)
    fig.suptitle(f"RASAero Run Test at alpha={args.alpha:g}° vs DATCOM alpha=0°")
    fig.tight_layout()

    output = Path(args.output) if args.output else here / "comparison_lift_drag.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight")
    csv_path = Path(args.csv) if args.csv else output.with_suffix(".csv")
    rasaero.to_csv(csv_path, index=False)
    print(f"RASAero parsed rows at alpha={args.alpha:g}°: {len(rasaero)}")
    print(f"RASAero Mach range: {rasaero['Mach'].min():.2f} to {rasaero['Mach'].max():.2f}")
    for frame, label, _color in cases:
        print(f"DATCOM alpha-zero rows: {label}={len(frame)}")
    print(f"Saved plot: {output}")
    print(f"Saved parsed RASAero rows: {csv_path}")
    if args.show:
        plt.show()


if __name__ == "__main__":
    main()

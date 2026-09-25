"""
Missile DATCOM for006.dat plotter
All reference quantities read directly from for006.dat — no hardcoded constants.
Compatible with any number of fins, any geometry, any beta/alpha/Mach sweep.

Standard plots (beta=0 cases):
  1. CD vs Mach
  2. CP vs Mach
  3. Stability margin vs Mach
  4. CD vs alpha
  5. CN vs alpha
  6. CM vs alpha
  7. CYB vs alpha  (skipped gracefully if not in data)

Asymmetry comparison plots (auto-enabled when beta sweep cases are present):
  8.  CN  vs Mach — sliced at +alpha, 0, -alpha and +beta, 0, -beta
  9.  CM  vs Mach — same
  10. CYB vs Mach — same
"""

import matplotlib.pyplot as plt
import matplotlib.cm as cm
import numpy as np
import os
from datcom_parser import parse_for006

# ---------------------------------------------------------------------------
# Load — everything comes from the file
# ---------------------------------------------------------------------------
current_dir = os.path.dirname(os.path.abspath(__file__))
df, refs = parse_for006(os.path.join(current_dir, "for006.dat"))

LREF = refs["lref"]
XCG  = refs["xcg"]
SREF = refs["sref"]

print(f"LREF={LREF} in  |  XCG={XCG} in  |  SREF={SREF} in²")
print(f"{len(df)} rows  |  Mach: {sorted(df['Mach'].unique())}")
print(f"Beta values: {sorted(df['Beta'].unique())}")
print(f"Alpha values: {sorted(df['ALPHA'].unique())}")

if df.empty:
    print("ERROR: No data parsed from for006.dat.")
    raise SystemExit(1)

# Keep only columns that exist — works for any configuration
KEEP = ["Case", "Mach", "Altitude_ft", "Beta", "ALPHA",
        "CN", "CM", "CA", "CD", "CNA", "CMA", "XCP", "CYB"]
df = df[[c for c in KEEP if c in df.columns]]

if "XCP" in df.columns and LREF:
    df["Stab_cal"] = -df["XCP"]
else:
    df["Stab_cal"] = np.nan

# ---------------------------------------------------------------------------
# Derived sweep info — read entirely from data, nothing hardcoded
# ---------------------------------------------------------------------------
ALPHA_TOL  = 0.01

all_machs  = sorted(df["Mach"].unique())
all_betas  = sorted(df["Beta"].unique())
all_alphas = sorted(df["ALPHA"].unique())
has_beta_sweep = len(all_betas) > 1

# Representative alpha for asymmetry plots:
# use the alpha closest to half the max absolute alpha in the sweep
max_alpha     = max(abs(a) for a in all_alphas)
target_slice  = max_alpha / 2.0
alpha_pos     = min(all_alphas, key=lambda a: abs(a - target_slice))
alpha_neg     = min(all_alphas, key=lambda a: abs(a + target_slice))

# beta=0 baseline subset
beta0        = df[df["Beta"].abs() < ALPHA_TOL]
alpha0_beta0 = beta0[beta0["ALPHA"].abs() < ALPHA_TOL].sort_values("Mach")

# ---------------------------------------------------------------------------
# Colour maps — derived from data ranges, not hardcoded
# ---------------------------------------------------------------------------
mach_colour = {m: cm.plasma(i / max(len(all_machs) - 1, 1))
               for i, m in enumerate(all_machs)}

max_beta_abs = max(abs(b) for b in all_betas) if all_betas else 1.0
beta_colour  = {b: cm.coolwarm(0.5 + 0.5 * b / (max_beta_abs + 1e-9))
                for b in all_betas}

# ---------------------------------------------------------------------------
# Plot style
# ---------------------------------------------------------------------------
plt.rcParams.update({
    "figure.dpi": 120,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "legend.fontsize": 7,
    "legend.framealpha": 0.6,
})

def _scatter_mach(ax, x, y):
    """Scatter coloured by Mach with a connecting trend line."""
    colours = [mach_colour[m] for m in x]
    ax.scatter(x, y, c=colours, s=70, zorder=3)
    ax.plot(x.values, y.values, color="gray", linewidth=0.8, alpha=0.4, zorder=2)

def _safe_plot(ax, sub, col, **kwargs):
    """Plot only if column has non-NaN data in this subset."""
    sub = sub.dropna(subset=[col])
    if sub.empty:
        return
    ax.plot(sub["Mach"], sub[col], **kwargs)

# ---------------------------------------------------------------------------
# 1. CD vs Mach
# ---------------------------------------------------------------------------
if "CA" in df.columns and not alpha0_beta0.empty:
    fig, ax = plt.subplots(figsize=(8, 5))
    _scatter_mach(ax, alpha0_beta0["Mach"], alpha0_beta0["CA"])
    ax.set_xlabel("Mach")
    ax.set_ylabel("CD  (axial force coeff, α=0°, β=0°)")
    ax.set_title("Drag coefficient vs Mach")
    plt.tight_layout()

# ---------------------------------------------------------------------------
# 2. CP vs Mach
# ---------------------------------------------------------------------------
if "XCP" in df.columns and not alpha0_beta0.empty:
    fig, ax = plt.subplots(figsize=(8, 5))
    _scatter_mach(ax, alpha0_beta0["Mach"], alpha0_beta0["XCP"])
    ax.axhline(0, color="gray", linewidth=0.8, linestyle="--", label="CG / moment centre")
    ax.set_xlabel("Mach")
    ax.set_ylabel("X-CP  (ref lengths from CG,  negative = aft = stable)")
    ax.set_title("Centre of pressure vs Mach")
    ax.legend()
    plt.tight_layout()

# ---------------------------------------------------------------------------
# 3. Stability margin vs Mach
# ---------------------------------------------------------------------------
if not alpha0_beta0.empty and not alpha0_beta0["Stab_cal"].isna().all():
    cal_unit = f"calibers  (1 cal = {LREF} in)" if LREF else "calibers"
    fig, ax = plt.subplots(figsize=(8, 5))
    _scatter_mach(ax, alpha0_beta0["Mach"], alpha0_beta0["Stab_cal"])
    ax.axhline(0, color="red",   linewidth=1.0, linestyle="--", alpha=0.7, label="Neutral stability")
    ax.axhline(1, color="green", linewidth=0.8, linestyle=":",  alpha=0.7, label="1 cal (min)")
    ax.axhline(2, color="green", linewidth=0.8, linestyle="--", alpha=0.5, label="2 cal (target)")
    ax.set_xlabel("Mach")
    ax.set_ylabel(f"Static stability margin  ({cal_unit})")
    ax.set_title("Stability margin vs Mach  [positive = stable, CP aft of CG]")
    ax.legend()
    plt.tight_layout()

# ---------------------------------------------------------------------------
# 4-7. Alpha sweep plots — beta=0 baseline, one line per Mach
# ---------------------------------------------------------------------------
for col, ylabel, title in [
    ("CA",  "CD  (axial force coefficient)",     "Drag vs alpha"),
    ("CN",  "CN  (normal force coefficient)",    "Normal force vs alpha"),
    ("CM",  "CM  (pitching moment coefficient)", "Pitching moment vs alpha"),
    ("CYB", "CYB  (side force deriv, per deg)",  "Side force derivative vs alpha"),
]:
    if col not in df.columns:
        continue
    fig, ax = plt.subplots(figsize=(9, 5))
    plotted = False
    for mach in all_machs:
        sub = beta0[beta0["Mach"] == mach].sort_values("ALPHA")
        if sub[col].isna().all():
            continue
        ax.plot(sub["ALPHA"], sub[col], "o-",
                color=mach_colour[mach], label=f"M {mach:.2f}",
                linewidth=1.5, markersize=4)
        plotted = True
    if not plotted:
        plt.close()
        continue
    ax.axvline(0, color="gray", linewidth=0.6, linestyle="--", alpha=0.5)
    ax.set_xlabel("Alpha (deg)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend(bbox_to_anchor=(1.01, 1), loc="upper left", ncol=2, fontsize=6)
    plt.tight_layout()

# ---------------------------------------------------------------------------
# 8-10. Asymmetry comparison plots — only shown when beta sweep exists
#        Slices derived from actual data range, not hardcoded values
# ---------------------------------------------------------------------------
if has_beta_sweep:
    for col, ylabel, title_base in [
        ("CN",  "CN  (normal force coefficient)",    "CN asymmetry comparison vs Mach"),
        ("CM",  "CM  (pitching moment coefficient)", "CM asymmetry comparison vs Mach"),
        ("CYB", "CYB  (side force deriv, per deg)",  "CYB asymmetry comparison vs Mach"),
    ]:
        if col not in df.columns:
            continue

        fig, ax = plt.subplots(figsize=(9, 5))
        plotted = False

        # Blue lines: beta=0, sliced at +alpha, 0, -alpha
        for alpha_val, ls, lbl in [
            (alpha_pos, "-",  f"β=0°, α=+{alpha_pos:.0f}°"),
            (0.0,       "--", f"β=0°, α=0°"),
            (alpha_neg, ":",  f"β=0°, α={alpha_neg:.0f}°"),
        ]:
            sub = df[
                (df["Beta"].abs() < ALPHA_TOL) &
                ((df["ALPHA"] - alpha_val).abs() < ALPHA_TOL)
            ].sort_values("Mach")
            if sub.empty or sub[col].isna().all():
                continue
            ax.plot(sub["Mach"], sub[col], linestyle=ls, color="steelblue",
                    linewidth=1.8, label=lbl, zorder=3)
            plotted = True

        # Coloured lines: alpha=0, one line per nonzero beta
        for beta_val in all_betas:
            if abs(beta_val) < ALPHA_TOL:
                continue
            sub = df[
                ((df["Beta"] - beta_val).abs() < ALPHA_TOL) &
                (df["ALPHA"].abs() < ALPHA_TOL)
            ].sort_values("Mach")
            if sub.empty or sub[col].isna().all():
                continue
            ax.plot(sub["Mach"], sub[col], "o-",
                    color=beta_colour[beta_val], linewidth=1.5, markersize=4,
                    label=f"β={beta_val:+.0f}°, α=0°", zorder=2)
            plotted = True

        if not plotted:
            plt.close()
            continue

        ax.axhline(0, color="gray", linewidth=0.6, linestyle="--", alpha=0.4)
        ax.set_xlabel("Mach")
        ax.set_ylabel(ylabel)
        ax.set_title(
            f"{title_base}\n"
            f"Blue lines = α slice at β=0°  |  Coloured lines = β slice at α=0°"
        )
        ax.legend(bbox_to_anchor=(1.01, 1), loc="upper left", fontsize=7)
        plt.tight_layout()

plt.show()

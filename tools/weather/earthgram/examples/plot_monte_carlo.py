"""Example: 500 GRAM Monte Carlo atmospheres above a launch site, plotted against altitude.

    python tools/weather/earthgram/examples/plot_monte_carlo.py [--n 500] [--out monte_carlo_profiles.png]

Top row: pressure, temperature, density and wind speed. Bottom row: each run's
deviation from the GRAM mean, where the scatter is visible (pressure and density
vary by well under 1%, which is too small to see on the top row).
"""
import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))  # tools/weather
import earthgram as eg  # noqa: E402

# reference palette (light): one series hue, ink for the mean, recessive grid/axes
SERIES = "#2a78d6"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
SURFACE = "#fcfcfb"

# (column, top-row label, scale, bottom-row deviation label, deviation kind)
PANELS = [
    ("p", "Pressure (kPa)", 1e-3, "Pressure deviation (%)", "pct"),
    ("temp", "Temperature (K)", 1.0, "Temperature deviation (K)", "abs"),
    ("rho", "Density (kg/m³)", 1.0, "Density deviation (%)", "pct"),
    ("speed", "Wind speed (m/s)", 1.0, "Wind speed deviation (m/s)", "abs"),
]


def style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=INK_2, labelsize=8, length=3, color=MUTED)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--time", default="2026-06-15 13:00")
    ap.add_argument("--tz", default="America/Chicago")
    ap.add_argument("--top", type=float, default=30_000)
    ap.add_argument("--out", type=Path, default=HERE / "monte_carlo_profiles.png")
    a = ap.parse_args(argv)

    site = eg.LaunchSite("West TX", lat=31.024, lon=-103.6616, elev_m=900)
    mc = eg.monte_carlo(site, a.time, a.tz, n=a.n, seed=a.seed, top_m=a.top, step_m=250)
    alt_km = mc.mean["alt_m"].to_numpy() / 1000
    q = mc.percentiles([5, 95], cols=[c for c, *_ in PANELS])

    fig, axes = plt.subplots(2, 4, figsize=(15, 10), sharey=True, facecolor=SURFACE,
                             gridspec_kw={"hspace": 0.28, "wspace": 0.12})
    for j, (col, label, scale, dlabel, kind) in enumerate(PANELS):
        m = mc.mean[col].to_numpy()
        vals = mc.values(col)  # (runs, altitudes)
        lo, hi = q[f"{col}_p5"].to_numpy(), q[f"{col}_p95"].to_numpy()
        rel = (lambda x: (x - m) / m * 100) if kind == "pct" else (lambda x: x - m)

        for row, (x, bounds, xm, xlabel) in enumerate((
                (vals * scale, (lo * scale, hi * scale), m * scale, label),
                (rel(vals), (rel(lo), rel(hi)), np.zeros_like(m), dlabel))):
            ax = axes[row, j]
            style(ax)
            # the fuzz: every run as a faint line, drawn as one collection
            ax.add_collection(LineCollection(np.stack([x, np.broadcast_to(alt_km, x.shape)], axis=-1),
                                             colors=SERIES, alpha=0.035, linewidths=0.6))
            ax.autoscale_view()
            for b in bounds:
                ax.plot(b, alt_km, color=SERIES, linewidth=1.2, linestyle=(0, (4, 2)))
            ax.plot(xm, alt_km, color=INK, linewidth=2)
            ax.set_xlabel(xlabel, color=INK_2, fontsize=9)
            if col == "p" and row == 0:
                ax.set_xscale("log")
            if row == 1:
                ax.axvline(0, color=MUTED, linewidth=0.6)
    for row in range(2):
        axes[row, 0].set_ylabel("Altitude (km MSL)", color=INK_2, fontsize=9)
    axes[0, 0].set_ylim(alt_km[0], alt_km[-1])

    # one legend for the whole figure (a single series, so it names the line kinds)
    handles = [
        plt.Line2D([], [], color=SERIES, alpha=0.4, linewidth=1, label=f"{mc.n} Monte Carlo atmospheres"),
        plt.Line2D([], [], color=SERIES, linewidth=1.2, linestyle=(0, (4, 2)), label="5th / 95th percentile"),
        plt.Line2D([], [], color=INK, linewidth=2, label="GRAM mean"),
    ]
    fig.legend(handles=handles, loc="upper right", bbox_to_anchor=(0.985, 0.955), ncol=3,
               frameon=False, fontsize=9, labelcolor=INK_2)
    local = mc.time_utc.tz_convert(a.tz)
    fig.suptitle(f"Earth-GRAM 2016 Monte Carlo above {site.lat:.3f}, {site.lon:.4f} ({site.elev_m:.0f} m)",
                 x=0.07, y=0.975, ha="left", color=INK, fontsize=13, fontweight="bold")
    fig.text(0.07, 0.937, f"{local:%d %b %Y %H:%M} local ({mc.time_utc:%H:%M} UTC) · NCEP 1997–2015 "
             f"{local:%B} climatology · seed {a.seed}", color=INK_2, fontsize=9, ha="left")
    fig.subplots_adjust(left=0.07, right=0.985, top=0.9, bottom=0.06)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(a.out, dpi=150, facecolor=SURFACE)
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()

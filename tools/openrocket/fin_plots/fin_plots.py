#!/usr/bin/env python3
"""
fin_plots.py  —  OpenRocket fin-sweep CSV  ->  envelope plots, best-apogee map, summary.

USAGE
    python fin_plots.py sim_results.csv
    python fin_plots.py sim_results.csv -o plots --condition worst --under 2.47 --over 6.85
    python fin_plots.py sim_results.csv --root 16.25 --tip 2        # report plot for this pair
    python fin_plots.py sim_results.csv --root 14 --tip 4           # label a single-planform file

INPUT
    The batch-export CSV (columns source_file, simulation_name, apogee_ft,
    min_stability_cal, max_stability_cal, ...).  Fin geometry is read from the .ork
    file name: ..._root-15.25in_tip-2in_height-6.5in_sweep-16in.ork
    (root/tip are optional: a file with only height/sweep is treated as one planform).
    If several sim conditions are present (worst / average / no wind ...), only
    --condition (default "worst") is used.

OUTPUT  (in the output folder, default "<csv name>_plots")
    report_envelope_root-R_tip-T.png   full-size envelope for one root/tip pair
                                       (best pair, or --root/--tip)
    optimum_map.png                    best-apogee map (annotated heatmap), root x tip
    master_trellis.png                 every envelope nested in the root x tip grid (no isobars)
    envelopes_tip-T.png                full envelopes for every root, one figure per tip
    best_per_root_tip.csv              best design for every root/tip pair
    summary.txt                        fit quality, best design, partial derivatives

METHOD
    * Each output (apogee, min stability, max stability) is fitted with a polynomial
      in the parameters that vary.  Degree (1-3, or 1-4 for a 2-parameter file) is
      picked by 5-fold cross-validation: the lowest degree within 2% of the best.
    * Stability limits are the level sets min_stab = UNDER and max_stab = OVER.
    * Best design per root/tip = highest fitted apogee inside the tested span x sweep
      range satisfying both limits (dense grid, then SLSQP).  '*' marks a best point
      on the edge of the tested range (the true corner lies outside it).

REQUIRES  numpy, pandas, matplotlib, scipy
"""
import argparse, itertools, os, re, sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from scipy.optimize import minimize

# ----------------------------------------------------------------- settings
DEFAULTS = dict(under=2.47, over=6.85, under_label="9%", over_label="25%", condition="worst")
X_LABEL, Y_LABEL = "Sweep Distance (in)", "Fin Span (in)"

SURF, INK, INK2, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984"
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
CMAP = LinearSegmentedColormap.from_list("apogee", SEQ)
UNDER_C, OVER_C, GOLD = "#eb6834", INK, "#ffd23f"
HALO = [pe.withStroke(linewidth=3, foreground="white")]
PARAMS = ["root", "tip", "span", "sweep"]


# ----------------------------------------------------------------- loading
def load(path, condition, root=None, tip=None):
    d = pd.read_csv(path)
    name = d.source_file.astype(str).str.split(r"[\\/]").str[-1]
    num = lambda key: name.str.extract(key + r"-([\d.]+)in", expand=False).astype(float)
    d["span"], d["sweep"], d["root"], d["tip"] = num("height"), num("sweep"), num("root"), num("tip")
    if d[["span", "sweep"]].isna().any().any():
        sys.exit("Could not read height/sweep from every source_file name.")
    single = d.root.isna().all()
    d["root"] = d.root.fillna(root if root is not None else -1)
    d["tip"] = d.tip.fillna(tip if tip is not None else -1)
    if "simulation_name" in d and d.simulation_name.nunique() > 1:
        conds = sorted(d.simulation_name.unique())
        if condition not in conds:
            sys.exit(f"Condition '{condition}' not in file. Available: {conds}")
        d = d[d.simulation_name == condition]
    if "status" in d:
        d = d[d.status.astype(str).str.upper() == "OK"]
    return d.reset_index(drop=True), single


# ----------------------------------------------------------------- fitting
class Fit:
    """Polynomial fit in the varying parameters; degree chosen by cross-validation."""

    def __init__(self, d, y):
        self.vars = [p for p in PARAMS if d[p].nunique() > 1]
        X = d[self.vars].values.astype(float)
        self.mu, self.sd = X.mean(0), X.std(0)
        self.nlev = [d[p].nunique() for p in self.vars]
        cands = [1, 2, 3, 4] if len(self.vars) <= 2 else [1, 2, 3]
        yv = d[y].values
        idx = np.random.default_rng(0).permutation(len(d))
        cv = {}
        for deg in cands:
            T = self._terms(deg)
            if len(T) >= 0.5 * len(d):
                break
            A, err = self._design(X, T), []
            for k in range(5):
                te = idx[k::5]; tr = np.setdiff1d(idx, te)
                c, *_ = np.linalg.lstsq(A[tr], yv[tr], rcond=None)
                err.append(yv[te] - A[te] @ c)
            cv[deg] = np.concatenate(err).std()
        best = min(cv.values())
        self.deg = min(k for k, v in cv.items() if v <= 1.02 * best)
        self.T = self._terms(self.deg)
        A = self._design(X, self.T)
        self.c, *_ = np.linalg.lstsq(A, yv, rcond=None)
        self.resid = float(np.std(yv - A @ self.c))
        self.cv = cv

    def _terms(self, deg):
        n = len(self.vars)
        return [()] + [t for k in range(1, deg + 1)
                       for t in itertools.combinations_with_replacement(range(n), k)
                       if all(t.count(i) < self.nlev[i] for i in set(t))]

    def _design(self, X, T):
        Z = (np.atleast_2d(X) - self.mu) / self.sd
        return np.column_stack([np.prod(Z[:, t], axis=1) if t else np.ones(len(Z)) for t in T])

    def __call__(self, r, t, h, w):
        h, w = np.broadcast_arrays(np.asarray(h, float), np.asarray(w, float))
        cols = {"root": np.full(h.size, r), "tip": np.full(h.size, t), "span": h.ravel(), "sweep": w.ravel()}
        X = np.column_stack([cols[v] for v in self.vars])
        return (self._design(X, self.T) @ self.c).reshape(h.shape)


# ----------------------------------------------------------------- analysis
def best_point(F, r, t, box, under, over):
    (hlo, hhi), (wlo, whi) = box
    Hg, Wg = np.meshgrid(np.linspace(hlo, hhi, 201), np.linspace(wlo, whi, 201), indexing="ij")
    A = F["apogee_ft"](r, t, Hg, Wg)
    ok = (F["min_stability_cal"](r, t, Hg, Wg) >= under) & (F["max_stability_cal"](r, t, Hg, Wg) <= over)
    if not ok.any():
        return None
    k = np.argmax(np.where(ok, A, -np.inf))
    x0 = np.array([Hg.flat[k], Wg.flat[k]])
    res = minimize(lambda x: -F["apogee_ft"](r, t, *x).item(), x0, method="SLSQP",
                   bounds=[(hlo, hhi), (wlo, whi)],
                   constraints=[{"type": "ineq", "fun": lambda x: F["min_stability_cal"](r, t, *x).item() - under},
                                {"type": "ineq", "fun": lambda x: over - F["max_stability_cal"](r, t, *x).item()}])
    h, w = res.x if res.success else x0
    th, tw = 1e-3 * (hhi - hlo), 1e-3 * (whi - wlo)
    return dict(root=r, tip=t, span=h, sweep=w,
                apogee_ft=F["apogee_ft"](r, t, h, w).item(),
                min_stability_cal=F["min_stability_cal"](r, t, h, w).item(),
                max_stability_cal=F["max_stability_cal"](r, t, h, w).item(),
                on_range_edge=bool(h - hlo < th or hhi - h < th or w - wlo < tw or whi - w < tw))


def nice_step(span_of_values, target=6):
    for s in [10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500]:
        if span_of_values / s <= target:
            return s
    return 5000


def cell_edges(v):
    v = np.asarray(v, float)
    if len(v) == 1:
        return np.array([v[0] - 0.5, v[0] + 0.5])
    return np.concatenate([[v[0] - (v[1] - v[0]) / 2], (v[:-1] + v[1:]) / 2, [v[-1] + (v[-1] - v[-2]) / 2]])


# ----------------------------------------------------------------- drawing
def draw_panel(ax, g, F, r, t, under, over, norm, spans, sweeps, b, *, isobars=None,
               dot=8, lw=2.2, iso_fmt="{:,.0f}", iso_fs=6.5, iso_lw=0.9):
    xe, ye = cell_edges(sweeps), cell_edges(spans)
    We, He = np.meshgrid(np.linspace(xe[0], xe[-1], 220), np.linspace(ye[0], ye[-1], 220))
    P = g.pivot_table(index="span", columns="sweep", values="apogee_ft").reindex(index=spans, columns=sweeps)
    ax.set_facecolor(SURF)
    ax.pcolormesh(xe, ye, P.values, cmap=CMAP, norm=norm, edgecolors=SURF,
                  linewidth=0.6 if dot >= 8 else 0.35, rasterized=True)
    MN, MX = F["min_stability_cal"](r, t, He, We), F["max_stability_cal"](r, t, He, We)
    ok = (MN >= under) & (MX <= over)
    ax.contourf(We, He, (~ok).astype(float), levels=[0.5, 1.5], colors=[SURF], alpha=0.72)
    if isobars is not None:
        A = F["apogee_ft"](r, t, He, We)
        cs = ax.contour(We, He, A, levels=isobars, colors=INK, linewidths=iso_lw)
        cs.set_path_effects(HALO)
        for tx in ax.clabel(cs, fmt=lambda v: iso_fmt.format(v), fontsize=iso_fs, inline=True, inline_spacing=5):
            tx.set_color(INK); tx.set_fontweight("bold"); tx.set_path_effects(HALO)
    if MN.min() < under < MN.max():
        ax.contour(We, He, MN, levels=[under], colors=[UNDER_C], linewidths=lw)
    if MX.min() < over < MX.max():
        ax.contour(We, He, MX, levels=[over], colors=[OVER_C], linewidths=lw)
    raw = (g.min_stability_cal >= under) & (g.max_stability_cal <= over)
    ax.scatter(g.sweep[raw], g.span[raw], s=dot, c=INK, lw=0, zorder=4)
    ax.scatter(g.sweep[~raw], g.span[~raw], s=dot, facecolors="none", edgecolors=MUTED, lw=0.6, zorder=4)
    if b:
        ax.scatter([b["sweep"]], [b["span"]], s=dot * 15, marker="*", c=GOLD, edgecolors=INK,
                   lw=0.8, zorder=6, clip_on=False)
    ax.set_xlim(xe[0], xe[-1]); ax.set_ylim(ye[0], ye[-1])
    for sp in ax.spines.values():
        sp.set_color(MUTED); sp.set_linewidth(0.6)
    ax.tick_params(colors=INK2, labelsize=8, length=2)


def legend_handles(o, isobars=False, best_overall=False, big=False):
    h = [Line2D([], [], color=OVER_C, lw=2.2, label=f"Overstable limit ({o.over_label})"),
         Line2D([], [], color=UNDER_C, lw=2.2, label=f"Understable limit ({o.under_label})")]
    if isobars:
        h.append(Line2D([], [], color=INK, lw=1.1, path_effects=HALO, label="Apogee isobar"))
    h += [Patch(facecolor=SEQ[4], edgecolor=SURF, label="Sim apogee (darker = higher)"),
          Patch(facecolor=SURF, edgecolor=MUTED, label="Outside stability window"),
          Line2D([], [], marker="o", ls="", color=INK, ms=4, label="Sim passed"),
          Line2D([], [], marker="o", ls="", mfc="none", mec=MUTED, ms=4, label="Sim failed"),
          Line2D([], [], marker="*", ls="", mfc=GOLD, mec=INK, ms=13 if big else 11,
                 label="Highest-apogee feasible point")]
    if best_overall:
        h.append(Patch(facecolor="none", edgecolor=GOLD, lw=2.5, label="Best overall"))
    return h


def pair_label(r, t):
    return "Single planform" if r < 0 or t < 0 else f"Root Chord - {r:g} in, Tip Chord {t:g} in"


def report_plot(d, F, o, r, t, b, path):
    g = d[(d.root == r) & (d.tip == t)]
    spans, sweeps = np.sort(g.span.unique()), np.sort(g.sweep.unique())
    norm = Normalize(g.apogee_ft.quantile(0.01), g.apogee_ft.quantile(0.99))
    step = o.isobar_step or nice_step(g.apogee_ft.max() - g.apogee_ft.min())
    lv = np.arange(np.floor(g.apogee_ft.min() / step) * step - step, g.apogee_ft.max() + 2 * step, step)
    fig, ax = plt.subplots(figsize=(9, 6.6), facecolor=SURF)
    draw_panel(ax, g, F, r, t, o.under, o.over, norm, spans, sweeps, b, isobars=lv,
               dot=12, lw=2.6, iso_fmt="{:,.0f} ft", iso_fs=8, iso_lw=1.3)
    if b:
        xe = cell_edges(sweeps)
        right = (b["sweep"] - xe[0]) / (xe[-1] - xe[0]) > 0.6
        ax.annotate(f"{b['apogee_ft']:,.0f} ft\nspan {b['span']:.2f} in, sweep {b['sweep']:.2f} in"
                    + ("\n(on edge of tested range)" if b["on_range_edge"] else ""),
                    (b["sweep"], b["span"]), xytext=(-40 if right else 40, -70), textcoords="offset points",
                    ha="right" if right else "left", fontsize=9, color=INK, zorder=8,
                    bbox=dict(boxstyle="round,pad=0.35", fc=SURF, ec=MUTED, lw=0.6),
                    arrowprops=dict(arrowstyle="-", color=INK, lw=0.7))
    if len(sweeps) <= 12: ax.set_xticks(sweeps)
    if len(spans) <= 12: ax.set_yticks(spans)
    ax.tick_params(labelsize=9)
    ax.set_xlabel(X_LABEL, color=INK2, fontsize=11); ax.set_ylabel(Y_LABEL, color=INK2, fontsize=11)
    ax.legend(handles=legend_handles(o, isobars=True, big=True), loc="upper right", fontsize=8,
              frameon=True, facecolor=SURF, edgecolor=MUTED, framealpha=1).set_zorder(20)
    fig.text(0.075, 0.955, "Sweep-Span Fin Geometry Envelope", fontsize=15, color=INK, va="top")
    fig.text(0.075, 0.912, o.subtitle or f"{pair_label(r, t)}, {o.condition}-case OR Sims",
             fontsize=10, color=INK2, va="top")
    fig.subplots_adjust(left=0.075, right=0.975, top=0.86, bottom=0.09)
    fig.savefig(path, dpi=200, facecolor=SURF); plt.close(fig)


def optimum_map(table, o, path):
    roots, tips = sorted(table.root.unique()), sorted(table.tip.unique())
    nc, nr = len(roots), len(tips)
    fig, ax = plt.subplots(figsize=(max(8, 1.22 * nc + 1.6), max(5.2, 1.5 * nr + 2.4)), facecolor=SURF)
    ax.set_facecolor(SURF)
    M = table.pivot(index="tip", columns="root", values="apogee_ft").reindex(index=tips, columns=roots)
    v = table.apogee_ft.dropna()
    norm = Normalize(v.min() - 0.05 * (v.max() - v.min() + 1), v.max())
    ax.imshow(M.values, origin="lower", cmap=CMAP, norm=norm, aspect="auto")
    top = table.loc[table.apogee_ft.idxmax()]
    for i, t in enumerate(tips):
        for j, r in enumerate(roots):
            row = table[(table.root == r) & (table.tip == t)].iloc[0]
            if pd.isna(row.apogee_ft):
                ax.text(j, i, "none\nfeasible", ha="center", va="center", fontsize=7, color=INK2)
                continue
            col = "white" if norm(row.apogee_ft) > 0.55 else INK
            ax.text(j, i + 0.17, f"{row.apogee_ft:,.0f} ft", ha="center", va="center", fontsize=8.5,
                    color=col, fontweight="bold")
            ax.text(j, i - 0.17, f"h {row.span:.2f}\nw {row.sweep:.2f}" + (" *" if row.on_range_edge else ""),
                    ha="center", va="center", fontsize=7.2, color=col, linespacing=1.3)
            if r == top.root and t == top.tip:
                ax.add_patch(plt.Rectangle((j - .5, i - .5), 1, 1, fill=False, ec=GOLD, lw=3))
    ax.set_xticks(range(nc), [f"{r:g}" for r in roots]); ax.set_yticks(range(nr), [f"{t:g}" for t in tips])
    ax.set_xlabel("Root Chord (in)", color=INK2); ax.set_ylabel("Tip Chord (in)", color=INK2)
    ax.tick_params(colors=INK2, length=0)
    for sp in ax.spines.values(): sp.set_visible(False)
    fig.text(0.01, 0.985, "Best Feasible Apogee by Root and Tip Chord", fontsize=15, color=INK, va="top")
    fig.text(0.01, 0.93, f"Each cell: highest apogee and its span (h) / sweep (w) inside the {o.over_label} / "
             f"{o.under_label} stability window, {o.condition}-case OR Sims\n"
             "gold box = best · * = best point on edge of tested span/sweep range",
             fontsize=9, color=INK2, va="top")
    H, Wd = fig.get_figheight(), fig.get_figwidth()
    fig.subplots_adjust(left=0.6 / Wd, right=0.99, top=1 - 1.2 / H, bottom=0.75 / H)
    fig.savefig(path, dpi=170, facecolor=SURF); plt.close(fig)


def master_trellis(d, F, o, table, path):
    roots, tips = sorted(d.root.unique()), sorted(d.tip.unique())
    spans, sweeps = np.sort(d.span.unique()), np.sort(d.sweep.unique())
    norm = Normalize(d.apogee_ft.quantile(0.01), d.apogee_ft.quantile(0.99))
    top = table.loc[table.apogee_ft.idxmax()]
    nr, nc = len(tips), len(roots)
    wide = 2.25 * nc + 1.3 >= 20
    fig, axes = plt.subplots(nr, nc, figsize=(2.25 * nc + 1.3, 2.25 * nr + (2.2 if wide else 2.6)),
                             sharex=True, sharey=True, facecolor=SURF, squeeze=False)
    for i, t in enumerate(reversed(tips)):
        for j, r in enumerate(roots):
            ax = axes[i, j]
            g = d[(d.root == r) & (d.tip == t)]
            b = table[(table.root == r) & (table.tip == t)].iloc[0]
            bd = None if pd.isna(b.apogee_ft) else b.to_dict()
            draw_panel(ax, g, F, r, t, o.under, o.over, norm, spans, sweeps, bd, dot=5, lw=1.8)
            is_top = r == top.root and t == top.tip
            lab = ("none feasible" if bd is None else
                   f"{b.apogee_ft:,.0f} ft\nh {b.span:.2f} · w {b.sweep:.2f}" + (" *" if b.on_range_edge else ""))
            ax.text(0.03, 0.97, lab, transform=ax.transAxes, ha="left", va="top", fontsize=7, color=INK,
                    fontweight="bold" if is_top else "normal", zorder=8, linespacing=1.25,
                    bbox=dict(boxstyle="round,pad=0.22", fc=SURF, ec="none", alpha=0.92))
            if is_top:
                for sp in ax.spines.values(): sp.set_color(GOLD); sp.set_linewidth(2.5)
            ax.tick_params(labelsize=7)
            ax.set_xticks([sweeps[0], sweeps[len(sweeps) // 2], sweeps[-1]])
            ax.set_yticks([spans[0], spans[len(spans) // 2], spans[-1]])
            if i == 0: ax.set_title(f"Root {r:g} in", fontsize=10, color=INK)
            if j == 0: ax.set_ylabel(f"Tip {t:g} in\n{Y_LABEL}", fontsize=10, color=INK, labelpad=6)
    H, Wd = fig.get_figheight(), fig.get_figwidth()
    fig.supxlabel(X_LABEL + "  (within each panel)", fontsize=10, color=INK2, y=0.25 / H)
    fig.legend(handles=legend_handles(o, best_overall=True), loc="upper left", ncol=8 if wide else 4, fontsize=9,
               frameon=False, bbox_to_anchor=(0.6 / Wd, 1 - 0.95 / H))
    fig.text(0.6 / Wd, 1 - 0.2 / H, "Fin Envelope Trellis by Root and Tip Chord", fontsize=15, color=INK, va="top")
    fig.text(0.6 / Wd, 1 - 0.58 / H, f"{len(d):,} {o.condition}-case OR Sims · each panel: sweep × span envelope "
             "with its highest-apogee point (apogee, span h, sweep w) · * = best point on edge of tested range",
             fontsize=9.5, color=INK2, va="top")
    fig.subplots_adjust(left=0.85 / Wd, right=0.995, top=1 - (1.7 if wide else 2.1) / H, bottom=0.75 / H,
                        wspace=0.07, hspace=0.1)
    fig.savefig(path, dpi=150, facecolor=SURF); plt.close(fig)


def per_tip_envelopes(d, F, o, table, outdir):
    roots, tips = sorted(d.root.unique()), sorted(d.tip.unique())
    spans, sweeps = np.sort(d.span.unique()), np.sort(d.sweep.unique())
    norm = Normalize(d.apogee_ft.quantile(0.01), d.apogee_ft.quantile(0.99))
    rng = d.groupby(["root", "tip"]).apogee_ft.agg(lambda s: s.max() - s.min()).median()
    step = o.isobar_step or nice_step(rng)
    lv = np.arange(np.floor(d.apogee_ft.min() / step) * step - step, d.apogee_ft.max() + 2 * step, step)
    top = table.loc[table.apogee_ft.idxmax()]
    ncol = min(4, len(roots)); nrow = int(np.ceil(len(roots) / ncol))
    for t in tips:
        fig, axes = plt.subplots(nrow, ncol, figsize=(3.7 * ncol, 3.05 * nrow + 2.0),
                                 sharex=True, sharey=True, facecolor=SURF, squeeze=False)
        axes = axes.ravel()
        for j, r in enumerate(roots):
            ax = axes[j]
            g = d[(d.root == r) & (d.tip == t)]
            b = table[(table.root == r) & (table.tip == t)].iloc[0]
            bd = None if pd.isna(b.apogee_ft) else b.to_dict()
            draw_panel(ax, g, F, r, t, o.under, o.over, norm, spans, sweeps, bd, isobars=lv)
            is_top = r == top.root and t == top.tip
            lab = ("none feasible" if bd is None else
                   f"{b.apogee_ft:,.0f} ft · h {b.span:.2f} · w {b.sweep:.2f}" + (" *" if b.on_range_edge else ""))
            ax.text(0.02, 0.98, lab, transform=ax.transAxes, ha="left", va="top", fontsize=7.5, color=INK,
                    fontweight="bold" if is_top else "normal", zorder=8,
                    bbox=dict(boxstyle="round,pad=0.25", fc=SURF, ec="none", alpha=0.92))
            if is_top:
                for sp in ax.spines.values(): sp.set_color(GOLD); sp.set_linewidth(2.5)
            ax.tick_params(labelbottom=True, labelleft=True)
            ax.set_title(f"Root {r:g} in", fontsize=10.5, color=INK)
            if j % ncol == 0: ax.set_ylabel(Y_LABEL, fontsize=9, color=INK2)
            if j >= len(roots) - ncol: ax.set_xlabel(X_LABEL, fontsize=9, color=INK2)
        for ax in axes[len(roots):]:
            ax.axis("off")
        H = fig.get_figheight()
        fig.legend(handles=legend_handles(o, isobars=True), loc="upper left", ncol=4, fontsize=9,
                   frameon=False, bbox_to_anchor=(0.01, 1 - 0.95 / H))
        fig.text(0.01, 1 - 0.2 / H, f"Sweep-Span Fin Geometry Envelopes — Tip Chord {t:g} in",
                 fontsize=15, color=INK, va="top")
        fig.text(0.01, 1 - 0.55 / H, f"{len(d[d.tip == t]):,} {o.condition}-case OR Sims · isobars every "
                 f"{step:,} ft · gold frame = best overall", fontsize=9.5, color=INK2, va="top")
        fig.subplots_adjust(left=0.06, right=0.99, top=1 - 2.05 / H, bottom=0.5 / H, wspace=0.12, hspace=0.32)
        fig.savefig(os.path.join(outdir, f"envelopes_tip-{t:g}in.png"), dpi=150, facecolor=SURF)
        plt.close(fig)


# ----------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Fin-sweep CSV -> envelope plots.")
    ap.add_argument("csv")
    ap.add_argument("-o", "--outdir")
    ap.add_argument("--condition", default=DEFAULTS["condition"], help="sim condition to use (default worst)")
    ap.add_argument("--under", type=float, default=DEFAULTS["under"], help="min stability limit, cal")
    ap.add_argument("--over", type=float, default=DEFAULTS["over"], help="max stability limit, cal")
    ap.add_argument("--under-label", default=DEFAULTS["under_label"])
    ap.add_argument("--over-label", default=DEFAULTS["over_label"])
    ap.add_argument("--root", type=float, help="root chord for the report plot (or label for a single-planform file)")
    ap.add_argument("--tip", type=float, help="tip chord for the report plot (or label for a single-planform file)")
    ap.add_argument("--isobar-step", type=float, help="ft between isobars (default: automatic)")
    ap.add_argument("--subtitle", help="subtitle for the report plot")
    o = ap.parse_args()
    outdir = o.outdir or os.path.splitext(os.path.basename(o.csv))[0] + "_plots"
    os.makedirs(outdir, exist_ok=True)

    d, single = load(o.csv, o.condition, o.root, o.tip)
    F = {y: Fit(d, y) for y in ["apogee_ft", "min_stability_cal", "max_stability_cal"]}
    box = ((d.span.min(), d.span.max()), (d.sweep.min(), d.sweep.max()))
    pairs = sorted(d.groupby(["root", "tip"]).groups.keys())
    rows = []
    for r, t in pairs:
        b = best_point(F, r, t, box, o.under, o.over)
        rows.append(b or dict(root=r, tip=t, apogee_ft=np.nan))
    table = pd.DataFrame(rows)
    for c in ["span", "sweep", "min_stability_cal", "max_stability_cal", "on_range_edge"]:
        if c not in table: table[c] = np.nan
    passing = d.assign(ok=(d.min_stability_cal >= o.under) & (d.max_stability_cal <= o.over)) \
               .groupby(["root", "tip"]).ok.sum().rename("passing_sims").reset_index()
    table = table.merge(passing, on=["root", "tip"])
    table.sort_values("apogee_ft", ascending=False).to_csv(os.path.join(outdir, "best_per_root_tip.csv"), index=False)
    if table.apogee_ft.isna().all():
        print("No feasible design anywhere in this file for the given limits.")
    top = table.loc[table.apogee_ft.idxmax()] if table.apogee_ft.notna().any() else table.iloc[0]

    # report plot
    if o.root is not None and o.tip is not None and not single:
        rr, tt = o.root, o.tip
        if not ((table.root == rr) & (table.tip == tt)).any():
            sys.exit(f"root {rr:g} / tip {tt:g} not in file. Roots: {sorted(d.root.unique())}, tips: {sorted(d.tip.unique())}")
    else:
        rr, tt = top.root, top.tip
    brow = table[(table.root == rr) & (table.tip == tt)].iloc[0]
    report_plot(d, F, o, rr, tt, None if pd.isna(brow.apogee_ft) else brow.to_dict(),
                os.path.join(outdir, "report_envelope" + ("" if rr < 0 else f"_root-{rr:g}_tip-{tt:g}") + ".png"))

    if len(pairs) > 1:
        optimum_map(table, o, os.path.join(outdir, "optimum_map.png"))
        master_trellis(d, F, o, table, os.path.join(outdir, "master_trellis.png"))
        per_tip_envelopes(d, F, o, table, outdir)

    # summary
    lines = [f"File: {o.csv}", f"Condition: {o.condition}   sims used: {len(d):,}",
             f"Limits: min stability >= {o.under} cal ({o.under_label}), max stability <= {o.over} cal ({o.over_label})",
             "Tested: " + ", ".join(f"{p} {d[p].min():g}-{d[p].max():g} ({d[p].nunique()} values)"
                                    for p in PARAMS if d[p].nunique() > 1), "",
             "Fits (degree chosen by cross-validation; residual = typical sim scatter about the fit):"]
    for y, f in F.items():
        lines.append(f"  {y:18s} degree {f.deg}   residual {f.resid:.4g}")
    if pd.notna(top.apogee_ft):
        lines += ["", f"Best design: {pair_label(top.root, top.tip)}, span {top.span:.3f} in, "
                      f"sweep {top.sweep:.3f} in -> {top.apogee_ft:,.0f} ft"
                      + ("  (on edge of tested range)" if top.on_range_edge else ""),
                  f"  min stability {top.min_stability_cal:.3f} cal, max stability {top.max_stability_cal:.3f} cal,"
                  f" passing sims for this pair: {int(top.passing_sims)}", "",
                  "Partial derivatives of apogee at the best design (others held fixed, ignores stability):"]
        eps = {"root": 0.05, "tip": 0.05, "span": 0.01, "sweep": 0.05}
        base = dict(r=top.root, t=top.tip, h=top.span, w=top.sweep)
        for p, key in [("root", "r"), ("tip", "t"), ("span", "h"), ("sweep", "w")]:
            if d[p].nunique() < 2: continue
            hi, lo = dict(base), dict(base); hi[key] += eps[p]; lo[key] -= eps[p]
            der = (F["apogee_ft"](hi["r"], hi["t"], hi["h"], hi["w"]).item()
                   - F["apogee_ft"](lo["r"], lo["t"], lo["h"], lo["w"]).item()) / (2 * eps[p])
            lines.append(f"  dApogee/d{p:5s} = {der:9.1f} ft/in")
    lines += ["", "Top designs:", table.sort_values("apogee_ft", ascending=False).head(10)
              .round(3).to_string(index=False)]
    txt = "\n".join(lines)
    open(os.path.join(outdir, "summary.txt"), "w").write(txt + "\n")
    print(txt)
    print(f"\nPlots written to {outdir}/")


if __name__ == "__main__":
    main()

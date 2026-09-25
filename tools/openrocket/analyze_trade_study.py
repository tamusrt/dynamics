#!/usr/bin/env python3
r"""
analyze_trade_study.py
-----------------------
Takes the results CSV produced by batch_simulate.py (one row per rocket file
+ simulation) and:
  1. Parses the trade-study traits out of each filename.
  2. Writes sorted/ranked CSVs (overall, and one per trait).
  3. Generates PNG charts ranking apogee, and showing how each trait affects
     apogee, with the unmodified baseline rocket called out for reference.

FILENAME CONVENTION THIS SCRIPT ASSUMES (as described for this trade study)
    "SolInvictus"      -- the unmodified baseline rocket, no other segments
    "_<x>Trim"         -- x = inches removed from the nosecone length
    "_<x>-6"           -- x = diameter (in) of the fore side of the transition
                           (aft side fixed at 6")
    "_<x>Trans"        -- x = length (in) of the transition
    "_<section>"       -- whichever segment is left over names the section
                           the transition is placed after

Segments are underscore-separated and matched independently in any order, and
any subset can be present (a file doesn't have to touch every trait) -- so
"SolInvictus_2Trim_AftBay.ork" and "SolInvictus_1.5Trans_4-6_Fins.ork" both
parse fine. Whatever segment doesn't match a known numeric pattern is treated
as the "section" label.

IMPORTANT -- CHECK THIS FIRST
    Filename parsing is guessed from your description, not verified against
    your real filenames. Every run writes `parsed_traits_debug.csv` with one
    row per input file showing exactly what was extracted. Open that file
    FIRST and confirm it looks right before trusting the ranking/plots --
    especially if any row shows unparsed_segments non-empty, or a trait you
    expected to see is blank. If something's off, paste a few real filenames
    back and the regexes below (see `parse_traits`) can be corrected in
    seconds.

MULTIPLE SIMULATIONS PER FILE
    If a source .ork file has more than one simulation in your results CSV,
    this script aggregates them per file for the ranking/trait plots (mean,
    by default -- see --agg) and clearly labels charts as showing that
    aggregate. The full per-simulation detail is preserved in
    overall_ranking.csv regardless.

USAGE
    python analyze_trade_study.py --results-csv results.csv
    python analyze_trade_study.py --results-csv results.csv --output-dir analysis --metric apogee_ft --agg max
"""

import argparse
import re
import sys
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    print("ERROR: pandas is required. Install it with:\n    pip install pandas matplotlib", file=sys.stderr)
    sys.exit(1)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    print("ERROR: matplotlib is required. Install it with:\n    pip install pandas matplotlib", file=sys.stderr)
    sys.exit(1)


# ----------------------------------------------------------------------------
# Palette (validated categorical/sequential set -- see conversation for source)
# ----------------------------------------------------------------------------
COLOR_SURFACE = "#fcfcfb"
COLOR_GRID = "#e1e0d9"
COLOR_MUTED = "#898781"
COLOR_INK = "#0b0b0b"
COLOR_INK_SECONDARY = "#52514e"
COLOR_MODIFIED = "#2a78d6"   # blue -- categorical slot 1
COLOR_BASELINE = "#eb6834"   # orange -- categorical slot 2, reserved for the one baseline series
COLOR_BASELINE_LINE = "#c3c2b7"
COLOR_INCREASE = "#2a78d6"   # blue -- delta-mode bars/points above baseline
COLOR_DECREASE = "#e34948"   # red -- categorical slot 8, delta-mode bars/points below baseline


def filename_stem(path_str: str) -> str:
    """
    Extract the filename stem (no directory, no extension) regardless of
    whether the path uses Windows backslashes or POSIX forward slashes,
    independent of which OS this script happens to be running on (pathlib's
    separator handling depends on the host OS, which matters if a results
    CSV with Windows paths is ever analyzed from a non-Windows machine).
    """
    normalized = str(path_str).replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name


NUM = r"(\d+(?:\.\d+)?)"
TRIM_RE = re.compile(rf"^{NUM}Trim$", re.IGNORECASE)
# Fore-aft diameter pair, e.g. "4-6" or "3.5-6.5" -- aft is whatever the
# following component's own diameter already is, not always literally 6", so
# both sides are captured generically rather than hardcoding "-6".
DIAM_RE = re.compile(rf"^{NUM}-{NUM}$")
TRANS_RE = re.compile(rf"^{NUM}Trans$", re.IGNORECASE)

TRAIT_COLUMNS = {
    "nosecone_trim_in": "Nosecone trim (in)",
    "transition_fore_diameter_in": "Transition fore diameter (in)",
    "transition_length_in": "Transition length (in)",
}


def parse_traits(stem: str, baseline_name: str) -> dict:
    """
    Split a .ork filename (without extension) into trade-study traits.
    See module docstring for the assumed convention. Returns a dict; any
    trait not present in the filename is None. `unparsed_segments` collects
    anything that didn't match a known numeric pattern and wasn't consumed
    as the section label, so you can spot-check parsing quality.
    """
    segments = [s for s in stem.split("_") if s != ""]
    base = segments[0] if segments else stem
    rest = segments[1:]

    traits = {
        "rocket_base": base,
        "is_baseline": base.strip().lower() == baseline_name.strip().lower() and not rest,
        "nosecone_trim_in": None,
        "transition_fore_diameter_in": None,
        "transition_aft_diameter_in": None,
        "transition_length_in": None,
        "section": None,
        "unparsed_segments": "",
    }

    section_candidates = []
    for seg in rest:
        m = TRIM_RE.match(seg)
        if m:
            traits["nosecone_trim_in"] = float(m.group(1))
            continue
        m = DIAM_RE.match(seg)
        if m:
            traits["transition_fore_diameter_in"] = float(m.group(1))
            traits["transition_aft_diameter_in"] = float(m.group(2))
            continue
        m = TRANS_RE.match(seg)
        if m:
            traits["transition_length_in"] = float(m.group(1))
            continue
        # Doesn't match a known numeric pattern -- treat as the section label.
        section_candidates.append(seg)

    if section_candidates:
        traits["section"] = section_candidates[0]
        if len(section_candidates) > 1:
            traits["unparsed_segments"] = ", ".join(section_candidates[1:])

    return traits


def make_variant_label(rocket_base: str, is_baseline: bool, stem: str) -> str:
    """
    A short, human-readable, per-row-unique label for charts. All variants in
    a trade study typically share the same rocket_base (e.g. "SolInvictus"),
    so labeling bars/points with rocket_base alone would make every row look
    identical -- this strips that common prefix and shows just the modified
    segments instead.
    """
    if is_baseline:
        return f"{rocket_base} (baseline)"
    prefix = rocket_base + "_"
    if stem.startswith(prefix) and len(stem) > len(prefix):
        return stem[len(prefix):]
    return stem


def load_results(results_csv: Path) -> pd.DataFrame:
    df = pd.read_csv(results_csv)
    required = {"source_file", "status"}
    missing = required - set(df.columns)
    if missing:
        print(f"ERROR: results CSV is missing expected column(s): {missing}", file=sys.stderr)
        sys.exit(1)

    bad = df[df["status"] != "OK"]
    if not bad.empty:
        print(f"Skipping {len(bad)} row(s) that didn't complete successfully (status != OK):")
        for _, row in bad.iterrows():
            print(f"    {row['source_file']}  (status={row['status']})")
    df = df[df["status"] == "OK"].copy()
    if df.empty:
        print("ERROR: no successful (status=OK) rows to analyze.", file=sys.stderr)
        sys.exit(1)
    return df


def add_trait_columns(df: pd.DataFrame, baseline_name: str) -> pd.DataFrame:
    df = df.copy()
    df["stem"] = df["source_file"].apply(filename_stem)
    parsed = df["stem"].apply(lambda s: parse_traits(s, baseline_name))
    parsed_df = pd.DataFrame(list(parsed), index=df.index)
    df = pd.concat([df, parsed_df], axis=1)
    df["variant_label"] = df.apply(
        lambda r: make_variant_label(r["rocket_base"], r["is_baseline"], r["stem"]), axis=1
    )
    return df


def aggregate_per_file(df: pd.DataFrame, metric: str, agg: str) -> pd.DataFrame:
    """
    Collapse multiple simulations for the same source file down to one row
    per file, using the requested aggregation, for the ranking/trait plots.
    Non-numeric/trait columns are taken from the first row (they're constant
    per file).
    """
    sim_counts = df.groupby("source_file")["source_file"].transform("count")
    df = df.assign(_sim_count=sim_counts)

    agg_funcs = {"mean": "mean", "max": "max", "min": "min"}
    if agg not in agg_funcs:
        print(f"ERROR: --agg must be one of {list(agg_funcs)}", file=sys.stderr)
        sys.exit(1)

    metric_agg = df.groupby("source_file")[metric].agg(agg_funcs[agg]).rename(f"{metric}_{agg}")

    static_cols = [
        "source_file", "stem", "rocket_base", "variant_label", "is_baseline",
        "nosecone_trim_in", "transition_fore_diameter_in", "transition_aft_diameter_in", "transition_length_in",
        "section", "unparsed_segments", "_sim_count",
    ]
    first_rows = df.groupby("source_file", as_index=False).first()[static_cols]

    result = first_rows.merge(metric_agg, on="source_file")
    return result


def make_ranking_chart(agg_df: pd.DataFrame, metric_label: str, metric_col: str, out_path: Path,
                        baseline_name: str = "baseline", diverging: bool = False):
    """
    diverging=True colors bars by sign (e.g. when metric_col is a delta vs
    baseline) instead of a flat "modified" color -- makes small differences
    much easier to see at a glance than uniformly-colored bars would.
    """
    ranked = agg_df.sort_values(metric_col, ascending=True)

    def bar_color(is_baseline, value):
        if is_baseline:
            return COLOR_BASELINE
        if diverging:
            return COLOR_INCREASE if value >= 0 else COLOR_DECREASE
        return COLOR_MODIFIED

    colors = [bar_color(b, v) for b, v in zip(ranked["is_baseline"], ranked[metric_col])]

    height = max(4, 0.28 * len(ranked) + 1.2)
    fig, ax = plt.subplots(figsize=(9, height))
    fig.patch.set_facecolor(COLOR_SURFACE)
    ax.set_facecolor(COLOR_SURFACE)

    ax.barh(ranked["variant_label"] + ranked["_label_suffix"], ranked[metric_col], color=colors, height=0.65)

    ax.set_xlabel(metric_label, color=COLOR_INK)
    ax.set_title(f"Rocket ranking by {metric_label}", color=COLOR_INK, fontsize=13, loc="left")
    ax.tick_params(colors=COLOR_INK_SECONDARY, labelsize=8)
    ax.grid(axis="x", color=COLOR_GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for spine in ["top", "right", "left"]:
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(COLOR_MUTED)

    baseline_rows = ranked[ranked["is_baseline"]]
    if not baseline_rows.empty:
        baseline_val = baseline_rows[metric_col].iloc[0]
        ax.axvline(baseline_val, color=COLOR_BASELINE_LINE, linestyle="--", linewidth=1, zorder=1)

    # Give bars a little breathing room past the min/max so end-of-bar labels
    # (if ever added) and the baseline line aren't flush against the frame.
    span = ranked[metric_col].max() - ranked[metric_col].min()
    pad = span * 0.08 if span > 0 else 1
    ax.set_xlim(ranked[metric_col].min() - pad, ranked[metric_col].max() + pad)

    from matplotlib.patches import Patch
    if diverging:
        handles = [
            Patch(facecolor=COLOR_INCREASE, label="Higher than baseline"),
            Patch(facecolor=COLOR_DECREASE, label="Lower than baseline"),
            Patch(facecolor=COLOR_BASELINE, label=f"Baseline ({baseline_name})"),
        ]
    else:
        handles = [
            Patch(facecolor=COLOR_MODIFIED, label="Modified rocket"),
            Patch(facecolor=COLOR_BASELINE, label=f"Baseline ({baseline_name})"),
        ]
    ax.legend(handles=handles, loc="lower right", frameon=False, fontsize=8, labelcolor=COLOR_INK_SECONDARY)

    fig.tight_layout()
    fig.savefig(out_path, dpi=150, facecolor=COLOR_SURFACE)
    plt.close(fig)


def make_trait_scatter(agg_df: pd.DataFrame, trait_col: str, trait_label: str, metric_label: str, metric_col: str, out_path: Path):
    subset = agg_df[agg_df[trait_col].notna()]
    if subset.empty:
        return False

    fig, ax = plt.subplots(figsize=(9, 5.5))
    fig.patch.set_facecolor(COLOR_SURFACE)
    ax.set_facecolor(COLOR_SURFACE)

    ax.scatter(subset[trait_col], subset[metric_col], color=COLOR_MODIFIED, s=60, zorder=3, edgecolor="white", linewidth=0.5)

    baseline_rows = agg_df[agg_df["is_baseline"]]
    if not baseline_rows.empty:
        baseline_val = baseline_rows[metric_col].iloc[0]
        ax.axhline(baseline_val, color=COLOR_BASELINE_LINE, linestyle="--", linewidth=1, zorder=1, label="Baseline")
        ax.scatter([], [], color=COLOR_BASELINE, s=60, label="Baseline")

    for _, row in subset.iterrows():
        ax.annotate(row["variant_label"], (row[trait_col], row[metric_col]),
                    fontsize=7, color=COLOR_INK_SECONDARY, xytext=(4, 4), textcoords="offset points")

    ax.set_xlabel(trait_label, color=COLOR_INK)
    ax.set_ylabel(metric_label, color=COLOR_INK)
    ax.set_title(f"{trait_label}: effect on outcome", color=COLOR_INK, fontsize=13, loc="left")
    ax.tick_params(colors=COLOR_INK_SECONDARY, labelsize=8)
    ax.grid(color=COLOR_GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color(COLOR_MUTED)
    ax.spines["bottom"].set_color(COLOR_MUTED)
    ax.legend(frameon=False, fontsize=8, labelcolor=COLOR_INK_SECONDARY)

    # Zoom the y-axis tightly to the data (plus baseline, if shown) rather
    # than leaving matplotlib's wider default margins, so small differences
    # between points don't get visually flattened.
    y_values = list(subset[metric_col])
    if not baseline_rows.empty:
        y_values.append(baseline_rows[metric_col].iloc[0])
    y_span = max(y_values) - min(y_values)
    y_pad = y_span * 0.12 if y_span > 0 else max(abs(min(y_values)), 1) * 0.05
    ax.set_ylim(min(y_values) - y_pad, max(y_values) + y_pad)

    fig.tight_layout()
    fig.savefig(out_path, dpi=150, facecolor=COLOR_SURFACE)
    plt.close(fig)
    return True


def make_section_chart(agg_df: pd.DataFrame, metric_label: str, metric_col: str, out_path: Path):
    subset = agg_df[agg_df["section"].notna()]
    if subset.empty:
        return False

    groups = subset.groupby("section")[metric_col].apply(list).sort_index()

    fig, ax = plt.subplots(figsize=(max(6, 0.9 * len(groups) + 2), 5))
    fig.patch.set_facecolor(COLOR_SURFACE)
    ax.set_facecolor(COLOR_SURFACE)

    positions = range(len(groups))
    bp = ax.boxplot(groups.values, positions=list(positions), widths=0.5, patch_artist=True,
                     medianprops=dict(color=COLOR_INK), boxprops=dict(facecolor=COLOR_MODIFIED, edgecolor=COLOR_INK, alpha=0.85),
                     whiskerprops=dict(color=COLOR_MUTED), capprops=dict(color=COLOR_MUTED),
                     flierprops=dict(markeredgecolor=COLOR_MUTED, markersize=4))

    baseline_rows = agg_df[agg_df["is_baseline"]]
    if not baseline_rows.empty:
        baseline_val = baseline_rows[metric_col].iloc[0]
        ax.axhline(baseline_val, color=COLOR_BASELINE_LINE, linestyle="--", linewidth=1, zorder=1, label="Baseline")
        ax.legend(frameon=False, fontsize=8, labelcolor=COLOR_INK_SECONDARY)

    ax.set_xticks(list(positions))
    ax.set_xticklabels(groups.index, rotation=30, ha="right", fontsize=8, color=COLOR_INK_SECONDARY)
    ax.set_ylabel(metric_label, color=COLOR_INK)
    ax.set_title("Transition placement: effect on outcome", color=COLOR_INK, fontsize=13, loc="left")
    ax.tick_params(colors=COLOR_INK_SECONDARY, labelsize=8)
    ax.grid(axis="y", color=COLOR_GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color(COLOR_MUTED)
    ax.spines["bottom"].set_color(COLOR_MUTED)

    all_values = [v for vals in groups.values for v in vals]
    if not baseline_rows.empty:
        all_values.append(baseline_rows[metric_col].iloc[0])
    y_span = max(all_values) - min(all_values)
    y_pad = y_span * 0.12 if y_span > 0 else max(abs(min(all_values)), 1) * 0.05
    ax.set_ylim(min(all_values) - y_pad, max(all_values) + y_pad)

    fig.tight_layout()
    fig.savefig(out_path, dpi=150, facecolor=COLOR_SURFACE)
    plt.close(fig)
    return True


def main():
    parser = argparse.ArgumentParser(description="Rank and plot OpenRocket trade-study results by trait and apogee.")
    parser.add_argument("--results-csv", required=True, help="Path to the CSV produced by batch_simulate.py")
    parser.add_argument("--output-dir", default="trade_study_analysis", help="Folder to write CSVs and charts into")
    parser.add_argument("--metric", default="apogee_ft", choices=[
        "apogee_ft", "apogee_m", "max_velocity_mph", "max_velocity_ms",
        "max_acceleration_g", "max_acceleration_ms2", "time_to_apogee_s", "flight_time_s",
    ], help="Which column to rank/plot by (default: apogee_ft)")
    parser.add_argument("--agg", default="mean", choices=["mean", "max", "min"],
                         help="How to combine multiple simulations per file for ranking/trait plots (default: mean)")
    parser.add_argument("--baseline-name", default="SolInvictus", help="Filename stem (no segments) identifying the unmodified baseline")
    parser.add_argument("--scale", default="auto", choices=["auto", "delta", "absolute"],
                         help="Chart scaling: 'delta' plots change vs baseline (default when a baseline is found -- "
                              "makes small differences much easier to see than raw values), 'absolute' plots raw "
                              "metric values, 'auto' picks delta if a baseline is found, else absolute")
    args = parser.parse_args()

    results_csv = Path(args.results_csv).expanduser().resolve()
    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    df = load_results(results_csv)
    df = add_trait_columns(df, args.baseline_name)

    debug_cols = [
        "source_file", "stem", "rocket_base", "is_baseline",
        "nosecone_trim_in", "transition_fore_diameter_in", "transition_aft_diameter_in", "transition_length_in",
        "section", "unparsed_segments",
    ]
    debug_path = out_dir / "parsed_traits_debug.csv"
    df[debug_cols].drop_duplicates(subset=["source_file"]).to_csv(debug_path, index=False)
    print(f"Wrote {debug_path} -- check this first to confirm filename parsing looks right.")

    unparsed = df[df["unparsed_segments"] != ""]
    if not unparsed.empty:
        print(f"\nWARNING: {unparsed['source_file'].nunique()} file(s) had leftover segments that weren't classified as trim/diameter/transition/section.")
        print("These extra segments were folded into 'unparsed_segments' in the debug CSV -- check them.")

    if not df["is_baseline"].any():
        print(f"\nWARNING: no file matched the baseline name '{args.baseline_name}' exactly (with no other segments). "
              f"Delta-vs-baseline columns will be blank. Pass --baseline-name if it's spelled differently.")

    metric_col = args.metric
    metric_label = metric_col.replace("_", " ")

    # --- Overall per-simulation ranking (full detail, not aggregated) ---
    overall_cols = [
        "source_file", "simulation_index", "simulation_name", "rocket_base", "variant_label", "is_baseline",
        "nosecone_trim_in", "transition_fore_diameter_in", "transition_aft_diameter_in", "transition_length_in", "section",
        metric_col, "apogee_ft", "apogee_m", "max_velocity_mph", "max_acceleration_g",
        "time_to_apogee_s", "flight_time_s",
    ]
    seen = set()
    overall_cols = [c for c in overall_cols if c in df.columns and not (c in seen or seen.add(c))]
    overall = df[overall_cols].sort_values(metric_col, ascending=False)

    baseline_metric = None
    baseline_rows_full = df[df["is_baseline"]]
    if not baseline_rows_full.empty:
        baseline_metric = baseline_rows_full[metric_col].mean()
        overall = overall.copy()
        overall["delta_vs_baseline"] = overall[metric_col] - baseline_metric
        overall["delta_vs_baseline_pct"] = (overall[metric_col] - baseline_metric) / baseline_metric * 100

    overall_path = out_dir / "overall_ranking.csv"
    overall.to_csv(overall_path, index=False)
    print(f"Wrote {overall_path}")

    # --- Aggregate per file for plots (collapses multiple sims per file) ---
    agg_df = aggregate_per_file(df, metric_col, args.agg)
    agg_metric_col = f"{metric_col}_{args.agg}"
    agg_df["_label_suffix"] = agg_df["_sim_count"].apply(lambda n: "" if n <= 1 else f" (n={n})")

    if baseline_metric is not None:
        agg_df["delta_vs_baseline"] = agg_df[agg_metric_col] - baseline_metric
        agg_df["delta_vs_baseline_pct"] = (agg_df[agg_metric_col] - baseline_metric) / baseline_metric * 100

    per_file_path = out_dir / f"per_file_ranking_{args.agg}.csv"
    agg_df.sort_values(agg_metric_col, ascending=False).to_csv(per_file_path, index=False)
    print(f"Wrote {per_file_path}")

    # --- Sorted-by-trait CSVs ---
    for trait_col, trait_label in TRAIT_COLUMNS.items():
        trait_subset = agg_df[agg_df[trait_col].notna()].sort_values([trait_col, agg_metric_col], ascending=[True, False])
        if trait_subset.empty:
            continue
        trait_path = out_dir / f"sorted_by_{trait_col}.csv"
        trait_subset.to_csv(trait_path, index=False)
        print(f"Wrote {trait_path}")

    if agg_df["section"].notna().any():
        section_subset = agg_df[agg_df["section"].notna()].sort_values(["section", agg_metric_col], ascending=[True, False])
        section_path = out_dir / "sorted_by_section.csv"
        section_subset.to_csv(section_path, index=False)
        print(f"Wrote {section_path}")

    # --- Charts ---
    agg_note = f" ({args.agg} of {agg_df['_sim_count'].max()} sim(s)/file)" if agg_df["_sim_count"].max() > 1 else ""

    use_delta = baseline_metric is not None and args.scale != "absolute"
    if args.scale == "delta" and baseline_metric is None:
        print("\nWARNING: --scale delta requested but no baseline was found; falling back to absolute values.")

    if use_delta:
        plot_col = "delta_vs_baseline"
        plot_label = f"Δ {metric_label} vs baseline{agg_note}"
    else:
        plot_col = agg_metric_col
        plot_label = f"{metric_label}{agg_note}"

    make_ranking_chart(agg_df, plot_label, plot_col, out_dir / "ranking_overall.png",
                        baseline_name=args.baseline_name, diverging=use_delta)
    print(f"Wrote {out_dir / 'ranking_overall.png'}")

    for trait_col, trait_label in TRAIT_COLUMNS.items():
        out_path = out_dir / f"trait_{trait_col}.png"
        if make_trait_scatter(agg_df, trait_col, trait_label, plot_label, plot_col, out_path):
            print(f"Wrote {out_path}")

    section_out = out_dir / "trait_section.png"
    if make_section_chart(agg_df, plot_label, plot_col, section_out):
        print(f"Wrote {section_out}")

    # --- Console summary ---
    print("\n--- Top 5 by", metric_label, "---")
    print(overall.head(5)[[c for c in ["source_file", "simulation_name", metric_col, "delta_vs_baseline_pct"] if c in overall.columns]].to_string(index=False))
    print("\n--- Bottom 5 by", metric_label, "---")
    print(overall.tail(5)[[c for c in ["source_file", "simulation_name", metric_col, "delta_vs_baseline_pct"] if c in overall.columns]].to_string(index=False))
    if baseline_metric is not None:
        print(f"\nBaseline ({args.baseline_name}) {metric_label}: {baseline_metric:.2f}")

    print(f"\nAll output written to: {out_dir}")


if __name__ == "__main__":
    main()

fin_plots.py
============

Turns an OpenRocket fin-sweep results CSV into a set of fin geometry
envelope plots, a best-apogee map, and a short text summary.


REQUIREMENTS
------------
Python 3 with numpy, pandas, matplotlib and scipy:

    pip install numpy pandas matplotlib scipy


QUICK START
-----------
    python fin_plots.py sim_results.csv

Plots are written to a folder named after the CSV
(e.g. sim_results_plots/). Use -o to choose a different folder.


INPUT
-----
The batch-export CSV with at least these columns:
    source_file, apogee_ft, min_stability_cal, max_stability_cal
(simulation_name and status are used if present).

Fin geometry is read from each .ork file name, for example:
    ..._root-15.25in_tip-2in_height-6.5in_sweep-16in.ork

    height = fin span, sweep = leading-edge sweep distance.
    root and tip are optional. A file with only height and sweep is
    treated as a single planform (label it with --root and --tip).

If the file has several sim conditions (worst / average / no wind),
only the worst-case sims are used unless --condition says otherwise.
Sims whose status is not OK are skipped.


OUTPUT
------
report_envelope_root-R_tip-T.png
    Full-size envelope for one root/tip pair: per-sim apogee cells,
    apogee isobars, both stability limits, pass/fail markers and the
    highest-apogee feasible point. Defaults to the best pair.

optimum_map.png
    Best-apogee map (annotated heatmap). One cell per root/tip pair
    with its best apogee, span (h) and sweep (w).

master_trellis.png
    Every root/tip envelope nested in one root x tip grid, no isobars.

envelopes_tip-T.png
    Full envelopes with isobars for every root, one figure per tip.

best_per_root_tip.csv
    Best design for each root/tip pair and its number of passing sims.

summary.txt
    Tested ranges, fit quality, the best design and the partial
    derivatives of apogee at that design.

The map, trellis and per-tip figures are only made when the file
contains more than one root/tip pair.


OPTIONS
-------
-o, --outdir DIR       output folder
--condition NAME       sim condition to use (default: worst)
--under CAL            minimum stability limit in calibers (default 2.47)
--over CAL             maximum stability limit in calibers (default 6.85)
--under-label TEXT     legend label for the minimum limit (default 9%)
--over-label TEXT      legend label for the maximum limit (default 25%)
--root IN, --tip IN    root/tip pair for the report plot, or the label
                       for a single-planform file
--isobar-step FT       spacing between isobars (default: automatic)
--subtitle TEXT        subtitle for the report plot
-h, --help             list all options

Examples:
    python fin_plots.py results.csv -o plots
    python fin_plots.py results.csv --root 16.25 --tip 2 --isobar-step 200
    python fin_plots.py results.csv --subtitle "Root 16.25 in, Tip 2 in, 3 in above fin can"


HOW IT WORKS
------------
1. Each output (apogee, min stability, max stability) is fitted with a
   polynomial in the fin parameters that vary in the file. The degree
   (straight line up to cubic) is chosen automatically by
   cross-validation.

2. The stability limits on the plots are where the fitted min and max
   stability equal the --under and --over values. The shaded region
   between them is the allowed design space.

3. For each root/tip pair, the best design is the highest fitted
   apogee inside the tested span and sweep range that stays within
   both limits.

4. A * next to a best point means it sits on the edge of the tested
   span/sweep range, so the true best design lies just outside what
   was simulated. Extending the sweep in that direction will find it.


NOTES
-----
- The best point comes from the fit and may fall between simulated
  grid points. Run one confirming sim at that exact geometry before
  finalizing.
- The partial derivatives in summary.txt change one parameter at a
  time with the others held fixed. They ignore stability, so they
  describe sensitivity, not a change that keeps the design in the
  allowed window.
- Fits are only valid inside the tested ranges.

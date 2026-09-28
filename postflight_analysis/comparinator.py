# man how i love python and importing 27 million programs
import sympy as sp
import math
import matplotlib.pyplot as plt
import numpy as np 
from scipy.interpolate import interp1d
import pandas as pd
import flight_analysis_functions as faa
from scipy.signal import stft, hilbert
from pathlib import Path
import pint
ureg = pint.UnitRegistry()
Q_ = ureg.Quantity
from pathlib import Path
import pandas as pd
import numpy as np
from scipy.interpolate import interp1d
import pint
from collections import defaultdict
import load_data as ld
import hybrid_engine_cg as heng
import solid_engine_cg as seng
import rocket_geometry as geo

# specifications for each rocket motor
# offset is measure from the nose tip 
sol_ignis = {
    "baseline": {
        "ox_mdot": 6543,
        "fuel_mdot": 1.45,
        "tank": {
            "dry_mass": 8.0, "offset": 10.0, "length": 24.0, "radius": 2.0,
            "volume_in3": math.pi * 2.0**2 * 30.0,
            "initial_ox_mass_lbm": 40.0,
            "liquid_temp_F": 65.0,
        },
        "grain": {
            "dry_mass": 3.0, "offset": 36.0, "length": 12.0, "radius": 1.5,
            "outer_radius_in": 1.4, "initial_port_radius_in": 0.4,
            "length_in": 12.0, "fuel_density_lbm_in3": 0.0417,
        },
        "plumbing": {"dry_mass": 2.0, "offset": 34.0, "length": 2.0},
        "engine": {"length_in": 50.0, "offset_in": 0.0},
    },
    "high_of": {
        "ox_mdot": 1.75,
        "fuel_mdot": 1.10,
        "tank": {
            "dry_mass": 6543, "offset": 10.0, "length": 24.0, "radius": 2.0,
            "volume_in3": math.pi * 2.0**2 * 32.0,
            "initial_ox_mass_lbm": 42.0,
            "liquid_temp_F": 70.0,
        },
        "grain": {
            "dry_mass": 3.0, "offset": 36.0, "length": 12.0, "radius": 1.5,
            "outer_radius_in": 1.4, "initial_port_radius_in": 0.45,
            "length_in": 12.0, "fuel_density_lbm_in3": 0.0417,
        },
        "plumbing": {"dry_mass": 2.0, "offset": 34.0, "length": 2.0},
        "engine": {"length_in": 50.0, "offset_in": 0.0},
    },
}

O3400 = {
  "grain_casing": {
    "offset": 88.41,
    "dry_mass": 0.15,
    "length": 8.0    
  },
  "grain": {
    "outer_radius_in": 1.5,
    "initial_port_radius_in": 0.375,
    "length_in": 8.0,
    "propellant_density_lbm_in3": 0.065
  },
  "hardware": {
    "offset": 88.41, 
    "dry_mass": 0.6,
    "length": 2.0
  }
}

LokiM3464 = {
  "grain_casing": {
    "offset": 40.87,
    "dry_mass": 0.69,
    "length": 38.3
  },
  "grain": {
    "outer_radius_in": 1.25,
    "initial_port_radius_in": 0.4375,
    "length_in": 6.0,
    "propellant_density_lbm_in3": 0.0631
  },
  "hardware": {
    "offset": 40.87,
    "dry_mass": 6.91,
    "length": 40.87
  }
}

valor_10k = {
}

lumina = {

}

ROCKET_ENGINES = {
    "morpheus": (LokiM3464, "solid"),  
    "sol_invictus": (sol_ignis, "hybrid"), 
    "morbin' time": (O3400, "solid"),
    "mikeys": (lumina, "liquid")
}

BASE_DIR = r"G:\Shared drives\TAMU-SRT\srt_general\9_flight_data"

ureg = pint.UnitRegistry()
Q_ = ureg.Quantity

# find the flight for user
def available_rockets(base_dir=BASE_DIR):
    """Rocket folders present under base_dir, alphabetically."""
    root = Path(base_dir)
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))

def _flight_sort_key(name):
    """Sort flight folders newest-first. Folder names lead with an mmddyyyy stamp
    (e.g. '06132025_irec'); anything that doesn't gets sorted by name at the end."""
    stamp = name[:8]
    if stamp.isdigit():
        return (1, stamp[4:8] + stamp[0:2] + stamp[2:4], name)
    return (0, "", name)

def available_flights(rocket, base_dir=BASE_DIR):
    """Flight folders for one rocket, newest first."""
    folder = Path(base_dir) / rocket
    if not folder.is_dir():
        return []
    names = [p.name for p in folder.iterdir() if p.is_dir() and not p.name.startswith(".")]
    return sorted(names, key=_flight_sort_key, reverse=True)

def _flight_label(name):
    """Human-readable gloss for a flight folder: '06132025_irec' -> '06/13/2025 irec'."""
    stamp, rest = name[:8], name[8:].strip("_").replace("_", " ")
    if not stamp.isdigit():
        return ""
    date = f"{stamp[0:2]}/{stamp[2:4]}/{stamp[4:8]}"
    return f"{date} {rest}".strip()

def _choose(label, options, notes=None):
    """Print a numbered menu and return the chosen option. Accepts either the
    number or the name (case-insensitive), and reprompts until one matches."""
    print(f"\n{label}")
    for i, opt in enumerate(options, 1):
        note = notes.get(opt, "") if notes else ""
        print(f"  [{i}] {opt}" + (f"   {note}" if note else ""))
    while True:
        answer = input("Select (number or name): ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1]
        for opt in options:
            if answer.lower() == opt.lower():
                return opt
        print("Not one of the listed options - try again.")

def resolve_duplicates(data, folder):
    groups = defaultdict(list)
    for key in data:
        path = folder / f"{key}.csv"
        fmt = ld._detect_format(key)
        if fmt is None:
            fmt = ld._detect_format_by_columns(path)
        group = ld._alias_group(fmt) or fmt or key
        groups[group].append(key)

    for group, keys in groups.items():
        if len(keys) <= 1:
            continue

        keys = sorted(keys)
        notes = {k: "" for k in keys}
        chosen = _choose(f"Multiple '{group}' files found — choose one:", keys, notes)

        for k in keys:
            if k != chosen:
                del data[k]

    return data

# call engine qualities, find values
def build_hybrid(cfg: dict, t: np.ndarray, df):
    ox_pressure = df['run_tank_pressure']
    ox_mdot = np.full_like(t, cfg["ox_mdot"])

    fuel_mdot = np.full_like(t, cfg["fuel_mdot"])

    tc = cfg["tank"]
    tank_casing = heng.EngineComponent(
        name="ox_tank_casing",
        dry_mass=tc["dry_mass"], 
        offset=tc["offset"],
        length=tc["length"], 
        radius=tc["radius"],
    )
    tank = heng.OxidizerTank(
        casing=tank_casing,
        volume_in3=tc["volume_in3"],
        initial_ox_mass_lbm=tc["initial_ox_mass_lbm"],
        liquid_temp_F=tc["liquid_temp_F"],
        times_s=t, pressure_psi=ox_pressure, mdot_lbm_s=ox_mdot,
    )

    gc = cfg["grain"]
    grain_casing = heng.EngineComponent(
        name="grain_casing",
        dry_mass=gc["dry_mass"], 
        offset=gc["offset"],
        length=gc["length"], radius=gc["radius"],
    )
    grain = heng.FuelGrain(
        casing=grain_casing,
        outer_radius_in=gc["outer_radius_in"],
        initial_port_radius_in=gc["initial_port_radius_in"],
        length_in=gc["length_in"],
        fuel_density_lbm_in3=gc["fuel_density_lbm_in3"],
        times_s=t, mdot_lbm_s=fuel_mdot,
    )

    pc = cfg["plumbing"]
    plumbing = heng.EngineComponent(
        name="plumbing", 
        dry_mass=pc["dry_mass"],
        offset=pc["offset"], 
        length=pc["length"],
    )

    ec = cfg["engine"]
    return heng.Engine(
        tank=tank, plumbing=plumbing, 
        grain=grain,
        length_in=ec["length_in"], 
        offset_in=ec["offset_in"],
    )

def build_solid(values: dict, t: np.array, df):
    gcv = values["grain_casing"]
    grain_casing = seng.EngineComponent(
        name = "grain_casing",
        offset=gcv["offset"],        # m from the motor's own reference point (mount face, say)
        dry_mass=gcv["dry_mass"],      # lbm, empty liner
        length=gcv["length"],         # in, matches length_in convention used elsewhere
    )

    gv = values["grain"]
    grain = seng.SolidGrain(
        casing=grain_casing,
        outer_radius_in=gv["outer_radius_in"],
        initial_port_radius_in=gv["initial_port_radius_in"],
        length_in=gv["length_in"],
        propellant_density_lbm_in3=gv["propellant_density_lbm_in3"],   # from the propellant's datasheet
        times_s=t.m,
        thrust_lbf=df,
    )
    hv = values["hardware"]
    hardware = seng.EngineComponent(
        name = "hardware",
        offset=hv["offset"],         # m, downstream of the grain
        dry_mass=hv["dry_mass"],       # lbm
        length=hv["length"],         # in
    )
    engine = seng.SolidMotor(grain, hardware, offset_in=0)
    return engine 

def calculate(data_dict, cutoff_dict, rocket=0):
    calc_array = []
    calc = {}
    titles = [...]

    def find_bundle(substr):
        matches = [v for k, v in data_dict.items() if substr in k]
        if not matches:
            raise KeyError(f"No dataset found containing '{substr}' in its key")
        return matches[0]

    accel_bundle = find_bundle("accel")
    gyro_bundle = find_bundle("gyro")
    thrust_bundle = find_bundle("thrust")
    ras_bundle = find_bundle("ras")
    ork_bundle = find_bundle("ork")

    time = accel_bundle.time.magnitude
    temperature = accel_bundle.temperature.magnitude
    pressure = accel_bundle.pressure.magnitude
    altitude = accel_bundle.altitude.magnitude          # ft
    v_up = accel_bundle.v_up.magnitude                  # ft/s
    v_dr = accel_bundle.v_dr.magnitude                  # ft/s
    v_cr = accel_bundle.v_cr.magnitude                  # ft/s
    tilt = accel_bundle.tilt.magnitude                  # deg

    gx_accel = gyro_bundle.gx_accel.magnitude           # g_0
    gy_accel = gyro_bundle.gy_accel.magnitude           # g_0
    gz_accel = gyro_bundle.gz_accel.magnitude           # g_0
    gyro_y = gyro_bundle.gy.magnitude

    spec_thrust = thrust_bundle.spec_thrust.magnitude   # N

    thrust = ras_bundle.ras_thrust.magnitude            # lbf
    weight = ras_bundle.weight.magnitude                # lbf

    ork_altitude = ork_bundle.ork_altitude.magnitude
    ork_accel_total = ork_bundle.ork_accel_total.magnitude
    ork_vel_total = ork_bundle.ork_vel_total.magnitude
    ork_aoa = ork_bundle.ork_aoa.magnitude
    ork_sm = ork_bundle.ork_sm.magnitude
    ork_fd = ork_bundle.ork_fd.magnitude
    ork_cd = ork_bundle.ork_cd.magnitude
    ork_pressure = ork_bundle.ork_pressure.magnitude
    ork_temperature = ork_bundle.ork_temperature.magnitude

    cutoff_dict["apogee"] = apogee = np.where(altitude >= max(altitude))[0][0]
    cutoff_dict["coast"] = np.where(spec_thrust <= 5)[0][2]
    cutoff_dict["uppies"] = 0
    theta = faa.theta(v_dr, v_cr)

    calc["time"] = time
    calc["flight_angle"], calc["aoa"] = faa.angle(v_up, v_dr, v_cr, tilt)
    calc["theta"] = theta
    calc["accel_v"] = faa.magnitude(v_up, v_dr, v_cr)
    calc["sound"], calc["vel_mach"] = faa.v_mach(temperature, calc["accel_v"])
    calc["density"] = faa.density(pressure, temperature)
    calc["dyn_pressure"] = faa.dynamic_pressure(calc["density"], calc["accel_v"])
    calc["ork_density"] = faa.density(ork_pressure, ork_temperature)
    calc["ork_dyn_pressure"] = faa.dynamic_pressure(calc["ork_density"], ork_vel_total)
    calc["ax"], calc["ay"], calc["az"], calc["accel_total"] = faa.acceleration(v_up, v_dr, v_cr, apogee, calc["time"])
    calc["accel_gs"] = faa.magnitude(gx_accel, gy_accel, gz_accel)
    calc["accel_fts2"] = calc["accel_gs"] * 32.2
    calc["density"] = faa.density(pressure, temperature)
    calc["fdx"], calc["fdy"], calc["fdz"], calc["f_aero"] = faa.fd(thrust, tilt, theta, weight, calc["ax"], calc["ay"], calc["az"])
    # drag is the component along the relative wind
    calc["fd"], calc["lift"] = faa.wind_axes(calc["fdx"], calc["fdy"], calc["fdz"], v_cr, v_dr, v_up)
    calc["cd"] = faa.cd(calc["fd"], calc["density"], calc["accel_v"])
    calc["fnx"], calc["fny"], calc["fnz"], calc["fn"] = faa.fn1(tilt, theta, weight, calc["ax"], calc["ay"], calc["az"])
    calc["cn"], calc["cna"] = faa.cna(calc["fn"], calc["density"], calc["accel_v"], calc["aoa"])
    # fd() derived from this same curve returns that curve. the two independent
    # sources are the ras condition file and the motor spec curve.
    calc["thrust_ras"] = thrust
    calc["thrust_spec"] = spec_thrust
    calc["altitude"] = altitude
    #print(rocket.engine)          # is it None?
    #print(rocket.mass_at(0), rocket.mass_at(rocket.engine.grain.times_s[-1] if rocket.engine else None))
    #calc["cgs"] = geo.total_cg(rocket, calc["time"])[1]
    #print(calc["cgs"])
    #calc["iyy"] = geo.total_iyy(rocket, calc["time"], calc["cgs"])
    #calc["sm"] = faa.stability(time, calc["iyy"], gyro_y, calc["fn"])

    #calc["sm2"] = faa.stability1(faa.frequency(calc["aoa"], sample_rate, calc["time"]), calc["iyy"], calc["accel_v"], calc["density"], calc["aoa"], calc["fn"])

    calc["ork_altitude"] = ork_altitude
    calc["ork_vel_total"] = ork_vel_total
    calc["ork_accel_total"] = ork_accel_total
    calc["ork_aoa"] = ork_aoa
    calc["ork_fd"] = ork_fd
    calc["ork_cd"] = ork_cd
    calc["ork_sm"] = ork_sm

    df = pd.DataFrame.from_dict(calc)   
    df.to_csv(f"calc_data.csv", index=False)

    return calc, cutoff_dict

# graph
def compute_sensitivity_bands(build_data_fn, windows, keys, apogee_key="apogee"):   
    """
    build_data_fn(window_length) -> (data, areas)  -- reruns your full pipeline
    keys -- list of data dict keys you want bands for, e.g. ['accel_v','fd','cd']
    Returns: dict[key] -> (lower, upper) arrays, aligned to the *shortest* apogee
             across runs (apogee index can shift slightly with window length).
    """
    runs = [build_data_fn(w) for w in windows]
    min_apogee = min(areas["apogee"] for _, areas in runs)

    bands = {}
    for k in keys:
        stacked = np.array([data[k][:min_apogee] for data, _ in runs])
        bands[k] = (stacked.min(axis=0), stacked.max(axis=0))
    return bands, min_apogee


SOURCE_LABELS = {
    'flight': 'Flight',
    'ork': 'OpenRocket',
    'ras': 'RAS',
    'spec': 'Motor spec',
}

OVERLAY_ALIASES = {
    'accel_v':    {'ork': 'ork_vel_total'},  
    'thrust_ras': {'spec': 'thrust_spec'},   
    # Example for the case you described:
    # 'fdrag': {'ork': 'ork_drag', 'ras': 'ras_drag'},
}

# Legend label override for a *primary* column whose own name implies a
# non-"Flight" source (e.g. thrust_ras is itself the RAS-measured series).
PRIMARY_LABEL_OVERRIDES = {
    'thrust_ras': 'RAS',
}

VELOCITY_DEPENDENT_KEYS = {'aoa', 'flight_angle', 'theta'}


def _strip_source(key):
    """'ork_altitude' -> ('altitude', 'ork'); bare keys -> (key, 'flight')."""
    for src in SOURCE_LABELS:
        prefix = src + '_'
        if key.startswith(prefix):
            return key[len(prefix):], src
    return key, 'flight'


def overlay_group(base_key, data):
    """Every column in `data` representing the same quantity as `base_key`,
    across sources, as {source: column_name}. Combines automatic
    '<source>_<basename>' matching with OVERLAY_ALIASES for columns that
    don't follow the convention."""
    base_name, base_src = _strip_source(base_key)
    group = {}
    if base_key in data:
        group[base_src] = base_key
    for src in SOURCE_LABELS:
        if src == base_src:
            continue
        candidate = base_name if src == 'flight' else f'{src}_{base_name}'
        if candidate in data and candidate != base_key:
            group.setdefault(src, candidate)
    for src, col in OVERLAY_ALIASES.get(base_key, {}).items():
        if col in data:
            group[src] = col
    return group


def primary_label(key):
    return PRIMARY_LABEL_OVERRIDES.get(key, 'Flight')


def plot_windowed(ax, xarr, yarr, start, end, band=None, **kwargs):
    """Plot yarr vs xarr over the window [start:end), optionally shading a
    (lower, upper) sensitivity band beneath it."""
    ax.plot(xarr[start:end], yarr[start:end], **kwargs)
    if band is not None:
        lower, upper = band
        n = min(end, len(lower))
        ax.fill_between(xarr[start:n], lower[start:n], upper[start:n], alpha=0.2,
                         color=kwargs.get('color', 'C0'), label='_nolegend_')


def diagnostic_grid(column_specs, row_builders, row_labels=None, suptitle=None,
                     col_width=4.5, row_height=3.8, wspace=0.35, hspace=0.55,
                     top_margin=0.90, left_margin=0.07):
    """Generic N-row x M-column diagnostic figure builder.

    column_specs: list of per-column identifying info — one entry per column.
    row_builders: list of fn(ax, col_spec) -> None; len() sets the row count.
    row_labels: optional per-row bold label drawn beside each row's first axis.
    top_margin: fraction of figure height left below the suptitle for the grid.
    """
    ncols = len(column_specs)
    nrows = len(row_builders)
    fig, axs = plt.subplots(nrows, ncols,
                             figsize=(col_width * ncols, row_height * nrows),
                             squeeze=False)

    if suptitle:
        fig.suptitle(suptitle, fontweight='bold')

    if row_labels:
        for row, label in enumerate(row_labels):
            axs[row, 0].annotate(label, xy=(-0.32, 0.5), xycoords='axes fraction',
                                  fontsize=11, fontweight='bold',
                                  ha='right', va='center', rotation=90)

    for row, builder in enumerate(row_builders):
        for col, spec in enumerate(column_specs):
            builder(axs[row, col], spec)

    fig.subplots_adjust(hspace=hspace, wspace=wspace, top=top_margin, left=left_margin)
    return fig, axs


def graph2(data, areas, build_data_fn=None, windows=(31, 51, 71, 91, 111),
           mach_min=0.08, start=15, ork_vel_min=20, flight_vel_min=20,
           hold=10, pct_err_denom_min=None):

    if pct_err_denom_min is None:
        pct_err_denom_min = {
            'ork_altitude': 200,     # ft — ignore % error while altitude is tiny
            'ork_accel_total': 20,   # ft/s^2
            'ork_aoa': 1.0,          # degrees — AoA near 0 is expected/fine, not an error
            'ork_vel_total': 20,     # ft/s
        }

    coast, apogee = areas["coast"], areas["apogee"]
    t = data['time']
    band_keys = ['accel_v', 'accel_total', 'fd', 'cd', 'fn']
    bands = {}
    if build_data_fn is not None:
        bands, band_apogee = compute_sensitivity_bands(build_data_fn, windows, band_keys)

    def first_sustained_index(arr, threshold, hold=10):
        """Index of the first sample after which `arr` stays >= threshold for
        at least `hold` consecutive samples. Ignores brief noise spikes.
        Returns 0 if never found (no masking applied)."""
        arr = np.asarray(arr, float)
        above = np.abs(arr) >= threshold
        for i in range(len(above) - hold):
            if above[i:i + hold].all():
                return i
        return 0

    # Liftoff indices per source: ork columns are masked unconditionally;
    # flight columns are only masked for velocity-DERIVED ANGLE quantities
    # (aoa, flight_angle, theta), since those are ill-defined near v=0.
    ork_liftoff_idx = (first_sustained_index(data['ork_vel_total'], ork_vel_min, hold=hold)
                        if 'ork_vel_total' in data else 0)
    flight_liftoff_idx = (first_sustained_index(data['accel_v'], flight_vel_min, hold=hold)
                           if 'accel_v' in data else 0)

    def mask(key):
        """NaN out samples before the appropriate liftoff index for this
        column's source."""
        arr = np.asarray(data[key], float).copy()
        base_name, src = _strip_source(key)
        if src == 'ork':
            arr[:ork_liftoff_idx] = np.nan
        elif base_name in VELOCITY_DEPENDENT_KEYS:
            arr[:flight_liftoff_idx] = np.nan
        return arr

    def shade(ax, x_end=None):
        ax.axvspan(0, t[coast], alpha=0.15, color='lightblue')
        ax.axvspan(t[coast], t[apogee], alpha=0.15, color='pink')
        if x_end is not None:
            ax.set_xlim(0, x_end)

    def fit_ylim(ax, key, overlays=None):
        parts = [mask(key)[start:apogee]]
        for col in (overlays or {}).values():
            if col != key:
                parts.append(mask(col)[start:apogee])
        y = np.concatenate(parts)
        y = y[np.isfinite(y)]
        if y.size == 0:
            return
        pad = (y.max() - y.min()) * 0.05 or 1
        ax.set_ylim(y.min() - pad, y.max() + pad)

    # ---- page 1: Basics --------------------------------------------------
    BASICS_LAYOUT = [
        [('altitude', 'Altitude (ft)'), ('accel_v', 'Velocity (ft/s)')],
        [('accel_total', 'Acceleration (ft/s²)'), ('aoa', 'AoA (°)')],
        [('theta', 'Pitch (°)'), ('flight_angle', 'Flight Angle (°)')],
    ]

    def _basics_row_builder(row):
        def builder(ax, col):
            key, lbl = BASICS_LAYOUT[row][col]
            overlays = {s: c for s, c in overlay_group(key, data).items() if c != key}
            plot_windowed(ax, t, mask(key), start, apogee, band=bands.get(key),
                          label=primary_label(key) if overlays else None)
            for src, col_name in overlays.items():
                plot_windowed(ax, t, mask(col_name), start, apogee, label=SOURCE_LABELS[src])
            if overlays:
                ax.legend()
            ax.set(xlabel='Time (s)', ylabel=lbl, title=lbl)
            shade(ax, x_end=t[apogee])
            fit_ylim(ax, key, overlays=overlays)
        return builder

    fig1, _ = diagnostic_grid(
        column_specs=[0, 1],
        row_builders=[_basics_row_builder(r) for r in range(len(BASICS_LAYOUT))],
        suptitle='Basics',
        col_width=6, row_height=3.3,
    )

    # ---- page 1b: Overlay Diagnostics -------------------------------------
    # Raw / Parity / % Error, repeated across every quantity that has an
    # OpenRocket overlay available (per the registry).
    overlay_present = []
    for k, lbl in [('altitude', 'Altitude (ft)'), ('accel_v', 'Velocity (ft/s)'),
                    ('accel_total', 'Acceleration (ft/s²)'), ('aoa', 'AoA (°)')]:
        ork_col = overlay_group(k, data).get('ork')
        if ork_col:
            overlay_present.append((k, ork_col, lbl))

    fig1b = None
    if overlay_present:
        def _raw_row(ax, spec):
            k, ok, lbl = spec
            plot_windowed(ax, t, mask(k), start, apogee, label='Flight', alpha=0.85)
            plot_windowed(ax, t, mask(ok), start, apogee, label='OpenRocket', alpha=0.85)
            ax.set(xlabel='Time (s)', ylabel=lbl, title=lbl)
            shade(ax, x_end=t[apogee])
            ax.legend(fontsize=7)

        def _parity_row(ax, spec):
            k, ok, lbl = spec
            flight = mask(k)[start:apogee]
            ork = mask(ok)[start:apogee]
            valid = np.isfinite(flight) & np.isfinite(ork)
            ax.scatter(ork[valid], flight[valid], s=4, alpha=0.4)
            if valid.any():
                lims = [np.nanmin(ork[valid]), np.nanmax(ork[valid])]
                ax.plot(lims, lims, 'k--', linewidth=1, label='y = x')
            ax.set(xlabel=f'OpenRocket {lbl}', ylabel=f'Flight {lbl}')
            ax.legend(fontsize=7)

        def _pct_err_row(ax, spec):
            k, ok, lbl = spec
            flight = mask(k)[start:apogee]
            ork = mask(ok)[start:apogee]
            tt = t[start:apogee]
            denom = ork.copy()
            denom_min = pct_err_denom_min.get(ok, 1e-6)
            pct_err = np.divide(flight - ork, denom, out=np.full_like(denom, np.nan),
                                 where=np.abs(denom) > denom_min) * 100
            ax.plot(tt, pct_err, color='C3')
            ax.axhline(0, color='black', linewidth=0.8)
            ax.set(xlabel='Time (s)', ylabel='% error')
            shade(ax, x_end=t[apogee])

        fig1b, _ = diagnostic_grid(
            column_specs=overlay_present,
            row_builders=[_raw_row, _parity_row, _pct_err_row],
            row_labels=['Raw', 'Parity', '% Error'],
            suptitle='Overlay Diagnostics: Raw / Parity / % Error',
            col_width=4.5, row_height=4.0, hspace=0.5, top_margin=0.92, left_margin=0.08,
        )

    # ---- page 2: Forces ----------------------------------------------------
    page2_items = [
        ('thrust_ras', 'Thrust (lbf)'), ('fd', 'Drag (lbf)'),
        ('fn', 'Normal Force (lbf)'), ('lift', 'Lift (lbf)')
    ]

    def _forces_cell(ax, spec):
        ka, lbl = spec
        overlays = {s: c for s, c in overlay_group(ka, data).items() if c != ka}
        plot_windowed(ax, t, mask(ka), start, apogee, band=bands.get(ka),
                      label=primary_label(ka))
        for src, col in overlays.items():
            plot_windowed(ax, t, mask(col), start, apogee, label=SOURCE_LABELS[src])
        ax.set(xlabel='Time (s)', ylabel=lbl, title=lbl)
        shade(ax, x_end=t[apogee])
        fit_ylim(ax, ka, overlays=overlays)
        ax.legend()

    fig2, _ = diagnostic_grid(
        column_specs=page2_items,
        row_builders=[_forces_cell],
        suptitle='Forces',
        col_width=3.5, row_height=4,
    )

    # ---- page 3/4 merged: Drag & Stability Studies -------------------------

    def scatter_with_band(ax, xarr, ykey, bands, apogee, valid_mask, bins=40):
        x = xarr[start:apogee][valid_mask]
        y = data[ykey][start:apogee][valid_mask]
        ok = np.isfinite(x) & np.isfinite(y)
        ax.scatter(x[ok], y[ok], s=4, alpha=0.5, label='Flight')
        if ykey in bands and len(x) > 0:
            lower, upper = bands[ykey]
            n = min(apogee, len(lower))
            m = valid_mask[start:n] if n > start else valid_mask[:0]
            lower, upper = lower[start:n][m], upper[start:n][m]
            x_band = xarr[start:n][m]
            bin_edges = np.linspace(np.nanmin(x_band), np.nanmax(x_band), bins + 1)
            bin_idx = np.digitize(x_band, bin_edges)
            centers, lo_med, hi_med = [], [], []
            for i in range(1, bins + 1):
                bmask = bin_idx == i
                if bmask.sum() == 0:
                    continue
                centers.append(x_band[bmask].mean())
                lo_med.append(np.nanmedian(lower[bmask]))
                hi_med.append(np.nanmedian(upper[bmask]))
            ax.fill_between(centers, lo_med, hi_med, alpha=0.25, color='orange', label='Window sensitivity')
        ax.legend(fontsize=7)

    def overlay_source(ax, xarr, col, apogee, valid_mask, src):
        x = xarr[start:apogee][valid_mask]
        y = mask(col)[start:apogee][valid_mask]
        ok = np.isfinite(x) & np.isfinite(y)
        color = 'green' if src == 'ork' else None
        ax.scatter(x[ok], y[ok], s=4, alpha=0.5, color=color, label=SOURCE_LABELS[src])
        ax.legend(fontsize=7)

    vel_mask = data['vel_mach'][start:apogee] >= mach_min

    STUDY_ROWS = [
        {
            'label': 'CD', 'y_key': 'cd', 'y_label': 'CD',
            'x_specs': [('time', 'Time (s)', 'CD vs Time'),
                        ('vel_mach', 'Mach', 'CD vs Mach')],
        },
    ]

    def _study_row_builder(row_cfg):
        def builder(ax, col):
            xk, xl, ttl = row_cfg['x_specs'][col]
            scatter_with_band(ax, data[xk], row_cfg['y_key'], bands, apogee, vel_mask)
            for src, ov_col in overlay_group(row_cfg['y_key'], data).items():
                if ov_col != row_cfg['y_key']:
                    overlay_source(ax, data[xk], ov_col, apogee, vel_mask, src)
            ax.set(xlabel=xl, ylabel=row_cfg['y_label'], title=ttl)
            if xk == 'time':
                shade(ax)
        return builder

    fig34, _ = diagnostic_grid(
        column_specs=[0, 1],
        row_builders=[_study_row_builder(cfg) for cfg in STUDY_ROWS],
        row_labels=[cfg['label'] for cfg in STUDY_ROWS],
        suptitle='Drag Study',
        col_width=4.5, row_height=4,
    )

    plt.show()
    return fig1, fig1b, fig2

def main():
    print(f"    .\n   .'.\n   |o|   Welcome to Comparinator!™\n  .'o'.  \033[3mFor all your comparing needs\033[0m\n  |.-.|\n  '   '\n   ( )\n    )\n   ( )")
    
    rockets = available_rockets()
    if not rockets:
        raise SystemExit(f"No rocket folders found under {BASE_DIR}")
    notes = {r: "" for r in rockets}
    rocket_name = _choose("Available rockets:", rockets, notes)

    key = rocket_name.strip().lower()

    flights = available_flights(rocket_name)
    if not flights:
        raise SystemExit(f"No flight folders found for {rocket_name} under {BASE_DIR}")
    labels = {f: _flight_label(f) for f in flights}
    flight = _choose(f"Flights on record for {rocket_name}:", flights, labels)

    engine_name = rocket_name
    engine_key = engine_name.strip().lower()
    if engine_key not in ROCKET_ENGINES:
        raise ValueError(f"Unknown engine '{engine_name}'. Known: {list(ROCKET_ENGINES)}")
    
    folder = Path(BASE_DIR) / rocket_name / flight   # match load()'s own folder construction
    data = ld.load(rocket_name, flight)
    data = resolve_duplicates(data, folder)

    
    def find_bundle(data, substr):
        matches = [v for k, v in data.items() if substr in k.lower()]
        if not matches:
            raise KeyError(f"No dataset found containing '{substr}' in its key")
        return matches[0]

    flight_dir = Path(BASE_DIR) / rocket_name / flight
    candidates = [f for f in flight_dir.iterdir() if f.name.lower().endswith('.xml')]

    if not candidates:
        raise FileNotFoundError(f"No suitable XML files found in {flight_dir}")
    if len(candidates) > 1:
        raise ValueError(f"Multiple suitable XML files found in {flight_dir}: {candidates}")
    
    for key, bundle in data.items():
        for col in bundle.columns:
            val = getattr(bundle, col)
            raw = val.magnitude if hasattr(val, "magnitude") else val
            if raw.dtype == object:
                print(f"{key}.{col}: non-numeric, sample = {raw[0]!r}")

    interpolated_data, cutoff_dict = ld.interpolate(data)

    for entry in interpolated_data:
        bundle = interpolated_data[entry]
        df = pd.DataFrame({col: getattr(bundle, col) for col in bundle.columns})
        df.to_csv(f"interp_{entry}.csv", index=False)

    if ROCKET_ENGINES[engine_key][1] == "hybrid":
        thrust_bundle = find_bundle(interpolated_data, "thrust")  # fix meeeeeeee, but the intention is to initialize the engine component
        set_bundle = find_bundle(interpolated_data, "set")

        spec_thrust = thrust_bundle['spec_thrust']
        nonzero_idx = np.flatnonzero(np.asarray(spec_thrust.magnitude))
        burnout = nonzero_idx[-1] + 1 if nonzero_idx.size else len(spec_thrust)

        for col in set_bundle.columns:
            setattr(set_bundle, col, set_bundle[col][:burnout])
        dictionary = ROCKET_ENGINES[engine_key][0]
        #engine_used = build_hybrid(ROCKET_ENGINES[engine_key][0]["high_of"], set_bundle['time'], set_bundle) 
    elif ROCKET_ENGINES[engine_key][1] == "solid":
        thrust_bundle = find_bundle(interpolated_data, "thrust")
        spec_thrust = thrust_bundle['spec_thrust']
        accel_bundle = find_bundle(interpolated_data, "accel")
        nonzero_idx = np.flatnonzero(np.asarray(spec_thrust.magnitude))
        burnout = nonzero_idx[-1] + 1 if nonzero_idx.size else len(spec_thrust)

        burn_time = accel_bundle['time'][:burnout].magnitude

        burn_thrust = spec_thrust[:burnout].magnitude
        #engine_used = build_solid(ROCKET_ENGINES[engine_key][0], accel_bundle['time'], thrust_bundle['spec_thrust'])

    #rocket = geo.Rocket.from_file(candidates[0], engine=engine_used)

    graph_values, cutoff_dict = calculate(interpolated_data, cutoff_dict)
    graph2(graph_values, cutoff_dict)


if __name__ == "__main__":
    main()
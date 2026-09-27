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
        "offset": 0.4,
        "dry_mass":0.15,      # lbm, empty liner
        "length":8.0,         # m from the motor's own reference point (mount face, say)
    },

    "grain": {    
        "dry_mass":0.15,      # lbm, empty liner
        "length":8.0,         # in, matches length_in convention used elsewhere
        "outer_radius_in":1.5,
        "initial_port_radius_in":0.375,
        "length_in":8.0,
        "propellant_density_lbm_in3":0.065,   
    },

    "hardware" : {
        "offset": 8.5,         # m, downstream of the grain
        "dry_mass": 0.6,       # lbm
        "length": 2.0,         # in
    }
}

valor_10k = {
}

lumina = {

}

ROCKET_ENGINES = {
    "morpheus": (O3400, "solid"),  
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

def calculate(data_dict, cutoff_dict, rocket):
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
    ork_pitch = ork_bundle.ork_pitch.magnitude
    ork_flight_angle = ork_bundle.ork_flight_angle.magnitude
    ork_sm = ork_bundle.ork_sm.magnitude
    ork_fd = ork_bundle.ork_fd.magnitude
    ork_cd = ork_bundle.ork_cd.magnitude

    cutoff_dict["apogee"] = apogee = np.where(altitude >= max(altitude))[0][0]
    cutoff_dict["coast"] = np.where(spec_thrust <= 5)[0][2]
    cutoff_dict["uppies"] = 0
    theta = faa.theta(v_dr, v_cr)

    calc["time"] = time
    calc["flight_angle"], calc["aoa"] = faa.angle(v_up, v_dr, v_cr, tilt)
    calc["theta"] = theta
    calc["accel_v"] = faa.magnitude(v_up, v_dr, v_cr)
    calc["sound"], calc["vel_mach"] = faa.v_mach(temperature, calc["accel_v"])
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
    print(rocket.engine)          # is it None?
    print(rocket.mass_at(0), rocket.mass_at(rocket.engine.grain.times_s[-1] if rocket.engine else None))
    calc["cgs"] = geo.total_cg(rocket, calc["time"])[1]
    print(["cg"])
    calc["iyy"] = geo.total_iyy(rocket, calc["time"], calc["cgs"])
    calc["sm"] = faa.stability(time, calc["iyy"], gyro_y, calc["fn"])

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

def plot_to(ax, xarr, yarr, band=None, apogee=None, **kwargs):
    n = apogee if apogee is not None else len(yarr)
    ax.plot(xarr[:n], yarr[:n], **kwargs)
    if band is not None:
        lower, upper = band
        ax.fill_between(xarr[:len(lower)], lower, upper, alpha=0.2,
                         color=kwargs.get('color', 'C0'), label='_nolegend_')
 
def plot_windowed(ax, xarr, yarr, start, end, band=None, **kwargs):
    '''Plot yarr vs xarr over the window [start:end), optionally shading a
    (lower, upper) sensitivity band beneath it.
 
    This is the single, module-level version of what used to be plot_to():
    one copy lived here, and a second copy was defined again inside graph2(),
    silently shadowing this one for every call made from within it. Anything
    that plots a windowed series with an optional band should call this one.'''
    ax.plot(xarr[start:end], yarr[start:end], **kwargs)
    if band is not None:
        lower, upper = band
        n = min(end, len(lower))
        ax.fill_between(xarr[start:n], lower[start:n], upper[start:n], alpha=0.2,
                         color=kwargs.get('color', 'C0'), label='_nolegend_')
 
def diagnostic_grid(column_specs, row_builders, row_labels=None, suptitle=None,
                     col_width=4.5, row_height=3.8, wspace=0.35, hspace=0.55,
                     top_margin=0.90, left_margin=0.07):
    '''Generic N-row x M-column diagnostic figure builder.
 
    column_specs: list of per-column identifying info (e.g. tuples of
        (data_key, ork_key, label)) — one entry per column.
    row_builders: list of functions, each with signature
        fn(ax, col_spec) -> None, called once per (row, column) to draw
        that cell. len(row_builders) determines the number of rows.
    row_labels: optional list of strings, one per row, drawn as a bold
        annotation to the left of each row's first axis instead of a
        per-axis title — avoids "Parity"/"Parity"/"Parity" clutter
        repeating across columns.
    suptitle: optional figure-level title.
    top_margin: fraction of figure height left below the suptitle for
        the actual plot grid (e.g. 0.90 means the top 10% is reserved).
    '''
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
        '''Index of the first sample after which `arr` stays >= threshold for at
        least `hold` consecutive samples. Ignores brief noise spikes that cross
        threshold only momentarily. Returns 0 if never found (no masking applied).'''
        arr= np.asarray(arr, float)
        above = np.abs(arr) >= threshold
        for i in range(len(above) - hold):
            if above[i:i + hold].all():
                return i
        return 0
 
    # OpenRocket-side liftoff index: mask everything before sustained ork velocity.
    if 'ork_vel_total' in data:
        ork_liftoff_idx = first_sustained_index(data['ork_vel_total'], ork_vel_min, hold=hold)
    else:
        ork_liftoff_idx = 0
 
    def ork_masked(key):
        '''Return data[key] with NaN before the sustained-liftoff index.'''
        arr = np.asarray(data[key], float).copy()
        arr[:ork_liftoff_idx] = np.nan
        return arr
 
    # Flight-side liftoff index: mask velocity-DERIVED ANGLE quantities (aoa,
    # flight_angle, theta) before sustained flight velocity — these are ill-defined
    # near v=0 since they're computed from velocity direction.
    if 'accel_v' in data:
        flight_liftoff_idx = first_sustained_index(data['accel_v'], flight_vel_min, hold=hold)
    else:
        flight_liftoff_idx = 0
 
    VELOCITY_DEPENDENT_KEYS = {'aoa', 'flight_angle', 'theta'}
 
    def flight_masked(key):
        '''Return data[key] with NaN before the sustained-liftoff index, for
        quantities that are only meaningful once real flight velocity exists.'''
        arr = np.asarray(data[key], float).copy()
        if key in VELOCITY_DEPENDENT_KEYS:
            arr[:flight_liftoff_idx] = np.nan
        return arr
 
    def shade(ax, x_end=None):
        ax.axvspan(0, t[coast], alpha=0.15, color='lightblue')
        ax.axvspan(t[coast], t[apogee], alpha=0.15, color='pink')
        if x_end is not None:
            ax.set_xlim(0, x_end)
 
    def fit_ylim(ax, key, overlay_key=None):
        base = flight_masked(key) if key in VELOCITY_DEPENDENT_KEYS else np.asarray(data[key], float)
        ys = [base[start:apogee]]
        if overlay_key is not None and overlay_key in data:
            ov = ork_masked(overlay_key)[start:apogee] if overlay_key.startswith('ork_') else \
                 np.asarray(data[overlay_key][start:apogee], float)
            ys.append(ov)
        y = np.concatenate(ys)
        y = y[np.isfinite(y)]
        if y.size == 0:
            return
        pad = (y.max() - y.min()) * 0.05 or 1
        ax.set_ylim(y.min() - pad, y.max() + pad)
 
    # ---- page 1: Basics --------------------------------------------------
    # A 3x2 grid of independent quantities. diagnostic_grid() crosses
    # row_builders against column_specs, so to keep this exact layout we
    # give it 2 generic column slots (0, 1) and build one row_builder per
    # row of BASICS_LAYOUT, each closing over which row it owns.
    ork_overlay_1 = {'altitude': 'ork_altitude', 'accel_total': 'ork_accel_total',
                      'accel_v': 'ork_vel_total', 'aoa': 'ork_aoa'}
    BASICS_LAYOUT = [
        [('altitude', 'Altitude (ft)'), ('accel_v', 'Velocity (ft/s)')],
        [('accel_total', 'Acceleration (ft/s²)'), ('aoa', 'AoA (°)')],
        [('theta', 'Pitch (°)'), ('flight_angle', 'Flight Angle (°)')],
    ]
 
    def _basics_row_builder(row):
        def builder(ax, col):
            key, lbl = BASICS_LAYOUT[row][col]
            overlay_key = ork_overlay_1.get(key)
            has_overlay = overlay_key is not None and overlay_key in data
            plot_windowed(ax, t, flight_masked(key), start, apogee, band=bands.get(key),
                          label='Flight' if has_overlay else None)
            if has_overlay:
                plot_windowed(ax, t, ork_masked(overlay_key), start, apogee, label='OpenRocket')
                ax.legend()
            ax.set(xlabel='Time (s)', ylabel=lbl, title=lbl)
            shade(ax, x_end=t[apogee])
            fit_ylim(ax, key, overlay_key=overlay_key)
        return builder
 
    fig1, _ = diagnostic_grid(
        column_specs=[0, 1],
        row_builders=[_basics_row_builder(r) for r in range(len(BASICS_LAYOUT))],
        suptitle='Basics',
        col_width=6, row_height=3.3,
    )
 
    # ---- page 1b: Overlay Diagnostics -------------------------------------
    # This is the layout diagnostic_grid was actually designed for: three
    # "kinds" of view (Raw / Parity / % Error) repeated across however many
    # quantities have an OpenRocket overlay available.
    overlay_present = [(k, ork_overlay_1[k], lbl) for k, lbl in [
        ('altitude', 'Altitude (ft)'), ('accel_v', 'Velocity (ft/s)'),
        ('accel_total', 'Acceleration (ft/s²)'), ('aoa', 'AoA (°)')
    ] if k in ork_overlay_1 and ork_overlay_1[k] in data]
 
    fig1b = None
    if overlay_present:
        def _raw_row(ax, spec):
            k, ok, lbl = spec
            tt = t[start:apogee]
            plot_windowed(ax, t, flight_masked(k), start, apogee, label='Flight', alpha=0.85)
            plot_windowed(ax, t, ork_masked(ok), start, apogee, label='OpenRocket', alpha=0.85)
            ax.set(xlabel='Time (s)', ylabel=lbl, title=lbl)
            shade(ax, x_end=t[apogee])
            ax.legend(fontsize=7)
 
        def _parity_row(ax, spec):
            k, ok, lbl = spec
            flight = flight_masked(k)[start:apogee]
            ork = ork_masked(ok)[start:apogee]
            valid = np.isfinite(flight) & np.isfinite(ork)
            ax.scatter(ork[valid], flight[valid], s=4, alpha=0.4)
            if valid.any():
                lims = [np.nanmin(ork[valid]), np.nanmax(ork[valid])]
                ax.plot(lims, lims, 'k--', linewidth=1, label='y = x')
            ax.set(xlabel=f'OpenRocket {lbl}', ylabel=f'Flight {lbl}')
            ax.legend(fontsize=7)
 
        def _pct_err_row(ax, spec):
            k, ok, lbl = spec
            flight = flight_masked(k)[start:apogee]
            ork = ork_masked(ok)[start:apogee]
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
    # Independent panels again, so this is diagnostic_grid with a single row.
    page2_items = [
        ('thrust_ras', 'Thrust (lbf)'), ('fd', 'Drag (lbf)'),
        ('fn', 'Normal Force (lbf)'), ('lift', 'Lift (lbf)')
    ]
 
    def _forces_cell(ax, spec):
        ka, lbl = spec
        plot_windowed(ax, t, data[ka], start, apogee, band=bands.get(ka),
                      label='RAS' if ka == 'thrust_ras' else 'Flight')
        if ka == 'thrust_ras':
            plot_windowed(ax, t, data['thrust_spec'], start, apogee, label='Motor spec')
        if ka == 'fd' and 'ork_fd' in data:
            plot_windowed(ax, t, ork_masked('ork_fd'), start, apogee, label='OpenRocket')
        ax.set(xlabel='Time (s)', ylabel=lbl, title=lbl)
        shade(ax, x_end=t[apogee])
        fit_ylim(ax, ka)
        ax.legend()
 
    fig2, _ = diagnostic_grid(
        column_specs=page2_items,
        row_builders=[_forces_cell],
        suptitle='Forces',
        col_width=3.5, row_height=4,
    )
 
    # ---- page 3/4 merged: Drag & Stability Studies -------------------------
    # CD-vs-{time,aoa,mach} and SM-vs-{time,cna,mach} used to be two separate
    # figures built from an identical pattern (scatter_with_band + overlay_ork
    # per column). They're the same row builder applied to two y-quantities,
    # so they're now two rows of one diagnostic_grid instead of two figures.
    def scatter_with_band(ax, xarr, ykey, bands, apogee, mask, bins=40):
        x = xarr[start:apogee][mask]
        y = data[ykey][start:apogee][mask]
        ok = np.isfinite(x) & np.isfinite(y)
        ax.scatter(x[ok], y[ok], s=4, alpha=0.5, label='Flight')
        if ykey in bands and len(x) > 0:
            lower, upper = bands[ykey]
            n = min(apogee, len(lower))
            m = mask[start:n] if n > start else mask[:0]
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
 
    def overlay_ork(ax, xarr, ork_key, apogee, mask, color='green'):
        if ork_key not in data:
            return
        x = xarr[start:apogee][mask]
        y = ork_masked(ork_key)[start:apogee][mask]
        ok = np.isfinite(x) & np.isfinite(y)
        ax.scatter(x[ok], y[ok], s=4, alpha=0.5, color=color, label='OpenRocket')
        ax.legend(fontsize=7)
 
    vel_mask = data['vel_mach'][start:apogee] >= mach_min
 
    STUDY_ROWS = [
        {
            'label': 'CD', 'y_key': 'cd', 'ork_key': 'ork_cd', 'y_label': 'CD',
            'x_specs': [('time', 'Time (s)', 'CD vs Time'),
                        ('aoa', 'AoA (°)', 'CD vs AoA'),
                        ('vel_mach', 'Mach', 'CD vs Mach')],
        },
        {
            'label': 'SM', 'y_key': 'sm', 'ork_key': 'ork_sm', 'y_label': 'SM (cal)',
            'x_specs': [('time', 'Time (s)', 'SM vs Time'),
                        ('cna', 'CN\u03b1', 'SM vs CN\u03b1'),
                        ('vel_mach', 'Mach', 'SM vs Mach')],
        },
    ]
 
    def _study_row_builder(row_cfg):
        def builder(ax, col):
            xk, xl, ttl = row_cfg['x_specs'][col]
            scatter_with_band(ax, data[xk], row_cfg['y_key'], bands, apogee, vel_mask)
            overlay_ork(ax, data[xk], row_cfg['ork_key'], apogee, vel_mask)
            ax.set(xlabel=xl, ylabel=row_cfg['y_label'], title=ttl)
            if xk == 'time':
                shade(ax)
        return builder
 
    fig34, _ = diagnostic_grid(
        column_specs=[0, 1, 2],
        row_builders=[_study_row_builder(cfg) for cfg in STUDY_ROWS],
        row_labels=[cfg['label'] for cfg in STUDY_ROWS],
        suptitle='Drag & Stability Studies',
        col_width=4.5, row_height=4,
    )
 
    plt.show()
    return fig1, fig1b, fig2, fig34

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
        engine_used = build_hybrid(ROCKET_ENGINES[engine_key][0]["high_of"], set_bundle['time'], set_bundle) 
    elif ROCKET_ENGINES[engine_key][1] == "solid":
        thrust_bundle = find_bundle(interpolated_data, "thrust")
        spec_thrust = thrust_bundle['spec_thrust']
        accel_bundle = find_bundle(interpolated_data, "accel")
        nonzero_idx = np.flatnonzero(np.asarray(spec_thrust.magnitude))
        burnout = nonzero_idx[-1] + 1 if nonzero_idx.size else len(spec_thrust)

        burn_time = accel_bundle['time'][:burnout].magnitude

        burn_thrust = spec_thrust[:burnout].magnitude
        engine_used = build_solid(ROCKET_ENGINES[engine_key][0], accel_bundle['time'], thrust_bundle['spec_thrust'])

    rocket = geo.Rocket.from_file(candidates[0], engine=engine_used)

    graph_values, cutoff_dict = calculate(interpolated_data, cutoff_dict, rocket)
    graph2(graph_values, cutoff_dict)


if __name__ == "__main__":
    main()
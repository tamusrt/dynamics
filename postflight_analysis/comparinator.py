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
from pathlib import Path
import pandas as pd
import numpy as np
from scipy.interpolate import interp1d
import pint

ureg = pint.UnitRegistry()
Q_ = ureg.Quantity

radius = 3
# x = 0 at the nose tip, increasing aft. offset is each part's forward face.
# plumbing and tail are still at station 0 and the engine internals total 0.83 in
# against a 50 in Engine.length; both need real measurements.
sol_ignis = faa.Engine(faa.EngineComponent(name="ox_tank", dry_mass=0.35, prop_mass=1.2, offset=0.0, length=0.45),
    faa.EngineComponent(name="plumbing", dry_mass=0.15, offset=0.45, length=0.08),
    faa.EngineComponent(name="fuel_grain", dry_mass=0.4, prop_mass=0.1, offset=0.53, length=0.30), length=50, offset=130)


ROCKET_PATHS = {
    "morpheus": r"G:\Shared drives\TAMU-SRT\srt_general\9_flight_data\Morpheus\04232025\morph.xml",
    # add other rockets here as needed
}

ROCKET_ENGINES = {
    "morpheus": sol_ignis,  
    "sol_invictus": sol_ignis # the faa.Engine instance built at module scope
    # add other rockets' engines here
}
# Real, on-disk file "types" -> which raw columns to keep, in order.
# Note: br_accel and bj_accel share the exact same schema, so they both
# get mapped to the same ALIASES group ("accel") below.
COLUMN_MAP = {
    "br_accel": ["Flight_Time_(s)", "Temperature_(F)", "Baro_Press_(atm)", "Baro_Altitude_ASL_(feet)",
                 "Velocity_Up", "Velocity_DR", "Velocity_CR", "Inertial_Altitude",
                 "Inertial_DR_Position", "Inertial_CR_position", "Tilt_Angle_(deg)"],
    "bj_accel": ["Flight_Time_(s)", "Temperature_(F)", "Baro_Press_(atm)", "Baro_Altitude_ASL_(feet)",
                 "Velocity_Up", "Velocity_DR", "Velocity_CR", "Inertial_Altitude",
                 "Inertial_DR_Position", "Inertial_CR_position", "Tilt_Angle_(deg)"],
    "gyro": ["Flight_Time_(s)", "Gyro_X", "Gyro_Y", "Gyro_Z", "Accel_X", "Accel_Y", "Accel_Z"],
    "thrust": ["Time", "Thrust (N)"],
    "ras": ["Flight Time Rounded (s)", "Thrust (lb)", "Accel (ft/sec^2)", "Weight (lb)"],
    "ork": ["Total acceleration (m/sÂ²)", "Angle of attack (Â°)"]
}

# Alias groups: canonical short names -> raw column names.
# Keys here ("accel", "gyro", "thrust", "ras") are deliberately GENERIC —
# they describe a *category* of file, not a specific filename.
ALIASES = {
    "accel": {
        "time": "Flight_Time_(s)",
        "temperature": "Temperature_(F)",
        "pressure": "Baro_Press_(atm)",
        "altitude": "Baro_Altitude_ASL_(feet)",
        "v_up": "Velocity_Up",
        "v_dr": "Velocity_DR",
        "v_cr": "Velocity_CR",
        "in_a": "Inertial_Altitude",
        "in_dr": "Inertial_DR_Position",
        "in_cr": "Inertial_CR_position",
        "tilt": "Tilt_Angle_(deg)",
    },
    "gyro": {
        "time": "Flight_Time_(s)",
        "gx": "Gyro_X",
        "gy": "Gyro_Y",
        "gz": "Gyro_Z",
        "gx_accel": "Accel_X",
        "gy_accel": "Accel_Y",
        "gz_accel": "Accel_Z",
    },
    "thrust": {
        "time": "Time",
        "spec_thrust": "Thrust (N)",
    },
    "ras": {
        "time": "Flight Time Rounded (s)",
        "ras_thrust": "Thrust (lb)",
        "accel": "Accel (ft/sec^2)",
        "weight": "Weight (lb)",
    },
}

# unit each alias column is stored in, per ALIASES group.
# "time" is not included since every group uses seconds -
# handled once via TIME_UNIT instead of repeating it in every group.
UNITS = {
    "accel": {
        "temperature": "degF",
        "pressure": "atm",
        "altitude": "ft",
        "v_up": "ft/s",
        "v_dr": "ft/s",
        "v_cr": "ft/s",
        "in_a": "ft",
        "in_dr": "ft",
        "in_cr": "ft",
        "tilt": "deg",
    },
    "gyro": {
        "gx": "deg/s",
        "gy": "deg/s",
        "gz": "deg/s",
        "gx_accel": "g_0",
        "gy_accel": "g_0",
        "gz_accel": "g_0",
    },
    "thrust": {
        "spec_thrust": "N",
    },
    "ras": {
        "ras_thrust": "lbf",
        "accel": "ft/s**2",
        "weight": "lbf",
    },
}
TIME_UNIT = "s"

# per-format read settings (delimiter/encoding)
# most of the csvs use comma separated, however, ras aero data defaults to
READ_CONFIG = {
    "ras": {"sep": ",", "encoding": "utf-8-sig"},
}
DEFAULT_READ_CONFIG = {"sep": ",", "encoding": "utf-8"}

# which alias name is "time", per ALIAS GROUP (not raw fmt), used for the t>=0 trim
TIME_ALIAS = {
    "accel": "time",
    "gyro": "time",
    "thrust": "time",
    "ras": "time",
}

def _detect_format(stem: str, format: str | None = None):
    """Match the filename stem against the REAL file types only
    (COLUMN_MAP / READ_CONFIG keys) — never against ALIASES keys.

    If `format` is given, it's used directly (after validation) instead
    of guessing from the filename. This lets callers resolve ambiguous
    cases (e.g. "br_accel" vs "accel") manually rather than relying on
    auto-detection.

    Auto-detection is sorted by length descending so a more specific key
    (e.g. "br_accel") always wins over a shorter one, if that ever
    becomes ambiguous.
    """
    candidates = set(COLUMN_MAP) | set(READ_CONFIG)

    if format is not None:
        format = format.lower()
        if format not in candidates:
            raise ValueError(
                f"Unknown format {format!r}. Valid options: {sorted(candidates)}"
            )
        return format

    stem = stem.lower()
    for key in sorted(candidates, key=len, reverse=True):
        if key in stem:
            return key
    return None

def _alias_group(fmt: str):
    """Map a raw fmt (e.g. 'br_accel') to its ALIASES group (e.g. 'accel')."""
    if fmt is None:
        return None
    if fmt in ALIASES:
        return fmt
    return next((g for g in ALIASES if g in fmt), None)


class ArrayBundle:
    """Holds one file's columns as numpy arrays (or pint Quantities, if a
    units dict is given), accessible by alias name either as an attribute
    (bundle.time) or a key (bundle['time'])."""

    def __init__(self, df: pd.DataFrame, units: dict | None = None):
        self._columns = list(df.columns)
        units = units or {}
        for col in df.columns:
            arr = df[col].to_numpy()
            unit = TIME_UNIT if col == "time" else units.get(col)
            if unit:
                arr = Q_(arr, unit)
            setattr(self, col, arr)

    def __getitem__(self, key):
        return getattr(self, key)

    @property
    def columns(self):
        return list(self._columns)

    def to_frame(self) -> pd.DataFrame:
        """Convert this bundle back into a real pandas DataFrame (units stripped)."""
        plain = {}
        for col in self._columns:
            val = getattr(self, col)
            plain[col] = val.magnitude if isinstance(val, ureg.Quantity) else val
        return pd.DataFrame(plain)

    def __len__(self):
        return len(getattr(self, self._columns[0]))

    def __repr__(self):
        return f"ArrayBundle(columns={self._columns})"


def load_file(path: Path, format: str | None = None) -> ArrayBundle:
    fmt = _detect_format(path.stem, format=format)
    if fmt is None:
        raise ValueError(
            f"Could not detect a format for {path.name} from its filename. "
            f"Pass format='br_accel' or format='bj_accel' explicitly."
        )
    group = _alias_group(fmt)
    read_kwargs = READ_CONFIG.get(fmt, DEFAULT_READ_CONFIG)

    df = pd.read_csv(path, **read_kwargs)
    df.columns = [c.strip().lstrip("\ufeff").lstrip("#").strip() for c in df.columns]

    if fmt in COLUMN_MAP:
        wanted = COLUMN_MAP[fmt]
        missing = [c for c in wanted if c not in df.columns]
        if missing:
            raise ValueError(f"{path.name} is missing expected columns: {missing}")
        df = df[wanted]

    if group in ALIASES:
        rename_map = {raw: alias for alias, raw in ALIASES[group].items() if raw in df.columns}
        df = df.rename(columns=rename_map)

    time_col = TIME_ALIAS.get(group)
    if time_col and time_col in df.columns:
        df = df[df[time_col] >= 0].reset_index(drop=True)

    return ArrayBundle(df, units=UNITS.get(group))


def load(rocket, flight, format=None, base_dir=r"G:\Shared drives\TAMU-SRT\srt_general\9_flight_data"):
    """Loads every CSV in a flight's folder into a dict of ArrayBundles, keyed by filename stem.
    `format`, if given (e.g. "br_accel" or "bj_accel"), is only applied to files whose
    filename alone doesn't already unambiguously indicate a type - it's a fallback,
    not an override, so gyro/thrust/ras files still auto-detect normally.
    Example: data = load("RocketA", "Flight3", format="br_accel")
             data["br_accel_launch"].altitude   -> numpy array
             data["br_accel_launch"]["altitude"] -> same, dict-style
    """
    folder = Path(base_dir) / rocket / flight
    if not folder.is_dir():
        raise NotADirectoryError(f"No such folder: {folder}")

    results = {}
    for csv_path in sorted(folder.glob("*.csv")):
        try:
            detected = _detect_format(csv_path.stem)
            chosen_format = detected if detected is not None else format
            results[csv_path.stem] = load_file(csv_path, format=chosen_format)
        except Exception as e:
            print(f"Skipping {csv_path.name}: {e}")
    return results


def _magnitude(val):
    """Return the plain numpy array underneath a value, whether or not it's a pint Quantity."""
    return val.magnitude if isinstance(val, ureg.Quantity) else val


def interpolate(data):
    '''Interpolates every dataset onto a common, evenly-spaced time base
    (the finest step found across all datasets), then zero-pads the
    shorter ones so every ArrayBundle has the same length.

    Works whether or not the bundles carry pint units: units (if present)
    are stripped before interpolation (scipy doesn't understand them) and
    reattached to the result afterward, so the output bundles keep the
    same units as the input.

    Accepts a dict of ArrayBundle (the output of `load`/`load_file`) and
    returns:
      - new_bundles: dict of ArrayBundle, interpolated + padded
      - cutoffs: dict of the number of *real* (non-padded) samples in each
        interpolated bundle, so you can later trim the zero padding back off
        via e.g. `bundle.time[:cutoffs[key]]`.
    '''
    new_bundles = {}
    cutoffs = {}

    # only bundles with a "time" column can be aligned this way
    timed = {key: bundle for key, bundle in data.items() if "time" in bundle.columns}
    if not timed:
        raise ValueError("None of the given datasets have a 'time' column to align on")

    # finest time step across all datasets
    step = min(np.diff(_magnitude(bundle.time)).min() for bundle in timed.values())

    for key, bundle in timed.items():
        time_val = _magnitude(bundle.time)
        new_time = np.clip(
            np.arange(time_val[0], time_val[-1] + step, step),
            time_val[0], time_val[-1]
        )

        interp_cols = {"time": new_time}
        units_here = {}
        for col in bundle.columns:
            if col == "time":
                continue
            col_val = getattr(bundle, col)
            if isinstance(col_val, ureg.Quantity):
                units_here[col] = col_val.units
                col_val = col_val.magnitude
            interp_func = interp1d(time_val, col_val, kind="linear")
            interp_cols[col] = interp_func(new_time)

        new_bundles[key] = ArrayBundle(pd.DataFrame(interp_cols), units=units_here)
        # real (pre-padding) length of the interpolated series
        cutoffs[key] = len(new_time)

    length = max(len(bundle) for bundle in new_bundles.values())

    for key, bundle in new_bundles.items():
        pad_amount = length - len(bundle)
        if pad_amount > 0:
            cols = {}
            units_here = {}
            for col in bundle.columns:
                val = getattr(bundle, col)
                if isinstance(val, ureg.Quantity):
                    units_here[col] = val.units
                    val = val.magnitude
                cols[col] = val
            df = pd.DataFrame(cols)
            padding = pd.DataFrame(0, index=range(pad_amount), columns=df.columns)
            padded = pd.concat([df, padding], ignore_index=True)
            new_bundles[key] = ArrayBundle(padded, units=units_here)

    return new_bundles, cutoffs


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

    # stages of flight
    #engine = [p for p in rocket if isinstance(p, faa.Engine)][0]
    #engine.set_curve(thrusts=spec_thrust, times=time)
    #engine._process_curve()
    cutoff_dict["apogee"] = apogee = np.where(altitude >= max(altitude))[0][0]
    cutoff_dict["coast"] = np.where(spec_thrust <= 5)[0][2]
    cutoff_dict["uppies"] = 0
    theta = faa.theta(v_dr, v_cr)
    #sample_rate = 1 / step

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
    # faa.thrust() is the algebraic inverse of faa.fd(), so feeding it the drag that
    # fd() derived from this same curve returns that curve. the two independent
    # sources are the ras condition file and the motor spec curve.
    calc["thrust_ras"] = thrust
    calc["thrust_spec"] = spec_thrust
    calc["altitude"] = altitude
    calc["cgs"] = faa.total_cg(rocket, calc["time"])[1]
    calc["iyy"] = faa.total_iyy(rocket, calc["time"], calc["cgs"])
    calc["sm"] = faa.stability(time, calc["iyy"], gyro_y, calc["fn"])
    #calc["sm2"] = faa.stability1(faa.frequency(calc["aoa"], sample_rate, calc["time"]), calc["iyy"], calc["accel_v"], calc["density"], calc["aoa"], calc["fn"])

    df = pd.DataFrame.from_dict(calc)   
    df.to_csv(f"calc_data.csv", index=False)

    return calc, cutoff_dict


def compute_sensitivity_bands(build_data_fn, windows, keys, apogee_key="apogee"):
    """
    NEED TO DOUBLE CHECK, THIS FUNCTION IS VIBECODED
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

def graph2(data, areas, build_data_fn=None, windows=(31, 51, 71, 91, 111), mach_min=0.3):
    '''graphs values across a couple different figures.
    mach_min gates the coefficient plots, where q sits in the denominator and
    goes to zero near apogee'''
    coast, apogee = areas["coast"], areas["apogee"]
    t = data['time']
    band_keys = ['accel_v', 'accel_total', 'fd', 'cd', 'fn']
    bands = {}
    if build_data_fn is not None:
        bands, band_apogee = compute_sensitivity_bands(build_data_fn, windows, band_keys)

    def shade(ax, x_end=None):
        ax.axvspan(0, t[coast], alpha=0.15, color='lightblue')
        ax.axvspan(t[coast], t[apogee], alpha=0.15, color='pink')
        if x_end is not None:
            ax.set_xlim(0, x_end)

    def fit_ylim(ax, key):
        y = np.asarray(data[key][:apogee], float); y = y[np.isfinite(y)]
        if y.size == 0:
            return
        pad = (y.max() - y.min()) * 0.05 or 1
        ax.set_ylim(y.min() - pad, y.max() + pad)

    def plot_to(ax, xarr, yarr, band=None, **kwargs):
        ax.plot(xarr[:apogee], yarr[:apogee], **kwargs)
        if band is not None:
            lower, upper = band
            n = min(apogee, len(lower))
            ax.fill_between(xarr[:n], lower[:n], upper[:n], alpha=0.2,
                             color=kwargs.get('color', 'C0'), label='_nolegend_')

    # page 1
    fig1, axs = plt.subplots(3, 2, figsize=(12, 10))
    fig1.suptitle('Basics', fontweight='bold')
    for ax, (k, lbl) in zip(axs.flat, [
        ('altitude','Altitude (ft)'), ('accel_v','Velocity (ft/s)'), ('accel_total','Acceleration (ft/s²)'),
        ('aoa','AoA (°)'), ('theta','Pitch (°)'), ('flight_angle','Flight Angle (°)')
    ]):
        plot_to(ax, t, data[k], band=bands.get(k))
        ax.set(xlabel='Time (s)', ylabel=lbl, title=lbl)
        shade(ax, x_end=t[apogee]); fit_ylim(ax, k)
    fig1.tight_layout()

    # page 2 — thrust and drag limited to apogee on x-axis
    fig2, axs = plt.subplots(1, 3, figsize=(14, 4))
    fig2.suptitle('Forces', fontweight='bold')
    for ax, (ka, lbl) in zip(axs, [
        ('thrust_ras','Thrust (lbf)'), ('fd','Drag (lbf)'), ('fn','Normal Force (lbf)')]):
        plot_to(ax, t, data[ka], band=bands.get(ka), label='RAS' if ka == 'thrust_ras' else 'Flight')
        if ka == 'thrust_ras':
            plot_to(ax, t, data['thrust_spec'], label='Motor spec')
        ax.set(xlabel='Time (s)', ylabel=lbl, title=lbl)
        shade(ax, x_end=t[apogee])
        fit_ylim(ax, ka)
        ax.legend()
    fig2.tight_layout()

    # page 3 — CD plots exclude low-velocity (low-Mach) points
    def scatter_with_band(ax, xarr, ykey, bands, apogee, mask, bins=40):
        x = xarr[:apogee][mask]
        y = data[ykey][:apogee][mask]
        ok = np.isfinite(x) & np.isfinite(y)
        ax.scatter(x[ok], y[ok], s=4, alpha=0.5, label='Actual')
        if ykey in bands and len(x) > 0:
            lower, upper = bands[ykey]
            n = min(apogee, len(lower))
            m = mask[:n]
            lower, upper = lower[:n][m], upper[:n][m]
            x_band = xarr[:n][m]
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

    vel_mask = data['vel_mach'][:apogee] >= mach_min

    fig3, axs = plt.subplots(1, 3, figsize=(14, 4))
    fig3.suptitle('Drag Coefficient Studies', fontweight='bold')
    for ax, (xk, xl, ttl) in zip(axs, [
        ('time','Time (s)','CD vs Time'), ('aoa','AoA (°)','CD vs AoA'), ('vel_mach','Mach','CD vs Mach')
    ]):
        scatter_with_band(ax, data[xk], 'cd', bands, apogee, vel_mask)
        ax.set(xlabel=xl, ylabel='CD', title=ttl)
        if xk == 'time': shade(ax)
    fig3.tight_layout()

    # page 4
    fig4, axs = plt.subplots(1, 3, figsize=(14, 4))
    fig4.suptitle('Stability Studies', fontweight='bold')
    for ax, (xk, xl, ttl) in zip(axs, [
        ('time','Time (s)','SM vs Time'), ('cna','CN\u03b1','SM vs CN\u03b1'), ('vel_mach','Mach','SM vs Mach')
    ]):
        scatter_with_band(ax, data[xk], 'sm', bands, apogee, vel_mask)
        ax.set(xlabel=xl, ylabel='SM (cal)', title=ttl)
        if xk == 'time': shade(ax)
    fig4.tight_layout()

    plt.show()
    return fig1, fig2, fig3, fig4

BLUE_FORMAT_MAP = {
    "blueraven": "br_accel", "br": "br_accel",
    "bluejay": "bj_accel", "bj": "bj_accel",
}


def main():
    rocket_name = input("Rocket name: ")
    flight = input("Flight date (mm/dd/yyyy): ")
    correct_blue = input("BlueRaven or BlueJay?: ")
    engine_name = input("Engine name: ")
 
    key = rocket_name.strip().lower()
    if key not in ROCKET_PATHS:
        raise ValueError(f"Unknown rocket '{rocket_name}'. Known: {list(ROCKET_PATHS)}")
 
    blue_key = correct_blue.strip().lower().replace(" ", "")
    accel_format = BLUE_FORMAT_MAP.get(blue_key)
    if accel_format is None:
        raise ValueError(
            f"Unknown BlueRaven/BlueJay answer '{correct_blue}'. "
            f"Expected one of: {sorted(set(BLUE_FORMAT_MAP.values()))}"
        )
 
    engine_key = engine_name.strip().lower()
    if engine_key not in ROCKET_ENGINES:
        raise ValueError(f"Unknown engine '{engine_name}'. Known: {list(ROCKET_ENGINES)}")
 
    def find_bundle(data, substr):
        matches = [v for k, v in data.items() if substr in k.lower()]
        if not matches:
            raise KeyError(f"No dataset found containing '{substr}' in its key")
        return matches[0]
 
    data = load(rocket_name, flight, format=accel_format)
 
    thrust_bundle = find_bundle(data, "thrust")
 
    thrusts_array = thrust_bundle.spec_thrust.magnitude
    times_array = thrust_bundle.time.magnitude
 
    print(len(thrusts_array), len(times_array))
    rocket = faa.Rocket.from_file(ROCKET_PATHS[key], engine=ROCKET_ENGINES[engine_key])
    rocket.engine.set_curve(thrusts_array, times_array)
 
    interpolated_data, cutoff_dict = interpolate(data)
    for entry in interpolated_data:
        bundle = interpolated_data[entry]
        df = pd.DataFrame({col: getattr(bundle, col) for col in bundle.columns})
        df.to_csv(f"interp_{entry}.csv", index=False)
    graph_values, cutoff_dict = calculate(interpolated_data, cutoff_dict, rocket)
    graph2(graph_values, cutoff_dict)


if __name__ == "__main__":
    main()
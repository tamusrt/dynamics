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
from pathlib import Path
import pandas as pd
import numpy as np
from scipy.interpolate import interp1d
import pint
from collections import defaultdict
import hybrid_engine_cg as eng

ureg = pint.UnitRegistry()
Q_ = ureg.Quantity


radius = 3
# x = 0 at the nose tip, increasing aft. offset is each part's forward face.
# plumbing and tail are still at station 0 and the engine internals total 0.83 in
# against a 50 in Engine.length; both need real measurements.

BASE_DIR = r"G:\Shared drives\TAMU-SRT\srt_general\9_flight_data"

sol_ignis = eng.Engine2(eng.EngineComponent2(name="ox_tank", dry_mass=8.0, prop_mass=40, offset=10.0, length=24.0),
                        eng.EngineComponent2(name="plumbing", dry_mass=2.0, offset=10.0, length=2.0),
                        eng.EngineComponent2(name="fuel_grain", dry_mass=3.0, prop_mass=0.1, offset=36.0, length=12.0), length=38, offset=0.0)

ROCKET_ENGINES = {
    "morpheus": sol_ignis,  
    "sol_invictus": sol_ignis 
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
    "ork": ["Time (s)","Altitude (m)","Total acceleration (m/s²)", "Angle of attack (°)", "Total velocity (m/s)","Pitch rate (°/s)", "Yaw rate (°/s)","Stability margin calibers (​)", "Drag force (N)", "Drag coefficient (​)"],
    "set": ["timestamp (ns)", "sensors/thrust.value", "sensors/chamber_pressure.value", "sensors/injector_pressure.value", "sensors/run_tank_pressure.value"]
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
    "ork": {
        "time": "Time (s)",
        "ork_altitude": "Altitude (m)",
        "ork_accel_total":"Total acceleration (m/s²)",
        "ork_vel_total": "Total velocity (m/s)",
        "ork_aoa": "Angle of attack (°)", 
        "ork_pitch":"Pitch rate (°/s)", 
        "ork_flight_angle":"Yaw rate (°/s)",
        "ork_sm": "Stability margin calibers (​)", 
        "ork_fd":"Drag force (N)", 
        "ork_cd":"Drag coefficient (​)"
    },
    "set": {
        "time": "timestamp (ns)",
        "thrust": "sensors/thrust.value",
        "chamber_pressure": "sensors/chamber_pressure.value",
        "injector_pressure": "sensors/injector_pressure.value",
        "run_tank_pressure": "sensors/run_tank_pressure.value"
    }
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
    "ork":{
        "ork_altitude": "m",
        "ork_accel_total":"m/s**2",
        "ork_vel_total":"m/s",
        "ork_aoa": "deg", 
        "ork_pitch":"deg/s", 
        "ork_flight_angle":"deg/s",
        "ork_sm": "dimensionless", 
        "ork_fd":"N", 
        "ork_cd":"dimensionless"
    },
    "set": {
        "time": "ns",
        "thrust": "N",
        "chamber_pressure": "psi",
        "injector_pressure": "psi",
        "run_tank_pressure": "psi"
    }
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
    "ork":"time",
    "set": "time"
}
ureg.define("psf = lbf / foot ** 2")
# Define your target imperial unit for each physical dimension you expect to see.
# Add/remove entries as needed for your dataset.
IMPERIAL_UNITS = {
    ureg.lbf.dimensionality: ureg.lbf,           # force
    ureg.foot.dimensionality: ureg.foot,         # length
    (ureg.foot / ureg.second).dimensionality: ureg.foot / ureg.second,       # velocity
    (ureg.foot / ureg.second**2).dimensionality: ureg.foot / ureg.second**2, # acceleration
    ureg.pound.dimensionality: ureg.pound,       # mass/weight 
    ureg.degF.dimensionality: ureg.degF,         # temperature
    ureg.second.dimensionality: ureg.second,     # time
    ureg.atm.dimensionality: ureg.atm,  # density in lbm/ft³
}

def to_imperial(qty):
    '''Given a pint Quantity, convert it to the preferred imperial unit
    for its dimensionality. Returns it unchanged if its dimensionality
    isn't in the table (so unknown types don't silently break).'''
    if not isinstance(qty, ureg.Quantity):
        return qty
    target = IMPERIAL_UNITS.get(qty.dimensionality)
    if target is None:
        return qty  # unrecognized dimensionality — leave as-is, don't guess
    return qty.to(target)

def _detect_format(stem: str, format: str | None = None):
    candidates = set(COLUMN_MAP) | set(READ_CONFIG)

    if format is not None:
        format = format.lower()
        if format not in candidates:
            raise ValueError(
                f"Unknown format {format!r}. Valid options: {sorted(candidates)}"
            )
        return format

    stem = stem.lower()

    # Pass 1: strict match against real format keys
    for key in sorted(candidates, key=len, reverse=True):
        if key in stem:
            return key

    # Pass 3: fallback to alias group names themselves (e.g. "accel", "gyro")
    for group in ALIASES:
        if group in stem:
            return group

    return None

def _detect_format_by_columns(path: Path):
    """Fallback: peek at the header row and find which COLUMN_MAP format's
    wanted columns are all present. Used when filename detection fails."""
    try:
        header_df = pd.read_csv(path, nrows=0)
    except Exception:
        return None

    header_cols = {c.strip().lstrip("\ufeff").lstrip("#").strip() for c in header_df.columns}

    best_fmt, best_score = None, 0
    for fmt, wanted in COLUMN_MAP.items():
        if set(wanted).issubset(header_cols) and len(wanted) > best_score:
            best_fmt, best_score = fmt, len(wanted)

    return best_fmt

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
        fmt = _detect_format_by_columns(path)
    if fmt is None:
        raise ValueError(
            f"Could not detect a format for {path.name} from filename or columns. "
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
    exclude = [] # problematic high frequency pieces not being used 
    step = min(np.diff(_magnitude(bundle.time)).min() for bundle in timed.values() if bundle not in exclude)

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
                col_val = to_imperial(col_val)
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

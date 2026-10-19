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
        "flow": {
            "ox_method": "constant",         # "constant" | "column" | "blowdown"
            "ox_mdot": 0.9,                  # lbm/s, used if constant
            "fuel_method": "regression",     # "regression" | "thrust" | "constant"
            "n": 0.6,
            "fuel_burned_lbm": 1.8,          # post-burn weigh-in; used to fit `a` (or give "a" directly)
        },
        "tank": {
            "dry_mass": 30, "offset": 10.0, "length": 50.0, "radius": 2.5,
            "volume_in3": math.pi * 2.0**2 * 50.0,
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
    "morpheus": (sol_ignis, "hybrid"),  
    "sol invictus": (sol_ignis, "hybrid"), 
    "morbin' time": (O3400, "solid"),
    "mikeys": (lumina, "liquid"),
    "l2d2": (O3400, "solid"),
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
def build_hybrid(cfg, t, ox_pressure_psi, thrust_lbf, ox_mdot_measured=None):
    fc, gc = cfg["flow"], cfg["grain"]
    t, ox_pressure_psi, thrust_lbf = (np.asarray(a, dtype=float) for a in (t, ox_pressure_psi, thrust_lbf))
    assert len(t) == len(ox_pressure_psi) == len(thrust_lbf), "t, pressure and thrust must share one time base"
    t_ign, t_end = heng.burn_window_from_thrust(t, thrust_lbf)
    in_burn = (t >= t_ign) & (t <= t_end)

    if fc["ox_method"] == "constant":
        ox_mdot = np.where(in_burn, fc["ox_mdot"], 0.0)
    elif fc["ox_method"] == "column":                    # pass an already-trimmed array
        ox_mdot = np.where(in_burn, np.asarray(ox_mdot_measured, dtype=float), 0.0)

    geom = dict(port_radius0_in=gc["initial_port_radius_in"], length_in=gc["length_in"],
                fuel_density_lbm_in3=gc["fuel_density_lbm_in3"], outer_radius_in=gc["outer_radius_in"])
    m = fc["fuel_method"]
    if m == "regression":
        a = fc.get("a") or heng.fit_regression_a(t, ox_mdot, fc["n"], fc["fuel_burned_lbm"], **geom)
        fuel_mdot = heng.fuel_mdot_from_regression(t, ox_mdot, a, fc["n"], **geom)
    elif m == "thrust":
        fuel_mdot = heng.fuel_mdot_from_thrust(t, thrust_lbf, ox_mdot,
                                               total_prop_burned_lbm=fc["total_prop_burned_lbm"])
    elif m == "of_ratio":                                
        fuel_mdot = ox_mdot / fc["of"]
    else:
        fuel_mdot = np.where(in_burn, fc["fuel_mdot"], 0.0)

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
        times_s=t, pressure_psi=ox_pressure_psi, mdot_lbm_s=ox_mdot,
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

def _ab_pad_arrays(ab_bundle, dt):
    '''Pre-launch rows of the airbrakes board as imperial arrays on the flight's sample spacing,
    in the form faa.ab_smooth(pre=...) expects. Returns None if the board log has no usable
    pre-launch segment. interpolate() hands pre_launch through untouched (still metric, native
    rate), so it is converted and resampled here to match the series it calibrates noise for.'''
    pre = getattr(ab_bundle, "pre_launch", None)
    if pre is None or len(pre) < 20:
        return None
    def mag(name):
        v = getattr(pre, name)
        return np.asarray(ld.to_imperial(v).magnitude if hasattr(v, "magnitude") else v, float)
    t_pre = mag("time")
    t_pre, keep = np.unique(t_pre, return_index=True)       # np.interp needs strictly increasing x
    grid = np.arange(t_pre[0], t_pre[-1], dt)
    if len(grid) < 20:
        return None
    cols = {"qw": "ab_qw", "qx": "ab_qx", "qy": "ab_qy", "qz": "ab_qz",
            "ax": "ab_ax", "ay": "ab_ay", "az": "ab_az"}
    if "ab_ekf_vz" in pre.columns:
        cols["v_up"] = "ab_ekf_vz"
    return {k: np.interp(grid, t_pre, mag(c)[keep]) for k, c in cols.items()}


def calculate(data_dict, cutoff_dict, rocket=None, smooth_ab=True, align_ab=True, ab_lag_s=None,
              ab_smooth_kwargs=None, out_csv="calc_data.csv"):
    '''smooth_ab : run the airbrakes board through faa.ab_smooth (Kalman/RTS) instead of raw accel.
    align_ab  : shift every ab_* series so its vertical velocity lines up with the Flight's
                (the two logs don't start at the same instant).
    ab_lag_s  : fix the board lag in seconds (positive = board is late) instead of estimating it.
    ab_smooth_kwargs : overrides for faa.ab_smooth, e.g. {'q_boost': 1e4, 'q_coast': 10}.
    out_csv   : where the calc table is written (per-flight name when running all flights).'''
    calc_array = []
    calc = {}
    titles = [...]
    print(data_dict)
    def find_bundle(substr):
        # prefer the alias group (set from the column headers), fall back to the key
        for k, b in data_dict.items():
            if getattr(b, "group", None) == substr:
                return b
        for k, b in data_dict.items():
            if substr in k.lower():
                return b
        raise KeyError(f"No dataset found with group or key containing '{substr}'")

    accel_bundle = find_bundle("accel")
    try:
        gyro_bundle = find_bundle("gyro")
        gyro_present = True
    except:
        print("no gyro data")
        gyro_present = False
    try:
        thrust_bundle = find_bundle("thrust")
        thrust_present = True
    except:
        print("no thrust data")
        thrust_present = False
    try:
        ras_bundle = find_bundle("ras")
        ras_present = True
    except: 
        print("no ras data")
        ras_present = False
        
    try:
        ork_bundle = find_bundle("ork")
        ork_present = True
    except:
        print("no ork data")
        ork_present = False

    try:
        ab_bundle = find_bundle("ab")
        ab_present = True
    except:
        print("no airbreaks data")
        ab_present = False

    pre = getattr(accel_bundle, "pre_launch", None)
    if pre is not None and len(pre) > 20:        # needs enough samples for the noise windows
        t_pre = pre.time.magnitude
        v_pre = pre.v_up.magnitude
    else:
        raise ValueError("No pre-launch data in the accel file. acceleration() needs a pad "
                        "segment to calibrate noise (v_pre cannot be empty).")

    time = accel_bundle.time.magnitude
    temperature = accel_bundle.temperature.magnitude
    pressure = accel_bundle.pressure.magnitude
    altitude = accel_bundle.altitude.magnitude          # ft
    v_up = accel_bundle.v_up.magnitude                  # ft/s
    v_dr = accel_bundle.v_dr.magnitude                  # ft/s
    v_cr = accel_bundle.v_cr.magnitude                  # ft/s
    tilt = accel_bundle.tilt.magnitude                  # deg

    if gyro_present:
        gx_accel = gyro_bundle.gx_accel.magnitude           # g_0
        gy_accel = gyro_bundle.gy_accel.magnitude           # g_0
        gz_accel = gyro_bundle.gz_accel.magnitude           # g_0
        gyro_y = gyro_bundle.gy.magnitude
    if thrust_present:
        spec_thrust = thrust_bundle.spec_thrust.magnitude   # N
    if ras_present:
        thrust = ras_bundle.ras_thrust.magnitude            # lbf
        weight = ras_bundle.weight.magnitude                # lbf
    if ork_present:
        ork_altitude = ork_bundle.ork_altitude.magnitude
        ork_accel_total = ork_bundle.ork_accel_total.magnitude
        ork_vel_total = ork_bundle.ork_vel_total.magnitude
        ork_aoa = ork_bundle.ork_aoa.magnitude
        ork_sm = ork_bundle.ork_sm.magnitude
        ork_fd = ork_bundle.ork_fd.magnitude
        ork_cd = ork_bundle.ork_cd.magnitude
        ork_pressure = ork_bundle.ork_pressure.magnitude
        ork_temperature = ork_bundle.ork_temperature.magnitude
        weight = ork_bundle.ork_mass.magnitude
    if ab_present:
        ab_ax = ab_bundle.ab_ax.magnitude
        ab_ay = ab_bundle.ab_ay.magnitude
        ab_az = ab_bundle.ab_az.magnitude
        ab_qw = ab_bundle.ab_qw.magnitude
        ab_qx = ab_bundle.ab_qx.magnitude
        ab_qy = ab_bundle.ab_qy.magnitude
        ab_qz = ab_bundle.ab_qz.magnitude
        ab_pressure = ab_bundle.ab_pressure.magnitude 
        ab_ekf_vz = ab_bundle.ab_ekf_vz.magnitude
        ab_altitude = ab_bundle.ab_altitude.magnitude if "ab_altitude" in ab_bundle.columns else None

    cutoff_dict["apogee"] = apogee = np.where(altitude >= max(altitude))[0][0]
    if thrust_present: cutoff_dict["coast"] = np.where(spec_thrust <= 5)[0][2]
    theta = faa.theta(v_dr, v_cr)

    calc["time"] = time
    calc["flight_angle"], calc["aoa"] = faa.angle(v_up, v_dr, v_cr, tilt)
    calc["theta"] = theta
    calc["accel_v"] = faa.magnitude(v_up, v_dr, v_cr)
    calc["sound"], calc["vel_mach"] = faa.v_mach(temperature, calc["accel_v"])
    calc["density"] = faa.density(pressure, temperature)
    calc["dyn_pressure"] = faa.dynamic_pressure(calc["density"], calc["accel_v"])
    #calc["ork_density"] = faa.density(ork_pressure, ork_temperature)
    #calc["ork_dyn_pressure"] = faa.dynamic_pressure(calc["ork_density"], ork_vel_total)
    calc["ax"], calc["ay"], calc["az"], calc["accel_total"] = faa.acceleration(v_up=v_up, v_dr=v_dr, v_cr=v_cr, apogee=apogee, t=calc["time"],
    t_pre=t_pre, v_pre=v_pre, burnout_idx=cutoff_dict["coast"],)
    #calc["accel_gs"] = faa.magnitude(gx_accel, gy_accel, gz_accel)
    #calc["accel_fts2"] = calc["accel_gs"] * 32.2
    calc["density"] = faa.density(pressure, temperature)

    # drag is the component along the relative wind
    calc["fdx"], calc["fdy"], calc["fdz"], calc["f_aero"] = faa.fd(spec_thrust, tilt, theta, weight, calc["ax"], calc["ay"], calc["az"])
    calc["fd"], calc["lift"] = faa.wind_axes(calc["fdx"], calc["fdy"], calc["fdz"], v_cr, v_dr, v_up)
    calc["cd"] = faa.cd(calc["fd"], calc["density"], calc["accel_v"])
    calc["fnx"], calc["fny"], calc["fnz"], calc["fn"] = faa.fn1(tilt, theta, weight, calc["ax"], calc["ay"], calc["az"])
    calc["cn"], calc["cna"] = faa.cna(calc["fn"], calc["density"], calc["accel_v"], calc["aoa"])
    calc["tilt"] = tilt

    # fd() derived from this same curve returns that curve. the two independent
    # sources are the ras condition file and the motor spec curve.
    if ras_present:
        calc["thrust_ras"] = thrust
    
    calc["thrust_spec"] = spec_thrust
    calc["altitude"] = altitude
    
    if (rocket != None):
        print(rocket.engine)          # is it None?
        print(rocket.mass_at(0), rocket.mass_at(rocket.engine.grain.times_s[-1] if rocket.engine else None))

        calc["cgs"] = geo.total_cg(rocket, calc["time"])
        calc["iyy"] = geo.total_iyy(rocket, calc["time"], calc["cgs"])
        if gyro_present:
            calc["sm"] = faa.stability(time, calc["iyy"], gyro_y, calc["fn"])

    if ork_present:
        calc["ork_altitude"] = ork_altitude
        calc["ork_vel_total"] = ork_vel_total
        calc["ork_accel_total"] = ork_accel_total
        calc["ork_aoa"] = ork_aoa
        calc["ork_fd"] = ork_fd
        calc["ork_cd"] = ork_cd
        calc["ork_sm"] = ork_sm
    else:
        print("skipping the ork")
    
    if ab_present:
        q = (ab_qw, ab_qx, ab_qy, ab_qz)
        t_ab = ab_bundle.time.magnitude
        axis = faa.body_axis_world(*q)
        if smooth_ab:
            # same kalman_smooth the Raven path uses, fusing the board's accelerometer with its EKF vz
            # R is calibrated on the board's own pre-launch (pad) rows, same process as the Raven path
            ab_pre = _ab_pad_arrays(ab_bundle, float(np.median(np.diff(t_ab))))
            sm = faa.ab_smooth(t_ab, *q, ab_ax, ab_ay, ab_az, v_up=ab_ekf_vz, pre=ab_pre,
                               **(ab_smooth_kwargs or {}))
            vx, vy, vz = sm["v"]
            awx, awy, awz = sm["a"]                            # coordinate acceleration (gravity removed)
        else:
            vx, vy, vz = faa.ab_velocity(*q, ab_ax, ab_ay, ab_az, t_ab, v_up=ab_ekf_vz)
            awx, awy, awz = faa.rotate_to_world(*q, ab_ax, ab_ay, ab_az)
            awz = awz - faa.G_FT                               # coordinate acceleration

        calc["ab_tilt"] = faa.tilt_from_quat(*q)
        calc["ab_accel_v"] = np.sqrt(vx**2 + vy**2 + vz**2)
        calc["ab_flight_angle"], calc["ab_aoa"] = faa.ab_flight_angles(vx, vy, vz, axis)
        off = faa.heading_offset(faa.ab_theta(vx, vy), theta, weights=calc["accel_v"] > 300)
        calc["ab_theta"] = faa.ab_theta(vx, vy, off)
        calc["ab_accel_total"] = np.sqrt(awx**2 + awy**2 + awz**2)
        if ab_altitude is not None:
            # board altitude is AGL from its own zero; put it on the Flight's (MSL) datum at t=0
            calc["ab_altitude"] = ab_altitude - ab_altitude[0] + altitude[0]
        th_body = np.degrees(np.arctan2(axis[:, 1], axis[:, 0]))

        fdx, fdy, fdz, _ = faa.fd(spec_thrust, calc["ab_tilt"], th_body, weight, awx, awy, awz)
        calc["ab_fd"], calc["ab_lift"] = faa.wind_axes(fdx, fdy, fdz, vx, vy, vz)
        _, _, _, calc["ab_fn"] = faa.fn1(calc["ab_tilt"], th_body, weight, awx, awy, awz)

        if align_ab:
            # board t=0 and Raven t=0 are different launch-detect events: line the two up on
            # vertical velocity (same quantity, scale/offset-free correlation) and shift every ab_*.
            dt_s = float(np.median(np.diff(calc["time"])))
            if ab_lag_s is None:
                lag_n, lag_s = faa.estimate_lag(calc["time"], v_up, vz)
            else:
                lag_n, lag_s = int(round(ab_lag_s / dt_s)), ab_lag_s
            print(f"[airbrakes] board lags Flight by {lag_s:+.3f} s ({lag_n:+d} samples); shifting ab_* series")
            for key in [k for k in calc if k.startswith("ab_")]:
                calc[key] = faa.shift_series(calc[key], lag_n)
    print(calc)
    df = pd.DataFrame.from_dict(calc)   
    df.to_csv(out_csv, index=False)

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
    'ab': 'Airbrakes board',
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
    [('theta', 'Pitch (°)'), ('tilt', 'Tilt (°)')],
    ]
    def _basics_row_builder(row):
        def builder(ax, col):
            if col >= len(BASICS_LAYOUT[row]):
                ax.axis('off')
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
    # One figure per comparison source (OpenRocket, Airbrakes board): Raw / Parity / Error rows
    # across every quantity both datasets have.
    #   reference = the series treated as truth for the parity x-axis and the error denominator:
    #     OpenRocket      -> the prediction is the reference (error = how far Flight is from the sim)
    #     Airbrakes board -> Flight (Raven) is the reference (error = how far the board is from it)
    OVERLAY_QUANTITIES = [('altitude', 'Altitude (ft)'), ('accel_v', 'Velocity (ft/s)'),
                          ('accel_total', 'Acceleration (ft/s²)'), ('aoa', 'AoA (°)'),
                          ('tilt', 'Tilt (°)')]
    OVERLAY_REFERENCE = {'ork': 'other', 'ab': 'flight'}
    ANGLE_KEYS = {'aoa', 'tilt'}            # error in degrees: a % of ~0° is meaningless
    ABS_COMPARE = {('ab', 'aoa')}           # board AoA is an unsigned 3-D angle; Flight's is signed
    DEFAULT_DENOM_MIN = {'altitude': 200, 'accel_v': 20, 'accel_total': 20}

    def _overlay_arrays(src, k, col):
        flight = mask(k)[start:apogee].copy()
        other = mask(col)[start:apogee].copy()
        if (src, k) in ABS_COMPARE:
            flight = np.abs(flight)
        return flight, other

    def _overlay_names(src, k):
        return ('Flight |AoA|' if (src, k) in ABS_COMPARE else 'Flight'), SOURCE_LABELS[src]

    def _unit_of(lbl):
        return lbl.split('(')[-1].rstrip(')') if '(' in lbl else ''

    def _raw_row(ax, spec):
        src, k, col, lbl = spec
        flight, other = _overlay_arrays(src, k, col)
        f_lbl, o_lbl = _overlay_names(src, k)
        tt = t[start:apogee]
        ax.plot(tt, flight, color='C0', label=f_lbl, alpha=0.85)
        ax.plot(tt, other, color='C1', label=o_lbl, alpha=0.85)
        ax.set(xlabel='Time (s)', ylabel=lbl, title=lbl)
        shade(ax, x_end=t[apogee])
        ax.legend(fontsize=7)

    def _parity_row(ax, spec):
        src, k, col, lbl = spec
        flight, other = _overlay_arrays(src, k, col)
        f_lbl, o_lbl = _overlay_names(src, k)
        if OVERLAY_REFERENCE[src] == 'flight':
            ref, test, x_lbl, y_lbl = flight, other, f_lbl, o_lbl
        else:
            ref, test, x_lbl, y_lbl = other, flight, o_lbl, f_lbl
        valid = np.isfinite(ref) & np.isfinite(test)
        boost = np.arange(start, apogee) < coast
        for sel, name, c in ((boost, 'boost', 'C0'), (~boost, 'coast', 'C2')):
            m = valid & sel
            if m.any():
                ax.scatter(ref[m], test[m], s=4, alpha=0.4, color=c, label=name)
        if valid.any():
            lo = min(ref[valid].min(), test[valid].min())
            hi = max(ref[valid].max(), test[valid].max())
            pad = (hi - lo) * 0.03 or 1
            ax.plot([lo, hi], [lo, hi], 'k--', linewidth=1, label='y = x')
            ax.set_xlim(lo - pad, hi + pad)
            ax.set_ylim(lo - pad, hi + pad)
        ax.set_aspect('equal', adjustable='box')
        u = _unit_of(lbl)
        ax.set(xlabel=f'{x_lbl} ({u})', ylabel=f'{y_lbl} ({u})')
        ax.legend(fontsize=7)

    def _err_row(ax, spec):
        src, k, col, lbl = spec
        flight, other = _overlay_arrays(src, k, col)
        ref, test = (flight, other) if OVERLAY_REFERENCE[src] == 'flight' else (other, flight)
        tt = t[start:apogee]
        if k in ANGLE_KEYS:
            err, unit, ylab = test - ref, '°', 'error (°)'
        else:
            denom_min = pct_err_denom_min.get(col, pct_err_denom_min.get(k, DEFAULT_DENOM_MIN.get(k, 1e-6)))
            err = np.divide(test - ref, ref, out=np.full_like(ref, np.nan),
                            where=np.abs(ref) > denom_min) * 100
            unit, ylab = '%', '% error'
        ax.plot(tt, err, color='C3')
        ax.axhline(0, color='black', linewidth=0.8)
        ax.set(xlabel='Time (s)', ylabel=ylab)
        shade(ax, x_end=t[apogee])
        fin = err[np.isfinite(err)]
        if fin.size:
            lo, hi = np.percentile(fin, [1, 99])        # clip the odd spike so it can't flatten the trace
            pad = (hi - lo) * 0.15 or 1
            ax.set_ylim(min(lo, 0) - pad, max(hi, 0) + pad)
            ax.text(0.02, 0.96, f'median {np.median(fin):+.1f}{unit}\nmean |err| {np.mean(np.abs(fin)):.1f}{unit}',
                    transform=ax.transAxes, va='top', fontsize=7,
                    bbox=dict(facecolor='white', alpha=0.7, edgecolor='none'))

    fig1b = {}
    for src in ('ork', 'ab'):
        specs = []
        for k, lbl in OVERLAY_QUANTITIES:
            col = overlay_group(k, data).get(src)
            if col:
                specs.append((src, k, col, lbl))
        if not specs:
            continue
        ref_name = 'Flight' if OVERLAY_REFERENCE[src] == 'flight' else SOURCE_LABELS[src]
        fig1b[src], _ = diagnostic_grid(
            column_specs=specs,
            row_builders=[_raw_row, _parity_row, _err_row],
            row_labels=['Raw', 'Parity', 'Error'],
            suptitle=f'{SOURCE_LABELS[src]} vs Flight: Raw / Parity / Error (reference = {ref_name})',
            col_width=4.2, row_height=3.8, hspace=0.5, top_margin=0.92, left_margin=0.07,
        )
    fig1b = fig1b or None

    # ---- page 2: Forces ----------------------------------------------------
    page2_items = [
        ('thrust_spec', 'Thrust (lbf)'), ('fd', 'Drag (lbf)'),
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
        y = mask(ykey)[start:apogee][valid_mask]        # was: data[ykey][start:apogee][valid_mask]
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
        }
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

ALL_FLIGHTS = "ALL FLIGHTS"


def graph_all_flights(results, start=15, mach_min=0.08, flight_vel_min=20, hold=10):
    """Overlay every flight of one rocket on the core quantities.
    results: {flight_folder: (data, areas)} as returned by calculate().
    Solid line = Flight (Raven); dashed = the airbrakes board for that flight, where present."""
    panels = [('altitude', 'Altitude (ft)'), ('accel_v', 'Velocity (ft/s)'),
              ('accel_total', 'Acceleration (ft/s²)'), ('aoa', 'AoA (°)')]
    fig, axs = plt.subplots(2, 3, figsize=(17, 9), squeeze=False)
    axs = axs.ravel()
    fig.suptitle(f'All flights ({len(results)})', fontweight='bold')

    for n, (flight, (data, areas)) in enumerate(results.items()):
        color = f'C{n % 10}'
        label = _flight_label(flight) or flight
        apogee, t = areas['apogee'], np.asarray(data['time'])
        lift = first_sustained_index(data['accel_v'], flight_vel_min, hold=hold) if 'accel_v' in data else 0
        for ax, (key, lbl) in zip(axs, panels):
            for col, ls, lw, lab in ((key, '-', 1.4, label), (f'ab_{key}', '--', 0.9, None)):
                if col not in data:
                    continue
                y = np.asarray(data[col], float).copy()
                if key == 'aoa':
                    y[:lift] = np.nan
                ax.plot(t[start:apogee], y[start:apogee], color=color, ls=ls, lw=lw, label=lab)
            ax.set(xlabel='Time (s)', ylabel=lbl, title=lbl)
        # CD vs Mach
        if 'cd' in data and 'vel_mach' in data:
            mach, cd = np.asarray(data['vel_mach'])[start:apogee], np.asarray(data['cd'])[start:apogee]
            ok = (mach >= mach_min) & np.isfinite(cd)
            axs[4].scatter(mach[ok], cd[ok], s=3, alpha=0.4, color=color)
        # drag force vs time
        if 'fd' in data:
            axs[5].plot(t[start:apogee], np.asarray(data['fd'], float)[start:apogee], color=color, lw=1.2)
    axs[4].set(xlabel='Mach', ylabel='CD', title='CD vs Mach')
    axs[5].set(xlabel='Time (s)', ylabel='Drag (lbf)', title='Drag')
    axs[0].legend(fontsize=8)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    plt.show()
    return fig


def _build_engine(engine_key, interpolated_data, find_bundle):
    """Build the engine for this rocket from its ROCKET_ENGINES config (hybrid or solid)."""
    cfg_all, kind = ROCKET_ENGINES[engine_key]
    if kind == "hybrid":
        of_case = "high_of"                                  # pick the O/F case in one place
        cfg = cfg_all[of_case]
        thrust_bundle = find_bundle(interpolated_data, "thrust")
        set_bundle = find_bundle(interpolated_data, "set")
        time = set_bundle['time'].magnitude
        thrust = thrust_bundle['spec_thrust'].magnitude
        pressure = set_bundle['run_tank_pressure'].magnitude
        _, t_end = heng.burn_window_from_thrust(time, thrust)
        keep = time <= t_end
        return build_hybrid(cfg, time[keep], pressure[keep], thrust[keep])
    if kind == "solid":
        thrust_bundle = find_bundle(interpolated_data, "thrust")
        accel_bundle = find_bundle(interpolated_data, "accel")
        return build_solid(cfg_all, accel_bundle['time'], thrust_bundle['spec_thrust'])
    return None                                              # e.g. "liquid": no builder yet


def run_flight(rocket_name, flight, engine_key, tag=""):
    """Full pipeline for one flight folder: load -> resolve duplicate files -> interpolate ->
    build engine -> calculate(). Returns (graph_values, cutoff_dict). `tag` prefixes the
    debug CSVs so flights don't overwrite each other."""
    folder = Path(BASE_DIR) / rocket_name / flight
    data = ld.load(rocket_name, flight)
    print("loaded:", {k: v.group for k, v in data.items()})  # before resolve_duplicates
    data = resolve_duplicates(data, folder)
    print("after resolve:", list(data))

    def find_bundle(data, substr):
        return ld.w_by_group(data, substr)

    candidates = [f for f in folder.iterdir() if f.name.lower().endswith('.xml')]
    if len(candidates) > 1:
        raise ValueError(f"Multiple suitable XML files found in {folder}: {candidates}")

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
        df.to_csv(f"{tag}interp_{entry}.csv", index=False)

    # The engine only feeds the CG/inertia/stability path, which needs rocket geometry that
    # calculate() isn't given yet (rocket=None). So it's optional: no config or missing files
    # just means no engine, and the flight still runs.
    engine_used = None
    if engine_key in ROCKET_ENGINES:
        try:
            engine_used = _build_engine(engine_key, interpolated_data, find_bundle)
        except Exception as e:
            print(f"[engine] skipped for {flight}: {type(e).__name__}: {e}")
    else:
        print(f"[engine] no config for '{engine_key}'; running without engine/geometry")
    #rocket = geo.Rocket.from_file(candidates[0], engine=engine_used)

    return calculate(interpolated_data, cutoff_dict, out_csv=f"{tag}calc_data.csv")


def main(per_flight_diagnostics=False):
    """per_flight_diagnostics: in ALL FLIGHTS mode, also open the full graph2() pages for every
    flight (one set of windows per flight). Off by default: you just get the overlay figure."""
    print(f"    .\n   .'.\n   |o|   Welcome to Comparinator!™\n  .'o'.  \033[3mFor all your comparing needs\033[0m\n  |.-.|\n  '   '\n   ( )\n    )\n   ( )")

    rockets = available_rockets()
    if not rockets:
        raise SystemExit(f"No rocket folders found under {BASE_DIR}")
    notes = {r: "" for r in rockets}
    rocket_name = _choose("Available rockets:", rockets, notes)

    engine_key = rocket_name.strip().lower()   # may not be in ROCKET_ENGINES; that's fine

    flights = available_flights(rocket_name)
    if not flights:
        raise SystemExit(f"No flight folders found for {rocket_name} under {BASE_DIR}")
    labels = {f: _flight_label(f) for f in flights}
    labels[ALL_FLIGHTS] = f"({len(flights)} flights, overlaid)"
    choice = _choose(f"Flights on record for {rocket_name}:", flights + [ALL_FLIGHTS], labels)

    if choice != ALL_FLIGHTS:
        graph_values, cutoff_dict = run_flight(rocket_name, choice, engine_key)
        graph2(graph_values, cutoff_dict)
        return

    results, failed = {}, {}
    for f in flights:
        print(f"\n===== {f} =====")
        try:
            results[f] = run_flight(rocket_name, f, engine_key, tag=f"{f}_")
        except Exception as e:                      # one bad flight shouldn't kill the batch
            failed[f] = f"{type(e).__name__}: {e}"
            print(f"Skipping {f}: {failed[f]}")
    if failed:
        print("\nSkipped flights:")
        for f, why in failed.items():
            print(f"  {f}: {why}")
    if not results:
        raise SystemExit("No flights could be processed.")
    if per_flight_diagnostics:
        for f, (graph_values, cutoff_dict) in results.items():
            graph2(graph_values, cutoff_dict)
    graph_all_flights(results)


if __name__ == "__main__":
    main()
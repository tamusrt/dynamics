import sympy as sp
import math
import matplotlib.pyplot as plt
import numpy as np 
from scipy.interpolate import interp1d
import pandas as pd
import flight_analysis_functions as faa
from scipy.signal import stft, hilbert

radius = 3
# x = 0 at the nose tip, increasing aft. offset is each part's forward face.
# plumbing and tail are still at station 0 and the engine internals total 0.83 in
# against a 50 in Engine.length; both need real measurements.
rocket = [
    faa.Component("nosetip", length=3.72, radius_= 0.5, mass=6.7/16, offset=0),
    faa.Component("nose_cone", length=30, radius_=3, mass=30.2/16, offset=3.72),
    faa.Component("payload", length=15.748, radius_=5.568/2, mass=70.4/16, offset=26),
    faa.Component("bulkhead", length=3, radius_=5.843/16, mass=0, offset=41.8), 
    faa.Component("straight", length=6, radius_=3, mass=9.4/16, offset=30),
    faa.Component("shoulder", length=8, radius_=5.845/2, mass=9.59/16, offset=34),
    faa.Component("forward", length=33.8, radius_=3, mass=50.9/16, offset=36),
    faa.Component("forward2", length=18.7, radius_=3, mass=17.4/16, offset=69.8),
    faa.Component("piston", length=6, radius_=3, mass=14.4/16, offset=58.5),
    faa.Engine(faa.EngineComponent(name="ox_tank", dry_mass=0.35, prop_mass=1.2, offset=0.0, length=0.45),faa.EngineComponent(name="plumbing", dry_mass=0.15, offset=0.45, length=0.08),faa.EngineComponent(name="fuel_grain", dry_mass=0.4, prop_mass=0.1, offset=0.53, length=0.30), length=50, offset=130),
    faa.Component("plumbing", length=7.13, radius_=3, mass=5.43/16, offset=0),
    faa.Component("tail", length=3, radius_=3, mass=5.43/16, offset=0),
    faa.Fins(mass=62.7/4/16,root_chord=15, tip_chord=3, span=5.25, offset=185, roll=0),
    faa.Fins(mass=62.7/4/16,root_chord=15, tip_chord=3, span=5.25, offset=185, roll=90),
    faa.Fins(mass=62.7/4/16,root_chord=15, tip_chord=3, span=5.25, offset=185, roll=180),
    faa.Fins(mass=62.7/4/16,root_chord=15, tip_chord=3, span=5.25, offset=185, roll=270),
    faa.Component("fin_can", length=18, radius_=6.055/3, mass=15/16, offset=183)
]
def load(name, flight):
    '''intakes the csv files, dropping the title'''
    with open(rf"G:\Shared drives\TAMU-SRT\srt_general\9_flight_data\{flight}\{name}.csv", "r") as new_file:
        lines = [line.split(",") for line in new_file.readlines()]
        opened = np.array(lines[1:]) # list of rows as floats
        if "accel" in name:
            opened = np.delete(opened, [*range(0,4), 5, 8, *range(10, 15),*range(22, len(lines[0]))], axis=1)
            lines[0] = np.delete(lines[0], [*range(0,4), 5, 8, *range(10, 15), *range(22, len(lines[0]))], axis=0)
        elif "gyro" in name:
            opened = np.delete(opened, [*range(0,4), 5, *range(12, len(lines[0]))], axis=1)
            lines[0] = np.delete(lines[0], [*range(0,4), 5, *range(12, len(lines[0]))], axis=0)
        opened = opened.astype(float)
        if "accel" in name or "gyro" in name:
            index = np.where(opened[:,0]>=0)[0][0]
            opened = opened[index:] 
    return opened, lines[0]

def interpolate(data, names):
    '''interpolates the other data sets based on the finest one, need to fix: xtending time values based on longest data collection, filling with zeros (assumes that zero is either appropriate or non-ocurring prior to apogee)'''
    # taking in pre-existing list of list
    raw_arrays = data 
    new_arrays = {}
    cutoffs = {}
    # finding finest dataset  
    global step
    step = min(float(np.diff(arr[:, 0]).min()) for arr in raw_arrays) # minimum difference between the rows in the first column
    for i, dataset in enumerate(raw_arrays):
        time_val = dataset[:, 0]
        column_data = []
        for column in dataset.T[1:]: # flips into rows
            interp_func = interp1d(time_val, column, kind = "linear")
            data_pt = interp_func(np.clip(np.arange(time_val[0], time_val[-1] + step, step), time_val[0], time_val[-1])) #  python rounding errors 
            column_data.append(data_pt) # list of arrays representing each column  
        if list(time_val) != list(np.arange(time_val[0], time_val[-1] + step, step)): # unless its the finest set  
            time_val = np.arange(time_val[0], time_val[-1] + step, step)
        column_data = np.array(column_data) # array again 
        new_arrays[names[i]] = np.vstack((time_val,column_data)).T # flips 
        cutoffs[names[i]] = dataset.shape[0] + 2
        length = max(len(new_arrays[array]) for array in new_arrays)
    for key, value in new_arrays.items():
        pad_amount = length - len(value)
        if pad_amount <= 0:
            continue
        padded = np.zeros((length, value.shape[1]))
        padded[:len(value)] = value
        # value columns pad with zeros, time carries on at the same step
        padded[len(value):, 0] = value[-1, 0] + step * np.arange(1, pad_amount + 1)
        new_arrays[key] = padded
    return new_arrays, cutoffs

def calculate(data_dict, cutoff_dict):
    calc_array = []
    calc = {}
    titles = [f"Time (sec)","Flight Angle (deg)", "AOA (deg)", "Theta (deg)", "Velocity, Accelerometer (ft/s)", "Sound ft/s",
              "Velocity, Accelerometer (mach)", "X-Acceleration, Accelerometer (ft/s^2)", 
              "Y-Acceleration, Accelerometer (ft/s^2)", "Z-Acceleration, Accelerometer (ft/s^2)",
              "Acceleration, Accelerometer (ft/s^2)", 
              "Acceleration, Gyrometer (ft/s^2)","Discrepancy", "Density (lbs/ft^3)",
              "Fdx", "Fdy", "Fdz", "Fd", "Cd", "Frequency", "Fnx", "Fny", "Fnz", "Fn", 
              "Cn", "Cna", "Thrust","Frequency","Thrust"]
    
    # values from csvs

    time, temperature, pressure, altitude, v_up, v_dr, v_cr, in_a, in_dr, in_cr, tilt = [data_dict[key] for key in data_dict if "accel" in key][0].T 
    gyro_x, gyro_y, gyro_z, gx_accel, gy_accel, gz_accel = [data_dict[key] for key in data_dict if "gyro" in key][0].T[1:] 
    assumed_thrust, r_accel, weight = [data_dict[key] for key in data_dict if "ras" in key][0].T[1:]
    thrust = [data_dict[key] for key in data_dict if "ras" in key][0][:, 1] # would be switched out
    spec = [data_dict[key] for key in data_dict if "SPEC" in key][0]
    thrust_spec = np.interp(time, spec[:, 0], spec[:, 1]) / 4.4482216152605 # newtons to lbf

    # stages of flight
    engine = [p for p in rocket if isinstance(p, faa.Engine)][0]
    engine.set_curve(thrusts=thrust, times=time)
    cutoff_dict["apogee"] = apogee = np.where([data_dict[key] for key in data_dict if "accel" in key][0][:,3] >= max(altitude))[0][0]
    peak = int(np.argmax(thrust)) # burnout is the first sample below threshold after the peak
    cutoff_dict["coast"] = peak + int(np.where(thrust[peak:] <= 5)[0][0])
    cutoff_dict["uppies"] = 0
    theta = faa.theta(v_dr,v_cr)
    sample_rate = 1/step 

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
    calc["thrust_spec"] = thrust_spec
    calc["altitude"] = altitude
    calc["cgs"] = faa.total_cg(rocket, calc["time"])[1]
    calc["iyy"] = faa.total_iyy(rocket, calc["time"], calc["cgs"])
    calc["sm"] = faa.stability(time, calc["iyy"], gyro_y, calc["fn"])
    calc["sm2"] = faa.stability1(faa.frequency(calc["aoa"], sample_rate, calc["time"]), calc["iyy"], calc["accel_v"], calc["density"], calc["aoa"], calc["fn"])
    #calc["check"] = faa.ndcheck_no_gyro(in_a, in_dr, in_cr, calc["time"], weight/32.2, thrust, apogee)
    #calc["double_check"] = faa.ndcheck_with_aoa(in_a, in_dr, in_cr, time, weight/32.2, thrust, calc["aoa"])
    #max_len = max(len(v) for v in calc.values())
    #fixed_data = {k: v[:max_len] for k, v in calc.items()}

    df = pd.DataFrame.from_dict(calc)   
    df.to_csv(f"calc_data.csv", index=False)

    return calc, cutoff_dict

def graph(data, areas):
    coast, apogee = areas["coast"], areas["apogee"]
    t = data['time']
    print(t[:apogee])
    def shade(ax, x_end=None):
        ax.axvspan(0, t[coast], alpha=0.15, color='lightblue')
        ax.axvspan(t[coast], t[apogee], alpha=0.15, color='pink')
        if x_end is not None:
            ax.set_xlim(0, x_end)

    def plot_to(ax, xarr, yarr, **kwargs):
        ax.plot(xarr[:apogee], yarr[:apogee], **kwargs)

    def scatter_to(ax, xarr, yarr):
        x, y = xarr[:apogee], yarr[:apogee]
        ax.scatter(x, y, s=4)
        pad_x = (max(x) - min(x)) * 0.05 or 1
        ax.set_xlim(min(x) - pad_x, max(x) + pad_x)

    def fit_ylim(ax, key):
        y = data[key][:apogee]; pad = (max(y) - min(y)) * 0.05 or 1
        ax.set_ylim(min(y) - pad, max(y) + pad)

    # page 1
    fig1, axs = plt.subplots(3, 2, figsize=(12, 10))
    fig1.suptitle('Basics', fontweight='bold')
    for ax, (k, lbl) in zip(axs.flat, [
        ('altitude','Altitude (ft)'), ('accel_v','Velocity (ft/s)'), ('accel_total','Acceleration (ft/s²)'),
        ('aoa','AoA (°)'), ('theta','Pitch (°)'), ('flight_angle','Flight Angle (°)')
    ]):
        plot_to(ax, t, data[k]); ax.set(xlabel='Time (s)', ylabel=lbl, title=lbl)
        shade(ax, x_end=t[apogee]); fit_ylim(ax, k)
    fig1.tight_layout()

    # page 2
    fig2, axs = plt.subplots(1, 3, figsize=(14, 4))
    fig2.suptitle('Forces', fontweight='bold')
    for ax, (ka, lbl) in zip(axs, [
        ('thrust_ras','Thrust (lbf)'), ('fd','Drag (lbf)'), ('fn','Normal Force (lbf)')]):
        plot_to(ax, t, data[ka], label='RAS' if ka == 'thrust_ras' else 'Flight')
        if ka == 'thrust_ras':
            plot_to(ax, t, data['thrust_spec'], label='Motor spec') 
        ax.set(xlabel='Time (s)', ylabel=lbl, title=lbl)
        fit_ylim(ax, ka); shade(ax, x_end=t[apogee]); ax.legend()
    fig2.tight_layout()

    # page 3
    fig3, axs = plt.subplots(1, 3, figsize=(14, 4))
    fig3.suptitle('Drag Coefficient Studies', fontweight='bold')
    for ax, (xk, xl, ttl) in zip(axs, [
        ('time','Time (s)','CD vs Time'), ('aoa','AoA (°)','CD vs AoA'), ('vel_mach','Mach','CD vs Mach')
    ]):
        scatter_to(ax, data[xk], data['cd'])
        ax.set(xlabel=xl, ylabel='CD', title=ttl)
        if xk == 'time': shade(ax)
    fig3.tight_layout()

    # page 4
    fig4, axs = plt.subplots(1, 3, figsize=(14, 4))
    fig4.suptitle('Stability Studies', fontweight='bold')
    for ax, (xk, xl, ttl) in zip(axs, [
        ('time','Time (s)','SM vs Time'), ('cna','CNα','SM vs CNα'), ('vel_mach','Mach','SM vs Mach')
    ]):
        scatter_to(ax, data[xk], data['sm'])
        ax.set(xlabel=xl, ylabel='SM (cal)', title=ttl)
        if xk == 'time': shade(ax)
    fig4.tight_layout()

    plt.show()
    return fig1, fig2, fig3, fig4

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

def main():
    flight = rf"Morpheus\04232025_lone_star_cup"
    names = ["CONDITION_ras","BR_accel","BR_gyro","SPEC_thrust"]
    data = []
    titles = {}
    for name in names:
        array, titles[name] = load(name, flight)
        data.append(array)
    interpolated_data, cutoff_dict = interpolate(data, names) 
    for entry in interpolated_data:
        df = pd.DataFrame(interpolated_data[entry], columns=titles[entry])
        df.to_csv(f"interp_{entry}.csv", index=False)
    graph_values, cutoff_dict = calculate(interpolated_data, cutoff_dict)
    graph2(graph_values, cutoff_dict)


if __name__ == "__main__":
    main()
import sympy as sp
import math
import matplotlib.pyplot as plt
import numpy as np 
from scipy.interpolate import interp1d
import pandas as pd
import flight_analysis_functions as faa
from scipy.signal import stft, hilbert

radius = 3
rocket = [
    faa.Component("nosetip", length=6.51, radius_ = 0.9, mass=0.89, offset=107.49, local_cg=4.74),
    faa.Component("nosecone_vonkarman", length=29.99, mass=0.99, offset=83.86, local_cg=24.03),
    faa.Component("forward", length=18, mass=3, offset=65.86, local_cg=9),
    faa.Component("aft", length=42, mass=3, offset=35.15, local_cg=21),
    faa.Component("coupler", length=6, mass=3, offset=0, local_cg=3),
    faa.Component("payload", length=16.75,  mass=7.28, offset=50, hollow=False, local_cg=8.87),
    faa.Component("bay", length=6, mass=5, offset=60, hollow=False, local_cg=3),
    faa.Component("ring", length=2, mass= 1, hollow=False, offset=30, local_cg=1.5),
    faa.Engine(total_mass=24.03+6, end_mass=6, offset=0, length=7.6, flow_rate=0.34),
    faa.Fins(mass=1,root_chord=11, tip_chord=3, span=5.25, offset=3),
    faa.Fins(mass=1,root_chord=11, tip_chord=3, span=5.25, offset=3),
    faa.Fins(mass=1,root_chord=11, tip_chord=3, span=5.25, offset=3),
    faa.Fins(mass=1,root_chord=11, tip_chord=3, span=5.25, offset=3)]
sol_invictus = [
    faa.Component("nosetip", length=6, radius_= 0.4, mass=0.6, offset=200, local_cg=5)
]
def load(name):
    '''intakes the csv files, dropping the title'''
    with open(f"current_{name}.csv", "r") as new_file: #G:\Shared drives\TAMU-SRT\srt_13\3_Dynamics\5_Post Flight Analysis\input\current_{name}.csv
        lines = [line.split(",") for line in new_file.readlines()]
        opened = np.array(lines[1:]) # list of rows as floats
        if name == "accel":
            opened = np.delete(opened, [*range(0,4), 5, 8, *range(10, 15), 18, 19, 20, *range(22, len(lines[0]))], axis=1)
            lines[0] = np.delete(lines[0], [*range(0,4), 5, 8, *range(10, 15), 18, 19, 20, *range(22, len(lines[0]))], axis=0)
        opened = opened.astype(float)
        if name == "accel":
            index = np.where(opened[:,0]>=0)[0][0]
            opened = opened[index:] 
    return opened, lines[0] 

def interpolate(data, names):
    '''interpolates the other data sets based on the finest one, need to fix: xtending time values based on longest data collection, filling with zeros'''
    # taking in pre-existing list of list
    raw_arrays = data # list of raw data, now in array format
    new_arrays = {}
    cutoffs = {}
    # finding finest dataset  
    global step
    step = min([np.diff(raw_arrays[i][:, 0]).tolist() for i in range(len(raw_arrays))])[0]# minimum difference between the rows in the first column
    for i, dataset in enumerate(raw_arrays):
        time_val = dataset[:, 0]
        column_data = []
        # interpolate the values for each column 
        for column in dataset.T[1:]: # flips into rows
            interp_func = interp1d(time_val, column, kind = "linear")
            data_pt = interp_func(np.clip(np.arange(time_val[0], time_val[-1] + step, step), time_val[0], time_val[-1])) # np.clip used because of python rounding errors 
            column_data.append(data_pt) # list of arrays representing each column  
        if list(time_val) != list(np.arange(time_val[0], time_val[-1] + step, step)): # unless its the finest set  
            time_val = np.arange(time_val[0], time_val[-1] + step, step)
        column_data = np.array(column_data) # back into array 
        new_arrays[names[i]] = np.vstack((time_val,column_data)).T # flips again
        cutoffs[names[i]] = dataset.shape[0] + 2
    length = max([len(new_arrays[array]) for array in new_arrays])
    for key, value in new_arrays.items():
        new_arrays[key] = np.resize(value,(length,value.shape[1]))
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
              "Cn", "Cna", "Thrust","Frequency","Thrust"] #*[f"Component CG {c}" for c in components], "Total CG", "Inertia",
              #"Frequency"]
    
    # values from csvs
    time, temperature, pressure, altitude, v_up, v_dr, v_cr, tilt = data_dict["accel"].T 
    gyro_x, gyro_y, gyro_z, gx_accel, gy_accel, gz_accel = data_dict["gyro"][:,1:].T 
    thrust, r_accel, weight = data_dict["ras"][:,1:].T
    
    # stages of flight
    engine = [p for p in rocket if isinstance(p, faa.Engine)][0]
    cutoff_dict["apogee"] = apogee = np.where(data_dict["accel"][:,3] >= max(altitude))[0][0]
    cutoff_dict["coast"] = np.where(time >= engine.burn_time)[0][0]
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
    calc["fdx"], calc["fdy"], calc["fdz"], calc["fd"] = faa.fd(thrust, tilt, theta, weight, calc["ax"], calc["ay"], calc["az"])
    calc["cd"] = faa.cd(calc["fd"], calc["density"], calc["accel_v"])
    calc["fnx"], calc["fny"], calc["fnz"], calc["fn"] = faa.fn1(tilt, theta, weight, calc["ax"], calc["ay"], calc["az"])
    calc["cn"], calc["cna"] = faa.cna(calc["fn"], calc["density"], calc["accel_v"], calc["aoa"])
    calc["thrust_calc"] = faa.thrust(weight, theta, calc["ax"], calc["ay"], calc["az"], calc["fdx"], calc["fdy"], calc["fdz"])
    calc["thrust_raw"] = thrust
    calc["altitude"] = altitude
    calc["cgs"] = faa.total_cg(rocket, calc["time"])[1]
    calc["iyy"] = faa.total_iyy(rocket, calc["time"], calc["cgs"])
    calc["sm"] = faa.stability(time, calc["iyy"], gyro_y, calc["fn"])
    calc["sm2"] = faa.stability1(faa.frequency(calc["aoa"], sample_rate, calc["time"]), calc["iyy"], calc["accel_v"], calc["density"], calc["aoa"], calc["fn"])
    
    df = pd.DataFrame.from_dict(calc)   
    df.to_csv(f"calc_data.csv", index=False)

    # stages of flight
    engine = [p for p in rocket if isinstance(p, faa.Engine)][0]
    cutoff_dict["apogee"] = apogee = np.where(data_dict["accel"][:,3] >= max(altitude))[0][0]
    cutoff_dict["coast"] = np.where(calc["thrust_calc"] <= 10)[0][0]
    cutoff_dict["uppies"] = 0
    theta = faa.theta(v_dr,v_cr)
    sample_rate = 1/step 

    return calc, cutoff_dict

def graph(data, areas):
    coast, apogee = areas["coast"], areas["apogee"]
    print(coast, apogee)
    t = data['time']
    def shade(ax):
        t_coast  = data['time'][coast]  / (1/step)
        t_apogee = data['time'][apogee] / (1/step)
        print(t_coast,t_apogee)
        ax.axvspan(0,          t[coast],  alpha=0.15, color='lightblue')
        ax.axvspan(t[coast],   t[-1],     alpha=0.15, color='pink')
        ax.set_xlim(0, t[-1])

    def plot_to(ax, xarr, yarr, **kwargs):
        ax.plot(xarr[:apogee], yarr[:apogee], **kwargs)

    def scatter_to(ax, xarr, yarr):
        ax.scatter(xarr[:apogee], yarr[:apogee], s=4)

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
        shade(ax); fit_ylim(ax, k)
    fig1.tight_layout()

    # page 2
    fig2, axs = plt.subplots(1, 3, figsize=(14, 4))
    fig2.suptitle('Forces', fontweight='bold')
    for ax, (ka, lbl) in zip(axs, [
        ('thrust_calc','Thrust (lbf)'), ('fd','Drag (lbf)'), ('fn','Normal Force (lbf)')
    ]):
        plot_to(ax, t, data[ka], label='Actual')
        ax.set(xlabel='Time (s)', ylabel=lbl, title=lbl)
        fit_ylim(ax, ka); shade(ax); ax.legend()
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

def main():
    names = ["ras","accel","gyro"]
    data = []
    titles = {}
    for name in names:
        array, titles[name] = load(name)
        data.append(array)
    interpolated_data, cutoff_dict = interpolate(data, names) 
    for entry in interpolated_data:
        df = pd.DataFrame(interpolated_data[entry], columns=titles[entry])
        df.to_csv(f"interp_{entry}.csv", index=False)
    graph_values, cutoff_dict = calculate(interpolated_data, cutoff_dict)
    graph(graph_values, cutoff_dict)


if __name__ == "__main__":
    main()
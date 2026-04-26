import sympy as sp
import math
import matplotlib.pyplot as plt
import numpy as np 
from scipy.interpolate import interp1d
import pandas as pd
import flight_analysis_functions as faa
from scipy.signal import find_peaks

def load(name):
    '''intakes the csv files, dropping the title'''
    with open(f"current_{name}.csv", "r") as new_file:
        lines = [line.split(",") for line in new_file.readlines()]
        opened = [[float(x) for x in row] for row in lines[1:]] # list of rows as floats
    return opened, lines[0] 

def interpolate(data, names):
    '''interpolates the other data sets based on the finest one'''
    # taking in pre-existing list of list
    raw_arrays = [np.array(i) for i in data] # list of raw data, now in array format
    new_arrays = {}
    cutoffs = {}
    # finding finest dataset  
    step = min([np.diff(raw_arrays[i][:, 0]).tolist() for i in range(len(raw_arrays))])[0] # minimum difference between the rows in the first column
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
        column_data = np.array(column_data) # turns list into array 
        new_arrays[names[i]] = np.vstack((time_val,column_data)).T # stacks as columns are rows, then flips again
        cutoffs[names[i]] = dataset.shape[0]
    length = max([len(new_arrays[array]) for array in new_arrays])
    for key, value in new_arrays.items():
        new_arrays[key] = np.resize(value,(length,value.shape[1]))
    return new_arrays, cutoffs

def calculate(data_dict, cutoff_dict):
    calc_array = []
    titles = ["Time (sec)","Flight Angle (deg)", "AOA (deg)", "Velocity, Accelerometer (ft/s)", 
              "Velocity, Accelerometer (mach)", "X-Acceleration, Accelerometer (ft/s^2)", 
              "Y-Acceleration, Accelerometer (ft/s^2)", "Z-Acceleration, Accelerometer (ft/s^2)",
              "Acceleration, Accelerometer (ft/s^2)", "Acceleration, Gyrometer (Gs)",
              "Acceleration, Gyrometer (ft/s^2)","Discrepancy", "Density (lbs/ft^3)", "Surface Area",
              "Fdx", "Fdy", "Fdz", "Fd", "Cd"]
    time, temperature, pressure, v_up, v_dr, v_cr, tilt, altitude = data_dict["accel"].T
    gyro_x, gyro_y, gyro_z, gx_accel, gy_accel, gz_accel = data_dict["gyro"][:,1:].T #switching to this to be cleaner
    thrust, r_accel, weight = data_dict["ras"][:,1:].T
    theta = faa.theta(v_dr,v_cr)
    calc_array.append(time) # time
    calc_array.extend((faa.angle(v_up, v_dr, v_cr, tilt))) # flight angle and aoa 
    calc_array.append(faa.magnitude(v_up,v_dr, v_cr)) # velocity, accelerometer
    calc_array.append(calc_array[-1]/968.0000000009) # velocity mach
    calc_array.extend((faa.acceleration(v_cr, time),faa.acceleration(v_dr, time),faa.acceleration(v_up, time))) # acceleration components
    calc_array.append(faa.acceleration(faa.magnitude(v_cr,v_up, v_dr),calc_array[0])) # acceleration, total 
    calc_array.append(faa.magnitude(gx_accel,gy_accel,gz_accel)) # acceleration
    calc_array.append(calc_array[-1]*32.2) # acce
    calc_array.append(faa.discrepancy(calc_array[-1], calc_array[-3]))
    calc_array.append(faa.density(pressure, temperature))
    calc_array.append(faa.s_area(calc_array[2]))
    calc_array.extend(faa.fd(thrust, tilt, theta, weight, calc_array[5], calc_array[6], calc_array[7]))
    calc_array.append(faa.cd(calc_array[-1], calc_array[12], calc_array[3], calc_array[13]))
    # do cutoffs for shorter vals 
    # need cutoff for the cd when thrust becomes 0
    # aoa needs a cutoff for apogee
    cutoff_dict["aoa"] = np.where(data_dict["accel"][:,7:] == max(altitude))[0][0]
    print(cutoff_dict["aoa"])
    calc_array = np.array(calc_array).T
    df = pd.DataFrame(calc_array, columns=titles)
    df.to_csv(f"calculated_data.csv", index=False)
    return calc_array, cutoff_dict

def graph(calc_data, cutoff_dict):
    plt.plot(calc_data[:,0][:cutoff_dict["aoa"]], calc_data[:,2][:cutoff_dict["aoa"]])  
    plt.xlabel("Time (s)")
    plt.ylabel("Angle of Attack (deg)")
    plt.title("AoA vs Time Until Apogee")
    plt.grid(True)
    peaks, _ = find_peaks(calc_data[:,2])
    amplitudes = calc_data[:,2][peaks]
    log_decrements = np.log(amplitudes[:-1] / amplitudes[1:])
    delta = np.mean(log_decrements)
    zeta = delta / np.sqrt(4 * np.pi**2 + delta**2)
    print(f"Log decrement δ: {delta:.4f}")
    print(f"Damping ratio ζ: {zeta:.4f}")
    plt.plot(calc_data[:,0][peaks], amplitudes, 'ro--', label='Peak amplitudes')
    plt.yscale('log')
    plt.show()    
    return 0

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

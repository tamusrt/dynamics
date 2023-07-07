import numpy as np
import pandas as pd
from RASAeroWrapper import populate
import matplotlib.pyplot as plt

fin_cases = populate()
print()

max_stabs = []
roots = []
tips = []
chamfs = []
sweeps = []

for sim_num in range(1, len(fin_cases)+1):

    filename = f"fin_{sim_num}.CSV"
    csv = pd.read_csv(filename)

    initial_stab = csv["Stability Margin (cal)"][0]
    max_stab = max(csv["Stability Margin (cal)"])
    min_stab = min(csv["Stability Margin (cal)"])
    apogee = max(csv["Altitude (ft)"])
    if (max_stab < 7.5) and (initial_stab > 1.75):
        print(f"file_{sim_num} - rail: {initial_stab} max: {max_stab} min: {min_stab} apogee: {apogee}")

    max_stabs.append(max_stab)
    roots.append(fin_cases[sim_num-1][1])
    tips.append(fin_cases[sim_num-1][2])
    chamfs.append(fin_cases[sim_num-1][7])
    sweeps.append(fin_cases[sim_num-1][3]/(fin_cases[sim_num-1][1]-fin_cases[sim_num-1][2]))

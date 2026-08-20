import matplotlib.pyplot as plt
import pandas as pd

# ----------------------------------------------------------------------------------------------------
# Read data
csv = pd.read_csv("FlightProfile.CSV")
mach_vals = csv["Mach Number"][:3003]
time_vals = csv["Time (sec)"][:3003]
stability_vals = csv["Stability Margin (cal)"][:3003]
cp_vals = csv["CP (in)"][:3003]
cg_vals = csv["CG (in)"][:3003]

# ----------------------------------------------------------------------------------------------------
# Locate transonic and supersonic intervals
transonic_mach_vals = []
transonic_cp_vals = []
transonic_stab_vals  = []
transonic_times = []
for i in range(len(mach_vals)):
    if (0.9 <= mach_vals[i]):
        transonic_mach_vals.append(mach_vals[i])
        transonic_times.append(time_vals[i])
        transonic_cp_vals.append(cp_vals[i])
        transonic_stab_vals.append(stability_vals[i])

supersonic_mach_vals = []
supersonic_cp_vals = []
supersonic_times = []
supersonic_stab_vals = []
for i in range(len(mach_vals)):
    if (1.1 < mach_vals[i]):
        supersonic_mach_vals.append(mach_vals[i])
        supersonic_times.append(time_vals[i])
        supersonic_cp_vals.append(cp_vals[i])
        supersonic_stab_vals.append(stability_vals[i])

# ----------------------------------------------------------------------------------------------------
# First plot
ax1 = plt.subplot()
ax1.plot(time_vals, mach_vals, color="black", label="Mach Number")
ax1.plot(transonic_times, transonic_mach_vals, color='orange')
ax1.plot(supersonic_times, supersonic_mach_vals, color='red')
ax1.set_xlabel("Time (sec)")
ax1.set_ylabel("Mach Number")
ax1.vlines(transonic_times[0], ymin=0, ymax=transonic_mach_vals[0], color='grey', linestyles='--')
ax1.vlines(transonic_times[-1], ymin=0, ymax=transonic_mach_vals[-1], color='grey', linestyles='--')
ax1.vlines(supersonic_times[0], ymin=0, ymax=supersonic_mach_vals[0], color='grey', linestyles='--')
ax1.vlines(supersonic_times[-1], ymin=0, ymax=supersonic_mach_vals[-1], color='grey', linestyles='--')
plt.show()

# ----------------------------------------------------------------------------------------------------
# Second plot
ax1 = plt.subplot()
l1 = ax1.plot(time_vals, cp_vals, color="red", label="Cp")
l2 = ax1.plot(time_vals, cg_vals, color="blue", label="Cg")
ax1.set_ylim(107, 140)
ax1.vlines(transonic_times[0], ymin=0, ymax=transonic_cp_vals[0], color='grey', linestyles='--')
ax1.vlines(transonic_times[-1], ymin=0, ymax=transonic_cp_vals[-1], color='grey', linestyles='--')
ax1.vlines(supersonic_times[0], ymin=0, ymax=supersonic_cp_vals[0], color='grey', linestyles='--')
ax1.vlines(supersonic_times[-1], ymin=0, ymax=supersonic_cp_vals[-1], color='grey', linestyles='--')

ax1.set_xlabel("Time (sec)")
ax1.set_ylabel("Cp and Cg (in from nose)")

ax1.legend()
plt.show()

# ----------------------------------------------------------------------------------------------------
# Third plot
ax1 = plt.subplot()
l1 = ax1.plot(time_vals, stability_vals, label="Stability (Cal)", color='black')
ax1.vlines(transonic_times[0], ymin=0, ymax=transonic_stab_vals[0], color='grey', linestyles='--')
ax1.vlines(transonic_times[-1], ymin=0, ymax=transonic_stab_vals[-1], color='grey', linestyles='--')
ax1.vlines(supersonic_times[0], ymin=0, ymax=supersonic_stab_vals[0], color='grey', linestyles='--')
ax1.vlines(supersonic_times[-1], ymin=0, ymax=supersonic_stab_vals[-1], color='grey', linestyles='--')
ax1.set_xlabel("Time (sec)")
ax1.set_ylabel("Stability (Cal)")
plt.show()

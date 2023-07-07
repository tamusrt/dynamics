import matplotlib.pyplot as plt
import pandas as pd

um = pd.read_csv("um.CSV")
# csv_10mph_5 = pd.read_csv("f_10mph_5.CSV")
# csv_5mph_5 = pd.read_csv("f_5mph_5.CSV")

# csv_10mph_10 = pd.read_csv("f_10mph_10.CSV")
# csv_10mph_0 = pd.read_csv("f_10mph_0.CSV")

# Time
time = um["Time (sec)"][:5001]
stab = um["Stability Margin (cal)"][:5001]

plt.figure(0)
plt.plot(time, stab)
plt.xlim(0, 10)
plt.title("Stability", fontsize=16)
plt.xlabel("Time (sec)", fontsize=14)
plt.ylabel("Stability (cal)", fontsize=14)
plt.show()

# # Stability
# stab_20mph_5 = csv_20mph_5["Stability Margin (cal)"][:5001]
# stab_10mph_5 = csv_10mph_5["Stability Margin (cal)"][:5001]
# stab_5_mph_5 = csv_5mph_5["Stability Margin (cal)"][:5001]

# stab_10mph_10 = csv_10mph_10["Stability Margin (cal)"][:5001]
# stab_10mph_5 = csv_10mph_5["Stability Margin (cal)"][:5001]
# stab_10mph_0 = csv_10mph_0["Stability Margin (cal)"][:5001]

# # Apogee
# apo_20mph_5 = csv_20mph_5["Altitude (ft)"][:5001]
# apo_10mph_5 = csv_10mph_5["Altitude (ft)"][:5001]
# apo_5_mph_5 = csv_5mph_5["Altitude (ft)"][:5001]

# apo_10mph_10 = csv_10mph_10["Altitude (ft)"][:5001]
# apo_10mph_5 = csv_10mph_5["Altitude (ft)"][:5001]
# apo_10mph_0 = csv_10mph_0["Altitude (ft)"][:5001]

# # Stability Plots
# plt.figure(1)
# plt.plot(time, stab_20mph_5, label='20 mph')
# plt.plot(time, stab_10mph_5, label='10 mph')
# plt.plot(time, stab_5_mph_5, label='5 mph')
# plt.legend()
# plt.title("Stability Over Time With Varying Wind")
# plt.xlabel("Time (sec)")
# plt.ylabel("Stability (cal)")
# plt.show()

# plt.figure(2)
# plt.plot(time, stab_10mph_10, label='10 deg')
# plt.plot(time, stab_10mph_5, label='5 deg')
# plt.plot(time, stab_10mph_0, label='0 deg')
# plt.legend()
# plt.title("Stability Over Time With Varying Launch Angle")
# plt.xlabel("Time (sec)")
# plt.ylabel("Stability (cal)")
# plt.show()

# # Apogee Plots
# plt.figure(3)
# plt.plot(time, apo_20mph_5, label='20 mph')
# plt.plot(time, apo_10mph_5, label='10 mph')
# plt.plot(time, apo_5_mph_5, label='5 mph')
# plt.legend()
# plt.title("Altitude Over Time With Varying Wind")
# plt.xlabel("Time (sec)")
# plt.ylabel("Altitude (feet)")
# plt.show()

# plt.figure(4)
# plt.plot(time, apo_10mph_10, label='10 deg')
# plt.plot(time, apo_10mph_5, label='5 deg')
# plt.plot(time, apo_10mph_0, label='0 deg')
# plt.legend()
# plt.title("Altitude Over Time With Varying Launch Angle")
# plt.xlabel("Time (sec)")
# plt.ylabel("Altitude (feet)")
# plt.show()

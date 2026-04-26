import sympy as sp
import math
import matplotlib.pyplot as plt
import numpy as np
###
def angle(v_up, v_dr, v_cr, tilt):
    ang = np.degrees(np.arccos(v_up / np.sqrt(v_up**2 + v_dr**2 + v_cr**2)))
    aoa = tilt - ang
    return ang, aoa

def magnitude(x, y, z):
    return np.sqrt(x**2 + y**2 + z**2)

def acceleration(v,t):
    return np.insert(np.diff(v) / np.diff(t), 0, 0)

def discrepancy(gyro, accel):
    return gyro - accel

def theta(v_dr,v_cr):
    with np.errstate(divide='ignore', invalid='ignore'):
        result = np.degrees(np.arctan(v_dr/v_cr))
        result[~np.isfinite(result)] = 0  
    return result

def fd(thrust, tilt, theta, mass, accelx, accely, accelz):
    fdx = thrust * np.sin(np.radians(tilt)) * np.cos(np.radians(theta)) - mass/32.2 * accelx
    fdy = thrust * np.sin(np.radians(tilt)) * np.sin(np.radians(theta)) - mass/32.2 * accely
    fdz = thrust * np.sin(np.radians(tilt)) * np.sin(np.radians(theta)) - mass/32.2 * (accelz + 32.174)
    fd = magnitude(fdx, fdy, fdz)
    return fdx, fdy, fdz, fd

def s_area(aoa):
    return (26.145 * np.abs(np.cos(np.radians(aoa))) + np.abs(591.14 * np.sin(np.radians(aoa)))) / 12

def density1(time, altitude):
    if altitude < 36152:
        temperature = 59 - 0.00356 * altitude + 459.7
        pressure = 2116 * (temperature/518.6)^5.256
    elif altitude > 36152 and altitude < 82345:
        temperature = -70 + 459.7
        pressure = 473.1 * np.e^(1.73-0.000048*altitude)
    else:
        temperature = 59 - 0.00356 * altitude + 459.7
        pressure = 2116 * (temperature/518.6)^5.256
    return pressure/(1718*temperature)/32.2

def density2(time, pressure, altitude):
    temperature = np.where(
        altitude < 36152,
        59 - 0.00356 * altitude + 459.7,
        np.where(
            altitude < 82345,
            -70 + 459.7,
            59 - 0.00356 * altitude + 459.7
        )
    )
    return pressure*515.4/ (1718 * temperature)

def density(pressure, temperature):
    return (pressure * 2116.22)/(53.35*((temperature +459.67)))

def cd(fd,density,velocity,s_area):
    return fd**2/(density*velocity**2*s_area)

# import no less than 20 functions...
from __future__ import annotations
import sympy as sp
import math
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import savgol_filter, stft
from scipy.ndimage import uniform_filter1d
from dataclasses import dataclass, field
from typing import List, Tuple
from scipy import integrate as sci_integrate
from scipy.integrate import quad_vec
import pandas as pd
from filterpy.kalman import KalmanFilter

G_FT = 32.174   # ft/s^2, and the lbm-ft/(lbf-s^2) unit conversion
IN_PER_FT = 12

radius = 3

# math Functions 

def angle(v_up, v_dr, v_cr, tilt):
    '''flight angle and angle of attack'''
    speed = np.sqrt(v_up**2 + v_dr**2 + v_cr**2)
    ratio = np.divide(v_up, speed, out=np.zeros_like(speed, dtype=float), where=speed > 1e-9)
    ang = np.degrees(np.arccos(np.clip(ratio, -1, 1)))
    aoa = tilt - ang
    return ang, aoa

def magnitude(*args):
    args = np.array(args)
    return np.sqrt(sum(args**2))

def v_mach(temperature, v):
    rankine = temperature + 459.67
    sound = np.sqrt(1.4*1716*(rankine))
    return sound, v/sound

def velocity(x, y, z, t):
    vx = np.diff(x)/np.diff(t)
    vy = np.diff(y)/np.diff(t)
    vz = np.diff(z)/np.diff(t)
    return np.insert(magnitude(vx,vy,vz), 0, 0)

def pad_to(arr, n):
    '''right-pad with zeros to length n'''
    arr = np.asarray(arr, float)
    return arr[:n] if arr.size >= n else np.concatenate([arr, np.zeros(n - arr.size)])

def _kalman_smooth_velocity(v, t):
    '''Run a constant-acceleration KF + RTS smoother on a single velocity
    component. Returns smoothed acceleration (state index 1) for the
    same-length input v, t.'''
    n = len(v)
    dt = np.diff(t)
    dt = np.append(dt, dt[-1])  # pad so len(dt) == n, for step k -> k+1

    kf = KalmanFilter(dim_x=2, dim_z=1)  # state: [velocity, acceleration]
    kf.x = np.array([[v[0]], [0.0]])
    kf.P *= 100.0                         # initial state uncertainty

    kf.H = np.array([[1.0, 0.0]])         # we observe velocity directly

    # measurement noise: how noisy is your velocity estimate (units: (ft/s)^2)
    r_var = 1.0
    kf.R = np.array([[r_var]])

    # process noise: how much acceleration is "allowed" to change per step
    # (units: (ft/s^2)^2 per unit time) -- this is your main tuning knob
    q_accel_var = 400.0

    xs, covs = [], []
    for k in range(n):
        step_dt = dt[k]
        kf.F = np.array([[1.0, step_dt],
                          [0.0, 1.0]])
        # discretized white-noise-acceleration process noise model
        kf.Q = q_accel_var * np.array([
            [step_dt**4 / 4, step_dt**3 / 2],
            [step_dt**3 / 2, step_dt**2]
        ])

        kf.predict()
        kf.update(np.array([[v[k]]]))

        xs.append(kf.x.copy())
        covs.append(kf.P.copy())

    xs, covs = np.array(xs), np.array(covs)

    # backward RTS pass -- uses future data too, removes forward-pass lag
    Fs = [np.array([[1.0, dt[k]], [0.0, 1.0]]) for k in range(n)]
    Qs = [q_accel_var * np.array([
            [dt[k]**4 / 4, dt[k]**3 / 2],
            [dt[k]**3 / 2, dt[k]**2]
          ]) for k in range(n)]

    xs_smooth, _, _, _ = kf.rts_smoother(xs, covs, Fs=Fs, Qs=Qs)

    velocity_smoothed = xs_smooth[:, 0, 0]
    accel_smoothed = xs_smooth[:, 1, 0]
    return accel_smoothed


def acceleration(v_up, v_dr, v_cr, apogee, t):
    '''accelerations are computed to apogee via a constant-acceleration
    Kalman filter + RTS smoother; samples past apogee are zero padding'''
    accelx, accely, accelz = (
        pad_to(_kalman_smooth_velocity(v[:apogee], t[:apogee]), t.shape[0])
        for v in (v_cr, v_dr, v_up)
    )

    total = magnitude(accelx, accely, accelz)

    return accelx, accely, accelz, total

def ndcheck_no_gyro(in_a, in_dr, in_cr, t, mass, thrust, apogee, gravity=G_FT, eps=1e-8):

    # single derivative for velocity (less noisy than double-diff)
    
    vdr = np.gradient(in_dr[:apogee], t[:apogee])
    vcr = np.gradient(in_cr[:apogee], t[:apogee])
    va  = np.gradient(in_a[:apogee], t[:apogee])
    v_vec = np.stack([vdr, vcr, va], axis=-1)          # (N,3)
    v_mag = np.linalg.norm(v_vec, axis=-1, keepdims=True)
    x_hat = v_vec / np.maximum(v_mag, eps)              # avoid div by zero at apex/launch
    # acceleration
    adr = np.gradient(vdr, t[:apogee])
    acr = np.gradient(vcr, t[:apogee])
    aa  = np.gradient(va, t[:apogee])
    a_vec = np.stack([adr, acr, aa], axis=-1)           # (N,3)

    g_vec = np.array([0, 0, -gravity])
    fnet_vec = mass[:apogee, None]*a_vec - mass[:apogee, None]*g_vec  # (N,3)

    # normal direction: component of accel perpendicular to velocity
    a_dot_x = np.sum(a_vec * x_hat, axis=-1, keepdims=True)
    a_perp = a_vec - a_dot_x * x_hat
    a_perp_mag = np.linalg.norm(a_perp, axis=-1, keepdims=True)
    z_hat = a_perp / np.maximum(a_perp_mag, eps)

    faxial = np.sum(fnet_vec * x_hat, axis=-1)
    fn     = np.sum(fnet_vec * z_hat, axis=-1)
    fd     = thrust[:apogee] - faxial   # drag opposes the velocity vector

    return fn, fd

def ndcheck_with_aoa(in_a, in_dr, in_cr, t, mass, thrust, aoa, gravity=G_FT, eps=1e-8):
    """
    aoa: array of angle-of-attack values (radians), same length as t
    """
    aoa = aoa/180 * np.pi
    vdr = np.gradient(in_dr, t)
    vcr = np.gradient(in_cr, t)
    va  = np.gradient(in_a, t)
    v_vec = np.stack([vdr, vcr, va], axis=-1)
    v_mag = np.linalg.norm(v_vec, axis=-1, keepdims=True)
    x_hat_v = v_vec / np.maximum(v_mag, eps)

    adr = np.gradient(vdr, t)
    acr = np.gradient(vcr, t)
    aa  = np.gradient(va, t)
    a_vec = np.stack([adr, acr, aa], axis=-1)

    g_vec = np.array([0, 0, -gravity])
    fnet_vec = mass[:, None]*a_vec - mass[:, None]*g_vec

    # perpendicular direction (maneuver-plane normal), as before
    a_dot_x = np.sum(a_vec * x_hat_v, axis=-1, keepdims=True)
    a_perp = a_vec - a_dot_x * x_hat_v
    a_perp_mag = np.linalg.norm(a_perp, axis=-1, keepdims=True)
    n_hat = a_perp / np.maximum(a_perp_mag, eps)

    # rotate by AoA within the maneuver plane
    cos_a = np.cos(aoa)[:, None]
    sin_a = np.sin(aoa)[:, None]
    x_hat_body =  cos_a * x_hat_v + sin_a * n_hat
    z_hat_body = -sin_a * x_hat_v + cos_a * n_hat

    faxial = np.sum(fnet_vec * x_hat_body, axis=-1)
    fn     = np.sum(fnet_vec * z_hat_body, axis=-1)
    fd     = thrust - faxial            # drag opposes the velocity vector

    return fn, fd

def theta(v_dr,v_cr):
    '''azimuth of the velocity vector in the horizontal plane, degrees in (-180, 180]'''
    return np.nan_to_num(np.degrees(np.arctan2(v_dr, v_cr)))

def axial_unit(tilt, theta):
    '''body axial unit vector stacked as (..., 3), from tilt off vertical
    and horizontal azimuth'''
    st, ct = np.sin(np.radians(tilt)), np.cos(np.radians(tilt))
    return np.stack([st*np.cos(np.radians(theta)), st*np.sin(np.radians(theta)),
                     np.broadcast_to(ct, np.shape(st))], axis=-1)

def specific_force(accelx, accely, accelz):
    '''acceleration stacked as (..., 3) with gravity added back into z'''
    return np.stack([accelx, accely, accelz + G_FT], axis=-1)

def thrust(weight, theta, accel_x, accel_y, accel_z, Fdx, Fdy, Fdz):
    '''thrust implied by measured acceleration and a known aerodynamic force vector.
    this is the algebraic inverse of fd(), so it is an independent estimate
    only when Fd comes from an independent aerodynamic model'''
    mass = np.asarray(weight, float)/G_FT
    ft = mass[..., None] * specific_force(accel_x, accel_y, accel_z) \
         + np.stack([Fdx, Fdy, Fdz], axis=-1)
    return np.linalg.norm(ft, axis=-1)

def fd(thrust, tilt, theta, weight, accelx, accely, accelz):
    '''total aerodynamic force acting upon the rocket, based on in flight data with
    thrust being predicted. the returned vector points opposite the aerodynamic
    force; resolve it with wind_axes() for drag or with the body axis for axial force'''

    # finding force provided by thrust in respective directions, subtracting total force in that direction
    mass = np.asarray(weight, float)/G_FT
    f = np.asarray(thrust, float)[..., None] * axial_unit(tilt, theta) \
        - mass[..., None] * specific_force(accelx, accely, accelz)
    
    return f[..., 0], f[..., 1], f[..., 2], np.linalg.norm(f, axis=-1)

def wind_axes(fdx, fdy, fdz, v_cr, v_dr, v_up, eps=1e-9):
    '''resolve the aerodynamic force into drag along the relative wind and lift
    perpendicular to it. still air is assumed, so the relative wind is the
    vehicle velocity; subtract a measured wind vector from it when one is available'''
    f = np.stack([fdx, fdy, fdz], axis=-1)
    v = np.stack([v_cr, v_dr, v_up], axis=-1)
    vhat = v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), eps)
    drag = np.sum(f * vhat, axis=-1)
    lift = np.linalg.norm(f - drag[..., None] * vhat, axis=-1)
    return drag, lift

def fn1(tilt, theta, weight, ax, ay, az):
    '''finding the normal force acting on the rocket, based on accelerometer data'''
    # removing acceleration due to gravity, subtracting by including the angle 
    accel = specific_force(ax, ay, az)
    # finding axial unit vector, concerning how force is being distributed 
    axial = axial_unit(tilt, theta)
    # dot product of acceleration for axial acceleration
    a_axial = np.sum(accel * axial, axis=-1, keepdims=True)
    # subtract component, multuply by mass 
    Fn = (accel - a_axial * axial) * (np.asarray(weight, float)/G_FT)[..., None]
    return Fn[..., 0], Fn[..., 1], Fn[..., 2], np.linalg.norm(Fn, axis=-1)

def fn2(weight, g_accelx, g_accelz):
    '''finding the normal force acting on the rocket, assuming that the gyroscope is the acceleration'''
    return weight/G_FT * magnitude(g_accelx, g_accelz)

def density(pressure, temperature):
    '''density using ideal gas law, constants used reflect atm and F'''
    return (pressure * 2116.22)/(53.35*((temperature + 459.67)))

def dynamic_pressure(density, velocity):
    '''q in lbf/ft^2 from density in lbm/ft^3 and velocity in ft/s'''
    return 0.5 * density * velocity**2 / G_FT

def ref_area(diameter):
    '''diameter in feet'''
    return np.pi * (diameter/2)**2

def cd(fd, density, velocity, diameter=2*radius/IN_PER_FT, v_min=200):
    '''diameter in feet. v_min: minimum velocity (ft/s) below which Cd is
    considered unreliable due to vanishing dynamic pressure.'''
    q = dynamic_pressure(density, velocity)
    denom = q * ref_area(diameter)
    valid = np.abs(velocity) > v_min
    return np.divide(fd, denom, out=np.full_like(np.asarray(fd, float), np.nan),
                     where=valid)

def rolling_slope(xv, yv, window, min_var):
    '''rolling least squares slope dy/dx, nan safe and valid for a non-monotonic x'''
    good = np.isfinite(xv) & np.isfinite(yv)
    xs, ys = np.where(good, xv, 0.0), np.where(good, yv, 0.0)
    w = good.astype(float)
    def mean(a):
        s = uniform_filter1d(a, window, mode='nearest')
        c = uniform_filter1d(w, window, mode='nearest')
        return np.divide(s, c, out=np.full_like(s, np.nan), where=c > 0)
    mx, my = mean(xs), mean(ys)
    var, cov = mean(xs*xs) - mx*mx, mean(xs*ys) - mx*my
    ok = var > min_var
    return np.where(ok, cov / np.where(ok, var, 1.0), np.nan)

def cna(fn, density, velocity, aoa, diameter=2*radius/IN_PER_FT, window=201, min_spread_deg=0.25):
    '''normal force coefficent, and its slope with respect to angle of attack.
    aoa oscillates, so the slope is fitted by rolling least squares against a
    non-monotonic coordinate'''
    denom = dynamic_pressure(density, velocity) * ref_area(diameter)
    cn = np.divide(fn, denom, out=np.full_like(np.asarray(fn, float), np.nan),
                   where=np.abs(denom) > 1e-9)
    # derivative of normal coefficent, per radian
    cna = rolling_slope(np.radians(aoa), cn, window, np.radians(min_spread_deg)**2)
    return cn, cna

def frequency(aoa, sample_rate, target_time, f_min=0.2):
    '''natrual frequency, from short fourier transform functions to isolate the atrual frequency of aoa oscillations'''
    sig = np.nan_to_num(np.asarray(aoa, float) - np.nanmean(aoa)) # oscillation about the trim aoa
    f, t, Zxx = stft(sig, fs=sample_rate, window='hann', nperseg=256) # finding frequency, time bucket, and array values 
    keep = f >= f_min # the dc bin holds the trim offset, not an oscillation
    f, Zxx = f[keep], Zxx[keep, :]
    t_index = np.argmin(np.abs(t[:, np.newaxis] - target_time), axis=0) # index, finding what each time value is closest to for bin sorting
    power = np.abs(Zxx[:, t_index]) # powers at that value
    frequency_n = f[np.argmax(power, axis=0)] # dominant frequency
    return frequency_n

def stability1(frequency_n, inertia_yy, velocity, density, aoa, fn, diameter=2*radius/IN_PER_FT):
    '''static margin in calibers from the pitch oscillation frequency, where the
    corrective moment coefficient C1 = omega^2 Iyy = q A d CNalpha SM'''
    omega = 2 * np.pi * frequency_n                       # rad/s
    iyy_slug_ft2 = inertia_yy / (G_FT * IN_PER_FT**2)     # lbm-in^2 -> slug-ft^2
    m_corrective = omega**2 * iyy_slug_ft2                # lbf-ft
    cn_a = cna(fn, density, velocity, aoa, diameter=diameter)[1]
    denom = dynamic_pressure(density, velocity) * ref_area(diameter) * diameter * cn_a
    sm = np.divide(m_corrective, denom, out=np.full_like(m_corrective, np.nan),
                   where=np.abs(denom) > 1e-12)
    return sm 

def stability(time, inertia_yy, gyro_y, fn, diameter=2*radius/IN_PER_FT, window=71, regression=1001):
    '''static margin in calibers from pitch angular acceleration, Iyy qdot = Fn d SM.
    gyro_y is smoothed before differentiating, and the margin is taken as a rolling
    regression of the restoring moment on Fn d, which stays defined where both
    oscillate through zero'''
    gyro_smooth = savgol_filter(np.radians(gyro_y), window_length=window, polyorder=3)
    q_accel = np.gradient(gyro_smooth, time)              # rad/s^2
    iyy_slug_ft2 = inertia_yy / (G_FT * IN_PER_FT**2)     # lbm-in^2 -> slug-ft^2
    moment = iyy_slug_ft2 * q_accel                       # lbf-ft
    arm = np.asarray(fn, float) * diameter                # lbf-ft per caliber
    num = uniform_filter1d(moment * arm, regression, mode='nearest')
    den = uniform_filter1d(arm * arm, regression, mode='nearest')
    sm = np.divide(num, den, out=np.full_like(num, np.nan), where=np.abs(den) > 1e-12)
    return sm

### math functions ^
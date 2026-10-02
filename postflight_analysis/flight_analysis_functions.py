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

radius = 2.5

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

### WE INTERRUPT THIS PROGRAM TO BRING YOU: smoothed velocity values do do do 
# measurement noise (R): find the flat segment, use to find noise estimate that can be applied across stages and change accordingly
def still_pad(pre_v, hold=3, min_steps=3, sigma_mult=5):
    '''find 'v' from the pre-liftoff values so that we can define where it actually is still, that 
    will be used later to estimate what the actual sensor noise is, calibrating it when its supposed to be flat 

    cuts off the first quarter of `v` (assumed still) to get a
    rough noise level, finds an approximate onset, then refines once using
    the trimmed segment. Returns (v_pad_trimmed, onset_index_or_None).'''
    def find_onset(std_ref, center):
        threshold = sigma_mult * std_ref
        if threshold <= 0:
            nonzero_diffs = np.abs(np.diff(pre_v))
            nonzero_diffs = nonzero_diffs[nonzero_diffs > 0]
            floor = nonzero_diffs.min() if nonzero_diffs.size else 1.0
            threshold = sigma_mult * floor
        deviation = np.abs(pre_v - center)        
        for i in range(len(deviation) - hold + 1):
            if (deviation[i:i + hold] >= threshold).all():
                return i
        return None

    quarter = max(hold + 1, len(pre_v) // 4)
    rough_std = np.std(pre_v[:quarter])
    rough_center = np.median(pre_v[:quarter])
    onset = find_onset(rough_std, rough_center)

    v_pad = pre_v if onset is None else pre_v[:onset]

    refined_std = np.std(v_pad)
    refined_center = np.median(v_pad)
    onset2 = find_onset(refined_std, refined_center)

    if onset2 is not None:
        v_pad = pre_v[:onset2]
    return v_pad, (onset2 if onset2 is not None else onset)

def estimate_noise(v, window=41, poly_order=2):
    '''find a rolling sensor noise variance for each time stamp, fit a polynominal, treat residual as noise. 
    assumes that true signal is smooth compared to window length. decides noise changes over the flight (e.g. motor vibration,
    transonic buffet) instead using average.'''
    n = len(v)
    half = window // 2
    r_series = np.full(n, np.nan) # measurement noise array
    for i in range(half, n - half): 
        idx = np.arange(i - half, i + half + 1)
        coeffs = np.polyfit(idx, v[idx], poly_order)
        fit = np.polyval(coeffs, idx)
        r_series[i] = np.var(v[idx] - fit)
    if n > 2 * half: # if an odd amount, fix that shit 
        r_series[:half] = r_series[half]
        r_series[-half:] = r_series[-half - 1]
    else:
        r_series[:] = np.nanvar(v)  # clone, find the variance excepting where window too wide for array
    return r_series

def calibrate_noise_window(v_pad, window_options=(21, 41, 61, 81), poly_order=2, verbose=True):
    '''grabs window size where rolling estimate produces the closest to TRUE
    noise variance on the pad segment. where velocity all of its variance is considered noise.
    Returns (best_window, true_pad_variance).'''
    true_r = np.var(v_pad)
    best_window, best_diff = window_options[0], np.inf
    for w in window_options:
        if w >= len(v_pad):
            continue
        est = estimate_noise(v_pad, window=w, poly_order=poly_order) # when the window is smaller 
        diff = abs(np.nanmedian(est) - true_r)
        if verbose:
            print(f'window={w}: detrended est={np.nanmedian(est):.4f}, true={true_r:.4f}')
        if diff < best_diff:
            best_diff, best_window = diff, w
    return best_window, true_r

def build_r_series(v_pre, v_flight, window_options=(21, 41, 61, 81), poly_order=2,
                             hold=3, sigma_mult=5, verbose=True):
    '''trims pre-liftoff data down to the true flat segment, calibrates a detrending window and applies across
    flight. floors result at the pad's true noise level, since
    baseline sensor noise doesn't disappear while moving.'''
    v_pad, onset_idx = still_pad(v_pre, hold=hold, sigma_mult=sigma_mult)
    print(f'v_pre length={len(v_pre)}, onset_idx={onset_idx}, '
      f'v_pad length={len(v_pad)}, v_pad var={np.var(v_pad) if len(v_pad) else "EMPTY"}')
    best_window, r_floor = calibrate_noise_window(v_pad, window_options, poly_order, verbose)
    r_series = estimate_noise(v_flight, window=best_window, poly_order=poly_order)
    r_series = np.maximum(r_series, r_floor)
    return r_series, {'onset_idx': onset_idx, 'window': best_window, 'r_floor': r_floor}

# process noise covariance matrix (Q): how much it should be able to vary
def _forward_nis(t, v, r_v, q, dt=None, p0=100.0):
    '''Forward-only KF pass (no RTS -- NIS is about one-step prediction
    consistency, which the backward smoother would hide) with a SINGLE q
    applied throughout. Returns the NIS value at each sample.'''
    n = len(v)
    r_v_arr = np.full(n, r_v, float) if np.isscalar(r_v) else np.asarray(r_v, float)
    if dt is None:
        dt = np.diff(t, prepend=t[0] - (t[1] - t[0]))
 
    def F_of(d):
        return np.array([[1.0, d], [0.0, 1.0]])
 
    def Q_of(d):
        return q * np.array([[d**4 / 4, d**3 / 2], [d**3 / 2, d**2]])
 
    H = np.array([[1.0, 0.0]])
    x = np.array([v[0], 0.0])
    P = np.eye(2) * p0
    nis = np.full(n, np.nan)
 
    for k in range(n):
        F, Q = F_of(dt[k]), Q_of(dt[k])
        x, P = F @ x, F @ P @ F.T + Q            # predict
        S = (H @ P @ H.T)[0, 0] + r_v_arr[k]      # predicted innovation variance
        innov = v[k] - (H @ x)[0]                 # actual innovation
        nis[k] = innov**2 / S
        K = (P @ H.T / S).flatten()               # update
        x = x + K * innov
        P = (np.eye(2) - np.outer(K, H)) @ P
    return nis
 
def calibrate_q(t, v, r_v, burnout_idx, q_boost_options, q_coast_options, warmup=20):
    '''Grid-search q_boost against the boost-segment NIS, then q_coast against
    the coast-segment NIS (with q_boost fixed to whatever was just chosen).
    `warmup` skips the first N samples of each segment, since NIS is
    unreliable right after the filter starts or right after Q switches,
    before P has settled.
 
    Returns (best_q_boost, best_q_coast, diagnostics) where diagnostics has
    the full score table for both sweeps, for plotting/sanity-checking.
    '''
    t, v = np.asarray(t, float), np.asarray(v, float)
    dt = np.diff(t, prepend=t[0] - (t[1] - t[0]))
    n = len(v)
 
    # --- sweep q_boost: score against NIS within the boost segment only ---
    boost_scores = []
    for q_b in q_boost_options:
        nis = _forward_nis(t, v, r_v, q_b, dt=dt)
        seg = nis[warmup:burnout_idx]
        score = abs(np.nanmean(seg) - 1.0)
        boost_scores.append(score)
    best_q_boost = q_boost_options[int(np.argmin(boost_scores))]
 
    # --- sweep q_coast: run with q_boost fixed, switch to q_coast at burnout,
    #     score against NIS within the coast segment only ---
    def forward_nis_boost_coast(q_boost, q_coast):
        r_v_arr = np.full(n, r_v, float) if np.isscalar(r_v) else np.asarray(r_v, float)
        F_of = lambda d: np.array([[1.0, d], [0.0, 1.0]])
        Q_of = lambda d, q: q * np.array([[d**4/4, d**3/2], [d**3/2, d**2]])
        H = np.array([[1.0, 0.0]])
        x = np.array([v[0], 0.0]); P = np.eye(2) * 100.0
        nis = np.full(n, np.nan)
        for k in range(n):
            q = q_boost if k < burnout_idx else q_coast
            F, Q = F_of(dt[k]), Q_of(dt[k], q)
            x, P = F @ x, F @ P @ F.T + Q
            S = (H @ P @ H.T)[0, 0] + r_v_arr[k]
            innov = v[k] - (H @ x)[0]
            nis[k] = innov**2 / S
            K = (P @ H.T / S).flatten()
            x = x + K * innov
            P = (np.eye(2) - np.outer(K, H)) @ P
        return nis
 
    coast_scores = []
    for q_c in q_coast_options:
        nis = forward_nis_boost_coast(best_q_boost, q_c)
        seg = nis[burnout_idx + warmup:]
        score = abs(np.nanmean(seg) - 1.0)
        coast_scores.append(score)
    best_q_coast = q_coast_options[int(np.argmin(coast_scores))]
 
    diagnostics = {
        'q_boost_options': list(q_boost_options), 'boost_scores': boost_scores,
        'q_coast_options': list(q_coast_options), 'coast_scores': coast_scores,
        'boost_at_edge': int(np.argmin(boost_scores)) in (0, len(boost_scores) - 1),
        'coast_at_edge': int(np.argmin(coast_scores)) in (0, len(coast_scores) - 1),
    }
    if diagnostics['boost_at_edge']:
        print(f'WARNING: best q_boost ({best_q_boost:g}) is at the edge of '
              f'q_boost_options -- the true optimum may be outside this range. '
              f'Widen q_boost_options and rerun.')
    if diagnostics['coast_at_edge']:
        print(f'WARNING: best q_coast ({best_q_coast:g}) is at the edge of '
              f'q_coast_options -- the true optimum may be outside this range. '
              f'Widen q_coast_options and rerun.')
    return best_q_boost, best_q_coast, diagnostics

# kalman filter application: state used is velocity (no acceleration rn), Q switches between different stages q, noise value found above changing by different pieces 
def kalman_smooth(t, v, a=None, r_v=1.0, r_a=1.0, q_boost=4000.0, q_coast=50.0,
                   burnout_idx=None, p0=100.0):
    '''
    finds (velocity_smoothed, acceleration_smoothed).
    '''
    t = np.asarray(t, float)
    n = len(t)
    v = np.asarray(v, float) # velocity measurements, during flight
    a = np.full(n, np.nan) if a is None else np.asarray(a, float) # acceleration measurements (optional)
    r_v_arr = np.full(n, r_v, float) if np.isscalar(r_v) else np.asarray(r_v, float) # noise variance in velocity (based on sesnor being a jerk)
    burnout_idx = n if burnout_idx is None else burnout_idx # where burnout is

    dt = np.diff(t, prepend=t[0] - (t[1] - t[0]))  # dt[k] = step INTO sample k

    def F_of(d):
        return np.array([[1.0, d], [0.0, 1.0]])

    def Q_of(d, q): # process noise covariance matrix, defining how much change is reasonable?
        return q * np.array([[d**4 / 4, d**3 / 2], [d**3 / 2, d**2]])

    def q_at(k): # placing definition(based on whats actually happening) 
        return q_boost if k < burnout_idx else q_coast

    x = np.array([v[0], 3 * G_FT])   # state: [velocity, acceleration]; accel guess at 3G because of when liftoff starts 
    P = np.eye(2) * p0       # pad, initial state uncertainty

    xs_f, Ps_f, xs_p, Ps_p, Fs, Qs = [], [], [], [], [], []
    for k in range(n):
        F = F_of(dt[k])
        Q = Q_of(dt[k], q_at(k))
        Fs.append(F); Qs.append(Q)

        x, P = F @ x, F @ P @ F.T + Q             # predict
        xs_p.append(x.copy()); Ps_p.append(P.copy())

        rows, zs, rs = [], [], []                  # stack whichever sensors reported
        if np.isfinite(v[k]):
            rows.append([1.0, 0.0]); zs.append(v[k]); rs.append(r_v_arr[k])
        if np.isfinite(a[k]):
            rows.append([0.0, 1.0]); zs.append(a[k]); rs.append(r_a)
        if rows:                                    # update
            H, z, R = np.array(rows), np.array(zs), np.diag(rs)
            S = H @ P @ H.T + R
            K = P @ H.T @ np.linalg.inv(S)
            x = x + K @ (z - H @ x)
            P = (np.eye(2) - K @ H) @ P
        xs_f.append(x.copy()); Ps_f.append(P.copy())

    # RTS backward pass -- each estimate also uses FUTURE data, removing
    # forward-pass lag (e.g. the filter "knowing" burnout is about to happen)
    xs, Ps = np.array(xs_f), np.array(Ps_f)
    for k in range(n - 2, -1, -1):
        F = Fs[k + 1]
        P_pred = Ps_p[k + 1]
        C = Ps_f[k] @ F.T @ np.linalg.inv(P_pred)
        xs[k] = xs_f[k] + C @ (xs[k + 1] - xs_p[k + 1])
        Ps[k] = Ps_f[k] + C @ (Ps[k + 1] - P_pred) @ C.T

    return xs[:, 0], xs[:, 1]
import numpy as np


# ===========================================================================
# Part 1: Measurement-noise (R) estimation
#
# Workflow: find the true pre-liftoff flat segment, use it to validate a
# detrending-based noise estimator, then apply that estimator across the
# whole flight so R can vary by stage (e.g. worse during motor burn) instead
# of being a single guessed constant.
# ===========================================================================
def trim_to_true_pad(v, hold=10, sigma_mult=5):
    '''`v` is your pre-liftoff velocity slice (e.g. v[:blue_raven_liftoff_idx]).
    The Raven's own liftoff flag is conservative, so real motion (ignition,
    initial rail travel) likely starts before that flag trips. This finds
    where velocity actually departs from pad noise, not where the Raven's
    3G/3ft-s threshold fires, so what you calibrate noise against is
    genuinely flat/zero.

    Bootstraps off the first quarter of `v` (assumed safely still) to get a
    rough noise level, finds an approximate onset, then refines once using
    the trimmed segment. Returns (v_pad_trimmed, onset_index_or_None).

    ALWAYS sanity-check onset_index against a plot of v before trusting it
    -- a handling bump or wind gust can look like sustained motion.
    '''
    def find_onset(std_ref, center):
        threshold = sigma_mult * std_ref
        if threshold <= 0:
            # Degenerate case: the reference segment has exactly zero
            # variance (e.g. integer-quantized sensor readings sitting
            # dead-flat at rest, like Blue Raven velocity reporting exact
            # 0s on the pad). A literal zero threshold makes even
            # zero-deviation samples satisfy `deviation >= threshold`
            # (0 >= 0 is True), falsely declaring onset at sample 0.
            # Fall back to the sensor's own quantization step -- the
            # smallest nonzero sample-to-sample change anywhere in v --
            # so "no measurable variation yet" isn't mistaken for
            # "constant deviation from center."
            nonzero_diffs = np.abs(np.diff(v))
            nonzero_diffs = nonzero_diffs[nonzero_diffs > 0]
            floor = nonzero_diffs.min() if nonzero_diffs.size else 1.0
            threshold = sigma_mult * floor
        deviation = np.abs(v - center)
        # `+ 1` so the final possible window -- the one ending exactly at
        # the array's last sample -- actually gets tested. Without it,
        # motion occurring only in the last `hold` samples (exactly the
        # case where liftoff is right at the end of a short pre-liftoff
        # buffer) can never be detected, no matter how real/large it is.
        for i in range(len(deviation) - hold + 1):
            if (deviation[i:i + hold] >= threshold).all():
                return i
        return None

    quarter = max(hold + 1, len(v) // 4)
    rough_std = np.std(v[:quarter])
    rough_center = np.median(v[:quarter])
    onset = find_onset(rough_std, rough_center)

    v_pad = v if onset is None else v[:onset]

    refined_std = np.std(v_pad)
    refined_center = np.median(v_pad)
    onset2 = find_onset(refined_std, refined_center)

    if onset2 is not None:
        v_pad = v[:onset2]
    return v_pad, (onset2 if onset2 is not None else onset)


def estimate_measurement_noise_series(v, window=41, poly_order=2):
    '''Rolling sensor-noise-variance estimate, one value per sample. Fits a
    low-order polynomial to each short window and treats the residual as
    noise -- valid as long as the true signal is smooth relative to the
    window length, which is usually true even during boost at a few hundred
    Hz. Captures noise that changes over the flight (e.g. motor vibration,
    transonic buffet) instead of averaging it into one global number.
    Edge samples (within `window//2` of either end) reuse the nearest
    computed value.'''
    n = len(v)
    half = window // 2
    r_series = np.full(n, np.nan)
    for i in range(half, n - half):
        idx = np.arange(i - half, i + half + 1)
        coeffs = np.polyfit(idx, v[idx], poly_order)
        fit = np.polyval(coeffs, idx)
        r_series[i] = np.var(v[idx] - fit)
    if n > 2 * half:
        r_series[:half] = r_series[half]
        r_series[-half:] = r_series[-half - 1]
    else:
        r_series[:] = np.nanvar(v)  # window too wide for this array; fallback
    return r_series


def calibrate_noise_window(v_pad, window_options=(21, 41, 61, 81), poly_order=2, verbose=True,
                            quantization_reference=None):
    '''Pick the window size whose rolling estimate best reproduces the TRUE
    noise variance on the pad segment (where the answer is known: velocity
    truly isn't changing, so all of its variance IS noise). A window that
    matches here is trustworthy elsewhere, since you can't check directly
    once the rocket is actually moving.

    quantization_reference: an array to derive the sensor's quantization
        step from, if v_pad itself is perfectly flat (zero internal
        variation, so v_pad's own diffs can't reveal the real step size).
        Pass the untrimmed pre-launch buffer here (which contains the real
        transition) rather than relying on v_pad alone. Falls back to
        v_pad's own diffs, then to 1.0, only if this isn't provided.

    Returns (best_window, true_pad_variance).'''
    true_r = np.var(v_pad)
    if true_r == 0:
        # A perfectly flat pad segment doesn't mean the sensor has zero
        # noise -- it means real noise is smaller than what the sensor can
        # resolve (its quantization step), so measured variance reads as
        # exactly 0. Treating that literal 0 as the true noise floor makes
        # R ~ 0 downstream, which tells the Kalman filter to trust every
        # reading completely -- it stops smoothing and just reproduces raw
        # noise. Fall back to the standard uniform-quantization-noise
        # estimate (step^2 / 12).
        source = quantization_reference if quantization_reference is not None else v_pad
        nonzero_diffs = np.abs(np.diff(source))
        nonzero_diffs = nonzero_diffs[nonzero_diffs > 0]
        step = nonzero_diffs.min() if nonzero_diffs.size else 1.0
        true_r = step**2 / 12
        if verbose:
            print(f'pad variance was exactly 0 -- falling back to quantization '
                  f'floor: step={step}, r_floor={true_r:.6g}')
    best_window, best_diff = window_options[0], np.inf
    for w in window_options:
        if w >= len(v_pad):
            continue
        est = estimate_measurement_noise_series(v_pad, window=w, poly_order=poly_order)
        diff = abs(np.nanmedian(est) - true_r)
        if verbose:
            print(f'window={w}: detrended est={np.nanmedian(est):.4f}, true={true_r:.4f}')
        if diff < best_diff:
            best_diff, best_window = diff, w
    return best_window, true_r


def build_r_series(v, liftoff_idx, window_options=(21, 41, 61, 81), poly_order=2,
                    hold=10, sigma_mult=5, verbose=True):
    '''End-to-end: trim pre-liftoff data down to the true flat segment,
    calibrate a detrending window against it, then apply that window across
    the whole flight. Floors the result at the pad's true noise level, since
    baseline sensor noise doesn't disappear once the sensor is moving --
    anything the rolling estimator reports below that floor is more likely a
    fitting artifact than real physics.

    Assumes the pad segment lives INSIDE `v`, before `liftoff_idx` (i.e. `t=0`
    is somewhere on the pad). If your flight data starts AT the liftoff flag
    instead (t=0 defined as the moment acceleration crosses threshold, so
    there's no pre-liftoff data in `v` at all), use
    build_r_series_from_pad() instead, with a genuinely separate pad array.
    '''
    v_pad, onset_idx = trim_to_true_pad(v[:liftoff_idx], hold=hold, sigma_mult=sigma_mult)
    best_window, r_floor = calibrate_noise_window(v_pad, window_options, poly_order, verbose)
    r_series = estimate_measurement_noise_series(v, window=best_window, poly_order=poly_order)
    r_series = np.maximum(r_series, r_floor)
    return r_series, {'onset_idx': onset_idx, 'window': best_window, 'r_floor': r_floor}


def build_r_series_from_pad(v_pre, v_flight, window_options=(21, 41, 61, 81), poly_order=2,
                             hold=10, sigma_mult=5, verbose=True):
    '''Same idea as build_r_series, but for the case where the pad segment
    is a SEPARATE array from the flight data being smoothed -- e.g. when
    t=0 in your flight data is defined as the liftoff-detected instant, so
    there's no pre-liftoff data inside it to slice out.

    v_pre    : a genuinely pre-liftoff velocity segment (its own t_pre array
               governs its timing; only the values matter here).
    v_flight : the flight-data array to actually estimate a noise series for
               (e.g. v_up[:apogee]) -- does NOT need any pad samples in it.
    '''
    v_pad, onset_idx = trim_to_true_pad(v_pre, hold=hold, sigma_mult=sigma_mult)
    best_window, r_floor = calibrate_noise_window(
        v_pad, window_options, poly_order, verbose,
        quantization_reference=v_pre,  # v_pad alone may be perfectly flat (zero
                                        # internal variation) if onset trimmed out
                                        # all real motion -- v_pre still has the
                                        # real transition to derive a step from.
    )
    r_series = estimate_measurement_noise_series(v_flight, window=best_window, poly_order=poly_order)
    r_series = np.maximum(r_series, r_floor)
    return r_series, {'onset_idx': onset_idx, 'window': best_window, 'r_floor': r_floor}


# ===========================================================================
# Part 2: Fused, boost/coast-aware Kalman smoother
#
# State: [velocity, acceleration]. Velocity is always observed (from v, the
# corrected Blue Raven output). Acceleration is optionally ALSO observed
# directly (pass `a`) if you have a second sensor -- the filter then fuses
# both instead of only ever inferring acceleration from velocity's shape.
#
# Q (process noise) switches between q_boost and q_coast at `burnout_idx`,
# since boost has large real acceleration changes sample-to-sample and
# coast doesn't -- a single Q has to compromise between tracking real boost
# transients and not chasing coast-phase noise.
#
# R (measurement noise) for velocity can be a single scalar OR a per-sample
# array (e.g. from build_r_series above), so noise that changes by flight
# stage is reflected rather than averaged into one constant.
# ===========================================================================
def kalman_smooth(t, v, a=None, r_v=1.0, r_a=1.0, q_boost=4000.0, q_coast=50.0,
                   burnout_idx=None, p0=100.0):
    '''
    t          : time array
    v          : velocity measurements (required) -- e.g. corrected Blue Raven v
    a          : acceleration measurements (optional) -- pass if you have a
                 second sensor to fuse; a[k] = NaN means "no reading at k"
    r_v        : velocity measurement-noise variance. Scalar or array of len(t)
                 (use build_r_series() for the array form).
    r_a        : acceleration measurement-noise variance (scalar; extend to an
                 array the same way as r_v if that sensor's noise also varies).
    q_boost    : process-noise variance (accel allowed to change per unit
                 time) before `burnout_idx`. Tune against real thrust-curve
                 sharpness -- too small and the boost peak gets smeared/lagged.
    q_coast    : process-noise variance from `burnout_idx` onward. Tune down
                 until ripple in the smoothed output disappears without
                 visibly lagging the real (slow) coast-phase decay.
    burnout_idx: sample index where boost ends / coast begins (your 'coast'
                 index elsewhere in the codebase). None = q_boost the whole way.
    p0         : initial state uncertainty. Not very sensitive -- the RTS
                 backward pass revises early estimates anyway. Pick roughly
                 (plausible initial guess error)^2; 100 means "I could be off
                 by about 10 units" on both velocity and acceleration.

    Returns (velocity_smoothed, acceleration_smoothed).
    '''
    t = np.asarray(t, float)
    n = len(t)
    v = np.asarray(v, float)
    a = np.full(n, np.nan) if a is None else np.asarray(a, float)
    r_v_arr = np.full(n, r_v, float) if np.isscalar(r_v) else np.asarray(r_v, float)
    burnout_idx = n if burnout_idx is None else burnout_idx

    dt = np.diff(t, prepend=t[0] - (t[1] - t[0]))  # dt[k] = step INTO sample k

    def F_of(d):
        return np.array([[1.0, d], [0.0, 1.0]])

    def Q_of(d, q):
        return q * np.array([[d**4 / 4, d**3 / 2], [d**3 / 2, d**2]])

    def q_at(k):
        return q_boost if k < burnout_idx else q_coast

    x = np.array([v[0], 0.0])   # state: [velocity, acceleration]; accel guess
    P = np.eye(2) * p0          # starts at 0 -- corrected within a few samples

    xs_f, Ps_f, xs_p, Ps_p, Fs, Qs = [], [], [], [], [], []
    for k in range(n):
        F = F_of(dt[k])
        Q = Q_of(dt[k], q_at(k))
        Fs.append(F); Qs.append(Q)

        x, P = F @ x, F @ P @ F.T + Q             # predict
        xs_p.append(x.copy()); Ps_p.append(P.copy())

        rows, zs, rs = [], [], []                  # stack whichever sensors reported
        if np.isfinite(v[k]):
            rows.append([1.0, 0.0]); zs.append(v[k]); rs.append(r_v_arr[k])
        if np.isfinite(a[k]):
            rows.append([0.0, 1.0]); zs.append(a[k]); rs.append(r_a)
        if rows:                                    # update
            H, z, R = np.array(rows), np.array(zs), np.diag(rs)
            S = H @ P @ H.T + R
            K = P @ H.T @ np.linalg.inv(S)
            x = x + K @ (z - H @ x)
            P = (np.eye(2) - K @ H) @ P
        xs_f.append(x.copy()); Ps_f.append(P.copy())

    # RTS backward pass -- each estimate also uses FUTURE data, removing
    # forward-pass lag (e.g. the filter "knowing" burnout is about to happen)
    xs, Ps = np.array(xs_f), np.array(Ps_f)
    for k in range(n - 2, -1, -1):
        F = Fs[k + 1]
        P_pred = Ps_p[k + 1]
        C = Ps_f[k] @ F.T @ np.linalg.inv(P_pred)
        xs[k] = xs_f[k] + C @ (xs[k + 1] - xs_p[k + 1])
        Ps[k] = Ps_f[k] + C @ (Ps[k + 1] - P_pred) @ C.T

    return xs[:, 0], xs[:, 1]


# ===========================================================================
# Part 3: Process-noise (Q) calibration
#
# R was calibrated against something KNOWN (the pad, where true velocity is
# genuinely flat). Q needs the same treatment instead of a guessed round
# number. The check used here: a correctly-tuned filter's own prediction
# error should match how uncertain the filter THINKS it is. That ratio is
# the normalized innovation squared (NIS). Averaged over many samples, NIS
# should be close to 1 if Q (and R) are consistent with reality:
#   NIS >> 1 : real errors are bigger than the filter expects -> Q too small
#              (filter is overconfident, e.g. output looks laggy/oversmoothed
#              relative to real transients)
#   NIS << 1 : real errors are smaller than the filter expects -> Q too large
#              (filter is underconfident, e.g. output looks noisy/jittery)
# This needs no external reference (no OpenRocket curve, no known-still
# segment) -- it checks the filter against its own measurements.
# ===========================================================================
def _forward_nis(t, v, r_v, q, dt=None, p0=100.0):
    '''Forward-only KF pass (no RTS -- NIS is about one-step prediction
    consistency, which the backward smoother would hide) with a SINGLE q
    applied throughout. Returns the NIS value at each sample.'''
    n = len(v)
    r_v_arr = np.full(n, r_v, float) if np.isscalar(r_v) else np.asarray(r_v, float)
    if dt is None:
        dt = np.diff(t, prepend=t[0] - (t[1] - t[0]))

    def F_of(d):
        return np.array([[1.0, d], [0.0, 1.0]])

    def Q_of(d):
        return q * np.array([[d**4 / 4, d**3 / 2], [d**3 / 2, d**2]])

    H = np.array([[1.0, 0.0]])
    x = np.array([v[0], 0.0])
    P = np.eye(2) * p0
    nis = np.full(n, np.nan)

    for k in range(n):
        F, Q = F_of(dt[k]), Q_of(dt[k])
        x, P = F @ x, F @ P @ F.T + Q            # predict
        S = (H @ P @ H.T)[0, 0] + r_v_arr[k]      # predicted innovation variance
        innov = v[k] - (H @ x)[0]                 # actual innovation
        nis[k] = innov**2 / S
        K = (P @ H.T / S).flatten()               # update
        x = x + K * innov
        P = (np.eye(2) - np.outer(K, H)) @ P
    return nis


def calibrate_q(t, v, r_v, burnout_idx, q_boost_options, q_coast_options, warmup=20):
    '''Grid-search q_boost against the boost-segment NIS, then q_coast against
    the coast-segment NIS (with q_boost fixed to whatever was just chosen).
    `warmup` skips the first N samples of each segment, since NIS is
    unreliable right after the filter starts or right after Q switches,
    before P has settled.

    Returns (best_q_boost, best_q_coast, diagnostics) where diagnostics has
    the full score table for both sweeps, for plotting/sanity-checking.
    '''
    t, v = np.asarray(t, float), np.asarray(v, float)
    dt = np.diff(t, prepend=t[0] - (t[1] - t[0]))
    n = len(v)

    # --- sweep q_boost: score against NIS within the boost segment only ---
    boost_scores = []
    for q_b in q_boost_options:
        nis = _forward_nis(t, v, r_v, q_b, dt=dt)
        seg = nis[warmup:burnout_idx]
        score = abs(np.nanmean(seg) - 1.0)
        boost_scores.append(score)
    best_q_boost = q_boost_options[int(np.argmin(boost_scores))]

    # --- sweep q_coast: run with q_boost fixed, switch to q_coast at burnout,
    #     score against NIS within the coast segment only ---
    def forward_nis_boost_coast(q_boost, q_coast):
        r_v_arr = np.full(n, r_v, float) if np.isscalar(r_v) else np.asarray(r_v, float)
        F_of = lambda d: np.array([[1.0, d], [0.0, 1.0]])
        Q_of = lambda d, q: q * np.array([[d**4/4, d**3/2], [d**3/2, d**2]])
        H = np.array([[1.0, 0.0]])
        x = np.array([v[0], 0.0]); P = np.eye(2) * 100.0
        nis = np.full(n, np.nan)
        for k in range(n):
            q = q_boost if k < burnout_idx else q_coast
            F, Q = F_of(dt[k]), Q_of(dt[k], q)
            x, P = F @ x, F @ P @ F.T + Q
            S = (H @ P @ H.T)[0, 0] + r_v_arr[k]
            innov = v[k] - (H @ x)[0]
            nis[k] = innov**2 / S
            K = (P @ H.T / S).flatten()
            x = x + K * innov
            P = (np.eye(2) - np.outer(K, H)) @ P
        return nis

    coast_scores = []
    for q_c in q_coast_options:
        nis = forward_nis_boost_coast(best_q_boost, q_c)
        seg = nis[burnout_idx + warmup:]
        score = abs(np.nanmean(seg) - 1.0)
        coast_scores.append(score)
    best_q_coast = q_coast_options[int(np.argmin(coast_scores))]

    diagnostics = {
        'q_boost_options': list(q_boost_options), 'boost_scores': boost_scores,
        'q_coast_options': list(q_coast_options), 'coast_scores': coast_scores,
        'boost_at_edge': int(np.argmin(boost_scores)) in (0, len(boost_scores) - 1),
        'coast_at_edge': int(np.argmin(coast_scores)) in (0, len(coast_scores) - 1),
    }
    if diagnostics['boost_at_edge']:
        print(f'WARNING: best q_boost ({best_q_boost:g}) is at the edge of '
              f'q_boost_options -- the true optimum may be outside this range. '
              f'Widen q_boost_options and rerun.')
    if diagnostics['coast_at_edge']:
        print(f'WARNING: best q_coast ({best_q_coast:g}) is at the edge of '
              f'q_coast_options -- the true optimum may be outside this range. '
              f'Widen q_coast_options and rerun.')
    return best_q_boost, best_q_coast, diagnostics


# ===========================================================================
# Part 4: Per-axis acceleration, calibrated end to end
# ===========================================================================
def acceleration(v_up, v_dr, v_cr, apogee, t, t_pre, v_pre, burnout_idx,
                  q_boost_options=(1e4, 1e5, 1e6, 1e7, 1e8),
                  q_coast_options=(1e1, 1e2, 1e3, 1e4)):
    '''Accelerations are computed to apogee via a constant-acceleration
    Kalman filter + RTS smoother; samples past apogee are zero-padded.

    t, v_up, v_dr, v_cr : flight data starting AT liftoff (t=0 is the
        instant acceleration crosses the 3G liftoff threshold) -- these do
        NOT contain any pre-liftoff/pad samples.
    t_pre, v_pre        : a SEPARATE segment of genuinely pre-liftoff data
        (recorded before the 3G flag fired), used only to calibrate
        measurement noise. `t_pre` isn't used numerically here beyond
        existing as the natural pairing for v_pre -- pass it in case you
        later want noise estimates that account for uneven pre-liftoff
        sample spacing.
    burnout_idx         : sample index (within t/v_*, i.e. relative to
        liftoff) where boost ends and coast begins.

    NOTE: only one pre-liftoff velocity series is available (v_pre), so it's
    used as the noise-calibration reference for ALL THREE axes below. If you
    have separate pre-liftoff data for the horizontal axes too, pass each
    one through its own build_r_series_from_pad call instead -- horizontal
    pad noise could look different from vertical.
    '''
    n_total = t.shape[0]

    # NOTE: build_r_series_from_pad() does its own trim_to_true_pad() call
    # internally, and specifically needs the RAW v_pre (not an already-
    # trimmed v_pad) to derive a quantization-noise floor when the trimmed
    # segment turns out perfectly flat -- a pre-trimmed array has zero
    # internal variation by definition, so there'd be nothing left to
    # derive a real step size from. Pass v_pre through untouched; any
    # "trim once outside the loop" optimization here would silently
    # reintroduce that bug.

    # NOTE: confirm this axis mapping is intentional -- cr/dr/up don't
    # obviously map to x/y/z without knowing your frame convention.
    components = {'x': v_cr, 'y': v_dr, 'z': v_up}

    accel_out = {}
    for axis, v in components.items():
        r_series, r_info = build_r_series_from_pad(v_pre, v[:apogee])
        print(f'axis {axis}: detected true motion onset in pad data at index '
              f'{r_info["onset_idx"]} -- check this against a plot before trusting it')

        q_boost, q_coast, q_diag = calibrate_q(
            t[:apogee], v[:apogee], r_series, burnout_idx,
            q_boost_options, q_coast_options,
        )
        if q_diag['boost_at_edge'] or q_diag['coast_at_edge']:
            print(f'axis {axis}: q calibration hit the edge of its options -- widen the range')

        _, accel = kalman_smooth(
            t[:apogee], v[:apogee], r_v=r_series,
            q_boost=q_boost, q_coast=q_coast, burnout_idx=burnout_idx,
        )
        accel_out[axis] = pad_to(accel, n_total)

    accelx, accely, accelz = accel_out['x'], accel_out['y'], accel_out['z']
    total = magnitude(accelx, accely, accelz)
    return accelx, accely, accelz, total

# ...AAAAND WE ARE BACK! 

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
# import no less than 20...
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



### Geometry Functions 

radius = 3

G_FT = 32.174   # ft/s^2, and the lbm-ft/(lbf-s^2) unit conversion
IN_PER_FT = 12

x = sp.symbols('x')

# defining the radius of various components so that they can later be caled from the dictionary, same number of arguments
def nosetip(r, length):
    return r/length * x

def von_karman(r, length):
    theta = sp.acos(1 - 2*x/length)
    return r / sp.sqrt(sp.pi) * sp.sqrt(theta - sp.sin(2*theta)/2)

def tube(r, length):
    return sp.Float(r)

def boat_tail(r, length):
    return r - 0.24/length * x


RAD_GEOMETRY = {
    "nosetip": nosetip,
    "nose_cone": von_karman,
    "tail": boat_tail}

def rad_func(component):
    '''numeric r(xi) for a component, clipped to stay real and non-negative'''
    expr = RAD_GEOMETRY.get(component.name, tube)(component.radius_, component.length)
    f = sp.lambdify(x, expr, 'numpy')
    def r_at(xi):
        xi = np.clip(np.asarray(xi, float), 0, component.length)
        if expr.is_number:
            return np.broadcast_to(float(expr), np.shape(xi)).astype(float)
        with np.errstate(invalid='ignore'):
            return np.nan_to_num(np.maximum(np.asarray(f(xi), float), 0))
    return r_at

# defining how to calculated the center of mass of a component, will be called through a later for loop
def local_cg(component, radius: float = radius) -> float:
    '''finding the center, information initialized in the dataclass, assuming equal mass distribution,
     definition ∫ x dm /  ∫ dm'''
    # defining the radius, using the dictionary and dataclass information
    radius_eq = rad_func(component)
    lo, hi = 0, component.length
    if component.hollow: # NEED TO FIX, IMPUT THICKNESS
        # hollow definition: thin shell, mass per unit length goes as r(x)
        numerator = lambda xi: xi * float(radius_eq(xi))
        denominator = lambda xi: float(radius_eq(xi))
    else:
        numerator = lambda xi: xi * float(radius_eq(xi))**2
        denominator = lambda xi: float(radius_eq(xi))**2
    # integral(num)/integral(den)
    num, _ = sci_integrate.quad(numerator, lo, hi)
    den, _ = sci_integrate.quad(denominator, lo, hi)
    if den == 0:
        return component.length / 2
    return float(num / den)

@dataclass
class Component: # used for all parts of the rocket that isn't Engine or fins
    '''Component(name, length, mass, offset, hollow, *center of mass), with local_cg being called'''
    name: str
    length: float   # inches
    mass: float     # lbm 
    offset: float   # inches, station of the forward face, x=0 at the nose tip
    local_cg: float = None # inches aft of the forward face, None uses the centroid
    radius_: float = radius # inches
    hollow: bool = True

    def __post_init__(self):
        if self.local_cg is None:
            self.local_cg = local_cg(self)
        elif not 0 <= self.local_cg <= self.length:
            raise ValueError(f'{self.name}: local_cg outside [0, {self.length}]')

    @property # going to print cg according to the rest of the rocket 
    def global_cg(self) -> float:
        return self.local_cg + self.offset

@dataclass
class EngineComponent:
    name: str
    dry_mass: float # lbs
    offset: float  # lbs
    length: float #inches
    prop_mass: float = 0.0   # to hold depletion (plumbing has none)
    radius: float = None

    def cg_offset(self) -> float:
        # assuming uniform density
        return self.offset + self.length / 2

@dataclass
class Engine:
    tank:      EngineComponent   # oxidizer
    plumbing:  EngineComponent   # injector, valves, lines
    grain:     EngineComponent   # fuel grain + casing
    length: float #inches 
    offset: float #inches
    thrusts:   np.ndarray = None
    times:     np.ndarray = None

    def __post_init__(self):
        self._curve_ready = False
        if self.thrusts is not None and self.times is not None:
            self._process_curve()

    def set_curve(self, thrusts, times):
        self.thrusts = thrusts
        self.times = times
        self._process_curve()

    def _process_curve(self):
        assert len(self.thrusts) == len(self.times)
        cumulative = np.zeros(len(self.times))
        cumulative[1:] = np.cumsum(0.5 * (self.thrusts[:-1] + self.thrusts[1:]) * np.diff(self.times))
        self.total_impulse = cumulative[-1]
        self._frac_expended = cumulative / self.total_impulse
        self.burn_time = self.times[-1]
        self._curve_ready = True

    def _check_ready(self):
        if not self._curve_ready:
            raise RuntimeError("Thrust curve not set — call set_curve(thrusts, times) first")

    def _frac_at(self, t):
        t = np.asarray(t, dtype=float)
        frac = np.interp(t, self.times, self._frac_expended, left=0.0, right=1.0)
        return np.where(t >= self.burn_time, 1.0, frac)

    def thrust_at(self, t):
        self._check_ready()
        t = np.asarray(t, dtype=float)
        return np.interp(t, self.times, self.thrusts, left=0.0, right=0.0)

    def mass_at(self, t):
        self._check_ready()
        frac = self._frac_at(t)
        # oxidizer depletes with the thrust curve; grain regression can use
        # the same frac, or a separate curve if you're tracking O/F ratio
        tank_mass = self.tank.dry_mass + self.tank.prop_mass * (1 - frac)
        grain_mass = self.grain.dry_mass + self.grain.prop_mass * (1 - frac)
        plumbing_mass = self.plumbing.dry_mass
        return tank_mass + grain_mass + plumbing_mass

    def cg_at(self, t):
        self._check_ready()
        frac = self._frac_at(t)

        tank_mass = self.tank.dry_mass + self.tank.prop_mass * (1 - frac)
        grain_mass = self.grain.dry_mass + self.grain.prop_mass * (1 - frac)
        plumbing_mass = self.plumbing.dry_mass

        total = tank_mass + grain_mass + plumbing_mass
        moment = (tank_mass * self.tank.cg_offset()
                  + grain_mass * self.grain.cg_offset()
                  + plumbing_mass * self.plumbing.cg_offset())
        # cg_offset() is measured aft of the engine's own forward face
        return self.offset + moment / total

@dataclass
class Fins:
    mass: float   # lbm
    root_chord: float   # inches, fin from body
    tip_chord: float   # inches, outer edge
    span: float   # inches, root to tip
    offset: float   # distance from nose to fin root leading edge
    roll: float = 0.0   # degrees about the roll axis, 0 lies in the pitch plane

    def __post_init__(self): # creating the center of mass from the shape instead of radius equation
        # centroid of trapezoid in axial direction
        # chordwise centroid of a trapezoid with an unswept leading edge,
        # int(c^2/2 ds) / int(c ds) = (r^2 + r t + t^2) / (3 (r + t))
        r, t = self.root_chord, self.tip_chord
        self._local_cg_axial = self.offset + (r**2 + r*t + t**2) / (3*(r + t))
        # centroid 
        self._local_cg_radial = radius + self.span * ((2*self.tip_chord + self.root_chord) / (3*(self.root_chord + self.tip_chord)))

    @property
    def area(self):
        return 0.5 * (self.root_chord + self.tip_chord) * self.span

    def iyy_cm(self) -> float:
        """iyy of shape, chordwise spread plus the pitch-plane share of the span"""
        chord_avg = (self.root_chord + self.tip_chord) / 2
        axial = (1/12) * self.mass * chord_avg**2
        spanwise = (1/12) * self.mass * self.span**2
        return axial + spanwise * np.cos(np.radians(self.roll))**2

    def iyy_contribution(self, rocket_cg: float) -> float:
        """total iyy of fin, including parallel axis portion"""
        d_axial = self._local_cg_axial - rocket_cg
        # only the pitch-plane share of the radial offset feeds iyy
        d_radial = self._local_cg_radial * np.cos(np.radians(self.roll))
        d_squared = d_axial**2 + d_radial**2
        return self.iyy_cm() + self.mass * d_squared    

def total_cg(rocket, t, radius=radius): # calling the cg of each component function 
    components = [c for c in rocket if isinstance(c, Component)] # fins have their own internal function, rather than definig outside
    engine = [p for p in rocket if isinstance(p, Engine)][0]

    fins = [f for f in rocket if isinstance(f, Fins)]
    masses = np.array([c.mass for c in components] + [f.mass for f in fins])
    global_cgs = np.array([c.global_cg for c in components]
                          + [f._local_cg_axial for f in fins])

    # engine portion 
    m_engine = engine.mass_at(t) # numpy array, of the mass and cg as the engine empties 
    x_engine = engine.cg_at(t) # how the center of mass shift
    
    #center of mass of total rocket
    total_mass = masses.sum() + m_engine                                  
    cg_total = (masses @ global_cgs + m_engine * x_engine) / total_mass
    return global_cgs, cg_total, total_mass

def section_moments(component):
    '''mass weighted moments of a section about the nose datum, where mass per
    unit length goes as r for a thin shell and r^2 for a solid section:
    m0 = int w dxi, m1 = int x w dxi, m2 = int x^2 w dxi, mr = int r^2 w dxi'''
    r_at = rad_func(component)
    power = 1 if component.hollow else 2
    w = lambda xi: float(r_at(xi))**power
    station = lambda xi: component.offset + xi
    hi = component.length
    m0, _ = sci_integrate.quad(w, 0, hi)
    m1, _ = sci_integrate.quad(lambda xi: station(xi) * w(xi), 0, hi)
    m2, _ = sci_integrate.quad(lambda xi: station(xi)**2 * w(xi), 0, hi)
    mr, _ = sci_integrate.quad(lambda xi: float(r_at(xi))**2 * w(xi), 0, hi)
    return m0, m1, m2, mr

def iyy_body(component, rocket_cg):
    '''iyy about a pitch axis through rocket_cg, vectorized over rocket_cg.
    int (x - cg)^2 w dxi expands to m2 - 2 cg m1 + cg^2 m0, so the section is
    integrated once and every cg after that is pure arithmetic'''
    m0, m1, m2, mr = section_moments(component)
    # a section's own transverse term: r^2/2 for a thin shell, r^2/4 for solid
    k = 0.5 if component.hollow else 0.25
    cg = np.asarray(rocket_cg, float)
    return component.mass * ((m2 - 2*cg*m1 + cg**2*m0) + k*mr) / m0

def total_iyy(rocket_parts, t: np.ndarray, rocket_cg: np.ndarray) -> np.ndarray:
    components = [p for p in rocket_parts if isinstance(p, Component)]
    fins = [p for p in rocket_parts if isinstance(p, Fins)]
    engine = next(p for p in rocket_parts if isinstance(p, Engine))
    iyy = sum(iyy_body(c, rocket_cg) for c in components)
    iyy += sum(fin.iyy_contribution(rocket_cg) for fin in fins)
    iyy += iyy_engine(engine, t, rocket_cg)   # pass t separately

    return iyy

def iyy_engine(engine, t: np.ndarray, rocket_cg: np.ndarray, radius=radius) -> np.ndarray:
    '''constant radius section, so the moments are analytic'''
    lo, hi = engine.offset, engine.offset + engine.length
    m0 = hi - lo
    m1 = (hi**2 - lo**2) / 2
    m2 = (hi**3 - lo**3) / 3
    cg = np.asarray(rocket_cg, float)
    return engine.mass_at(t) * ((m2 - 2*cg*m1 + cg**2*m0) / m0 + 0.25*radius**2)

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

def acceleration(v_up, v_dr, v_cr, apogee, t, window_length=71):
    '''accelerations are computed to apogee, samples past it are zero padding'''
    smooth = savgol_filter(np.stack([v_cr, v_dr, v_up])[:, :apogee],
                           window_length=window_length, polyorder=3, axis=-1)
    accelx, accely, accelz = (pad_to(np.gradient(v, t[:apogee]), t.shape[0])
                              for v in smooth)

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

def cd(fd, density, velocity, diameter=2*radius/IN_PER_FT):
    '''diameter in feet'''
    denom = dynamic_pressure(density, velocity) * ref_area(diameter)
    return np.divide(fd, denom, out=np.full_like(np.asarray(fd, float), np.nan),
                     where=np.abs(denom) > 1e-9)

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
# import no less than 20...
import sympy as sp
import math
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import savgol_filter, stft
from dataclasses import dataclass, field
from typing import List, Tuple
from scipy import integrate as sci_integrate
from scipy.integrate import quad_vec
import pandas as pd



### Geometry Functions 

radius = 3

x = sp.symbols('x')

# defining the radius of various components so that they can later be caled from the dictionary, same number of arguments
def nosetip(r, length):
    return r - r/length * x

def von_karman(r, length):
    theta = sp.acos(1 - 2*x/length)
    return r / sp.sqrt(sp.pi) * sp.sqrt(theta - sp.sin(2*theta)/2)

def tube(r, length):
    return sp.Integer(r)

def boat_tail(r, length):
    return r - 0.24/length * x


RAD_GEOMETRY = {
    "nosecone_vonkarman": tube} 

# defining how to calculated the center of mass of a component, will be called through a later for loop
def local_cg(component, radius: float = radius) -> float:
    '''finding the center, information initialized in the dataclass, assuming equal mass distribution,
     definition ∫ x dm /  ∫ dm'''
    # defining the radius, using the dictionary and dataclass information
    radius_eq = sp.lambdify(x, RAD_GEOMETRY[component.name](radius, component.length), 'numpy')  
    lo, hi = 0, component.length
    if component.hollow: # NEED TO FIX, IMPUT THICKNESS
        r_dot_eq = sp.diff(RAD_GEOMETRY[component.name](radius, component.length), x) 
        r_dot_func = sp.lambdify(x, r_dot_eq, 'numpy') # lambidfy used here because it took too long otherwise idk it was a fix
        # hollow definition: thin shell, r(x) sqrt(1 + r'**2) dx
        numerator = lambda xi: xi * radius_eq(xi) * np.sqrt(1 + r_dot_func(xi)**2)
        denominator = lambda xi: radius_eq(xi) * np.sqrt(1 + r_dot_func(xi)**2)
    else:
        numerator = lambda xi: xi * radius_eq(xi)**2
        denominator = lambda xi: radius_eq(xi)**2
    # integral(num)/integral(den)
    num, _ = sci_integrate.quad(numerator, lo, hi)
    den, _ = sci_integrate.quad(denominator, lo, hi)
    return float(num / den)

@dataclass
class Component: # used for all parts of the rocket that isn't Engine or fins
    '''Component(name, length, mass, offset, hollow, *center of mass), with local_cg being called'''
    name: str
    length: float   # inches
    mass: float     # lbm 
    offset: float   # inches, from end of boat tail  
    local_cg: float # inches 
    radius_: float = radius # inches
    hollow: bool = True

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
        return moment / total

@dataclass
class Fins:
    mass: float   # lbm
    root_chord: float   # inches, fin from body
    tip_chord: float   # inches, outer edge
    span: float   # inches, root to tip
    offset: float   # distance from nose to fin root leading edge

    def __post_init__(self): # creating the center of mass from the shape instead of radius equation
        # centroid of trapezoid in axial direction
        self._local_cg_axial = (
            self.offset + 
            (self.root_chord/2 + 
             (self.tip_chord - self.root_chord)/3 *  # trapezoidal centroid
             (self.root_chord + 2*self.tip_chord) / 
             (self.root_chord + self.tip_chord))
        )
        # centroid 
        self._local_cg_radial = radius + self.span * ((2*self.tip_chord + self.root_chord) / (3*(self.root_chord + self.tip_chord)))

    @property
    def area(self):
        return 0.5 * (self.root_chord + self.tip_chord) * self.span

    def iyy_cm(self) -> float:
        """iyy of shape (approximate because no radius equation was written.... shh)"""
        chord_avg = (self.root_chord + self.tip_chord) / 2
        return (1/12) * self.mass * chord_avg**2

    def iyy_contribution(self, rocket_cg: float) -> float:
        """total iyy of fin, including parallel axis portion"""
        d_axial = self._local_cg_axial - rocket_cg  
        d_radial = self._local_cg_radial 
        d_squared = d_axial**2 + d_radial**2
        return self.iyy_cm() + self.mass * d_squared    

def total_cg(rocket, t, radius=radius): # calling the cg of each component function 
    components = [c for c in rocket if isinstance(c, Component)] # fins have their own internal function, rather than definig outside
    engine = [p for p in rocket if isinstance(p, Engine)][0]

    masses = np.array([c.mass for c in components])       
    global_cgs = np.array([c.global_cg for c in components])

    # engine portion 
    m_engine = engine.mass_at(t) # numpy array, of the mass and cg as the engine empties 
    x_engine = engine.cg_at(t) # how the center of mass shift
    
    #center of mass of total rocket
    total_mass = masses.sum() + m_engine                                  
    cg_total = (masses @ global_cgs + m_engine * x_engine) / total_mass
    return global_cgs, cg_total, total_mass

def iyy_body(component, rocket_cg, radius=radius):
    if component.name in RAD_GEOMETRY:
        r_func = sp.lambdify(x, RAD_GEOMETRY[component.name](radius, component.length), 'numpy')
    else:
        r_func = sp.lambdify(x, sp.Integer(5), 'numpy')
    lo, hi = component.offset, component.offset + component.length

    if component.hollow:
        def num_integrand(xi, cg):
            return (xi - cg)**2 * r_func(xi - component.offset)
        def den_integrand(xi, cg):
            return r_func(xi - component.offset)
    else:
        def num_integrand(xi, cg):
            return (xi - cg)**2 * r_func(xi - component.offset)**2
        def den_integrand(xi, cg):
            return r_func(xi - component.offset)**2

    # quad_vec integrates over xi for each value of cg simultaneously
    num, _ = quad_vec(lambda xi: num_integrand(xi, rocket_cg), lo, hi)  # (100000,)
    den, _ = quad_vec(lambda xi: den_integrand(xi, rocket_cg), lo, hi)  # (100000,)

    return component.mass * num / den 

def total_iyy(rocket_parts, t: np.ndarray, rocket_cg: np.ndarray) -> np.ndarray:
    components = [p for p in rocket_parts if isinstance(p, Component)]
    fins = [p for p in rocket_parts if isinstance(p, Fins)]
    engine = next(p for p in rocket_parts if isinstance(p, Engine))
    iyy = sum(iyy_body(c, rocket_cg) for c in components)
    iyy += sum(fin.iyy_contribution(rocket_cg) for fin in fins)
    iyy += iyy_engine(engine, t, rocket_cg)   # pass t separately

    return iyy

def iyy_engine(engine, t: np.ndarray, rocket_cg: np.ndarray, radius=radius) -> np.ndarray:
    lo, hi = engine.offset, engine.offset + engine.length

    num, _ = quad_vec(lambda xi: (xi - rocket_cg)**2 * radius**2, lo, hi)
    den, _ = quad_vec(lambda xi: radius**2, lo, hi)

    return engine.mass_at(t) * num / den  

# math Functions 

def angle(v_up, v_dr, v_cr, tilt):
    '''flight angle and angle of attack'''
    ang = np.degrees(np.arccos(v_up / np.sqrt(v_up**2 + v_dr**2 + v_cr**2)))
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
    return np.insert(magnitude(vx,vy,vz), -1, 0)

def acceleration(v_up, v_dr, v_cr, apogee, t):
    v_up_smooth = savgol_filter(v_up, window_length=71, polyorder=3)
    v_dr_smooth = savgol_filter(v_dr, window_length=71, polyorder=3)
    v_cr_smooth = savgol_filter(v_cr, window_length=71, polyorder=3)

    accelz = np.resize(np.gradient(v_up_smooth[:apogee], t[:apogee]), t.shape)
    accely = np.resize(np.gradient(v_dr_smooth[:apogee], t[:apogee]), t.shape)
    accelx = np.resize(np.gradient(v_cr_smooth[:apogee], t[:apogee]), t.shape)

    total = np.resize(magnitude(accelx, accely, accelz), t.shape)
    
    return accelx, accely, accelz, total

def ndcheck_no_gyro(in_a, in_dr, in_cr, t, mass, thrust, apogee, gravity=9.81, eps=1e-8):

    # single derivative for velocity (less noisy than double-diff)
    
    vdr = np.gradient(in_dr[:apogee], t[:apogee])
    vcr = np.gradient(in_cr[:apogee], t[:apogee])
    va  = np.gradient(in_a[:apogee], t[:apogee])
    v_vec = np.stack([vdr, vcr, va], axis=-1)          # (N,3)
    v_mag = np.linalg.norm(v_vec, axis=-1, keepdims=True)
    x_hat = v_vec / np.maximum(v_mag, eps)              # avoid div by zero at apex/launch
    print(len(vdr), len(t))
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
    fd     = faxial - thrust[:apogee]

    return fn, fd

def ndcheck_with_aoa(in_a, in_dr, in_cr, t, mass, thrust, aoa, gravity=9.81, eps=1e-8):
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
    fd     = faxial - thrust

    return fn, fd

def theta(v_dr,v_cr):
    '''finding the angle between the rockets direction and horizon'''
    with np.errstate(divide='ignore', invalid='ignore'):
        result = np.degrees(np.arctan(v_dr/v_cr))
        result[~np.isfinite(result)] = 0  
    return result

def thrust(weight, theta, accel_x, accel_y, accel_z, Fdx, Fdy, Fdz):
    '''force delivered by thrust acting upon the rocket, with drag force being compared from ras'''
    ftx = weight/32.2 * accel_x + Fdx 
    fty = weight/32.2 * accel_y + Fdy 
    ftz = weight/32.2 * (accel_z + 32.2) + Fdz 
    thrust = magnitude(ftx, fty, ftz)
    return thrust 

def fd(thrust, tilt, theta, weight, accelx, accely, accelz):
    '''finding the drag force acting upon the rocket, based on in flight data with thrust being predicted'''

    # finding force provided by thrust in respective directions, subtracting total force in that direction 
    fdx = thrust * np.sin(np.radians(tilt)) * np.cos(np.radians(theta)) - weight/32.2 * accelx
    fdy = thrust * np.sin(np.radians(tilt)) * np.sin(np.radians(theta)) - weight/32.2 * accely
    fdz = thrust * np.cos(np.radians(tilt)) - weight/32.2 * (accelz + 32.174) # adding gravity back in
    
    fd = magnitude(fdx, fdy, fdz)
    
    return fdx, fdy, fdz, fd

def fn1(tilt, theta, weight, ax, ay, az):
    '''finding the normal force acting on the rocket, based on accelerometer data'''
    # removing acceleration due to gravity, subtracting by including the angle 
    az = az + 32.174 
    # finding axial unit vector, concerning how force is being distributed 
    axialx = np.sin(np.radians(tilt)) * np.cos(np.radians(theta))
    axialy = np.sin(np.radians(tilt)) * np.sin(np.radians(theta))
    axialz = np.cos(np.radians(tilt))
    # dot product of acceleration for axial acceleration
    a_axial = ax * axialx + ay * axialy + az * axialz
    # subtract component, multuply by mass 
    Fnx = (ax - a_axial * axialx) * weight/32.174
    Fny = (ay - a_axial * axialy) * weight/32.174
    Fnz = (az - a_axial * axialz) * weight/32.174
    Fn = magnitude(Fnx, Fny, Fnz)
    return Fnx, Fny, Fnz, Fn

def fn2(weight, g_accelx, g_accelz):
    '''finding the normal force acting on the rocket, assuming that the gyroscope is the acceleration'''
    return weight/32.2 * magnitude(g_accelx, g_accelz)

def density(pressure, temperature):
    '''density using ideal gas law, constants used reflect atm and F'''
    return (pressure * 2116.22)/(53.35*((temperature + 459.67)))

def cd(fd, density, velocity, diameter=radius/12):
    return (fd)/(density*velocity**2*((diameter/2)**2 * np.pi)*0.5)

def cna(fn, density, velocity, aoa, diameter=6):
    '''normal force coefficent, derivative of the coefficent with respect to angle of attack'''
    cn = (fn)/(density*velocity**2*(((diameter/2)**2 * np.pi)*0.5))
    # derivative of normal coefficent 
    cna = np.gradient(cn, np.radians(aoa))
    return cn, cna

def frequency(aoa, sample_rate, target_time):
    '''natrual frequency, from short fourier transform functions to isolate the atrual frequency of aoa oscillations'''
    f, t, Zxx = stft(aoa, fs=sample_rate, window='hann', nperseg=256) # finding frequency, time bucket, and array values 
    t_index = np.argmin(np.abs(t[:, np.newaxis] - target_time), axis=0) # index, finding what each time value is closest to for bin sorting
    power = np.abs(Zxx[:, t_index]) # powers at that value
    frequency_n = f[np.argmax(power, axis=0)] # dominant frequency
    return frequency_n

def stability1(frequency_n, inertia_yy, velocity, density, aoa, fn, diameter=radius):
    '''finding stability values at point in time'''
    m_corrective = frequency_n ** 2 * inertia_yy / (diameter * (diameter/2)**2 * np.pi * density)
    cn_a = cna(fn, density, velocity, aoa)[1]
    sm = m_corrective / cn_a
    return sm 

def stability(time, inertia_yy, gyro_y, fn, diameter=radius):
    q_accel = np.gradient(gyro_y, time)
    sm = inertia_yy * q_accel / (fn * diameter * 381.6)
    return sm

### math functions ^
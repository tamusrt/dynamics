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
    "nosecone_vonkarman": von_karman,
    "nosetip": nosetip,
    "forward": tube,
    "aft": tube,
    "coupler": tube,
    "payload": tube, 
    "bay": tube, 
    "ring": tube,
    "payload": tube,
    "boat_tail": boat_tail}

# defining how to calculated the center of mass of a component, will be called through a later for loop
def local_cg(component, radius: float = radius) -> float:
    '''finding the center of a Component, based on the information initialized in the dataclass, assuming equal mass distribution,
     ∫ x dm /  ∫ dm'''
    # defining the radius and (internal) refrence points, using the dictionary and dataclass information
    radius_eq = sp.lambdify(x, RAD_GEOMETRY[component.name](radius, component.length), 'numpy')  
    lo, hi = 0, component.length
    if component.hollow:
        r_dot_eq = sp.diff(RAD_GEOMETRY[component.name](radius, component.length), x) 
        r_dot_func = sp.lambdify(x, r_dot_eq, 'numpy') # lambidfy used here because it took too long otherwise
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
class Component: # Used for all parts of the rocket that isn't Engine or fins
    '''Component(name, length, mass, offset, hollow, *center of mass), with local_cg being called'''
    name: str
    length: float   # inches
    mass: float     # lbm 
    offset: float   # inches, from end of boat tail  
    local_cg: float # inches 
    radius_: float = radius # inches
    hollow: bool = True
    #_local_cg: float = field(init=False, repr=False)

    #def __post_init__(self): # finding local cg 
    #    self._local_cg = local_cg(self)  

    @property # going to print cg according to the rest of the rocket 
    def global_cg(self) -> float:
        return self.local_cg + self.offset

@dataclass
class Engine:
    '''Engine(total_mass, end_mass, offset, length, flow_rate), lowkey this is assuming constant thrust which is not great'''
    total_mass: float    # lbm
    end_mass: float      # lbm 
    offset: float        # inches, from end of boat tail 
    length: float        # inches
    flow_rate: float     # lbm/s

    def __post_init__(self): # finding total burn time of the engine
        self.burn_time = (self.total_mass - self.end_mass) / self.flow_rate

    def mass_at(self, t: np.ndarray) -> np.ndarray:
        return np.where(t >= self.burn_time, self.end_mass, self.total_mass - self.flow_rate * t)

    def cg_at(self, t: np.ndarray) -> np.ndarray:
        return np.full_like(t, self.offset + self.length / 2)

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
    r_func = sp.lambdify(x, RAD_GEOMETRY[component.name](radius, component.length), 'numpy')

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
    iyy  = sum(iyy_body(c, rocket_cg) for c in components)
    iyy += sum(fin.iyy_contribution(rocket_cg) for fin in fins)
    iyy += iyy_engine(engine, t, rocket_cg)   # pass t separately

    return iyy

def iyy_engine(engine, t: np.ndarray, rocket_cg: np.ndarray, radius=radius) -> np.ndarray:
    lo, hi = engine.offset, engine.offset + engine.length

    num, _ = quad_vec(lambda xi: (xi - rocket_cg)**2 * radius**2, lo, hi)
    den, _ = quad_vec(lambda xi: radius**2, lo, hi)

    return engine.mass_at(t) * num / den  

# Math Functions 

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
    v_up_smooth = savgol_filter(v_up, window_length=51, polyorder=3)
    v_dr_smooth = savgol_filter(v_dr, window_length=51, polyorder=3)
    v_cr_smooth = savgol_filter(v_cr, window_length=51, polyorder=3)

    accelz = np.resize(np.gradient(v_up_smooth[:apogee], t[:apogee]), t.shape)
    accely = np.resize(np.gradient(v_dr_smooth[:apogee], t[:apogee]), t.shape)
    accelx = np.resize(np.gradient(v_cr_smooth[:apogee], t[:apogee]), t.shape)

    total = np.resize(magnitude(accelx, accely, accelz), t.shape)
    
    return accelx, accely, accelz, total

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
    
    # clean transitions
    thrust_clean = np.where(thrust < 10, 0, thrust)
    burn_start = np.where(np.diff(thrust_clean) > 50)[0]
    burn_end   = np.where(np.diff(thrust_clean) < -50)[0]

    mask = np.ones(len(fd), dtype=bool)
    for idx in np.concatenate([burn_start, burn_end]):
        mask[max(0, idx-5) : idx+5] = False

    fd  = pd.Series(np.where(mask, fd,  np.nan)).interpolate(limit_direction='both').values
    fdx = pd.Series(np.where(mask, fdx, np.nan)).interpolate(limit_direction='both').values
    fdy = pd.Series(np.where(mask, fdy, np.nan)).interpolate(limit_direction='both').values
    fdz = pd.Series(np.where(mask, fdz, np.nan)).interpolate(limit_direction='both').values
    return fdx, fdy, fdz, fd

def fn1(tilt, theta, weight, accelx, accely, accelz):
    '''finding the normal force acting on the rocket, based on accelerometer data'''
    # removing acceleration due to gravity, subtracting by including the angle 
    ax = accelx - 32.174 * np.sin(np.radians(tilt)) * np.cos(np.radians(theta))
    ay = accely - 32.174 * np.sin(np.radians(tilt)) * np.sin(np.radians(theta))
    az = accelz + 32.174 * np.cos(np.radians(tilt))
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

def cd(fd,density,velocity,diameter=radius/12):
    '''drag coefficent'''
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
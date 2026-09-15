
from dataclasses import dataclass
import numpy as np

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
    pressures: np.ndarray = None
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
    
    @staticmethod
    def _rod_iyy(m: float, L: float) -> float:
        return (1.0 / 12.0) * m * L ** 2 if L > 0 else 0.0

    def iyy_at(self, t, rocket_cg: float) -> float:
        """Engine's contribution to pitch Iyy about rocket_cg, at time t."""
        self._check_ready()
        frac = self._frac_at(t)
        tank_mass = self.tank.dry_mass + self.tank.prop_mass * (1 - frac)
        grain_mass = self.grain.dry_mass + self.grain.prop_mass * (1 - frac)
        plumbing_mass = self.plumbing.dry_mass

        total = 0.0
        for comp, m in ((self.tank, tank_mass),
                         (self.grain, grain_mass),
                         (self.plumbing, plumbing_mass)):
            cg_local = self.offset + comp.cg_offset()
            d = cg_local - rocket_cg
            total += self._rod_iyy(m, comp.length) + m * d ** 2
        return total
    
sol_ignis = Engine(EngineComponent(name="ox_tank", dry_mass=0.35, prop_mass=1.2, offset=0.0, length=0.45),
    EngineComponent(name="plumbing", dry_mass=0.15, offset=0.45, length=0.08),
    EngineComponent(name="fuel_grain", dry_mass=0.4, prop_mass=0.1, offset=0.53, length=0.30), length=50, offset=130)

thrusts_array = np.array([1000, 1500, 2000])  # example thrust values
times_array = np.array([0, 1, 2])  # example time values

sol_ignis.set_curve(thrusts_array, times_array) # call function .set_curve so it'll run the above functions
print(sol_ignis.cg_at(times_array)) 
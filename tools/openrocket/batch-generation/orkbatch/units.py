"""Unit handling.

OpenRocket stores lengths in meters, masses in kg and angles in degrees.
Users type values in a default unit (``--unit`` / ``--mass-unit``) or attach
an explicit unit to any number, e.g. ``254mm``, ``10in``, ``0.5lb``, ``0.1rad``.
"""

import math
import re

LENGTH_UNITS = {"in": 0.0254, "mm": 0.001, "cm": 0.01, "m": 1.0, "ft": 0.3048}
MASS_UNITS = {"kg": 1.0, "g": 0.001, "lb": 0.45359237, "oz": 0.028349523125}
ANGLE_UNITS = {"deg": 1.0, "rad": 180.0 / math.pi}  # factor to degrees (OpenRocket's storage unit)
UNIT_TABLES = {"length": LENGTH_UNITS, "mass": MASS_UNITS, "angle": ANGLE_UNITS}

NUMERIC_KINDS = ("length", "mass", "angle", "int", "number", "factor")

_QTY_RE = re.compile(r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-z]*)\s*$")


class Units:
    def __init__(self, length="in", mass="kg"):
        if length not in LENGTH_UNITS:
            raise ValueError(f"Unknown length unit '{length}' (use one of {', '.join(LENGTH_UNITS)})")
        if mass not in MASS_UNITS:
            raise ValueError(f"Unknown mass unit '{mass}' (use one of {', '.join(MASS_UNITS)})")
        self.length = length
        self.mass = mass

    def label(self, kind):
        """Display unit for a parameter kind ('' if unitless)."""
        return {"length": self.length, "mass": self.mass, "angle": "deg"}.get(kind, "")

    def to_si(self, kind, value, unit=None):
        table = UNIT_TABLES.get(kind)
        if table is None:
            if unit:
                raise ValueError(f"'{value}{unit}': this parameter is unitless, drop the '{unit}'")
            return value
        unit = unit or self.label(kind)
        if unit not in table:
            raise ValueError(f"Unit '{unit}' isn't valid for a {kind} (use one of {', '.join(table)})")
        return value * table[unit]

    def from_si(self, kind, si_value):
        table = UNIT_TABLES.get(kind)
        if table is None or si_value is None or isinstance(si_value, str):
            return si_value
        return si_value / table[self.label(kind)]


def split_quantity(token):
    """'10.5in' -> (10.5, 'in'); '3' -> (3.0, ''). Raises ValueError if not numeric."""
    m = _QTY_RE.match(str(token))
    if not m:
        raise ValueError(f"'{token}' is not a number")
    return float(m.group(1)), m.group(2)


def is_number(token):
    try:
        split_quantity(token)
        return True
    except ValueError:
        return False


def fmt_value(v):
    """Compact display: 2 not 2.0, 4.5 stays 4.5, floats to 6 significant digits."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, str):
        return v
    if isinstance(v, int):
        return str(v)
    v = round(float(v), 9)
    if v == 0:
        return "0"
    if v.is_integer() and abs(v) < 1e15:
        return str(int(v))
    return f"{v:.6g}"

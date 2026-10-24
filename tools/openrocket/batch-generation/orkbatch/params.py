"""Component parameters: what they are, how to read them, how to write them.

A parameter is normally just a child tag of a component element, e.g. the
<rootchord> of a <trapezoidfinset> or the <length> of a <nosecone>. Its
"kind" (length / angle / mass / int / choice / ...) decides how user values
are unit-converted. A few *virtual* parameters are computed from real tags:

    diameter, forediameter, aftdiameter, outerdiameter, innerdiameter
        2x the matching *radius tag (only when no real tag of that name exists,
        e.g. a parachute's <diameter> is a real tag and is used as-is)
    sweepangle
        fin leading-edge sweep angle; written as sweeplength = height*tan(angle)
    tag@attr
        an XML attribute, e.g. material@density or axialoffset@method
"""

import math

FIN_TAGS = ("trapezoidfinset", "ellipticalfinset", "freeformfinset", "tubefinset")
BODY_TAGS = ("nosecone", "bodytube", "transition")

LENGTH_TAGS = {
    "length", "thickness", "radius", "foreradius", "aftradius", "outerradius", "innerradius",
    "rootchord", "tipchord", "height", "sweeplength", "filletradius",
    "tabheight", "tablength", "tabposition",
    "axialoffset", "position", "radiusoffset", "radialposition",
    "packedlength", "packedradius", "instanceseparation",
    "foreshoulderradius", "foreshoulderlength", "foreshoulderthickness",
    "aftshoulderradius", "aftshoulderlength", "aftshoulderthickness",
    "linelength", "overhang", "overridecg", "striplength", "stripwidth",
    "outerdiameter", "innerdiameter", "baseheight", "flangeheight", "screwheight",
}
DIAMETER_REAL_TAGS = {"diameter"}  # real tags on parachutes / motors; still lengths
ANGLE_TAGS = {"cant", "angleoffset", "rotation", "radialdirection", "sweepangle"}
MASS_TAGS = {"mass", "overridemass"}
INT_TAGS = {"fincount", "instancecount", "linecount"}
NUMBER_TAGS = {"shapeparameter", "cd", "overridecd", "clusterscale"}
CHOICES = {
    "crosssection": ("square", "rounded", "airfoil"),
    "shape": ("conical", "ogive", "ellipsoid", "power", "parabolic", "haack"),
    "finish": ("rough", "unfinished", "normal", "smooth", "finishpolished", "polished"),
}
BOOL_TAGS = {"shapeclipped", "isflipped", "foreshouldercapped", "aftshouldercapped",
             "overridesubcomponentsmass"}

# OpenRocket 23+/24 writes some values twice under old and new names. When one
# is written, its twin (if present on the element) is written too.
TWINS = {"axialoffset": "position", "position": "axialoffset",
         "angleoffset": "rotation", "rotation": "angleoffset",
         "fincount": "instancecount"}

# Tags that may legitimately be absent and are created when set.
CREATABLE = {"overridemass", "overridecg", "overridecd"}

# Shorthand names accepted anywhere a parameter name is expected.
ALIASES = {
    "root": "rootchord", "tip": "tipchord", "span": "height", "sweep": "sweeplength",
    "count": "fincount", "n": "fincount", "thick": "thickness", "fillet": "filletradius",
    "pos": "axialoffset", "xsec": "crosssection", "sweepang": "sweepangle",
    "dia": "diameter", "od": "outerdiameter",
}

# Default filename labels (match the old fin GUI so manifests/filenames line up).
SHORT_LABELS = {
    "rootchord": "root", "tipchord": "tip", "height": "height", "sweeplength": "sweep",
    "sweepangle": "sweepang", "thickness": "thick", "fincount": "n", "cant": "cant",
    "filletradius": "fillet", "crosssection": "xsec", "axialoffset": "pos",
    "angleoffset": "rot", "radiusoffset": "radoff", "tabheight": "tabh",
    "tablength": "tabl", "tabposition": "tabpos",
}


class ParamError(Exception):
    pass


def canonical_param(name):
    name = name.strip()
    tag, sep, attr = name.partition("@")
    tag = tag.lower()
    tag = ALIASES.get(tag, tag)
    return f"{tag}@{attr}" if sep else tag


def kind_of(param):
    """Kind of a (canonical) parameter name, independent of any file."""
    if "@" in param:
        return "number" if param.endswith("@density") else "raw"
    if param in LENGTH_TAGS or param in DIAMETER_REAL_TAGS or param.endswith("diameter"):
        return "length"
    if param in ANGLE_TAGS:
        return "angle"
    if param in MASS_TAGS:
        return "mass"
    if param in INT_TAGS:
        return "int"
    if param in NUMBER_TAGS:
        return "number"
    if param in CHOICES:
        return "choice"
    if param in BOOL_TAGS:
        return "bool"
    return "raw"


def parse_numeric_text(text):
    """'0.0762' -> (0.0762, False); 'auto 0.0762' -> (0.0762, True); 'auto' -> (None, True)."""
    if text is None:
        return None, False
    parts = text.strip().split()
    if not parts:
        return None, False
    is_auto = parts[0].lower() == "auto"
    try:
        return float(parts[-1]), is_auto
    except ValueError:
        return None, is_auto


def _fmt_xml_number(v):
    return repr(float(v))


# ------------------------------------------------------------------ accessors

class Accessor:
    """Reads/writes one parameter on one element (values in SI / degrees)."""
    kind = "raw"
    virtual = False

    def read(self):
        raise NotImplementedError

    def write(self, value):
        raise NotImplementedError

    def is_auto(self):
        return False


class TagAccessor(Accessor):
    def __init__(self, el, tag, kind):
        self.el, self.tag, self.kind = el, tag, kind

    def _child(self):
        return self.el.find(self.tag)

    def is_auto(self):
        child = self._child()
        return child is not None and parse_numeric_text(child.text)[1]

    def read(self):
        child = self._child()
        if child is None:
            return None
        text = (child.text or "").strip()
        if self.kind in ("length", "angle", "mass", "number"):
            return parse_numeric_text(text)[0]
        if self.kind == "int":
            v = parse_numeric_text(text)[0]
            return None if v is None else int(round(v))
        if self.kind == "bool":
            return text.lower() == "true"
        if self.kind == "raw":
            v, auto = parse_numeric_text(text)
            return v if (v is not None and not auto and len(text.split()) == 1) else text
        return text

    def write(self, value):
        for tag in (self.tag, TWINS.get(self.tag)):
            if tag is None:
                continue
            child = self.el.find(tag)
            if child is None:
                if tag != self.tag:
                    continue
                child = _create_child(self.el, tag)
            child.text = _to_text(self.kind, value)


class AttrAccessor(Accessor):
    def __init__(self, el, tag, attr, kind):
        self.el, self.tag, self.attr, self.kind = el, tag, attr, kind

    def read(self):
        child = self.el.find(self.tag)
        text = None if child is None else child.get(self.attr)
        if text is None:
            return None
        if self.kind == "number":
            return parse_numeric_text(text)[0]
        v, _ = parse_numeric_text(text)
        return v if v is not None and len(text.split()) == 1 else text

    def write(self, value):
        child = self.el.find(self.tag)
        child.set(self.attr, _to_text(self.kind, value))


class DiameterAccessor(Accessor):
    kind = "length"
    virtual = True

    def __init__(self, el, radius_tag):
        self.radius = TagAccessor(el, radius_tag, "length")

    def is_auto(self):
        return self.radius.is_auto()

    def read(self):
        r = self.radius.read()
        return None if r is None else 2.0 * r

    def write(self, value):
        self.radius.write(value / 2.0)


class SweepAngleAccessor(Accessor):
    """Fin sweep angle; OpenRocket's own definition: sweeplength = height * tan(angle)."""
    kind = "angle"
    virtual = True

    def __init__(self, el):
        self.el = el
        self.sweep = TagAccessor(el, "sweeplength", "length")
        self.height = TagAccessor(el, "height", "length")

    def read(self):
        s, h = self.sweep.read(), self.height.read()
        if s is None or h is None:
            return None
        return math.degrees(math.atan2(s, h)) if h else 0.0

    def write(self, value):
        self.sweep.write(self.height.read() * math.tan(math.radians(value)))


def _create_child(el, tag):
    import xml.etree.ElementTree as ET
    child = ET.Element(tag)
    # keep tags in a sensible spot: right after <id> if possible
    kids = list(el)
    pos = next((i + 1 for i, c in enumerate(kids) if c.tag == "id"), len(kids))
    el.insert(pos, child)
    if tag == "overridemass" and el.find("overridesubcomponentsmass") is None:
        sub = ET.Element("overridesubcomponentsmass")
        sub.text = "false"
        el.insert(pos + 1, sub)
    return child


def _to_text(kind, value):
    if kind == "int":
        return str(int(round(value)))
    if kind == "bool":
        if isinstance(value, str):
            return value.lower()
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _fmt_xml_number(value)
    return str(value)


def resolve_param(el, param):
    """Return an Accessor for `param` (canonical name) on element `el`, or raise ParamError."""
    present = {c.tag for c in el}
    if "@" in param:
        tag, attr = param.split("@", 1)
        child = el.find(tag)
        if child is None or child.get(attr) is None:
            raise ParamError(f"<{el.tag}> has no <{tag} {attr}=...> attribute")
        return AttrAccessor(el, tag, attr, kind_of(param))

    if param == "sweepangle":
        if "sweeplength" in present and "height" in present:
            return SweepAngleAccessor(el)
        raise ParamError(f"sweepangle needs <sweeplength> and <height> (not on <{el.tag}>)")

    if param in present:
        return TagAccessor(el, param, kind_of(param))

    if param.endswith("diameter"):
        stem = param[: -len("diameter")]
        for radius_tag in ([stem + "radius"] if stem else ["radius", "outerradius"]):
            if radius_tag in present:
                return DiameterAccessor(el, radius_tag)

    if param in CREATABLE:
        return TagAccessor(el, param, kind_of(param))

    have = sorted(t for t in present if t not in ("name", "id", "subcomponents", "comment"))
    raise ParamError(f"<{el.tag}> '{(el.findtext('name') or '').strip()}' has no parameter '{param}'. "
                     f"Available: {', '.join(have)}")


def list_params(el):
    """[(param, accessor)] for every editable leaf tag on an element, plus virtual params."""
    out = []
    for child in el:
        if not isinstance(child.tag, str) or len(child) or child.tag in ("name", "id", "comment"):
            continue
        if child.tag in ("color", "linestyle", "appearance", "decal") or child.text is None:
            continue
        out.append((child.tag, TagAccessor(el, child.tag, kind_of(child.tag))))
    present = {c.tag for c in el}
    if "sweeplength" in present and "height" in present:
        out.append(("sweepangle", SweepAngleAccessor(el)))
    for radius_tag in ("radius", "foreradius", "aftradius", "outerradius", "innerradius"):
        if radius_tag in present:
            dname = "diameter" if radius_tag == "radius" else radius_tag.replace("radius", "diameter")
            if dname not in present:
                out.append((dname, DiameterAccessor(el, radius_tag)))
    return out


# ----------------------------------------------------------------- validation

_POSITIVE = ("length", "rootchord", "height", "radius", "outerradius")
_NON_NEGATIVE = ("tipchord", "filletradius", "tabheight", "tablength", "thickness",
                 "foreradius", "aftradius", "innerradius", "mass", "overridemass",
                 "packedlength", "packedradius", "linelength", "instanceseparation")


def validate_element(el):
    """Human-readable geometry problems for one component (empty list if OK)."""
    problems = []
    name = (el.findtext("name") or el.tag).strip()

    def val(tag):
        return parse_numeric_text(el.findtext(tag))[0]

    for tag in _POSITIVE:
        v = val(tag)
        if v is not None and v <= 0:
            problems.append(f"{name}: {tag} must be > 0 (got {v:g} m)")
    for tag in _NON_NEGATIVE:
        v = val(tag)
        if v is not None and v < 0:
            problems.append(f"{name}: {tag} must be >= 0 (got {v:g})")
    if el.tag in FIN_TAGS:
        t = val("thickness")
        if t is not None and t <= 0:
            problems.append(f"{name}: fin thickness must be > 0")
        n = val("fincount")
        if n is not None and not (1 <= n <= 8):
            problems.append(f"{name}: fin count must be 1-8 (got {n:g})")
        s, h = val("sweeplength"), val("height")
        if s is not None and h:
            ang = math.degrees(math.atan2(s, h))
            if not (-89 < ang < 89):
                problems.append(f"{name}: sweep angle {ang:.1f} deg is outside -89..89")
    if el.tag == "nosecone":
        L = val("length")
        if L is not None and L < 0.005:
            problems.append(f"{name}: nose cone length {L * 1000:.1f} mm is too short (< 5 mm)")
    if el.tag == "bodytube":
        r, t = val("radius"), val("thickness")
        if r is not None and t is not None and t > r:
            problems.append(f"{name}: wall thickness exceeds radius")
    for tag, choices in CHOICES.items():
        text = el.findtext(tag)
        if text is not None and text.strip() and text.strip() not in choices:
            if tag == "finish" or (tag == "shape" and el.tag not in BODY_TAGS):
                continue
            problems.append(f"{name}: {tag} '{text.strip()}' not one of {', '.join(choices)}")
    return problems

"""Parsing --vary entries, expanding them into variants, and naming the output files.

Entry syntax (CLI and config strings):

    TARGET OP SPEC [as LABEL] [link GROUP]

    TARGET  selector.param          e.g. fins.rootchord, @"Nose Cone".length
    OP      =   absolute value
            +=  add to the reference value      -=  subtract from it
            *=  multiply the reference value (unitless factor)
    SPEC    single value            0.3   0.3in   7.62mm   airfoil
            list                    10,12,14    square,airfoil
            range (inclusive)       start:stop:step        10:16:1
            N evenly spaced points  start:stop:nN          10:16:n7
    LABEL   name used in filenames / manifest / --name-template
    GROUP   entries sharing a link group advance together (zipped) instead of
            forming a grid with each other
"""

import itertools
import math
import re
from dataclasses import dataclass
from typing import Optional

from .params import CHOICES, SHORT_LABELS, canonical_param, kind_of
from .units import fmt_value, split_quantity

OP_WORDS = {"=": "", "+=": "plus", "-=": "minus", "*=": "x"}

_ENTRY_RE = re.compile(r"^\s*(?P<target>.+?)\s*(?P<op>\+=|-=|\*=|=)\s*(?P<spec>.*?)\s*$", re.S)
_TAIL_RE = re.compile(r"\s+(as|link)\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", re.I)


class EntryError(Exception):
    pass


@dataclass
class Entry:
    source: str
    selector: str
    param: str
    op: str
    kind: str                       # kind used to convert values (factor for *=)
    values: list                    # SI values (or strings for choice / raw)
    display: list                   # values in user units, for names / manifest
    unit: str                       # display unit ('' if unitless)
    label: str
    group: Optional[str] = None
    explicit_label: bool = False

    @property
    def target(self):
        return f"{self.selector}.{self.param}"


def split_target(target):
    target = target.strip()
    if "." not in target:
        raise EntryError(f"'{target}' must look like SELECTOR.PARAM, e.g. fins.rootchord or nosecone.length")
    sel, param = target.rsplit(".", 1)
    if not sel.strip() or not param.strip():
        raise EntryError(f"'{target}' must look like SELECTOR.PARAM")
    return sel.strip(), param.strip()


def _frange(start, stop, step):
    if step == 0:
        raise EntryError("range step can't be 0")
    if (stop - start) * step < 0:
        raise EntryError(f"range step {step:g} goes the wrong way for {start:g} -> {stop:g}")
    n = int(math.floor((stop - start) / step + 1e-9)) + 1
    return [round(start + i * step, 12) for i in range(n)]


def _parse_spec(spec, kind, units):
    """Return the list of SI (or string) values a SPEC stands for."""
    spec = str(spec).strip()
    if spec == "":
        raise EntryError("empty value")
    if kind in ("choice", "bool"):
        return [s.strip() for s in spec.split(",") if s.strip()]
    if kind == "raw":
        toks = [s.strip() for s in spec.split(",") if s.strip()]
        if ":" not in spec and not all(_isnum(t) for t in toks):
            return toks
    qkind = "number" if kind in ("raw", "factor") else kind

    def q(token):
        v, unit = split_quantity(token)
        return units.to_si(qkind, v, unit or None)

    if ":" in spec:
        parts = [p.strip() for p in spec.split(":")]
        if len(parts) != 3:
            raise EntryError(f"range '{spec}' must be start:stop:step or start:stop:nN")
        start, stop = q(parts[0]), q(parts[1])
        if re.fullmatch(r"[nN]\d+", parts[2]):
            n = int(parts[2][1:])
            if n < 1:
                raise EntryError("nN needs N >= 1")
            vals = [start] if n == 1 else [start + (stop - start) * i / (n - 1) for i in range(n)]
        else:
            vals = _frange(start, stop, q(parts[2]))
    else:
        vals = [q(t) for t in spec.split(",") if t.strip()]
    if kind == "int":
        for v in vals:
            if abs(v - round(v)) > 1e-9:
                raise EntryError(f"{v:g} isn't a whole number")
        vals = [int(round(v)) for v in vals]
    return vals


def _isnum(t):
    try:
        split_quantity(t)
        return True
    except ValueError:
        return False


def _values_from_obj(obj, kind, units):
    """Config-file value forms: values / range / linspace / spec."""
    unit = obj.get("unit")

    def tok(v):
        return f"{v}{unit}" if unit and not isinstance(v, str) else str(v)

    if "spec" in obj:
        if unit:
            raise EntryError("\"unit\" can't be combined with \"spec\" -- write units inside the spec (e.g. 250mm:400mm:10mm)")
        return _parse_spec(obj["spec"], kind, units)
    if "values" in obj:
        vals = obj["values"] if isinstance(obj["values"], list) else [obj["values"]]
        return _parse_spec(",".join(tok(v) for v in vals), kind, units)
    if "value" in obj:
        return _parse_spec(tok(obj["value"]), kind, units)
    if "range" in obj:
        r = obj["range"]
        if len(r) != 3:
            raise EntryError("\"range\" must be [start, stop, step]")
        return _parse_spec(":".join(tok(x) for x in r), kind, units)
    if "linspace" in obj:
        r = obj["linspace"]
        if len(r) != 3:
            raise EntryError("\"linspace\" must be [start, stop, count]")
        return _parse_spec(f"{tok(r[0])}:{tok(r[1])}:n{int(r[2])}", kind, units)
    raise EntryError("needs one of \"values\", \"value\", \"range\", \"linspace\" or \"spec\"")


def _finish_entry(source, target, op, raw_values_fn, units, label=None, group=None):
    selector, param = split_target(target)
    param = canonical_param(param)
    default_label = None
    if param == "trim":  # nose-cone trim: length -= value
        if op != "=":
            raise EntryError("use 'trim=VALUE' (it already means length -= VALUE)")
        param, op, default_label = "length", "-=", "trim"
    kind = kind_of(param)
    if op != "=" and kind not in ("length", "angle", "mass", "int", "number", "raw"):
        raise EntryError(f"'{op}' only works on numeric parameters ('{param}' is {kind})")
    value_kind = "factor" if op == "*=" else kind

    try:
        values = raw_values_fn(value_kind)
    except (ValueError, EntryError) as e:
        raise EntryError(f"{source}: {e}")
    if not values:
        raise EntryError(f"{source}: no values")
    if value_kind == "choice" and param in CHOICES:
        bad = [v for v in values if v not in CHOICES[param]]
        if bad:
            raise EntryError(f"{source}: {param} must be one of {', '.join(CHOICES[param])} (got {', '.join(bad)})")

    unit = units.label(value_kind)
    display = [units.from_si(value_kind, v) for v in values]
    if label is None:
        base = default_label or SHORT_LABELS.get(param, param.replace("@", "_"))
        lab = base if default_label else base + OP_WORDS[op]
        explicit = False
    else:
        lab, explicit = label, True
    return Entry(source=source, selector=selector, param=param, op=op, kind=value_kind, values=values,
                 display=display, unit=unit, label=lab, group=group, explicit_label=explicit)


def parse_entry(text, units):
    """Parse a CLI/config string entry."""
    source = text.strip()
    body, label, group = source, None, None
    while True:
        m = _TAIL_RE.search(body)
        if not m:
            break
        if m.group(1).lower() == "as":
            label = m.group(2)
        else:
            group = m.group(2)
        body = body[: m.start()]
    m = _ENTRY_RE.match(body)
    if not m:
        raise EntryError(f"Can't parse '{text}'. Expected SELECTOR.PARAM=VALUE (or +=, -=, *=)")
    return _finish_entry(source, m.group("target"), m.group("op"),
                         lambda kind: _parse_spec(m.group("spec"), kind, units),
                         units, label=label, group=group)


def entry_from_obj(obj, units):
    """Parse a config-file object entry: {"target": ..., "op": ..., "values"|"range"|..., "label", "link"}."""
    if isinstance(obj, str):
        return parse_entry(obj, units)
    if "target" not in obj:
        raise EntryError(f"config entry {obj} has no \"target\"")
    op = obj.get("op", "=")
    op = {"set": "=", "abs": "=", "add": "+=", "delta": "+=", "sub": "-=", "subtract": "-=",
          "scale": "*=", "mul": "*="}.get(op, op)
    if op not in OP_WORDS:
        raise EntryError(f"config entry {obj}: op must be =, +=, -=, *= (or set/add/sub/scale)")
    source = f"{obj['target']}{op}{obj.get('spec', obj.get('values', obj.get('value', obj.get('range', obj.get('linspace')))))}"
    return _finish_entry(source, obj["target"], op, lambda kind: _values_from_obj(obj, kind, units),
                         units, label=obj.get("label") or obj.get("as"),
                         group=obj.get("link") or obj.get("group"))


def dedupe_labels(entries):
    """Make vary labels unique (prefix a selector slug where two entries collide)."""
    seen = {}
    for e in entries:
        seen.setdefault(e.label, []).append(e)
    for lab, group in seen.items():
        if len(group) > 1:
            for e in group:
                if e.explicit_label:
                    raise EntryError(f"Label '{lab}' is used by more than one --vary entry")
                slug = re.sub(r"[^A-Za-z0-9]+", "", e.selector) or "c"
                e.label = f"{slug}_{lab}"
    labels = [e.label for e in entries]
    for i, e in enumerate(entries):
        if labels.count(e.label) > 1:
            e.label = f"{e.label}{i}"
    for e in entries:
        if not e.label.isidentifier():
            e.label = re.sub(r"\W", "_", e.label)
            if not e.label.isidentifier():
                e.label = "_" + e.label


# ------------------------------------------------------------------ expansion

def expand(entries, combine="grid"):
    """Return a list of tuples: value index for each entry, one tuple per variant.

    Entries in the same link group are zipped (must have equal value counts);
    groups (and unlinked entries) are combined as a full grid, or all zipped
    together when combine == 'zip'.
    """
    if not entries:
        return [()]
    groups = {}
    for i, e in enumerate(entries):
        key = ("link", e.group) if e.group else (("zip",) if combine == "zip" else ("own", i))
        groups.setdefault(key, []).append(i)
    group_lists = list(groups.values())
    for members in group_lists:
        counts = {len(entries[i].values) for i in members}
        if len(counts) > 1:
            detail = ", ".join(f"{entries[i].label}={len(entries[i].values)}" for i in members)
            raise EntryError(f"Linked/zipped entries need the same number of values ({detail})")
    combos = []
    for picks in itertools.product(*[range(len(entries[m[0]].values)) for m in group_lists]):
        idx = [0] * len(entries)
        for members, k in zip(group_lists, picks):
            for i in members:
                idx[i] = k
        combos.append(tuple(idx))
    return combos


# --------------------------------------------------------------------- naming

def sanitize_filename(name):
    return re.sub(r'[<>:"/\\|?*\s]+', "_", name).strip("._") or "variant"


def variant_name(prefix, vary_entries, combo, template=None, extra_fields=None):
    """Filename stem for one variant."""
    if template:
        fields = dict(extra_fields or {})
        fields["prefix"] = prefix
        for e, k in zip(vary_entries, combo):
            fields[e.label] = fmt_value(e.display[k])
        try:
            return sanitize_filename(template.format(**fields))
        except KeyError as ex:
            raise EntryError(f"--name-template uses {{{ex.args[0]}}}, which isn't one of: "
                             f"{', '.join(sorted(fields))}")
    parts = [prefix]
    for e, k in zip(vary_entries, combo):
        parts.append(f"{e.label}-{fmt_value(e.display[k])}{e.unit}")
    return sanitize_filename("_".join(parts))

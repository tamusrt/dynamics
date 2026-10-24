"""Planning and building variants from reference .ork files."""

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import geometry
from .orkio import OrkDocument
from .params import ParamError, kind_of, resolve_param, validate_element
from .selectors import ComponentIndex, SelectorError, comp_name
from .sweep import EntryError, expand, variant_name
from .units import fmt_value


class BatchError(Exception):
    pass


@dataclass
class Options:
    prefix: Optional[str] = None
    name_template: Optional[str] = None
    combine: str = "grid"
    preserve_nose_volume: bool = False
    clear_mass_override: bool = False
    keep_sims: bool = False
    allow_invalid: bool = False


class Base:
    """A reference .ork file, loaded once (simulation results stripped unless keep_sims)."""

    def __init__(self, path, keep_sims=False):
        self.path = Path(path)
        self.stem = self.path.stem
        self.doc = OrkDocument(path)
        if not keep_sims:
            self.doc.strip_sim_data()
        self.index = ComponentIndex(self.doc.root)


@dataclass
class Variant:
    base: Base
    combo: tuple                    # value index per vary entry
    name: str = ""
    labels: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    finals: dict = field(default_factory=dict)
    root: object = None


# ------------------------------------------------------------------ checking

def check_entries(base, entries, opts):
    """Resolve every entry against a base file. Returns [(entry, [component labels])].

    Raises BatchError listing every problem found, so typos surface before
    anything is written.
    """
    errors, out = [], []
    for e in entries:
        try:
            els = base.index.resolve(e.selector)
        except SelectorError as ex:
            errors.append(f"{e.source}: {ex}")
            continue
        labels = []
        for el in els:
            try:
                acc = resolve_param(el, e.param)
            except ParamError as ex:
                errors.append(f"{e.source}: {ex}")
                continue
            if e.op != "=":
                cur = acc.read()
                if cur is None or isinstance(cur, str):
                    errors.append(f"{e.source}: '{comp_name(el)}'.{e.param} has no numeric value "
                                  f"({el.findtext(e.param)!r}) to apply '{e.op}' to -- use '='")
            labels.append(f"{comp_name(el)} <{el.tag}>")
        out.append((e, labels))
    if errors:
        raise BatchError(f"{base.path.name}:\n  - " + "\n  - ".join(errors))
    return out


# ------------------------------------------------------------------ planning

def plan(bases, varies, opts):
    """Expand bases x vary combinations into Variant objects (not built yet)."""
    combos = expand(varies, opts.combine)
    variants = []
    for base in bases:
        prefix = base.stem if not opts.prefix else (opts.prefix if len(bases) == 1 else f"{opts.prefix}_{base.stem}")
        for i, combo in enumerate(combos, 1):
            labels = {e.label: e.display[k] for e, k in zip(varies, combo)}
            name = variant_name(prefix, varies, combo, opts.name_template,
                                extra_fields={"base": base.stem, "i": i})
            variants.append(Variant(base, combo, name=name, labels=labels))

    seen = {}
    for v in variants:
        key = v.name.lower()
        if key in seen:
            seen[key] += 1
            v.name = f"{v.name}_{seen[key]}"
        else:
            seen[key] = 1
    return variants


# ------------------------------------------------------------------ building

def _apply_op(acc, op, value):
    if op == "=":
        acc.write(value)
        return
    cur = acc.read()
    new = cur + value if op == "+=" else cur - value if op == "-=" else cur * value
    if acc.kind == "int":
        new = int(round(new))
    acc.write(new)


def _column(el, param, units):
    kind = kind_of(param)
    u = units.label(kind)
    return f"{comp_name(el)}.{param}" + (f" ({u})" if u else "")


def build_variant(variant, varies, opts, units):
    """Apply all edits to a fresh copy of the base rocket. Fills variant.root / notes / problems / finals."""
    base = variant.base
    root = base.doc.clone_root()
    index = ComponentIndex(root)
    assigns = [(e, e.values[k]) for e, k in zip(varies, variant.combo)]

    noses = {id(n): (n, geometry.nose_state(n)) for n in index.components if n.tag == "nosecone"}
    modified, explicit_override, deferred = [], set(), []

    def touch(el):
        if el not in modified:
            modified.append(el)

    for e, value in assigns:
        for el in index.resolve(e.selector):
            acc = resolve_param(el, e.param)
            if e.param == "sweepangle":
                deferred.append((acc, e.op, value))  # after height changes, so sweep uses the new height
            else:
                _apply_op(acc, e.op, value)
            if e.param == "overridemass":
                explicit_override.add(id(el))
            touch(el)
    for acc, op, value in deferred:
        _apply_op(acc, op, value)

    notes, problems = [], []
    if opts.preserve_nose_volume:
        for nc, before in noses.values():
            if nc in modified:
                n, m = geometry.preserve_nose_volume(index, nc, before)
                notes += n
                for el in m:
                    touch(el)

    if opts.clear_mass_override:
        for el in modified:
            ov = el.find("overridemass")
            if ov is not None and id(el) not in explicit_override:
                el.remove(ov)
                flag = el.find("overridesubcomponentsmass")
                if flag is not None:
                    el.remove(flag)
                notes.append(f"cleared mass override on '{comp_name(el)}'")

    for el in modified:
        problems += validate_element(el)

    finals = {}
    for e, _ in assigns:
        for el in index.resolve(e.selector):
            acc = resolve_param(el, e.param)
            finals[_column(el, e.param, units)] = fmt_value(units.from_si(acc.kind, acc.read()))

    variant.root, variant.notes, variant.problems, variant.finals = root, notes, problems, finals
    return variant


def write_manifest(path, variants, varies, statuses):
    label_cols = [e.label + (f" ({e.unit})" if e.unit else "") for e in varies]
    final_cols = []
    for v in variants:
        for k in v.finals:
            if k not in final_cols:
                final_cols.append(k)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "base", "status"] + label_cols + final_cols + ["problems", "notes"])
        for v in variants:
            row = [v.name + ".ork", v.base.path.name, statuses.get(id(v), "")]
            row += [fmt_value(v.labels.get(e.label)) for e in varies]
            row += [v.finals.get(c, "") for c in final_cols]
            row += ["; ".join(v.problems), "; ".join(v.notes)]
            w.writerow(row)


__all__ = ["BatchError", "EntryError", "Options", "Base", "Variant", "check_entries", "plan",
           "build_variant", "write_manifest"]

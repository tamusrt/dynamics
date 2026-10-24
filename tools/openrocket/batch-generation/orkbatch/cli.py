"""Command-line interface. Run `python ork_batch.py -h` or see README.md."""

import argparse
import glob
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

from . import __version__
from .engine import BatchError, Base, Options, build_variant, check_entries, plan, write_manifest
from .orkio import OrkDocument, OrkLoadError
from .params import (ALIASES, ANGLE_TAGS, BOOL_TAGS, CHOICES, FIN_TAGS, INT_TAGS, LENGTH_TAGS, MASS_TAGS,
                     NUMBER_TAGS, SHORT_LABELS, ParamError, kind_of, list_params, parse_numeric_text)
from .selectors import ComponentIndex, SelectorError, comp_name
from .sweep import EntryError, dedupe_labels, entry_from_obj
from .units import LENGTH_UNITS, MASS_UNITS, Units, fmt_value

BIG_BATCH = 1000
cancel_event = None  # a threading.Event set by a front end (the GUI) to stop a running batch

# config keys that map 1:1 onto Options / run settings
SCALAR_KEYS = ("out_dir", "prefix", "name_template", "unit", "mass_unit", "combine", "preserve_nose_volume",
               "clear_mass_override", "keep_sims", "allow_invalid", "overwrite", "manifest", "dedupe")
LIST_KEYS = ("base", "vary")
DEFAULTS = {"out_dir": "ork_variants", "unit": "in", "mass_unit": "kg", "combine": "grid",
            "manifest": True, "dedupe": True}
REMOVED_KEYS = {"set", "sample", "seed", "include_baseline", "where", "insert_transition", "resize_forward",
                "preserve_transition_volume"}


def die(msg, code=1):
    sys.stdout.flush()
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def confirm(question, assume_yes):
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        return False
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


# ------------------------------------------------------------------- config

def _as_list(v):
    if v is None:
        return []
    return list(v) if isinstance(v, list) else [v]


def load_config(path):
    path = Path(path)
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        die(f"can't read config {path}: {e}")
    if not isinstance(cfg, dict):
        die(f"config {path} must be a JSON object")
    cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}  # "_comment" keys etc.
    unknown = set(cfg) - set(SCALAR_KEYS) - set(LIST_KEYS)
    removed = unknown & REMOVED_KEYS
    if removed:
        die(f"config {path}: {', '.join(sorted(removed))} no longer supported -- fixed edits, sampling, filters "
            f"and transition insertion were removed; only sweeps under \"vary\" are allowed")
    if unknown:
        die(f"config {path}: unknown key(s) {', '.join(sorted(unknown))}")
    here = path.parent
    if "base" in cfg:
        cfg["base"] = [str(p if Path(p).is_absolute() else here / p) for p in _as_list(cfg["base"])]
    if cfg.get("out_dir") and not Path(cfg["out_dir"]).is_absolute():
        cfg["out_dir"] = str(here / cfg["out_dir"])
    return cfg


def merge_settings(args):
    """defaults <- config file <- command line (lists are concatenated)."""
    s = dict(DEFAULTS)
    cfg = load_config(args.config) if getattr(args, "config", None) else {}
    for k in SCALAR_KEYS:
        if k in cfg:
            s[k] = cfg[k]
    for k in LIST_KEYS:
        s[k] = _as_list(cfg.get(k))
    for k in SCALAR_KEYS:
        v = getattr(args, k, None)
        if v is not None:
            s[k] = v
    cli_bases = [b for group in (getattr(args, "base", None) or []) for b in group]
    s["base"] += cli_bases
    s["vary"] += getattr(args, "vary", None) or []
    return s


def expand_bases(patterns):
    out = []
    for p in patterns:
        hits = sorted(glob.glob(p)) if any(ch in p for ch in "*?[") else [p]
        if not hits:
            die(f"no files match '{p}'")
        for h in hits:
            if Path(h).is_dir():
                hits_dir = sorted(Path(h).glob("*.ork"))
                if not hits_dir:
                    die(f"folder '{h}' has no .ork files")
                out += [str(x) for x in hits_dir]
            else:
                out.append(h)
    seen, uniq = set(), []
    for p in out:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def make_options(s):
    return Options(prefix=s.get("prefix"), name_template=s.get("name_template"), combine=s["combine"],
                   preserve_nose_volume=bool(s.get("preserve_nose_volume")),
                   clear_mass_override=bool(s.get("clear_mass_override")), keep_sims=bool(s.get("keep_sims")),
                   allow_invalid=bool(s.get("allow_invalid")))


def parse_entries(s, units):
    try:
        varies = [entry_from_obj(x, units) for x in s["vary"]]
        dedupe_labels(varies)
    except EntryError as e:
        die(str(e))
    return varies


def load_bases(paths, keep_sims):
    bases = []
    for p in paths:
        try:
            bases.append(Base(p, keep_sims=keep_sims))
        except OrkLoadError as e:
            die(str(e))
    return bases


# ------------------------------------------------------------------ inspect

def _fmt_param(name, acc, units):
    v = acc.read()
    kind = acc.kind
    if v is None:
        return f"{name}={'auto' if acc.is_auto() else '?'}"
    shown = fmt_value(units.from_si(kind, v)) if not isinstance(v, str) else v
    u = units.label(kind) if not isinstance(v, str) else ""
    auto = " (auto)" if acc.is_auto() else ""
    return f"{name}={shown}{(' ' + u) if u and shown != '' else ''}{auto}"


def cmd_inspect(args):
    units = Units(args.unit, args.mass_unit)
    try:
        doc = OrkDocument(args.file)
    except OrkLoadError as e:
        die(str(e))
    index = ComponentIndex(doc.root)
    comps = index.components
    if args.select:
        try:
            comps = index.resolve(args.select)
        except SelectorError as e:
            die(str(e))
    if args.type:
        wanted = set(FIN_TAGS) if args.type.lower() in ("fins", "fin") else {args.type.lower()}
        comps = [c for c in comps if c.tag in wanted]

    if args.json:
        out = []
        for c in comps:
            sel_tag, sel_name = index.canonical(c)
            params = {}
            for name, acc in list_params(c):
                v = acc.read()
                params[name] = {"value": units.from_si(acc.kind, v) if not isinstance(v, str) else v,
                                "unit": units.label(acc.kind) if not isinstance(v, str) else "",
                                "kind": acc.kind, "auto": acc.is_auto(), "virtual": acc.virtual}
            out.append({"name": comp_name(c), "type": c.tag, "id": c.findtext("id"),
                        "selector": sel_tag, "name_selector": sel_name, "depth": index.depth(c),
                        "params": params})
        print(json.dumps(out, indent=2))
        return

    print(f"{Path(args.file).name}   ({doc.container} container, {doc.simulation_count()} simulation(s), "
          f"lengths in {units.length}, masses in {units.mass}, angles in deg)")
    print(f"Rocket: {comp_name(index.rocket)}\n")
    width = shutil.get_terminal_size((120, 20)).columns
    for c in comps:
        depth = index.depth(c)
        pad = "  " * depth
        sel_tag, sel_name = index.canonical(c)
        sels = sel_tag + (f"  |  {sel_name}" if sel_name else "")
        print(f"{pad}{comp_name(c)}  <{c.tag}>    select: {sels}")
        if args.brief:
            continue
        items = [_fmt_param(n, a, units) for n, a in list_params(c)]
        line, indent = pad + "    ", pad + "    "
        for it in items:
            if len(line) + len(it) + 3 > width and line.strip():
                print(line.rstrip())
                line = indent
            line += it + "   "
        if line.strip():
            print(line.rstrip())
    print("\nUse SELECTOR.PARAM in --vary, e.g.  fins.rootchord=12   nosecone.length-=1:5:1")


# ------------------------------------------------------------------- params

def cmd_params(args):
    def show(title, names):
        print(f"{title}:")
        line = "    "
        for n in sorted(names):
            if len(line) + len(n) > 96:
                print(line)
                line = "    "
            line += n + "  "
        print(line + "\n")

    print("Any leaf tag shown by `inspect` can be targeted. Known tags and how values are converted:\n")
    show(f"Lengths (default --unit; accepts {', '.join(LENGTH_UNITS)} suffixes)", LENGTH_TAGS | {"diameter"})
    show("Angles (deg; 'rad' suffix accepted)", ANGLE_TAGS)
    show(f"Masses (default --mass-unit; accepts {', '.join(MASS_UNITS)})", MASS_TAGS)
    show("Integers", INT_TAGS)
    show("Unitless numbers", NUMBER_TAGS | {"<tag>@density"})
    print("Choices:")
    for k, v in CHOICES.items():
        print(f"    {k}: {', '.join(v)}")
    print()
    show("Booleans (true/false)", BOOL_TAGS)
    print("Virtual parameters:")
    print("    diameter / forediameter / aftdiameter / outerdiameter / innerdiameter -> 2 x matching radius")
    print("    sweepangle      fin sweep angle (deg); writes sweeplength = height * tan(angle)")
    print("    trim            nose-cone trim: 'nosecone.trim=2' == 'nosecone.length-=2'")
    print("    TAG@ATTR        an XML attribute, e.g. material@density, axialoffset@method\n")
    print("Aliases: " + ", ".join(f"{a}->{t}" for a, t in sorted(ALIASES.items())))
    print("Default filename labels: " + ", ".join(f"{t}->{a}" for t, a in sorted(SHORT_LABELS.items())))


# ----------------------------------------------------------------- generate

def _print_resolution(base, resolved, quiet):
    if quiet:
        return
    print(f"{base.path.name}:")
    for e, labels in resolved:
        print(f"   vary {e.target} {e.op} {len(e.values)} value(s)  -> {', '.join(labels)}   [label {e.label}]")


def _snapshot(s, bases, path):
    snap = {k: s.get(k) for k in SCALAR_KEYS if s.get(k) is not None}
    snap["base"] = [str(b.path.resolve()) for b in bases]
    snap["vary"] = s["vary"]
    snap["out_dir"] = str(Path(s["out_dir"]).resolve())
    snap["_generated_by"] = f"ork_batch.py {__version__} on {time.strftime('%Y-%m-%d %H:%M:%S')}"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2)


def _print_table(variants, varies, limit):
    rows = variants if limit is None else variants[:limit]
    heads = ["#", "file"] + [e.label + (f"({e.unit})" if e.unit else "") for e in varies] + ["problems"]
    data = []
    for i, v in enumerate(rows, 1):
        vals = [fmt_value(v.labels.get(e.label)) for e in varies]
        data.append([str(i), v.name + ".ork"] + vals + ["; ".join(v.problems)[:80]])
    widths = [max(len(h), *(len(r[j]) for r in data)) if data else len(h) for j, h in enumerate(heads)]
    print("  ".join(h.ljust(w) for h, w in zip(heads, widths)))
    for r in data:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))
    if limit is not None and len(variants) > limit:
        print(f"... {len(variants) - limit} more (use --show all)")


def cmd_generate(args):
    s = merge_settings(args)
    if not s["base"]:
        die("no reference .ork given (use -b/--base or \"base\" in the config)")
    try:
        units = Units(s["unit"], s["mass_unit"])
    except ValueError as e:
        die(str(e))
    if s["combine"] not in ("grid", "zip"):
        die("--combine must be grid or zip")
    varies = parse_entries(s, units)
    if not varies:
        die("nothing to vary -- add --vary entries")
    opts = make_options(s)
    bases = load_bases(expand_bases(s["base"]), opts.keep_sims)

    try:
        for b in bases:
            _print_resolution(b, check_entries(b, varies, opts), args.quiet)
        variants = plan(bases, varies, opts)
    except (BatchError, EntryError) as e:
        die(str(e))

    out_dir = Path(s["out_dir"])

    if args.dry_run:
        for v in variants:
            build_variant(v, varies, opts, units)
        print(f"\nDry run: {len(variants)} variant(s) from {len(bases)} reference file(s) -> {out_dir}")
        bad = sum(1 for v in variants if v.problems)
        if bad:
            print(f"{bad} variant(s) have geometry problems and would be "
                  f"{'written anyway (--allow-invalid)' if opts.allow_invalid else 'skipped'}.")
        print()
        _print_table(variants, varies, None if args.show == "all" else int(args.show))
        return

    if len(variants) > BIG_BATCH and not confirm(f"This will write {len(variants)} files. Continue?", args.yes):
        die(f"aborted ({len(variants)} files; pass -y to confirm big batches)")
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = [v for v in variants if (out_dir / (v.name + ".ork")).exists()]
    if existing and not s.get("overwrite"):
        if not confirm(f"{len(existing)} file(s) already exist in {out_dir}. Overwrite?", args.yes):
            die(f"{len(existing)} output file(s) already exist (e.g. {existing[0].name}.ork); "
                f"pass --overwrite or -y, or pick another --out-dir")

    statuses, written, skipped, dupes = {}, 0, 0, 0
    seen_xml = {}
    t0 = time.time()
    n = len(variants)
    for i, v in enumerate(variants, 1):
        if cancel_event is not None and cancel_event.is_set():
            print(f"Cancelled after {i - 1} of {n} variant(s); the manifest lists only those.")
            break
        tag = f"[{i:>{len(str(n))}}/{n}]"
        try:
            build_variant(v, varies, opts, units)
        except (SelectorError, ParamError) as e:
            v.problems.append(str(e))
        if v.problems and not opts.allow_invalid:
            statuses[id(v)] = "skipped"
            skipped += 1
            print(f"{tag} SKIP {v.name}.ork: {'; '.join(v.problems)}")
            v.root = None
            continue
        xml = v.base.doc.serialize(v.root)
        key = (str(v.base.path), hashlib.sha1(xml).hexdigest())
        if s.get("dedupe", True) and key in seen_xml:
            statuses[id(v)] = f"duplicate of {seen_xml[key]}.ork"
            dupes += 1
            if not args.quiet:
                print(f"{tag} SKIP {v.name}.ork: identical to {seen_xml[key]}.ork")
            v.root = None
            continue
        seen_xml[key] = v.name
        v.base.doc.save(out_dir / (v.name + ".ork"), xml_bytes=xml)
        statuses[id(v)] = "written" if not v.problems else "written-invalid"
        written += 1
        if not args.quiet:
            extra = f"   ({'; '.join(v.notes)})" if v.notes and args.verbose else ""
            print(f"{tag} wrote {v.name}.ork{extra}")
        v.root = None  # free memory as we go

    if s.get("manifest", True):
        write_manifest(out_dir / "variants_manifest.csv", [v for v in variants if id(v) in statuses],
                       varies, statuses)
        _snapshot(s, bases, out_dir / "batch_config.json")
    print(f"\nDone in {time.time() - t0:.1f}s: {written} written, {skipped} skipped (invalid), "
          f"{dupes} skipped (duplicate) -> {out_dir.resolve()}")
    if s.get("manifest", True):
        print("Manifest: variants_manifest.csv   Re-run config: batch_config.json")
    if bases and all(b.doc.simulation_count() == 0 for b in bases):
        print("note: the reference file(s) have no saved simulations, so batch_simulate.py "
              "won't have anything to run on these variants.")


# --------------------------------------------------------------------- diff

def _leaf_values(el):
    vals = {}
    for c in el:
        if not isinstance(c.tag, str) or len(c) or c.tag in ("id",):
            continue
        vals[c.tag] = (c.text or "").strip()
        for a, av in c.attrib.items():
            vals[f"{c.tag}@{a}"] = av
    return vals


def cmd_diff(args):
    units = Units(args.unit, args.mass_unit)
    try:
        a, b = OrkDocument(args.a), OrkDocument(args.b)
    except OrkLoadError as e:
        die(str(e))
    ia, ib = ComponentIndex(a.root), ComponentIndex(b.root)

    # pair components: same <id>, else same tag+name, else Nth unmatched of the same tag
    pairs, free_b = {}, list(ib.components)
    for match in (lambda x, y: x.findtext("id") == y.findtext("id"),
                  lambda x, y: x.tag == y.tag and comp_name(x) == comp_name(y),
                  lambda x, y: x.tag == y.tag):
        for ca in ia.components:
            if id(ca) in pairs:
                continue
            cb = next((c for c in free_b if match(ca, c)), None)
            if cb is not None:
                pairs[id(ca)] = cb
                free_b.remove(cb)

    changes = 0
    for ca in ia.components:
        cb = pairs.get(id(ca))
        if cb is None:
            print(f"- only in A: {comp_name(ca)} <{ca.tag}>")
            changes += 1
            continue
        va, vb = _leaf_values(ca), _leaf_values(cb)
        lines = []
        for t in sorted(set(va) | set(vb)):
            x, y = va.get(t), vb.get(t)
            if x == y:
                continue
            kind = kind_of(t)
            nx, ny = parse_numeric_text(x)[0] if x else None, parse_numeric_text(y)[0] if y else None
            if kind in ("length", "angle", "mass") and nx is not None and ny is not None:
                if abs(nx - ny) <= 1e-12 * max(1.0, abs(nx)):
                    continue
                u = units.label(kind)
                d = units.from_si(kind, ny - nx)
                lines.append(f"    {t}: {fmt_value(units.from_si(kind, nx))} -> "
                             f"{fmt_value(units.from_si(kind, ny))} {u}  ({'+' if d >= 0 else ''}{fmt_value(d)})")
            else:
                lines.append(f"    {t}: {x!r} -> {y!r}")
        if lines:
            renamed = f"  (B: '{comp_name(cb)}')" if comp_name(cb) != comp_name(ca) else ""
            print(f"~ {comp_name(ca)} <{ca.tag}>{renamed}")
            print("\n".join(lines))
            changes += 1
    for c in free_b:
        print(f"+ only in B: {comp_name(c)} <{c.tag}>")
        changes += 1
    if not changes:
        print("No component differences.")


# -------------------------------------------------------------- init-config

EXAMPLE_CONFIG = {
    "_comment": "ork_batch.py config. Paths are relative to this file. CLI flags override / extend these.",
    "base": "../path/to/reference.ork",
    "out_dir": "ork_variants",
    "prefix": "Variant",
    "unit": "in",
    "mass_unit": "kg",
    "combine": "grid",
    "vary": [
        "fins.rootchord=10:16:1",
        "fins.tipchord=4:8:1",
        {"target": "fins.height", "values": [6, 7, 8], "label": "span"},
        {"target": "nosecone.length", "op": "-=", "range": [0, 4, 2], "label": "trim"},
    ],
    "clear_mass_override": False,
    "preserve_nose_volume": False,
    "keep_sims": False,
}


def cmd_init_config(args):
    p = Path(args.path)
    if p.exists() and not args.force:
        die(f"{p} exists (use --force)")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(EXAMPLE_CONFIG, f, indent=2)
    print(f"wrote {p}")


# ------------------------------------------------------------------- parser

def _add_unit_args(p):
    p.add_argument("--unit", choices=sorted(LENGTH_UNITS), default=None,
                   help="default length unit for values you type and for display (default in)")
    p.add_argument("--mass-unit", choices=sorted(MASS_UNITS), default=None,
                   help="default mass unit (default kg)")


def _add_generate_args(p):
    p.add_argument("-c", "--config", help="JSON config file (see init-config)")
    _add_unit_args(p)
    p.add_argument("--preserve-nose-volume", action=argparse.BooleanOptionalAction, default=None,
                   help="when a nose cone loses internal volume (trim, shape change), lengthen the tube behind it")
    p.add_argument("--clear-mass-override", action=argparse.BooleanOptionalAction, default=None,
                   help="remove <overridemass> from every component that gets modified, so OpenRocket "
                        "recomputes its mass from the new geometry")
    p.add_argument("--keep-sims", action=argparse.BooleanOptionalAction, default=None,
                   help="keep stored simulation results (default: strip them and mark sims outdated)")
    p.add_argument("--allow-invalid", action=argparse.BooleanOptionalAction, default=None,
                   help="write variants even if they fail geometry checks")
    p.add_argument("--overwrite", action=argparse.BooleanOptionalAction, default=None,
                   help="overwrite existing output files without asking")
    p.add_argument("-y", "--yes", action="store_true", help="answer yes to all prompts")
    p.add_argument("-q", "--quiet", action="store_true", help="only print errors, skips and the summary")
    p.add_argument("--verbose", action="store_true", help="print per-file notes (volume compensation etc.)")


def build_parser():
    ap = argparse.ArgumentParser(
        prog="ork_batch.py",
        description="Batch-generate OpenRocket .ork variants from reference designs.",
        epilog="Run `ork_batch.py <command> -h` for details. Full docs: README.md")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="command", metavar="command")

    p = sub.add_parser("inspect", help="list components, selectors and parameter values of an .ork")
    p.add_argument("file")
    _add_unit_args(p)
    p.add_argument("--brief", action="store_true", help="component tree only, no parameter values")
    p.add_argument("--type", help="only show components of this tag (e.g. nosecone, fins)")
    p.add_argument("--select", metavar="SELECTOR", help="only show components matching a selector")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("params", help="reference of parameter names, units, aliases and virtual params")
    p.set_defaults(func=cmd_params)

    p = sub.add_parser("generate", aliases=["gen"], help="generate a batch of variants (sweeps)")
    p.add_argument("-b", "--base", action="append", nargs="+", metavar="ORK",
                   help="reference .ork file(s), globs or folders (repeatable)")
    _add_generate_args(p)
    p.add_argument("-v", "--vary", action="append", metavar="SPEC",
                   help="swept parameter: 'TARGET OP VALUES [as LABEL] [link GROUP]' (repeatable)")
    p.add_argument("-o", "--out-dir", help="output folder (default ./ork_variants)")
    p.add_argument("--prefix", help="filename prefix (default: reference file name)")
    p.add_argument("--name-template", help="filename template, e.g. '{base}_{trim}Trim' "
                                           "(fields: prefix, base, i, and every vary label)")
    p.add_argument("--combine", choices=["grid", "zip"], default=None,
                   help="grid = every combination (default); zip = step all unlinked vary entries together")
    p.add_argument("--dedupe", action=argparse.BooleanOptionalAction, default=None,
                   help="skip variants whose output is identical to an earlier one (default on)")
    p.add_argument("--manifest", action=argparse.BooleanOptionalAction, default=None,
                   help="write variants_manifest.csv + batch_config.json (default on)")
    p.add_argument("-n", "--dry-run", action="store_true", help="show what would be generated, write nothing")
    p.add_argument("--show", default="25", help="rows to show in --dry-run (number or 'all')")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("diff", help="show parameter differences between two .ork files")
    p.add_argument("a")
    p.add_argument("b")
    _add_unit_args(p)
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("init-config", help="write an example JSON config to start from")
    p.add_argument("path", nargs="?", default="ork_batch_config.json")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init_config)
    return ap


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    for name in ("unit", "mass_unit"):
        if hasattr(args, name) and getattr(args, name) is None and args.func in (cmd_inspect, cmd_diff):
            setattr(args, name, DEFAULTS[name])
    try:
        args.func(args)
    except KeyboardInterrupt:
        die("interrupted", 130)
    return 0

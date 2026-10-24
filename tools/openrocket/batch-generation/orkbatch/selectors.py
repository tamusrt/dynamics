"""Finding components in a rocket.

Selector grammar (case-insensitive), one or more segments joined by '/':

    nosecone              the only <nosecone> (error if there are several)
    bodytube[2]           the third <bodytube> in document order (0-based; -1 = last)
    bodytube[*]           every <bodytube>
    fins, fins[1]         any fin set type (trapezoid / elliptical / freeform / tube)
    nose, tube            shorthand for nosecone, bodytube
    *                     any component
    @Fiberglass Tube      component by name   (also @"Fiberglass Tube", @'...')
    @RECO Bulkhead[1]     second component with that name
    #585ea21c             component whose <id> starts with this
    @Fiberglass Tube/masscomponent[0]
                          a segment after '/' only searches inside the previous match

Indices are counted within the current scope (the whole rocket for the first
segment). `inspect` prints a ready-to-use selector next to every component.
"""

import re

from .params import FIN_TAGS

TYPE_ALIASES = {
    "fins": FIN_TAGS, "fin": FIN_TAGS, "finset": FIN_TAGS, "finsets": FIN_TAGS,
    "nose": ("nosecone",), "tube": ("bodytube",), "body": ("bodytube",),
}

_IDX = r"(?:\s*\[\s*(?P<idx>\*|-?\d+)\s*\])?"
_NAME_RE = re.compile(r"^@\s*(?:\"(?P<q1>[^\"]*)\"|'(?P<q2>[^']*)'|(?P<bare>.+?))" + _IDX + r"\s*$", re.S)
_ID_RE = re.compile(r"^#\s*(?P<id>[0-9A-Za-z-]+)\s*$")
_TYPE_RE = re.compile(r"^(?P<type>[A-Za-z]+|\*)" + _IDX + r"\s*$")


class SelectorError(Exception):
    pass


def comp_name(el):
    return (el.findtext("name") or "").strip() or f"(unnamed {el.tag})"


def split_segments(selector):
    """Split on '/' that isn't inside quotes."""
    segs, cur, quote = [], [], None
    for ch in selector:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch == "/":
            segs.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    segs.append("".join(cur).strip())
    if any(not s for s in segs):
        raise SelectorError(f"Empty segment in selector '{selector}'")
    return segs


class ComponentIndex:
    """All components of a rocket (elements with an <id> that live in <subcomponents>)."""

    def __init__(self, root):
        self.root = root
        self.rocket = root.find("rocket")
        self.parent = {c: p for p in self.rocket.iter() for c in p}
        self.components = [el for el in self.rocket.iter()
                           if el is not self.rocket and isinstance(el.tag, str)
                           and el.find("id") is not None
                           and getattr(self.parent.get(el), "tag", None) == "subcomponents"]

    def owner(self, el):
        """The component that contains `el` (via its <subcomponents>), or None."""
        sc = self.parent.get(el)
        return self.parent.get(sc) if sc is not None else None

    def depth(self, el):
        d, cur = 0, self.owner(el)
        while cur is not None and cur is not self.rocket:
            d, cur = d + 1, self.owner(cur)
        return d

    def descendants(self, scope):
        inside = set(id(e) for e in scope.iter())
        return [c for c in self.components if c is not scope and id(c) in inside]

    def canonical(self, el):
        """A selector that uniquely identifies `el` (tag[i] globally, or @name if unique)."""
        name = comp_name(el)
        same_name = [c for c in self.components if comp_name(c).lower() == name.lower()]
        if len(same_name) == 1 and el.findtext("name"):
            sel_name = f'@"{name}"' if any(ch in name for ch in "/[]'\"") else f"@{name}"
        else:
            sel_name = None
        same_tag = [c for c in self.components if c.tag == el.tag]
        sel_tag = el.tag if len(same_tag) == 1 else f"{el.tag}[{same_tag.index(el)}]"
        return sel_tag, sel_name

    def resolve(self, selector):
        """Return the list of elements matched by `selector` (never empty; raises SelectorError)."""
        scopes = [None]
        for seg in split_segments(selector):
            found = []
            for scope in scopes:
                pool = self.components if scope is None else self.descendants(scope)
                for el in self._match_segment(seg, pool, selector):
                    if el not in found:
                        found.append(el)
            scopes = found
        return scopes

    def _match_segment(self, seg, pool, full):
        idx = None
        m = _NAME_RE.match(seg)
        if m:
            name = (m.group("q1") if m.group("q1") is not None else
                    m.group("q2") if m.group("q2") is not None else m.group("bare")).strip()
            cands = [c for c in pool if comp_name(c).lower() == name.lower()]
            what = f"named '{name}'"
            idx = m.group("idx")
        elif _ID_RE.match(seg):
            prefix = _ID_RE.match(seg).group("id").lower()
            cands = [c for c in pool if (c.findtext("id") or "").strip().lower().startswith(prefix)]
            what = f"with id starting '{prefix}'"
        else:
            m = _TYPE_RE.match(seg)
            if not m:
                raise SelectorError(f"Can't understand selector segment '{seg}' in '{full}'")
            t = m.group("type").lower()
            idx = m.group("idx")
            if t == "*":
                cands = list(pool)
            else:
                tags = TYPE_ALIASES.get(t, (t,))
                cands = [c for c in pool if c.tag in tags]
            what = f"of type '{t}'"

        if not cands:
            raise SelectorError(f"No component {what} (selector '{full}'). Run `inspect` to see components.")
        if idx == "*":
            return cands
        if idx is None:
            if len(cands) > 1:
                listing = "; ".join(f"[{i}] {comp_name(c)} <{c.tag}>" for i, c in enumerate(cands))
                raise SelectorError(f"Selector '{full}' matches {len(cands)} components {what}: {listing}. "
                                    f"Add an index like [0] or [*], or select by @name.")
            return cands
        i = int(idx)
        if not (-len(cands) <= i < len(cands)):
            raise SelectorError(f"Index [{i}] out of range: only {len(cands)} component(s) {what} "
                                f"(selector '{full}')")
        return [cands[i]]

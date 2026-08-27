# THIS IS A THROWAWAY HARNESS, KEPT BECAUSE IT WAS USEFUL - NOT MAINTAINED.
#
# It reads from `app/` and it is NOT covered by the test suite, so nothing fails
# when production moves underneath it. Assume it is stale until you have run it.
# Before trusting anything it draws, check the notes at the top of
# prototype/glasses/README.md, and run it: an ImportError or a KeyError is the
# cheap failure. The expensive one is a page that still renders while quietly
# disagreeing with what the app ships.
#
# The exception: this is a RULE, not a page. It takes a path and returns a
# path, touches nothing in `app/`, and is the least likely thing here to
# rot. Every hand-drawn glass in production went through it.

"""PROTOTYPE - THROWAWAY. Make a hand-drawn SVG path symmetrical about x=150.

The maintainer's hand-modelled glasses are drawn by eye, so the two sides never
quite agree - a few units of drift that reads as a wonky glass once the shape is
otherwise right. Rather than nudging coordinates one at a time, this expresses
each correction as a RULE, so the same rule can be applied to every glass and
compared honestly:

    mirror_left   - keep the left profile, reflect it to make the right
    mirror_right  - keep the right profile, reflect it to make the left
    average       - meet in the middle: each pair averages its distance
                    from the axis, and its height

All three then recentre the result on x=150 so the pour, the stem and the head
share one axis.

The trick that makes this simple: these outlines are all drawn as one closed
loop down one side, across the bottom, and back up the other, so the Nth point
from the start is the mirror partner of the Nth point from the end. Correcting
symmetry is then a fold of that list onto itself. Commands are never rewritten -
only the points they carry move - so an arc keeps its radii and its sweep flag
and nothing has to be re-reasoned.

NOT production code. If a hand-drawn shape wins, its corrected path is pasted
into `app/beer_glass.py` as literal path data; this module does not ship.
"""
from __future__ import annotations

import re

_TOKEN = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|(-?\d*\.?\d+(?:[eE][-+]?\d+)?)")

# How many trailing points of each command are (x, y) pairs we may move.
# For an arc only the endpoint is a point; rx/ry/rotation/flags are not.
_POINT_COUNT = {"M": 1, "L": 1, "C": 3, "Q": 2, "A": 1, "Z": 0}


def _tokenize(d: str):
    for cmd, num in _TOKEN.findall(d):
        yield ("cmd", cmd) if cmd else ("num", float(num))


def parse(d: str) -> list[tuple[str, list[float]]]:
    """Path data -> a list of (command, params) with every point absolute."""
    out: list[tuple[str, list[float]]] = []
    toks = list(_tokenize(d))
    i = 0
    cur = cmd = None
    x = y = start_x = start_y = 0.0

    def nums(n: int) -> list[float]:
        nonlocal i
        vals = []
        while len(vals) < n:
            kind, v = toks[i]
            if kind != "num":
                raise ValueError(f"expected a number, got {v!r}")
            vals.append(v)
            i += 1
        return vals

    while i < len(toks):
        kind, v = toks[i]
        if kind == "cmd":
            cmd = v
            i += 1
            if cmd in "Zz":
                out.append(("Z", []))
                x, y = start_x, start_y
                cur = None
                continue
            cur = cmd
        elif cur is None:
            raise ValueError("path does not start with a command")
        else:
            # A repeated parameter set: an implicit repeat of the last command
            # (M repeats as L, per the SVG spec).
            cmd = {"M": "L", "m": "l"}.get(cur, cur)

        rel = cmd.islower()
        up = cmd.upper()

        if up == "M":
            px, py = nums(2)
            x, y = (x + px, y + py) if rel else (px, py)
            start_x, start_y = x, y
            out.append(("M", [x, y]))
            cur = cmd
        elif up == "L":
            px, py = nums(2)
            x, y = (x + px, y + py) if rel else (px, py)
            out.append(("L", [x, y]))
        elif up == "H":
            (px,) = nums(1)
            x = x + px if rel else px
            out.append(("L", [x, y]))
        elif up == "V":
            (py,) = nums(1)
            y = y + py if rel else py
            out.append(("L", [x, y]))
        elif up == "C":
            a, b, c, e, f, g = nums(6)
            pts = ([x + a, y + b, x + c, y + e, x + f, y + g] if rel
                   else [a, b, c, e, f, g])
            x, y = pts[4], pts[5]
            out.append(("C", pts))
        elif up == "Q":
            a, b, c, e = nums(4)
            pts = [x + a, y + b, x + c, y + e] if rel else [a, b, c, e]
            x, y = pts[2], pts[3]
            out.append(("Q", pts))
        elif up == "A":
            rx, ry, rot, laf, sweep, px, py = nums(7)
            x, y = (x + px, y + py) if rel else (px, py)
            out.append(("A", [rx, ry, rot, laf, sweep, x, y]))
        else:
            raise NotImplementedError(f"command {cmd!r} not handled")
    return out


def _points(cmds):
    """Indices into each command's params of the (x, y) pairs we may move."""
    for ci, (cmd, params) in enumerate(cmds):
        n = _POINT_COUNT[cmd]
        base = len(params) - 2 * n
        for k in range(n):
            yield ci, base + 2 * k


def _subpaths(cmds):
    """Group command indices into subpaths, each starting at an M."""
    groups: list[list[int]] = []
    for ci, (cmd, _params) in enumerate(cmds):
        if cmd == "M" or not groups:
            groups.append([])
        groups[-1].append(ci)
    return groups


def bbox(d: str) -> tuple[float, float, float, float]:
    """Rough bounds from the path's points (control points included)."""
    cmds = parse(d)
    xs, ys = [], []
    for ci, k in _points(cmds):
        xs.append(cmds[ci][1][k])
        ys.append(cmds[ci][1][k + 1])
    return min(xs), min(ys), max(xs), max(ys)


def axis_of(d: str) -> float:
    x0, _y0, x1, _y1 = bbox(d)
    return (x0 + x1) / 2


def _fmt(v: float) -> str:
    return f"{round(v, 2):g}"


def serialize(cmds) -> str:
    parts = []
    for cmd, params in cmds:
        parts.append(cmd if cmd == "Z" else cmd + " " + " ".join(_fmt(p) for p in params))
    return " ".join(parts)


def symmetrise(d: str, mode: str, centre: float = 150.0) -> str:
    """Return `d` made symmetrical by `mode`, recentred on `centre`.

    `mode` is "left", "right" or "average". "as-is" returns the path untouched
    apart from being recentred, which is what makes the comparison fair: every
    candidate sits on the same axis, so only the symmetry differs.
    """
    cmds = parse(d)
    axis = axis_of(d)

    if mode != "as-is":
        for group in _subpaths(cmds):
            pts = [(ci, k) for ci, k in _points(cmds) if ci in group]
            # A loop that closes with an arc back to its own start (both hand-
            # modelled stems do) repeats that point at each end of the list.
            # Folding it against itself would collapse it onto the axis, so the
            # duplicate sits out and is copied back afterwards.
            closed_on_itself = (
                len(pts) > 2
                and abs(cmds[pts[0][0]][1][pts[0][1]] - cmds[pts[-1][0]][1][pts[-1][1]]) < 0.01
                and abs(cmds[pts[0][0]][1][pts[0][1] + 1]
                        - cmds[pts[-1][0]][1][pts[-1][1] + 1]) < 0.01
            )
            tail = pts[-1] if closed_on_itself else None
            if tail:
                pts = pts[:-1]
            n = len(pts)
            if n % 2:                            # the lone middle point
                mid = pts[n // 2]
                cmds[mid[0]][1][mid[1]] = axis
            for i in range(n // 2):
                a, b = pts[i], pts[n - 1 - i]
                ax, ay = cmds[a[0]][1][a[1]], cmds[a[0]][1][a[1] + 1]
                bx, by = cmds[b[0]][1][b[1]], cmds[b[0]][1][b[1] + 1]
                # Decide by geometry, not by order: a path may be drawn
                # right-to-left (the schooner is), and "keep the left profile"
                # has to mean the same thing either way.
                if ax <= bx:
                    (lx, ly), (rx, ry) = (ax, ay), (bx, by)
                    left_is_a = True
                else:
                    (lx, ly), (rx, ry) = (bx, by), (ax, ay)
                    left_is_a = False

                if mode == "left":
                    nl, nr = (lx, ly), (2 * axis - lx, ly)
                elif mode == "right":
                    nl, nr = (2 * axis - rx, ry), (rx, ry)
                elif mode == "average":
                    dist = ((axis - lx) + (rx - axis)) / 2
                    ymid = (ly + ry) / 2
                    nl, nr = (axis - dist, ymid), (axis + dist, ymid)
                else:
                    raise ValueError(f"unknown mode {mode!r}")

                na, nb = (nl, nr) if left_is_a else (nr, nl)
                cmds[a[0]][1][a[1]], cmds[a[0]][1][a[1] + 1] = na
                cmds[b[0]][1][b[1]], cmds[b[0]][1][b[1] + 1] = nb

            if tail:
                cmds[tail[0]][1][tail[1]] = cmds[pts[0][0]][1][pts[0][1]]
                cmds[tail[0]][1][tail[1] + 1] = cmds[pts[0][0]][1][pts[0][1] + 1]

    shift = centre - axis
    if shift:
        for ci, k in _points(cmds):
            cmds[ci][1][k] += shift
    return serialize(cmds)


def rim(d: str, tolerance: float = 4.0) -> tuple[float, float, float]:
    """(top_y, left_x, right_x) of the mouth: the points at the path's top edge.

    Used to fit the head to whatever shape the correction produced, so the foam
    is never hand-placed against a path that has since moved.
    """
    cmds = parse(d)
    pts = [(cmds[ci][1][k], cmds[ci][1][k + 1]) for ci, k in _points(cmds)]
    top = min(y for _x, y in pts)
    at_top = [x for x, y in pts if y <= top + tolerance]
    return top, min(at_top), max(at_top)

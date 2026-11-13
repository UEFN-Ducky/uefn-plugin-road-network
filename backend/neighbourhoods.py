"""Neighbourhoods: what fills a super-block, with its own private access network.

A public street grid defines the blocks; each big block becomes one neighbourhood (seeded per block, weighted
by district) generated in the block's own frame (u along its long side, v across, the entrance from the -v side):

- ResidentialSubdivisionGenerator: 'grid' (two through streets), 'loop' (a U street with two entrances),
  'cul_de_sac' (a dead-end stem with a turnaround bulb), 'organic' (a curved loop). Lots line both sides of
  the private streets and face them; driveways come from each lot (pcg_layers._lot_ground).
- ApartmentComplexGenerator: 'garden' (rows of buildings round a courtyard inside a ring road with parking),
  'suburban' (buildings round a central parking lot), 'urban' (big blocks + a parking garage + a short drive).
- Parking: polygon -> aisles (6.5 m) -> stalls 2.6 x 5.5 m on both sides, never generated one by one.

Everything returns world coordinates: private streets as polylines (the '<City> Access' spline actor), lots
[x, y, yaw, w, d, front x, front y, role, footprint], parking slabs and stall marks [x, y, yaw, sx, sy], and
turnaround pads [x, y, radius].
"""

from __future__ import annotations

import math

from . import parcels

PRIVATE_HALF = 400.0  # private street: 8 m (two lanes, no centre line)
WALK = 150.0  # its sidewalk strip each side
BULB = 1000.0  # cul-de-sac turnaround radius
STALL_W, STALL_D, AISLE = 260.0, 550.0, 650.0
TYPES = {  # name: (group, weight per district ring)
    # (in the suburbs the curving kinds are drawn most: a ring of small grids read as squares at a city's edge)
    "grid": ("subdivision", {"housing": 1, "normal": 1}),
    "loop": ("subdivision", {"housing": 3, "normal": 1}),
    "cul_de_sac": ("subdivision", {"housing": 3}),
    "organic": ("subdivision", {"housing": 4}),
    "branch": ("subdivision", {"housing": 4, "normal": 1}),  # a winding street through the block, cul-de-sacs off it
    # a whole superblock (BIG_SIDE): one winding street through it, cul-de-sacs off both sides; never drawn by lot
    "collector": ("subdivision", {}),
    "garden": ("apartments", {"normal": 2, "housing": 1}),
    "suburban": ("apartments", {"normal": 2, "housing": 1}),
    "urban": ("apartments", {"normal": 1}),
    "parcels": ("parcels", {"normal": 3, "housing": 1}),  # plain lots on the public streets (shops, apartments)
}
MIN_SIDE = 12000.0  # a block narrower than this stays plain parcels
BIG_SIDE = 30000.0  # a block this wide both ways is a superblock: it gets the 'collector' layout


def pick(ring: str, rnd) -> str:
    names = [n for n, (_, w) in TYPES.items() if w.get(ring)]
    return rnd.choices(names, [TYPES[n][1][ring] for n in names])[0] if names else "parcels"


class _Frame:
    """The block's own frame: u along its long side, v across, entrance on the -v side."""

    def __init__(self, poly):
        self.cx, self.cy, self.ux, self.uy, self.L, self.W = parcels._obb(poly)

    def w(self, u, v):
        return (self.cx + self.ux * u - self.uy * v, self.cy + self.uy * u + self.ux * v)

    def local(self, x, y):  # a world point in the block's frame
        dx, dy = x - self.cx, y - self.cy
        return dx * self.ux + dy * self.uy, -dx * self.uy + dy * self.ux

    def side(self, x, y):  # which side of the block a world point on its edge is: 'u-', 'u+', 'v-' or 'v+'
        u, v = self.local(x, y)
        if abs(v) * self.L >= abs(u) * self.W:
            return "v+" if v > 0 else "v-"
        return "u+" if u > 0 else "u-"

    def yaw(self, du, dv):  # a local direction as a world yaw in degrees
        return math.degrees(math.atan2(self.uy * du + self.ux * dv, self.ux * du - self.uy * dv))


def _inside(poly: list, x: float, y: float) -> bool:
    """Point in polygon (even-odd ray cast)."""
    hit = False
    for (ax, ay), (bx, by) in zip(poly, poly[1:] + poly[:1]):
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            hit = not hit
    return hit


def _within(poly: list, x: float, y: float, grow: float) -> bool:
    """Inside the polygon, or no further than `grow` outside it."""
    return _inside(poly, x, y) or any(_dist_seg((x, y), a, b) <= grow for a, b in zip(poly, poly[1:] + poly[:1]))


def _round(street: list, r: float = 2000.0, n: int = 5) -> list:
    """The polyline with every corner swept as a curve of about radius r (a quadratic through the corner's two
    tangent points): subdivision streets bend, they do not turn square."""
    out = [street[0]]
    for a, b, c in zip(street, street[1:], street[2:]):
        la, lc = math.dist(a, b), math.dist(b, c)
        d = min(r, la * 0.45, lc * 0.45)
        p = (b[0] + (a[0] - b[0]) * d / la, b[1] + (a[1] - b[1]) * d / la)
        q = (b[0] + (c[0] - b[0]) * d / lc, b[1] + (c[1] - b[1]) * d / lc)
        for k in range(n + 1):
            t = k / n
            out.append(((1 - t) ** 2 * p[0] + 2 * (1 - t) * t * b[0] + t * t * q[0],
                        (1 - t) ** 2 * p[1] + 2 * (1 - t) * t * b[1] + t * t * q[1]))
    return out + [street[-1]]


def _lots_along(street, f: _Frame, lot_w, lot_d, streets, taken, role="house", rnd=None, yard=None, block=None):
    """Lots on both sides of a local polyline street, walked along its whole length so they follow its bends and
    face it wherever it turns, each its own width (0.85-1.15 x lot_w: no row of identical plots), inside the block
    (`block`: its outline in the frame; its box alone let lots of a slanted block reach onto the street) and off
    every street. yard: {trees, fences, pools} gets each lot's back fence and one side fence
    (u, v, du, dv, length), a pool (u, v, du, dv) or a tree (u, v) in the back yard, a front-corner tree for some."""
    out = []
    lim_u, lim_v = f.L / 2 - 100, f.W / 2 - 100
    cum = [0.0]
    for p, q in zip(street, street[1:]):
        cum.append(cum[-1] + math.dist(p, q))
    back = PRIVATE_HALF + WALK + lot_d / 2
    t, k = 0.0, 0
    while True:
        w = lot_w * (rnd.uniform(0.85, 1.15) if rnd else 1.0)
        mid = t + w / 2
        t += w
        if t > cum[-1]:
            break
        while cum[k + 1] < mid:
            k += 1
        (pu, pv), (qu, qv) = street[k], street[k + 1]
        L = cum[k + 1] - cum[k] or 1.0
        du, dv = (qu - pu) / L, (qv - pv) / L
        su, sv = pu + du * (mid - cum[k]), pv + dv * (mid - cum[k])
        for side in (1, -1):
            nu, nv = -dv * side, du * side  # away from the street
            cu, cv = su + nu * back, sv + nv * back
            corners = [(cu + du * a * w / 2 + nu * b * lot_d / 2, cv + dv * a * w / 2 + nv * b * lot_d / 2)
                       for a in (1, -1) for b in (1, -1)]
            if any(abs(x) > lim_u or abs(y) > lim_v or (block and not _inside(block, x, y)) for x, y in corners):
                continue  # past the block's edge (the public street's sidewalk)
            if any(_dist_seg((cu, cv), a, b) < (back * 0.85 if s is street else PRIVATE_HALF + lot_d * 0.45)
                   for s in streets for a, b in zip(s, s[1:])):
                continue  # on another private street, or squeezed inside this one's bend
            if any(math.hypot(cu - x, cv - y) < min(lot_w, lot_d) * 0.92 for x, y in taken):
                continue
            taken.append((cu, cv))
            fu, fv = su + nu * (PRIVATE_HALF + WALK), sv + nv * (PRIVATE_HALF + WALK)  # front lot line
            (wx, wy), (frx, fry) = f.w(cu, cv), f.w(fu, fv)
            out.append([round(wx), round(wy), round(f.yaw(-nu, -nv), 1), round(w), round(lot_d),
                        round(frx), round(fry), role, False])
            if yard is not None and rnd:
                if rnd.random() < 0.55:  # not every yard is fenced: about half have a back fence, half of those a side one
                    yard["fences"].append((cu + nu * lot_d / 2, cv + nv * lot_d / 2, du, dv, w))  # the back lot line
                    if rnd.random() < 0.5:
                        yard["fences"].append((cu + du * w / 2 + nu * lot_d * 0.2, cv + dv * w / 2 + nv * lot_d * 0.2, nu, nv, lot_d * 0.6))
                roll = rnd.random()
                if roll < 0.12:  # a pool behind the house
                    yard["pools"].append((cu + nu * lot_d * 0.3, cv + nv * lot_d * 0.3, du, dv))
                elif roll < 0.85:  # or a tree
                    o = rnd.uniform(-0.3, 0.3) * w
                    yard["trees"].append((cu + nu * lot_d * 0.36 + du * o, cv + nv * lot_d * 0.36 + dv * o))
                if rnd.random() < 0.35:  # the front corner, by the lot line
                    yard["trees"].append((fu + nu * 250 + du * w * 0.4, fv + nv * 250 + dv * w * 0.4))
    return out


def _dist_seg(p, a, b):
    ax, ay = a
    bx, by = b
    L2 = (bx - ax) ** 2 + (by - ay) ** 2 or 1.0
    t = max(0.0, min(1.0, ((p[0] - ax) * (bx - ax) + (p[1] - ay) * (by - ay)) / L2))
    return math.hypot(p[0] - ax - t * (bx - ax), p[1] - ay - t * (by - ay))


def _bezier(p0, p1, p2, p3, n=12):
    out = []
    for k in range(n + 1):
        t = k / n
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out


def parking(f: _Frame, u0, u1, v0, v1, face: int = 1, cross=()):
    """A surface lot: slab [x, y, yaw, sx, sy] (in metres) and its stall marks [x, y, yaw, 1, 1].
    Aisles run along u; a 17.5 m module = stalls 5.5 + aisle 6.5 + stalls 5.5.
    cross: where a drive comes in across the rows (u along the slab): a cross aisle there, the slab's whole
    depth, with no stall on it. A drive that arrived at a row of stalls had no way into the car park."""
    slab = [*map(round, f.w((u0 + u1) / 2, (v0 + v1) / 2)), round(f.yaw(1, 0), 1),
            round((u1 - u0) / 100, 2), round((v1 - v0) / 100, 2)]
    stalls = []
    module = 2 * STALL_D + AISLE
    free = lambda u: not any(abs(u - c) < (AISLE + STALL_W) / 2 for c in cross)
    if v1 - v0 < module:  # a strip along a road: one row of stalls, nosing in from the road side
        for k in range(int((u1 - u0) // STALL_W)):
            if free(u0 + (k + 0.5) * STALL_W):
                x, y = f.w(u0 + (k + 0.5) * STALL_W, (v0 + v1) / 2)
                stalls.append([round(x), round(y), round(f.yaw(0, face), 1), 1, 1])
        return slab, stalls
    v = v0
    while v + module <= v1 + 1:
        for row_v, face in ((v + STALL_D / 2, 1), (v + STALL_D + AISLE + STALL_D / 2, -1)):
            for k in range(int((u1 - u0) // STALL_W)):
                if free(u0 + (k + 0.5) * STALL_W):
                    x, y = f.w(u0 + (k + 0.5) * STALL_W, row_v)
                    stalls.append([round(x), round(y), round(f.yaw(0, face), 1), 1, 1])
        v += module
    return slab, stalls


def aisles(v0, v1) -> list:
    """The centre line (v) of each aisle of a car park between v0 and v1: where a drive from its end comes in."""
    module = 2 * STALL_D + AISLE
    return [v0 + STALL_D + AISLE / 2 + k * module for k in range(int((v1 - v0 + 1) // module))]


def _gates(rnd, sides: list, spans: dict, apart: float = 4000.0) -> list:
    """Where a complex's drives meet the streets round it: two or three (side, how far along that side from its
    middle), on different sides where the block has them, each off-centre (a fifth to three fifths of the way to
    a corner) and never within `apart` of another. sides: the block's sides that have a street, the main one
    first; spans: half the usable length of each."""
    out = []
    for k in range(rnd.choice((2, 2, 3))):
        s = sides[k % len(sides)]
        off = rnd.uniform(0.2, 0.6) * spans[s] * rnd.choice((-1, 1))
        for o in (off, -off):
            if not any(s == s2 and abs(o - o2) < apart for s2, o2 in out):
                out.append((s, o))
                break
    return out


def generate(poly: list, kind: str, rnd, lot_w=1600.0, lot_d=3000.0, joins: list | None = None,
             closed: list | None = None) -> dict:
    """One neighbourhood in a block (its inset polygon, world cm). Everything it lays out stays inside that
    polygon: a block is rarely the exact rectangle its frame is, and one with a dead-end street reaching into
    it, or a corner cut off, had houses and private streets on the public street.
    closed: the stretches of the block's edge with NO street ([(point, point)], world cm: the next block lies
    right there). The neighbourhood is turned so that it opens onto a side that has one, and none of its
    streets crosses a closed side. joins (a 'collector' superblock): the middle of each closed side, where its
    streets run on to and meet the streets of the subdivision beside it."""
    f = _Frame(poly)
    shut = lambda: {f.side(*((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)) for p, q in closed or []}
    if closed and kind != "collector":
        for _ in range(3):  # its entrance (the -v side) on a side with a street: turned round, or a quarter turn
            if "v-" not in shut():
                break
            if "v+" not in shut():
                f.ux, f.uy = -f.ux, -f.uy
                break
            f.ux, f.uy, f.L, f.W = -f.uy, f.ux, f.W, f.L
    L, W = f.L, f.W
    out = {"kind": kind, "streets": [], "lots": [], "parking": [], "stalls": [], "pads": [], "trees": [],
           "fences": [], "pools": []}
    edge = -W / 2 - 900  # the private street runs on through the sidewalk to the public street's kerb
    streets = []
    yard: dict = {"trees": [], "fences": [], "pools": []}
    trees = yard["trees"]
    if kind == "collector":
        # One winding street right through the superblock, entering and leaving square at the middle of two
        # opposite sides, always along the same world axis: the next superblock's street starts across the road
        # where this one ends, so it runs on through block after block. Cul-de-sacs branch off both sides.
        if abs(f.ux) < abs(f.uy):
            f.ux, f.uy, f.L, f.W = -f.uy, f.ux, f.W, f.L
        if closed and {"u-", "u+"} <= shut() and not {"v-", "v+"} <= shut():
            f.ux, f.uy, f.L, f.W = -f.uy, f.ux, f.W, f.L  # (no street at either end that way: it runs the other way)
        L, W = f.L, f.W
        # ... and where no road was built between this block and the subdivision beside it (joins), the street
        # does not stop at the edge: it runs on to the middle of that side, where the neighbour's begins. Across
        # the other two sides the nearest cul-de-sac runs through instead of ending in a bulb.
        local = [f.local(x, y) for x, y in joins or []]
        lo = next((p for p in local if p[0] < -L / 2 + 2500 and abs(p[1]) < W * 0.3), None)
        hi = next((p for p in local if p[0] > L / 2 - 2500 and abs(p[1]) < W * 0.3), None)
        across = [p for p in local if abs(p[1]) > W / 2 - 2500 and abs(p[0]) < L / 2 - 2500]
        ease = lambda t: 2 * t ** 3 - 3 * t * t + 1  # 1 at the low end, 0 at the high one, level at both
        aim = lambda u: (lo[1] if lo else 0.0) * ease(u / L + 0.5) + (hi[1] if hi else 0.0) * (1 - ease(u / L + 0.5))
        wind = lambda u: W * 0.2 * math.sin(2 * math.pi * (u / L + 0.5)) * math.sin(math.pi * (u / L + 0.5)) + aim(u)
        streets = [[lo or (-L / 2 - 900, 0.0)] + [(u, wind(u)) for u in (L * (k / 24 - 0.5) for k in range(25))] + [hi or (L / 2 + 900, 0.0)]]
        pitch = 2 * lot_d + 2 * (PRIVATE_HALF + WALK) + 500  # two back-to-back lot rows between neighbouring stubs
        stubs = []
        for side in (1, -1):
            u = -L / 2 + pitch * (0.6 if side > 0 else 1.1)
            end = side * (W / 2 - BULB - lot_d * 0.5)
            while u < L / 2 - pitch * 0.6:
                v0 = wind(u)
                lean = rnd.uniform(-0.2, 0.2) * abs(end - v0)
                if abs(end - v0) > 2 * lot_w:
                    stubs.append([side, u, v0, end, lean, None])
                u += pitch
        for ju, jv in across:  # the stub nearest each open side's middle runs through to it
            side = 1 if jv > 0 else -1
            mine = [s for s in stubs if s[0] == side and s[5] is None]
            if mine:
                min(mine, key=lambda s: abs(s[1] - ju))[5] = (ju, jv)
            else:
                stubs.append([side, ju, wind(ju), jv, 0.0, (ju, jv)])
        for side, u, v0, end, lean, through in stubs:
            if through:
                streets.append(_round([(u, v0), (u, (v0 + through[1]) / 2), through], n=4))
            else:
                streets.append(_round([(u, v0), (u, (v0 + end) / 2), (u + lean, end)], n=4))
                out["pads"].append([*map(round, f.w(u + lean, end)), BULB])
    if kind == "branch":  # an S through the block (two entrances) with a cul-de-sac off each bend
        a = L * rnd.uniform(0.15, 0.25)
        spine = [(-a, edge)] + _bezier((-a, -W / 2), (-a, W * 0.1), (a, -W * 0.1), (a, W / 2))[1:-1] + [(a, W / 2 + 900)]
        streets = [spine]
        for sd, v in ((-1, W * 0.2), (1, -W * 0.2)):
            end = sd * (L / 2 - BULB - lot_d * 0.5)
            start = min(spine, key=lambda p: abs(p[1] - v))
            if abs(end - start[0]) > 2 * lot_w:
                streets.append([start, (end, v)])
                out["pads"].append([*map(round, f.w(end, v)), BULB])
    # (No street of a neighbourhood enters at the exact middle of a side, and a grid is not always a cross with
    # four ways in: every block opening at the same four mid-points read as one stamp across the city.)
    if kind == "grid":  # a long street that sways gently and a cross street, which half the time stops at it
        sway = min(W * 0.12, 1500.0) * rnd.choice((1, -1))
        v0 = W * rnd.uniform(-0.08, 0.08)
        u0 = L * rnd.uniform(0.1, 0.24) * rnd.choice((1, -1))
        long_ = [(-L / 2 - 900, v0)] + _bezier((-L / 2, v0), (-L / 6, v0 + sway), (L / 6, v0 - sway), (L / 2, v0))[1:-1] + [(L / 2 + 900, v0)]
        meet = min(long_, key=lambda p: abs(p[0] - u0))[1]
        streets = [long_, [(u0, edge), (u0, W / 2 + 900 if rnd.random() < 0.5 else meet)]]
    elif kind == "loop":  # a U with swept corners
        b = W / 2 - lot_d - PRIVATE_HALF - WALK - 200
        a1, a2 = L * rnd.uniform(0.18, 0.3), L * rnd.uniform(0.18, 0.3)
        streets = [_round([(-a1, edge), (-a1, b), (a2, b), (a2, edge)])]
    elif kind == "organic":
        b = W / 2 - lot_d - PRIVATE_HALF - WALK - 200
        a1, a2 = L * rnd.uniform(0.18, 0.3), L * rnd.uniform(0.18, 0.3)
        streets = [[(-a1, edge)] + _bezier((-a1, -W / 2), (-a1 * 1.6, b * 1.5), (a2 * 1.6, b * 1.5), (a2, -W / 2))[1:-1] + [(a2, edge)]]
    elif kind == "cul_de_sac":
        end = W / 2 - BULB - lot_d * 0.6
        stem = L * rnd.uniform(0.06, 0.16) * rnd.choice((1, -1))
        streets = [[(stem, edge), (stem, end)]]
        out["pads"].append([*map(round, f.w(stem, end)), BULB])
    block = [f.local(x, y) for x, y in poly]  # (after a 'collector' has turned the frame its way)
    walls = [(f.local(*p), f.local(*q)) for p, q in closed or []]
    # (a street may run 9 m out of the block, across the sidewalk to the kerb: never where there is no street)
    reach = lambda p: _inside(block, p[0], p[1]) or (not any(_dist_seg(p, a, b) < 1300 for a, b in walls)
                                                     and _within(block, p[0], p[1], 1000.0))
    met = [f.local(x, y) for x, y in joins or []] if kind == "collector" else []
    def in_block(streets):
        """The private streets, each stopped where it would leave the block (the 9 m across the sidewalk to the
        kerb aside): the part outside goes, and a piece under 15 m with it; then the turnarounds no street
        reaches any more. A street that is inside all the way keeps its own points."""
        kept, whole = [], True
        for s in streets:
            fine = [s[0]]
            for a, b in zip(s, s[1:]):
                n = max(1, int(math.dist(a, b) // 500))
                fine += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(1, n + 1)]
            ok = lambda p: reach(p) or any(math.dist(p, m) < 5 for m in met)  # (where it meets the neighbour's)
            if all(ok(p) for p in fine):
                kept.append(s)
                continue
            whole, piece = False, []
            for p in fine + [None]:
                if p is not None and ok(p):
                    piece.append(p)
                else:
                    if len(piece) > 3:  # (over 15 m)
                        kept.append(piece)
                    piece = []
        if not whole:
            ends = [p for s in kept for p in s]
            out["pads"] = [p for p in out["pads"] if any(math.dist(f.local(p[0], p[1]), q) < 600 for q in ends)]
        return kept
    streets = in_block(streets)
    taken: list = []
    for s in streets:
        out["lots"] += _lots_along(s, f, lot_w, lot_d, streets, taken, rnd=rnd, yard=yard, block=block)
    if kind == "cul_de_sac":  # lots round the bulb, facing its centre
        stem, end = streets[0][-1]
        ring = BULB + WALK + lot_d / 2
        n = max(3, int(math.pi * ring / lot_w))
        for k in range(n):
            a = math.pi * (0.15 + 0.7 * k / max(1, n - 1)) - math.pi  # the far half, away from the stem
            a = -a
            cu, cv = stem + math.cos(a) * ring, end + math.sin(a) * ring
            ca, sa = math.cos(a), math.sin(a)  # (the whole lot inside the block: the far ones reached over its edge)
            if abs(cu) > L / 2 - 100 or abs(cv) > W / 2 - 100 or any(math.hypot(cu - x, cv - y) < lot_w * 0.9 for x, y in taken) \
                    or not all(_inside(block, cu + ca * i * lot_d / 2 - sa * j * lot_w / 2, cv + sa * i * lot_d / 2 + ca * j * lot_w / 2)
                               for i in (1, -1) for j in (1, -1)):
                continue
            taken.append((cu, cv))
            fu, fv = stem + math.cos(a) * (BULB + WALK), end + math.sin(a) * (BULB + WALK)
            (wx, wy), (frx, fry) = f.w(cu, cv), f.w(fu, fv)
            out["lots"].append([round(wx), round(wy), round(f.yaw(-math.cos(a), -math.sin(a)), 1), round(lot_w),
                                round(lot_d), round(frx), round(fry), "house", False])
    if kind in ("garden", "suburban", "urban"):
        g = 1000.0
        open_sides = [s for s in ("v-", "u+", "u-", "v+") if s not in shut()] or ["v-"]
        if kind == "garden":  # ring road, parking along its inside, two rows of buildings round a courtyard
            ru, rv = L / 2 - g, W / 2 - g
            # two or three drives in from the streets round it, on different sides and off-centre: a complex with
            # one gate in the middle of one side is a diagram, not a place
            gates = _gates(rnd, open_sides, {"v-": ru - 1500, "v+": ru - 1500, "u-": rv - 1500, "u+": rv - 1500})
            meet = [(o, (1 if s[1] == "+" else -1) * rv) if s[0] == "v" else ((1 if s[1] == "+" else -1) * ru, o) for s, o in gates]
            drives = [[(o, (1 if s[1] == "+" else -1) * (W / 2 + 900)), m] if s[0] == "v" else
                      [((1 if s[1] == "+" else -1) * (L / 2 + 900), o), m] for (s, o), m in zip(gates, meet)]
            streets = drives + [[(-ru, -rv), (ru, -rv), (ru, rv), (-ru, rv), (-ru, -rv)]]
            for side in (1, -1):
                v0 = side * (rv - PRIVATE_HALF) - (STALL_D if side > 0 else 0)
                slab, stalls = parking(f, -ru + 1500, ru - 1500, min(v0, v0 + STALL_D), max(v0, v0 + STALL_D), -side)
                out["parking"].append(slab)
                out["stalls"] += stalls
            bw, bd = 4000.0, 1400.0
            n = max(1, int((2 * ru - 3000) // (bw + 1200)))
            for row in (1, -1):
                for k in range(n):
                    cu = -((n - 1) * (bw + 1200)) / 2 + k * (bw + 1200)
                    cv = row * (W / 4 - 300)
                    (wx, wy), (frx, fry) = f.w(cu, cv), f.w(cu, cv - row * bd / 2)
                    out["lots"].append([round(wx), round(wy), round(f.yaw(0, -row), 1), round(bw), round(bd),
                                        round(frx), round(fry), "apartment", True])
            ch = W / 4 - 300 - bd / 2 - 400  # half the courtyard between the rows
            if 2 * ch >= 1500:  # a building across each end closes the courtyard: a U / L group, not two bars
                for side in (1, -1):
                    cu = side * (ru - PRIVATE_HALF - 1200 - bd / 2)
                    (wx, wy), (frx, fry) = f.w(cu, 0.0), f.w(cu - side * bd / 2, 0.0)
                    out["lots"].append([round(wx), round(wy), round(f.yaw(-side, 0), 1), round(min(bw, 2 * ch)), round(bd),
                                        round(frx), round(fry), "apartment", True])
            out["pads"].append([*map(round, f.w(*meet[0])), 900.0])  # the main entrance: a roundabout with a planted middle
            trees.append(meet[0])
            cw = ru - 3800  # the courtyard: a planted green
            if cw > 500 and ch > 300:
                trees += [(rnd.uniform(-cw, cw), rnd.uniform(-ch, ch)) for _ in range(max(4, int(cw * ch / 2.5e6)))]
        elif kind == "suburban":  # buildings round a central parking lot, drives in from the streets
            depth = max(2 * STALL_D + AISLE, int((W / 2 - 600) // (2 * STALL_D + AISLE)) * (2 * STALL_D + AISLE))
            # two or three drives: from the front, off-centre, and from the ends of the car park (never through
            # the buildings along the back)
            gates = _gates(rnd, [s for s in open_sides if s != "v+"] or ["v-"],
                           {"v-": L / 2 - 5000, "u-": depth / 2 - 700, "u+": depth / 2 - 700})
            # a drive from the front runs right through the car park as a cross aisle (no stalls on it); one from
            # an end runs straight into the nearest aisle. The drive used to stop at a row of parked cars.
            lanes, cross = aisles(-W / 4, -W / 4 + depth), []
            for s, o in gates:
                if s == "v-":
                    streets.append([(o, edge), (o, -W / 4 + depth)])
                    cross.append(o)
                else:
                    end_, lane = (1 if s[1] == "+" else -1), min(lanes, key=lambda v: abs(v - (-W / 4 + depth / 2 + o)))
                    streets.append([(end_ * (L / 2 + 900), lane), (end_ * (L / 2 - 3500), lane)])
            slab, stalls = parking(f, -L / 2 + 3500, L / 2 - 3500, -W / 4, -W / 4 + depth, cross=cross)
            out["parking"].append(slab)
            out["stalls"] += stalls
            front = next((o for s, o in gates if s == "v-"), None)
            if front is not None:
                out["pads"].append([*map(round, f.w(front, -W / 4 - 800)), 800.0])  # a drop-off loop where the front drive arrives
            bw, bd = 4500.0, 1600.0
            n = max(1, int((L - 2000) // (bw + 1500)))
            for k in range(n):
                cu = -((n - 1) * (bw + 1500)) / 2 + k * (bw + 1500)
                cv = W / 2 - bd / 2 - 800
                (wx, wy), (frx, fry) = f.w(cu, cv), f.w(cu, cv - bd / 2)
                out["lots"].append([round(wx), round(wy), round(f.yaw(0, -1), 1), round(bw), round(bd),
                                    round(frx), round(fry), "apartment", True])
        else:  # urban: two big blocks on the street, a parking garage between, a short drive
            streets = [[(0.0, edge), (0.0, -W / 2 + 2500)]]
            bw, bd = L / 2 - 2500, W - 3000
            for side in (1, -1):
                cu = side * (L / 4 + 600)
                (wx, wy), (frx, fry) = f.w(cu, 0.0), f.w(cu, -bd / 2)
                out["lots"].append([round(wx), round(wy), round(f.yaw(0, -1), 1), round(bw), round(bd),
                                    round(frx), round(fry), "apartment", True])
            (gx, gy), (gfx, gfy) = f.w(0.0, W / 2 - 2600), f.w(0.0, W / 2 - 4600)
            out["lots"].append([round(gx), round(gy), round(f.yaw(0, -1), 1), 2200, 4000, round(gfx), round(gfy),
                                "parking_garage", True])
    if kind in ("garden", "suburban", "urban"):
        streets = in_block(streets)
    out["streets"] = [[[round(c) for c in f.w(u, v)] for u, v in s] for s in streets]
    out["trees"] = [[round(c) for c in f.w(u, v)] for u, v in trees if _inside(block, u, v)]
    out["fences"] = [[*map(round, f.w(u, v)), round(f.yaw(du, dv), 1), round(ln / 100, 2)] for u, v, du, dv, ln in yard["fences"]
                     if _inside(block, u, v)]
    out["pools"] = [[*map(round, f.w(u, v)), round(f.yaw(du, dv), 1), 8.0, 4.0] for u, v, du, dv in yard["pools"]
                    if _inside(block, u, v)]
    # the complexes' own buildings, parking and roundabouts are drawn in the frame's box: whatever of them falls
    # outside the block itself is not built
    on = lambda x, y: _inside(poly, x, y)
    out["lots"] = [r for r in out["lots"] if not r[8] or on(r[0], r[1])]
    out["stalls"] = [r for r in out["stalls"] if on(r[0], r[1])]
    out["parking"] = [r for r in out["parking"] if on(r[0], r[1])]
    out["pads"] = [r for r in out["pads"] if on(r[0], r[1])]
    return out


def _demo() -> None:
    import random
    nb_dist = _dist_seg
    block = [(0, 0), (18000, 0), (18000, 16000), (0, 16000)]
    rnd = random.Random(3)
    big = generate([(0, 0), (38000, 0), (38000, 38000), (0, 38000)], "collector", rnd)
    (x0, y0), (x1, y1) = big["streets"][0][0], big["streets"][0][-1]
    assert (x0, y0, x1, y1) == (-900, 19000, 38900, 19000), "the street crosses the superblock, mid-side to mid-side"
    assert len(big["streets"]) >= 7 and len(big["pads"]) == len(big["streets"]) - 1, "cul-de-sacs off both sides"
    assert len(big["lots"]) > 120, len(big["lots"])
    print(f"  collector: {len(big['streets'])} streets, {len(big['lots'])} lots")
    # two ordinary blocks side by side: the street leaves one where it enters the next, across the public street
    one = generate(block, "collector", rnd)["streets"]
    two = generate([(x + 20000, y) for x, y in block], "collector", rnd)["streets"]
    assert len(one) >= 3 and one[0][-1][1] == two[0][0][1] == 8000 and two[0][0][0] - one[0][-1][0] == 200, (one[0][-1], two[0][0])
    # no road between two superblocks: the winding street of each runs to the middle of the side they share and
    # they meet there, to the centimetre; across the other side a cul-de-sac runs through instead of ending
    sq = [(0, 0), (38000, 0), (38000, 38000), (0, 38000)]
    left = generate(sq, "collector", random.Random(3), joins=[(38150, 19400), (20000, 38150)])
    right = generate([(x + 38300, y) for x, y in sq], "collector", random.Random(4), joins=[(38150, 19400)])
    assert left["streets"][0][-1] == [38150, 19400] == right["streets"][0][0], (left["streets"][0][-1], right["streets"][0][0])
    assert left["streets"][0][0] == [-900, 19000] and right["streets"][0][-1] == [38300 + 38900, 19000], "its other end as ever"
    through = [s for s in left["streets"][1:] if s[-1] == [20000, 38150]]
    assert len(through) == 1 and len(left["pads"]) == len(left["streets"]) - 2, "one cul-de-sac runs through, bulb and all gone"
    assert len(left["lots"]) > 110, len(left["lots"])
    # a block with a corner cut off: nothing of the neighbourhood outside it (its box reaches over the corner)
    cut_off = [(0, 0), (18000, 0), (18000, 9000), (11000, 16000), (0, 16000)]
    for kind in ("grid", "loop", "organic", "branch", "garden", "suburban", "urban", "cul_de_sac"):
        g = generate(cut_off, kind, random.Random(5))
        for r in g["lots"]:
            fx_, fy_ = math.cos(math.radians(r[2])), math.sin(math.radians(r[2]))
            for a_, b_ in ((1, 1), (1, -1), (-1, 1), (-1, -1)) if not r[8] else ((0, 0),):
                px, py = r[0] + fx_ * a_ * r[4] / 2 - fy_ * b_ * r[3] / 2, r[1] + fy_ * a_ * r[4] / 2 + fx_ * b_ * r[3] / 2
                assert _within(cut_off, px, py, 5.0), (kind, "a lot outside its block", r[:5])
        assert all(_within(cut_off, p[0], p[1], 1005.0) for s in g["streets"] for p in s), (kind, "a street outside its block")
        assert all(_inside(cut_off, r[0], r[1]) for key in ("stalls", "parking", "trees", "pools") for r in g[key]), kind
    # a block with no street on one side or two: the neighbourhood turns its entrance to a side that has one,
    # and no street of its own crosses a side that has none
    box = [(0, 0), (20000, 0), (20000, 20000), (0, 20000)]
    side_of = {"south": ((0, -150), (20000, -150)), "north": ((0, 20150), (20000, 20150)),
               "west": ((-150, 0), (-150, 20000)), "east": ((20150, 0), (20150, 20000))}
    for shut in (("south",), ("north",), ("south", "north"), ("south", "west"), ("east", "north", "south")):
        walls = [side_of[s] for s in shut]
        for kind in ("grid", "loop", "cul_de_sac", "organic", "branch", "garden", "suburban", "urban", "collector"):
            g = generate(box, kind, random.Random(7), closed=walls)
            assert g["lots"] and g["streets"], (kind, shut)
            out_of = lambda p: p[0] < -1 or p[0] > 20001 or p[1] < -1 or p[1] > 20001
            leaves = [p for s in g["streets"] for p in s if out_of(p)]
            assert leaves, (kind, shut, "it reaches a street")
            for p in leaves:
                assert not any(nb_dist(p, a, b) < 1000 for a, b in walls), (kind, shut, "a street out over a side with none", p)
    for kind in ("grid", "loop", "cul_de_sac", "organic", "branch", "garden", "suburban", "urban"):
        g = generate(block, kind, rnd)
        assert g["streets"], kind
        assert g["lots"], kind
        for x, y, yaw, w, d, frx, fry, role, foot in g["lots"]:
            assert -50 <= x <= 18050 and -50 <= y <= 16050, (kind, x, y)
            if not foot:  # a house lot faces its street: its front line is +X of its centre
                f = math.radians(yaw)
                assert (frx - x) * math.cos(f) + (fry - y) * math.sin(f) > 0, (kind, x, y, yaw)
                for s in g["streets"]:
                    for a, b in zip(s, s[1:]):
                        assert _dist_seg((x, y), a, b) > PRIVATE_HALF + d * 0.4, (kind, "lot on a street")
        if kind in ("garden", "suburban"):
            assert g["stalls"] and g["parking"], kind
        if kind == "cul_de_sac":
            assert g["pads"], "turnaround bulb"
        if kind in ("grid", "loop", "organic", "cul_de_sac", "branch"):
            assert g["trees"] and len({r[3] for r in g["lots"]}) > 1, (kind, "yards have trees, lots differ in width")
            assert 0 < len(g["fences"]) < 2 * sum(1 for r in g["lots"] if not r[8]), (kind, "some yards fenced, not all")
        if kind == "branch":
            assert len(g["streets"]) == 3 and len(g["pads"]) == 2, "a spine with two cul-de-sacs"
        if kind in ("grid", "loop", "organic", "cul_de_sac", "branch"):  # no street enters at the middle of a side
            ends = [p for s in g["streets"] for p in (s[0], s[-1]) if not (0 <= p[0] <= 18000 and 0 <= p[1] <= 16000)]
            assert ends and not any(abs(p[0] - 9000) < 400 and p[1] in (-900, 16900) or abs(p[1] - 8000) < 400 and p[0] in (-900, 18900)
                                    for p in ends), (kind, ends)
            assert kind != "grid" or len(ends) in (3, 4), ends
        if kind == "suburban":  # every drive reaches the car park's driving surface: an aisle, or a cross aisle with no stall on it
            x0, y0, x1, y1 = (lambda s: (s[0] - s[3] * 50, s[1] - s[4] * 50, s[0] + s[3] * 50, s[1] + s[4] * 50))(g["parking"][0])
            for s in g["streets"]:
                (ax, ay), (bx, by) = s[0], s[-1]
                assert x0 - 5 <= bx <= x1 + 5 and y0 - 5 <= by <= y1 + 5, ("a drive ends at the car park", s)
                for st in g["stalls"]:  # no stall within a car's width of the drive's line, or of where it comes in
                    if ax == bx:
                        assert abs(st[0] - ax) > 300, ("a stall on the cross aisle", st, s)
                    else:
                        assert abs(st[1] - by) > 300, ("a drive that ends at a row of stalls", st, s)
        if kind == "garden":
            assert len(g["lots"]) >= 6 and g["pads"], "end buildings close the courtyard; a roundabout at the entrance"
        if kind in ("garden", "suburban"):  # two or three ways in, none in the middle of its side, on more than one side
            ways = [s[0] for s in g["streets"] if not (0 <= s[0][0] <= 18000 and 0 <= s[0][1] <= 16000)]
            assert 2 <= len(ways) <= 3 and len({(p[0] in (-900, 18900), p[1] in (-900, 16900)) for p in ways}) > 1, (kind, ways)
            # (a suburban complex's end drives run into an aisle of its car park, wherever that aisle lies)
            assert not any(abs(p[0] - 9000) < 500 and p[1] in (-900, 16900) or
                           kind == "garden" and abs(p[1] - 8000) < 500 and p[0] in (-900, 18900) for p in ways), (kind, ways)
        if kind in ("loop", "organic"):
            assert len({r[2] for r in g["lots"]}) > 3, (kind, "lots turn with the street")
        print(f"  {kind}: {len(g['streets'])} streets, {len(g['lots'])} lots, {len(g['stalls'])} stalls")
    # parking maths: a 60 x 35 m lot = two 17.5 m modules, 23 stalls a row, 4 rows
    f = _Frame([(0, 0), (6000, 0), (6000, 3500), (0, 3500)])
    _, stalls = parking(f, -3000, 3000, -1750, 1750)
    assert len(stalls) == 4 * 23, len(stalls)
    print("neighbourhoods ok")


if __name__ == "__main__":
    _demo()

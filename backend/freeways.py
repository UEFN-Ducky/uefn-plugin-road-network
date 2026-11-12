"""Freeways: the regional / urban layer, planned before the city's blocks.

FreewayGenerator
  HighwayNetworkPlanner   plan(): per city a north-south and an east-west urban freeway on the city's own grid
                          (mid-block, so they never sit on a street), a directional stack where they cross, and
                          an interchange on every main road they cross (diamond downtown, partial cloverleaf in
                          the normal city, diamond with frontage roads in the suburbs), at least ~800 m apart
  FreewaySplineGenerator  mainline(): a viaduct one level over the city's streets, down to grade outside it
  InterchangeGenerator    interchange(): diamond, parclo (partial cloverleaf), cloverleaf, trumpet, stack
  RampGenerator           ramp() / loop(): cubic Bezier ramps (taper off the freeway edge, curve, descend to the
                          cross road) and 270 degree loops, every one checked against its class's minimum radius
                          with the curvature k(t) = |x'y'' - y'x''| / (x'^2 + y'^2)^(3/2)
  FrontageRoadGenerator   two-lane frontage roads beside the freeway, tied to the cross road (suburbs)
  RoadNetworkValidator    validate(): crossings without vertical clearance, ramps under their minimum radius,
                          ramp ends that touch no road

Levels (lift above the ground): 0 terrain and local streets, LEVEL_CM freeway viaducts, 2 x LEVEL_CM the upper
freeway at a stack, 3-4 x LEVEL_CM the directional flyovers. LEVEL_CM comes from clearance: 5.5 m for vehicles
+ 1.5 m of deck structure + 0.5 m margin; two roads crossing need at least CLEAR_CM between them.
Everything is in a frame per interchange: a along the freeway, b along the crossing road, both in cm.
"""

from __future__ import annotations

import math

LEVEL_CM = 750.0
CLEAR_CM = 700.0  # 5.5 m clearance + 1.5 m deck: any two roads crossing in plan need this much between them
RAMP_HALF = 650.0  # a ramp: one lane + shoulders, the elevated ramp mesh's half width
MAX_GRADE = {"freeway": 0.04, "loop": 0.06, "ramp": 0.06, "direct": 0.06, "frontage": 0.08}
MIN_RADIUS = {"loop": 4500.0, "ramp": 15000.0, "direct": 12000.0, "frontage": 3000.0}  # cm, by ramp class
LOOP_R = 5500.0
DIAMOND_A = 26000.0  # a diamond ramp leaves the freeway 260 m before the cross road
STACK_A = 55000.0  # stack right-turn connectors leave 550 m out
STACK_CORE = 50000.0  # stack flyovers hold their level within 500 m of the centre (they cross there)
STACK_FLY = 115000.0  # and leave the freeway 1.15 km out, so they can climb two levels at 6 %
FRONTAGE_B = 4200.0  # frontage road centre: this far past the freeway's edge


def bezier(p0, p1, p2, p3, n=12):
    out = []
    for k in range(n + 1):
        t = k / n
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out


def curvature(p0, p1, p2, p3, t):
    """k(t) of a cubic Bezier: |x'y'' - y'x''| / (x'^2 + y'^2)^(3/2) (1 / radius)."""
    d1 = [3 * (1 - t) ** 2 * (p1[i] - p0[i]) + 6 * (1 - t) * t * (p2[i] - p1[i]) + 3 * t * t * (p3[i] - p2[i]) for i in (0, 1)]
    d2 = [6 * (1 - t) * (p2[i] - 2 * p1[i] + p0[i]) + 6 * t * (p3[i] - 2 * p2[i] + p1[i]) for i in (0, 1)]
    speed = math.hypot(*d1) or 1e-9
    return abs(d1[0] * d2[1] - d1[1] * d2[0]) / speed ** 3


def min_radius(points: list) -> float:
    """Tightest radius along a polyline (circle through each three neighbours)."""
    best = float("inf")
    for a, b, c in zip(points, points[1:], points[2:]):
        ab, bc, ca = math.dist(a[:2], b[:2]), math.dist(b[:2], c[:2]), math.dist(c[:2], a[:2])
        cross = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
        if cross > 1e-6:
            best = min(best, ab * bc * ca / (2 * cross))
    return best


def _smooth(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


class Frame:
    """An interchange's own frame: a along the freeway, b along the crossing road, origin at the crossing."""

    def __init__(self, cx, cy, ax, ay):
        self.cx, self.cy, self.ax, self.ay = cx, cy, ax, ay

    def w(self, a, b):
        return (self.cx + self.ax * a - self.ay * b, self.cy + self.ay * a + self.ax * b)


class Builder:
    """Collects nodes and edges in build_actor's format, plus each ramp's class for the validator."""

    def __init__(self, tag):
        self.tag, self.nodes, self.edges, self.ramps, self.n = tag, {}, [], [], 0

    def edge(self, pts, kind, cls="ramp"):
        """pts: [(x, y, lift)] world cm. The first and last become nodes, the rest via points."""
        self.n += 1
        a, b = self.tag + (self.n, 0), self.tag + (self.n, 1)
        self.nodes[a] = [round(pts[0][0]), round(pts[0][1])] + ([round(pts[0][2])] if pts[0][2] > 0 else [])
        self.nodes[b] = [round(pts[-1][0]), round(pts[-1][1])] + ([round(pts[-1][2])] if pts[-1][2] > 0 else [])
        self.edges.append([a, b, kind, [[round(x), round(y), round(max(0.0, z)), kind] for x, y, z in pts[1:-1]]])
        self.ramps.append({"cls": cls, "kind": kind, "points": [(round(x), round(y), round(max(0.0, z))) for x, y, z in pts]})


def ramp(bld: Builder, f: Frame, p0, d0, p3, d3, z0, z1, cls="ramp", peak=0.0, kind="ER", n=14, handle=0.4, rise=0.4, core=0.0):
    """A cubic Bezier ramp from p0 (leaving along d0) to p3 (arriving along d3), frame coords. Its height runs
    smoothly from z0 to z1; `peak` lifts its middle to a flyover level (rising over 40 % of its length each
    side, so the grade stays gentle). `handle`: Bezier handle length as a share of the chord. `core` > 0: the
    peak is held everywhere within `core` of the interchange centre (where flyovers cross the freeways and
    each other) and fades out beyond it over the length the ramp's grade limit needs."""
    span = math.dist(p0, p3)
    p1 = (p0[0] + d0[0] * span * handle, p0[1] + d0[1] * span * handle)
    p2 = (p3[0] - d3[0] * span * handle, p3[1] - d3[1] * span * handle)
    pts = []
    for k, (a, b) in enumerate(bezier(p0, p1, p2, p3, n)):
        t = k / n
        z = z0 + (z1 - z0) * _smooth(t)
        if peak > 0 and core > 0:
            fade = 1.6 * (peak - z) / MAX_GRADE[cls]  # smoothstep's steepest slope is 1.5x its mean
            z = z + (peak - z) * _smooth((core + fade - math.hypot(a, b)) / fade)
        elif peak > 0:
            z = z + (peak - z) * _smooth(min(t, 1 - t) / rise)
        x, y = f.w(a, b)
        pts.append((x, y, z))
    bld.edge(pts, kind, cls)
    return (p0, p1, p2, p3)


def loop(bld: Builder, f: Frame, i, j, hA, hB, z0, z1, R=LOOP_R, n=16, merge=False):
    """A loop in quadrant (i, j), tangent to the freeway's edge where it leaves it. merge (two freeways, a full
    cloverleaf): 270 degrees round the far side, tangent to the cross road's edge where it merges. Else (a
    partial cloverleaf on a city street): half a circle round the far side, then straight back to the cross
    road, meeting it square at a junction like a real parclo loop (merging alongside a street left its end
    piece sticking out into the field, Nov 12 2026). Height z0 -> z1."""
    ca, cb = i * (R + hB + RAMP_HALF), j * (R + hA + RAMP_HALF)
    a0 = math.atan2(-j, 0.0)
    if merge:
        sweep = (math.atan2(0.0, -i) - a0) % (2 * math.pi)
        if sweep < math.pi:
            sweep -= 2 * math.pi  # the long way round, away from the crossing
        arc = [(ca + R * math.cos(a0 + sweep * k / n), cb + R * math.sin(a0 + sweep * k / n)) for k in range(n + 1)]
    else:
        sweep = math.pi if i * j > 0 else -math.pi  # round the far side (+i), away from the crossing
        arc = [(ca + R * math.cos(a0 + sweep * k / n), cb + R * math.sin(a0 + sweep * k / n)) for k in range(n + 1)]
        ex, ey = arc[-1]
        arc += [(ex + (i * (hB + 50) - ex) * k / 4, ey) for k in range(1, 5)]  # straight in to the street's edge
    cum = [0.0]
    for p, q in zip(arc, arc[1:]):
        cum.append(cum[-1] + math.dist(p, q))
    pts = [(*f.w(a, b), z0 + (z1 - z0) * _smooth(s / cum[-1])) for (a, b), s in zip(arc, cum)]
    bld.edge(pts, "ER", "loop")


def interchange(kind: str, f: Frame, hA: float, hB: float, liftA, liftB, tag: tuple, frontage_b=None,
                frontage_a=None) -> Builder:
    """Ramps of one interchange. liftA(a) / liftB(b): each road's height above the ground along it.
    diamond: 4 ramps, one per quadrant, between the freeway and the cross road
    diamond_frontage: the same ramps meeting frontage roads that run on to the cross road
    parclo: partial cloverleaf, loops in two opposite quadrants, diamond ramps in the other two
    cloverleaf: 4 loops + 4 outer connectors
    trumpet: a loop and a semi-direct ramp on one side (a freeway ending at a road)
    stack: freeway-to-freeway, 4 right-turn connectors at the freeways' level and 4 left-turn flyovers on
    two levels above them
    frontage_b: in a city, the parallel street's centre this far from the freeway (half a block): diamond
    ramps stay between the freeway and it, and frontage roads run on it out to frontage_a."""
    bld = Builder(tag)
    quads = [(i, j) for i in (1, -1) for j in (1, -1)]

    # a tight urban diamond keeps its ramps in the strip between the freeway and the next parallel street
    end_b = max(hA + RAMP_HALF + 300, frontage_b - 1400 - RAMP_HALF - 200) if frontage_b else hA + 6000
    fb_at = frontage_b or hA + FRONTAGE_B
    fa = frontage_a or DIAMOND_A + 4000

    def diamond_ramp(i, j):
        p0, p3 = (i * DIAMOND_A, j * (hA + RAMP_HALF)), (i * (hB + 50), j * end_b)
        ramp(bld, f, p0, (-i, 0.0), p3, (-i, 0.0), liftA(p0[0]), liftB(p3[1]))

    if kind in ("diamond", "diamond_frontage"):
        for i, j in quads:
            if kind == "diamond":
                diamond_ramp(i, j)
            else:  # the ramp meets the frontage road's inner edge; the frontage runs on to the cross road
                fb = j * fb_at
                p0, p3 = (i * DIAMOND_A, j * (hA + RAMP_HALF)), (i * DIAMOND_A * 0.2, fb - j * (525 + RAMP_HALF))
                ramp(bld, f, p0, (-i, 0.0), p3, (-i, 0.0), liftA(p0[0]), 0.0)
                pts = [(*f.w(i * (hB + 50), fb), 0.0), (*f.w(i * fa, fb), 0.0)]
                bld.edge(pts, "FR", "frontage")
    elif kind == "parclo":
        for i, j in quads:
            if i * j > 0:
                loop(bld, f, i, j, hA, hB, liftA(i * (LOOP_R + hB)), liftB(j * (LOOP_R + hA)))
            else:
                diamond_ramp(i, j)
    elif kind == "cloverleaf":
        for i, j in quads:
            loop(bld, f, i, j, hA, hB, liftA(i * (LOOP_R + hB)), liftB(j * (LOOP_R + hA)), merge=True)
            far = 38000.0  # far enough out that the right-turn connector sweeps round outside the loop
            p0, p3 = (i * (far + hB), j * (hA + RAMP_HALF)), (i * (hB + RAMP_HALF), j * (far + hA))
            ramp(bld, f, p0, (-i, 0.0), p3, (0.0, j), liftA(p0[0]), liftB(p3[1]), cls="direct")
    elif kind == "trumpet":
        loop(bld, f, 1, 1, hA, hB, liftA(LOOP_R + hB), liftB(LOOP_R + hA), merge=True)
        p0, p3 = (-(hB + 30000), hA + RAMP_HALF), (-(hB + RAMP_HALF), hA + 30000)
        ramp(bld, f, p0, (1.0, 0.0), p3, (0.0, 1.0), liftA(p0[0]), liftB(p3[1]), cls="direct")
    elif kind == "stack":
        for i, j in quads:  # right turns: the outer quarter, at the freeways' own level
            p0, p3 = (i * STACK_A, j * (hA + RAMP_HALF)), (i * (hB + RAMP_HALF), j * STACK_A)
            ramp(bld, f, p0, (-i, 0.0), p3, (0.0, j), liftA(p0[0]), liftB(p3[1]), cls="direct")
        for i, j in quads:  # left turns: over both freeways, two flyover levels so crossing flyovers clear
            p0, p3 = (i * STACK_FLY, -j * (hA + RAMP_HALF)), (-i * (hB + RAMP_HALF), j * STACK_FLY)
            level = (3 if i * j > 0 else 4) * LEVEL_CM
            ramp(bld, f, p0, (-i, 0.0), p3, (0.0, j), liftA(p0[0]), liftB(p3[1]), cls="direct", peak=level, n=28,
                 handle=0.65, core=STACK_CORE)
    return bld


# ------------------------------------------------------------------ planner
FW_MIN_R = 60000.0  # a city smaller than 600 m radius gets no urban freeway (the regional highway is enough)
STACK_MIN_R = 200000.0  # two freeways and a stack only from 2 km radius (its flyovers reach 1.15 km out each
# way, and service interchanges stay clear of them); smaller cities get one north-south freeway
IC_SPACING = 80000.0  # interchanges at least 800 m apart along a freeway (urban practice: ~1 km, a mile rural)
STACK_GAP = STACK_FLY + DIAMOND_A + 20000.0  # no service interchange inside the stack's own ramps
FW_HALF = 1650.0  # urban freeway: 3 lanes each way + shoulders (the ED deck / FW mesh half width)
MAP_EDGE = 5000.0  # nothing closer than 50 m to the map's edge
IC_ROOM = 35000.0  # an interchange's ramps and frontage roads reach ~300 m from its centre
CORRIDOR = 1500.0  # right of way past a road's edge: no building lot inside it


def _densify(pts, step=1500.0):
    out = [pts[0]]
    for p, q in zip(pts, pts[1:]):
        n = max(1, int(math.dist(p[:2], q[:2]) // step))
        out += [tuple(p[i] + (q[i] - p[i]) * k / n for i in range(3)) for k in range(1, n + 1)]
    return out


class Plan:
    """A city's freeway layer: the '<City> Freeways' actor graph (nodes / edges), every line for the validator,
    and the land it takes (a point grid with a half width each, for blocked()). Each piece belongs to a group
    (a mainline, the stack, one interchange): drop() takes whole interchanges out again."""

    def __init__(self):
        self.nodes, self.edges, self.lines, self.interchanges = {}, [], [], []
        self._grid: dict = {}
        self._builds: list = []

    def add(self, bld: Builder, ground_kind: str, group=None):
        self._builds.append((bld, ground_kind, group))
        self.nodes.update(bld.nodes)
        for (a, b, kind, via), r in zip(bld.edges, bld.ramps):
            if r["cls"] == "freeway":  # a viaduct where lifted, a plain freeway on the ground
                pts = r["points"]
                kinds = ["ED" if max(p[2], q[2]) > 0 else ground_kind for p, q in zip(pts, pts[1:])]
                via = [[x, y, z, k] for (x, y, z), k in zip(pts[1:-1], kinds[1:])]
                kind = kinds[0]
            self.edges.append([a, b, kind, via])
            half = {"freeway": FW_HALF, "frontage": 525.0}.get(r["cls"], RAMP_HALF)
            name = f"{r['cls']} {len(self.lines)}"
            self.lines.append({"name": name, "cls": r["cls"], "kind": kind, "points": r["points"], "half": half,
                               "group": group})
            dense = _densify(r["points"])
            ends = (r["points"][0], r["points"][-1])
            for p in dense:
                # the last 30 m of a ramp or frontage road joins its road: it never blocks that road
                near_end = r["cls"] != "freeway" and min(math.dist(p[:2], e[:2]) for e in ends) < 3000
                cell = (int(p[0] // 10000), int(p[1] // 10000))
                self._grid.setdefault(cell, []).append((p[0], p[1], p[2], half, near_end))

    def drop(self, groups: set) -> None:
        """Take these interchanges out (their cross road is gone: water, an airport, a shipyard)."""
        if not groups:
            return
        keep = [b for b in self._builds if b[2] not in groups]
        ics = [i for i in self.interchanges if i.get("group") not in groups]
        self.__init__()
        for bld, gk, g in keep:
            self.add(bld, gk, g)
        self.interchanges = ics

    def blocked(self, x, y, r=0.0, low_only=False):
        """Does anything of the freeway layer come within r of (x, y)? low_only: only parts too low to pass
        over a street (lift < CLEAR_CM, not a road's joining end) count, as for a street; lots count all."""
        reach = r + FW_HALF + CORRIDOR
        for gx in range(int((x - reach) // 10000), int((x + reach) // 10000) + 1):
            for gy in range(int((y - reach) // 10000), int((y + reach) // 10000) + 1):
                for px, py, pz, half, near_end in self._grid.get((gx, gy), ()):
                    if low_only and (pz >= CLEAR_CM or near_end):
                        continue
                    if math.hypot(px - x, py - y) < r + half + (0 if low_only else CORRIDOR):
                        return True
        return False

    def street_blocked(self, p, q, half):
        """A city street segment p-q that a low part of the freeway layer runs over or into."""
        L = math.dist(p, q)
        n = max(1, int(L // 1000))
        return any(self.blocked(p[0] + (q[0] - p[0]) * k / n, p[1] + (q[1] - p[1]) * k / n, half + 200, True)
                   for k in range(n + 1))


def reserved(city: dict):
    """(x, y) -> True on land the city has given to an airport or a shipyard (freeways keep off it)."""
    from . import airport, shipyard
    aps, yards = city.get("airports") or [], city.get("shipyards") or []
    if not aps and not yards:
        return None
    return lambda x, y: any(airport.inside(a, x, y, 3000) for a in aps) or any(shipyard.inside(y_, x, y, 2000)
                                                                                for y_ in yards)


def plan(city: dict, o: dict) -> Plan:
    """HighwayNetworkPlanner for one city, before its blocks: a north-south urban freeway on the city's grid,
    mid-block just past downtown (it never sits on a street), and from STACK_MIN_R an east-west one too, joined
    by a directional stack. Each freeway is a viaduct one level over the city (the streets keep running under
    it), back to grade out past the edge, and ends short of land the city reserved (an airport, a shipyard). A
    service interchange where it crosses a main road (collector or axis), at least IC_SPACING apart, clear of
    the stack and of reserved land: diamond downtown (tight land), partial cloverleaf in the normal city,
    diamond with frontage roads in the suburbs. Returns a Plan."""
    from . import citygen as cg
    out = Plan()
    R = city["radius"]
    if o.get("freeways", "yes") != "yes" or R < FW_MIN_R:
        return out
    hard = reserved(city)
    block, down, normal = o["block_m"] * 100, o["downtown"], max(o["normal"], o["downtown"])
    turn = math.radians(cg.orientation(city))
    c, s = math.cos(turn), math.sin(turn)
    W = lambda lx, ly: (city["x"] + c * lx - s * ly, city["y"] + s * lx + c * ly)
    off = (round(down * R / block) + 0.5) * block  # mid-block, at the edge of downtown
    span, fade = 1.3 * R, 1.6 * LEVEL_CM / MAX_GRADE["freeway"]
    two = R >= STACK_MIN_R
    stack_at = (off, -off)
    end = span + fade + 20000  # how far a freeway runs each way from the city: back at grade 200 m past the fade
    box = city.get("map")  # [x0, y0, x1, y1]: a freeway comes down and ends before the map's edge (Nov 11 2026)

    def room(px, py, dx, dy):
        """How far from (px, py) along (dx, dy) before the map's edge (less MAP_EDGE); unlimited with no map."""
        t = math.inf
        if box:
            for p, d, lo, hi in ((px, dx, box[0], box[2]), (py, dy, box[1], box[3])):
                if d > 1e-9:
                    t = min(t, (hi - MAP_EDGE - p) / d)
                elif d < -1e-9:
                    t = min(t, (lo + MAP_EDGE - p) / d)
        return max(0.0, t)

    def lift(along, other, sides=(span, span)):  # (distance along the freeway, from the stack along it, its spans)
        sp = sides[1] if along >= 0 else sides[0]
        z = LEVEL_CM * _smooth((sp + fade - abs(along)) / fade)
        if two and other is not None:  # the upper freeway climbs to level 2 around the stack
            z += LEVEL_CM * _smooth((60000 - abs(other)) / 35000)
        return z

    mains = [("ns", (1.0, 0.0), off, (0.0, 1.0))] + ([("ew", (0.0, 1.0), -off, (1.0, 0.0))] if two else [])
    sides_of, reach_of = {}, {}
    for name, normal_axis, at, axis in mains:  # each way: the full run, or as far as the map lets it go
        px, py = W(normal_axis[0] * at, normal_axis[1] * at)
        dx, dy = c * axis[0] - s * axis[1], s * axis[0] + c * axis[1]
        back, ahead = room(px, py, -dx, -dy), room(px, py, dx, dy)
        reach_of[name] = (back, ahead)
        sides_of[name] = tuple(span if r >= end + 2000 else max(0.0, min(span, r - fade - 2000)) for r in (back, ahead))
    for name, normal_axis, at, axis in mains:
        stack_along = stack_at[1] if name == "ns" else stack_at[0]
        upper = name == "ns"
        sides, (back, ahead) = sides_of[name], reach_of[name]
        lf = lambda t, sa=stack_along, up=upper, sd=sides: lift(t, (t - sa) if up else None, sd)
        first = int(-end // 2000) if back >= end + 2000 else -int(back // 2000)
        last = int(end // 2000) if ahead >= end + 2000 else int(ahead // 2000)
        ts = [k * 2000.0 for k in range(first, last + 1)]
        pts = []
        for t in ts:
            lx, ly = normal_axis[0] * at + axis[0] * t, normal_axis[1] * at + axis[1] * t
            pts.append((*W(lx, ly), lf(t)))
        if hard:  # stop short of an airport or a shipyard: keep the run through the city centre
            mid = min(range(len(ts)), key=lambda k: abs(ts[k]))
            lo = hi = mid
            while lo > 0 and not hard(*pts[lo - 1][:2]):
                lo -= 1
            while hi < len(pts) - 1 and not hard(*pts[hi + 1][:2]):
                hi += 1
            pts = pts[lo:hi + 1]
        if len(pts) < 2:
            continue
        bld = Builder(("fw", name))
        bld.edge(pts, "ED", "freeway")
        out.add(bld, "6L", ("main", name))
        # service interchanges on the main roads this freeway crosses
        ax_w = (c * axis[0] - s * axis[1], s * axis[0] + c * axis[1])
        done = [stack_along] if two else []
        gap = STACK_GAP if two else 0.0
        n_max = int(R // block)
        for n in sorted(range(-n_max, n_max + 1), key=abs):
            t = n * block
            if n % o["collector_every"] and n:
                continue  # only collectors and the main axis
            if any(abs(t - d) < (gap if d == stack_along and two else IC_SPACING) for d in done):
                continue
            if not -back + IC_ROOM <= t <= ahead - IC_ROOM:
                continue  # its ramps would run past the map's edge
            lx, ly = normal_axis[0] * at + axis[0] * t, normal_axis[1] * at + axis[1] * t
            cx, cy = W(lx, ly)
            if box and min(room(cx, cy, 1, 0), room(cx, cy, -1, 0), room(cx, cy, 0, 1), room(cx, cy, 0, -1)) < IC_ROOM:
                continue
            share = cg.zone_share(city, cx, cy)
            if share > 0.9:
                continue
            if hard and any(hard(cx + math.cos(a) * 30000, cy + math.sin(a) * 30000) or hard(cx, cy)
                            for a in (k * math.pi / 4 for k in range(8))):
                continue  # its ramps would be on reserved land
            kind = "diamond" if share <= down else "parclo" if share <= normal else "diamond_frontage"
            road = o["core_roads"] if share <= down or not n else "4L" if share <= normal else o["neighbourhood_roads"]
            f = Frame(cx, cy, *ax_w)
            bld = interchange(kind, f, FW_HALF, cg.HALF.get(road, 1400.0), lambda a, t=t, lf=lf: lf(t + a), lambda b: 0.0,
                              ("ic", name, n), frontage_b=block / 2, frontage_a=math.ceil((DIAMOND_A + 4000) / block) * block)
            out.add(bld, "6L", ("ic", name, n))
            out.interchanges.append({"kind": kind, "at": [round(cx), round(cy)], "freeway": name, "cross_road": road,
                                     "b_axis": [round(-ax_w[1], 4), round(ax_w[0], 4)], "group": ("ic", name, n)})
            done.append(t)
    sx, sy = W(*stack_at)
    near_edge = box and min(room(sx, sy, 1, 0), room(sx, sy, -1, 0), room(sx, sy, 0, 1), room(sx, sy, 0, -1)) < STACK_FLY + 20000
    if two and not (hard and hard(sx, sy)) and not near_edge:  # its flyovers reach 1.15 km out: all on the map
        cx, cy = W(*stack_at)
        f = Frame(cx, cy, -s, c)  # a along the north-south freeway (upper), b along the east-west one
        ns = lambda a: lift(a + stack_at[1], a, sides_of["ns"])
        ew = lambda b: lift(-b + stack_at[0], None, sides_of["ew"])  # frame b runs along -x local
        out.add(interchange("stack", f, FW_HALF, FW_HALF, ns, ew, ("stack",)), "6L", ("stack",))
        out.interchanges.append({"kind": "stack", "at": [round(cx), round(cy)], "freeway": "ns+ew", "group": ("stack",)})
    return out


# ------------------------------------------------------------------ validator
def _seg_hit(p, q, r, s):
    """Crossing point parameters (t on p-q, u on r-s) of two plan segments, or None."""
    d = (q[0] - p[0]) * (s[1] - r[1]) - (q[1] - p[1]) * (s[0] - r[0])
    if abs(d) < 1e-9:
        return None
    t = ((r[0] - p[0]) * (s[1] - r[1]) - (r[1] - p[1]) * (s[0] - r[0])) / d
    u = ((r[0] - p[0]) * (q[1] - p[1]) - (r[1] - p[1]) * (q[0] - p[0])) / d
    return (t, u) if 0 < t < 1 and 0 < u < 1 else None


def validate(lines: list, ends: list | None = None) -> dict:
    """RoadNetworkValidator. lines: [{"name", "cls", "kind", "points": [(x, y, lift)]}] - freeways, ramps,
    frontage roads and the city's streets (lift 0). Reports
    conflicts: two roads crossing in plan with less than CLEAR_CM between them (frontage roads and streets
      may cross at grade: that is an ordinary junction),
    tight: ramps tighter than their class's MIN_RADIUS,
    loose_ends: ramp ends further than 3 m from any other road's centre line + half width (`ends` = roads a
      ramp may join: [(points, half)])."""
    conflicts, tight, loose, steep = [], [], [], []
    for ln in lines:
        cap = MAX_GRADE.get(ln["cls"])
        for p, q in zip(ln["points"], ln["points"][1:]) if cap else ():
            run = math.dist(p[:2], q[:2])
            if run > 1 and abs(q[2] - p[2]) / run > cap * 1.05:
                steep.append({"name": ln["name"], "grade": round(abs(q[2] - p[2]) / run, 3), "max": cap})
                break
    grid: dict = {}
    cell = 20000.0
    segs = []
    for li, ln in enumerate(lines):
        pts = ln["points"]
        for k, (p, q) in enumerate(zip(pts, pts[1:])):
            sid = len(segs)
            segs.append((li, k, p, q))
            for gx in range(int(min(p[0], q[0]) // cell), int(max(p[0], q[0]) // cell) + 1):
                for gy in range(int(min(p[1], q[1]) // cell), int(max(p[1], q[1]) // cell) + 1):
                    grid.setdefault((gx, gy), []).append(sid)
    seen = set()
    for ids in grid.values():
        for x in range(len(ids)):
            for y in range(x + 1, len(ids)):
                a, b = segs[ids[x]], segs[ids[y]]
                if a[0] == b[0] or (ids[x], ids[y]) in seen:
                    continue
                seen.add((ids[x], ids[y]))
                hit = _seg_hit(a[2], a[3], b[2], b[3])
                if not hit:
                    continue
                za = a[2][2] + (a[3][2] - a[2][2]) * hit[0]
                zb = b[2][2] + (b[3][2] - b[2][2]) * hit[1]
                la, lb = lines[a[0]], lines[b[0]]
                at_grade_ok = {la["cls"], lb["cls"]} <= {"frontage", "street"}
                if abs(za - zb) < CLEAR_CM and not at_grade_ok:
                    hx = a[2][0] + (a[3][0] - a[2][0]) * hit[0]
                    hy = a[2][1] + (a[3][1] - a[2][1]) * hit[0]
                    conflicts.append({"a": la["name"], "b": lb["name"], "at": [round(hx), round(hy)], "gap_cm": round(abs(za - zb))})
    for ln in lines:
        need = MIN_RADIUS.get(ln["cls"])
        if need and len(ln["points"]) > 2:
            r = min_radius(ln["points"])
            if r < need * 0.97:
                tight.append({"name": ln["name"], "radius_m": round(r / 100, 1), "min_m": round(need / 100, 1)})
    for ln in lines:
        if ln["cls"] not in ("ramp", "loop", "direct"):
            continue
        for end in (ln["points"][0], ln["points"][-1]):
            ok = False
            for pts, half in ends or []:
                if pts is ln["points"]:
                    continue
                for p, q in zip(pts, pts[1:]):
                    if _dist_seg(end, p, q) <= half + RAMP_HALF + 300:
                        ok = True
                        break
                if ok:
                    break
            if not ok:
                loose.append({"name": ln["name"], "at": [round(end[0]), round(end[1])]})
    return {"ok": not (conflicts or tight or loose or steep), "conflicts": conflicts[:50], "tight": tight[:50],
            "steep": steep[:50], "loose_ends": loose[:50], "checked": len(lines)}


def _dist_seg(p, a, b):
    L2 = (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 or 1.0
    t = max(0.0, min(1.0, ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / L2))
    return math.hypot(p[0] - a[0] - t * (b[0] - a[0]), p[1] - a[1] - t * (b[1] - a[1]))


def _demo() -> None:
    f = Frame(0.0, 0.0, 1.0, 0.0)
    hA, hB = 1650.0, 1400.0
    viaduct = lambda a: LEVEL_CM  # the freeway one level up over the cross road
    ground = lambda b: 0.0
    for kind in ("diamond", "diamond_frontage", "parclo", "cloverleaf", "trumpet", "stack"):
        upper = (lambda a: LEVEL_CM * (1 + _smooth((60000 - abs(a)) / 35000))) if kind == "stack" else viaduct
        lower = (lambda b: LEVEL_CM) if kind == "stack" else ground
        bld = interchange(kind, f, hA, hB, upper, lower, ("t",))
        span = 120000
        main = [(a, 0.0, upper(a)) for a in range(-span, span + 1, 2000)]
        cross = [(0.0, b, lower(b)) for b in range(-span, span + 1, 2000)]
        lines = [{"name": "A", "cls": "freeway", "kind": "FW", "points": main},
                 {"name": "B", "cls": "freeway" if kind == "stack" else "street", "kind": "4L", "points": [(p[1], p[0], p[2]) for p in [(b, 0.0, z) for _, b, z in cross]]}]
        lines[1]["points"] = cross
        for k, r in enumerate(bld.ramps):
            lines.append({"name": f"{kind} {r['cls']} {k}", "cls": r["cls"], "kind": r["kind"], "points": r["points"]})
        ends = [(main, hA), (cross, hB)] + [(r["points"], 525.0) for r in bld.ramps if r["cls"] == "frontage"]
        rep = validate(lines, ends)
        assert rep["ok"], (kind, rep)
        print(f"  {kind}: {len(bld.ramps)} pieces, validator clean")
    # the validator catches a same-level crossing and a ramp too tight for its class
    bad = validate([{"name": "x", "cls": "freeway", "kind": "FW", "points": [(-1000, 0, 0), (1000, 0, 0)]},
                    {"name": "y", "cls": "ramp", "kind": "ER", "points": [(0, -1000, 0), (0, 1000, 0)]}])
    assert bad["conflicts"] and bad["loose_ends"], bad
    tight = validate([{"name": "t", "cls": "ramp", "kind": "ER", "points": [(0, 0, 0), (1000, 0, 0), (1000, 1000, 0)]}])
    assert tight["tight"], tight
    # curvature of a Bezier quarter-ish curve equals 1/radius of the circle it approximates (within 5 %)
    k = curvature((0, 0), (5523, 0), (10000, 4477), (10000, 10000), 0.5)
    assert abs(1 / k - 10000) < 500, 1 / k
    print("freeways ok")


if __name__ == "__main__":
    _demo()

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
    "grid": ("subdivision", {"housing": 2, "normal": 1}),
    "loop": ("subdivision", {"housing": 2, "normal": 1}),
    "cul_de_sac": ("subdivision", {"housing": 3}),
    "organic": ("subdivision", {"housing": 2}),
    "garden": ("apartments", {"normal": 2, "housing": 1}),
    "suburban": ("apartments", {"normal": 2, "housing": 1}),
    "urban": ("apartments", {"normal": 1}),
    "parcels": ("parcels", {"normal": 3, "housing": 1}),  # plain lots on the public streets (shops, apartments)
}
MIN_SIDE = 12000.0  # a block narrower than this stays plain parcels


def pick(ring: str, rnd) -> str:
    names = [n for n, (_, w) in TYPES.items() if w.get(ring)]
    return rnd.choices(names, [TYPES[n][1][ring] for n in names])[0] if names else "parcels"


class _Frame:
    """The block's own frame: u along its long side, v across, entrance on the -v side."""

    def __init__(self, poly):
        self.cx, self.cy, self.ux, self.uy, self.L, self.W = parcels._obb(poly)

    def w(self, u, v):
        return (self.cx + self.ux * u - self.uy * v, self.cy + self.uy * u + self.ux * v)

    def yaw(self, du, dv):  # a local direction as a world yaw in degrees
        return math.degrees(math.atan2(self.uy * du + self.ux * dv, self.ux * du - self.uy * dv))


def _lots_along(street, f: _Frame, lot_w, lot_d, streets, taken, role="house"):
    """Lots on both sides of a local polyline street, facing it, inside the block, off every street."""
    out = []
    lim_u, lim_v = f.L / 2 - 100, f.W / 2 - 100
    for (pu, pv), (qu, qv) in zip(street, street[1:]):
        L = math.hypot(qu - pu, qv - pv)
        if L < lot_w:
            continue
        du, dv = (qu - pu) / L, (qv - pv) / L
        for k in range(int(L // lot_w)):
            t = (k + 0.5) * lot_w
            su, sv = pu + du * t, pv + dv * t
            for side in (1, -1):
                nu, nv = -dv * side, du * side  # away from the street
                back = PRIVATE_HALF + WALK + lot_d / 2
                cu, cv = su + nu * back, sv + nv * back
                corners = [(cu + du * a * lot_w / 2 + nu * b * lot_d / 2, cv + dv * a * lot_w / 2 + nv * b * lot_d / 2)
                           for a in (1, -1) for b in (1, -1)]
                if any(abs(x) > lim_u or abs(y) > lim_v for x, y in corners):
                    continue  # past the block's edge (the public street's sidewalk)
                if any(_dist_seg((cu, cv), a, b) < PRIVATE_HALF + lot_d * 0.45 for s in streets for a, b in zip(s, s[1:])
                       if (a, b) != ((pu, pv), (qu, qv))):
                    continue  # it would sit on another private street
                if any(math.hypot(cu - x, cv - y) < min(lot_w, lot_d) * 0.92 for x, y in taken):
                    continue
                taken.append((cu, cv))
                fu, fv = su + nu * (PRIVATE_HALF + WALK), sv + nv * (PRIVATE_HALF + WALK)  # front lot line
                (wx, wy), (frx, fry) = f.w(cu, cv), f.w(fu, fv)
                out.append([round(wx), round(wy), round(f.yaw(-nu, -nv), 1), round(lot_w), round(lot_d),
                            round(frx), round(fry), role, False])
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


def parking(f: _Frame, u0, u1, v0, v1, face: int = 1):
    """A surface lot: slab [x, y, yaw, sx, sy] (in metres) and its stall marks [x, y, yaw, 1, 1].
    Aisles run along u; a 17.5 m module = stalls 5.5 + aisle 6.5 + stalls 5.5."""
    slab = [*map(round, f.w((u0 + u1) / 2, (v0 + v1) / 2)), round(f.yaw(1, 0), 1),
            round((u1 - u0) / 100, 2), round((v1 - v0) / 100, 2)]
    stalls = []
    module = 2 * STALL_D + AISLE
    if v1 - v0 < module:  # a strip along a road: one row of stalls, nosing in from the road side
        for k in range(int((u1 - u0) // STALL_W)):
            x, y = f.w(u0 + (k + 0.5) * STALL_W, (v0 + v1) / 2)
            stalls.append([round(x), round(y), round(f.yaw(0, face), 1), 1, 1])
        return slab, stalls
    v = v0
    while v + module <= v1 + 1:
        for row_v, face in ((v + STALL_D / 2, 1), (v + STALL_D + AISLE + STALL_D / 2, -1)):
            for k in range(int((u1 - u0) // STALL_W)):
                x, y = f.w(u0 + (k + 0.5) * STALL_W, row_v)
                stalls.append([round(x), round(y), round(f.yaw(0, face), 1), 1, 1])
        v += module
    return slab, stalls


def generate(poly: list, kind: str, rnd, lot_w=1600.0, lot_d=3000.0) -> dict:
    """One neighbourhood in a block (its inset polygon, world cm)."""
    f = _Frame(poly)
    L, W = f.L, f.W
    out = {"kind": kind, "streets": [], "lots": [], "parking": [], "stalls": [], "pads": []}
    edge = -W / 2 - 900  # the private street runs on through the sidewalk to the public street's kerb
    streets = []
    if kind == "grid":
        streets = [[(-L / 2 - 900, 0.0), (L / 2 + 900, 0.0)], [(0.0, edge), (0.0, W / 2 + 900)]]
    elif kind == "loop":
        a, b = L / 4, W / 2 - lot_d - PRIVATE_HALF - WALK - 200
        streets = [[(-a, edge), (-a, b), (a, b), (a, edge)]]
    elif kind == "organic":
        a, b = L / 4, W / 2 - lot_d - PRIVATE_HALF - WALK - 200
        streets = [[(-a, edge)] + _bezier((-a, -W / 2), (-a * 1.6, b * 1.5), (a * 1.6, b * 1.5), (a, -W / 2))[1:-1] + [(a, edge)]]
    elif kind == "cul_de_sac":
        end = W / 2 - BULB - lot_d * 0.6
        streets = [[(0.0, edge), (0.0, end)]]
        out["pads"].append([*map(round, f.w(0.0, end)), BULB])
    taken: list = []
    for s in streets:
        out["lots"] += _lots_along(s, f, lot_w, lot_d, streets, taken)
    if kind == "cul_de_sac":  # lots round the bulb, facing its centre
        end = streets[0][-1][1]
        ring = BULB + WALK + lot_d / 2
        n = max(3, int(math.pi * ring / lot_w))
        for k in range(n):
            a = math.pi * (0.15 + 0.7 * k / max(1, n - 1)) - math.pi  # the far half, away from the stem
            a = -a
            cu, cv = math.cos(a) * ring, end + math.sin(a) * ring
            if abs(cu) > L / 2 - 100 or abs(cv) > W / 2 - 100 or any(math.hypot(cu - x, cv - y) < lot_w * 0.9 for x, y in taken):
                continue
            taken.append((cu, cv))
            fu, fv = math.cos(a) * (BULB + WALK), end + math.sin(a) * (BULB + WALK)
            (wx, wy), (frx, fry) = f.w(cu, cv), f.w(fu, fv)
            out["lots"].append([round(wx), round(wy), round(f.yaw(-math.cos(a), -math.sin(a)), 1), round(lot_w),
                                round(lot_d), round(frx), round(fry), "house", False])
    if kind in ("garden", "suburban", "urban"):
        g = 1000.0
        if kind == "garden":  # ring road, parking along its inside, two rows of buildings round a courtyard
            ru, rv = L / 2 - g, W / 2 - g
            streets = [[(0.0, edge), (0.0, -rv)], [(-ru, -rv), (ru, -rv), (ru, rv), (-ru, rv), (-ru, -rv)]]
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
        elif kind == "suburban":  # buildings round a central parking lot, a drive in from the street
            streets = [[(0.0, edge), (0.0, -W / 4)]]
            depth = int((W / 2 - 600) // (2 * STALL_D + AISLE)) * (2 * STALL_D + AISLE)
            slab, stalls = parking(f, -L / 2 + 3500, L / 2 - 3500, -W / 4, -W / 4 + max(2 * STALL_D + AISLE, depth))
            out["parking"].append(slab)
            out["stalls"] += stalls
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
    out["streets"] = [[[round(c) for c in f.w(u, v)] for u, v in s] for s in streets]
    return out


def _demo() -> None:
    import random
    block = [(0, 0), (18000, 0), (18000, 16000), (0, 16000)]
    rnd = random.Random(3)
    for kind in ("grid", "loop", "cul_de_sac", "organic", "garden", "suburban", "urban"):
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
        print(f"  {kind}: {len(g['streets'])} streets, {len(g['lots'])} lots, {len(g['stalls'])} stalls")
    # parking maths: a 60 x 35 m lot = two 17.5 m modules, 23 stalls a row, 4 rows
    f = _Frame([(0, 0), (6000, 0), (6000, 3500), (0, 3500)])
    _, stalls = parking(f, -3000, 3000, -1750, 1750)
    assert len(stalls) == 4 * 23, len(stalls)
    print("neighbourhoods ok")


if __name__ == "__main__":
    _demo()

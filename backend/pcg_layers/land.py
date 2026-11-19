"""Open land, paved lots, street end caps, groves, district tests and building heights."""

from __future__ import annotations

import math
import zlib

from ..citygen import outline, zone_share
from .graph import _in_wedge, rings
from .samples import _Hash

def open_land(cities: list):
    """(x, y), a point inside a city's edge -> True when nothing was planned for that land: in no block that has a
    layout of its own (a subdivision, a complex, a park), on no lot (nor within 4 m of one) and on none of the
    planned grounds and parking lots, off the airfields. What is left is the middle of a block of plain lots, the land
    behind the last row of houses, the corner two streets leave. It stood bare inside every city (a quarter of
    the owner's Valley's cities, Nov 12 2026); the forest layers now grow there too."""
    from .. import airport
    every = len(cities)
    cities = [c for c in cities if c.get("blocks")]  # (a city with no plan is built along its roads: all of it is "free")
    if not cities:
        return lambda x, y: False
    # (asked for a point that is not outside every city: with a plan for each, it is in a planned one)
    in_city = _where_test("cities", cities) if len(cities) != every else None
    cell = 5000.0
    laid: dict = {}  # cell -> the laid-out blocks that reach it
    rects: dict = {}  # cell -> what stands planned there, as turned rectangles
    fields = [a for c in cities for a in c.get("airports") or []]

    def add_rect(x, y, yaw, half_d, half_w):
        f = math.radians(yaw)
        r = math.hypot(half_d, half_w)
        for gx in range(int((x - r) // cell), int((x + r) // cell) + 1):
            for gy in range(int((y - r) // cell), int((y + r) // cell) + 1):
                rects.setdefault((gx, gy), []).append((x, y, math.cos(f), math.sin(f), half_d, half_w))
    for c in cities:
        for b in c.get("blocks") or []:
            if len(b) > 4 and len(b[4]) > 2 and b[2] != "parcels":
                xs, ys = [p[0] for p in b[4]], [p[1] for p in b[4]]
                for gx in range(int(min(xs) // cell), int(max(xs) // cell) + 1):
                    for gy in range(int(min(ys) // cell), int(max(ys) // cell) + 1):
                        laid.setdefault((gx, gy), []).append(b[4])
        for r in c.get("lots") or []:
            add_rect(r[0], r[1], r[2], r[4] / 2 + 400, r[3] / 2 + 400)
        priv = c.get("private") or {}
        for row in (priv.get("grounds") or []) + (priv.get("parking") or []):
            add_rect(row[0], row[1], row[2], row[3] * 50 + 300, row[4] * 50 + 300)

    def free(x: float, y: float) -> bool:
        if in_city and not in_city(x, y):
            return False
        key = (int(x // cell), int(y // cell))
        if any(abs((x - rx) * c + (y - ry) * s) < hd and abs(-(x - rx) * s + (y - ry) * c) < hw
               for rx, ry, c, s, hd, hw in rects.get(key, ())):
            return False
        if any(inside(poly, x, y) for poly in laid.get(key, ())):
            return False
        return not any(airport.inside(a, x, y, 3000) for a in fields)
    return free


TREE_DENSITY = {"temperate_forest": 1.0, "conifer_forest": 1.0, "rainforest": 1.0, "wetland": 0.4, "meadow": 0.3,
                "grassland": 0.3, "alpine_meadow": 0.2, "savanna": 0.2, "shrubland": 0.15, "beach": 0.1,
                "tundra": 0.05, "desert": 0.05, "rocky_slope": 0.05}  # share of tree candidates kept; others: none
CONIFER_FROM_M = 900.0  # above this only conifers grow
PALM_BIOMES = ("beach", "rainforest", "savanna")  # tropical and beach: the only places a palm grows
# metres a plant keeps from open water (sea, lake, river), checked all round it; a palm or a bush may stand closer
SHORE_M = {"conifer": 30.0, "broadleaf": 25.0, "palm": 6.0, "bush": 8.0}
DRY_M = {"conifer": 3.0, "broadleaf": 3.0, "palm": 1.0, "bush": 1.5}  # ... and stands this far over the water
ON_SAND = {"palm": 1.0, "bush": 0.25}  # who grows on the beach (dry sand, dunes), at this share; trees never do
GROVE_CM = 20000.0  # thin biomes grow their trees in groves about this size, not one here, one there


def tree_kind(layer: dict) -> str:
    """'conifer', 'palm' or 'broadleaf', from a tree layer's name and meshes."""
    text = (str(layer.get("name")) + repr(layer.get("meshes"))).lower()
    if any(w in text for w in ("conifer", "pine", "spruce", "fir")):
        return "conifer"
    return "palm" if "palm" in text else "broadleaf"


def biome_trees(terrain: dict | None):
    """(x, y, kind='tree') -> keep this plant candidate, from an engine terrain's biome map (None without one: keep
    all). kind 'conifer' / 'palm' / 'bush' / anything else (a broadleaf tree). Rejected: under or near water (its
    SHORE_M all round, DRY_M over the water), on snow or cliff, on the beach unless ON_SAND grows there; above
    CONIFER_FROM_M only conifers, palms only in PALM_BIOMES. The rest is kept at TREE_DENSITY of its biome, by a
    grove field (GROVE_CM) mixed with the spot's own draw, so a thin biome has groves and clearings, not a
    sprinkle. The same answer for a spot on every run."""
    maps = (terrain or {}).get("maps") or {}
    biome = maps.get("biome")
    if not biome:
        return None
    from ..terrain.climate import BIOMES
    from .layers import patch
    n, heights, sea = terrain["grid"], terrain["heights"], terrain.get("sea")
    (mx, my), (Mx, My) = terrain["min"], terrain["max"]
    river, lake = maps.get("river"), maps.get("lake")
    cx, cy = n / (Mx - mx), n / (My - my)

    def cell(x, y):
        i = min(n - 1, max(0, int((x - mx) * cx)))
        j = min(n - 1, max(0, int((y - my) * cy)))
        return i, j, j * n + i

    def wet(x, y, dry_cm):
        i, j, k = cell(x, y)
        return (sea is not None and heights[j][i] < sea + dry_cm) or (river and river[k] > 0) or (lake and lake[k] > 0.5)

    ring = [(math.cos(a * math.pi / 4), math.sin(a * math.pi / 4)) for a in range(8)]

    def keep(x: float, y: float, kind: str = "tree") -> bool:
        kind = kind if kind in SHORE_M else "broadleaf"
        i, j, k = cell(x, y)
        name, z = BIOMES[int(biome[k])], heights[j][i]
        if wet(x, y, DRY_M[kind] * 100):
            return False
        # ponytail: 8 samples at the exclusion distance (and half of it), not a distance map; misses a creek
        # narrower than a grid cell between two of them
        r = SHORE_M[kind] * 100
        if any(wet(x + dx * rr, y + dy * rr, 0.0) for dx, dy in ring for rr in (r, r * 0.5)):
            return False
        if name == "beach":
            dens = ON_SAND.get(kind, 0.0)
        else:
            dens = TREE_DENSITY.get(name, 0.0) * (0.6 if kind == "bush" and name in ("temperate_forest", "conifer_forest", "rainforest") else 1.0)
            if kind == "bush" and name in ("shrubland", "savanna", "meadow", "grassland", "wetland"):
                dens = min(1.0, dens * 2.5)  # scrub belongs to open country: dune scrub, shrubland, hedges
        if kind == "palm" and name not in PALM_BIOMES:
            return False
        if kind not in ("conifer", "bush") and z > CONIFER_FROM_M * 100:
            return False
        if dens >= 1.0:
            return True
        return 0.85 * patch(x + 7300.0, y - 1900.0, GROVE_CM) + 0.15 * _u01("biome_tree", round(x), round(y)) < dens
    return keep


PAVED_NORMAL = 0.3  # of the normal city's blocks of plain lots, how many are paved (downtown and industry: all)
PAVE_TILE_CM = 700.0  # a paved lot is laid in slabs about this size, each at the height of the ground under it
PAVE_DEEP_CM = 200.0  # ... and this deep: where the next slab lies lower its side shows as a low wall
PAVE_UP_CM = 6.0  # a slab's top over the highest ground under it
GREEN_ROLES = ("house", "mansion", "park")  # what stands on these keeps its garden


def paved_lots(cities: list, placed: dict, ground) -> dict:
    """The built-in layer 'Paved lots': in the city centre the ground is not grass. Every lot downtown, every lot
    of the industrial zone and the lots of some blocks of the normal city (PAVED_NORMAL of its blocks of plain
    lots, each block by its own draw) is covered edge to edge with concrete, laid in slabs PAVE_TILE_CM across.
    Each slab lies PAVE_UP_CM over the highest ground under it and is PAVE_DEEP_CM deep, so on a slope the slabs
    step down the hill like stacked boxes, each one's side the wall above the next; on level ground (under and
    round a building, where the lot was cut level) they join into one. `ground`: the finished ground, lots cut
    in: the paving levels nothing itself and never rises over a building's floor, the ground does not.
    Not a lot with a house, a mansion or a park on it, nor a spot a plan drew a building at (a complex, a
    facility: those have their own grounds). A tree on a paved lot stays: it stands in its pit.
    Rows [x, y, z, yaw, sx, sy, sz] for the 1 m apron slab, z its bottom."""
    built = _Rects()
    for layer in placed.values():
        if layer.get("kind") == "buildings":
            for p in layer.get("points") or []:
                if len(p) > 8:
                    f = math.radians(p[3])
                    built.add((p[0], p[1], math.cos(f), math.sin(f), p[7] * 50, p[8] * 50, layer.get("role")))
    downtown, industry = _where_test("downtown", cities), _where_test("industrial", cities)
    rows, lots = [], 0
    for c in cities:
        cell = 20000.0
        blocks: dict = {}
        for b in c.get("blocks") or []:
            if len(b) > 4 and len(b[4]) > 2:
                xs, ys = [p[0] for p in b[4]], [p[1] for p in b[4]]
                for gx in range(int(min(xs) // cell), int(max(xs) // cell) + 1):
                    for gy in range(int(min(ys) // cell), int(max(ys) // cell) + 1):
                        blocks.setdefault((gx, gy), []).append(b)
        for r in c.get("lots") or []:
            x, y, yaw, w, d = r[:5]
            if (len(r) > 8 and r[8] is True) or (len(r) > 7 and r[7] in GREEN_ROLES):
                continue
            b = next((b for b in blocks.get((int(x // cell), int(y // cell)), ()) if inside(b[4], x, y)), None)
            if b is not None and b[2] == "park":
                continue
            if not (industry(x, y) or (b[3] == "downtown" if b is not None else downtown(x, y))
                    or (b is not None and b[3] == "normal" and b[2] == "parcels"
                        and _u01("paved", round(b[0]), round(b[1])) < PAVED_NORMAL)):
                continue
            f = math.radians(yaw)
            fx, fy = math.cos(f), math.sin(f)
            if any(o[6] in GREEN_ROLES for o in built.near(x, y, math.hypot(w, d) / 2)
                   if _rects_hit((x, y, fx, fy, w / 2, d / 2), o, -100.0)):
                continue
            lots += 1
            # the lot in slabs: nu along its facing, nv across; each slab's top from the ground at its corners
            nu, nv = max(1, round(d / PAVE_TILE_CM)), max(1, round(w / PAVE_TILE_CM))
            du, dv = d / nu, w / nv
            at = lambda u, v: (x + fx * u - fy * v, y + fy * u + fx * v)
            node = [[ground(*at(-d / 2 + i * du, -w / 2 + j * dv)) or 0.0 for j in range(nv + 1)] for i in range(nu + 1)]
            top = [[max(node[i][j], node[i + 1][j], node[i][j + 1], node[i + 1][j + 1]) + PAVE_UP_CM for j in range(nv)]
                   for i in range(nu)]
            done_ = [[False] * nv for _ in range(nu)]
            for i in range(nu):
                for j in range(nv):
                    if done_[i][j]:
                        continue
                    same = lambda a, b_: not done_[a][b_] and abs(top[a][b_] - top[i][j]) <= 4.0
                    i1 = i
                    while i1 + 1 < nu and same(i1 + 1, j):
                        i1 += 1
                    j1 = j
                    while j1 + 1 < nv and all(same(a, j1 + 1) for a in range(i, i1 + 1)):
                        j1 += 1
                    z = max(top[a][b_] for a in range(i, i1 + 1) for b_ in range(j, j1 + 1))
                    for a in range(i, i1 + 1):
                        for b_ in range(j, j1 + 1):
                            done_[a][b_] = True
                    cx, cy = at(-d / 2 + (i + i1 + 1) / 2 * du, -w / 2 + (j + j1 + 1) / 2 * dv)
                    rows.append([round(cx), round(cy), round(z - PAVE_DEEP_CM), round(yaw, 1),
                                 round((i1 - i + 1) * du / 100, 2), round((j1 - j + 1) * dv / 100, 2), PAVE_DEEP_CM / 10.0])
    if not rows:
        return {}
    # (pad False: it lies on the ground as the lots left it and is never itself a reason to cut the ground)
    return {"Paved lots": {"kind": "site", "role": "site", "mesh": "SM_Kit_Apron", "box_m": [1, 1, 0.1],
                           "tint": "mix:fg-dim:bg:0.45", "planted": True, "pad": False, "lots": lots,
                           "count": len(rows), "shown": len(rows), "points": rows, "meshes": [],
                           "row": "x, y, z (its bottom), yaw, sx, sy, sz"}}


STREET_PIECES = ("TwoLane", "FourLane", "Boulevard", "OneLane", "PrivateStreet")  # the road pieces a street is laid with
END_CAP_CM = 350.0  # a street's end cap reaches this far past the road's own edge, all round


def dead_ends(actors: list, terrain: dict | None = None) -> list:
    """Per actor, every street end that needs a turnaround: [(end point i, the point before it)]. A street that
    runs out (a dead end at the city's edge, a stub the water or a freeway took the rest of, a cul-de-sac).
    Not where the road goes on in another actor (a street joining two cities, a highway at its gate), not at a
    bridge, a ramp or a freeway's end, not at the edge of the map. actors: every road actor [(points, segments)]."""
    at: dict = {}  # how many actors have a point at each spot (to the metre)
    for pts, _segs in actors:
        for key in {(round(p["x"] / 100), round(p["y"] / 100)) for p in pts}:
            at[key] = at.get(key, 0) + 1
    lo, hi = (terrain or {}).get("min"), (terrain or {}).get("max")
    sea = (terrain or {}).get("sea")
    out = []
    for pts, segs in actors:
        by_i = {p["i"]: p for p in pts}
        found = []
        out.append(found)
        ends: dict = {}
        for s in segs:
            for n, m in ((s["a"], s["b"]), (s["b"], s["a"])):
                ends.setdefault(n, []).append((m, s["mesh"]))
        for n, links in ends.items():
            p = by_i.get(n)
            if p is None or len(links) != 1 or p.get("mesh"):
                continue  # a street that goes on; a junction's own point
            other, mesh = by_i.get(links[0][0]), str(links[0][1] or "")
            if other is None or "Bridge" in mesh or not any(k in mesh for k in STREET_PIECES):
                continue
            if at.get((round(p["x"] / 100), round(p["y"] / 100)), 0) > 1:
                continue  # the road goes on in another actor
            if lo and hi and not (lo[0] + 6000 <= p["x"] <= hi[0] - 6000 and lo[1] + 6000 <= p["y"] <= hi[1] - 6000):
                continue
            if sea is not None and p["z"] < sea + 50:
                continue
            found.append((n, links[0][0]))
    return out


def cap_all(actors: list, terrain: dict | None = None) -> int:
    """Close every street that ends with its turnaround on the spline (splines_text.cap_ends) as soon as the roads
    are made, so the preview and every push show the cul-de-sac (Build pieces only builds its mesh; a capped end is
    no dead end any more, so it is never capped twice). actors: every road actor [(points, segments)]."""
    from ..splines_text import cap_ends
    return sum(cap_ends(pts, segs, found) for (pts, segs), found in zip(actors, dead_ends(actors, terrain)))


def cap_private(pts: list, segs: list, others: list, terrain: dict | None = None) -> int:
    """cap_all for the private streets join_private just moved into a roads actor (points marked 'join'): each
    one that ends gets its cul-de-sac, marked 'join' too so unjoin_private takes it out with its street (a re-run
    caps it once again, never twice). others: the network's other road actors [(points, segments)]."""
    from ..splines_text import cap_ends
    had_p, had_s = {p["i"] for p in pts}, {s["i"] for s in segs}
    private = {p["i"] for p in pts if p.get("join")}
    n = cap_ends(pts, segs, [(e, o) for e, o in dead_ends([(pts, segs)] + list(others), terrain)[0] if {e, o} <= private])
    for x in [p for p in pts if p["i"] not in had_p] + [s for s in segs if s["i"] not in had_s]:
        x["join"] = True
    return n


def road_ends(actors: list, terrain: dict | None = None) -> dict:
    """Where each dead end's turnaround lies ([x, y, z, yaw, sx, sy, sz], radius = sx * 1000): the area kept
    clear of PCG. The turnaround itself is a 1-arm junction piece on the road's spline (splines_text.cap_ends,
    built by Build pieces in the road's own materials), never a PCG mesh: this layer is not pushed."""
    rows = []
    for (pts, _segs), found in zip(actors, dead_ends(actors, terrain)):
        by_i = {p["i"]: p for p in pts}
        for n, o in found:
            p, other = by_i[n], by_i[o]
            r = max(300.0, float(p["w"])) + END_CAP_CM
            yaw = math.degrees(math.atan2(p["y"] - other["y"], p["x"] - other["x"]))
            rows.append([round(p["x"]), round(p["y"]), round(p["z"] + 3), round(yaw, 1), round(r / 1000, 3), round(r / 1000, 3), 1.0])
    if not rows:
        return {}
    return {"Road ends": {"kind": "site", "role": "site", "mesh": "SM_Kit_Turnaround", "box_m": [20, 20, 0.1], "on_road": True,
                          "keep_z": True, "pad": False, "count": len(rows), "shown": len(rows), "points": rows, "meshes": [],
                          "row": "x, y, z, yaw, sx, sy, sz"}}


def aprons(placed: dict) -> dict:
    """A dropped kerb where every driveway meets its street: a concrete apron from the lot line across the
    3.5 m sidewalk to the carriageway, a metre wider than the drive, lying on the sidewalk (the kerb is lowered
    there, the sidewalk runs on across it). Built from the previewed lots' driveways ([.., paths])."""
    rows = []
    for v in placed.values():
        if v.get("kind") != "buildings":
            continue
        for p in v.get("points") or []:
            for width, flat in (p[10] if len(p) > 10 else []):
                if not 3.0 <= width < 8.0 or len(flat) < 6:
                    continue
                ux, uy = flat[-3] - flat[0], flat[-2] - flat[1]
                d = math.hypot(ux, uy)
                if d > 1:
                    rows.append([round(flat[-3] + ux / d * 175), round(flat[-2] + uy / d * 175), flat[-1],
                                 round(math.degrees(math.atan2(uy, ux)), 1), 3.5, width + 1.0, 1.0])
    if not rows:
        return {}
    return {"Driveway aprons": {"kind": "site", "role": "site", "mesh": "SM_Kit_Apron", "box_m": [1, 1, 0.1], "on_road": True,
                                "tint": "mix:fg-dim:bg:0.2", "count": len(rows), "shown": len(rows), "points": rows,
                                "meshes": [], "row": "x, y, z, yaw, sx, sy, sz"}}


def interchange_groves(fast: list, ground, sea=None, radius: float = 16000.0, count: int = 90) -> dict:
    """Trees in and round every interchange: the land inside the loops and between the ramps is right of way, so
    no lot is cut there and it stood bare. fast: the freeway / highway actors [(points, segments)]; an interchange
    is where their ramp connectors (gores, flares) cluster. `count` trees in a disc of `radius` round each, the
    same every time; the caller takes out the ones on a road (clear_of). Never houses: nobody lives in a loop."""
    import random
    ends = [(p["x"], p["y"]) for pts, _segs in fast for p in pts if str(p.get("mesh") or "").startswith("SM_Road_Ramp")]
    groups: list = []
    for e in ends:  # greedy clusters, 300 m: one interchange's ramp ends
        g = next((g for g in groups if math.dist(g[0], e) < 30000), None)
        if g:
            g.append(e)
        else:
            groups.append([e])
    rows = []
    for g in groups:
        if len(g) < 3:
            continue  # a lone ramp end is no interchange
        cx, cy = sum(p[0] for p in g) / len(g), sum(p[1] for p in g) / len(g)
        rnd = random.Random(f"grove:{round(cx)}:{round(cy)}")
        for k in range(count):
            a, d = rnd.uniform(0, 2 * math.pi), radius * math.sqrt(rnd.random())
            x, y = cx + math.cos(a) * d, cy + math.sin(a) * d
            z = ground(x, y)
            if z is not None and (sea is None or z > sea + 50):
                rows.append([round(x), round(y), round(z), round(rnd.uniform(0, 360)), round(rnd.uniform(0.8, 1.3), 2)])
    if not rows:
        return {}
    return {"Interchange trees": {"kind": "scatter", "role": "tree", "count": len(rows), "shown": len(rows), "points": rows,
                                  "radius_cm": 250, "meshes": [], "build": "", "row": "x, y, z, yaw, scale"}}


def off_roads(streets: list, samples: list, margin: float = 1000.0) -> list:
    """Private streets stopped short of every freeway, ramp and highway (road_samples of those actors): each
    polyline is walked every 5 m and cut where it comes within margin of one, the pieces under 15 m dropped.
    A subdivision street laid across a highway ramp was graded like a road and filled the ramp's cutting."""
    cell = 3000.0
    grid: dict = {}
    for x, y, half, _dx, _dy in samples:
        grid.setdefault((int(x // cell), int(y // cell)), []).append((x, y, half + 400.0 + margin))
    if not grid:
        return streets
    near = lambda x, y: any(math.hypot(x - sx, y - sy) < r for i in (-1, 0, 1) for j in (-1, 0, 1)
                            for sx, sy, r in grid.get((int(x // cell) + i, int(y // cell) + j), ()))
    out = []
    for s in streets:
        piece: list = []
        pieces = [piece]
        for a, b in zip(s, s[1:]):
            n = max(1, int(math.dist(a[:2], b[:2]) // 500))
            for k in range(n + (b is s[-1])):
                p = [round(a[0] + (b[0] - a[0]) * k / n), round(a[1] + (b[1] - a[1]) * k / n)]
                if near(*p):
                    piece = []
                    pieces.append(piece)
                else:
                    piece.append(p)
        out += [s] if len(pieces) == 1 else [p for p in pieces if len(p) > 3]  # an uncut street keeps its own points
    return out


def clear_of(placed: dict, samples: list, margin: float = 200.0) -> int:
    """Nothing stands on or under a freeway, a ramp or a highway: every placed thing whose footprint reaches a
    road sample (road_samples of those actors) is taken out. Lots are cut from the city's blocks before the
    highways and their interchanges are laid, so towers stood on ramps (Nov 12 2026). Bridge parts stay.
    Returns how many were removed."""
    cell = 3000.0
    grid: dict = {}
    for x, y, half, _dx, _dy in samples:
        grid.setdefault((int(x // cell), int(y // cell)), []).append((x, y, half))
    if not grid:
        return 0

    def hit(x, y, r):
        n = int((r + 2500) // cell) + 1
        cx, cy = int(x // cell), int(y // cell)
        return any((x - sx) ** 2 + (y - sy) ** 2 < (r + half + margin) ** 2
                   for gx in range(cx - n, cx + n + 1) for gy in range(cy - n, cy + n + 1) for sx, sy, half in grid.get((gx, gy), ()))
    gone = 0
    for name, layer in placed.items():
        if layer.get("role") == "bridge" or name.startswith(("Bridge", "Pier")):
            continue
        box = layer.get("box_m")
        keep = []
        for p in layer.get("points") or []:
            if layer.get("kind") == "buildings" and len(p) > 8:
                r = 50.0 * max(p[7], p[8]) * 0.8  # width_m, depth_m
            elif box and len(p) > 5:
                r = 50.0 * max(box[0] * p[4], box[1] * p[5]) * 0.8
            else:
                r = float(layer.get("radius_cm") or 150.0) * 0.5
            if not hit(p[0], p[1], r):
                keep.append(p)
        lost = len(layer.get("points") or []) - len(keep)
        if lost:
            gone += lost
            layer["points"] = keep
            layer["shown"] = len(keep)
            layer["count"] = max(len(keep), int(layer.get("count", len(keep))) - lost)
    return gone


def inside(poly: list, x: float, y: float) -> bool:
    """Point in polygon (even-odd ray cast)."""
    hit = False
    for (ax, ay), (bx, by) in zip(poly, poly[1:] + poly[:1]):
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            hit = not hit
    return hit


OUTLINE_MAX = 1.27  # outline() is 1 +- 0.265 at most: this far past a ring is outside it in every direction
OUTLINE_MIN = 0.73  # ... and this far inside it is inside it in every direction


def _where_test(where: str, cities: list):
    """_in_where for one layer, as (x, y) -> bool: the rings worked out once, and a point farther than any
    outline reaches from a city skips that city's outline (most forest candidates are)."""
    if where == "everywhere" or not cities:
        return lambda x, y: True
    if where == "shipyards":
        from .. import shipyard
        yards = [s for c in cities for s in c.get("shipyards") or []]
        return lambda x, y: any(shipyard.zone(s, *shipyard.uv(s, x, y)) == "platform" for s in yards)
    # a block the person rezoned (city 'rezoned': [[polygon, zone]]) is that zone whatever ring it lies in
    # (the bigger city first: where two overlap, the land is the bigger one's, citygen.yield_plan)
    rows = [(c, c["x"], c["y"], *rings(c),
             [(poly, min(p[0] for p in poly), min(p[1] for p in poly), max(p[0] for p in poly), max(p[1] for p in poly), zone)
              for poly, zone in c.get("rezoned") or []])
            for c in sorted(cities, key=lambda c: (-float(c["radius"]), str(c.get("name") or "")))]

    def test(x: float, y: float) -> bool:
        for c, cx, cy, down, normal, outer, rezoned in rows:
            # (before the rings: the houses along a street that joins two cities lie outside both cities' edges)
            for poly, x0, y0, x1, y1, zone in rezoned:
                if x0 <= x <= x1 and y0 <= y <= y1 and inside(poly, x, y):
                    return where == "cities" or where == zone
        for c, cx, cy, down, normal, outer, rezoned in rows:
            dist = math.hypot(x - cx, y - cy)
            if dist >= outer * OUTLINE_MAX:
                continue
            if dist < outer * OUTLINE_MIN and where in ("outside", "cities"):
                return where == "cities"  # inside the outer ring whichever way its outline bends
            # distance against the outline in this direction: the same bent edges the plan's polygons have
            # (from the city's centre district, where that was moved)
            d = zone_share(c, x, y) * c["radius"] if c.get("core") else dist / outline(c, math.degrees(math.atan2(y - cy, x - cx)))
            if where == "outside" and d < outer:
                return False
            if d >= outer:
                continue
            wedge = d >= down and _in_wedge(c, x, y)
            if (where == "cities") or (where == "downtown" and d < down) or (where == "industrial" and wedge) or \
                    (where == "normal" and down <= d < normal and not wedge) or \
                    (where == "housing" and normal <= d < outer and not wedge):
                return True
        return where == "outside"
    return test


def _in_where(where: str, cities: list, x: float, y: float) -> bool:
    """Same areas as masks(): rings per city, the industrial wedge cut out of normal and housing."""
    return _where_test(where, cities)(x, y)


def _rects_hit(a: tuple, b: tuple, margin: float = 0.0) -> bool:
    """Do two turned rectangles (x, y, cos, sin of the way they face, half width across, half depth along) come
    within `margin` of each other? Separating axes: exact, where a circle test let long houses overlap end on."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    for ax, ay in ((a[2], a[3]), (-a[3], a[2]), (b[2], b[3]), (-b[3], b[2])):
        reach = sum(r[5] * abs(ax * r[2] + ay * r[3]) + r[4] * abs(-ax * r[3] + ay * r[2]) for r in (a, b))
        if abs(dx * ax + dy * ay) > reach + margin:
            return False
    return True


class _Rects:
    """Every building placed so far, as the rectangle it stands on: nothing is built on top of another."""

    def __init__(self, cell: float = 4000.0):
        self.cell, self.grid, self.reach = cell, {}, 0.0
        self.cover: dict = {}  # each rectangle in every cell its reach covers: hit() reads only the cells round it

    def add(self, r: tuple) -> None:
        self.grid.setdefault((int(r[0] // self.cell), int(r[1] // self.cell)), []).append(r)
        self.reach = max(self.reach, math.hypot(r[4], r[5]))
        # _rects_hit(a, b, m) leaves the centres at most (a4 + a5) + sqrt(2) (b4 + b5) + 2 m apart on either world
        # axis: b goes in every cell within 1.5 (b4 + b5) of its centre, a looks in the cells within a4 + a5 + 2 m
        c, k = self.cell, 1.5 * (r[4] + r[5])
        for i in range(int((r[0] - k) // c), int((r[0] + k) // c) + 1):
            for j in range(int((r[1] - k) // c), int((r[1] + k) // c) + 1):
                self.cover.setdefault((i, j), []).append(r)

    def near(self, x: float, y: float, reach: float):
        n = int((reach + self.reach) // self.cell) + 1
        cx, cy = int(x // self.cell), int(y // self.cell)
        return (r for gx in range(cx - n, cx + n + 1) for gy in range(cy - n, cy + n + 1) for r in self.grid.get((gx, gy), ()))

    def hit(self, r: tuple, margin: float = 100.0) -> bool:
        # (every cell the widest building's reach could come from was walked for every tree: 100 lookups a tree)
        c, k, get = self.cell, r[4] + r[5] + 2 * margin, self.cover.get
        for i in range(int((r[0] - k) // c), int((r[0] + k) // c) + 1):
            for j in range(int((r[1] - k) // c), int((r[1] + k) // c) + 1):
                for o in get((i, j), ()):
                    if _rects_hit(r, o, margin):
                        return True
        return False


def fit_fences(placed: dict, samples: list) -> int:
    """Yard fences that make sense only: one stays when a house stands on its lot (within 22 m), it runs through
    no building, crosses no road or private street and no fence kept before it. Lots overlapped inside street
    bends, and every lot's fences were drawn whether or not its house was. Returns how many went."""
    layer = placed.get("Yard fences")
    if not layer:
        return 0
    rects = _Rects()
    for v in placed.values():
        if v.get("kind") == "buildings":
            for p in v.get("points") or []:
                if len(p) > 8:
                    rects.add((p[0], p[1], math.cos(math.radians(p[3])), math.sin(math.radians(p[3])), p[7] * 50, p[8] * 50))
    roads = _Hash()
    for x, y, half, _dx, _dy in samples:
        roads.add(x, y, half, (x, y, half))

    def cross(a, b, c, d):  # do segments a-b and c-d cross away from their ends?
        den = (b[0] - a[0]) * (d[1] - c[1]) - (b[1] - a[1]) * (d[0] - c[0])
        if abs(den) < 1e-9:
            return False
        t = ((c[0] - a[0]) * (d[1] - c[1]) - (c[1] - a[1]) * (d[0] - c[0])) / den
        u = ((c[0] - a[0]) * (b[1] - a[1]) - (c[1] - a[1]) * (b[0] - a[0])) / den
        return 0.06 < t < 0.94 and 0.06 < u < 0.94
    kept, lines = [], _Rects()
    for row in layer.get("points") or []:
        x, y, yaw, h = row[0], row[1], math.radians(row[3]), row[4] * 50
        c, s = math.cos(yaw), math.sin(yaw)
        me = (x, y, c, s, 10.0, h)
        a, b = (x - c * h, y - s * h), (x + c * h, y + s * h)
        if not any(math.hypot(r[0] - x, r[1] - y) < 2200 for r in rects.near(x, y, 2200)) or rects.hit(me, 30.0):
            continue
        if any(roads.hit(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, 50.0) for t in (0.0, 0.25, 0.5, 0.75, 1.0)):
            continue
        if any(cross(a, b, (o[0] - o[2] * o[5], o[1] - o[3] * o[5]), (o[0] + o[2] * o[5], o[1] + o[3] * o[5])) for o in lines.near(x, y, h)):
            continue
        lines.add(me)
        kept.append(row)
    gone = len(layer.get("points") or []) - len(kept)
    layer["points"], layer["count"], layer["shown"] = kept, len(kept), len(kept)
    return gone


def _lot_hits_road(road_hash: "_Hash", x: float, y: float, yaw: float, half_w: float, half_d: float) -> bool:
    """Does a lot (frontage 2*half_w along its street, depth 2*half_d, facing yaw) reach onto any road?"""
    fx, fy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    for rx, ry, half in road_hash.near(x, y, max(half_w, half_d)):
        dx, dy = rx - x, ry - y
        along_face = abs(dx * fx + dy * fy)  # toward the street / the back
        along_front = abs(-dx * fy + dy * fx)  # sideways along the street
        if along_face < half_d + half and along_front < half_w + half:
            return True
    return False


def core_peak(cities: list, x: float, y: float) -> float:
    """The city's height field at (x, y): 1 at the very core, falling off like a bell (half at ~18 % of
    the radius, near 0 by 40 %), so the skyline peaks in the middle and steps down to the suburbs."""
    best = 0.0
    for c in cities:
        if math.hypot(x - c["x"], y - c["y"]) > c["radius"] * 1.3:
            continue
        best = max(best, math.exp(-(zone_share(c, x, y) / 0.22) ** 2))
    return best


def _height(layer: dict, cities: list, x: float, y: float, rnd, neighbours: "_Hash") -> float:
    """A believable skyline: tallest at the city core, falling off outward (the transect), randomised,
    and never within 18 % of the range of a building of this layer closer than 25 m (no row of equal
    heights)."""
    lo, hi = layer["height_min_m"], layer["height_max_m"]
    span = hi - lo
    if span <= 0.5:
        return lo
    core = core_peak(cities, x, y)
    # the layer's range follows the city's height field: top of the range only at the very core, the
    # bottom at its district's edge, with real scatter (a few towers pop up, a few stay short)
    h = lo + span * min(1.0, max(0.0, core * rnd.uniform(0.55, 1.2) + rnd.uniform(-0.08, 0.12)))
    for _ in range(4):  # step away from a too-similar neighbour, up or down, whichever fits
        clash = [nh for nh in neighbours.near(x, y, 2500.0) if abs(nh - h) < 0.18 * span]
        if not clash:
            break
        h = h + 0.3 * span if h + 0.3 * span <= hi else h - 0.3 * span
        h = max(lo, min(hi, h + rnd.uniform(-0.05, 0.05) * span))
    return h


TILE_CM = 50000.0  # scatter, roadside and junction pieces are placed in 500 m tiles. A tile's candidates come from a
# random of its own, a city's lots are taken in an order of their own, and every piece's size, height and shape
# come from its own spot: what stands in one place never depends on what was placed anywhere else, so changing one
# city leaves the rest of the world exactly as it was
DIRTY_CM = 10000.0  # a change is tracked on a 100 m grid (PreviewMemo)


def _u01(*key) -> float:
    """A fixed number 0..1 for a key (a layer and a spot): the same on every run, whatever else is placed."""
    return zlib.crc32(repr(key).encode()) / 4294967296.0


def _city_sig(c: dict) -> tuple:
    """Everything of a city the placement reads, as (where and how big it is, its plan part by part): a city that
    moved, turned or changed size is placed again whole; one whose plan changed in a block, only round what
    changed. Each part: {a row as text: where that row is ((x, y) or a polygon or a line)}."""
    num = lambda v: round(float(v), 2) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
    frame = zlib.crc32(repr([num(c.get(k)) for k in ("x", "y", "radius", "seed", "spin")]
                            + [c.get(k) for k in ("shape", "districts", "shipyards", "shores")]).encode())
    parts = ({repr(r): [r[:2]] for r in c.get("lots") or []},
             {repr(b): b[4] for b in c.get("blocks") or [] if len(b) > 4},
             {repr(z): z[0] for z in c.get("rezoned") or []},
             {repr(s): s for s in (c.get("private") or {}).get("streets") or []})
    return frame, parts


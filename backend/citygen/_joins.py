from __future__ import annotations

JOIN_MAX_CM = 60000.0  # cities whose edges are closer than this (600 m) are joined by streets
JOIN_ROADS = 5  # ... this many where they touch, one where they are at the limit: the closer, the more
JOIN_LONGEST_CM = 45000.0  # a joining street is never longer than the gap plus this
JOIN_GRADE = 0.08  # ... never steeper than this from one city's level to the other's
JOIN_OFF_IC_CM = 15000.0  # ... and never starts within 150 m of a freeway interchange


def joins(cities: list, graphs: list, opts: dict | None = None, wet=None, keep: list | None = None, ground=None) -> list:
    """Streets between cities that lie close together or touch: the closer two cities are, the more streets run
    from one to the other (JOIN_ROADS where they touch, one at JOIN_MAX_CM), spread across the side where they
    face each other. Each joins a street corner at the edge of one city to one of the other, straight. One is
    built only where a real street could be: never through or beside water, a keep zone, a freeway's ramps or
    an interchange (of any city), and never steeper than JOIN_GRADE from one city's level to the other's.
    Returns [(i, j, node of i, node of j, [mid x, mid y])]: each city lays its own half, from its corner to the
    middle (level_joins then makes the two halves one even street)."""
    o = options(opts)
    out = []
    edge_nodes = []
    for nodes, edges in graphs:
        used = {n for e in edges for n in e[:2]}
        edge_nodes.append([(n, nodes[n]) for n in nodes if n in used and isinstance(n, tuple) and len(n) == 2
                           and isinstance(n[0], int)])
    plans = [freeway_plan(c, o) for c in cities]
    half = HALF.get(CITY_STREET.get(o["neighbourhood_roads"], o["neighbourhood_roads"]), 875.0)

    def fits(p, q, L) -> bool:
        ux, uy = (q[0] - p[0]) / L, (q[1] - p[1]) / L
        steps = max(2, int(L // 500))
        for t in range(steps + 1):
            x, y = p[0] + (q[0] - p[0]) * t / steps, p[1] + (q[1] - p[1]) * t / steps
            # the street, its sidewalks and the houses along it: dry 30 m to either side as well
            if wet and (wet(x, y) or wet(x - uy * 3000, y + ux * 3000) or wet(x + uy * 3000, y - ux * 3000)):
                return False
            if t % 4 == 0 and in_keep(keep, x, y, 2000):
                return False
        for fw in plans:
            if not fw.lines:
                continue
            if fw.street_blocked(p[:2], q[:2], half):
                return False  # a ramp or an at-grade freeway would cut it
            if any(min(math.dist(p[:2], ic["at"]), math.dist(q[:2], ic["at"])) < JOIN_OFF_IC_CM for ic in fw.interchanges):
                return False  # a corner inside an interchange is no place for one more street
        if ground:  # the two cities may stand on different levels: a street, not a ski jump
            za, zb = ground(p[0], p[1]), ground(q[0], q[1])
            if za is not None and zb is not None and abs(za - zb) / L > JOIN_GRADE:
                return False
        return True

    for i in range(len(cities)):
        for j in range(i + 1, len(cities)):
            a, b = cities[i], cities[j]
            dx, dy = b["x"] - a["x"], b["y"] - a["y"]
            d = math.hypot(dx, dy) or 1.0
            if d > (a["radius"] + b["radius"]) * 1.27 + JOIN_MAX_CM:
                continue
            ux, uy = dx / d, dy / d
            way = math.degrees(math.atan2(dy, dx))
            gap = d - a["radius"] * outline(a, way) - b["radius"] * outline(b, way + 180.0)
            if gap > JOIN_MAX_CM:
                continue
            n = 1 + round((JOIN_ROADS - 1) * (1.0 - max(0.0, gap) / JOIN_MAX_CM))
            wide = 1.2 * min(a["radius"], b["radius"])  # the side where they face each other
            slot = wide / n
            # every corner as (how far along the line from a to b, how far to its side, node, spot)
            side = lambda p: (-(p[0] - a["x"]) * uy + (p[1] - a["y"]) * ux)
            along = lambda p: ((p[0] - a["x"]) * ux + (p[1] - a["y"]) * uy)
            mine = [(along(p), side(p), n_, p) for n_, p in edge_nodes[i]]
            theirs = [(along(p), side(p), n_, p) for n_, p in edge_nodes[j]]
            taken: set = set()
            for k in range(n):
                at = wide * ((k + 0.5) / n - 0.5)
                near_a = sorted((r for r in mine if abs(r[1] - at) <= slot / 2), key=lambda r: -r[0])[:5]
                near_b = sorted((r for r in theirs if abs(r[1] - at) <= slot / 2), key=lambda r: r[0])[:5]
                pairs = sorted(((math.dist(ra[3][:2], rb[3][:2]), ra, rb) for ra in near_a for rb in near_b
                                if ra[2] not in taken and rb[2] not in taken and rb[0] > ra[0]), key=lambda r: r[0])
                for L, ra, rb in pairs:  # the shortest pair of corners a street can really run between
                    if not 1500.0 <= L <= max(0.0, gap) + JOIN_LONGEST_CM or not fits(ra[3], rb[3], L):
                        continue
                    p, q = ra[3], rb[3]
                    taken.update((ra[2], rb[2]))
                    out.append((i, j, ra[2], rb[2], [round((p[0] + q[0]) / 2), round((p[1] + q[1]) / 2)]))
                    break
    return out


def meet_city(road: dict, city: dict | None, ends: list) -> int:
    """A road from outside (an airport's access and cargo roads) ends on its city's junction at that junction's
    height. It was graded on the raw ground, the city on its own cut, so Northgate's airport road stopped 24 m above
    the junction it was laid to (Nov 17 2026). Returns how many ends were brought down (or up) to the city."""
    done = 0
    for x, y in ends:
        cp = next((p for p in (city or {}).get("points", ()) if abs(p["x"] - x) < 2 and abs(p["y"] - y) < 2), None)
        rp = next((p for p in road.get("points", ()) if abs(p["x"] - x) < 2 and abs(p["y"] - y) < 2), None)
        if cp and rp and rp["z"] != cp["z"]:
            rp["z"] = cp["z"]
            done += 1
    return done


def level_joins(actors: dict, cities: list, rows: list) -> int:
    """The two halves of a joining street meet at ONE height. Each city's roads are graded on their own, and two
    cities on different levels (each on its own flattened ground, say) left a step in the middle of every joining
    street, up to 24 m (Nov 12 2026). The street now runs at one even grade from one city's corner to the other's:
    both halves end at the height that grade gives the middle. Returns how many streets were levelled."""
    done = 0
    for i, j, _ni, _nj, mid in rows:
        halves = []
        for c in (cities[i], cities[j]):
            a = actors.get(f"{c['name']} Roads")
            end = next((p for p in (a or {}).get("points", ()) if abs(p["x"] - mid[0]) < 2 and abs(p["y"] - mid[1]) < 2), None)
            segs = [s for s in a["segments"] if end["i"] in (s["a"], s["b"])] if end else []
            if len(segs) != 1:
                break  # (not laid after all: the water took it)
            by = {p["i"]: p for p in a["points"]}
            far = by[segs[0]["b"] if segs[0]["a"] == end["i"] else segs[0]["a"]]
            halves.append((by, end, far, segs[0], math.hypot(far["x"] - end["x"], far["y"] - end["y"])))
        if len(halves) != 2:
            continue
        (by_a, end_a, far_a, seg_a, len_a), (by_b, end_b, far_b, seg_b, len_b) = halves
        end_a["z"] = end_b["z"] = far_a["z"] + (far_b["z"] - far_a["z"]) * len_a / ((len_a + len_b) or 1.0)
        st.retangent(by_a, seg_a)
        st.retangent(by_b, seg_b)
        done += 1
    return done


def share_freeways(cities: list, opts: dict | None = None) -> None:
    """Each city learns where its neighbours' freeways, ramps and frontage roads run low over its land (sets
    city['fw_near']: [[x, y, half width]] every few metres along them, within the city's reach): its own streets
    give way to those as they do to its own freeway, so no street is laid through a neighbour's ramp."""
    for c in cities:
        c.pop("fw_near", None)
    for c in cities:
        low = freeway_plan(c, opts).low_points()
        for u in cities:
            if u is c or not low:
                continue
            reach = u["radius"] * 1.3 + 30000
            rows = [[x, y, h] for x, y, h in low if math.hypot(x - u["x"], y - u["y"]) < reach]
            if rows:
                u.setdefault("fw_near", []).extend(rows)


def _near_cuts(city: dict):
    """(p, q, half) -> True when a neighbour's low freeway part (city['fw_near']) runs over or into the street."""
    cells: dict = {}
    for x, y, h in city.get("fw_near") or ():
        cells.setdefault((int(x // 5000), int(y // 5000)), []).append((x, y, h))

    def cut(p, q, half: float) -> bool:
        L = math.dist(p[:2], q[:2])
        n = max(1, int(L // 1000))
        for k in range(n + 1):
            x, y = p[0] + (q[0] - p[0]) * k / n, p[1] + (q[1] - p[1]) * k / n
            for gx in (int(x // 5000) - 1, int(x // 5000), int(x // 5000) + 1):
                for gy in (int(y // 5000) - 1, int(y // 5000), int(y // 5000) + 1):
                    for px, py, h in cells.get((gx, gy), ()):
                        if math.hypot(px - x, py - y) < half + 200 + h:
                            return True
        return False
    return cut


JOIN_LOT_D_CM = 3000.0  # a house lot along a joining street: 16 m of frontage, 30 m deep


def join_lots(city: dict, cities: list, opts: dict | None = None, wet=None, keep: list | None = None) -> tuple:
    """The open land between two cities that lie close fills in: house lots along both sides of each of this
    city's joining streets (city['joins']: [[x0, y0, x1, y1]] from its corner to the middle), never inside a
    city's own edge (its blocks are there), in water or in a keep zone. Returns (lots, corridors): lots as
    city_layout makes them, and each street's corridor as a polygon the layers treat as housing."""
    o = options(opts)
    half = HALF.get(CITY_STREET.get(o["neighbourhood_roads"], o["neighbourhood_roads"]), 875.0)
    lots, corridors = [], []
    for x0, y0, x1, y1 in city.get("joins") or []:
        L = math.hypot(x1 - x0, y1 - y0)
        if L < LOT_W_CM * 2:
            continue
        ux, uy = (x1 - x0) / L, (y1 - y0) / L
        reach = half + 50 + JOIN_LOT_D_CM + 300
        corridors.append([[round(x0 + ux * t - uy * s * reach), round(y0 + uy * t + ux * s * reach)]
                          for t, s in ((0, 1), (L, 1), (L, -1), (0, -1))])
        n = int((L - 400) // LOT_W_CM)
        for k in range(n):
            t = 200 + (L - 400 - n * LOT_W_CM) / 2 + (k + 0.5) * LOT_W_CM
            for s in (1, -1):
                fx, fy = x0 + ux * t - uy * s * (half + 50), y0 + uy * t + ux * s * (half + 50)
                cx, cy = fx - uy * s * JOIN_LOT_D_CM / 2, fy + ux * s * JOIN_LOT_D_CM / 2
                if any(zone_share(c, cx, cy) <= 1.03 for c in cities) or (wet and wet(cx, cy)) or in_keep(keep, cx, cy, 2000):
                    continue
                yaw = math.degrees(math.atan2(-ux * s, uy * s))  # +X faces the street
                lots.append([round(cx), round(cy), round(yaw, 1), round(LOT_W_CM), round(JOIN_LOT_D_CM), round(fx), round(fy), "", False])
    return lots, corridors


def shore_plan(cities: list, opts: dict | None = None, wet=None) -> None:
    """Where each city's freeways must end: at the shore when only open water lies beyond, and before a
    neighbouring city's land (sets city['fw_shore'], see freeways.shore_reach). Before the streets: the freeway
    plan and everything laid round it follow."""
    from .. import freeways
    for c in cities:
        others = [u for u in cities if u is not c and math.hypot(u["x"] - c["x"], u["y"] - c["y"]) < (u["radius"] + c["radius"]) * 1.3 + 400000]
        taken = (lambda x, y, c=c, others=others: zone_share(c, x, y) > 1.0 and any(zone_share(u, x, y) <= 1.02 for u in others))             if others else None
        got = freeways.shore_reach(c, city_options(c, opts), wet, taken) if wet or taken else None
        if got:
            c["fw_shore"] = got
        else:
            c.pop("fw_shore", None)


MODEL_FLAT_CM = 400.0  # a Blender interchange model (flat to a metre) goes only where the ground under it varies less
# ... on ground the world owns (natural city ground) grade_to_roads cuts its pad level: up to this much relief
# (4 m over 1.4 km left no model on any natural hillside at all: a bare freeway, Nov 20 2026; much more and
# the pad stands as a grey plate on the hill, Nov 19 2026)
MODEL_GRADE_CM = 1500.0


def slope_plan(cities: list, opts: dict | None, ground, flat_cm: float = MODEL_FLAT_CM, keep_one: bool = False) -> None:
    """Where a Blender interchange model would stand on a slope (its centre and sockets more than MODEL_FLAT_CM apart
    in height: a parclo on a hillside had its two freeway ends 124 m apart, Nov 18 2026), the spot goes in
    city['ic_steep'] and the plan builds generated ramps there instead. Again until no model is left on a slope.
    keep_one (ground the world owns, cut to the pad): a freeway with every spot too steep still keeps its gentlest
    one, so it is never left without a Blender interchange."""
    for c in cities:
        c.pop("ic_steep", None)
        best = None  # (relief, spot): the gentlest model spot seen
        for _ in range(4):
            steep = []
            for ic in freeway_plan(c, opts).interchanges:
                m = ic.get("model")
                if m:
                    # its centre, sockets and the corners of its levelled cross (the middle half of its square at
                    # full width): a hill beside the freeway is cut too, not only the ground under the sockets
                    (cx, cy), yaw = ic["at"], math.radians(m["yaw"])
                    ax, ay, u, v = math.cos(yaw), math.sin(yaw), m["size"][0] / 4, m["size"][1] / 2
                    corners = [(cx + ax * a - ay * b, cy + ay * a + ax * b) for a in (-u, u) for b in (-v, v)]
                    zs = [z for z in (ground(x, y) for x, y in [ic["at"]] + m["sockets"] + corners) if z is not None]
                    relief = max(zs) - min(zs) if zs else 0.0
                    if relief > flat_cm:
                        steep.append(list(ic["at"]))
                    if ic.get("freeway") != "ns+ew" and (best is None or relief < best[0]):
                        best = (relief, list(ic["at"]))
            if not steep:
                break
            c["ic_steep"] = (c.get("ic_steep") or []) + steep
        if keep_one and best and best[1] in (c.get("ic_steep") or []) and \
                not any(ic.get("model") and ic.get("freeway") != "ns+ew" for ic in freeway_plan(c, opts).interchanges):
            c["ic_steep"] = [s for s in c["ic_steep"] if s != best[1]]


PORT_ROAD = "4L"  # the road to a port: four lanes, for its trucks
PORT_CELL_CM = 6000.0  # ... found on a grid this coarse
PORT_REACH_CM = 600000.0  # ... to a street or a highway no further than this from its gate
PORT_GRADE = 0.08  # ... keeping to ground no steeper than this where it can (steeper costs it dearly)
# a straight port road only over ground no steeper than this: checked for dry land alone, one ran straight down a
# 40 m sea cliff in a trench to its yard (Nov 20 2026); steeper, it goes overland, along the slope
PORT_STRAIGHT_GRADE = 0.12


def overland(start, goals: list, ok, ground, cell: float = PORT_CELL_CM, reach: float = PORT_REACH_CM):
    """The way over open land from `start` (x, y) to the nearest of `goals` [(x, y, tag)]: the cheapest path on a
    grid, where a step costs its length, more on steep ground, and only land `ok(x, y)` is stepped on (a goal's own
    cell always is). Returns ([(x, y), ...] from start to the goal, both in, the goal's tag) or None."""
    import heapq
    key = lambda x, y: (int(x // cell), int(y // cell))
    at = {}
    for g in goals:
        if math.dist(g[:2], start) <= reach:
            at.setdefault(key(g[0], g[1]), g)
    if not at:
        return None
    s = key(*start)
    mid = lambda k: ((k[0] + 0.5) * cell, (k[1] + 0.5) * cell)
    height: dict = {}

    def z(k):
        if k not in height:
            height[k] = ground(*mid(k)) or 0.0
        return height[k]
    lim = int(reach // cell)
    best, prev, heap = {s: 0.0}, {}, [(0.0, s)]
    while heap:
        d, k = heapq.heappop(heap)
        if d > best.get(k, math.inf):
            continue
        if k in at:
            path = [tuple(at[k][:2])]
            while k != s:
                k = prev[k]
                path.append(mid(k))
            path[-1] = tuple(start)
            return path[::-1], at[key(*path[0])][2]
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                n = (k[0] + dx, k[1] + dy)
                if n == k or abs(n[0] - s[0]) > lim or abs(n[1] - s[1]) > lim or (n not in at and not ok(*mid(n))):
                    continue
                step = cell * math.hypot(dx, dy)
                nd = d + step * (1.0 + (abs(z(n) - z(k)) / step / PORT_GRADE) ** 2)
                if nd < best.get(n, math.inf):
                    best[n], prev[n] = nd, k
                    heapq.heappush(heap, (nd, n))
    return None


def port_roads(cities: list, graphs: list, hw_nodes: dict, hw_edges: list, ground, wet=None, keep: list | None = None,
               airports: list | None = None) -> list:
    """A road to every port. A settled shipyard is a walled platform at the water with one gate, and no street came
    near it (a city's streets keep 15 m off a yard): nothing could drive in. Each yard now gets a PORT_ROAD from
    just inside its gate, out through it, and overland (`overland`: off the water, off every yard and airfield,
    easy on the slopes) to the nearest street crossing of any city, or the nearest plain stretch of a highway,
    which gets a junction there. The road is the city's own street (or part of the highways) from then on.
    Sets city['port_roads'] ([[x, y], ...] each) and returns them all."""
    from .. import airport, shipyard
    yards = [(ci, y) for ci, c in enumerate(cities) for y in c.get("shipyards") or [] if y.get("z") is not None]
    for c in cities:
        c.pop("port_roads", None)
    if not yards:
        return []
    every = [y for _ci, y in yards]

    def ok(x, y):
        if wet and (wet(x, y) or any(wet(x + a, y + b) for a, b in ((4000, 0), (-4000, 0), (0, 4000), (0, -4000)))):
            return False
        return not (any(shipyard.reserved(s, x, y, 3000) for s in every)
                    or any(airport.inside(a, x, y, 6000) for a in airports or [])
                    or in_keep(keep, x, y, 6000))
    goals = []
    for ci, (nodes, _edges) in enumerate(graphs):
        used = {n for e in _edges for n in e[:2]}
        goals += [(p[0], p[1], ("street", ci, n)) for n, p in nodes.items()
                  if n in used and not any(shipyard.reserved(s, p[0], p[1], 1000) for s in every)]
    for ei, e in enumerate(hw_edges):
        if len(e) > 3 and e[2] in HIGHWAYS:
            goals += [(v[0], v[1], ("highway", ei, k)) for k, v in enumerate(e[3])
                      if (len(v) < 3 or not v[2]) and (len(v) < 4 or v[3] in (None, e[2]))]
    made = []
    for yi, (ci, y) in enumerate(yards):
        f, L, D = y["frame"], y["L"], y["D"]
        w = lambda u, v: (f["x"] + f["ux"] * u + f["vx"] * v, f["y"] + f["uy"] * u + f["vy"] * v)
        for key in ("gate_u", "gate_side", "gate_v"):
            y.pop(key, None)
        got, bank = None, float(y.get("bank") or 0.0)
        # its gate: the back corner where shipyard.build puts it; with water or no way out behind that, further
        # along its back edge; with water all along its back, in one of its sides (a yard on a spit had the lake
        # behind its gate and got no road at all)
        ways = [(None, u) for u in (L / 2 - 9950, L / 4, 0.0, -L / 4, -(L / 2 - 9950))] + \
               [(s, v) for v in (D * 0.75, D * 0.5, D * 0.25) for s in (1, -1)]
        def straight(out_, reach=30000.0, dry=True):
            """The nearest street crossing within `reach` that a straight road reaches clear of every yard, and
            (dry) over dry land: (path, tag). A yard squeezed between the water and a street has no room for the
            grid's 60 m cells."""
            for g in sorted((g for g in goals if g[2][0] == "street" and math.dist(g[:2], out_) < reach), key=lambda g: math.dist(g[:2], out_)):
                n = max(1, int(math.dist(g[:2], out_) // 800))
                line = [(out_[0] + (g[0] - out_[0]) * i / n, out_[1] + (g[1] - out_[1]) * i / n) for i in range(n + 1)]
                if not any((dry and wet and wet(*p)) or any(shipyard.reserved(s, p[0], p[1], 300) for s in every) for p in line[1:]) \
                        and not (dry and any(abs((ground(*q) or 0.0) - (ground(*p) or 0.0)) > PORT_STRAIGHT_GRADE * math.dist(p, q)
                                             for p, q in zip(line, line[1:]))):
                    return [tuple(out_), tuple(g[:2])], g[2]
            return None
        for k, (side, at) in enumerate(ways):
            out_ = w(at, D + bank + 3500) if side is None else w(side * (L / 2 + bank + 3500), at)
            if wet and wet(*out_):
                continue
            got = straight(out_) or overland(out_, goals, ok, ground)
            if got:
                if side is not None:
                    y["gate_side"], y["gate_v"] = side, round(at + 950)  # (the gatehouse beside the road)
                elif k:
                    y["gate_u"] = round(at + 950)
                break
        if not got:
            # an island of made ground with water all round it: from its gate straight to the nearest street,
            # across the water (that stretch is bridged with the city's other crossings, over_water)
            k, side, at = 0, None, L / 2 - 9950
            out_ = w(at, D + bank + 3500)
            got = straight(out_, 60000.0, dry=False)
        if not got:
            continue
        if side is None:
            inside_, gate_ = w(at, D - (2500 if k == 0 else 700)), w(at, D)
        else:
            inside_, gate_ = w(side * (L / 2 - 700), at), w(side * L / 2, at)
        path, tag = got
        # eased: the grid's 45-degree steps become one line (a point kept every other cell, each the mean of three)
        mids = [((a[0] + b[0] + c_[0]) / 3, (a[1] + b[1] + c_[1]) / 3) for a, b, c_ in zip(path, path[1:], path[2:])][::2]
        via = [list(gate_), list(out_)] + [[round(x), round(y_)] for x, y_ in mids if math.dist((x, y_), path[-1]) > 4000
                                           and math.dist((x, y_), out_) > 4000]
        a = ("port", ci, yi)
        if tag[0] == "street":
            nodes, edges = graphs[tag[1]]
            nodes[a] = [round(inside_[0]), round(inside_[1])]
            edges.append([a, tag[2], PORT_ROAD, via])
            end = nodes[tag[2]]
        else:
            e = hw_edges[tag[1]]
            j = ("port", ci, yi, "hw")
            hw_nodes[a], hw_nodes[j] = [round(inside_[0]), round(inside_[1])], [e[3][tag[2]][0], e[3][tag[2]][1]]
            hw_edges[tag[1]] = [e[0], j, e[2], e[3][:tag[2]]]
            hw_edges.append([j, e[1], e[2], e[3][tag[2] + 1:]])
            hw_edges.append([a, j, PORT_ROAD, via])
            end = hw_nodes[j]
            # (the other goals on that highway edge: their places in it have moved)
            goals = [g for g in goals if not (g[2][0] == "highway" and g[2][1] == tag[1])]
        line = [[round(p[0]), round(p[1])] for p in [inside_] + via + [end]]
        cities[ci].setdefault("port_roads", []).append(line)
        made.append(line)
    return made


def links(cities: list) -> list:
    """Highways: the shortest tree joining every city (Prim), then a second way out of every city that has only
    one, to its nearest city not yet joined, where that highway crosses no other: from three cities on, a city is
    never cut off by one road (a tree gave each end city a single highway, Nov 18 2026)."""
    if len(cities) < 2:
        return []
    at = lambda i: (cities[i]["x"], cities[i]["y"])
    D = lambda p: math.dist(at(p[0]), at(p[1]))
    done, out = {0}, []
    while len(done) < len(cities):
        a, b = min(((i, j) for i in done for j in range(len(cities)) if j not in done), key=D)
        done.add(b)
        out.append((a, b))

    def crosses(p, q):
        def side(o, u, v):
            return (u[0] - o[0]) * (v[1] - o[1]) - (u[1] - o[1]) * (v[0] - o[0])
        a, b, c, d = at(p[0]), at(p[1]), at(q[0]), at(q[1])
        return side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0
    for i in range(len(cities)):
        if sum(i in e for e in out) >= 2:
            continue
        for j in sorted((j for j in range(len(cities)) if j != i), key=lambda j: D((i, j))):
            if (i, j) not in out and (j, i) not in out and not any(crosses((i, j), e) for e in out if not {i, j} & set(e)):
                out.append((i, j))
                break
    return out


# ------------------------------------------------------------------ splines
ROAD_OFFSET_CM = 6.0  # a road segment's mesh is drawn this far up its points (junction decks: road_io._OWN_DECK_Z)
# (10 left every deck ~12 cm over the graded ground with its kerb wall showing; 0 let bumps poke through)


def _point(i, x, y, z, yaw, w, mesh="", lifted=False, piece=""):
    """A control point. lifted: on a bridge / ramp (never shapes the ground). piece: a mesh path that sits
    on the point (a trumpet loop, a cloverleaf)."""
    live = "False" if lifted else "True"
    lines = [f"Width={w:.1f}", f"bRaiseTerrain={live}", f"bLowerTerrain={live}", "SideFalloff=800.000000",
             "EndFalloff=1000.000000"] + ([] if mesh else [f"SegmentMeshOffset={ROAD_OFFSET_CM:.1f}"])
    if piece:
        name = piece.rsplit("/", 1)[-1]
        lines += [f"Mesh=\"/Script/Engine.StaticMesh'{piece}.{name}'\"", "MeshScale=(X=1.000000,Y=1.000000,Z=1.000000)"]
        mesh = name
    return {"i": i, "x": x, "y": y, "z": z, "pitch": 0.0, "yaw": yaw, "roll": 0.0, "w": w, "side": 800.0,
            "mesh": mesh, "raise": not lifted, "lines": lines}


def _segment(i, a, b, kind, folder, lifted=False):
    live = "False" if lifted else "True"  # bridges and ramps never shape the ground under them
    lines = [f"bRaiseTerrain={live}", f"bLowerTerrain={live}"]
    path = ROAD_MESH.get(kind, "")
    mesh = path.rsplit("/", 1)[-1]
    if mesh:
        lines.insert(0, f"SplineMeshes(0)=(Mesh=\"/Script/Engine.StaticMesh'{folder}/{path}.{mesh}'\",bScaleToWidth=False)")
    return {"i": i, "a": a, "b": b, "ta": 0.0, "tb": 0.0, "mesh": mesh, "raise": not lifted, "lines": lines}


ROAD_SAMPLE_CM = 2000.0  # a street on the ground gets a point this often, draped on the hill (straighten keeps crests)
UNSAMPLED = {"ED", "ER", "BR", "BRR", "BW", "CN", "DK", "RW", "RS", "TW"}  # decks, bridges, canals, docks, runways: level by design


def _sampled(e: list, nodes: dict) -> list:
    """e with a via point every ROAD_SAMPLE_CM along each leg that lies on the ground as the edge's own kind (no lift,
    no bridge / ramp kind of its own). A street had points only at its crossings: one 370 m straight line from foot to
    foot of a ridge, and the grading dug a trench through the ridge to it (Nov 20 2026)."""
    if e[2] in UNSAMPLED:
        return e
    via = e[3] if len(e) > 3 else []
    line = [nodes[e[0]]] + via + [nodes[e[1]]]
    flat = lambda v: len(v) < 3 or not float(v[2] or 0)  # noqa: E731
    out, changed = [], False
    for k, (p, q) in enumerate(zip(line, line[1:])):
        if k:
            out.append(via[k - 1])
        own = k == 0 or len(via[k - 1]) < 4 or via[k - 1][3] in (None, e[2])
        n = math.ceil(math.dist(p[:2], q[:2]) / ROAD_SAMPLE_CM) - 1
        if n and own and flat(p) and flat(q):
            out += [[p[0] + (q[0] - p[0]) * t / (n + 1), p[1] + (q[1] - p[1]) * t / (n + 1)] for t in range(1, n + 1)]
            changed = True
    return [e[0], e[1], e[2], out] + list(e[4:]) if changed else e


def build_actor(nodes: dict, edges: list, ground, folder: str, pieces: dict | None = None) -> dict:
    """Street graph -> spline points and segments. A node with 3+ streets becomes a junction (a
    pending piece with a mouth point on every arm); edges may carry 'via' points (highways):
    [x, y] or [x, y, lift_cm, kind of the segment that starts there] for bridges and ramps.
    pieces: {node: mesh path} puts a piece (trumpet loop, cloverleaf) on an end point."""
    pieces = pieces or {}
    edges = [_sampled(e, nodes) for e in edges]

    def high(x, y):  # a bridge / ramp point sits over the HIGHEST ground near it, never in a hill between samples
        return max(ground(x + dx, y + dy) for dx, dy in ((0, 0), (1500, 0), (-1500, 0), (0, 1500), (0, -1500)))

    adj: dict = {}
    for e in edges:
        adj.setdefault(e[0], []).append(e)
        adj.setdefault(e[1], []).append(e)
    pts, segs, ends = [], [], {}
    glim: dict = {}  # point -> steepest grade of the roads through it (the flattest road wins)
    ids = iter(range(1 << 30))

    def angle(p, q):
        return math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))

    def first_step(e, n):  # the next position along e when leaving node n
        via = e[3] if len(e) > 3 else []
        if via:
            return via[0] if e[0] == n else via[-1]
        return nodes[e[1] if e[0] == n else e[0]]

    def turn(n, arms):  # degrees a 2-arm node turns (0 = straight on)
        x, y = nodes[n][:2]
        (ax, ay), (bx, by) = first_step(arms[0], n)[:2], first_step(arms[1], n)[:2]
        a1, a2 = math.atan2(ay - y, ax - x), math.atan2(by - y, bx - x)
        return 180.0 - abs((math.degrees(a1 - a2) + 180.0) % 360.0 - 180.0)

    for n, arms in adj.items():
        x, y = nodes[n][:2]
        lifted_node = len(nodes[n]) > 2 and float(nodes[n][2]) > 0
        # a junction piece where 3+ streets meet, and an ELBOW piece where a street turns sharply (a square
        # corner): a plain spline point would smooth a 90 degree turn into a wide arc that loops out
        corner = len(arms) == 2 and not lifted_node and turn(n, arms) > CORNER_DEG
        if len(arms) >= 3 or corner:
            w = max(HALF[e[2]] for e in arms) + 300.0
            j = _point(next(ids), x, y, ground(x, y), 0.0, w, JUNCTION)
            pts.append(j)
            for e in arms:
                q = first_step(e, n)
                a = angle((x, y), q)
                mx, my = x + MOUTH_CM * math.cos(math.radians(a)), y + MOUTH_CM * math.sin(math.radians(a))
                m = _point(next(ids), mx, my, ground(mx, my), a, HALF[e[2]])
                pts.append(m)
                segs.append(_segment(next(ids), j["i"], m["i"], "", folder))
                ends[(id(e), n)] = m["i"]
                for i in (j["i"], m["i"]):
                    glim[i] = min(glim.get(i, 1.0), GRADE.get(e[2], 0.08))
        else:
            steps = [first_step(e, n) for e in arms]
            yaw = angle(steps[0], steps[1]) if len(steps) == 2 else angle((x, y), steps[0])
            up = float(nodes[n][2]) if len(nodes[n]) > 2 else 0.0  # a ramp end up on a bridge
            p = _point(next(ids), x, y, (high(x, y) if up > 0 else ground(x, y)) + up, yaw, max(HALF[e[2]] for e in arms),
                       piece=pieces.get(n, ""), lifted=up > 0)
            pts.append(p)
            for e in arms:
                ends[(id(e), n)] = p["i"]
    chains = []
    for e in edges:
        via = e[3] if len(e) > 3 else []
        chain = [ends[(id(e), e[0])]]
        chains.append(chain)
        line = [nodes[e[0]]] + via + [nodes[e[1]]]
        lift = {chain[0]: 0.0}
        kinds = [e[2]]  # the kind of the segment starting at each chain point
        for k, v in enumerate(via, start=1):
            x, y = v[0], v[1]
            up = float(v[2]) if len(v) > 2 else 0.0
            p = _point(next(ids), x, y, (high(x, y) if up > 0 else ground(x, y)) + up, angle(line[k - 1], line[k + 1]),
                       HALF[e[2]], lifted=up > 0)
            pts.append(p)
            chain.append(p["i"])
            lift[p["i"]] = up
            kinds.append(v[3] if len(v) > 3 and v[3] else e[2])
        chain.append(ends[(id(e), e[1])])
        lift[chain[0]] = float(nodes[e[0]][2]) if len(nodes[e[0]]) > 2 else 0.0
        lift[chain[-1]] = float(nodes[e[1]][2]) if len(nodes[e[1]]) > 2 else 0.0
        for k, (a, b) in enumerate(zip(chain, chain[1:])):
            segs.append(_segment(next(ids), a, b, kinds[k], folder, lifted=lift.get(a, 0) > 0 or lift.get(b, 0) > 0))
            for i in (a, b):
                glim[i] = min(glim.get(i, 1.0), GRADE.get(kinds[k], 0.08))
    by_i = {p["i"]: p for p in pts}
    for chain in chains:  # viaducts, decks and flyovers run level, never riding the hills under them
        level_decks([by_i[i] for i in chain])
    for s in segs:
        st.retangent(by_i, s)
    st.drape(pts, segs, {p["i"]: p["z"] for p in pts}, iters=4, max_grade=glim, max_cut=MAX_CUT_CM)
    # a one-point bridge between two junctions a few metres apart (over_water's 'ramps meet mid-span') stood on the
    # highest ground within 15 m, 7 m over both its ends: a 55 % hump. It rises HUMP_GRADE from its lower end, no more
    for chain in chains:
        for a, m, b in zip(chain, chain[1:], chain[2:]):
            pa, pm, pb = by_i[a], by_i[m], by_i[b]
            if not pm["raise"] and pa["raise"] and pb["raise"] and \
                    max(math.hypot(q["x"] - pm["x"], q["y"] - pm["y"]) for q in (pa, pb)) < 3000:
                cap = min(q["z"] + HUMP_GRADE * math.hypot(q["x"] - pm["x"], q["y"] - pm["y"]) for q in (pa, pb))
                if pm["z"] > cap:
                    pm["z"] = cap
                    for s in segs:
                        if m in (s["a"], s["b"]):
                            st.retangent(by_i, s)
    return {"points": pts, "segments": segs}


HUMP_GRADE = 0.12  # a one-point bridge's rise from its ends


def detach(actor: dict) -> None:
    """A freeway or highway actor that never shapes the landscape: its splines carry their meshes only (the
    plugin's own heightmap is already cut to them). City streets keep shaping it."""
    for x in actor["points"] + actor["segments"]:
        if "raise" in x:
            x["raise"] = False
        x["lines"] = [l.replace("bRaiseTerrain=True", "bRaiseTerrain=False").replace("bLowerTerrain=True", "bLowerTerrain=False")
                      for l in x.get("lines") or []]


DECK_WINDOW_CM = 15000.0  # a deck's height is smoothed over this much road each way


def level_decks(ps: list) -> None:
    """Each run of lifted points along one road (a viaduct, a deck, a flyover) on its own smooth line instead of a
    fixed lift over every bump of the ground (decks rode the hills, Nov 12 2026). The line is the mean, over
    DECK_WINDOW_CM each way, of the highest planned height within DECK_WINDOW_CM: never under any point's planned
    clearance, since each point's height is in every window around it. Toward the run's ends it eases back to the
    planned heights, so the ramps down still land where they were planned."""
    k = 0
    while k < len(ps):
        if ps[k]["raise"]:
            k += 1
            continue
        j = k
        while j < len(ps) and not ps[j]["raise"]:
            j += 1
        run = ps[k:j]
        k = j
        if len(run) < 3:
            continue
        s = [0.0]
        for a, b in zip(run, run[1:]):
            s.append(s[-1] + math.hypot(b["x"] - a["x"], b["y"] - a["y"]))
        base = [p["z"] for p in run]
        W = DECK_WINDOW_CM
        env = [max(base[m] for m in range(len(run)) if abs(s[m] - s[n]) <= W) for n in range(len(run))]
        for n, p in enumerate(run):
            near = [env[m] for m in range(len(run)) if abs(s[m] - s[n]) <= W]
            ease = min(1.0, min(s[n], s[-1] - s[n]) / W)
            p["z"] = base[n] + (sum(near) / len(near) - base[n]) * ease


def _seg_cross(p1, p2, q1, q2):
    """Where segment p1-p2 crosses q1-q2, as the share along p (None if they don't)."""
    d = (p2[0] - p1[0]) * (q2[1] - q1[1]) - (p2[1] - p1[1]) * (q2[0] - q1[0])
    if abs(d) < 1e-9:
        return None
    t = ((q1[0] - p1[0]) * (q2[1] - q1[1]) - (q1[1] - p1[1]) * (q2[0] - q1[0])) / d
    u = ((q1[0] - p1[0]) * (p2[1] - p1[1]) - (q1[1] - p1[1]) * (p2[0] - p1[0])) / d
    return t if 0 < t < 1 and 0 < u < 1 else None


def overpass_via(line: list, streets: list, kind: str) -> tuple:
    """A highway polyline's via points with a bridge over every street it crosses: ramp up 40 m, deck
    over the street, ramp down 40 m (crossings closer than 150 m share one bridge). Returns (via, count)."""
    cum = [0.0]
    for p, q in zip(line, line[1:]):
        cum.append(cum[-1] + math.dist(p[:2], q[:2]))
    hits = []
    for k, (p, q) in enumerate(zip(line, line[1:])):
        for a, b in streets:
            t = _seg_cross(p, q, a, b)
            if t is not None:
                hits.append(cum[k] + t * (cum[k + 1] - cum[k]))
    hits.sort()
    spans = []
    for s in hits:  # merge close crossings into one longer bridge
        if spans and s - spans[-1][1] < 15000:
            spans[-1][1] = s
        else:
            spans.append([s, s])
    total = cum[-1]
    spans = [sp for sp in spans if sp[0] > 40000 and sp[1] < total - 40000]  # never in the gate interchanges

    def at(s):
        for k in range(len(cum) - 1):
            if cum[k] <= s <= cum[k + 1]:
                t = (s - cum[k]) / ((cum[k + 1] - cum[k]) or 1)
                return [line[k][0] + (line[k + 1][0] - line[k][0]) * t, line[k][1] + (line[k + 1][1] - line[k][1]) * t]
        return list(line[-1][:2])

    keep = [(cum[k], list(line[k][:2])) for k in range(1, len(line) - 1)
            if not any(sp[0] - 8000 < cum[k] < sp[1] + 8000 for sp in spans)]
    for a, b in spans:
        deck, ramp = structure_kinds(kind)
        keep += [(a - 7000, at(a - 7000) + [0.0, ramp]), (a - 3000, at(a - 3000) + [BRIDGE_LIFT_CM, deck]),
                 (b + 3000, at(b + 3000) + [BRIDGE_LIFT_CM, ramp]), (b + 7000, at(b + 7000) + [0.0, kind])]
    keep.sort(key=lambda r: r[0])
    return [v for _, v in keep], len(spans)


def interchange(kind: str, end: list, toward: list, tag: tuple, hw: str) -> tuple:
    """Real geometry for where a highway meets a city, made of our ramp and bridge pieces:
    (highway profile [[distance from end, lift cm]], ramp nodes, ramp edges, cross road [x0, x1]).
    The highway bridges over a cross road 180 m out from its end; the cross road belongs to the city's
    own streets (generate joins both its ends to them). Every ramp touches the road edges it links:
    loops are tangent to the highway and the cross road (centre = radius + that road's half width),
    start at the highway's height there and come down to the cross road.
    cloverleaf: 4 loops + 4 slip ramps; trumpet: 1 loop + 1 slip ramp; diamond: 4 straight slip ramps."""
    ux, uy = toward[0] - end[0], toward[1] - end[1]
    ul = math.hypot(ux, uy) or 1.0
    ux, uy = ux / ul, uy / ul
    nx, ny = -uy, ux
    D, R, deck = 18000.0, 5500.0, 6000.0  # crossing 180 m out; loop radius 55 m; deck +-60 m around it
    lift = BRIDGE_LIFT_CM
    h_hw, h_x, h_ramp = HALF[hw], HALF["4L"], HALF["ER"]
    oa, ob = R + h_x, R + h_hw  # loop centre: off the cross road (along the highway), off the highway
    cx, cy = end[0] + ux * D, end[1] + uy * D
    P = lambda a, b, up=0.0: [cx + ux * a + nx * b, cy + uy * a + ny * b] + ([up] if up else [])

    def deck_lift(a):  # the highway's height a cm from the crossing: deck, then a 60 m ramp down
        return lift * max(0.0, min(1.0, 1 - (abs(a) - deck) / deck))

    profile = [[D - 2 * deck, 0.0], [D - deck, lift], [D + deck, lift], [D + 2 * deck, 0.0]]
    nodes, edges = {}, []
    sides = (1,) if kind == "trumpet" else (-1, 1)
    reach = ob + R + 6000.0  # the cross road runs past the loops and slip ramps on each side
    cross = [P(0, -reach if kind != "trumpet" else -h_hw - 2000), P(0, reach)]
    quads = [(i, j) for i in (-1, 1) for j in sides]
    loops = quads if kind == "cloverleaf" else ([(1, 1)] if kind == "trumpet" else [])
    slips = quads if kind in ("cloverleaf", "diamond") else ([(-1, 1)] if kind == "trumpet" else [])
    for i, j in loops:  # 270 deg loop: tangent to the highway edge, down to the cross road edge
        qa, qb = i * oa, j * ob
        a0 = math.atan2(-j, 0.0)  # the point facing the highway
        sweep = 1.5 * math.pi * (1 if i * j > 0 else -1)
        top = deck_lift(qa)
        arc = []
        for k in range(13):
            t = a0 + sweep * k / 12
            arc.append(P(qa + R * math.cos(t), qb + R * math.sin(t), top * (1 - k / 12)))
        a, b = tag + ("l", i, j, 0), tag + ("l", i, j, 1)
        nodes[a], nodes[b] = arc[0] if len(arc[0]) > 2 else arc[0] + [0.0], arc[-1][:2]
        edges.append([a, b, "ER", [p + ["ER"] if len(p) > 2 else p + [0.0, "ER"] for p in arc[1:-1]]])
    for i, j in slips:  # slip ramp: from the highway edge beyond the deck to the cross road edge, bowed out
        far = 2 * deck + R
        h = P(i * far, j * (h_hw + h_ramp))
        x = P(i * (h_x + h_ramp), j * (ob + R))
        mid = P(i * (far + h_x) * 0.55, j * (h_hw + ob + R) * 0.6)
        a, b = tag + ("s", i, j, 0), tag + ("s", i, j, 1)
        nodes[a], nodes[b] = h, x
        edges.append([a, b, "2L", [mid + [0.0, "2L"]]])
    return profile, nodes, edges, cross
    return profile, nodes, edges



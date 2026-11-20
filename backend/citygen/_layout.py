from __future__ import annotations

COLLECTOR_ROWS = 0.65  # of the suburbs' rows of blocks, how many share one winding street from block to block
SUPERBLOCK_SHARE = 0.8  # of the suburbs' whole collector cells, how many are one subdivision with no public street inside
LOT_W_CM, LOT_D_CM = 1600.0, 3500.0  # a lot: 16 m frontage, up to 35 m deep (two back to back in a block)
# ... wider where what is built there is wider: an office block or a tower downtown (a 16 m lot fits neither, and
# every downtown lot between two towers stood empty), a warehouse or a factory in the industrial zone
ZONE_LOT_W_CM = {"downtown": 2800.0, "industrial": 4000.0}


NO_ROAD = "--"  # a block's side where the street was not built (_street_gaps): the next block lies right there
NO_ROAD_INSET = 150.0  # ... so the block reaches to 1.5 m short of that line (back fences 3 m apart), no sidewalk
SOLID_MIN = 0.86  # a block that fills less of its own box than this (an L, a wedge, one a dead end reaches deep into)
# is cut into plain lots along its streets: a neighbourhood is laid out in the box


def city_layout(city: dict, opts: dict | None = None, wet=None, ground=None, keep: list | None = None,
                water: dict | None = None, clear: list | None = None) -> dict:
    """BlockGenerator + neighbourhoods: every block of the city's street grid becomes either plain street-fronting
    lots (parcels) or one neighbourhood (neighbourhoods.TYPES: subdivisions with private streets, apartment
    complexes with parking), picked per block from the city's seed and weighted by its district ring. Then
    every street side that no block lies on gets a row of lots of its own (frontage_lots): the land between
    the last street and the city's edge, and round every dead end, is built on like any other.
    Lots: [[x, y, yaw, width_cm, depth_cm, front x, front y, role ('' = any layer), footprint]].
    keep: the planned facilities to keep where they are; clear: the world's keep zones (nothing is laid there)."""
    from .. import neighbourhoods as nb
    from .. import parcels
    o = city_options(city, opts)
    unbuilt: list = []
    nodes, edges = city_graph(city, opts, wet, virtual=unbuilt)
    # the land is parted into blocks along every street, and along the streets that were not built between two
    # neighbouring blocks (_street_gaps): each is still laid out on its own ground, with nothing between it and
    # the next
    cut_nodes, cut_edges = nodes, edges
    if unbuilt:
        cut_nodes, cut_edges = dict(nodes), list(edges)
        have = {(e[0], e[1]) for e in edges} | {(e[1], e[0]) for e in edges}
        for a, b, pa, pb in unbuilt:
            if (a, b) not in have:
                cut_nodes.setdefault(a, pa)
                cut_nodes.setdefault(b, pb)
                cut_edges.append([a, b, NO_ROAD])
    cut_half = {**HALF, NO_ROAD: NO_ROAD_INSET - parcels.SIDEWALK_CM}
    wet = water_of(city, o, wet)  # canals are water too: no lot in them
    rnd = random.Random(f"lots:{city.get('seed', 1)}")
    fw = freeway_plan(city, o)
    out = {"lots": [], "streets": [], "parking": [], "stalls": [], "pads": [], "trees": [], "fences": [], "pools": [], "hoods": {},
           "blocks": [], "rezoned": []}
    down, normal = o["downtown"], max(o["normal"], o["downtown"])
    zones = [z for z in city.get("zones") or [] if isinstance(z, dict) and "x" in z and "y" in z]
    big_park = central_park(city, o, wet)
    if big_park:  # the block the streets left whole there is a park (the person's own choice for it still wins)
        grid_at = street_grid(city, o)[0]
        corners = [grid_at(i, j)[:2] for i in (big_park[0], big_park[2]) for j in (big_park[1], big_park[3])]
        zones = [{"x": sum(p[0] for p in corners) / 4, "y": sum(p[1] for p in corners) / 4, "kind": "park"}] + zones
    park_grounds: list = []
    in_wedge = zoning(city, o)[1]
    PARTS = ("lots", "streets", "parking", "stalls", "pads", "trees", "fences", "pools")
    fronted: set = set()  # (node, next node) of every street side a block lies on
    meets: set = set()  # the middle of every block side with no street: where two subdivisions' streets may meet
    ce = o["collector_every"]
    crossing = lambda n: isinstance(n, tuple) and len(n) == 2 and isinstance(n[0], int) and n[0] % ce == 0 and n[1] % ce == 0
    for area, sides in parcels.block_faces(cut_nodes, cut_edges, cut_half):
        fronted.update((a, b) for a, b, _k in sides)
        # where this block has no road on a side: that side's middle (each stretch from one main-road crossing
        # to the next), where its streets meet the neighbour's; and the lines no lot may front
        ghosts = [(cut_nodes[a][:2], cut_nodes[b][:2]) for a, b, k in sides if k == NO_ROAD]
        open_mids, run = [], []
        start = next((i for i, (a, _b, k) in enumerate(sides) if k != NO_ROAD or crossing(a)), 0) if ghosts else 0
        for a, b, k in (sides[start:] + sides[:start] + [(None, None, None)] if ghosts else ()):
            if k == NO_ROAD:
                run = (run or [a]) + [b]
            if run and (k != NO_ROAD or crossing(b)):
                m = len(run) // 2  # (the same point from either block: the middle crossing, or the middle of the middle piece)
                p, q = cut_nodes[run[m]], cut_nodes[run[m] if len(run) % 2 else run[m - 1]]
                open_mids.append([round((p[0] + q[0]) / 2), round((p[1] + q[1]) / 2)])
                run = []
        meets.update((p[0], p[1]) for p in open_mids)
        cx, cy = sum(p[0] for p in area) / len(area), sum(p[1] for p in area) / len(area)
        share = zone_share(city, cx, cy)
        ring = "core" if share <= down else "normal" if share <= normal else "housing"
        _, _, ux, uy, L, W = parcels._obb(area)
        if abs(ux) < abs(uy):  # the axis neighbourhoods' 'collector' street runs along (the same for every block)
            ux, uy = -uy, ux
        row = round((cx * uy - cy * ux) * (1 if ux > 0 else -1) / 6000)  # blocks side by side along it share a row
        kind = nb.pick(ring, rnd) if ring != "core" and min(L, W) >= nb.MIN_SIDE else "parcels"
        # (a block the freeway cuts gets plain lots, the ones clear of it kept; so does one that is no near
        # rectangle: a neighbourhood drawn in its box would not fit it)
        cut = bool(fw.lines and fw.blocked(cx, cy, 0.5 * max(L, W))) or parcels.solid(area) < SOLID_MIN
        if clear and not cut:
            # ... and one that a keep zone reaches into: nothing is laid in the zone, and what a neighbourhood has
            # left outside it is half a layout (its entrance gone, a block bare beside the zone)
            cut = any(in_keep(clear, p[0], p[1], 2000) for p in area) or in_keep(clear, cx, cy, 2000) or \
                any(_inside(area, x, y) for k in clear for x in (k["x0"], k["x1"]) for y in (k["y0"], k["y1"]))
        if cut:
            kind = "parcels"
        elif ring != "core" and min(L, W) >= nb.BIG_SIDE:
            kind = "collector"  # a superblock: plain lots would be landlocked in its middle
        elif ring == "housing" and kind != "parcels" and \
                random.Random(f"row:{city.get('seed', 1)}:{row}").random() < COLLECTOR_ROWS:
            kind = "collector"  # a whole row of suburb blocks: the one winding street runs on through them all
        zone = "downtown" if ring == "core" else "industrial" if in_wedge(cx, cy) else ring
        if zone == "industrial":
            kind = "parcels"  # industry stands on plain lots along the streets: no subdivision among the factories
        plain = lambda r, z: {"lots": [[x, y, yaw, w, d, fx, fy, "", False]
                                       for x, y, yaw, w, d, _poly, fx, fy in parcels.lots_in(area, ZONE_LOT_W_CM.get(z, LOT_W_CM), LOT_D_CM, r)
                                       # (no lot faces a side where no road was built)
                                       if not any(_dist_seg((fx, fy), p, q) < NO_ROAD_INSET + 150 for p, q in ghosts)]}
        laid = lambda k, r: nb.generate(area, k, r, joins=open_mids if k == "collector" else None, closed=ghosts)
        # the block as the city's own plan lays it, on the city's shared random as always ...
        add = plain(rnd, zone) if kind == "parcels" else laid(kind, rnd)
        own = next((z for z in reversed(zones) if _inside(area, z["x"], z["y"])), None) if zones else None
        if own:
            # ... then the person's own choice for it, on a random of the block's own: the plan above was still
            # drawn (and dropped), so no other block of the city changes when one is given a type
            local = random.Random(f"block:{city.get('seed', 1)}:{round(cx)}:{round(cy)}")
            want = own.get("kind") or ""
            if own.get("zone") in BLOCK_ZONES and own["zone"] != zone:
                zone = own["zone"]
                out["rezoned"].append([[[round(x), round(y)] for x, y in area], zone])
                if not want and zone == "downtown":  # a core block is plain lots for the towers; any other keeps
                    want = "parcels"  # its own layout and only changes what is built on it
            if want == "park":
                kind, add = "park", _park(area, local)
                park_grounds += add["grounds"]
            elif want == "parcels" or (want in BLOCK_KINDS and want in nb.TYPES and not cut
                                       and min(L, W) >= nb.MIN_SIDE):
                kind, add = want, plain(local, zone) if want == "parcels" else laid(want, local)
        # some of its lots are planted instead of built on: the block's own share, else the world's
        green = own["green"] if own and own.get("green") is not None else o["green_lots"]
        green = max(0.0, min(1.0, float(green)))
        if green and add.get("lots"):
            add = _green_lots(add, green, random.Random(f"green:{city.get('seed', 1)}:{round(cx)}:{round(cy)}"))
        if kind == "parcels" and add.get("lots"):
            # a block of plain lots is built along its streets only: whatever it leaves in the middle (a court
            # 130 m across in a 200 m block, the strip along a side with no street) is planted, never bare
            court = _court(area, add["lots"], random.Random(f"court:{city.get('seed', 1)}:{round(cx)}:{round(cy)}"))
            if len(court) >= COURT_MIN:
                add = {**add, "trees": list(add.get("trees") or []) + court}
        out["hoods"][kind] = out["hoods"].get(kind, 0) + 1
        for key in PARTS:
            out[key] += add.get(key) or []
        out["blocks"].append([round(cx), round(cy), kind, zone, [[round(x), round(y)] for x, y in area], round(green, 2)])
    # two subdivisions with no road between them: where the street of one ends on the street of the other, the
    # two are one street
    ends: dict = {}
    for s in out["streets"]:
        for at_end in (0, -1):
            ends.setdefault((s[at_end][0], s[at_end][1]), []).append(s)
    for point in [k for k, v in ends.items() if len(v) == 2 and v[0] is not v[1]]:
        a, b = ends[point]
        if a not in out["streets"] or b not in out["streets"]:
            continue
        a_ = a if (a[-1][0], a[-1][1]) == point else a[::-1]
        b_ = b if (b[0][0], b[0][1]) == point else b[::-1]
        one = a_ + b_[1:]
        out["streets"] = [s for s in out["streets"] if s is not a and s is not b] + [one]
        for k, v in ends.items():
            ends[k] = [one if s is a or s is b else s for s in v]
    # ... and where the block beside it has no street to meet (a complex, a loop, plain lots), the street does
    # not run up to a back fence: it stops 13 m short, in a turnaround
    for s in out["streets"]:
        for at_end, nxt in ((0, 1), (-1, -2)):
            p, q = s[at_end], s[nxt]
            if (p[0], p[1]) in meets:
                d = math.dist(p[:2], q[:2]) or 1.0
                back = min(1300.0, 0.9 * d)
                s[at_end] = [round(p[0] + (q[0] - p[0]) * back / d), round(p[1] + (q[1] - p[1]) * back / d)]
                out["pads"].append([s[at_end][0], s[at_end][1], nb.BULB])
    out["lots"] += frontage_lots(city, o, nodes, edges, fronted, wet, clear)
    if clear:
        # a keep zone is left alone: no street is laid into it (generate), so no lot, private street, parking or
        # yard is planned there either (lots stood there on streets that were never built, empty for good)
        for key in ("lots", "parking", "stalls", "pads", "fences", "pools"):
            out[key] = [r for r in out[key] if not in_keep(clear, r[0], r[1], 2000)]
        out["streets"] = [s for s in out["streets"] if not any(in_keep(clear, p[0], p[1], 2000) for p in s)]
    if fw.lines:  # right of way: nothing built under or beside a freeway or ramp
        out["lots"] = [r for r in out["lots"] if not fw.blocked(r[0], r[1], 0.5 * max(r[3], r[4]))]
        for key in ("parking", "stalls", "pads", "trees", "fences", "pools"):
            out[key] = [r for r in out[key] if not fw.blocked(r[0], r[1])]
    if wet:  # nothing is built in the water
        for key in ("lots", "parking", "stalls", "pads", "trees", "fences", "pools"):
            out[key] = [r for r in out[key] if not wet(r[0], r[1])]
        out["streets"] = [s for s in out["streets"] if not any(wet(p[0], p[1]) for p in s)]
    if city.get("airports"):  # FAA land-use compatibility: no homes in an airport's noise zone, industry instead
        from .. import airport
        noisy = lambda x, y: any(airport.in_noise(a, x, y) or airport.inside(a, x, y, 2000) for a in city["airports"])
        kept = []
        for r in out["lots"]:
            if not noisy(r[0], r[1]):
                kept.append(r)
            elif not (len(r) > 7 and r[7] in ("house", "apartment", "mansion")) and not (len(r) > 8 and r[8] is True):
                kept.append(r[:7] + ["warehouse", False])  # a plain parcel: logistics and light industry
        out["lots"] = kept
        out["streets"] = [s for s in out["streets"] if not any(noisy(p[0], p[1]) for p in s)]
    out["grounds"], out["plan"], out["water"], out["waterfront"] = [], None, [], None
    ring_of, industrial = zoning(city, o)
    if out["rezoned"]:  # the planner and the waterfront see a rezoned block as what it now is
        ring0, ind0 = ring_of, industrial
        zone_at = lambda x, y: next((z for poly, z in out["rezoned"] if _inside(poly, x, y)), "")
        ring_of = lambda x, y: {"downtown": "core", "normal": "normal", "housing": "housing"}.get(zone_at(x, y)) or ring0(x, y)
        industrial = lambda x, y: (lambda z: z == "industrial" if z else ind0(x, y))(zone_at(x, y))
    if o["waterfront"] == "yes" and water and water.get("sea") is not None and ground:
        # waterfront first: the shore's quays, marinas, beaches and ports take their land before the services
        from .. import waterfront
        pop = round(len(out["lots"]) * 9)  # a rough count for the port / ferry thresholds; urban.plan counts properly
        roads = [(*nodes[a][:2], *nodes[b][:2], HALF.get(k, 500.0)) for a, b, k, *_ in edges if a in nodes and b in nodes]
        roads += [(*p[:2], *q[:2], 400.0) for s in out["streets"] for p, q in zip(s, s[1:])]
        wf = waterfront.plan(city, water, out["lots"], ring_of, industrial, ground, nodes, pop, roads)
        out["lots"], out["water"] = wf["lots"], wf["rows"]
        # (a lane joins a street on the ground: one joined to a bridge's deck ran 12 m up to it)
        on_ground = [(*nodes[a][:2], *nodes[b][:2], HALF.get(k, 500.0)) for a, b, k, *via in edges
                     if a in nodes and b in nodes and not (via and via[0])]
        on_ground += [(*p[:2], *q[:2], 400.0) for s in out["streets"] for p, q in zip(s, s[1:])]
        out["streets"] += [s for s in home_lanes(out["lots"], on_ground) if not (wet and any(wet(*p[:2]) for p in s))]
        out["waterfront"] = {k: wf[k] for k in ("segments", "developments", "shoreline_m", "made_m", "report", "shipyards")}
    # the blocks' own trees keep off what is not a block's ground: a made shore, a shipyard, an airfield, a keep zone
    from .. import airport, shipyard, waterfront
    look = waterfront.Made([city]) if city.get("shores") else None
    yards = (city.get("shipyards") or []) + (city.get("shipyards_near") or [])
    if look or yards or city.get("airports") or clear:
        out["trees"] = [t for t in out["trees"] if not (
            (look and look.shelf(t[0], t[1], 300)) or in_keep(clear, t[0], t[1], 2000)
            or any(shipyard.reserved(y, t[0], t[1], 500) for y in yards if y.get("z") is not None)
            or any(airport.inside(a, t[0], t[1], 2000) for a in city.get("airports") or []))]
    if o["planner"] == "yes" and out["lots"]:
        from .. import urban
        barriers = [(p, q) for ln in fw.lines for p, q in zip(ln["points"], ln["points"][1:])]
        got = urban.plan(out["lots"], nodes, edges, ring_of, ground, [tuple(i["at"]) for i in fw.interchanges],
                         keep, (city["x"], city["y"]), city["radius"], industrial, barriers)
        out["lots"] = got["lots"]
        out["parking"] += got["parking"]
        out["stalls"] += got["stalls"]
        out["grounds"], out["plan"] = list(got["grounds"]), got["plan"]
        # the drives into the sites' car parks: private streets like a complex's own
        out["streets"] += [s for s in got.get("streets") or []
                           if not any(fw.blocked(p[0], p[1]) or (wet and wet(p[0], p[1])) for p in s)]
        if fw.lines and out["plan"]:
            # a site the planner merged from lots each clear of the freeway can still have its middle in the
            # freeway's corridor (a power plant did): that site is not built, its grounds go with it
            lost = [f for f in out["plan"]["facilities"] if fw.blocked(f["x"], f["y"])]
            if lost:
                out["plan"]["facilities"] = [f for f in out["plan"]["facilities"] if f not in lost]
                for f in lost:
                    out["plan"]["counts"][f["type"]] = out["plan"]["counts"].get(f["type"], 1) - 1
                out["lots"] = [r for r in out["lots"] if not (len(r) > 8 and r[8] is True and fw.blocked(r[0], r[1]))]
                out["grounds"] = [g for g in out["grounds"] if not fw.blocked(g[0], g[1])]
    # a port's road comes into the city across land the blocks were cut without it: no lot, tree or car park on it
    for line in city.get("port_roads") or []:
        segs = list(zip(line, line[1:]))
        near = lambda x, y, m: any(_dist_seg((x, y), p, q) < m for p, q in segs)
        out["lots"] = [r for r in out["lots"] if not near(r[0], r[1], HALF[PORT_ROAD] + 0.5 * max(r[3], r[4]) + 300)]
        for key in ("trees", "stalls", "pads", "fences", "pools"):
            out[key] = [r for r in out[key] if not near(r[0], r[1], HALF[PORT_ROAD] + 400)]
        out["parking"] = [r for r in out["parking"] if not near(r[0], r[1], HALF[PORT_ROAD] + 50 * max(r[3], r[4]))]
    # what the park blocks hold (paths, benches, playgrounds, a pond, playing fields): grounds like a planned site's
    out["grounds"] += [g for g in park_grounds if not fw.blocked(g[0], g[1]) and not (wet and wet(g[0], g[1]))
                       and not in_keep(clear, g[0], g[1], 2000)]
    # a stall is painted on a car park: where the slab was not built (in the water, under a freeway, in a keep
    # zone) its stalls go with it, and none is left as white lines on the grass
    slabs = [(s[0], s[1], math.cos(math.radians(s[2])), math.sin(math.radians(s[2])), s[3] * 50 + 60, s[4] * 50 + 60) for s in out["parking"]]
    by_cell: dict = {}
    for s in slabs:
        r = math.hypot(s[4], s[5])
        for gx in range(int((s[0] - r) // 20000), int((s[0] + r) // 20000) + 1):
            for gy in range(int((s[1] - r) // 20000), int((s[1] + r) // 20000) + 1):
                by_cell.setdefault((gx, gy), []).append(s)
    out["stalls"] = [t for t in out["stalls"]
                     if any(abs((t[0] - x) * c + (t[1] - y) * s_) < a and abs(-(t[0] - x) * s_ + (t[1] - y) * c) < b
                            for x, y, c, s_, a, b in by_cell.get((int(t[0] // 20000), int(t[1] // 20000)), ()))]
    return out


def city_lots(city: dict, opts: dict | None = None) -> list:
    return city_layout(city, opts)["lots"]


HOME_LOT = [1700, 2400]  # a home on the water (waterfront.HOUSE_LOT)
LANE_OUT_CM = 300.0  # its lane runs this far out from the lot's front edge
LANE_REACH_CM = 15000.0  # ... and joins a street no further than this from the row's end


def home_lanes(lots: list, roads: list) -> list:
    """Homes on the water face the land, their lawns down to the water, and every one can be driven to. They
    were laid facing the lake, their drives ran into the lawn, and no street came near them: Valley's Northgate
    and Riverton had 17 houses with no road (Nov 17 2026). Each home is turned round (in place in `lots`); a
    row with no street at its door gets a private lane along the fronts, joined at its nearer end to the nearest
    street. roads: [(x0, y0, x1, y1, half width)]. Returns the lanes, [[x, y], ...] each (private streets).
    A row with no street within LANE_REACH_CM keeps its lane unjoined here: lot_drives reaches further for it."""
    nearest = lambda p: _nearest_road(p, roads)  # noqa: E731
    rows, lanes = [], []
    for r in lots:
        if r[7] != "house" or [round(r[3]), round(r[4])] != HOME_LOT:
            continue
        nx, ny = (r[5] - r[0]) / (r[4] / 2), (r[6] - r[1]) / (r[4] / 2)  # toward the water (the old front)
        r[2] = round((r[2] + 180.0 + 180.0) % 360.0 - 180.0, 1)
        r[5], r[6] = round(r[0] - nx * r[4] / 2), round(r[1] - ny * r[4] / 2)
        at = (r[5] - nx * LANE_OUT_CM, r[6] - ny * LANE_OUT_CM)
        if nearest((r[5], r[6]))[0] < 600.0:
            continue  # a street at its door already
        if rows and math.dist(rows[-1][-1], at) < 2.5 * HOME_LOT[0]:
            rows[-1].append(at)
        else:
            rows.append([at])
    for row in rows:
        (da, qa), (db, qb) = nearest(row[0]), nearest(row[-1])
        if min(da, db) < LANE_REACH_CM:
            row = [qa] + row if da <= db else row + [qb]
        if len(row) > 1:
            lanes.append([[round(v) for v in p] for p in row])
    return lanes


def _nearest_road(p, roads: list) -> tuple:
    """(distance from the nearest street's edge, the point on its centre line) of roads [(x0, y0, x1, y1, half)]."""
    best = (math.inf, None)
    for x0, y0, x1, y1, h in roads:
        dx, dy = x1 - x0, y1 - y0
        t = max(0.0, min(1.0, ((p[0] - x0) * dx + (p[1] - y0) * dy) / ((dx * dx + dy * dy) or 1.0)))
        q = (x0 + dx * t, y0 + dy * t)
        if math.dist(p, q) - h < best[0]:
            best = (math.dist(p, q) - h, q)
    return best


def ground_roads(actors: list) -> list:
    """[(x0, y0, x1, y1, half width)]: every street of these spline actors that lies on the ground (no bridge, ramp,
    freeway or runway): what a private drive may join."""
    out = []
    for a in actors:
        at = {p["i"]: p for p in a["points"]}
        for s in a["segments"]:
            p, q, m = at.get(s["a"]), at.get(s["b"]), str(s.get("mesh") or "")
            if p and q and m.startswith("SM_Road_") and "Freeway" not in m and \
                    all(x.get("raise", True) for x in (s, p, q)):
                out.append((p["x"], p["y"], q["x"], q["y"], max(p["w"], q["w"])))
    return out


DOOR_CM = 2500.0  # a street this close to a lot's front is at its door
DRIVE_REACH_CM = 25000.0  # ... else a private drive runs to the nearest street this close
DRIVE_SHORT_CM = 300.0  # a drive's end, this far short of its street's middle (still well inside its reach)
ACCESS_REACH_CM = 60000.0  # lanes that reach no street (a shipyard's, 373 m out at Valley's Northgate): one access road


def lot_edge(r: list, q) -> tuple:
    """Where a lot's outline meets the line from its middle to q: its edge on the way to that street."""
    L = math.dist(r[:2], q) or 1.0
    ux, uy = (q[0] - r[0]) / L, (q[1] - r[1]) / L
    nl = math.dist(r[:2], r[5:7]) or 1.0
    nx, ny = (r[5] - r[0]) / nl, (r[6] - r[1]) / nl
    along, across = abs(ux * nx + uy * ny), abs(-ux * ny + uy * nx)
    s = min(r[4] / 2 / along if along > 1e-6 else math.inf, r[3] / 2 / across if across > 1e-6 else math.inf)
    return r[0] + ux * s, r[1] + uy * s


def airport_drives(lots: list, nodes: dict, edges: list) -> None:
    """An airport building no landside road reaches gets a drive in the landside network itself, to its nearest
    junction on the ground: its substation stood 119 m out (Nov 18 2026), and a drive in the city's own actor
    cannot join the airport's (UEFN joins points of one actor only). In place in nodes / edges."""
    flat = [e for e in edges if e[0] in nodes and e[1] in nodes and not str(e[2]).startswith("E")]  # (no ramp)
    lines = [(*nodes[e[0]][:2], *nodes[e[1]][:2], HALF.get(e[2], 500.0)) for e in flat]
    ends = sorted({n for e in flat for n in e[:2]}, key=str)
    for k, r in enumerate(lots):
        if not lines or _nearest_road((r[5], r[6]), lines)[0] < DOOR_CM:
            continue
        n = min(ends, key=lambda m: math.dist(nodes[m][:2], r[:2]))
        if math.dist(nodes[n][:2], r[:2]) <= DRIVE_REACH_CM:
            nodes[("drive", k)] = list(lot_edge(r, nodes[n][:2]))
            edges.append([("drive", k), n, "PR"])


STEP_CM = 150.0  # two splines meeting in one spot more than this apart in height: a step
APART_MESHES = ("SM_Air_", "SM_Water_")  # runways, taxiways, canals, docks: their own pieces by design


def network_report(actors: dict) -> dict:
    """The generated road network checked as a whole: {ok, pieces, sizes, loose: [[x, y, actor]], steps:
    [[x, y, metres]], stacked: [[x, y, actor, actor]]}. One piece (every point reached from every other; points of
    two actors in one spot meet), no height step where splines meet, and no road drawn on top of another (one street
    laid twice, Valley's airport road, Nov 2026). Air and water splines may stand apart.
    ponytail: stacked = a segment's middle on another's centre line, parallel; a grid of 100 m cells keeps it fast."""
    par: dict = {}

    def top(k):
        while par.setdefault(k, k) != k:
            par[k] = par[par[k]]
            k = par[k]
        return k
    spot: dict = {}
    pos, meshes = {}, {}
    lines = []
    for label, a in actors.items():
        P = {p["i"]: p for p in a.get("points") or []}
        for p in P.values():
            k = (label, p["i"])
            pos[k] = p
            spot.setdefault((round(p["x"] / 5), round(p["y"] / 5)), []).append(k)
        for s in a.get("segments") or []:
            if s["a"] in P and s["b"] in P:
                par[top((label, s["a"]))] = top((label, s["b"]))
                m = str(s.get("mesh") or "")
                for i in (s["a"], s["b"]):
                    meshes.setdefault((label, i), set()).add(m)
                if m.startswith(("SM_Road_", "SM_Path_PrivateStreet")):
                    lines.append((label, P[s["a"]], P[s["b"]], s))
    steps = []
    for ks in spot.values():
        for k in ks[1:]:
            par[top(k)] = top(ks[0])
        z = [pos[k]["z"] for k in ks]
        if max(z) - min(z) > STEP_CM:
            steps.append([round(pos[ks[0]]["x"]), round(pos[ks[0]]["y"]), round((max(z) - min(z)) / 100, 1)])
    groups: dict = {}
    for k in pos:
        groups.setdefault(top(k), []).append(k)
    apart = lambda ks: all(m.startswith(APART_MESHES) or not m for k in ks for m in meshes.get(k, {""}))  # noqa: E731
    parts = sorted((g for g in groups.values() if not apart(g)), key=len, reverse=True)
    loose = [[round(pos[g[0]]["x"]), round(pos[g[0]]["y"]), g[0][0]] for g in parts[1:]]
    cell: dict = {}
    for n, (label, p, q, s) in enumerate(lines):
        for f in (0.0, 0.5, 1.0):
            x, y = p["x"] + (q["x"] - p["x"]) * f, p["y"] + (q["y"] - p["y"]) * f
            cell.setdefault((int(x // 10000), int(y // 10000)), set()).add(n)
    stacked = []
    for n, (label, p, q, s) in enumerate(lines):
        L = math.hypot(q["x"] - p["x"], q["y"] - p["y"])
        if L < 300:
            continue
        mx, my, mz = (p["x"] + q["x"]) / 2, (p["y"] + q["y"]) / 2, (p["z"] + q["z"]) / 2
        for m in cell.get((int(mx // 10000), int(my // 10000)), ()):
            l2, p2, q2, s2 = lines[m]
            if m == n or {(label, s["a"]), (label, s["b"])} & {(l2, s2["a"]), (l2, s2["b"])}:
                continue
            L2 = math.hypot(q2["x"] - p2["x"], q2["y"] - p2["y"])
            if L2 < 300 or abs(((q["x"] - p["x"]) * (q2["x"] - p2["x"]) + (q["y"] - p["y"]) * (q2["y"] - p2["y"]))
                               / (L * L2)) < 0.9:
                continue
            t = max(0.0, min(1.0, ((mx - p2["x"]) * (q2["x"] - p2["x"]) + (my - p2["y"]) * (q2["y"] - p2["y"])) / L2 ** 2))
            fx, fy = p2["x"] + (q2["x"] - p2["x"]) * t, p2["y"] + (q2["y"] - p2["y"]) * t
            fz = p2["z"] + (q2["z"] - p2["z"]) * t
            if 0.0 < t < 1.0 and math.hypot(mx - fx, my - fy) < 0.5 * min(p["w"], p2["w"]) and abs(mz - fz) < 300:
                stacked.append([round(mx), round(my), label, l2])
                break
    return {"ok": not loose and not steps and not stacked, "pieces": len(parts), "sizes": [len(g) for g in parts[:5]],
            "loose": loose[:20], "steps": steps[:20], "stacked": stacked[:20]}


def lot_drives(lots: list, streets: list, blocks: list, roads: list, wet=None) -> list:
    """Every lot and every private lane can be driven to: a lot outside the street blocks (a waterfront restaurant
    or hotel, a shipyard's sheds, an airport's substation) with no street at its door, and a lane with neither end
    on a street, get a private drive to the nearest street on the ground, over no water. Valley had 31 such
    buildings 30 to 180 m from any road, and a lane of two homes on its own (Nov 18 2026). roads: ground_roads.
    Returns the drives, [[x, y], [x, y]] each (private streets).
    ponytail: a drive runs straight, past whatever stands between; route it round lots if one is seen crossing."""
    every = roads + [(*p[:2], *q[:2], HALF["PR"]) for s in streets for p, q in zip(s, s[1:])]
    polys = [b[4] for b in blocks if len(b) > 4 and b[4]]
    out = []

    def drive(a, q, reach=DRIVE_REACH_CM):
        n = max(1, int(math.dist(a, q) // 500))
        if math.dist(a, q) <= reach and not (wet and any(
                wet(a[0] + (q[0] - a[0]) * k / n, a[1] + (q[1] - a[1]) * k / n) for k in range(n + 1))):
            # it ends DRIVE_SHORT_CM short of the street's middle, on its own line: drives from lots round one street
            # end met in one point, became one lane through it and were never joined (3 at Valley's Riverton)
            f = min(DRIVE_SHORT_CM, math.dist(a, q) / 2) / (math.dist(a, q) or 1.0)
            q = (q[0] + (a[0] - q[0]) * f, q[1] + (a[1] - q[1]) * f)
            out.append([[round(a[0]), round(a[1])], [round(q[0]), round(q[1])]])
    # the lots nearest a street first, each to the nearest street OR drive made so far: drives branch off one another
    # like a tree. Each straight to the street, a shipyard's sheds side by side ran four drives on top of each
    # other into one spot (Valley's Northgate, Nov 18 2026)
    need = [r for r in lots if not any(_inside(poly, r[0], r[1]) for poly in polys)
            and _nearest_road((r[5], r[6]), every)[0] >= DOOR_CM]  # (not a block's lot, nor one with a street at its door)
    for r in sorted(need, key=lambda r: _nearest_road((r[0], r[1]), every)[0]):
        q = _nearest_road((r[0], r[1]), every + [(*d[0], *d[1], HALF["PR"]) for d in out])[1]
        if q is not None:
            drive(lot_edge(r, q), q)  # from the lot's edge, the way to that street
    # lanes and drives that touch are one group: a group with no end on a street gets one access road, from its
    # point nearest a street
    lanes = [s for s in streets + out if len(s) > 1]
    group = list(range(len(lanes)))

    def top(k):
        while group[k] != k:
            group[k] = group[group[k]]
            k = group[k]
        return k
    for i, a in enumerate(lanes):
        for j in range(i + 1, len(lanes)):
            b = lanes[j]
            if any(_dist_seg(e[:2], p[:2], q[:2]) < 500 for x, y in ((a, b), (b, a)) for e in (x[0], x[-1])
                   for p, q in zip(y, y[1:])):
                group[top(i)] = top(j)
    parts: dict = {}
    for k, s in enumerate(lanes):
        parts.setdefault(top(k), []).append(s)
    for part in parts.values():
        if any(_nearest_road(e[:2], roads)[0] < JOIN_REACH_CM for s in part for e in (s[0], s[-1])):
            continue
        best = min(((_nearest_road(p[:2], roads), p) for s in part for p in s), key=lambda t: t[0][0], default=None)
        if best and best[0][1] is not None:
            drive(tuple(best[1][:2]), best[0][1], ACCESS_REACH_CM)
    return out


FRONT_LOT_D_CM = 3000.0  # a lot along a street with open land behind it: 16 m of frontage, 30 m deep
FRONT_END_CM = 1400.0  # ... none this close to the street's end at a junction (the corner stays clear)


def frontage_lots(city: dict, o: dict, nodes: dict, edges: list, fronted: set, wet=None, clear: list | None = None) -> list:
    """Lots along every side of a street that no block lies on. Only land ringed by streets is a block, so the
    land behind a city's outermost streets, between two streets that never meet again and on both sides of a dead
    end had no lot at all: a street ran through bare ground with a lamp every 30 m (in the owner's Valley a
    quarter of each city, Nov 12 2026). Each such side now gets a row of lots facing it, as city_layout's:
    inside the city's edge, off the water, off every other street, a shipyard, an airport and a keep zone
    (`clear`), and never over a lot of the row round the corner. fronted: (node, next node) of every street
    side a block lies on (its land is on the left going from the one to the other)."""
    from .. import airport, parcels, shipyard
    yards = (city.get("shipyards") or []) + (city.get("shipyards_near") or [])
    fields = city.get("airports") or []
    near_cut = _near_cuts(city) if city.get("fw_near") else None
    cell = 6000.0
    road: dict = {}
    for k, e in enumerate(edges):
        line = [nodes[e[0]][:2]] + [v[:2] for v in (e[3] if len(e) > 3 and e[3] else [])] + [nodes[e[1]][:2]]
        for p, q in zip(line, line[1:]):
            n = max(1, int(math.dist(p, q) // (cell / 2)))
            for i in range(n + 1):
                key = (int((p[0] + (q[0] - p[0]) * i / n) // cell), int((p[1] + (q[1] - p[1]) * i / n) // cell))
                road.setdefault(key, set()).add((k, tuple(p), tuple(q), HALF.get(e[2], 500.0)))

    def on_road(x, y, own) -> bool:
        gx, gy = int(x // cell), int(y // cell)
        return any(k != own and _dist_seg((x, y), p, q) < half + 0.5 * parcels.SIDEWALK_CM
                   for i in (-1, 0, 1) for j in (-1, 0, 1) for k, p, q, half in road.get((gx + i, gy + j), ()))
    deg: dict = {}
    for e in edges:
        deg[e[0]] = deg.get(e[0], 0) + 1
        deg[e[1]] = deg.get(e[1], 0) + 1
    made: dict = {}  # the rows laid so far, by 60 m cell: (x, y, ux, uy)
    W, D = LOT_W_CM, FRONT_LOT_D_CM
    out = []
    for k, e in enumerate(edges):
        if (len(e) > 3 and e[3]) or e[2] == "DT":
            continue  # a bridge, a dirt track
        for a, b in ((e[0], e[1]), (e[1], e[0])):
            if (a, b) in fronted:
                continue
            p, q = nodes[a], nodes[b]
            L = math.dist(p[:2], q[:2])
            if in_keep(clear, p[0], p[1], 2000) or in_keep(clear, q[0], q[1], 2000):
                continue  # (the street itself is not laid there)
            # the corner stays clear at a junction only: where one street just bends (a winding suburb street is a
            # point every 45 m) the lots run on round it. With the gap at every bend a 45 m piece held one lot, and
            # 70 % of the winding streets' frontage stood empty (Nov 20 2026)
            gap = lambda n: FRONT_END_CM if deg[n] > 2 else 100.0 if deg[n] == 2 else 300.0
            m0, m1 = gap(a), gap(b)
            n = int((L - m0 - m1) // W)
            if n < 1:
                continue
            ux, uy = (q[0] - p[0]) / L, (q[1] - p[1]) / L
            nx, ny = -uy, ux  # the open land: on the left
            back = HALF.get(e[2], 500.0) + parcels.SIDEWALK_CM
            yaw = round(math.degrees(math.atan2(-ny, -nx)), 1)  # +X faces the street
            for i in range(n):
                t = m0 + (L - m0 - m1 - n * W) / 2 + (i + 0.5) * W
                fx, fy = p[0] + ux * t + nx * back, p[1] + uy * t + ny * back
                cx, cy = fx + nx * D / 2, fy + ny * D / 2
                spots = [(cx, cy)] + [(fx + ux * s * (W / 2 - 50) + nx * d, fy + uy * s * (W / 2 - 50) + ny * d)
                                      for s in (1, -1) for d in (50.0, D)]
                if zone_share(city, cx, cy) > 1.0 or (city.get("yield_to") and yielded(city, cx, cy)):
                    continue
                if (wet and any(wet(x, y) for x, y in spots)) or in_keep(clear, cx, cy, 2500):
                    continue
                if any(shipyard.reserved(y_, cx, cy, 1500) if y_.get("z") is not None else shipyard.inside(y_, cx, cy, -2000)
                       for y_ in yards) or any(airport.inside(f, cx, cy, 3000) for f in fields):
                    continue
                if any(on_road(x, y, k) for x, y in spots) or (near_cut and near_cut((cx, cy), (cx, cy), D / 2)):
                    continue
                gx, gy = int(cx // cell), int(cy // cell)
                if any(abs((cx - x) * vx + (cy - y) * vy) < (W if abs(ux * vx + uy * vy) > 0.7 else (W + D) / 2) - 60 and
                       abs(-(cx - x) * vy + (cy - y) * vx) < (D if abs(ux * vx + uy * vy) > 0.7 else (W + D) / 2) - 60
                       for i_ in (-1, 0, 1) for j_ in (-1, 0, 1) for x, y, vx, vy in made.get((gx + i_, gy + j_), ())):
                    continue  # over a lot already laid (the row across a narrow strip, or round the corner)
                made.setdefault((gx, gy), []).append((cx, cy, ux, uy))
                out.append([round(cx), round(cy), yaw, round(W), round(D), round(fx), round(fy), "", False])
    return out


def _inside(poly: list, x: float, y: float) -> bool:
    """Point in polygon (even-odd ray cast)."""
    hit = False
    for (ax, ay), (bx, by) in zip(poly, list(poly[1:]) + list(poly[:1])):
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            hit = not hit
    return hit


GROVE_CM = 700.0  # a park block: a tree about every 7 m
COURT_CM = 1000.0  # the planted middle of a block of plain lots: one about every 10 m
COURT_MIN = 4  # ... when there is room for this many


def _court(area: list, lots: list, rnd) -> list:
    """Trees on the land a block's lots leave free ([[x, y]], world cm): a jittered grid inside the block, none
    on a lot or within 3 m of one."""
    rects = [(r[0], r[1], math.cos(math.radians(r[2])), math.sin(math.radians(r[2])), r[4] / 2 + 300, r[3] / 2 + 300) for r in lots]
    xs, ys = [p[0] for p in area], [p[1] for p in area]
    out = []
    y = min(ys) + COURT_CM / 2
    while y < max(ys):
        x = min(xs) + COURT_CM / 2
        while x < max(xs):
            px, py = x + rnd.uniform(-0.35, 0.35) * COURT_CM, y + rnd.uniform(-0.35, 0.35) * COURT_CM
            if _inside(area, px, py) and min(_dist_seg((px, py), a, b) for a, b in zip(area, area[1:] + area[:1])) > 300 and \
                    not any(abs((px - cx) * fx + (py - cy) * fy) < hd and abs(-(px - cx) * fy + (py - cy) * fx) < hw
                            for cx, cy, fx, fy, hd, hw in rects):
                out.append([round(px), round(py)])
            x += COURT_CM
        y += COURT_CM
    return out


def _green_lots(add: dict, share: float, rnd) -> dict:
    """A block's parts with `share` of its lots planted instead of built on: those lots leave 'lots' and their
    ground is filled with trees ('trees'). Only lots a layer would fill (never a footprint a complex planned).
    The lots are taken in one seeded order, so a bigger share keeps every lot a smaller one already planted."""
    free = [k for k, r in enumerate(add["lots"]) if not (len(r) > 8 and r[8] is True)]
    rnd.shuffle(free)
    gone = set(free[:round(share * len(free))])
    if not gone:
        return add
    trees = list(add.get("trees") or [])
    for k in sorted(gone):
        x, y, yaw, w, d = add["lots"][k][:5]
        fx, fy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))  # +X faces the street
        nu, nv = max(1, round((d - 300) / GROVE_CM)), max(1, round((w - 300) / GROVE_CM))
        for i in range(nu):
            for j in range(nv):
                u = ((i + 0.5) / nu - 0.5) * (d - 300) + rnd.uniform(-0.3, 0.3) * GROVE_CM
                v = ((j + 0.5) / nv - 0.5) * (w - 300) + rnd.uniform(-0.3, 0.3) * GROVE_CM
                u, v = max(-d / 2 + 120, min(d / 2 - 120, u)), max(-w / 2 + 120, min(w / 2 - 120, v))
                trees.append([round(x + fx * u - fy * v), round(y + fy * u + fx * v)])
    return {**add, "lots": [r for k, r in enumerate(add["lots"]) if k not in gone], "trees": trees}


def _grove(area: list, rnd) -> list:
    """Trees all over a block ([[x, y]], world cm): a jittered grid inside its polygon."""
    xs, ys = [p[0] for p in area], [p[1] for p in area]
    out = []
    y = min(ys) + GROVE_CM / 2
    while y < max(ys):
        x = min(xs) + GROVE_CM / 2
        while x < max(xs):
            px, py = x + rnd.uniform(-0.35, 0.35) * GROVE_CM, y + rnd.uniform(-0.35, 0.35) * GROVE_CM
            if _inside(area, px, py):
                out.append([round(px), round(py)])
            x += GROVE_CM
        y += GROVE_CM
    return out


PARK_PATH_CM = 300.0  # a park path is this wide ...
PARK_PIECE_CM = 600.0  # ... and laid in pieces this long, each on its own ground
PARK_BENCH_CM = 3000.0  # a bench beside a path about this often


def park_paths(to_world, yaw: float, half_l: float, half_w: float, boxes: list, rnd, inside=None) -> tuple:
    """The paths of a park and the benches along them, in the park's own frame (u along its length, v across;
    to_world(u, v) -> (x, y); yaw: the way u points). A walk round it, one down its length and one across about
    every 140 m, in pieces PARK_PIECE_CM long; none over what stands in it (`boxes` [(u0, u1, v0, v1)]) or
    outside it (`inside(x, y)`). Returns (ground rows [x, y, yaw, sx_m, sy_m, kind, 'park'], the path lines
    [((u, v), (u, v))])."""
    inset = max(600.0, min(1500.0, 0.08 * 2 * min(half_l, half_w)))
    a, b = half_l - inset, half_w - inset
    if a < 1200 or b < 1200:
        return [], []
    v0 = rnd.uniform(-0.25, 0.25) * b
    lines = [((-a, -b), (a, -b)), ((a, -b), (a, b)), ((a, b), (-a, b)), ((-a, b), (-a, -b)), ((-a, v0), (a, v0))]
    n_cross = max(1, int(2 * a // 14000))
    for k in range(n_cross):
        u0 = -a + (k + 0.5 + rnd.uniform(-0.2, 0.2)) * 2 * a / n_cross
        lines.append(((u0, -b), (u0, b)))
    rows = []
    for p, q in lines:
        length = math.dist(p, q)
        n = max(1, math.ceil(length / PARK_PIECE_CM))
        du, dv = (q[0] - p[0]) / length, (q[1] - p[1]) / length
        ang = (yaw + math.degrees(math.atan2(dv, du))) % 360
        since = PARK_BENCH_CM * rnd.random()
        for k in range(n):
            u, v = p[0] + (q[0] - p[0]) * (k + 0.5) / n, p[1] + (q[1] - p[1]) * (k + 0.5) / n
            since += length / n
            if any(u0 - 200 < u < u1 + 200 and w0 - 200 < v < w1 + 200 for u0, u1, w0, w1 in boxes):
                continue
            x, y = to_world(u, v)
            if inside and not inside(x, y):
                continue
            rows.append([round(x), round(y), round(ang, 1), round(length / n / 100 + 0.1, 2), PARK_PATH_CM / 100, "path", "park"])
            if since >= PARK_BENCH_CM:  # a bench at the path's edge, facing it
                side = rnd.choice((1, -1))
                bu, bv = u - dv * side * (PARK_PATH_CM / 2 + 80), v + du * side * (PARK_PATH_CM / 2 + 80)
                bx, by = to_world(bu, bv)
                if not any(u0 < bu < u1 and w0 < bv < w1 for u0, u1, w0, w1 in boxes) and not (inside and not inside(bx, by)):
                    rows.append([round(bx), round(by), round((ang + (180 if side > 0 else 0)) % 360, 1), 1.0, 1.0, "bench", "park"])
                    since = 0.0
    return rows, lines


def _park(area: list, rnd) -> dict:
    """What a park block holds ([[x, y]], world cm): trees in groves with open lawns between them and a row of
    trees round its edge (never the even grid of a plantation), paths and benches (park_paths) and, by its size,
    playgrounds, a pond and playing fields. {'trees': [[x, y]], 'grounds': [[x, y, yaw, sx_m, sy_m, kind,
    'park']]}: the grounds are laid like a planned site's (pcg_layers.GROUNDS)."""
    from .. import parcels
    ox, oy, ux, uy, L, W = parcels._obb(area)
    to_world = lambda u, v: (ox + ux * u - uy * v, oy + uy * u + ux * v)
    yaw = math.degrees(math.atan2(uy, ux))
    ha = abs(parcels._area(area)) / 1e8
    boxes, grounds = [], []

    def put(kind, su, sv):
        """A rectangle su x sv somewhere in the park, clear of what is there already."""
        if L < su + 3000 or W < sv + 3000:
            return
        for _ in range(24):
            u, v = rnd.uniform(-L / 2 + su / 2 + 1500, L / 2 - su / 2 - 1500), rnd.uniform(-W / 2 + sv / 2 + 1500, W / 2 - sv / 2 - 1500)
            box = (u - su / 2, u + su / 2, v - sv / 2, v + sv / 2)
            if any(box[0] < b[1] + 800 and b[0] < box[1] + 800 and box[2] < b[3] + 800 and b[2] < box[3] + 800 for b in boxes):
                continue
            if not all(_inside(area, *to_world(p, q)) for p in (box[0], box[1]) for q in (box[2], box[3])):
                continue
            boxes.append(box)
            grounds.append([*map(round, to_world(u, v)), round(yaw, 1), round(su / 100, 2), round(sv / 100, 2), kind, "park"])
            return
    for _ in range(2 if ha >= 9 else 1 if ha >= 4 else 0):
        put("field", 10800.0, 7200.0)  # a full-size pitch (100 x 64 m) with its run-off (pcg_layers.fit_pitch)
    if ha >= 1.2:
        put("pond", min(15000.0, L * rnd.uniform(0.2, 0.3)), min(9000.0, W * rnd.uniform(0.25, 0.4)))
    for _ in range(1 if ha < 2 else 2 if ha < 6 else 3):
        put("playground", rnd.uniform(1600.0, 2400.0), rnd.uniform(1200.0, 1800.0))
    rows, lines = park_paths(to_world, yaw, L / 2, W / 2, boxes, rnd, lambda x, y: _inside(area, x, y))
    grounds += rows
    tau = 2 * math.pi
    waves = [(rnd.uniform(2500.0, 5500.0), rnd.uniform(0, tau), rnd.uniform(0, tau)) for _ in range(3)]
    inset = max(600.0, min(1500.0, 0.08 * min(L, W)))
    trees = []
    for x, y in _grove(area, rnd):
        u, v = (x - ox) * ux + (y - oy) * uy, -(x - ox) * uy + (y - oy) * ux
        if any(u0 - 300 < u < u1 + 300 and w0 - 300 < v < w1 + 300 for u0, u1, w0, w1 in boxes):
            continue
        if any(_dist_seg((u, v), p, q) < PARK_PATH_CM / 2 + 250 for p, q in lines):
            continue
        belt = abs(u) > L / 2 - inset or abs(v) > W / 2 - inset  # the trees round its edge, outside the walk
        if belt or sum(math.sin(x / lam + p) * math.sin(y / lam * 1.3 + q) for lam, p, q in waves) > 0.0:
            trees.append([x, y])
    return {"trees": trees, "grounds": grounds}


def gate(nodes: dict, city: dict, toward: tuple):
    """The city's street node furthest out in the direction of another city."""
    dx, dy = toward[0] - city["x"], toward[1] - city["y"]
    L = math.hypot(dx, dy) or 1.0
    streets = [n for n in nodes if isinstance(n[0], int)]  # never another highway's stub
    return max(streets, key=lambda n: ((nodes[n][0] - city["x"]) * dx + (nodes[n][1] - city["y"]) * dy) / L)



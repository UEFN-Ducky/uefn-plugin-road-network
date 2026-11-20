from __future__ import annotations

def generate(cities: list, ground, folder: str, seed: int = 1, opts: dict | None = None, keep: list | None = None,
             wet=None, bridges: list | None = None, airports: list | None = None,
             airport_builds: dict | None = None, terrain: dict | None = None) -> dict:
    """{label: {points, segments}}: one '<City> Roads' actor per city and one 'Highways' actor.
    A highway ends 40 m outside its city's gate street, where the city's own stub road ends; there it
    meets the city through the chosen interchange (direct, a trumpet loop, a cloverleaf piece, or a
    stack: an elevated deck on ramps), and it bridges over every street it crosses on the way.
    terrain: an engine terrain() with maps.road_cost: each highway then takes the cheapest way (cost_route),
    through the passes, instead of its one gentle curve."""
    o = options(opts)
    graphs = [city_graph(c, o, wet, bridges) for c in cities]
    # a Blender interchange's cross road ends where the grid's next street would be: in an organic or mixed city
    # there was none and the stub stopped 20-100 m short, its city cut off from its freeway (Nov 18 2026). A street
    # of the city's own now runs from that end to its nearest junction
    for c, (nodes, edges) in zip(cities, graphs):
        own = [n for n in nodes if isinstance(n, tuple) and len(n) == 2 and isinstance(n[0], int)]
        for k, ic in enumerate(freeway_plan(c, o).interchanges):
            for j, xy in enumerate((ic.get("model") or {}).get("cross") or ()):
                near = min(own, key=lambda n: math.dist(nodes[n][:2], xy), default=None)
                if near is not None and 100 < math.dist(nodes[near][:2], xy) < 40000:
                    nodes[("icx", k, j)] = list(xy)
                    edges.append([("icx", k, j), near, ic.get("cross_road") or o["core_roads"]])
    rnd = random.Random(seed)
    hw_nodes, hw_edges, pieces = {}, [], {}
    streets = [(n[a][:2], n[b][:2]) for n, e in graphs for a, b, *_ in e]
    gates = o["gate_interchanges"] == "yes"
    tips = []  # per city: its freeways' open ends [x, y, the way it was heading there (unit), in the water]
    for c in cities:
        fwp = freeway_plan(c, o)
        runs = [ln["points"] for ln in fwp.lines if ln["cls"] == "freeway" and len(ln["points"]) > 1]
        socks = [s for ic in fwp.interchanges for s in (ic.get("model") or {}).get("sockets") or ()]
        # (a freeway cut at a Blender interchange's socket goes on through the model: no highway starts there)
        every_tip = [(p, q) for ps in runs for p, q in ((ps[0], ps[1]), (ps[-1], ps[-2]))
                     if not any(math.dist(p[:2], s) < 100 for s in socks)]
        mine = []
        for p, q in every_tip:
            L = math.dist(p[:2], q[:2]) or 1.0
            if sum(1 for u, _v in every_tip if math.dist(u[:2], p[:2]) < 200) == 1 and not in_keep(keep, p[0], p[1], 6000):
                hx, hy = (p[0] - q[0]) / L, (p[1] - q[1]) / L
                # (in the water, or ended at a shore with the water right ahead: no highway carries on from it)
                mine.append([p[0], p[1], hx, hy, bool(wet and any(wet(p[0] + hx * d, p[1] + hy * d) for d in (0.0, 10000.0, 20000.0, 30000.0)))])
        tips.append(mine)
    # cities that lie close together are joined street to street, the closer the more; each lays its own half
    joined: set = set()
    for c in cities:
        c.pop("joins", None)
    join_kind = CITY_STREET.get(o["neighbourhood_roads"], o["neighbourhood_roads"])
    join_rows = joins(cities, graphs, o, wet, keep, ground)
    for k, (i, j, ni, nj, mid) in enumerate(join_rows):
        joined.add((i, j))
        for me, other, node in ((i, j, ni), (j, i, nj)):
            nodes, edges = graphs[me]
            end = ("join", other, k)
            nodes[end] = list(mid)
            edges.append([node, end, join_kind])
            cities[me].setdefault("joins", []).append([round(nodes[node][0]), round(nodes[node][1]), mid[0], mid[1]])
    for a, b in links(cities):
        if (min(a, b), max(a, b)) in joined:
            continue  # one town to the eye: its streets join them, no highway runs between
        ends, leads = [], []
        for me, other in ((a, b), (b, a)):
            cx, cy, ox, oy = cities[me]["x"], cities[me]["y"], cities[other]["x"], cities[other]["y"]
            D = math.hypot(ox - cx, oy - cy) or 1.0
            # (an end on the city's far side would send the highway back across the city: only ends within about
            # 100 degrees of the way to the other city)
            toward = [] if gates else [t for t in tips[me] if not t[4] and (t[0] - cx) * (ox - cx) + (t[1] - cy) * (oy - cy)
                                       > -0.2 * D * math.hypot(t[0] - cx, t[1] - cy)]
            if toward:  # the highway IS the city's freeway from here on: it starts where that ends, heading the same way
                tip = min(toward, key=lambda t: math.hypot(t[0] - ox, t[1] - oy))
                tips[me].remove(tip)
                ends.append([tip[0], tip[1]])
                leads.append(tip)
                continue
            leads.append(None)
            nodes, edges = graphs[me]
            g = gate(nodes, cities[me], (cities[other]["x"], cities[other]["y"]))
            gx, gy = nodes[g]
            dx, dy = cities[other]["x"] - gx, cities[other]["y"] - gy
            L = math.hypot(dx, dy) or 1.0
            stub = ("gate", other)
            nodes[stub] = [gx + dx / L * 4000, gy + dy / L * 4000]
            edges.append([g, stub, o["highway"]])
            ends.append(nodes[stub])
        (x0, y0), (x1, y1) = ends
        L = math.hypot(x1 - x0, y1 - y0)
        steps = max(1, int(L // 20000))
        nx, ny = -(y1 - y0) / (L or 1), (x1 - x0) / (L or 1)
        bend = rnd.uniform(-1, 1) * o["highway_curve"] * L  # one gentle curve per highway
        via = [[x0 + (x1 - x0) * t + nx * bend * math.sin(math.pi * t), y0 + (y1 - y0) * t + ny * bend * math.sin(math.pi * t)]
               for t in (k / steps for k in range(1, steps))]
        if terrain and (terrain.get("maps") or {}).get("road_cost"):
            routed = cost_route(terrain, ends[0], ends[1])
            if routed is None:  # no way under ROUTE_MAX_GRADE: the plain curve, and the city says so
                cities[a].setdefault("unrouted", []).append(cities[b].get("name"))
            else:
                via = routed
        box = cities[a].get("map")
        if box:  # its curve stays on the map
            via = [[min(max(v[0], box[0] + 5000), box[2] - 5000), min(max(v[1], box[1] + 5000), box[3] - 5000)] for v in via]
        if keep:  # a highway goes round a keep zone, never through it
            line, dense = [ends[0]] + via + [ends[1]], []
            for p, q in zip(line, line[1:]):
                n = max(1, int(math.dist(p, q) // 4000))
                dense += [[p[0] + (q[0] - p[0]) * i / n, p[1] + (q[1] - p[1]) * i / n] for i in range(n)]
            dense = dense[1:]
            if any(in_keep(keep, *v, 6000) for v in dense):
                via = [push_out(keep, v[0], v[1], 8000) for v in dense][::2]
        for k, tip in enumerate(leads):  # the freeway's own heading carries on 200 m, then swings to the other END
            leads[k] = []  # (not the other city's centre: its gate may lie 700 m to one side, and the road doubled back)
            if tip:
                tx, ty = ends[1 - k][0] - tip[0], ends[1 - k][1] - tip[1]
                tl = math.hypot(tx, ty) or 1.0
                cos = (tip[2] * tx + tip[3] * ty) / tl
                if cos > -0.3:
                    leads[k] = [[tip[0] + tip[2] * 20000, tip[1] + tip[3] * 20000]]
                    if cos < 0.8:  # (halfway between its heading and the way on from that first point: two even bends)
                        tx, ty = ends[1 - k][0] - leads[k][0][0], ends[1 - k][1] - leads[k][0][1]
                        tl = math.hypot(tx, ty) or 1.0
                        bx, by = tip[2] + tx / tl, tip[3] + ty / tl
                        bl = math.hypot(bx, by) or 1.0
                        leads[k].append([leads[k][0][0] + bx / bl * 30000, leads[k][0][1] + by / bl * 30000])
        via = [v for v in via if all(math.dist(v[:2], e) > 60000 for e, ld in zip(ends, leads) if ld)]
        via = leads[0] + via + leads[1][::-1]
        for field in airports or []:  # ... and round an airport, never through its terminal and car park
            from .. import airport
            via = [v for v in via if not airport.inside(field, v[0], v[1], 10000)]
            line, went = [ends[0]] + via + [ends[1]], []
            for p, q in zip(line, line[1:]):
                went += airport.around(field, p, q) + [q]
            via = [[v[0], v[1]] for v in went[:-1]]
        if o["overpasses"] == "yes":
            via, _ = overpass_via([ends[0]] + via + [ends[1]], streets, o["highway"])
        if gates and o["interchange"] == "stack" and L > 70000:  # rise onto an elevated deck into each gate
            line = [ends[0]] + [v[:2] for v in via] + [ends[1]]
            heads = []
            for end, nxt in ((ends[0], line[1]), (ends[1], line[-2])):
                ux, uy = nxt[0] - end[0], nxt[1] - end[1]
                ul = math.hypot(ux, uy) or 1.0
                heads.append(lambda d, h, k, end=end, ux=ux / ul, uy=uy / ul: [end[0] + ux * d, end[1] + uy * d, h, k])
            hw = o["highway"]
            near = [heads[0](1500, 0.0, "ER"), heads[0](6000, 600.0, "ED"), heads[0](14000, 600.0, "ER"),
                    heads[0](22000, 0.0, hw)]
            far = [heads[1](22000, 0.0, "ER"), heads[1](14000, 600.0, "ED"), heads[1](6000, 600.0, "ER"),
                   heads[1](1500, 0.0, hw)]
            via = near + [v for v in via if math.dist(v[:2], ends[0]) > 26000 and math.dist(v[:2], ends[1]) > 26000] + far
        if gates and o["interchange"] in ("cloverleaf", "trumpet", "diamond") and L > 90000:
            # real ramps and a bridge at each city end, all from our ramp / bridge / road pieces
            line = [ends[0]] + [v[:2] for v in via] + [ends[1]]
            heads = []
            for k, (end, nxt) in enumerate(((ends[0], line[1]), (ends[1], line[-2]))):
                profile, inodes, iedges, cross = interchange(o["interchange"], end, nxt, ("ic", a, b, k), o["highway"])
                hw_nodes.update(inodes)
                hw_edges.extend(iedges)
                # the cross road is a city street: both ends join the city's nearest streets
                cnodes, cedges = graphs[(a, b)[k]]
                city_nodes = [n for n in cnodes if isinstance(n[0], int)]  # not `streets`: that one is the overpass list
                ends_x = [("ic", a, b, k, "x0"), ("ic", a, b, k, "x1")]
                cnodes[ends_x[0]], cnodes[ends_x[1]] = cross
                cedges.append([ends_x[0], ends_x[1], "4L"])
                for e in ends_x:
                    if city_nodes:
                        near = min(city_nodes, key=lambda n: math.dist(cnodes[n][:2], cnodes[e][:2]))
                        cedges.append([e, near, o["neighbourhood_roads"]])
                ux, uy = nxt[0] - end[0], nxt[1] - end[1]
                ul = math.hypot(ux, uy) or 1.0
                heads.append([[end[0] + ux / ul * d, end[1] + uy / ul * d, up, None] for d, up in profile])
            clear = 32000  # the highway's own via points give way to the interchange profile
            via = heads[0] + [v for v in via if math.dist(v[:2], ends[0]) > clear and math.dist(v[:2], ends[1]) > clear] + heads[1][::-1]
            # each segment's piece from the lift at its two ends: deck, ramp, or plain highway
            lifts = [0.0] + [v[2] if len(v) > 2 else 0.0 for v in via] + [0.0]
            for k, v in enumerate(via):
                if len(v) > 3 and v[3] is None:
                    up, nxt_up = lifts[k + 1], lifts[k + 2]
                    deck, ramp = structure_kinds(o["highway"])
                    v[3] = deck if up > 0 and nxt_up > 0 else (ramp if up > 0 or nxt_up > 0 else o["highway"])
        hw_nodes[("hw", a, b, 0)], hw_nodes[("hw", a, b, 1)] = ends
        hw_edges.append([("hw", a, b, 0), ("hw", a, b, 1), o["highway"], via])
    # every port gets its road: from a street or a highway, overland, through its gate and onto its platform
    port_roads(cities, graphs, hw_nodes, hw_edges, ground, wet, keep, airports)
    for nodes, edges in graphs:  # a city street never runs into a keep zone
        gone = {n for n, p in nodes.items() if in_keep(keep, p[0], p[1], 2000)}
        edges[:] = [e for e in edges if e[0] not in gone and e[1] not in gone]
    if wet or any(canal_lines(c, o) for c in cities):  # cross roads added after city_graph bridge or stop too
        graphs = [over_water(n, e, water_of(c, o, wet), bridges=bridges, stubs=False) for c, (n, e) in zip(cities, graphs)]
    # every city one network, whatever above parted it: a district left on its own is joined to the rest
    graphs = [join_parts(n, e, water_of(c, o, wet), o["core_roads"], bridges, land=o["neighbourhood_roads"])
              for c, (n, e) in zip(cities, graphs)]
    out = {f"{c['name']} Roads": build_actor(n, e, ground, folder) for c, (n, e) in zip(cities, graphs)}
    level_joins(out, cities, join_rows)  # each city graded its half on its own: the two meet at one height
    for ap in airports or []:  # airports: the airside graph (runways + taxiways) and the landside roads
        from .. import airport
        city_nodes = next((n for c, (n, _e) in zip(cities, graphs) if c["name"] == ap["city"]), {})
        city_nodes = {k: v for k, v in city_nodes.items() if isinstance(k, tuple) and len(k) == 2}
        got = airport.build(ap, ground, city_nodes, wet)
        an, ae = got["airside"]
        level = ap.get("level_cm")
        flat = (lambda x, y, z=level: z) if level is not None else ground  # an airfield is graded level
        label = f"{ap['city']} Airport" if ap["type"] != "military" else f"{ap['city']} Airbase"
        out[label] = build_actor(an, ae, flat, folder)
        rn, re_ = got["landside"]
        route_out(terrain, rn, re_)
        if re_:
            airport_drives(got["lots"], rn, re_)
            out[f"{label} Roads"] = build_actor(rn, re_, ground, folder)
            meet_city(out[f"{label} Roads"], out.get(f"{ap['city']} Roads"), [l["to_city_node"] for l in got["links"]])
        if airport_builds is not None:
            airport_builds[label] = got
    for c in cities:  # graving docks: laid at the dock floor so the spline digs the basin
        if c.get("shipyards"):
            from .. import shipyard
            dn, de = {}, []
            for y in c["shipyards"]:
                n_, e_ = shipyard.dock_edges(y)
                dn.update(n_)
                de += e_
            if de:
                # (12 m under the yard's platform; a yard that was never settled: under the ground as it lies)
                level = lambda x, y, yards=c["shipyards"]: next(
                    (s["z"] for s in yards if s.get("z") is not None and shipyard.inside(s, x, y, 4000)), ground(x, y))
                out[f"{c['name']} Docks"] = build_actor(dn, de, lambda x, y: level(x, y) - shipyard.DOCK_DEPTH_CM, folder)
    for c in cities:  # canals: laid at the channel bed so the spline lowers the ground into them
        lines = canal_lines(c, o)
        if lines:
            cn, ce = {}, []
            for k, line in enumerate(lines):
                cn[("cn", k, 0)], cn[("cn", k, 1)] = line[0], line[-1]
                ce.append([("cn", k, 0), ("cn", k, 1), "CN", [list(p) for p in line[1:-1]]])
            out[f"{c['name']} Canals"] = build_actor(cn, ce, lambda x, y: ground(x, y) - CANAL_DEPTH_CM, folder)
    for c in cities:  # the urban freeway layer: its own actor, so a change to it never re-pastes the streets
        fw = freeway_plan(c, o)
        fn, fe = over_water(fw.nodes, fw.edges, wet, bridges=bridges) if wet else (fw.nodes, fw.edges)
        if fe:
            out[f"{c['name']} Freeways"] = model_pieces(build_actor(fn, fe, ground, folder), fw.interchanges, ground, folder)
    if wet and hw_edges:  # a highway crosses any water on a bridge (a long one if it has to)
        long = {k: 1e9 for k in (*HIGHWAYS, "2L", "ER", "ED", "BR", "BRR")}
        hw_nodes, hw_edges = over_water(hw_nodes, hw_edges, wet, long, bridges)
    if hw_edges:
        out["Highways"] = build_actor(hw_nodes, hw_edges, ground, folder, pieces)
    edge = {"box": cities[0].get("map") if cities else None, "ground": ground, "wet": wet}
    for _ in range(8):  # again while anything moved: an end may land on a road whose own end just moved
        if not weld_ends(out, edge):
            break
    for _ in range(3):  # the welds' height changes, spread along the whole ramp (through its forks too) ...
        for label, a in out.items():
            if label.endswith(("Freeways", "Highways")):
                settle_grades(a)
        if not weld_ends(out, edge):  # ... and any end whose road that moved, put back on it
            break
    for _ in range(4):  # (a ramp cut back in the last of those passes still has its new end to land)
        if not weld_ends(out, edge):
            break
    for label, a in out.items():  # a last piece under half a metre long (an end welded onto the point before it) goes
        if label.endswith(("Freeways", "Highways")):
            by = {p["i"]: p for p in a["points"]}
            deg: dict = {}
            for s in a["segments"]:
                for i in (s["a"], s["b"]):
                    deg[i] = deg.get(i, 0) + 1
            for s in list(a["segments"]):
                pa, pb = by.get(s["a"]), by.get(s["b"])
                tip = next((q for q in (pa, pb) if q and deg.get(q["i"]) == 1 and not q["mesh"]), None)
                if pa and pb and tip and math.dist((pa["x"], pa["y"], pa["z"]), (pb["x"], pb["y"], pb["z"])) < 50:
                    a["segments"].remove(s)
                    a["points"].remove(tip)
    # a point only where a road turns, meets another or carries a piece: straight from corner to corner (a point
    # per 20 m, each on its own bump, made every road wave); then every welded end put back on the road it reaches
    for label in list(out):
        pts, segs = st.straighten(out[label]["points"], out[label]["segments"], keep_z=st.keeps_heights(label))
        out[label] = {**out[label], "points": pts, "segments": segs}
    for _ in range(8):
        if not weld_ends(out, edge):
            break
    return out


INTERCHANGE_FOLDER = "Interchanges"  # under the roads folder: the IC_* models (assets/interchanges)


ROUTE_N = 128  # A* cells across the map for highway routing
ROUTE_TOL_CM = 15000.0  # Douglas-Peucker tolerance of the routed line
ROUTE_STEP_CM = 20000.0  # the routed line gets a via point at least this often (the highway drapes on them)


def _simplify(pts: list, tol: float) -> list:
    """Douglas-Peucker: the fewest of pts (ends kept) with none of the rest farther than tol from the line."""
    if len(pts) < 3:
        return list(pts)
    (ax, ay), (bx, by) = pts[0], pts[-1]
    L = math.hypot(bx - ax, by - ay) or 1.0
    k, far = max(((k, abs((bx - ax) * (ay - p[1]) - (ax - p[0]) * (by - ay)) / L) for k, p in enumerate(pts[1:-1], 1)),
                 key=lambda t: t[1])
    if far <= tol:
        return [pts[0], pts[-1]]
    return _simplify(pts[:k + 1], tol)[:-1] + _simplify(pts[k:], tol)


def terrain_z(ter: dict, x: float, y: float) -> float:
    """A terrain() world's ground (cm) at (x, y) cm, bilinear between its cell centres."""
    g, rows, (mx, my), (Mx, My) = ter["grid"], ter["heights"], ter["min"], ter["max"]
    u = min(max((x - mx) / (Mx - mx) * g - 0.5, 0.0), g - 1.0)
    v = min(max((y - my) / (My - my) * g - 0.5, 0.0), g - 1.0)
    i, j = min(int(u), g - 2), min(int(v), g - 2)
    fu, fv = u - i, v - j
    return ((rows[j][i] * (1 - fu) + rows[j][i + 1] * fu) * (1 - fv)
            + (rows[j + 1][i] * (1 - fu) + rows[j + 1][i + 1] * fu) * fv)


ROUTE_CLIMB = 25.0  # cost of a metre up or down, in metres of flat road: a 500 m crest is worth a 25 km detour
ROUTE_MAX_GRADE = 0.08  # no step of a routed highway steeper than this
STREET_ROUTE_GRADE = 0.15  # an airport's access road (a street) may climb this where no highway grade gets through


def cost_route(ter: dict, p, q, n: int = ROUTE_N, max_grade: float = ROUTE_MAX_GRADE) -> list | None:
    """Via points (cm, ends left out) of the cheapest way from p to q (8-way A* on an n x n grid over the terrain):
    the engine's road_cost per metre, ROUTE_CLIMB per metre climbed or descended, and no step steeper than
    ROUTE_MAX_GRADE, so a highway crosses a range at its pass. Simplified at ROUTE_TOL_CM, then split so no two
    via points lie more than ROUTE_STEP_CM apart. None when no way stays under the grade."""
    import heapq
    cost, g = ter["maps"]["road_cost"], ter["grid"]
    (mx, my), (Mx, My) = ter["min"], ter["max"]
    cw, ch = (Mx - mx) / n, (My - my) / n
    # ponytail: nearest terrain cell per route cell; bilinear if the terrain grid is much coarser than n
    c = [cost[min(g - 1, j * g // n) * g + min(g - 1, i * g // n)] for j in range(n) for i in range(n)]
    z = [terrain_z(ter, mx + (i + 0.5) * cw, my + (j + 0.5) * ch) / 100.0 for j in range(n) for i in range(n)]
    low = min(c) or 1e-6
    cell = lambda x, y: (min(n - 1, max(0, int((x - mx) / cw))), min(n - 1, max(0, int((y - my) / ch))))
    (si, sj), (gi, gj) = cell(*p[:2]), cell(*q[:2])
    start, goal = sj * n + si, gj * n + gi
    h = lambda k: low * math.hypot((k % n - gi) * cw, (k // n - gj) * ch) / 100.0
    best, came, heap = {start: 0.0}, {}, [(h(start), start)]
    steps = [(di, dj, math.hypot(di * cw, dj * ch) / 100.0) for di in (-1, 0, 1) for dj in (-1, 0, 1) if di or dj]
    while heap:
        f, k = heapq.heappop(heap)
        if k == goal:
            break
        d = best[k]
        if f - h(k) > d + 1e-6:
            continue
        i, j = k % n, k // n
        for di, dj, L in steps:
            a, b = i + di, j + dj
            if 0 <= a < n and 0 <= b < n:
                m = b * n + a
                rise = abs(z[m] - z[k])
                if rise > L * max_grade:
                    continue
                nd = d + L * (c[k] + c[m]) / 2 + ROUTE_CLIMB * rise
                if nd < best.get(m, 1e30):
                    best[m], came[m] = nd, k
                    heapq.heappush(heap, (nd + h(m), m))
    if goal != start and goal not in came:
        return None
    path, k = [], goal
    while k in came:
        path.append(k)
        k = came[k]
    pts = [list(p[:2])] + [[mx + (k % n + 0.5) * cw, my + (k // n + 0.5) * ch] for k in reversed(path[1:])] + [list(q[:2])]
    line = _simplify(pts, ROUTE_TOL_CM)
    via = []
    for a, b in zip(line, line[1:]):
        m = max(1, math.ceil(math.dist(a, b) / ROUTE_STEP_CM))
        via += [[a[0] + (b[0] - a[0]) * t / m, a[1] + (b[1] - a[1]) * t / m] for t in range(1, m + 1)]
    return via[:-1]


def route_out(terrain: dict | None, nodes: dict, edges: list) -> None:
    """An airport's access and cargo roads out to the city (edges ending at a (..., 'city') node) take the
    cheapest way over the terrain, round a mountain or through its pass: straight, one ran level through a
    mountain in a 100 m trench (Nov 20 2026). Highway grade first, a street's where that finds none; left
    straight when neither does. In place."""
    if not terrain or not (terrain.get("maps") or {}).get("road_cost"):
        return
    for e in edges:
        if e[1][-1] == "city":
            via = e[3] if len(e) > 3 else []
            a, b = via[-1] if via else nodes[e[0]], nodes[e[1]]
            routed = cost_route(terrain, a, b) or cost_route(terrain, a, b, max_grade=STREET_ROUTE_GRADE)
            if routed:
                e[3:] = [via + routed]


def model_pieces(a: dict, ics: list, ground, folder: str) -> dict:
    """Each Blender interchange model (freeways.place_model) as a piece on its own point, and every road end on
    one of its sockets held there (marked 'IC_Socket'): weld_ends never runs a freeway on into the model. The
    model's point is linked to each socket (a mesh-less link, as a junction to its mouths): without them the
    model joined nothing and every freeway through one fell apart in two, its cross roads with it (Nov 18 2026)."""
    socks = [s for ic in ics if ic.get("model") for s in ic["model"]["sockets"]]
    # (each link carries half the model's width across it, so grade_to_roads levels the model's cross (a stack: its square);
    # the sockets take the model's height and settle_grades eases the freeway and cross roads to it)
    held = {}
    for p in a["points"]:
        if not p["mesh"] and any(math.hypot(p["x"] - x, p["y"] - y) < 100 for x, y in socks):
            p["mesh"] = "IC_Socket"
        if p["mesh"] == "IC_Socket":
            held[p["i"]] = p
    i = max([p["i"] for p in a["points"]] + [s["i"] for s in a["segments"]] + [0]) + 1
    for ic in ics:
        m = ic.get("model")
        if m:
            x, y = ic["at"]
            centre = _point(i, x, y, ground(x, y), m["yaw"], m["width"], lifted=True,
                            piece=f"{folder}/{INTERCHANGE_FOLDER}/{m['mesh']}")
            a["points"].append(centre)
            i += 1
            size = m.get("size") or [2 * m["width"]] * 2
            for sx, sy in m["sockets"]:
                along = math.hypot(sx - x, sy - y)
                across = size[1] / 2 if abs(along - size[0] / 2) <= abs(along - size[1] / 2) else size[0] / 2
                if ic.get("kind") != "stack":  # the cross freeways.place_model takes: a flat pad of the whole
                    across /= 2  # square cut a trench with walls into the hill beside the freeway (Nov 18 2026)
                for p in held.values():
                    if math.hypot(p["x"] - sx, p["y"] - sy) < 100:
                        p["z"] = centre["z"]
                end = min(held.values(), key=lambda p: math.hypot(p["x"] - sx, p["y"] - sy), default=None)
                if end and math.hypot(end["x"] - sx, end["y"] - sy) < 100:
                    a["segments"].append({**_segment(i, centre["i"], end["i"], "", folder, lifted=True), "half": across})
                    i += 1
    return a


WELD_REACH_CM = 2600.0  # an open end this close (in plan) to another road is meant to join it
WELD_GRADE = 0.08  # the end's own road eases to the new height at most this steeply
WELD_SNAP_CM = 6000.0  # an end that stops short of every road by less than this is brought up to the nearest one
FREEWAY_LINK_CM = 15000.0  # a freeway that ends this close to a road runs on to it (an urban freeway ending in a field)
FREEWAY_OUT_CM = 400000.0  # a freeway end no highway took runs on to the map's edge when that is this close
FREEWAY_REACH_CM = 150000.0  # ... or runs on up to 1.5 km to the nearest road ahead of it
FREEWAY_MESHES = {ROAD_MESH[k].split("/")[-1] for k in ("6L", "ED")}
RAMP_MESHES = {ROAD_MESH[k].split("/")[-1] for k in ("ER", "FR")}  # their ends get a connector piece
# a connector's half width at the ramp's end (road_profiles.connector: the gore widens to 10 m, the flare to 10.5 m)
CONNECTOR_HALF = {"SM_Road_RampGore30m": 1000.0, "SM_Road_RampFlare15m": 1050.0}
FLARE_CM = 1500.0  # the flare runs this far on past the ramp's end, to the street's kerb line


DECK_DIP_CM = 1000.0  # how far a freeway deck may come down to meet ramps too short to climb to it
SETTLE_GRADE = 0.15  # steeper than anything planned (an overpass's approach climbs at up to ~13 %): only faults


def settle_grades(a: dict, grade: float = SETTLE_GRADE, rounds: int = 400) -> int:
    """No piece of a freeway or ramp steeper than `grade`: wherever one is, its two ends move toward each other's
    height, again and again, so a step is shared out along the road and on through its forks (weld_ends eased an
    end's road only as far as the first fork: a ramp 30 m under the deck it joins climbed it in its last 10 m,
    Nov 12 2026). Welded ends (they carry their connector), open ends and junction mouths stay where they are;
    between two of those too close for the grade, the slope comes out even. Returns the points moved."""
    by = {p["i"]: p for p in a["points"]}
    segs = [s for s in a["segments"] if s["a"] in by and s["b"] in by]
    count: dict = {}
    fixed = {p["i"] for p in a["points"] if p.get("mesh")}
    for s in segs:
        for i in (s["a"], s["b"]):
            count[i] = count.get(i, 0) + 1
            if not s["mesh"]:
                fixed.add(i)
    fixed |= {i for i, n in count.items() if n == 1}
    # A ramp's merge end rides with the freeway deck beside it instead of being nailed to it: where the ramp is too
    # short for the climb (a viaduct 30 m over the street its ramps leave from, 150 m of ramp: 21 %), the deck
    # itself comes down toward the interchange, by at most DECK_DIP_CM and never steeper than it was laid or 6 %.
    decks = [s for s in segs if s["mesh"].split("/")[-1] in FREEWAY_MESHES]
    ties = []
    for p in a["points"]:
        if p.get("mesh") != "SM_Road_RampGore30m":
            continue
        best = None
        for s in decks:
            u, v = by[s["a"]], by[s["b"]]
            wx, wy = v["x"] - u["x"], v["y"] - u["y"]
            t = max(0.0, min(1.0, ((p["x"] - u["x"]) * wx + (p["y"] - u["y"]) * wy) / (wx * wx + wy * wy or 1.0)))
            d = math.hypot(p["x"] - u["x"] - wx * t, p["y"] - u["y"] - wy * t)
            if d < 3500 and (best is None or d < best[0]):
                best = (d, u["i"], v["i"], t)
        if best:
            ties.append((p["i"], *best[1:]))
            fixed.discard(p["i"])
    floor = {i: by[i]["z"] - DECK_DIP_CM for s in decks for i in (s["a"], s["b"])}
    runs = []
    for s in segs:
        if s["mesh"]:
            run = math.hypot(by[s["b"]]["x"] - by[s["a"]]["x"], by[s["b"]]["y"] - by[s["a"]]["y"])
            lim = max(abs(by[s["b"]]["z"] - by[s["a"]]["z"]), 0.06 * run) if s in decks else grade * run
            runs.append((s["a"], s["b"], min(lim, grade * run)))
    moved: set = set()
    for _ in range(rounds):
        worst = 0.0
        for i, ui, vi, t in ties:
            diff = by[i]["z"] - (by[ui]["z"] * (1 - t) + by[vi]["z"] * t)
            if abs(diff) < 1:
                continue
            wu, wv = (0.0 if ui in fixed else 1 - t), (0.0 if vi in fixed else t)
            lam = diff / (1 + wu * wu + wv * wv)
            by[i]["z"] -= lam
            by[ui]["z"] = max(floor[ui], by[ui]["z"] + lam * wu)
            by[vi]["z"] = max(floor[vi], by[vi]["z"] + lam * wv)
            moved.update((i, ui, vi))
        for i, j, lim in runs:
            dz = by[j]["z"] - by[i]["z"]
            over = abs(dz) - lim
            free = (i not in fixed) + (j not in fixed)
            if over <= 0 or not free:
                continue
            worst = max(worst, over)
            step = math.copysign(over / free, dz)
            if i not in fixed:
                by[i]["z"] += step
                moved.add(i)
            if j not in fixed:
                by[j]["z"] -= step
                moved.add(j)
        if worst < 2:
            break
    for s in segs:
        if s["a"] in moved or s["b"] in moved:
            st.retangent(by, s)
    return len(moved)


def _connector(p: dict, yaw: float, seg: dict, name: str) -> None:
    """Put connector `name` (road_profiles.CONNECTORS) on a ramp's end point p, turned to `yaw` (along the road a
    gore joins, or straight at the street a flare meets: never the ramp's own last kink, which left pieces
    sticking out into the field), in the roads folder its own segment's mesh comes from. The point's yaw is also
    the spline's heading there, so the ramp bends into the piece."""
    import re
    m = re.search(r"StaticMesh'(.+?)/(?:Elevated/)?SM_[^/.']+\.", " ".join(seg.get("lines") or []))
    folder = m.group(1) if m else ""
    p["yaw"] = yaw
    p["mesh"] = name
    p["lines"] = [l for l in p.get("lines") or [] if not l.startswith(("Mesh=", "MeshScale=", "SegmentMeshOffset="))] + \
        [f"Mesh=\"/Script/Engine.StaticMesh'{folder}/{name}.{name}'\"", "MeshScale=(X=1.000000,Y=1.000000,Z=1.000000)"]


def own_run(touch: dict, by: dict, i, limit: float = 8000.0) -> set:
    """The points of the road an open end belongs to, back from the end up to `limit` cm or the first fork: an end
    never 'joins' its own road (its previous piece, a few metres back at its own height, passed as the road it
    meets, so ramps planned 16 m off their street were never moved and the check said they were fine)."""
    run, prev, cur, gone = {i}, None, i, 0.0
    while gone < limit:
        nxt = [s["b"] if s["a"] == cur else s["a"] for s in touch.get(cur, ())]
        nxt = [n for n in nxt if n != prev and n in by]
        if len(nxt) != 1 or len(touch.get(cur, ())) > 2:
            break
        gone += math.hypot(by[nxt[0]]["x"] - by[cur]["x"], by[nxt[0]]["y"] - by[cur]["y"])
        prev, cur = cur, nxt[0]
        run.add(cur)
    return run


def weld_ends(actors: dict, edge: dict | None = None) -> int:
    """Every open end of a freeway, ramp, frontage road or highway lands on the road it reaches, at that road's
    height. Each actor is laid and graded on its own, so a ramp planned onto a street came out metres above or
    below it: here the end takes the height of the road under it (the one nearest its own level), and its own
    road eases back from there at WELD_GRADE. Junctions and their mouths never move. Returns the ends moved."""
    every = []
    for label, a in actors.items():
        if label.endswith((" Docks", " Canals", " Paths", " Access")):
            continue
        by = {p["i"]: p for p in a["points"]}
        every += [(label, by[s["a"]], by[s["b"]], s["mesh"].split("/")[-1])
                  for s in a["segments"] if s["mesh"] and s["a"] in by and s["b"] in by]
    half_of = {m.split("/")[-1]: HALF[k] for k, m in ROAD_MESH.items()}
    # the roads near each end only (every road of the world for every open end was 5 s a rebuild): by 200 m cell
    cell, reach = 20000.0, max(WELD_REACH_CM, WELD_SNAP_CM, FREEWAY_REACH_CM)
    by_cell: dict = {}
    for k, (_lab, u, v, _mesh) in enumerate(every):
        for cx in range(int(min(u["x"], v["x"]) // cell), int(max(u["x"], v["x"]) // cell) + 1):
            for cy in range(int(min(u["y"], v["y"]) // cell), int(max(u["y"], v["y"]) // cell) + 1):
                by_cell.setdefault((cx, cy), []).append(k)

    def roads_near(p):  # in the order they were listed: the first of two equal finds still wins
        ks = {k for cx in range(int((p["x"] - reach) // cell), int((p["x"] + reach) // cell) + 1)
              for cy in range(int((p["y"] - reach) // cell), int((p["y"] + reach) // cell) + 1)
              for k in by_cell.get((cx, cy), ())}
        return [every[k] for k in sorted(ks)]
    # every freeway / highway open end: one that sits on another actor's end is that road going on (the highway
    # starting on a freeway's tip). It stays there: slid to the freeway's edge it stood 2.5 m off the tip, joined
    # nothing, and the freeway ran on past it 200 m along the highway (Nov 18 2026)
    tips = []
    for label, a in actors.items():
        if label.endswith(("Freeways", "Highways")):
            deg: dict = {}
            for s in a["segments"]:
                if s["mesh"]:
                    for i in (s["a"], s["b"]):
                        deg[i] = deg.get(i, 0) + 1
            tips += [(label, p) for p in a["points"] if deg.get(p["i"]) == 1]
    moved = 0
    for label, a in actors.items():
        if not label.endswith(("Freeways", "Highways")):
            continue
        by = {p["i"]: p for p in a["points"]}
        touch: dict = {}
        for s in a["segments"]:
            for i in (s["a"], s["b"]):
                touch.setdefault(i, []).append(s)
        for i, ss in touch.items():
            p = by.get(i)
            if not p or len(ss) != 1 or not ss[0]["mesh"] or (p["mesh"] and p["mesh"] not in CONNECTOR_HALF):
                continue  # only a road's own open end (one with its connector is welded again: its road may move)
            other = next((q for lab2, q in tips if lab2 != label and math.hypot(q["x"] - p["x"], q["y"] - p["y"]) < 50), None)
            if other:
                if label.endswith("Highways") and abs(other["z"] - p["z"]) > 1:  # (the freeway's height wins)
                    p["z"] = other["z"]
                    st.retangent(by, ss[0])
                    moved += 1
                continue
            own = half_of.get(ss[0]["mesh"].split("/")[-1], 650.0)
            mine = own_run(touch, by, i)
            o0 = by[ss[0]["b"] if ss[0]["a"] == i else ss[0]["a"]]
            best, near, link = None, None, None  # best: in reach, nearest the end's level; near / link: closest beyond
            for lab2, u, v, mesh in roads_near(p):
                if lab2 == label and u["i"] in mine and v["i"] in mine:
                    continue
                ux, uy = v["x"] - u["x"], v["y"] - u["y"]
                t = max(0.0, min(1.0, ((p["x"] - u["x"]) * ux + (p["y"] - u["y"]) * uy) / (ux * ux + uy * uy or 1.0)))
                nx, ny, z = u["x"] + ux * t, u["y"] + uy * t, u["z"] + (v["z"] - u["z"]) * t
                d = math.hypot(p["x"] - nx, p["y"] - ny)
                hit = (d, nx, ny, z, half_of.get(mesh, 800.0), mesh, ux, uy)
                if d <= WELD_REACH_CM:
                    if best is None or abs(z - p["z"]) < abs(best[3] - p["z"]):
                        best = hit
                elif d <= WELD_SNAP_CM and (near is None or d < near[0]):
                    near = hit
                # (another actor's road only: past own_run's 80 m a freeway's own deck passed as the road to link
                # to, and it was folded back over itself for 20 m. Beyond FREEWAY_LINK_CM only a road ahead of it,
                # within 60 degrees of the way it was going)
                elif d <= FREEWAY_REACH_CM and lab2 != label and (link is None or d < link[0]) and (
                        d <= FREEWAY_LINK_CM or (nx - p["x"]) * (p["x"] - o0["x"]) + (ny - p["y"]) * (p["y"] - o0["y"])
                        > 0.5 * d * math.hypot(p["x"] - o0["x"], p["y"] - o0["y"])):
                    link = hit
            hit = best or near
            if not hit:
                if link and ss[0]["mesh"].split("/")[-1] in FREEWAY_MESHES:
                    # a freeway that stops short in the fields runs on to the road it meets, a piece every 20 m,
                    # coming down evenly to that road's height (the next weld puts its new end inside that road)
                    d, nx, ny, level, half, *_r = link
                    n = max(1, round((d - 2000) / 2000))
                    nid = 1 + max([q["i"] for q in a["points"]] + [s["i"] for s in a["segments"]])
                    prev = i
                    for k in range(1, n + 1):
                        f = k / n * (d - 2000) / d
                        q = {**p, "i": nid, "x": p["x"] + (nx - p["x"]) * f, "y": p["y"] + (ny - p["y"]) * f,
                             "z": p["z"] + (level - p["z"]) * k / n, "lines": list(p.get("lines") or [])}
                        s = {**ss[0], "i": nid + 1, "a": prev, "b": nid, "lines": list(ss[0].get("lines") or [])}
                        a["points"].append(q)
                        a["segments"].append(s)
                        by[nid] = q
                        st.retangent(by, s)
                        prev, nid = nid, nid + 2
                    moved += 1
                elif edge and edge.get("box") and ss[0]["mesh"].split("/")[-1] in FREEWAY_MESHES:
                    # no road to reach at all: the freeway runs straight on out of the world, as far as the map's
                    # edge, at the height over the ground it ended at (over dry land only: no bridge is laid here)
                    box, gnd, wet = edge["box"], edge["ground"], edge.get("wet")
                    L0 = math.hypot(p["x"] - o0["x"], p["y"] - o0["y"]) or 1.0
                    ex, ey = (p["x"] - o0["x"]) / L0, (p["y"] - o0["y"]) / L0
                    d = min((box[2] - p["x"]) / ex if ex > 1e-6 else (box[0] - p["x"]) / ex if ex < -1e-6 else 1e18,
                            (box[3] - p["y"]) / ey if ey > 1e-6 else (box[1] - p["y"]) / ey if ey < -1e-6 else 1e18)
                    n = max(1, round(d / 2000))
                    line = [(p["x"] + ex * d * k / n, p["y"] + ey * d * k / n) for k in range(1, n + 1)]
                    if 2000 < d <= FREEWAY_OUT_CM and not (wet and any(wet(x, y) for x, y in line)):
                        up = p["z"] - (gnd(p["x"], p["y"]) or 0.0)
                        nid = 1 + max([q["i"] for q in a["points"]] + [s["i"] for s in a["segments"]])
                        prev = i
                        for x, y in line:
                            q = {**p, "i": nid, "x": x, "y": y, "z": (gnd(x, y) or 0.0) + up, "lines": list(p.get("lines") or [])}
                            s = {**ss[0], "i": nid + 1, "a": prev, "b": nid, "lines": list(ss[0].get("lines") or [])}
                            a["points"].append(q)
                            a["segments"].append(s)
                            by[nid] = q
                            st.retangent(by, s)
                            prev, nid = nid, nid + 2
                        moved += 1
                continue
            d, nx, ny, level, half, onto, ux, uy = hit
            if ss[0]["mesh"].split("/")[-1] in RAMP_MESHES and onto not in FREEWAY_MESHES | STRUCTURES:
                # (a street only: beside a freeway the ramp's last pieces are its merge lane)
                # a ramp that comes in at a shallow angle ran on INSIDE the road it joins for its last pieces (a slip
                # ramp along a frontage road, 100 m of deck lying in the street): while the point before the end is
                # in that road too, the end goes; the ramp stops where it first touches
                def inside(o):
                    for lab2, u, v, mesh in every:
                        if mesh != onto or (lab2 == label and u["i"] in mine and v["i"] in mine):
                            continue
                        wx, wy = v["x"] - u["x"], v["y"] - u["y"]
                        t = max(0.0, min(1.0, ((o["x"] - u["x"]) * wx + (o["y"] - u["y"]) * wy) / (wx * wx + wy * wy or 1.0)))
                        # (half its width in the road, up to a storey above or below: a piece brushing the kerb is
                        # its merge)
                        if math.hypot(o["x"] - u["x"] - wx * t, o["y"] - u["y"] - wy * t) < half + own * 0.5 and \
                                abs(u["z"] + (v["z"] - u["z"]) * t - o["z"]) < 600:
                            return True
                    return False
                end, seg, gone = p, ss[0], 0
                while gone < 40:  # (all of them in this pass: the next welds the new end)
                    o = by[seg["b"] if seg["a"] == end["i"] else seg["a"]]
                    if len(touch.get(o["i"], ())) != 2 or o["mesh"] or not inside(o) or \
                            seg not in a["segments"] or end not in a["points"]:  # (gone already, from its other end)
                        break
                    a["points"].remove(end)
                    a["segments"].remove(seg)
                    gone += 1
                    end, seg = o, next(s for s in touch[o["i"]] if s is not seg)
                if gone:
                    moved += 1
                    continue
            # where the end sits: inside the road it joins, its edge on that road's edge (never beside it with grass
            # between, Nov 12 2026). A ramp end gets its own piece: running along the road (a merge or a diverge)
            # it stops beside it and its gore widens out to the road's edge line; meeting it head on (a loop's foot,
            # a diamond ramp at the cross street) it ends on the road's edge and its flare meets the kerb line
            piece = ""
            tgt = max(0.0, half - own)
            if ss[0]["mesh"].split("/")[-1] in RAMP_MESHES:
                o = by[ss[0]["b"] if ss[0]["a"] == i else ss[0]["a"]]
                rx, ry = p["x"] - o["x"], p["y"] - o["y"]
                along = abs(rx * ux + ry * uy) / ((math.hypot(rx, ry) * math.hypot(ux, uy)) or 1.0) > 0.7
                along = along and onto in FREEWAY_MESHES | STRUCTURES  # a street always gets the flare
                piece = "SM_Road_RampGore30m" if along else "SM_Road_RampFlare15m"
                tgt = half + CONNECTOR_HALF[piece] - 50 if along else half - walk_of(half) + FLARE_CM - 50
            slide = abs(d - tgt) > 50 and d > 1
            if slide:
                p["x"], p["y"] = nx + (p["x"] - nx) / d * tgt, ny + (p["y"] - ny) / d * tgt
            if piece:
                if piece == "SM_Road_RampGore30m":  # along the road, the way the ramp was heading
                    sgn = 1.0 if rx * ux + ry * uy >= 0 else -1.0
                    yaw = math.degrees(math.atan2(sgn * uy, sgn * ux))
                else:  # straight at the street
                    yaw = math.degrees(math.atan2(ny - p["y"], nx - p["x"])) if d > 1 else \
                        math.degrees(math.atan2(ry, rx))
                _connector(p, yaw, ss[0], piece)
                st.retangent(by, ss[0])
            if not slide and abs(level - p["z"]) <= 30:
                continue
            p["z"] = level
            moved += 1
            touched, prev, cur = {i}, None, i
            for _ in range(80):  # ease the road back from its end
                nxt = [n for n in (s["b"] if s["a"] == cur else s["a"] for s in touch[cur] if s["mesh"]) if n != prev]
                q = by.get(nxt[0]) if len(nxt) == 1 else None
                if not q or q["mesh"] or len(touch[q["i"]]) < 2 or any(not s["mesh"] for s in touch[q["i"]]):
                    break  # a fork, a piece, a junction's mouth or another end (welded itself): that stays put
                lim = WELD_GRADE * math.hypot(q["x"] - by[cur]["x"], q["y"] - by[cur]["y"])
                z = max(by[cur]["z"] - lim, min(by[cur]["z"] + lim, q["z"]))
                if abs(z - q["z"]) < 1:
                    break
                q["z"] = z
                touched.add(q["i"])
                prev, cur = cur, q["i"]
            for s in a["segments"]:
                if s["a"] in touched or s["b"] in touched:
                    st.retangent(by, s)
    return moved


def parcels_area(poly: list) -> float:
    from .. import parcels
    return parcels._area(poly)



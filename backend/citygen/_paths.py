from __future__ import annotations

def _cum_of(line):
    cum = [0.0]
    for p, q in zip(line, line[1:]):
        cum.append(cum[-1] + math.dist(p[:2], q[:2]))
    return cum


_FW_CACHE: dict = {}


def freeway_plan(city: dict, opts: dict | None = None):
    """The city's freeway layer (freeways.plan), cached: city_graph, city_layout and generate all ask for it."""
    from .. import freeways
    o = city_options(city, opts)
    key = repr((city.get("name"), city["x"], city["y"], city["radius"], city.get("seed"), city.get("map"),
                city.get("turn"), city.get("shape"), city.get("yield_to"), city.get("fw_shore"), city.get("ic_steep"),
                [o[k] for k in ("freeways", "interchange_models", "block_m", "block_ratio", "downtown", "normal", "collector_every", "core_roads",
                                "neighbourhood_roads", "canals")],
                [(a.get("center"), a.get("heading"), a.get("type")) for a in city.get("airports") or []],
                [(y.get("frame"), y.get("L"), y.get("D"), y.get("bank"))
                 for y in (city.get("shipyards") or []) + (city.get("shipyards_near") or [])]))
    if key not in _FW_CACHE:
        if len(_FW_CACHE) > 32:
            _FW_CACHE.clear()
        _FW_CACHE[key] = freeways.plan(city, o)
    return _FW_CACHE[key]


def freeway_report(city: dict, opts: dict | None = None) -> dict:
    """RoadNetworkValidator over the city's freeways, ramps, frontage roads and its streets."""
    from .. import freeways
    fw = freeway_plan(city, opts)
    if not fw.lines:
        return {"interchanges": [], "rejected": fw.rejected, "validation": {"ok": True, "checked": 0}}
    nodes, edges = city_graph(city, opts)
    streets = [{"name": f"street {a}-{b}", "cls": "street", "kind": k, "half": HALF.get(k, 500.0),
                "points": [(*nodes[a][:2], 0.0), (*nodes[b][:2], 0.0)]} for a, b, k, *_ in edges]
    lines = fw.lines + streets
    rep = freeways.validate(lines, [(ln["points"], ln["half"]) for ln in lines])
    return {"interchanges": fw.interchanges, "rejected": fw.rejected, "validation": rep}


def sidewalks(nodes: dict, edges: list) -> tuple:
    """SidewalkGenerator, pedestrian corridors: a sidewalk spline on both sides of every city street whose
    mesh has none (NO_SIDEWALK), offset by the road's half width + half the sidewalk, stopping at the
    junction mouths. Returns (nodes, edges) to merge into a Paths actor."""
    out_n, out_e = {}, []
    for a, b, kind, *_ in edges:
        if kind not in NO_SIDEWALK or a not in nodes or b not in nodes:
            continue
        (ax, ay), (bx, by) = nodes[a][:2], nodes[b][:2]
        L = math.hypot(bx - ax, by - ay)
        if L < 2 * MOUTH_CM + 500:
            continue
        ux, uy = (bx - ax) / L, (by - ay) / L
        off = HALF[kind] + HALF["SW"]
        for side in (1, -1):
            nx, ny = -uy * off * side, ux * off * side
            k0, k1 = ("sw", a, b, side, 0), ("sw", a, b, side, 1)
            out_n[k0] = [ax + ux * MOUTH_CM + nx, ay + uy * MOUTH_CM + ny]
            out_n[k1] = [bx - ux * MOUTH_CM + nx, by - uy * MOUTH_CM + ny]
            out_e.append([k0, k1, "SW"])
    return out_n, out_e


APRON_M = 8.0  # wider than this a lot's front path is a plaza slab, not a spline


def path_kind(width: float) -> str:
    return "DD" if width >= 5.0 else "DW" if width >= 3.0 else "WK"


def paths_graph(rows: list, kinds: tuple = ("WK", "DW", "DD")) -> tuple:
    """FrontageConnections: the previewed lots' walkways / driveways ([.., paths [[width_m, flat xyz]]]) as
    spline edges of these kinds (WK walkway, DW single, DD double driveway). Plaza aprons stay slabs."""
    nodes, edges = {}, []
    for k, row in enumerate(rows):
        for j, (width, flat) in enumerate(row[10] if len(row) > 10 else []):
            if width > APRON_M or len(flat) < 6 or path_kind(width) not in kinds:
                continue
            pts = [flat[i:i + 2] for i in range(0, len(flat) - 2, 3)]
            a, b = ("p", k, j, 0), ("p", k, j, 1)
            nodes[a], nodes[b] = list(pts[0]), list(pts[-1])
            edges.append([a, b, path_kind(width), [list(p) for p in pts[1:-1]]])
    return nodes, edges


def paths_actor(city: dict, opts: dict | None, rows: list, ground, folder: str, wet=None) -> dict:
    """PedestrianNetwork: the city's '<City> Paths' spline actor: its missing sidewalks and every walkway."""
    cn, ce = city_graph(city, opts, wet)
    ce = [e for e in ce if len(e) < 4 or not e[3]]  # no sidewalk spline along a bridge
    sn, se = sidewalks(cn, ce)
    for k, line in enumerate(canal_lines(city, opts)):  # canal-side walkways on both quays
        for p, q in zip(line, line[1:]):
            L = math.dist(p, q) or 1.0
            ux, uy = (q[0] - p[0]) / L, (q[1] - p[1]) / L
            off = HALF["CN"] + HALF["SW"]
            for side in (1, -1):
                a, b = ("cw", k, tuple(p), side, 0), ("cw", k, tuple(p), side, 1)
                sn[a] = [p[0] - uy * off * side + ux * MOUTH_CM, p[1] + ux * off * side + uy * MOUTH_CM]
                sn[b] = [q[0] - uy * off * side - ux * MOUTH_CM, q[1] + ux * off * side - uy * MOUTH_CM]
                se.append([a, b, "SW"])
    pn, pe = paths_graph(rows, ("WK",))
    return build_actor({**sn, **pn}, se + pe, ground, folder)


FILLET_CM = 900.0  # private street corners: a 9 m kerb radius, not a spline's wide arc


def fillet(pts: list, radius: float = FILLET_CM) -> list:
    """A polyline with every real corner (turn > 20 degrees) replaced by a short arc of `radius` (or less on a
    short leg), so a spline through it turns tight at the corner and runs straight between corners. A closed
    ring (first point == last) is restarted mid-side so every one of its corners is rounded too."""
    pts = [tuple(p[:2]) for p in pts]
    closed = len(pts) > 3 and math.dist(pts[0], pts[-1]) < 1
    if closed:
        ring = pts[:-1]
        mid = ((ring[0][0] + ring[1][0]) / 2, (ring[0][1] + ring[1][1]) / 2)
        pts = [mid] + ring[1:] + ring[:1] + [mid]
    out = [pts[0]]
    for a, b, c in zip(pts, pts[1:], pts[2:]):
        d1, d2 = (b[0] - a[0], b[1] - a[1]), (c[0] - b[0], c[1] - b[1])
        l1, l2 = math.hypot(*d1), math.hypot(*d2)
        if l1 < 1 or l2 < 1:
            continue
        u1, u2 = (d1[0] / l1, d1[1] / l1), (d2[0] / l2, d2[1] / l2)
        turn = math.acos(max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1])))
        if turn < math.radians(20):
            out.append(b)
            continue
        r = min(radius, 0.4 * l1, 0.4 * l2)
        t = r * math.tan(turn / 2)  # distance from the corner to where the arc starts and ends
        p0, p3 = (b[0] - u1[0] * t, b[1] - u1[1] * t), (b[0] + u2[0] * t, b[1] + u2[1] * t)
        for s in (0.0, 1 / 3, 2 / 3, 1.0):  # a quadratic through the corner: tangent to both legs
            out.append(((1 - s) ** 2 * p0[0] + 2 * (1 - s) * s * b[0] + s * s * p3[0],
                        (1 - s) ** 2 * p0[1] + 2 * (1 - s) * s * b[1] + s * s * p3[1]))
    out.append(pts[-1])
    return [[round(x), round(y)] for x, y in out]


PR_SAMPLE_CM = 2000.0  # a private street gets a point at least this often, so it lies on the ground between its corners


def _every(pts: list, step: float = PR_SAMPLE_CM) -> list:
    """pts with points added along every leg longer than `step`: a 360 m straight street had only its two ends, and
    its spline ran straight over the dip between them 23 m up (thin drops the extra points where the ground is even)."""
    out = [pts[0]]
    for p, q in zip(pts, pts[1:]):
        n = int(math.dist(p, q) // step)
        out += [[round(p[0] + (q[0] - p[0]) * t / (n + 1)), round(p[1] + (q[1] - p[1]) * t / (n + 1))] for t in range(1, n + 1)]
        out.append(q)
    return out


PR_STEEP = 0.30  # a private street ends before a leg of ground steeper than this: no drive climbs it


def steep_cut(streets: list, ground) -> list:
    """Each private street cut at the first leg (from its mouth) whose ground is steeper than PR_STEEP: the part
    it still reaches stays, with the lots along it. Test1's dead ends up 50 % slopes hung 23 m over the ground
    (Nov 20 2026). The mouth is its first point, or its last when only that one meets another private street;
    a street joined at both ends runs through and is kept whole."""
    def touched(k, p):
        return any(math.dist(p, q[:2]) < 100 for j, o in enumerate(streets) if j != k for q in o)
    out = []
    for k, s in enumerate(streets):
        if len(s) < 2:
            continue
        a, b = touched(k, s[0][:2]), touched(k, s[-1][:2])
        if a and b:
            out.append(s)
            continue
        run = _every(s[::-1] if b else s)
        keep = 1
        while keep < len(run) and abs(ground(*run[keep][:2]) - ground(*run[keep - 1][:2])) \
                <= PR_STEEP * max(1.0, math.dist(run[keep][:2], run[keep - 1][:2])):
            keep += 1
        if keep == len(run):
            out.append(s)
        elif keep >= 2:
            out.append([list(p) for p in run[:keep]])
    return out


def access_actor(city: dict, rows: list, ground, folder: str) -> dict:
    """Private access network: the city's '<City> Access' spline actor: every subdivision / complex street
    (the city doc's private streets, less what is too steep: steep_cut) and every driveway (single DW, double
    DD), graded like roads."""
    nodes, edges = paths_graph(rows, ("DW", "DD"))
    for k, s in enumerate(steep_cut((city.get("private") or {}).get("streets") or [], ground)):
        s = _every(fillet(s))
        a, b = ("pr", k, 0), ("pr", k, 1)
        nodes[a], nodes[b] = list(s[0]), list(s[-1])
        edges.append([a, b, "PR", [list(p) for p in s[1:-1]]])
    return build_actor(nodes, edges, ground, folder)


JOIN_REACH_CM = 1200.0  # a private street end this far past a street's edge still joins it with a junction
_KIND_OF_MESH = {}
for _k, _m in ROAD_MESH.items():
    if HALF.get(_k, 0) >= HALF.get(_KIND_OF_MESH.get(_m, ""), 0):
        _KIND_OF_MESH[_m] = _k


PR_GRADE = 0.10  # a joined private street eases from its street's height back to its own at most this steeply


def _ramp(rp: dict, rs: list, start, came, z0: float) -> None:
    """From the mouth (`came`, at height z0) along a private street: the step between z0 and the street's own
    height is shared out over the first |step| / PR_GRADE of it, fading to nothing; past that the street keeps its
    own profile (laid on the ground). Clamping every point to within PR_GRADE of z0 instead held a lane down a 24 %
    slope to 10 %, and its far end stood 24 m over the shore (Nov 20 2026)."""
    step = z0 - rp[start]["z"] if start in rp else 0.0
    if abs(step) < 1:
        return
    span, d, cur = abs(step) / PR_GRADE, 0.0, start
    while cur in rp:
        q, p = rp[cur], rp[came]
        d += math.hypot(q["x"] - p["x"], q["y"] - p["y"])
        if d >= span:
            return
        q["z"] += step * (1 - d / span)
        on = [x for x in rs if cur in (x["a"], x["b"]) and came not in (x["a"], x["b"])]
        if len(on) != 1:
            return
        came, cur = cur, on[0]["b"] if on[0]["a"] == cur else on[0]["a"]

def unjoin_private(roads: dict) -> dict:
    """The roads actor as it was before join_private: every joined private street out, every host road whole again."""
    pts = [p for p in roads["points"] if not p.get("join")]
    segs = [s for s in roads["segments"] if not s.get("join")]
    for p in roads["points"]:
        undo = p.get("undo")
        if p.get("join") and undo:  # what a join cut out of a host; a host a join made itself is not brought back
            pts += [dict(q) for q in undo["points"] if not q.get("join")]
            segs += [dict(s) for s in undo["segments"] if not s.get("join")]
    seen: set = set()
    pts = [p for p in pts if not (p["i"] in seen or seen.add(p["i"]))]
    seen = set()
    segs = [s for s in segs if not (s["i"] in seen or seen.add(s["i"]))]
    keep = {p["i"] for p in pts}
    return {"points": pts, "segments": [s for s in segs if s["a"] in keep and s["b"] in keep]}


def join_private(roads: dict, access: dict, folder: str) -> tuple:
    """Every private street joins the street (or the private street) its end meets with a real junction, in ONE
    spline actor: UEFN only connects control points of the same actor, so a private street kept in its own
    '<City> Access' actor ended on the grass beside its street. Each private street run moves from `access` into
    `roads`; where its end lies within the host's half width + JOIN_REACH_CM, the host is cut open there: a junction
    centre (pending piece, Build pieces makes the mesh) with a mouth MOUTH_CM up and down the host and one into the
    private street. Driveways stay in `access`. Returns (roads, access, joins). Re-running is safe: unjoin first."""
    roads = unjoin_private(roads)
    rp = {p["i"]: dict(p) for p in roads["points"]}
    rs = [dict(s) for s in roads["segments"]]
    ap = {p["i"]: dict(p) for p in access["points"]}
    pr_mesh = ROAD_MESH["PR"].rsplit("/", 1)[-1]
    moving = [dict(s) for s in access["segments"] if s["mesh"] == pr_mesh]
    moved_pts = {i for s in moving for i in (s["a"], s["b"])}
    rest_segs = [dict(s) for s in access["segments"] if s["mesh"] != pr_mesh and s["a"] not in moved_pts and s["b"] not in moved_pts]
    rest_pts = {i for s in rest_segs for i in (s["a"], s["b"])}
    nid = iter(range(max([0] + list(rp) + list(ap) + [s["i"] for s in rs + list(access["segments"])]) + 1, 1 << 30))
    remap = {}
    for i in moved_pts:  # the private street points come over under new ids
        q = dict(ap[i], i=next(nid), join=True)
        remap[i] = q["i"]
        rp[q["i"]] = q
    for s in moving:
        rs.append(dict(s, i=next(nid), a=remap[s["a"]], b=remap[s["b"]], join=True))

    def nbrs(i):
        return [s for s in rs if i in (s["a"], s["b"])]

    def half(s):
        return HALF.get(_KIND_OF_MESH.get(s["mesh"], ""), 400.0)

    def plain(i):  # a point along a road (never a junction's mouth: one of its links has no mesh)
        p, two = rp[i], nbrs(i)
        return len(two) == 2 and all(s["mesh"] for s in two) and not p.get("mesh") and p.get("raise", True)

    joins = 0
    ends = [remap[i] for i in moved_pts if len([s for s in moving if i in (s["a"], s["b"])]) == 1]
    for e in ends:
        if e not in rp or len(nbrs(e)) != 1:
            continue
        E = rp[e]
        own = {e}
        stack = [e]
        while stack:  # the end's own private street, never its own host
            c = stack.pop()
            for s in nbrs(c):
                o = s["b"] if s["a"] == c else s["a"]
                if o not in own and rp[o].get("join"):
                    own.add(o)
                    stack.append(o)
        best = None
        for s in rs:
            if not s["mesh"] or s["a"] in own or s["b"] in own or not s.get("raise", True):
                continue
            a, b = rp[s["a"]], rp[s["b"]]
            dx, dy = b["x"] - a["x"], b["y"] - a["y"]
            L2 = dx * dx + dy * dy
            if L2 < 1.0:
                continue
            t = max(0.0, min(1.0, ((E["x"] - a["x"]) * dx + (E["y"] - a["y"]) * dy) / L2))
            px, py = a["x"] + dx * t, a["y"] + dy * t
            d = math.hypot(E["x"] - px, E["y"] - py)
            # a street in reach wins over a private street nearer still: two drives to one street end took each
            # other's end, chained up and never reached the street (Valley, Nov 18 2026)
            key = (s["mesh"] == pr_mesh, d)
            if d <= half(s) + JOIN_REACH_CM and (best is None or key < best[0]):
                best = (key, s, t, px, py)
        if best is None:
            continue
        _d, host, t, px, py = best
        a, b = rp[host["a"]], rp[host["b"]]
        pz = a["z"] + (b["z"] - a["z"]) * t
        hx, hy = (b["x"] - a["x"]), (b["y"] - a["y"])
        hl = math.hypot(hx, hy) or 1.0
        ux, uy = hx / hl, hy / hl

        def walk(start_seg, from_pt, dist):
            """Points passed and the cut along the host from the centre, through plain points, `dist` cm one way."""
            gone, s, cur, (cx, cy), left = [], start_seg, from_pt, (px, py), dist
            while True:
                q = rp[cur]
                step = math.hypot(q["x"] - cx, q["y"] - cy)
                if step >= left:
                    if step - left < 100 and not plain(cur):
                        return None  # a cut on a junction's mouth: join that junction (no sliver of road)
                    f = left / (step or 1.0)
                    return gone, s, cur, (cx + (q["x"] - cx) * f, cy + (q["y"] - cy) * f)
                if not plain(cur):
                    return None
                left -= step
                gone.append(cur)
                cx, cy = q["x"], q["y"]
                s = next(x for x in nbrs(cur) if x is not s)
                cur = s["b"] if s["a"] == cur else s["a"]

        back = walk(host, host["a"], MOUTH_CM)
        fwd = walk(host, host["b"], MOUTH_CM)
        hub = None
        if not back or not fwd:
            # too close to a junction to cut the host open: it joins that junction as one more arm. Left as it was,
            # it was capped there, a dead end inside the junction at its own height (12 m under Valley's Riverton
            # street, Nov 18 2026)
            # (the host's own end, or the junction a mouth of it leads into; or where the host just ends, its end)
            near = [rp[host["a"]], rp[host["b"]]]
            near += [rp[o] for q in near for x in nbrs(q["i"]) if not x["mesh"]
                     for o in (x["a"], x["b"]) if o in rp and str(rp[o].get("mesh") or "").startswith("SM_Junc")]
            ok = [q for q in near if str(q.get("mesh") or "").startswith("SM_Junc") or
                  (not q.get("mesh") and len(nbrs(q["i"])) == 1)]  # (a dead end; never a junction's mouth)
            hub = min(ok, key=lambda q: math.hypot(q["x"] - E["x"], q["y"] - E["y"])) if ok else None
            if hub is None or math.hypot(hub["x"] - E["x"], hub["y"] - E["y"]) > hub["w"] + JOIN_REACH_CM:
                continue  # beside a piece, or too far off: left as it is
            px, py, pz = hub["x"], hub["y"], hub["z"]
        # the private street's side: drop its points inside the junction, the mouth on its line MOUTH_CM out
        run, s, cur = [e], next(iter(nbrs(e))), e
        while True:
            nxt = s["b"] if s["a"] == cur else s["a"]
            q = rp[nxt]
            if math.hypot(q["x"] - px, q["y"] - py) > MOUTH_CM or len(nbrs(nxt)) != 2:
                break
            run.append(nxt)
            s = next(x for x in nbrs(nxt) if x is not s)
            cur = nxt
        far = rp[nxt]
        vx, vy = far["x"] - px, far["y"] - py
        vl = math.hypot(vx, vy) or 1.0
        if vl <= MOUTH_CM + 100:
            continue  # a stub shorter than the junction: nothing to join
        mx, my = px + vx / vl * MOUTH_CM, py + vy / vl * MOUTH_CM
        if hub is not None:
            for i in run:
                rp.pop(i, None)
            rs = [x for x in rs if x is not s and x["a"] not in run and x["b"] not in run]
            if not str(hub.get("mesh") or "").startswith("SM_Junc"):
                # where the street just ends, the private street carries straight on from its end
                new = [_segment(next(nid), hub["i"], nxt, "PR", folder)]
                came = hub["i"]
            else:
                if not hub.get("join"):  # (one a join made already keeps its own undo)
                    rp[hub["i"]] = dict(hub, mesh=JUNCTION, join=True, undo={"points": [dict(hub)], "segments": []})
                m3 = _point(next(nid), mx, my, pz, math.degrees(math.atan2(vy, vx)), HALF["PR"])
                m3["join"] = True
                rp[m3["i"]] = m3
                new = [_segment(next(nid), hub["i"], m3["i"], "", folder), _segment(next(nid), m3["i"], nxt, "PR", folder)]
                came = m3["i"]
            for x in new:
                x["join"] = True
            rs += new
            _ramp(rp, rs, nxt, came, pz)
            joins += 1
            continue
        undo = {"points": [dict(rp[i]) for i in back[0] + fwd[0]],
                "segments": [dict(x) for x in rs if x["a"] in back[0] + fwd[0] or x["b"] in back[0] + fwd[0] or x is host]}
        tmpl = dict(host)
        for i in back[0] + fwd[0]:
            rp.pop(i, None)
        dead = set(back[0] + fwd[0])
        rs = [x for x in rs if x is not host and x["a"] not in dead and x["b"] not in dead]
        for i in run:
            rp.pop(i, None)
        rs = [x for x in rs if x is not s and x["a"] not in run and x["b"] not in run]
        j = _point(next(nid), px, py, pz, 0.0, max(half(host), HALF["PR"]) + 300.0, JUNCTION)
        j.update(join=True, undo=undo)
        m1 = _point(next(nid), back[3][0], back[3][1], pz, math.degrees(math.atan2(-uy, -ux)), half(host))
        m2 = _point(next(nid), fwd[3][0], fwd[3][1], pz, math.degrees(math.atan2(uy, ux)), half(host))
        m3 = _point(next(nid), mx, my, pz, math.degrees(math.atan2(vy, vx)), HALF["PR"])
        for p in (j, m1, m2, m3):
            p["join"] = True
            rp[p["i"]] = p
        new = [_segment(next(nid), j["i"], m["i"], "", folder) for m in (m1, m2, m3)]
        new.append(dict(tmpl, i=next(nid), a=back[2], b=m1["i"], ta=0.0, tb=0.0, join=True))
        new.append(dict(tmpl, i=next(nid), a=m2["i"], b=fwd[2], ta=0.0, tb=0.0, join=True))
        new.append(_segment(next(nid), m3["i"], nxt, "PR", folder))
        for x in new:
            x["join"] = True
        rs += new
        _ramp(rp, rs, nxt, m3["i"], pz)
        joins += 1
    by_i = rp
    for s in rs:
        if s.get("join") and s["a"] in by_i and s["b"] in by_i:
            st.retangent(by_i, s)
    keep = set(rp)
    rs = [s for s in rs if s["a"] in keep and s["b"] in keep]
    out_access = {"points": [ap[i] for i in rest_pts], "segments": rest_segs}
    return {"points": list(rp.values()), "segments": rs}, out_access, joins


LINK_REACH_CM = 600.0  # a walkway / driveway / sidewalk end this far past a host's edge is tied to it
# ... onto the host's own point when one is this close along it, instead of a new one: the link draws nothing, so
# it may run aslant. At 1.5 m every driveway split its street once more (a street point per house, both sides)
SNAP_CM = 2500.0


FAR_LINK_CM = 6000.0  # ... and a driveway / walkway further out than that runs on to a street this close


def join_links(roads: dict, extra: list, folder: str) -> tuple:
    """Every spline in ONE actor and none on its own: each actor in `extra` (sidewalks, walkways, driveways) moves into
    `roads`, and every loose end (one segment) is tied to the nearest road / sidewalk it reaches with a mesh-less link
    to a point on that host (the host's own point when one is within SNAP_CM, else the host is split there). The link
    draws nothing and never shapes the ground: the pieces look as they did, but form one connected spline network.
    Marked like join_private's work, so unjoin_private takes it all out again. Returns (roads, links)."""
    rp = {p["i"]: dict(p) for p in roads["points"]}
    rs = [dict(s) for s in roads["segments"]]
    nid = iter(range(max([0] + list(rp) + [s["i"] for s in rs]) + 1, 1 << 30))
    for actor in extra:  # their points and segments come over under new ids
        remap = {}
        for p in actor["points"]:
            q = dict(p, i=next(nid), join=True)
            remap[p["i"]] = q["i"]
            rp[q["i"]] = q
        for s in actor["segments"]:
            if s["a"] in remap and s["b"] in remap:
                rs.append(dict(s, i=next(nid), a=remap[s["a"]], b=remap[s["b"]], join=True))
    at: dict = {}
    for s in rs:
        at.setdefault(s["a"], []).append(s)
        at.setdefault(s["b"], []).append(s)

    def half(s):
        return HALF.get(_KIND_OF_MESH.get(s["mesh"], ""), 200.0)

    path_meshes = {ROAD_MESH[k].rsplit("/", 1)[-1] for k in ("WK", "DW", "DD", "SW")}

    def component(e):  # every point joined to e through segments that draw a mesh (its own road / path)
        seen, stack = {e}, [e]
        while stack:
            c = stack.pop()
            for s in at.get(c, []):
                o = s["b"] if s["a"] == c else s["a"]
                if o not in seen and s["mesh"]:
                    seen.add(o)
                    stack.append(o)
        return seen

    links = 0
    for e in [i for i in list(rp) if len(at.get(i, [])) == 1 and at[i][0]["mesh"] and rp[i].get("join")]:
        if len(at[e]) != 1:
            continue  # an end another end just linked to: tied already (two links once, one each way)
        E, own = rp[e], component(e)
        best = far = None
        for s in rs:
            if not s["mesh"] or s["a"] in own or s["b"] in own:
                continue
            a, b = rp[s["a"]], rp[s["b"]]
            dx, dy = b["x"] - a["x"], b["y"] - a["y"]
            L2 = dx * dx + dy * dy
            if L2 < 1.0:
                continue
            t = max(0.0, min(1.0, ((E["x"] - a["x"]) * dx + (E["y"] - a["y"]) * dy) / L2))
            px, py = a["x"] + dx * t, a["y"] + dy * t
            d = math.hypot(E["x"] - px, E["y"] - py)
            if d <= half(s) + LINK_REACH_CM and (best is None or d < best[0]):
                best = (d, s, t, px, py)
            if d <= half(s) + FAR_LINK_CM and s["mesh"] not in path_meshes and (far is None or d < far[0]):
                far = (d, s, t, px, py)
        o0 = rp[at[e][0]["b"] if at[e][0]["a"] == e else at[e][0]["a"]]
        if best is None and far and at[e][0]["mesh"] in path_meshes and \
                math.hypot(o0["x"] - far[3], o0["y"] - far[4]) > far[0] and \
                not any(not s["mesh"] for c in own for s in at.get(c, [])):  # (its street end, if not tied yet)
            # a driveway / walkway that stops short of every street (12 to 52 m, 43 at Valley, Nov 18 2026) runs on
            # in its own mesh to the nearest street's edge: a new end there, tied to it below
            k = (far[0] - half(far[1])) / (far[0] or 1.0)
            seg0 = at[e][0]
            n = _point(next(nid), E["x"] + (far[3] - E["x"]) * k, E["y"] + (far[4] - E["y"]) * k, E["z"],
                       E.get("yaw", 0.0), E.get("w", 200.0))
            n["join"] = True
            rp[n["i"]] = n
            ext = dict(seg0, i=next(nid), a=e, b=n["i"], ta=0.0, tb=0.0, join=True)
            rs.append(ext)
            at[e].append(ext)
            at[n["i"]] = [ext]
            e, E = n["i"], n
            best = (half(far[1]), *far[1:])
        if best is None:
            continue
        _d, host, t, px, py = best
        a, b = rp[host["a"]], rp[host["b"]]
        # the driveway / walkway itself reaches the host's edge (its own mesh fills the gap; the link drew nothing
        # and the slab ended metres out on the grass), along its own line when that heads for the host
        own_seg = at[e][0]
        o = rp[own_seg["b"] if own_seg["a"] == e else own_seg["a"]]
        ux, uy = E["x"] - o["x"], E["y"] - o["y"]
        ul = math.hypot(ux, uy) or 1.0
        if _d > half(host) + 30 and _d > 1:
            c = (ux * (px - E["x"]) + uy * (py - E["y"])) / (ul * _d)
            if c >= 0.5:
                s_ = (_d - half(host)) / c
                E["x"], E["y"] = E["x"] + ux / ul * s_, E["y"] + uy / ul * s_
                E["z"] = a["z"] + (b["z"] - a["z"]) * t
                st.retangent(rp, own_seg)
        L = math.hypot(b["x"] - a["x"], b["y"] - a["y"])
        if min(t, 1 - t) * L <= SNAP_CM:
            h = a if t * L <= (1 - t) * L else b
        else:  # split the host there: two halves of the same piece, one shared point
            pz = a["z"] + (b["z"] - a["z"]) * t
            h = _point(next(nid), px, py, pz, a["yaw"], a.get("w", half(host)))
            h["lines"] = [l for l in (a.get("lines") or []) if not l.startswith(("Mesh=", "MeshScale="))] or h["lines"]
            h.update(join=True, undo={"points": [], "segments": [dict(host)]})
            rp[h["i"]] = h
            rs.remove(host)
            at[host["a"]].remove(host)
            at[host["b"]].remove(host)
            for u, v in ((host["a"], h["i"]), (h["i"], host["b"])):
                ns = dict(host, i=next(nid), a=u, b=v, ta=0.0, tb=0.0, join=True)
                rs.append(ns)
                at.setdefault(u, []).append(ns)
                at.setdefault(v, []).append(ns)
                st.retangent(rp, ns)
        hz = a["z"] + (b["z"] - a["z"]) * t
        if abs(E["z"] - hz) > 50 and E.get("raise", True) and host.get("raise", True):
            # the end meets its host at the host's height, its own street graded down to it: an end kept at its own
            # height stood 3 to 13 m over or under the street it is tied to (Valley's private streets, Nov 18 2026)
            E["z"] = hz
            _ramp(rp, rs, o["i"], e, hz)
            st.retangent(rp, own_seg)
        link = _segment(next(nid), e, h["i"], "", folder, lifted=True)  # draws nothing, shapes nothing
        link["join"] = True
        rs.append(link)
        at.setdefault(e, []).append(link)
        at.setdefault(h["i"], []).append(link)
        links += 1
    # a walkway / driveway no street reaches even now stands on its own: dropped (an airport building's front walk,
    # its drive in the airport's own actor 600 m off, Valley Nov 18 2026). Nothing in a network stands apart
    seen: set = set()
    gone: set = set()
    for i in list(rp):
        if i in seen or not at.get(i):
            continue
        comp, stack = {i}, [i]
        while stack:
            c = stack.pop()
            for s in at.get(c, []):
                o = s["b"] if s["a"] == c else s["a"]
                if o not in comp:
                    comp.add(o)
                    stack.append(o)
        seen |= comp
        if all(rp[c].get("join") for c in comp) and all(s["mesh"] in path_meshes for c in comp for s in at[c]):
            gone |= comp
    rs = [s for s in rs if s["a"] not in gone and s["b"] not in gone]
    return {"points": [p for i, p in rp.items() if i not in gone], "segments": rs}, links



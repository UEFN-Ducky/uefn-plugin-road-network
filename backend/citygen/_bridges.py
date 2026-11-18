from __future__ import annotations

LONG_BRIDGE_CM = 30000.0  # a bridge over 300 m from end to end is a long one ...
BRIDGES_APART_CM = 80000.0  # ... no two long ones come within 800 m of each other ...
LONG_BRIDGES = 3  # ... and a city builds this many at most, unless a district across the water needs one more


def few_bridges(nodes: dict, edges: list, bridges: list | None = None, since: int = 0) -> tuple:
    """Every street that reached the water got its own bridge to the far bank. A city on a lake then had a
    dozen bridges a kilometre long side by side, several fanning out to one landing and crossing each other in
    the air: 15 km of bridge deck in a town with 60 km of streets (the owner's Valley, Nov 13 2026). Now:
      - no bridge crosses another;
      - first the bridges the city needs: the ones that join two parts of it no street joins (a district on the
        far bank, of LOOSE_MIN junctions or more when the bridge is a long one), shortest first. Where two of
        those would cross, the set that leaves most of the city in one piece is built;
      - then any other bridge only if it is short (creeks and rivers keep one on every street) or no long one
        (over LONG_BRIDGE_CM) comes within BRIDGES_APART_CM of it anywhere along its length: never two to one
        landing, never two side by side over the same water. And LONG_BRIDGES long ones in all, no more.
    The ones not built stop at the shore like any street with no crossing.
    bridges: the world's list of planned bridges; the entries added from index `since` for bridges not built go."""
    def crosses(j, k):
        (p, q), (a, b) = line[j], line[k]
        d = lambda o, u, v: (u[0] - o[0]) * (v[1] - o[1]) - (u[1] - o[1]) * (v[0] - o[0])
        return d(p, q, a) * d(p, q, b) < 0 and d(a, b, p) * d(a, b, q) < 0
    apart = lambda j, k: min(_dist_seg(line[j][0], *line[k]), _dist_seg(line[j][1], *line[k]),
                             _dist_seg(line[k][0], *line[j]), _dist_seg(line[k][1], *line[j]))
    line = {k: (nodes[e[0]][:2], nodes[e[1]][:2]) for k, e in enumerate(edges) if len(e) > 3 and e[3]}
    if not line:
        return nodes, edges
    length = {k: math.dist(p, q) for k, (p, q) in line.items()}
    spans = sorted(line, key=lambda k: (length[k], k))
    land: dict = {}  # each junction's part of the city, as the streets alone join it

    def part(of, n):
        while of.get(n, n) != n:
            n = of[n]
        return n
    for e in edges:
        land.setdefault(e[0], e[0])
        land.setdefault(e[1], e[1])
    for k, e in enumerate(edges):
        if k not in line:
            land[part(land, e[0])] = part(land, e[1])

    def joined(first=None):  # the needed bridges with `first` built before all others: (the biggest part's size, -deck length, which)
        of, got = dict(land), []
        sizes: dict = {}
        for n in of:
            sizes[part(of, n)] = sizes.get(part(of, n), 0) + 1
        for k in ([first] if first is not None else []) + [k for k in spans if k != first]:
            a, b = part(of, edges[k][0]), part(of, edges[k][1])
            if a == b or any(crosses(k, j) for j in got):
                continue
            if length[k] > LONG_BRIDGE_CM and min(sizes[a], sizes[b]) < LOOSE_MIN:
                continue  # (no long bridge for a corner of three streets: that corner is not built, one_network)
            got.append(k)
            of[a] = b
            sizes[b] += sizes.pop(a)
        return max(sizes.values()), -sum(length[k] for k in got), got
    needed = [k for k in spans if part(land, edges[k][0]) != part(land, edges[k][1])]
    built = max([joined()] + [joined(k) for k in needed[:40]], key=lambda t: t[:2])[2] if needed else []
    of, sizes = dict(land), {}
    for n in of:
        sizes[part(of, n)] = sizes.get(part(of, n), 0) + 1
    for k in built:
        a, b = part(of, edges[k][0]), part(of, edges[k][1])
        of[a] = b
        sizes[b] += sizes.pop(a)
    for k in spans:
        if k in built or any(crosses(k, j) for j in built):
            continue
        a, b = part(of, edges[k][0]), part(of, edges[k][1])
        if length[k] > LONG_BRIDGE_CM and (
                (a != b and min(sizes[a], sizes[b]) < LOOSE_MIN)
                or sum(1 for j in built if length[j] > LONG_BRIDGE_CM) >= LONG_BRIDGES
                or any(length[j] > LONG_BRIDGE_CM and apart(k, j) < BRIDGES_APART_CM for j in built)):
            continue
        built.append(k)
        if a != b:
            of[a] = b
            sizes[b] += sizes.pop(a)
    gone = set(line) - set(built)
    if not gone:
        return nodes, edges
    if bridges is not None:
        # (its own entry: on its line and running its way. One built right across it lies on its line too.)
        along = lambda b, p, q: abs(math.cos(math.radians(b.get("yaw", 0.0)) - math.atan2(q[1] - p[1], q[0] - p[0]))) > 0.95
        bridges[since:] = [b for b in bridges[since:]
                           if not any(_dist_seg((b["x"], b["y"]), *line[k]) < 500 and along(b, *line[k]) for k in gone)]
    edges = [e for k, e in enumerate(edges) if k not in gone]
    used = {n for e in edges for n in e[:2]}
    return {n: p for n, p in nodes.items() if n in used}, edges


LOOSE_MIN = 8  # a part of the street network that nothing joins to the rest is kept only with this many junctions
PART_BRIDGE_CM = 200000.0  # ... and such a part gets a bridge of its own to the rest when one this short will do


def join_parts(nodes: dict, edges: list, wet, kind: str, bridges: list | None = None, land: str = "") -> tuple:
    """A district on the far bank that no bridge reaches (its own street's crossing would have cut across another
    bridge, or was too long) is joined all the same: one bridge straight from one of its junctions to the nearest
    junction of the rest of the city, the shortest pair that crosses no street and no bridge, leaves neither
    junction along a street that is already there, and is no longer than PART_BRIDGE_CM. A district of fewer
    than LOOSE_MIN junctions gets none (one_network takes it out). With `land` (a street kind) a district cut off
    on dry land is joined too, by that street: Valley's Northgate lake homes and Riverton's gate were parted
    from their cities by 45 to 85 m of grass (Nov 18 2026)."""
    def crosses(p, q, a, b):
        d = lambda o, u, v: (u[0] - o[0]) * (v[1] - o[1]) - (u[1] - o[1]) * (v[0] - o[0])
        return d(p, q, a) * d(p, q, b) < 0 and d(a, b, p) * d(a, b, q) < 0
    for _round in range(8):
        adj: dict = {}
        for e in edges:
            adj.setdefault(e[0], []).append(e[1])
            adj.setdefault(e[1], []).append(e[0])
        parts, seen = [], set()
        for n in adj:
            if n not in seen:
                got, todo = [], [n]
                seen.add(n)
                while todo:
                    u = todo.pop()
                    got.append(u)
                    for m in adj[u]:
                        if m not in seen:
                            seen.add(m)
                            todo.append(m)
                parts.append(got)
        parts.sort(key=lambda g: (-len(g), str(g[0])))
        if len(parts) < 2:
            break
        lines = [(nodes[e[0]][:2], nodes[e[1]][:2], e[0], e[1]) for e in edges]
        new = None
        for far in parts[1:]:
            if len(far) < LOOSE_MIN:
                continue
            rest = [n for g in parts if g is not far and len(g) >= LOOSE_MIN for n in g]
            pairs = sorted(((math.dist(nodes[a][:2], nodes[b][:2]), str(a), str(b), a, b) for a in far for b in rest
                            if math.dist(nodes[a][:2], nodes[b][:2]) <= PART_BRIDGE_CM), key=lambda t: t[:3])
            for length, _sa, _sb, a, b in pairs[:60]:
                p, q = nodes[a][:2], nodes[b][:2]
                if any(crosses(p, q, u, v) for u, v, _m, _n in lines):
                    continue
                way = math.atan2(q[1] - p[1], q[0] - p[0])
                if any(abs(math.cos(way - math.atan2(nodes[m][1] - nodes[n][1], nodes[m][0] - nodes[n][0]))) > 0.9
                       for n in (a, b) for m in adj[n]):
                    continue  # (it would leave a junction along a street that is there: one road drawn twice)
                plans: list = []
                got_e = over_water({a: nodes[a], b: nodes[b]}, [[a, b, kind]], wet, bridges=plans, stubs=False)[1] \
                    if wet else [[a, b, kind]]
                if len(got_e) == 1 and got_e[0][:2] == [a, b]:
                    if len(got_e[0]) > 3 and got_e[0][3]:
                        new = (got_e[0], plans)
                    elif land:
                        new = ([a, b, land], [])
                if new:
                    break
            if new:
                break
        if not new:
            break
        edges = edges + [new[0]]
        if bridges is not None:
            bridges.extend(new[1])
    return nodes, edges


def one_network(nodes: dict, edges: list) -> tuple:
    """The streets as one network: a bridge with no street at one of its ends is not built (it led from a street
    to nothing, or stood on its own between two shores), and a few streets nothing joins to the rest of the
    city go too (a district of its own across the water stays)."""
    while True:
        deg: dict = {}
        for e in edges:
            deg[e[0]] = deg.get(e[0], 0) + 1
            deg[e[1]] = deg.get(e[1], 0) + 1
        bare = {id(e) for e in edges if len(e) > 3 and e[3] and (deg[e[0]] == 1 or deg[e[1]] == 1)}
        if not bare:
            break
        edges = [e for e in edges if id(e) not in bare]
    adj: dict = {}
    for e in edges:
        adj.setdefault(e[0], []).append(e[1])
        adj.setdefault(e[1], []).append(e[0])
    part: dict = {}
    sizes = []
    for n in adj:
        if n in part:
            continue
        k, todo = len(sizes), [n]
        part[n] = k
        count = 0
        while todo:
            u = todo.pop()
            count += 1
            for m in adj[u]:
                if m not in part:
                    part[m] = k
                    todo.append(m)
        sizes.append(count)
    if len(sizes) > 1:
        main = max(range(len(sizes)), key=lambda k: sizes[k])
        edges = [e for e in edges if part[e[0]] == main or sizes[part[e[0]]] >= LOOSE_MIN]
    used = {n for e in edges for n in e[:2]}
    return {n: p for n, p in nodes.items() if n in used}, edges


def _dist_seg(p, a, b):
    L2 = (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 or 1.0
    t = max(0.0, min(1.0, ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / L2))
    return math.hypot(p[0] - a[0] - t * (b[0] - a[0]), p[1] - a[1] - t * (b[1] - a[1]))


WATER_SPAN = {"6L": 300000.0, "6S": 300000.0, "FR": 300000.0, "4L": 150000.0, "2S": 60000.0, "2L": 20000.0,
              "ED": 300000.0, "ER": 300000.0, "BR": 300000.0, "BRR": 300000.0}  # longest water a road of
# this kind bridges (avenues cross a lake, local streets a river); a wider crossing is not built
SHORT_SPAN = 6000.0  # any street (not dirt) bridges a creek this narrow
SHORE_MARGIN_CM = 2500.0  # a road cut at the shore stops this far inland of the last wet sample


def cross_water(nodes: dict, edges: list, wet) -> tuple:
    """A street whose junctions fall in the water crosses it in one edge, bank to bank: from each dry node into
    the water, follow the straightest street on through the wet junctions to the first dry one and join the two
    (over_water then bridges it or, too wide for the road, drops it). Streets left in the water are removed;
    a street into open water keeps its land part (over_water cuts it at the shore)."""
    def dry(n):
        p = nodes[n]
        return (len(p) > 2 and p[2] >= BRIDGE_LIFT_CM - 1) or not wet(p[0], p[1])
    is_dry = {n: dry(n) for n in nodes}
    if all(is_dry.values()):
        return nodes, edges
    adj: dict = {}
    for e in edges:
        if len(e) > 3 and e[3]:
            continue  # an edge with its own shape (ramps, curves) keeps it
        adj.setdefault(e[0], []).append((e[1], e[2]))
        adj.setdefault(e[1], []).append((e[0], e[2]))
    out, seen, used_first = [], set(), set()
    for a in nodes:
        if not is_dry[a]:
            continue
        for b, kind in adj.get(a, []):
            if is_dry[b]:
                continue
            prev, cur, kinds = a, b, [kind]
            dx, dy = nodes[b][0] - nodes[a][0], nodes[b][1] - nodes[a][1]
            for _ in range(80):  # ponytail: straight-line walk, enough for grid and organic streets
                if is_dry[cur]:
                    break
                L = math.hypot(dx, dy) or 1.0
                best, bd = None, 0.9
                for nb, k in adj.get(cur, []):
                    if nb == prev:
                        continue
                    ux, uy = nodes[nb][0] - nodes[cur][0], nodes[nb][1] - nodes[cur][1]
                    d = (ux * dx + uy * dy) / (L * (math.hypot(ux, uy) or 1.0))
                    if d > bd:
                        best, bd = (nb, k), d
                if best is None:
                    break
                prev, cur = cur, best[0]
                kinds.append(best[1])
                dx, dy = nodes[cur][0] - nodes[a][0], nodes[cur][1] - nodes[a][1]
            if not is_dry[cur] or cur == a:  # the street breaks off in the water: the nearest dry junction
                ax, ay = nodes[a][0], nodes[a][1]  # straight ahead on the far bank, if the road may span to it
                ux, uy = nodes[b][0] - ax, nodes[b][1] - ay
                ul = math.hypot(ux, uy) or 1.0
                reach = max(WATER_SPAN.get(kind, 0.0), SHORT_SPAN) + 2 * ul
                best, bd = None, reach
                for c, p in nodes.items():
                    if not is_dry[c] or c == a or not adj.get(c):
                        continue
                    vx, vy = p[0] - ax, p[1] - ay
                    d = math.hypot(vx, vy)
                    if ul < d < bd and (vx * ux + vy * uy) / (d * ul) > 0.985:
                        best, bd = c, d
                if best is None:
                    continue
                cur = best
            used_first.add((a, b))
            key = frozenset((a, cur))
            if key not in seen:
                seen.add(key)
                out.append([a, cur, max(kinds, key=lambda k: HALF.get(k, 0.0))])
    for e in edges:
        a, b = e[0], e[1]
        if (len(e) > 3 and e[3]) or (is_dry[a] and is_dry[b]):
            out.append(e)
        elif is_dry[a] != is_dry[b] and ((a, b) not in used_first and (b, a) not in used_first):
            out.append(e)  # into open water: over_water keeps the part on land
    used = {n for e in out for n in e[:2]}
    return {n: p for n, p in nodes.items() if n in used}, out


def wet_from(ter: dict):
    """(x, y) -> True where the terrain is under its water line; None when the terrain has no water."""
    sea = (ter or {}).get("sea")
    if sea is None or not (ter or {}).get("heights"):
        return None
    return lambda x, y: (st.sample_grid(ter, x, y) if st.sample_grid(ter, x, y) is not None else sea + 1) < sea


def over_water(nodes: dict, edges: list, wet, span: dict | None = None, bridges: list | None = None,
               stubs: bool = True) -> tuple:
    """Roads know where the water is. An edge that runs over water becomes a bridge (ramp up 40 m, deck
    BRIDGE_LIFT_CM over the water, ramp down) when its kind may span that far (WATER_SPAN; any paved street
    over a creek <= SHORT_SPAN) and it is not already high enough there; otherwise it is removed (a side street
    stops at the shore). An end in the water is cut back to the shore (new node ('shore', a, b, 0|1)).
    span: {kind: cm} overrides (highways pass a long one). stubs=False (city streets): a street whose end is in
    the water goes altogether, back to its last junction, instead of running down the bank as a dead end."""
    if not wet:
        return nodes, edges
    span = {**WATER_SPAN, **(span or {})}
    out, extra = [], {}
    for e in edges:
        a, b, kind = e[0], e[1], e[2]
        via = list(e[3]) if len(e) > 3 and e[3] else []
        line = [list(nodes[a][:2]) + [float(nodes[a][2]) if len(nodes[a]) > 2 else 0.0]] + \
               [[v[0], v[1], float(v[2]) if len(v) > 2 else 0.0] for v in via] + \
               [list(nodes[b][:2]) + [float(nodes[b][2]) if len(nodes[b]) > 2 else 0.0]]
        cum, samples = 0.0, []  # (distance along, x, y, lift)
        for p, q in zip(line, line[1:]):
            L = math.dist(p[:2], q[:2])
            n = max(1, int(L // 1000))
            for k in range(n):
                t = k / n
                samples.append((cum + L * t, p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t, p[2] + (q[2] - p[2]) * t))
            cum += L
        samples.append((cum, *line[-1]))
        runs, cur = [], None
        for s, x, y, up in samples:
            if up < BRIDGE_LIFT_CM - 1 and wet(x, y):
                cur = [s, s] if cur is None else [cur[0], s]
            elif cur:
                runs.append(cur)
                cur = None
        if cur:
            runs.append(cur)
        if not runs:
            out.append(e)
            continue
        def at(s):
            for (s0, *p), (s1, *q) in zip(samples, samples[1:]):
                if s0 <= s <= s1:
                    t = (s - s0) / ((s1 - s0) or 1)
                    return [p[i] + (q[i] - p[i]) * t for i in range(3)]
            return list(samples[-1][1:])

        vcum = _cum_of(line)[1:-1]
        # an end in the water: the road is cut back to the shore (a freeway running out into a lake ends there)
        s_a, s_b, na, nb, first = 0.0, cum, a, b, kind
        if runs[0][0] <= 0:
            s_a = runs.pop(0)[1] + SHORE_MARGIN_CM
            na = ("shore", a, b, 0)
            first = next((v[3] for s, v in reversed(list(zip(vcum, via))) if s <= s_a and len(v) > 3 and v[3]), kind)
        if runs and runs[-1][1] >= cum:
            s_b = runs.pop()[0] - SHORE_MARGIN_CM
            nb = ("shore", a, b, 1)
        if s_b - s_a < 3000 or (s_a > 0 and s_b < cum and s_a >= s_b):
            continue  # nothing left on land
        if not stubs and (na != a or nb != b):
            continue  # a city street never runs out to the water's edge and stops: it ends at the cross street before
        limit = max(span.get(kind, 0.0), SHORT_SPAN if kind not in ("DT", "1W", "1S") else 0.0)
        if runs and max(r1 - r0 for r0, r1 in runs) > limit:
            continue  # too wide for this road: a side street stops at the shore (its neighbours still reach it)
        for key, s in ((na, s_a), (nb, s_b)):
            if key not in (a, b):
                p = at(s)
                extra[key] = [round(p[0]), round(p[1])] + ([round(p[2])] if p[2] > 0 else [])
        deck, ramp = structure_kinds(kind)
        spans = []
        for r0, r1 in runs:  # merge runs closer than two ramps: one longer bridge
            if spans and r0 - spans[-1][1] < 15000:
                spans[-1][1] = r1
            else:
                spans.append([r0, r1])
        spans = [[max(s_a, r0 - 3000), min(s_b, r1 + 3000)] for r0, r1 in spans]  # deck reaches past the shore
        # BridgeGenerator: the type and deck clearance follow the span (navigation needs height), the ramps are
        # long enough for the road's grade (6 %), never shorter than the 40 m ramp piece
        from ..waterfront import bridge_kind
        plan = []
        for r0, r1 in spans:
            btype, lift = bridge_kind(max(0.0, r1 - r0 - 6000), kind)  # by the water it spans, not its landings
            ramp_len = max(4000.0, lift / 0.06)
            plan.append((r0, r1, lift, ramp_len, btype))
        keep = [(s, v) for s, v in zip(vcum, via)
                if not any(r0 - rl - 1000 < s < r1 + rl + 1000 for r0, r1, _, rl, _ in plan)]
        for r0, r1, lift, rl, btype in plan:
            fa, fb = max(s_a + 1, r0 - rl), min(s_b - 1, r1 + rl)  # ramp feet, never past the edge's ends
            pa, pb = at(fa), at(fb)
            top = max(lift, at(r0)[2], at(r1)[2])
            d0, d1 = max(r0, fa + rl), min(r1, fb - rl)  # deck ends where the ramps reach the top
            if d1 - d0 < 1000:  # a junction close to the bank: the ramps meet mid-span on a lower deck
                d0 = d1 = (fa + fb) / 2  # ponytail: short approaches trade clearance for an 8 % ramp
                top = min(top, max(300.0, (d0 - fa) * 0.08))
            keep += [(fa, [pa[0], pa[1], pa[2], ramp])]
            keep += [(d0, at(d0)[:2] + [top, deck]), (d1, at(d1)[:2] + [top, ramp])] if d1 > d0 else \
                [(d0, at(d0)[:2] + [top, ramp])]
            keep += [(fb, [pb[0], pb[1], pb[2], kind])]
            if bridges is not None:
                mid = at((r0 + r1) / 2)
                a_, b_ = at(r0), at(r1)
                yaw = math.degrees(math.atan2(b_[1] - a_[1], b_[0] - a_[0]))
                bridges.append({"type": btype, "kind": kind, "span_m": round((r1 - r0) / 100), "clearance_m": top / 100,
                                "x": round(mid[0]), "y": round(mid[1]), "yaw": round(yaw, 1), "deck_z_rel": top,
                                # on the deck as built (a straight chord between its ends), never on the planned
                                # polyline beside it: towers stood 10-20 m off the deck
                                "towers": [on_deck(at(d0), at(d1), (r0 + (r1 - r0) * f - d0) / ((d1 - d0) or 1.0))
                                           for f in (0.3, 0.7)]})
        keep.sort(key=lambda r: r[0])
        out.append([na, nb, first, [[round(v[0]), round(v[1])] + list(v[2:]) for s, v in keep if s_a < s < s_b]])
    nodes = {**nodes, **extra}
    used = {n for e in out for n in e[:2]}
    return {n: p for n, p in nodes.items() if n in used}, out


def on_deck(a, b, u: float) -> list:
    """A point u (0..1, clamped) of the way along a deck from a to b, in plan."""
    u = max(0.0, min(1.0, u))
    return [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u]


PIER_EVERY_CM = 3000.0  # a pier bent under a deck every 30 m
PIER_MIN_CM = 300.0  # no pier where the deck is lower than this over the ground (an approach on its fill)
DECK_DEPTH_CM = 150.0  # road surface to the deck's underside: the pier cap sits under that
CAP_CM = 120.0  # the cap beam's depth
GIRDER_CM = 120.0  # a bridge's girders under its slab (road_profiles); the pier cap carries them
ABUTMENT_CM = 250.0  # an abutment's wall, from the deck's underside down into the bank
FILL_STEP_CM = 500.0  # an approach wall's blocks under a low deck, one this long at a time (they overlap a little)
EMBED_CM = 100.0  # how far a wall or a foundation goes into the ground under its lowest corner


def _ground_roads(actors: dict):
    """in_road(x, y, pad): is this spot on a road that lies on the ground, or within `pad` cm of its edge."""
    cell = 5000.0
    grade: dict = {}  # roads on the ground, by 50 m cell
    for a in actors.values():
        by = {p["i"]: p for p in a["points"]}
        for s in a["segments"]:
            mesh = (s.get("mesh") or "").split("/")[-1]
            pa, pb = by.get(s["a"]), by.get(s["b"])
            if mesh and mesh not in STRUCTURES and pa and pb:
                h = HALF.get(next((k for k, m in ROAD_MESH.items() if m.split("/")[-1] == mesh), ""), 800.0)
                for gx in range(int(min(pa["x"], pb["x"]) // cell) - 1, int(max(pa["x"], pb["x"]) // cell) + 2):
                    for gy in range(int(min(pa["y"], pb["y"]) // cell) - 1, int(max(pa["y"], pb["y"]) // cell) + 2):
                        grade.setdefault((gx, gy), []).append((pa, pb, h))

    def in_road(x, y, pad=250.0):
        for pa, pb, h in grade.get((int(x // cell), int(y // cell)), ()):
            ux, uy = pb["x"] - pa["x"], pb["y"] - pa["y"]
            t = max(0.0, min(1.0, ((x - pa["x"]) * ux + (y - pa["y"]) * uy) / (ux * ux + uy * uy or 1.0)))
            if math.hypot(x - pa["x"] - ux * t, y - pa["y"] - uy * t) < h + pad:
                return True
        return False
    return in_road


def deck_fills(actors: dict, ground, sea=None) -> list:
    """A deck's or ramp's own side walls where it is too low for piers: an approach wall under it, from the ground
    up into its slab, as wide as the slab's flat underside. So a ramp coming down to its street, or a freeway deck
    a few metres over the land, stands on solid concrete the way a ground road stands on its skirts, never on air.
    Left open: over water (a bridge), over a road it crosses, where the deck already lies on the ground, and where
    it is high enough for piers (bridge_piers takes over there, the same test).
    Site rows [x, y, base z, yaw, length_m, width_m, height_m] for SM_Kit_ApproachWall (1 x 1 x 1 m at scale 1,
    its origin at the middle of its base)."""
    rows = []
    in_road = _ground_roads(actors)
    for a in actors.values():
        by = {p["i"]: p for p in a["points"]}
        for s in a["segments"]:
            mesh = (s.get("mesh") or "").split("/")[-1]
            pa, pb = by.get(s["a"]), by.get(s["b"])
            if mesh not in STRUCTURES or not pa or not pb:
                continue
            half = HALF.get(next((k for k, m in ROAD_MESH.items() if m.split("/")[-1] == mesh), ""), 800.0)
            L = math.hypot(pb["x"] - pa["x"], pb["y"] - pa["y"])
            if L < 100:
                continue
            yaw = round(math.degrees(math.atan2(pb["y"] - pa["y"], pb["x"] - pa["x"])), 1)
            ux, uy = (pb["x"] - pa["x"]) / L, (pb["y"] - pa["y"]) / L
            inner = half - min(150.0, half * 0.3)  # the slab's flat underside (road_profiles.shape 'deck' tapers in)
            n = max(1, round(L / FILL_STEP_CM))
            rise = abs(pb["z"] - pa["z"]) / n / 2  # a block's higher end: its flat top goes up into the sloping slab there
            for k in range(n):
                t = (k + 0.5) / n
                x, y = pa["x"] + (pb["x"] - pa["x"]) * t, pa["y"] + (pb["y"] - pa["y"]) * t
                top = pa["z"] + (pb["z"] - pa["z"]) * t - DECK_DEPTH_CM - (GIRDER_CM if "Bridge" in mesh else 0.0)
                spots = [(x - uy * o, y + ux * o) for o in (-inner, 0.0, inner)]
                feet = [ground(px, py) for px, py in spots]
                if any(f is None for f in feet):
                    continue
                low = min(feet)
                if sea is not None and low < sea:
                    continue  # over water: a bridge stands on its piers
                if top - low < 20 or top - CAP_CM - low >= PIER_MIN_CM:
                    continue  # on the ground already, or high enough to stand on piers
                if any(in_road(px, py, -50.0) for px, py in spots):
                    continue  # a road runs under it here: the deck spans it
                rows.append([round(x), round(y), round(low - EMBED_CM), yaw, round(L / n * 1.04 / 100, 2),
                             round(inner * 2 / 100, 2), round((top + rise + 5 - low + EMBED_CM) / 100, 2)])
    return rows


def bridge_piers(actors: dict, ground) -> tuple:
    """Real bridges: under every deck and elevated ramp a pier bent every PIER_EVERY_CM, from the ground (or the
    lake bed) up to the deck's underside: a cap beam across the deck's width and one column (two under a deck
    16 m or wider), turned with the deck. Returns (columns, caps) as site rows [x, y, z, yaw, sx, sy, sz] for
    SM_Kit_BridgePier (200 x 200 x 100 cm at scale 1) and SM_Kit_PierCap (200 x 100 x 120 cm)."""
    cols, caps = [], []
    in_road = _ground_roads(actors)  # a pier never stands in a road the deck crosses
    for label, a in actors.items():
        by = {p["i"]: p for p in a["points"]}
        for s in a["segments"]:
            mesh = (s.get("mesh") or "").split("/")[-1]
            pa, pb = by.get(s["a"]), by.get(s["b"])
            if mesh not in STRUCTURES or not pa or not pb:
                continue
            half = HALF.get(next((k for k, m in ROAD_MESH.items() if m.split("/")[-1] == mesh), ""), 800.0)
            L = math.hypot(pb["x"] - pa["x"], pb["y"] - pa["y"])
            if L < 100:
                continue
            yaw = math.degrees(math.atan2(pb["y"] - pa["y"], pb["x"] - pa["x"]))
            ux, uy = (pb["x"] - pa["x"]) / L, (pb["y"] - pa["y"]) / L
            n = max(1, int(L // PIER_EVERY_CM))
            for k in range(n):
                t = (k + 0.5) / n
                x, y = pa["x"] + (pb["x"] - pa["x"]) * t, pa["y"] + (pb["y"] - pa["y"]) * t
                # the deck's underside, or its girders' on a bridge
                top = pa["z"] + (pb["z"] - pa["z"]) * t - DECK_DEPTH_CM - (GIRDER_CM if "Bridge" in mesh else 0.0)
                legs = [0.0] if half < 800 else [-half * 0.55, half * 0.55]
                feet = [ground(x - uy * o, y + ux * o) or 0.0 for o in legs]
                if top - CAP_CM - min(feet) < PIER_MIN_CM:
                    continue  # the deck is on (or just over) the ground here: no pier
                if any(in_road(x - uy * o, y + ux * o) for o in legs):
                    continue  # over a road it crosses: the deck spans it
                caps.append([round(x), round(y), round(top - CAP_CM), round(yaw, 1), 1.0, round(half * 2 * 0.9 / 100, 2), 1.0])
                for o, f in zip(legs, feet):
                    if top - CAP_CM - f >= 50:  # a leg standing on a bank higher than the cap is left out
                        cols.append([round(x - uy * o), round(y + ux * o), round(f), round(yaw, 1), 1.0, 1.0,
                                     round((top - CAP_CM - f) / 100, 2)])
    return cols, caps


TOWER_OVER_CM = {"cable-stayed bridge": 6000.0, "suspension bridge": 9000.0}  # a tower's top over its deck
TOWER_LEG_CM = (400.0, 300.0)  # a tower leg: along the deck, across it
TOWER_CLEAR_CM = 150.0  # open air between the deck's edge and a leg
STAY_EVERY_CM = 2500.0  # a stay cable about every 25 m of deck
STAY_REACH = 0.19  # each fan reaches this share of the span from its tower: towers at 0.3 and 0.7 all but meet
STAY_WIDE = 2.5  # a stay's Y scale: the 12 cm bar becomes a 30 cm cable


def _decks(actors: dict) -> list:
    """Every bridge and viaduct segment: (ax, ay, az, bx, by, bz, half width, road surface to its underside)."""
    out = []
    for a in actors.values():
        by = {p["i"]: p for p in a["points"]}
        for s in a["segments"]:
            mesh = (s.get("mesh") or "").split("/")[-1]
            pa, pb = by.get(s["a"]), by.get(s["b"])
            if mesh in STRUCTURES and pa and pb:
                half = HALF.get(next((k for k, m in ROAD_MESH.items() if m.split("/")[-1] == mesh), ""), 800.0)
                out.append((pa["x"], pa["y"], pa["z"], pb["x"], pb["y"], pb["z"], half,
                            DECK_DEPTH_CM + (GIRDER_CM if "Bridge" in mesh else 0.0)))
    return out


def _deck_at(decks: list, x: float, y: float, within: float = 1500.0):
    """The deck over a spot (or within `within` cm beside it): (x, y on its centre line, z, half width, ux, uy,
    depth to its underside), or None."""
    best = None
    for ax, ay, az, bx, by, bz, half, deep in decks:
        L2 = (bx - ax) ** 2 + (by - ay) ** 2
        if L2 < 1:
            continue
        t = max(0.0, min(1.0, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / L2))
        px, py = ax + (bx - ax) * t, ay + (by - ay) * t
        d = math.hypot(x - px, y - py)
        if d <= within and (best is None or d < best[0]):
            L = math.sqrt(L2)
            best = (d, px, py, az + (bz - az) * t, half, (bx - ax) / L, (by - ay) / L, deep)
    return best[1:] if best else None


def bridge_towers(actors: dict, bridges: list, ground, sea=None) -> dict:
    """Real towers for the cable-stayed and suspension bridges (`bridges`: over_water's records, each with its two
    tower spots on the deck). A tower stands ASTRIDE the deck, never in it: a leg outside each edge, from the
    ground or the bed to 60 / 90 m over the road, joined by a beam under the deck (the deck rests on it), one
    high over the road and one at the top. From each leg's top a fan of stay cables runs down to that edge of
    the deck, both ways along it. Site rows [x, y, z, yaw, sx, sy, sz]:
      legs   SM_Kit_BridgePier (200 x 200 x 100 cm at scale 1)
      beams  SM_Kit_PierCap (200 x 100 x 120 cm)
      stays  SM_Kit_BridgeStay: a bar from 1 m up over its origin down to 1 m out along +X, so sx = how far out
             the cable lands (m) and sz = how high over the deck it starts (m); z is the deck's surface there
      spans  (x, y, ux, uy, reach, half) per tower: the deck its cables carry (under_cables: no pier there)."""
    out = {"legs": [], "beams": [], "stays": [], "spans": []}
    decks = _decks(actors)
    la, lc = TOWER_LEG_CM
    for b in bridges or []:
        over = TOWER_OVER_CM.get(b.get("type"))
        if not over or not b.get("towers"):
            continue
        span = float(b.get("span_m") or 0) * 100
        near = [d for d in decks if min(math.hypot(d[0] - b["x"], d[1] - b["y"]),
                                        math.hypot(d[3] - b["x"], d[4] - b["y"])) < span + 20000]
        reach = STAY_REACH * span
        n = max(3, min(8, round(reach / STAY_EVERY_CM)))
        for spot in b["towers"]:
            at = _deck_at(near, spot[0], spot[1])
            if not at:
                continue  # the deck is not there any more (the road was changed by hand): no tower in open water
            tx, ty, z, half, ux, uy, deep = at
            nx, ny = -uy, ux
            yaw = round(math.degrees(math.atan2(uy, ux)), 1)
            top = z + over
            off = half + TOWER_CLEAR_CM + lc / 2
            feet = []
            for side in (1, -1):
                lx, ly = tx + nx * off * side, ty + ny * off * side
                foot = ground(lx, ly)
                if foot is None:
                    foot = (sea if sea is not None else z) - 500.0
                feet.append((side, lx, ly))
                out["legs"].append([round(lx), round(ly), round(foot), yaw, la / 200, lc / 200, round((top - foot) / 100, 2)])
            for bz, tall in ((z - deep - 250.0, 250.0), (z + over * 0.55, 250.0), (top - 300.0, 300.0)):
                out["beams"].append([round(tx), round(ty), round(bz), yaw, round(la * 0.8 / 200, 2),
                                     round((2 * off + lc) / 100, 2), round(tall / 120, 2)])
            for side, lx, ly in feet:
                for way in (1, -1):
                    for k in range(1, n + 1):
                        d = reach * k / n
                        deck = _deck_at(near, tx + ux * d * way, ty + uy * d * way, 600.0)
                        if not deck:
                            continue  # past the deck's end: no cable into the air
                        ax, ay = deck[0] + nx * (half - 40.0) * side, deck[1] + ny * (half - 40.0) * side
                        up = top - 200.0 - (n - k) * 220.0 - deck[2]  # the longest cable leaves from the very top
                        run = math.hypot(ax - lx, ay - ly)
                        if up < 500 or run < 300:
                            continue
                        out["stays"].append([round(lx), round(ly), round(deck[2]), round(math.degrees(math.atan2(ay - ly, ax - lx)), 2),
                                             round(run / 100, 2), STAY_WIDE, round(up / 100, 2)])
            out["spans"].append((tx, ty, ux, uy, reach, half))
    return out


def under_cables(spans: list):
    """(x, y) -> True under a stretch of deck that hangs from a tower's cables: no pier bent stands there."""
    def held(x: float, y: float) -> bool:
        for tx, ty, ux, uy, reach, half in spans:
            if abs((x - tx) * ux + (y - ty) * uy) <= reach + 300 and abs(-(x - tx) * uy + (y - ty) * ux) <= half + 300:
                return True
        return False
    return held


def bridge_abutments(actors: dict) -> list:
    """Where a bridge, viaduct or ramp meets the road on the ground: an abutment under the deck's end, its wall
    across the deck's width and a wing wall back along each side of the approach. Site rows
    [x, y, z, yaw, 1, width_m, 1] for SM_Kit_BridgeAbutment (1 m wide at scale 1, ABUTMENT_CM tall, its wall's
    face at x 0 looking along +X under the deck)."""
    rows = []
    for a in actors.values():
        by = {p["i"]: p for p in a["points"]}
        at: dict = {}
        for s in a["segments"]:
            mesh = (s.get("mesh") or "").split("/")[-1]
            for i, j in ((s["a"], s["b"]), (s["b"], s["a"])):
                if mesh and i in by and j in by:
                    at.setdefault(i, []).append((mesh, by[j]))
        for i, legs in at.items():
            decks = [(m, q) for m, q in legs if m in STRUCTURES]
            if not decks or len(decks) == len(legs):
                continue  # no deck here, or the deck runs on
            p, (mesh, q) = by[i], decks[0]
            half = HALF.get(next((k for k, m in ROAD_MESH.items() if m.split("/")[-1] == mesh), ""), 800.0)
            rows.append([round(p["x"]), round(p["y"]), round(p["z"] - DECK_DEPTH_CM - ABUTMENT_CM),
                         round(math.degrees(math.atan2(q["y"] - p["y"], q["x"] - p["x"])), 1), 1.0, round(half * 2 / 100 + 1.0, 2), 1.0])
    return rows


def structure_kinds(kind: str) -> tuple:
    """(deck, approach) for a road of this kind crossing water or another road: a bridge as wide as the road it
    carries. A four-lane or wider road rides the wide deck piece all the way up and across (the two-lane bridge
    and the one-lane ramp under an avenue stepped 10-27 m in width at each end); a street takes the bridge pieces;
    a ramp stays a ramp."""
    if kind == "ER":
        return "ER", "ER"
    if kind == "6S":
        return "BW", "BW"
    return ("ED", "ED") if HALF.get(kind, 0.0) >= 1400.0 else ("BR", "BRR")



from __future__ import annotations


PARK_MIN_R = 100000.0  # a city of this radius or more has one big park in its centre district ...
PARK_BIG_R = 180000.0  # ... two blocks by three; from this radius two by four
PARK_AT = 0.6  # ... about this far from the middle of that district to its edge


def central_park(city: dict, o: dict, wet=None, grid: tuple | None = None):
    """The city's one big park, like the one in the middle of Manhattan: a rectangle of the centre district's own
    blocks with no street through it. (ix0, iy0, ix1, iy1): the grid lines of its four sides, or None (a small
    city, or no room). Wholly inside the centre district, on dry land, never across one of the city's two main
    axes or a freeway and the streets beside it. Its place is the city's own draw: the same every time.
    grid: (nodes, ring) of the street grid when the caller has them (city_graph); else worked out here."""
    R, down, seed = city["radius"], o["downtown"], city.get("seed", 1)
    if R < PARK_MIN_R:
        return None
    if grid is None:
        at, kx, ky = street_grid(city, o)
        nodes, ring = {}, {}
        for ix in range(-kx, kx + 1):
            for iy in range(-ky, ky + 1):
                wx, wy, share = at(ix, iy)
                if share <= down and not (city.get("yield_to") and yielded(city, wx, wy)):
                    nodes[(ix, iy)], ring[(ix, iy)] = [wx, wy], share
    else:
        nodes, ring = grid
    fw_x, fw_y = _grid_local(city, o)[2]
    long_ = 4 if R >= PARK_BIG_R else 3
    best = None
    for nx, ny in ((2, long_), (long_, 2)):
        for ix0, iy0 in nodes:
            ix1, iy1 = ix0 + nx, iy0 + ny
            if ix0 < 0 < ix1 or iy0 < 0 < iy1:
                continue  # the two main streets run on through the city
            if (fw_x is not None and ix0 - 1 < fw_x < ix1 + 1) or (fw_y is not None and iy0 - 1 < fw_y < iy1 + 1):
                continue
            ks = [(i, j) for i in range(ix0, ix1 + 1) for j in range(iy0, iy1 + 1)]
            if any(k not in nodes or ring[k] > down for k in ks) or (wet and any(wet(*nodes[k][:2]) for k in ks)):
                continue
            off = abs(sum(ring[k] for k in ks) / len(ks) - PARK_AT * down)
            key = (round(off / 0.04), random.Random(f"park:{seed}:{ix0}:{iy0}:{nx}").random())
            if best is None or key < best[0]:
                best = (key, (ix0, iy0, ix1, iy1))
    return best[1] if best else None


def city_graph(city: dict, opts: dict | None = None, wet=None, bridges: list | None = None,
               virtual: list | None = None) -> tuple:
    """Street network of one city: nodes {key: [x, y]}, edges [(a, b, kind)]. Real cities mix patterns
    (urban-design transect): a strict grid of short blocks in the core, a looser grid in the
    neighbourhoods, curving streets with cul-de-sacs in the suburbs, all inside an irregular outline.
    Downtown and the two main axes are four-lane; suburbs are two-lane; the rest has pavements.
    virtual (a list to fill): the streets left out between neighbouring blocks (_street_gaps), as
    [node, node, [x, y], [x, y]]: no road, but still the line the land is parted into blocks along."""
    o = city_options(city, opts)
    down, normal = o["downtown"], max(o["normal"], o["downtown"])
    rnd = random.Random(city.get("seed", 1))
    style = city.get("style", "mixed")
    rnd.uniform(0, math.pi / 2)  # the seed's own grid angle (seed_turn): drawn first, as always
    at, kx, ky = street_grid(city, o)  # the grid at the city's angle, its streets bowed (never jogged)
    nodes, ring = {}, {}
    for ix in range(-kx, kx + 1):
        for iy in range(-ky, ky + 1):
            wx, wy, share = at(ix, iy)
            if share > 1.0:
                continue  # outside this city's irregular edge
            if city.get("yield_to") and yielded(city, wx, wy):
                continue  # a bigger city's land: its streets are the streets there
            nodes[(ix, iy)] = [wx, wy]
            ring[(ix, iy)] = share
    edges = []
    for (ix, iy) in nodes:
        for nb in ((ix + 1, iy), (ix, iy + 1)):
            if nb not in nodes:
                continue
            share = max(ring[(ix, iy)], ring[nb])
            axis = (ix == 0 == nb[0]) or (iy == 0 == nb[1])  # arterials: the two main axes
            line = ix if nb[0] == ix else iy  # the grid line this street runs along
            collector = line % o["collector_every"] == 0
            if not axis and collector and share > down:  # collectors: a wider street every Nth line
                kind = o["neighbourhood_roads"] if share > normal else "4L"
            elif axis or share <= down:
                kind = o["core_roads"]
                # one-way pairs: every other side street in the core is a narrow one-way lane
                if not axis and o["one_way_core"] == "yes" and (ix if nb[0] == ix else iy) % 2:
                    kind = "1W"
            else:
                kind = o["suburb_roads"] if share > normal else o["neighbourhood_roads"]
            edges.append([(ix, iy), nb, kind])
    # the one big park: no street runs through it. The streets round it stay, whatever is thinned out below.
    # (on the same water city_layout reads: the lakes and the city's own canals)
    park, rim = central_park(city, o, water_of(city, o, wet), (nodes, ring)), set()
    if park:
        px0, py0, px1, py1 = park
        in_park = lambda n: px0 <= n[0] <= px1 and py0 <= n[1] <= py1
        on_rim = lambda a, b: (a[0] == b[0] and a[0] in (px0, px1)) or (a[1] == b[1] and a[1] in (py0, py1))
        edges = [e for e in edges if not (in_park(e[0]) and in_park(e[1]) and not on_rim(e[0], e[1]))]
        for n in [n for n in nodes if px0 < n[0] < px1 and py0 < n[1] < py1]:
            del nodes[n]
        rim = {(e[0], e[1]) for e in edges if in_park(e[0]) and in_park(e[1])}
    if o["superblocks"] == "yes":
        def drop(e):
            a, b, _ = e
            line = a[0] if b[0] == a[0] else a[1]
            axis = (a[0] == 0 == b[0]) or (a[1] == 0 == b[1])
            return (max(ring[a], ring[b]) > down and line % 2 != 0 and line % o["collector_every"] != 0
                    and not axis)
        edges = [e for e in edges if not drop(e)]
        # suburbs go further: most whole collector cells there keep no public street inside at all. Each is one
        # subdivision (neighbourhoods 'collector'): a winding street through it, cul-de-sacs off that
        ce, big, fwp = o["collector_every"], {}, freeway_plan(city, o)

        def superblock(g):
            if g not in big:
                ks = [(g[0] * ce + i, g[1] * ce + j) for i in range(ce + 1) for j in range(ce + 1)]
                inner = lambda n: 0 < n[0] - g[0] * ce < ce and 0 < n[1] - g[1] * ce < ce  # its edge may be in town
                big[g] = all(n in nodes and (ring[n] > normal or not inner(n)) for n in ks) and not (
                    fwp.lines and fwp.blocked(*nodes[(g[0] * ce + ce // 2, g[1] * ce + ce // 2)][:2], 0.75 * ce * max(block_xy(o)))
                ) and random.Random(f"superblock:{city.get('seed', 1)}:{g}").random() < SUPERBLOCK_SHARE
            return big[g]
        edges = [e for e in edges if (e[0][0] if e[1][0] == e[0][0] else e[0][1]) % ce == 0
                 or not superblock((e[0][0] // ce, e[0][1] // ce))]
        # (in a Grid city as well: its streets stay ruled, but its subdivisions need no ring of roads each either)
        edges = _street_gaps(city, o, nodes, ring, edges, {g for g, yes in big.items() if yes}, virtual,
                             [ic["at"] for ic in fwp.interchanges if ic.get("b_axis")])
    hills = city.get("hills") if style != "grid" and o["follow_hills"] else None
    if hills:
        # no local street straight up a steep hillside: the ones that would climb steeper than STEEP_STREET are
        # left out (never a main road, never downtown in a mixed city), so the blocks there lie along the hill
        def too_steep(e):
            a, b, kind = e[0], e[1], e[2]
            up_v = a[0] == b[0]
            line = a[0] if up_v else a[1]
            if line % o["collector_every"] == 0 or (a[0] == 0 == b[0]) or (a[1] == 0 == b[1]) or \
                    (style == "mixed" and max(ring[a], ring[b]) <= down):
                return False
            return (abs(hill_at(hills, *a)[3 if up_v else 2]) + abs(hill_at(hills, *b)[3 if up_v else 2])) / 2 > STEEP_STREET
        edges = [e for e in edges if not too_steep(e)]
        if not o["prune"]:  # (pruning keeps the network in one piece below; without it, here)
            adj: dict = {}
            for e in edges:
                adj.setdefault(e[0], []).append(e[1])
                adj.setdefault(e[1], []).append(e[0])
            seen, todo = set(), [min(adj, key=lambda n: abs(n[0]) + abs(n[1]))] if adj else []
            while todo:
                n = todo.pop()
                if n not in seen:
                    seen.add(n)
                    todo.extend(adj.get(n, []))
            edges = [e for e in edges if e[0] in seen]
            nodes = {n: p for n, p in nodes.items() if n in seen}
    if style != "grid" and o["prune"]:
        # neighbourhoods drop some side streets (never a main one, never a dead end); suburbs drop more and
        # may leave cul-de-sacs, like real subdivisions
        degree = {}
        for a, b, _ in edges:
            degree[a] = degree.get(a, 0) + 1
            degree[b] = degree.get(b, 0) + 1
        kept = []
        for e in edges:
            a, b, kind = e
            suburb = min(ring[a], ring[b]) > normal
            floor = 1 if suburb else 2  # a suburb street may end in a cul-de-sac
            chance = o["prune"] * (1.6 if suburb else 1.0)
            line = a[0] if b[0] == a[0] else a[1]
            local = kind != o["core_roads"] and kind != "4L" and line % o["collector_every"] != 0 and (a, b) not in rim
            if local and degree[a] > floor and degree[b] > floor and rnd.random() < chance:
                degree[a] -= 1
                degree[b] -= 1
                continue
            kept.append(e)
        edges = kept
        # keep the one connected street network that holds the centre: pruning may cut a corner off
        adj: dict = {}
        for a, b, _ in edges:
            adj.setdefault(a, []).append(b)
            adj.setdefault(b, []).append(a)
        start = min(adj, key=lambda n: abs(n[0]) + abs(n[1])) if adj else None
        seen, todo = set(), [start] if start is not None else []
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            seen.add(n)
            todo.extend(adj.get(n, []))
        edges = [e for e in edges if e[0] in seen]
        nodes = {n: p for n, p in nodes.items() if n in seen}
    if o["suburbs"] == "winding" and style != "grid":
        # the suburbs are no grid: public winding streets from the grid's last junctions out to the city's edge.
        # Everything below (water, shores, freeways, widths, one network) and every lot, house and PCG layer
        # treats them as the streets they are
        nodes, edges = winding_suburbs(city, o, nodes, ring, edges, water_of(city, o, wet))
        if virtual:  # (no block lines of the grid that is gone there)
            virtual[:] = [v for v in virtual if max(ring.get(v[0], 0), ring.get(v[1], 0)) <= normal]
    if city.get("hills"):
        # a dead end up a hillside no house fits on goes, back to where the ground eases: a road out of town ran up
        # a mountain to a turnaround with nothing along it (Nov 20 2026)
        while True:
            deg: dict = {}
            for e in edges:
                deg[e[0]] = deg.get(e[0], 0) + 1
                deg[e[1]] = deg.get(e[1], 0) + 1
            dead = {n for n, k in deg.items() if k == 1 and n in nodes and hill_grade(city, o, *nodes[n][:2]) > SUB_STEEP}
            if not dead:
                break
            edges = [e for e in edges if e[0] not in dead and e[1] not in dead]
            nodes = {n: p for n, p in nodes.items() if n not in dead}
    yards = (city.get("shipyards") or []) + (city.get("shipyards_near") or [])
    fields = city.get("airports") or []
    if yards or fields:  # shipyards and airports are dedicated districts with their own roads
        from .. import airport, shipyard
        # (a settled yard is a walled platform with its bank round it: no street on either, nor 15 m from them)
        drop = {n for n, p in nodes.items()
                if any(shipyard.reserved(y, p[0], p[1], 1500) if y.get("z") is not None
                       else shipyard.inside(y, p[0], p[1], -2000) for y in yards)
                or any(airport.inside(a, p[0], p[1], 3000) for a in fields)}
        edges = [e for e in edges if e[0] not in drop and e[1] not in drop]
        used = {n for e in edges for n in e[:2]}
        nodes = {n: p for n, p in nodes.items() if n in used}
    cw = canal_wet(city, o)
    if cw:  # a canal takes its street line; the streets crossing it run straight over it on a bridge
        on_canal = {n for n, p in nodes.items() if isinstance(n, tuple) and len(n) == 2 and cw(*p[:2])}
        kept, arms = [], {}
        for e in edges:
            a, b = e[0], e[1]
            if a in on_canal and b in on_canal:
                continue  # the street the canal replaced
            if a in on_canal or b in on_canal:
                mid, other = (a, b) if a in on_canal else (b, a)
                arms.setdefault(mid, []).append((other, e[2]))
                continue
            kept.append(e)
        for mid, ends in arms.items():  # (ix-1, iy) - [canal] - (ix+1, iy): one street with a canal bridge
            for (p, kp), (q, kq) in ((ends[i], ends[j]) for i in range(len(ends)) for j in range(i + 1, len(ends))):
                if isinstance(p, tuple) and isinstance(q, tuple) and len(p) == len(q) == 2 and p[1] == q[1] == mid[1]:
                    kept.append([p, q, kp])
        edges = kept
        used = {n for e in edges for n in e[:2]}
        nodes = {n: p for n, p in nodes.items() if n in used}
        wet = water_of(city, o, wet)
    if wet:  # streets cross water on bridges (one edge bank to bank), the rest stop at the shore
        planned_before = len(bridges) if bridges is not None else 0
        nodes, edges = over_water(*cross_water(nodes, edges, wet), wet, bridges=bridges, stubs=False)
        nodes, edges = few_bridges(nodes, edges, bridges, planned_before)
        nodes, edges = join_parts(nodes, edges, wet, o["core_roads"], bridges)
    if city.get("shores"):
        # no street on a made shore where the land was cut down for it: it ends at the top of the bank, above
        # the promenade (laid there it stood on a ramp 10 m over the quay). A bridge keeps both its ends: a
        # crossing is never cut off for a promenade.
        from .. import waterfront
        look = waterfront.Made([city])
        spans = {n for e in edges if len(e) > 3 and e[3] for n in e[:2]}
        drop = {n for n, p in nodes.items() if n not in spans and look.no_street(p[0], p[1])}
        if drop and spans:
            # ... nor is its own street: a bridge that lands on a made shore kept its end there and lost every
            # street that led to it, so it stood at the water joined to nothing (Nov 12 2026). From each end
            # that would be left bare, the shortest way inland to a junction that stays is kept.
            adj: dict = {}
            for e in edges:
                if not (len(e) > 3 and e[3]):
                    adj.setdefault(e[0], []).append(e[1])
                    adj.setdefault(e[1], []).append(e[0])
            for end in sorted(spans, key=str):
                if any(m not in drop for m in adj.get(end, ())):
                    continue  # a street still leaves it
                came, todo, found = {end: None}, [end], None
                while todo and found is None:
                    nxt = []
                    for n in todo:
                        for m in adj.get(n, ()):
                            if m in came:
                                continue
                            came[m] = n
                            if m not in drop and m not in spans:
                                found = m
                                break
                            nxt.append(m)
                        if found is not None:
                            break
                    todo = nxt
                while found is not None:
                    drop.discard(found)
                    found = came[found]
        if drop:
            edges = [e for e in edges if e[0] not in drop and e[1] not in drop]
            used = {n for e in edges for n in e[:2]}
            nodes = {n: p for n, p in nodes.items() if n in used}
    # freeways last: every street that water, canals, an airport or a shipyard took is gone by now, so an
    # interchange whose cross road is gone is dropped before any street is cut for its ramps
    fw = freeway_plan(city, o)
    if fw.lines:
        def crosses(ic):
            bx, by = ic["b_axis"]
            for e in edges:
                p, q = nodes[e[0]], nodes[e[1]]
                L = math.dist(p[:2], q[:2]) or 1.0
                if abs((q[0] - p[0]) * bx + (q[1] - p[1]) * by) / L > 0.97 and \
                        _dist_seg(ic["at"], p[:2], q[:2]) < 1500:
                    return True
            return False
        fw.drop({ic["group"] for ic in fw.interchanges if ic.get("b_axis") and not crosses(ic)})
        # the cross road keeps its interchange width through the whole interchange (ramps were sized for it)
        for ic in fw.interchanges:
            if not ic.get("b_axis"):
                continue
            bx, by = ic["b_axis"]
            for e in edges:
                p, q = nodes[e[0]], nodes[e[1]]
                L = math.dist(p[:2], q[:2]) or 1.0
                along = abs((q[0] - p[0]) * bx + (q[1] - p[1]) * by) / L
                mx, my = (p[0] + q[0]) / 2 - ic["at"][0], (p[1] + q[1]) / 2 - ic["at"][1]
                off = abs(mx * by - my * bx)  # distance of the edge's middle from the cross road's line
                if along > 0.97 and off < 1500 and math.hypot(mx, my) < 20000 and HALF.get(e[2], 0) < HALF[ic["cross_road"]]:
                    e[2] = ic["cross_road"]
        # a street a low ramp or an at-grade freeway would cut gives way (streets pass under viaducts)
        edges = [e for e in edges if not fw.street_blocked(nodes[e[0]], nodes[e[1]], HALF.get(e[2], 500.0))]
        used = {n for e in edges for n in e[:2]}
        nodes = {n: p for n, p in nodes.items() if n in used}
    if city.get("fw_near"):  # ... and to a neighbouring city's ramps and frontage roads where they reach over its land
        cut = _near_cuts(city)
        edges = [e for e in edges if not cut(nodes[e[0]], nodes[e[1]], HALF.get(e[2], 500.0))]
        used = {n for e in edges for n in e[:2]}
        nodes = {n: p for n, p in nodes.items() if n in used}
    # a street keeps one width between junctions: where two blocks of different kinds meet at a plain point
    # (no side street left there) the narrower takes the wider's kind, so no road steps in width mid-block
    at: dict = {}
    for e in edges:
        for n in e[:2]:
            at.setdefault(n, []).append(e)
    changed = True
    while changed:
        changed = False
        for es in at.values():
            if len(es) == 2 and es[0][2] != es[1][2] and "DT" not in (es[0][2], es[1][2]):
                wide = max(es, key=lambda e: HALF.get(e[2], 0.0))[2]
                for e in es:
                    if e[2] != wide:
                        e[2] = wide
                        changed = True
    for e in edges:  # every city street carries its sidewalks in its own mesh
        e[2] = CITY_STREET.get(e[2], e[2])
        if len(e) > 3 and e[3]:  # and its bridge pieces follow its final kind: a street widened after its
            deck, ramp = structure_kinds(e[2])  # bridge was planned rode a narrower deck (a 7 m width jump)
            for v in e[3]:
                if len(v) > 3 and v[3]:
                    v[3] = deck if v[3] in ("BR", "ED", "BW") else ramp if v[3] == "BRR" else e[2]
    edges = _no_fans(nodes, edges)
    # no street laid along another: two that run the same way, share no junction and lie closer than their half
    # widths together are one road drawn twice (a bridge beside the shore street it left): the narrower goes. (At
    # 0.6 of them two of Riverton's 17.5 m streets stayed 11.6 m apart, pavements over each other, Nov 17 2026)
    geo = [(nodes[e[0]][:2], nodes[e[1]][:2], HALF.get(e[2], 500.0)) for e in edges]
    cell, near, lost = 20000.0, {}, set()
    for k, (p, q, _h) in enumerate(geo):
        near.setdefault((int((p[0] + q[0]) / 2 // cell), int((p[1] + q[1]) / 2 // cell)), []).append(k)
    for k, (p, q, h) in enumerate(geo):
        mx, my, ux, uy = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2, q[0] - p[0], q[1] - p[1]
        ul = math.hypot(ux, uy)
        for j in (j for gx in (-1, 0, 1) for gy in (-1, 0, 1) for j in near.get((int(mx // cell) + gx, int(my // cell) + gy), ())):
            if j == k or k in lost or j in lost or ul < 300 or {edges[k][0], edges[k][1]} & {edges[j][0], edges[j][1]}:
                continue
            p2, q2, h2 = geo[j]
            vx, vy = q2[0] - p2[0], q2[1] - p2[1]
            vl = math.hypot(vx, vy)
            if vl < 300 or abs(ux * vx + uy * vy) / (ul * vl) < 0.94:
                continue
            t = max(0.0, min(1.0, ((mx - p2[0]) * vx + (my - p2[1]) * vy) / (vl * vl)))
            if math.hypot(mx - p2[0] - vx * t, my - p2[1] - vy * t) < h + h2:
                lost.add(k if (h, ul) <= (h2, vl) else j)
    if lost:
        edges = [e for k, e in enumerate(edges) if k not in lost]
        used = {n for e in edges for n in e[:2]}
        nodes = {n: p for n, p in nodes.items() if n in used}
    nodes, edges = one_network(nodes, edges)
    if virtual:
        # the land is not parted where it is no land of the city's blocks: water, a shipyard, an airport, a made
        # shore, a freeway's ramps (the streets there are gone the same way)
        from .. import airport, shipyard, waterfront
        look = waterfront.Made([city]) if city.get("shores") else None

        def gone(v) -> bool:
            for p in (v[2], v[3]):
                if (wet and wet(p[0], p[1])) or (look and look.no_street(p[0], p[1])) or \
                        any(shipyard.reserved(y, p[0], p[1], 1500) if y.get("z") is not None
                            else shipyard.inside(y, p[0], p[1], -2000) for y in yards) or \
                        any(airport.inside(a, p[0], p[1], 3000) for a in fields):
                    return True
            return bool(fw.lines and fw.street_blocked(v[2], v[3], 500.0))
        virtual[:] = [v for v in virtual if not gone(v)]
    return nodes, edges


FAN_DEG = 20.0  # two streets leaving one junction closer together than this overlap from its mouth on


def _no_fans(nodes: dict, edges: list) -> list:
    """No two streets out of one junction the same way: the same two junctions joined twice (Valley's Riverton drew
    both, bowed apart, Nov 17 2026) or a second street fanned off a few degrees beside the first. The overlap check
    after this skips streets that share a junction, so it never saw them. Bridges first, then the wider, then the
    longer street stays.
    ponytail: a dropped street that alone reached a corner leaves it to one_network (it goes, or is joined again)."""
    rank = sorted(range(len(edges)), key=lambda k: (not (len(edges[k]) > 3 and edges[k][3]), -HALF.get(edges[k][2], 500.0),
                                                    -math.dist(nodes[edges[k][0]][:2], nodes[edges[k][1]][:2]), k))
    out, kept, lim = {}, set(), math.radians(FAN_DEG)
    for k in rank:
        a, b = edges[k][0], edges[k][1]
        ends = [(n, math.atan2(nodes[m][1] - nodes[n][1], nodes[m][0] - nodes[n][0])) for n, m in ((a, b), (b, a))]
        if a == b or any(abs((t - u + math.pi) % (2 * math.pi) - math.pi) < lim for n, t in ends for u in out.get(n, ())):
            continue
        kept.add(k)
        for n, t in ends:
            out.setdefault(n, []).append(t)
    return [e for k, e in enumerate(edges) if k in kept]


SUB_STEP = 4500.0  # winding suburb streets: a point every 45 m
SUB_CLEAR = 7000.0  # ... never closer than this to another street's point (two rows of lots fit between)
SUB_OFF = 4000.0  # ... nor this close to a street of the grid
SUB_SEED_GAP = 20000.0  # a collector leaves the grid about every 200 m
SUB_KEY = 10 ** 6
SUB_STEEP = 0.2  # a winding street stops where the hillside is steeper than this: no house lot fits there


def hill_grade(city: dict, o: dict, x: float, y: float) -> float:
    """The ground's grade at a world point, from the city's hills (plan_hills) at the nearest grid crossing; 0 with none.
    ponytail: nearest crossing, a block apart; sample the ground itself if streets need finer."""
    hills = city.get("hills")
    if not hills:
        return 0.0
    bx, by = block_xy(o)
    t = math.radians(orientation(city))
    dx, dy = x - city["x"], y - city["y"]
    g = hill_at(hills, round((math.cos(t) * dx + math.sin(t) * dy) / bx), round((-math.sin(t) * dx + math.cos(t) * dy) / by))
    return math.hypot(g[2], g[3])


def winding_suburbs(city: dict, o: dict, nodes: dict, ring: dict, edges: list, wet=None) -> tuple:
    """The suburbs as an organic street network instead of the grid: past the normal city every grid street goes
    (the two main axes stay: the roads out of town), and from the grid's last junctions winding collectors grow
    out to the city's edge, local streets branch off them on both sides (crescents; some close a loop on a
    neighbour), and short cul-de-sacs fill what room is left. The further out, the more a street winds. Nodes are
    (SUB_KEY + n, 0, 0) (three ints: they sort with the grid's (ix, iy) wherever nodes are ordered); all are public streets, so lots front them (frontage_lots) and every end gets its turnaround.
    No street climbs onto a hillside steeper than SUB_STEEP (a mountain): no house would fit along it."""
    normal = max(o["normal"], o["downtown"])
    outer = lambda e: max(ring[e[0]], ring[e[1]]) > normal and not ((e[0][0] == 0 == e[1][0]) or (e[0][1] == 0 == e[1][1]))
    cut, edges, old = [e for e in edges if outer(e)], [e for e in edges if not outer(e)], nodes
    used = {n for e in edges for n in e[:2]}
    nodes = {n: p for n, p in old.items() if n in used}
    rnd = random.Random(f"suburbs:{city.get('seed', 1)}")
    core = city.get("core") or (0, 0)
    cx, cy = city["x"] + core[0], city["y"] + core[1]
    kind, cell, grid, adj, count = o["suburb_roads"], SUB_CLEAR, {}, {}, 0
    turn = lambda a, b: (a - b + math.pi) % (2 * math.pi) - math.pi

    def put(x, y, key):
        grid.setdefault((int(x // cell), int(y // cell)), []).append((x, y, key))

    for n, p in nodes.items():
        put(p[0], p[1], n)
    for e in edges:  # (and along the grid's streets, so none is crossed between its junctions)
        p, q = nodes[e[0]], nodes[e[1]]
        L = math.dist(p[:2], q[:2])
        for k in range(1, int(L // 3000)):
            put(p[0] + (q[0] - p[0]) * k * 3000 / L, p[1] + (q[1] - p[1]) * k * 3000 / L, None)

    def hit(x, y, skip):
        best = None
        for gx in (-1, 0, 1):
            for gy in (-1, 0, 1):
                for px, py, key in grid.get((int(x // cell) + gx, int(y // cell) + gy), ()):
                    d = math.hypot(px - x, py - y)
                    if key not in skip and d < (SUB_OFF if key is None else SUB_CLEAR) and (best is None or d < best[0]):
                        best = (d, key)
        return best

    def grow(start, h, steps, depth, skip):
        nonlocal count
        skip, last, line = set(skip) | {start}, start, []
        x, y = nodes[start][:2]
        ph, w, c = rnd.uniform(0, 2 * math.pi), rnd.uniform(0.5, 0.9), rnd.uniform(-0.22, 0.22)
        for i in range(steps):
            far = max(0.0, min(1.0, (zone_share(city, x, y) - normal) / max(1e-6, 1.0 - normal)))
            if depth == 0:  # a collector heads out of town, swinging wider the further out it is
                h += 0.2 * turn(math.atan2(y - cy, x - cx), h) + (0.1 + 0.4 * far) * math.sin(ph + i * w)
            elif i:  # a local street keeps one bend: a crescent
                h += c * (0.4 + 0.6 * far)
            nx, ny = x + SUB_STEP * math.cos(h), y + SUB_STEP * math.sin(h)
            if zone_share(city, nx, ny) > 0.97 or (wet and wet(nx, ny)) or (city.get("yield_to") and yielded(city, nx, ny)) \
                    or hill_grade(city, o, nx, ny) > SUB_STEEP:
                break
            got = hit(nx, ny, skip)
            if got:
                key = got[1]  # another winding street in the way: close the loop on it, or end here (a cul-de-sac)
                if depth and line and key is not None and len(key) == 3 and key not in adj.get(last, ()) and \
                        math.dist(nodes[last][:2], nodes[key][:2]) < 2.2 * SUB_STEP and rnd.random() < 0.6:
                    edges.append([last, key, kind])
                    adj.setdefault(last, set()).add(key)
                    adj.setdefault(key, set()).add(last)
                break
            key = (SUB_KEY + count, 0, 0)
            count += 1
            nodes[key] = [nx, ny]
            put(nx, ny, key)
            skip.add(key)
            edges.append([last, key, kind])
            adj.setdefault(last, set()).add(key)
            adj.setdefault(key, set()).add(last)
            line.append((key, h))
            last, x, y = key, nx, ny
        return line

    seeds = []  # where a street out of the grid was cut: (junction that stays, the way that street ran)
    for e in cut:
        if (e[0] in used) != (e[1] in used):
            s, gone = (e[0], e[1]) if e[0] in used else (e[1], e[0])
            if all(math.dist(nodes[s][:2], nodes[t][:2]) >= SUB_SEED_GAP for t, _h in seeds):
                seeds.append((s, math.atan2(old[gone][1] - old[s][1], old[gone][0] - old[s][0])))
    level = [grow(s, h, 60, 0, ()) for s, h in seeds]
    for depth in (1, 2):  # local streets off the collectors, then cul-de-sacs off those where there is room
        nxt = []
        for line in level:
            for i, (key, h) in enumerate(line[1:], 1):
                for side in ((1 if i % 2 else -1,) if depth == 1 else (1, -1)):
                    if depth == 2 and rnd.random() > 0.6:
                        continue
                    steps = rnd.randint(3, 8) if depth == 1 else rnd.randint(1, 3)
                    nxt.append(grow(key, h + side * (math.pi / 2 + rnd.uniform(-0.25, 0.25)), steps, depth, adj.get(key, ())))
        level = nxt
    return nodes, edges


# Of the streets that would run between two neighbouring blocks, how many are not built: between two whole
# subdivisions (superblocks), between two blocks of the suburbs, between two of the neighbourhoods round downtown
GAPS = {"super": 0.55, "housing": 0.5, "normal": 0.35}


def _street_gaps(city: dict, o: dict, nodes: dict, ring: dict, edges: list, supers: set, virtual: list | None,
                 interchanges: list | None = None) -> list:
    """The street edges without the streets that are not built. Past downtown every block of the grid was ringed
    by its own square of roads, a subdivision or a complex inside each: housing on top of a chequerboard. Real
    neighbourhoods lie side by side with nothing but back fences between them. So of the streets between two
    neighbouring blocks many are left out (GAPS): each block is still laid out on its own ground and turned to a
    side that has a street (city_layout, neighbourhoods), and the streets that remain take the traffic.
    Never: downtown, a stretch of the city's two axes, the two streets beside a freeway, an interchange's cross
    road within three blocks of it (`interchanges`: where each one is), a block's last two streets, a crossing's
    last two roads, the street that holds the network together. The stretches left out are added to `virtual`. nodes: the grid crossings {(ix, iy): ...}; ring: each
    one's share of the way to the city's edge; supers: the collector cells that are one subdivision."""
    ce, down, normal = o["collector_every"], o["downtown"], max(o["normal"], o["downtown"])
    at = {}
    for e in edges:
        at[(e[0], e[1])] = at[(e[1], e[0])] = e
    fw_x, fw_y = _grid_local(city, o)[2]
    bx, by = block_xy(o)
    turn = math.radians(orientation(city))
    cross = set()  # ('x' | 'y', grid line) of every interchange's cross road
    for x, y in interchanges or []:
        gx = ((x - city["x"]) * math.cos(turn) + (y - city["y"]) * math.sin(turn)) / bx
        gy = (-(x - city["x"]) * math.sin(turn) + (y - city["y"]) * math.cos(turn)) / by
        if fw_x is not None and abs(gx - fw_x) < 0.6:
            cross.add(("y", round(gy)))
        if fw_y is not None and abs(gy - fw_y) < 0.6:
            cross.add(("x", round(gx)))
    cells = {n for n in nodes if (n[0] + 1, n[1]) in nodes and (n[0], n[1] + 1) in nodes and (n[0] + 1, n[1] + 1) in nodes}
    east = lambda c: ((c[0] + 1, c[1]), (c[0] + 1, c[1] + 1))  # the street between a cell and the one east of it
    north = lambda c: ((c[0], c[1] + 1), (c[0] + 1, c[1] + 1))
    block: dict = {}  # cell -> its block: the cells no street parts
    members: list = []
    for c in sorted(cells):
        if c in block:
            continue
        block[c], todo, got = len(members), [c], []
        while todo:
            u = todo.pop()
            got.append(u)
            for v, e in (((u[0] + 1, u[1]), east(u)), ((u[0], u[1] + 1), north(u)),
                         ((u[0] - 1, u[1]), east((u[0] - 1, u[1]))), ((u[0], u[1] - 1), north((u[0], u[1] - 1)))):
                if v in cells and v not in block and e not in at:
                    block[v] = len(members)
                    todo.append(v)
        members.append(got)
    sides: dict = {}  # (block, block, 'x' | 'y', grid line) -> the street between the two, piece by piece
    real = [set() for _ in members]  # each block's sides that have a street
    for c in sorted(cells):
        me = block[c]
        for v, e, way, line in (((c[0] + 1, c[1]), east(c), "x", c[0] + 1), ((c[0], c[1] + 1), north(c), "y", c[1] + 1)):
            if e in at and v in cells and block[v] != me:
                key = (min(me, block[v]), max(me, block[v]), way, line)
                sides.setdefault(key, []).append(e)
                real[me].add(key)
                real[block[v]].add(key)
            elif e in at and v not in cells:
                real[me].add((me, None, way, line))
        for v, e, way, line in (((c[0] - 1, c[1]), east((c[0] - 1, c[1])), "x", c[0]), ((c[0], c[1] - 1), north((c[0], c[1] - 1)), "y", c[1])):
            if v not in cells and e in at:
                real[me].add((me, None, way, line))
    zone, whole = [], []
    for got in members:
        lo = min(ring[n] for c in got for n in (c, (c[0] + 1, c[1]), (c[0], c[1] + 1), (c[0] + 1, c[1] + 1)))
        zone.append("housing" if lo > normal else "normal" if lo > down else "core")
        c0 = min(got)
        whole.append(len(got) == ce * ce and (c0[0] // ce, c0[1] // ce) in supers)
    todo = []
    for key in sorted(sides):
        ra, rb, way, line = key
        es = sorted(sides[key])
        if line == 0 or "core" in (zone[ra], zone[rb]) or min(len(members[ra]), len(members[rb])) < 4:
            continue  # an axis, downtown, or a block of the fine grid
        beside, across = (fw_x, fw_y) if way == "x" else (fw_y, fw_x)  # the freeway it runs along, the one it meets
        if any(p[1] != q[0] for p, q in zip(es, es[1:])) or (beside is not None and abs(line - beside) < 1.0) or \
                ((way, line) in cross and min(abs(n[1 if way == "x" else 0] - across) for e in es for n in e) < 3.5):
            continue  # (not one straight stretch; a street beside a freeway; an interchange's cross road)
        rate = GAPS["super"] if whole[ra] and whole[rb] else GAPS["housing"] if zone[ra] == zone[rb] == "housing" else GAPS["normal"]
        # (each stretch has its own draw, from where it is: a change in one corner of the city does not re-deal
        # the streets of the rest)
        draw = random.Random(f"gap:{city.get('seed', 1)}:{way}:{line}:{es[0][0]}").random()
        if draw < rate:
            todo.append((draw, key, es[0][0], es[-1][1], [at[e] for e in es]))
    todo.sort(key=lambda t: (t[0], t[1][2], t[1][3], t[2]))
    adj: dict = {}
    for e in edges:
        adj.setdefault(e[0], set()).add(e[1])
        adj.setdefault(e[1], set()).add(e[0])
    gone = set()
    for _draw, key, a, b, es in todo:
        if len(real[key[0]]) < 3 or len(real[key[1]]) < 3 or len(adj[a]) < 3 or len(adj[b]) < 3:
            continue
        cut = {(e[0], e[1]) for e in es} | {(e[1], e[0]) for e in es}
        seen, walk = {a}, [a]
        while walk and b not in seen:  # still one network without it?
            n = walk.pop()
            for m in adj[n]:
                if m not in seen and (n, m) not in cut:
                    seen.add(m)
                    walk.append(m)
        if b not in seen:
            continue
        real[key[0]].discard(key)
        real[key[1]].discard(key)
        for e in es:
            adj[e[0]].discard(e[1])
            adj[e[1]].discard(e[0])
            gone.add(id(e))
            if virtual is not None:
                virtual.append([e[0], e[1], list(nodes[e[0]][:2]), list(nodes[e[1]][:2])])
    return [e for e in edges if id(e) not in gone]



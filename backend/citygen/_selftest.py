from __future__ import annotations

def _demo() -> None:
    cities = place_cities(3, 3, ((-200000, -200000), (200000, 200000)))
    assert len(cities) == 3 and all(c["style"] in AUTO_STYLES for c in cities), "no generated city is a ruled grid"
    ter = terrain(3, 400000, 6000, cities, grid=64)
    assert ter["heights"] == terrain(3, 400000, 6000, [], grid=64)["heights"], "natural ground ignores the cities"
    flat = terrain(3, 400000, 6000, cities, grid=64, opts={"city_ground": "flatten"})
    # islands: the land comes from noise alone (the cities never shape it), sea at the map's edge, and every city
    # is moved onto dry land
    for mode in ("one", "many"):
        isl = terrain(3, 400000, 6000, cities, grid=64, opts={"island": mode})
        assert isl["heights"] == terrain(3, 400000, 6000, [], grid=64, opts={"island": mode})["heights"], mode
        wet_i = wet_from(isl)
        assert isl["sea"] == 0.0 and all(wet_i(x, y) for x, y in ((-195000, -195000), (195000, 195000))), mode
        moved = [dict(c) for c in cities]
        towns_on_land(moved, island_land(3, 400000, (0.0, 0.0), mode), ((-200000, -200000), (200000, 200000)))
        assert not any(wet_i(c["x"] + c["radius"] * math.cos(a), c["y"] + c["radius"] * math.sin(a))
                       for c in moved for a in (k * math.pi / 4 for k in range(8))), (mode, "cities on land")
    for c in cities:
        z, z2 = st.sample_grid(flat, c["x"], c["y"]), st.sample_grid(flat, c["x"] + c["radius"] * 0.5, c["y"])
        assert abs(z - z2) < 50, "flatten: a city sits on a flat plateau"
    actors = generate(cities, lambda x, y: st.sample_grid(ter, x, y) or 0.0, "/Game/X/WorldGen/Roads", seed=3)
    assert set(actors) == {f"{c['name']} Roads" for c in cities} | {"Highways"}
    # natural ground graded to the roads: every street point sits on the ground, no road on a wall
    gt = grade_to_roads(ter, actors)
    off = [abs(p["z"] - st.sample_grid(gt, p["x"], p["y"])) for c in cities for p in actors[f"{c['name']} Roads"]["points"]
           if abs(p["z"] - st.sample_grid(ter, p["x"], p["y"])) < 2000]
    before = [abs(p["z"] - st.sample_grid(ter, p["x"], p["y"])) for c in cities for p in actors[f"{c['name']} Roads"]["points"]]
    assert sorted(off)[int(len(off) * 0.95)] < 150, ("graded ground meets the streets", max(off), max(before))
    # a dead end's turnaround on a hillside: the ground is cut level across its whole round, not just the road's width
    slope = {"grid": 40, "min": [-10000, -10000], "max": [10000, 10000], "heights": [[i * 50.0 for i in range(40)] for _ in range(40)]}
    road = ROAD_MESH["2S"].split("/")[-1]
    hill = {"T Roads": {"points": [{"i": 0, "x": -8000, "y": 0, "z": 0, "yaw": 0, "pitch": 0, "w": 875, "mesh": ""},
                                   {"i": 1, "x": -1200, "y": 0, "z": 0, "yaw": 180, "pitch": 0, "w": 875, "mesh": ""},
                                   {"i": 2, "x": 0, "y": 0, "z": 0, "yaw": 0, "pitch": 0, "w": 1425, "mesh": "SM_JK_2"}],
                        "segments": [{"i": 0, "a": 0, "b": 1, "mesh": road, "ta": 6800, "tb": -6800},
                                     {"i": 1, "a": 2, "b": 1, "mesh": "", "ta": 1200, "tb": -1200}]}}
    for x in hill["T Roads"]["points"] + hill["T Roads"]["segments"]:
        x["lines"] = []
    hg = grade_to_roads(slope, hill)
    assert all(abs(st.sample_grid(hg, 1000 * math.cos(a), 1000 * math.sin(a)) + ROAD_SINK_CM) < 60
               for a in (0.0, 1.57, -1.57, 0.8)), "the turnaround's whole round is cut level"
    # a Blender interchange's pad (a model link, wider than it is long) cut 50 m into a 20 % hillside leaves a
    # bank all round, past its ends too, never a cliff (ROAD_BANK)
    side = {"grid": 40, "min": [-10000, -10000], "max": [10000, 10000], "heights": [[i * 100.0 for i in range(40)] for _ in range(40)]}
    cut = {"C Freeways": {"points": [{"i": 0, "x": 0, "y": -1000, "z": -3000, "yaw": 90, "pitch": 0, "w": 875, "mesh": "", "lines": []},
                                     {"i": 1, "x": 0, "y": 1000, "z": -3000, "yaw": 90, "pitch": 0, "w": 875, "mesh": "", "lines": []}],
                          "segments": [{"i": 0, "a": 0, "b": 1, "mesh": "", "ta": 2000, "tb": -2000, "half": 3000, "lines": []}]}}
    pad = grade_to_roads(side, cut)["heights"]
    for line in (pad[20], [r[20] for r in pad]):  # across it, and on past its end
        assert max(abs(b - a) for a, b in zip(line, line[1:])) / 500 < 1.0, ("bank round a pad", [round(z) for z in line])
    # the ground never covers a street between its points either (at a world's own grid, ~37 m a cell)
    fine = terrain(3, 400000, 6000, cities, grid=108)
    fa = generate(cities[:1], lambda x, y: st.sample_grid(fine, x, y) or 0.0, "/Game/X/WorldGen/Roads", seed=3)
    fg = grade_to_roads(fine, fa)
    over = []
    a = fa[f"{cities[0]['name']} Roads"]
    by = {p["i"]: p for p in a["points"]}
    for s in a["segments"]:
        p, q = by[s["a"]], by[s["b"]]
        for t in (0.25, 0.5, 0.75):
            x, y, z = (p[k] + (q[k] - p[k]) * t for k in ("x", "y", "z"))
            over.append(st.sample_grid(fg, x, y) - z)
    over.sort()
    assert over[int(len(over) * 0.95)] < 40, ("ground over the road", over[int(len(over) * 0.95)], over[-1])
    for label, a in actors.items():
        bad = st.problems(a["points"], a["segments"])
        assert not any(bad.values()), (label, {k: len(v) for k, v in bad.items()})
        back = st.parse(st.serialize(a["points"], a["segments"]))
        assert len(back["points"]) == len(a["points"]) and len(back["segments"]) == len(a["segments"])
    city = actors[f"{cities[0]['name']} Roads"]
    assert st.pending_nodes(city["points"], city["segments"]), "junctions wait for generated pieces"
    assert len(actors["Highways"]["segments"]) >= 2
    # super-blocks: outside the core, blocks are about twice as wide (room for subdivisions / complexes)
    from .. import parcels
    sb = {"x": 0, "y": 0, "radius": 80000, "seed": 5, "style": "grid"}
    sn, se = city_graph(sb)
    areas = {"core": [], "out": []}
    for face in parcels.blocks(sn, se):
        poly = [sn[n] for n in face]
        cx, cy = sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)
        areas["core" if zone_share(sb, cx, cy) < 0.3 else "out"].append(abs(parcels._area(poly)))
    assert max(areas["core"]) < 1.5e8 < max(areas["out"]), {k: max(v) for k, v in areas.items()}
    # planned grid: streets bow, they never jog. Along one street the heading changes a little from block to block
    # (always a spline bend, never an elbow), no block is squeezed thin, and collectors run every 4th line
    block = OPTIONS["block_m"][0] * 100
    ce = OPTIONS["collector_every"][0]
    main_bent: dict = {}
    core_bent: dict = {}
    for style in ("mixed", "organic"):
        city = {"x": 0, "y": 0, "radius": 60000, "seed": 11, "style": style, "turn": 0.0}
        gn, ge = city_graph(city, {"wobble": 9, "freeways": "no"})  # asked for more: clamped to 0.45
        bent = 0.0
        for a, b, _ in ge:
            L = math.dist(gn[a], gn[b])
            assert block * 0.5 <= L <= block * 1.5, (style, L)
        for (ix, iy), p in gn.items():
            for da in ((1, 0), (0, 1)):
                q, r = gn.get((ix + da[0], iy + da[1])), gn.get((ix + 2 * da[0], iy + 2 * da[1]))
                if q and r:
                    turn = abs((math.degrees(math.atan2(r[1] - q[1], r[0] - q[0]) - math.atan2(q[1] - p[1], q[0] - p[0])) + 180) % 360 - 180)
                    assert turn < CORNER_DEG, (style, "a street bends as a spline, never an elbow", turn)
                    bent = max(bent, turn)
                    if (iy if da[0] else ix) % ce == 0:
                        main_bent[style] = max(main_bent.get(style, 0.0), turn)
                        if max(abs(ix + da[0]), abs(iy + da[1])) <= 1:
                            core_bent[style] = max(core_bent.get(style, 0.0), turn)
        assert bent > 5, (style, "streets between the main roads curve", bent)
        assert {"4L"} <= {k for _, _, k in ge}, "collectors / arterials exist"
    # streets follow the hills. A town on a round hill 60 m high: every street across the slope bends to run
    # along the hillside (the ground along it rises and falls less than along the plain grid line), no street
    # turns into an elbow for it, and nothing moves on flat ground or in a Grid town
    dome = lambda x, y: 6000.0 * math.exp(-(x * x + y * y) / (2 * 30000.0 ** 2))
    quiet = {"wobble": 0, "freeways": "no", "prune": 0, "superblocks": "no", "follow_hills": 1}
    hill_town = {"x": 0, "y": 0, "radius": 60000, "seed": 11, "style": "organic", "turn": 0.0}
    plan_hills([hill_town], dome, quiet)
    assert hill_town.get("hills") and len(hill_town["hills"]["u"]) == (2 * hill_town["hills"]["k"][0] + 1) * (2 * hill_town["hills"]["k"][1] + 1)
    import json as _json
    assert _json.loads(_json.dumps(hill_town["hills"])) == hill_town["hills"], "kept as plain text with the city"
    hn, he = city_graph(hill_town, quiet)
    plain_town = {k: v for k, v in hill_town.items() if k != "hills"}
    pn, pe = city_graph(plain_town, quiet)
    swing = lambda nodes_, line: (lambda zs: max(zs) - min(zs))([dome(*nodes_[(ix, line)]) for ix in range(-4, 5) if (ix, line) in nodes_])
    for line in (3, -3, 4, -4, 5):
        assert swing(hn, line) < swing(pn, line) * 0.8, ("a street across the hill follows it", line, swing(hn, line), swing(pn, line))
    assert max(math.dist(hn[n], pn[n]) for n in hn if n in pn) <= HILL_MOVE * block * 1.45, "never more than a block and a half"
    assert all(math.dist(hn[n], pn[n]) > 500 for n in ((2, 3), (-3, 2))) and math.dist(hn[(0, 0)], pn[(0, 0)]) < 50.0
    for a_, b_, _k in he:  # ... and the net never tears: no block squeezed under half its size or pulled to double
        assert block * 0.5 <= math.dist(hn[a_], hn[b_]) <= block * 1.7, (a_, b_, math.dist(hn[a_], hn[b_]))
    for (ix, iy), p in hn.items():
        for da in ((1, 0), (0, 1)):
            q, r = hn.get((ix + da[0], iy + da[1])), hn.get((ix + 2 * da[0], iy + 2 * da[1]))
            if q and r:
                turn = abs((math.degrees(math.atan2(r[1] - q[1], r[0] - q[0]) - math.atan2(q[1] - p[1], q[0] - p[0])) + 180) % 360 - 180)
                assert turn < CORNER_DEG, ("the hills bend a street, they never break it", (ix, iy), turn)
    # beside a freeway nothing moves: what lands on one is laid out straight
    fw_town = dict(plain_town)
    plan_hills([fw_town], dome, {**quiet, "freeways": "yes"})
    assert hill_at(fw_town["hills"], 2, 3)[:2] == (0.0, 0.0) and hill_at(fw_town["hills"], 3, -3)[:2] == (0.0, 0.0)
    assert math.hypot(*hill_at(fw_town["hills"], -3, 4)[:2]) > 0.1
    flat_town = dict(plain_town)
    plan_hills([flat_town], lambda x, y: 100.0 + x * 0.005, quiet)
    assert "hills" not in flat_town, "half a percent of fall is flat ground"
    grid_town = dict(plain_town, style="grid")
    plan_hills([grid_town], dome, quiet)
    assert "hills" not in grid_town and city_graph(dict(hill_town, style="grid"), quiet)[0] == city_graph(grid_town, quiet)[0]
    assert city_graph(hill_town, {**quiet, "follow_hills": 0})[0] == pn, "follow_hills 0: the grid ignores the hills"
    # a street that climbs steep ground winds (its crossings swing to one side, then the other), and a local
    # street that would climb steeper than 12 % is left out: the blocks there lie along the hill
    slope_town = dict(plain_town)
    plan_hills([slope_town], lambda x, y: 0.10 * y, quiet)
    sn_, se_ = city_graph(slope_town, quiet)
    side = [sn_[(2, j)][0] - 2 * block for j in range(-3, 4)]
    assert all(a * b < 0 for a, b in zip(side, side[1:])) and 300 < abs(side[0]) < HILL_ZIG * block, side
    assert all(abs(sn_[(i, 2)][1] - 2 * block) < 1.0 for i in range(-3, 4)), "on an even slope a street across it is level already"
    steep_town = dict(plain_town)
    plan_hills([steep_town], lambda x, y: 0.16 * y, quiet)
    tn, te = city_graph(steep_town, quiet)
    up = [e for e in te if e[0][0] == e[1][0]]
    assert up and all(e[0][0] % ce == 0 for e in up), "only main roads climb a 16 % hillside"
    assert len([e for e in te if e[0][1] == e[1][1]]) == len([e for e in pe if e[0][1] == e[1][1]]), "every street along it stays"
    # a mixed city's main roads are dead straight downtown and wander past it; an organic city's bow all the way
    assert core_bent["mixed"] < 1e-6 and main_bent["mixed"] > 3 and main_bent["organic"] > 3, (core_bent, main_bent)
    # the whole street net drifts off the grid in long waves, main roads with it, as soon as streets may bow at
    # all: with a wobble of 4 % a main road already leaves its line by tens of metres (it bowed 3 m: every
    # fourth line was a ruled road, every four of them a square). Never in a grid city, never with wobble 0.
    drifting = {"x": 0, "y": 0, "radius": 120000, "seed": 11, "style": "organic", "turn": 0.0}
    dn, de = city_graph(drifting, {"wobble": 0.04, "freeways": "no", "prune": 0, "superblocks": "no"})
    off_line = [abs(dn[(ix, 8)][1] - 8 * block) for ix in range(-10, 11) if (ix, 8) in dn]  # (a main road of the suburbs)
    assert max(off_line) > 2000 and max(off_line) <= (MEANDER + 0.04 + EDGE_BOW) * block, ("a main road wanders", max(off_line))
    # ... and the nearer the city's edge the more its streets bow, the main roads too; not in the normal city
    calm = city_graph(drifting, {"wobble": 0.04, "freeways": "no", "prune": 0, "superblocks": "no"})[0]
    bend_at = lambda net, line, lo, hi: max(abs(net[(i + 1, line)][1] - 2 * net[(i, line)][1] + net[(i - 1, line)][1])
                                            for i in range(lo, hi) if all((i + d, line) in net for d in (-1, 0, 1)))
    assert bend_at(calm, 8, -4, 5) > 2 * bend_at(calm, 4, -2, 3), ("a main road of the suburbs bends more than one of the inner city",
                                                             bend_at(calm, 8, -4, 5), bend_at(calm, 4, -2, 3))
    assert all(math.dist(dn[(i, j)], (i * block, j * block)) < 0.05 * block for i in range(-2, 3) for j in range(-2, 3)
               if (i, j) in dn), "the centre stays straight (less the crossings the big park took)"
    assert all(block * 0.6 <= math.dist(dn[a], dn[b]) <= block * 1.4 for a, b, _k in de), "blocks keep their size"
    for (ix, iy), p in dn.items():
        for da in ((1, 0), (0, 1)):
            q, r = dn.get((ix + da[0], iy + da[1])), dn.get((ix + 2 * da[0], iy + 2 * da[1]))
            if q and r:
                turn = abs((math.degrees(math.atan2(r[1] - q[1], r[0] - q[0]) - math.atan2(q[1] - p[1], q[0] - p[0])) + 180) % 360 - 180)
                assert turn < 18, ("a road swings, it never kinks", (ix, iy), turn)
    assert meander(dict(drifting, style="grid"), options({"wobble": 0.3})) is None and meander(drifting, options({"wobble": 0})) is None
    mixed_n, _me = city_graph(dict(drifting, style="mixed"), {"wobble": 0.04, "freeways": "no", "prune": 0, "superblocks": "no"})
    assert all(math.dist(mixed_n[(i, j)], (i * block, j * block)) < 1.0 for i in range(-3, 4) for j in range(-3, 4)
               if (i, j) in mixed_n), "downtown stays a grid"
    assert max(math.dist(p, (n[0] * block, n[1] * block)) for n, p in mixed_n.items()) > 2000
    # past downtown many of the streets between two neighbouring blocks are not built. Against the same city
    # with every one of them built: every street is either built or listed as left out, a fair share is
    # left out, none of them downtown or on an axis, the network is in one piece and no new dead end was made
    gap_city = {"x": 0, "y": 0, "radius": 150000, "seed": 5, "style": "organic", "turn": 0.0}
    gap_o = {"wobble": 0, "freeways": "no", "prune": 0, "planner": "no"}
    left_out: list = []
    gn2, ge2 = city_graph(gap_city, gap_o, virtual=left_out)
    every_gap = dict(GAPS)
    GAPS.update({k: 0.0 for k in GAPS})
    all_n, all_e = city_graph(gap_city, gap_o)
    lay_all = city_layout(gap_city, gap_o)
    GAPS.update(every_gap)
    ruled: list = []  # a Grid city leaves streets out too, and keeps every street it builds on the grid's lines
    rn_, re_ = city_graph(dict(gap_city, style="grid"), {**gap_o, "wobble": 0.3}, virtual=ruled)
    assert len(ruled) > 40 and all(math.dist(p, (n[0] * block, n[1] * block)) < 1e-6 for n, p in rn_.items()), len(ruled)
    pair = lambda e: frozenset(e[:2])
    assert {pair(e) for e in ge2} | {pair(v) for v in left_out} == {pair(e) for e in all_e} and not {pair(e) for e in ge2} & {pair(v) for v in left_out}
    assert 0.08 * len(all_e) < len(left_out) < 0.4 * len(all_e), (len(left_out), len(all_e))
    go = city_options(gap_city, gap_o)
    for a_, b_, pa, pb in left_out:
        assert min(zone_share(gap_city, *pa), zone_share(gap_city, *pb)) > go["downtown"], "never downtown"
        assert not (a_[0] == 0 == b_[0] or a_[1] == 0 == b_[1]), "never one of the two axes"
    ends_of = lambda es: {n: sum(1 for e in es if n in e[:2]) for e in es for n in e[:2]}
    d_now, d_all = ends_of(ge2), ends_of(all_e)
    assert sum(1 for n, d in d_now.items() if d == 1) <= sum(1 for n, d in d_all.items() if d == 1), "no new dead end"
    reach_, walk_ = {(0, 0)}, [(0, 0)]
    while walk_:
        n_ = walk_.pop()
        for e in ge2:
            if n_ in e[:2]:
                m_ = e[1] if e[0] == n_ else e[0]
                if m_ not in reach_:
                    reach_.add(m_)
                    walk_.append(m_)
    assert reach_ == set(gn2), "one network"
    assert city_graph(gap_city, gap_o)[1] == ge2, "the same streets whether or not the caller asks which were left out"
    # ... and the land is still parted along them: as many blocks as the strict grid has, each laid out on its
    # own ground; no lot fronts a street that is not there, and no block is left without a street
    lay_gap = city_layout(gap_city, gap_o)
    assert len(lay_gap["blocks"]) == len(lay_all["blocks"]), (len(lay_gap["blocks"]), len(lay_all["blocks"]))
    built = [(gn2[e[0]][:2], gn2[e[1]][:2]) for e in ge2]
    for r in lay_gap["lots"]:
        if r[7] == "":
            assert min(_dist_seg((r[5], r[6]), p, q) for p, q in built) < 2500, ("a lot's front is on a street", r[:7])
    # no private street runs up to a line where no street was built and stops there: it meets the street of the
    # subdivision beside it, or ends in a turnaround short of it
    on_line = lambda p: any(_dist_seg(p, v[2], v[3]) < 5 for v in left_out)
    tips = [tuple(s[k][:2]) for s in lay_gap["streets"] for k in (0, -1)]
    assert not [p for p in tips if on_line(p)], [p for p in tips if on_line(p)][:3]
    joined_ = [s for s in lay_gap["streets"] if any(on_line(p) for p in s[1:-1])]
    assert joined_ or len(lay_gap["hoods"]) and lay_gap["hoods"].get("collector", 0) < 2, "two subdivisions side by side share a street"
    # a block of plain lots is planted where its lots leave it free (a court in its middle), never on a lot
    courts = 0
    for bx_, by_, kind_, _zone, poly_, _green in lay_gap["blocks"]:
        if kind_ != "parcels":
            continue
        mine = [r for r in lay_gap["lots"] if _inside(poly_, r[0], r[1])]
        for t in (t for t in lay_gap["trees"] if _inside(poly_, t[0], t[1])):
            courts += 1
            for r in mine:
                fx_, fy_ = math.cos(math.radians(r[2])), math.sin(math.radians(r[2]))
                assert abs((t[0] - r[0]) * fx_ + (t[1] - r[1]) * fy_) >= r[4] / 2 or abs(-(t[0] - r[0]) * fy_ + (t[1] - r[1]) * fx_) >= r[3] / 2, "a court tree on a lot"
    assert courts > 100, courts
    # every side of a street that no block lies on gets a row of lots: the land beyond a city's last streets is
    # built on. Each such lot is inside the city, off every street and off the lot beside it.
    polys = [b[4] for b in lay_gap["blocks"]]
    beyond = [r for r in lay_gap["lots"] if r[7] == "" and not any(_inside(p, r[0], r[1]) for p in polys)]
    assert len(beyond) > 60, len(beyond)
    for k_, r in enumerate(beyond):
        assert zone_share(gap_city, r[0], r[1]) <= 1.0, "inside the city's edge"
        assert min(_dist_seg((r[0], r[1]), p, q) for p, q in built) > 1500, ("off every street", r[:5])
        assert all(math.dist(r[:2], s[:2]) > 1400 for s in beyond[:k_]), ("not on the lot beside it", r[:5])
    assert not frontage_lots(gap_city, go, gn2, ge2, {(e[0], e[1]) for e in ge2} | {(e[1], e[0]) for e in ge2}), "none where blocks lie"
    assert not [r for r in frontage_lots(gap_city, go, gn2, ge2, set(), clear=[{"x0": -9e9, "x1": 9e9, "y0": -9e9, "y1": 9e9}])], "none in a keep zone"
    kept_clear = [{"name": "K", "x0": 20000.0, "x1": 50000.0, "y0": -15000.0, "y1": 15000.0, "z": 0.0}]
    lay_keep = city_layout(gap_city, gap_o, clear=kept_clear)
    assert not any(in_keep(kept_clear, r[0], r[1], 2000) for key in ("lots", "trees", "parking", "stalls", "pools") for r in lay_keep[key])
    assert not any(in_keep(kept_clear, p[0], p[1], 2000) for s in lay_keep["streets"] for p in s), "nothing planned in a keep zone"
    assert any(in_keep(kept_clear, r[0], r[1], 2000) for r in lay_gap["lots"]), "(lots were planned there)"
    beside = [b for b in lay_keep["blocks"] if any(in_keep(kept_clear, p[0], p[1], 2000) for p in b[4])]
    assert beside and all(b[2] in ("parcels", "park") for b in beside), "a block a keep zone reaches into is plain lots"
    # few bridges. Two banks of a lake, a street along each: of five long bridges side by side 150 m apart only
    # the shortest is built, a sixth two kilometres on is, the one right across them is not; every creek keeps
    # its own; three to one landing are one bridge
    deck = [[0, 0, 800, "BR"]]
    lake_n, lake_e, plans = {}, [], []
    spots = [k * 15000.0 for k in range(5)] + [200000.0]
    for k, x in enumerate(spots):
        lake_n[("a", k)], lake_n[("b", k)] = [x, 0.0], [x, 60000.0 + k * 1000.0]
        lake_e.append([("a", k), ("b", k), "6S", deck])
        plans.append({"x": x, "y": 30000.0 + k * 500.0, "yaw": 90.0})
        if k:
            lake_e += [[("a", k - 1), ("a", k), "2S"], [("b", k - 1), ("b", k), "2S"]]
    lake_n["x0"], lake_n["x1"] = [-30000.0, 20000.0], [150000.0, 21000.0]  # one right across the first five
    lake_e += [["x0", "x1", "6S", deck], ["x0", ("a", 0), "2S"], ["x1", ("b", 5), "2S"]]
    for s, y in (("a", -10000.0), ("b", 80000.0)):  # (each bank a town of its own: more streets behind the shore)
        lake_n.update({(s, "back", k): [k * 10000.0, y] for k in range(4)})
        lake_e += [[(s, 0), (s, "back", 0), "2S"]] + [[(s, "back", k), (s, "back", k + 1), "2S"] for k in range(3)]
    for k in range(3):  # three streets over a creek, 100 m apart
        lake_n[("c", k)], lake_n[("d", k)] = [300000.0 + k * 10000.0, 0.0], [300000.0 + k * 10000.0, 6000.0]
        lake_e.append([("c", k), ("d", k), "2S", deck])
    few_n, few_e = few_bridges(lake_n, lake_e, plans, 0)
    left = {(e[0], e[1]) for e in few_e if len(e) > 3}
    assert left == {(("a", 0), ("b", 0)), (("a", 5), ("b", 5)), (("c", 0), ("d", 0)), (("c", 1), ("d", 1)), (("c", 2), ("d", 2))}, left
    assert [p["x"] for p in plans] == [0.0, 200000.0] and len(few_e) == len(lake_e) - 5, (plans, len(few_e))
    fan_n = {"h": [0.0, 0.0], "l": [0.0, 90000.0], "m": [30000.0, 0.0], "r": [60000.0, 0.0]}
    fan_e = [[n, "l", "6S", deck] for n in ("h", "m", "r")] + [["h", "m", "2S"], ["m", "r", "2S"]]
    for s, y in (("h", -10000.0), ("l", 100000.0)):  # (a town behind each bank)
        fan_n.update({(s, k): [k * 10000.0, y] for k in range(8)})
        fan_e += [[s, (s, 0), "2S"]] + [[(s, k), (s, k + 1), "2S"] for k in range(7)]
    fan = few_bridges(fan_n, fan_e)[1]
    assert [e[0] for e in fan if len(e) > 3] == ["h"], "three bridges to one landing are one bridge"
    # a district on the far bank is joined: where the bridges to two districts would cross, the one that joins
    # more of the city is built (the shorter one, to the smaller district, would have cut the bigger one off)
    isle_n = {"c0": [0.0, 0.0], "c1": [10000.0, 0.0], "s": [40000.0, 50000.0], "t0": [-40000.0, 50000.0]}
    isle_n.update({("big", k): [-40000.0 - k * 5000.0, 50000.0] for k in range(1, 12)})
    isle_n.update({("town", k): [k * 5000.0, -5000.0] for k in range(10)})
    isle_n.update({("sm", k): [40000.0 + k * 5000.0, 50000.0] for k in range(1, 8)})
    isle_e = [["c0", "c1", "2S"], ["c1", "t0", "6S", deck], ["c0", "s", "6S", deck], ["t0", ("big", 1), "2S"], ["s", ("sm", 1), "2S"],
              ["c0", ("town", 0), "2S"]] + [[("big", k), ("big", k + 1), "2S"] for k in range(1, 11)] + \
             [[("town", k), ("town", k + 1), "2S"] for k in range(9)] + [[("sm", k), ("sm", k + 1), "2S"] for k in range(1, 7)]
    assert math.dist(isle_n["c0"], isle_n["s"]) < math.dist(isle_n["c1"], isle_n["t0"])
    isle = few_bridges(isle_n, isle_e)[1]
    assert [e[1] for e in isle if len(e) > 3] == ["t0"], "the bridge to the bigger district is the one built"
    lone_n = {**{("town", k): [k * 5000.0, 0.0] for k in range(10)}, "x": [0.0, 60000.0], "y": [5000.0, 60000.0]}
    lone_e = [[("town", k), ("town", k + 1), "2S"] for k in range(9)] + [[("town", 0), "x", "6S", deck], ["x", "y", "2S"]]
    assert not [e for e in few_bridges(lone_n, lone_e)[1] if len(e) > 3], "no long bridge to a corner of two junctions"
    # a district across the water that no bridge reaches gets one: straight to the nearest junction of the rest,
    # never across a street or a bridge that is there; a part of three junctions gets none
    strait = lambda x, y: 20000 < x < 50000
    bank_n = {("w", k): [0.0, k * 10000.0] for k in range(LOOSE_MIN)}
    bank_n.update({("e", k): [70000.0, k * 10000.0 + 2000.0] for k in range(LOOSE_MIN)})
    bank_e = [[(s, k), (s, k + 1), "2S"] for s in "we" for k in range(LOOSE_MIN - 1)]
    plans2: list = []
    jn, je = join_parts(bank_n, bank_e, strait, "6L", plans2)
    span = [e for e in je if len(e) > 3 and e[3]]
    assert len(span) == 1 and {span[0][0][0], span[0][1][0]} == {"w", "e"} and len(plans2) == 1, (span, plans2)
    assert abs(jn[span[0][0]][1] - jn[span[0][1]][1]) == 2000.0, "the two nearest junctions"
    assert len(join_parts(jn, je, strait, "6L")[1]) == len(je), "once joined, no second one"
    tiny = {**{("w", k): bank_n[("w", k)] for k in range(LOOSE_MIN)}, ("e", 0): [70000.0, 0.0], ("e", 1): [70000.0, 10000.0], ("e", 2): [80000.0, 0.0]}
    tiny_e = [e for e in bank_e if e[0][0] == "w"] + [[("e", 0), ("e", 1), "2S"], [("e", 0), ("e", 2), "2S"]]
    assert len(join_parts(tiny, tiny_e, strait, "6L")[1]) == len(tiny_e)
    # one network: a bridge with a bare end is not built, a few streets joined to nothing go, a district stays
    net_n = {k: [k * 1000.0, 0.0] for k in range(13)}
    net_n.update({"far": [19000.0, 0.0], "x": [0.0, 9000.0], "y": [1000.0, 9000.0]})
    net_n.update({("d", k): [k * 1000.0, 20000.0] for k in range(LOOSE_MIN)})
    net_e = [[k, k + 1, "2S"] for k in range(12)] + [[12, "far", "2S", [[16000, 0, 800, "BR"]]], ["x", "y", "2S"]] + \
            [[("d", k), ("d", k + 1), "2S"] for k in range(LOOSE_MIN - 1)]
    one_n, one_e = one_network(net_n, net_e)
    assert "far" not in one_n and "x" not in one_n and ("d", 0) in one_n and len(one_e) == 12 + LOOSE_MIN - 1, one_e
    joined = net_e + [["far", "x", "2S"], ["y", 0, "2S"]]  # the bridge now leads somewhere: everything stays
    assert len(one_network(net_n, joined)[1]) == len(joined)
    # ... but not beside a freeway: the streets on either side of it and the main roads that cross it stay put
    fw_city = {"x": 0, "y": 0, "radius": 120000, "seed": 11, "style": "organic", "turn": 0.0}
    fo = city_options(fw_city, {"wobble": 0.45})
    fat, _fk, _fj = street_grid(fw_city, fo)
    fline = round(fo["downtown"] * 120000 / block)
    for iy in range(-8, 9):
        for ix in (fline, fline + 1):
            assert abs(fat(ix, iy)[0] - ix * block) < 1e-6, "the two streets beside the freeway never move"
    for iy in (-8, 0, 4, 8):  # (main roads: every 4th line)
        for ix in (fline - 1, fline, fline + 1, fline + 2):
            assert abs(fat(ix, iy)[1] - iy * block) < 1e-6, "a main road crosses the freeway straight"
    far = [abs(fat(ix, 8)[1] - 8 * block) for ix in range(-9, fline - 4)]
    assert max(far) > 500, ("and bows five blocks from it", far)
    still, _ = city_graph({"x": 0, "y": 0, "radius": 60000, "seed": 11, "style": "organic", "turn": 0.0}, {"wobble": 0, "freeways": "no"})
    assert all(abs(p[0] - ix * block) < 1e-6 and abs(p[1] - iy * block) < 1e-6 for (ix, iy), p in still.items()), "wobble 0: an exact grid"
    # long blocks: block_ratio 2 makes every block twice as long as it is wide, the same streets otherwise
    longn, longe = city_graph({"x": 0, "y": 0, "radius": 60000, "seed": 11, "style": "grid", "turn": 0.0}, {"block_ratio": 2, "freeways": "no"})
    assert all(abs(p[0] - ix * block) < 1e-6 and abs(p[1] - iy * 2 * block) < 1e-6 for (ix, iy), p in longn.items())
    assert {round(math.dist(longn[a], longn[b])) for a, b, _ in longe} == {round(block), round(2 * block)}
    for ratio in (1.5, 2.5):  # freeways and their interchanges still land on the long grid's main roads
        fr = freeway_report({"name": "L", "x": 0, "y": 0, "radius": 160000, "seed": 4, "style": "mixed"}, {"block_ratio": ratio})
        assert fr["interchanges"] and fr["validation"]["ok"], (ratio, {k: v for k, v in fr["validation"].items() if v})
    # graded roads: on a 10 % hill no ground-level street segment is steeper than its class allows (the cut and
    # fill it takes stay under MAX_CUT_CM); on a 20 % hill the street follows the hill instead of a 6 m+ wall
    for slope, holds in ((0.1, True), (0.2, False)):
        hill = lambda x, y, k=slope: x * k
        ga = build_actor({0: [0, 0], 1: [10000, 0], 2: [20000, 0], 3: [30000, 0]},
                         [[0, 1, "2L"], [1, 2, "2L"], [2, 3, "2L"]], hill, "/Game/X")
        gb = {p["i"]: p for p in ga["points"]}
        # (+50 cm: on the 20 % hill SPIKE_GRADE wins over the cut cap at the free ends, 12.4 m)
        assert all(abs(p["z"] - hill(p["x"], p["y"])) <= MAX_CUT_CM + 50 for p in ga["points"] if p["raise"]), slope
        for s in ga["segments"]:
            a, b = gb[s["a"]], gb[s["b"]]
            run = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
            if holds and a["raise"] and b["raise"] and run > 0:
                # (+5 cm: sampled every 20 m, the drape's 4 passes leave a 20 m piece a few cm over, 8.1 % for 8 %)
                assert abs(a["z"] - b["z"]) <= GRADE["2L"] * run + 5, (abs(a["z"] - b["z"]) / run)
    # SidewalkGenerator: a 6-lane boulevard gets a sidewalk spline on both sides; lot paths become splines
    sn, se = sidewalks({0: [0, 0], 1: [10000, 0]}, [[0, 1, "6L"]])
    assert len(se) == 2 and all(abs(abs(p[1]) - (HALF["6L"] + HALF["SW"])) < 1 for p in sn.values())
    rows = [[0, 0, 0, 0, 1, 7, 0, 12, 12, 0, [[1.6, [600, 0, 0, 1600, 0, 0]], [3.5, [600, -400, 0, 1000, -600, 0, 1600, -400, 0]],
                                            [18.0, [600, 0, 0, 1600, 0, 0]]], []]]
    pa = paths_actor({"x": 0, "y": 0, "radius": 1, "seed": 1}, None, rows, lambda x, y: 0.0, "/Game/X/WorldGen/Roads")
    meshes = {s["mesh"] for s in pa["segments"]}
    assert meshes == {"SM_Path_Walkway_10m"}, meshes  # pedestrians only; the driveway is private access
    ac = access_actor({"private": {"streets": [[[0, 0], [5000, 0], [5000, 5000]]]}}, rows, lambda x, y: 0.0,
                      "/Game/X/WorldGen/Roads")
    am = {s["mesh"] for s in ac["segments"]}
    assert {"SM_Path_PrivateStreet_10m", "SM_Path_Driveway_10m"} <= am, am
    # a square ring road keeps its straight sides: every corner becomes a 9 m arc, nothing bulges out
    sq = fillet([[0, 0], [10000, 0], [10000, 10000], [0, 10000], [0, 0]])
    assert all(-1 <= x <= 10001 and -1 <= y <= 10001 for x, y in sq), "inside the square"
    corner = min(sq, key=lambda p: math.dist(p, (10000, 0)))
    assert 200 < math.dist(corner, (10000, 0)) < 900, corner  # cut by the arc, not by a wide curve
    assert len(sq) == 4 * 4 + 2, len(sq)  # 4 arcs of 4 points + the restart point twice
    # a street turning 90 degrees gets an elbow piece (square corner), a gentle bend stays a spline point
    el = build_actor({0: [0, 0], 1: [10000, 0], 2: [10000, 10000], 3: [20000, 10500]},
                     [[0, 1, "2L"], [1, 2, "2L"], [2, 3, "2L"]], lambda x, y: 0.0, "/Game/X")
    pieces_at = {(round(p["x"]), round(p["y"])) for p in el["points"] if p["mesh"] == JUNCTION}
    assert pieces_at == {(10000, 0), (10000, 10000)}, pieces_at
    assert st.pending_nodes(el["points"], el["segments"]), "elbows wait for a generated piece too"
    # rule: one city alone gets no highway and so no interchange
    solo = generate(cities[:1], lambda x, y: 0.0, "/Game/X/WorldGen/Roads", seed=3, opts={"interchange": "cloverleaf"})
    assert "Highways" not in solo, "no stand-alone highway or cloverleaf"
    # 1:1 ground: buildings on a 20 % hillside checked against the landscape UEFN imports (heightmap samples,
    # bilinear): with their pads cut in nothing is buried or floating; without pads they would be
    hill = {"min": [-200000, -200000], "max": [200000, 200000], "grid": 64,
            "heights": [[0.2 * (-200000 + 400000 * (i + 0.5) / 64) for i in range(64)] for _ in range(64)]}
    rnd = random.Random(5)
    homes = []
    for k in range(25):
        x, y, yaw, w, d = rnd.uniform(-90000, 90000), rnd.uniform(-90000, 90000), rnd.uniform(0, 360), 12.0, 10.0
        c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        zs = [0.2 * (x + c * a * d * 50 - s * b * w * 50) for a in (-1, 1) for b in (-1, 1)]
        pad = sum(zs) / 4
        homes.append((f"h{k}", x, y, yaw, w, d, pad, round((pad - min(zs)) / 100 + 0.05, 1)))
    mg = pad_margin(hill)
    hp = [(x, y, math.cos(math.radians(yaw)), math.sin(math.radians(yaw)), w * 50 + mg / 2, d * 50 + mg / 2, z)
          for _l, x, y, yaw, w, d, z, _f in homes]
    fit = ground_fit(imported_ground(hill, pads=hp), homes)
    assert fit["ok"] and fit["checked"] == 25, fit
    assert ground_fit(imported_ground(hill), homes)["buried"] > 0, "without pads the hill buries them"
    # a row of neighbours up the hill, 6 m apart (their pads overlap): each keeps its own level, none buried
    row = [(f"r{k}", 2000.0 * k, 0.0, 0.0, 10.0, 14.0, 0.2 * 2000 * k, 2.0) for k in range(6)]
    rp = [(x, y, 1.0, 0.0, w * 50 + mg / 2, d * 50 + mg / 2, z) for _l, x, y, _yaw, w, d, z, _f in row]
    rf = ground_fit(imported_ground(hill, pads=rp), row)
    assert rf["buried"] == 0, rf
    # neighbours too close for the landscape to step between (no pads at all here): settled, nothing misfits
    moves, before, after = settle(imported_ground(hill), homes)
    assert before["buried"] and moves and after["ok"], (before["buried"], after)
    # rule: a bridge on a slope sits over the highest ground near it, never inside the hill
    slope = lambda x, y: x * 0.2  # 20 % grade along x
    br = build_actor({0: [0, 0], 1: [20000, 0]}, [[0, 1, "4L", [[10000, 0, BRIDGE_LIFT_CM, "BR"]]]], slope, "/Game/X")
    deck = max(p["z"] for p in br["points"] if not p["raise"])
    assert deck >= slope(10000 + 1500, 0) + BRIDGE_LIFT_CM - 1, deck
    # options: clamped, lakes flatten the lowlands, smaller blocks mean more streets, 2-lane highways
    o = options({"block_m": 9999, "sea_m": -5, "highway": "8L", "detail": 3.4, "interchange": "stack"})
    assert o["block_m"] == 250 and o["highway"] == "4L" and o["detail"] == 3 and o["interchange"] == "stack"
    # road types per district: a six-lane core with one-way side streets, dirt suburbs
    kinds = {e[2] for e in city_graph(cities[0], {"core_roads": "6L", "one_way_core": "yes", "suburb_roads": "DT"})[1]}
    assert {"6S", "1S", "DT"} <= kinds and not kinds & set(CITY_STREET), ("city streets all have sidewalks", kinds)
    # a highway bridges over a street it crosses: ramp up, deck, ramp down; bridges never shape the ground
    via, n = overpass_via([[0, 0], [200000, 0]], [([100000, -5000], [100000, 5000])], "4L")
    # (an avenue rides the wide deck up, across and down: its bridge is never narrower than the road)
    assert n == 1 and [v[3] for v in via if len(v) > 3] == ["ED", "ED", "ED", "4L"] and max(v[2] for v in via) == BRIDGE_LIFT_CM
    assert [v[3] for v in overpass_via([[0, 0], [200000, 0]], [([100000, -5000], [100000, 5000])], "2L")[0] if len(v) > 3] == \
        ["BRR", "BR", "BRR", "2L"], "a street takes the two-lane bridge"
    assert all(HALF[structure_kinds(k)[0]] >= HALF[k] - 351 for k in ("2L", "2S", "4L", "6L", "6S")), "no bridge narrower than its carriageway"
    assert HALF[structure_kinds("6S")[0]] == HALF["6S"] and WALK[structure_kinds("6S")[0]] == WALK["6S"], "a boulevard keeps its sidewalks across"
    br = build_actor({"a": [0, 0], "b": [200000, 0]}, [["a", "b", "4L", via]], lambda x, y: 0.0, "/Game/X/Roads")
    meshes = [s["mesh"] for s in br["segments"]]
    assert meshes.count("SM_Elev_Deck20m") == 3, meshes
    deck = next(s for s in br["segments"] if s["mesh"] == "SM_Elev_Deck20m")
    assert "bRaiseTerrain=False" in deck["lines"] and not deck["raise"]
    top = max(p["z"] for p in br["points"])
    assert abs(top - BRIDGE_LIFT_CM) < 1, top
    text = st.serialize(br["points"], br["segments"])
    assert "Elevated/SM_Elev_Deck20m" in text and "SM_Road_Bridge" not in text
    # interchanges: a stack rises onto elevated decks, a trumpet puts the loop piece on each highway end
    far = [{"name": "P", "x": -150000, "y": 0, "radius": 30000, "style": "grid", "seed": 3},
           {"name": "Q", "x": 150000, "y": 0, "radius": 30000, "style": "grid", "seed": 4}]
    stack = generate(far, lambda x, y: 0.0, "/Game/X/Roads", seed=3, opts={"interchange": "stack", "gate_interchanges": "yes"})["Highways"]
    assert {"SM_Elev_Deck20m", "SM_Elev_Ramp20m"} <= {s["mesh"] for s in stack["segments"]}
    assert "/Game/X/Roads/Elevated/SM_Elev_Deck20m" in st.serialize(stack["points"], stack["segments"])
    # real interchanges, built from our pieces: a bridge over a cross road and loop / slip ramps at each end
    made = {}
    for kind in ("cloverleaf", "trumpet", "diamond"):
        world = generate(far, lambda x, y: 0.0, "/Game/X/Roads", seed=3, opts={"interchange": kind, "highway": "6L", "gate_interchanges": "yes"})
        hw = world["Highways"]
        meshes = [s["mesh"] for s in hw["segments"]]
        made[kind] = hw
        assert meshes.count("SM_Elev_Deck20m") >= 2 and "SM_Road_Bridge_Straight20m" not in meshes, \
            (kind, "a bridge at each end, as wide as the six-lane highway it carries")
        loops = meshes.count("SM_Elev_Ramp20m")
        assert loops == {"cloverleaf": 2 * 4 * 12, "trumpet": 2 * 12, "diamond": 0}[kind], (kind, loops)
        top = max(p["z"] for p in hw["points"])
        assert abs(top - BRIDGE_LIFT_CM) < 1, (kind, top)
        # the cross road is a city street now, joined to the city: no Highways stub going nowhere
        assert "SM_Road_FourLane_Straight10m" not in meshes, kind
        for c in far:
            city = world[f"{c['name']} Roads"]
            assert any(s["mesh"] == "SM_Road_FourLane_Straight10m" for s in city["segments"]), (kind, c["name"])
    # several highways in one world (each one's interchange adds city streets before the next is routed)
    three = far + [{"name": "R", "x": 0, "y": 250000, "radius": 30000, "style": "mixed", "seed": 5}]
    multi = generate(three, lambda x, y: 0.0, "/Game/X/Roads", seed=3, opts={"interchange": "cloverleaf", "highway": "6L", "gate_interchanges": "yes"})
    assert weld_ends(multi) == 0, "every ramp and highway end already sits on the road it reaches"
    ends = [p["mesh"] for a in multi.values() for p in a["points"] if p["mesh"].startswith("SM_Road_Ramp")]
    assert {"SM_Road_RampFlare15m", "SM_Road_RampGore30m"} <= set(ends), ("ramp ends get their connectors", set(ends))
    # a viaduct over bumpy ground runs level: never under its planned clearance, far smoother than the ground
    bumps = [_point(k, k * 2000.0, 0, 750 + 400 * math.sin(k * 1.3), 0, 1650, lifted=k not in (0, 60)) for k in range(61)]
    planned = [p["z"] for p in bumps]
    level_decks(bumps)
    wob = lambda zs: max(abs(zs[k - 1] - 2 * zs[k] + zs[k + 1]) for k in range(1, len(zs) - 1))
    assert all(p["z"] >= z - 1 for p, z in zip(bumps, planned)) and wob([p["z"] for p in bumps[8:53]]) < wob(planned) / 10
    # an end never joins its own road (the old weld slid ends onto their own previous point)
    rb = {k: {"i": k, "x": k * 1000.0, "y": 0.0} for k in range(4)}
    rs = [{"a": k, "b": k + 1} for k in range(3)]
    rt: dict = {}
    for s in rs:
        for k in (s["a"], s["b"]):
            rt.setdefault(k, []).append(s)
    assert own_run(rt, rb, 3) == {0, 1, 2, 3} and own_run(rt, rb, 3, 1500) == {1, 2, 3}
    # every ramp end lies inside the road it joins (its edge on that road's edge), never beside it
    half_of = {m.split("/")[-1]: HALF[k] for k, m in ROAD_MESH.items()}
    segs_all = [(lab, {p["i"]: p for p in a["points"]}, s) for lab, a in multi.items() for s in a["segments"] if s["mesh"]]
    for lab, a in multi.items():
        if not lab.endswith(("Freeways", "Highways")):
            continue
        by = {p["i"]: p for p in a["points"]}
        deg: dict = {}
        for s in a["segments"]:
            for i in (s["a"], s["b"]):
                deg.setdefault(i, []).append(s)
        for i, ss in deg.items():
            if len(ss) == 1 and ss[0]["mesh"] and not by[i]["mesh"]:
                p = by[i]
                gaps = [math.hypot(p["x"] - q["x"] - (r["x"] - q["x"]) * t, p["y"] - q["y"] - (r["y"] - q["y"]) * t)
                        - max(0.0, half_of.get(s2["mesh"].split("/")[-1], 800.0) - half_of.get(ss[0]["mesh"].split("/")[-1], 650.0))
                        for lab2, b2, s2 in segs_all if not (lab2 == lab and i in (s2["a"], s2["b"]))
                        for q, r in [(b2[s2["a"]], b2[s2["b"]])]
                        for t in [max(0.0, min(1.0, ((p["x"] - q["x"]) * (r["x"] - q["x"]) + (p["y"] - q["y"]) * (r["y"] - q["y"]))
                                                / (((r["x"] - q["x"]) ** 2 + (r["y"] - q["y"]) ** 2) or 1.0)))]]
                assert not gaps or min(gaps) <= 60, (lab, round(min(gaps)), "a ramp end beside its road")
    # real bridges: piers under every deck that stands clear of the ground, none where it sits on it
    cols, caps = bridge_piers({"x": {"points": [_point(0, 0, 0, 900, 0, 1650, lifted=True), _point(1, 12000, 0, 900, 0, 1650, lifted=True)],
                                     "segments": [_segment(2, 0, 1, "ED", "/G", lifted=True)]}}, lambda x, y: 0.0)
    assert len(caps) == 4 and len(cols) == 8 and all(abs(c[2] + c[6] * 100 - (900 - DECK_DEPTH_CM - CAP_CM)) < 2 for c in cols), (caps, cols)
    under = bridge_piers({"x": {"points": [_point(0, 0, 0, 900, 0, 1650, lifted=True), _point(1, 12000, 0, 900, 0, 1650, lifted=True),
                                          _point(3, 1500, -5000, 0, 0, 525), _point(4, 1500, 5000, 0, 0, 525)],
                                "segments": [_segment(2, 0, 1, "ED", "/G", lifted=True), _segment(5, 3, 4, "2L", "/G")]}},
                         lambda x, y: 0.0)[1]
    assert len(under) == 3 and all(abs(c[0] - 1500) > 525 for c in under), ("no pier in the street under the deck", under)
    # two cities that overlap: the bigger keeps the land (none of the smaller one's streets inside it), the bigger
    # one's own streets are exactly what they were, and a city put somewhere by hand is never pushed away
    big_c = {"name": "Big", "x": 0, "y": 0, "radius": 90000, "seed": 3, "style": "grid"}
    small_c = {"name": "Small", "x": 120000, "y": 0, "radius": 60000, "seed": 5, "style": "grid"}
    alone, _e0 = city_graph(dict(small_c))
    pair = [dict(big_c), dict(small_c)]
    yield_plan(pair)
    assert "yield_to" not in pair[0] and [o["name"] for o in pair[1]["yield_to"]] == ["Big"]
    clipped, _e1 = city_graph(pair[1])
    inside_big = lambda p: zone_share(big_c, p[0], p[1]) <= 1.0
    assert any(inside_big(p) for p in alone.values()) and not any(inside_big(p) for p in clipped.values()), "no street twice"
    assert city_graph(pair[0])[0] == city_graph(dict(big_c))[0], "the bigger city is as it was"
    assert len(clipped) > 0.5 * len(alone)
    kept_put = spread([dict(big_c), {**small_c, "pinned": True}], 30000)
    assert [(t["x"], t["y"]) for t in kept_put] == [(0, 0), (120000, 0)], "a city put by hand stays, and pushes nobody"
    assert spread([dict(big_c), dict(small_c)], 30000)[1]["x"] > 120000
    # cities that lie close are joined street to street, the closer the more; far apart, not at all
    def joined_at(gap_cm):
        cs = [{"name": "A", "x": 0, "y": 0, "radius": 60000, "seed": 3, "style": "grid", "shape": [1.0] * SHAPE_POINTS},
              {"name": "B", "x": 120000 + gap_cm, "y": 0, "radius": 60000, "seed": 5, "style": "grid", "shape": [1.0] * SHAPE_POINTS}]
        gs = [city_graph(c, {"freeways": "no"}) for c in cs]
        return cs, gs, joins(cs, gs, {"freeways": "no"})
    cs, gs, close = joined_at(8000)
    _cs, _gs, mid_far = joined_at(40000)
    assert len(close) >= 3 and len(close) > len(mid_far) >= 1 and not joined_at(90000)[2], (len(close), len(mid_far))
    for i, j, ni, nj, mid in close:
        p, q = gs[i][0][ni], gs[j][0][nj]
        assert p[0] < mid[0] < q[0] and math.dist(p, q) < 8000 + JOIN_LONGEST_CM and abs(mid[1]) < 60000, (p, mid, q)
    assert len({(ni, nj) for _i, _j, ni, nj, _m in close}) == len(close), "each street joins its own two corners"
    assert not joins(cs, gs, {"freeways": "no"}, wet=lambda x, y: 60000 < x < 68000), "never through water"
    assert len(joins(cs, gs, {"freeways": "no"}, wet=lambda x, y: y > 20000)) < len(close), "nor along a shore"
    # a shipyard's platform cut into the terrain: level on it, 9 m of water along its quay (the ground ends 15 m
    # behind the quay line, under the paving), the hill behind and beside it cut back to a bank, the rest as it was
    yard = {"type": "repair", "frame": {"x": 0, "y": 0, "ux": 0.0, "uy": 1.0, "vx": -1.0, "vy": 0.0}, "L": 40000.0,
            "D": 20000, "z": 250.0, "docks": []}
    hillside = {"min": [-40000, -40000], "max": [40000, 40000], "grid": 80, "sea": 0.0,
                "heights": [[3000.0 if i < 40 else -200.0 for i in range(80)] for _ in range(80)]}
    town = [{"name": "Y", "shipyards": [yard]}]
    cut = cut_yards(hillside, town)["heights"]
    assert (cut[40][29], cut[40][38], cut[40][39], cut[40][40], cut[40][19], cut[40][0], cut[60][29]) == \
        (250.0, 250.0, -900.0, -900.0, 550.0, 3000.0, 550.0), (cut[40][29], cut[40][38], cut[40][39], cut[40][40], cut[40][19])
    assert cut[40][60] == -200.0 and cut[40][45] == -900.0, "shallow water past the basin stays"
    assert cut_yards(hillside, [{"name": "N", "shipyards": [{**yard, "z": None}]}]) is hillside and cut_yards(hillside, []) is hillside
    level = on_yards(town, lambda x, y: 3000.0)
    assert (level(-100, 19900), level(-19900, -19900), level(100, 0), level(-20100, 0), level(-100, 20100)) == \
        (250.0, 250.0, 3000.0, 3000.0, 3000.0) and on_yards([], level) is level
    # a made shore cut into the terrain. The water lies at x > 0 and the land stands 6 m over it: a quay gets its
    # level promenade with 3 m of water at the wall, a beach runs from half a metre under the water up to its
    # boardwalk, homes get a lawn up to their row, and behind each the land comes back as a bank
    from .. import waterfront as _wf
    bluff = {"min": [-20000, -20000], "max": [20000, 20000], "grid": 80, "sea": 0.0,
             "heights": [[600.0 if i < 40 else -800.0 for i in range(80)] for _ in range(80)]}
    line = [[0, y, 1.0, 0.0] for y in range(-18000, 18001, 2000)]
    cell = lambda use, i: cut_shores(bluff, [{"name": "S", "shores": [{"use": use, "sea": 0.0, "bank": 1000.0, "pts": line}]}])["heights"][40][i]
    assert (cell("commercial", 39), cell("commercial", 31), cell("commercial", 40), cell("commercial", 44)) == (200.0, 200.0, -800.0, -800.0)
    assert cell("commercial", 29) == 200 + 0.5 * 250 and cell("commercial", 20) == 600.0, "the bank, then the land as it was"
    assert cut_shores({**bluff, "heights": [[600.0 if i < 40 else -100.0 for i in range(80)] for _ in range(80)]},
                      [{"name": "S", "shores": [{"use": "public", "sea": 0.0, "bank": 0.0, "pts": line}]}])["heights"][40][40] == -300.0
    assert (cell("beach", 41), cell("beach", 39), cell("beach", 33), cell("beach", 31)) == (-18.8, 6.2, 81.2, 200.0), cell("beach", 41)
    assert (cell("residential", 39), cell("residential", 30), cell("residential", 40)) == (147.5, 450.0, -800.0)
    assert cut_shores(bluff, []) is bluff and cut_shores(bluff, [{"name": "S"}]) is bluff
    # a stretch fades into the natural shore at its ends: nothing is made past them
    ends = cut_shores(bluff, [{"name": "S", "shores": [{"use": "commercial", "sea": 0.0, "bank": 0.0, "pts": line[6:13]}]}])["heights"]
    assert ends[40][35] == 200.0 and ends[5][35] == 600.0 and 200.0 < ends[32][35] < 600.0, (ends[40][35], ends[32][35], ends[5][35])
    # what is built there stands at the made level, and a street stays off where the land was cut down for it
    made_z = on_made([{"name": "S", "shores": [{"use": "commercial", "sea": 0.0, "bank": 1000.0, "pts": [p + [3000.0] for p in line]}]}],
                     lambda x, y: 600.0 if x < 0 else -800.0)
    assert made_z(-2500, 0) == 200.0 and made_z(-15000, 0) == 600.0 and on_made([], made_z) is made_z
    look = _wf.Made([{"shores": [{"use": "commercial", "sea": 0.0, "bank": 1000.0, "pts": [p + [3000.0] for p in line]}]}])
    assert look.no_street(-2500, 0) and not look.no_street(-3500, 0) and not look.no_street(-2500, 19500)
    assert look.shelf(-4900, 0)["use"] == "commercial" and look.shelf(-5200, 0) is None and look.shelf(100, 0) is None
    # ... and into the coarse terrain the roads are planned on: only cells wholly on the platform or in its basin
    coarse = {**hillside, "grid": 8, "heights": [[3000.0 if i < 4 else -200.0 for i in range(8)] for _ in range(8)]}
    rough = cut_yards(coarse, town, whole=True)["heights"]
    assert [rough[4][i] for i in range(8)] == [3000.0, 3000.0, 250.0, 250.0, -200.0, -200.0, -200.0, -200.0], rough[4]
    finer = cut_yards({**hillside, "grid": 16, "heights": [[3000.0 if i < 8 else -200.0 for i in range(16)] for _ in range(16)]},
                      town, whole=True)["heights"]
    assert (finer[8][7], finer[8][8], finer[8][9]) == (250.0, -900.0, -200.0), "the basin: 90 m out from the quay"
    assert rough[1][2] == 3000.0 and rough[2][2] == 250.0 and rough[6][3] == 3000.0, "a cell half off the platform is left"
    # two cities on different levels: joined while a street can climb it, not when it would be a ski jump
    assert len(joins(cs, gs, {"freeways": "no"}, ground=lambda x, y: 0.0 if x < 64000 else 1200.0)) == len(close)
    assert not joins(cs, gs, {"freeways": "no"}, ground=lambda x, y: 0.0 if x < 64000 else 6000.0), "too steep to join"
    # ... and its two halves, each graded with its own city, meet at one height: an even grade corner to corner
    halves = {"A Roads": {"points": [_point(0, 0, 0, 1700, 0, 875), _point(1, 20000, 0, 1650, 0, 875)],
                          "segments": [_segment(2, 0, 1, "2S", "/G")]},
              "B Roads": {"points": [_point(0, 50000, 0, -1300, 0, 875), _point(1, 20000, 0, -500, 0, 875)],
                          "segments": [_segment(2, 0, 1, "2S", "/G")]}}
    assert level_joins(halves, cs, [(0, 1, None, None, [20000, 0])]) == 1
    za, zb = halves["A Roads"]["points"][1]["z"], halves["B Roads"]["points"][1]["z"]
    assert za == zb and abs(za - (1700 - 3000 * 0.4)) < 1e-6, (za, zb)
    assert level_joins(halves, cs, [(0, 1, None, None, [99999, 0])]) == 0, "a street the water took is left alone"
    # a neighbour's ramp running low over this city's land: the street it would cut gives way
    cut = _near_cuts({"fw_near": [[5000, 0, 650]]})
    assert cut((5000, -3000), (5000, 3000), 525.0) and cut((3800, -3000), (3800, 3000), 525.0)
    assert not cut((9000, -3000), (9000, 3000), 525.0) and not _near_cuts({})((0, 0), (1, 1), 500.0)
    # the way overland: round the water, never over it; and a road to every port, from a street of the city
    # through the port's gate onto its platform
    lake_ = lambda x, y: 20000 < x < 60000 and -30000 < y < 30000
    way, tag_ = overland((0, 0), [(80000, 0, "far"), (0, 200000, "further")], lambda x, y: not lake_(x, y), lambda x, y: 0.0)
    assert tag_ == "far" and way[0] == (0, 0) and way[-1] == (80000, 0) and not any(lake_(*p) for p in way), way
    assert max(abs(p[1]) for p in way) > 30000 and overland((0, 0), [(80000, 0, "far")], lambda x, y: x < 20000, lambda x, y: 0.0) is None
    hilly, _t = overland((0, 0), [(60000, 0, "g")], lambda x, y: True, lambda x, y: 3000.0 if 20000 < x < 40000 and abs(y) < 20000 else 0.0)
    assert max(abs(p[1]) for p in hilly) > 15000, "it goes round a steep hill"
    yard_ = {"type": "repair", "frame": {"x": 0, "y": -150000, "ux": 1.0, "uy": 0.0, "vx": 0.0, "vy": 1.0}, "L": 40000, "D": 26000,
             "z": 250.0, "bank": 2000.0, "docks": []}
    port_c = {"name": "Port", "x": 0, "y": 0, "radius": 90000, "seed": 3, "style": "grid", "shipyards": [yard_]}
    g_port = [list(city_graph(port_c))]
    before_n = len(g_port[0][1])
    roads_ = port_roads([port_c], g_port, {}, [], lambda x, y: 0.0)
    assert len(roads_) == 1 and port_c["port_roads"] == roads_ and len(g_port[0][1]) == before_n + 1, roads_
    r0 = roads_[0]
    from .. import shipyard as _sy
    assert _sy.zone(yard_, *_sy.uv(yard_, *r0[0])) == "platform" and tuple(r0[-1]) in {(round(p[0]), round(p[1])) for p in g_port[0][0].values()}, "from on its platform to a street crossing"
    assert abs(_sy.uv(yard_, *r0[1])[1] - 26000) < 1 and abs(_sy.uv(yard_, *r0[1])[0] - (20000 - 9950)) < 1, "out through its gate"
    assert all(not _sy.reserved(yard_, p[0], p[1], 1000) for p in r0[3:]), "then clear of the yard"
    pond_ = lambda x, y: 0 < x < 20000 and -124000 + 2000 < y < -110000  # water right behind its gate corner
    spit = {**port_c, "name": "Spit", "shipyards": [dict(yard_)]}
    moved_ = port_roads([spit], [list(city_graph(spit))], {}, [], lambda x, y: 0.0, pond_)
    assert len(moved_) == 1 and spit["shipyards"][0].get("gate_u") is not None and spit["shipyards"][0]["gate_u"] < 9000, "its gate moves along the back edge to dry land"
    assert not any(pond_(*p) for p in moved_[0]) and "gate_u" not in yard_
    strait = lambda x, y: -124000 + 2000 < y < -112000 and -40000 < x < 40000  # water along the whole of its back
    side_c = {**port_c, "name": "Side", "shipyards": [dict(yard_)]}
    by_side = port_roads([side_c], [list(city_graph(side_c))], {}, [], lambda x, y: 0.0, strait)
    sy_ = side_c["shipyards"][0]
    assert len(by_side) == 1 and sy_.get("gate_side") in (1, -1) and "gate_u" not in sy_, "with water all along its back it is entered from a side"
    assert abs(abs(_sy.uv(sy_, *by_side[0][1])[0]) - 20000) < 1 and not any(strait(*p) for p in by_side[0])
    # ... and a yard with water all round it gets its road straight across the water, to be bridged
    moat = lambda x, y: abs(y + 137000) < 30000 and abs(x) < 60000 and not (abs(x) <= 20000 and -150000 <= y <= -124000)
    isle = {**port_c, "name": "Isle", "shipyards": [dict(yard_)]}
    over_ = port_roads([isle], [list(city_graph(isle))], {}, [], lambda x, y: 0.0, moat)
    assert len(over_) == 1 and any(moat(*p) for p in over_[0]) and "gate_side" not in isle["shipyards"][0], "straight over the water from its own gate"
    # a yard with a street right beside it joins it straight (no room there for the route's 60 m cells)
    tight = {**yard_, "frame": {**yard_["frame"], "y": -40000 - 26000 - 2000 - 3500 - 9000}}
    near_c = {**port_c, "name": "Near", "shipyards": [tight]}
    g_near = list(city_graph(near_c))
    short = port_roads([near_c], [g_near], {}, [], lambda x, y: 0.0)
    assert len(short) == 1 and len(short[0]) == 4 and math.dist(short[0][2], short[0][3]) < 30000, short
    # ... but never straight up a 40 m cliff between them: then it goes the long way, overland
    cliff_c = {**near_c, "name": "Cliff", "shipyards": [dict(tight)]}
    steep = port_roads([cliff_c], [list(city_graph(cliff_c))], {}, [], lambda x, y: 0.0 if y < -48000 else 4000.0)
    assert len(steep) == 1 and (len(steep[0]) != 4 or steep[0][-1][1] < -48000), ("no straight road up the cliff", steep)
    hw_n, hw_e = {"a": [-300000, -230000], "b": [300000, -230000]}, [["a", "b", "4L", [[x, -230000] for x in range(-280000, 280001, 20000)]]]
    far_c = {**port_c, "name": "Far", "y": 400000}
    port_roads([far_c], [list(city_graph(far_c))], hw_n, hw_e, lambda x, y: 0.0)
    assert len(hw_e) == 3 and len(hw_n) == 4 and far_c["port_roads"], "a port far from every street joins the highway, at a junction of its own"
    j_ = hw_n[("port", 0, 0, "hw")]
    assert hw_e[0][1] == ("port", 0, 0, "hw") == hw_e[1][0] and len(hw_e[0][3]) + len(hw_e[1][3]) == 28 and j_[1] == -230000
    # one big park in the centre district: no street through it, the streets round it all there, the block they
    # leave a park (trees in groves, paths, benches, playgrounds, a pond, fields), nothing built on it
    park_c = {"name": "Parkville", "x": 0, "y": 0, "radius": 200000, "seed": 11, "style": "mixed"}
    po = city_options(park_c, None)
    pk = central_park(park_c, po)
    assert pk and sorted((pk[2] - pk[0], pk[3] - pk[1])) == [2, 4], pk
    assert central_park(park_c, po) == pk and central_park({**park_c, "radius": 60000}, po) is None, "the same every time; a small city has none"
    assert not (pk[0] < 0 < pk[2] or pk[1] < 0 < pk[3]), "never across a main axis"
    pn, pe = city_graph(park_c)
    inside_pk = lambda n: isinstance(n, tuple) and len(n) == 2 and pk[0] < n[0] < pk[2] and pk[1] < n[1] < pk[3]
    assert not any(inside_pk(n) for n in pn) and not any(inside_pk(e[0]) or inside_pk(e[1]) for e in pe), "no street in it"
    rim_n = [(i, j) for i in range(pk[0], pk[2] + 1) for j in range(pk[1], pk[3] + 1) if i in (pk[0], pk[2]) or j in (pk[1], pk[3])]
    have_e = {(e[0], e[1]) for e in pe} | {(e[1], e[0]) for e in pe}
    assert all(n in pn for n in rim_n) and all(((i, j), (i + 1, j)) in have_e for i in range(pk[0], pk[2]) for j in (pk[1], pk[3])) \
        and all(((i, j), (i, j + 1)) in have_e for j in range(pk[1], pk[3]) for i in (pk[0], pk[2])), "a street all the way round it"
    lay_p = city_layout(park_c, {"planner": "no"})
    mid_p = [sum(pn[(i, j)][k] for i in (pk[0], pk[2]) for j in (pk[1], pk[3])) / 4 for k in (0, 1)]
    blk = [b for b in lay_p["blocks"] if _inside(b[4], *mid_p)]
    assert len(blk) == 1 and blk[0][2] == "park" and abs(parcels_area(blk[0][4])) > 5 * 1e8, blk and blk[0][:4]
    kinds_p = [g[5] for g in lay_p["grounds"] if _inside(blk[0][4], g[0], g[1])]
    assert kinds_p.count("path") > 100 and "playground" in kinds_p and "pond" in kinds_p and "bench" in kinds_p, sorted(set(kinds_p))
    in_blk = [t for t in lay_p["trees"] if _inside(blk[0][4], t[0], t[1])]
    full = len(_grove(blk[0][4], random.Random(1)))
    assert 0.3 * full < len(in_blk) < 0.8 * full, ("groves and lawns, not a plantation", len(in_blk), full)
    assert not any(_inside(blk[0][4], r[0], r[1]) for r in lay_p["lots"]), "nothing built in the park"
    solid_g = [g for g in lay_p["grounds"] if g[5] in ("pond", "field", "playground") and _inside(blk[0][4], g[0], g[1])]
    for t in in_blk:
        for g in solid_g:
            f_ = math.radians(g[2])
            du_, dv_ = (t[0] - g[0]) * math.cos(f_) + (t[1] - g[1]) * math.sin(f_), -(t[0] - g[0]) * math.sin(f_) + (t[1] - g[1]) * math.cos(f_)
            assert not (abs(du_) < g[3] * 50 and abs(dv_) < g[4] * 50), ("a tree in a pond, a field or a playground", t, g)
    # a lake drawn by hand: dug to the world's water line (a world with none gets one under its lowest ground,
    # so nothing else floods), a bed in its middle, banks no steeper than 1 in 3, nothing changed far from it
    drawn = lake_list([{"pts": [[-60000, 0], [60000, 0]], "width_m": 300}, {"pts": [[0, 90000]]}, {"pts": "x"}, {"id": "lake-1", "pts": [[1, 1], [1, 1]], "width_m": 5}])
    assert [(l["id"], len(l["pts"]), l["width_m"]) for l in drawn] == [("lake-2", 2, 300.0), ("lake-3", 1, 200.0), ("lake-1", 1, 60.0)], drawn
    dry_t = terrain(5, 400000, 3000, [], grid=80)
    lake_t = terrain(5, 400000, 3000, [], grid=80, lakes=drawn[:1])
    assert dry_t["sea"] is None and lake_t["sea"] < min(min(r) for r in dry_t["heights"]), "its water line lies under all the land"
    wet_l = wet_from(lake_t)
    assert all(wet_l(x, 0) for x in range(-55000, 55001, 5000)) and wet_l(0, 10000) and not wet_l(0, 40000), "water along the line, as wide as drawn"
    flooded = sum(1 for j in range(80) for i in range(80) if lake_t["heights"][j][i] < lake_t["sea"])
    assert 0.6 * 120000 * 30000 < flooded * 5000 * 5000 < 1.6 * 150000 * 30000, flooded
    same = sum(1 for j in range(80) for i in range(80) if lake_t["heights"][j][i] == dry_t["heights"][j][i])
    assert same > 0.5 * 6400 and lake_t["heights"][2][2] == dry_t["heights"][2][2], "the land away from it is as it was"
    steep = max(abs(lake_t["heights"][j][40] - lake_t["heights"][j + 1][40]) / 5000 for j in range(46, 70))
    assert steep <= LAKE_BANK_GRADE * 1.6, ("its banks are slopes, not a pit", steep)
    sea_t = terrain(5, 400000, 3000, [], grid=80, opts={"sea_m": -10}, lakes=drawn[:1])
    assert sea_t["sea"] == -1000.0 and wet_from(sea_t)(0, 0), "a world with water: dug to that water's level"
    # a city's centre district moved off its centre: the rings lie round the new spot, the edge stays where it
    # is; its own district sizes and industrial side are the city's, whatever the world's are
    plain_c = {"name": "Plain", "x": 0, "y": 0, "radius": 100000, "seed": 4, "style": "grid", "turn": 0.0}
    moved_c = {**plain_c, "core": [40000, 0]}
    assert core_reach(plain_c) is None and zone_share(moved_c, 40000, 0) == 0.0
    for deg_ in range(0, 360, 7):
        ex, ey = (100000 * outline(plain_c, deg_) * f_ for f_ in (math.cos(math.radians(deg_)), math.sin(math.radians(deg_))))
        assert abs(zone_share(moved_c, ex, ey) - 1.0) < 0.004, (deg_, zone_share(moved_c, ex, ey))
        assert zone_share(moved_c, ex * 0.98, ey * 0.98) < 1.0 < zone_share(moved_c, ex * 1.02, ey * 1.02)
    assert zone_share(moved_c, 0, 0) > 0.25 and zone_share(plain_c, 40000, 0) > 0.3, "the old centre is no longer the middle"
    ring_m, _ind = zoning(moved_c, {"downtown": 0.3})
    assert ring_m(40000, 0) == "core" and ring_m(-20000, 0) != "core" and zoning(plain_c, {"downtown": 0.3})[0](0, 0) == "core"
    assert clamp_core(plain_c, 500, 500) == [0, 0] and math.hypot(*clamp_core(plain_c, 300000, 0)) <= 0.6 * 100000 * 1.27
    own = {**plain_c, "features": {"downtown": 0.2, "normal": 0.5}, "industry_deg": 90.0, "spin": 10.0}
    d_own = districts(own, {"downtown": 0.4})
    assert d_own["downtown"]["radius"] == 20000 and d_own["normal"]["radius"] == 50000 and d_own["industrial"]["angle"] == 100.0
    assert "core" not in d_own and districts(moved_c)["core"] == [40000, 0]
    g_plain, g_moved = city_graph(plain_c, {"downtown": 0.3}), city_graph(moved_c, {"downtown": 0.3})
    wide = lambda g, x0, x1: sum(1 for a, b, k, *_ in g[1] if k == options()["core_roads"] and x0 < g[0][a][0] < x1 and abs(g[0][a][1]) < 20000)
    assert wide(g_moved, 25000, 55000) > wide(g_plain, 25000, 55000), "downtown's streets are where the centre district is now"
    # a freeway never runs out over a lake to end in the water: it stops at the shore, back at grade; a river with
    # land beyond it is crossed as before
    from .. import freeways as _fw
    lake_city = {"name": "Lake", "x": 0, "y": 0, "radius": 120000, "seed": 4, "style": "grid", "turn": 0.0}
    lake = lambda x, y: y > 60000
    shore_plan([lake_city], None, lake)
    assert lake_city["fw_shore"]["ns"][1] <= 60000 < lake_city["fw_shore"]["ns"][0], lake_city["fw_shore"]
    held_back = freeway_plan(lake_city)
    assert held_back.lines and max(p[1] for ln in held_back.lines if ln["cls"] == "freeway" for p in ln["points"]) <= 60000
    assert min(p[1] for ln in held_back.lines if ln["cls"] == "freeway" for p in ln["points"]) < -150000, "the dry end runs on"
    river_city = dict(lake_city, name="River")
    river_city.pop("fw_shore")
    shore_plan([river_city], None, lambda x, y: 60000 < y < 66000)
    assert min(river_city["fw_shore"]["ns"]) > 150000, "land beyond the water: the freeway bridges it"
    # ... a river, not a bay: water wider than freeways.FW_WATER_MAX ends it on the near shore, land beyond or not
    bay_city = dict(lake_city, name="Bay")
    bay_city.pop("fw_shore")
    shore_plan([bay_city], None, lambda x, y: 60000 < y < 60000 + _fw.FW_WATER_MAX + 30000)
    assert bay_city["fw_shore"]["ns"][1] <= 60000 < bay_city["fw_shore"]["ns"][0], bay_city["fw_shore"]
    # ... and no interchange with a ramp in the water: the ones a river runs past are left out, the rest stay
    dry_ics = [i["at"] for i in freeway_plan(dict(lake_city, name="Dry", fw_shore=None)).interchanges if i["kind"] != "stack"]
    pond_at = dry_ics[0]
    pond_city = dict(lake_city, name="Pond")
    pond_city.pop("fw_shore")
    shore_plan([pond_city], None, lambda x, y: math.hypot(x - pond_at[0], y - pond_at[1]) < 8000)
    assert min(pond_city["fw_shore"]["ns"]) > 150000, "(a pond 160 m across: the freeway itself bridges it)"
    left = [i["at"] for i in freeway_plan(pond_city).interchanges]
    assert left and all(math.dist(i, pond_at) > 30000 for i in left), (pond_at, left)
    assert _fw.shore_reach(lake_city, options(), None) is None and "fw_shore" not in (shore_plan([river_city], None, None) or river_city)
    # ... and it ends before a neighbouring city's land: none of it runs through the other city
    host = {"name": "Host", "x": 0, "y": 0, "radius": 120000, "seed": 4, "style": "grid", "turn": 0.0}
    guest = {"name": "Guest", "x": 48000, "y": 190000, "radius": 60000, "seed": 9, "style": "grid", "shape": [1.0] * SHAPE_POINTS}
    through = [p for ln in freeway_plan(dict(host)).lines if ln["cls"] == "freeway" for p in ln["points"] if zone_share(guest, p[0], p[1]) <= 1.0]
    shore_plan([host, guest], None, None)
    after = [p for ln in freeway_plan(host).lines if ln["cls"] == "freeway" for p in ln["points"]]
    assert through and after and not any(zone_share(guest, p[0], p[1]) <= 1.0 for p in after), (len(through), len(after))
    assert "fw_shore" not in guest or guest["fw_shore"], "the small one has no freeway to limit"
    # ... and it ends at the foot of a mountain, never inside it; a gentle rise it climbs
    hill_city = dict(lake_city, name="Hill")
    hill_city.pop("fw_shore")
    shore_plan([hill_city], None, None, lambda x, y: max(0.0, y - 80000) * 0.5)
    back, ahead = hill_city["fw_shore"]["ns"]
    assert 80000 <= ahead <= 90000 and back > 150000, hill_city["fw_shore"]
    assert max(p[1] for ln in freeway_plan(hill_city).lines if ln["cls"] == "freeway" for p in ln["points"]) <= ahead
    hill_city.pop("fw_shore")
    shore_plan([hill_city], None, None, lambda x, y: abs(y) * 0.03)
    assert min(hill_city["fw_shore"]["ns"]) > 150000, hill_city["fw_shore"]
    # ... and the land along a joining street fills in with house lots, outside both cities' own edges
    cs2 = [{**cs[0], "joins": [[60000, 0, 80000, 0]]}, {**cs[1], "x": 220000}]
    jl, corridors = join_lots(cs2[0], cs2)
    assert len(jl) >= 12 and len(corridors) == 1 and all(_inside(corridors[0], r[0], r[1]) for r in jl), len(jl)
    assert all(abs(r[1]) > 875 and abs(r[6]) < abs(r[1]) and all(zone_share(c, r[0], r[1]) > 1.0 for c in cs2) for r in jl)
    assert {r[2] for r in jl} == {90.0, -90.0}, "each lot faces the street"
    # a cable-stayed bridge: each tower stands astride the deck (a leg outside each edge, nothing in the roadway),
    # beams join its legs, and its stays run from a leg's top down to that edge of the deck
    long_deck = {"x": {"points": [_point(k, k * 6000, 0, 3000, 0, 1650, lifted=True) for k in range(11)],
                       "segments": [_segment(20 + k, k, k + 1, "ED", "/G", lifted=True) for k in range(10)]}}
    stayed = [{"type": "cable-stayed bridge", "x": 30000, "y": 0, "span_m": 600, "towers": [[18000, 40], [42000, -30]]}]
    tw = bridge_towers(long_deck, stayed, lambda x, y: -800.0, 0.0)
    half = HALF["ED"]
    assert len(tw["legs"]) == 4 and all(abs(r[1]) >= half + TOWER_CLEAR_CM for r in tw["legs"]), ("no leg in the roadway", tw["legs"])
    assert all(r[2] == -800 and abs(r[2] + r[6] * 100 - (3000 + 6000)) < 2 for r in tw["legs"]), "from the bed to 60 m over the deck"
    assert len(tw["beams"]) == 6 and all(r[5] * 100 > 2 * half + 300 and r[1] == 0 for r in tw["beams"]), tw["beams"]
    assert min(r[2] + r[6] * 120 for r in tw["beams"]) <= 3000 - DECK_DEPTH_CM and \
        sorted(r[2] for r in tw["beams"])[2] > 3000 + 1500, "one beam under the deck, the others well over the traffic"
    assert len(tw["stays"]) == 2 * 2 * 2 * 5, len(tw["stays"])
    for x, y, z, yaw, sx, _sy, sz in tw["stays"]:
        ex, ey = x + math.cos(math.radians(yaw)) * sx * 100, y + math.sin(math.radians(yaw)) * sx * 100
        assert any(abs(x - r[0]) < 2 and abs(y - r[1]) < 2 for r in tw["legs"]), "a stay leaves from a leg"
        assert z == 3000 and abs(abs(ey) - (half - 40)) < 3 and 0 < ex < 60000 and y * ey > 0, ("and lands on its edge of the deck", ex, ey)
        assert 3000 + sz * 100 <= 3000 + 6000 and sz > 30
    held = under_cables(tw["spans"])
    assert held(18000, 0) and held(25000, 900) and not held(1000, 0) and not held(18000, 5000)
    assert [c for c in bridge_piers(long_deck, lambda x, y: -800.0)[1] if not held(c[0], c[1])], "the approaches keep their piers"
    assert not bridge_towers(long_deck, [{**stayed[0], "type": "girder bridge (navigable)"}], lambda x, y: -800.0)["legs"]
    assert not bridge_towers(long_deck, [{**stayed[0], "towers": [[18000, 9000]]}], lambda x, y: -800.0)["legs"], "no deck there: no tower"
    # a slip ramp sliding into a street at a shallow angle: the pieces lying inside the street go
    slip = {"T Roads": {"points": [_point(0, 0, 0, 0, 0, 525), _point(1, 20000, 0, 0, 0, 525)], "segments": [_segment(2, 0, 1, "2L", "/G")]},
            "T Freeways": {"points": [_point(k, 2000 + 2000 * k, y, 0, 0, 650, lifted=True) for k, y in enumerate((5000, 3000, 2000, 1200, 400, 200))],
                           "segments": [_segment(10 + k, k, k + 1, "ER", "/G", lifted=True) for k in range(5)]}}
    for _ in range(8):
        if not weld_ends(slip):
            break
    assert len(slip["T Freeways"]["points"]) == 5 and all(abs(p["y"]) > 1000 for p in slip["T Freeways"]["points"]), \
        [(p["x"], p["y"]) for p in slip["T Freeways"]["points"]]
    # a ramp welded 30 m up with a fork 10 m back: the climb is shared out along both branches, none left a cliff
    yp = [_point(0, 0, 0, 3000, 0, 650, lifted=True), _point(1, 1000, 0, 0, 0, 650, lifted=True)] + \
        [_point(2 + k, 3000 + 2000 * k, sd * 500 * (k + 1), 0, 0, 650, lifted=True) for sd in (1, -1) for k in range(30)][:60]
    for n, p in enumerate(yp):
        p["i"] = n
    yp[0]["mesh"] = "SM_Road_RampGore30m"
    ys = [_segment(100, 0, 1, "ER", "/G", lifted=True)] + [_segment(101 + n, 1 if n in (2, 32) else n - 1, n, "ER", "/G", lifted=True) for n in range(2, 62)]
    ya = {"points": yp, "segments": ys}
    assert settle_grades(ya) > 10
    yb = {p["i"]: p for p in yp}
    steep = max(abs(yb[s["a"]]["z"] - yb[s["b"]]["z"]) / math.hypot(yb[s["a"]]["x"] - yb[s["b"]]["x"], yb[s["a"]]["y"] - yb[s["b"]]["y"]) for s in ys)
    assert steep < SETTLE_GRADE + 0.005 and yp[0]["z"] == 3000 and yp[31]["z"] == 0 and yp[61]["z"] == 0, (steep, yp[1]["z"])
    ab = bridge_abutments({"x": {"points": [_point(0, 0, 0, 0, 0, 525), _point(1, 5000, 0, 100, 0, 925, lifted=True), _point(2, 9000, 0, 900, 0, 925, lifted=True)],
                                 "segments": [_segment(3, 0, 1, "2S", "/G"), _segment(4, 1, 2, "BR", "/G", lifted=True)]}})
    assert ab == [[5000, 0, 100 - 150 - 250, 0.0, 1.0, 19.5, 1.0]], ("one abutment, where the street becomes the bridge", ab)
    assert not bridge_piers({"x": {"points": [_point(0, 0, 0, 200, 0, 650, lifted=True), _point(1, 6000, 0, 200, 0, 650, lifted=True)],
                                   "segments": [_segment(2, 0, 1, "ER", "/G", lifted=True)]}}, lambda x, y: 0.0)[0], "a deck on its fill"
    # ... that low ramp stands on its own approach wall instead: from 1 m in the ground up into its slab, end to end
    low = {"x": {"points": [_point(0, 0, 0, 400, 0, 650, lifted=True), _point(1, 6000, 0, 400, 0, 650, lifted=True),
                            _point(3, 3000, -5000, 0, 0, 525), _point(4, 3000, 5000, 0, 0, 525)],
                 "segments": [_segment(2, 0, 1, "ER", "/G", lifted=True), _segment(5, 3, 4, "2L", "/G")]}}
    fills = deck_fills(low, lambda x, y: 0.0)
    assert fills and all(r[2] == -EMBED_CM and abs(r[2] + r[6] * 100 - (400 - DECK_DEPTH_CM + 5)) < 2 for r in fills), fills
    assert abs(sum(r[4] for r in fills) - (60 - 10.5) * 1.04) < 6 and all(abs(r[0] - 3000) > 500 for r in fills), "open over the street"
    assert not deck_fills(low, lambda x, y: -900.0), "high enough for piers: no wall"
    assert not deck_fills(low, lambda x, y: -200.0, sea=0.0), "over water: a bridge"
    assert not deck_fills(low, lambda x, y: 245.0), "a deck lying on the ground needs none"
    # a lot levelled 8 m above the street it fronts: the ground comes down to the road no steeper than 1 in 2 ...
    plain = {"min": [0, 0], "max": [10000, 10000], "grid": 10, "heights": [[0.0] * 10 for _ in range(10)]}
    lot8 = [(3500, 5000, 1.0, 0.0, 1500, 1500, 800.0)]
    by_road = cut_pads(plain, lot8, {(j, 0) for j in range(10)})["heights"][5]
    assert by_road[:3] == [0.0, 500.0, 800.0] and cut_pads(plain, lot8)["heights"][5][1] > 550, by_road
    # ... and a foundation is as deep as the ground really falls away under its building
    assert footings(lambda x, y: -0.3 * x, [("h", 0, 0, 0.0, 12, 12, 0)]) == [1.8]
    assert footings(lambda x, y: 0.0, [("h", 0, 0, 0.0, 12, 12, 0)]) == [0.0]
    # a city turned by hand: its grid, its outline and its industrial side turn together, nothing else changes
    town = {"name": "T", "x": 0, "y": 0, "radius": 60000, "seed": 5, "style": "grid"}
    spun = {**town, "turn": 30.0, "spin": 30.0 - seed_turn(town)}
    assert abs(orientation(spun) - 30.0) < 1e-9 and orientation(town) == seed_turn(town)
    assert abs(outline(spun, 100.0) - outline(town, 100.0 - spun["spin"])) < 1e-9
    off = (districts(spun)["industrial"]["angle"] - districts(town)["industrial"]["angle"] - spun["spin"]) % 360
    assert min(off, 360 - off) < 1e-6, off
    gn, _ge = city_graph(spun)
    far = max(gn.values(), key=lambda p: abs(p[0]) + abs(p[1]) if abs(p[1] - p[0] * math.tan(math.radians(30))) < 50 else 0)
    assert abs(math.degrees(math.atan2(far[1], far[0])) % 180 - 30.0) < 1.0, ("a street through the centre runs at 30 degrees", far)
    # an outline drawn by hand: each handle is where the city's edge is, eased between them, turned with the city
    hand = [1.0] * SHAPE_POINTS
    hand[0], hand[4] = 1.26, 0.74
    drawn = {**town, "shape": hand}
    assert outline(drawn, 0.0) == 1.26 and outline(drawn, 90.0) == 0.74 and abs(outline(drawn, 11.25) - 1.13) < 1e-9
    assert outline({**drawn, "spin": 45.0}, 45.0) == 1.26
    assert all(SHAPE_RANGE[0] <= outline(drawn, a) <= SHAPE_RANGE[1] for a in range(0, 360, 3))
    # one block given a type: only that block changes, every other block of the city is exactly as it was
    big = {"name": "B", "x": 0, "y": 0, "radius": 90000, "seed": 3, "style": "grid"}
    base = city_layout(big)
    pick = next(b for b in base["blocks"] if b[2] not in ("parcels", "collector") and b[3] == "housing")
    parked = city_layout({**big, "zones": [{"x": pick[0], "y": pick[1], "kind": "park"}]})
    there = lambda rows: [r for r in rows if _inside(pick[4], r[0], r[1])]
    assert [b[2] for b in parked["blocks"] if b[:2] == pick[:2]] == ["park"]
    assert not there(parked["lots"]) and len(there(parked["trees"])) > 20, "a park block: trees, nothing built"
    away = lambda rows: [r for r in rows if not _inside(pick[4], r[0], r[1])]
    bare = {"planner": "no"}  # the lots as the blocks cut them, before the planner picks its sites from them
    # (... which it does anew for the city as it now is: with the homes of that block gone a daycare may move)
    assert away(city_layout({**big, "zones": [{"x": pick[0], "y": pick[1], "kind": "park"}]}, bare)["lots"]) == \
        away(city_layout(big, bare)["lots"]) and [b for b in parked["blocks"] if b[:2] != pick[:2]] == \
        [b for b in base["blocks"] if b[:2] != pick[:2]], "no other block moved"
    # lots left green: that share of the block's lots is planted instead of built on, a bigger share keeps the
    # ones a smaller share planted, and no other block changes
    lots_at = lambda lay: {(r[0], r[1]) for r in lay["lots"] if _inside(pick[4], r[0], r[1])}
    plots = city_layout({**big, "zones": [{"x": pick[0], "y": pick[1], "kind": "parcels"}]}, bare)
    half = city_layout({**big, "zones": [{"x": pick[0], "y": pick[1], "kind": "parcels", "green": 0.5}]}, bare)
    most = city_layout({**big, "zones": [{"x": pick[0], "y": pick[1], "kind": "parcels", "green": 0.9}]}, bare)
    n0, n5, n9 = len(lots_at(plots)), len(lots_at(half)), len(lots_at(most))
    assert 0 < n9 < n5 < n0 and lots_at(most) <= lots_at(half) <= lots_at(plots), (n0, n5, n9)
    assert len(there(half["trees"])) > len(there(plots["trees"])) + 10 and away(half["lots"]) == away(plots["lots"])
    assert all(_inside(pick[4], *t) for t in there(half["trees"])), "its trees stand on its own lots"
    assert [b[5] for b in half["blocks"] if b[:2] == pick[:2]] == [0.5]
    all_lots, some_green = city_layout(big, bare)["lots"], city_layout(big, {**bare, "green_lots": 0.3})["lots"]
    assert 0.6 * len(all_lots) < len(some_green) < 0.85 * len(all_lots), (len(all_lots), len(some_green))
    core = city_layout({**big, "zones": [{"x": pick[0], "y": pick[1], "zone": "downtown"}]})
    assert core["rezoned"] and core["rezoned"][0][1] == "downtown" and \
        [b[2:4] for b in core["blocks"] if b[:2] == pick[:2]] == [["parcels", "downtown"]]
    hilly = generate(three, lambda x, y: 800.0 * math.sin(x / 9000.0) + 600.0 * math.cos(y / 7000.0), "/Game/X/Roads", seed=3)
    assert weld_ends(hilly) == 0, "on hills too: generate welds the ends itself"
    assert all(not any(st.problems(a["points"], a["segments"]).values()) for a in multi.values())
    # every ramp touches its roads: a loop starts on the highway's edge, ends on the cross road's edge
    _, inodes, _, cross = interchange("cloverleaf", [0, 0], [100000, 0], ("t",), "6L")
    for (_, i, j, end), p in ((k[1:], v) for k, v in inodes.items() if k[1] == "l"):
        ax, by = p[0] - 18000, p[1]  # highway along +x, crossing at x = 180 m, cross road along y
        if end == 0:
            assert abs(abs(by) - HALF["6L"]) < 1 and p[2] > 0, ("loop starts on the highway edge, up on it", p)
        else:
            assert abs(abs(ax) - HALF["4L"]) < 1, ("loop ends on the cross road edge", p)
    assert abs(cross[0][0] - 18000) < 1 and abs(cross[1][0] - 18000) < 1, cross
    trumpet = made["trumpet"]
    for a in (stack, br, *made.values()):
        bad = st.problems(a["points"], a["segments"])
        assert not any(bad.values()), {k: len(v) for k, v in bad.items()}
    # full relief: the grid spans the hill height, not a third of it
    flat_hills = [v for row in terrain(3, 400000, 6000, [], grid=48)["heights"] for v in row]
    assert max(flat_hills) > 5500 and min(flat_hills) < -5500, (min(flat_hills), max(flat_hills))
    # water: every cell that was under the water line is pushed well below it, never a flat sheet on it
    dry = terrain(3, 400000, 6000, [], grid=48)["heights"]
    wet = terrain(3, 400000, 6000, [], grid=48, opts={"sea_m": -5, "water_depth_m": 12})
    sea = wet["sea"]
    under = [(d, w) for rd, rw in zip(dry, wet["heights"]) for d, w in zip(rd, rw) if d < sea - 1200]
    assert under and all(w < sea - 1200 for d, w in under), "deep water stays deep"
    assert sum(1 for d, w in under if abs(w - sea) < 50) == 0, "no lake bed sits flat at the water line"
    assert all(w <= d + 1 for rd, rw in zip(dry, wet["heights"]) for d, w in zip(rd, rw)), "water only lowers"
    # a city put over a lake leaves the lake where it is: flattening levels the land, never the water
    lake_city = [{"name": "L", "x": 0, "y": 0, "radius": 150000}]
    t = terrain(3, 400000, 6000, lake_city, grid=48, opts={"sea_m": 20, "city_ground": "flatten"})
    lake = terrain(3, 400000, 6000, [], grid=48, opts={"sea_m": 20})
    was_wet = [(j, i) for j in range(48) for i in range(48) if lake["heights"][j][i] < lake["sea"]]
    assert was_wet and all(t["heights"][j][i] == lake["heights"][j][i] for j, i in was_wet), "water stays water under a flattened city"
    assert t["heights"] != lake["heights"], "the land round it is still levelled"
    small = city_graph(cities[0], {"block_m": 60})[1]
    assert len(small) > len(city_graph(cities[0], {"block_m": 150})[1])
    two = generate(cities, lambda x, y: 0.0, "/Game/X/WorldGen/Roads", seed=3, opts={"highway": "2L"})
    assert set(two["Highways"]["segments"][k]["mesh"] for k in range(len(two["Highways"]["segments"]))) <= {"", ROAD_MESH["2L"]}
    steps = terrain(3, 400000, 6000, [], grid=32, opts={"terrace_m": 20})
    assert steps["heights"], "terraces build"
    png, settings = heightmap_png(ter, res=65)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and settings["scale"][2] == 100.0
    # paint mask: a road disc and a turned lot are painted, the corner far from both is not
    sq = {"min": [0, 0], "max": [6400, 6400]}
    wm = weightmap_rows(sq, 65, [(1000, 1000, 300)], [(4000, 4000, 0.0, 1.0, 500, 1000, 0)])
    assert wm[10][10] == 255 and wm[50][45] == 255 and wm[40][50] == 0 and wm[60][5] == 0, "roads and lots painted"
    assert weightmap_png(sq, 65, [], [])[:8] == b"\x89PNG\r\n\x1a\n"
    # landscape size: snapped to whole 63-quad components, 2017 by default (the most UEFN's 2048 x 2048 cap takes)
    assert landscape_res({}) == 2017 and landscape_res({"landscape_px": 1000}) == 1009 and 2017 ** 2 <= UEFN_MAX_SAMPLES
    assert landscape_res({"landscape_px": 2600}) ** 2 > UEFN_MAX_SAMPLES
    # 2017 stays one World Partition region: 16 x 16 components of 2 x 2 sections (32 x 32 came in as 4 copies)
    _png, big = heightmap_png(ter, rows=[[32768] * 2017] * 2017)
    assert big["components"].startswith("16 x 16") and "2x2" in big["components"], big["components"]
    # bicubic refine: a plane stays a plane (no creases), and the coarse samples are kept
    ramp = {"min": [0, 0], "max": [1000, 1000], "grid": 8, "heights": [[i * 10.0 + j for i in range(8)] for j in range(8)]}
    fr = refine(ramp, 32)
    assert all(abs(fr["heights"][j][i] - ((i + 0.5) / 4 - 0.5) * 10 - ((j + 0.5) / 4 - 0.5)) < 0.2
               for j in range(6, 26) for i in range(6, 26)), "a plane refines to the same plane"  # (inside: taps clamp at the border)
    # a keep zone (the cinema): flat at its own height, no city on it, the highway goes round it
    keep = keep_zones([{"name": "Cinema", "x0": -10000, "y0": -12000, "x1": 14000, "y1": 11000, "z": 1341}, {"bad": 1}])
    assert len(keep) == 1
    kc = place_cities(3, 4, ((-200000, -200000), (200000, 200000)), keep)
    assert kc and all(keep_gap(keep[0], c["x"], c["y"]) > c["radius"] for c in kc)
    kt = terrain(3, 400000, 6000, kc, grid=96, keep=keep)
    for x, y in ((0, 0), (12000, 9000), (-8000, -10000)):
        assert abs(st.sample_grid(kt, x, y) - 1341) < 30, (x, y, st.sample_grid(kt, x, y))
    across = [{"name": "W", "x": -150000, "y": 0, "radius": 30000, "style": "grid", "seed": 1},
              {"name": "E", "x": 150000, "y": 0, "radius": 30000, "style": "grid", "seed": 2}]
    hw = generate(across, lambda x, y: 0.0, "/Game/X/Roads", seed=3, opts={"interchange": "direct"}, keep=keep)["Highways"]
    assert not any(in_keep(keep, p["x"], p["y"], 2000) for p in hw["points"]), "the highway goes round the cinema"
    # freeways: a big city gets two urban freeways, a stack and service interchanges, planned before its blocks;
    # the validator is clean, no lot sits in the right of way, streets still run under the viaducts
    mid = {"name": "Mid", "x": 0, "y": 0, "radius": 120000, "seed": 7, "style": "mixed"}
    mk = [i["kind"] for i in freeway_plan(mid).interchanges]
    assert mk and "stack" not in mk and freeway_report(mid)["validation"]["ok"], mk
    assert "diamond_frontage" in mk and "Mid Freeways" in generate([mid], lambda x, y: 0.0, "/G", seed=1)  # FR builds
    big = {"name": "Metro", "x": 0, "y": 0, "radius": 220000, "seed": 7, "style": "mixed"}
    fw = freeway_plan(big)
    kinds = [i["kind"] for i in fw.interchanges]
    assert "stack" in kinds and {"diamond", "parclo"} & set(kinds), kinds
    rep = freeway_report(big)
    assert rep["validation"]["ok"], {k: v for k, v in rep["validation"].items() if v}
    # Blender interchange models: only where the whole square is inside the city's circle, freeway cut at its
    # sockets; 'no' (or a city too small for one) keeps the generated ramps
    from .. import freeways as _fw
    if _fw.models():
        huge = {"name": "Huge", "x": 0, "y": 0, "radius": 450000, "seed": 7, "style": "mixed"}
        got = [i for i in freeway_plan(huge).interchanges if i.get("model")]
        assert got, [i["kind"] for i in freeway_plan(huge).interchanges]
        assert all(math.hypot(*i["at"]) <= 450000 for i in got), "centre inside the city circle"
        # a street crossing gets a street model (XR cross sockets): never a freeway-to-freeway cloverleaf
        assert all(i["kind"] in _fw.SERVICE_FALLBACK for i in got if i["freeway"] != "ns+ew"), [i["kind"] for i in got]
        mid = {"name": "Mid", "x": 0, "y": 0, "radius": 150000, "seed": 7, "style": "mixed"}  # a normal city uses them too
        assert any(i.get("model") for i in freeway_plan(mid).interchanges), [i["kind"] for i in freeway_plan(mid).interchanges]
        world = generate([huge], lambda x, y: 0.0, "/G", seed=1)["Huge Freeways"]
        held = {(round(p["x"]), round(p["y"])) for p in world["points"] if p["mesh"] == "IC_Socket"}
        for i in got:
            for sx_, sy_ in i["model"]["sockets"]:
                assert any(math.hypot(sx_ - x, sy_ - y) < 100 for x, y in held), (i["kind"], sx_, sy_)
        assert sum(1 for p in world["points"] if "/Interchanges/IC_" in "".join(p["lines"])) == len(got)
        assert not any(i.get("model") for i in freeway_plan(huge, {"interchange_models": "no"}).interchanges)
        # a spot on a slope the ground can't take gets no interchange (generated ramps broke there); a
        # gentle natural slope (20 m over the square) keeps its Blender model: the pad is cut level
        n0 = len(freeway_plan(mid).interchanges)
        tilt = lambda x, y: x * 0.01
        slope_plan([mid], None, tilt)  # 1 %: ~14 m over a 1.4 km model, more than a scan may have
        sl = freeway_plan(mid)
        assert mid.get("ic_steep") and len(sl.interchanges) < n0, (n0, [i["kind"] for i in sl.interchanges])
        assert all(i.get("model") for i in sl.interchanges if i.get("kind") != "stack"), sl.interchanges
        assert any(r["why"].startswith("slope") for r in sl.rejected), sl.rejected
        slope_plan([mid], None, tilt, MODEL_GRADE_CM)
        assert not mid.get("ic_steep") and any(i.get("model") for i in freeway_plan(mid).interchanges)
        mid.pop("ic_steep", None)
    # winding suburbs never climb a mountain no house fits on: east of x=60 km the ground rises 35 %
    wind = {"name": "W", "x": 0, "y": 0, "radius": 140000, "seed": 3, "style": "mixed", "features": {"suburbs": "winding"}}
    mount = lambda x, y: max(0.0, x - 60000) * 0.35
    plan_hills([wind], mount)
    wn, we = city_graph(wind)
    sub = [p for n, p in wn.items() if len(n) == 3 and n[0] >= SUB_KEY]
    assert sub and not any(hill_grade(wind, city_options(wind), *p[:2]) > SUB_STEEP for p in sub), len(sub)
    assert not any(p[0] > 75000 for p in sub), max(p[0] for p in sub)
    ends = [n for n in wn if sum((e[0] == n) + (e[1] == n) for e in we) == 1]
    assert not any(hill_grade(wind, city_options(wind), *wn[n][:2]) > SUB_STEEP for n in ends), "no dead end up the mountain"
    # every ramp lands on its road in jittered (mixed) cities of any seed: main roads are straight lines
    for s in range(1, 9):
        r = freeway_report({"name": "K", "x": 0, "y": 0, "radius": 160000, "seed": s, "style": "mixed"},
                           {"core_roads": "6L", "one_way_core": "yes"})["validation"]
        assert r["ok"], (s, {k: v for k, v in r.items() if v and k != "ok"})
    lay = city_layout(big)
    assert lay["lots"] and not any(fw.blocked(r[0], r[1], 0 if r[8] is True else 0.5 * max(r[3], r[4]))
                                   for r in lay["lots"])
    # the planned city: services from demand, complete developments, never across a freeway
    p = lay["plan"]
    assert p and p["counts"].get("fire_station") and p["counts"].get("elementary_school"), p and p["counts"]
    assert {g[5] for g in lay["grounds"]} >= {"apron", "field"}, "sites come complete"
    for f in p["facilities"]:
        assert not fw.blocked(f["x"], f["y"]), ("a site sits in the freeway", f["type"])
    gn, ge = city_graph(big)
    under = sum(1 for a, b, _ in ge if fw.street_blocked(gn[a], gn[b], 0) is False and
                any(_seg_cross(gn[a], gn[b], ln["points"][k][:2], ln["points"][k + 1][:2]) is not None
                    for ln in fw.lines if ln["cls"] == "freeway" for k in range(len(ln["points"]) - 1)))
    assert under > 10, under
    world = generate([big], lambda x, y: 0.0, "/Game/X/Roads", seed=3)
    fa = world["Metro Freeways"]
    assert {"SM_Elev_Deck20m", "SM_Elev_Ramp20m"} <= {s["mesh"] for s in fa["segments"]}
    assert max(p["z"] for p in fa["points"]) >= 4 * 750 - 1, "flyovers on level 4"
    small = {"name": "Town", "x": 0, "y": 0, "radius": 40000, "seed": 7}
    assert not freeway_plan(small).lines and "Town Freeways" not in generate([small], lambda x, y: 0.0, "/G", seed=1)
    assert not freeway_plan(big, {"freeways": "no"}).lines
    # water: a river (x 40..120 m) and a lake (x > 400 m). Main roads bridge the river, side streets stop at the
    # shore, nothing ends in the lake; a highway crosses the lake on a long bridge
    wet = lambda x, y: 4000 < x < 13000 or x > 40000
    wn, we = over_water({0: [-10000, 0], 1: [25000, 0], 2: [-10000, 5000], 3: [25000, 5000], 4: [30000, 0], 5: [50000, 0]},
                        [[0, 1, "4L"], [2, 3, "DT"], [4, 5, "4L"]], wet)
    assert [e[2] for e in we] == ["4L", "4L"], we  # the dirt road stops at the river
    cut = we[1]  # the street into the lake ends at the shore
    assert cut[0] == 4 and cut[1] == ("shore", 4, 5, 1) and not wet(*wn[cut[1]][:2]) and wn[cut[1]][0] > 37000, cut
    vb = we[0][3]
    assert [v[3] for v in vb] == ["ED", "ED", "ED", "4L"] and max(v[2] for v in vb) == BRIDGE_LIFT_CM, vb
    assert all(not wet(v[0], v[1]) for v in vb if v[2] == 0), "ramps start on land"
    hb: list = []
    hn, he = over_water({"a": [0, 0], "b": [130000, 0]}, [["a", "b", "4L"]], lambda x, y: 20000 < x < 110000,
                        {"4L": 1e9}, hb)
    assert he and max(v[2] for v in he[0][3]) == 3000, "900 m of navigable water: a high bridge (30 m clearance)"
    assert hb and hb[0]["type"] == "cable-stayed bridge" and len(hb[0]["towers"]) == 2, hb
    assert over_water({0: [0, 0], 1: [10000, 0]}, [[0, 1, "2L"]], lambda x, y: 5000 < x < 5500)[1], "creek bridged"
    lake = {"name": "Lake", "x": 0, "y": 0, "radius": 60000, "seed": 3, "style": "grid"}
    shore = lambda x, y: x > 30000
    ln, le = city_graph(lake, {"freeways": "no"}, shore)
    assert all(x <= 30000 or len(e) > 3 for e in le for x in (ln[e[0]][0], ln[e[1]][0])), "no street node in the lake"
    assert not any(shore(r[0], r[1]) for r in city_layout(lake, {"freeways": "no"}, shore)["lots"])
    # a 100 m river through a grid city: the streets cross it on bridges, none dead-ends at the bank
    river = lambda x, y: 10000 < x < 20000
    rb: list = []
    rn, re_ = city_graph(lake, {"freeways": "no"}, river, bridges=rb)
    assert not any(river(*rn[n][:2]) for e in re_ for n in e[:2]), "no junction in the river"
    over = [e for e in re_ if min(rn[e[0]][0], rn[e[1]][0]) < 10000 < 20000 < max(rn[e[0]][0], rn[e[1]][0])]
    assert len(over) >= 5 and all(max(v[2] for v in e[3]) >= 300 for e in over), over[:2]
    wide_river = city_graph(lake, {"freeways": "no", "core_roads": "6L"}, river)[1]
    assert all(v[3] in (*structure_kinds(e[2]), e[2]) for e in re_ + wide_river if len(e) > 3 and e[3]
               for v in e[3] if len(v) > 3), "a bridge is as wide as the street it carries"
    assert len(rb) == len(over), (len(rb), len(over))
    ends = [rn[n] for n in rn if isinstance(n, tuple) and n and n[0] == "shore"]
    assert all(math.hypot(*p[:2]) > 0.85 * lake["radius"] for p in ends), ("only the city edge ends at the bank", ends)
    # city size and distance between cities
    small_c = place_cities(5, 3, ((-300000, -300000), (300000, 300000)), size=0.6, gap_cm=50000)
    big_c = place_cities(5, 3, ((-300000, -300000), (300000, 300000)), size=1.5, gap_cm=50000)
    assert max(c["radius"] for c in small_c) < min(c["radius"] for c in big_c), "city size scales radii"
    for cs in (small_c, big_c):
        assert all(math.hypot(a["x"] - b["x"], a["y"] - b["y"]) >= a["radius"] + b["radius"] + 50000 - 1
                   for i, a in enumerate(cs) for b in cs[i + 1:])
    apart = spread([{"x": 0, "y": 0, "radius": 50000}, {"x": 60000, "y": 0, "radius": 50000}], 80000)
    assert abs(apart[1]["x"] - apart[0]["x"] - 180000) < 2 and apart[0]["x"] == -60000, apart
    # canal city: canals replace every third inner street line, cross streets bridge them on canal arches
    venice = {"name": "Canalle", "x": 0, "y": 0, "radius": 60000, "seed": 9, "style": "grid"}
    co = {"canals": "canal_city", "freeways": "no", "planner": "no"}
    assert canal_lines(venice, co)
    br: list = []
    vw = generate([venice], lambda x, y: 0.0, "/Game/X/Roads", seed=1, opts=co, bridges=br)
    assert "Canalle Canals" in vw and vw["Canalle Canals"]["segments"], sorted(vw)
    assert all(abs(p["z"] + CANAL_DEPTH_CM) < 1 for p in vw["Canalle Canals"]["points"]), "laid at the channel bed"
    assert br and all(b["type"] == "canal arch" and b["clearance_m"] == 3.0 for b in br), br[:2]
    cw = canal_wet(venice, co)
    vn, ve = city_graph(venice, co)
    assert not any(cw(*vn[n][:2]) for e in ve for n in e[:2]), "no street ends in a canal"
    assert not any(cw(r[0], r[1]) for r in city_layout(venice, co)["lots"]), "no lot in a canal"
    walks = paths_actor(venice, co, [], lambda x, y: 0.0, "/Game/X/Roads")
    assert len(walks["segments"]) > len(paths_actor(venice, {**co, "canals": "none"}, [], lambda x, y: 0.0,
                                                    "/Game/X/Roads")["segments"]), "canal-side walkways"
    # airports: planned before the streets, built as actors; no city street inside, no homes in the noise zone
    ac = {"name": "Aero", "x": 0, "y": 0, "radius": 120000, "seed": 4, "style": "grid"}  # ~22k people: regional
    aps = plan_airports([ac], lambda x, y: 0.0, None, [], ((-600000, -600000), (600000, 600000)), {})
    assert aps and aps[0]["type"] == "regional", aps
    # a city's own features beat the world's: its airport taken off, a small town given one, freeways off
    bounds6 = ((-600000, -600000), (600000, 600000))
    assert plan_airports([{**ac, "features": {"airports": "none"}}], lambda x, y: 0.0, None, [], bounds6, {}) == []
    town = {"name": "Tiny", "x": 0, "y": 0, "radius": 30000, "seed": 4, "style": "grid"}
    assert not plan_airports([dict(town)], lambda x, y: 0.0, None, [], bounds6, {})
    got = plan_airports([{**town, "features": {"airports": "auto"}}], lambda x, y: 0.0, None, [], bounds6, {"airports": "none"})
    assert got and got[0]["type"] == "general_aviation", got
    assert not freeway_plan({**big, "features": {"freeways": "no"}}).lines and freeway_plan(big).lines
    builds: dict = {}
    aw = generate([ac], lambda x, y: 0.0, "/Game/X/Roads", seed=1, opts={"freeways": "no"}, airports=aps,
                  airport_builds=builds)
    assert "Aero Airport" in aw and "Aero Airport Roads" in aw, sorted(aw)
    am = {s["mesh"] for s in aw["Aero Airport"]["segments"]}
    assert {"SM_Air_Runway_20m", "SM_Air_Taxiway_10m"} <= am, am
    assert builds["Aero Airport"]["report"]["ok"], builds["Aero Airport"]["report"]
    from .. import airport as _ap
    gn, ge = city_graph(ac)
    assert not any(_ap.inside(aps[0], *gn[n][:2]) for n in gn), "no city street on the airfield"
    lay = city_layout(ac, {"planner": "no"})
    assert not any(r[7] in ("house", "apartment") and _ap.in_noise(aps[0], r[0], r[1]) for r in lay["lots"])
    # reserved land beats freeways: an airport across the freeway's path stops the mainline short and drops
    # the interchanges whose cross roads it took; every ramp left still lands on a road
    for s in (1, 2, 3):
        rc = {"name": "Res", "x": 0, "y": 0, "radius": 160000, "seed": s, "style": "mixed"}
        base = freeway_plan(rc)
        ns = next(l for l in base.lines if l["group"] == ("main", "ns"))
        tip = ns["points"][-1]
        rc["airports"] = [{"city": "Res", "type": "general_aviation", "center": [round(tip[0]), round(tip[1])],
                           "heading": 0.0}]
        fwr = freeway_plan(rc)
        main = next(l for l in fwr.lines if l["group"] == ("main", "ns"))
        assert not any(_ap.inside(rc["airports"][0], p[0], p[1]) for p in main["points"]), "freeway stops short"
        rep = freeway_report(rc)["validation"]
        assert rep["ok"], (s, {k: v for k, v in rep.items() if v and k != "ok"})
    print("citygen ok", {k: len(v["segments"]) for k, v in actors.items()}, "metro interchanges", kinds)


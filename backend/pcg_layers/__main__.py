"""Self-check: py -3 -m backend.pcg_layers"""

from __future__ import annotations

import math

from ..citygen import outline
from . import *  # noqa: F403
from . import (_Hash, _in_wedge, _in_where, _lot_ground, _lot_hits_road, _rects_hit, _Rects, _ring,
               _where_test)

def _demo() -> None:
    # _Rects.hit reads only the cells round the rectangle: the same answers as testing every one placed
    import random
    rnd = random.Random(7)
    turned = lambda: (lambda f: (rnd.uniform(-40000, 40000), rnd.uniform(-40000, 40000), math.cos(f), math.sin(f),
                                 rnd.choice((150, 400, 2500, 9000)) * rnd.uniform(0.3, 1), rnd.choice((150, 600, 6000, 12000)) * rnd.uniform(0.3, 1)))(
        rnd.uniform(0, 6.3))
    placed_r, rects = [turned() for _ in range(400)], _Rects()
    for r in placed_r:
        rects.add(r)
    for _ in range(4000):
        q, m = turned(), rnd.choice((0.0, 30.0, 100.0, 800.0))
        assert rects.hit(q, m) == any(_rects_hit(q, o, m) for o in placed_r), (q, m)
    cities = [{"name": "A", "x": 0, "y": 0, "radius": 50000, "districts": {"downtown": {"radius": 20000},
               "normal": {"radius": 37500}, "housing": {"radius": 50000}, "industrial": {"angle": 90}}}]
    full = preset()
    assert len(full) == len(NATURE) + len(CITY_BUILDINGS) + len(CITY_STREET) and all(l["meshes"] == [] for l in full)
    assert not any("grass" in l["name"].lower() for l in full), "no grass layer"
    assert {l["kind"] for l in full if l["role"] in ("tree", "rock", "bush")} == {"foliage"}, "nature is foliage"
    roles = {l["role"] for l in full}
    for r in ("traffic_light", "stop_sign", "street_sign", "bench", "bin", "hydrant", "bus_stop", "billboard",
              "skyscraper", "factory", "hospital", "school", "police", "fire_station", "park", "parking", "mall"):
        assert r in roles, r
    # a city: an east-west and a north-south road crossing at a junction in the middle
    J = {"i": 9, "x": 0, "y": 0, "w": 1200, "mesh": "SM_Junc_Gen"}
    mouths = [{"i": 10 + k, "x": 1500 * math.cos(k * math.pi / 2), "y": 1500 * math.sin(k * math.pi / 2), "w": 875, "mesh": ""}
              for k in range(4)]
    ends = [{"i": 20 + k, "x": 60000 * math.cos(k * math.pi / 2), "y": 60000 * math.sin(k * math.pi / 2), "w": 875, "mesh": ""}
            for k in range(4)]
    pts = [J] + mouths + ends
    segs = [{"a": 9, "b": 10 + k, "mesh": ""} for k in range(4)] + [{"a": 10 + k, "b": 20 + k, "mesh": "SM_Road_TwoLane"} for k in range(4)]
    actors = [(pts, segs)]
    juncs = junctions(actors)
    assert len(juncs) == 1 and len(juncs[0][3]) == 4
    corners = junction_corners(juncs, 1.0, 4)
    assert len(corners) == 4 and all(abs(abs(x) - abs(y)) < 1 and math.hypot(x, y) > 875 for x, y, _ in corners), corners
    out = plan({"name": "Valley", "generated": {"size_km": 6}}, full, cities, "valley_roads", asset_set="fortnite", juncs=juncs)
    ids = {n["id"] for n in out["nodes"]}
    assert all(e["to"] in ids and (e.get("from") in ids or "param" in e) for e in out["edges"]), "every wire joins real nodes"
    assert not any(i.startswith(("Trees_", "Rocks_", "Bushes_")) for i in ids), "foliage is not in the PCG graph"
    assert [f["layer"] for f in out["foliage"]] == ["Trees", "Rocks", "Bushes"]
    assert "TrafficLights_Corners" in ids and "Skyscrapers_InArea" in ids and "Houses_NotThere" in ids
    order = [n["id"] for n in out["nodes"]]
    for p in ("Skyscrapers", "StreetLamps", "TrafficLights"):
        assert order.index(f"{p}_Vary") < order.index(f"{p}_Footprint") < order.index(f"{p}_NoOverlap") < order.index(f"{p}_Spawn"), p
    assert all("Fortnite" in p["from"] for p in out["pieces"]) and out["asset_set"] == "fortnite"
    sky = next(p for p in out["pieces"] if p["layer"] == "Skyscrapers")
    assert sky["storeys"] == [23, 62] and sky["lot_m"] == [32, 32], sky
    assert "own: BuildingKit House" in next(p for p in plan({"name": "V"}, full, cities, "t", juncs=juncs)["pieces"]
                                            if p["layer"] == "Houses")["from"]
    # offline preview of the whole city
    ter = {"min": [-80000, -80000], "max": [80000, 80000]}
    pv = preview(full, cities, ter, road_samples(actors), lambda x, y: 0.0, juncs=juncs, arms=arm_samples(actors))
    for name in ("Trees", "Rocks", "Skyscrapers", "Office blocks", "Factories", "Warehouses", "Apartments", "Shops",
                 "Houses", "Traffic lights", "Street lamps", "Trash bins"):
        assert pv[name]["shown"] > 0, (name, {n: v["shown"] for n, v in pv.items()})
    for name, v in pv.items():
        for x, y, *_ in v["points"]:
            assert min(abs(x), abs(y)) > 875 or max(abs(x), abs(y)) > 60000, f"{name} on a road at {x},{y}"
    od = lambda p: math.hypot(p[0], p[1]) / outline(cities[0], math.degrees(math.atan2(p[1], p[0])))  # outline distance
    assert all(od(p) >= 50000 for p in pv["Trees"]["points"]), "trees stay outside the city's irregular edge"
    # a forest covers the whole map: as dense as asked within its reach of a city; beyond it just as dense when
    # the layer's budget of points allows, thinner when it does not, but never gone (it once stopped at its reach)
    wood = clean({"name": "Wood", "kind": "foliage", "where": "outside", "role": "tree", "density": 0.05, "cover": 1.0,
                  "reach": 1.2, "seed": 3})
    wide = {"min": [-200000, -200000], "max": [200000, 200000]}
    per_ha = lambda pts, lo_, hi_: sum(1 for p in pts if lo_ <= math.hypot(p[0], p[1]) < hi_) / (math.pi * (hi_ ** 2 - lo_ ** 2) / 1e8)
    roomy = preview([wood], cities, wide, [], lambda x, y: 0.0, foliage_limit=200000)["Wood"]["points"]
    assert per_ha(roomy, 65000, 110000) > 0.8 * per_ha(roomy, 52000, 60000) > 2, (per_ha(roomy, 65000, 110000), per_ha(roomy, 52000, 60000))
    tight = preview([wood], cities, wide, [], lambda x, y: 0.0, foliage_limit=1000)["Wood"]["points"]
    near_, far_ = per_ha(tight, 52000, 60000), per_ha(tight, 65000, 190000)
    assert 0 < far_ < 0.6 * near_, (near_, far_)
    assert {tuple(p[:2]) for p in roomy if math.hypot(p[0], p[1]) < 60000} == \
        {tuple(p[:2]) for p in preview([clean({**wood, "reach": 0})], cities, wide, [], lambda x, y: 0.0, foliage_limit=200000)["Wood"]["points"]
         if math.hypot(p[0], p[1]) < 60000}, "round the cities it stands where it stood"
    # no tree on a bank: a 45 degree slope between two flats stays bare, the flats are forest
    bank = lambda x, y: max(0.0, min(20000.0, x - 100000.0))  # flat, then 45 degrees for 200 m, then flat again
    slope_wood = preview([clean({**wood, "reach": 0, "density": 0.2})], cities, wide, [], bank, foliage_limit=200000)["Wood"]["points"]
    assert not [p for p in slope_wood if 100500 < p[0] < 119500], "a tree on the bank"
    assert [p for p in slope_wood if p[0] < 99000] and [p for p in slope_wood if p[0] > 121000], "the flats are wooded"
    # nothing stands on a road, whatever placed it: a junction's arms and a private street are road too. Trees
    # and bushes go when they are within half a metre of one, street furniture when it stands a metre into one
    town = [{"name": "T", "x": 0, "y": 0, "radius": 50000, "private": {"streets": [[[20000, 20000], [30000, 20000]]]}}]
    surface = surface_samples(actors, town)
    assert len(arm_samples(actors)) > 12 and any(abs(x) < 1 and abs(y) < 1 for x, y, _h in surface), "the junction's middle is road"
    stuff = {"Garden trees": {"kind": "scatter", "role": "tree", "count": 4, "shown": 4,
                              "points": [[300, 400, 0, 0, 1], [0, 30000, 0, 0, 1], [25000, 20200, 0, 0, 1], [9000, 9000, 0, 0, 1]]},
             "Street lamps": {"kind": "along_roads", "role": "street_lamp", "count": 3, "shown": 3,
                              "points": [[30000, 1025, 0, 0, 1], [30000, 500, 0, 0, 1], [900, 900, 0, 0, 1]]},
             "Houses": {"kind": "buildings", "role": "house", "count": 1, "shown": 1,
                        "points": [[0, 0, 0, 0, 1, 6, 0, 10.0, 8.0]]},
             "Parking stalls": {"kind": "site", "role": "site", "count": 1, "shown": 1, "points": [[0, 0, 0, 0, 1, 1, 1]]}}
    assert clear_roads(stuff, surface) == {"Garden trees": 3, "Street lamps": 1}, stuff
    assert stuff["Garden trees"]["points"] == [[9000, 9000, 0, 0, 1]] and stuff["Garden trees"]["count"] == 1
    assert [p[:2] for p in stuff["Street lamps"]["points"]] == [[30000, 1025], [900, 900]], "a lamp at the kerb, one on the corner: they stay"
    assert len(stuff["Houses"]["points"]) == 1 and len(stuff["Parking stalls"]["points"]) == 1
    # a cap on every street that ends: the dead end gets a turnaround; a through street, a street that goes on in
    # another actor (two cities joined), a bridge's end and a freeway's end get none
    street = lambda i, a, b: {"a": a, "b": b, "mesh": "SM_Road_TwoLaneSidewalk_Straight10m"}
    pt = lambda i, x, y, mesh="": {"i": i, "x": x, "y": y, "z": 120.0, "w": 875, "mesh": mesh}
    town_a = ([pt(0, 0, 0), pt(1, 10000, 0), pt(2, 20000, 0, "SM_Junc_Gen"), pt(3, 20000, 9000), pt(4, 30000, 0)],
              [street(0, 0, 1), street(1, 1, 2), street(2, 2, 3), street(3, 2, 4)])
    town_b = ([pt(0, 30000, 0), pt(1, 40000, 0), pt(2, 50000, 0)],
              [street(0, 0, 1), {"a": 1, "b": 2, "mesh": "SM_Road_BridgeSidewalk_Straight20m"}])
    fast = ([pt(0, 0, 50000), pt(1, 9000, 50000)], [{"a": 0, "b": 1, "mesh": "SM_Road_Freeway_Lane20m"}])
    caps = road_ends([town_a, town_b, fast], {"min": [-90000, -90000], "max": [90000, 90000]})["Road ends"]
    assert sorted(r[:2] for r in caps["points"]) == [[0, 0], [20000, 9000]], caps["points"]
    assert caps["points"][0][2] == 123 and caps["points"][0][4] == 1.225 and caps["keep_z"] and caps["pad"] is False
    assert not road_ends([town_a], {"min": [-3000, -90000], "max": [33000, 90000]})["Road ends"]["points"][0][0] == 0, "none at the map's edge"
    # ... and the roads get it as they are made: one turnaround node per dead end, never a second one
    as_made = lambda p: {**p, "yaw": 0.0, "pitch": 0.0, "lines": []}  # noqa: E731
    made = ([as_made(p) for p in town_a[0]], [{**s, "i": k, "ta": 0.0, "tb": 0.0, "raise": True, "lines": []}
                                          for k, s in enumerate(town_a[1])])
    world = {"min": [-90000, -90000], "max": [90000, 90000]}
    assert cap_all([made], world) == 3 and sum(1 for p in made[0] if p["mesh"] == "SM_Junc_End") == 3  # alone, its east end too
    assert cap_all([made], world) == 0 and not road_ends([made], world), "a capped end is no dead end"
    # ... and no tree inside a building (the forest is placed first; a house built along a road knows nothing of it)
    grove = {"Trees": {"kind": "foliage", "role": "tree", "count": 4, "shown": 4,
                       "points": [[10000, 10000, 0, 0, 1], [10300, 10350, 0, 0, 1], [10450, 10000, 0, 0, 1], [10700, 10000, 0, 0, 1]]},
             "Houses": {"kind": "buildings", "role": "house", "count": 1, "shown": 1,
                        "points": [[10000, 10000, 0, 0, 1, 6, 0, 10.0, 8.0]]}}
    assert clear_buildings(grove) == {"Trees": 2}, "(the house is 8 m deep along x, 10 m wide: the first two are inside it)"
    assert grove["Trees"]["points"] == [[10450, 10000, 0, 0, 1], [10700, 10000, 0, 0, 1]], "a tree half a metre behind the wall stays"
    # ... nor on a car park; a tree on a paved lot stays (it stands in its pit)
    lot_trees = lambda: {"Trees": {"kind": "foliage", "role": "tree", "count": 2, "shown": 2, "points": [[500, 0, 0, 0, 1], [5000, 0, 0, 0, 1]]}}
    slab = {"kind": "site", "role": "site", "mesh": "SM_Kit_ParkingLot", "box_m": [1, 1, 0.08], "points": [[0, 0, 0, 0.0, 30.0, 20.0, 1.0]]}
    assert clear_buildings({**lot_trees(), "Complex parking": slab}) == {"Trees": 1}
    assert clear_buildings({**lot_trees(), "Paved lots": {**slab, "planted": True}}) == {}
    # a planned park is trees in groves on the ground as it lies, off its own playground and paths; it levels nothing
    park_c = {**cities[0], "private": {"grounds": [[0, 0, 0.0, 20.0, 14.0, "playground", "neighbourhood_park"],
                                                   [0, 2000, 0.0, 60.0, 3.0, "path", "neighbourhood_park"]], "parking": []}}
    green = {"Parks": {"kind": "buildings", "role": "park", "points": [[0, 0, 0, 0, 1.0, 1.0, 0, 80.0, 60.0, 0.0, [], []]]}}
    # (every tree on open land comes from nature.grow, into the world's forest layer: none without one)
    woods = lambda: {"Trees": {"kind": "foliage", "role": "tree", "count": 0, "shown": 0, "points": [],
                               "grow": {"cover": 1.0, "sink_cm": 0}}}
    assert park_trees(dict(green), [park_c], lambda x, y: 0.1 * x) == 0, "no forest layer: no tree anywhere"
    green.update(woods())
    assert park_trees(green, [park_c], lambda x, y: 0.1 * x) > 15
    grown = green["Trees"]["points"]
    assert all(abs(t[0]) <= 3000 and abs(t[1]) <= 4000 and abs(t[2] - 0.1 * t[0]) <= 1 for t in grown), "inside the park, each on its own ground"
    assert len({t[0] % 700 for t in grown}) > len(grown) // 2, "scattered like the forest, not on a grid"
    assert not any(abs(t[0]) < 1000 and abs(t[1]) < 700 for t in grown) and not any(abs(t[0]) < 3000 and abs(t[1] - 2000) < 150 for t in grown)
    assert not pads(green), "a park levels nothing"
    # the city centre stands on concrete round its buildings: a downtown lot with a building is paved at its level;
    # an empty one, a house's lot and a suburb's are not; the paving levels nothing itself
    core = {**cities[0], "blocks": [[0, 0, "parcels", "downtown", [(-6000, -6000), (6000, -6000), (6000, 6000), (-6000, 6000)], 0]],
            "lots": [[-3000, 0, 90.0, 2800, 3000, -3000, 1500, "", False], [0, 0, 90.0, 2800, 3000, 0, 1500, "", False],
                     [3000, 0, 90.0, 2800, 3000, 3000, 1500, "house", False], [45000, 0, 90.0, 1600, 3000, 45000, 1500, "", False]]}
    stands = {"Offices": {"kind": "buildings", "role": "office", "points": [[-3000, 200, 340, 90, 1.0, 20.0, 0, 24.0, 22.0, 0.5]]},
              "Homes": {"kind": "buildings", "role": "house", "points": [[3000, 200, 100, 90, 1.0, 6.0, 0, 12.0, 10.0, 0.5]]}}
    paved = paved_lots([core], stands, lambda x, y: 100.0)["Paved lots"]
    # level ground: one slab, the office's whole lot (30 m deep along its facing, 28 m wide), its top 6 cm over the ground
    assert [p[:2] + p[4:6] for p in paved["points"]] == [[-3000, 0, 30.0, 28.0]], paved["points"]
    assert paved["lots"] == 1 and all(p[2] + paved["box_m"][2] * 100 * p[6] == 100 + PAVE_UP_CM for p in paved["points"])
    assert not pads({"Paved lots": paved}), "it lies on the ground as it is: it levels nothing"
    assert not paved_lots([core], {}, lambda x, y: 100.0), "nothing built: no paving (the forest grows there)"
    # a slope: only the slabs level with the building, never a staircase of boxes down the hill
    hill = lambda x, y: 100.0 + 0.1 * y
    steps = paved_lots([core], stands, hill)["Paved lots"]["points"]
    assert steps and len({p[2] for p in steps}) == 1, steps
    assert all(abs(p[0] + 3000) < 1500 for p in steps), "the office's lot only"
    # the land a planned city leaves free: the middle of a block of plain lots, the ground beyond its last lots;
    # never a lot, a block with its own layout, a planned ground. A city with no plan has none.
    sq = lambda x0, y0, s: [[x0, y0], [x0 + s, y0], [x0 + s, y0 + s], [x0, y0 + s]]
    planned = [{**cities[0], "blocks": [[5000, 5000, "parcels", "normal", sq(0, 0, 10000), 0], [25000, 5000, "loop", "normal", sq(20000, 0, 10000), 0]],
                "lots": [[1000, 5000, 0.0, 1600, 2000, 2000, 5000, "", False]],
                "private": {"grounds": [[5000, 9000, 0.0, 20.0, 10.0, "field"]]}}]
    free = open_land(planned)
    assert free(5000, 5000) and free(-5000, 5000), "a court, and land no block has"
    assert not free(1000, 5000) and not free(2300, 5000) and not free(25000, 5000) and not free(5000, 9000), "a lot, 3 m off it, a subdivision, a sports field"
    assert not open_land(cities)(5000, 5000) and not open_land(planned + cities)(-5000, 5000 + 200000), "a city with no plan has none"
    wild = clean({"name": "Wild", "kind": "scatter", "where": "outside", "role": "tree", "density": 2.0, "seed": 9})
    grown = preview([wild], planned, {"min": [-80000, -80000], "max": [80000, 80000]}, [], lambda x, y: 0.0, limit=100000)["Wild"]["points"]
    in_town = [p for p in grown if math.hypot(p[0], p[1]) < 30000]
    assert len(in_town) > 200 and not any(20000 <= p[0] <= 30000 and 0 <= p[1] <= 10000 for p in in_town), "the forest grows on the city's free land, not in its subdivision"
    assert not any(abs(p[0] - 1000) < 1300 and abs(p[1] - 5000) < 1100 for p in in_town), "nor on a lot"
    assert all(od(p) < 20000 and 90 <= p[5] <= 240 for p in pv["Skyscrapers"]["points"])
    for p in pv["Factories"]["points"]:
        assert 20000 <= od(p) < 50000 and _in_wedge(cities[0], p[0], p[1]), "factories in the industrial zone"
    for name in ("Apartments", "Shops", "Houses"):
        assert not any(_in_wedge(cities[0], p[0], p[1]) for p in pv[name]["points"]), f"no {name} in the industrial zone"
    houses = pv["Houses"]["points"]
    assert all(37500 <= math.hypot(p[0], p[1]) / outline(cities[0], math.degrees(math.atan2(p[1], p[0]))) < 50000
               for p in houses), "houses only in the (irregular) housing ring"
    # skyline: core buildings are taller on average, and no two close neighbours share a height
    apts = pv["Apartments"]["points"] + pv["Office blocks"]["points"] + pv["Skyscrapers"]["points"]
    near = [p[5] for p in pv["Skyscrapers"]["points"] if math.hypot(p[0], p[1]) < 10000]
    far = [p[5] for p in pv["Skyscrapers"]["points"] if math.hypot(p[0], p[1]) >= 10000]
    assert near and far and sum(near) / len(near) > sum(far) / len(far), (near, far)
    for name in ("Skyscrapers", "Apartments"):
        ps, span = pv[name]["points"], 150 if name == "Skyscrapers" else 20
        same = sum(1 for i, a in enumerate(ps) for b in ps[i + 1:]
                   if math.hypot(a[0] - b[0], a[1] - b[1]) < 2500 and abs(a[5] - b[5]) < 0.1 * span)
        assert same == 0, f"{same} {name} neighbours with the same height"
    assert apts
    lots = [(p[0], p[1], 50 * max(*pv[n]["size_m"])) for n in pv if "size_m" in pv[n] for p in pv[n]["points"]]
    clash = sum(1 for i, a in enumerate(lots) for b in lots[i + 1:] if math.hypot(a[0] - b[0], a[1] - b[1]) < (a[2] + b[2]) * 0.7)
    assert clash == 0, f"{clash} buildings overlap"
    # flat ground: no foundation; a house gets one straight drive, a shop one walkway: one strip a lot
    h = pv["Houses"]["points"][0]
    assert h[9] == 0.0 and [p[0] for p in h[10]] == [DRIVE_M] and len(h[10][0][1]) == 6, h
    assert len(pv["Shops"]["points"][0][10]) == 1
    assert pv["Parks"]["shown"] == 0 or pv["Parks"]["points"][0][10] == []
    # a hillside rising 30 % away from the street: a level pad at the mean, a foundation where it falls away,
    # the walkway goes straight down and the driveway winds across the slope
    house = clean({"name": "H", "kind": "buildings", "role": "house"})
    slope = lambda x, y: -0.3 * x  # +X (the street side) is downhill
    pad, found, paths = _lot_ground(house, slope, 0, 0, 0.0, 12, 12)
    assert abs(pad) < 1 and found == 1.8, (pad, found)
    assert len(h) > 11 and h[11] == [], "no wall slabs on lot edges"
    # a block the person rezoned is that zone for every layer, whatever ring it lies in
    hx = next(r for r in range(20000, 60000, 500) if _in_where("housing", cities, r, 0))  # a spot in the housing ring
    sq = [[hx - 1000, -2000], [hx + 1000, -2000], [hx + 1000, 2000], [hx - 1000, 2000]]
    zoned = [{**cities[0], "rezoned": [[sq, "downtown"]]}]
    assert _in_where("downtown", zoned, hx, 0) and not _in_where("housing", zoned, hx, 0)
    assert _in_where("cities", zoned, hx, 0) and not _in_where("outside", zoned, hx, 0)
    assert all(_in_where(w, zoned, hx, 9000) == _in_where(w, cities, hx, 9000)
               for w in ("downtown", "normal", "housing", "industrial")), "the block beside it is as it was"
    # a city whose centre district was moved: downtown, and the skyline's peak, are round the new spot
    off_c = {**cities[0], "core": [20000, 0]}
    assert _where_test("downtown", [off_c])(20000, 0) and not _where_test("downtown", [off_c])(-15000, 0)
    assert _where_test("downtown", cities)(-15000, 0) and core_peak([off_c], 20000, 0) == 1.0 > core_peak([off_c], 0, 0)
    assert _where_test("cities", [off_c])(-45000, 0) and not _where_test("cities", [off_c])(70000, 0), "its edge stays"
    ring_ = _ring(off_c, 20000.0)
    assert inside(ring_, 20000, 0) and not inside(ring_, -15000, 0), "the downtown ring is drawn round it"
    # what a city planned stays free for it: a tower along the street stands on no planned car park, and not on
    # the spot of a planned building that a later layer builds
    lot_town = {**cities[0], "lots": [[9000, -3000, 90.0, 3000, 2500, 9000, -1700, "shop", True]],
                "private": {"parking": [[-9000, -3000, 0.0, 60.0, 30.0]], "stalls": []}}
    tower = {"name": "Towers", "kind": "buildings", "role": "skyscraper", "where": "cities", "width_m": 30, "depth_m": 30,
             "spacing_m": 40, "setback_m": 4, "order": 1}
    shops = {"name": "Shops", "kind": "buildings", "role": "shop", "width_m": 14, "depth_m": 12, "order": 2}
    street = [(x, -100.0, 700.0, 1.0, 0.0) for x in range(-30000, 30001, 400)]
    stood = preview([tower, shops], [lot_town], {"min": [-80000, -80000], "max": [80000, 80000]}, street, lambda x, y: 0.0)
    for p in stood["Towers"]["points"]:
        assert not (-12000 - 1500 < p[0] < -6000 + 1500 and -4500 - 1500 < p[1] < -1500 + 1500), ("a tower on a car park", p[:2])
        assert not (7500 - 1500 < p[0] < 10500 + 1500 and -4250 - 1500 < p[1] < -1750 + 1500), ("a tower on a planned shop's spot", p[:2])
    assert stood["Towers"]["points"] and len(stood["Shops"]["points"]) == 1, "the towers stand elsewhere along the street; the shop is built"
    # a block laid out as a park: a layer that builds along road frontage leaves it empty
    front = clean({"name": "Mansions", "kind": "buildings", "where": "cities", "width_m": 30, "depth_m": 30, "spacing_m": 60,
                   "setback_m": 6, "role": "mansion"})
    street = [(hx - 8000 + i * 400, -2600, 400.0, 1.0, 0.0) for i in range(40)]  # along the block's south edge
    lawn = [[hx - 6000, -2400], [hx + 6000, -2400], [hx + 6000, 4000], [hx - 6000, 4000]]
    in_sq = lambda res: [p for p in res["Mansions"]["points"] if inside(lawn, p[0], p[1])]
    no_lots = [{k: v for k, v in cities[0].items() if k != "lots"}]
    plain = preview([front], no_lots, {}, street, lambda x, y: 0.0)
    parked = preview([front], [{**no_lots[0], "blocks": [[hx, 0, "park", "housing", lawn]]}], {}, street, lambda x, y: 0.0)
    assert in_sq(plain) and not in_sq(parked), ("nothing built on a park block", len(in_sq(plain)), len(in_sq(parked)))
    assert len(parked["Mansions"]["points"]) >= len(plain["Mansions"]["points"]) - len(in_sq(plain)) - 1
    shop = clean({"name": "S", "kind": "buildings", "role": "shop"})
    assert _lot_ground(shop, lambda x, y: 0.0, 0, 0, 0.0, 20, 15)[2][0][0] == 18.0, "a shop fronts the street full width"
    # a house is not built on a foundation deeper than 6 m: it goes and its lot is planted; a park and a planned
    # site stay; a tower stands on a podium down to 14 m
    deep = {"Houses": {"kind": "buildings", "role": "house", "count": 3, "shown": 3, "points": [
                [0, 0, 0, 0, 1, 7, 0, 16, 24, 6.5, [], []], [5000, 0, 0, 0, 1, 7, 0, 12, 12, 6.0, [], []],
                [9000, 0, 0, 0, 1, 7, 0, 12, 12, 9.0, [], [], "hospital"]]},
            "Towers": {"kind": "buildings", "role": "skyscraper", "count": 2, "shown": 2, "points": [
                [20000, 0, 0, 0, 1, 90, 0, 32, 32, 13.5, [], []], [30000, 0, 0, 0, 1, 90, 0, 32, 32, 14.5, [], []]]},
            "Parks": {"kind": "buildings", "role": "park", "count": 1, "shown": 1, "points": [[0, 9000, 0, 0, 1, 0, 0, 40, 40, 12.0, [], []]]}}
    out_lots, sites, lost = leave_out_deep(deep)
    assert out_lots == [(0, 0, 0, 16, 24), (30000, 0, 0, 32, 32)] and sites == 1 and lost == {"Houses": 1, "Towers": 1}, (out_lots, lost)
    assert deep["Houses"]["count"] == 2 and [p[0] for p in deep["Towers"]["points"]] == [20000], "the tower on a 13.5 m podium stands"
    out_lots = out_lots[:1]
    assert len(deep["Parks"]["points"]) == 1
    deep.update(woods())
    assert plant_lots(deep, out_lots, lambda x, y: 50.0) > 5 and deep["Trees"]["count"] == len(deep["Trees"]["points"])
    assert all(abs(t[0]) <= 1200 and abs(t[1]) <= 800 and t[2] == 50 for t in deep["Trees"]["points"]), "on its lot, on the ground"
    assert plant_lots(deep, out_lots, lambda x, y: -50.0, sea=0.0) == 0, "no tree in the water"
    assert plant_lots(deep, out_lots, lambda x, y: 50.0, keep=lambda x, y, k: False) == 0, "nor where its biome grows none"
    from .nature import grow as _grow
    meadow = {"Trees": {**woods()["Trees"], "grow": {"cover": 0.0}}}
    assert not _grow(meadow, lambda x, y: True, (0, 0, 20000, 20000), lambda x, y: 0.0, "m"), "a meadow: the forest's cover"
    assert _grow(woods(), lambda x, y: True, (0, 0, 4000, 4000), lambda x, y: 0.0, "k") == \
        _grow(woods(), lambda x, y: True, (0, 0, 4000, 4000), lambda x, y: 0.0, "k"), "the same every run"
    # on the hill as it is, before any pad: a warehouse needs flat land, a house takes a moderate slope, nothing
    # ordinary goes on a mountain face, a car park on a slope goes with its stalls; flat ground keeps everything
    from .lots import grade_under, leave_out_steep
    hill = lambda x, y: 0.12 * x  # noqa: E731  (12 %)
    assert abs(grade_under(hill, 0, 0, 30.0, 20, 30)[0] - 0.12) < 1e-9, "the plane's grade, whatever the turn"
    def town():
        return {"Warehouses": {"kind": "buildings", "role": "warehouse", "count": 1, "shown": 1,
                               "points": [[0, 0, 0, 0, 1, 9, 0, 40, 30, 0, [], []]]},
                "Houses": {"kind": "buildings", "role": "house", "count": 1, "shown": 1,
                           "points": [[5000, 0, 0, 0, 1, 7, 0, 12, 12, 0, [], []]]},
                "Car parks": {"kind": "site", "role": "site", "box_m": [1, 1, 0.1], "count": 1, "shown": 1,
                              "points": [[9000, 0, 0, 0, 30, 20, 1]]},
                "Stalls": {"kind": "site", "role": "site", "mesh": "SM_Kit_ParkingStall", "box_m": [2.5, 5, 0.1],
                           "count": 2, "shown": 2, "points": [[9000, 500, 0, 0, 1, 1, 1], [40000, 0, 0, 0, 1, 1, 1]]}}
    flat_t = town()
    assert leave_out_steep(flat_t, lambda x, y: 0.0) == ([], {}), "flat land keeps everything"
    hill_t = town()
    gone, lost = leave_out_steep(hill_t, hill)
    assert lost == {"Warehouses": 1, "Car parks": 1, "Stalls": 1} and len(gone) == 2, lost
    assert hill_t["Houses"]["count"] == 1 and [p[0] for p in hill_t["Stalls"]["points"]] == [40000], "a house takes 12 %"
    face_t = town()
    leave_out_steep(face_t, lambda x, y: 0.4 * x)
    assert not any(face_t[k]["points"] for k in ("Warehouses", "Houses", "Car parks")), "a mountain face stays natural"
    flats = {**town(), "Apartments": {"kind": "buildings", "role": "apartment", "count": 1, "shown": 1,
                                      "points": [[20000, 0, 0, 0, 1, 20, 0, 22, 18, 0, [], []]]}}
    gone, lost = leave_out_steep(flats, lambda x, y: 0.2 * x)
    assert not flats["Apartments"]["points"] and lost["Apartments"] == 1, "too steep for apartments"
    assert any(p[0] == 20000 and p[7] <= 13.2 for p in flats["Houses"]["points"]), "... a house on its lot instead"
    (drive,) = paths
    assert drive[0] == DRIVE_M and len(drive[1]) == 6 and drive[1][2] == round(pad) and drive[1][-1] < pad - 100, paths
    # a lot never reaches onto a street
    rh = _Hash()
    rh.add(0.0, 1500.0, 875.0, (0.0, 1500.0, 875.0))
    assert _lot_hits_road(rh, 0, 0, 90.0, 600, 800) and not _lot_hits_road(rh, 0, -1500, 90.0, 600, 800)
    from .. import citygen as cg
    ter = {"min": [-2000, -2000], "max": [2000, 2000], "grid": 40,
           "heights": [[-0.3 * (-2000 + 4000 * (i + 0.5) / 40) for i in range(40)] for _ in range(40)]}
    g = cg.graded(ter, pads({"H": {"kind": "buildings", "points": [[0, 0, 0, 0, 1, 8, 0, 12, 12, 0, []]]}}))
    assert abs(g(300, 300)) < 1 and abs(g(-300, 0)) < 1, "the lot is level"
    assert abs(g(1500, 0) - (-450)) < 30, "the hill away from the lot is untouched"
    bad = clean({"name": "x", "kind": "nope", "density": -5, "scale_min": 3, "scale_max": 1})
    assert bad["kind"] == "scatter" and bad["density"] == 0.01 and bad["scale_min"] == 1 and bad["scale_max"] == 3
    # an area-limited layer places only inside its outline
    boxed = clean({"name": "Box trees", "kind": "scatter", "density": 5, "area": [[0, 0], [5000, 0], [5000, 5000], [0, 5000]]})
    bp = preview([boxed], [], {"min": [-20000, -20000], "max": [20000, 20000]}, [], lambda x, y: 0.0)["Box trees"]["points"]
    assert bp and all(0 <= p[0] <= 5000 and 0 <= p[1] <= 5000 for p in bp), len(bp)
    # parcels: houses fill the lots cut from a real grid city's blocks, one per lot, facing their street
    from .. import citygen as cg
    town = {"name": "L", "x": 0, "y": 0, "radius": 40000, "seed": 4, "style": "grid"}
    town["districts"] = cg.districts(town)
    town["lots"] = cg.city_lots(town, {"planner": "no"})  # every lot plain: this checks the house fill
    assert len(town["lots"]) > 120, len(town["lots"])  # nine blocks; which neighbourhood each gets moves the count
    houses = clean({"name": "Houses", "kind": "buildings", "role": "house", "where": "cities", "width_m": 12,
                    "depth_m": 12, "setback_m": 6, "spacing_m": 20})
    hv = preview([houses], [town], {"min": [-50000, -50000], "max": [50000, 50000]}, [], lambda x, y: 0.0)["Houses"]
    assert hv["count"] > 120, hv["count"]  # (a house that would stand on its neighbour or a street is left out)
    by_front = {}
    for lot_row in town["lots"]:
        lx, ly, lyaw, lw, ld, frx, fry = lot_row[:7]
        by_front[(round(lx), round(ly))] = (lyaw, frx, fry)
    for row in hv["points"]:
        f = math.radians(row[3])  # the house's front faces +X: one setback + half its depth behind a lot's front
        front = (row[0] + math.cos(f) * (600 + row[8] * 50), row[1] + math.sin(f) * (600 + row[8] * 50))
        assert any(math.dist(front, (frx, fry)) < 30 and abs((row[3] - lyaw + 180) % 360 - 180) < 1
                   for lyaw, frx, fry in by_front.values()), row[:4]
    # no lot is left with nothing. Downtown lots are cut for what is built downtown and the big buildings take
    # them (they only knew road frontage: half the downtown lots stood empty between the towers); a lot next to
    # a big building is still built on; and a lot nothing fits is planted.
    core_town = {"name": "D", "x": 0, "y": 0, "radius": 60000, "seed": 4, "style": "grid"}
    core_town["districts"] = cg.districts(core_town)
    lay = cg.city_layout(core_town, {"planner": "no", "freeways": "no"})
    core_town.update(lots=lay["lots"], blocks=lay["blocks"])
    in_core = _where_test("downtown", [core_town])
    core_lots = [r for r in core_town["lots"] if in_core(r[0], r[1])]
    wide = [r for r in core_lots if r[3] >= 2200 and r[4] >= 2400]
    assert len(wide) > 30 and len(wide) > 0.6 * len(core_lots), (len(wide), len(core_lots))
    office = clean({"name": "Office blocks", "kind": "buildings", "role": "office", "where": "downtown", "width_m": 26,
                    "depth_m": 24, "setback_m": 5, "spacing_m": 34, "height_min_m": 30, "height_max_m": 75, "order": 1})
    homes = clean({"name": "Houses", "kind": "buildings", "role": "house", "where": "cities", "width_m": 11,
                   "depth_m": 14, "setback_m": 10, "spacing_m": 17, "order": 2})
    fill = preview([office, homes], [core_town], {"min": [-80000, -80000], "max": [80000, 80000]}, [], lambda x, y: 0.0)

    def stands(r, rows):  # is one of these on this lot?
        fx_, fy_ = math.cos(math.radians(r[2])), math.sin(math.radians(r[2]))
        return any(abs((p[0] - r[0]) * fx_ + (p[1] - r[1]) * fy_) <= r[4] / 2 + 1 and abs(-(p[0] - r[0]) * fy_ + (p[1] - r[1]) * fx_) <= r[3] / 2 + 1
                   for p in rows if abs(p[0] - r[0]) < 6000 and abs(p[1] - r[1]) < 6000)
    towers = fill["Office blocks"]["points"]
    assert sum(1 for r in wide if stands(r, towers)) > 0.85 * len(wide), (sum(1 for r in wide if stands(r, towers)), len(wide))
    assert all(in_core(p[0], p[1]) for p in towers), "office blocks stay downtown"
    assert len(fill["Houses"]["points"]) > 300 and set(fill) == {"Office blocks", "Houses"}, \
        "homes outside downtown; no tree of a rule of its own on the lots nothing fits (no forest layer here)"
    # ... but what the planner sites (a mall, a school) never takes a free lot: with none planned it stays a rare
    # building along a road (18 malls stood on downtown lots)
    mall = clean({"name": "Malls", "kind": "buildings", "role": "mall", "where": "downtown", "width_m": 30, "depth_m": 20,
                  "setback_m": 5, "spacing_m": 100, "order": 1})
    box = {"min": [-80000, -80000], "max": [80000, 80000]}
    assert not preview([mall], [core_town], box, [], lambda x, y: 0.0)["Malls"]["points"]
    # ... and where the planner ran and sited none, there is none: not along the roads either
    ring_road = [(x, 9000.0, 300.0, 1.0, 0.0) for x in range(-20000, 20001, 500)]
    assert preview([mall], [core_town], box, ring_road, lambda x, y: 0.0)["Malls"]["points"], "(no planner: it takes road frontage)"
    assert not preview([mall], [{**core_town, "plan": {"facilities": []}}], box, ring_road, lambda x, y: 0.0)["Malls"]["points"]
    # a dropped kerb at the street end of a house's drive, half the sidewalk past the lot line
    ap = aprons({"H": {"kind": "buildings", "points": [[0, 0, 0, 0, 1, 8, 0, 12, 12, 0, [[DRIVE_M, [600, 0, 0, 1600, 0, 0]]]]]}})
    assert ap["Driveway aprons"]["points"] == [[1775, 0, 0, 0.0, 3.5, DRIVE_M + 1.0, 1.0]], ap
    # two houses end on, 4 m apart at their centres' line: the old circle test passed them, the rectangles do not
    east = (0.0, 0.0, 1.0, 0.0, 600.0, 900.0)
    assert _rects_hit(east, (1700.0, 0.0, 1.0, 0.0, 600.0, 900.0)) and not _rects_hit(east, (1900.0, 0.0, 1.0, 0.0, 600.0, 900.0))
    assert not _rects_hit(east, (0.0, 1300.0, 1.0, 0.0, 600.0, 900.0)) and _rects_hit(east, (800.0, 1000.0, 0.7071, 0.7071, 600.0, 900.0))
    # fences: one behind a house stays; one through the house, one with no house, one across a street and one
    # crossing a kept fence all go
    fl = {"H": {"kind": "buildings", "points": [[0, 0, 0, 0, 1, 8, 0, 12, 12, 0, []]]},
          "Yard fences": {"kind": "site", "points": [[-1200, 0, 0, 90, 16, 1, 0.6], [0, 0, 0, 90, 16, 1, 0.6], [90000, 0, 0, 90, 16, 1, 0.6],
                                                     [1500, 0, 0, 0, 16, 1, 0.6], [-1200, 0, 0, 0, 10, 1, 0.6]]}}
    assert fit_fences(fl, [(1500.0, 0.0, 400.0, 0.0, 1.0)]) == 4 and fl["Yard fences"]["points"] == [[-1200, 0, 0, 90, 16, 1, 0.6]]
    # trees fill an interchange (four ramp ends round a point), none for a lone ramp end
    ramp_end = lambda x, y: {"x": x, "y": y, "mesh": "SM_Road_RampFlare15m"}
    ramps = [([ramp_end(0, 0), ramp_end(9000, 0), ramp_end(0, 9000), ramp_end(9000, 9000), ramp_end(90000, 0)], [])]
    grove = woods()
    assert interchange_groves(grove, ramps, lambda x, y: 0.0) > 90
    assert all(math.dist(p[:2], (4500, 4500)) <= 16000 for p in grove["Trees"]["points"]), "round the interchange only"
    assert interchange_groves(woods(), ramps, lambda x, y: 0.0, blocked=lambda x, y: True) == 0, "never on a road"
    # a private street stops each side of a ramp it would cross (ramp half width 500 at x 5000)
    cut = off_roads([[[0, 0], [10000, 0]]], [(5000, 0, 500, 0, 1)])
    assert len(cut) == 2 and cut[0][-1][0] <= 3100 and cut[1][0][0] >= 6900 and cut[1][-1] == [10000, 0], cut
    assert off_roads([[[0, 9000], [10000, 9000]]], [(5000, 0, 500, 0, 1)]) == [[[0, 9000], [10000, 9000]]]
    # nothing stands on a freeway or a ramp: a tower on it goes, its neighbour 60 m off stays, bridge parts stay
    on = {"T": {"kind": "buildings", "role": "office", "count": 2, "points": [[0, 500, 0, 0, 1, 40, 0, 30.0, 30.0], [0, 6000, 0, 0, 1, 40, 0, 30.0, 30.0]]},
          "Bridge piers": {"kind": "site", "role": "bridge", "box_m": [2, 2, 1], "points": [[0, 0, 0, 0, 1, 1, 5]]}}
    assert clear_of(on, [(x, 0.0, 650.0, 1.0, 0.0) for x in range(-2000, 2001, 400)]) == 1
    assert [p[1] for p in on["T"]["points"]] == [6000] and on["T"]["count"] == 1 and len(on["Bridge piers"]["points"]) == 1
    # a container stack on a street goes (site pieces are otherwise never swept); one in its yard 20 m off stays
    stacks = {"Container stacks": {"kind": "site", "role": "site", "count": 2, "shown": 2,
                                   "points": [[0, 300, 0, 0.0, 1, 1, 1], [0, 2500, 0, 0.0, 1, 1, 1]]}}
    assert clear_roads(stacks, [(x, 0.0, 500.0) for x in range(-2000, 2001, 300)]) == {"Container stacks": 1}
    assert [p[1] for p in stacks["Container stacks"]["points"]] == [2500]
    # no building on a container yard, wherever it came from; one 50 m beyond the yard's edge stays
    port = {"Port yards": {"kind": "site", "points": [[0, 0, 0, 145.0, 144.0, 80.0, 1.0]]},
            "Warehouses": {"kind": "buildings", "count": 2, "shown": 2,
                           "points": [[322, 476, 0, -150, 1, 8, 0, 24.0, 12.0], [-13000, -9000, 0, 0, 1, 8, 0, 24.0, 12.0]]}}
    assert clear_yards(port) == {"Warehouses": 1} and port["Warehouses"]["points"][0][0] == -13000
    # a yard a street crosses goes whole, with its stacks; a yard no street touches, and its stacks, stay
    road = [(0.0, y, 500.0) for y in range(-6000, 6001, 300)]
    yard = {"Port yards": {"kind": "site", "points": [[0, 0, 0, 0.0, 200.0, 80.0, 1.0], [30000, 0, 0, 0.0, 50.0, 40.0, 1.0]]},
            "Container stacks": {"kind": "site", "points": [[a * 1400, b * 1000, 0, 0.0, 1, 1, 1]
                                                            for a in range(-6, 7) for b in (-2, 0, 2)] + [[30000, 0, 0, 0.0, 1, 1, 1]]}}
    assert fit_yards(yard, road) == {"removed": 1}
    assert [p[0] for p in yard["Port yards"]["points"]] == [30000]
    assert [s[0] for s in yard["Container stacks"]["points"]] == [30000]
    assert fit_yards(yard, road) == {}
    # the person's edits: a delete takes the nearest piece of its layer, a move puts one on the ground where it
    # goes; an edit whose piece is gone is handed back
    town = {"Houses": {"kind": "buildings", "count": 2, "shown": 2, "points": [[0, 0, 0, 0.0, 1], [5000, 0, 0, 0.0, 1]]},
            "Port yards": {"kind": "site", "keep_z": True, "points": [[9000, 0, -36, 0.0, 50.0, 40.0, 1.0]]}}
    gone = {"op": "delete", "layer": "", "x": 9000, "y": 9000}
    assert apply_edits(town, [{"op": "delete", "layer": "Houses", "x": 120, "y": -80},
                              {"op": "move", "layer": "Houses", "x": 5000, "y": 0, "to": [7000, 100], "yaw": 90},
                              {"op": "move", "layer": "", "x": 9000, "y": 0, "to": [9500, 0]}, gone],
                       lambda x, y: 250.0) == [gone]
    assert town["Houses"]["points"] == [[7000, 100, 250, 90.0, 1]] and town["Houses"]["count"] == 1
    assert town["Port yards"]["points"][0][:3] == [9500, 0, -36], "a yard keeps its own level"
    # a copy keeps the original; every=true clears all of a layer within reach, nothing else
    assert apply_edits(town, [{"op": "copy", "layer": "Houses", "x": 0, "y": 0, "reach": 8000, "to": [100, 100]}],
                       lambda x, y: 0.0) == []
    assert [p[:2] for p in town["Houses"]["points"]] == [[7000, 100], [100, 100]] and town["Houses"]["count"] == 2
    assert apply_edits(town, [{"op": "delete", "layer": "Houses", "every": True, "x": 0, "y": 0, "reach": 8000}]) == []
    assert town["Houses"]["points"] == [] and len(town["Port yards"]["points"]) == 1
    # a field gets the biggest real pitch that fits, at scale 1, turned to fit; too small for 5-a-side: none
    assert fit_pitch(110, 75) == ("pitch11", 0) and fit_pitch(46, 70) == ("pitch7", 90) and fit_pitch(30, 20) is None
    sport = site_layers([{"private": {"grounds": [[0, 0, 0.0, 70.0, 50.0, "field", "school"],
                                                  [9000, 0, 30.0, 20.0, 14.0, "playground", "school"]]}}], lambda x, y: 0.0)
    assert sport["Football pitches (7-a-side)"]["points"] == [[0, 0, 0, 0.0, 1.0, 1.0, 1.0]] and "Sports fields" not in sport
    kit = [(n, p) for n, v in sport.items() if n.startswith("Playground ") for p in v["points"]]
    assert len(kit) >= 4 and all(p[4:7] == [1.0, 1.0, 1.0] for _n, p in kit), "equipment at scale 1"
    assert sport["Playgrounds"]["points"][0][4:6] == [20.0, 14.0], "the soft surface still covers the whole playground"
    for _n, p in kit:  # every piece inside the playground, clear of its edge
        f = math.radians(30.0)
        u, v = (p[0] - 9000) * math.cos(f) + p[1] * math.sin(f), -(p[0] - 9000) * math.sin(f) + p[1] * math.cos(f)
        assert abs(u) < 1000 and abs(v) < 700, (_n, u, v)
    print("pcg layers ok", len(full), "layers,", len(out["nodes"]), "nodes", len(out["edges"]), "wires,",
          {n: v["count"] for n, v in pv.items() if v["count"]})
if __name__ == "__main__":
    _demo()

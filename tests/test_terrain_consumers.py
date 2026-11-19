import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import citygen as cg  # noqa: E402

BOUNDS = ((-300000.0, -300000.0), (300000.0, 300000.0))


import pytest  # noqa: E402


@pytest.mark.parametrize("half_cm", [300000.0, 1000000.0])  # 6 km and 20 km maps
def test_cities_on_dry_buildable_ground(half_cm):
    bounds = ((-half_cm, -half_cm), (half_cm, half_cm))
    opts = {"preset": "Coastal"}
    towns = cg.place_cities(7, 3, bounds, None, 1.0, 50000.0, opts=opts)
    assert len(towns) == 3
    _, world = cg.build_sites(7, bounds, {**cg.options(opts), "relief_m": 60.0}, 60000.0, 1)
    g, m = world.grid, world.maps
    water = lambda k: (m.get("land") is not None and m["land"][k] < 0.5) or any(
        m.get(w) is not None and m[w][k] > 0.5 for w in ("sea", "lake")) or (m.get("river") is not None and m["river"][k] > 0)
    for t in towns:
        assert t.get("site") != "none", t
        i = min(g.n - 1, max(0, int((t["x"] / 100.0 - g.x0) / g.cell)))
        j = min(g.n - 1, max(0, int((t["y"] / 100.0 - g.y0) / g.cell)))
        assert m["slope"][g.idx(i, j)] <= 0.05, (t, m["slope"][g.idx(i, j)])
        r = t["radius"] * 0.5 / 100.0  # the 0.5 x radius disc, in metres
        wet = [(a, b) for b in range(g.n) for a in range(g.n)
               if (g.xs[a] - t["x"] / 100.0) ** 2 + (g.ys[b] - t["y"] / 100.0) ** 2 <= r * r and water(g.idx(a, b))]
        assert not wet, (t, wet[:3])
    for a in towns:  # still apart (a fallback city may stand closer)
        for b in towns:
            if a is not b:
                gap = 0 if (a.get("site") or b.get("site")) else 49000
                assert ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5 > a["radius"] + b["radius"] + gap
    # and the world they are built in has no lake or river at a city centre either
    ter = cg.terrain(7, 2 * half_cm, 6000.0, towns, opts=opts)
    n, mp = ter["grid"], ter["maps"]
    for t in towns:
        i, j = int((t["x"] + half_cm) / (2 * half_cm) * n), int((t["y"] + half_cm) / (2 * half_cm) * n)
        k = j * n + i
        assert mp["lake"][k] <= 0.5 and mp["river"][k] <= 0 and (ter["sea"] is None or ter["heights"][j][i] >= ter["sea"]), t


def _ridge_world(n=128, size_m=8000.0):
    """A 500 m range along x = 0 (2 km wide) with one 100 m pass at y = 2500 m, as an engine terrain() in cm."""
    import math
    from backend.terrain.grid import Grid
    from backend.terrain import suitability
    g = Grid(n, -size_m / 2, -size_m / 2, size_m)
    hm = lambda x, y: 500.0 * math.exp(-(x / 2000.0) ** 2) * (1 - 0.8 * math.exp(-((y - 2500.0) / 1500.0) ** 2))
    maps = {"height": g.new()}
    for j, y in enumerate(g.ys):
        for i, x in enumerate(g.xs):
            maps["height"][j * n + i] = hm(x, y)
    suitability.run(g, {}, None, maps)
    ter = {"min": [g.x0 * 100, g.y0 * 100], "max": [(g.x0 + size_m) * 100, (g.y0 + size_m) * 100], "grid": n,
           "heights": [[maps["height"][j * n + i] * 100 for i in range(n)] for j in range(n)],
           "maps": {"road_cost": list(maps["road_cost"])}}
    return ter, hm


def _top_m(ter, line):
    top = -1e9
    for p, q in zip(line, line[1:]):
        for t in range(41):
            top = max(top, cg.terrain_z(ter, p[0] + (q[0] - p[0]) * t / 40, p[1] + (q[1] - p[1]) * t / 40) / 100)
    return top


def _saddle_m(ter, p, q):
    """The lowest crest any way from p to q must cross (minimax over the terrain's cells), metres."""
    import heapq
    n, rows, (mx, my), (Mx, My) = ter["grid"], ter["heights"], ter["min"], ter["max"]
    cell = lambda x, y: (min(n - 1, int((x - mx) / (Mx - mx) * n)), min(n - 1, int((y - my) / (My - my) * n)))
    (si, sj), goal = cell(*p), cell(*q)
    best, heap = {(si, sj): rows[sj][si]}, [(rows[sj][si], si, sj)]
    while heap:
        top, i, j = heapq.heappop(heap)
        if (i, j) == goal:
            return top / 100
        for a in (i - 1, i, i + 1):
            for b in (j - 1, j, j + 1):
                if 0 <= a < n and 0 <= b < n:
                    t = max(top, rows[b][a])
                    if t < best.get((a, b), 1e30):
                        best[(a, b)] = t
                        heapq.heappush(heap, (t, a, b))


def test_highway_crosses_at_the_pass():
    ter, hm = _ridge_world()
    a, b = [-350000.0, -150000.0], [350000.0, -150000.0]  # 4 km from the pass: road_cost alone went over the crest
    line = [a] + cg.cost_route(ter, a, b) + [b]
    assert _top_m(ter, line) <= 100.0 + 50.0, _top_m(ter, line)  # the pass is 100 m; over the crest 500 m
    assert all(((u[0] - v[0]) ** 2 + (u[1] - v[1]) ** 2) ** 0.5 <= 20001 for u, v in zip(line, line[1:]))
    wall = {**ter, "heights": [[r * 20 for r in row] for row in ter["heights"]]}  # 2 km at its pass: too steep
    assert cg.cost_route(wall, a, b) is None


def test_highways_on_20km_coastal_take_the_passes():
    half = 1000000.0
    opts = {"preset": "Coastal"}
    towns = cg.place_cities(7, 3, ((-half, -half), (half, half)), None, 1.0, 50000.0, opts=opts)
    ter = cg.terrain(7, 2 * half, 6000.0, towns, opts=opts)
    for i, j in cg.links(towns):
        p, q = [towns[i]["x"], towns[i]["y"]], [towns[j]["x"], towns[j]["y"]]
        via = cg.cost_route(ter, p, q)
        assert via is not None, (i, j)
        top, saddle = _top_m(ter, [p] + via + [q]), _saddle_m(ter, p, q)
        assert top <= saddle + 50 or top < 300, (i, j, top, saddle)


def test_trees_follow_biomes():
    import random
    from backend.pcg_layers.land import biome_trees
    from backend.terrain.climate import BIOMES
    ter = cg.terrain(7, 2000000.0, 6000.0, [], grid=64, opts={"preset": "Alpine", "mountain_m": 2400, "mountain_cover": 0.5})
    keep = biome_trees(ter)
    n, b = ter["grid"], ter["maps"]["biome"]
    (mx, my), (Mx, My) = ter["min"], ter["max"]
    rnd = random.Random(1)
    seen, kept = {}, {}
    for _ in range(40000):
        x, y = rnd.uniform(mx, Mx), rnd.uniform(my, My)
        k = min(n - 1, int((y - my) / (My - my) * n)) * n + min(n - 1, int((x - mx) / (Mx - mx) * n))
        name = BIOMES[int(b[k])]
        seen[name] = seen.get(name, 0) + 1
        if keep(x, y, "conifer"):
            kept[name] = kept.get(name, 0) + 1
            assert name not in ("snow", "cliff", "ocean"), name
    assert seen.get("snow", 0) + seen.get("cliff", 0) > 0, seen  # the test met snow / cliff ground
    share = lambda names: sum(kept.get(s, 0) for s in names) / max(1, sum(seen.get(s, 0) for s in names))
    forest = share(("temperate_forest", "conifer_forest", "rainforest"))
    assert forest > 3 * share(("meadow",)) or not seen.get("meadow"), (forest, kept, seen)
    assert forest > 0.5  # (rivers and lakes through the forest take the rest)
    # palms only where it is warm or sandy; broadleaf trees not above the conifer line
    for _ in range(4000):
        x, y = rnd.uniform(mx, Mx), rnd.uniform(my, My)
        i, j = min(n - 1, int((x - mx) / (Mx - mx) * n)), min(n - 1, int((y - my) / (My - my) * n))
        if keep(x, y, "palm"):
            assert BIOMES[int(b[j * n + i])] in ("beach", "rainforest", "savanna")
        if keep(x, y, "tree"):
            assert ter["heights"][j][i] <= 90000
    assert biome_trees({"heights": []}) is None


def test_plants_keep_off_the_shore():
    """A 200 m strip: sea west of x = 100 m, sand (beach) from 100 to 110 m, forest east of it. Trees keep 25-30 m
    from the water and never stand on the sand; palms grow on the sand; bushes come closer than trees."""
    from backend.pcg_layers.land import biome_trees
    from backend.terrain.climate import B
    n = 100  # 2 m cells
    heights = [[(-500.0 if i < 50 else 150.0 + 20.0 * (i - 50)) for i in range(n)] for _ in range(n)]
    biome = [float(B["ocean"] if i < 50 else B["beach"] if i < 55 else B["temperate_forest"])
             for _ in range(n) for i in range(n)]
    keep = biome_trees({"grid": n, "heights": heights, "sea": 0.0, "min": [0, 0], "max": [20000, 20000],
                        "maps": {"biome": biome}})
    at = lambda m: (m * 100.0, 10000.0)
    assert not keep(*at(50), "tree") and not keep(*at(108), "tree"), "no tree under water or on the sand"
    assert not keep(*at(120), "broadleaf") and keep(*at(130), "broadleaf"), "a tree stands 25 m back from the water"
    assert not keep(*at(125), "conifer") and keep(*at(135), "conifer")
    assert keep(*at(108), "palm") and not keep(*at(102), "palm"), "a palm on dry sand, not at the waterline"
    assert any(keep(11200.0, y * 100.0, "bush") for y in range(0, 200, 4)), "scrub 12 m from the water, where no tree is"
    # thin biomes grow groves: kept spots cluster (neighbours 10 m apart agree far more often than chance)
    meadow = [float(B["meadow"])] * (n * n)
    flat = [[1000.0] * n for _ in range(n)]
    big = biome_trees({"grid": n, "heights": flat, "sea": 0.0, "min": [0, 0], "max": [400000, 400000],
                       "maps": {"biome": meadow}})
    row = [big(x * 1000.0, 200000.0) for x in range(400)]
    share = sum(row) / len(row)
    same = sum(a == b for a, b in zip(row, row[1:])) / (len(row) - 1)
    assert 0.02 < share < 0.4 and same > 1 - 2 * share * (1 - share) + 0.05, (share, same)
    # ... and a meadow keeps about its TREE_DENSITY share of the candidates (0.3), not a few %
    many = [big(x * 1000.0, y * 1000.0) for x in range(0, 400, 4) for y in range(0, 400, 4)]
    assert 0.2 < sum(many) / len(many) < 0.4, sum(many) / len(many)
    # the Shore gap slider moves the line: at 50 m a broadleaf 30 m from the water goes, at 0 m only the sand stops it
    wide = biome_trees({"grid": n, "heights": heights, "sea": 0.0, "min": [0, 0], "max": [20000, 20000],
                        "maps": {"biome": biome}, "veg": {"tree_shore_m": 50}})
    assert not wide(*at(130), "broadleaf") and wide(*at(155), "broadleaf")
    none = biome_trees({"grid": n, "heights": heights, "sea": 0.0, "min": [0, 0], "max": [20000, 20000],
                        "maps": {"biome": biome}, "veg": {"tree_shore_m": 0}})
    assert none(*at(116), "broadleaf") and not none(*at(108), "broadleaf")


def test_vegetation_sliders_scale_the_wild_layers():
    from backend.pcg_layers import NATURE, with_veg, patch_rank
    import random
    out = {l["name"]: l for l in with_veg([dict(l) for l in NATURE] + [{"name": "Park trees", "kind": "scatter", "role": "tree",
                                                                       "where": "cities", "density": 2.0}],
                                          {"tree_density": 2.0, "bush_density": 0.0, "forest_cover": 0.8})}
    assert out["Trees"]["density"] == 16.0 and out["Trees"]["cover"] == 0.8
    assert out["Bushes"]["enabled"] is False and out["Rocks"]["density"] == 0.1
    assert out["Park trees"]["density"] == 2.0, "a city's own trees keep their layer's settings"
    assert with_veg(NATURE, {}) is NATURE
    r = random.Random(3)
    ranks = [patch_rank(r.uniform(-1e7, 1e7), r.uniform(-1e7, 1e7)) for _ in range(4000)]
    assert abs(sum(v < 0.3 for v in ranks) / len(ranks) - 0.3) < 0.04, "cover 0.3 is 30 % of the land"


def _ground(ter):
    n, (mx, my), (Mx, My) = ter["grid"], ter["min"], ter["max"]
    cell = lambda x, y: (min(n - 1, max(0, int((x - mx) / (Mx - mx) * n))), min(n - 1, max(0, int((y - my) / (My - my) * n))))
    return cell, lambda x, y: ter["heights"][cell(x, y)[1]][cell(x, y)[0]]


def test_preview_trees_follow_biomes():
    from backend.pcg_layers.layers import NATURE, clean
    from backend.pcg_layers.preview import preview
    from backend.terrain.climate import BIOMES
    trees = clean(dict(NATURE[0]))
    ter = cg.terrain(7, 600000.0, 3000.0, [], grid=48, opts={"preset": "Alpine", "mountain_m": 2400, "mountain_cover": 0.8})
    cell, z = _ground(ter)
    pts = preview([trees], [], ter, [], z, foliage_limit=20000)["Trees"]["points"]
    b = ter["maps"]["biome"]
    names = [BIOMES[int(b[cell(p[0], p[1])[1] * ter["grid"] + cell(p[0], p[1])[0]])] for p in pts]
    assert pts and not [s for s in names if s in ("snow", "cliff", "ocean")]
    assert any(BIOMES[int(v)] in ("snow", "cliff") for v in b)  # the world has snow / cliff to avoid
    # Classic: no biome map, the same trees as before
    classic = cg.terrain(7, 600000.0, 3000.0, [], grid=48)
    assert "maps" not in classic or not (classic.get("maps") or {}).get("biome")
    _, cz = _ground(classic)
    a = preview([trees], [], classic, [], cz, foliage_limit=20000)["Trees"]
    bare = preview([trees], [], {k: v for k, v in classic.items() if k != "maps"}, [], cz, foliage_limit=20000)["Trees"]
    assert a["points"] == bare["points"] and a["count"] == bare["count"]


def test_classic_unchanged():
    assert cg.place_cities(7, 3, BOUNDS) == cg.place_cities(7, 3, BOUNDS, opts={"preset": "Classic"})

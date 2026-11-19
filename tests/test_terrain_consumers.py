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
    """A 500 m range along x = 0 with one 100 m pass at y = 1500 m, as an engine terrain() in cm."""
    import math
    from backend.terrain.grid import Grid
    from backend.terrain import suitability
    g = Grid(n, -size_m / 2, -size_m / 2, size_m)
    hm = lambda x, y: 500.0 * math.exp(-(x / 300.0) ** 2) * (1 - 0.8 * math.exp(-((y - 1500.0) / 400.0) ** 2))
    maps = {"height": g.new()}
    for j, y in enumerate(g.ys):
        for i, x in enumerate(g.xs):
            maps["height"][j * n + i] = hm(x, y)
    suitability.run(g, {}, None, maps)
    ter = {"min": [g.x0 * 100, g.y0 * 100], "max": [(g.x0 + size_m) * 100, (g.y0 + size_m) * 100], "grid": n,
           "maps": {"road_cost": list(maps["road_cost"])}}
    return ter, hm


def test_highway_crosses_at_the_pass():
    ter, hm = _ridge_world()
    a, b = [-250000.0, 0.0], [250000.0, 0.0]
    line = [a] + cg.cost_route(ter, a, b) + [b]
    top = 0.0
    for p, q in zip(line, line[1:]):
        for t in range(41):
            x, y = p[0] + (q[0] - p[0]) * t / 40, p[1] + (q[1] - p[1]) * t / 40
            top = max(top, hm(x / 100, y / 100))
    assert top <= 100.0 + 50.0, top  # the pass is 100 m high; straight over the crest would be 500 m
    assert all(((u[0] - v[0]) ** 2 + (u[1] - v[1]) ** 2) ** 0.5 <= 20001 for u, v in zip(line, line[1:]))


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

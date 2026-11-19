import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import citygen as cg  # noqa: E402

BOUNDS = ((-300000.0, -300000.0), (300000.0, 300000.0))


def test_cities_on_buildable_ground():
    opts = {"preset": "Coastal"}
    towns = cg.place_cities(7, 3, BOUNDS, None, 1.0, 50000.0, opts=opts)
    assert len(towns) == 3
    _, world = cg.build_sites(7, BOUNDS, {**cg.options(opts), "relief_m": 60.0}, 60000.0, 1)
    g, m = world.grid, world.maps
    water = lambda k: (m.get("land") is not None and m["land"][k] < 0.5) or any(
        m.get(w) is not None and m[w][k] > 0.5 for w in ("sea", "lake")) or (m.get("river") is not None and m["river"][k] > 0)
    for t in towns:
        i = min(g.n - 1, max(0, int((t["x"] / 100.0 - g.x0) / g.cell)))
        j = min(g.n - 1, max(0, int((t["y"] / 100.0 - g.y0) / g.cell)))
        k = g.idx(i, j)
        assert m["slope"][k] <= 0.05, (t, m["slope"][k])
        assert not water(k), t
    for a in towns:  # still apart
        for b in towns:
            if a is not b:
                assert ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5 > a["radius"] + b["radius"] + 49000


def test_classic_unchanged():
    assert cg.place_cities(7, 3, BOUNDS) == cg.place_cities(7, 3, BOUNDS, opts={"preset": "Classic"})

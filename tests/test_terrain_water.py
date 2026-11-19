"""Terrain engine water stages: erosion (stage 7), rivers and lakes (stage 8)."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from backend.terrain.erosion import erode, hydraulic, thermal  # noqa: E402
from backend.terrain.grid import Grid  # noqa: E402
from backend.terrain.hydrology import hydrology  # noqa: E402
from backend.terrain.noise import Noise  # noqa: E402


def _plain_and_range(n=96, cell=20.0, seed=7):
    """Plains (x < 40 cells, ~5 m) rising over a ramp into a ridged range (x > 60 cells, up to ~900 m)."""
    g = Grid(n, 0.0, 0.0, n * cell)
    nz = Noise(seed)
    h = g.new()
    for k, x, y in g.points():
        i = k % n
        t = min(max((i - 40) / 20.0, 0.0), 1.0)
        plain = 5.0 + 0.3 * nz.fbm(x, y, 3, scale=200.0)
        mount = 100.0 + 800.0 * nz.ridged(x, y, 5, scale=600.0)
        h[k] = plain + (mount - plain) * t
    return g, nz, h


def _mean(vals):
    vals = list(vals)
    return sum(vals) / len(vals)


def test_erosion_keeps_volume_flattens_mountains_spares_plains():
    g, nz, h0 = _plain_and_range()
    maps = {'height': h0}
    erode(g, {'erosion': 0.6}, nz, maps)
    h1 = maps['height']
    v0, v1 = sum(h0), sum(h1)
    assert abs(v1 - v0) <= 0.02 * abs(v0)
    n = g.n
    mount = [k for k in range(n * n) if k % n > 62]
    s0, s1 = g.slope(h0), g.slope(h1)
    assert _mean(s1[k] for k in mount) < _mean(s0[k] for k in mount)
    plains = [k for k in range(n * n) if k % n < 8]
    assert max(abs(h1[k] - h0[k]) for k in plains) < 0.5


def test_hydraulic_alone_is_seeded_and_conserves():
    g, nz, h0 = _plain_and_range(64)
    a = hydraulic(h0, g, {'erosion': 0.5}, 3)
    assert a == hydraulic(h0, g, {'erosion': 0.5}, 3)
    assert abs(sum(a) - sum(h0)) <= 1e-6 * abs(sum(h0))
    assert a != h0


def test_thermal_only_moves_steep_ground():
    g = Grid(32, 0.0, 0.0, 320.0)
    h = g.new()
    for k, x, _ in g.points():
        h[k] = 0.3 * x  # 17 deg ramp: below the talus angle
    assert thermal(h, g, 8) == h
    spike = g.new()
    spike[g.idx(16, 16)] = 100.0
    out = thermal(spike, g, 8)
    assert out[g.idx(16, 16)] < 100.0 and abs(sum(out) - 100.0) < 1e-9


def test_erosion_budget_at_256():
    g, nz, h0 = _plain_and_range(256, 10.0)
    t = time.perf_counter()
    erode(g, {'erosion': 0.5}, nz, {'height': h0})
    assert time.perf_counter() - t <= 8.0


# ---- rivers + lakes ----

def _coastal_world(n=128, size_m=12800.0, seed=7):
    """Coastal preset, seed 7: coast + regions + ridges, a simple land ramp under the ridges, eroded."""
    from backend.terrain import coast, mountains, regions
    from backend.terrain.config import terrain_config
    cfg = terrain_config({'preset': 'Coastal', 'seed': seed})
    g, nz = Grid(n, -size_m / 2, -size_m / 2, size_m), Noise(seed)
    maps = {}
    coast.land_mask(g, cfg, nz, maps)
    regions.region_masks(g, cfg, nz, maps)
    mountains.stage(g, cfg, nz, maps)
    h = g.new()
    for k, x, y in g.points():
        if maps['sea'][k] >= 0.5:
            h[k] = -20.0
        else:
            h[k] = 2.0 + min(maps['coast_dist'][k], 20000.0) * 0.004 + 15.0 * nz.fbm(x, y, 4, scale=900.0) \
                + maps['ridge'][k]
    maps['height'] = h
    erode(g, cfg, nz, maps)
    hydrology(g, cfg, nz, maps)
    return g, cfg, maps


def test_rivers_run_downhill_to_an_outlet_without_loops():
    g, _, maps = _coastal_world()
    n, h, flow = g.n, maps['height'], maps['flow']
    river, lake, sea = maps['river'], maps['lake'], maps['sea']
    rcells = [k for k in range(n * n) if river[k] > 0]
    assert rcells
    for k in rcells:
        seen = set()
        while True:
            assert k not in seen
            seen.add(k)
            d = flow[k]
            if d < 0 or lake[d] or sea[d] >= 0.5 or (d % n) in (0, n - 1) or (d // n) in (0, n - 1):
                break  # sea, lake or the map edge
            assert river[d] > 0 and h[d] <= h[k] + 1e-6
            k = d
    assert all(o >= 1 for o in (maps['river_order'][k] for k in rcells))
    assert maps['river_lines'] and all(len(line['pts']) >= 2 for line in maps['river_lines'])


def test_coastal_seed7_has_a_mountain_river():
    g, _, maps = _coastal_world()
    n, flow, river = g.n, maps['flow'], maps['river']
    fed = {flow[k] for k in range(n * n) if river[k] > 0 and flow[k] >= 0}
    sources = [k for k in range(n * n) if river[k] > 0 and k not in fed]
    assert any(maps['mountains'][k] >= 0.5 for k in sources)


def test_deep_wide_pit_is_a_lake_small_pit_is_filled():
    g = Grid(64, 0.0, 0.0, 6400.0)  # 100 m cells
    h = g.new()
    for k, x, y in g.points():
        h[k] = 100.0 + 0.01 * x  # tilted plain draining west
        if (x - 3200) ** 2 + (y - 3200) ** 2 < 800 ** 2:
            h[k] -= 10.0  # 2 km2, 10 m deep
        if (x - 5500) ** 2 + (y - 1000) ** 2 < 150 ** 2:
            h[k] -= 5.0   # 0.07 km2
    maps = {'height': h, 'sea': g.new()}
    hydrology(g, {'lakes': 0.4, 'rivers': 0.5}, Noise(1), maps)
    assert maps['lake_count'] == 1 and maps['lake'][g.idx(32, 32)] == 1.0
    assert maps['lake'][g.idx(55, 10)] == 0.0 and maps['height'][g.idx(55, 10)] >= h[g.idx(55, 10)] + 2.5

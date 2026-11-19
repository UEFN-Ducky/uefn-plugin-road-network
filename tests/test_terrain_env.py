"""Terrain engine stages 9 + 10: climate / biomes / material masks, suitability."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from backend.terrain.climate import B, LAYERS, material_masks, run as climate  # noqa: E402
from backend.terrain.config import terrain_config  # noqa: E402
from backend.terrain.grid import Grid  # noqa: E402
from backend.terrain.noise import Noise  # noqa: E402


def _world(n=48, size=24000.0):
    """Sea on the east (+x) edge, a flat 2 km plateau in the north-west, a steep wall south of it, plains elsewhere."""
    g = Grid(n, 0.0, 0.0, size)
    h, land = g.new(), g.new(1.0)
    for k, x, y in g.points():
        i, j = k % n, k // n
        if i >= n - 4:
            h[k], land[k] = -20.0, 0.0
        elif i < 12 and j >= n - 12:
            h[k] = 2200.0                      # plateau above the snowline
        elif i < 12 and j == n - 13:
            h[k] = 2200.0 - 0.9 * g.cell        # one-cell 90 % step down: a cliff band
        else:
            h[k] = 50.0 + 0.002 * x
    sea = [1.0 - v for v in land]
    return g, {'height': h, 'land': land, 'sea': sea}


def test_snowline_cliff_and_masks():
    cfg = terrain_config({'preset': 'Coastal', 'seed': 7})
    g, maps = _world()
    climate(g, cfg, Noise(7), maps)
    n = g.n
    for k in range(n * n):
        if maps['land'][k] < 0.5:
            assert maps['biome'][k] == B['ocean']
        elif maps['slope'][k] > 0.8:
            assert maps['biome'][k] == B['cliff']
        elif maps['height'][k] >= cfg['snowline_m']:
            assert maps['biome'][k] == B['snow']
    assert maps['biome'][g.idx(2, n - 2)] == B['snow']
    assert any(b == B['cliff'] for b in maps['biome'])
    # it gets colder uphill, moisture stays 0..1
    assert maps['temp'][g.idx(2, n - 2)] < maps['temp'][g.idx(30, 5)] - 10
    assert all(0.0 <= m <= 1.0 for m in maps['moist'])
    masks = material_masks(maps)
    assert set(masks) == set(LAYERS)
    for k in range(n * n):
        ws = [masks[name][k] for name in LAYERS]
        assert all(0.0 <= w <= 1.0 for w in ws) and abs(sum(ws) - 1.0) < 1e-9
    assert masks['snow'][g.idx(2, n - 2)] > 0.5 and masks['sand'][g.idx(n - 1, 5)] == 1.0


def test_rain_shadow_and_dry_preset():
    # a long north-south ridge; wind from the sea (east): the west side is drier than the east side
    g = Grid(48, 0.0, 0.0, 24000.0)
    h = g.new()
    for k, x, y in g.points():
        h[k] = max(0.0, 1500.0 - abs(x - 12000.0) * 0.6) + 20.0
    maps = {'height': h, 'land': g.new(1.0)}
    cfg = terrain_config({'preset': 'Coastal', 'seed': 3})
    climate(g, cfg, Noise(3), maps)
    j = 24
    east = sum(maps['moist'][g.idx(i, j)] for i in range(34, 40)) / 6
    west = sum(maps['moist'][g.idx(i, j)] for i in range(8, 14)) / 6
    assert east > west + 0.2
    flat = {'height': g.new(100.0), 'land': g.new(1.0)}
    climate(g, terrain_config({'preset': 'Desert'}), Noise(1), flat)
    assert sum(b == B['desert'] for b in flat['biome']) > 0.8 * g.n * g.n

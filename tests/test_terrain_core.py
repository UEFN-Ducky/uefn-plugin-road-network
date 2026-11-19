"""Terrain engine core: grid maths, noise, config, coast + regions, hills."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from backend.terrain.grid import Grid  # noqa: E402
from backend.terrain.noise import Noise  # noqa: E402


# ---- grid + noise ----

def test_noise_same_seed_same_values():
    a, b = Noise(7), Noise(7)
    assert a.fbm(1234.5, -800, 5) == b.fbm(1234.5, -800, 5)
    assert a.ridged(3.3, 4.4, 4) == b.ridged(3.3, 4.4, 4)
    assert a.warp(10.0, 20.0, 50.0, 300.0) == b.warp(10.0, 20.0, 50.0, 300.0)
    assert Noise(8).fbm(1234.5, -800, 5, scale=500) != a.fbm(1234.5, -800, 5, scale=500)


def test_fbm_and_ridged_in_range():
    n = Noise(3)
    for k in range(4000):
        x, y = (k * 37.7) % 911 - 400, (k * 13.1) % 677 - 300
        assert -1.0 <= n.fbm(x, y, 6, scale=37.0) <= 1.0
        assert 0.0 <= n.ridged(x, y, 5, scale=41.0) <= 1.0


def test_tiles_agree_at_shared_world_points():
    nz = Noise(11)
    g64 = Grid(64, 0.0, 0.0, 640.0)        # 10 m cells
    g128 = Grid(128, -640.0, -640.0, 1280.0)  # same cells, a bigger tile around it

    def field(g):
        return [nz.fbm(x, y, 5, scale=150.0) for _, x, y in g.points()]

    f64, f128 = field(g64), field(g128)
    for j in range(0, 64, 7):
        for i in range(0, 64, 5):
            assert abs(f64[g64.idx(i, j)] - f128[g128.idx(i + 64, j + 64)]) < 1e-9


def test_grid_basics_sample_blur_slope():
    g = Grid(16, 100.0, 200.0, 160.0)
    assert g.cell == 10.0 and g.xs[0] == 105.0 and g.ys[-1] == 355.0
    ramp = g.new()
    for k, x, y in g.points():
        ramp[k] = 0.1 * x  # 10 % slope along x
    assert abs(g.sample(ramp, 150.0, 250.0) - 15.0) < 1e-9
    sl = g.slope(ramp)
    assert all(abs(s - 0.1) < 1e-9 for s in sl)
    b = g.blur(ramp, 2)
    assert abs(b[g.idx(8, 8)] - ramp[g.idx(8, 8)]) < 1e-9  # a box blur keeps a straight ramp in the middle
    spike = g.new()
    spike[g.idx(8, 8)] = 1.0
    bs = g.blur(spike, 1)
    assert abs(sum(bs) - 1.0) < 1e-9 and bs[g.idx(8, 8)] < 1.0


def test_dist_to_single_cell():
    g = Grid(40, 0.0, 0.0, 400.0)
    m = g.new()
    m[g.idx(10, 20)] = 1.0
    d = g.dist_to(m)
    for i, j in ((10, 20), (30, 20), (10, 0), (35, 39), (0, 0)):
        true = math.hypot((i - 10) * g.cell, (j - 20) * g.cell)
        assert abs(d[g.idx(i, j)] - true) <= g.cell
    assert d[g.idx(30, 20)] == 200.0


# ---- config ----

from backend.terrain.config import CHOICES, KEYS, PRESETS, terrain_config  # noqa: E402


def test_presets_validate():
    assert len(PRESETS) == 11 and 'Classic' in PRESETS and 'Coastal' in PRESETS
    for name, p in PRESETS.items():
        assert set(p) <= set(KEYS) | set(CHOICES) | {'classic'}, name
        for k, v in p.items():
            if k in KEYS:
                assert KEYS[k][1] <= v <= KEYS[k][2], (name, k)
            if k in CHOICES:
                assert v in CHOICES[k][1], (name, k)
        cfg = terrain_config({'preset': name})
        assert cfg['preset'] == name and cfg['classic'] == (name == 'Classic')
        assert all(KEYS[k][1] <= cfg[k] <= KEYS[k][2] for k in KEYS)


def test_clamp_and_user_beats_preset():
    cfg = terrain_config({'preset': 'Coastal', 'mountain_m': 1800})
    assert cfg['mountain_intensity'] == 0.55 and cfg['mountain_m'] == 1800.0 and cfg['coast'] == 'one'
    cfg = terrain_config({'mountain_m': 99999, 'mountain_cover': 0.0, 'rain': 'wet', 'coast': 'moon',
                          'nonsense': 1, 'seed': '7'})
    assert cfg['mountain_m'] == 2500.0 and cfg['mountain_cover'] == 0.05 and cfg['rain'] == KEYS['rain'][0]
    assert cfg['coast'] == PRESETS['Coastal']['coast'] and 'nonsense' not in cfg and cfg['seed'] == 7
    assert terrain_config({'preset': 'Alpine'})['snowline_m'] == 1400.0
    assert terrain_config({'preset': 'Desert', 'rain': 0.8})['rain'] == 0.8


# ---- stage 1 + 2: coast and regions ----

from backend.terrain.coast import land_mask  # noqa: E402
from backend.terrain.regions import region_masks, shares  # noqa: E402

MASKS = ('mountains', 'foothills', 'hills', 'plateaus', 'plains', 'valleys', 'wetlands')


def _world(n=96, size=30000.0, **opts):
    cfg = terrain_config({'preset': 'Coastal', 'seed': 7, **opts})
    g, nz, maps = Grid(n, -size / 2, -size / 2, size), Noise(cfg['seed']), {}
    land_mask(g, cfg, nz, maps)
    region_masks(g, cfg, nz, maps)
    return g, cfg, maps


def _side_mean(g, arr, lo, hi, axis):
    vals = [arr[g.idx(i, j)] for j in range(g.n) for i in range(g.n)
            if lo <= ((i if axis == 0 else j) + 0.5) / g.n < hi]
    return sum(vals) / len(vals)


def test_coast_on_the_chosen_side():
    for deg, axis, sea_hi in ((0, 0, True), (180, 0, False), (90, 1, True), (270, 1, False)):
        g, _, maps = _world(n=64, coast_side_deg=deg)
        far = (0.9, 1.0) if sea_hi else (0.0, 0.1)
        near = (0.0, 0.1) if sea_hi else (0.9, 1.0)
        assert _side_mean(g, maps['land'], *far, axis) < 0.2, deg
        assert _side_mean(g, maps['land'], *near, axis) > 0.95, deg
        k_land = max(range(g.n * g.n), key=lambda k: maps['coast_dist'][k])
        assert maps['land'][k_land] >= 0.5 and min(maps['coast_dist']) < 0


def test_coast_modes():
    g, _, maps = _world(n=64, coast='none')
    assert min(maps['land']) == 1.0 and min(maps['coast_dist']) > 1e6
    g, _, maps = _world(n=64, coast='island')
    edge = [maps['land'][g.idx(i, j)] for j in range(g.n) for i in range(g.n) if min(i, j, g.n - 1 - i, g.n - 1 - j) == 0]
    assert max(edge) < 0.5 and maps['land'][g.idx(32, 32)] == 1.0
    g, _, maps = _world(n=64, coast='archipelago')
    share = sum(1 for v in maps['land'] if v >= 0.5) / len(maps['land'])
    assert 0.1 < share < 0.7


def test_region_shares_follow_config():
    for opts in ({}, {'mountain_cover': 0.4, 'hills': 0.8, 'flatland': 0.3, 'plateaus': 0.3}):
        g, cfg, maps = _world(**opts)
        sh = shares(maps)
        assert abs(sh['mountains'] - cfg['mountain_cover']) <= 0.05, sh
        rest = [k for k in range(g.n * g.n) if maps['land'][k] >= 0.5 and maps['mountains'][k] < 0.5
                and maps['foothills'][k] < 0.5]
        hills = sum(1 for k in rest if maps['hills'][k] >= 0.5) / len(rest)
        plat = sum(1 for k in rest if maps['plateaus'][k] >= 0.5) / len(rest)
        assert abs(hills - cfg['hills'] * (1 - cfg['flatland'])) <= 0.05, (hills, opts)
        assert abs(plat - cfg['plateaus'] * (1 - cfg['flatland'])) <= 0.05, (plat, opts)
        for k in range(g.n * g.n):
            vals = [maps[m][k] for m in MASKS]
            assert all(0.0 <= v <= 1.0 for v in vals)
            assert sum(vals) <= maps['land'][k] + 1e-9
        # inland: no mountain within 2 km of the sea
        assert all(maps['mountains'][k] < 0.01 for k in range(g.n * g.n) if maps['coast_dist'][k] < 2000)

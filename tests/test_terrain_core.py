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

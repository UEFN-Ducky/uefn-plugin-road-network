"""Terrain engine water stages: erosion (stage 7), rivers and lakes (stage 8)."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from backend.terrain.erosion import erode, hydraulic, thermal  # noqa: E402
from backend.terrain.grid import Grid  # noqa: E402
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

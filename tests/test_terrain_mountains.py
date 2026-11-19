import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from backend.terrain import Grid, Noise  # noqa: E402
from backend.terrain.mountains import passes, ridge_network, ridge_height  # noqa: E402

CFG = {'mountain_m': 2000.0, 'mountain_intensity': 1.0}


def _world(seed=3):
    """A 24 km grid with one long range: a rotated ellipse 16 x 5 km."""
    g = Grid(96, 0.0, 0.0, 24000.0)
    mask = g.new()
    a = math.radians(30)
    for k, x, y in g.points():
        u = (x - 12000) * math.cos(a) + (y - 12000) * math.sin(a)
        v = -(x - 12000) * math.sin(a) + (y - 12000) * math.cos(a)
        if (u / 8000) ** 2 + (v / 2500) ** 2 <= 1:
            mask[k] = 1.0
    return g, mask, Noise(seed)


def _seg_dist(px, py, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    ll = dx * dx + dy * dy
    t = min(max(((px - a[0]) * dx + (py - a[1]) * dy) / ll, 0), 1) if ll else 0
    return math.hypot(px - a[0] - dx * t, py - a[1] - dy * t)


def test_network_shape():
    g, mask, nz = _world()
    net = ridge_network(g, CFG, nz, mask)
    assert len(net['spines']) == 1 and len(net['spines'][0]) > 10
    assert len(net['branches']) >= 3
    assert net['peaks'] and abs(max(p[2] for p in net['peaks']) - 2000.0) < 1e-6
    sp = net['spines'][0]
    for br in net['branches']:  # 35-70 degrees off the local spine direction
        x, y = br[0][0], br[0][1]
        i = min(range(len(sp) - 1), key=lambda q: math.hypot(sp[q][0] - x, sp[q][1] - y))
        t = math.atan2(sp[i + 1][1] - sp[i][1], sp[i + 1][0] - sp[i][0])
        b = math.atan2(br[1][1] - y, br[1][0] - x)
        d = abs((b - t + math.pi) % (2 * math.pi) - math.pi)
        assert 25 <= math.degrees(d) <= 95  # 35-70 plus the noise bend on the first segment


def test_ridge_height():
    g, mask, nz = _world()
    net = ridge_network(g, CFG, nz, mask)
    h, _ = ridge_height(g, CFG, nz, mask, net)
    k = max(range(len(h)), key=h.__getitem__)
    assert 1800.0 <= h[k] <= 2200.0
    px, py = g.xs[k % g.n], g.ys[k // g.n]
    sp = net['spines'][0]
    assert min(_seg_dist(px, py, a, b) for a, b in zip(sp, sp[1:])) <= 300.0
    dist = g.dist_to(mask)
    assert all(h[q] == 0.0 for q in range(len(h)) if dist[q] > 3000.0)
    assert min(h) >= 0.0


def test_summits_are_rounded():
    """No needle summits: crest tops fall on average under 7 % of their height to their 8 neighbours (250 m away),
    none over 12 %. The bare (1 - d/w)^sharp cone fell 13 % on average, up to 28 %."""
    for seed in (3, 5):
        g, mask, nz = _world(seed)
        h, _ = ridge_height(g, CFG, nz, mask)
        n, top, drops = g.n, max(h), []
        for k in range(n * n):
            i, j = k % n, k // n
            if 0 < i < n - 1 and 0 < j < n - 1 and h[k] >= 0.6 * top:
                nb = [h[(j + b) * n + i + a] for a in (-1, 0, 1) for b in (-1, 0, 1) if a or b]
                if h[k] >= max(nb):
                    drops.append((h[k] - sum(nb) / 8) / h[k])
        assert len(drops) >= 2, drops  # several summits along the range, not one
        assert sum(drops) / len(drops) < 0.07 and max(drops) < 0.12, (seed, drops)


def test_pass_between_cities():
    g, mask, nz = _world()
    net = ridge_network(g, CFG, nz, mask)
    h, _ = ridge_height(g, CFG, nz, mask, net)
    before = h[:]
    a = math.radians(30)
    off = (-math.sin(a) * 6000, math.cos(a) * 6000)  # either side of the range, across its axis
    ca, cb = (12000 + off[0], 12000 + off[1]), (12000 - off[0], 12000 - off[1])
    ps, pmap = passes(g, net, [ca, {'x': cb[0], 'y': cb[1]}], h)
    assert len(ps) == 1
    px, py, ph = ps[0]
    sp = net['spines'][0]
    peak = max(g.sample(before, x, y) for x, y, _ in sp if math.hypot(x - px, y - py) <= 5000)
    assert g.sample(h, px, py) < 0.5 * peak and ph < 0.5 * peak
    assert _seg_dist(px, py, ca, cb) <= 5000.0
    assert max(pmap) > 0.9 and min(pmap) == 0.0
    assert passes(g, net, [ca], h)[0] == []  # one city: no link, no pass


def test_pass_for_every_crossing_pair():
    g, mask, nz = _world()
    net = ridge_network(g, CFG, nz, mask)
    h, _ = ridge_height(g, CFG, nz, mask, net)
    before = h[:]
    a = math.radians(30)

    def at(u, v):  # u along the range axis, v across it
        return (12000 + u * math.cos(a) - v * math.sin(a), 12000 + u * math.sin(a) + v * math.cos(a))
    cities = [at(-4000, -6000), at(-4000, 6000), at(4000, 6000)]  # 0-1 and 0-2 cross the range, 1-2 does not
    ps, _ = passes(g, net, cities, h)
    sp = net['spines'][0]
    crossing = [(p, q) for p, q in ((0, 1), (0, 2), (1, 2)) if _crosses(cities[p], cities[q], sp)]
    assert sorted(crossing) == [(0, 1), (0, 2)]
    for p, q in crossing:
        ok = False
        for px, py, ph in ps:
            peak = max(g.sample(before, x, y) for x, y, _ in sp if math.hypot(x - px, y - py) <= 5000)
            if (_seg_dist(px, py, cities[p], cities[q]) <= 5000 and ph < 0.5 * peak
                    and g.sample(h, px, py) < 0.5 * peak):
                ok = True
        assert ok, (p, q)
    for i, (x1, y1, _) in enumerate(ps):  # merged: no two passes closer than 2 km
        assert all(math.hypot(x1 - x2, y1 - y2) > 2000 for x2, y2, _ in ps[i + 1:])


def _crosses(c1, c2, sp):
    from backend.terrain.mountains import _cross
    return any(_cross(c1, c2, s0, s1) for s0, s1 in zip(sp, sp[1:]))


def _coastal(intensity, mountain_m=2000.0):
    from backend.terrain import engine, terrain_config
    cfg = terrain_config({'preset': 'Coastal', 'seed': 7, 'mountain_m': mountain_m, 'mountain_intensity': intensity})
    return engine.generate(cfg, 128, 0.0, 0.0, 30000.0).maps


def test_tallest_peak_is_mountain_height():
    soft, sharp = _coastal(0.2), _coastal(0.9)
    top_soft, top_sharp = max(soft['height']), max(sharp['height'])
    assert 1900.0 <= top_soft <= 2100.0 and 1900.0 <= top_sharp <= 2100.0
    assert abs(top_soft - top_sharp) <= 0.05 * top_soft  # intensity shapes, never caps

    def mean_slope(m):
        ks = [k for k, v in enumerate(m['mountains']) if v >= 0.5]
        return sum(m['slope'][k] for k in ks) / len(ks)
    assert mean_slope(sharp) > mean_slope(soft)
    assert max(_coastal(0.5, 0.0)['ridge']) == 0.0  # Mountain height 0: no mountains


def test_seeded_and_empty():
    g, mask, nz = _world(5)
    assert ridge_height(g, CFG, nz, mask)[0] == ridge_height(g, CFG, Noise(5), mask)[0]
    empty = g.new()
    assert ridge_network(g, CFG, nz, empty) == {'spines': [], 'branches': [], 'peaks': [], 'widths': [], 'bwidths': []}
    assert max(ridge_height(g, CFG, nz, empty)[0]) == 0.0

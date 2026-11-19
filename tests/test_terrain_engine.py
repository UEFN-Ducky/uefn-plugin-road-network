"""Terrain engine pipeline: stage cache, preview speed, metadata, city flattening, citygen shape."""
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from backend.terrain import engine  # noqa: E402
from backend.terrain.config import terrain_config  # noqa: E402

SIZE = 24000.0


def _gen(n=64, quality='final', cities=(), keep=(), **opts):
    cfg = terrain_config({'preset': 'Coastal', 'seed': 7, **opts})
    return engine.generate(cfg, n, -SIZE / 2, -SIZE / 2, SIZE, cities=cities, keep=keep, quality=quality)


def test_only_stages_after_a_changed_slider_rerun():
    engine.clear_cache()
    first = _gen(erosion=0.3)
    assert first.meta['ran'][:6] == ['coast', 'regions', 'macro', 'mountains', 'hills', 'blend']
    again = _gen(erosion=0.6)
    assert not {'coast', 'regions', 'macro', 'mountains', 'hills', 'blend'} & set(again.meta['ran'])
    assert 'detail' in again.meta['ran']
    same = _gen(erosion=0.6)
    assert same.meta['ran'] == [] and list(same.maps['height']) == list(again.maps['height'])
    # cached arrays are copies: changing a result never changes the next one
    same.maps['height'][0] = 1e6
    assert _gen(erosion=0.6).maps['height'][0] != 1e6


def test_preview_128_is_fast():
    engine.clear_cache()
    t = time.perf_counter()
    w = _gen(n=256, quality='preview')
    dt = time.perf_counter() - t
    assert w.grid.n == 128 and 'erosion' not in w.meta['ran']
    assert dt < 2.0, (dt, w.meta['stage_ms'])  # 1.5 before the sea floor stage (ocean, ~0.2 s)


def test_meta_and_maps():
    engine.clear_cache()
    w = _gen()
    m = w.meta
    for k in ('seed', 'preset', 'stage_ms', 'min', 'max', 'shares', 'rivers', 'lakes'):
        assert k in m
    assert m['seed'] == 7 and m['preset'] == 'Coastal' and m['min'] < 0 < m['max'] <= 2500.0
    for k in ('land', 'coast_dist', 'plains', 'hills', 'foothills', 'mountains', 'plateaus', 'valleys',
              'wetlands', 'height', 'slope'):
        assert len(w.maps[k]) == 64 * 64, k


def test_cities_are_flattened():
    engine.clear_cache()
    city = {'x': -3000.0, 'y': 1500.0, 'radius': 1500.0}
    w = _gen(n=96, cities=[city], erosion=0.0, mountain_cover=0.6)
    g, h = w.grid, w.maps['height']
    inside = [h[k] for k, x, y in g.points() if (x - city['x']) ** 2 + (y - city['y']) ** 2 < (city['radius']) ** 2]
    assert inside and max(inside) - min(inside) < 1.0


def test_city_on_the_shore_never_fills_the_sea_and_natural_leaves_the_ground():
    engine.clear_cache()
    cfg = terrain_config({'preset': 'Coastal', 'seed': 7, 'mountain_m': 0.0, 'erosion': 0.0})
    base = engine.generate(cfg, 96, -SIZE / 2, -SIZE / 2, SIZE)
    g, cd = base.grid, base.maps['coast_dist']
    k0 = min((k for k in range(len(cd)) if 300.0 < cd[k] < 800.0), key=lambda k: cd[k])  # a city on the beach
    city = {'x': g.xs[k0 % g.n], 'y': g.ys[k0 // g.n], 'radius': 1500.0}
    flat = engine.generate(cfg, 96, -SIZE / 2, -SIZE / 2, SIZE, cities=[city])
    wet = [k for k, x, y in g.points() if base.maps['land'][k] < 0.5 and math.hypot(x - city['x'], y - city['y']) < 1650.0]
    assert wet and all(flat.maps['height'][k] < 0.0 for k in wet)  # the bay under the city is still water
    nat = engine.generate(cfg, 96, -SIZE / 2, -SIZE / 2, SIZE, cities=[city], flatten_cities=False)
    assert list(nat.maps['height']) == list(base.maps['height'])  # natural: the land as it was


def test_core_ships_alone(monkeypatch):
    engine.clear_cache()
    monkeypatch.setattr(engine, '_optional', lambda name, reads: (None, reads))
    w = _gen(mountain_m=1500.0)
    assert w.meta['ran'] == ['coast', 'regions', 'macro', 'mountains', 'hills', 'blend', 'ocean', 'detail']
    assert 500.0 < w.meta['max'] <= 1500.0
    engine.clear_cache()


def test_to_citygen_shape_matches_terrain():
    from backend import citygen as cg
    old = cg.terrain(7, SIZE * 100, 3000.0, [], grid=32)
    new = _gen(n=32).to_citygen()
    assert set(old) <= set(new)
    assert new['grid'] == 32 and len(new['heights']) == 32 and all(len(r) == 32 for r in new['heights'])
    assert new['min'] == [-SIZE * 50, -SIZE * 50] and new['max'] == [SIZE * 50, SIZE * 50]
    assert type(new['min']) is type(old['min']) and isinstance(new['z_scale'], float)
    assert new['sea'] == 0.0
    hi = _gen(n=32, mountain_m=2000.0, mountain_intensity=1.0).to_citygen()
    assert hi['z_scale'] > 100.0

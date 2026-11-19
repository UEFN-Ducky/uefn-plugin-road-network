"""ocean.py: a real sea floor (shelf, slope, deep floor with relief, canyons) and beaches, across seeds."""
import statistics as st

import pytest

from backend.terrain import generate
from backend.terrain.config import terrain_config

SIZE = 8000.0


def _world(preset, seed, **kw):
    return generate(terrain_config({'preset': preset, 'seed': seed, **kw}), 128, -SIZE / 2, -SIZE / 2, SIZE,
                    quality='preview')


@pytest.mark.parametrize('preset,seed', [('Island', 1), ('Island', 7), ('Coastal', 42), ('Tropical', 3)])
def test_sea_floor_has_depth_and_relief(preset, seed):
    w = _world(preset, seed)
    h, cd, s0 = w.maps['height'], w.maps['coast_dist'], w.cfg['sea_level_m']
    sea = [k for k in range(len(h)) if cd[k] < 0]
    assert all(h[k] < s0 for k in sea), 'no sea cell stands out of the water'
    far = [h[k] for k in sea if cd[k] < -1500]
    assert max(w.maps['depth']) > 150, 'the shelf drops to a deep floor'
    assert st.pstdev(far) > 8, 'the deep floor is not a flat sheet'
    shelf_edge = [-cd[k] for k in sea if 40 < s0 - h[k] < 60]
    assert st.pstdev(shelf_edge) > 80, 'the shelf edge wanders, never a ring at one distance'
    assert any(v > 0.5 for v in w.maps['canyon']), 'canyons cut out to sea'


def test_coast_is_continuous_and_beaches_sit_at_the_water():
    w = _world('Tropical', 1)
    h, cd, s0, n = w.maps['height'], w.maps['coast_dist'], w.cfg['sea_level_m'], w.grid.n
    beach = w.maps['beach']
    for k in range(n * n - 1):  # (a cliff is a wall on purpose: only the sandy shore is checked)
        if beach[k] > 0.5 and cd[k + 1] < 0 and (k + 1) % n:
            assert abs(h[k + 1] - h[k]) < 0.15 * w.grid.cell, 'a beach runs into the water, no step'
    sand = [h[k] for k in range(n * n) if beach[k] > 0.5]
    assert sand and s0 < st.median(sand) < s0 + 4
    assert sum(w.meta['shore'].values()) > 0


def test_no_coast_world_has_no_sea_floor():
    w = _world('Plains', 2)
    assert 'depth' not in w.maps and 'beach_type' not in w.maps

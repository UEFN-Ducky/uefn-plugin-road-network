"""The terrain pipeline: 12 stages in order, each cached by a hash of what it reads, so moving one slider reruns
only the stages after it. Units are metres; to_citygen() converts to citygen's cm.

Optional stage modules (mountains, erosion, hydrology, climate, suitability) load through try/import: the core
ships alone. Each exposes one stage function f(grid, cfg, noise, maps) (the first of ENTRY that it has) and may
list the cfg keys it reads in READS."""
import copy
import hashlib
import importlib
import math
import time
from array import array
from collections import OrderedDict

from . import coast, hills, ocean, regions
from .config import terrain_config
from .grid import Grid, smoothstep
from .noise import Noise

PREVIEW_MAX_N = 128
CACHE_PER_STAGE = 8
FLATTEN_BLEND_M = 600.0
ENTRY = ('stage', 'run', 'erode', 'hydrology', 'flow')


def _optional(name, reads):
    try:
        mod = importlib.import_module('.' + name, __package__)
    except ImportError:
        return None, reads
    fn = next((getattr(mod, e) for e in ENTRY if callable(getattr(mod, e, None))), None)
    return fn, tuple(getattr(mod, 'READS', reads))


def _hills(grid, cfg, noise, maps):
    hills.hill_height(grid, cfg, noise, maps)
    return hills.plateau_height(grid, cfg, noise, maps)


def _flatten(grid, cfg, noise, maps):
    """Blend, then cities and keep zones: flat at the local plains level (keep: its own z) inside radius x 1.1,
    blended back over FLATTEN_BLEND_M. Land only: the sea and lakes under a city stay water (levelled with them,
    a city over a bay filled it in as a grey plate to its centre's height)."""
    hills.blend(grid, cfg, noise, maps)
    zones = [(c['x'], c['y'], c.get('radius', 0.0), c.get('z')) for c in maps.get('_flat', [])]
    if not zones:
        return maps
    h, macro, land = maps['height'], maps['macro'], maps['land']
    levels = [(x, y, r * 1.1, grid.sample(macro, x, y) if z is None else z) for x, y, r, z in zones]
    for k, x, y in grid.points():
        for cx, cy, r, z in levels:
            w = (1.0 - smoothstep(r, r + FLATTEN_BLEND_M, math.hypot(x - cx, y - cy))) * land[k]
            if w > 0:
                h[k] += (z - h[k]) * w
    return maps


def _stages():
    """(name, fn, cfg keys it reads). Built per call so a module pushed later is picked up."""
    mtn, mtn_r = _optional('mountains', ('mountain_m', 'mountain_intensity', 'mountain_cover'))
    ero, ero_r = _optional('erosion', ('erosion', 'thermal'))
    hyd, hyd_r = _optional('hydrology', ('rivers', 'lakes', 'sea_level_m'))
    cli, cli_r = _optional('climate', ('temp_c', 'rain', 'snowline_m', 'sea_level_m'))
    sui, sui_r = _optional('suitability', ('sea_level_m',))
    return [
        ('coast', coast.land_mask, ('coast', 'coast_side_deg', 'land_size')),
        ('regions', regions.region_masks, ('mountain_cover', 'hills', 'flatland', 'plateaus')),
        ('macro', hills.macro_height, ('sea_level_m', 'relief_m', 'flatland', 'coast')),
        ('mountains', mtn or hills.mountain_fallback, mtn_r + ('mountain_m', 'mountain_intensity')),
        ('hills', _hills, ('hill_km', 'relief_m', 'flatland', 'mountain_m', 'plateaus', 'sea_level_m')),
        ('blend', _flatten, ()),
        ('erosion', ero, ero_r),
        ('hydrology', hyd, hyd_r),
        ('ocean', ocean.stage, ocean.READS),
        ('climate', cli, cli_r),
        ('suitability', sui, sui_r),
        ('detail', hills.detail, ('detail',)),
    ]


_CACHE: dict = {}  # stage name -> OrderedDict(key -> {map name: value}) of what that stage added or changed


def clear_cache():
    _CACHE.clear()


def _copy(v):
    return array('d', v) if isinstance(v, array) else copy.deepcopy(v)


def _run(name, fn, key, grid, cfg, noise, maps):
    lru = _CACHE.setdefault(name, OrderedDict())
    if key in lru:
        lru.move_to_end(key)
        maps.update({k: _copy(v) for k, v in lru[key].items()})
        return False
    before = {k: (id(v), len(v) if isinstance(v, array) else None) for k, v in maps.items()}
    snap = {k: array('d', v) for k, v in maps.items() if isinstance(v, array)}
    fn(grid, cfg, noise, maps)
    out = {k: _copy(v) for k, v in maps.items()
           if k not in before or id(v) != before[k][0] or (k in snap and v != snap[k])}
    lru[key] = out
    while len(lru) > CACHE_PER_STAGE:
        lru.popitem(last=False)
    return True


def _components(grid, on) -> int:
    """Connected groups (4-neighbour) of the cells where on[k] is true."""
    n, seen, count = grid.n, set(), 0
    for s in range(n * n):
        if not on[s] or s in seen:
            continue
        count += 1
        seen.add(s)
        stack = [s]
        while stack:
            k = stack.pop()
            i, j = k % n, k // n
            for q in ((k - 1) if i else -1, (k + 1) if i < n - 1 else -1, k - n if j else -1, k + n if j < n - 1 else -1):
                if q >= 0 and on[q] and q not in seen:
                    seen.add(q)
                    stack.append(q)
    return count


class World:
    def __init__(self, grid, cfg, maps, meta):
        self.grid, self.cfg, self.maps, self.meta = grid, cfg, maps, meta

    def to_citygen(self) -> dict:
        """citygen's terrain() shape: {min, max, grid, heights[j][i] cm, sea cm (None without a coast), z_scale,
        maps}. maps are the engine's own (metres, flat arrays): callers keep only what they need."""
        g, h = self.grid, self.maps['height']
        rows = [[round(h[j * g.n + i] * 100.0, 1) for i in range(g.n)] for j in range(g.n)]
        peak = max((abs(v) for r in rows for v in r), default=0.0)
        z = max(100.0, math.ceil(peak * 1.02 * 128 / 32767 / 50) * 50.0)  # same rule as citygen z_scale()
        sea = None if self.cfg.get('coast') == 'none' else self.cfg.get('sea_level_m', 0.0) * 100.0
        return {'min': [g.x0 * 100.0, g.y0 * 100.0], 'max': [(g.x0 + g.size) * 100.0, (g.y0 + g.size) * 100.0],
                'grid': g.n, 'heights': rows, 'sea': sea, 'z_scale': z, 'maps': self.maps}


def generate(cfg, n, x0, y0, size_m, cities=(), keep=(), quality='final', flatten_cities=True) -> World:
    """Run the stages on an n x n grid from the (x0, y0) corner over size_m metres.
    cfg: terrain_config() output (a raw opts dict is accepted too). cities / keep: dicts with x, y, radius in metres
    (keep may add z, the level it is held at). quality 'preview' caps n at PREVIEW_MAX_N and skips erosion.
    flatten_cities=False: the cities only keep their passes through the ranges; the ground under them is left as it
    is (city_ground 'natural': the city fits itself to the land)."""
    if 'classic' not in cfg:
        cfg = terrain_config(cfg)
    if quality == 'preview':
        n = min(int(n), PREVIEW_MAX_N)
    grid, noise = Grid(n, x0, y0, size_m), Noise(cfg['seed'])
    cities = [{'x': float(c['x']), 'y': float(c['y']), 'radius': float(c.get('radius', 0.0))} for c in cities]
    keep = [dict(k) for k in keep]
    maps = {'cities': [(c['x'], c['y']) for c in cities], '_flat': (cities if flatten_cities else []) + keep}
    meta = {'seed': cfg['seed'], 'preset': cfg.get('preset'), 'quality': quality, 'stage_ms': {}, 'ran': []}
    key = repr((n, float(x0), float(y0), float(size_m), cfg['seed']))
    for name, fn, reads in _stages():
        if fn is None or (name == 'erosion' and quality == 'preview'):
            continue
        extra = maps['cities'] if name == 'mountains' else (maps['_flat'] if name in ('blend', 'ocean') else None)
        key = hashlib.sha1(repr((name, key, sorted((k, cfg.get(k)) for k in reads), extra)).encode()).hexdigest()
        t = time.perf_counter()
        if _run(name, fn, key, grid, cfg, noise, maps):
            meta['ran'].append(name)
        meta['stage_ms'][name] = round((time.perf_counter() - t) * 1000.0, 1)
    # stage 12: output maps and metadata
    t = time.perf_counter()
    maps.pop('_flat', None)
    maps['slope'] = grid.slope(maps['height'])
    h = maps['height']
    meta.update(min=min(h), max=max(h), shares=regions.shares(maps), islands=regions.island_stats(grid, maps))
    lines = maps.get('river_lines')
    riv, lake = maps.get('river'), maps.get('lake')
    meta['rivers'] = len(lines) if isinstance(lines, list) else (
        _components(grid, [v > 0 for v in riv]) if riv is not None else 0)
    meta['lakes'] = _components(grid, [v > 0.5 for v in lake]) if lake is not None else 0
    bt = maps.get('beach_type')
    if bt is not None:  # shore cells per beach type, and how deep the sea floor goes
        meta['shore'] = {t: sum(1 for v in bt if int(v) == i) for i, t in enumerate(ocean.BEACH_TYPES) if i}
        meta['max_depth'] = max(maps['depth'])
    meta['stage_ms']['output'] = round((time.perf_counter() - t) * 1000.0, 1)
    return World(grid, cfg, maps, meta)

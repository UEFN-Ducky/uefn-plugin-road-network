"""Stage 9: temperature, moisture and biomes, plus material_masks() for the landscape paint layers.

run(grid, cfg, noise, maps) reads height (m) and, when present, land, sea, lake, river, coast_dist, wetlands and
slope; it adds temp (degC), moist (0..1), slope (if missing) and biome (index into BIOMES, stored as float)."""
import math
from array import array

from .grid import smoothstep

BIOMES = ('ocean', 'beach', 'wetland', 'grassland', 'meadow', 'temperate_forest', 'conifer_forest', 'rainforest',
          'savanna', 'desert', 'shrubland', 'rocky_slope', 'alpine_meadow', 'tundra', 'snow', 'cliff')
B = {name: k for k, name in enumerate(BIOMES)}

READS = ('temp_c', 'rain', 'snowline_m', 'coast_side_deg', 'sea_level_m', 'seed')

LAPSE_C_PER_M = 0.0065
CLIFF_SLOPE = 0.8
ROCK_SLOPE = 0.5
ALPINE_BAND_M = 500.0      # below the snowline: alpine meadow
SHADOW_KM = 10.0           # how far upwind a ridge still blocks rain

# temperature bands (upper edge, degC) x moisture bands (<0.25, <0.5, <0.75, wetter)
_TABLE = (
    (2.0, ('tundra', 'tundra', 'conifer_forest', 'conifer_forest')),
    (10.0, ('shrubland', 'grassland', 'conifer_forest', 'conifer_forest')),
    (18.0, ('shrubland', 'grassland', 'meadow', 'temperate_forest')),
    (24.0, ('desert', 'shrubland', 'temperate_forest', 'rainforest')),
    (1e9, ('desert', 'savanna', 'savanna', 'rainforest')),
)


def table_biome(t: float, m: float) -> int:
    col = 0 if m < 0.25 else 1 if m < 0.5 else 2 if m < 0.75 else 3
    for top, row in _TABLE:
        if t < top:
            return B[row[col]]
    return B[_TABLE[-1][1][col]]


def _on(maps, name, k):
    a = maps.get(name)
    return a is not None and a[k] > 0.5


def run(grid, cfg, noise, maps):
    n, cell = grid.n, grid.cell
    h = maps['height']
    sea_lvl = cfg.get('sea_level_m', 0.0)
    snow = cfg.get('snowline_m', 1900.0)
    rain = cfg.get('rain', 0.5)
    slope = maps.get('slope') or grid.slope(h)
    maps['slope'] = slope

    def water(k):
        return _on(maps, 'sea', k) or _on(maps, 'lake', k) or ('land' in maps and maps['land'][k] < 0.5) \
            or ('sea' not in maps and 'land' not in maps and h[k] < sea_lvl)

    # temperature: lapse rate on height above the sea plus a little regional noise
    temp = grid.new()
    for k, x, y in grid.points():
        temp[k] = cfg.get('temp_c', 14.0) - LAPSE_C_PER_M * max(h[k] - sea_lvl, 0.0) \
            + 1.5 * noise.fbm(x, y, 3, scale=20000.0)

    # moisture sources: the sea (wide), rivers and lakes (narrow)
    coast = maps.get('coast_dist')
    if coast is None:
        sea_mask = array('d', (1.0 if water(k) and not _on(maps, 'lake', k) else 0.0 for k in range(n * n)))
        coast = grid.dist_to(sea_mask)
    riv, lake = maps.get('river'), maps.get('lake')
    wet_src = array('d', (1.0 if (riv is not None and riv[k] > 0) or (lake is not None and lake[k] > 0.5) else 0.0
                          for k in range(n * n)))
    d_water = grid.dist_to(wet_src)

    # wind blows in from the sea side: upwind is towards coast_side_deg
    a = math.radians(cfg.get('coast_side_deg', 0.0))
    ux, uy = math.cos(a), math.sin(a)
    steps = 8
    step_m = max(cell, SHADOW_KM * 1000.0 / steps)
    near_m = max(cell * 2, 200.0)

    # ponytail: 8 bilinear samples per cell, ~1 s per 256^2; march on a coarser grid if it shows up in stage_ms
    moist = grid.new()
    for k, x, y in grid.points():
        hk = h[k]
        up = max(grid.sample(h, x + ux * step_m * s, y + uy * step_m * s) for s in range(1, steps + 1))
        shadow = 0.6 * smoothstep(0.0, 1200.0, up - hk)
        windward = 0.25 * smoothstep(0.0, 0.15, (hk - grid.sample(h, x + ux * near_m, y + uy * near_m)) / near_m)
        c = math.exp(-coast[k] / 20000.0) if coast[k] != float('inf') else 0.0
        w = math.exp(-d_water[k] / 2000.0) if d_water[k] != float('inf') else 0.0
        m = rain * (0.8 + 0.45 * c + 0.35 * w + windward) - shadow + 0.1 * noise.fbm(x - 3e4, y + 7e3, 3, scale=8000.0)
        moist[k] = min(max(m, 0.0), 1.0)

    wl = maps.get('wetlands')
    biome = grid.new()
    for k in range(n * n):
        hk, s = h[k], slope[k]
        if water(k):
            b = B['ocean']  # ponytail: lakes share the open-water biome; the lake map tells them apart
        elif s > CLIFF_SLOPE:
            b = B['cliff']
        elif hk >= snow:
            b = B['snow']
        elif s > ROCK_SLOPE:
            b = B['rocky_slope']
        elif coast[k] < 3 * cell + 30.0 and hk < sea_lvl + 4.0 and s < 0.15:
            b = B['beach']
        elif hk >= snow - ALPINE_BAND_M:
            b = B['alpine_meadow'] if moist[k] >= 0.25 else B['tundra']
        elif (wl is not None and wl[k] > 0.5) or (moist[k] > 0.85 and s < 0.03 and d_water[k] < 500.0):
            b = B['wetland']
        else:
            b = table_biome(temp[k], moist[k])
        biome[k] = b

    maps['temp'], maps['moist'], maps['biome'] = temp, moist, biome
    return maps


# grass, rock, sand, snow, dirt per biome
LAYERS = ('grass', 'rock', 'sand', 'snow', 'dirt')
_MIX = {
    'ocean': (0, 0, 1, 0, 0), 'beach': (0, 0, 1, 0, 0), 'wetland': (0.6, 0, 0, 0, 0.4),
    'grassland': (0.9, 0, 0, 0, 0.1), 'meadow': (1, 0, 0, 0, 0), 'temperate_forest': (0.7, 0, 0, 0, 0.3),
    'conifer_forest': (0.5, 0.1, 0, 0, 0.4), 'rainforest': (0.8, 0, 0, 0, 0.2), 'savanna': (0.5, 0, 0.2, 0, 0.3),
    'desert': (0, 0.1, 0.9, 0, 0), 'shrubland': (0.4, 0.1, 0.1, 0, 0.4), 'rocky_slope': (0.1, 0.8, 0, 0, 0.1),
    'alpine_meadow': (0.7, 0.2, 0, 0, 0.1), 'tundra': (0.3, 0.3, 0, 0.1, 0.3), 'snow': (0, 0.1, 0, 0.9, 0),
    'cliff': (0, 1, 0, 0, 0),
}
_MIX_BY_ID = [_MIX[name] for name in BIOMES]


def material_masks(maps) -> dict:
    """{layer: array('d') 0..1} for grass/rock/sand/snow/dirt; the five weights of every cell sum to 1.
    Biome sets the mix, steep ground fades to rock."""
    biome, slope = maps['biome'], maps['slope']
    out = {name: array('d', bytes(8 * len(biome))) for name in LAYERS}
    cols = [out[name] for name in LAYERS]
    for k in range(len(biome)):
        mix = _MIX_BY_ID[int(biome[k])]
        r = smoothstep(0.35, CLIFF_SLOPE, slope[k])
        w = [v * (1.0 - r) for v in mix]
        w[1] += r
        tot = sum(w)
        for c, v in zip(cols, w):
            c[k] = v / tot
    return out

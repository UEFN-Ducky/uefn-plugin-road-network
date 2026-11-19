"""Stage 10: slope, flood risk, buildability and road cost, plus best_sites() for city centres.

run(grid, cfg, noise, maps) reads height (m) and, when present, land, sea, lake, river, river_order, passes,
biome and slope; it adds slope (if missing), flood (0..1), build (0..1) and road_cost (>= 0.3)."""
import math
from array import array

from .climate import B, CLIFF_SLOPE
from .grid import smoothstep

READS = ('sea_level_m',)

MAX_BUILD_SLOPE = 0.15     # build is 0 from here
SITE_SLOPE = 0.08          # a city centre never sits steeper than this
FLOOD_REACH_M = 1000.0     # how far from a river the valley floor is searched


def _window(grid, arr, r, pick):
    """Separable min / max filter over a (2r+1)^2 square, edges clamp."""
    n = grid.n
    out = array('d', arr)
    for axis in (0, 1):
        for line in range(n):
            sl = slice(line * n, line * n + n) if axis == 0 else slice(line, n * n, n)
            row = list(out[sl])
            out[sl] = array('d', (pick(row[max(i - r, 0):i + r + 1]) for i in range(n)))
    return out


def run(grid, cfg, noise, maps):
    n, cell = grid.n, grid.cell
    nn = n * n
    h = maps['height']
    slope = maps.get('slope') or grid.slope(h)
    maps['slope'] = slope
    land, sea, lake, riv = maps.get('land'), maps.get('sea'), maps.get('lake'), maps.get('river')
    order = maps.get('river_order')
    passes, biome = maps.get('passes'), maps.get('biome')
    sea_lvl = cfg.get('sea_level_m', 0.0)

    def is_river(k):
        return riv is not None and riv[k] > 0

    def is_lake(k):
        return lake is not None and lake[k] > 0.5

    water = [is_river(k) or is_lake(k) or (sea is not None and sea[k] > 0.5)
             or (land is not None and land[k] < 0.5) or (sea is None and land is None and h[k] < sea_lvl)
             for k in range(nn)]
    cliff = [slope[k] > CLIFF_SLOPE or (biome is not None and biome[k] == B['cliff']) for k in range(nn)]

    # flood: near a river (wider for big rivers) and not far above the valley floor next to it
    src = array('d', (1.0 if is_river(k) or is_lake(k) else 0.0 for k in range(nn)))
    flood = grid.new()
    if any(src):
        d = grid.dist_to(src)
        r = max(1, min(int(FLOOD_REACH_M / cell), n))
        floor = _window(grid, array('d', (h[k] if src[k] else 1e9 for k in range(nn))), r, min)
        lvl = array('d', ((order[k] if order is not None else 1.0) if is_river(k) else 2.0 if is_lake(k) else 0.0
                          for k in range(nn)))
        lvl = _window(grid, lvl, r, max)
        for k in range(nn):
            o = max(lvl[k], 1.0)
            if water[k]:
                flood[k] = 1.0
            elif floor[k] < 1e8:
                near = math.exp(-d[k] / (150.0 * o))
                low = 1.0 - smoothstep(0.5, 2.0 + 2.0 * o, h[k] - floor[k])
                flood[k] = near * low

    sand = maps.get('beach'), maps.get('dune')  # ocean.py: a beach and its dunes stay open, towns step back
    build, cost = grid.new(), grid.new()
    for k in range(nn):
        s = slope[k]
        if not water[k] and not cliff[k]:
            build[k] = max(0.0, 1.0 - s / MAX_BUILD_SLOPE) * (1.0 - flood[k])
            if sand[0] is not None:
                build[k] *= (1.0 - sand[0][k]) * (1.0 - sand[1][k])
        c = 1.0 + 40.0 * s * s + (500.0 if water[k] else 0.0) + (1e4 if cliff[k] else 0.0)
        if passes is not None and passes[k] > 0.5:
            c *= 0.3
        cost[k] = c

    maps['flood'], maps['build'], maps['road_cost'] = flood, build, cost
    return maps


def best_sites(maps, count, radius_m, gap_m, grid):
    """Greedy city centres: highest mean `build` inside radius_m, each at least gap_m from the others,
    never on water or steeper than SITE_SLOPE. -> [{'x', 'y', 'i', 'j', 'score'}] best first (may be short)."""
    build, slope = maps['build'], maps['slope']
    n, cell = grid.n, grid.cell
    # ponytail: two box blurs of radius/2 approximate the disc mean; a true disc kernel if sites hug edges
    mean = grid.blur(build, max(1, int(radius_m / cell / 2)))
    order = sorted(range(n * n), key=lambda k: -mean[k])
    gap2 = gap_m * gap_m
    out = []
    for k in order:
        if len(out) >= count or mean[k] <= 0.05:
            break
        if build[k] <= 0.0 or slope[k] > SITE_SLOPE:
            continue
        x, y = grid.xs[k % n], grid.ys[k // n]
        if all((x - s['x']) ** 2 + (y - s['y']) ** 2 >= gap2 for s in out):
            out.append({'x': x, 'y': y, 'i': k % n, 'j': k // n, 'score': mean[k]})
    return out

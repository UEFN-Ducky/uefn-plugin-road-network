"""Stage 1: world layout and coastline. Adds land (0..1 soft), coast_dist (m, + on land, - at sea) and sea (0/1)."""
import math
import random
from array import array

from .grid import smoothstep

FAR_M = 1.0e7  # coast_dist where there is no sea at all


def land_mask(grid, cfg, noise, maps):
    mode = cfg.get('coast', 'none')
    size, half = grid.size, grid.size / 2
    cx, cy = grid.x0 + half, grid.y0 + half
    if mode == 'none':
        maps['land'] = grid.new(1.0)
        maps['coast_dist'] = grid.new(FAR_M)
        maps['sea'] = grid.new(0.0)
        return maps
    a = math.radians(cfg.get('coast_side_deg', 0.0))
    dirs = [(math.cos(a), math.sin(a))]
    if mode == 'two':  # sea on two neighbouring sides
        dirs.append((math.cos(a + math.pi / 2), math.sin(a + math.pi / 2)))
    warp_amt, warp_sc = size * 0.07, size * 0.22
    jag_sc = max(size * 0.06, 600.0)
    # island: an off-centre ellipse, its own size, shape and turn per seed (a centred disc read as a platform)
    rng = random.Random(noise.seed * 31 + 5)
    icx, icy = cx + rng.uniform(-0.06, 0.06) * size, cy + rng.uniform(-0.06, 0.06) * size
    rot = rng.uniform(0.0, math.pi)
    grow = cfg.get('land_size', 1.0)  # Island size: the island this much bigger than its base shape (Island only)
    ra, rb = rng.uniform(0.7, 0.85) * grow, rng.uniform(0.4, 0.65) * grow
    ca, sa = math.cos(rot), math.sin(rot)

    def edge(x, y):
        """Metres inside the coastline (negative at sea)."""
        wx, wy = noise.warp(x, y, warp_amt, warp_sc, 2)
        jag = noise.fbm(x / jag_sc + 31.7, y / jag_sc - 12.9, 3)  # bays and headlands
        if mode == 'island':
            u, v = ((wx - icx) * ca + (wy - icy) * sa) / half, (-(wx - icx) * sa + (wy - icy) * ca) / half
            # one scale per job, amplitudes falling: peninsulas and big bays, coves, then shoreline detail
            # (fbm mostly stays within +-0.35, hence the large factors)
            shape = (1.0 - math.hypot(u / ra, v / rb) + 1.1 * noise.fbm(x, y, 2, scale=size * 0.28)
                     + 0.35 * noise.fbm(x + 71.0, y - 13.0, 2, scale=size * 0.08)
                     + 0.1 * noise.fbm(x - 5.0, y + 44.0, 2, scale=size * 0.025)) * rb * half
            return min(shape, 0.92 * half - max(abs(x - cx), abs(y - cy)))  # never runs off the map
        if mode == 'archipelago':
            r = math.hypot(wx - cx, wy - cy) / half
            blobs = noise.fbm(wx / (size * 0.16) - 40.1, wy / (size * 0.16) + 7.7, 3)
            return (blobs + 0.35 * (0.62 - r) + 0.12 * jag) * half * 0.5
        t = min(0.42 - ((wx - cx) * dx + (wy - cy) * dy) / half for dx, dy in dirs)
        return (t + 0.12 * jag) * half

    e = grid.field(edge, min(jag_sc / 4, size * 0.006))
    c, n = grid.cell, grid.n
    land = array('d', (smoothstep(-c * 0.5, c * 0.5, v) for v in e))
    sea = array('d', (0.0 if v >= 0.5 else 1.0 for v in land))
    # one distance pass, to the shore cells (land with a sea neighbour), signed by side
    shore = grid.new()
    for k in range(n * n):
        if not sea[k]:
            i, j = k % n, k // n
            if (i and sea[k - 1]) or (i < n - 1 and sea[k + 1]) or (j and sea[k - n]) or (j < n - 1 and sea[k + n]):
                shore[k] = 1.0
    to_shore = grid.dist_to(shore)
    inf = float('inf')
    cd = grid.new()
    for k in range(n * n):
        d = to_shore[k]
        d = FAR_M if d == inf else d + c * 0.5
        cd[k] = d if not sea[k] else -d
    maps['land'], maps['coast_dist'], maps['sea'] = land, cd, sea
    return maps

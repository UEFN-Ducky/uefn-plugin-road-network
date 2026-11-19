"""Stage 1: world layout and coastline. Adds land (0..1 soft), coast_dist (m, + on land, - at sea) and sea (0/1)."""
import math
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

    def edge(x, y):
        """Metres inside the coastline (negative at sea)."""
        wx, wy = noise.warp(x, y, warp_amt, warp_sc, 2)
        jag = noise.fbm(x / jag_sc + 31.7, y / jag_sc - 12.9, 4)  # bays and headlands
        if mode == 'island':
            return (0.7 - math.hypot(wx - cx, wy - cy) / half + 0.22 * jag) * half
        if mode == 'archipelago':
            r = math.hypot(wx - cx, wy - cy) / half
            blobs = noise.fbm(wx / (size * 0.16) - 40.1, wy / (size * 0.16) + 7.7, 3)
            return (blobs + 0.35 * (0.62 - r) + 0.12 * jag) * half * 0.5
        t = min(0.42 - ((wx - cx) * dx + (wy - cy) * dy) / half for dx, dy in dirs)
        return (t + 0.12 * jag) * half

    e = grid.field(edge, min(jag_sc / 6, size / 48))
    c = grid.cell
    land = array('d', (smoothstep(-c * 0.5, c * 0.5, v) for v in e))
    sea = array('d', (0.0 if v >= 0.5 else 1.0 for v in land))
    to_sea, to_land = grid.dist_to(sea), grid.dist_to(land)
    inf = float('inf')
    cd = grid.new()
    for k in range(len(cd)):
        d = to_sea[k] if land[k] >= 0.5 else -to_land[k]
        cd[k] = FAR_M if d == inf else (-FAR_M if d == -inf else d)
    maps['land'], maps['coast_dist'], maps['sea'] = land, cd, sea
    return maps

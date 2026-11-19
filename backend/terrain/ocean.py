"""Stage 8b: the sea floor and the coast, in the height map itself (metres, after hydrology, before climate).

Bathymetry: from the coast out, an irregular continental shelf (its edge warped by noise, never a ring), the
continental slope, then the deep floor. On top, by depth: broad swells, underwater hills, ridged crests, warped
basins, canyons from the river mouths and the coast out across the shelf edge, and sediment settled in the hollows.

Beaches: a suitability B = slope x geology along the shore; where it is high a beach profile (shoreface, berm,
backshore) of irregular width, dunes behind it and sandbars in front; where it is low a rocky or cliffed shore.

Adds maps: depth (m under the sea level, 0 on land), seabed (0 sand .. 1 mud), canyon (0..1), beach (1 = sand),
dune (0..1), beach_type (BEACH_TYPES index, along the shore only). A world with no sea is left as it is."""
import math
import random
from array import array

from .grid import smoothstep

READS = ('coast', 'sea_level_m', 'mountain_m')

BEACH_TYPES = ('none', 'wide_sandy', 'narrow_sandy', 'mixed', 'rocky', 'cliff')
NEAR_M = 80.0  # the old near-shore ground blends into the new profile over this far out


def _scales(size_m):
    """Shelf width, slope width, shelf depth, deep floor depth (m), sized to the map: a 2 km map still shows the
    shelf, the slope and the deep floor; a 16 km one gets a wide shelf and a deep ocean."""
    shelf = min(max(0.12 * size_m, 400.0), 5000.0)
    return shelf, 0.6 * shelf, min(max(0.005 * size_m, 18.0), 60.0), min(max(0.03 * size_m, 90.0), 350.0)


def _mouths(grid, maps, cd, rng, shelf):
    """Canyon heads: where the rivers reach the sea (order 2 and up), then seeded points along the coast, each
    at least half a shelf from the others."""
    heads = []
    for r in maps.get('river_lines') or []:
        if r.get('order', 1) >= 2 and r.get('pts'):
            x, y = r['pts'][-1][0] / 100.0, r['pts'][-1][1] / 100.0
            if grid.sample(cd, x, y) < 3 * grid.cell + 60.0:
                heads.append((x, y))
    shore = [(x, y) for k, x, y in grid.points() if abs(cd[k]) < grid.cell]
    rng.shuffle(shore)
    want = len(heads) + 2 + int(grid.size / 3000.0)
    for x, y in shore:
        if len(heads) >= want:
            break
        if all(math.hypot(x - a, y - b) > 0.5 * shelf for a, b in heads):
            heads.append((x, y))
    return heads


def _canyon_mask(grid, noise, cdb, heads, reach):
    """Each head walked out to sea down the coast distance's gradient, meandering, until `reach` m offshore."""
    n, c = grid.n, grid.cell
    mask = grid.new()
    lo_x, lo_y, hi_x, hi_y = grid.x0, grid.y0, grid.x0 + grid.size, grid.y0 + grid.size
    for h, (x, y) in enumerate(heads):
        step = 1.2 * c
        for s in range(int(3 * reach / step)):
            gx = grid.sample(cdb, x + c, y) - grid.sample(cdb, x - c, y)
            gy = grid.sample(cdb, x, y + c) - grid.sample(cdb, x, y - c)
            g = math.hypot(gx, gy)
            if g < 1e-9:
                break
            a = math.atan2(-gy, -gx) + 0.7 * noise.fbm(s * step / 900.0 + 31.7 * h, 4.2, 2)
            x, y = x + step * math.cos(a), y + step * math.sin(a)
            if not (lo_x <= x < hi_x and lo_y <= y < hi_y) or -grid.sample(cdb, x, y) > reach:
                break
            i, j = int((x - grid.x0) / c), int((y - grid.y0) / c)
            mask[min(j, n - 1) * n + min(i, n - 1)] = 1.0
    return mask


def _zones(grid, maps):
    """(zone, harbour): 1 inside a city or keep zone (its ground is set by the blend stage), fading out over
    600 m; the harbour fades out to 2.5 radii + 600 m, so a coastal town's roads and gates stay in shallow water."""
    zones = [(z['x'], z['y'], z.get('radius', 0.0)) for z in maps.get('_flat') or []]
    out, port = grid.new(), grid.new()
    if zones:
        for k, x, y in grid.points():
            ds = [(math.hypot(x - zx, y - zy), r) for zx, zy, r in zones]
            out[k] = max(1.0 - smoothstep(1.1 * r, 1.1 * r + 600.0, d) for d, r in ds)
            port[k] = max(1.0 - smoothstep(1.1 * r, 2.5 * r + 600.0, d) for d, r in ds)
    return out, port


def stage(grid, cfg, noise, maps):
    cd_raw = maps.get('coast_dist')
    if cfg.get('coast') == 'none' or cd_raw is None or min(cd_raw) >= 0:
        return maps
    n, cell, size = grid.n, grid.cell, grid.size
    s0 = cfg.get('sea_level_m', 0.0)
    h = array('d', maps['height'])
    shelf, slope_w, d_shelf, d_deep = _scales(size)
    cdb = grid.blur(cd_raw, max(1, int(400.0 / cell)))  # creases of the raw distance field show as panels
    land, lake = maps['land'], maps.get('lake')
    mtn = maps.get('mountains')
    rng = random.Random(cfg.get('seed', 0) * 7919 + 17)

    # --- fields (smooth ones on a lattice: grid.field) ---
    big = grid.field(lambda x, y: noise.fbm(x / (2.5 * shelf) + 41.3, y / (2.5 * shelf) - 7.7, 3), shelf / 8)
    mid = grid.field(lambda x, y: noise.fbm(x / (0.6 * shelf) - 13.1, y / (0.6 * shelf) + 22.9, 3), shelf / 8)
    swell = grid.field(lambda x, y: noise.fbm(x / 3000.0 + 3.3, y / 3000.0 + 8.8, 4), 200.0)
    knoll = grid.field(lambda x, y: noise.fbm(x / 650.0 - 5.5, y / 650.0 + 1.1, 4), 100.0)
    crest = grid.field(lambda x, y: noise.ridged(x / 2400.0 + 9.9, y / 2400.0 - 3.1, 4), 150.0)

    def basin_fn(x, y):
        wx, wy = noise.warp(x, y, 900.0, 2500.0)
        return noise.fbm(wx / 1800.0 + 77.0, wy / 1800.0 - 19.0, 3)
    basin = grid.field(basin_fn, 150.0)
    ripple = array('d', (noise.fbm(x / 90.0 + 0.3, y / 90.0 - 0.6, 2) for _, x, y in grid.points())) \
        if cell < 45.0 else grid.new()

    # beach suitability B = C (the band below) x S (gentle ground) x G (soft rock, away from the ranges)
    soft_slope = grid.slope(grid.blur(h, max(1, int(100.0 / cell))))
    zone, port = _zones(grid, maps)
    fine = 1.0 - smoothstep(30.0, 80.0, cell)  # dunes and cliffs are narrower than a coarse cell: spikes there
    near_m = max(NEAR_M, 2.5 * cell)  # a coarse grid: the first sea cell is a whole cell out, never a wall

    # canyons: distance to each walked centreline
    heads = _mouths(grid, maps, cd_raw, rng, shelf)
    cmask = _canyon_mask(grid, noise, cdb, heads, shelf + 1.4 * slope_w)
    cdist = grid.dist_to(cmask) if any(cmask) else array('d', [float('inf')]) * (n * n)

    depth_m, seabed, canyon = grid.new(), grid.new(), grid.new()
    beach, dune, btype = grid.new(), grid.new(), grid.new()
    for k, x, y in grid.points():
        cr, cb = cd_raw[k], cdb[k]
        if cr > 400.0:
            continue  # inland: nothing here changes
        B = cliff = wb = 0.0
        if -400.0 < cr < 400.0:  # the shore's character (B, cliff, beach width) matters only near it
            geo = noise.fbm(x / 2200.0 - 61.0, y / 2200.0 + 5.0, 3)  # rock hardness
            wid = noise.fbm(x / 1400.0 + 8.1, y / 1400.0 + 92.0, 2)  # beach width
            brk = noise.fbm(x / 350.0 + 2.2, y / 350.0 - 2.2, 2)  # gaps in the bars and dunes
            hard = min(max(0.5 + geo + (0.6 * mtn[k] if mtn is not None else 0.0), 0.0), 1.0)
            S = 1.0 - smoothstep(0.06, 0.25, soft_slope[k])
            G = 1.0 - smoothstep(0.45, 0.8, hard)
            B = smoothstep(0.25, 0.65, S * G) * (1.0 - zone[k])
            if lake is not None and lake[k] > 0.5:
                B = 0.0
            # a cliff steepens a coast that already rises (hard rock, hilly ground); flat plains stay buildable
            cliff = smoothstep(0.6, 0.85, hard) * smoothstep(0.1, 0.3, soft_slope[k]) * (1.0 - B) \
                * (1.0 - zone[k]) * fine
            wb = min(max(70.0 + 70.0 * wid, 15.0), 160.0) * (0.5 + 0.5 * B)  # W_b = W0 + A_w N, clamped

        if cr < 0:
            # --- sea: shelf -> slope -> deep floor, then relief by depth ---
            d = -(cr + (cb - cr) * smoothstep(100.0, 600.0, -cr))
            dw = d + 0.35 * shelf * big[k] + 0.1 * shelf * mid[k]  # warped: the shelf edge wanders in and out
            sw = shelf * (0.75 + 0.5 * (0.5 + big[k]))
            dsh = d_shelf * (0.7 + 0.6 * (0.5 + mid[k]))
            t = min(max(dw / sw, 0.0), 1.0)
            base = dsh * (0.5 * t + 0.5 * t * t)  # the shelf: 0 at the shore, steepening to its outer edge
            base += (d_deep - dsh) * smoothstep(sw, sw + slope_w, dw)  # continental slope
            # the shoreface: gentle off a beach (1:40), steep off rock (1:8), into the shelf by 250 m
            near = d * (0.025 + 0.1 * (1.0 - B))
            base = near + (base - near) * smoothstep(0.0, 250.0, d)
            deep = smoothstep(0.0, sw + slope_w, dw)
            relief = (0.3 * base + 3.0) * swell[k] \
                + (2.0 + 0.1 * base) * knoll[k] * 2.0 \
                - 0.35 * d_deep * crest[k] ** 1.5 * smoothstep(0.4 * sw, sw + slope_w, dw) \
                + 0.3 * base * max(0.0, -basin[k]) * 2.0 * smoothstep(0.3 * sw, sw, dw) \
                + 0.4 * ripple[k] * smoothstep(5.0, 30.0, base)
            # canyon: A exp(-dist^2 / 2 sigma^2), wider and deeper out to sea, cutting the shelf edge
            sg = 50.0 + 0.07 * d
            cw = math.exp(-cdist[k] ** 2 / (2.0 * sg * sg)) if cdist[k] < 4.0 * sg else 0.0
            cut = cw * (5.0 + 0.6 * base) * smoothstep(30.0, 300.0, d)
            # sandbars off a beach: shallow ridges parallel to the coast, broken by noise
            bar = 0.0
            if B > 0.05 and d < 320.0:
                bar = 1.2 * B * max(0.0, math.sin(dw / 55.0 * math.pi)) ** 2 * smoothstep(-0.1, 0.25, brk) \
                    * smoothstep(25.0, 60.0, d) * (1.0 - smoothstep(200.0, 320.0, d))
            new = s0 - base - relief - cut + bar
            new = min(new, s0 - 0.3 - 0.01 * d)  # never out of the water: an islet would have no land mask
            hk = h[k] + (new - h[k]) * smoothstep(0.0, near_m, d) * (1.0 - port[k])  # a town keeps its level
            if zone[k] < 0.5:
                hk = min(hk, s0 - 0.3)  # the old near-shore ground could stand dry inside the sea mask
            depth_m[k] = s0 - hk
            canyon[k] = cw * smoothstep(30.0, 300.0, d)
            mud = smoothstep(0.6 * dsh, 1.6 * dsh, base) * (0.6 + 0.4 * deep)
            seabed[k] = min(1.0, mud + 0.5 * canyon[k] + 0.4 * max(0.0, -basin[k]))
            h[k] = hk
        elif cr < wb + 200.0:
            # --- land by the sea: beach profile, dunes; cliffs where the rock is hard and B low ---
            hk = h[k]
            if B > 0:
                berm = 1.2 + 1.0 * (0.5 + wid)
                target = s0 + 0.25 + berm * smoothstep(0.0, 0.6 * wb, cr)
                w = smoothstep(0.15, 0.5, B) * (1.0 - smoothstep(wb, wb + 90.0, cr))
                hk += (target - hk) * w
                ridge = math.exp(-((cr - wb - 30.0) / 18.0) ** 2) + 0.6 * math.exp(-((cr - wb - 80.0) / 24.0) ** 2)
                dn = B * ridge * (0.5 + 0.5 * smoothstep(-0.3, 0.3, brk)) * fine
                hk += (2.5 + 4.0 * (0.5 + geo)) * dn
                dune[k] = min(1.0, dn)
                beach[k] = 1.0 if (B > 0.5 and cr < wb) else 0.0
            if cliff > 0 and cr < 120.0:
                top = s0 + (6.0 + 14.0 * cliff) * smoothstep(-5.0, 25.0, cr) - 0.3 * max(0.0, cr - 35.0)
                hk = max(hk, hk + (top - hk) * cliff)
            h[k] = hk
        if abs(cr) < 2.0 * cell + 10.0:
            btype[k] = 5 if cliff > 0.5 else 4 if B < 0.15 else 3 if B < 0.55 else 1 if wb > 75.0 else 2

    maps['height'], maps['depth'], maps['seabed'], maps['canyon'] = h, depth_m, seabed, canyon
    maps['beach'], maps['dune'], maps['beach_type'] = beach, dune, btype
    return maps

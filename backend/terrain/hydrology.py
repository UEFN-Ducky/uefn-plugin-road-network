"""Stage 8: lakes and rivers. Priority-flood the height map from the sea and the map edge, keep deep, wide
depressions as lakes and fill the rest, route D8 flow over the filled surface, then mark, order and carve rivers."""
import heapq
import math
from array import array

EPS = 1e-4          # m per cell the filled surface still falls, so every land cell has a way down
LAKE_DEPTH_M = 3.0
_NB = [(di, dj, math.hypot(di, dj)) for dj in (-1, 0, 1) for di in (-1, 0, 1) if di or dj]


def _threshold_km2(rivers: float) -> float:
    """Wet upstream area that makes a river: 10 km2 at rivers 0+, 1 km2 at 0.5, 0.1 km2 at 1."""
    return 10 ** (1.0 - 2.0 * rivers)


def priority_flood(grid, h, outlet):
    """Filled surface: every cell's level >= its height, falling EPS per cell towards an outlet cell (sea / edge)."""
    n = grid.n
    f = array('d', h)
    done = bytearray(n * n)
    pq = []
    for k in range(n * n):
        i, j = k % n, k // n
        if outlet[k] or i in (0, n - 1) or j in (0, n - 1):
            done[k] = 1
            pq.append((h[k], k))
    heapq.heapify(pq)
    pop, push = heapq.heappop, heapq.heappush
    while pq:
        fk, k = pop(pq)
        i, j = k % n, k // n
        for di, dj, _ in _NB:
            ii, jj = i + di, j + dj
            if 0 <= ii < n and 0 <= jj < n:
                q = jj * n + ii
                if not done[q]:
                    done[q] = 1
                    f[q] = max(h[q], fk + EPS)
                    push(pq, (f[q], q))
    return f


def _components(n, cells):
    """4-neighbour groups of the given cell set."""
    left, out = set(cells), []
    while left:
        stack, comp = [left.pop()], []
        while stack:
            k = stack.pop()
            comp.append(k)
            i, j = k % n, k // n
            for q in ((k - 1) if i > 0 else -1, (k + 1) if i < n - 1 else -1,
                      (k - n) if j > 0 else -1, (k + n) if j < n - 1 else -1):
                if q in left:
                    left.remove(q)
                    stack.append(q)
        out.append(comp)
    return out


def hydrology(grid, cfg, noise, maps):
    """Adds flow (downstream cell, -1 = outlet), accum (upstream km2, weighted by height rain), river (width m, 0 = none), river_order
    (Strahler), lake (1 = lake), sea (1 = sea, kept from the coast stage), river_lines ([{pts: [(x, y) cm], order,
    width_cm}]) and lake_count; maps['height'] gets lakes' bed kept, other pits filled, channels and floodplains cut."""
    n, c = grid.n, grid.cell
    nn = n * n
    h = maps['height']
    sea_lvl = float(cfg.get('sea_level_m', 0.0))
    sea = maps.get('sea') or array('d', (1.0 if v <= sea_lvl else 0.0 for v in h))
    outlet = bytearray(1 if v >= 0.5 else 0 for v in sea)
    f = priority_flood(grid, h, outlet)

    # lakes: depressions deeper than 3 m and wider than lakes x 0.5 km2; every other pit is filled flat
    lakes_amt = float(cfg.get('lakes', 0.4))
    min_cells = lakes_amt * 0.5e6 / (c * c)
    lake = array('d', bytes(8 * nn))
    hz = array('d', h)
    nlakes = 0
    for comp in _components(n, [k for k in range(nn) if f[k] - h[k] > 1e-3]):
        if lakes_amt > 0 and len(comp) >= min_cells and max(f[k] - h[k] for k in comp) > LAKE_DEPTH_M:
            nlakes += 1
            for k in comp:
                lake[k] = 1.0
        else:
            for k in comp:
                hz[k] = f[k]

    # D8 steepest descent over the filled surface; outlets (sea, edge) drain off the map
    flow = array('l', [-1]) * nn
    for k in range(nn):
        i, j = k % n, k // n
        if outlet[k] or i in (0, n - 1) or j in (0, n - 1):
            continue
        best, bq, fk = 0.0, -1, f[k]
        for di, dj, dist in _NB:
            q = (j + dj) * n + i + di
            s = (fk - f[q]) / dist
            if s > best:
                best, bq = s, q
        flow[k] = bq

    order = sorted(range(nn), key=f.__getitem__, reverse=True)  # upstream first
    cell_km2 = c * c / 1e6
    # ponytail: rain grows with height (x2 at 500 m), so rivers rise in the mountains; climate's moist map
    # runs after this stage, swap it in if the order ever flips
    accum = array('d', (0.0 if outlet[k] else cell_km2 * (1.0 + max(h[k], 0.0) / 500.0) for k in range(nn)))
    for k in order:
        d = flow[k]
        if d >= 0:
            accum[d] += accum[k]

    rivers_amt = float(cfg.get('rivers', 0.5))
    thr = _threshold_km2(rivers_amt)
    is_r = bytearray(1 if rivers_amt > 0 and accum[k] >= thr and not outlet[k] and not lake[k] else 0
                     for k in range(nn))

    # Strahler order: sources 1; two equal top tributaries meeting -> +1
    strahler = array('d', bytes(8 * nn))
    top = array('l', [0]) * nn
    cnt = array('l', [0]) * nn
    for k in order:
        if not is_r[k]:
            continue
        o = top[k] or 1
        if cnt[k] >= 2:
            o += 1
        strahler[k] = o
        d = flow[k]
        if d >= 0 and is_r[d]:
            if o > top[d]:
                top[d], cnt[d] = o, 1
            elif o == top[d]:
                cnt[d] += 1

    # carve: channel 1.5 m x order below the filled level, floodplain 3 x width flat at bank level
    width = array('d', bytes(8 * nn))
    rcells = [k for k in order if is_r[k]]
    for k in rcells:
        width[k] = 4.0 * strahler[k] ** 1.6
    for k in rcells:
        bank = f[k] + 0.5
        r = min(int(1.5 * width[k] / c), 12)  # ponytail: radius capped at 12 cells, grows only on coarse grids
        i, j = k % n, k // n
        for jj in range(max(j - r, 0), min(j + r, n - 1) + 1):
            for ii in range(max(i - r, 0), min(i + r, n - 1) + 1):
                q = jj * n + ii
                if not is_r[q] and not lake[q] and not outlet[q] and (ii - i) ** 2 + (jj - j) ** 2 <= r * r:
                    if hz[q] > bank:
                        hz[q] = bank
    for k in rcells:
        hz[k] = f[k] - 1.5 * strahler[k]
    for k in rcells:  # upstream first, so a lowered cell passes it on: a channel never climbs on its way out
        d = flow[k]
        if d >= 0 and is_r[d] and hz[d] > hz[k]:
            hz[d] = hz[k]

    maps['height'], maps['flow'], maps['accum'] = hz, flow, accum
    maps['river'], maps['river_order'], maps['lake'], maps['sea'] = width, strahler, lake, sea
    maps['river_lines'] = river_lines(grid, flow, is_r, strahler, width, rcells)
    maps['lake_count'] = nlakes
    return maps


def river_lines(grid, flow, is_r, strahler, width, rcells):
    """One polyline (cm) per reach of constant order, source to junction / mouth; each ends on the next reach's
    first point so they join up."""
    n = grid.n
    xs, ys = grid.xs, grid.ys
    has_up = bytearray(len(flow))
    for k in rcells:
        if flow[k] >= 0:
            has_up[flow[k]] = 1
    seen = bytearray(len(flow))
    starts = [k for k in rcells if not has_up[k]]
    lines = []
    while starts:
        k = starts.pop()
        o, pts = strahler[k], []
        while True:
            pts.append((xs[k % n] * 100.0, ys[k // n] * 100.0))
            if seen[k]:
                break
            seen[k] = 1
            d = flow[k]
            if d < 0 or not is_r[d]:
                break
            if strahler[d] != o:
                if not seen[d]:
                    starts.append(d)
                pts.append((xs[d % n] * 100.0, ys[d // n] * 100.0))
                break
            k = d
        if len(pts) >= 2:
            lines.append({'pts': pts, 'order': int(o), 'width_cm': 400.0 * o ** 1.6})
    return lines

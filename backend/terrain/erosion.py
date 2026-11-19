"""Stage 7: hydraulic (droplet) + thermal erosion on the height map (metres). Volume is kept: every grain a
droplet picks up is put back down somewhere on the map."""
import math
import random
from array import array

INERTIA, CAPACITY, DEPOSIT, ERODE, EVAPORATE, LIFE = 0.05, 4.0, 0.3, 0.3, 0.01, 30
GRAVITY, MIN_CAPACITY = 4.0, 0.01
TALUS = math.tan(math.radians(35.0))

# radius-2 brush: weight = 2 - distance, normalised
_BRUSH = [(di, dj, 2.0 - math.hypot(di, dj)) for dj in range(-2, 3) for di in range(-2, 3) if math.hypot(di, dj) < 2.0]
_BW = sum(w for _, _, w in _BRUSH)
_BRUSH = [(di, dj, w / _BW) for di, dj, w in _BRUSH]


def hydraulic(height, grid, cfg, seed) -> array:
    """Droplets (erosion x n^2 x 0.6 of them) run downhill for up to 30 steps, cutting with a radius-2 brush and
    dropping sediment where they slow. Works in cell units (height / cell) so the constants hold at any cell size."""
    n = grid.n
    amount = float(cfg.get('erosion', 0.5))
    count = int(amount * n * n * 0.6)
    if cfg.get('quality') == 'preview':
        count //= 4  # ponytail: preview is a quarter of the droplets, enough to see the gullies
    s = 1.0 / grid.cell
    h = array('d', (v * s for v in height))
    if count <= 0 or n < 4:
        return array('d', height)
    rnd = random.Random(seed)
    top = n - 1.0
    brush = _BRUSH

    def deposit(px, py, amt):
        i, j = int(px), int(py)
        u, v = px - i, py - j
        k = j * n + i
        h[k] += amt * (1 - u) * (1 - v)
        h[k + 1] += amt * u * (1 - v)
        h[k + n] += amt * (1 - u) * v
        h[k + n + 1] += amt * u * v

    for _ in range(count):
        px, py = rnd.random() * (top - 1e-6), rnd.random() * (top - 1e-6)
        dx = dy = 0.0
        speed, water, sed = 1.0, 1.0, 0.0
        for _step in range(LIFE):
            i, j = int(px), int(py)
            u, v = px - i, py - j
            k = j * n + i
            a, b, c, d = h[k], h[k + 1], h[k + n], h[k + n + 1]
            gx = (b - a) * (1 - v) + (d - c) * v
            gy = (c - a) * (1 - u) + (d - b) * u
            hcur = a * (1 - u) * (1 - v) + b * u * (1 - v) + c * (1 - u) * v + d * u * v
            dx = dx * INERTIA - gx * (1 - INERTIA)
            dy = dy * INERTIA - gy * (1 - INERTIA)
            ln = math.sqrt(dx * dx + dy * dy)
            if ln < 1e-12:
                break
            dx, dy = dx / ln, dy / ln
            nx, ny = px + dx, py + dy
            if not (0.0 <= nx < top and 0.0 <= ny < top):
                break
            ni, nj = int(nx), int(ny)
            nu, nv = nx - ni, ny - nj
            m = nj * n + ni
            hnew = (h[m] * (1 - nu) * (1 - nv) + h[m + 1] * nu * (1 - nv)
                    + h[m + n] * (1 - nu) * nv + h[m + n + 1] * nu * nv)
            dh = hnew - hcur
            cap = max(-dh * speed * water * CAPACITY, MIN_CAPACITY)
            if sed > cap or dh > 0:
                amt = min(dh, sed) if dh > 0 else (sed - cap) * DEPOSIT
                sed -= amt
                deposit(px, py, amt)
            else:
                want = min((cap - sed) * ERODE, -dh)
                if 2 <= i < n - 2 and 2 <= j < n - 2:
                    for di, dj, w in brush:
                        h[k + dj * n + di] -= want * w
                    sed += want
                else:  # edge: the brush cells that are on the map, renormalised
                    cells = [((j + dj) * n + i + di, w) for di, dj, w in brush if 0 <= i + di < n and 0 <= j + dj < n]
                    tw = sum(w for _, w in cells)
                    for q, w in cells:
                        h[q] -= want * w / tw
                    sed += want
            speed = math.sqrt(max(0.0, speed * speed + dh * GRAVITY))
            water *= 1 - EVAPORATE
            px, py = nx, ny
        if sed:
            deposit(px, py, sed)  # what it still carries lands where it stops, so nothing is lost
    c = grid.cell
    return array('d', (v * c for v in h))


def thermal(height, grid, passes: int = 5) -> array:
    """Slopes steeper than 35 deg shed half their excess to the steepest of the 8 neighbours, per pass."""
    n, c = grid.n, grid.cell
    h = array('d', height)
    nbr = [(di, dj, c * math.hypot(di, dj)) for dj in (-1, 0, 1) for di in (-1, 0, 1) if di or dj]
    for _ in range(passes):
        delta = array('d', bytes(8 * n * n))
        for j in range(n):
            for i in range(n):
                k = j * n + i
                hk = h[k]
                best, bq, bd = 0.0, -1, 0.0
                for di, dj, dist in nbr:
                    ii, jj = i + di, j + dj
                    if 0 <= ii < n and 0 <= jj < n:
                        q = jj * n + ii
                        sl = (hk - h[q]) / dist
                        if sl > best:
                            best, bq, bd = sl, q, dist
                if best > TALUS:
                    amt = 0.25 * (best - TALUS) * bd  # half the excess drop, halved again so neighbours don't overshoot
                    delta[k] -= amt
                    delta[bq] += amt
        for k in range(n * n):
            h[k] += delta[k]
    return h


def erode(grid, cfg, noise, maps):
    """Engine stage: maps['height'] -> eroded height. erosion (0..1) sets droplets and thermal passes (3..8)."""
    amount = float(cfg.get('erosion', 0.5))
    h = maps['height']
    if amount > 0:
        h = hydraulic(h, grid, cfg, noise.seed)
        h = thermal(h, grid, 3 + int(round(5 * min(amount, 1.0))))
    maps['height'] = h
    return maps

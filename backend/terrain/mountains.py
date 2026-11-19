"""Stage 4, mountains: a ridge network (spine, side spurs, peaks) per connected mountain region, then ridge height.

Units are metres. A spine follows the region's principal axis (PCA of its cells), bent to the region's own centre
line and wiggled by noise; branches leave it every 1.5-3 km at 35-70 degrees and shorten toward the ends.
The tallest peak is mountain_m; mountain_intensity only shapes the ranges (width, crags, how many peaks near the top)."""
import math
import random
from array import array
from collections import deque

from .grid import smoothstep

_ON = 0.5          # mask value that counts as mountain
TAPER_M = 1500.0   # a range fades to nothing over this far inside its region's edge (foothills, never a wall)
TAPER_SHARE = 0.6  # ... and over at most this share of the region's depth
ROOM_M = 1000.0    # foothill run outside a region that a range's flanks may use
RISE = 0.8         # a range is at most RISE x (its region's depth + ROOM_M) tall: a 2.5 km cone on a 3 km island is no range
VALLEY = 0.3       # how deep drainage cuts the valleys (share of their height per run), + 0.25 x intensity
APRON_M = 1500.0   # the foothill apron reaches this far past a range's half-width
APEX = 1.0         # steepest flank just under a crest (rise / run, 45 degrees)
SPURS = 0.15       # spur spacing, share of the widest ridge half-width
GAUSS = 0.4        # the ridge's cross profile: a Gaussian with sigma = GAUSS x the half-width
BODY = 0.3         # valley floors between side ridges sit at this share of the envelope
KEEP = (0.3, 1.0)  # spur noise fades out from this share of the top to the summit (smoothstep, a wide band: a
                   # narrow one cut a cliff ring round every summit, a crater once hydrology filled it)
_MIN_CELLS = 4     # smaller specks get no range


def _intensity(cfg) -> float:
    return min(max(float(cfg.get('mountain_intensity', cfg.get('intensity', 0.7))), 0.0), 1.0)


def _height(cfg) -> float:
    return min(max(float(cfg.get('mountain_m', 1200.0)), 0.0), 2500.0)


def _width(cfg) -> float:
    """Ridge half-width: ridge_width_m when given, else 3 km at intensity 0 down to 1 km at 1 (narrower = sharper)."""
    w = cfg.get('ridge_width_m', 3000.0 - 1000.0 * _intensity(cfg))
    return min(max(float(w), 1000.0), 3000.0)


def _sharp(inten) -> float:
    """(1 - d/w)^sharp: a pointed crest, flanks concave toward the foot; higher = sharper."""
    return 1.3 + 0.5 * inten


def _regions(grid, mask):
    """Connected (4-neighbour) groups of cells with mask >= _ON, as lists of cell indices."""
    n = grid.n
    seen = bytearray(n * n)
    out = []
    for k0 in range(n * n):
        if seen[k0] or mask[k0] < _ON:
            continue
        seen[k0] = 1
        q, cells = deque([k0]), []
        while q:
            k = q.popleft()
            cells.append(k)
            i, j = k % n, k // n
            for ii, jj in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)):
                if 0 <= ii < n and 0 <= jj < n:
                    kk = jj * n + ii
                    if not seen[kk] and mask[kk] >= _ON:
                        seen[kk] = 1
                        q.append(kk)
        if len(cells) >= _MIN_CELLS:
            out.append(cells)
    return out


def _inside(grid, mask, x, y) -> bool:
    i, j = int((x - grid.x0) / grid.cell), int((y - grid.y0) / grid.cell)
    return 0 <= i < grid.n and 0 <= j < grid.n and mask[j * grid.n + i] >= _ON


def _spine(grid, noise, cells, step):
    """Polyline [(x, y)] along the region's principal axis, through its per-slice centre, plus the half-width."""
    n, xs, ys = grid.n, grid.xs, grid.ys
    pts = [(xs[k % n], ys[k // n]) for k in cells]
    m = len(pts)
    cx = sum(p[0] for p in pts) / m
    cy = sum(p[1] for p in pts) / m
    sxx = sum((p[0] - cx) ** 2 for p in pts) / m
    syy = sum((p[1] - cy) ** 2 for p in pts) / m
    sxy = sum((p[0] - cx) * (p[1] - cy) for p in pts) / m
    ang = 0.5 * math.atan2(2 * sxy, sxx - syy)  # principal axis of the covariance
    ax, ay = math.cos(ang), math.sin(ang)
    bx, by = -ay, ax
    bins = {}
    for x, y in pts:
        t = (x - cx) * ax + (y - cy) * ay
        s = (x - cx) * bx + (y - cy) * by
        bins.setdefault(int(math.floor(t / step)), []).append(s)
    keys = sorted(bins)
    # drop thin tips (under a third of the median slice) so the spine ends inside the range, not on a sliver
    counts = sorted(len(bins[b]) for b in keys)
    floor_n = max(1, counts[len(counts) // 2] // 3)
    keys = [b for b in keys if len(bins[b]) >= floor_n] or keys
    mids = [sum(bins[b]) / len(bins[b]) for b in keys]
    halfw = [(max(bins[b]) - min(bins[b])) * 0.5 for b in keys]
    sm = [sum(mids[max(0, i - 2):i + 3]) / len(mids[max(0, i - 2):i + 3]) for i in range(len(mids))]
    line = []
    for b, s, hw in zip(keys, sm, halfw):
        t = (b + 0.5) * step
        x0, y0 = cx + ax * t + bx * s, cy + ay * t + by * s
        s += noise.fbm(x0, y0, 3, scale=6000.0) * 0.3 * hw  # wiggle, kept to the slice's own width
        line.append((cx + ax * t + bx * s, cy + ay * t + by * s))
    return line, sorted(halfw)[len(halfw) // 2]


def _peakline(noise, line, top, inten):
    """Crest height along the spine, max == top. Intensity 0: one rounded hump (a single summit near top);
    1: a flat envelope with strong short noise, so many jagged peaks come close to top."""
    m = len(line)
    if m == 1:
        return [top]
    hs = []
    # several summits and saddles along every crest: a lone hump was the cone people saw
    e, amp, sc = 0.9 - 0.6 * inten, 0.3 + 0.25 * inten, 2500.0 - 1200.0 * inten
    for i, (x, y) in enumerate(line):
        u = (i + 0.5) / m
        env = math.sin(math.pi * u) ** e
        hs.append(env * (1.0 - amp + amp * noise.fbm(x, y, 2, scale=sc)))  # 2 octaves: a 3rd made one-point spikes
    hi = max(hs) or 1.0
    return [h / hi * top for h in hs]


def ridge_network(grid, cfg, noise, mountains_mask):
    """{spines: [[(x, y, h)]], branches: [[(x, y, h)]], peaks: [(x, y, h)]} for every mountain region.
    The biggest region's top is mountain_m (intensity shapes it, never scales it); smaller ones scale by sqrt of
    their size (at least half). mountain_m 0: no ranges."""
    top = _height(cfg)
    inten = _intensity(cfg)
    step = max(2.0 * grid.cell, 150.0)
    regions = _regions(grid, mountains_mask)
    out = {'spines': [], 'branches': [], 'peaks': [], 'widths': [], 'bwidths': []}
    w0 = _width(cfg)
    if not regions or top <= 0:
        return out
    big = max(len(r) for r in regions)
    inside = grid.dist_to(array('d', (1.0 - m for m in mountains_mask)))
    for ri, cells in enumerate(regions):
        rtop = top * max(0.5, math.sqrt(len(cells) / big))
        rtop = min(rtop, RISE * (max(inside[k] for k in cells) + ROOM_M))
        line, halfw = _spine(grid, noise, cells, step)
        hs = _peakline(noise, line, rtop, inten)
        spine = [(x, y, h) for (x, y), h in zip(line, hs)]
        out['spines'].append(spine)
        # a range no wider than its region (x 1.3, the foothills take the rest): a 2.5 km half-width over a
        # 1 km wide region made the whole island one cone
        rw = min(w0, max(halfw * 1.3, 6.0 * grid.cell))  # under ~6 cells the summit is a spike erosion chops off
        # and wide enough that the flank just under the crest is at most APEX (rise / run): steeper, erosion planed
        # a third off every summit
        rw = max(rw, _sharp(inten) * rtop / APEX)
        out['widths'].append(rw)
        # peaks: crest maxima within ~1.5 km either side, above half the range top
        win = max(1, int(1500.0 / step))
        for i, (x, y, h) in enumerate(spine):
            near = hs[max(0, i - win):i + win + 1]
            if h >= max(near) and h >= 0.5 * rtop:
                out['peaks'].append((x, y, h))
        # branches: alternate sides, 1.5-3 km apart, 35-70 degrees off the spine, shorter toward the ends
        rng = random.Random(noise.seed * 7919 + ri)
        length = (len(spine) - 1) * step
        if length <= 0:
            continue
        side, s = 1, rng.uniform(0.75, 1.5) * 1000.0
        while s < length:
            i = min(int(s / step), len(spine) - 2)
            x, y, h = spine[i]
            tx, ty = spine[i + 1][0] - x, spine[i + 1][1] - y
            a = math.atan2(ty, tx) + side * math.radians(rng.uniform(35.0, 70.0))
            u = s / length
            blen = max(halfw * 1.8, 600.0) * (1.0 - 0.7 * abs(2 * u - 1))
            br, segs = [(x, y, h * 0.85)], 4
            for q in range(1, segs + 1):
                f = q / segs
                bend = noise.fbm(x + q * 311.0, y, 2, scale=2000.0) * 0.35
                bx = x + math.cos(a + bend) * blen * f
                by = y + math.sin(a + bend) * blen * f
                if not _inside(grid, mountains_mask, bx, by):
                    break
                br.append((bx, by, h * (0.85 - 0.5 * f)))
            if len(br) > 1:
                out['branches'].append(br)
                out['bwidths'].append(rw * 0.6)
            side = -side
            s += rng.uniform(1500.0, 3000.0)
    return out


CARVE_PASSES = 12


def _carve(grid, height, cells, amp, ref_cells):
    """Stream-power valleys, in place: each pass sends every cell to its lowest neighbour, adds up the cells
    draining through it, and lowers it by amp / passes of its height x sqrt(drainage / ref_cells) (capped at 1).
    Channels that gather more water cut deeper and pull in more, so spurs stand between drained valleys. A cell
    never goes below its downhill neighbour, so no pit is made (the hydrology stage fills pits flat)."""
    n, c = grid.n, grid.cell
    nb = [(di, dj, c * math.hypot(di, dj)) for dj in (-1, 0, 1) for di in (-1, 0, 1) if di or dj]
    rate = amp / CARVE_PASSES
    for _ in range(CARVE_PASSES):
        recv = {}
        for k in cells:
            i, j, hk = k % n, k // n, height[k]
            best, bq = 0.0, -1
            for di, dj, dist in nb:
                ii, jj = i + di, j + dj
                if 0 <= ii < n and 0 <= jj < n:
                    q = jj * n + ii
                    s = (hk - height[q]) / dist
                    if s > best:
                        best, bq = s, q
            if bq >= 0:
                recv[k] = (bq, c * math.hypot((bq % n) - i, (bq // n) - j))
        acc = dict.fromkeys(cells, 1.0)
        for k in sorted(cells, key=height.__getitem__, reverse=True):
            r = recv.get(k)
            if r and r[0] in acc:
                acc[r[0]] += acc[k]
        for k in sorted(cells, key=height.__getitem__):  # downstream first: a receiver is final before its donors
            r = recv.get(k)
            if not r:
                continue
            cut = height[k] * rate * min(1.0, math.sqrt(acc[k] / ref_cells))
            height[k] = max(height[k] - cut, height[r[0]] + 1e-3 * r[1])


def ridge_height(grid, cfg, noise, mountains_mask, net=None):
    """(height, strength) arrays. height = peakline x falloff(distance to the ridge polylines) + ridged noise
    (0.1 + 0.3 x intensity share), all x mask, top scaled to the network's highest peak. strength = 0..1 nearness."""
    net = net if net is not None else ridge_network(grid, cfg, noise, mountains_mask)
    n, c, xs, ys = grid.n, grid.cell, grid.xs, grid.ys
    height, strength = grid.new(), grid.new()
    if not net['spines']:
        return height, strength
    inten = _intensity(cfg)
    w0 = _width(cfg)
    e_w = math.exp(-0.5 / GAUSS ** 2)  # the Gaussian at the half-width, taken off so the ridge ends at 0 there
    segs = []
    for lines, ws in ((net['spines'], net.get('widths')), (net['branches'], net.get('bwidths'))):
        for li, line in enumerate(lines):
            w = ws[li] if ws else w0
            if len(line) == 1:
                segs.append(line[0] + line[0] + (w,))
            for a, b in zip(line, line[1:]):
                segs.append(a + b + (w,))
    for x1, y1, h1, x2, y2, h2, w in segs:
        dx, dy = x2 - x1, y2 - y1
        ll = dx * dx + dy * dy
        i0 = max(0, int((min(x1, x2) - w - grid.x0) / c))
        i1 = min(n - 1, int((max(x1, x2) + w - grid.x0) / c))
        j0 = max(0, int((min(y1, y2) - w - grid.y0) / c))
        j1 = min(n - 1, int((max(y1, y2) + w - grid.y0) / c))
        for j in range(j0, j1 + 1):
            y, row = ys[j], j * n
            for i in range(i0, i1 + 1):
                k = row + i
                if mountains_mask[k] <= 0.0:
                    continue
                x = xs[i]
                t = min(max(((x - x1) * dx + (y - y1) * dy) / ll, 0.0), 1.0) if ll else 0.0
                d = math.hypot(x - x1 - dx * t, y - y1 - dy * t)
                if d >= w:
                    continue
                # a Gaussian across the ridge: a rounded crest, the steepest flank a third of the way down, a foot
                # that flares into the foothills. (1 - d/w)^sharp was nearly straight: every segment a flat-faced
                # tent meeting the next in one needle summit; smoothstep made the whole range a dome
                f = (math.exp(-0.5 * (d / (GAUSS * w)) ** 2) - e_w) / (1.0 - e_w)
                v = (h1 + (h2 - h1) * t) * f
                if v > height[k]:
                    height[k] = v
                if f > strength[k]:
                    strength[k] = f
    top = max(p[2] for line in net['spines'] for p in line)
    # the range fades out over TAPER_M inside its region's edge (and the coast): the mask goes 0 -> 1 in one cell,
    # and multiplying by it stood every range on vertical walls
    # ... over at most TAPER_SHARE of the region's depth, and blurred: on a small region (a 6 km island's one range) a
    # TAPER_M ramp covered nearly all of it, and the mountain took the distance field's shape, flat faces meeting
    # in straight creases with a round plateau where the ramp topped out
    inside = grid.dist_to(array('d', (1.0 - m for m in mountains_mask)))
    tw = max(2.0 * c, min(TAPER_M, w0, TAPER_SHARE * max(inside)))
    inside = grid.blur(inside, max(1, int(0.5 * tw / c)))
    taper = [smoothstep(0.0, tw, d) for d in inside]
    wm = max(net.get('widths') or [w0])
    # spur spacing: several down every flank (at 0.35 x the width one swell spanned a flank, and the envelope's
    # planes showed through as a tent)
    sc = max(wm * SPURS, 5.0 * c)
    cells = []
    for k in range(n * n):
        if strength[k] <= 0.0:
            continue
        # the body is warped ridged noise under the ridge envelope: side ridges and the valleys between them at
        # several scales (the envelope alone is a smooth tent); full height kept toward the crest line
        wx, wy = noise.warp(xs[k % n], ys[k // n], sc * 0.6, sc * 2.5, 2)
        r = noise.ridged(wx + 913.0, wy - 377.0, 5, scale=sc * 2.0)
        u = min(height[k] / top, 1.0)
        # the summit keeps the envelope whole (noise right under it left a one-cell needle erosion chopped); the
        # noise fades in down the flanks, never switched on at one height
        s = smoothstep(KEEP[0], KEEP[1], u)
        body = BODY + (1.0 - BODY) * (r + (1.0 - r) * s)
        height[k] *= body * taper[k]
        cells.append(k)
    before = array('d', height)
    _carve(grid, height, cells, VALLEY + 0.25 * inten, (sc / c) ** 2)
    # the cut, blurred over ~80 m: flow to one of 8 neighbours cuts one-cell gullies along the 8 compass lines, a
    # star round every summit at fine cells; blurred they are valleys with sides
    cut = grid.blur(array('d', (b - a for a, b in zip(height, before))), max(1, int(80.0 / c)))
    for k in cells:
        height[k] = max(0.0, before[k] - cut[k])
    hi = max(height)
    if hi > 0:
        s = top / hi
        for k in range(n * n):
            height[k] *= s
    return height, strength


def _xy(c):
    return (float(c['x']), float(c['y'])) if isinstance(c, dict) else (float(c[0]), float(c[1]))


def _cross(a, b, c, d):
    """Point where segment ab crosses segment cd, else None."""
    rx, ry, sx, sy = b[0] - a[0], b[1] - a[1], d[0] - c[0], d[1] - c[1]
    den = rx * sy - ry * sx
    if abs(den) < 1e-9:
        return None
    t = ((c[0] - a[0]) * sy - (c[1] - a[1]) * sx) / den
    u = ((c[0] - a[0]) * ry - (c[1] - a[1]) * rx) / den
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return a[0] + rx * t, a[1] + ry * t
    return None


_ALL_PAIRS_MAX = 12  # up to 12 cities every pair may get a highway (66 links); more: each city's 3 nearest


def _pairs(pts):
    m = len(pts)
    if m <= _ALL_PAIRS_MAX:
        return [(a, b) for a in range(m) for b in range(a + 1, m)]
    # ponytail: k-nearest links past 12 cities; feed the real highway plan in if it links farther pairs
    out = set()
    for a in range(m):
        near = sorted((b for b in range(m) if b != a),
                      key=lambda q: math.hypot(pts[q][0] - pts[a][0], pts[q][1] - pts[a][1]))[:3]
        out.update((min(a, b), max(a, b)) for b in near)
    return sorted(out)


def passes(grid, network, cities, height=None, depth=0.42, reach=5000.0, notch=1500.0, across=3000.0, merge=2000.0):
    """Saddles where city links (every pair up to 12 cities) cross a spine. For each crossing, the lowest spine point
    within `reach` m is notched to `depth` x the local ridge top over a `notch` m wide cut (across the whole ridge);
    a crossing with a pass already within `merge` m of its saddle and `reach` m of the crossing reuses it.
    Lowers `height` in place when given. Returns (passes [(x, y, h)], passes map 0..1). Cities: (x, y) or {x, y}."""
    pmap = grid.new()
    pts = [_xy(c) for c in cities]
    pairs = _pairs(pts)
    out = []
    n, xs, ys = grid.n, grid.xs, grid.ys
    for a, b in pairs:
        for spine in network['spines']:
            hit = None
            for q in range(len(spine) - 1):
                hit = _cross(pts[a], pts[b], spine[q], spine[q + 1])
                if hit:
                    break
            if not hit:
                continue
            near = [i for i, p in enumerate(spine) if math.hypot(p[0] - hit[0], p[1] - hit[1]) <= reach]
            crest = [grid.sample(height, spine[i][0], spine[i][1]) if height is not None else spine[i][2] for i in near]
            low = near[min(range(len(near)), key=crest.__getitem__)]
            top = max(crest)
            px, py = spine[low][0], spine[low][1]
            if any(math.hypot(qx - px, qy - py) <= merge and math.hypot(qx - hit[0], qy - hit[1]) <= reach
                   for qx, qy, _ in out):
                continue
            h = depth * top
            # local spine direction at the saddle: the notch runs across it
            p0, p1 = spine[max(low - 1, 0)], spine[min(low + 1, len(spine) - 1)]
            tx, ty = p1[0] - p0[0], p1[1] - p0[1]
            tl = math.hypot(tx, ty) or 1.0
            tx, ty = tx / tl, ty / tl
            half, c = notch * 0.5, grid.cell
            r = math.hypot(half, across)
            for j in range(max(0, int((py - r - grid.y0) / c)), min(n - 1, int((py + r - grid.y0) / c)) + 1):
                for i in range(max(0, int((px - r - grid.x0) / c)), min(n - 1, int((px + r - grid.x0) / c)) + 1):
                    dx, dy = xs[i] - px, ys[j] - py
                    w = smoothstep(half, 0.0, abs(dx * tx + dy * ty)) * smoothstep(across, across * 0.6, abs(dx * ty - dy * tx))
                    if w <= 0.0:
                        continue
                    k = j * n + i
                    if w > pmap[k]:
                        pmap[k] = w
                    if height is not None and height[k] > h:
                        height[k] -= (height[k] - h) * w
            out.append((px, py, h))
    return out, pmap


def stage(grid, cfg, noise, maps):
    """Adds maps['ridge'] (mountain height, m), maps['ridge_strength'] (0..1), maps['ridge_net'] and maps['passes']
    (0..1, notched where nearest-city links cross a spine; cities from maps['cities'] as (x, y) metres)."""
    net = ridge_network(grid, cfg, noise, maps['mountains'])
    maps['ridge'], maps['ridge_strength'] = ridge_height(grid, cfg, noise, maps['mountains'], net)
    # apron 0..1: falls steadily from the ridge lines out past the range into its foothills; the plinth under the
    # range follows it (built from region masks it had a moat at the region edge, filled flat by hydrology)
    d = grid.dist_to(array('d', (1.0 if s >= 0.9 else 0.0 for s in maps['ridge_strength'])))
    reach = max(net['widths'] or [_width(cfg)]) + APRON_M
    # the distance is wobbled by noise at two scales: a bare distance field is flat-faced (planes meeting in creases)
    wob = grid.field(lambda x, y: noise.fbm(x, y - 211.0, 2, scale=reach * 0.6)
                     + 0.4 * noise.fbm(x + 57.0, y, 2, scale=reach * 0.15), reach * 0.03)
    ap = array('d', (smoothstep(reach, 0.0, v * max(0.3, 1.0 + 0.9 * w)) for v, w in zip(d, wob)))
    maps['apron'] = grid.blur(ap, max(1, int(300.0 / grid.cell)))  # and blurred: its creases still showed
    net['passes'], maps['passes'] = passes(grid, net, maps.get('cities', []), maps['ridge'])
    maps['ridge_net'] = net
    return maps

"""Stage 2: region masks. Soft 0..1 masks that sum to <= land per cell:
mountains, foothills, hills, plateaus, valleys, wetlands and plains (the rest).

Every island is ranked on its own (one ranking over the whole map put every range on the one island the noise
favoured and left the rest flat). Each island gets a mountain share from its size and a seeded roll (tiny ones
none, big ones likely), scaled so they add up to mountain_cover of the land; hills and plateaus get their share
on every island, and an island without its share of mountains gets half of it back as hills.

Shares: mountains = mountain_cover of the land (kept off the beach, placed by noise); plains keep at least
flatland x (1 - mountain_cover) of the land; hills = hills x (1 - flatland) and plateaus = plateaus x (1 - flatland)
of the land left after mountains and foothills, cut down to what the plains leave over. The 3 km foothill band
narrows when it would leave the plains short (a small map is not all foothills)."""
import random
from array import array
from collections import deque

from .grid import quantile, smoothstep

BAND = 0.02        # soft edge of a ranked mask, in score units (mask = 0.5 exactly at the share's threshold)
FOOTHILL_M = 3000.0
SIDE_MAX = 0.45    # valleys + wetlands never take more than this of a plains cell, so it stays plains
PLAINS_MARGIN = 0.04  # extra land kept for plains: soft mask edges count for nobody
TINY_KM2 = 0.3     # an island under this gets no range: rocks and hills
RANGE_KM2 = 6.0    # ... from this size on a range is (nearly) sure
ISLAND_MTN = 0.6   # at most this share of one island is mountain (unless mountain_cover asks for more)


def islands(grid, land):
    """(id per cell, -1 off the land; the cells of each island, biggest first). Land = land >= 0.5, 4-neighbour."""
    n = grid.n
    seen = bytearray(n * n)
    groups = []
    for k0 in range(n * n):
        if seen[k0] or land[k0] < 0.5:
            continue
        seen[k0] = 1
        q, cells = deque([k0]), []
        while q:
            k = q.popleft()
            cells.append(k)
            i, j = k % n, k // n
            for kk in ((k - 1) if i else -1, (k + 1) if i < n - 1 else -1, k - n if j else -1, k + n if j < n - 1 else -1):
                if kk >= 0 and not seen[kk] and land[kk] >= 0.5:
                    seen[kk] = 1
                    q.append(kk)
        groups.append(cells)
    groups.sort(key=len, reverse=True)
    iid = array('i', [-1]) * (n * n)
    for g, cells in enumerate(groups):
        for k in cells:
            iid[k] = g
    return iid, groups


def mountain_shares(groups, cell_m2, cover, seed):
    """Mountain share of each island: suitability from its area (0 under TINY_KM2, 1 from RANGE_KM2), a seeded
    roll whether it gets a range at all (a medium island may not, a big one nearly always does) and how much,
    then one scale for all so the ranges add up to `cover` of the land (each capped at ISLAND_MTN)."""
    rng = random.Random(seed * 104729 + 17)
    weights = []
    for cells in groups:
        suit = smoothstep(TINY_KM2, RANGE_KM2, len(cells) * cell_m2 / 1e6)
        has, amount = rng.random(), rng.random()  # always both: one island's roll never shifts the next one's
        weights.append(suit * (0.5 + amount) if suit > 0 and has < 0.2 + suit else 0.0)
    cap = max(ISLAND_MTN, min(cover, 0.95))
    want = cover * sum(len(c) for c in groups)
    out = [0.0] * len(groups)
    if want <= 0 or not any(weights):
        return out
    lo, hi = 0.0, cap / min(w for w in weights if w > 0)
    for _ in range(40):  # one scale for every island; capped islands pass what they can't take to the others
        mid = (lo + hi) * 0.5
        got = sum(min(cap, mid * w) * len(c) for w, c in zip(weights, groups))
        lo, hi = (mid, hi) if got < want else (lo, mid)
    return [min(cap, lo * w) for w in weights]


def _ranked_in(score, groups, shares, n2, floor=-1e9):
    """Soft mask per group of cells: 1 on the top `share` of the group by score, 0 below, a BAND-wide edge; 0
    outside the groups. floor: the threshold never goes under it (a score of 0 = not allowed there)."""
    out = array('d', bytes(8 * n2))
    for cells, share in zip(groups, shares):
        if share <= 0 or not cells:
            continue
        th = max(quantile([score[k] for k in cells], 1.0 - min(share, 1.0)), floor)
        for k in cells:
            out[k] = smoothstep(th - BAND, th + BAND, score[k])
    return out


def region_masks(grid, cfg, noise, maps):
    land, cd = maps['land'], maps['coast_dist']
    size, n2 = grid.size, grid.n * grid.n
    big = max(size * 0.3, 2000.0)  # (4 km on a 6 km map: one blob of mountain score for the whole map)
    flat = cfg.get('flatland', 0.5)
    cover = cfg.get('mountain_cover', 0.2)

    def mtn_score(x, y):
        wx, wy = noise.warp(x, y, big * 0.35, big, 2)
        return (noise.fbm(wx / big + 101.3, wy / big - 57.1, 4) + 1.0) * 0.5

    ms = grid.field(mtn_score, big / 16)
    iid, groups = islands(grid, land)
    # inland only as a gate: off the coastal plain (2..4 km from the sea, shrunk to the island's own depth), then
    # the noise alone picks where. Ranked by distance from the sea every island got one cone on its middle.
    deep = [max(max((cd[k] for k in cells), default=0.0), grid.cell) for cells in groups]
    score = grid.new()
    for g, cells in enumerate(groups):
        lo, hi = min(2000.0, deep[g] * 0.1), min(4000.0, deep[g] * 0.3)
        for k in cells:
            score[k] = ms[k] * smoothstep(lo, hi, cd[k])
    on_land = [k for k in range(n2) if land[k] >= 0.5]
    n_land = max(len(on_land), 1)
    # a share of the LAND, dealt out island by island
    m_sh = mountain_shares(groups, grid.cell * grid.cell, cover, noise.seed)
    rk = _ranked_in(score, groups, m_sh, n2, floor=1e-9)
    mtn = array('d', (rk[k] * land[k] for k in range(n2)))
    # an island short of its share of ranges takes half the difference as hills: no island is left all flat
    h_extra = [max(0.0, cover - s) * 0.5 for s in m_sh]

    dm = grid.dist_to(mtn)
    want = flat * (1.0 - cover)                     # plains' share of the land, at least
    band = FOOTHILL_M
    for _ in range(6):  # the apron narrows (to ~size / 8 on a small map) while it leaves the plains short
        # cubed falloff: the main mask only in its first ~30 %, the plains keep the rest
        foot = array('d', ((1.0 - mtn[k]) * (1.0 - smoothstep(0.0, band, dm[k])) ** 3 * land[k] for k in range(n2)))
        rest = array('d', (max(0.0, land[k] - mtn[k] - foot[k]) for k in range(n2)))
        free = [k for k in range(n2) if rest[k] >= 0.5]
        avail = len(free) / n_land                  # share of the land not in a range or its apron
        if avail >= want + PLAINS_MARGIN * 2:
            break
        band *= 0.6

    hsc = max(size * 0.12, 2500.0)
    hs = grid.field(lambda x, y: noise.fbm(x / hsc - 23.9, y / hsc + 88.2, 3), hsc / 6)
    psc = max(size * 0.1, 2000.0)
    ps = grid.field(lambda x, y: noise.fbm(x / psc + 61.4, y / psc + 19.6, 3), psc / 6)
    margin = PLAINS_MARGIN
    free_in = [[k for k in cells if rest[k] >= 0.5] for cells in groups]
    for _ in range(5):  # soft edges count for nobody: measure what plains got, give hills / plateaus less, again
        spare = max(0.0, avail - want - margin) / max(avail, 1e-9)  # of free: what plains can do without
        h_share = min(cfg.get('hills', 0.5) * (1.0 - flat), spare)
        rk = _ranked_in(hs, free_in, [min(0.9, h_share + e) if h_share > 0 else 0.0 for e in h_extra], n2)
        hills = array('d', (min(rk[k], rest[k]) for k in range(n2)))  # min: >= 0.5 on the share
        p_share = min(cfg.get('plateaus', 0.1) * (1.0 - flat), max(0.0, spare - h_share))
        left_in = [[k for k in cells if hills[k] < 0.5] for cells in free_in]
        rk = _ranked_in(ps, left_in, [p_share * len(f) / max(len(lf), 1) for f, lf in zip(free_in, left_in)], n2)
        plat = array('d', (min(rk[k], max(0.0, rest[k] - hills[k])) for k in range(n2)))
        plains = array('d', (max(0.0, rest[k] - hills[k] - plat[k]) for k in range(n2)))
        short = want - sum(1 for k in on_land if plains[k] >= 0.5) / n_land
        if short <= 0 or h_share + p_share <= 0:
            break
        margin += short * 1.5 + 0.005
    maps['region_targets'] = {'mountains_map': cover, 'hills_free': h_share, 'plateaus_free': p_share,
                              'plains_land': want}
    # valleys: the low ground between and beside ranges (the range score dips under its own surroundings)
    sm = grid.blur(ms, max(1, int(3000.0 / grid.cell)))
    near = [1.0 - smoothstep(4000.0, 10000.0, d) for d in dm]
    vall = array('d', (min(max((sm[k] - ms[k]) / 0.04, 0.0), 1.0) * near[k] for k in range(n2)))
    wsc = max(size * 0.05, 800.0)
    wet = grid.field(lambda x, y: noise.fbm(x / wsc + 3.3, y / wsc - 71.8, 2), wsc / 4)
    wetl = array('d', ((1.0 - smoothstep(200.0, 1500.0, cd[k])) * smoothstep(-0.05, 0.2, wet[k])
                       if cd[k] > 0 else 0.0 for k in range(n2)))
    for k in range(n2):
        p = plains[k]
        w = SIDE_MAX * wetl[k] * p
        v = SIDE_MAX * max(0.0, vall[k] - wetl[k]) * p
        room = p - 0.5 if p >= 0.5 else p  # a plains cell stays plains
        if w + v > room:
            s = max(room, 0.0) / (w + v)
            w, v = w * s, v * s
        wetl[k], vall[k], plains[k] = w, v, p - w - v
    maps.update(mountains=mtn, foothills=foot, hills=hills, plateaus=plat, plains=plains,
                valleys=vall, wetlands=wetl, mountain_score=score,
                island=array('d', (v + 1.0 for v in iid)))  # 0 = sea, 1 = the biggest island, ...
    return maps


def island_stats(grid, maps) -> list:
    """Per island (biggest first): area, highest point, region shares and a one-word profile, for the generate
    report and the reason one island is flat and another has a range."""
    isl, h = maps.get('island'), maps.get('height')
    if isl is None:
        return []
    by: dict = {}
    for k, v in enumerate(isl):
        if v >= 1:
            by.setdefault(int(v), []).append(k)
    out = []
    for g in sorted(by):
        cells = by[g]
        sh = {name: round(sum(1 for k in cells if maps[name][k] >= 0.5) / len(cells), 3)
              for name in ('mountains', 'foothills', 'hills', 'plateaus', 'plains') if name in maps}
        m = sh.get('mountains', 0.0)
        profile = ('mountain range' if m >= 0.15 else 'peak and hills' if m > 0 else
                   'rolling hills' if sh.get('hills', 0.0) >= 0.3 else
                   'highlands' if sh.get('plateaus', 0.0) >= 0.2 else 'lowland')
        out.append({'id': g, 'area_km2': round(len(cells) * grid.cell * grid.cell / 1e6, 3),
                    'top_m': round(max(h[k] for k in cells), 1) if h is not None else None,
                    'profile': profile, 'shares': sh})
    return out


def shares(maps, keys=('mountains', 'foothills', 'hills', 'plateaus', 'plains', 'valleys', 'wetlands')) -> dict:
    """Share of the land cells where each mask is the main one (>= 0.5)."""
    land = maps['land']
    on = [k for k in range(len(land)) if land[k] >= 0.5] or [0]
    return {name: sum(1 for k in on if maps[name][k] >= 0.5) / len(on) for name in keys if name in maps}

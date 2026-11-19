"""Stage 2: region masks. Soft 0..1 masks that sum to <= land per cell:
mountains, foothills, hills, plateaus, valleys, wetlands and plains (the rest).

Shares: mountains = mountain_cover of the whole map, all on land (biased inland); plains keep at least
flatland x (1 - mountain_cover) of the land; hills = hills x (1 - flatland) and plateaus = plateaus x (1 - flatland)
of the land left after mountains and foothills, cut down to what the plains leave over. The 3 km foothill band
narrows when it would leave the plains short (a small map is not all foothills)."""
from array import array

from .grid import quantile, smoothstep

BAND = 0.02        # soft edge of a ranked mask, in score units (mask = 0.5 exactly at the share's threshold)
FOOTHILL_M = 3000.0
SIDE_MAX = 0.45    # valleys + wetlands never take more than this of a plains cell, so it stays plains
PLAINS_MARGIN = 0.04  # extra land kept for plains: soft mask edges count for nobody


def _ranked(score, cells, share, floor=-1e9):
    """Soft mask: 1 on the top `share` of `cells` by score, 0 below, a BAND-wide edge. floor: the threshold never
    goes under it (a score of 0 = not allowed there)."""
    if share <= 0 or not cells:
        return None
    th = max(quantile([score[k] for k in cells], 1.0 - share), floor)
    return array('d', (smoothstep(th - BAND, th + BAND, s) for s in score))


def region_masks(grid, cfg, noise, maps):
    land, cd = maps['land'], maps['coast_dist']
    size, n2 = grid.size, grid.n * grid.n
    big = max(size * 0.3, 4000.0)
    flat = cfg.get('flatland', 0.5)
    cover = cfg.get('mountain_cover', 0.2)

    def mtn_score(x, y):
        wx, wy = noise.warp(x, y, big * 0.35, big, 2)
        return (noise.fbm(wx / big + 101.3, wy / big - 57.1, 4) + 1.0) * 0.5

    ms = grid.field(mtn_score, big / 16)
    score = array('d', (ms[k] * smoothstep(2000.0, 8000.0, cd[k]) for k in range(n2)))  # inland
    on_land = [k for k in range(n2) if land[k] >= 0.5]
    n_land = max(len(on_land), 1)
    rk = _ranked(score, on_land, min(cover * n2 / n_land, 0.95), floor=1e-9)
    mtn = array('d', (rk[k] * land[k] for k in range(n2))) if rk else grid.new()

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
    for _ in range(5):  # soft edges count for nobody: measure what plains got, give hills / plateaus less, again
        spare = max(0.0, avail - want - margin) / max(avail, 1e-9)  # of free: what plains can do without
        h_share = min(cfg.get('hills', 0.5) * (1.0 - flat), spare)
        rk = _ranked(hs, free, h_share)
        hills = array('d', (min(rk[k], rest[k]) for k in range(n2))) if rk else grid.new()  # min: >= 0.5 on the share
        left = [k for k in free if hills[k] < 0.5]
        p_share = min(cfg.get('plateaus', 0.1) * (1.0 - flat), max(0.0, spare - h_share))
        rk = _ranked(ps, left, min(p_share * len(free) / max(len(left), 1), 1.0))
        plat = array('d', (min(rk[k], max(0.0, rest[k] - hills[k])) for k in range(n2))) if rk else grid.new()
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
                valleys=vall, wetlands=wetl, mountain_score=score)
    return maps


def shares(maps, keys=('mountains', 'foothills', 'hills', 'plateaus', 'plains', 'valleys', 'wetlands')) -> dict:
    """Share of the land cells where each mask is the main one (>= 0.5)."""
    land = maps['land']
    on = [k for k in range(len(land)) if land[k] >= 0.5] or [0]
    return {name: sum(1 for k in on if maps[name][k] >= 0.5) / len(on) for name in keys if name in maps}

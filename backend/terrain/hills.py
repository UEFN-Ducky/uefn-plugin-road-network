"""Stages 3, 5, 6, 11: macro elevation, hills / foothills / plateaus, the blend into `height`, and detail noise.
Heights in metres. Bands: sea floor to -50, plains 0-40, hills 40-300, foothills 300-800, mountains on top."""
import math
from array import array

from .grid import smoothstep

TERRACES = 3


def _f01(v):
    return min(max(0.5 + v, 0.0), 1.0)


def macro_height(grid, cfg, noise, maps):
    """Stage 3 -> macro: plains 1.5-40 m over the sea level, tilting up away from the coast; the sea floor drops
    to -50 m within 2.5 km of the shore."""
    land, cd = maps['land'], maps['coast_dist']
    s0 = cfg.get('sea_level_m', 0.0)
    wob = grid.field(lambda x, y: noise.fbm(x / 8000.0 + 12.1, y / 8000.0 - 4.4, 3), 500.0)
    out = grid.new()
    for k in range(len(out)):
        tilt = smoothstep(0.0, 12000.0, cd[k])
        land_h = s0 + 1.5 + 38.5 * (0.55 * tilt + 0.45 * _f01(wob[k]))
        sea_h = s0 - 2.0 - 48.0 * smoothstep(0.0, 2500.0, -cd[k])
        out[k] = land[k] * land_h + (1.0 - land[k]) * sea_h
    maps['macro'] = out
    return maps


def foothill_top(cfg) -> float:
    return min(800.0, 0.35 * cfg.get('mountain_m', 0.0))


def hill_height(grid, cfg, noise, maps):
    """Stage 5a -> hill_h: hills (0.5-2.5 x relief_m at hill_km spacing), a soft roll on plains (none at
    flatland 1), foothills rising to foothill_top() at the range edge, the range's plinth, valleys pressed down."""
    sc = cfg.get('hill_km', 2.0) * 1000.0
    f = grid.field(lambda x, y: noise.fbm(x / sc - 3.7, y / sc + 15.2, 4), sc / 10)
    relief = cfg.get('relief_m', 60.0)
    roll = (1.0 - cfg.get('flatland', 0.5)) * relief * 0.3
    top = foothill_top(cfg)
    dip = min(60.0, 0.05 * cfg.get('mountain_m', 0.0))
    hm, pm, fm, mm, vm = (maps[k] for k in ('hills', 'plains', 'foothills', 'mountains', 'valleys'))
    out = grid.new()
    for k in range(len(out)):
        t = _f01(f[k])
        out[k] = (hm[k] * relief * (0.5 + 2.0 * t) + pm[k] * roll * f[k]
                  + (fm[k] + mm[k]) * top * (0.55 + 0.45 * t) - vm[k] * dip)
    maps['hill_h'] = out
    return maps


def plateau_height(grid, cfg, noise, maps):
    """Stage 5b -> plateau_w (0..1 terraced weight) and plateau_top (m): flat tops at one level, a stepped edge."""
    pm = maps['plateaus']
    w = grid.new()
    for k in range(len(w)):
        s = pm[k] * TERRACES
        fl = math.floor(s)
        w[k] = min(1.0, (fl + smoothstep(0.75, 1.0, s - fl)) / TERRACES)
    maps['plateau_w'] = w
    maps['plateau_top'] = cfg.get('sea_level_m', 0.0) + 100.0 + 200.0 * cfg.get('plateaus', 0.1)
    return maps


def mountain_fallback(grid, cfg, noise, maps):
    """Stage 4 stand-in when mountains.py is not there -> mountain_h: ridged noise in the mask's core."""
    peak = cfg.get('mountain_m', 0.0) * (0.4 + 0.6 * cfg.get('mountain_intensity', 0.5)) - foothill_top(cfg)
    mm = maps['mountains']
    out = grid.new()
    if peak > 0:
        for k, x, y in grid.points():
            if mm[k] > 0:
                out[k] = mm[k] ** 2 * peak * (0.3 + 0.7 * noise.ridged(x / 3000.0 + 7.1, y / 3000.0 - 2.2, 4))
    maps['mountain_h'] = out
    return maps


def blend(grid, cfg, noise, maps):
    """Stage 6 -> height = macro + hills (or the plateau top where it stands) + the mountain layer."""
    macro, hh, w, top = maps['macro'], maps['hill_h'], maps['plateau_w'], maps['plateau_top']
    mh = maps.get('mountain_h')
    out = grid.new()
    for k in range(len(out)):
        base = macro[k] + hh[k]
        out[k] = base + (top - base) * w[k] + (mh[k] if mh is not None else 0.0)
    maps['height'] = out
    return maps


def detail(grid, cfg, noise, maps):
    """Stage 11 -> height with 0-3 m of fine noise, scaled by slope (plains and plateau tops stay smooth)."""
    amt = 3.0 * cfg.get('detail', 0.5)
    h = array('d', maps['height'])
    if amt > 0:
        sl, pm, land = grid.slope(h), maps.get('plateaus'), maps['land']
        for k, x, y in grid.points():
            a = amt * min(1.0, sl[k] * 4.0) * (1.0 - (pm[k] if pm is not None else 0.0)) * land[k]
            if a > 0.01:
                h[k] += a * noise.fbm(x / 60.0 + 0.37, y / 60.0 + 0.71, 3)
    maps['height'] = h
    return maps

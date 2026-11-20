"""The one tree generator. Every tree the world grows on open land comes from grow(): the forest round the
cities (preview's foliage scatter keeps its own tiles, by the same rule: in_forest), an empty or a left-out lot, a
park's groves, the land inside an interchange. Each was planted by a rule of its own (an 8 m grid on a lot, a tree
or two in the middle of an empty one, a jittered grid in a park, a disc of 90 round an interchange) and the city's
blocks read as tree plantations next to a natural forest (Nov 20 2026). Its trees join the forest layer itself, so
they are the same species, sizes and bake."""

from __future__ import annotations

import math
import random
import zlib

from .layers import NATURE, patch_rank
from .samples import BASE_RADIUS, _Hash

GROVE_CELL_CM = 6000.0  # a lot's or a park's woods: groves and clearings about this size (the forest's are 600 m)
FOREST = NATURE[0]  # the forest's settings when a world's own layer gives none


def _u(*key) -> float:  # (land._u01's draw: the forest keeps every tree where it stood)
    return zlib.crc32(repr(key).encode()) / 4294967296.0


def in_forest(x: float, y: float, cover: float, seed, cell: float) -> bool:
    """The forest's patch rule: this spot is woods (not a meadow) at `cover`, its edge thinning out over 15 %."""
    return patch_rank(x, y, cell) <= cover * (1.0 + 0.15 * _u("edge", seed, round(x), round(y))) - 0.075


def forest_of(placed: dict):
    """(name, settings) of the world's forest: its first foliage layer of trees. (None, None) without one: a world
    with its forest switched off grows no tree anywhere."""
    for name, v in placed.items():
        if v.get("kind") == "foliage" and v.get("role") == "tree":
            return name, {**{k: FOREST[k] for k in ("density", "cover", "scale_min", "scale_max", "sink_cm", "footprint")},
                          "max_slope_deg": 20.0, "seed": 0, **(v.get("grow") or {})}
    return None, None


def grow(placed: dict, area, box, ground, key, sea=None, keep=None, cell: float = GROVE_CELL_CM, plant: bool = True) -> list:
    """Trees on the land `area(x, y)` says is open, inside box (x0, y0, x1, y1), by the forest's rule: candidates at
    its density, kept where in_forest (groves and clearings of `cell`), where the biome takes a tree (`keep`, from
    land.biome_trees), dry and no steeper than it grows; never closer than its own size. `key` makes it the same
    every run. Added to the forest layer (plant=False: only returned). Returns the rows [x, y, z, yaw, scale]."""
    name, f = forest_of(placed)
    if not name:
        return []
    x0, y0, x1, y1 = box
    rnd = random.Random(f"grow:{f['seed']}:{key}")
    want = f["density"] / 100.0 * max(0.0, x1 - x0) * max(0.0, y1 - y0) / 1e4
    tan = math.tan(math.radians(f["max_slope_deg"]))
    own = _Hash(1000.0)
    rows = []
    for _ in range(int(want) + (rnd.random() < want - int(want))):
        x, y = rnd.uniform(x0, x1), rnd.uniform(y0, y1)
        if not area(x, y) or not in_forest(x, y, f["cover"], f["seed"], cell) or (keep and not keep(x, y, "tree")):
            continue
        z = ground(x, y)
        if z is None or (sea is not None and z < sea + 50):
            continue
        zs = (ground(x + 300, y), ground(x - 300, y), ground(x, y + 300), ground(x, y - 300))
        if None not in zs and math.hypot(zs[0] - zs[1], zs[2] - zs[3]) > 600 * tan:
            continue  # a bank or a cliff
        s = f["scale_min"] + (f["scale_max"] - f["scale_min"]) * rnd.random()
        r = BASE_RADIUS["foliage"] * f["footprint"] * s
        if own.hit(x, y, r):
            continue
        own.add(x, y, r)
        rows.append([round(x), round(y), round(z - f["sink_cm"]), round(rnd.uniform(0, 360)), round(s, 2)])
    if plant:
        add(placed, rows)
    return rows


def add(placed: dict, rows: list) -> int:
    """Rows (from grow) into the forest layer."""
    name, _ = forest_of(placed)
    if not name or not rows:
        return 0
    v = placed[name]
    v["points"] = list(v.get("points") or []) + rows
    v["count"] = v.get("count", 0) + len(rows)
    v["shown"] = len(v["points"])
    return len(rows)


def in_rect(x0: float, y0: float, yaw: float, half_d: float, half_w: float):
    """(area test, box) of a turned rectangle: half_d along yaw, half_w across."""
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    r = math.hypot(half_d, half_w)
    return ((lambda x, y: abs((x - x0) * c + (y - y0) * s) <= half_d and abs(-(x - x0) * s + (y - y0) * c) <= half_w),
            (x0 - r, y0 - r, x0 + r, y0 + r))

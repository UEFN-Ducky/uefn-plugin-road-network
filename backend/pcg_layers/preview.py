"""Offline preview: every layer placed unit by unit, and PreviewMemo, what the last run placed."""

from __future__ import annotations

import copy
import functools
import math
import random
import zlib

from .graph import avoid_list
from .land import (DIRTY_CM, TILE_CM, _city_sig, _height, _lot_hits_road, _Rects, _u01, _where_test, core_peak,
                   biome_trees, inside, open_land, tree_kind)
from .layers import FAR_KEEP, FORM_NAMES, PLANNED_ROLES, clean, patch
from .lots import BIG_SITE_M, _lot_ground
from .samples import BASE_RADIUS, SCATTERED, _Hash, junction_corners, street_samples

class PreviewMemo:
    """One world's last placement, unit by unit (a layer's lots in one city, its roadside, junction or scatter
    pieces in one 500 m tile), with what it was placed from. The next run places again only the units a change can
    reach: a city that is not what it was, and every unit within 100 m of a road sample, a city or a piece of
    ground that changed. Every other unit is put back exactly as it stood, so a small edit costs a small part of
    the world and changes nothing else."""

    def __init__(self):
        self.sig = self.frame = self.heights = None
        self.units: dict = {}    # (layer, unit) -> what it placed
        self.cities: dict = {}   # name -> (signature, x, y, radius)
        self.roads: set = set()  # road samples to half a metre
        self.dirty = None        # the 100 m cells a change touched; None = everything
        self.moved: list = []    # cities that changed: (as it was, as it is), each (x, y, radius) or None
        self.last = {"placed": 0, "kept": 0}  # units placed again / put back in the last run

    def begin(self, sig, cities: list, roads: list, terrain: dict) -> None:
        cell, reach = DIRTY_CM, 1.35  # (a layer that reaches further out from a city asks reaches() too)
        self.moved = []
        new_cities = {str(c.get("name") or k): (*_city_sig(c), c["x"], c["y"], c["radius"]) for k, c in enumerate(cities)}
        new_roads = {(round(x / 50), round(y / 50), round(half)) for x, y, half, *_ in roads}
        heights = terrain.get("heights")
        frame = (terrain.get("min"), terrain.get("max"), terrain.get("grid"), terrain.get("sea"), len(heights or ()))
        if sig != self.sig or frame != self.frame:
            self.dirty, self.units = None, {}
        else:
            dirty: set = set()
            why = {"road samples": len(new_roads ^ self.roads), "cities": 0, "ground cells": 0}
            for qx, qy, _half in new_roads ^ self.roads:
                cx, cy = int(qx * 50 // cell), int(qy * 50 // cell)
                dirty.update((cx + i, cy + j) for i in (-1, 0, 1) for j in (-1, 0, 1))
            for name in set(new_cities) | set(self.cities):
                a, b = self.cities.get(name), new_cities.get(name)
                if a and b and a[0] == b[0]:
                    # where it was, as big, turned the same way: only the rows of its plan that are not what they
                    # were (a block given another type, a lot the planner took) are a change, where they lie
                    for old, new in zip(a[1], b[1]):
                        for row in set(old) ^ set(new):
                            why["plan rows"] = why.get("plan rows", 0) + 1
                            pts = (old.get(row) or new.get(row))
                            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                            dirty.update((i, j) for i in range(int(min(xs) // cell) - 1, int(max(xs) // cell) + 2)
                                         for j in range(int(min(ys) // cell) - 1, int(max(ys) // cell) + 2))
                    continue
                why["cities"] += 1
                self.moved.append((a and a[2:], b and b[2:]))
                for got in (a, b):
                    if got:
                        _frame, _parts, x, y, radius = got
                        r = radius * reach + 20000
                        for i in range(int((x - r) // cell), int((x + r) // cell) + 1):
                            for j in range(int((y - r) // cell), int((y + r) // cell) + 1):
                                if ((i + 0.5) * cell - x) ** 2 + ((j + 0.5) * cell - y) ** 2 <= (r + cell) ** 2:
                                    dirty.add((i, j))
            if heights and self.heights is not None and heights != self.heights:
                (x0, y0), (x1, y1) = terrain["min"], terrain["max"]
                rows, cols = len(heights), len(heights[0])
                sx, sy = (x1 - x0) / cols, (y1 - y0) / rows
                for j, (new, old) in enumerate(zip(heights, self.heights)):
                    if new == old:
                        continue
                    for i, (p, q) in enumerate(zip(new, old)):
                        if p != q:
                            why["ground cells"] += 1
                            cx, cy = int((x0 + (i + 0.5) * sx) // cell), int((y0 + (j + 0.5) * sy) // cell)
                            dirty.update((cx + a, cy + b) for a in (-1, 0, 1) for b in (-1, 0, 1))
            self.dirty = dirty
            self.why = {**why, "cells": len(dirty)}
        self.sig, self.frame, self.cities, self.roads, self.heights = sig, frame, new_cities, new_roads, heights
        self.last = {"placed": 0, "kept": 0, **(getattr(self, "why", None) or {})}

    def reaches(self, box: tuple, reach: float) -> bool:
        """For a layer placed within `reach` radii of every city: is this box somewhere a changed city's reach
        was and is no longer, or the other way round? (Deep inside it both times, or outside both, nothing moved.)"""
        if self.dirty is None:
            return True
        x0, y0, x1, y1 = box

        def side(city):  # 1 inside its reach, -1 clear of it, 0 across its edge
            if not city:
                return -1
            cx, cy, radius = city
            r = radius * reach
            if all((x - cx) ** 2 + (y - cy) ** 2 <= r * r for x in (x0, x1) for y in (y0, y1)):
                return 1
            return -1 if max(x0 - cx, 0.0, cx - x1) ** 2 + max(y0 - cy, 0.0, cy - y1) ** 2 > r * r else 0
        return any(side(a) != side(b) or side(a) == 0 for a, b in self.moved)

    def touched(self, x0: float, y0: float, x1: float, y1: float) -> bool:
        """Did anything change inside this box or within 100 m of it?"""
        if self.dirty is None:
            return True
        if not self.dirty:
            return False
        c = DIRTY_CM
        i0, i1, j0, j1 = int(x0 // c) - 1, int(x1 // c) + 1, int(y0 // c) - 1, int(y1 // c) + 1
        if (i1 - i0 + 1) * (j1 - j0 + 1) > len(self.dirty):
            return any(i0 <= i <= i1 and j0 <= j <= j1 for i, j in self.dirty)
        return any((i, j) in self.dirty for i in range(i0, i1 + 1) for j in range(j0, j1 + 1))


def preview(layers: list, cities: list, terrain: dict, roads: list, sample_z, limit: int = 2500,
            juncs: list | None = None, keep: list | None = None, foliage_limit: int = 0,
            memo: PreviewMemo | None = None, arms: list | None = None, on_layer=None) -> dict:
    """Run every enabled layer offline with the same rules as the graph (mask, off roads, keep clear of
    earlier layers, self pruning, sink) and return {layer name: {points: [[x, y, z, yaw, scale]], ...}}.
    Scatter is thinned to ``limit`` points per layer for the view; ``count`` says the full estimate.
    Every layer is placed unit by unit (a city's lots; a 500 m tile's scatter, roadside or junction pieces) and
    nothing in a unit depends on a random shared with another, so a change in one place moves nothing elsewhere.
    ``memo`` (a PreviewMemo kept by the caller for this world) puts back every unit no change can reach and
    places only the others again. ``arms`` (arm_samples): the junctions' own road surface, which nothing is
    placed along but everything keeps off. ``on_layer(i, n, name)`` is called as each layer starts."""
    layers = sorted((clean(l) for l in layers if l.get("enabled", True)), key=lambda l: (l["order"], l["name"]))
    lo, hi = terrain.get("min"), terrain.get("max")
    # with a terrain, nothing is placed past its edge (its sampler answers the edge's height out there, so the
    # ground check alone let forests and a pushed-out city's buildings off the map, Nov 11 2026)
    on_map = (lambda x, y: lo[0] <= x <= hi[0] and lo[1] <= y <= hi[1]) if lo and hi else (lambda x, y: True)
    if not lo or not hi:
        xs = [r[0] for r in roads] + [c["x"] for c in cities] or [0]
        ys = [r[1] for r in roads] + [c["y"] for c in cities] or [0]
        lo, hi = [min(xs) - 20000, min(ys) - 20000], [max(xs) + 20000, max(ys) + 20000]
    sea = terrain.get("sea")
    by_biome = biome_trees(terrain)  # an engine world: trees follow its biomes (None on Classic: as before)
    road_hash = _Hash()
    for x, y, half, _, _ in roads:
        road_hash.add(x, y, half, (x, y, half))
    for x, y, half in arms or []:
        road_hash.add(x, y, half, (x, y, half))
    priv_hash = _Hash()  # the subdivisions' and complexes' own streets: nothing on those either
    for x, y, half, _, _ in street_samples(cities):
        priv_hash.add(x, y, half, (x, y, half))
    built = _Rects()  # every building of every layer, as the rectangle it stands on
    # What the cities planned, before any layer is placed: each car park, each ground (an apron, a sports field)
    # and each building a complex or a facility drew at its own spot. A layer that builds along road frontage, or
    # on a free lot, never stands on one. They knew only what was already built: a tower took the corner first, on
    # a shopping centre's car park and on the spot of the shops themselves, which were then never built (a car
    # park with a notch out of it and a building in the notch, Nov 13 2026).
    held = _Rects()
    for c in cities:
        priv = c.get("private") or {}
        for row in (priv.get("parking") or []) + (priv.get("grounds") or []):
            f = math.radians(row[2])
            held.add((row[0], row[1], math.cos(f), math.sin(f), row[4] * 50, row[3] * 50))
        for r in c.get("lots") or []:
            if len(r) > 8 and r[8] is True:
                f = math.radians(r[2])
                held.add((r[0], r[1], math.cos(f), math.sin(f), r[3] / 2, r[4] / 2))
    # blocks laid out as a park (city 'blocks': [x, y, kind, zone, polygon]): trees and paths only, so a layer that
    # builds along road frontage (mansions, offices) leaves them alone too
    parks = [(b[4], min(p[0] for p in b[4]), min(p[1] for p in b[4]), max(p[0] for p in b[4]), max(p[1] for p in b[4]))
             for c in cities for b in c.get("blocks") or [] if len(b) > 4 and b[2] == "park" and len(b[4]) > 2]
    # a shipyard's platform and the bank round it are the yard's: only its own pieces, a layer drawn for an area
    # there, or a layer set to the shipyards stand on them (no forest on the hardstand, no street lamp in a dock)
    from .. import shipyard, waterfront
    yards = [s for c in cities for s in c.get("shipyards") or [] if s.get("z") is not None]
    shores = waterfront.Made(cities)  # ... and a made shore (quay, beach, lawn) is the waterfront plan's
    vacant = None  # (x, y) -> on land inside a city that nothing was planned for (open_land, made when first asked)
    infill: list = []  # lots left empty because a building would stand on another or on a street: planted instead
    plan_roles = {row[7] for c in cities for row in c.get("lots") or [] if len(row) > 8 and row[8] is True}
    # (where the planner ran, what it sites is built on its sites only: when it found a city too small for a mall
    # the Malls layer went back to road frontage and put one every 250 m, 18 in a world that had one)
    has_plan = any(c.get("plan") for c in cities)
    planned = lambda layer: layer["role"] in PLANNED_ROLES and (has_plan or layer["role"] in plan_roles)
    kept: dict = {}
    taken: set = set()  # lots already built on (by front point)
    lot_layers: set = set()  # layers that filled lots
    building_layers = {l["name"] for l in layers if l["kind"] == "buildings"}
    out = {}
    T = TILE_CM
    tile_of = lambda x, y: (int(x // T), int(y // T))
    tile_box = lambda t: (t[0] * T, t[1] * T, t[0] * T + T, t[1] * T + T)
    if memo is not None:
        memo.begin(repr([layers, limit, foliage_limit, keep, lo, hi]), cities, roads + [(*a, 0.0, 0.0) for a in arms or []], terrain)
    units_now: dict = {}
    for li, layer in enumerate(layers):
        seed, name = layer["seed"], layer["name"]
        if on_layer:
            on_layer(li, len(layers), name)
        building = layer["kind"] == "buildings"
        units = []  # (unit id, its box (x0, y0, x1, y1), its candidates or a function that makes them)
        full = 0.0
        uses_lots = False
        if layer["kind"] == "at_junctions":
            by_tile: dict = {}
            for c in junction_corners(juncs or [], layer["setback_m"], layer["corners"]):
                by_tile.setdefault(tile_of(c[0], c[1]), []).append(c)
            units = [(("t", *t), tile_box(t), cs) for t, cs in sorted(by_tile.items())]
        elif layer["kind"] in SCATTERED:
            cap = (foliage_limit or limit) if layer["kind"] == "foliage" else limit
            discs = [(c["x"], c["y"], c["radius"] * layer["reach"]) for c in cities] if layer["reach"] and cities else []
            # The whole map. Round the cities (within `reach`) as dense as the layer asks; beyond, the same where
            # the layer's budget of points allows it, else thinner, down to FAR_KEEP of it, before the cities'
            # forest gives anything up. (It stopped dead at `reach` once: a ring of forest round each city and
            # bare hills over the rest of the map.)
            map_m2 = (hi[0] - lo[0]) * (hi[1] - lo[1]) / 1e4
            near_m2 = min(map_m2, sum(math.pi * r * r for _, _, r in discs) / 1e4) if discs else map_m2
            far_m2 = map_m2 - near_m2
            want_m2, budget = layer["density"] / 100.0, cap * 4.0  # candidates per m2 before patches, masks and pruning
            far, thin_all = 1.0, 1.0
            if want_m2 * map_m2 > budget:
                far = max(FAR_KEEP, (budget / want_m2 - near_m2) / far_m2) if far_m2 > 0 else 1.0
                thin_all = min(1.0, budget / (want_m2 * (near_m2 + far * far_m2)))
            per_m2 = want_m2 * thin_all
            # density counts inside the patches only: a forest is dense, the meadows between are open
            full = want_m2 * (near_m2 + far * far_m2) * layer["cover"]
            n = int(per_m2 * (near_m2 + far * far_m2))

            def scatter(ix, iy, layer=layer, discs=discs, per_m2=per_m2, seed=seed, name=name, far=far):
                x0, y0, x1, y1 = max(lo[0], ix * T), max(lo[1], iy * T), min(hi[0], ix * T + T), min(hi[1], iy * T + T)
                rnd = random.Random(f"{seed}:{name}:{ix}:{iy}")  # this tile's own: no other tile draws from it
                want = per_m2 * max(0.0, x1 - x0) * max(0.0, y1 - y0) / 1e4
                cs = []
                for _ in range(int(want) + (rnd.random() < want - int(want))):
                    x, y, yaw = rnd.uniform(x0, x1), rnd.uniform(y0, y1), rnd.uniform(0, 360)
                    if discs and far < 1.0 and not any((x - cx) ** 2 + (y - cy) ** 2 <= r * r for cx, cy, r in discs) \
                            and _u01("far", seed, name, round(x), round(y)) >= far:
                        continue  # past the cities' reach: the share of it the budget leaves (the spot's own draw)
                    if layer["cover"] < 1.0 and patch(x, y) > layer["cover"] - 0.12 * _u01("edge", seed, round(x), round(y)):
                        continue  # a meadow, not a forest (thinning out over its last 0.12: woodland at the edge)
                    cs.append((x, y, yaw))
                return cs
            spans = [(lo[0], lo[1], hi[0], hi[1])]
            tiles = sorted({(ix, iy) for x0, y0, x1, y1 in spans
                            for ix in range(int(max(lo[0], x0) // T), int(min(hi[0], x1) // T) + 1)
                            for iy in range(int(max(lo[1], y0) // T), int(min(hi[1], y1) // T) + 1)}) if n else []
            units = [(("t", *t), tile_box(t), functools.partial(scatter, *t)) for t in tiles]
        elif building and (layer["width_m"] < BIG_SITE_M or planned(layer)) and any(c.get("lots") for c in cities):
            # everyday buildings fill the lots cut from the city's blocks (parcels): each faces its street square
            # on, its front yard is the layer's setback, and it is sized to its lot, never over a road
            uses_lots = True
            for c in cities:
                cs = []
                for lot_row in c.get("lots") or []:
                    lx, ly, lyaw, lw, ld, frx, fry = lot_row[:7]
                    role, foot = (lot_row[7], lot_row[8]) if len(lot_row) > 8 else ("", False)
                    fac = lot_row[9] if len(lot_row) > 9 and isinstance(lot_row[9], str) else ""  # planner's facility
                    cs.append((lx, ly, lyaw, "lot", lw, ld, frx, fry, role, foot, fac))
                # which layer gets which free lot is mixed, not swept from one corner; each lot's place in the order
                # is its own, so a city that gains or loses a lot keeps every other one as it was
                cs.sort(key=lambda k: _u01(seed, name, k[6], k[7]))
                r = c["radius"] * 1.35
                if cs:  # (its box: the city, and any lot of its own beyond it, along a street that joins it to another)
                    units.append((("c", str(c.get("name"))), (min(c["x"] - r, min(k[0] for k in cs) - 5000),
                                                              min(c["y"] - r, min(k[1] for k in cs) - 5000),
                                                              max(c["x"] + r, max(k[0] for k in cs) + 5000),
                                                              max(c["y"] + r, max(k[1] for k in cs) + 5000)), cs))
        else:
            # every road sample on both sides is a candidate; the placing loop below takes the first good one
            # once `spacing` has passed since the last placed one on that side (a lot blocked by a cross
            # street slides along to the next good spot instead of being lost)
            by_tile = {}
            progress, last = 0.0, None
            for x, y, half, dx, dy in roads:
                if last is not None and math.hypot(x - last[0], y - last[1]) <= 2000:
                    progress += math.hypot(x - last[0], y - last[1])
                last = (x, y)
                # from this road's kerb, whatever its width; a building's centre is half its depth further back;
                # street furniture stands its own size past the setback line, never on the kerb
                back = half + layer["setback_m"] * 100 + (layer["depth_m"] * 50 if building
                                                          else BASE_RADIUS[layer["kind"]] * layer["footprint"] * layer["scale_max"])
                cs = by_tile.setdefault(tile_of(x, y), [])
                for side in (1, -1):  # normal to the road, both sides, facing it
                    nx, ny = -dy * side, dx * side
                    yaw = math.degrees(math.atan2(-ny, -nx))
                    cs.append((x + nx * back, y + ny * back, yaw, progress, side))
            units = [(("t", *t), tile_box(t), cs) for t, cs in sorted(by_tile.items())]
            if building:
                # ... and, first, every lot a plan drew for exactly this kind of building (a shipyard's fabrication
                # halls and warehouses, a port's sheds): built there, at that size. These layers place big sites
                # along the roads and never looked at lots, so such a hall was never built at all.
                drawn = []
                for c in cities:
                    cs = [(r[0], r[1], r[2], "lot", r[3], r[4], r[5], r[6], r[7], True,
                           r[9] if len(r) > 9 and isinstance(r[9], str) else "")
                          for r in c.get("lots") or [] if len(r) > 8 and r[8] is True and r[7] == layer["role"]]
                    if cs:
                        drawn.append((("p", str(c.get("name"))), (min(k[0] for k in cs) - 20000, min(k[1] for k in cs) - 20000,
                                                                  max(k[0] for k in cs) + 20000, max(k[1] for k in cs) + 20000), cs))
                # ... and then every free lot of its district that it fits, before any road frontage: a tower or
                # an office block stands on a lot of its block like a house on its own. These layers knew no
                # lots, and no other layer builds downtown or in the industrial zone: half the lots there stood
                # empty between the big buildings (Nov 12 2026).
                free = []
                for c in cities:
                    cs = [(r[0], r[1], r[2], "lot", r[3], r[4], r[5], r[6], "", False, "")
                          for r in c.get("lots") or [] if not (len(r) > 7 and r[7])]
                    cs.sort(key=lambda k: _u01(seed, name, k[6], k[7]))
                    r = c["radius"] * 1.35
                    if cs and layer["role"] not in PLANNED_ROLES:  # (what the planner sites is never put on a free lot)
                        free.append((("f", str(c.get("name"))), (min(c["x"] - r, min(k[0] for k in cs) - 5000),
                                                                 min(c["y"] - r, min(k[1] for k in cs) - 5000),
                                                                 max(c["x"] + r, max(k[0] for k in cs) + 5000),
                                                                 max(c["y"] + r, max(k[1] for k in cs) + 5000)), cs))
                units = drawn + free + units
        spacing = layer["spacing_m"] * 100
        r_base = BASE_RADIUS[layer["kind"]] * layer["footprint"]
        if building:  # its lot, not a fixed size: a skyscraper needs far more room than a shop
            r_base = 50.0 * max(layer["width_m"], layer["depth_m"]) * layer["footprint"]
        # cells about twice this layer's widest thing (5-20 m): a look at a forest reads a few trees, not dozens
        own = _Hash(max(500.0, min(2000.0, 2 * r_base * layer["scale_max"])))
        own_heights = _Hash()  # this layer's buildings and their heights, for the neighbour rule
        pts = []
        avoid = avoid_list(layer, [l for l in layers if l["name"] in kept])
        where_ok = _where_test(layer["where"], cities)
        if layer["kind"] in SCATTERED and layer["where"] == "outside" and cities:
            # the forest does not stop at a city's edge: it grows on every piece of the city's land that nothing
            # was planned for (behind the last row of lots, in the corner two streets leave, round an interchange)
            if vacant is None:
                vacant = open_land(cities)
            where_ok = (lambda x, y, out_=where_ok, free=vacant: out_(x, y) or free(x, y))  # (not outside: in a city)
        if by_biome and layer["kind"] in SCATTERED and layer["role"] in ("tree", "bush"):
            where_ok = (lambda x, y, ok=where_ok, k=tree_kind(layer) if layer["role"] == "tree" else "bush":
                        ok(x, y) and by_biome(x, y, k))
        candidates = 0
        why: dict = {}  # building layers: why each lot was passed over (road_network_pcg_preview `skipped`)
        no = lambda k: why.__setitem__(k, why.get(k, 0) + 1)
        todo = []
        for uid, box, cands in units:
            had = memo.units.get((name, uid)) if memo is not None else None
            if had is None or memo.touched(*box) or (layer["reach"] and layer["kind"] in SCATTERED and memo.reaches(box, layer["reach"])):
                todo.append((uid, cands))
                continue
            # as it stood: no change reaches it. Put back first, so the units placed again fit round these
            units_now[(name, uid)] = had
            memo.last["kept"] += 1
            candidates += had["n"]
            for x, y, r in had["own"]:
                own.add(x, y, r)
            for rect in had["rects"]:
                built.add(rect)
            taken.update(had["taken"])
            for x, y, h in had["heights"]:
                own_heights.add(x, y, 0.0, h)
            infill.extend(list(r) for r in had["infill"])
            pts.extend(copy.deepcopy(had["rows"]) if building else [list(r) for r in had["rows"]])
        for uid, cands in todo:
            if callable(cands):
                cands = cands()
            candidates += len(cands)
            rec = {"n": len(cands), "rows": [], "own": [], "rects": [], "taken": [], "heights": [], "infill": []}
            last_at = {1: -1e18, -1: -1e18}
            for cand in cands:
                x, y, yaw = cand[:3]
                along = len(cand) == 5
                lot = len(cand) == 11
                lot_role = cand[8] if lot else ""
                if lot:
                    if (cand[6], cand[7]) in taken:
                        no("taken")
                        continue  # an earlier layer built on this lot
                    if lot_role and lot_role != layer["role"]:
                        continue  # a neighbourhood decided this lot: houses in a subdivision, apartments in a complex
                    if not lot_role and layer["role"] in PLANNED_ROLES and planned(layer):
                        continue  # the planner placed these: a school is where children live, not on any free lot
                    lw, ld = cand[4] / 100, cand[5] / 100
                    if cand[9]:  # a building footprint the complex planned: exactly that size and spot
                        bw, bd = round(lw * 0.96, 1), round(ld * 0.96, 1)
                    else:
                        bw = round(min(layer["width_m"] * (1 + layer["size_jitter"] * 0.5), lw * 0.85), 1)
                        bd = round(min(layer["depth_m"] * (1 + layer["size_jitter"] * 0.3), ld - layer["setback_m"] - 2), 1)
                        if bw < layer["width_m"] * 0.6 or bd < layer["depth_m"] * 0.5:
                            no("lot too small")
                            continue  # the lot is too small for this kind of building
                        f = math.radians(yaw)  # centre: the front lot line, back by the setback and half the building
                        back = (layer["setback_m"] + bd / 2) * 100
                        x, y = cand[6] - math.cos(f) * back, cand[7] - math.sin(f) * back
                if along and cand[3] - last_at[cand[4]] < spacing:
                    continue  # too soon after the last one on this side of the street
                if not lot_role and not where_ok(x, y):
                    lot and no("wrong district")
                    continue  # (a neighbourhood's own lots are already in the right place)
                if layer["area"] and not inside(layer["area"], x, y):
                    continue  # outside the area this layer was drawn for
                if yards and not lot_role and not layer["area"] and layer["where"] != "shipyards" and \
                        any(shipyard.reserved(s, x, y, 500) for s in yards):
                    continue  # a shipyard's own land
                if shores.n and not lot_role and not layer["area"] and shores.shelf(x, y, 300):
                    continue  # a made shore: its promenade, sand and lawns stay clear
                if parks and building and not lot and \
                        any(x0 <= x <= x1 and y0 <= y <= y1 and inside(poly, x, y) for poly, x0, y0, x1, y1 in parks):
                    continue  # a park block: nothing is built on it
                if keep and any(max(k["x0"] - x, 0, x - k["x1"]) ** 2 + max(k["y0"] - y, 0, y - k["y1"]) ** 2 < 4e6 for k in keep):
                    continue  # 20 m around a keep zone (a cinema, a spawn) stays clear
                spot = (seed, name, round(x), round(y))  # this piece's own: its size, height and shape come from it
                if building and layer["height_min_m"] >= 40 and _u01("core", *spot) > 0.08 + 0.92 * core_peak(cities, x, y):
                    continue  # towers cluster at the core and thin out toward the edge: no wall of equal towers
                z = sample_z(x, y)
                if z is None or (sea is not None and z < sea + 50) or not on_map(x, y):
                    lot and no("under water / off map")
                    continue  # nothing grows under water or off the map
                if layer["kind"] in SCATTERED and layer["max_slope_deg"] < 90:
                    zs = (sample_z(x + 300, y), sample_z(x - 300, y), sample_z(x, y + 300), sample_z(x, y - 300))
                    if None not in zs and math.hypot(zs[0] - zs[1], zs[2] - zs[3]) > 600 * math.tan(math.radians(layer["max_slope_deg"])):
                        continue  # too steep: nothing grows on a bank or a cliff (slope over 6 m round the spot)
                s = layer["scale_min"] + (layer["scale_max"] - layer["scale_min"]) * _u01("scale", *spot)
                r = r_base * s
                # scatter keeps the clearance; roadside layers sit at their setback, so only their own size counts
                clear = layer["road_clear_m"] * 100 if layer["kind"] in SCATTERED else 0.0
                if lot and _lot_hits_road(road_hash, x, y, yaw, bw * 50, bd * 50):
                    no("on a road")
                    continue  # a lot is cut off its own street, but a subdivision's lot can still reach over another one
                if layer["avoid_roads"] and not lot:
                    if building:  # the lot as the rectangle it is, facing its street
                        if _lot_hits_road(road_hash, x, y, yaw, layer["width_m"] * 50, layer["depth_m"] * 50):
                            continue
                    elif road_hash.hit(x, y, r + clear) or priv_hash.hit(x, y, r):
                        continue
                # a lot keeps clear of the buildings before it by its own rectangle against theirs (below), never by
                # the wide circle round a big building: that left the three lots on either side of every mansion
                # and office empty, with no tree either
                if any(kept[other].hit(x, y, r) for other in avoid if not (lot and (other in lot_layers or other in building_layers))):
                    lot and no("near an avoided layer")
                    continue
                # one of a kind per `spacing` in any direction (two hospitals never sit across the street from
                # each other); dense layers just must not overlap
                if lot:  # only one-of-a-kind spacing (100 m+) applies on lots; neighbours sit lot to lot.
                    # A site the planner chose (a library next to city hall) is never pruned by the layer's spacing:
                    # that spacing is for scattering, and it silently dropped planned civic buildings and schools
                    gap = 0.0 if cand[9] else spacing * 0.5 if spacing >= 10000 else 0.0
                else:
                    gap = max(r, spacing * 0.5) if along and spacing > 4 * r else r
                if layer["self_prune"] and gap and own.hit(x, y, gap):
                    lot and no("spacing")
                    continue
                if building:
                    # Never on another building, never on a street: its own rectangle against every one placed so far
                    # (lots overlap inside a street's bend, and big sites along a road knew nothing of private streets)
                    rw, rd = (bw, bd) if lot else (layer["width_m"] * (1 + layer["size_jitter"] * 0.5),
                                                   layer["depth_m"] * (1 + layer["size_jitter"] * 0.3))
                    rect = (x, y, math.cos(math.radians(yaw)), math.sin(math.radians(yaw)), rw * 50, rd * 50)
                    if not (lot and cand[9]) and held.hit(rect, 0.0):
                        lot and no("planned site")
                        continue  # a planned car park, ground or building spot: not this building's land
                    if _lot_hits_road(priv_hash, x, y, yaw, rw * 50, rd * 50) or built.hit(rect):
                        if lot:
                            no("on a private street" if _lot_hits_road(priv_hash, x, y, yaw, rw * 50, rd * 50)
                               else "on another building")
                            tree = [round(x), round(y), round(z), round((x * 7 + y * 13) % 360),
                                    round(0.9 + int(_u01("infill", round(x), round(y)) * 40) / 100, 2)]
                            infill.append(tree)
                            rec["infill"].append(list(tree))
                        continue
                    built.add(rect)
                    rec["rects"].append(rect)
                own.add(x, y, r)
                rec["own"].append((x, y, r))
                if along:
                    last_at[cand[4]] = cand[3]
                yaw = yaw if layer["random_yaw"] or layer["kind"] not in SCATTERED else 0.0
                row = [round(x), round(y), round(z - layer["sink_cm"]), round(yaw), round(s, 2)]
                if building:  # + its height in metres, so the preview draws a real skyline
                    rnd = random.Random(zlib.crc32(repr(spot).encode()))  # this building's own
                    row.append(round(_height(layer, cities, x, y, rnd, own_heights), 1))
                    # its form and its own size: no two neighbours the same shape and footprint
                    names, weights = list(layer["forms"]), list(layer["forms"].values())
                    j = layer["size_jitter"]
                    row += [FORM_NAMES.index(rnd.choices(names, weights)[0]),
                            bw if lot else round(layer["width_m"] * rnd.uniform(1 - j, 1 + j * 0.5), 1),
                            bd if lot else round(layer["depth_m"] * rnd.uniform(1 - j, 1 + j * 0.3), 1)]
                    if lot:
                        taken.add((cand[6], cand[7]))
                        rec["taken"].append((cand[6], cand[7]))
                    own_heights.add(x, y, 0.0, row[5])
                    rec["heights"].append((x, y, row[5]))
                    pad, found, paths = _lot_ground(layer, sample_z, x, y, yaw, row[7], row[8])
                    row[2] = round(pad - layer["sink_cm"])
                    row += [found, paths, []]  # ([]: where lot-edge wall slabs were; the fields after it keep their place)
                    if lot and cand[10]:
                        row.append(cand[10])  # the facility (city_hall, jail, power_plant...): how the bake builds it
                pts.append(row)
                if memo is not None:
                    rec["rows"].append(copy.deepcopy(row) if building else list(row))
            if memo is not None:
                units_now[(name, uid)] = rec
                memo.last["placed"] += 1
        kept[name] = own
        if uses_lots:
            lot_layers.add(name)
        thin = full / max(1, candidates) if layer["kind"] in SCATTERED and candidates else 1.0
        count = int(len(pts) * thin) if layer["kind"] in SCATTERED else len(pts)
        shown_cap = (foliage_limit or limit) if layer["kind"] == "foliage" else limit
        if len(pts) > shown_cap:
            # shown thinned, each piece by its own spot (a share in steps of 2 %, so it rarely moves): the same
            # ones stay on screen from run to run
            share = math.ceil(shown_cap / len(pts) * 50) / 50
            pts = [p for p in pts if _u01("shown", seed, name, p[0], p[1]) < share]
        out[name] = {"kind": layer["kind"], "role": layer["role"], "count": count, "shown": len(pts), "points": pts,
                     **({"skipped": why} if why else {}),
                     "radius_cm": round(r_base), "meshes": layer["meshes"], "build": layer["build"],
                     **({"size_m": [layer["width_m"], layer["depth_m"]], "setback_m": layer["setback_m"],
                         "forms": FORM_NAMES, "driveway": layer["driveway"], "walkway": layer["walkway"],
                         "row": "x, y, pad z, yaw, scale, height_m, form, width_m, depth_m, foundation_m, "
                                "paths [[width_m, [x, y, z, ...]]], [] (unused), facility (planned sites)"} if building else {})}
    if memo is not None:
        memo.units = units_now
    # no lot is left with nothing: one that no layer built on (too shallow for a house behind its front yard, a
    # corner nothing fits) is planted, like the ones a building could not stand on
    planted: dict = {}
    for t in infill:
        planted.setdefault((int(t[0] // 4000), int(t[1] // 4000)), []).append(t)
    if any(l["kind"] == "buildings" for l in layers):
        for c in cities:
            for row in c.get("lots") or []:
                if (row[5], row[6]) in taken or (len(row) > 8 and row[8] is True):
                    continue  # built on; a footprint a plan drew (its own layer builds it, or none does)
                f = math.radians(row[2])
                fx, fy = math.cos(f), math.sin(f)
                gx, gy = int(row[0] // 4000), int(row[1] // 4000)
                if any(abs((t[0] - row[0]) * fx + (t[1] - row[1]) * fy) <= row[4] / 2 and abs(-(t[0] - row[0]) * fy + (t[1] - row[1]) * fx) <= row[3] / 2
                       for i in (-1, 0, 1) for j in (-1, 0, 1) for t in planted.get((gx + i, gy + j), ())):
                    continue  # it has its tree
                for back in ((0.0,) if row[4] < 2400 else (0.22, -0.25)):  # one tree, two on a deep lot
                    x, y = row[0] + fx * row[4] * back, row[1] + fy * row[4] * back
                    z = sample_z(x, y)
                    if z is None or (sea is not None and z < sea + 50) or not on_map(x, y) or road_hash.hit(x, y, 250.0) or \
                            priv_hash.hit(x, y, 250.0):
                        continue
                    infill.append([round(x), round(y), round(z), round((x * 7 + y * 13) % 360),
                                   round(0.9 + int(_u01("infill", round(x), round(y)) * 40) / 100, 2)])
    if infill:  # a tree on every lot nothing could be built on
        out["Infill trees"] = {"kind": "scatter", "role": "tree", "count": len(infill), "shown": len(infill), "points": infill,
                               "radius_cm": 250, "meshes": [], "build": "", "row": "x, y, z, yaw, scale"}
    return out


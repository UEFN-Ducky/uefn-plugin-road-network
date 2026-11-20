"""A lot's ground (pad, foundation, paths), pads, deep lots left out, lot and park trees."""

from __future__ import annotations

import math
import random

from .land import _Rects
from .samples import GROUNDS

BIG_SITE_M = 25.0  # wider than a lot (~17 m frontage, fits up to ~1.5x): offices, towers, civic, mansions take
# road frontage; apartments, shops, gas stations and houses fill the lots cut from the blocks
WALK_M, DRIVE_M, DRIVE2_M = 1.6, 3.5, 5.5  # walkway, single and double driveway widths
# lots whose front is a full-width sidewalk apron (plaza) to the kerb rather than a garden walkway
FRONTAGE_ROLES = ("shop", "mall", "office", "skyscraper", "apartment", "hospital", "school", "police",
                  "fire_station", "gas_station")
STEEP = 0.15  # a driveway steeper than 15 % winds across the slope instead of straight down it


def _lot_ground(layer: dict, sample_z, x: float, y: float, yaw: float, w_m: float, d_m: float) -> tuple:
    """A lot on a hill the way it is built: a level pad at the corners' mean height (cut uphill, fill
    downhill), a foundation as deep as the ground falls away under it, and the paths from its front to
    the kerb: a walkway from the door, a driveway at one side (winding when steep).
    Shops, offices, apartments and civic buildings get a full-width frontage (a sidewalk apron from the facade
    to the kerb, ramped from the pad to the street) instead of a narrow walkway. An uphill cut is held by the
    graded ground itself (citygen.cut_pads blends the pad back into the hill): no wall slabs on lot edges.
    The foundation is a real piece on every building (kit_bake.foundation); its depth here is a first figure,
    measured again on the ground the heightmap leaves once the pads are cut (citygen.footings).
    Returns (pad z cm, foundation m, paths [[width_m, [x, y, z, ...]]])."""
    fx, fy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))  # +X faces the street
    sx, sy = -fy, fx
    hd, hw = d_m * 50, w_m * 50
    corners = [sample_z(x + fx * a * hd + sx * b * hw, y + fy * a * hd + sy * b * hw) for a in (1, -1) for b in (1, -1)]
    ground = [z for z in corners if z is not None] or [sample_z(x, y) or 0.0]
    pad = sum(ground) / len(ground)
    found = round(max(0.0, pad - min(ground)) / 100, 1)
    reach = max(100.0, layer["depth_m"] * 50 + layer["setback_m"] * 100 - hd)  # front wall to kerb
    paths = []

    def path(width, off):
        ax, ay = x + fx * hd + sx * off, y + fy * hd + sy * off
        bx, by = ax + fx * reach, ay + fy * reach
        bz = sample_z(bx, by)
        bz = pad if bz is None else bz
        # straight from the door or the garage to the kerb (a driveway winding across a slope drew as a fan of
        # slats wider than its own bend, Nov 12 2026)
        paths.append([width, [round(v) for p in ((ax, ay, pad), (bx, by, bz)) for v in p]])

    # ONE strip a lot: a house's drive (it serves the door too), everything else its walkway or frontage. A
    # walkway and a driveway side by side in front of every house read as road spam
    if layer["driveway"] and layer["role"] in ("house", "mansion"):
        path(DRIVE_M, -hw * 0.3)
    elif layer["walkway"]:
        path(round(w_m * 0.9, 1) if layer["role"] in FRONTAGE_ROLES else WALK_M, 0.0)
    return pad, found, paths


FOUND_MAX_M = 6.0  # a house or a mansion is not built where the land falls further than this away under it ...
PODIUM_MAX_M = 14.0  # ... anything bigger stands on a podium (its foundation, storeys deep) down to this
SMALL_ROLES = ("house", "mansion")  # (a house on a wall taller than itself reads as a cliff of concrete; a tower on a
# four-storey podium is a city on a hill. At 4 m for everything, a hilly world lost four buildings in ten and its
# centre was blocks of trees with a tower here and there: 1,913 of 4,811 on the owner's Valley, Nov 13 2026)
PLANT_EVERY_CM = 800.0  # ... with a tree about every 8 m


# the steepest natural ground (best-fit plane under the whole footprint, before any pad is cut) each kind is built on
GRADE_MAX = {"warehouse": 0.06, "factory": 0.06, "mall": 0.06, "utility": 0.06, "parking": 0.06, "parking_garage": 0.06,
             "site": 0.06, "house": 0.25, "mansion": 0.25}
# apartments, shops, offices, civic. (15 % for houses and 10 % for these took 158 houses and 265 apartments out of
# test1's gentle hills, most of the winding suburbs stood empty, Nov 20 2026: a house steps down a 25 % lot)
GRADE_OTHER = 0.15
GRADE_PLANNED = 0.25  # a site the planner chose (the city's one hospital) may take a hillside, never a mountain face
STALL_MESH = "SM_Kit_ParkingStall"


def grade_under(ground, x: float, y: float, yaw: float, w_m: float, d_m: float):
    """(grade, relief m) of the natural ground under a footprint: the best-fit plane through a 3 x 3 grid of
    samples over it (corners, edge middles, centre), and how far its highest and lowest samples lie apart."""
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    z = {(a, b): ground(x + c * a * d_m * 50 - s * b * w_m * 50, y + s * a * d_m * 50 + c * b * w_m * 50)
         for a in (-1, 0, 1) for b in (-1, 0, 1)}
    if any(v is None for v in z.values()):
        return 0.0, 0.0
    gu = sum(z[(1, b)] - z[(-1, b)] for b in (-1, 0, 1)) / 3 / max(d_m * 100, 1.0)
    gv = sum(z[(a, 1)] - z[(a, -1)] for a in (-1, 0, 1)) / 3 / max(w_m * 100, 1.0)
    return math.hypot(gu, gv), (max(z.values()) - min(z.values())) / 100


def leave_out_steep(placed: dict, ground) -> tuple:
    """Before any pad is cut: take out every building and paved slab (car park, yard, apron) standing on natural
    ground steeper than its kind allows (GRADE_MAX), with the parking stalls on a slab that goes. A warehouse on a
    mountain face was levelled into a box on a 14 m podium and its car parks stepped up the slope as slabs (Nov 20
    2026): measured on the cut ground, every one of them passed. Parks, shipyards and anything laid at its own
    level stay. Returns (footprints taken out [(x, y, yaw, width_m, depth_m)], {layer: how many it lost})."""
    gone, lost, slabs = [], {}, []
    for name, v in placed.items():
        slab = v.get("kind") == "site" and v.get("role") == "site" and list((v.get("box_m") or [0, 0])[:2]) == [1, 1]
        if v.get("keep_z") or v.get("pad") is False or v.get("on_road") or not (slab or v.get("kind") == "buildings") \
                or v.get("role") == "park":
            continue
        keep = []
        for p in v.get("points") or []:
            w, d = (p[5], p[4]) if slab else (p[7], p[8]) if len(p) > 8 else (0, 0)
            role = "site" if slab else v.get("role")
            limit = GRADE_PLANNED if (not slab and len(p) > 12 and p[12]) else GRADE_MAX.get(role, GRADE_OTHER)
            if w and d and grade_under(ground, p[0], p[1], p[3], w, d)[0] > limit:
                gone.append((p[0], p[1], p[3], w, d))
                if slab:
                    slabs.append(gone[-1])
                continue
            keep.append(p)
        if len(keep) != len(v.get("points") or []):
            lost[name] = len(v["points"]) - len(keep)
            v["count"] = max(0, v.get("count", len(v["points"])) - lost[name])
            v["points"], v["shown"] = keep, len(keep)
    if slabs:  # the stalls painted on a slab that went
        def on(px, py):
            for x, y, yaw, w, d in slabs:
                c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
                u, t = (px - x) * c + (py - y) * s, -(px - x) * s + (py - y) * c
                if abs(u) <= d * 50 + 100 and abs(t) <= w * 50 + 100:
                    return True
            return False
        for name, v in placed.items():
            if v.get("mesh") == STALL_MESH:
                keep = [p for p in v["points"] if not on(p[0], p[1])]
                if len(keep) != len(v["points"]):
                    lost[name] = len(v["points"]) - len(keep)
                    v["points"], v["count"], v["shown"] = keep, len(keep), len(keep)
    return gone, lost


def leave_out_deep(placed: dict, max_m: float | None = None) -> tuple:
    """Take out every building that would stand on a foundation deeper than its kind allows (its foundation_m,
    measured on the cut ground): FOUND_MAX_M for a house or a mansion, PODIUM_MAX_M for everything bigger, which
    stands on a podium on a slope (`max_m`: one limit for all). Parks stay (planted ground, no slab), and so does
    a site the planner chose: a city keeps its one hospital even on a steep lot.
    Returns (footprints taken out [(x, y, yaw, width_m, depth_m)] for plant_lots, planned sites kept though deep,
    {layer: how many it lost})."""
    gone, kept_sites, lost = [], 0, {}
    for name, v in placed.items():
        if v.get("kind") != "buildings" or v.get("role") == "park":
            continue
        keep = []
        limit = max_m if max_m is not None else FOUND_MAX_M if v.get("role") in SMALL_ROLES else PODIUM_MAX_M
        for p in v.get("points") or []:
            if len(p) > 9 and p[9] > limit:
                if len(p) > 12 and p[12]:
                    kept_sites += 1
                else:
                    gone.append((p[0], p[1], p[3], p[7], p[8]))
                    continue
            keep.append(p)
        if len(keep) != len(v["points"]):
            lost[name] = len(v["points"]) - len(keep)
            v["count"] = max(0, v.get("count", len(v["points"])) - lost[name])
            v["points"], v["shown"] = keep, len(keep)
    return gone, kept_sites, lost


def plant_lots(placed: dict, lots: list, sample_z, sea=None) -> int:
    """Trees on the lots leave_out_deep emptied, in the 'Infill trees' layer with the lots nothing could be built
    on: a jittered grid over each footprint, standing on the ground as it is once the lot's pad is gone."""
    rows = []
    for x, y, yaw, w_m, d_m in lots:
        c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        nu, nv = max(1, int(d_m * 100 // PLANT_EVERY_CM)), max(1, int(w_m * 100 // PLANT_EVERY_CM))
        for i in range(nu):
            for j in range(nv):
                seed = abs(int(x) * 31 + int(y) * 17 + i * 7919 + j * 104729)
                u = -d_m * 50 + (i + 0.5) * d_m * 100 / nu + (seed % 97 - 48) / 48 * 150
                w = -w_m * 50 + (j + 0.5) * w_m * 100 / nv + (seed % 89 - 44) / 44 * 150
                px, py = x + c * u - s * w, y + s * u + c * w
                z = sample_z(px, py)
                if z is not None and not (sea is not None and z < sea):
                    rows.append([round(px), round(py), round(z), seed % 360, round(0.85 + (seed % 40) / 100, 2)])
    if rows:
        layer = placed.setdefault("Infill trees", {"kind": "scatter", "role": "tree", "count": 0, "shown": 0, "points": [],
                                                   "radius_cm": 250, "meshes": [], "build": "", "row": "x, y, z, yaw, scale"})
        layer["points"] = layer["points"] + rows
        layer["count"] = layer["shown"] = len(layer["points"])
    return len(rows)


def pads(previewed: dict, margin_cm: float = 400.0) -> list:
    """Every previewed lot as a level pad for the heightmap: (x, y, cos, sin, half width, half depth, z)."""
    out = []
    for v in previewed.values():
        if v.get("on_road"):
            continue  # lies on a road's own sidewalk: nothing to level
        if v.get("pad") is False:
            continue  # (a shipyard's paving: its whole platform is levelled already, citygen.cut_yards)
        if v.get("kind") == "site" and v.get("role") == "site" and list((v.get("box_m") or [0, 0])[:2]) == [1, 1]:
            # a development's slab (car park, apron, field, pool, yard): level under it too, or it lies half
            # buried on a slope with its stalls draped over the hill beside it
            for p in v["points"]:
                a = math.radians(p[3])
                out.append((p[0], p[1], math.cos(a), math.sin(a), p[5] * 50 + margin_cm / 2, p[4] * 50 + margin_cm / 2, p[2]))
            continue
        if v.get("kind") != "buildings" or v.get("role") == "park":
            continue  # (a park lies on the ground as it is: a levelled park was a green table on the hillside)
        for p in v["points"]:
            if len(p) > 9:
                a = math.radians(p[3])
                out.append((p[0], p[1], math.cos(a), math.sin(a), p[7] * 50 + margin_cm / 2, p[8] * 50 + margin_cm / 2, p[2]))
    return out


PARK_TREE_CM = 700.0  # a planned park's trees: one about every 7 m where a grove stands ...


def park_trees(placed: dict, cities: list, ground) -> dict:
    """The built-in layer 'Park trees': what a planned park (a row of a layer with role 'park') really is. Trees in
    groves with open lawns between them, standing on the ground as it lies, kept off the park's own playground,
    pond, field, car park, paths and benches (the city's grounds and car parks) and off every building. A park
    was drawn as one flat green slab the size of its lot, level, hanging over the hillside (Nov 13 2026)."""
    slabs = _Rects()
    for c in cities:
        priv = c.get("private") or {}
        for row in (priv.get("parking") or []) + (priv.get("grounds") or []):
            f = math.radians(row[2])
            thing = GROUNDS.get(row[5], (0, [1])) if len(row) > 5 else (0, [1])
            half = (row[4] * 50 + 150, row[3] * 50 + 150) if thing[1][0] == 1 else (150.0, 150.0)
            slabs.add((row[0], row[1], math.cos(f), math.sin(f), *half))
    for layer in placed.values():
        if layer.get("kind") == "buildings" and layer.get("role") != "park":
            for p in layer.get("points") or []:
                if len(p) > 8:
                    f = math.radians(p[3])
                    slabs.add((p[0], p[1], math.cos(f), math.sin(f), p[7] * 50 + 200, p[8] * 50 + 200))
    rows = []
    for layer in placed.values():
        if layer.get("kind") != "buildings" or layer.get("role") != "park":
            continue
        for p in layer.get("points") or []:
            if len(p) < 9:
                continue
            x, y, yaw, w, d = p[0], p[1], p[3], p[7] * 100, p[8] * 100
            fx, fy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
            rnd = random.Random(f"parktrees:{round(x)}:{round(y)}")
            waves = [(rnd.uniform(2500.0, 5500.0), rnd.uniform(0, 6.283), rnd.uniform(0, 6.283)) for _ in range(3)]
            nu, nv = max(1, int(d // PARK_TREE_CM)), max(1, int(w // PARK_TREE_CM))
            for i in range(nu):
                for j in range(nv):
                    u = -d / 2 + (i + 0.5 + rnd.uniform(-0.35, 0.35)) * d / nu
                    v = -w / 2 + (j + 0.5 + rnd.uniform(-0.35, 0.35)) * w / nv
                    scale, turn = round(0.85 + rnd.random() * 0.5, 2), round(rnd.uniform(0, 360))
                    tx, ty = x + fx * u - fy * v, y + fy * u + fx * v
                    edge = i in (0, nu - 1) or j in (0, nv - 1)  # the trees round its edge always stand
                    if not edge and sum(math.sin(tx / lam + a) * math.sin(ty / lam * 1.3 + b) for lam, a, b in waves) <= 0.0:
                        continue  # a lawn
                    if slabs.hit((tx, ty, 1.0, 0.0, 1.0, 1.0), 0.0):
                        continue
                    z = ground(tx, ty)
                    if z is not None:
                        rows.append([round(tx), round(ty), round(z), turn, scale])
    if not rows:
        return {}
    return {"Park trees": {"kind": "scatter", "role": "tree", "count": len(rows), "shown": len(rows), "points": rows,
                           "radius_cm": 250, "meshes": [], "build": "", "row": "x, y, z, yaw, scale"}}


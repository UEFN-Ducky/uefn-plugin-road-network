"""PCG placement layers for a road network world, and the PCG graph plan that builds them in UEFN.

A layer says WHAT goes WHERE: meshes (with weights) scattered on the ground, lined along the
roads, or set back from them as buildings; inside one district ring of every city, outside the
cities, or everywhere; avoiding the roads and the layers placed before it. ``plan()`` turns the
layers into one PCG graph, node by node in data-flow order, with the native node names and the
settings that worked in UEFN 43 (pcg skill, ``graph_layers``):

    sample -> Transform Points -> Bounds Modifier -> district mask -> Difference (Binary) against
    roads and earlier layers -> Self Pruning -> Static Mesh Spawner

Order matters: transform and bounds before pruning and differences (bad bounds = bad pruning),
and every Difference subtracts the *bounds-modified* points of the other set, never its spawner.
"""

from __future__ import annotations

import math
import re

KINDS = ("scatter", "along_roads", "buildings")
WHERE = ("outside", "cities", "downtown", "normal", "housing", "industrial", "everywhere")
INDUSTRIAL_SPREAD = 75.0  # degrees: each city's industrial zone is one wedge of its outer rings

# name: (default, min, max)
NUMBERS = {
    "density": (1.0, 0.01, 50.0),       # scatter: points per 100 m2
    "spacing_m": (25.0, 2.0, 1000.0),   # along roads / buildings: one every N m (gas stations ~650 m apart)
    "setback_m": (2.0, 0.0, 80.0),      # along roads / buildings: distance from the road EDGE (kerb)
    "scale_min": (0.9, 0.1, 10.0),
    "scale_max": (1.2, 0.1, 10.0),
    "sink_cm": (10.0, 0.0, 500.0),      # push into the ground so roots and bases never float
    "footprint": (0.8, 0.1, 2.0),       # bounds scale used for pruning and differences (1 = mesh bounds)
    "road_clear_m": (2.0, 0.0, 50.0),   # extra clearance past the road edge
    "width_m": (12.0, 1.0, 200.0),      # buildings: frontage along the road
    "depth_m": (12.0, 1.0, 200.0),      # buildings: depth away from the road
    "height_min_m": (6.0, 1.0, 600.0),  # buildings: height range (the preview draws it; pick meshes to match)
    "height_max_m": (9.0, 1.0, 600.0),
    "seed": (7, 0, 99999),
    "order": (0, 0, 99),
}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", str(text or "").strip().lower()).strip("-")[:40]


def clean(layer: dict) -> dict:
    """A layer with every field present and every value in range."""
    out = {"name": str(layer.get("name") or "Layer").strip()[:60]}
    out["kind"] = layer.get("kind") if layer.get("kind") in KINDS else "scatter"
    out["where"] = layer.get("where") if layer.get("where") in WHERE else "outside"
    for key, (default, lo, hi) in NUMBERS.items():
        try:
            v = float(layer.get(key, default))
        except (TypeError, ValueError):
            v = default
        v = max(lo, min(hi, v))
        out[key] = int(round(v)) if isinstance(default, int) else round(v, 4)
    if out["scale_max"] < out["scale_min"]:
        out["scale_min"], out["scale_max"] = out["scale_max"], out["scale_min"]
    if out["height_max_m"] < out["height_min_m"]:
        out["height_min_m"], out["height_max_m"] = out["height_max_m"], out["height_min_m"]
    meshes = []
    for m in layer.get("meshes") or []:
        path = str((m or {}).get("path") if isinstance(m, dict) else m or "").strip()
        if path:
            try:
                weight = max(0.01, float((m or {}).get("weight", 1) if isinstance(m, dict) else 1))
            except (TypeError, ValueError):
                weight = 1.0
            meshes.append({"path": path, "weight": weight})
    out["meshes"] = meshes
    out["avoid_roads"] = bool(layer.get("avoid_roads", True))
    out["avoid"] = [str(a) for a in (layer.get("avoid") or []) if str(a).strip()]
    out["self_prune"] = bool(layer.get("self_prune", True))
    out["upright"] = bool(layer.get("upright", out["kind"] != "scatter"))  # absolute rotation: no lean on slopes
    out["random_yaw"] = bool(layer.get("random_yaw", out["kind"] == "scatter"))
    out["collision"] = bool(layer.get("collision", out["kind"] != "scatter" or out["density"] < 2))
    out["enabled"] = bool(layer.get("enabled", True))
    return out


def _bld(name, where, order, spacing, setback, w, d, hmin, hmax, **extra):
    return {"name": name, "kind": "buildings", "where": where, "order": order, "spacing_m": spacing, "setback_m": setback,
            "width_m": w, "depth_m": d, "height_min_m": hmin, "height_max_m": hmax, "scale_min": 1, "scale_max": 1,
            "footprint": 1.0, **extra}


# Everything a city has, biggest first so smaller things fill the gaps between them.
CITY_BUILDINGS = [
    _bld("Skyscrapers", "downtown", 10, 48, 6, 32, 32, 90, 240),
    _bld("Office blocks", "downtown", 11, 34, 5, 26, 24, 30, 75, avoid=["Skyscrapers"]),
    _bld("Hospitals and schools", "normal", 12, 420, 12, 48, 40, 14, 28),
    _bld("Factories", "industrial", 13, 110, 10, 46, 34, 10, 20),  # gaps between them take the warehouses
    _bld("Warehouses", "industrial", 14, 44, 8, 34, 22, 8, 12, avoid=["Factories"]),
    _bld("Apartments", "normal", 15, 28, 6, 22, 18, 12, 32, avoid=["Hospitals and schools"]),
    _bld("Shops", "normal", 16, 14, 2, 12, 10, 4, 8, avoid=["Apartments", "Hospitals and schools"]),
    _bld("Gas stations", "cities", 17, 650, 4, 20, 16, 5, 6, avoid=["Skyscrapers", "Office blocks", "Apartments", "Shops"]),
    _bld("Houses", "housing", 18, 17, 10, 11, 14, 5, 8),
]


def preset(name: str = "full_city") -> list:
    """Starter layers. Meshes stay empty: the person (or the AI placing it) picks the project's own.
    'full_city': nature, street furniture and every kind of building; 'city': the buildings only;
    'forest_and_streets': nature, street trees, lamps and houses; 'forest': trees, rocks, bushes."""
    rows = [
        {"name": "Trees", "kind": "scatter", "where": "outside", "density": 0.8, "scale_min": 0.8, "scale_max": 1.4,
         "sink_cm": 15, "footprint": 0.35, "order": 1},
        {"name": "Rocks", "kind": "scatter", "where": "outside", "density": 0.2, "scale_min": 0.7, "scale_max": 2.0,
         "sink_cm": 40, "footprint": 0.8, "avoid": ["Trees"], "order": 2},
        {"name": "Bushes and grass", "kind": "scatter", "where": "everywhere", "density": 6, "scale_min": 0.8,
         "scale_max": 1.6, "sink_cm": 5, "footprint": 0.5, "collision": False, "avoid": ["Trees", "Rocks"], "order": 3},
        {"name": "Street trees", "kind": "along_roads", "where": "normal", "spacing_m": 22, "setback_m": 3,
         "scale_min": 0.9, "scale_max": 1.1, "random_yaw": True, "footprint": 0.4, "order": 4},
        {"name": "Street lamps", "kind": "along_roads", "where": "cities", "spacing_m": 30, "setback_m": 1.5,
         "scale_min": 1, "scale_max": 1, "footprint": 0.5, "avoid": ["Street trees"], "order": 5},
    ]
    if name == "forest":
        rows = rows[:3]
    elif name == "city":
        rows = [dict(b) for b in CITY_BUILDINGS]
    elif name == "forest_and_streets":
        rows += [dict(CITY_BUILDINGS[-1])]
    else:  # full_city: street trees and lamps keep clear of every building
        rows = [r for r in rows] + [dict(b) for b in CITY_BUILDINGS]
        for r in rows:
            if r["name"] in ("Street trees", "Street lamps"):
                r["order"] += 20
                r["avoid"] = sorted(set(r.get("avoid", [])) | {b["name"] for b in CITY_BUILDINGS})
    for r in rows:  # one seed per layer: two layers on the same seed sample the same spots and wipe each other out
        r["seed"] = 7 + r["order"] * 101
    return [clean(r) for r in rows]


def _ring(c: dict, radius: float, sides: int = 24) -> list:
    return [[round(c["x"] + radius * math.cos(2 * math.pi * k / sides)),
             round(c["y"] + radius * math.sin(2 * math.pi * k / sides))] for k in range(sides)]


def avoid_list(layer: dict, earlier: list) -> list:
    """Names this layer keeps clear of: its own list, plus every earlier building layer for a building
    (two buildings never share a lot, whatever their type)."""
    names = [a for a in layer["avoid"] if any(e["name"] == a for e in earlier)]
    if layer["kind"] == "buildings":
        names += [e["name"] for e in earlier if e["kind"] == "buildings" and e["name"] not in names]
    return names


def rings(c: dict) -> tuple:
    """(downtown, normal, outer) radii of one city, cm."""
    d = c.get("districts") or {}
    outer = d.get("housing", {}).get("radius", c["radius"])
    return d.get("downtown", {}).get("radius", c["radius"] * 0.4), d.get("normal", {}).get("radius", c["radius"] * 0.75), outer


def industrial_angle(c: dict) -> float:
    """Where a city's industrial wedge points (degrees): its own setting, else from its seed."""
    a = ((c.get("districts") or {}).get("industrial") or {}).get("angle")
    return float(a) if a is not None else float((int(c.get("seed", 0)) * 137) % 360)


def _in_wedge(c: dict, x: float, y: float) -> bool:
    ang = math.degrees(math.atan2(y - c["y"], x - c["x"]))
    return abs((ang - industrial_angle(c) + 180) % 360 - 180) <= INDUSTRIAL_SPREAD / 2


def _wedge(c: dict, steps: int = 10) -> list:
    """Annular sector between downtown and the outer ring: the industrial zone's outline."""
    down, _, outer = rings(c)
    a0 = math.radians(industrial_angle(c) - INDUSTRIAL_SPREAD / 2)
    span = math.radians(INDUSTRIAL_SPREAD)
    arc = lambda r: [[round(c["x"] + r * math.cos(a0 + span * k / steps)), round(c["y"] + r * math.sin(a0 + span * k / steps))]
                     for k in range(steps + 1)]
    return arc(outer) + arc(down)[::-1]


def masks(cities: list, where: str) -> tuple:
    """(keep, cut): a point must be in one keep polygon (None = anywhere) and in no cut polygon.
    Rings come from each city's districts; the industrial wedge is cut out of normal and housing."""
    if where == "everywhere" or not cities:
        return None, []
    r = {c["name"]: rings(c) for c in cities}
    wedges = [_wedge(c) for c in cities]
    if where == "outside":
        return None, [_ring(c, r[c["name"]][2]) for c in cities]
    if where == "cities":
        return [_ring(c, r[c["name"]][2]) for c in cities], []
    if where == "downtown":
        return [_ring(c, r[c["name"]][0]) for c in cities], []
    if where == "industrial":
        return wedges, []
    if where == "normal":
        return [_ring(c, r[c["name"]][1]) for c in cities], [_ring(c, r[c["name"]][0]) for c in cities] + wedges
    return [_ring(c, r[c["name"]][2]) for c in cities], [_ring(c, r[c["name"]][1]) for c in cities] + wedges  # housing


def plan(world: dict, layers: list, cities: list, road_tag: str, road_half_cm: float = 875.0) -> dict:
    """The PCG graph for every enabled layer, as nodes and edges an agent builds with Epic's
    PCGToolset (node names are native PCG node titles; settings use their camelCase keys)."""
    layers = sorted((clean(l) for l in layers if l.get("enabled", True)), key=lambda l: (l["order"], l["name"]))
    nodes, edges, params, notes = [], [], [], []

    def node(nid, kind, settings=None, comment=""):
        nodes.append({"id": nid, "node": kind, "settings": settings or {}, **({"comment": comment} if comment else {})})
        return nid

    def wire(a, b, pin="In", out="Out"):
        edges.append({"from": a, "out": out, "to": b, "in": pin})

    node("Ground", "Get Landscape Data", {}, "the surface every layer samples or projects onto")
    any_roads = any(l["kind"] != "scatter" or l["avoid_roads"] for l in layers)
    if any_roads:
        node("Roads", "Get Spline Data", {"actorFilter": "AllWorldActors", "actorSelection": "ByTag",
                                         "actorSelectionTag": road_tag, "bSelectMultiple": True},
             f"every road actor tagged {road_tag}")
        node("RoadPoints", "Spline Sampler", {"samplerParams": {"mode": "Distance", "distanceIncrement": 200}})
        wire("Roads", "RoadPoints", "Spline")
        # samples carry the road width as scale: shrink to the asphalt, then grow by the clearance
        node("RoadArea", "Bounds Modifier", {"mode": "Scale", "boundsMin": {"x": 1, "y": 0.42, "z": 1},
                                              "boundsMax": {"x": 1, "y": 0.42, "z": 1}},
             "road surface (scale.Y carries the road width)")
        wire("RoadPoints", "RoadArea")
    done: dict = {}
    for layer in layers:
        p = _slug(layer["name"]).replace("-", "_") or "layer"
        P = "".join(w.capitalize() for w in p.split("_"))
        params += [{"name": f"{P}Meshes", "type": "SoftObjectPath", "containerType": "Array",
                    "default": [m["path"] for m in layer["meshes"]]},
                   {"name": f"{P}Seed", "type": "Integer32", "default": layer["seed"]}]
        if layer["kind"] == "scatter":
            params.append({"name": f"{P}Density", "type": "Double", "default": layer["density"] / 100.0})
            src = node(f"{P}_Sample", "Surface Sampler", {"pointsPerSquaredMeter": layer["density"] / 100.0,
                                                          "looseness": 1.0, "seed": layer["seed"]},
                       f"{layer['name']}: {layer['density']} per 100 m2")
            wire("Ground", src, "Surface")
            edges.append({"param": f"{P}Density", "to": src, "in": "pointsPerSquaredMeter"})
        else:
            src = node(f"{P}_Sample", "Spline Sampler", {"samplerParams": {"mode": "Distance",
                                                                           "distanceIncrement": layer["spacing_m"] * 100}},
                       f"{layer['name']}: one every {layer['spacing_m']} m")
            wire("Roads", src, "Spline")
            node(f"{P}_Flat", "Transform Points", {"bAbsoluteScale": True, "scaleMin": {"x": 1, "y": 1, "z": 1},
                                                   "scaleMax": {"x": 1, "y": 1, "z": 1}},
                 "drop the road width the samples carry as scale")
            wire(src, f"{P}_Flat")
            # the graph offsets by a fixed distance: the world's most common road half width + the setback
            back = road_half_cm + layer["setback_m"] * 100 + (layer["depth_m"] * 50 if layer["kind"] == "buildings" else 0)
            node(f"{P}_Left", "Transform Points", {"offsetMin": {"x": 0, "y": back, "z": 0}, "offsetMax": {"x": 0, "y": back, "z": 0},
                                                   "rotationMin": {"pitch": 0, "yaw": -90, "roll": 0},
                                                   "rotationMax": {"pitch": 0, "yaw": -90, "roll": 0}},
                 "left side, facing the road (mesh front is +X)")
            node(f"{P}_Right", "Transform Points", {"offsetMin": {"x": 0, "y": -back, "z": 0}, "offsetMax": {"x": 0, "y": -back, "z": 0},
                                                    "rotationMin": {"pitch": 0, "yaw": 90, "roll": 0},
                                                    "rotationMax": {"pitch": 0, "yaw": 90, "roll": 0}}, "right side")
            node(f"{P}_Sides", "Merge Points")
            for side in ("Left", "Right"):
                wire(f"{P}_Flat", f"{P}_{side}")
                wire(f"{P}_{side}", f"{P}_Sides")
            node(f"{P}_OnGround", "Projection", {"bProjectPositions": True, "bProjectRotations": False,
                                                 "bProjectScales": False}, "onto the landscape")
            wire(f"{P}_Sides", f"{P}_OnGround")
            wire("Ground", f"{P}_OnGround", "Projection Target")
            src = f"{P}_OnGround"
        tr = node(f"{P}_Vary", "Transform Points", {
            "offsetMin": {"x": 0, "y": 0, "z": -layer["sink_cm"]}, "offsetMax": {"x": 0, "y": 0, "z": -layer["sink_cm"] * 0.5},
            "rotationMin": {"pitch": 0, "yaw": 0, "roll": 0},
            "rotationMax": {"pitch": 0, "yaw": 360 if layer["random_yaw"] else 0, "roll": 0},
            "bAbsoluteRotation": layer["upright"] and layer["kind"] == "scatter",
            "scaleMin": {"x": layer["scale_min"]} | {"y": layer["scale_min"], "z": layer["scale_min"]},
            "scaleMax": {"x": layer["scale_max"]} | {"y": layer["scale_max"], "z": layer["scale_max"]},
            "bUniformScale": True}, "random size and turn, sunk into the ground")
        wire(src, tr)
        if layer["kind"] == "buildings":  # a building's lot: frontage x depth (the point faces the road)
            hw, hd = layer["width_m"] * 50 * layer["footprint"], layer["depth_m"] * 50 * layer["footprint"]
            bm = node(f"{P}_Footprint", "Bounds Modifier", {"mode": "Set", "boundsMin": {"x": -hd, "y": -hw, "z": 0},
                                                            "boundsMax": {"x": hd, "y": hw, "z": layer["height_max_m"] * 100}},
                      f"lot {layer['width_m']} x {layer['depth_m']} m, up to {layer['height_max_m']} m tall")
        else:
            bm = node(f"{P}_Footprint", "Bounds Modifier", {"mode": "Scale",
                                                            "boundsMin": {"x": layer["footprint"], "y": layer["footprint"], "z": 1},
                                                            "boundsMax": {"x": layer["footprint"], "y": layer["footprint"], "z": 1}},
                      "bounds = what pruning and differences see: shrink to the trunk or base")
        wire(tr, bm)
        cur = bm
        keep, cut = masks(cities, layer["where"])
        if keep is not None or cut:
            tall = node(f"{P}_Tall", "Bounds Modifier", {"mode": "Set", "boundsMin": {"z": -100000}, "boundsMax": {"z": 100000}},
                        "tall bounds: a 2D polygon only meets points that reach z = 0")
            wire(cur, tall)
            cur = tall
        if keep is not None:
            node(f"{P}_Area", "Create Points", {"polygons": keep, "coordinateSpace": "World"},
                 f"district outline(s): {layer['where']} (one closed polygon per city)")
            node(f"{P}_Poly", "Create Polygon 2D")
            wire(f"{P}_Area", f"{P}_Poly")
            nxt = node(f"{P}_InArea", "Intersection")
            wire(cur, nxt, "Primary Source")
            wire(f"{P}_Poly", nxt, "Source")
            cur = nxt
        if cut:
            node(f"{P}_Cut", "Create Points", {"polygons": cut, "coordinateSpace": "World"},
                 "areas this layer keeps out of (inner rings, industrial zones, the cities)")
            node(f"{P}_CutPoly", "Create Polygon 2D")
            wire(f"{P}_Cut", f"{P}_CutPoly")
            out_ = node(f"{P}_NotThere", "Difference", {"densityFunction": "Binary"})
            wire(cur, out_, "Source")
            wire(f"{P}_CutPoly", out_, "Differences")
            cur = out_
        avoid = avoid_list(layer, [l for l in layers if l["name"] in done])
        if layer["avoid_roads"] and any_roads:
            gap = node(f"{P}_RoadGap", "Bounds Modifier", {"mode": "Scale", "boundsMin": {"x": 1, "y": 1 + layer["road_clear_m"] / 10, "z": 1},
                                                             "boundsMax": {"x": 1, "y": 1 + layer["road_clear_m"] / 10, "z": 1}},
                       f"road plus {layer['road_clear_m']} m clearance")
            wire("RoadArea", gap)
            d = node(f"{P}_OffRoad", "Difference", {"densityFunction": "Binary"}, "nothing on the roads")
            wire(cur, d, "Source")
            wire(gap, d, "Differences")
            cur = d
        for other in avoid:
            d = node(f"{P}_Not{done[other]['P']}", "Difference", {"densityFunction": "Binary"}, f"keep clear of {other}")
            wire(cur, d, "Source")
            wire(done[other]["bounds"], d, "Differences")
            cur = d
        if layer["self_prune"]:
            sp = node(f"{P}_NoOverlap", "Self Pruning", {"pruningType": "LargeToSmall", "bRandomizedPruning": False},
                      "after transform + bounds, or it prunes the wrong sizes")
            wire(cur, sp)
            cur = sp
        spawn = node(f"{P}_Spawn", "Static Mesh Spawner", {
            "meshSelectorType": "PCGMeshSelectorWeighted",
            "meshEntries": [{"mesh": m["path"], "weight": m["weight"],
                             "collision": "Default" if layer["collision"] else "NoCollision"} for m in layer["meshes"]]},
            f"{layer['name']}: {len(layer['meshes'])} meshes, collision {'on' if layer['collision'] else 'off'}")
        wire(cur, spawn)
        edges.append({"param": f"{P}Meshes", "to": spawn, "in": "meshes"})
        edges.append({"param": f"{P}Seed", "to": spawn, "in": "seed"})
        if not layer["meshes"]:
            notes.append(f"{layer['name']} has no meshes yet: pick the project's own (its Kit / City folders) before running it.")
        if not layer["collision"] and layer["density"] >= 2:
            notes.append(f"{layer['name']}: collision off (dense scatter with collision freezes the editor and blocks players).")
        done[layer["name"]] = {"P": P, "bounds": bm}
    span = 0.0
    for c in cities:
        span = max(span, abs(c["x"]) + c["radius"], abs(c["y"]) + c["radius"])
    size = (world.get("generated") or {}).get("size_km")
    half = (size * 50000.0) if size else max(span * 1.15, 20000.0)
    return {"ok": True, "graph": f"PCG_{_slug(world.get('name') or 'World').replace('-', '_').title().replace('_', '')}Layers",
            "road_tag": road_tag, "layers": [l["name"] for l in layers], "nodes": nodes, "edges": edges,
            "params": params, "volume": {"location": [0, 0, 0], "scale": [round(half / 100, 1), round(half / 100, 1), 600]},
            "notes": notes}


# ------------------------------------------------------------------ offline preview
BASE_RADIUS = {"scatter": 250.0, "along_roads": 150.0, "buildings": 700.0}  # cm, at footprint 1 and scale 1


class _Hash:
    """Points by 20 m cell: who is within r of (x, y)."""

    def __init__(self, cell: float = 2000.0):
        self.cell, self.cells = cell, {}

    def add(self, x, y, r, item=None):
        self.cells.setdefault((int(x // self.cell), int(y // self.cell)), []).append((x, y, r, item))

    def near(self, x, y, r):
        k = int(math.ceil((r + 2000.0) / self.cell))
        cx, cy = int(x // self.cell), int(y // self.cell)
        for i in range(cx - k, cx + k + 1):
            for j in range(cy - k, cy + k + 1):
                for px, py, pr, item in self.cells.get((i, j), ()):
                    if math.hypot(px - x, py - y) < r + pr:
                        yield item


def road_samples(actors: list, step: float = 500.0) -> list:
    """(x, y, half_width, dir_x, dir_y) every ~step cm along every meshed road segment."""
    out = []
    for pts, segs in actors:
        by_i = {p["i"]: p for p in pts}
        for s in segs:
            a, b = by_i.get(s["a"]), by_i.get(s["b"])
            if not a or not b or not s["mesh"]:
                continue
            dx, dy = b["x"] - a["x"], b["y"] - a["y"]
            L = math.hypot(dx, dy)
            if L < 1:
                continue
            half = max(300.0, min(a["w"], b["w"]))
            for k in range(max(1, int(L // step)) + 1):
                t = k / max(1, int(L // step))
                out.append((a["x"] + dx * t, a["y"] + dy * t, half, dx / L, dy / L))
    return out


def _in_where(where: str, cities: list, x: float, y: float) -> bool:
    """Same areas as masks(): rings per city, the industrial wedge cut out of normal and housing."""
    if where == "everywhere" or not cities:
        return True
    for c in cities:
        d = math.hypot(x - c["x"], y - c["y"])
        down, normal, outer = rings(c)
        if where == "outside" and d < outer:
            return False
        if d >= outer:
            continue
        wedge = d >= down and _in_wedge(c, x, y)
        if (where == "cities") or (where == "downtown" and d < down) or (where == "industrial" and wedge) or \
                (where == "normal" and down <= d < normal and not wedge) or \
                (where == "housing" and normal <= d < outer and not wedge):
            return True
    return where == "outside"


def preview(layers: list, cities: list, terrain: dict, roads: list, sample_z, limit: int = 2500) -> dict:
    """Run every enabled layer offline with the same rules as the graph (mask, off roads, keep clear of
    earlier layers, self pruning, sink) and return {layer name: {points: [[x, y, z, yaw, scale]], ...}}.
    Scatter is thinned to ``limit`` points per layer for the view; ``count`` says the full estimate."""
    import random
    layers = sorted((clean(l) for l in layers if l.get("enabled", True)), key=lambda l: (l["order"], l["name"]))
    lo, hi = terrain.get("min"), terrain.get("max")
    if not lo or not hi:
        xs = [r[0] for r in roads] + [c["x"] for c in cities] or [0]
        ys = [r[1] for r in roads] + [c["y"] for c in cities] or [0]
        lo, hi = [min(xs) - 20000, min(ys) - 20000], [max(xs) + 20000, max(ys) + 20000]
    sea = terrain.get("sea")
    road_hash = _Hash()
    for x, y, half, _, _ in roads:
        road_hash.add(x, y, half)
    kept: dict = {}
    out = {}
    for layer in layers:
        rnd = random.Random(f"{layer['seed']}:{layer['name']}")  # different densities never share spots in PCG either
        cands = []
        if layer["kind"] == "scatter":
            area_m2 = (hi[0] - lo[0]) * (hi[1] - lo[1]) / 1e4
            full = area_m2 * layer["density"] / 100.0
            n = int(min(full, limit * 4))  # candidates before masks and pruning
            for _ in range(n):
                cands.append((rnd.uniform(lo[0], hi[0]), rnd.uniform(lo[1], hi[1]), rnd.uniform(0, 360)))
            thin = full / n if n else 1.0
        else:
            spacing = layer["spacing_m"] * 100
            acc = 0.0
            last = None
            for x, y, half, dx, dy in roads:
                if last is not None and math.hypot(x - last[0], y - last[1]) > 2000:
                    last = None  # a jump to another segment: count on from here (a 420 m spacing spans many 100 m streets)
                acc += math.hypot(x - last[0], y - last[1]) if last else 0.0
                if not cands and acc == 0.0:
                    acc = spacing  # the very first road point places one
                last = (x, y)
                if acc < spacing:
                    continue
                acc = 0.0
                # from this road's kerb, whatever its width; a building's centre is half its depth further back
                back = half + layer["setback_m"] * 100 + (layer["depth_m"] * 50 if layer["kind"] == "buildings" else 0)
                for side in (1, -1):  # normal to the road, both sides, facing it
                    nx, ny = -dy * side, dx * side
                    yaw = math.degrees(math.atan2(-ny, -nx))
                    cands.append((x + nx * back, y + ny * back, yaw))
            thin = 1.0
        r_base = BASE_RADIUS[layer["kind"]] * layer["footprint"]
        if layer["kind"] == "buildings":  # its lot, not a fixed size: a skyscraper needs far more room than a shop
            r_base = 50.0 * max(layer["width_m"], layer["depth_m"]) * layer["footprint"]
        own = _Hash()
        pts = []
        avoid = avoid_list(layer, [l for l in layers if l["name"] in kept])
        for x, y, yaw in cands:
            if not _in_where(layer["where"], cities, x, y):
                continue
            z = sample_z(x, y)
            if z is None or (sea is not None and z < sea + 50):
                continue  # nothing grows under water or off the map
            s = rnd.uniform(layer["scale_min"], layer["scale_max"])
            r = r_base * s
            # scatter keeps the clearance; roadside layers sit at their setback, so only their own size counts
            clear = layer["road_clear_m"] * 100 if layer["kind"] == "scatter" else 0.0
            if layer["avoid_roads"] and any(True for _ in road_hash.near(x, y, r + clear)):
                continue
            if any(True for other in avoid for _ in kept[other].near(x, y, r)):
                continue
            if layer["self_prune"] and any(True for _ in own.near(x, y, r)):
                continue
            own.add(x, y, r)
            yaw = yaw if layer["random_yaw"] or layer["kind"] != "scatter" else 0.0
            row = [round(x), round(y), round(z - layer["sink_cm"]), round(yaw), round(s, 2)]
            if layer["kind"] == "buildings":  # + its height in metres, so the preview draws a real skyline
                row.append(round(rnd.uniform(layer["height_min_m"], layer["height_max_m"]), 1))
            pts.append(row)
        kept[layer["name"]] = own
        count = int(len(pts) * thin) if layer["kind"] == "scatter" else len(pts)
        if len(pts) > limit:
            pts = rnd.sample(pts, limit)
        out[layer["name"]] = {"kind": layer["kind"], "count": count, "shown": len(pts), "points": pts,
                              "radius_cm": round(r_base),
                              **({"size_m": [layer["width_m"], layer["depth_m"]]} if layer["kind"] == "buildings" else {})}
    return out


def _demo() -> None:
    cities = [{"name": "A", "x": 0, "y": 0, "radius": 50000, "districts": {"downtown": {"radius": 20000},
               "normal": {"radius": 37500}, "housing": {"radius": 50000}, "industrial": {"angle": 90}}}]
    layers = preset("forest_and_streets")
    assert len(layers) == 6 and all(l["meshes"] == [] for l in layers)
    assert len(preset()) == 5 + len(CITY_BUILDINGS), "the full city has nature, street furniture and every building"
    out = plan({"name": "Valley", "generated": {"size_km": 6}}, layers, cities, "valley_roads")
    ids = {n["id"] for n in out["nodes"]}
    assert all(e["to"] in ids and (e.get("from") in ids or "param" in e) for e in out["edges"]), "every wire joins real nodes"
    order = [n["id"] for n in out["nodes"]]
    for p in ("Trees", "Rocks", "StreetLamps"):
        assert order.index(f"{p}_Vary") < order.index(f"{p}_Footprint") < order.index(f"{p}_NoOverlap") < order.index(f"{p}_Spawn"), p
    assert "Rocks_NotTrees" in ids and "BushesAndGrass_NotRocks" in ids and "StreetLamps_NotStreetTrees" in ids
    assert any(e["to"] == "Rocks_NotTrees" and e["from"] == "Trees_Footprint" for e in out["edges"] if "from" in e), \
        "a difference subtracts the other layer's bounds, never its spawner"
    assert "Houses_InArea" in ids and "Houses_NotThere" in ids and "Trees_NotThere" in ids
    assert out["volume"]["scale"][0] == 3000.0 and len(out["notes"]) >= 6
    # offline preview: a straight road through a city, every layer placed, nothing on the road, no overlaps
    pts = [{"i": 0, "x": -60000, "y": 0, "w": 875}, {"i": 1, "x": 60000, "y": 0, "w": 875}]
    segs = [{"a": 0, "b": 1, "mesh": "SM_Road_TwoLane"}]
    roads = road_samples([(pts, segs)])
    ter = {"min": [-80000, -80000], "max": [80000, 80000]}
    for l in layers:
        l["meshes"] = [{"path": "/x/SM", "weight": 1}]
    pv = preview(layers, cities, ter, roads, lambda x, y: 0.0)
    assert all(pv[n]["shown"] > 0 for n in ("Trees", "Rocks", "Street trees", "Street lamps", "Houses")), \
        {n: v["shown"] for n, v in pv.items()}
    for name, v in pv.items():
        for x, y, *_ in v["points"]:
            assert abs(y) > 875 or abs(x) > 60000, f"{name} on the road at {x},{y}"
    trees = pv["Trees"]["points"]
    assert all(math.hypot(x, y) >= 50000 for x, y, *_ in trees), "trees stay outside the city"
    houses = pv["Houses"]["points"]
    assert all(37500 <= math.hypot(x, y) < 50000 for x, y, *_ in houses), "houses only in the housing ring"
    assert all(abs(abs(y) - 2575) < 2 for _, y, *_ in houses), "a house's centre: kerb 875 + setback 1000 + half depth 700"
    # the whole city: a north-south road too, so the industrial wedge (pointing +y) has a street
    pts2 = pts + [{"i": 2, "x": 0, "y": -60000, "w": 875}, {"i": 3, "x": 0, "y": 60000, "w": 875}]
    segs2 = segs + [{"a": 2, "b": 3, "mesh": "SM_Road_TwoLane"}]
    city = preset("city")
    pv2 = preview(city, cities, ter, road_samples([(pts2, segs2)]), lambda x, y: 0.0)
    for name in ("Skyscrapers", "Office blocks", "Factories", "Warehouses", "Apartments", "Shops", "Houses"):
        assert pv2[name]["shown"] > 0, (name, {n: v["shown"] for n, v in pv2.items()})
    sky = pv2["Skyscrapers"]["points"]
    assert all(math.hypot(p[0], p[1]) < 20000 and 90 <= p[5] <= 240 for p in sky), "skyscrapers downtown, 90-240 m"
    for p in pv2["Factories"]["points"]:
        assert 20000 <= math.hypot(p[0], p[1]) < 50000 and _in_wedge(cities[0], p[0], p[1]), "factories in the industrial zone"
    for name in ("Apartments", "Shops", "Houses"):
        assert not any(_in_wedge(cities[0], p[0], p[1]) for p in pv2[name]["points"]), f"no {name} in the industrial zone"
    lots = [(p[0], p[1], 50 * max(*pv2[n]["size_m"])) for n in pv2 for p in pv2[n]["points"]]
    clash = sum(1 for i, a in enumerate(lots) for b in lots[i + 1:] if math.hypot(a[0] - b[0], a[1] - b[1]) < (a[2] + b[2]) * 0.7)
    assert clash == 0, f"{clash} buildings overlap"
    bad = clean({"name": "x", "kind": "nope", "density": -5, "scale_min": 3, "scale_max": 1})
    assert bad["kind"] == "scatter" and bad["density"] == 0.01 and bad["scale_min"] == 1 and bad["scale_max"] == 3
    print("pcg layers ok", len(out["nodes"]), "nodes", len(out["edges"]), "wires")


if __name__ == "__main__":
    _demo()

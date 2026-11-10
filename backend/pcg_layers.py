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
WHERE = ("outside", "cities", "downtown", "normal", "housing", "everywhere")

# name: (default, min, max)
NUMBERS = {
    "density": (1.0, 0.01, 50.0),       # scatter: points per 100 m2
    "spacing_m": (25.0, 2.0, 200.0),    # along roads / buildings: one every N m along the road
    "setback_m": (2.0, 0.0, 80.0),      # along roads / buildings: distance from the road EDGE (kerb)
    "scale_min": (0.9, 0.1, 10.0),
    "scale_max": (1.2, 0.1, 10.0),
    "sink_cm": (10.0, 0.0, 500.0),      # push into the ground so roots and bases never float
    "footprint": (0.8, 0.1, 2.0),       # bounds scale used for pruning and differences (1 = mesh bounds)
    "road_clear_m": (2.0, 0.0, 50.0),   # extra clearance past the road edge
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


def preset(name: str = "forest_and_streets") -> list:
    """Starter layers. Meshes stay empty: the person (or the AI placing it) picks the project's own."""
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
        {"name": "Houses", "kind": "buildings", "where": "housing", "spacing_m": 17, "setback_m": 10,
         "scale_min": 1, "scale_max": 1, "footprint": 1.0, "order": 6},
    ]
    if name == "forest":
        rows = rows[:3]
    for r in rows:  # one seed per layer: two layers on the same seed sample the same spots and wipe each other out
        r["seed"] = 7 + r["order"] * 101
    return [clean(r) for r in rows]


def _ring(c: dict, radius: float, sides: int = 24) -> list:
    return [[round(c["x"] + radius * math.cos(2 * math.pi * k / sides)),
             round(c["y"] + radius * math.sin(2 * math.pi * k / sides))] for k in range(sides)]


def masks(cities: list, where: str) -> tuple:
    """(mode, polygons): 'inside' keeps points in any polygon, 'outside' drops them, 'none' keeps all.
    Rings come from each city's districts; 'normal' is the band between downtown and normal."""
    if where == "everywhere" or not cities:
        return "none", []
    if where == "outside":
        return "outside", [_ring(c, (c.get("districts") or {}).get("housing", {}).get("radius", c["radius"])) for c in cities]
    if where == "cities":
        return "inside", [_ring(c, c["radius"]) for c in cities]
    rad = lambda c, ring: (c.get("districts") or {}).get(ring, {}).get("radius", 0)
    if where == "downtown":
        return "inside", [_ring(c, rad(c, "downtown")) for c in cities]
    if where == "normal":  # band: inside normal, then a second mask removes downtown
        return "band", [[_ring(c, rad(c, "normal")) for c in cities], [_ring(c, rad(c, "downtown")) for c in cities]]
    return "band", [[_ring(c, c["radius"]) for c in cities], [_ring(c, rad(c, "normal")) for c in cities]]  # housing


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
            back = road_half_cm + layer["setback_m"] * 100
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
        bm = node(f"{P}_Footprint", "Bounds Modifier", {"mode": "Scale",
                                                        "boundsMin": {"x": layer["footprint"], "y": layer["footprint"], "z": 1},
                                                        "boundsMax": {"x": layer["footprint"], "y": layer["footprint"], "z": 1}},
                  "bounds = what pruning and differences see: shrink to the trunk or base")
        wire(tr, bm)
        cur = bm
        mode, polys = masks(cities, layer["where"])
        if mode != "none":
            keep = polys[0] if mode == "band" else polys
            node(f"{P}_Area", "Create Points", {"points": [[x, y] for poly in keep for x, y in poly], "polygons": keep,
                                                 "coordinateSpace": "World"},
                 f"district outline(s): {layer['where']} (one closed polygon per city)")
            node(f"{P}_Poly", "Create Polygon 2D")
            wire(f"{P}_Area", f"{P}_Poly")
            tall = node(f"{P}_Tall", "Bounds Modifier", {"mode": "Set", "boundsMin": {"z": -100000}, "boundsMax": {"z": 100000}},
                        "tall bounds: a 2D polygon only meets points that reach z = 0")
            wire(cur, tall)
            op = "Difference" if mode == "outside" else "Intersection"
            nxt = node(f"{P}_InArea", op, {"densityFunction": "Binary"} if op == "Difference" else {})
            wire(tall, nxt, "Source" if op == "Difference" else "Primary Source")
            wire(f"{P}_Poly", nxt, "Differences" if op == "Difference" else "Source")
            cur = nxt
            if mode == "band":
                node(f"{P}_Inner", "Create Points", {"polygons": polys[1], "coordinateSpace": "World"}, "inner ring to cut out")
                node(f"{P}_InnerPoly", "Create Polygon 2D")
                wire(f"{P}_Inner", f"{P}_InnerPoly")
                band = node(f"{P}_Band", "Difference", {"densityFunction": "Binary"})
                wire(cur, band, "Source")
                wire(f"{P}_InnerPoly", band, "Differences")
                cur = band
        avoid = [a for a in layer["avoid"] if a in done]
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
    if where == "everywhere" or not cities:
        return True
    for c in cities:
        d = math.hypot(x - c["x"], y - c["y"])
        rings = c.get("districts") or {}
        down = rings.get("downtown", {}).get("radius", c["radius"] * 0.4)
        normal = rings.get("normal", {}).get("radius", c["radius"] * 0.75)
        outer = rings.get("housing", {}).get("radius", c["radius"])
        if where == "outside" and d < outer:
            return False
        if (where == "cities" and d < outer) or (where == "downtown" and d < down) or \
                (where == "normal" and down <= d < normal) or (where == "housing" and normal <= d < outer):
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
                    acc = 0.0  # a new road
                acc += math.hypot(x - last[0], y - last[1]) if last else spacing
                last = (x, y)
                if acc < spacing:
                    continue
                acc = 0.0
                back = half + layer["setback_m"] * 100  # from this road's kerb, whatever its width
                for side in (1, -1):  # normal to the road, both sides, facing it
                    nx, ny = -dy * side, dx * side
                    yaw = math.degrees(math.atan2(-ny, -nx))
                    cands.append((x + nx * back, y + ny * back, yaw))
            thin = 1.0
        r_base = BASE_RADIUS[layer["kind"]] * layer["footprint"]
        own = _Hash()
        pts = []
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
            if any(True for other in layer["avoid"] if other in kept for _ in kept[other].near(x, y, r)):
                continue
            if layer["self_prune"] and any(True for _ in own.near(x, y, r)):
                continue
            own.add(x, y, r)
            yaw = yaw if layer["random_yaw"] or layer["kind"] != "scatter" else 0.0
            pts.append([round(x), round(y), round(z - layer["sink_cm"]), round(yaw), round(s, 2)])
        kept[layer["name"]] = own
        count = int(len(pts) * thin) if layer["kind"] == "scatter" else len(pts)
        if len(pts) > limit:
            pts = rnd.sample(pts, limit)
        out[layer["name"]] = {"kind": layer["kind"], "count": count, "shown": len(pts), "points": pts,
                              "radius_cm": round(r_base)}
    return out


def _demo() -> None:
    cities = [{"name": "A", "x": 0, "y": 0, "radius": 50000, "districts": {"downtown": {"radius": 20000},
               "normal": {"radius": 37500}, "housing": {"radius": 50000}}}]
    layers = preset()
    assert len(layers) == 6 and all(l["meshes"] == [] for l in layers)
    out = plan({"name": "Valley", "generated": {"size_km": 6}}, layers, cities, "valley_roads")
    ids = {n["id"] for n in out["nodes"]}
    assert all(e["to"] in ids and (e.get("from") in ids or "param" in e) for e in out["edges"]), "every wire joins real nodes"
    order = [n["id"] for n in out["nodes"]]
    for p in ("Trees", "Rocks", "StreetLamps"):
        assert order.index(f"{p}_Vary") < order.index(f"{p}_Footprint") < order.index(f"{p}_NoOverlap") < order.index(f"{p}_Spawn"), p
    assert "Rocks_NotTrees" in ids and "BushesAndGrass_NotRocks" in ids and "StreetLamps_NotStreetTrees" in ids
    assert any(e["to"] == "Rocks_NotTrees" and e["from"] == "Trees_Footprint" for e in out["edges"] if "from" in e), \
        "a difference subtracts the other layer's bounds, never its spawner"
    assert "Houses_Band" in ids and "Trees_InArea" in ids
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
        for x, y, z, yaw, s in v["points"]:
            assert abs(y) > 875 or abs(x) > 60000, f"{name} on the road at {x},{y}"
    trees = pv["Trees"]["points"]
    assert all(math.hypot(x, y) >= 50000 for x, y, *_ in trees), "trees stay outside the city"
    houses = pv["Houses"]["points"]
    assert all(37500 <= math.hypot(x, y) < 50000 for x, y, *_ in houses), "houses only in the housing ring"
    assert all(abs(abs(y) - 1875) < 2 for _, y, *_ in houses), "houses sit 10 m past the kerb (875 + 1000)"
    bad = clean({"name": "x", "kind": "nope", "density": -5, "scale_min": 3, "scale_max": 1})
    assert bad["kind"] == "scatter" and bad["density"] == 0.01 and bad["scale_min"] == 1 and bad["scale_max"] == 3
    print("pcg layers ok", len(out["nodes"]), "nodes", len(out["edges"]), "wires")


if __name__ == "__main__":
    _demo()

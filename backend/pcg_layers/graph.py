"""District rings and masks, and plan(): the layers as one PCG graph."""

from __future__ import annotations

import math

from ..citygen import core_reach, outline  # every district edge follows the city's irregular outline
from .layers import ASSET_SETS, FORMS, INDUSTRIAL_SPREAD, STOREY_M, _slug, asset_hint, clean
from .samples import junction_corners

def _ring(c: dict, radius: float, sides: int = 48) -> list:
    """A district edge: the ring radius bent by the city's outline, never a circle; round the city's centre
    district where that was moved (citygen.zone_share)."""
    out = []
    reach = core_reach(c)
    for k in range(sides):
        a = 2 * math.pi * k / sides
        if reach:
            r = reach[round(math.degrees(a)) % 360] * radius / c["radius"]
            out.append([round(c["x"] + c["core"][0] + r * math.cos(a)), round(c["y"] + c["core"][1] + r * math.sin(a))])
            continue
        r = radius * outline(c, math.degrees(a))
        out.append([round(c["x"] + r * math.cos(a)), round(c["y"] + r * math.sin(a))])
    return out


def avoid_list(layer: dict, earlier: list) -> list:
    """Names this layer keeps clear of: its own list, plus every earlier building layer for a building or
    street layer (two buildings never share a lot, and no lamp, sign or bench stands inside one)."""
    names = [a for a in layer["avoid"] if any(e["name"] == a for e in earlier)]
    if layer["kind"] in ("buildings", "along_roads", "at_junctions"):  # nothing of the city stands in a lot
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
    return float(a) if a is not None else float(((int(c.get("seed", 0)) * 137) % 360 + float(c.get("spin") or 0.0)) % 360)


def _in_wedge(c: dict, x: float, y: float) -> bool:
    ang = math.degrees(math.atan2(y - c["y"], x - c["x"]))
    return abs((ang - industrial_angle(c) + 180) % 360 - 180) <= INDUSTRIAL_SPREAD / 2


def _wedge(c: dict, steps: int = 10) -> list:
    """Annular sector between downtown and the outer ring: the industrial zone's outline."""
    down, _, outer = rings(c)
    a0 = math.radians(industrial_angle(c) - INDUSTRIAL_SPREAD / 2)
    span = math.radians(INDUSTRIAL_SPREAD)
    def arc(r):
        pts = []
        for k in range(steps + 1):
            a = a0 + span * k / steps
            rr = r * outline(c, math.degrees(a))
            pts.append([round(c["x"] + rr * math.cos(a)), round(c["y"] + rr * math.sin(a))])
        return pts
    return arc(outer) + arc(down)[::-1]


def masks(cities: list, where: str) -> tuple:
    """(keep, cut): a point must be in one keep polygon (None = anywhere) and in no cut polygon.
    Rings come from each city's districts; the industrial wedge is cut out of normal and housing."""
    if where == "everywhere" or not cities:
        return None, []
    if where == "shipyards":  # every shipyard's paved platform
        from .. import shipyard
        return [shipyard.outline(y) for c in cities for y in c.get("shipyards") or []], []
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


def plan(world: dict, layers: list, cities: list, road_tag: str, road_half_cm: float = 875.0,
         asset_set: str = "own", juncs: list | None = None) -> dict:
    """The PCG graph for every enabled city layer, as nodes and edges an agent builds with Epic's
    PCGToolset (node names are native PCG node titles; settings use their camelCase keys), plus the
    foliage layers for UEFN's Foliage mode and where each layer's pieces come from (asset_set)."""
    every = sorted((clean(l) for l in layers if l.get("enabled", True)), key=lambda l: (l["order"], l["name"]))
    asset_set = asset_set if asset_set in ASSET_SETS else "own"
    pieces = [{"layer": l["name"], "role": l["role"], "kind": l["kind"], "meshes": [m["path"] for m in l["meshes"]],
               "from": asset_hint(l["role"], asset_set),
               **({"storeys": [max(1, round(l["height_min_m"] / STOREY_M)), max(1, round(l["height_max_m"] / STOREY_M))],
                    "lot_m": [l["width_m"], l["depth_m"]], "size_jitter": l["size_jitter"],
                    "forms": {f: {"weight": w, "what": FORMS[f]} for f, w in l["forms"].items()}}
                 if l["kind"] == "buildings" else {})} for l in every]
    foliage = [{"layer": l["name"], "role": l["role"], "where": l["where"], "per_100m2": l["density"],
                "scale": [l["scale_min"], l["scale_max"]], "meshes": [m["path"] for m in l["meshes"]],
                "keep_off_roads_m": l["road_clear_m"] if l["avoid_roads"] else None, "collision": l["collision"]}
               for l in every if l["kind"] == "foliage"]
    layers = [l for l in every if l["kind"] != "foliage"]  # nature is painted in Foliage mode, not this graph
    nodes, edges, params, notes = [], [], [], []

    def node(nid, kind, settings=None, comment=""):
        nodes.append({"id": nid, "node": kind, "settings": settings or {}, **({"comment": comment} if comment else {})})
        return nid

    def wire(a, b, pin="In", out="Out"):
        edges.append({"from": a, "out": out, "to": b, "in": pin})

    node("Ground", "Get Landscape Data", {}, "the surface every layer samples or projects onto")
    any_roads = any(l["kind"] in ("along_roads", "buildings") or l["avoid_roads"] for l in layers)
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
    lots = None
    for layer in layers:
        p = _slug(layer["name"]).replace("-", "_") or "layer"
        P = "".join(w.capitalize() for w in p.split("_"))
        params += [{"name": f"{P}Meshes", "type": "SoftObjectPath", "containerType": "Array",
                    "default": [m["path"] for m in layer["meshes"]]},
                   {"name": f"{P}Seed", "type": "Integer32", "default": layer["seed"]}]
        if layer["kind"] == "at_junctions":
            corners = junction_corners(juncs or [], layer["setback_m"], layer["corners"])
            node(f"{P}_Corners", "Create Points", {"coordinateSpace": "World", "points": [
                {"location": [round(x), round(y), 0], "rotation": [0, round(yaw, 1), 0]} for x, y, yaw in corners]},
                f"{layer['name']}: {len(corners)} kerb corners, {layer['corners']} per junction, facing the crossing")
            src = node(f"{P}_OnGround", "Projection", {"bProjectPositions": True, "bProjectRotations": False,
                                                       "bProjectScales": False}, "onto the landscape")
            wire(f"{P}_Corners", src)
            wire("Ground", src, "Projection Target")
        elif layer["kind"] == "scatter":
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
        building_names = {n for n, v in done.items() if v["kind"] == "buildings"}
        if lots and building_names & set(avoid):  # every earlier lot at once: one Difference, not one per layer
            d = node(f"{P}_OffLots", "Difference", {"densityFunction": "Binary"}, "keep clear of every building lot so far")
            wire(cur, d, "Source")
            wire(lots, d, "Differences")
            cur = d
        for other in avoid:
            if other in building_names and lots:
                continue
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
        if layer["kind"] == "buildings" and layer["driveway"]:  # a pad from the lot's front to the kerb
            reach = layer["depth_m"] * 50 + layer["setback_m"] * 50
            dw = node(f"{P}_DrivewayAt", "Transform Points", {"offsetMin": {"x": reach, "y": 0, "z": 0},
                                                             "offsetMax": {"x": reach, "y": 0, "z": 0},
                                                             "scaleMin": {"x": max(0.2, layer["setback_m"] / 10), "y": 0.35, "z": 1},
                                                             "scaleMax": {"x": max(0.2, layer["setback_m"] / 10), "y": 0.35, "z": 1}},
                      f"{layer['name']}: driveway pad between the front (+X faces the street) and the kerb")
            wire(cur, dw)
            dws = node(f"{P}_Driveways", "Static Mesh Spawner", {"meshSelectorType": "PCGMeshSelectorWeighted", "meshEntries": []},
                       "driveway pieces (role driveway)")
            wire(dw, dws)
            params.append({"name": f"{P}DrivewayMeshes", "type": "SoftObjectPath", "containerType": "Array", "default": []})
            edges.append({"param": f"{P}DrivewayMeshes", "to": dws, "in": "meshes"})
        edges.append({"param": f"{P}Meshes", "to": spawn, "in": "meshes"})
        edges.append({"param": f"{P}Seed", "to": spawn, "in": "seed"})
        if not layer["meshes"]:
            notes.append(f"{layer['name']} ({layer['role']}) has no meshes yet: pick them from {asset_hint(layer['role'], asset_set)}.")
        if not layer["collision"] and layer["density"] >= 2:
            notes.append(f"{layer['name']}: collision off (dense scatter with collision freezes the editor and blocks players).")
        # what later layers keep clear of: the points this layer really spawns (after its masks and pruning)
        done[layer["name"]] = {"P": P, "bounds": cur, "kind": layer["kind"]}
        if layer["kind"] == "buildings":  # one running set of every lot so far
            if lots is None:
                lots = cur
            else:
                merged = node(f"{P}_LotsSoFar", "Merge Points", {}, "every building lot placed so far")
                wire(lots, merged)
                wire(cur, merged)
                lots = merged
    span = 0.0
    for c in cities:
        span = max(span, abs(c["x"]) + c["radius"], abs(c["y"]) + c["radius"])
    size = (world.get("generated") or {}).get("size_km")
    half = (size * 50000.0) if size else max(span * 1.15, 20000.0)
    return {"ok": True, "graph": f"PCG_{_slug(world.get('name') or 'World').replace('-', '_').title().replace('_', '')}Layers",
            "road_tag": road_tag, "layers": [l["name"] for l in layers], "nodes": nodes, "edges": edges,
            "params": params, "volume": {"location": [0, 0, 0], "scale": [round(half / 100, 1), round(half / 100, 1), 600]},
            "notes": notes, "asset_set": asset_set, "pieces": pieces, "foliage": foliage,
            "foliage_how": "Foliage layers are NOT in the graph: in UEFN Foliage mode add one Foliage Type per mesh (density per "
                           "100 m2, scale range, random yaw, Align to Normal off for trees), then paint each layer's area by "
                           "mouse (pcg skill agent_driving) keeping off roads and city rings. No grass: the landscape material does it.",
            "buildings_how": "Buildings: one mesh per lot, or with the own set stack BuildingKit storeys (3.84 m each, storeys "
                             "range in pieces[]) into a building mesh first; a lot is frontage x depth facing the street. "
                             "Skyline: give each layer several meshes of different heights (tallest near the core); never one "
                             "height for a whole street. road_network_pcg_preview gives every lot: point = [x, y, z, yaw, "
                             "scale, height_m, form, width_m, depth_m], form = index into forms[] (FORMS says what each "
                             "looks like: podium + tower, stepped, round, twin, L, courtyard...); build one variant mesh per "
                             "form and height band so the city matches the preview. Each lot also has foundation_m "
                             "(> 0 on a hill: a plinth / retaining wall that deep under the building, which sits at the "
                             "level pad z) and paths [[width_m, [x, y, z, ...]]]: a 1.6 m walkway from the door and a "
                             "3.5 m driveway (winding on steep ground) to the kerb; lay them as flat tiles or spline "
                             "meshes along those points. A generated world's heightmap already has every lot levelled.",
            "layout": "Transect: strict short-block grid in the core, looser grid in the neighbourhoods, curving streets "
                      "and cul-de-sacs in the suburbs, irregular city and district edges, industry in one wedge."}


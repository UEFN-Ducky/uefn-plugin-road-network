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

from .citygen import outline  # every district edge follows the city's irregular outline

KINDS = ("scatter", "along_roads", "at_junctions", "buildings", "foliage")
# foliage = trees, rocks, bushes: imported as Foliage Types and painted in UEFN's Foliage mode (like by
# mouse), never a PCG graph and never grass. at_junctions = traffic lights, street signs, stop signs on
# the corners of every junction.
ASSET_SETS = ("own", "fortnite", "mixed")

# What each role is, and where its pieces come from in each asset set. "own" names the project's own
# meshes (WorldGen/City SM_City_*, WorldGen/Kit, the BuildingKit modular storeys from Blender);
# "fortnite" gives Content Browser search words for Fortnite's own props. The AI building it picks
# real assets from the chosen set and saves them on the layer.
ROLES = {
    "tree": ("Trees", "WorldGen/Kit V4 trees", "tree pine oak birch"),
    "rock": ("Rocks", "WorldGen/Kit rocks (or Blender rock kit)", "rock boulder stone cliff"),
    "bush": ("Bushes", "WorldGen/Kit shrubs", "bush shrub hedge"),
    "street_lamp": ("Street lamps", "WorldGen/Kit 3 lamps", "street light lamp post"),
    "street_tree": ("Street trees", "WorldGen/Kit V4 trees, small", "tree planter street tree"),
    "traffic_light": ("Traffic lights", "WorldGen/Kit (make SM_TrafficLight if missing)", "traffic light signal"),
    "street_sign": ("Street signs", "WorldGen/Kit signs", "street sign road sign pole"),
    "stop_sign": ("Stop signs", "WorldGen/Kit signs", "stop sign"),
    "bench": ("Benches", "WorldGen/Kit props", "bench seat"),
    "bin": ("Trash bins", "WorldGen/Kit props", "trash can bin dumpster"),
    "hydrant": ("Fire hydrants", "WorldGen/Kit props", "fire hydrant"),
    "bus_stop": ("Bus stops", "WorldGen/Kit props", "bus stop shelter"),
    "billboard": ("Billboards", "BuildingKit SM_BK_Sky_Billboard / SM_BK_Mall_BillboardFrame", "billboard sign advert"),
    "power_pole": ("Power poles", "WorldGen/Kit props", "power pole utility pole electric"),
    "skyscraper": ("Skyscrapers", "SM_City_DT_* towers, Kit Sky04-16, BuildingKit Sky round storeys R10/R15/R30 stacked 3.84 m + crown/spire",
                   "skyscraper tower office building tall"),
    "office": ("Office blocks", "BuildingKit Tower curtain-wall storeys (6-20), SM_City_DT_*", "office building city block"),
    "apartment": ("Apartments", "BuildingKit Tower/Modern storeys 3-8, SM_City_*", "apartment building residential"),
    "shop": ("Shops", "BuildingKit Shop walls (storefront, entrance, awning) 1 storey", "shop store storefront"),
    "mall": ("Malls", "BuildingKit Mall (drum entrance, skylight dome, escalator) 2 storeys", "mall shopping centre"),
    "house": ("Houses", "SM_City_HS_House2 / HS_HouseWide / HS_Modern, BuildingKit House 1-2 storeys + gable roof",
              "house suburban home"),
    "mansion": ("Mansions", "SM_City_RC_Mansion, BuildingKit Mansion pieces", "mansion villa"),
    "factory": ("Factories", "WorldGen/Kit containers + crane, BuildingKit Shared walls", "factory industrial plant"),
    "warehouse": ("Warehouses", "WorldGen/Kit containers, BuildingKit Shared walls", "warehouse hangar shed"),
    "hospital": ("Hospitals", "WorldGen/Kit Bld_Hospital", "hospital clinic"),
    "school": ("Schools", "BuildingKit Modern storeys 2-3", "school"),
    "police": ("Police stations", "WorldGen/Kit Bld_Police", "police station"),
    "fire_station": ("Fire stations", "BuildingKit Shared walls + garage doors", "fire station"),
    "gas_station": ("Gas stations", "WorldGen/Kit (canopy + pumps)", "gas station fuel pump"),
    "park": ("Parks", "flat lot: grass, paths, benches, a few trees", "park fountain playground"),
    "driveway": ("Driveways", "WorldGen/Roads OneLane / Dirt tile cut short, or a flat concrete slab", "driveway concrete pad"),
    "parking": ("Parking lots", "flat lot: asphalt, painted bays", "parking lot car park"),
}
STOREY_M = 3.84  # BuildingKit storey pitch: a building's height in storeys = height / 3.84
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
    natural = out["kind"] in ("scatter", "foliage")
    out["upright"] = bool(layer.get("upright", not natural))  # absolute rotation: no lean on slopes
    out["random_yaw"] = bool(layer.get("random_yaw", natural))
    out["collision"] = bool(layer.get("collision", out["kind"] != "scatter" or out["density"] < 2))
    out["enabled"] = bool(layer.get("enabled", True))
    out["role"] = layer.get("role") if layer.get("role") in ROLES else _guess_role(out["name"], out["kind"])
    out["corners"] = max(1, min(4, int(layer.get("corners", 2) or 2)))  # at_junctions: corners used per junction
    # buildings that cars reach get a driveway from their front to the street
    out["driveway"] = bool(layer.get("driveway", out["kind"] == "buildings" and out["role"] in DRIVEWAY_ROLES))
    return out


DRIVEWAY_ROLES = ("house", "mansion", "gas_station", "warehouse", "factory", "parking", "fire_station", "police",
                  "hospital", "school", "mall")


def _guess_role(name: str, kind: str) -> str:
    n = name.lower()
    for role, (label, *_rest) in ROLES.items():
        if role.replace("_", " ") in n or label.lower().rstrip("s") in n:
            return role
    return {"foliage": "tree", "buildings": "house", "at_junctions": "street_sign"}.get(kind, "street_lamp")


def asset_hint(role: str, asset_set: str) -> str:
    """Where this role's pieces come from in the world's asset set (own kit, Fortnite or both)."""
    _, own, fortnite = ROLES.get(role, ("", "the project's own meshes", role))
    if asset_set == "own":
        return f"own: {own}"
    if asset_set == "fortnite":
        return f"Fortnite: search the Content Browser (Fortnite content) for '{fortnite}'"
    return f"own first ({own}), else Fortnite ('{fortnite}')"


def _bld(name, where, order, spacing, setback, w, d, hmin, hmax, **extra):
    return {"name": name, "kind": "buildings", "where": where, "order": order, "spacing_m": spacing, "setback_m": setback,
            "width_m": w, "depth_m": d, "height_min_m": hmin, "height_max_m": hmax, "scale_min": 1, "scale_max": 1,
            "footprint": 1.0, **extra}


# Everything a city has, in planning order: the rare big sites first (landmarks and civic buildings are
# reserved before anything fills the streets, as in a real masterplan), then the dense fill biggest first.
CITY_BUILDINGS = [
    _bld("Malls", "downtown", 5, 500, 8, 60, 40, 8, 14, role="mall"),
    _bld("Hospitals", "normal", 6, 900, 12, 48, 40, 14, 28, role="hospital"),
    _bld("Schools", "normal", 7, 700, 12, 44, 36, 8, 14, role="school"),
    _bld("Police stations", "normal", 8, 1000, 8, 30, 24, 8, 12, role="police"),
    _bld("Fire stations", "normal", 9, 1000, 8, 26, 22, 8, 10, role="fire_station"),
    _bld("Parks", "normal", 10, 350, 4, 50, 40, 0.5, 1, role="park", collision=False),
    _bld("Parking lots", "downtown", 11, 260, 3, 30, 22, 0.5, 1, role="parking", collision=False),
    _bld("Gas stations", "cities", 12, 650, 4, 20, 16, 5, 6, role="gas_station"),
    _bld("Mansions", "housing", 13, 160, 14, 30, 30, 8, 12, role="mansion"),
    _bld("Skyscrapers", "downtown", 20, 72, 6, 32, 32, 90, 240, role="skyscraper"),  # gaps take office blocks
    _bld("Office blocks", "downtown", 21, 34, 5, 26, 24, 30, 75, role="office"),
    _bld("Factories", "industrial", 22, 110, 10, 46, 34, 10, 20, role="factory"),  # gaps between them take the warehouses
    _bld("Warehouses", "industrial", 23, 44, 8, 34, 22, 8, 12, role="warehouse"),
    _bld("Apartments", "normal", 24, 28, 6, 22, 18, 12, 32, role="apartment"),
    _bld("Shops", "normal", 25, 14, 2, 12, 10, 4, 8, role="shop"),
    _bld("Houses", "housing", 26, 17, 10, 11, 14, 5, 8, role="house"),
]


def _prop(name, kind, where, order, role, spacing=30, setback=1.0, **extra):
    return {"name": name, "kind": kind, "where": where, "order": order, "role": role, "spacing_m": spacing,
            "setback_m": setback, "scale_min": 1, "scale_max": 1, "footprint": 0.5, **extra}


# The street: placed after the buildings, so it keeps clear of every lot.
CITY_STREET = [
    _prop("Traffic lights", "at_junctions", "downtown", 40, "traffic_light", setback=1.0, corners=4),
    _prop("Traffic lights (city)", "at_junctions", "normal", 41, "traffic_light", setback=1.0, corners=2),
    _prop("Stop signs", "at_junctions", "housing", 42, "stop_sign", setback=0.8, corners=2),
    _prop("Street signs", "at_junctions", "cities", 43, "street_sign", setback=1.5, corners=1),
    _prop("Street lamps", "along_roads", "cities", 44, "street_lamp", spacing=30, setback=1.5),
    _prop("Street trees", "along_roads", "normal", 45, "street_tree", spacing=22, setback=3, random_yaw=True,
          scale_min=0.9, scale_max=1.1, footprint=0.4),
    _prop("Bus stops", "along_roads", "normal", 46, "bus_stop", spacing=320, setback=1.5, footprint=1.2),
    _prop("Benches", "along_roads", "normal", 47, "bench", spacing=60, setback=1.0),
    _prop("Trash bins", "along_roads", "cities", 48, "bin", spacing=45, setback=0.8),
    _prop("Fire hydrants", "along_roads", "cities", 49, "hydrant", spacing=90, setback=0.5, footprint=0.3),
    _prop("Billboards", "along_roads", "downtown", 50, "billboard", spacing=150, setback=4, footprint=1.5),
    _prop("Power poles", "along_roads", "outside", 51, "power_pole", spacing=45, setback=4, footprint=0.4),
]

# Nature: Foliage Types painted in UEFN's Foliage mode. No grass (the landscape material does that).
NATURE = [
    {"name": "Trees", "kind": "foliage", "where": "outside", "role": "tree", "density": 0.2, "scale_min": 0.8,
     "scale_max": 1.4, "sink_cm": 15, "footprint": 0.35, "order": 1},
    {"name": "Rocks", "kind": "foliage", "where": "outside", "role": "rock", "density": 0.05, "scale_min": 0.7,
     "scale_max": 2.0, "sink_cm": 40, "footprint": 0.8, "avoid": ["Trees"], "order": 2},
    {"name": "Bushes", "kind": "foliage", "where": "outside", "role": "bush", "density": 0.3, "scale_min": 0.8,
     "scale_max": 1.4, "sink_cm": 5, "footprint": 0.4, "collision": False, "avoid": ["Trees", "Rocks"], "order": 3},
]


def preset(name: str = "full_city") -> list:
    """Starter layers. Meshes stay empty: the AI placing it picks them from the world's asset set.
    'full_city': nature (foliage), every kind of building and all street furniture; 'city': buildings and
    the street; 'buildings': buildings only; 'street': street furniture only; 'nature': trees, rocks, bushes."""
    sets = {"nature": NATURE, "buildings": CITY_BUILDINGS, "street": CITY_STREET,
            "city": CITY_BUILDINGS + CITY_STREET, "full_city": NATURE + CITY_BUILDINGS + CITY_STREET,
            "forest": NATURE, "forest_and_streets": NATURE + CITY_STREET + CITY_BUILDINGS[-1:]}
    rows = [dict(r) for r in sets.get(name, sets["full_city"])]
    buildings = [b["name"] for b in CITY_BUILDINGS]
    for r in rows:
        if r["kind"] in ("along_roads", "at_junctions"):  # street furniture keeps clear of every lot
            r["avoid"] = sorted(set(r.get("avoid", [])) | set(buildings))
        r["seed"] = 7 + r["order"] * 101  # one seed per layer: same seeds sample the same spots
    return [clean(r) for r in rows]


def _ring(c: dict, radius: float, sides: int = 48) -> list:
    """A district edge: the ring radius bent by the city's outline, never a circle."""
    out = []
    for k in range(sides):
        a = 2 * math.pi * k / sides
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
    return float(a) if a is not None else float((int(c.get("seed", 0)) * 137) % 360)


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
                   "lot_m": [l["width_m"], l["depth_m"]]} if l["kind"] == "buildings" else {})} for l in every]
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
                             "height for a whole street. road_network_pcg_preview shows the intended height per lot (point[5]).",
            "layout": "Transect: strict short-block grid in the core, looser grid in the neighbourhoods, curving streets "
                      "and cul-de-sacs in the suburbs, irregular city and district edges, industry in one wedge."}


# ------------------------------------------------------------------ offline preview
BASE_RADIUS = {"scatter": 250.0, "foliage": 250.0, "along_roads": 150.0, "at_junctions": 120.0,
               "buildings": 700.0}  # cm, at footprint 1 and scale 1
SCATTERED = ("scatter", "foliage")


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


def junctions(actors: list) -> list:
    """(x, y, road_half_width, [arm angles in degrees]) for every junction: a point carrying a junction
    piece (SM_Junc_* / SM_JK_*), its arms the mesh-less links to its mouth points."""
    out = []
    for pts, segs in actors:
        by_i = {p["i"]: p for p in pts}
        arms: dict = {}
        for s in segs:
            if s["mesh"]:
                continue
            for a, b in ((s["a"], s["b"]), (s["b"], s["a"])):
                arms.setdefault(a, []).append(b)
        for p in pts:
            if not str(p.get("mesh") or "").startswith(("SM_Junc_", "SM_JK_")) or p["mesh"] == "SM_Junc_Empty":
                continue
            mouths = [by_i[m] for m in arms.get(p["i"], []) if m in by_i]
            if len(mouths) < 3:
                continue
            angles = sorted(math.degrees(math.atan2(m["y"] - p["y"], m["x"] - p["x"])) % 360 for m in mouths)
            out.append((p["x"], p["y"], max(300.0, max(m["w"] for m in mouths)), angles))
    return out


def junction_corners(juncs: list, setback_m: float, corners: int) -> list:
    """(x, y, yaw) on the kerb corners between neighbouring arms, facing the junction centre: where
    traffic lights, stop signs and street-name signs stand. ``corners`` per junction, widest gaps first."""
    out = []
    for x, y, half, angles in juncs:
        gaps = []
        for k, a in enumerate(angles):
            b = angles[(k + 1) % len(angles)] + (360 if k + 1 == len(angles) else 0)
            gaps.append((b - a, a + (b - a) / 2))
        for gap, mid in sorted(gaps, reverse=True)[:corners]:
            if gap >= 175:
                continue  # a straight run, no corner
            d = min((half + setback_m * 100) / max(0.2, math.sin(math.radians(gap / 2))), half * 3 + setback_m * 100)
            cx, cy = x + d * math.cos(math.radians(mid)), y + d * math.sin(math.radians(mid))
            out.append((cx, cy, (mid + 180) % 360))
    return out


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
        # distance against the outline in this direction: the same bent edges the plan's polygons have
        d = math.hypot(x - c["x"], y - c["y"]) / outline(c, math.degrees(math.atan2(y - c["y"], x - c["x"])))
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


def _lot_hits_road(road_hash: "_Hash", x: float, y: float, yaw: float, half_w: float, half_d: float) -> bool:
    """Does a lot (frontage 2*half_w along its street, depth 2*half_d, facing yaw) reach onto any road?"""
    fx, fy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    for rx, ry, half in road_hash.near(x, y, max(half_w, half_d)):
        dx, dy = rx - x, ry - y
        along_face = abs(dx * fx + dy * fy)  # toward the street / the back
        along_front = abs(-dx * fy + dy * fx)  # sideways along the street
        if along_face < half_d + half and along_front < half_w + half:
            return True
    return False


def core_peak(cities: list, x: float, y: float) -> float:
    """The city's height field at (x, y): 1 at the very core, falling off like a bell (half at ~18 % of
    the radius, near 0 by 40 %), so the skyline peaks in the middle and steps down to the suburbs."""
    best = 0.0
    for c in cities:
        dx, dy = x - c["x"], y - c["y"]
        share = math.hypot(dx, dy) / (c["radius"] * outline(c, math.degrees(math.atan2(dy, dx))))
        best = max(best, math.exp(-(share / 0.22) ** 2))
    return best


def _height(layer: dict, cities: list, x: float, y: float, rnd, neighbours: "_Hash") -> float:
    """A believable skyline: tallest at the city core, falling off outward (the transect), randomised,
    and never within 18 % of the range of a building of this layer closer than 25 m (no row of equal
    heights)."""
    lo, hi = layer["height_min_m"], layer["height_max_m"]
    span = hi - lo
    if span <= 0.5:
        return lo
    core = core_peak(cities, x, y)
    # the layer's range follows the city's height field: top of the range only at the very core, the
    # bottom at its district's edge, with real scatter (a few towers pop up, a few stay short)
    h = lo + span * min(1.0, max(0.0, core * rnd.uniform(0.55, 1.2) + rnd.uniform(-0.08, 0.12)))
    for _ in range(4):  # step away from a too-similar neighbour, up or down, whichever fits
        clash = [nh for nh in neighbours.near(x, y, 2500.0) if abs(nh - h) < 0.18 * span]
        if not clash:
            break
        h = h + 0.3 * span if h + 0.3 * span <= hi else h - 0.3 * span
        h = max(lo, min(hi, h + rnd.uniform(-0.05, 0.05) * span))
    return h


def preview(layers: list, cities: list, terrain: dict, roads: list, sample_z, limit: int = 2500,
            juncs: list | None = None) -> dict:
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
        road_hash.add(x, y, half, (x, y, half))
    kept: dict = {}
    out = {}
    for layer in layers:
        rnd = random.Random(f"{layer['seed']}:{layer['name']}")  # different densities never share spots in PCG either
        cands = []
        if layer["kind"] == "at_junctions":
            cands = junction_corners(juncs or [], layer["setback_m"], layer["corners"])
            thin = 1.0
        elif layer["kind"] in SCATTERED:
            area_m2 = (hi[0] - lo[0]) * (hi[1] - lo[1]) / 1e4
            full = area_m2 * layer["density"] / 100.0
            n = int(min(full, limit * 4))  # candidates before masks and pruning
            for _ in range(n):
                cands.append((rnd.uniform(lo[0], hi[0]), rnd.uniform(lo[1], hi[1]), rnd.uniform(0, 360)))
            thin = full / n if n else 1.0
        else:
            # every road sample on both sides is a candidate; the placing loop below takes the first good one
            # once `spacing` has passed since the last placed one on that side (a lot blocked by a cross
            # street slides along to the next good spot instead of being lost)
            progress, last = 0.0, None
            for x, y, half, dx, dy in roads:
                if last is not None and math.hypot(x - last[0], y - last[1]) <= 2000:
                    progress += math.hypot(x - last[0], y - last[1])
                last = (x, y)
                # from this road's kerb, whatever its width; a building's centre is half its depth further back;
                # street furniture stands its own size past the setback line, never on the kerb
                back = half + layer["setback_m"] * 100 + (layer["depth_m"] * 50 if layer["kind"] == "buildings"
                                                          else BASE_RADIUS[layer["kind"]] * layer["footprint"] * layer["scale_max"])
                for side in (1, -1):  # normal to the road, both sides, facing it
                    nx, ny = -dy * side, dx * side
                    yaw = math.degrees(math.atan2(-ny, -nx))
                    cands.append((x + nx * back, y + ny * back, yaw, progress, side))
            thin = 1.0
        spacing = layer["spacing_m"] * 100
        last_at = {1: -1e18, -1: -1e18}
        r_base = BASE_RADIUS[layer["kind"]] * layer["footprint"]
        if layer["kind"] == "buildings":  # its lot, not a fixed size: a skyscraper needs far more room than a shop
            r_base = 50.0 * max(layer["width_m"], layer["depth_m"]) * layer["footprint"]
        own = _Hash()
        own_heights = _Hash()  # this layer's buildings and their heights, for the neighbour rule
        pts = []
        avoid = avoid_list(layer, [l for l in layers if l["name"] in kept])
        for cand in cands:
            x, y, yaw = cand[:3]
            along = len(cand) == 5
            if along and cand[3] - last_at[cand[4]] < spacing:
                continue  # too soon after the last one on this side of the street
            if not _in_where(layer["where"], cities, x, y):
                continue
            if layer["kind"] == "buildings" and layer["height_min_m"] >= 40 and rnd.random() > 0.08 + 0.92 * core_peak(cities, x, y):
                continue  # towers cluster at the core and thin out toward the edge: no wall of equal towers
            z = sample_z(x, y)
            if z is None or (sea is not None and z < sea + 50):
                continue  # nothing grows under water or off the map
            s = rnd.uniform(layer["scale_min"], layer["scale_max"])
            r = r_base * s
            # scatter keeps the clearance; roadside layers sit at their setback, so only their own size counts
            clear = layer["road_clear_m"] * 100 if layer["kind"] in SCATTERED else 0.0
            if layer["avoid_roads"]:
                if layer["kind"] == "buildings":  # the lot as the rectangle it is, facing its street
                    if _lot_hits_road(road_hash, x, y, yaw, layer["width_m"] * 50, layer["depth_m"] * 50):
                        continue
                elif any(True for _ in road_hash.near(x, y, r + clear)):
                    continue
            if any(True for other in avoid for _ in kept[other].near(x, y, r)):
                continue
            # one of a kind per `spacing` in any direction (two hospitals never sit across the street from
            # each other); dense layers just must not overlap
            gap = max(r, spacing * 0.5) if along and spacing > 4 * r else r
            if layer["self_prune"] and any(True for _ in own.near(x, y, gap)):
                continue
            own.add(x, y, r)
            if along:
                last_at[cand[4]] = cand[3]
            yaw = yaw if layer["random_yaw"] or layer["kind"] not in SCATTERED else 0.0
            row = [round(x), round(y), round(z - layer["sink_cm"]), round(yaw), round(s, 2)]
            if layer["kind"] == "buildings":  # + its height in metres, so the preview draws a real skyline
                row.append(round(_height(layer, cities, x, y, rnd, own_heights), 1))
                own_heights.add(x, y, 0.0, row[-1])
            pts.append(row)
        kept[layer["name"]] = own
        count = int(len(pts) * thin) if layer["kind"] in SCATTERED else len(pts)
        if len(pts) > limit:
            pts = rnd.sample(pts, limit)
        out[layer["name"]] = {"kind": layer["kind"], "role": layer["role"], "count": count, "shown": len(pts), "points": pts,
                              "radius_cm": round(r_base),
                              **({"size_m": [layer["width_m"], layer["depth_m"]], "setback_m": layer["setback_m"],
                                  "driveway": layer["driveway"]} if layer["kind"] == "buildings" else {})}
    return out


def _demo() -> None:
    cities = [{"name": "A", "x": 0, "y": 0, "radius": 50000, "districts": {"downtown": {"radius": 20000},
               "normal": {"radius": 37500}, "housing": {"radius": 50000}, "industrial": {"angle": 90}}}]
    full = preset()
    assert len(full) == len(NATURE) + len(CITY_BUILDINGS) + len(CITY_STREET) and all(l["meshes"] == [] for l in full)
    assert not any("grass" in l["name"].lower() for l in full), "no grass layer"
    assert {l["kind"] for l in full if l["role"] in ("tree", "rock", "bush")} == {"foliage"}, "nature is foliage"
    roles = {l["role"] for l in full}
    for r in ("traffic_light", "stop_sign", "street_sign", "bench", "bin", "hydrant", "bus_stop", "billboard",
              "skyscraper", "factory", "hospital", "school", "police", "fire_station", "park", "parking", "mall"):
        assert r in roles, r
    # a city: an east-west and a north-south road crossing at a junction in the middle
    J = {"i": 9, "x": 0, "y": 0, "w": 1200, "mesh": "SM_Junc_Gen"}
    mouths = [{"i": 10 + k, "x": 1500 * math.cos(k * math.pi / 2), "y": 1500 * math.sin(k * math.pi / 2), "w": 875, "mesh": ""}
              for k in range(4)]
    ends = [{"i": 20 + k, "x": 60000 * math.cos(k * math.pi / 2), "y": 60000 * math.sin(k * math.pi / 2), "w": 875, "mesh": ""}
            for k in range(4)]
    pts = [J] + mouths + ends
    segs = [{"a": 9, "b": 10 + k, "mesh": ""} for k in range(4)] + [{"a": 10 + k, "b": 20 + k, "mesh": "SM_Road_TwoLane"} for k in range(4)]
    actors = [(pts, segs)]
    juncs = junctions(actors)
    assert len(juncs) == 1 and len(juncs[0][3]) == 4
    corners = junction_corners(juncs, 1.0, 4)
    assert len(corners) == 4 and all(abs(abs(x) - abs(y)) < 1 and math.hypot(x, y) > 875 for x, y, _ in corners), corners
    out = plan({"name": "Valley", "generated": {"size_km": 6}}, full, cities, "valley_roads", asset_set="fortnite", juncs=juncs)
    ids = {n["id"] for n in out["nodes"]}
    assert all(e["to"] in ids and (e.get("from") in ids or "param" in e) for e in out["edges"]), "every wire joins real nodes"
    assert not any(i.startswith(("Trees_", "Rocks_", "Bushes_")) for i in ids), "foliage is not in the PCG graph"
    assert [f["layer"] for f in out["foliage"]] == ["Trees", "Rocks", "Bushes"]
    assert "TrafficLights_Corners" in ids and "Skyscrapers_InArea" in ids and "Houses_NotThere" in ids
    order = [n["id"] for n in out["nodes"]]
    for p in ("Skyscrapers", "StreetLamps", "TrafficLights"):
        assert order.index(f"{p}_Vary") < order.index(f"{p}_Footprint") < order.index(f"{p}_NoOverlap") < order.index(f"{p}_Spawn"), p
    assert all("Fortnite" in p["from"] for p in out["pieces"]) and out["asset_set"] == "fortnite"
    sky = next(p for p in out["pieces"] if p["layer"] == "Skyscrapers")
    assert sky["storeys"] == [23, 62] and sky["lot_m"] == [32, 32], sky
    assert "own: SM_City_HS_House2" in next(p for p in plan({"name": "V"}, full, cities, "t", juncs=juncs)["pieces"]
                                            if p["layer"] == "Houses")["from"]
    # offline preview of the whole city
    ter = {"min": [-80000, -80000], "max": [80000, 80000]}
    pv = preview(full, cities, ter, road_samples(actors), lambda x, y: 0.0, juncs=juncs)
    for name in ("Trees", "Rocks", "Skyscrapers", "Office blocks", "Factories", "Warehouses", "Apartments", "Shops",
                 "Houses", "Traffic lights", "Street lamps", "Trash bins"):
        assert pv[name]["shown"] > 0, (name, {n: v["shown"] for n, v in pv.items()})
    for name, v in pv.items():
        for x, y, *_ in v["points"]:
            assert min(abs(x), abs(y)) > 875 or max(abs(x), abs(y)) > 60000, f"{name} on a road at {x},{y}"
    od = lambda p: math.hypot(p[0], p[1]) / outline(cities[0], math.degrees(math.atan2(p[1], p[0])))  # outline distance
    assert all(od(p) >= 50000 for p in pv["Trees"]["points"]), "trees stay outside the city's irregular edge"
    assert all(od(p) < 20000 and 90 <= p[5] <= 240 for p in pv["Skyscrapers"]["points"])
    for p in pv["Factories"]["points"]:
        assert 20000 <= od(p) < 50000 and _in_wedge(cities[0], p[0], p[1]), "factories in the industrial zone"
    for name in ("Apartments", "Shops", "Houses"):
        assert not any(_in_wedge(cities[0], p[0], p[1]) for p in pv[name]["points"]), f"no {name} in the industrial zone"
    houses = pv["Houses"]["points"]
    assert all(37500 <= math.hypot(p[0], p[1]) / outline(cities[0], math.degrees(math.atan2(p[1], p[0]))) < 50000
               for p in houses), "houses only in the (irregular) housing ring"
    # skyline: core buildings are taller on average, and no two close neighbours share a height
    apts = pv["Apartments"]["points"] + pv["Office blocks"]["points"] + pv["Skyscrapers"]["points"]
    near = [p[5] for p in pv["Skyscrapers"]["points"] if math.hypot(p[0], p[1]) < 10000]
    far = [p[5] for p in pv["Skyscrapers"]["points"] if math.hypot(p[0], p[1]) >= 10000]
    assert near and far and sum(near) / len(near) > sum(far) / len(far), (near, far)
    for name in ("Skyscrapers", "Apartments"):
        ps, span = pv[name]["points"], 150 if name == "Skyscrapers" else 20
        same = sum(1 for i, a in enumerate(ps) for b in ps[i + 1:]
                   if math.hypot(a[0] - b[0], a[1] - b[1]) < 2500 and abs(a[5] - b[5]) < 0.1 * span)
        assert same == 0, f"{same} {name} neighbours with the same height"
    assert apts
    lots = [(p[0], p[1], 50 * max(*pv[n]["size_m"])) for n in pv if "size_m" in pv[n] for p in pv[n]["points"]]
    clash = sum(1 for i, a in enumerate(lots) for b in lots[i + 1:] if math.hypot(a[0] - b[0], a[1] - b[1]) < (a[2] + b[2]) * 0.7)
    assert clash == 0, f"{clash} buildings overlap"
    bad = clean({"name": "x", "kind": "nope", "density": -5, "scale_min": 3, "scale_max": 1})
    assert bad["kind"] == "scatter" and bad["density"] == 0.01 and bad["scale_min"] == 1 and bad["scale_max"] == 3
    print("pcg layers ok", len(full), "layers,", len(out["nodes"]), "nodes", len(out["edges"]), "wires,",
          {n: v["count"] for n, v in pv.items() if v["count"]})
if __name__ == "__main__":
    _demo()

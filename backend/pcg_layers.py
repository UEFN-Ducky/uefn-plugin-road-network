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

import copy
import functools
import math
import random
import re
import zlib

from .citygen import core_reach, outline, zone_share  # every district edge follows the city's irregular outline

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
    "sidewalk_corner": ("Sidewalk corners", "WorldGen/Kit/Street SM_Kit_SidewalkCorner", "sidewalk curb corner"),
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
    "civic": ("Civic buildings", "BuildingKit Modern storeys 2-4, columns / canopy entrance (city hall, library, "
              "community centre, post office, courthouse)", "city hall library civic building"),
    "utility": ("Utilities", "BuildingKit Shared walls, low sheds and plant (substations, treatment plants, "
                "recycling)", "substation water treatment plant utility building"),
    "transit": ("Transit stations", "BuildingKit Shop walls + long canopy (bus terminal, train station)",
                "train station bus terminal"),
    "driveway": ("Driveways", "WorldGen/Roads OneLane / Dirt tile cut short, or a flat concrete slab", "driveway concrete pad"),
    "parking": ("Parking lots", "flat lot: asphalt, painted bays", "parking lot car park"),
}
STOREY_M = 3.84  # BuildingKit storey pitch: a building's height in storeys = height / 3.84

# Building forms (massing): what each lot's building looks like, so the preview draws a real city and the AI
# building it knows what to make (BuildingKit storeys stacked in that shape, or a mesh of that shape).
FORMS = {
    "box": "plain block filling the lot",
    "slab": "long thin slab, 1.5x as wide, 0.6x as deep",
    "tower": "slim tower, 70 % of the lot, full height",
    "podium_tower": "10-storey podium on the whole lot with a slim tower on top",
    "stepped": "wedding-cake tower: three tiers stepping in at 55 % and 80 % of the height",
    "round": "round (curved) tower",
    "round_podium": "podium with a round tower on top",
    "twin": "two towers on one podium, different heights",
    "spire": "tower with a crown and spire on top",
    "L": "L-shaped block (two wings)",
    "courtyard": "perimeter block around an open courtyard",
    "shed": "wide low shed with a raised roof strip (factory, warehouse)",
    "gable": "house with a pitched roof",
    "flat": "flat lot (park, parking)",
}
FORM_NAMES = list(FORMS)
ROLE_FORMS = {
    "skyscraper": {"tower": 2, "podium_tower": 3, "stepped": 2, "round": 2, "round_podium": 2, "twin": 1, "spire": 2},
    "office": {"box": 2, "slab": 2, "podium_tower": 2, "courtyard": 1, "L": 1, "round": 1},
    "apartment": {"slab": 3, "L": 2, "courtyard": 2, "box": 2, "stepped": 1},
    "mall": {"box": 2, "round_podium": 1, "L": 1},
    "hospital": {"L": 2, "box": 1, "courtyard": 1, "podium_tower": 1},
    "school": {"L": 2, "courtyard": 2, "box": 1},
    "police": {"box": 2, "L": 1}, "fire_station": {"box": 2, "L": 1},
    "factory": {"shed": 3, "L": 1}, "warehouse": {"shed": 4, "box": 1},
    "shop": {"box": 3, "gable": 1}, "gas_station": {"box": 1},
    "house": {"gable": 4, "L": 1, "box": 1}, "mansion": {"L": 2, "courtyard": 1, "gable": 2},
    "park": {"flat": 1}, "parking": {"flat": 1},
    "civic": {"box": 2, "L": 1, "courtyard": 1}, "utility": {"shed": 3, "box": 1}, "transit": {"slab": 2, "shed": 1},
}
# Roles the urban planner places (urban.py): their layers build only on the planned sites, never at random
PLANNED_ROLES = ("mall", "hospital", "school", "police", "fire_station", "park", "gas_station", "civic", "utility",
                 "transit")
WHERE = ("outside", "cities", "downtown", "normal", "housing", "industrial", "shipyards", "everywhere")
INDUSTRIAL_SPREAD = 75.0  # degrees: each city's industrial zone is one wedge of its outer rings

# name: (default, min, max)
NUMBERS = {
    "density": (1.0, 0.01, 50.0),       # scatter: points per 100 m2 (inside its patches when cover < 1)
    "cover": (1.0, 0.05, 1.0),          # scatter / foliage: share of the land in patches (forests), the rest open
    # foliage: as dense as `density` says within this many city radii of a city centre. Beyond that it goes on
    # over the whole map, as dense as the layer's budget of points allows (never under FAR_KEEP of it).
    # 0 = one density everywhere
    "reach": (0.0, 0.0, 10.0),
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
            entry = {"path": path, "weight": weight}
            size = (m or {}).get("size_cm") if isinstance(m, dict) else None
            if isinstance(size, (list, tuple)) and len(size) == 3 and all(isinstance(v, (int, float)) and v > 0 for v in size):
                entry["size_cm"] = [float(v) for v in size]  # buildings: the model's size, so lots fit it
            meshes.append(entry)
    out["meshes"] = meshes
    out["avoid_roads"] = bool(layer.get("avoid_roads", True))
    out["avoid"] = [str(a) for a in (layer.get("avoid") or []) if str(a).strip()]
    out["self_prune"] = bool(layer.get("self_prune", True))
    natural = out["kind"] in ("scatter", "foliage")
    out["upright"] = bool(layer.get("upright", not natural))  # absolute rotation: no lean on slopes
    out["random_yaw"] = bool(layer.get("random_yaw", natural))
    out["collision"] = bool(layer.get("collision", out["kind"] != "scatter" or out["density"] < 2))
    out["enabled"] = bool(layer.get("enabled", True))
    # an optional outline [[x, y], ...] (cm, world): the layer only places inside it (an area drawn in the panel)
    area = layer.get("area") if isinstance(layer.get("area"), list) else []
    out["area"] = [[float(p[0]), float(p[1])] for p in area if isinstance(p, (list, tuple)) and len(p) >= 2][:200]
    if len(out["area"]) < 3:
        out["area"] = []
    out["role"] = layer.get("role") if layer.get("role") in ROLES else _guess_role(out["name"], out["kind"])
    out["corners"] = max(1, min(4, int(layer.get("corners", 2) or 2)))  # at_junctions: corners used per junction
    # buildings that cars reach get a driveway from their front to the street
    out["driveway"] = bool(layer.get("driveway", out["kind"] == "buildings" and out["role"] in DRIVEWAY_ROLES))
    # and every building with a door gets a walkway from it to the pavement
    out["walkway"] = bool(layer.get("walkway", out["kind"] == "buildings" and out["role"] not in ("park", "parking")))
    forms = layer.get("forms") if isinstance(layer.get("forms"), dict) else ROLE_FORMS.get(out["role"], {"box": 1})
    out["forms"] = {f: max(0.0, float(w)) for f, w in forms.items() if f in FORMS and float(w) > 0} or {"box": 1.0}
    try:
        out["size_jitter"] = max(0.0, min(0.6, float(layer.get("size_jitter", 0.3))))  # lots vary +-30 % by default
    except (TypeError, ValueError):
        out["size_jitter"] = 0.3
    # buildings: 'mesh' stretches one kit building to the lot; 'modular' builds it from BuildingKit storeys and
    # 5.12 m wall modules by grammar (grammar.py), so windows and doors keep their real size
    out["build"] = layer.get("build") if layer.get("build") in BUILD_MODES else "mesh"
    return out


BUILD_MODES = ("mesh", "modular")


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
    _bld("Civic buildings", "cities", 9, 1000, 8, 40, 30, 8, 18, role="civic"),
    _bld("Utilities", "cities", 9, 1000, 6, 30, 20, 5, 9, role="utility"),
    _bld("Transit stations", "cities", 9, 1000, 6, 50, 20, 6, 10, role="transit"),
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
    # the sidewalk carried round every junction corner (paving + kerb), placed before the poles that stand on it
    _prop("Sidewalk corners", "at_junctions", "cities", 39, "sidewalk_corner", setback=1.9, corners=4, self_prune=False),
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

# Nature: no grass (the landscape material does that). Forests: VERY dense patches (cover = share of open land
# that is forest) with meadows between, a tree every ~3.5 m inside a patch (8 per 100 m2) out to 1.8 city radii,
# where the players are; the rest of the map is forest too, as dense as the points left under FOREST_LIMIT make it.
FAR_KEEP = 0.2  # past its reach a forest is never thinner than this share of what it is round the cities
NATURE = [
    {"name": "Trees", "kind": "foliage", "where": "outside", "role": "tree", "density": 8.0, "cover": 0.55,
     "reach": 1.8, "scale_min": 0.8, "scale_max": 1.5, "sink_cm": 15, "footprint": 0.35, "order": 1},
    {"name": "Rocks", "kind": "foliage", "where": "outside", "role": "rock", "density": 0.1, "cover": 0.55,
     "reach": 1.8, "scale_min": 0.7, "scale_max": 2.0, "sink_cm": 40, "footprint": 0.8, "avoid": ["Trees"], "order": 2},
    {"name": "Bushes", "kind": "foliage", "where": "outside", "role": "bush", "density": 4.0, "cover": 0.6,
     "reach": 1.8, "scale_min": 0.8, "scale_max": 1.4, "sink_cm": 5, "footprint": 0.4, "collision": False,
     "avoid": ["Trees", "Rocks"], "order": 3},
]
PATCH_CM = 60000.0  # forest patch scale: patches a few hundred metres across


@functools.lru_cache(maxsize=1 << 16)  # a world has a few thousand lattice points; patch() asks 12 per spot
def _lattice(i: int, j: int) -> float:
    n = (i * 374761393 + j * 668265263) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    return (n & 0xFFFF) / 65535.0


def patch(x: float, y: float, cell: float = PATCH_CM) -> float:
    """Smooth value noise 0..1 shared by every foliage layer (bushes grow in the same forests as trees)."""
    h = _lattice
    total, amp, norm = 0.0, 1.0, 0.0
    for octave in range(3):  # three octaves: ragged forest edges, clearings inside
        c = cell / (2 ** octave)
        gx, gy = x / c, y / c
        i, j = math.floor(gx), math.floor(gy)
        fx, fy = gx - i, gy - j
        sx, sy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
        h00, h01 = h(i, j), h(i, j + 1)
        a = h00 + (h(i + 1, j) - h00) * sx
        b = h01 + (h(i + 1, j + 1) - h01) * sx
        total += (a + (b - a) * sy) * amp
        norm += amp
        amp *= 0.5
    return total / norm


PRESETS = ("full_city", "city", "buildings", "street", "nature", "forest", "forest_and_streets")  # the built-in sets


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
        from . import shipyard
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


# ------------------------------------------------------------------ offline preview
BASE_RADIUS = {"scatter": 250.0, "foliage": 250.0, "along_roads": 150.0, "at_junctions": 120.0,
               "buildings": 700.0}  # cm, at footprint 1 and scale 1
SCATTERED = ("scatter", "foliage")


class _Hash:
    """Points by 20 m cell: who is within r of (x, y)."""

    def __init__(self, cell: float = 2000.0):
        self.cell, self.cells = cell, {}
        self.reach = 0.0  # the widest point added (capped at 20 m, the most a look ever reached out for)
        self.box = [math.inf, math.inf, -math.inf, -math.inf]  # where its points are: a look outside is a miss at once

    def add(self, x, y, r, item=None):
        self.cells.setdefault((int(x // self.cell), int(y // self.cell)), []).append((x, y, r, item))
        if r > self.reach:
            self.reach = min(r, 2000.0)
        b = self.box
        if x < b[0]:
            b[0] = x
        if y < b[1]:
            b[1] = y
        if x > b[2]:
            b[2] = x
        if y > b[3]:
            b[3] = y

    def _keys(self, x, y, r):
        """The cells a point within r of (x, y) can be in: as far out as r plus the widest point added (a
        tree is 2-5 m, so 3 x 3 cells instead of 5 x 5 for every candidate of a forest)."""
        k = int(math.ceil((r + self.reach) / self.cell))
        cx, cy = int(x // self.cell), int(y // self.cell)
        if (2 * k + 1) ** 2 > len(self.cells):
            # A wide look (one-of-a-kind spacing, 100 m+) walks the filled cells: every empty one in a 2 km
            # square is 10 000+ lookups per spot, and the city preview never finished (Nov 11 2026).
            return [c for c in self.cells if abs(c[0] - cx) <= k and abs(c[1] - cy) <= k]
        return [(i, j) for i in range(cx - k, cx + k + 1) for j in range(cy - k, cy + k + 1)]

    def near(self, x, y, r):
        get, hypot = self.cells.get, math.hypot
        for key in self._keys(x, y, r):
            for px, py, pr, item in get(key, ()):
                if hypot(px - x, py - y) < r + pr:
                    yield item

    def hit(self, x, y, r) -> bool:
        """Is anything within r of (x, y)? near(), stopping at the first (millions of calls per city)."""
        b, far = self.box, r + self.reach
        if x + far < b[0] or x - far > b[2] or y + far < b[1] or y - far > b[3]:
            return False  # a forest tile asks every building layer: most have nothing anywhere near it
        cell = self.cell
        k = int(math.ceil((r + self.reach) / cell))
        if (2 * k + 1) ** 2 > len(self.cells):
            return any(True for _ in self.near(x, y, r))  # a wide look, or few points yet
        get, hypot = self.cells.get, math.hypot
        cx, cy = int(x // cell), int(y // cell)
        for i in range(cx - k, cx + k + 1):
            for j in range(cy - k, cy + k + 1):
                for px, py, pr, _ in get((i, j), ()):
                    if hypot(px - x, py - y) < r + pr:
                        return True
        return False


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


def site_layers(cities: list, ground) -> dict:
    """The neighbourhoods' own pieces as built-in layers (kind 'site', rows [x, y, z, yaw, sx, sy, sz] already
    scaled for `mesh`, `box_m` its size at scale 1 for the preview): complex parking lots, painted stalls,
    and parking garages (lots a complex reserved with role 'parking_garage'). Cul-de-sac turnarounds are pieces on
    the road splines (splines_text.cap_ends), not PCG."""
    lots, stalls, garages, trees, fences = [], [], [], [], []
    grounds: dict = {}
    for c in cities:
        priv = c.get("private") or {}
        for x, y, yaw, sx, sy, kind, *fac in priv.get("grounds") or []:  # the planned developments' grounds
            if kind == "tower" and fac and fac[0] == "telecom_tower":
                kind = "telecom"  # a mast, not a water tank
            mesh, box, _tint = GROUNDS[kind]
            z = round(ground(x, y) or 0)
            if box[0] == 1:  # 1 m unit slabs, stretched to the part
                grounds.setdefault(kind, []).append([x, y, z, yaw, sx, sy, 1.0])
            else:  # fixed pieces (towers, benches) stand in its middle
                grounds.setdefault(kind, []).append([x, y, z, yaw, 1.0, 1.0, 1.0])
        for x, y, yaw, sx, sy in priv.get("parking") or []:
            lots.append([x, y, round(ground(x, y) or 0), yaw, sx, sy, 1.0])
        for x, y, yaw, *_ in priv.get("stalls") or []:
            stalls.append([x, y, round((ground(x, y) or 0) + 9), yaw, 1.0, 1.0, 1.0])
        for x, y, yaw, ln in priv.get("fences") or []:  # back and side lot lines: 1.5 m timber fence
            fences.append([x, y, round(ground(x, y) or 0), yaw, ln, 1.0, 0.6])
        for x, y, yaw, sx, sy in priv.get("pools") or []:  # back-yard pools
            grounds.setdefault("pool", []).append([x, y, round(ground(x, y) or 0), yaw, sx, sy, 1.0])
        for k, (x, y) in enumerate(priv.get("trees") or []):  # back yards, front corners, courtyards
            trees.append([x, y, round(ground(x, y) or 0), (x * 7 + y * 13) % 360, round(0.8 + (k * 37 % 50) / 100, 2)])
        for row in c.get("lots") or []:
            if len(row) > 8 and row[7] == "parking_garage":
                garages.append([row[0], row[1], round(ground(row[0], row[1]) or 0), row[2],
                                round(row[4] / 1684, 3), round(row[3] / 2064, 3), round(1200 / 2466, 3)])
    out = {}
    for name, mesh, rows, box in (("Complex parking", "SM_Kit_ParkingLot", lots, [1, 1, 0.08]),
                                  ("Parking stalls", "SM_Kit_ParkingStall", stalls, [5.5, 2.6, 0.02]),
                                  ("Yard fences", "SM_Kit_Fence", fences, [1, 0.1, 2.5]),
                                  ("Parking garages", "SM_City_CT_MidRise", garages, [16.8, 20.6, 24.7])):
        if rows:
            out[name] = {"kind": "site", "role": "site", "mesh": mesh, "box_m": box, "count": len(rows),
                         "shown": len(rows), "points": rows, "meshes": [], "row": "x, y, z, yaw, sx, sy, sz"}
    if trees:  # drawn and baked like any tree layer (role 'tree')
        out["Garden trees"] = {"kind": "scatter", "role": "tree", "count": len(trees), "shown": len(trees), "points": trees,
                               "radius_cm": 250, "meshes": [], "build": "", "row": "x, y, z, yaw, scale"}
    for kind, rows in grounds.items():
        mesh, box, tint = GROUNDS[kind]
        out[GROUND_NAMES[kind]] = {"kind": "site", "role": "site", "mesh": mesh, "box_m": box, "tint": tint,
                                   "count": len(rows), "shown": len(rows), "points": rows, "meshes": [],
                                   "row": "x, y, z, yaw, sx, sy, sz"}
    return out


# the planned developments' ground parts (urban.ARCHETYPES): kind -> (mesh, size at scale 1 in m, preview tint)
GROUNDS = {"apron": ("SM_Kit_Apron", [1, 1, 0.1], "mix:fg-dim:bg:0.35"),
           "field": ("SM_Kit_SportsField", [1, 1, 0.02], "mix:green:fg:0.25"),
           "playground": ("SM_Kit_Playground", [1, 1, 0.05], "mix:red:amber:0.5"),
           "pool": ("SM_Kit_Pool", [1, 1, 0.1], "mix:blue:fg:0.3"),
           "yard": ("SM_Kit_UtilityYard", [1, 1, 0.05], "mix:fg-dim:bg:0.5"),
           "pond": ("SM_Kit_Pond", [1, 1, 0.05], "mix:blue:bg:0.3"),
           "tower": ("SM_Kit_WaterTower", [12, 12, 30], "fg-dim"),
           "telecom": ("SM_Kit_TelecomMast", [3.2, 3.2, 40], "fg-dim"),
           # a park's own (citygen.park_paths): its paths in pieces, each on its own ground, and the benches along them
           "path": ("SM_Kit_PathSlab", [1, 1, 0.08], "mix:fg-dim:bg:0.3"),
           "bench": ("SM_Prop_Bench", [1.94, 0.67, 1.11], "fg-dim")}
GROUND_NAMES = {"apron": "Service aprons", "field": "Sports fields", "playground": "Playgrounds", "pool": "Pools",
                "yard": "Utility yards", "pond": "Ponds and tanks", "tower": "Towers", "telecom": "Telecom masts",
                "path": "Park paths", "bench": "Park benches"}


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


def surface_samples(actors: list, cities: list, step: float = 300.0) -> list:
    """(x, y, half width) every ~step cm along everything that is road surface: every public road and street, a
    junction's arms with it (the mesh-less links from its middle to its mouths, which road_samples leaves out:
    a junction 25 m across was no road to anything placed beside it) and every private street of the cities."""
    out = []
    for pts, segs in actors:
        by_i = {p["i"]: p for p in pts}
        for s in segs:
            a, b = by_i.get(s["a"]), by_i.get(s["b"])
            if not a or not b:
                continue
            dx, dy = b["x"] - a["x"], b["y"] - a["y"]
            L = math.hypot(dx, dy)
            if L < 1:
                continue
            half = max(300.0, min(a["w"], b["w"]))  # (an arm: as wide as its mouth, where the kerb corners stand)
            n = max(1, int(L // step))
            out += [(a["x"] + dx * k / n, a["y"] + dy * k / n, half) for k in range(n + 1)]
    out += [(x, y, half) for x, y, half, _dx, _dy in street_samples(cities, step)]
    return out


def arm_samples(actors: list, step: float = 300.0) -> list:
    """(x, y, half width) along every junction's arms (its mesh-less links): road that road_samples does not
    list, because nothing is placed along it. preview() keeps everything else off it."""
    out = []
    for pts, segs in actors:
        by_i = {p["i"]: p for p in pts}
        for s in segs:
            a, b = by_i.get(s["a"]), by_i.get(s["b"])
            if s["mesh"] or not a or not b:
                continue
            dx, dy = b["x"] - a["x"], b["y"] - a["y"]
            n = max(1, int(math.hypot(dx, dy) // step))
            half = max(300.0, min(a["w"], b["w"]))
            out += [(a["x"] + dx * k / n, a["y"] + dy * k / n, half) for k in range(n + 1)]
    return out


NATURAL = ("tree", "street_tree", "bush", "rock")


def clear_roads(placed: dict, samples: list) -> dict:
    """Nothing stands on a road. Every layer is placed by its own rule (a setback from its own street, a lot, a
    yard, a grove round an interchange), and each rule knew some roads and not others: garden trees none at all,
    lamps and bins neither the junction they stand beside nor a private street (178 things on the roads of the
    owner's Valley, Nov 12 2026). So, last of all, everything is checked against every road surface
    (surface_samples): a tree, bush or rock goes when it stands within half a metre of one, street furniture when
    it stands on one (a metre in from its edge: its place is the kerb). Buildings, bridge parts and the pieces
    that are laid on the ground (stalls, aprons, sidewalk corners) are not looked at. Returns {layer: removed}."""
    cell = 3000.0
    grid: dict = {}
    for x, y, half in samples:
        grid.setdefault((int(x // cell), int(y // cell)), []).append((x, y, half))
    gone: dict = {}
    if not grid:
        return gone
    near = {(gx + i, gy + j) for gx, gy in grid for i in (-1, 0, 1) for j in (-1, 0, 1)}  # (most of a forest is far from any road)
    for name, layer in placed.items():
        if layer.get("kind") in ("buildings", "site", "walls") or layer.get("role") in ("bridge", "sidewalk_corner"):
            continue
        natural = layer.get("role") in NATURAL or layer.get("kind") in SCATTERED
        slack = 50.0 if natural else -100.0
        keep = []
        for p in layer.get("points") or []:
            cx, cy = int(p[0] // cell), int(p[1] // cell)
            if (cx, cy) not in near or not any(
                    (p[0] - sx) ** 2 + (p[1] - sy) ** 2 < (half + slack) ** 2
                    for gx in (cx - 1, cx, cx + 1) for gy in (cy - 1, cy, cy + 1) for sx, sy, half in grid.get((gx, gy), ())):
                keep.append(p)
        lost = len(layer.get("points") or []) - len(keep)
        if lost:
            gone[name] = lost
            layer["points"] = keep
            layer["shown"] = len(keep)
            layer["count"] = max(len(keep), int(layer.get("count", len(keep))) - lost)
    return gone


def clear_buildings(placed: dict, margin: float = 0.0) -> dict:
    """No tree, bush or rock inside a building: the forest is placed before the cities' buildings, and one that
    takes road frontage (a mansion, an office) knows nothing of a tree that grew there first. Only inside its
    walls: a yard tree half a metre behind its house is where the plan put it. Nor on a slab laid on the ground
    (a car park, an apron, a sports field, a pool): the trees planted round an interchange stood between the
    parked cars. Paved lots are not such a slab: a tree there stands in its pit. Returns {layer: removed}."""
    rows = [(p[0], p[1], p[3], p[8] * 50 + margin, p[7] * 50 + margin)
            for layer in placed.values() if layer.get("kind") == "buildings" for p in layer.get("points") or [] if len(p) > 8]
    rows += [(p[0], p[1], p[3], p[4] * 50, p[5] * 50)
             for layer in placed.values() if layer.get("kind") == "site" and layer.get("role") == "site"
             and list((layer.get("box_m") or [0, 0])[:2]) == [1, 1] and not layer.get("planted")
             for p in layer.get("points") or []]
    gone: dict = {}
    if not rows:
        return gone
    # (cells as wide as the widest building reaches: a tree looks at its own cell and the eight round it, and a
    # tree with no building in those nine, most of a forest, at nothing)
    cell = max(4000.0, max(math.hypot(r[3], r[4]) for r in rows))
    grid: dict = {}
    for x, y, yaw, hd, hw in rows:
        f = math.radians(yaw)
        grid.setdefault((int(x // cell), int(y // cell)), []).append((x, y, math.cos(f), math.sin(f), hd, hw))
    near = {(gx + i, gy + j) for gx, gy in grid for i in (-1, 0, 1) for j in (-1, 0, 1)}
    for name, layer in placed.items():
        if not (layer.get("role") in NATURAL or layer.get("kind") in SCATTERED):
            continue
        keep = []
        for p in layer.get("points") or []:
            cx, cy = int(p[0] // cell), int(p[1] // cell)
            if (cx, cy) not in near or not any(
                    abs((p[0] - x) * c + (p[1] - y) * s) < hd and abs(-(p[0] - x) * s + (p[1] - y) * c) < hw
                    for gx in (cx - 1, cx, cx + 1) for gy in (cy - 1, cy, cy + 1) for x, y, c, s, hd, hw in grid.get((gx, gy), ())):
                keep.append(p)
        lost = len(layer.get("points") or []) - len(keep)
        if lost:
            gone[name] = lost
            layer["points"] = keep
            layer["shown"] = len(keep)
            layer["count"] = max(len(keep), int(layer.get("count", len(keep))) - lost)
    return gone


def open_land(cities: list):
    """(x, y), a point inside a city's edge -> True when nothing was planned for that land: in no block that has a
    layout of its own (a subdivision, a complex, a park), on no lot (nor within 4 m of one) and on none of the
    planned grounds and parking lots, off the airfields. What is left is the middle of a block of plain lots, the land
    behind the last row of houses, the corner two streets leave. It stood bare inside every city (a quarter of
    the owner's Valley's cities, Nov 12 2026); the forest layers now grow there too."""
    from . import airport
    every = len(cities)
    cities = [c for c in cities if c.get("blocks")]  # (a city with no plan is built along its roads: all of it is "free")
    if not cities:
        return lambda x, y: False
    # (asked for a point that is not outside every city: with a plan for each, it is in a planned one)
    in_city = _where_test("cities", cities) if len(cities) != every else None
    cell = 5000.0
    laid: dict = {}  # cell -> the laid-out blocks that reach it
    rects: dict = {}  # cell -> what stands planned there, as turned rectangles
    fields = [a for c in cities for a in c.get("airports") or []]

    def add_rect(x, y, yaw, half_d, half_w):
        f = math.radians(yaw)
        r = math.hypot(half_d, half_w)
        for gx in range(int((x - r) // cell), int((x + r) // cell) + 1):
            for gy in range(int((y - r) // cell), int((y + r) // cell) + 1):
                rects.setdefault((gx, gy), []).append((x, y, math.cos(f), math.sin(f), half_d, half_w))
    for c in cities:
        for b in c.get("blocks") or []:
            if len(b) > 4 and len(b[4]) > 2 and b[2] != "parcels":
                xs, ys = [p[0] for p in b[4]], [p[1] for p in b[4]]
                for gx in range(int(min(xs) // cell), int(max(xs) // cell) + 1):
                    for gy in range(int(min(ys) // cell), int(max(ys) // cell) + 1):
                        laid.setdefault((gx, gy), []).append(b[4])
        for r in c.get("lots") or []:
            add_rect(r[0], r[1], r[2], r[4] / 2 + 400, r[3] / 2 + 400)
        priv = c.get("private") or {}
        for row in (priv.get("grounds") or []) + (priv.get("parking") or []):
            add_rect(row[0], row[1], row[2], row[3] * 50 + 300, row[4] * 50 + 300)

    def free(x: float, y: float) -> bool:
        if in_city and not in_city(x, y):
            return False
        key = (int(x // cell), int(y // cell))
        if any(abs((x - rx) * c + (y - ry) * s) < hd and abs(-(x - rx) * s + (y - ry) * c) < hw
               for rx, ry, c, s, hd, hw in rects.get(key, ())):
            return False
        if any(inside(poly, x, y) for poly in laid.get(key, ())):
            return False
        return not any(airport.inside(a, x, y, 3000) for a in fields)
    return free


PAVED_NORMAL = 0.3  # of the normal city's blocks of plain lots, how many are paved (downtown and industry: all)
PAVE_TILE_CM = 700.0  # a paved lot is laid in slabs about this size, each at the height of the ground under it
PAVE_DEEP_CM = 200.0  # ... and this deep: where the next slab lies lower its side shows as a low wall
PAVE_UP_CM = 6.0  # a slab's top over the highest ground under it
GREEN_ROLES = ("house", "mansion", "park")  # what stands on these keeps its garden


def paved_lots(cities: list, placed: dict, ground) -> dict:
    """The built-in layer 'Paved lots': in the city centre the ground is not grass. Every lot downtown, every lot
    of the industrial zone and the lots of some blocks of the normal city (PAVED_NORMAL of its blocks of plain
    lots, each block by its own draw) is covered edge to edge with concrete, laid in slabs PAVE_TILE_CM across.
    Each slab lies PAVE_UP_CM over the highest ground under it and is PAVE_DEEP_CM deep, so on a slope the slabs
    step down the hill like stacked boxes, each one's side the wall above the next; on level ground (under and
    round a building, where the lot was cut level) they join into one. `ground`: the finished ground, lots cut
    in: the paving levels nothing itself and never rises over a building's floor, the ground does not.
    Not a lot with a house, a mansion or a park on it, nor a spot a plan drew a building at (a complex, a
    facility: those have their own grounds). A tree on a paved lot stays: it stands in its pit.
    Rows [x, y, z, yaw, sx, sy, sz] for the 1 m apron slab, z its bottom."""
    built = _Rects()
    for layer in placed.values():
        if layer.get("kind") == "buildings":
            for p in layer.get("points") or []:
                if len(p) > 8:
                    f = math.radians(p[3])
                    built.add((p[0], p[1], math.cos(f), math.sin(f), p[7] * 50, p[8] * 50, layer.get("role")))
    downtown, industry = _where_test("downtown", cities), _where_test("industrial", cities)
    rows, lots = [], 0
    for c in cities:
        cell = 20000.0
        blocks: dict = {}
        for b in c.get("blocks") or []:
            if len(b) > 4 and len(b[4]) > 2:
                xs, ys = [p[0] for p in b[4]], [p[1] for p in b[4]]
                for gx in range(int(min(xs) // cell), int(max(xs) // cell) + 1):
                    for gy in range(int(min(ys) // cell), int(max(ys) // cell) + 1):
                        blocks.setdefault((gx, gy), []).append(b)
        for r in c.get("lots") or []:
            x, y, yaw, w, d = r[:5]
            if (len(r) > 8 and r[8] is True) or (len(r) > 7 and r[7] in GREEN_ROLES):
                continue
            b = next((b for b in blocks.get((int(x // cell), int(y // cell)), ()) if inside(b[4], x, y)), None)
            if b is not None and b[2] == "park":
                continue
            if not (industry(x, y) or (b[3] == "downtown" if b is not None else downtown(x, y))
                    or (b is not None and b[3] == "normal" and b[2] == "parcels"
                        and _u01("paved", round(b[0]), round(b[1])) < PAVED_NORMAL)):
                continue
            f = math.radians(yaw)
            fx, fy = math.cos(f), math.sin(f)
            if any(o[6] in GREEN_ROLES for o in built.near(x, y, math.hypot(w, d) / 2)
                   if _rects_hit((x, y, fx, fy, w / 2, d / 2), o, -100.0)):
                continue
            lots += 1
            # the lot in slabs: nu along its facing, nv across; each slab's top from the ground at its corners
            nu, nv = max(1, round(d / PAVE_TILE_CM)), max(1, round(w / PAVE_TILE_CM))
            du, dv = d / nu, w / nv
            at = lambda u, v: (x + fx * u - fy * v, y + fy * u + fx * v)
            node = [[ground(*at(-d / 2 + i * du, -w / 2 + j * dv)) or 0.0 for j in range(nv + 1)] for i in range(nu + 1)]
            top = [[max(node[i][j], node[i + 1][j], node[i][j + 1], node[i + 1][j + 1]) + PAVE_UP_CM for j in range(nv)]
                   for i in range(nu)]
            done_ = [[False] * nv for _ in range(nu)]
            for i in range(nu):
                for j in range(nv):
                    if done_[i][j]:
                        continue
                    same = lambda a, b_: not done_[a][b_] and abs(top[a][b_] - top[i][j]) <= 4.0
                    i1 = i
                    while i1 + 1 < nu and same(i1 + 1, j):
                        i1 += 1
                    j1 = j
                    while j1 + 1 < nv and all(same(a, j1 + 1) for a in range(i, i1 + 1)):
                        j1 += 1
                    z = max(top[a][b_] for a in range(i, i1 + 1) for b_ in range(j, j1 + 1))
                    for a in range(i, i1 + 1):
                        for b_ in range(j, j1 + 1):
                            done_[a][b_] = True
                    cx, cy = at(-d / 2 + (i + i1 + 1) / 2 * du, -w / 2 + (j + j1 + 1) / 2 * dv)
                    rows.append([round(cx), round(cy), round(z - PAVE_DEEP_CM), round(yaw, 1),
                                 round((i1 - i + 1) * du / 100, 2), round((j1 - j + 1) * dv / 100, 2), PAVE_DEEP_CM / 10.0])
    if not rows:
        return {}
    # (pad False: it lies on the ground as the lots left it and is never itself a reason to cut the ground)
    return {"Paved lots": {"kind": "site", "role": "site", "mesh": "SM_Kit_Apron", "box_m": [1, 1, 0.1],
                           "tint": "mix:fg-dim:bg:0.45", "planted": True, "pad": False, "lots": lots,
                           "count": len(rows), "shown": len(rows), "points": rows, "meshes": [],
                           "row": "x, y, z (its bottom), yaw, sx, sy, sz"}}


STREET_PIECES = ("TwoLane", "FourLane", "Boulevard", "OneLane", "PrivateStreet")  # the road pieces a street is laid with
END_CAP_CM = 350.0  # a street's end cap reaches this far past the road's own edge, all round


def dead_ends(actors: list, terrain: dict | None = None) -> list:
    """Per actor, every street end that needs a turnaround: [(end point i, the point before it)]. A street that
    runs out (a dead end at the city's edge, a stub the water or a freeway took the rest of, a cul-de-sac).
    Not where the road goes on in another actor (a street joining two cities, a highway at its gate), not at a
    bridge, a ramp or a freeway's end, not at the edge of the map. actors: every road actor [(points, segments)]."""
    at: dict = {}  # how many actors have a point at each spot (to the metre)
    for pts, _segs in actors:
        for key in {(round(p["x"] / 100), round(p["y"] / 100)) for p in pts}:
            at[key] = at.get(key, 0) + 1
    lo, hi = (terrain or {}).get("min"), (terrain or {}).get("max")
    sea = (terrain or {}).get("sea")
    out = []
    for pts, segs in actors:
        by_i = {p["i"]: p for p in pts}
        found = []
        out.append(found)
        ends: dict = {}
        for s in segs:
            for n, m in ((s["a"], s["b"]), (s["b"], s["a"])):
                ends.setdefault(n, []).append((m, s["mesh"]))
        for n, links in ends.items():
            p = by_i.get(n)
            if p is None or len(links) != 1 or p.get("mesh"):
                continue  # a street that goes on; a junction's own point
            other, mesh = by_i.get(links[0][0]), str(links[0][1] or "")
            if other is None or "Bridge" in mesh or not any(k in mesh for k in STREET_PIECES):
                continue
            if at.get((round(p["x"] / 100), round(p["y"] / 100)), 0) > 1:
                continue  # the road goes on in another actor
            if lo and hi and not (lo[0] + 6000 <= p["x"] <= hi[0] - 6000 and lo[1] + 6000 <= p["y"] <= hi[1] - 6000):
                continue
            if sea is not None and p["z"] < sea + 50:
                continue
            found.append((n, links[0][0]))
    return out


def road_ends(actors: list, terrain: dict | None = None) -> dict:
    """Where each dead end's turnaround lies ([x, y, z, yaw, sx, sy, sz], radius = sx * 1000): the area kept
    clear of PCG. The turnaround itself is a 1-arm junction piece on the road's spline (splines_text.cap_ends,
    built by Build pieces in the road's own materials), never a PCG mesh: this layer is not pushed."""
    rows = []
    for (pts, _segs), found in zip(actors, dead_ends(actors, terrain)):
        by_i = {p["i"]: p for p in pts}
        for n, o in found:
            p, other = by_i[n], by_i[o]
            r = max(300.0, float(p["w"])) + END_CAP_CM
            yaw = math.degrees(math.atan2(p["y"] - other["y"], p["x"] - other["x"]))
            rows.append([round(p["x"]), round(p["y"]), round(p["z"] + 3), round(yaw, 1), round(r / 1000, 3), round(r / 1000, 3), 1.0])
    if not rows:
        return {}
    return {"Road ends": {"kind": "site", "role": "site", "mesh": "SM_Kit_Turnaround", "box_m": [20, 20, 0.1], "on_road": True,
                          "keep_z": True, "pad": False, "count": len(rows), "shown": len(rows), "points": rows, "meshes": [],
                          "row": "x, y, z, yaw, sx, sy, sz"}}


def aprons(placed: dict) -> dict:
    """A dropped kerb where every driveway meets its street: a concrete apron from the lot line across the
    3.5 m sidewalk to the carriageway, a metre wider than the drive, lying on the sidewalk (the kerb is lowered
    there, the sidewalk runs on across it). Built from the previewed lots' driveways ([.., paths])."""
    rows = []
    for v in placed.values():
        if v.get("kind") != "buildings":
            continue
        for p in v.get("points") or []:
            for width, flat in (p[10] if len(p) > 10 else []):
                if not 3.0 <= width < 8.0 or len(flat) < 6:
                    continue
                ux, uy = flat[-3] - flat[0], flat[-2] - flat[1]
                d = math.hypot(ux, uy)
                if d > 1:
                    rows.append([round(flat[-3] + ux / d * 175), round(flat[-2] + uy / d * 175), flat[-1],
                                 round(math.degrees(math.atan2(uy, ux)), 1), 3.5, width + 1.0, 1.0])
    if not rows:
        return {}
    return {"Driveway aprons": {"kind": "site", "role": "site", "mesh": "SM_Kit_Apron", "box_m": [1, 1, 0.1], "on_road": True,
                                "tint": "mix:fg-dim:bg:0.2", "count": len(rows), "shown": len(rows), "points": rows,
                                "meshes": [], "row": "x, y, z, yaw, sx, sy, sz"}}


def interchange_groves(fast: list, ground, sea=None, radius: float = 16000.0, count: int = 90) -> dict:
    """Trees in and round every interchange: the land inside the loops and between the ramps is right of way, so
    no lot is cut there and it stood bare. fast: the freeway / highway actors [(points, segments)]; an interchange
    is where their ramp connectors (gores, flares) cluster. `count` trees in a disc of `radius` round each, the
    same every time; the caller takes out the ones on a road (clear_of). Never houses: nobody lives in a loop."""
    import random
    ends = [(p["x"], p["y"]) for pts, _segs in fast for p in pts if str(p.get("mesh") or "").startswith("SM_Road_Ramp")]
    groups: list = []
    for e in ends:  # greedy clusters, 300 m: one interchange's ramp ends
        g = next((g for g in groups if math.dist(g[0], e) < 30000), None)
        if g:
            g.append(e)
        else:
            groups.append([e])
    rows = []
    for g in groups:
        if len(g) < 3:
            continue  # a lone ramp end is no interchange
        cx, cy = sum(p[0] for p in g) / len(g), sum(p[1] for p in g) / len(g)
        rnd = random.Random(f"grove:{round(cx)}:{round(cy)}")
        for k in range(count):
            a, d = rnd.uniform(0, 2 * math.pi), radius * math.sqrt(rnd.random())
            x, y = cx + math.cos(a) * d, cy + math.sin(a) * d
            z = ground(x, y)
            if z is not None and (sea is None or z > sea + 50):
                rows.append([round(x), round(y), round(z), round(rnd.uniform(0, 360)), round(rnd.uniform(0.8, 1.3), 2)])
    if not rows:
        return {}
    return {"Interchange trees": {"kind": "scatter", "role": "tree", "count": len(rows), "shown": len(rows), "points": rows,
                                  "radius_cm": 250, "meshes": [], "build": "", "row": "x, y, z, yaw, scale"}}


def off_roads(streets: list, samples: list, margin: float = 1000.0) -> list:
    """Private streets stopped short of every freeway, ramp and highway (road_samples of those actors): each
    polyline is walked every 5 m and cut where it comes within margin of one, the pieces under 15 m dropped.
    A subdivision street laid across a highway ramp was graded like a road and filled the ramp's cutting."""
    cell = 3000.0
    grid: dict = {}
    for x, y, half, _dx, _dy in samples:
        grid.setdefault((int(x // cell), int(y // cell)), []).append((x, y, half + 400.0 + margin))
    if not grid:
        return streets
    near = lambda x, y: any(math.hypot(x - sx, y - sy) < r for i in (-1, 0, 1) for j in (-1, 0, 1)
                            for sx, sy, r in grid.get((int(x // cell) + i, int(y // cell) + j), ()))
    out = []
    for s in streets:
        piece: list = []
        pieces = [piece]
        for a, b in zip(s, s[1:]):
            n = max(1, int(math.dist(a[:2], b[:2]) // 500))
            for k in range(n + (b is s[-1])):
                p = [round(a[0] + (b[0] - a[0]) * k / n), round(a[1] + (b[1] - a[1]) * k / n)]
                if near(*p):
                    piece = []
                    pieces.append(piece)
                else:
                    piece.append(p)
        out += [s] if len(pieces) == 1 else [p for p in pieces if len(p) > 3]  # an uncut street keeps its own points
    return out


def clear_of(placed: dict, samples: list, margin: float = 200.0) -> int:
    """Nothing stands on or under a freeway, a ramp or a highway: every placed thing whose footprint reaches a
    road sample (road_samples of those actors) is taken out. Lots are cut from the city's blocks before the
    highways and their interchanges are laid, so towers stood on ramps (Nov 12 2026). Bridge parts stay.
    Returns how many were removed."""
    cell = 3000.0
    grid: dict = {}
    for x, y, half, _dx, _dy in samples:
        grid.setdefault((int(x // cell), int(y // cell)), []).append((x, y, half))
    if not grid:
        return 0

    def hit(x, y, r):
        n = int((r + 2500) // cell) + 1
        cx, cy = int(x // cell), int(y // cell)
        return any((x - sx) ** 2 + (y - sy) ** 2 < (r + half + margin) ** 2
                   for gx in range(cx - n, cx + n + 1) for gy in range(cy - n, cy + n + 1) for sx, sy, half in grid.get((gx, gy), ()))
    gone = 0
    for name, layer in placed.items():
        if layer.get("role") == "bridge" or name.startswith(("Bridge", "Pier")):
            continue
        box = layer.get("box_m")
        keep = []
        for p in layer.get("points") or []:
            if layer.get("kind") == "buildings" and len(p) > 8:
                r = 50.0 * max(p[7], p[8]) * 0.8  # width_m, depth_m
            elif box and len(p) > 5:
                r = 50.0 * max(box[0] * p[4], box[1] * p[5]) * 0.8
            else:
                r = float(layer.get("radius_cm") or 150.0) * 0.5
            if not hit(p[0], p[1], r):
                keep.append(p)
        lost = len(layer.get("points") or []) - len(keep)
        if lost:
            gone += lost
            layer["points"] = keep
            layer["shown"] = len(keep)
            layer["count"] = max(len(keep), int(layer.get("count", len(keep))) - lost)
    return gone


def inside(poly: list, x: float, y: float) -> bool:
    """Point in polygon (even-odd ray cast)."""
    hit = False
    for (ax, ay), (bx, by) in zip(poly, poly[1:] + poly[:1]):
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            hit = not hit
    return hit


OUTLINE_MAX = 1.27  # outline() is 1 +- 0.265 at most: this far past a ring is outside it in every direction
OUTLINE_MIN = 0.73  # ... and this far inside it is inside it in every direction


def _where_test(where: str, cities: list):
    """_in_where for one layer, as (x, y) -> bool: the rings worked out once, and a point farther than any
    outline reaches from a city skips that city's outline (most forest candidates are)."""
    if where == "everywhere" or not cities:
        return lambda x, y: True
    if where == "shipyards":
        from . import shipyard
        yards = [s for c in cities for s in c.get("shipyards") or []]
        return lambda x, y: any(shipyard.zone(s, *shipyard.uv(s, x, y)) == "platform" for s in yards)
    # a block the person rezoned (city 'rezoned': [[polygon, zone]]) is that zone whatever ring it lies in
    # (the bigger city first: where two overlap, the land is the bigger one's, citygen.yield_plan)
    rows = [(c, c["x"], c["y"], *rings(c),
             [(poly, min(p[0] for p in poly), min(p[1] for p in poly), max(p[0] for p in poly), max(p[1] for p in poly), zone)
              for poly, zone in c.get("rezoned") or []])
            for c in sorted(cities, key=lambda c: (-float(c["radius"]), str(c.get("name") or "")))]

    def test(x: float, y: float) -> bool:
        for c, cx, cy, down, normal, outer, rezoned in rows:
            # (before the rings: the houses along a street that joins two cities lie outside both cities' edges)
            for poly, x0, y0, x1, y1, zone in rezoned:
                if x0 <= x <= x1 and y0 <= y <= y1 and inside(poly, x, y):
                    return where == "cities" or where == zone
        for c, cx, cy, down, normal, outer, rezoned in rows:
            dist = math.hypot(x - cx, y - cy)
            if dist >= outer * OUTLINE_MAX:
                continue
            if dist < outer * OUTLINE_MIN and where in ("outside", "cities"):
                return where == "cities"  # inside the outer ring whichever way its outline bends
            # distance against the outline in this direction: the same bent edges the plan's polygons have
            # (from the city's centre district, where that was moved)
            d = zone_share(c, x, y) * c["radius"] if c.get("core") else dist / outline(c, math.degrees(math.atan2(y - cy, x - cx)))
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
    return test


def _in_where(where: str, cities: list, x: float, y: float) -> bool:
    """Same areas as masks(): rings per city, the industrial wedge cut out of normal and housing."""
    return _where_test(where, cities)(x, y)


def _rects_hit(a: tuple, b: tuple, margin: float = 0.0) -> bool:
    """Do two turned rectangles (x, y, cos, sin of the way they face, half width across, half depth along) come
    within `margin` of each other? Separating axes: exact, where a circle test let long houses overlap end on."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    for ax, ay in ((a[2], a[3]), (-a[3], a[2]), (b[2], b[3]), (-b[3], b[2])):
        reach = sum(r[5] * abs(ax * r[2] + ay * r[3]) + r[4] * abs(-ax * r[3] + ay * r[2]) for r in (a, b))
        if abs(dx * ax + dy * ay) > reach + margin:
            return False
    return True


class _Rects:
    """Every building placed so far, as the rectangle it stands on: nothing is built on top of another."""

    def __init__(self, cell: float = 4000.0):
        self.cell, self.grid, self.reach = cell, {}, 0.0

    def add(self, r: tuple) -> None:
        self.grid.setdefault((int(r[0] // self.cell), int(r[1] // self.cell)), []).append(r)
        self.reach = max(self.reach, math.hypot(r[4], r[5]))

    def near(self, x: float, y: float, reach: float):
        n = int((reach + self.reach) // self.cell) + 1
        cx, cy = int(x // self.cell), int(y // self.cell)
        return (r for gx in range(cx - n, cx + n + 1) for gy in range(cy - n, cy + n + 1) for r in self.grid.get((gx, gy), ()))

    def hit(self, r: tuple, margin: float = 100.0) -> bool:
        return any(_rects_hit(r, o, margin) for o in self.near(r[0], r[1], math.hypot(r[4], r[5]) + margin))


def street_samples(cities: list, step: float = 400.0) -> list:
    """(x, y, half width, dir x, dir y) along every private street (the cities' subdivision and complex streets):
    they are roads too, and nothing is built on them."""
    out = []
    for c in cities:
        for s in (c.get("private") or {}).get("streets") or []:
            for p, q in zip(s, s[1:]):
                L = math.dist(p[:2], q[:2])
                if L < 1:
                    continue
                for k in range(max(1, int(L // step)) + 1):
                    t = min(1.0, k * step / L)
                    out.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t, 400.0, (q[0] - p[0]) / L, (q[1] - p[1]) / L))
    return out


def fit_fences(placed: dict, samples: list) -> int:
    """Yard fences that make sense only: one stays when a house stands on its lot (within 22 m), it runs through
    no building, crosses no road or private street and no fence kept before it. Lots overlapped inside street
    bends, and every lot's fences were drawn whether or not its house was. Returns how many went."""
    layer = placed.get("Yard fences")
    if not layer:
        return 0
    rects = _Rects()
    for v in placed.values():
        if v.get("kind") == "buildings":
            for p in v.get("points") or []:
                if len(p) > 8:
                    rects.add((p[0], p[1], math.cos(math.radians(p[3])), math.sin(math.radians(p[3])), p[7] * 50, p[8] * 50))
    roads = _Hash()
    for x, y, half, _dx, _dy in samples:
        roads.add(x, y, half, (x, y, half))

    def cross(a, b, c, d):  # do segments a-b and c-d cross away from their ends?
        den = (b[0] - a[0]) * (d[1] - c[1]) - (b[1] - a[1]) * (d[0] - c[0])
        if abs(den) < 1e-9:
            return False
        t = ((c[0] - a[0]) * (d[1] - c[1]) - (c[1] - a[1]) * (d[0] - c[0])) / den
        u = ((c[0] - a[0]) * (b[1] - a[1]) - (c[1] - a[1]) * (b[0] - a[0])) / den
        return 0.06 < t < 0.94 and 0.06 < u < 0.94
    kept, lines = [], _Rects()
    for row in layer.get("points") or []:
        x, y, yaw, h = row[0], row[1], math.radians(row[3]), row[4] * 50
        c, s = math.cos(yaw), math.sin(yaw)
        me = (x, y, c, s, 10.0, h)
        a, b = (x - c * h, y - s * h), (x + c * h, y + s * h)
        if not any(math.hypot(r[0] - x, r[1] - y) < 2200 for r in rects.near(x, y, 2200)) or rects.hit(me, 30.0):
            continue
        if any(roads.hit(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, 50.0) for t in (0.0, 0.25, 0.5, 0.75, 1.0)):
            continue
        if any(cross(a, b, (o[0] - o[2] * o[5], o[1] - o[3] * o[5]), (o[0] + o[2] * o[5], o[1] + o[3] * o[5])) for o in lines.near(x, y, h)):
            continue
        lines.add(me)
        kept.append(row)
    gone = len(layer.get("points") or []) - len(kept)
    layer["points"], layer["count"], layer["shown"] = kept, len(kept), len(kept)
    return gone


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
        if math.hypot(x - c["x"], y - c["y"]) > c["radius"] * 1.3:
            continue
        best = max(best, math.exp(-(zone_share(c, x, y) / 0.22) ** 2))
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


TILE_CM = 50000.0  # scatter, roadside and junction pieces are placed in 500 m tiles. A tile's candidates come from a
# random of its own, a city's lots are taken in an order of their own, and every piece's size, height and shape
# come from its own spot: what stands in one place never depends on what was placed anywhere else, so changing one
# city leaves the rest of the world exactly as it was
DIRTY_CM = 10000.0  # a change is tracked on a 100 m grid (PreviewMemo)


def _u01(*key) -> float:
    """A fixed number 0..1 for a key (a layer and a spot): the same on every run, whatever else is placed."""
    return zlib.crc32(repr(key).encode()) / 4294967296.0


def _city_sig(c: dict) -> tuple:
    """Everything of a city the placement reads, as (where and how big it is, its plan part by part): a city that
    moved, turned or changed size is placed again whole; one whose plan changed in a block, only round what
    changed. Each part: {a row as text: where that row is ((x, y) or a polygon or a line)}."""
    num = lambda v: round(float(v), 2) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
    frame = zlib.crc32(repr([num(c.get(k)) for k in ("x", "y", "radius", "seed", "spin")]
                            + [c.get(k) for k in ("shape", "districts", "shipyards", "shores")]).encode())
    parts = ({repr(r): [r[:2]] for r in c.get("lots") or []},
             {repr(b): b[4] for b in c.get("blocks") or [] if len(b) > 4},
             {repr(z): z[0] for z in c.get("rezoned") or []},
             {repr(s): s for s in (c.get("private") or {}).get("streets") or []})
    return frame, parts


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
            memo: PreviewMemo | None = None, arms: list | None = None) -> dict:
    """Run every enabled layer offline with the same rules as the graph (mask, off roads, keep clear of
    earlier layers, self pruning, sink) and return {layer name: {points: [[x, y, z, yaw, scale]], ...}}.
    Scatter is thinned to ``limit`` points per layer for the view; ``count`` says the full estimate.
    Every layer is placed unit by unit (a city's lots; a 500 m tile's scatter, roadside or junction pieces) and
    nothing in a unit depends on a random shared with another, so a change in one place moves nothing elsewhere.
    ``memo`` (a PreviewMemo kept by the caller for this world) puts back every unit no change can reach and
    places only the others again. ``arms`` (arm_samples): the junctions' own road surface, which nothing is
    placed along but everything keeps off."""
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
    from . import shipyard, waterfront
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
    for layer in layers:
        seed, name = layer["seed"], layer["name"]
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
                    if layer["cover"] < 1.0 and patch(x, y) > layer["cover"]:
                        continue  # a meadow, not a forest
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
    pv = preview(full, cities, ter, road_samples(actors), lambda x, y: 0.0, juncs=juncs, arms=arm_samples(actors))
    for name in ("Trees", "Rocks", "Skyscrapers", "Office blocks", "Factories", "Warehouses", "Apartments", "Shops",
                 "Houses", "Traffic lights", "Street lamps", "Trash bins"):
        assert pv[name]["shown"] > 0, (name, {n: v["shown"] for n, v in pv.items()})
    for name, v in pv.items():
        for x, y, *_ in v["points"]:
            assert min(abs(x), abs(y)) > 875 or max(abs(x), abs(y)) > 60000, f"{name} on a road at {x},{y}"
    od = lambda p: math.hypot(p[0], p[1]) / outline(cities[0], math.degrees(math.atan2(p[1], p[0])))  # outline distance
    assert all(od(p) >= 50000 for p in pv["Trees"]["points"]), "trees stay outside the city's irregular edge"
    # a forest covers the whole map: as dense as asked within its reach of a city; beyond it just as dense when
    # the layer's budget of points allows, thinner when it does not, but never gone (it once stopped at its reach)
    wood = clean({"name": "Wood", "kind": "foliage", "where": "outside", "role": "tree", "density": 0.05, "cover": 1.0,
                  "reach": 1.2, "seed": 3})
    wide = {"min": [-200000, -200000], "max": [200000, 200000]}
    per_ha = lambda pts, lo_, hi_: sum(1 for p in pts if lo_ <= math.hypot(p[0], p[1]) < hi_) / (math.pi * (hi_ ** 2 - lo_ ** 2) / 1e8)
    roomy = preview([wood], cities, wide, [], lambda x, y: 0.0, foliage_limit=200000)["Wood"]["points"]
    assert per_ha(roomy, 65000, 110000) > 0.8 * per_ha(roomy, 52000, 60000) > 2, (per_ha(roomy, 65000, 110000), per_ha(roomy, 52000, 60000))
    tight = preview([wood], cities, wide, [], lambda x, y: 0.0, foliage_limit=1000)["Wood"]["points"]
    near_, far_ = per_ha(tight, 52000, 60000), per_ha(tight, 65000, 190000)
    assert 0 < far_ < 0.6 * near_, (near_, far_)
    assert {tuple(p[:2]) for p in roomy if math.hypot(p[0], p[1]) < 60000} == \
        {tuple(p[:2]) for p in preview([clean({**wood, "reach": 0})], cities, wide, [], lambda x, y: 0.0, foliage_limit=200000)["Wood"]["points"]
         if math.hypot(p[0], p[1]) < 60000}, "round the cities it stands where it stood"
    # nothing stands on a road, whatever placed it: a junction's arms and a private street are road too. Trees
    # and bushes go when they are within half a metre of one, street furniture when it stands a metre into one
    town = [{"name": "T", "x": 0, "y": 0, "radius": 50000, "private": {"streets": [[[20000, 20000], [30000, 20000]]]}}]
    surface = surface_samples(actors, town)
    assert len(arm_samples(actors)) > 12 and any(abs(x) < 1 and abs(y) < 1 for x, y, _h in surface), "the junction's middle is road"
    stuff = {"Garden trees": {"kind": "scatter", "role": "tree", "count": 4, "shown": 4,
                              "points": [[300, 400, 0, 0, 1], [0, 30000, 0, 0, 1], [25000, 20200, 0, 0, 1], [9000, 9000, 0, 0, 1]]},
             "Street lamps": {"kind": "along_roads", "role": "street_lamp", "count": 3, "shown": 3,
                              "points": [[30000, 1025, 0, 0, 1], [30000, 500, 0, 0, 1], [900, 900, 0, 0, 1]]},
             "Houses": {"kind": "buildings", "role": "house", "count": 1, "shown": 1,
                        "points": [[0, 0, 0, 0, 1, 6, 0, 10.0, 8.0]]},
             "Parking stalls": {"kind": "site", "role": "site", "count": 1, "shown": 1, "points": [[0, 0, 0, 0, 1, 1, 1]]}}
    assert clear_roads(stuff, surface) == {"Garden trees": 3, "Street lamps": 1}, stuff
    assert stuff["Garden trees"]["points"] == [[9000, 9000, 0, 0, 1]] and stuff["Garden trees"]["count"] == 1
    assert [p[:2] for p in stuff["Street lamps"]["points"]] == [[30000, 1025], [900, 900]], "a lamp at the kerb, one on the corner: they stay"
    assert len(stuff["Houses"]["points"]) == 1 and len(stuff["Parking stalls"]["points"]) == 1
    # a cap on every street that ends: the dead end gets a turnaround; a through street, a street that goes on in
    # another actor (two cities joined), a bridge's end and a freeway's end get none
    street = lambda i, a, b: {"a": a, "b": b, "mesh": "SM_Road_TwoLaneSidewalk_Straight10m"}
    pt = lambda i, x, y, mesh="": {"i": i, "x": x, "y": y, "z": 120.0, "w": 875, "mesh": mesh}
    town_a = ([pt(0, 0, 0), pt(1, 10000, 0), pt(2, 20000, 0, "SM_Junc_Gen"), pt(3, 20000, 9000), pt(4, 30000, 0)],
              [street(0, 0, 1), street(1, 1, 2), street(2, 2, 3), street(3, 2, 4)])
    town_b = ([pt(0, 30000, 0), pt(1, 40000, 0), pt(2, 50000, 0)],
              [street(0, 0, 1), {"a": 1, "b": 2, "mesh": "SM_Road_BridgeSidewalk_Straight20m"}])
    fast = ([pt(0, 0, 50000), pt(1, 9000, 50000)], [{"a": 0, "b": 1, "mesh": "SM_Road_Freeway_Lane20m"}])
    caps = road_ends([town_a, town_b, fast], {"min": [-90000, -90000], "max": [90000, 90000]})["Road ends"]
    assert sorted(r[:2] for r in caps["points"]) == [[0, 0], [20000, 9000]], caps["points"]
    assert caps["points"][0][2] == 123 and caps["points"][0][4] == 1.225 and caps["keep_z"] and caps["pad"] is False
    assert not road_ends([town_a], {"min": [-3000, -90000], "max": [33000, 90000]})["Road ends"]["points"][0][0] == 0, "none at the map's edge"
    # ... and no tree inside a building (the forest is placed first; a house built along a road knows nothing of it)
    grove = {"Trees": {"kind": "foliage", "role": "tree", "count": 4, "shown": 4,
                       "points": [[10000, 10000, 0, 0, 1], [10300, 10350, 0, 0, 1], [10450, 10000, 0, 0, 1], [10700, 10000, 0, 0, 1]]},
             "Houses": {"kind": "buildings", "role": "house", "count": 1, "shown": 1,
                        "points": [[10000, 10000, 0, 0, 1, 6, 0, 10.0, 8.0]]}}
    assert clear_buildings(grove) == {"Trees": 2}, "(the house is 8 m deep along x, 10 m wide: the first two are inside it)"
    assert grove["Trees"]["points"] == [[10450, 10000, 0, 0, 1], [10700, 10000, 0, 0, 1]], "a tree half a metre behind the wall stays"
    # ... nor on a car park; a tree on a paved lot stays (it stands in its pit)
    lot_trees = lambda: {"Trees": {"kind": "foliage", "role": "tree", "count": 2, "shown": 2, "points": [[500, 0, 0, 0, 1], [5000, 0, 0, 0, 1]]}}
    slab = {"kind": "site", "role": "site", "mesh": "SM_Kit_ParkingLot", "box_m": [1, 1, 0.08], "points": [[0, 0, 0, 0.0, 30.0, 20.0, 1.0]]}
    assert clear_buildings({**lot_trees(), "Complex parking": slab}) == {"Trees": 1}
    assert clear_buildings({**lot_trees(), "Paved lots": {**slab, "planted": True}}) == {}
    # a planned park is trees in groves on the ground as it lies, off its own playground and paths; it levels nothing
    park_c = {**cities[0], "private": {"grounds": [[0, 0, 0.0, 20.0, 14.0, "playground", "neighbourhood_park"],
                                                   [0, 2000, 0.0, 60.0, 3.0, "path", "neighbourhood_park"]], "parking": []}}
    green = {"Parks": {"kind": "buildings", "role": "park", "points": [[0, 0, 0, 0, 1.0, 1.0, 0, 80.0, 60.0, 0.0, [], []]]}}
    grown = park_trees(green, [park_c], lambda x, y: 0.1 * x)["Park trees"]["points"]
    assert 15 < len(grown) < 0.8 * (80 * 60 / 49), ("groves and lawns", len(grown))
    assert all(abs(t[0]) <= 3000 and abs(t[1]) <= 4000 and abs(t[2] - 0.1 * t[0]) <= 1 for t in grown), "inside the park, each on its own ground"
    assert not any(abs(t[0]) < 1000 and abs(t[1]) < 700 for t in grown) and not any(abs(t[0]) < 3000 and abs(t[1] - 2000) < 150 for t in grown)
    assert not pads(green), "a park levels nothing"
    # the city centre stands on concrete, lot by lot: a downtown lot is paved at its building's level, an empty one
    # at the ground's, a house's lot and a suburb's are not; the ground is levelled under each, to its own edge
    core = {**cities[0], "blocks": [[0, 0, "parcels", "downtown", [(-6000, -6000), (6000, -6000), (6000, 6000), (-6000, 6000)], 0]],
            "lots": [[-3000, 0, 90.0, 2800, 3000, -3000, 1500, "", False], [0, 0, 90.0, 2800, 3000, 0, 1500, "", False],
                     [3000, 0, 90.0, 2800, 3000, 3000, 1500, "house", False], [45000, 0, 90.0, 1600, 3000, 45000, 1500, "", False]]}
    stands = {"Offices": {"kind": "buildings", "role": "office", "points": [[-3000, 200, 340, 90, 1.0, 20.0, 0, 24.0, 22.0, 0.5]]},
              "Homes": {"kind": "buildings", "role": "house", "points": [[3000, 200, 100, 90, 1.0, 6.0, 0, 12.0, 10.0, 0.5]]}}
    paved = paved_lots([core], stands, lambda x, y: 100.0)["Paved lots"]
    # level ground: one slab a lot, the whole lot (30 m deep along its facing, 28 m wide), its top 6 cm over the ground
    assert [p[:2] + p[4:6] for p in paved["points"]] == [[-3000, 0, 30.0, 28.0], [0, 0, 30.0, 28.0]], paved["points"]
    assert paved["lots"] == 2 and all(p[2] + paved["box_m"][2] * 100 * p[6] == 100 + PAVE_UP_CM for p in paved["points"])
    assert not pads({"Paved lots": paved}), "it lies on the ground as it is: it levels nothing"
    # a slope: the slabs step down it like stacked boxes, none below the ground under it, none more than a step over
    hill = lambda x, y: 100.0 + 0.1 * y
    steps = paved_lots([core], {}, hill)["Paved lots"]["points"]
    assert len(steps) > 6 and len({p[2] for p in steps}) > 3, len(steps)
    for p in steps:
        top, far = p[2] + PAVE_DEEP_CM, p[1] + p[4] * 50  # (yaw 90: its depth runs along y)
        assert hill(0, far) + PAVE_UP_CM - 1 <= top <= hill(0, far) + PAVE_UP_CM + 5, (p, top, hill(0, far))
    area = sum(p[4] * p[5] for p in steps)
    assert abs(area - 2 * 30.0 * 28.0) < 1.0, ("both lots covered edge to edge, no slab twice; the house's lot is not", area)
    # the land a planned city leaves free: the middle of a block of plain lots, the ground beyond its last lots;
    # never a lot, a block with its own layout, a planned ground. A city with no plan has none.
    sq = lambda x0, y0, s: [[x0, y0], [x0 + s, y0], [x0 + s, y0 + s], [x0, y0 + s]]
    planned = [{**cities[0], "blocks": [[5000, 5000, "parcels", "normal", sq(0, 0, 10000), 0], [25000, 5000, "loop", "normal", sq(20000, 0, 10000), 0]],
                "lots": [[1000, 5000, 0.0, 1600, 2000, 2000, 5000, "", False]],
                "private": {"grounds": [[5000, 9000, 0.0, 20.0, 10.0, "field"]]}}]
    free = open_land(planned)
    assert free(5000, 5000) and free(-5000, 5000), "a court, and land no block has"
    assert not free(1000, 5000) and not free(2300, 5000) and not free(25000, 5000) and not free(5000, 9000), "a lot, 3 m off it, a subdivision, a sports field"
    assert not open_land(cities)(5000, 5000) and not open_land(planned + cities)(-5000, 5000 + 200000), "a city with no plan has none"
    wild = clean({"name": "Wild", "kind": "scatter", "where": "outside", "role": "tree", "density": 2.0, "seed": 9})
    grown = preview([wild], planned, {"min": [-80000, -80000], "max": [80000, 80000]}, [], lambda x, y: 0.0, limit=100000)["Wild"]["points"]
    in_town = [p for p in grown if math.hypot(p[0], p[1]) < 30000]
    assert len(in_town) > 200 and not any(20000 <= p[0] <= 30000 and 0 <= p[1] <= 10000 for p in in_town), "the forest grows on the city's free land, not in its subdivision"
    assert not any(abs(p[0] - 1000) < 1300 and abs(p[1] - 5000) < 1100 for p in in_town), "nor on a lot"
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
    # flat ground: no foundation; a house gets one straight drive, a shop one walkway: one strip a lot
    h = pv["Houses"]["points"][0]
    assert h[9] == 0.0 and [p[0] for p in h[10]] == [DRIVE_M] and len(h[10][0][1]) == 6, h
    assert len(pv["Shops"]["points"][0][10]) == 1
    assert pv["Parks"]["shown"] == 0 or pv["Parks"]["points"][0][10] == []
    # a hillside rising 30 % away from the street: a level pad at the mean, a foundation where it falls away,
    # the walkway goes straight down and the driveway winds across the slope
    house = clean({"name": "H", "kind": "buildings", "role": "house"})
    slope = lambda x, y: -0.3 * x  # +X (the street side) is downhill
    pad, found, paths = _lot_ground(house, slope, 0, 0, 0.0, 12, 12)
    assert abs(pad) < 1 and found == 1.8, (pad, found)
    assert len(h) > 11 and h[11] == [], "no wall slabs on lot edges"
    # a block the person rezoned is that zone for every layer, whatever ring it lies in
    hx = next(r for r in range(20000, 60000, 500) if _in_where("housing", cities, r, 0))  # a spot in the housing ring
    sq = [[hx - 1000, -2000], [hx + 1000, -2000], [hx + 1000, 2000], [hx - 1000, 2000]]
    zoned = [{**cities[0], "rezoned": [[sq, "downtown"]]}]
    assert _in_where("downtown", zoned, hx, 0) and not _in_where("housing", zoned, hx, 0)
    assert _in_where("cities", zoned, hx, 0) and not _in_where("outside", zoned, hx, 0)
    assert all(_in_where(w, zoned, hx, 9000) == _in_where(w, cities, hx, 9000)
               for w in ("downtown", "normal", "housing", "industrial")), "the block beside it is as it was"
    # a city whose centre district was moved: downtown, and the skyline's peak, are round the new spot
    off_c = {**cities[0], "core": [20000, 0]}
    assert _where_test("downtown", [off_c])(20000, 0) and not _where_test("downtown", [off_c])(-15000, 0)
    assert _where_test("downtown", cities)(-15000, 0) and core_peak([off_c], 20000, 0) == 1.0 > core_peak([off_c], 0, 0)
    assert _where_test("cities", [off_c])(-45000, 0) and not _where_test("cities", [off_c])(70000, 0), "its edge stays"
    ring_ = _ring(off_c, 20000.0)
    assert inside(ring_, 20000, 0) and not inside(ring_, -15000, 0), "the downtown ring is drawn round it"
    # what a city planned stays free for it: a tower along the street stands on no planned car park, and not on
    # the spot of a planned building that a later layer builds
    lot_town = {**cities[0], "lots": [[9000, -3000, 90.0, 3000, 2500, 9000, -1700, "shop", True]],
                "private": {"parking": [[-9000, -3000, 0.0, 60.0, 30.0]], "stalls": []}}
    tower = {"name": "Towers", "kind": "buildings", "role": "skyscraper", "where": "cities", "width_m": 30, "depth_m": 30,
             "spacing_m": 40, "setback_m": 4, "order": 1}
    shops = {"name": "Shops", "kind": "buildings", "role": "shop", "width_m": 14, "depth_m": 12, "order": 2}
    street = [(x, -100.0, 700.0, 1.0, 0.0) for x in range(-30000, 30001, 400)]
    stood = preview([tower, shops], [lot_town], {"min": [-80000, -80000], "max": [80000, 80000]}, street, lambda x, y: 0.0)
    for p in stood["Towers"]["points"]:
        assert not (-12000 - 1500 < p[0] < -6000 + 1500 and -4500 - 1500 < p[1] < -1500 + 1500), ("a tower on a car park", p[:2])
        assert not (7500 - 1500 < p[0] < 10500 + 1500 and -4250 - 1500 < p[1] < -1750 + 1500), ("a tower on a planned shop's spot", p[:2])
    assert stood["Towers"]["points"] and len(stood["Shops"]["points"]) == 1, "the towers stand elsewhere along the street; the shop is built"
    # a block laid out as a park: a layer that builds along road frontage leaves it empty
    front = clean({"name": "Mansions", "kind": "buildings", "where": "cities", "width_m": 30, "depth_m": 30, "spacing_m": 60,
                   "setback_m": 6, "role": "mansion"})
    street = [(hx - 8000 + i * 400, -2600, 400.0, 1.0, 0.0) for i in range(40)]  # along the block's south edge
    lawn = [[hx - 6000, -2400], [hx + 6000, -2400], [hx + 6000, 4000], [hx - 6000, 4000]]
    in_sq = lambda res: [p for p in res["Mansions"]["points"] if inside(lawn, p[0], p[1])]
    no_lots = [{k: v for k, v in cities[0].items() if k != "lots"}]
    plain = preview([front], no_lots, {}, street, lambda x, y: 0.0)
    parked = preview([front], [{**no_lots[0], "blocks": [[hx, 0, "park", "housing", lawn]]}], {}, street, lambda x, y: 0.0)
    assert in_sq(plain) and not in_sq(parked), ("nothing built on a park block", len(in_sq(plain)), len(in_sq(parked)))
    assert len(parked["Mansions"]["points"]) >= len(plain["Mansions"]["points"]) - len(in_sq(plain)) - 1
    shop = clean({"name": "S", "kind": "buildings", "role": "shop"})
    assert _lot_ground(shop, lambda x, y: 0.0, 0, 0, 0.0, 20, 15)[2][0][0] == 18.0, "a shop fronts the street full width"
    # a house is not built on a foundation deeper than 6 m: it goes and its lot is planted; a park and a planned
    # site stay; a tower stands on a podium down to 14 m
    deep = {"Houses": {"kind": "buildings", "role": "house", "count": 3, "shown": 3, "points": [
                [0, 0, 0, 0, 1, 7, 0, 16, 24, 6.5, [], []], [5000, 0, 0, 0, 1, 7, 0, 12, 12, 6.0, [], []],
                [9000, 0, 0, 0, 1, 7, 0, 12, 12, 9.0, [], [], "hospital"]]},
            "Towers": {"kind": "buildings", "role": "skyscraper", "count": 2, "shown": 2, "points": [
                [20000, 0, 0, 0, 1, 90, 0, 32, 32, 13.5, [], []], [30000, 0, 0, 0, 1, 90, 0, 32, 32, 14.5, [], []]]},
            "Parks": {"kind": "buildings", "role": "park", "count": 1, "shown": 1, "points": [[0, 9000, 0, 0, 1, 0, 0, 40, 40, 12.0, [], []]]}}
    out_lots, sites, lost = leave_out_deep(deep)
    assert out_lots == [(0, 0, 0, 16, 24), (30000, 0, 0, 32, 32)] and sites == 1 and lost == {"Houses": 1, "Towers": 1}, (out_lots, lost)
    assert deep["Houses"]["count"] == 2 and [p[0] for p in deep["Towers"]["points"]] == [20000], "the tower on a 13.5 m podium stands"
    out_lots = out_lots[:1]
    assert len(deep["Parks"]["points"]) == 1
    assert plant_lots(deep, out_lots, lambda x, y: 50.0) == 6 and deep["Infill trees"]["count"] == 6
    assert all(abs(t[0]) <= 1350 and abs(t[1]) <= 950 and t[2] == 50 for t in deep["Infill trees"]["points"])
    assert plant_lots(deep, out_lots, lambda x, y: -50.0, sea=0.0) == 0, "no tree in the water"
    (drive,) = paths
    assert drive[0] == DRIVE_M and len(drive[1]) == 6 and drive[1][2] == round(pad) and drive[1][-1] < pad - 100, paths
    # a lot never reaches onto a street
    rh = _Hash()
    rh.add(0.0, 1500.0, 875.0, (0.0, 1500.0, 875.0))
    assert _lot_hits_road(rh, 0, 0, 90.0, 600, 800) and not _lot_hits_road(rh, 0, -1500, 90.0, 600, 800)
    from . import citygen as cg
    ter = {"min": [-2000, -2000], "max": [2000, 2000], "grid": 40,
           "heights": [[-0.3 * (-2000 + 4000 * (i + 0.5) / 40) for i in range(40)] for _ in range(40)]}
    g = cg.graded(ter, pads({"H": {"kind": "buildings", "points": [[0, 0, 0, 0, 1, 8, 0, 12, 12, 0, []]]}}))
    assert abs(g(300, 300)) < 1 and abs(g(-300, 0)) < 1, "the lot is level"
    assert abs(g(1500, 0) - (-450)) < 30, "the hill away from the lot is untouched"
    bad = clean({"name": "x", "kind": "nope", "density": -5, "scale_min": 3, "scale_max": 1})
    assert bad["kind"] == "scatter" and bad["density"] == 0.01 and bad["scale_min"] == 1 and bad["scale_max"] == 3
    # an area-limited layer places only inside its outline
    boxed = clean({"name": "Box trees", "kind": "scatter", "density": 5, "area": [[0, 0], [5000, 0], [5000, 5000], [0, 5000]]})
    bp = preview([boxed], [], {"min": [-20000, -20000], "max": [20000, 20000]}, [], lambda x, y: 0.0)["Box trees"]["points"]
    assert bp and all(0 <= p[0] <= 5000 and 0 <= p[1] <= 5000 for p in bp), len(bp)
    # parcels: houses fill the lots cut from a real grid city's blocks, one per lot, facing their street
    from . import citygen as cg
    town = {"name": "L", "x": 0, "y": 0, "radius": 40000, "seed": 4, "style": "grid"}
    town["districts"] = cg.districts(town)
    town["lots"] = cg.city_lots(town, {"planner": "no"})  # every lot plain: this checks the house fill
    assert len(town["lots"]) > 120, len(town["lots"])  # nine blocks; which neighbourhood each gets moves the count
    houses = clean({"name": "Houses", "kind": "buildings", "role": "house", "where": "cities", "width_m": 12,
                    "depth_m": 12, "setback_m": 6, "spacing_m": 20})
    hv = preview([houses], [town], {"min": [-50000, -50000], "max": [50000, 50000]}, [], lambda x, y: 0.0)["Houses"]
    assert hv["count"] > 120, hv["count"]  # (a house that would stand on its neighbour or a street is left out)
    by_front = {}
    for lot_row in town["lots"]:
        lx, ly, lyaw, lw, ld, frx, fry = lot_row[:7]
        by_front[(round(lx), round(ly))] = (lyaw, frx, fry)
    for row in hv["points"]:
        f = math.radians(row[3])  # the house's front faces +X: one setback + half its depth behind a lot's front
        front = (row[0] + math.cos(f) * (600 + row[8] * 50), row[1] + math.sin(f) * (600 + row[8] * 50))
        assert any(math.dist(front, (frx, fry)) < 30 and abs((row[3] - lyaw + 180) % 360 - 180) < 1
                   for lyaw, frx, fry in by_front.values()), row[:4]
    # no lot is left with nothing. Downtown lots are cut for what is built downtown and the big buildings take
    # them (they only knew road frontage: half the downtown lots stood empty between the towers); a lot next to
    # a big building is still built on; and a lot nothing fits is planted.
    core_town = {"name": "D", "x": 0, "y": 0, "radius": 60000, "seed": 4, "style": "grid"}
    core_town["districts"] = cg.districts(core_town)
    lay = cg.city_layout(core_town, {"planner": "no", "freeways": "no"})
    core_town.update(lots=lay["lots"], blocks=lay["blocks"])
    in_core = _where_test("downtown", [core_town])
    core_lots = [r for r in core_town["lots"] if in_core(r[0], r[1])]
    wide = [r for r in core_lots if r[3] >= 2200 and r[4] >= 2400]
    assert len(wide) > 30 and len(wide) > 0.6 * len(core_lots), (len(wide), len(core_lots))
    office = clean({"name": "Office blocks", "kind": "buildings", "role": "office", "where": "downtown", "width_m": 26,
                    "depth_m": 24, "setback_m": 5, "spacing_m": 34, "height_min_m": 30, "height_max_m": 75, "order": 1})
    homes = clean({"name": "Houses", "kind": "buildings", "role": "house", "where": "cities", "width_m": 11,
                   "depth_m": 14, "setback_m": 10, "spacing_m": 17, "order": 2})
    fill = preview([office, homes], [core_town], {"min": [-80000, -80000], "max": [80000, 80000]}, [], lambda x, y: 0.0)

    def stands(r, rows):  # is one of these on this lot?
        fx_, fy_ = math.cos(math.radians(r[2])), math.sin(math.radians(r[2]))
        return any(abs((p[0] - r[0]) * fx_ + (p[1] - r[1]) * fy_) <= r[4] / 2 + 1 and abs(-(p[0] - r[0]) * fy_ + (p[1] - r[1]) * fx_) <= r[3] / 2 + 1
                   for p in rows if abs(p[0] - r[0]) < 6000 and abs(p[1] - r[1]) < 6000)
    towers = fill["Office blocks"]["points"]
    assert sum(1 for r in wide if stands(r, towers)) > 0.85 * len(wide), (sum(1 for r in wide if stands(r, towers)), len(wide))
    assert all(in_core(p[0], p[1]) for p in towers), "office blocks stay downtown"
    all_of_it = towers + fill["Houses"]["points"] + (fill.get("Infill trees") or {"points": []})["points"]
    bare = [r[:5] for r in core_town["lots"] if not r[8] and not stands(r, all_of_it)]
    assert not bare, ("a lot with nothing on it", len(bare), bare[:3])
    assert len(fill["Houses"]["points"]) > 300 and fill.get("Infill trees"), "homes outside downtown; trees where nothing fits"
    # ... but what the planner sites (a mall, a school) never takes a free lot: with none planned it stays a rare
    # building along a road (18 malls stood on downtown lots)
    mall = clean({"name": "Malls", "kind": "buildings", "role": "mall", "where": "downtown", "width_m": 30, "depth_m": 20,
                  "setback_m": 5, "spacing_m": 100, "order": 1})
    box = {"min": [-80000, -80000], "max": [80000, 80000]}
    assert not preview([mall], [core_town], box, [], lambda x, y: 0.0)["Malls"]["points"]
    # ... and where the planner ran and sited none, there is none: not along the roads either
    ring_road = [(x, 9000.0, 300.0, 1.0, 0.0) for x in range(-20000, 20001, 500)]
    assert preview([mall], [core_town], box, ring_road, lambda x, y: 0.0)["Malls"]["points"], "(no planner: it takes road frontage)"
    assert not preview([mall], [{**core_town, "plan": {"facilities": []}}], box, ring_road, lambda x, y: 0.0)["Malls"]["points"]
    # a dropped kerb at the street end of a house's drive, half the sidewalk past the lot line
    ap = aprons({"H": {"kind": "buildings", "points": [[0, 0, 0, 0, 1, 8, 0, 12, 12, 0, [[DRIVE_M, [600, 0, 0, 1600, 0, 0]]]]]}})
    assert ap["Driveway aprons"]["points"] == [[1775, 0, 0, 0.0, 3.5, DRIVE_M + 1.0, 1.0]], ap
    # two houses end on, 4 m apart at their centres' line: the old circle test passed them, the rectangles do not
    east = (0.0, 0.0, 1.0, 0.0, 600.0, 900.0)
    assert _rects_hit(east, (1700.0, 0.0, 1.0, 0.0, 600.0, 900.0)) and not _rects_hit(east, (1900.0, 0.0, 1.0, 0.0, 600.0, 900.0))
    assert not _rects_hit(east, (0.0, 1300.0, 1.0, 0.0, 600.0, 900.0)) and _rects_hit(east, (800.0, 1000.0, 0.7071, 0.7071, 600.0, 900.0))
    # fences: one behind a house stays; one through the house, one with no house, one across a street and one
    # crossing a kept fence all go
    fl = {"H": {"kind": "buildings", "points": [[0, 0, 0, 0, 1, 8, 0, 12, 12, 0, []]]},
          "Yard fences": {"kind": "site", "points": [[-1200, 0, 0, 90, 16, 1, 0.6], [0, 0, 0, 90, 16, 1, 0.6], [90000, 0, 0, 90, 16, 1, 0.6],
                                                     [1500, 0, 0, 0, 16, 1, 0.6], [-1200, 0, 0, 0, 10, 1, 0.6]]}}
    assert fit_fences(fl, [(1500.0, 0.0, 400.0, 0.0, 1.0)]) == 4 and fl["Yard fences"]["points"] == [[-1200, 0, 0, 90, 16, 1, 0.6]]
    # trees fill an interchange (four ramp ends round a point), none for a lone ramp end
    ramp_end = lambda x, y: {"x": x, "y": y, "mesh": "SM_Road_RampFlare15m"}
    grove = interchange_groves([([ramp_end(0, 0), ramp_end(9000, 0), ramp_end(0, 9000), ramp_end(9000, 9000), ramp_end(90000, 0)], [])],
                               lambda x, y: 0.0)["Interchange trees"]
    assert grove["count"] == 90 and all(math.dist(p[:2], (4500, 4500)) <= 16000 for p in grove["points"]), grove["count"]
    # a private street stops each side of a ramp it would cross (ramp half width 500 at x 5000)
    cut = off_roads([[[0, 0], [10000, 0]]], [(5000, 0, 500, 0, 1)])
    assert len(cut) == 2 and cut[0][-1][0] <= 3100 and cut[1][0][0] >= 6900 and cut[1][-1] == [10000, 0], cut
    assert off_roads([[[0, 9000], [10000, 9000]]], [(5000, 0, 500, 0, 1)]) == [[[0, 9000], [10000, 9000]]]
    # nothing stands on a freeway or a ramp: a tower on it goes, its neighbour 60 m off stays, bridge parts stay
    on = {"T": {"kind": "buildings", "role": "office", "count": 2, "points": [[0, 500, 0, 0, 1, 40, 0, 30.0, 30.0], [0, 6000, 0, 0, 1, 40, 0, 30.0, 30.0]]},
          "Bridge piers": {"kind": "site", "role": "bridge", "box_m": [2, 2, 1], "points": [[0, 0, 0, 0, 1, 1, 5]]}}
    assert clear_of(on, [(x, 0.0, 650.0, 1.0, 0.0) for x in range(-2000, 2001, 400)]) == 1
    assert [p[1] for p in on["T"]["points"]] == [6000] and on["T"]["count"] == 1 and len(on["Bridge piers"]["points"]) == 1
    print("pcg layers ok", len(full), "layers,", len(out["nodes"]), "nodes", len(out["edges"]), "wires,",
          {n: v["count"] for n, v in pv.items() if v["count"]})
if __name__ == "__main__":
    _demo()

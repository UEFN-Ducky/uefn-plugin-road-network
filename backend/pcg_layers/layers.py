"""What a layer is: kinds, roles, building forms, number ranges, the built-in layers and presets, clean()."""

from __future__ import annotations

import functools
import math
import re

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
    "house": ("Houses", "BuildingKit House parts (walls, corners, open doorways) 1-3 storeys, box or L",
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
    "max_slope_deg": (20.0, 1.0, 90.0),  # scatter / foliage: nothing grows on ground steeper than this (banks, cliffs)
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


# the World panel's Vegetation sliders (generate options): tree_density / bush_density scale every tree / bush layer's
# density, forest_cover sets their cover, tree_shore_m and groves go to land.biome_trees
VEG_KEYS = ("tree_density", "bush_density", "forest_cover", "tree_shore_m", "groves")


def with_veg(layers: list, veg: dict | None) -> list:
    """Copies of the layers with the Vegetation sliders applied to the foliage / scatter layers of trees and bushes
    (a park's or a street's own trees keep their layer's settings: they are not 'outside')."""
    if not veg:
        return layers
    out = []
    for layer in layers:
        role = layer.get("role")
        if layer.get("kind") in ("foliage", "scatter") and role in ("tree", "bush") and layer.get("where", "outside") == "outside":
            layer = dict(layer)
            mul = float(veg.get("tree_density" if role == "tree" else "bush_density", 1.0))
            if "density" in layer:
                layer["density"] = layer["density"] * mul
            if "forest_cover" in veg:
                layer["cover"] = float(veg["forest_cover"]) + (0.05 if role == "bush" else 0.0)
            if mul <= 0:
                layer["enabled"] = False
        out.append(layer)
    return out


def patch_rank(x: float, y: float, cell: float = PATCH_CM) -> float:
    """patch() as a share 0..1 of the map below it (its values bunch round 0.5, sd 0.144): `patch_rank < 0.3` is 30 %
    of the land. Compared raw, a 0.3 share kept 3 % (a meadow's groves were gone) and a 0.55 forest cover was 63 %."""
    return 0.5 * (1.0 + math.erf((patch(x, y, cell) - 0.5) / (0.1441 * math.sqrt(2.0))))


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


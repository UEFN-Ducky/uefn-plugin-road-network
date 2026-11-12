"""Generate a world offline: a terrain heightmap, several cities (each with its own districts and
road style) and highways joining them, as road actors in the same point/segment form the
spline text parser makes. Nothing here talks to UEFN.

Units are UE centimetres. A city is {name, x, y, radius, style, seed}; style is
"mixed" (straight downtown grid, wobbly suburbs), "grid" or "organic". Every knob lives in
OPTIONS (metres and shares, as the panel shows them); options() fills in the defaults.
"""

from __future__ import annotations

import math
import random
import struct
import zlib

from . import splines_text as st

# Every road type in our kit (WorldGen/Roads, from NeonSkyscrapers.blend RoadKit; Elevated/ from the stack
# interchange). code: (mesh under the roads folder, half width cm, lanes, sidewalk, what it is)
ROAD_TYPES = {
    "1W": ("SM_Road_OneLane_Straight10m", 300.0, 1, False, "one-way lane"),
    "DT": ("SM_Road_Dirt_Straight10m", 375.0, 2, False, "dirt road"),
    "2L": ("SM_Road_TwoLane_Straight10m", 525.0, 2, False, "two-way street, no sidewalk"),
    "2S": ("SM_Road_TwoLaneSidewalk_Straight10m", 875.0, 2, True, "two-way street with sidewalks"),
    "4L": ("SM_Road_FourLane_Straight10m", 1400.0, 4, True, "four-lane avenue with sidewalks"),
    "6L": ("SM_Road_Freeway_Lane20m", 1650.0, 6, False, "six-lane freeway (no sidewalks: never a city street)"),
    # the city versions of the lane and the boulevard: the same decks with a kerb and a 3.5 m sidewalk each side,
    # in the one mesh (model them from the piece manifest; a city never uses a street without sidewalks)
    "1S": ("SM_Road_OneLaneSidewalk_Straight10m", 650.0, 1, True, "one-way street with sidewalks"),
    "6S": ("SM_Road_BoulevardSidewalk_Straight20m", 2000.0, 6, True, "six-lane boulevard with sidewalks"),
    "BR": ("SM_Road_Bridge_Straight20m", 925.0, 2, False, "bridge deck"),
    "BRR": ("SM_Road_Bridge_Ramp40m", 925.0, 2, False, "bridge ramp"),
    # a boulevard's bridge: the wide deck with its 3.5 m sidewalks and a barrier on each edge, so the street keeps
    # its width and its pavements across the water (the plain deck dropped them, stepping 7 m at each end)
    "BW": ("SM_Road_BridgeSidewalk_Straight20m", 2000.0, 6, True, "boulevard bridge deck with sidewalks"),
    "ED": ("Elevated/SM_Elev_Deck20m", 1650.0, 4, False, "elevated deck"),
    "ER": ("Elevated/SM_Elev_Ramp20m", 650.0, 1, False, "elevated ramp"),
    "FR": ("SM_Road_TwoLane_Straight10m", 525.0, 2, False, "frontage road beside a freeway"),
    # a canal is a spline too (the '<City> Canals' actor): quay walls, a 3 m channel and its water, laid at the
    # channel bed so the landscape spline lowers the ground into it; cross streets bridge it (canal arches)
    "CN": ("SM_Water_Canal_10m", 1200.0, 0, False, "canal: quay walls, 3 m channel, water 1.5 m below the banks"),
    # a shipyard's graving dry dock (the '<City> Docks' actor): floor + 12 m walls, laid at the dock floor so the
    # landscape spline digs the basin into the shore (shipyard.py)
    "DK": ("SM_Water_DryDock_10m", 2000.0, 0, False, "graving dry dock: 40 m basin, 12 m deep, walls and walkways"),
    # airfields (the '<City> Airport' actor, airport.py): graded flat by the landscape spline, junction pieces
    # where taxiways meet runways
    "RW": ("SM_Air_Runway_20m", 2250.0, 0, False, "runway: 45 m with shoulders, centre line, edge stripes"),
    "RS": ("SM_Air_RunwaySmall_20m", 900.0, 0, False, "small runway (general aviation): 18 m"),
    "TW": ("SM_Air_Taxiway_10m", 1150.0, 0, False, "taxiway: 23 m with shoulders, yellow centre line"),
    # pedestrian and access splines (the '<City> Paths' actor): live like roads, so they bend, keep a grade
    # and cut / fill the ground. City Builder builds these three meshes itself (street kit).
    "SW": ("SM_Path_Sidewalk_10m", 190.0, 0, True, "sidewalk (1.8 m clear path + frontage + furniture strip)"),
    "WK": ("SM_Path_Walkway_10m", 80.0, 0, True, "walkway from a door to the sidewalk"),
    "DW": ("SM_Path_Driveway_10m", 175.0, 0, False, "driveway from a garage to the street"),
    # the private access network (the '<City> Access' actor): subdivision / complex streets and double drives
    "PR": ("SM_Path_PrivateStreet_10m", 400.0, 2, False, "private street (subdivision, apartment complex)"),
    "DD": ("SM_Path_DrivewayDouble_10m", 275.0, 0, False, "double driveway (two-car garage)"),
}
NO_SIDEWALK = ("6L", "1W", "2L")  # city roads whose mesh has no sidewalk: the sidewalk generator adds them
# (dirt roads stay bare); with these every city street has a pavement from kerb to lot line, never grass
ROAD_MESH = {k: v[0] for k, v in ROAD_TYPES.items()}
HALF = {k: v[1] for k, v in ROAD_TYPES.items()}
# A street's sidewalks are part of its road mesh: this much of each side's half width (cm) is kerb + sidewalk.
# A junction is built with the same sidewalks round its corners, in its one mesh (road_io._junction_geometry).
WALK = {"2S": 350.0, "4L": 350.0, "1S": 350.0, "6S": 350.0, "BW": 350.0}
# inside a city every street has sidewalks: a district set to a bare road type gets its sidewalk version
CITY_STREET = {"2L": "2S", "1W": "1S", "6L": "6S"}


def walk_of(half: float) -> float:
    """The sidewalk width inside a road of this half width (0 = a road with none)."""
    return next((w for k, w in WALK.items() if abs(HALF[k] - half) < 1), 0.0)


def refine(ter: dict, grid: int) -> dict:
    """The same terrain on a finer grid (bilinear), so roads can be cut into it a landscape pixel at a time."""
    s = st.grid_sampler(ter)
    (x0, y0), (x1, y1) = ter["min"], ter["max"]
    cw, ch = (x1 - x0) / grid, (y1 - y0) / grid
    return {**ter, "grid": grid, "heights": [[round(s(x0 + (i + 0.5) * cw, y0 + (j + 0.5) * ch) or 0.0, 1)
                                              for i in range(grid)] for j in range(grid)]}
LOOP_PIECE = "SM_Road_Loop_Big"  # trumpet loop (point piece)
CLOVER_PIECE = "SM_Road_Freeway_Cloverleaf"  # in NeonSkyscrapers RoadKit: import it before using cloverleaf
# per-district road choices (the panel's Roads tab)
CHOICES = {
    "core_roads": (("4L", "6L", "2S"), "4L"),
    "neighbourhood_roads": (("2S", "2L", "4L"), "2S"),
    "suburb_roads": (("2L", "2S", "1W", "DT"), "2S"),  # the default street carries its sidewalks in its own mesh
    "highway": (("4L", "6L", "2L"), "4L"),
    "interchange": (("direct", "diamond", "trumpet", "cloverleaf", "stack"), "direct"),
    "one_way_core": (("no", "yes"), "no"),  # core side streets as alternating one-way lanes
    "overpasses": (("yes", "no"), "yes"),  # highways bridge over the streets they cross
    # outside the core every other local street line is left out: ~200 m super-blocks that subdivisions and
    # apartment complexes fill with their own private streets (neighbourhoods.py)
    "superblocks": (("yes", "no"), "yes"),
    # urban freeways through each big city, planned before its blocks (freeways.py): viaducts over the grid,
    # service interchanges on the main roads, a stack where two freeways cross
    "freeways": (("yes", "no"), "yes"),
    # demand-driven urban planner (urban.py): schools, emergency services, retail, parks, utilities and transit
    # placed from population, road travel time and coverage, as complete developments
    "planner": (("yes", "no"), "yes"),
    # waterfront.py: shorelines analysed and developed (quays, marinas, beaches, ports, stilt / floating homes)
    "waterfront": (("yes", "no"), "yes"),
    # a canal city: every third street line in the inner city is a canal with walkways, its cross streets bridge it
    "canals": (("none", "canal_city"), "none"),
    # shipyard.py: commercial / naval / offshore / repair / yacht yards, fishing industry and ship recycling on
    # suitable deep industrial shore, as dedicated districts with dry docks, halls and cranes
    "shipyards": (("auto", "none"), "auto"),
    # airport.py: an airport per city by population (general aviation / regional / international), planned before
    # its streets; a military airbase on request
    "airports": (("auto", "none"), "auto"),
    "military_base": (("no", "yes"), "no"),
    # the ground under a city: 'natural' leaves the hills as they are (streets drape, lots get their own pads,
    # water is bridged), 'flatten' presses a flat plateau under every city
    "city_ground": (("natural", "flatten"), "natural"),
}
# steepest grade per road (rise / run): arterials and highways 4 %, collectors 5-6 %, local streets 8 %,
# dirt 10 % (CityEngine-style targets for a game, not legal limits). Steeper ground is cut and filled.
GRADE = {"6L": 0.04, "6S": 0.04, "1S": 0.08, "4L": 0.05, "2S": 0.06, "2L": 0.08, "1W": 0.08, "DT": 0.10,
         "SW": 0.08, "WK": 0.08, "DW": 0.15, "PR": 0.10, "DD": 0.15, "FR": 0.08,
         "CN": 0.01, "DK": 0.01,  # canals and dry docks: nearly level (locks are not modelled)
         "RW": 0.01, "RS": 0.015, "TW": 0.015}  # runways and taxiways: FAA-like longitudinal grades
MAX_CUT_CM = 1200.0  # deepest a road is cut into / built up on the ground; past that it follows the hill more steeply
CANAL_DEPTH_CM = 300.0  # walkways at ramp grade (8.3 %), driveways up to 15 %
BRIDGE_LIFT_CM = 750.0  # clearance a highway rises to over a street it crosses (a real freeway overpass: 7.5 m)

# How City Builder designs a world. Code follows these; an AI building with the plugin follows them too
# (road_network_generate_options returns them), so the concept comes from the plugin, never from a chat.
RULES = [
    "Highways only join two cities (shortest tree over the cities); one city on its own gets no highway.",
    "An interchange (cloverleaf / trumpet / diamond / stack) is only built where a highway that links two cities "
    "meets one of them, 180 m out from the city's gate, and only when the highway is longer than 900 m. Never "
    "stand-alone: its cross road is a city street joined at both ends to the city's nearest streets.",
    "A highway bridges over every street it crosses: ramp up 40 m, deck 7.5 m above the street, ramp down 40 m.",
    "Bridges, decks and ramps never shape the ground; their height is taken over the highest ground within 15 m, "
    "so nothing sits inside a hill. Ground-level roads shape the terrain (live splines).",
    "Interchange loops are tangent to the highway and the cross road and come down from deck height to the cross road.",
    "Keep zones (the cinema, spawn islands) are never built on: flat at their height, streets stop short, highways go round.",
    "Every building lot is levelled (pad) with a plinth where the hill falls away, a walkway to the kerb and, for "
    "houses and businesses, a driveway.",
    "Forests grow in dense patches with meadows between, in a belt around each city; parks are dense groves.",
    "PCG is tiled (400 m) and incremental: a change rebuilds only the tiles it touches.",
    "Urban freeways are planned before a city's blocks (from 600 m radius one north-south, from 2 km a second "
    "east-west one and a directional stack): mid-block on the city's grid, a viaduct 7.5 m (one level) over the "
    "streets, which keep running under it; the upper freeway at a stack is level 2, its flyovers levels 3-4.",
    "Service interchanges only on main roads (collectors, the axes), 800 m+ apart and clear of the stack: a tight "
    "diamond downtown, a partial cloverleaf in the normal city, a diamond with frontage roads in the suburbs.",
    "Ramps are Bezier curves checked for curvature: loops >= 45 m radius, ramps >= 150 m, directional >= 120 m; "
    "ramps <= 6 % grade, freeways <= 4 %; any two roads crossing need 7 m between them (5.5 m clearance + deck).",
    "A street that a low ramp or an at-grade freeway would cut is removed; no building lot inside a freeway's right "
    "of way (15 m past its edge). The validator (road_network_generate's freeways report) must come back clean.",
    "Roads know the water: a highway or freeway crosses it on a bridge (deck 7.5 m up, ramps on land), main roads "
    "(2S / 4L / 6L) bridge up to 400 m, any paved street bridges a creek up to 60 m; a side street that would run "
    "into a lake stops at the shore. No street node, lot, parking or private street is ever in the water.",
    "City size (city_size) scales every city; the distance between cities (city_gap_m) is open country between "
    "their edges, and cities closer than that are pushed apart. One city is resized with road_network_city_save.",
    "Facilities are never placed at random (urban.py): population comes from the lots, travel is measured along "
    "the roads, and each school, station, store, park, utility and transit site is placed for demand, coverage "
    "or terrain, as a complete development (building, parking, aprons, fields, playground, yards) on whole lots "
    "of one block. Civic PCG layers build only on those planned sites.",
    "Water is planned like roads (waterfront.py): the shoreline is analysed (normals, curvature, shelter, depth, "
    "width, slope, flood) and each 100 m gets one use. Seawalls only on commercial / industrial quays; beaches, "
    "wetlands and natural shores stay natural. Marinas are compact and shared (not a dock per house), on "
    "sheltered deep water, piers never reach past a third of the water's width.",
    "Shipyards are dedicated industrial districts planned before the streets (shipyard.py): the type comes from "
    "the water, the stretch and the city size; the campus runs inland -> water as steel storage, fabrication, "
    "assembly, docks / slipways, outfitting quay; administration at the back; open hardstand stays open; no city "
    "street or lot inside; dry docks are DK splines that dig their basin; the launch basin stays clear.",
    "Airports are planned before the streets (airport.py): type from the city's size, the site flat, dry and "
    "outside every city, runways turned into the prevailing wind; stands before the terminal; one connected "
    "taxiway graph; nothing tall near a runway or in its approach; no homes in the noise zone (industry instead); "
    "cargo trucks on their own road, never on the passenger curb.",
    "Bridges are typed by the water they span: canal arches (3 m clearance), beam (7.5 m), navigable girder "
    "(15 m), cable-stayed (30 m) and suspension (45 m) with pylons; ramps keep the road's grade.",
    "One editor call does little (one mesh build, a few junctions, one tile): UEFN frees windows only per frame.",
    "Every door a player can walk through opens: house, garage, shop, office, revolving, grand, industrial and roller "
    "doors are Scene Graph entities, never fixed meshes. One Entity Prefab per door kind (the door leaf + a component "
    "that opens it on interact); the door's wall module is modelled as an open doorway (frame only). PCG places the "
    "doors: they go where the building grammar put each door module, with its exact transform. If the PCG spawn step "
    "can't take an Entity Prefab in this UEFN version, PCG still decides where: place the prefab instances at that "
    "step's points (its positions and rotations) with the Scene Graph tools, never at hand-picked spots, and say so.",
    "Everything put into UEFN has collision: buildings, walls, floors, stairs, roofs, road and bridge pieces, props, "
    "rocks and tree trunks all block players; only grass, leaves, bushes and flat paint (lines, parking stalls) have "
    "none. Simple collision (boxes, convex hulls, UCX from Blender) by default; complex as simple only where boxes "
    "would be wrong (stairs, ramps, arches, doorways, curved decks): it costs more. A site piece (a park, a parking "
    "lot) collides by its parts (trees, benches, kerbs, lamps), never one box over the whole site, which would be an "
    "invisible wall. Set it on each mesh before the PCG build, tell the person which pieces got simple, complex as "
    "simple or none, and check in a playtest that nothing can be walked through.",
]
STYLES = ("mixed", "grid", "organic")
JUNCTION = "SM_Junc_Gen"  # waits for Build pieces (st.pending_nodes picks it up)
MOUTH_CM = 1500.0
CORNER_DEG = 30.0  # a 2-street node turning more than this gets a square elbow piece, not a spline bend
NAMES = ["Northgate", "Riverton", "Ashford", "Kingsport", "Millbrook", "Eastvale", "Stonebridge", "Westmoor",
         "Harbor Point", "Lakeside", "Redcliff", "Oakhurst"]

# name: (default, min, max)
OPTIONS = {
    # terrain
    "hill_km": (2.0, 0.3, 10.0),       # size of the big hills
    "roughness": (0.5, 0.2, 0.8),      # how much the small bumps add
    "detail": (5, 1, 8),               # noise octaves
    "flatten_m": (300.0, 0.0, 2000.0), # blend from a city's plateau back to the hills
    "sea_m": (-1000.0, -1000.0, 200.0),  # lowlands below this become flat lake beds (-1000 = off)
    "terrace_m": (0.0, 0.0, 50.0),     # step the hills into terraces (0 = smooth)
    "ridges": (0.0, 0.0, 1.0),         # 0 rolling hills .. 1 sharp mountain ridges
    "peaks": (0.0, 0.0, 1.0),          # 0 even hills .. 1 wide low valleys with tall sharp peaks
    "water_depth_m": (12.0, 0.0, 80.0),  # how deep lake beds drop below the water line
    # cities
    "city_size": (1.0, 0.3, 4.0),     # every city's radius x this (new cities; regenerating scales the old ones)
    "city_gap_m": (300.0, 0.0, 6000.0),  # open country between two cities' edges, at least
    # roads
    "block_m": (100.0, 50.0, 250.0),   # city block, centre line to centre line
    # planned grid (Parish & Mueller hierarchy; real cities ~80 % planned): an intersection moves at most 8 %
    # of a block, the whole intersection at once, so streets stay straight and blocks stay rectangular
    "wobble": (0.04, 0.0, 0.08),       # how far an intersection may move (share of a block)
    "prune": (0.12, 0.0, 0.35),        # share of local side streets removed in neighbourhoods / suburbs
    "collector_every": (4, 2, 8),      # every Nth grid line is a collector (wider, through the city)
    "downtown": (0.4, 0.1, 0.8),       # downtown ring, share of the radius
    "normal": (0.75, 0.2, 0.95),       # normal-city ring; housing beyond it
    "highway_curve": (0.08, 0.0, 0.3), # how far a highway bows out, share of its length
    "wind_deg": (240.0, 0.0, 360.0),   # prevailing wind (degrees, map yaw): runways are turned into it
}
HIGHWAYS = CHOICES["highway"][0]


# what one city may have or not, over the world's choice (city["features"], the Cities list's remove / + Add)
FEATURES = ("airports", "shipyards", "freeways", "waterfront", "canals", "planner")


def city_options(city: dict, opts: dict | None = None) -> dict:
    """The world's options with this city's own feature choices on top."""
    return options({**(opts or {}), **{k: v for k, v in (city.get("features") or {}).items() if k in FEATURES}})


def options(given: dict | None = None) -> dict:
    """Defaults filled in, every number clamped to its range; highway is '4L' or '2L'."""
    given = given or {}
    out = {}
    for key, (default, lo, hi) in OPTIONS.items():
        try:
            v = float(given.get(key, default))
        except (TypeError, ValueError):
            v = default
        v = max(lo, min(hi, v))
        out[key] = int(round(v)) if isinstance(default, int) else v
    for key, (allowed, default) in CHOICES.items():
        out[key] = given.get(key) if given.get(key) in allowed else default
    return out


# ------------------------------------------------------------------ terrain
def _fbm(seed: int, octaves: int = 5, roughness: float = 0.5, ridges: float = 0.0):
    rnd = random.Random(seed)
    table = [rnd.random() for _ in range(256 * 256)]

    def val(ix, iy):
        return table[(ix & 255) * 256 + (iy & 255)]

    def smooth(x, y):
        ix, iy = math.floor(x), math.floor(y)
        fx, fy = x - ix, y - iy
        fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
        a, b = val(ix, iy), val(ix + 1, iy)
        c, d = val(ix, iy + 1), val(ix + 1, iy + 1)
        return (a + (b - a) * fx) * (1 - fy) + (c + (d - c) * fx) * fy

    def fbm(x, y):
        total, amp, norm = 0.0, 1.0, 0.0
        for o in range(octaves):
            n = smooth(x * 2 ** o + o * 17.3, y * 2 ** o - o * 9.1)
            if ridges:  # fold the noise into sharp crests
                n = n * (1 - ridges) + (1 - abs(2 * n - 1)) * ridges
            total += amp * n
            norm += amp
            amp *= roughness
        return total / norm  # 0..1
    return fbm


def _smoothstep(e0: float, e1: float, x: float) -> float:
    if e1 <= e0:
        return 0.0 if x < e0 else 1.0
    t = max(0.0, min(1.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


# ------------------------------------------------------------------ keep-out zones
# Something already built that the world must leave alone (a cinema, a spawn island): a rectangle
# {x0, y0, x1, y1, z} in cm. The ground stays flat at z there and blends back into the hills; no
# city, highway or PCG lot goes inside.
def keep_zones(raw) -> list:
    out = []
    for k in raw or []:
        try:
            x0, x1 = sorted((float(k["x0"]), float(k["x1"])))
            y0, y1 = sorted((float(k["y0"]), float(k["y1"])))
            out.append({"name": str(k.get("name") or "Keep"), "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                        "z": float(k.get("z", 0.0))})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def keep_gap(k: dict, x: float, y: float) -> float:
    """How far (cm) a point is outside a keep zone; 0 inside."""
    return math.hypot(max(k["x0"] - x, 0, x - k["x1"]), max(k["y0"] - y, 0, y - k["y1"]))


def in_keep(keep: list, x: float, y: float, margin: float = 0.0) -> bool:
    return any(keep_gap(k, x, y) <= margin for k in keep or [])


def push_out(keep: list, x: float, y: float, margin: float) -> list:
    """A point moved to the nearest spot at least `margin` outside every keep zone."""
    for k in keep or []:
        if keep_gap(k, x, y) < margin:
            cands = [(k["x0"] - margin, y), (k["x1"] + margin, y), (x, k["y0"] - margin), (x, k["y1"] + margin)]
            x, y = min(cands, key=lambda p: math.hypot(p[0] - x, p[1] - y))
    return [x, y]


def terrain(seed: int, size_cm: float, relief_cm: float, cities: list, grid: int = 160,
            center: tuple = (0.0, 0.0), opts: dict | None = None, keep: list | None = None) -> dict:
    """Hills (natural ground; option city_ground 'flatten' presses a plateau under every city). Same shape as a ground scan:
    {min, max, grid, heights[j][i]} with cell centres, so the 3D view and drape read it as is."""
    o = options(opts)
    fbm = _fbm(seed, o["detail"], o["roughness"], o["ridges"])
    half = size_cm / 2
    lo, hi = [center[0] - half, center[1] - half], [center[0] + half, center[1] + half]
    feature = o["hill_km"] * 100000.0
    sea = o["sea_m"] * 100 if o["sea_m"] > OPTIONS["sea_m"][1] else None
    step = o["terrace_m"] * 100

    xs = [lo[0] + size_cm * (i + 0.5) / grid for i in range(grid)]
    ys = [lo[1] + size_cm * (j + 0.5) / grid for j in range(grid)]
    noise = [[fbm(x / feature, y / feature) for x in xs] for y in ys]
    # Layered noise averages out (5 octaves span about 0.3..0.7, not 0..1), so "60 m hills" came out
    # ~ +-20 m and looked flat: stretch the grid to the full -relief..+relief.
    n_lo = min(min(r) for r in noise)
    n_hi = max(max(r) for r in noise)
    span = (n_hi - n_lo) or 1.0
    shore = 400.0  # cm of beach that slopes into the water
    depth = o["water_depth_m"] * 100

    def shape(n):
        t = (n - n_lo) / span  # 0..1
        if o["peaks"]:  # wide valleys and sharp tops: bend the curve before scaling
            t = t ** (1 + 2 * o["peaks"])
        h = (t * 2 - 1) * relief_cm
        if step:  # soft terraces: flat shelves joined by short slopes
            k = h / step
            f = k - math.floor(k)
            h = (math.floor(k) + _smoothstep(0.7, 1.0, f)) * step
        if sea is not None and h < sea + shore:
            # under water stays under water: the bed drops away from the shore (never a flat sheet at
            # the water line), and the last few metres of land slope down to a beach
            below = sea + shore - h
            h = sea + shore - below - _smoothstep(0, shore * 3, below) * (depth + shore)
        return h

    base = [[shape(n) for n in row] for row in noise]

    def at(x, y):
        i = max(0, min(grid - 1, int((x - lo[0]) / size_cm * grid)))
        j = max(0, min(grid - 1, int((y - lo[1]) / size_cm * grid)))
        return base[j][i]

    blend = o["flatten_m"] * 100
    # a city's plateau is its centre's ground, lifted out of the water when it sits in a lake
    plateaus = [(c, max(at(c["x"], c["y"]), (sea + shore * 2) if sea is not None else -1e9)) for c in cities] \
        if o["city_ground"] == "flatten" else []
    rows = []
    for j, y in enumerate(ys):
        row = []
        for i, x in enumerate(xs):
            h = base[j][i]
            for c, flat in plateaus:
                w = 1 - _smoothstep(c["radius"] * 1.05, c["radius"] * 1.05 + blend, math.hypot(x - c["x"], y - c["y"]))
                h += (flat - h) * w
            for k in keep or []:  # last: whatever is built there sits exactly on its own ground
                h += (k["z"] - h) * (1 - _smoothstep(0, max(blend, 5000.0), keep_gap(k, x, y)))
            row.append(round(h, 1))
        rows.append(row)
    return {"min": lo, "max": hi, "grid": grid, "heights": rows, "sea": sea}


PAD_BLEND_CM = 400.0  # a lot's pad blends back into the hillside over this distance
PAD_CELL_CM = 4000.0


def graded(ter: dict, pads: list, blend: float = PAD_BLEND_CM):
    """The terrain with every lot cut and filled to its level pad (pcg_layers.pads): z = f(x, y)."""
    cells: dict = {}
    for p in pads:
        r = max(p[4], p[5]) + blend
        for i in range(int((p[0] - r) // PAD_CELL_CM), int((p[0] + r) // PAD_CELL_CM) + 1):
            for j in range(int((p[1] - r) // PAD_CELL_CM), int((p[1] + r) // PAD_CELL_CM) + 1):
                cells.setdefault((i, j), []).append(p)

    def at(x, y):
        z = st.sample_grid(ter, x, y) or 0.0
        best, bz = blend, z  # neighbouring pads never blend into each other: the one this spot is deepest in wins
        for cx, cy, c, s, hw, hd, pz in cells.get((int(x // PAD_CELL_CM), int(y // PAD_CELL_CM)), ()):
            dx, dy = x - cx, y - cy
            e = max(abs(dx * c + dy * s) - hd, abs(-dx * s + dy * c) - hw)  # outside the pad, in cm
            if e < best:
                best, bz = e, pz
        return z + (bz - z) * (1 - _smoothstep(0, blend, best)) if best < blend else z
    return at


DECK_CLEAR_CM = 200.0  # the ground stays this far under a deck's top (its slab and beams), at least
ROAD_SINK_CM = 10.0  # the ground sits this far under a road's surface: the mesh always shows, never flickers with it
ROAD_BLEND_CM = 3000.0  # generated ground meets a road at its kerb and blends back to the hills over this
STRUCTURES = {ROAD_MESH[k].split("/")[-1] for k in ("BR", "BRR", "BW", "ED", "ER")}  # bridges and viaducts stand free


def cut_pads(ter: dict, pads: list, locked=(), blend: float = 0.0) -> dict:
    """Every lot (pcg_layers.pads) cut level into a fine terrain grid, a cell at a time, blending back into the
    hillside. Cells a road owns (`locked`, from grade_to_roads) keep the road's height: a street always wins
    over the lot beside it, so the ground never climbs over a kerb."""
    grid, (x0, y0), (x1, y1) = ter["grid"], ter["min"], ter["max"]
    cw, ch = (x1 - x0) / grid, (y1 - y0) / grid
    blend = blend or max(PAD_BLEND_CM, 1.5 * max(cw, ch))
    best: dict = {}  # (j, i) -> (how far outside the pad, pad z): the pad a cell is deepest in wins
    for cx, cy, c, s, hw, hd, pz in pads:
        r = math.hypot(hw, hd) + blend
        for j in range(max(0, int((cy - r - y0) / ch)), min(grid, int((cy + r - y0) / ch) + 1)):
            dy = y0 + (j + 0.5) * ch - cy
            for i in range(max(0, int((cx - r - x0) / cw)), min(grid, int((cx + r - x0) / cw) + 1)):
                if (j, i) in locked:
                    continue
                dx = x0 + (i + 0.5) * cw - cx
                e = max(abs(dx * c + dy * s) - hd, abs(-dx * s + dy * c) - hw)
                if e < blend and e < best.get((j, i), (blend,))[0]:
                    best[(j, i)] = (e, pz)
    rows = [list(r) for r in ter["heights"]]
    for (j, i), (e, pz) in best.items():
        z = rows[j][i]
        rows[j][i] = round(z + (pz - z) * (1 - _smoothstep(0, blend, e)), 1)
    return {**ter, "heights": rows}


def grade_to_roads(ter: dict, actors: dict, locked: set | None = None) -> dict:
    """The generated terrain cut and filled to its roads (what the live splines do to a landscape in UEFN), so
    the ground meets every street at its kerb instead of the street floating on a wall over a valley. Bridges,
    viaducts, structures more than 20 m off the ground, docks, canals and water cells are left alone.
    Only for terrain the world owns: a scanned UEFN landscape is never written."""
    if not ter or not ter.get("heights"):
        return ter
    grid, (x0, y0), (x1, y1) = ter["grid"], ter["min"], ter["max"]
    cw, ch = (x1 - x0) / grid, (y1 - y0) / grid
    sea = ter.get("sea")
    best: dict = {}  # (j, i) -> (weight, -distance, road z): the strongest, then the nearest road sample wins
    fine = max(cw, ch) <= 1500  # cells small enough to tell one deck from the next (a landscape pixel, not a scan cell)
    cap: dict = {}  # (j, i) -> the highest the ground may be there: under a deck or ramp
    for label, a in actors.items():
        if label.endswith(("Docks", "Canals")):
            continue
        by_i = {p["i"]: p for p in a["points"]}
        for s in a["segments"]:
            pa, pb = by_i.get(s["a"]), by_i.get(s["b"])
            mesh = (s.get("mesh") or "").split("/")[-1]
            if not pa or not pb:
                continue
            half = HALF.get(next((k for k, m in ROAD_MESH.items() if m.split("/")[-1] == mesh), ""), 800.0)
            if mesh in STRUCTURES:
                # a deck or ramp stands free, but the ground may never rise through it: where a viaduct comes
                # down to grade the hill under it is cut to DECK_CLEAR_CM below the deck (never filled up to it)
                if fine:
                    reach_s = half + 0.75 * max(cw, ch)
                    ux, uy = pb["x"] - pa["x"], pb["y"] - pa["y"]
                    L2 = ux * ux + uy * uy or 1.0
                    for j in range(max(0, int((min(pa["y"], pb["y"]) - reach_s - y0) / ch)),
                                   min(grid, int((max(pa["y"], pb["y"]) + reach_s - y0) / ch) + 1)):
                        cy = y0 + (j + 0.5) * ch
                        for i in range(max(0, int((min(pa["x"], pb["x"]) - reach_s - x0) / cw)),
                                       min(grid, int((max(pa["x"], pb["x"]) + reach_s - x0) / cw) + 1)):
                            cx = x0 + (i + 0.5) * cw
                            t = max(0.0, min(1.0, ((cx - pa["x"]) * ux + (cy - pa["y"]) * uy) / L2))
                            if math.hypot(cx - pa["x"] - ux * t, cy - pa["y"] - uy * t) <= reach_s:
                                z = pa["z"] + (pb["z"] - pa["z"]) * t - DECK_CLEAR_CM
                                cap[(j, i)] = min(cap.get((j, i), 1e12), z)
                continue
            # every grid cell around the road sits exactly at road height, so the ground between them (bilinear)
            # never rises over the road; it blends back to the hills beyond that
            deck = half + 0.75 * max(cw, ch)  # a cell this close is on the road's own deck (or the nearest to it)
            half = max(half, 1.5 * max(cw, ch))
            reach = half + ROAD_BLEND_CM
            ga, gb = st.sample_grid(ter, pa["x"], pa["y"]), st.sample_grid(ter, pb["x"], pb["y"])
            if ga is None or gb is None or min(pa["z"] - ga, pb["z"] - gb) > 2000:
                continue  # a structure high above the ground stands free (a deep cut is always graded)
            ux, uy = pb["x"] - pa["x"], pb["y"] - pa["y"]
            L2 = ux * ux + uy * uy or 1.0
            for j in range(max(0, int((min(pa["y"], pb["y"]) - reach - y0) / ch)),
                           min(grid, int((max(pa["y"], pb["y"]) + reach - y0) / ch) + 1)):
                cy = y0 + (j + 0.5) * ch
                for i in range(max(0, int((min(pa["x"], pb["x"]) - reach - x0) / cw)),
                               min(grid, int((max(pa["x"], pb["x"]) + reach - x0) / cw) + 1)):
                    cx = x0 + (i + 0.5) * cw
                    # the road's own height at its nearest point to this cell (a linear field, so the ground
                    # interpolated between cells follows the road exactly)
                    t = max(0.0, min(1.0, ((cx - pa["x"]) * ux + (cy - pa["y"]) * uy) / L2))
                    d = math.hypot(cx - pa["x"] - ux * t, cy - pa["y"] - uy * t)
                    w = 1 - _smoothstep(half, reach, d)
                    if w <= 0:
                        continue
                    new = (w, -d, pa["z"] + (pb["z"] - pa["z"]) * t - ROAD_SINK_CM, w, d <= deck and "Water_" not in mesh)
                    cur = best.get((j, i))
                    # (measured on a 277 km world: letting the nearest point decide between pieces of one road put
                    # the ground over 3x more street points; the lower deck always, for one road or two)
                    if cur is None:
                        take = True
                    elif fine and new[4] and cur[4] and abs(new[2] - cur[2]) > 30:
                        # two decks at different heights on one cell (a ramp beside its freeway, a road under a
                        # flyover, two arms of a junction on a slope): the lower one, so no road is ever buried;
                        # the upper one's deck and wall cover the step
                        take = new[2] < cur[2]
                    else:
                        take = new[:2] > cur[:2]
                    if take:
                        best[(j, i)] = new
    rows = [list(r) for r in ter["heights"]]
    for (j, i), (_r, _d, z, w, _deck) in best.items():
        h = rows[j][i]
        if sea is not None and h < sea:
            continue  # the water stays water
        rows[j][i] = round(h + (z - h) * w, 1)
        if locked is not None and w >= 0.999:
            locked.add((j, i))  # the road's own cells: nothing cut later may move them
    for (j, i), top in cap.items():
        own = best.get((j, i))
        if own and own[4] and own[3] >= 0.999 and abs(own[2] - top - DECK_CLEAR_CM) <= 100:
            continue  # a ramp lands on this road's own deck: the ground stays at the road (was 2 m under it)
        if rows[j][i] > top and not (sea is not None and rows[j][i] < sea):
            rows[j][i] = round(top, 1)
            if locked is not None:
                locked.add((j, i))  # no lot may lift the ground back into the deck
    return {**ter, "heights": rows}


HEIGHTMAP_RES = 1009  # 16 components x 63 quads + 1


def heightmap_px(ter: dict, res: int = HEIGHTMAP_RES) -> float:
    """One heightmap pixel in cm: the finest step the imported landscape has."""
    return max(ter["max"][0] - ter["min"][0], ter["max"][1] - ter["min"][1]) / (res - 1)


def pad_margin(ter: dict, res: int = HEIGHTMAP_RES) -> float:
    """How much wider (cm, both sides together) a lot's pad is cut than the building: a pixel a side, so every
    landscape quad under the building has all four corners exactly at pad height after import."""
    return max(400.0, 2 * heightmap_px(ter, res) + 100)


def _pixel(ter: dict, res: int, pads: list | None):
    """(i, j) -> the heightmap's 16-bit sample there (32768 = 0, 128 per metre), lots levelled in."""
    lo, hi = ter["min"], ter["max"]
    ground = graded(ter, pads or [], max(PAD_BLEND_CM, 1.5 * heightmap_px(ter, res)))
    return lambda i, j: max(0, min(65535, int(round(32768 + ground(lo[0] + (hi[0] - lo[0]) * i / (res - 1),
                                                                   lo[1] + (hi[1] - lo[1]) * j / (res - 1)) * 128 / 100))))


def heightmap_rows(ter: dict, res: int = HEIGHTMAP_RES, pads: list | None = None) -> list:
    """The landscape UEFN gets: res x res 16-bit samples, lots levelled in."""
    px = _pixel(ter, res, pads)
    return [[px(i, j) for i in range(res)] for j in range(res)]


def imported_ground(ter: dict, res: int = HEIGHTMAP_RES, pads: list | None = None):
    """z(x, y) of the imported landscape exactly as UEFN builds it: bilinear between the heightmap's samples
    (computed only where asked, so checking every building costs a few thousand samples, not a million)."""
    (x0, y0), (x1, y1) = ter["min"], ter["max"]
    px, memo = _pixel(ter, res, pads), {}

    def v(i, j):
        if (i, j) not in memo:
            memo[(i, j)] = px(i, j)
        return memo[(i, j)]

    def at(x, y):
        fx = max(0.0, min(res - 1.0, (x - x0) / (x1 - x0) * (res - 1)))
        fy = max(0.0, min(res - 1.0, (y - y0) / (y1 - y0) * (res - 1)))
        i, j = min(res - 2, int(fx)), min(res - 2, int(fy))
        tx, ty = fx - i, fy - j
        a, b, c, d = v(i, j), v(i + 1, j), v(i, j + 1), v(i + 1, j + 1)
        return ((a + (b - a) * tx) * (1 - ty) + (c + (d - c) * tx) * ty - 32768) * 100 / 128
    return at


FIT_TOL_CM = 25.0  # what a slab or foundation hides; more than this buried or in the air is a misfit


def ground_fit(ground, buildings: list, keep: int = 50) -> dict:
    """Every building checked against the imported landscape: buried (ground above its floor) or floating (a gap
    under it its foundation doesn't fill). buildings: [(label, x, y, yaw, width_m, depth_m, floor z, foundation_m)].
    Each misfit gets the foundation it needs (`need_m`): the build uses it, so nothing floats."""
    bad = []
    hash_: dict = {}
    for k, (_l, x, y, _yaw, w_m, d_m, *_r) in enumerate(buildings):
        hash_.setdefault((int(x // 5000), int(y // 5000)), []).append(k)

    def inside_other(k, px, py):
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                for o in hash_.get((int(px // 5000) + i, int(py // 5000) + j), ()):
                    _l, ox, oy, oyaw, ow, od, *_r = buildings[o]
                    if o == k:
                        continue
                    c, s = math.cos(math.radians(oyaw)), math.sin(math.radians(oyaw))
                    dx, dy = px - ox, py - oy
                    if abs(dx * c + dy * s) < od * 50 and abs(-dx * s + dy * c) < ow * 50:
                        return True
        return False

    for k, (label, x, y, yaw, w_m, d_m, z, found) in enumerate(buildings):
        c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        pts = [(x + c * a * d_m * 50 - s * b * w_m * 50, y + s * a * d_m * 50 + c * b * w_m * 50)
               for a in (-1, 0, 1) for b in (-1, 0, 1)]
        zs = [ground(px, py) for px, py in pts]
        buried, gap = max(zs) - z, z - found * 100 - min(zs)
        if buried > FIT_TOL_CM or gap > FIT_TOL_CM:
            bad.append({"label": label, "x": round(x), "y": round(y), "buried_cm": round(max(0.0, buried)),
                        "floating_cm": round(max(0.0, gap)), "need_m": round((z - min(zs)) / 100 + 0.05, 1),
                        "overlaps": any(inside_other(k, px, py) for px, py in pts), "i": k,
                        "top": max(zs), "low": min(zs)})
    return {"checked": len(buildings), "buried": sum(1 for b in bad if b["buried_cm"]),
            "floating": sum(1 for b in bad if b["floating_cm"]), "overlapping": sum(1 for b in bad if b["overlaps"]),
            "ok": not bad, "misfits": bad[:keep]}


def settle(ground, buildings: list) -> tuple:
    """Every building that the imported landscape would bury or leave floating sits on the highest ground under
    it, its foundation reaching the lowest (how a builder sets a slab where neighbours' pads meet closer than a
    landscape quad). Returns ({index: (floor z, foundation_m)}, fit before, fit after)."""
    before = ground_fit(ground, buildings, keep=len(buildings))
    moves = {m["i"]: (round(m["top"] + 1), round((m["top"] + 1 - m["low"]) / 100 + 0.05, 1)) for m in before["misfits"]}
    after_rows = [(*b[:6], *moves[k]) if k in moves else b for k, b in enumerate(buildings)]
    after = ground_fit(ground, after_rows)
    for f in (before, after):
        for m in f["misfits"]:
            m.pop("top", None), m.pop("low", None), m.pop("i", None)
    before["misfits"] = before["misfits"][:20]
    return moves, before, after


def heightmap_png(ter: dict, res: int = HEIGHTMAP_RES, pads: list | None = None, rows: list | None = None) -> tuple:
    """16-bit greyscale PNG of the terrain for Landscape > Import, plus the import settings
    (Z scale 100: 1 unit = 100/128 cm, so +-256 m fits). pads: lots levelled into it."""
    lo, hi = ter["min"], ter["max"]
    rows = rows or heightmap_rows(ter, res, pads)
    res = len(rows)
    raw = bytearray()
    for vals in rows:
        raw += b"\x00" + struct.pack(f">{res}H", *vals)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", res, res, 16, 0, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))
    scale = (hi[0] - lo[0]) / (res - 1)
    # Landscape mode's New > Location is the landscape's centre, not its corner; 1009 = 16 components x 63 quads + 1
    return png, {"resolution": f"{res} x {res}", "location": [round((lo[0] + hi[0]) / 2), round((lo[1] + hi[1]) / 2), 0],
                 "scale": [round(scale, 3), round(scale, 3), 100.0],
                 "components": f"{(res - 1) // 63} x {(res - 1) // 63}, 63 quads, 1 section"}


# ------------------------------------------------------------------ cities
def place_cities(seed: int, count: int, bounds: tuple, keep: list | None = None, size: float = 1.0,
                 gap_cm: float | None = None) -> list:
    """count cities inside bounds ((x0, y0), (x1, y1)), never overlapping each other or a keep zone;
    their size follows the map (8-14 % of its width, so a 6 km map gets 500-850 m cities) x `size`, and at least
    gap_cm of open country lies between two cities' edges (default: half their radii)."""
    rnd = random.Random(seed * 31 + 7)
    (x0, y0), (x1, y1) = bounds
    span = min(x1 - x0, y1 - y0)
    min_r, max_r = span * 0.08 * size, span * 0.14 * size
    max_r = min(max_r, span / 2.6)  # still fits on the map
    min_r = min(min_r, max_r)
    out = []
    for _ in range(count * 60):
        if len(out) >= count:
            break
        r = rnd.uniform(min_r, max_r)
        x, y = rnd.uniform(x0 + r * 1.2, x1 - r * 1.2), rnd.uniform(y0 + r * 1.2, y1 - r * 1.2)
        if in_keep(keep, x, y, r * 1.5):
            continue
        gap = lambda c: gap_cm if gap_cm is not None else (r + c["radius"]) * 0.5
        if all(math.hypot(x - c["x"], y - c["y"]) > r + c["radius"] + gap(c) for c in out):
            out.append({"name": NAMES[len(out) % len(NAMES)], "x": round(x), "y": round(y), "radius": round(r),
                        "style": STYLES[len(out) % len(STYLES)], "seed": rnd.randrange(1 << 30)})
    return out


EDGE_REACH = 1.3  # a city's outline reaches 1.265 x its radius in places (outline()): its edge, as a factor of radius
EDGE_MARGIN_CM = 20000.0  # and this much more stays on the map (gates, interchanges, the first trees)


def fit_inside(towns: list, bounds: tuple, gap_cm: float, keep: list | None = None) -> tuple:
    """Cities pushed apart (spread) and kept on the map: every city's outline plus EDGE_MARGIN_CM stays inside
    bounds ((x0, y0), (x1, y1)). A city wider than the map shrinks to fit; when they can't all fit apart, every
    city shrinks a little until they do. Returns (towns, notes): one note per city that had to shrink."""
    (x0, y0), (x1, y1) = bounds
    span = min(x1 - x0, y1 - y0)
    asked = {t["name"]: t["radius"] for t in towns}
    towns = [dict(t) for t in towns]
    for t in towns:
        t["radius"] = min(t["radius"], max(15000.0, (span / 2 - EDGE_MARGIN_CM) / EDGE_REACH))

    def clamp() -> bool:
        moved = False
        for t in towns:
            m = t["radius"] * EDGE_REACH + EDGE_MARGIN_CM
            x = min(max(t["x"], x0 + m), x1 - m) if x1 - x0 > 2 * m else (x0 + x1) / 2
            y = min(max(t["y"], y0 + m), y1 - m) if y1 - y0 > 2 * m else (y0 + y1) / 2
            if abs(x - t["x"]) > 1 or abs(y - t["y"]) > 1:
                t["x"], t["y"], moved = round(x), round(y), True
        return moved

    def crowded() -> bool:
        return any(math.hypot(a["x"] - b["x"], a["y"] - b["y"]) < a["radius"] + b["radius"] + gap_cm - 100
                   for i, a in enumerate(towns) for b in towns[i + 1:])

    for _ in range(12):
        for _ in range(30):  # push apart, pull back onto the map, until both hold
            towns[:] = spread(towns, gap_cm, keep)
            if not clamp():
                break
        if not crowded():
            break
        for t in towns:  # no room for them all at this size: every city a little smaller
            t["radius"] = max(15000.0, t["radius"] * 0.92)
    clamp()
    notes = [f"{t['name']} is {round(t['radius'] / 100)} m, not {round(asked[t['name']] / 100)} m: the map has no "
             f"room for more" for t in towns if t["radius"] < asked.get(t["name"], 0) - 100]
    return towns, notes


def spread(towns: list, gap_cm: float, keep: list | None = None) -> list:
    """Existing cities pushed apart (each pair moves equally along the line between them) until at least gap_cm
    of open country lies between every two edges; cities already far enough apart stay where they are."""
    towns = [dict(t) for t in towns]
    for _ in range(60):
        moved = False
        for i in range(len(towns)):
            for j in range(i + 1, len(towns)):
                a, b = towns[i], towns[j]
                dx, dy = b["x"] - a["x"], b["y"] - a["y"]
                d = math.hypot(dx, dy) or 1.0
                short = a["radius"] + b["radius"] + gap_cm - d
                if short > 1:
                    ux, uy = dx / d, dy / d
                    a["x"], a["y"] = round(a["x"] - ux * short / 2), round(a["y"] - uy * short / 2)
                    b["x"], b["y"] = round(b["x"] + ux * short / 2), round(b["y"] + uy * short / 2)
                    moved = True
        if not moved:
            break
    return towns


def districts(city: dict, opts: dict | None = None) -> dict:
    o = options(opts)
    r = city["radius"]
    return {"downtown": {"radius": round(r * o["downtown"])}, "normal": {"radius": round(r * max(o["normal"], o["downtown"]))},
            "housing": {"radius": round(r)},
            # one wedge of the outer rings is the industrial zone (factories, warehouses), its direction from the seed
            "industrial": {"angle": (int(city.get("seed", 0)) * 137) % 360, "spread": 75}}


def outline(city: dict, angle_deg: float) -> float:
    """How far the city (and each of its district rings) reaches in this direction, as a factor of its
    radius (~0.75..1.25): a few seeded waves, so no city or district is a perfect circle."""
    s = int(city.get("seed", 1))
    a = math.radians(angle_deg)
    f = 0.0
    for k, amp in ((2, 0.11), (3, 0.08), (5, 0.05), (7, 0.025)):
        phase = ((s * (k * 7919 + 13)) % 997) / 997 * 2 * math.pi
        f += amp * math.sin(k * a + phase)
    return 1.0 + f


def zone_share(city: dict, x: float, y: float) -> float:
    """Distance from the centre as a share of the city's (irregular) edge in that direction: <downtown
    share = core, <normal share = neighbourhood, <1 = suburb."""
    dx, dy = x - city["x"], y - city["y"]
    return math.hypot(dx, dy) / (city["radius"] * outline(city, math.degrees(math.atan2(dy, dx))))


def orientation(city: dict) -> float:
    """RoadOrientation: the city's grid angle in degrees (the first draw of city_graph's seeded random)."""
    return math.degrees(random.Random(city.get("seed", 1)).uniform(0, math.pi / 2))


def canal_lines(city: dict, opts: dict | None = None) -> list:
    """CanalGenerator: the canal city's canals, polylines in world cm: every third grid line (never a collector or
    an axis) through the downtown and normal rings, along the city's own grid."""
    o = city_options(city, opts)
    if o["canals"] != "canal_city":
        return []
    block, normal = o["block_m"] * 100, max(o["normal"], o["downtown"])
    t = math.radians(orientation(city))
    c, s = math.cos(t), math.sin(t)
    W = lambda lx, ly: [city["x"] + c * lx - s * ly, city["y"] + s * lx + c * ly]
    k = int(city["radius"] * 1.3 // block)
    out = []
    for ix in range(-k, k + 1):
        if ix % 3 != 1 or ix % o["collector_every"] == 0:
            continue
        run = [iy for iy in range(-k, k + 1) if zone_share(city, *W(ix * block, iy * block)) <= normal]
        if len(run) >= 3:
            out.append([W(ix * block, iy * block) for iy in range(run[0], run[-1] + 1)])
    return out


def canal_wet(city: dict, opts: dict | None = None):
    """(x, y) -> True inside a canal's channel; None without canals."""
    lines = canal_lines(city, opts)
    if not lines:
        return None
    half = HALF["CN"] + 100
    segs = [(p, q) for ln in lines for p, q in zip(ln, ln[1:])]

    def inside(x, y):
        for p, q in segs:
            L2 = (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2 or 1.0
            t = max(0.0, min(1.0, ((x - p[0]) * (q[0] - p[0]) + (y - p[1]) * (q[1] - p[1])) / L2))
            if math.hypot(x - p[0] - t * (q[0] - p[0]), y - p[1] - t * (q[1] - p[1])) < half:
                return True
        return False
    return inside


def zoning(city: dict, opts: dict | None = None) -> tuple:
    """(ring_of(x, y) -> 'core' | 'normal' | 'housing', industrial(x, y) -> bool) for one city."""
    o = options(opts)
    down, normal = o["downtown"], max(o["normal"], o["downtown"])
    ring_of = lambda x, y: (lambda s: "core" if s <= down else "normal" if s <= normal else "housing")(
        zone_share(city, x, y))
    ind = districts(city, o)["industrial"]

    def industrial(x, y):
        a = math.degrees(math.atan2(y - city["y"], x - city["x"]))
        return abs((a - ind["angle"] + 180) % 360 - 180) <= ind["spread"] / 2 and zone_share(city, x, y) > normal * 0.8
    return ring_of, industrial


def plan_airports(cities: list, ground, wet, keep: list | None, bounds: tuple, opts: dict | None = None) -> list:
    """AirportSitePlanner for the world, before the streets: sets city['airports'] (every airport: streets and
    homes keep clear of all of them) and returns the airports."""
    from . import airport
    o = options(opts)
    pops = {c["name"]: round(math.pi * (c["radius"] / 100000) ** 2 * 4800) for c in cities}
    aps = airport.plan_world(cities, ground, wet, keep, bounds, o, pops)  # each city's own choice applies there
    for c in cities:
        c["airports"] = aps
    return aps


def plan_shipyards(cities: list, water: dict, opts: dict | None = None) -> None:
    """ShipyardPlanner for every city, before its streets: sets city['shipyards'] (campuses + their docks)."""
    from . import shipyard
    from . import waterfront
    o = options(opts)
    for c in cities:
        c["shipyards"] = []
        co = city_options(c, o)
        if co["shipyards"] == "none" or co["waterfront"] != "yes" or not water or water.get("sea") is None:
            continue
        ring_of, industrial = zoning(c, o)
        pop = round(math.pi * (c["radius"] / 100000) ** 2 * 4800)  # ~4,800 people per km2 before the lots exist
        segs = waterfront.segments_for(c, water, ring_of, industrial, pop)
        c["shipyards"] = shipyard.sites(c, segs, pop, o)


def water_of(city: dict, opts: dict | None, wet):
    """The terrain's water and the city's canals as one (x, y) -> bool (None when there is neither)."""
    cw = canal_wet(city, opts)
    if not cw:
        return wet
    return lambda x, y: cw(x, y) or bool(wet and wet(x, y))


def city_graph(city: dict, opts: dict | None = None, wet=None, bridges: list | None = None) -> tuple:
    """Street network of one city: nodes {key: [x, y]}, edges [(a, b, kind)]. Real cities mix patterns
    (urban-design transect): a strict grid of short blocks in the core, a looser grid in the
    neighbourhoods, curving streets with cul-de-sacs in the suburbs, all inside an irregular outline.
    Downtown and the two main axes are four-lane; suburbs are two-lane; the rest has pavements."""
    o = city_options(city, opts)
    block, down, normal = o["block_m"] * 100, o["downtown"], max(o["normal"], o["downtown"])
    rnd = random.Random(city.get("seed", 1))
    cx, cy, R, style = city["x"], city["y"], city["radius"], city.get("style", "mixed")
    turn = rnd.uniform(0, math.pi / 2)
    c, s = math.cos(turn), math.sin(turn)
    k = int(R * 1.3 // block)
    nodes, ring = {}, {}
    for ix in range(-k, k + 1):
        for iy in range(-k, k + 1):
            lx, ly = ix * block, iy * block
            wx, wy = cx + c * lx - s * ly, cy + s * lx + c * ly
            share = zone_share(city, wx, wy)
            if share > 1.0:
                continue  # outside this city's irregular edge
            # controlled irregularity: the core is exact, everything else moves whole intersections by at
            # most `wobble` (<= 8 % of a block), so roads stay straight between them
            amount = 0.0 if style == "grid" or (style == "mixed" and share <= down) else o["wobble"]
            if amount and (ix or iy):
                # main roads (collectors and the axes, every collector_every-th line) stay dead straight, as real
                # arterials do; freeway interchanges and ramps land on them exactly
                jx, jy = rnd.uniform(-1, 1), rnd.uniform(-1, 1)
                if ix % o["collector_every"]:
                    lx += jx * amount * block
                if iy % o["collector_every"]:
                    ly += jy * amount * block
            nodes[(ix, iy)] = [cx + c * lx - s * ly, cy + s * lx + c * ly]
            ring[(ix, iy)] = share
    edges = []
    for (ix, iy) in nodes:
        for nb in ((ix + 1, iy), (ix, iy + 1)):
            if nb not in nodes:
                continue
            share = max(ring[(ix, iy)], ring[nb])
            axis = (ix == 0 == nb[0]) or (iy == 0 == nb[1])  # arterials: the two main axes
            line = ix if nb[0] == ix else iy  # the grid line this street runs along
            collector = line % o["collector_every"] == 0
            if not axis and collector and share > down:  # collectors: a wider street every Nth line
                kind = o["neighbourhood_roads"] if share > normal else "4L"
            elif axis or share <= down:
                kind = o["core_roads"]
                # one-way pairs: every other side street in the core is a narrow one-way lane
                if not axis and o["one_way_core"] == "yes" and (ix if nb[0] == ix else iy) % 2:
                    kind = "1W"
            else:
                kind = o["suburb_roads"] if share > normal else o["neighbourhood_roads"]
            edges.append([(ix, iy), nb, kind])
    if o["superblocks"] == "yes":
        def drop(e):
            a, b, _ = e
            line = a[0] if b[0] == a[0] else a[1]
            axis = (a[0] == 0 == b[0]) or (a[1] == 0 == b[1])
            return (max(ring[a], ring[b]) > down and line % 2 != 0 and line % o["collector_every"] != 0
                    and not axis)
        edges = [e for e in edges if not drop(e)]
        # suburbs go further: most whole collector cells there keep no public street inside at all. Each is one
        # subdivision (neighbourhoods 'collector'): a winding street through it, cul-de-sacs off that
        ce, big, fwp = o["collector_every"], {}, freeway_plan(city, o)

        def superblock(g):
            if g not in big:
                ks = [(g[0] * ce + i, g[1] * ce + j) for i in range(ce + 1) for j in range(ce + 1)]
                inner = lambda n: 0 < n[0] - g[0] * ce < ce and 0 < n[1] - g[1] * ce < ce  # its edge may be in town
                big[g] = all(n in nodes and (ring[n] > normal or not inner(n)) for n in ks) and not (
                    fwp.lines and fwp.blocked(*nodes[(g[0] * ce + ce // 2, g[1] * ce + ce // 2)][:2], 0.75 * ce * block)
                ) and random.Random(f"superblock:{city.get('seed', 1)}:{g}").random() < SUPERBLOCK_SHARE
            return big[g]
        edges = [e for e in edges if (e[0][0] if e[1][0] == e[0][0] else e[0][1]) % ce == 0
                 or not superblock((e[0][0] // ce, e[0][1] // ce))]
    if style != "grid" and o["prune"]:
        # neighbourhoods drop some side streets (never a main one, never a dead end); suburbs drop more and
        # may leave cul-de-sacs, like real subdivisions
        degree = {}
        for a, b, _ in edges:
            degree[a] = degree.get(a, 0) + 1
            degree[b] = degree.get(b, 0) + 1
        kept = []
        for e in edges:
            a, b, kind = e
            suburb = min(ring[a], ring[b]) > normal
            floor = 1 if suburb else 2  # a suburb street may end in a cul-de-sac
            chance = o["prune"] * (1.6 if suburb else 1.0)
            line = a[0] if b[0] == a[0] else a[1]
            local = kind != o["core_roads"] and kind != "4L" and line % o["collector_every"] != 0
            if local and degree[a] > floor and degree[b] > floor and rnd.random() < chance:
                degree[a] -= 1
                degree[b] -= 1
                continue
            kept.append(e)
        edges = kept
        # keep the one connected street network that holds the centre: pruning may cut a corner off
        adj: dict = {}
        for a, b, _ in edges:
            adj.setdefault(a, []).append(b)
            adj.setdefault(b, []).append(a)
        start = min(adj, key=lambda n: abs(n[0]) + abs(n[1])) if adj else None
        seen, todo = set(), [start] if start is not None else []
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            seen.add(n)
            todo.extend(adj.get(n, []))
        edges = [e for e in edges if e[0] in seen]
        nodes = {n: p for n, p in nodes.items() if n in seen}
    yards = city.get("shipyards") or []
    fields = city.get("airports") or []
    if yards or fields:  # shipyards and airports are dedicated districts with their own roads
        from . import airport, shipyard
        drop = {n for n, p in nodes.items() if any(shipyard.inside(y, p[0], p[1], -2000) for y in yards)
                or any(airport.inside(a, p[0], p[1], 3000) for a in fields)}
        edges = [e for e in edges if e[0] not in drop and e[1] not in drop]
        used = {n for e in edges for n in e[:2]}
        nodes = {n: p for n, p in nodes.items() if n in used}
    cw = canal_wet(city, o)
    if cw:  # a canal takes its street line; the streets crossing it run straight over it on a bridge
        on_canal = {n for n, p in nodes.items() if isinstance(n, tuple) and len(n) == 2 and cw(*p[:2])}
        kept, arms = [], {}
        for e in edges:
            a, b = e[0], e[1]
            if a in on_canal and b in on_canal:
                continue  # the street the canal replaced
            if a in on_canal or b in on_canal:
                mid, other = (a, b) if a in on_canal else (b, a)
                arms.setdefault(mid, []).append((other, e[2]))
                continue
            kept.append(e)
        for mid, ends in arms.items():  # (ix-1, iy) - [canal] - (ix+1, iy): one street with a canal bridge
            for (p, kp), (q, kq) in ((ends[i], ends[j]) for i in range(len(ends)) for j in range(i + 1, len(ends))):
                if isinstance(p, tuple) and isinstance(q, tuple) and len(p) == len(q) == 2 and p[1] == q[1] == mid[1]:
                    kept.append([p, q, kp])
        edges = kept
        used = {n for e in edges for n in e[:2]}
        nodes = {n: p for n, p in nodes.items() if n in used}
        wet = water_of(city, o, wet)
    if wet:  # streets cross water on bridges (one edge bank to bank), the rest stop at the shore
        nodes, edges = over_water(*cross_water(nodes, edges, wet), wet, bridges=bridges)
    # freeways last: every street that water, canals, an airport or a shipyard took is gone by now, so an
    # interchange whose cross road is gone is dropped before any street is cut for its ramps
    fw = freeway_plan(city, o)
    if fw.lines:
        def crosses(ic):
            bx, by = ic["b_axis"]
            for e in edges:
                p, q = nodes[e[0]], nodes[e[1]]
                L = math.dist(p[:2], q[:2]) or 1.0
                if abs((q[0] - p[0]) * bx + (q[1] - p[1]) * by) / L > 0.97 and \
                        _dist_seg(ic["at"], p[:2], q[:2]) < 1500:
                    return True
            return False
        fw.drop({ic["group"] for ic in fw.interchanges if ic.get("b_axis") and not crosses(ic)})
        # the cross road keeps its interchange width through the whole interchange (ramps were sized for it)
        for ic in fw.interchanges:
            if not ic.get("b_axis"):
                continue
            bx, by = ic["b_axis"]
            for e in edges:
                p, q = nodes[e[0]], nodes[e[1]]
                L = math.dist(p[:2], q[:2]) or 1.0
                along = abs((q[0] - p[0]) * bx + (q[1] - p[1]) * by) / L
                mx, my = (p[0] + q[0]) / 2 - ic["at"][0], (p[1] + q[1]) / 2 - ic["at"][1]
                off = abs(mx * by - my * bx)  # distance of the edge's middle from the cross road's line
                if along > 0.97 and off < 1500 and math.hypot(mx, my) < 20000 and HALF.get(e[2], 0) < HALF[ic["cross_road"]]:
                    e[2] = ic["cross_road"]
        # a street a low ramp or an at-grade freeway would cut gives way (streets pass under viaducts)
        edges = [e for e in edges if not fw.street_blocked(nodes[e[0]], nodes[e[1]], HALF.get(e[2], 500.0))]
        used = {n for e in edges for n in e[:2]}
        nodes = {n: p for n, p in nodes.items() if n in used}
    # a street keeps one width between junctions: where two blocks of different kinds meet at a plain point
    # (no side street left there) the narrower takes the wider's kind, so no road steps in width mid-block
    at: dict = {}
    for e in edges:
        for n in e[:2]:
            at.setdefault(n, []).append(e)
    changed = True
    while changed:
        changed = False
        for es in at.values():
            if len(es) == 2 and es[0][2] != es[1][2] and "DT" not in (es[0][2], es[1][2]):
                wide = max(es, key=lambda e: HALF.get(e[2], 0.0))[2]
                for e in es:
                    if e[2] != wide:
                        e[2] = wide
                        changed = True
    for e in edges:  # every city street carries its sidewalks in its own mesh
        e[2] = CITY_STREET.get(e[2], e[2])
        if len(e) > 3 and e[3]:  # and its bridge pieces follow its final kind: a street widened after its
            deck, ramp = structure_kinds(e[2])  # bridge was planned rode a narrower deck (a 7 m width jump)
            for v in e[3]:
                if len(v) > 3 and v[3]:
                    v[3] = deck if v[3] in ("BR", "ED", "BW") else ramp if v[3] == "BRR" else e[2]
    return nodes, edges


def _dist_seg(p, a, b):
    L2 = (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 or 1.0
    t = max(0.0, min(1.0, ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / L2))
    return math.hypot(p[0] - a[0] - t * (b[0] - a[0]), p[1] - a[1] - t * (b[1] - a[1]))


WATER_SPAN = {"6L": 300000.0, "6S": 300000.0, "FR": 300000.0, "4L": 150000.0, "2S": 60000.0, "2L": 20000.0,
              "ED": 300000.0, "ER": 300000.0, "BR": 300000.0, "BRR": 300000.0}  # longest water a road of
# this kind bridges (avenues cross a lake, local streets a river); a wider crossing is not built
SHORT_SPAN = 6000.0  # any street (not dirt) bridges a creek this narrow
SHORE_MARGIN_CM = 2500.0  # a road cut at the shore stops this far inland of the last wet sample


def cross_water(nodes: dict, edges: list, wet) -> tuple:
    """A street whose junctions fall in the water crosses it in one edge, bank to bank: from each dry node into
    the water, follow the straightest street on through the wet junctions to the first dry one and join the two
    (over_water then bridges it or, too wide for the road, drops it). Streets left in the water are removed;
    a street into open water keeps its land part (over_water cuts it at the shore)."""
    def dry(n):
        p = nodes[n]
        return (len(p) > 2 and p[2] >= BRIDGE_LIFT_CM - 1) or not wet(p[0], p[1])
    is_dry = {n: dry(n) for n in nodes}
    if all(is_dry.values()):
        return nodes, edges
    adj: dict = {}
    for e in edges:
        if len(e) > 3 and e[3]:
            continue  # an edge with its own shape (ramps, curves) keeps it
        adj.setdefault(e[0], []).append((e[1], e[2]))
        adj.setdefault(e[1], []).append((e[0], e[2]))
    out, seen, used_first = [], set(), set()
    for a in nodes:
        if not is_dry[a]:
            continue
        for b, kind in adj.get(a, []):
            if is_dry[b]:
                continue
            prev, cur, kinds = a, b, [kind]
            dx, dy = nodes[b][0] - nodes[a][0], nodes[b][1] - nodes[a][1]
            for _ in range(80):  # ponytail: straight-line walk, enough for grid and organic streets
                if is_dry[cur]:
                    break
                L = math.hypot(dx, dy) or 1.0
                best, bd = None, 0.9
                for nb, k in adj.get(cur, []):
                    if nb == prev:
                        continue
                    ux, uy = nodes[nb][0] - nodes[cur][0], nodes[nb][1] - nodes[cur][1]
                    d = (ux * dx + uy * dy) / (L * (math.hypot(ux, uy) or 1.0))
                    if d > bd:
                        best, bd = (nb, k), d
                if best is None:
                    break
                prev, cur = cur, best[0]
                kinds.append(best[1])
                dx, dy = nodes[cur][0] - nodes[a][0], nodes[cur][1] - nodes[a][1]
            if not is_dry[cur] or cur == a:  # the street breaks off in the water: the nearest dry junction
                ax, ay = nodes[a][0], nodes[a][1]  # straight ahead on the far bank, if the road may span to it
                ux, uy = nodes[b][0] - ax, nodes[b][1] - ay
                ul = math.hypot(ux, uy) or 1.0
                reach = max(WATER_SPAN.get(kind, 0.0), SHORT_SPAN) + 2 * ul
                best, bd = None, reach
                for c, p in nodes.items():
                    if not is_dry[c] or c == a or not adj.get(c):
                        continue
                    vx, vy = p[0] - ax, p[1] - ay
                    d = math.hypot(vx, vy)
                    if ul < d < bd and (vx * ux + vy * uy) / (d * ul) > 0.985:
                        best, bd = c, d
                if best is None:
                    continue
                cur = best
            used_first.add((a, b))
            key = frozenset((a, cur))
            if key not in seen:
                seen.add(key)
                out.append([a, cur, max(kinds, key=lambda k: HALF.get(k, 0.0))])
    for e in edges:
        a, b = e[0], e[1]
        if (len(e) > 3 and e[3]) or (is_dry[a] and is_dry[b]):
            out.append(e)
        elif is_dry[a] != is_dry[b] and ((a, b) not in used_first and (b, a) not in used_first):
            out.append(e)  # into open water: over_water keeps the part on land
    used = {n for e in out for n in e[:2]}
    return {n: p for n, p in nodes.items() if n in used}, out


def wet_from(ter: dict):
    """(x, y) -> True where the terrain is under its water line; None when the terrain has no water."""
    sea = (ter or {}).get("sea")
    if sea is None or not (ter or {}).get("heights"):
        return None
    return lambda x, y: (st.sample_grid(ter, x, y) if st.sample_grid(ter, x, y) is not None else sea + 1) < sea


def over_water(nodes: dict, edges: list, wet, span: dict | None = None, bridges: list | None = None) -> tuple:
    """Roads know where the water is. An edge that runs over water becomes a bridge (ramp up 40 m, deck
    BRIDGE_LIFT_CM over the water, ramp down) when its kind may span that far (WATER_SPAN; any paved street
    over a creek <= SHORT_SPAN) and it is not already high enough there; otherwise it is removed (a side street
    stops at the shore). An end in the water is cut back to the shore (new node ('shore', a, b, 0|1)).
    span: {kind: cm} overrides (highways pass a long one)."""
    if not wet:
        return nodes, edges
    span = {**WATER_SPAN, **(span or {})}
    out, extra = [], {}
    for e in edges:
        a, b, kind = e[0], e[1], e[2]
        via = list(e[3]) if len(e) > 3 and e[3] else []
        line = [list(nodes[a][:2]) + [float(nodes[a][2]) if len(nodes[a]) > 2 else 0.0]] + \
               [[v[0], v[1], float(v[2]) if len(v) > 2 else 0.0] for v in via] + \
               [list(nodes[b][:2]) + [float(nodes[b][2]) if len(nodes[b]) > 2 else 0.0]]
        cum, samples = 0.0, []  # (distance along, x, y, lift)
        for p, q in zip(line, line[1:]):
            L = math.dist(p[:2], q[:2])
            n = max(1, int(L // 1000))
            for k in range(n):
                t = k / n
                samples.append((cum + L * t, p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t, p[2] + (q[2] - p[2]) * t))
            cum += L
        samples.append((cum, *line[-1]))
        runs, cur = [], None
        for s, x, y, up in samples:
            if up < BRIDGE_LIFT_CM - 1 and wet(x, y):
                cur = [s, s] if cur is None else [cur[0], s]
            elif cur:
                runs.append(cur)
                cur = None
        if cur:
            runs.append(cur)
        if not runs:
            out.append(e)
            continue
        def at(s):
            for (s0, *p), (s1, *q) in zip(samples, samples[1:]):
                if s0 <= s <= s1:
                    t = (s - s0) / ((s1 - s0) or 1)
                    return [p[i] + (q[i] - p[i]) * t for i in range(3)]
            return list(samples[-1][1:])

        vcum = _cum_of(line)[1:-1]
        # an end in the water: the road is cut back to the shore (a freeway running out into a lake ends there)
        s_a, s_b, na, nb, first = 0.0, cum, a, b, kind
        if runs[0][0] <= 0:
            s_a = runs.pop(0)[1] + SHORE_MARGIN_CM
            na = ("shore", a, b, 0)
            first = next((v[3] for s, v in reversed(list(zip(vcum, via))) if s <= s_a and len(v) > 3 and v[3]), kind)
        if runs and runs[-1][1] >= cum:
            s_b = runs.pop()[0] - SHORE_MARGIN_CM
            nb = ("shore", a, b, 1)
        if s_b - s_a < 3000 or (s_a > 0 and s_b < cum and s_a >= s_b):
            continue  # nothing left on land
        limit = max(span.get(kind, 0.0), SHORT_SPAN if kind not in ("DT", "1W", "1S") else 0.0)
        if runs and max(r1 - r0 for r0, r1 in runs) > limit:
            continue  # too wide for this road: a side street stops at the shore (its neighbours still reach it)
        for key, s in ((na, s_a), (nb, s_b)):
            if key not in (a, b):
                p = at(s)
                extra[key] = [round(p[0]), round(p[1])] + ([round(p[2])] if p[2] > 0 else [])
        deck, ramp = structure_kinds(kind)
        spans = []
        for r0, r1 in runs:  # merge runs closer than two ramps: one longer bridge
            if spans and r0 - spans[-1][1] < 15000:
                spans[-1][1] = r1
            else:
                spans.append([r0, r1])
        spans = [[max(s_a, r0 - 3000), min(s_b, r1 + 3000)] for r0, r1 in spans]  # deck reaches past the shore
        # BridgeGenerator: the type and deck clearance follow the span (navigation needs height), the ramps are
        # long enough for the road's grade (6 %), never shorter than the 40 m ramp piece
        from .waterfront import bridge_kind
        plan = []
        for r0, r1 in spans:
            btype, lift = bridge_kind(max(0.0, r1 - r0 - 6000), kind)  # by the water it spans, not its landings
            ramp_len = max(4000.0, lift / 0.06)
            plan.append((r0, r1, lift, ramp_len, btype))
        keep = [(s, v) for s, v in zip(vcum, via)
                if not any(r0 - rl - 1000 < s < r1 + rl + 1000 for r0, r1, _, rl, _ in plan)]
        for r0, r1, lift, rl, btype in plan:
            fa, fb = max(s_a + 1, r0 - rl), min(s_b - 1, r1 + rl)  # ramp feet, never past the edge's ends
            pa, pb = at(fa), at(fb)
            top = max(lift, at(r0)[2], at(r1)[2])
            d0, d1 = max(r0, fa + rl), min(r1, fb - rl)  # deck ends where the ramps reach the top
            if d1 - d0 < 1000:  # a junction close to the bank: the ramps meet mid-span on a lower deck
                d0 = d1 = (fa + fb) / 2  # ponytail: short approaches trade clearance for an 8 % ramp
                top = min(top, max(300.0, (d0 - fa) * 0.08))
            keep += [(fa, [pa[0], pa[1], pa[2], ramp])]
            keep += [(d0, at(d0)[:2] + [top, deck]), (d1, at(d1)[:2] + [top, ramp])] if d1 > d0 else \
                [(d0, at(d0)[:2] + [top, ramp])]
            keep += [(fb, [pb[0], pb[1], pb[2], kind])]
            if bridges is not None:
                mid = at((r0 + r1) / 2)
                a_, b_ = at(r0), at(r1)
                yaw = math.degrees(math.atan2(b_[1] - a_[1], b_[0] - a_[0]))
                bridges.append({"type": btype, "kind": kind, "span_m": round((r1 - r0) / 100), "clearance_m": top / 100,
                                "x": round(mid[0]), "y": round(mid[1]), "yaw": round(yaw, 1), "deck_z_rel": top,
                                # on the deck as built (a straight chord between its ends), never on the planned
                                # polyline beside it: towers stood 10-20 m off the deck
                                "towers": [on_deck(at(d0), at(d1), (r0 + (r1 - r0) * f - d0) / ((d1 - d0) or 1.0))
                                           for f in (0.3, 0.7)]})
        keep.sort(key=lambda r: r[0])
        out.append([na, nb, first, [[round(v[0]), round(v[1])] + list(v[2:]) for s, v in keep if s_a < s < s_b]])
    nodes = {**nodes, **extra}
    used = {n for e in out for n in e[:2]}
    return {n: p for n, p in nodes.items() if n in used}, out


def on_deck(a, b, u: float) -> list:
    """A point u (0..1, clamped) of the way along a deck from a to b, in plan."""
    u = max(0.0, min(1.0, u))
    return [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u]


PIER_EVERY_CM = 3000.0  # a pier bent under a deck every 30 m
PIER_MIN_CM = 300.0  # no pier where the deck is lower than this over the ground (an approach on its fill)
DECK_DEPTH_CM = 150.0  # road surface to the deck's underside: the pier cap sits under that
CAP_CM = 120.0  # the cap beam's depth


def bridge_piers(actors: dict, ground) -> tuple:
    """Real bridges: under every deck and elevated ramp a pier bent every PIER_EVERY_CM, from the ground (or the
    lake bed) up to the deck's underside: a cap beam across the deck's width and one column (two under a deck
    16 m or wider), turned with the deck. Returns (columns, caps) as site rows [x, y, z, yaw, sx, sy, sz] for
    SM_Kit_BridgePier (200 x 200 x 100 cm at scale 1) and SM_Kit_PierCap (200 x 100 x 120 cm)."""
    cols, caps = [], []
    cell = 5000.0
    grade: dict = {}  # roads on the ground, by 50 m cell: a pier never stands in a road the deck crosses
    for label, a in actors.items():
        by = {p["i"]: p for p in a["points"]}
        for s in a["segments"]:
            mesh = (s.get("mesh") or "").split("/")[-1]
            pa, pb = by.get(s["a"]), by.get(s["b"])
            if mesh and mesh not in STRUCTURES and pa and pb:
                h = HALF.get(next((k for k, m in ROAD_MESH.items() if m.split("/")[-1] == mesh), ""), 800.0)
                for gx in range(int(min(pa["x"], pb["x"]) // cell) - 1, int(max(pa["x"], pb["x"]) // cell) + 2):
                    for gy in range(int(min(pa["y"], pb["y"]) // cell) - 1, int(max(pa["y"], pb["y"]) // cell) + 2):
                        grade.setdefault((gx, gy), []).append((pa, pb, h))

    def in_road(x, y):
        for pa, pb, h in grade.get((int(x // cell), int(y // cell)), ()):
            ux, uy = pb["x"] - pa["x"], pb["y"] - pa["y"]
            t = max(0.0, min(1.0, ((x - pa["x"]) * ux + (y - pa["y"]) * uy) / (ux * ux + uy * uy or 1.0)))
            if math.hypot(x - pa["x"] - ux * t, y - pa["y"] - uy * t) < h + 250:
                return True
        return False
    for label, a in actors.items():
        by = {p["i"]: p for p in a["points"]}
        for s in a["segments"]:
            mesh = (s.get("mesh") or "").split("/")[-1]
            pa, pb = by.get(s["a"]), by.get(s["b"])
            if mesh not in STRUCTURES or not pa or not pb:
                continue
            half = HALF.get(next((k for k, m in ROAD_MESH.items() if m.split("/")[-1] == mesh), ""), 800.0)
            L = math.hypot(pb["x"] - pa["x"], pb["y"] - pa["y"])
            if L < 100:
                continue
            yaw = math.degrees(math.atan2(pb["y"] - pa["y"], pb["x"] - pa["x"]))
            ux, uy = (pb["x"] - pa["x"]) / L, (pb["y"] - pa["y"]) / L
            n = max(1, int(L // PIER_EVERY_CM))
            for k in range(n):
                t = (k + 0.5) / n
                x, y = pa["x"] + (pb["x"] - pa["x"]) * t, pa["y"] + (pb["y"] - pa["y"]) * t
                top = pa["z"] + (pb["z"] - pa["z"]) * t - DECK_DEPTH_CM  # the deck's underside
                legs = [0.0] if half < 800 else [-half * 0.55, half * 0.55]
                feet = [ground(x - uy * o, y + ux * o) or 0.0 for o in legs]
                if top - CAP_CM - min(feet) < PIER_MIN_CM:
                    continue  # the deck is on (or just over) the ground here: no pier
                if any(in_road(x - uy * o, y + ux * o) for o in legs):
                    continue  # over a road it crosses: the deck spans it
                caps.append([round(x), round(y), round(top - CAP_CM), round(yaw, 1), 1.0, round(half * 2 * 0.9 / 100, 2), 1.0])
                for o, f in zip(legs, feet):
                    if top - CAP_CM - f >= 50:  # a leg standing on a bank higher than the cap is left out
                        cols.append([round(x - uy * o), round(y + ux * o), round(f), round(yaw, 1), 1.0, 1.0,
                                     round((top - CAP_CM - f) / 100, 2)])
    return cols, caps


def structure_kinds(kind: str) -> tuple:
    """(deck, approach) for a road of this kind crossing water or another road: a bridge as wide as the road it
    carries. A four-lane or wider road rides the wide deck piece all the way up and across (the two-lane bridge
    and the one-lane ramp under an avenue stepped 10-27 m in width at each end); a street takes the bridge pieces;
    a ramp stays a ramp."""
    if kind == "ER":
        return "ER", "ER"
    if kind == "6S":
        return "BW", "BW"
    return ("ED", "ED") if HALF.get(kind, 0.0) >= 1400.0 else ("BR", "BRR")


def _cum_of(line):
    cum = [0.0]
    for p, q in zip(line, line[1:]):
        cum.append(cum[-1] + math.dist(p[:2], q[:2]))
    return cum


_FW_CACHE: dict = {}


def freeway_plan(city: dict, opts: dict | None = None):
    """The city's freeway layer (freeways.plan), cached: city_graph, city_layout and generate all ask for it."""
    from . import freeways
    o = city_options(city, opts)
    key = repr((city.get("name"), city["x"], city["y"], city["radius"], city.get("seed"), city.get("map"),
                [o[k] for k in ("freeways", "block_m", "downtown", "normal", "collector_every", "core_roads",
                                "neighbourhood_roads", "canals")],
                [(a.get("center"), a.get("heading"), a.get("type")) for a in city.get("airports") or []],
                [(y.get("frame"), y.get("L"), y.get("D")) for y in city.get("shipyards") or []]))
    if key not in _FW_CACHE:
        if len(_FW_CACHE) > 32:
            _FW_CACHE.clear()
        _FW_CACHE[key] = freeways.plan(city, o)
    return _FW_CACHE[key]


def freeway_report(city: dict, opts: dict | None = None) -> dict:
    """RoadNetworkValidator over the city's freeways, ramps, frontage roads and its streets."""
    from . import freeways
    fw = freeway_plan(city, opts)
    if not fw.lines:
        return {"interchanges": [], "validation": {"ok": True, "checked": 0}}
    nodes, edges = city_graph(city, opts)
    streets = [{"name": f"street {a}-{b}", "cls": "street", "kind": k, "half": HALF.get(k, 500.0),
                "points": [(*nodes[a][:2], 0.0), (*nodes[b][:2], 0.0)]} for a, b, k, *_ in edges]
    lines = fw.lines + streets
    rep = freeways.validate(lines, [(ln["points"], ln["half"]) for ln in lines])
    return {"interchanges": fw.interchanges, "validation": rep}


def sidewalks(nodes: dict, edges: list) -> tuple:
    """SidewalkGenerator, pedestrian corridors: a sidewalk spline on both sides of every city street whose
    mesh has none (NO_SIDEWALK), offset by the road's half width + half the sidewalk, stopping at the
    junction mouths. Returns (nodes, edges) to merge into a Paths actor."""
    out_n, out_e = {}, []
    for a, b, kind, *_ in edges:
        if kind not in NO_SIDEWALK or a not in nodes or b not in nodes:
            continue
        (ax, ay), (bx, by) = nodes[a][:2], nodes[b][:2]
        L = math.hypot(bx - ax, by - ay)
        if L < 2 * MOUTH_CM + 500:
            continue
        ux, uy = (bx - ax) / L, (by - ay) / L
        off = HALF[kind] + HALF["SW"]
        for side in (1, -1):
            nx, ny = -uy * off * side, ux * off * side
            k0, k1 = ("sw", a, b, side, 0), ("sw", a, b, side, 1)
            out_n[k0] = [ax + ux * MOUTH_CM + nx, ay + uy * MOUTH_CM + ny]
            out_n[k1] = [bx - ux * MOUTH_CM + nx, by - uy * MOUTH_CM + ny]
            out_e.append([k0, k1, "SW"])
    return out_n, out_e


APRON_M = 8.0  # wider than this a lot's front path is a plaza slab, not a spline


def path_kind(width: float) -> str:
    return "DD" if width >= 5.0 else "DW" if width >= 3.0 else "WK"


def paths_graph(rows: list, kinds: tuple = ("WK", "DW", "DD")) -> tuple:
    """FrontageConnections: the previewed lots' walkways / driveways ([.., paths [[width_m, flat xyz]]]) as
    spline edges of these kinds (WK walkway, DW single, DD double driveway). Plaza aprons stay slabs."""
    nodes, edges = {}, []
    for k, row in enumerate(rows):
        for j, (width, flat) in enumerate(row[10] if len(row) > 10 else []):
            if width > APRON_M or len(flat) < 6 or path_kind(width) not in kinds:
                continue
            pts = [flat[i:i + 2] for i in range(0, len(flat) - 2, 3)]
            a, b = ("p", k, j, 0), ("p", k, j, 1)
            nodes[a], nodes[b] = list(pts[0]), list(pts[-1])
            edges.append([a, b, path_kind(width), [list(p) for p in pts[1:-1]]])
    return nodes, edges


def paths_actor(city: dict, opts: dict | None, rows: list, ground, folder: str, wet=None) -> dict:
    """PedestrianNetwork: the city's '<City> Paths' spline actor: its missing sidewalks and every walkway."""
    cn, ce = city_graph(city, opts, wet)
    ce = [e for e in ce if len(e) < 4 or not e[3]]  # no sidewalk spline along a bridge
    sn, se = sidewalks(cn, ce)
    for k, line in enumerate(canal_lines(city, opts)):  # canal-side walkways on both quays
        for p, q in zip(line, line[1:]):
            L = math.dist(p, q) or 1.0
            ux, uy = (q[0] - p[0]) / L, (q[1] - p[1]) / L
            off = HALF["CN"] + HALF["SW"]
            for side in (1, -1):
                a, b = ("cw", k, tuple(p), side, 0), ("cw", k, tuple(p), side, 1)
                sn[a] = [p[0] - uy * off * side + ux * MOUTH_CM, p[1] + ux * off * side + uy * MOUTH_CM]
                sn[b] = [q[0] - uy * off * side - ux * MOUTH_CM, q[1] + ux * off * side - uy * MOUTH_CM]
                se.append([a, b, "SW"])
    pn, pe = paths_graph(rows, ("WK",))
    return build_actor({**sn, **pn}, se + pe, ground, folder)


FILLET_CM = 900.0  # private street corners: a 9 m kerb radius, not a spline's wide arc


def fillet(pts: list, radius: float = FILLET_CM) -> list:
    """A polyline with every real corner (turn > 20 degrees) replaced by a short arc of `radius` (or less on a
    short leg), so a spline through it turns tight at the corner and runs straight between corners. A closed
    ring (first point == last) is restarted mid-side so every one of its corners is rounded too."""
    pts = [tuple(p[:2]) for p in pts]
    closed = len(pts) > 3 and math.dist(pts[0], pts[-1]) < 1
    if closed:
        ring = pts[:-1]
        mid = ((ring[0][0] + ring[1][0]) / 2, (ring[0][1] + ring[1][1]) / 2)
        pts = [mid] + ring[1:] + ring[:1] + [mid]
    out = [pts[0]]
    for a, b, c in zip(pts, pts[1:], pts[2:]):
        d1, d2 = (b[0] - a[0], b[1] - a[1]), (c[0] - b[0], c[1] - b[1])
        l1, l2 = math.hypot(*d1), math.hypot(*d2)
        if l1 < 1 or l2 < 1:
            continue
        u1, u2 = (d1[0] / l1, d1[1] / l1), (d2[0] / l2, d2[1] / l2)
        turn = math.acos(max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1])))
        if turn < math.radians(20):
            out.append(b)
            continue
        r = min(radius, 0.4 * l1, 0.4 * l2)
        t = r * math.tan(turn / 2)  # distance from the corner to where the arc starts and ends
        p0, p3 = (b[0] - u1[0] * t, b[1] - u1[1] * t), (b[0] + u2[0] * t, b[1] + u2[1] * t)
        for s in (0.0, 1 / 3, 2 / 3, 1.0):  # a quadratic through the corner: tangent to both legs
            out.append(((1 - s) ** 2 * p0[0] + 2 * (1 - s) * s * b[0] + s * s * p3[0],
                        (1 - s) ** 2 * p0[1] + 2 * (1 - s) * s * b[1] + s * s * p3[1]))
    out.append(pts[-1])
    return [[round(x), round(y)] for x, y in out]


def access_actor(city: dict, rows: list, ground, folder: str) -> dict:
    """Private access network: the city's '<City> Access' spline actor: every subdivision / complex street
    (the city doc's private streets) and every driveway (single DW, double DD), graded like roads."""
    nodes, edges = paths_graph(rows, ("DW", "DD"))
    for k, s in enumerate((city.get("private") or {}).get("streets") or []):
        if len(s) < 2:
            continue
        s = fillet(s)
        a, b = ("pr", k, 0), ("pr", k, 1)
        nodes[a], nodes[b] = list(s[0]), list(s[-1])
        edges.append([a, b, "PR", [list(p) for p in s[1:-1]]])
    return build_actor(nodes, edges, ground, folder)


COLLECTOR_ROWS = 0.5  # of the suburbs' rows of blocks, how many share one winding street from block to block
SUPERBLOCK_SHARE = 0.7  # of the suburbs' whole collector cells, how many are one subdivision with no public street inside
LOT_W_CM, LOT_D_CM = 1600.0, 3500.0  # a lot: 16 m frontage, up to 35 m deep (two back to back in a block)


def city_layout(city: dict, opts: dict | None = None, wet=None, ground=None, keep: list | None = None,
                water: dict | None = None) -> dict:
    """BlockGenerator + neighbourhoods: every block of the city's street grid becomes either plain street-fronting
    lots (parcels) or one neighbourhood (neighbourhoods.TYPES: subdivisions with private streets, apartment
    complexes with parking), picked per block from the city's seed and weighted by its district ring.
    Lots: [[x, y, yaw, width_cm, depth_cm, front x, front y, role ('' = any layer), footprint]]."""
    from . import neighbourhoods as nb
    from . import parcels
    o = city_options(city, opts)
    nodes, edges = city_graph(city, opts, wet)
    wet = water_of(city, o, wet)  # canals are water too: no lot in them
    rnd = random.Random(f"lots:{city.get('seed', 1)}")
    fw = freeway_plan(city, o)
    out = {"lots": [], "streets": [], "parking": [], "stalls": [], "pads": [], "trees": [], "fences": [], "pools": [], "hoods": {}}
    down, normal = o["downtown"], max(o["normal"], o["downtown"])
    for area in parcels.block_areas(nodes, edges, HALF):
        cx, cy = sum(p[0] for p in area) / len(area), sum(p[1] for p in area) / len(area)
        share = zone_share(city, cx, cy)
        ring = "core" if share <= down else "normal" if share <= normal else "housing"
        _, _, ux, uy, L, W = parcels._obb(area)
        if abs(ux) < abs(uy):  # the axis neighbourhoods' 'collector' street runs along (the same for every block)
            ux, uy = -uy, ux
        row = round((cx * uy - cy * ux) * (1 if ux > 0 else -1) / 6000)  # blocks side by side along it share a row
        kind = nb.pick(ring, rnd) if ring != "core" and min(L, W) >= nb.MIN_SIDE else "parcels"
        if fw.lines and fw.blocked(cx, cy, 0.5 * max(L, W)):
            kind = "parcels"
        elif ring != "core" and min(L, W) >= nb.BIG_SIDE:
            kind = "collector"  # a superblock: plain lots would be landlocked in its middle
        elif ring == "housing" and kind != "parcels" and \
                random.Random(f"row:{city.get('seed', 1)}:{row}").random() < COLLECTOR_ROWS:
            kind = "collector"  # a whole row of suburb blocks: the one winding street runs on through them all  # a block the freeway cuts gets plain lots, the ones clear of it kept
        out["hoods"][kind] = out["hoods"].get(kind, 0) + 1
        if kind == "parcels":
            out["lots"] += [[x, y, yaw, w, d, fx, fy, "", False]
                            for x, y, yaw, w, d, _poly, fx, fy in parcels.lots_in(area, LOT_W_CM, LOT_D_CM, rnd)]
            continue
        g = nb.generate(area, kind, rnd)
        for key in ("lots", "streets", "parking", "stalls", "pads", "trees", "fences", "pools"):
            out[key] += g[key]
    if fw.lines:  # right of way: nothing built under or beside a freeway or ramp
        out["lots"] = [r for r in out["lots"] if not fw.blocked(r[0], r[1], 0.5 * max(r[3], r[4]))]
        for key in ("parking", "stalls", "pads", "trees", "fences", "pools"):
            out[key] = [r for r in out[key] if not fw.blocked(r[0], r[1])]
    if wet:  # nothing is built in the water
        for key in ("lots", "parking", "stalls", "pads", "trees", "fences", "pools"):
            out[key] = [r for r in out[key] if not wet(r[0], r[1])]
        out["streets"] = [s for s in out["streets"] if not any(wet(p[0], p[1]) for p in s)]
    if city.get("airports"):  # FAA land-use compatibility: no homes in an airport's noise zone, industry instead
        from . import airport
        noisy = lambda x, y: any(airport.in_noise(a, x, y) or airport.inside(a, x, y, 2000) for a in city["airports"])
        kept = []
        for r in out["lots"]:
            if not noisy(r[0], r[1]):
                kept.append(r)
            elif not (len(r) > 7 and r[7] in ("house", "apartment", "mansion")) and not (len(r) > 8 and r[8] is True):
                kept.append(r[:7] + ["warehouse", False])  # a plain parcel: logistics and light industry
        out["lots"] = kept
        out["streets"] = [s for s in out["streets"] if not any(noisy(p[0], p[1]) for p in s)]
    out["grounds"], out["plan"], out["water"], out["waterfront"] = [], None, [], None
    ring_of, industrial = zoning(city, o)
    if o["waterfront"] == "yes" and water and water.get("sea") is not None and ground:
        # waterfront first: the shore's quays, marinas, beaches and ports take their land before the services
        from . import waterfront
        pop = round(len(out["lots"]) * 9)  # a rough count for the port / ferry thresholds; urban.plan counts properly
        wf = waterfront.plan(city, water, out["lots"], ring_of, industrial, ground, nodes, pop)
        out["lots"], out["water"] = wf["lots"], wf["rows"]
        out["waterfront"] = {k: wf[k] for k in ("segments", "developments", "shoreline_m", "report", "shipyards")}
    if o["planner"] == "yes" and out["lots"]:
        from . import urban
        barriers = [(p, q) for ln in fw.lines for p, q in zip(ln["points"], ln["points"][1:])]
        got = urban.plan(out["lots"], nodes, edges, ring_of, ground, [tuple(i["at"]) for i in fw.interchanges],
                         keep, (city["x"], city["y"]), city["radius"], industrial, barriers)
        out["lots"] = got["lots"]
        out["parking"] += got["parking"]
        out["stalls"] += got["stalls"]
        out["grounds"], out["plan"] = got["grounds"], got["plan"]
    return out


def city_lots(city: dict, opts: dict | None = None) -> list:
    return city_layout(city, opts)["lots"]


def gate(nodes: dict, city: dict, toward: tuple):
    """The city's street node furthest out in the direction of another city."""
    dx, dy = toward[0] - city["x"], toward[1] - city["y"]
    L = math.hypot(dx, dy) or 1.0
    streets = [n for n in nodes if isinstance(n[0], int)]  # never another highway's stub
    return max(streets, key=lambda n: ((nodes[n][0] - city["x"]) * dx + (nodes[n][1] - city["y"]) * dy) / L)


def links(cities: list) -> list:
    """Highways: the shortest tree joining every city (Prim)."""
    if len(cities) < 2:
        return []
    done, out = {0}, []
    while len(done) < len(cities):
        a, b = min(((i, j) for i in done for j in range(len(cities)) if j not in done),
                   key=lambda p: math.hypot(cities[p[0]]["x"] - cities[p[1]]["x"], cities[p[0]]["y"] - cities[p[1]]["y"]))
        done.add(b)
        out.append((a, b))
    return out


# ------------------------------------------------------------------ splines
def _point(i, x, y, z, yaw, w, mesh="", lifted=False, piece=""):
    """A control point. lifted: on a bridge / ramp (never shapes the ground). piece: a mesh path that sits
    on the point (a trumpet loop, a cloverleaf)."""
    live = "False" if lifted else "True"
    lines = [f"Width={w:.1f}", f"bRaiseTerrain={live}", f"bLowerTerrain={live}", "SideFalloff=800.000000",
             "EndFalloff=1000.000000"] + ([] if mesh else ["SegmentMeshOffset=50.0"])
    if piece:
        name = piece.rsplit("/", 1)[-1]
        lines += [f"Mesh=\"/Script/Engine.StaticMesh'{piece}.{name}'\"", "MeshScale=(X=1.000000,Y=1.000000,Z=1.000000)"]
        mesh = name
    return {"i": i, "x": x, "y": y, "z": z, "pitch": 0.0, "yaw": yaw, "roll": 0.0, "w": w, "side": 800.0,
            "mesh": mesh, "raise": not lifted, "lines": lines}


def _segment(i, a, b, kind, folder, lifted=False):
    live = "False" if lifted else "True"  # bridges and ramps never shape the ground under them
    lines = [f"bRaiseTerrain={live}", f"bLowerTerrain={live}"]
    path = ROAD_MESH.get(kind, "")
    mesh = path.rsplit("/", 1)[-1]
    if mesh:
        lines.insert(0, f"SplineMeshes(0)=(Mesh=\"/Script/Engine.StaticMesh'{folder}/{path}.{mesh}'\",bScaleToWidth=False)")
    return {"i": i, "a": a, "b": b, "ta": 0.0, "tb": 0.0, "mesh": mesh, "raise": not lifted, "lines": lines}


def build_actor(nodes: dict, edges: list, ground, folder: str, pieces: dict | None = None) -> dict:
    """Street graph -> spline points and segments. A node with 3+ streets becomes a junction (a
    pending piece with a mouth point on every arm); edges may carry 'via' points (highways):
    [x, y] or [x, y, lift_cm, kind of the segment that starts there] for bridges and ramps.
    pieces: {node: mesh path} puts a piece (trumpet loop, cloverleaf) on an end point."""
    pieces = pieces or {}

    def high(x, y):  # a bridge / ramp point sits over the HIGHEST ground near it, never in a hill between samples
        return max(ground(x + dx, y + dy) for dx, dy in ((0, 0), (1500, 0), (-1500, 0), (0, 1500), (0, -1500)))

    adj: dict = {}
    for e in edges:
        adj.setdefault(e[0], []).append(e)
        adj.setdefault(e[1], []).append(e)
    pts, segs, ends = [], [], {}
    glim: dict = {}  # point -> steepest grade of the roads through it (the flattest road wins)
    ids = iter(range(1 << 30))

    def angle(p, q):
        return math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))

    def first_step(e, n):  # the next position along e when leaving node n
        via = e[3] if len(e) > 3 else []
        if via:
            return via[0] if e[0] == n else via[-1]
        return nodes[e[1] if e[0] == n else e[0]]

    def turn(n, arms):  # degrees a 2-arm node turns (0 = straight on)
        x, y = nodes[n][:2]
        (ax, ay), (bx, by) = first_step(arms[0], n)[:2], first_step(arms[1], n)[:2]
        a1, a2 = math.atan2(ay - y, ax - x), math.atan2(by - y, bx - x)
        return 180.0 - abs((math.degrees(a1 - a2) + 180.0) % 360.0 - 180.0)

    for n, arms in adj.items():
        x, y = nodes[n][:2]
        lifted_node = len(nodes[n]) > 2 and float(nodes[n][2]) > 0
        # a junction piece where 3+ streets meet, and an ELBOW piece where a street turns sharply (a square
        # corner): a plain spline point would smooth a 90 degree turn into a wide arc that loops out
        corner = len(arms) == 2 and not lifted_node and turn(n, arms) > CORNER_DEG
        if len(arms) >= 3 or corner:
            w = max(HALF[e[2]] for e in arms) + 300.0
            j = _point(next(ids), x, y, ground(x, y), 0.0, w, JUNCTION)
            pts.append(j)
            for e in arms:
                q = first_step(e, n)
                a = angle((x, y), q)
                mx, my = x + MOUTH_CM * math.cos(math.radians(a)), y + MOUTH_CM * math.sin(math.radians(a))
                m = _point(next(ids), mx, my, ground(mx, my), a, HALF[e[2]])
                pts.append(m)
                segs.append(_segment(next(ids), j["i"], m["i"], "", folder))
                ends[(id(e), n)] = m["i"]
                for i in (j["i"], m["i"]):
                    glim[i] = min(glim.get(i, 1.0), GRADE.get(e[2], 0.08))
        else:
            steps = [first_step(e, n) for e in arms]
            yaw = angle(steps[0], steps[1]) if len(steps) == 2 else angle((x, y), steps[0])
            up = float(nodes[n][2]) if len(nodes[n]) > 2 else 0.0  # a ramp end up on a bridge
            p = _point(next(ids), x, y, (high(x, y) if up > 0 else ground(x, y)) + up, yaw, max(HALF[e[2]] for e in arms),
                       piece=pieces.get(n, ""), lifted=up > 0)
            pts.append(p)
            for e in arms:
                ends[(id(e), n)] = p["i"]
    chains = []
    for e in edges:
        via = e[3] if len(e) > 3 else []
        chain = [ends[(id(e), e[0])]]
        chains.append(chain)
        line = [nodes[e[0]]] + via + [nodes[e[1]]]
        lift = {chain[0]: 0.0}
        kinds = [e[2]]  # the kind of the segment starting at each chain point
        for k, v in enumerate(via, start=1):
            x, y = v[0], v[1]
            up = float(v[2]) if len(v) > 2 else 0.0
            p = _point(next(ids), x, y, (high(x, y) if up > 0 else ground(x, y)) + up, angle(line[k - 1], line[k + 1]),
                       HALF[e[2]], lifted=up > 0)
            pts.append(p)
            chain.append(p["i"])
            lift[p["i"]] = up
            kinds.append(v[3] if len(v) > 3 and v[3] else e[2])
        chain.append(ends[(id(e), e[1])])
        lift[chain[0]] = float(nodes[e[0]][2]) if len(nodes[e[0]]) > 2 else 0.0
        lift[chain[-1]] = float(nodes[e[1]][2]) if len(nodes[e[1]]) > 2 else 0.0
        for k, (a, b) in enumerate(zip(chain, chain[1:])):
            segs.append(_segment(next(ids), a, b, kinds[k], folder, lifted=lift.get(a, 0) > 0 or lift.get(b, 0) > 0))
            for i in (a, b):
                glim[i] = min(glim.get(i, 1.0), GRADE.get(kinds[k], 0.08))
    by_i = {p["i"]: p for p in pts}
    for chain in chains:  # viaducts, decks and flyovers run level, never riding the hills under them
        level_decks([by_i[i] for i in chain])
    for s in segs:
        st.retangent(by_i, s)
    st.drape(pts, segs, {p["i"]: p["z"] for p in pts}, iters=4, max_grade=glim, max_cut=MAX_CUT_CM)
    return {"points": pts, "segments": segs}


def detach(actor: dict) -> None:
    """A freeway or highway actor that never shapes the landscape: its splines carry their meshes only (the
    plugin's own heightmap is already cut to them). City streets keep shaping it."""
    for x in actor["points"] + actor["segments"]:
        if "raise" in x:
            x["raise"] = False
        x["lines"] = [l.replace("bRaiseTerrain=True", "bRaiseTerrain=False").replace("bLowerTerrain=True", "bLowerTerrain=False")
                      for l in x.get("lines") or []]


DECK_WINDOW_CM = 15000.0  # a deck's height is smoothed over this much road each way


def level_decks(ps: list) -> None:
    """Each run of lifted points along one road (a viaduct, a deck, a flyover) on its own smooth line instead of a
    fixed lift over every bump of the ground (decks rode the hills, Nov 12 2026). The line is the mean, over
    DECK_WINDOW_CM each way, of the highest planned height within DECK_WINDOW_CM: never under any point's planned
    clearance, since each point's height is in every window around it. Toward the run's ends it eases back to the
    planned heights, so the ramps down still land where they were planned."""
    k = 0
    while k < len(ps):
        if ps[k]["raise"]:
            k += 1
            continue
        j = k
        while j < len(ps) and not ps[j]["raise"]:
            j += 1
        run = ps[k:j]
        k = j
        if len(run) < 3:
            continue
        s = [0.0]
        for a, b in zip(run, run[1:]):
            s.append(s[-1] + math.hypot(b["x"] - a["x"], b["y"] - a["y"]))
        base = [p["z"] for p in run]
        W = DECK_WINDOW_CM
        env = [max(base[m] for m in range(len(run)) if abs(s[m] - s[n]) <= W) for n in range(len(run))]
        for n, p in enumerate(run):
            near = [env[m] for m in range(len(run)) if abs(s[m] - s[n]) <= W]
            ease = min(1.0, min(s[n], s[-1] - s[n]) / W)
            p["z"] = base[n] + (sum(near) / len(near) - base[n]) * ease


def _seg_cross(p1, p2, q1, q2):
    """Where segment p1-p2 crosses q1-q2, as the share along p (None if they don't)."""
    d = (p2[0] - p1[0]) * (q2[1] - q1[1]) - (p2[1] - p1[1]) * (q2[0] - q1[0])
    if abs(d) < 1e-9:
        return None
    t = ((q1[0] - p1[0]) * (q2[1] - q1[1]) - (q1[1] - p1[1]) * (q2[0] - q1[0])) / d
    u = ((q1[0] - p1[0]) * (p2[1] - p1[1]) - (q1[1] - p1[1]) * (p2[0] - p1[0])) / d
    return t if 0 < t < 1 and 0 < u < 1 else None


def overpass_via(line: list, streets: list, kind: str) -> tuple:
    """A highway polyline's via points with a bridge over every street it crosses: ramp up 40 m, deck
    over the street, ramp down 40 m (crossings closer than 150 m share one bridge). Returns (via, count)."""
    cum = [0.0]
    for p, q in zip(line, line[1:]):
        cum.append(cum[-1] + math.dist(p[:2], q[:2]))
    hits = []
    for k, (p, q) in enumerate(zip(line, line[1:])):
        for a, b in streets:
            t = _seg_cross(p, q, a, b)
            if t is not None:
                hits.append(cum[k] + t * (cum[k + 1] - cum[k]))
    hits.sort()
    spans = []
    for s in hits:  # merge close crossings into one longer bridge
        if spans and s - spans[-1][1] < 15000:
            spans[-1][1] = s
        else:
            spans.append([s, s])
    total = cum[-1]
    spans = [sp for sp in spans if sp[0] > 40000 and sp[1] < total - 40000]  # never in the gate interchanges

    def at(s):
        for k in range(len(cum) - 1):
            if cum[k] <= s <= cum[k + 1]:
                t = (s - cum[k]) / ((cum[k + 1] - cum[k]) or 1)
                return [line[k][0] + (line[k + 1][0] - line[k][0]) * t, line[k][1] + (line[k + 1][1] - line[k][1]) * t]
        return list(line[-1][:2])

    keep = [(cum[k], list(line[k][:2])) for k in range(1, len(line) - 1)
            if not any(sp[0] - 8000 < cum[k] < sp[1] + 8000 for sp in spans)]
    for a, b in spans:
        deck, ramp = structure_kinds(kind)
        keep += [(a - 7000, at(a - 7000) + [0.0, ramp]), (a - 3000, at(a - 3000) + [BRIDGE_LIFT_CM, deck]),
                 (b + 3000, at(b + 3000) + [BRIDGE_LIFT_CM, ramp]), (b + 7000, at(b + 7000) + [0.0, kind])]
    keep.sort(key=lambda r: r[0])
    return [v for _, v in keep], len(spans)


def interchange(kind: str, end: list, toward: list, tag: tuple, hw: str) -> tuple:
    """Real geometry for where a highway meets a city, made of our ramp and bridge pieces:
    (highway profile [[distance from end, lift cm]], ramp nodes, ramp edges, cross road [x0, x1]).
    The highway bridges over a cross road 180 m out from its end; the cross road belongs to the city's
    own streets (generate joins both its ends to them). Every ramp touches the road edges it links:
    loops are tangent to the highway and the cross road (centre = radius + that road's half width),
    start at the highway's height there and come down to the cross road.
    cloverleaf: 4 loops + 4 slip ramps; trumpet: 1 loop + 1 slip ramp; diamond: 4 straight slip ramps."""
    ux, uy = toward[0] - end[0], toward[1] - end[1]
    ul = math.hypot(ux, uy) or 1.0
    ux, uy = ux / ul, uy / ul
    nx, ny = -uy, ux
    D, R, deck = 18000.0, 5500.0, 6000.0  # crossing 180 m out; loop radius 55 m; deck +-60 m around it
    lift = BRIDGE_LIFT_CM
    h_hw, h_x, h_ramp = HALF[hw], HALF["4L"], HALF["ER"]
    oa, ob = R + h_x, R + h_hw  # loop centre: off the cross road (along the highway), off the highway
    cx, cy = end[0] + ux * D, end[1] + uy * D
    P = lambda a, b, up=0.0: [cx + ux * a + nx * b, cy + uy * a + ny * b] + ([up] if up else [])

    def deck_lift(a):  # the highway's height a cm from the crossing: deck, then a 60 m ramp down
        return lift * max(0.0, min(1.0, 1 - (abs(a) - deck) / deck))

    profile = [[D - 2 * deck, 0.0], [D - deck, lift], [D + deck, lift], [D + 2 * deck, 0.0]]
    nodes, edges = {}, []
    sides = (1,) if kind == "trumpet" else (-1, 1)
    reach = ob + R + 6000.0  # the cross road runs past the loops and slip ramps on each side
    cross = [P(0, -reach if kind != "trumpet" else -h_hw - 2000), P(0, reach)]
    quads = [(i, j) for i in (-1, 1) for j in sides]
    loops = quads if kind == "cloverleaf" else ([(1, 1)] if kind == "trumpet" else [])
    slips = quads if kind in ("cloverleaf", "diamond") else ([(-1, 1)] if kind == "trumpet" else [])
    for i, j in loops:  # 270 deg loop: tangent to the highway edge, down to the cross road edge
        qa, qb = i * oa, j * ob
        a0 = math.atan2(-j, 0.0)  # the point facing the highway
        sweep = 1.5 * math.pi * (1 if i * j > 0 else -1)
        top = deck_lift(qa)
        arc = []
        for k in range(13):
            t = a0 + sweep * k / 12
            arc.append(P(qa + R * math.cos(t), qb + R * math.sin(t), top * (1 - k / 12)))
        a, b = tag + ("l", i, j, 0), tag + ("l", i, j, 1)
        nodes[a], nodes[b] = arc[0] if len(arc[0]) > 2 else arc[0] + [0.0], arc[-1][:2]
        edges.append([a, b, "ER", [p + ["ER"] if len(p) > 2 else p + [0.0, "ER"] for p in arc[1:-1]]])
    for i, j in slips:  # slip ramp: from the highway edge beyond the deck to the cross road edge, bowed out
        far = 2 * deck + R
        h = P(i * far, j * (h_hw + h_ramp))
        x = P(i * (h_x + h_ramp), j * (ob + R))
        mid = P(i * (far + h_x) * 0.55, j * (h_hw + ob + R) * 0.6)
        a, b = tag + ("s", i, j, 0), tag + ("s", i, j, 1)
        nodes[a], nodes[b] = h, x
        edges.append([a, b, "2L", [mid + [0.0, "2L"]]])
    return profile, nodes, edges, cross
    return profile, nodes, edges


def generate(cities: list, ground, folder: str, seed: int = 1, opts: dict | None = None, keep: list | None = None,
             wet=None, bridges: list | None = None, airports: list | None = None,
             airport_builds: dict | None = None) -> dict:
    """{label: {points, segments}}: one '<City> Roads' actor per city and one 'Highways' actor.
    A highway ends 40 m outside its city's gate street, where the city's own stub road ends; there it
    meets the city through the chosen interchange (direct, a trumpet loop, a cloverleaf piece, or a
    stack: an elevated deck on ramps), and it bridges over every street it crosses on the way."""
    o = options(opts)
    graphs = [city_graph(c, o, wet, bridges) for c in cities]
    rnd = random.Random(seed)
    hw_nodes, hw_edges, pieces = {}, [], {}
    streets = [(n[a][:2], n[b][:2]) for n, e in graphs for a, b, *_ in e]
    for a, b in links(cities):
        ends = []
        for me, other in ((a, b), (b, a)):
            nodes, edges = graphs[me]
            g = gate(nodes, cities[me], (cities[other]["x"], cities[other]["y"]))
            gx, gy = nodes[g]
            dx, dy = cities[other]["x"] - gx, cities[other]["y"] - gy
            L = math.hypot(dx, dy) or 1.0
            stub = ("gate", other)
            nodes[stub] = [gx + dx / L * 4000, gy + dy / L * 4000]
            edges.append([g, stub, o["highway"]])
            ends.append(nodes[stub])
        (x0, y0), (x1, y1) = ends
        L = math.hypot(x1 - x0, y1 - y0)
        steps = max(1, int(L // 20000))
        nx, ny = -(y1 - y0) / (L or 1), (x1 - x0) / (L or 1)
        bend = rnd.uniform(-1, 1) * o["highway_curve"] * L  # one gentle curve per highway
        via = [[x0 + (x1 - x0) * t + nx * bend * math.sin(math.pi * t), y0 + (y1 - y0) * t + ny * bend * math.sin(math.pi * t)]
               for t in (k / steps for k in range(1, steps))]
        box = cities[a].get("map")
        if box:  # its curve stays on the map
            via = [[min(max(v[0], box[0] + 5000), box[2] - 5000), min(max(v[1], box[1] + 5000), box[3] - 5000)] for v in via]
        if keep:  # a highway goes round a keep zone, never through it
            line, dense = [ends[0]] + via + [ends[1]], []
            for p, q in zip(line, line[1:]):
                n = max(1, int(math.dist(p, q) // 4000))
                dense += [[p[0] + (q[0] - p[0]) * i / n, p[1] + (q[1] - p[1]) * i / n] for i in range(n)]
            dense = dense[1:]
            if any(in_keep(keep, *v, 6000) for v in dense):
                via = [push_out(keep, v[0], v[1], 8000) for v in dense][::2]
        if o["overpasses"] == "yes":
            via, _ = overpass_via([ends[0]] + via + [ends[1]], streets, o["highway"])
        if o["interchange"] == "stack" and L > 70000:  # rise onto an elevated deck into each gate
            line = [ends[0]] + [v[:2] for v in via] + [ends[1]]
            heads = []
            for end, nxt in ((ends[0], line[1]), (ends[1], line[-2])):
                ux, uy = nxt[0] - end[0], nxt[1] - end[1]
                ul = math.hypot(ux, uy) or 1.0
                heads.append(lambda d, h, k, end=end, ux=ux / ul, uy=uy / ul: [end[0] + ux * d, end[1] + uy * d, h, k])
            hw = o["highway"]
            near = [heads[0](1500, 0.0, "ER"), heads[0](6000, 600.0, "ED"), heads[0](14000, 600.0, "ER"),
                    heads[0](22000, 0.0, hw)]
            far = [heads[1](22000, 0.0, "ER"), heads[1](14000, 600.0, "ED"), heads[1](6000, 600.0, "ER"),
                   heads[1](1500, 0.0, hw)]
            via = near + [v for v in via if math.dist(v[:2], ends[0]) > 26000 and math.dist(v[:2], ends[1]) > 26000] + far
        if o["interchange"] in ("cloverleaf", "trumpet", "diamond") and L > 90000:
            # real ramps and a bridge at each city end, all from our ramp / bridge / road pieces
            line = [ends[0]] + [v[:2] for v in via] + [ends[1]]
            heads = []
            for k, (end, nxt) in enumerate(((ends[0], line[1]), (ends[1], line[-2]))):
                profile, inodes, iedges, cross = interchange(o["interchange"], end, nxt, ("ic", a, b, k), o["highway"])
                hw_nodes.update(inodes)
                hw_edges.extend(iedges)
                # the cross road is a city street: both ends join the city's nearest streets
                cnodes, cedges = graphs[(a, b)[k]]
                city_nodes = [n for n in cnodes if isinstance(n[0], int)]  # not `streets`: that one is the overpass list
                ends_x = [("ic", a, b, k, "x0"), ("ic", a, b, k, "x1")]
                cnodes[ends_x[0]], cnodes[ends_x[1]] = cross
                cedges.append([ends_x[0], ends_x[1], "4L"])
                for e in ends_x:
                    if city_nodes:
                        near = min(city_nodes, key=lambda n: math.dist(cnodes[n][:2], cnodes[e][:2]))
                        cedges.append([e, near, o["neighbourhood_roads"]])
                ux, uy = nxt[0] - end[0], nxt[1] - end[1]
                ul = math.hypot(ux, uy) or 1.0
                heads.append([[end[0] + ux / ul * d, end[1] + uy / ul * d, up, None] for d, up in profile])
            clear = 32000  # the highway's own via points give way to the interchange profile
            via = heads[0] + [v for v in via if math.dist(v[:2], ends[0]) > clear and math.dist(v[:2], ends[1]) > clear] + heads[1][::-1]
            # each segment's piece from the lift at its two ends: deck, ramp, or plain highway
            lifts = [0.0] + [v[2] if len(v) > 2 else 0.0 for v in via] + [0.0]
            for k, v in enumerate(via):
                if len(v) > 3 and v[3] is None:
                    up, nxt_up = lifts[k + 1], lifts[k + 2]
                    deck, ramp = structure_kinds(o["highway"])
                    v[3] = deck if up > 0 and nxt_up > 0 else (ramp if up > 0 or nxt_up > 0 else o["highway"])
        hw_nodes[("hw", a, b, 0)], hw_nodes[("hw", a, b, 1)] = ends
        hw_edges.append([("hw", a, b, 0), ("hw", a, b, 1), o["highway"], via])
    for nodes, edges in graphs:  # a city street never runs into a keep zone
        gone = {n for n, p in nodes.items() if in_keep(keep, p[0], p[1], 2000)}
        edges[:] = [e for e in edges if e[0] not in gone and e[1] not in gone]
    if wet or any(canal_lines(c, o) for c in cities):  # cross roads added after city_graph bridge or stop too
        graphs = [over_water(n, e, water_of(c, o, wet), bridges=bridges) for c, (n, e) in zip(cities, graphs)]
    out = {f"{c['name']} Roads": build_actor(n, e, ground, folder) for c, (n, e) in zip(cities, graphs)}
    for ap in airports or []:  # airports: the airside graph (runways + taxiways) and the landside roads
        from . import airport
        city_nodes = next((n for c, (n, _e) in zip(cities, graphs) if c["name"] == ap["city"]), {})
        city_nodes = {k: v for k, v in city_nodes.items() if isinstance(k, tuple) and len(k) == 2}
        got = airport.build(ap, ground, city_nodes, wet)
        an, ae = got["airside"]
        level = ap.get("level_cm")
        flat = (lambda x, y, z=level: z) if level is not None else ground  # an airfield is graded level
        label = f"{ap['city']} Airport" if ap["type"] != "military" else f"{ap['city']} Airbase"
        out[label] = build_actor(an, ae, flat, folder)
        rn, re_ = got["landside"]
        if re_:
            out[f"{label} Roads"] = build_actor(rn, re_, ground, folder)
        if airport_builds is not None:
            airport_builds[label] = got
    for c in cities:  # graving docks: laid at the dock floor so the spline digs the basin
        if c.get("shipyards"):
            from . import shipyard
            dn, de = {}, []
            for y in c["shipyards"]:
                n_, e_ = shipyard.dock_edges(y)
                dn.update(n_)
                de += e_
            if de:
                out[f"{c['name']} Docks"] = build_actor(dn, de, lambda x, y: ground(x, y) - shipyard.DOCK_DEPTH_CM, folder)
    for c in cities:  # canals: laid at the channel bed so the spline lowers the ground into them
        lines = canal_lines(c, o)
        if lines:
            cn, ce = {}, []
            for k, line in enumerate(lines):
                cn[("cn", k, 0)], cn[("cn", k, 1)] = line[0], line[-1]
                ce.append([("cn", k, 0), ("cn", k, 1), "CN", [list(p) for p in line[1:-1]]])
            out[f"{c['name']} Canals"] = build_actor(cn, ce, lambda x, y: ground(x, y) - CANAL_DEPTH_CM, folder)
    for c in cities:  # the urban freeway layer: its own actor, so a change to it never re-pastes the streets
        fw = freeway_plan(c, o)
        fn, fe = over_water(fw.nodes, fw.edges, wet, bridges=bridges) if wet else (fw.nodes, fw.edges)
        if fe:
            out[f"{c['name']} Freeways"] = build_actor(fn, fe, ground, folder)
    if wet and hw_edges:  # a highway crosses any water on a bridge (a long one if it has to)
        long = {k: 1e9 for k in (*HIGHWAYS, "2L", "ER", "ED", "BR", "BRR")}
        hw_nodes, hw_edges = over_water(hw_nodes, hw_edges, wet, long, bridges)
    if hw_edges:
        out["Highways"] = build_actor(hw_nodes, hw_edges, ground, folder, pieces)
    for _ in range(8):  # again while anything moved: an end may land on a road whose own end just moved
        if not weld_ends(out):
            break
    return out


WELD_REACH_CM = 2600.0  # an open end this close (in plan) to another road is meant to join it
WELD_GRADE = 0.08  # the end's own road eases to the new height at most this steeply
WELD_SNAP_CM = 6000.0  # an end that stops short of every road by less than this is brought up to the nearest one
FREEWAY_LINK_CM = 15000.0  # a freeway that ends this close to a road runs on to it (an urban freeway ending in a field)
FREEWAY_MESHES = {ROAD_MESH[k].split("/")[-1] for k in ("6L", "ED")}
RAMP_MESHES = {ROAD_MESH[k].split("/")[-1] for k in ("ER", "FR")}  # their ends get a connector piece
# a connector's half width at the ramp's end (road_profiles.connector: the gore widens to 10 m, the flare to 10.5 m)
CONNECTOR_HALF = {"SM_Road_RampGore30m": 1000.0, "SM_Road_RampFlare15m": 1050.0}
FLARE_CM = 1500.0  # the flare runs this far on past the ramp's end, to the street's kerb line


def _connector(p: dict, yaw: float, seg: dict, name: str) -> None:
    """Put connector `name` (road_profiles.CONNECTORS) on a ramp's end point p, turned to `yaw` (along the road a
    gore joins, or straight at the street a flare meets: never the ramp's own last kink, which left pieces
    sticking out into the field), in the roads folder its own segment's mesh comes from. The point's yaw is also
    the spline's heading there, so the ramp bends into the piece."""
    import re
    m = re.search(r"StaticMesh'(.+?)/(?:Elevated/)?SM_[^/.']+\.", " ".join(seg.get("lines") or []))
    folder = m.group(1) if m else ""
    p["yaw"] = yaw
    p["mesh"] = name
    p["lines"] = [l for l in p.get("lines") or [] if not l.startswith(("Mesh=", "MeshScale=", "SegmentMeshOffset="))] + \
        [f"Mesh=\"/Script/Engine.StaticMesh'{folder}/{name}.{name}'\"", "MeshScale=(X=1.000000,Y=1.000000,Z=1.000000)"]


def own_run(touch: dict, by: dict, i, limit: float = 8000.0) -> set:
    """The points of the road an open end belongs to, back from the end up to `limit` cm or the first fork: an end
    never 'joins' its own road (its previous piece, a few metres back at its own height, passed as the road it
    meets, so ramps planned 16 m off their street were never moved and the check said they were fine)."""
    run, prev, cur, gone = {i}, None, i, 0.0
    while gone < limit:
        nxt = [s["b"] if s["a"] == cur else s["a"] for s in touch.get(cur, ())]
        nxt = [n for n in nxt if n != prev and n in by]
        if len(nxt) != 1 or len(touch.get(cur, ())) > 2:
            break
        gone += math.hypot(by[nxt[0]]["x"] - by[cur]["x"], by[nxt[0]]["y"] - by[cur]["y"])
        prev, cur = cur, nxt[0]
        run.add(cur)
    return run


def weld_ends(actors: dict) -> int:
    """Every open end of a freeway, ramp, frontage road or highway lands on the road it reaches, at that road's
    height. Each actor is laid and graded on its own, so a ramp planned onto a street came out metres above or
    below it: here the end takes the height of the road under it (the one nearest its own level), and its own
    road eases back from there at WELD_GRADE. Junctions and their mouths never move. Returns the ends moved."""
    every = []
    for label, a in actors.items():
        if label.endswith((" Docks", " Canals", " Paths", " Access")):
            continue
        by = {p["i"]: p for p in a["points"]}
        every += [(label, by[s["a"]], by[s["b"]], s["mesh"].split("/")[-1])
                  for s in a["segments"] if s["mesh"] and s["a"] in by and s["b"] in by]
    half_of = {m.split("/")[-1]: HALF[k] for k, m in ROAD_MESH.items()}
    moved = 0
    for label, a in actors.items():
        if not label.endswith(("Freeways", "Highways")):
            continue
        by = {p["i"]: p for p in a["points"]}
        touch: dict = {}
        for s in a["segments"]:
            for i in (s["a"], s["b"]):
                touch.setdefault(i, []).append(s)
        for i, ss in touch.items():
            p = by.get(i)
            if not p or len(ss) != 1 or not ss[0]["mesh"] or (p["mesh"] and p["mesh"] not in CONNECTOR_HALF):
                continue  # only a road's own open end (one with its connector is welded again: its road may move)
            own = half_of.get(ss[0]["mesh"].split("/")[-1], 650.0)
            mine = own_run(touch, by, i)
            best, near, link = None, None, None  # best: in reach, nearest the end's level; near / link: closest beyond
            for lab2, u, v, mesh in every:
                if lab2 == label and u["i"] in mine and v["i"] in mine:
                    continue
                ux, uy = v["x"] - u["x"], v["y"] - u["y"]
                t = max(0.0, min(1.0, ((p["x"] - u["x"]) * ux + (p["y"] - u["y"]) * uy) / (ux * ux + uy * uy or 1.0)))
                nx, ny, z = u["x"] + ux * t, u["y"] + uy * t, u["z"] + (v["z"] - u["z"]) * t
                d = math.hypot(p["x"] - nx, p["y"] - ny)
                hit = (d, nx, ny, z, half_of.get(mesh, 800.0), mesh, ux, uy)
                if d <= WELD_REACH_CM:
                    if best is None or abs(z - p["z"]) < abs(best[3] - p["z"]):
                        best = hit
                elif d <= WELD_SNAP_CM and (near is None or d < near[0]):
                    near = hit
                elif d <= FREEWAY_LINK_CM and (link is None or d < link[0]):
                    link = hit
            hit = best or near
            if not hit:
                if link and ss[0]["mesh"].split("/")[-1] in FREEWAY_MESHES:
                    # a freeway that stops short in the fields runs on to the road it meets (the next weld puts
                    # its new end inside that road, at its height)
                    d, nx, ny, level, half, *_r = link
                    nid = 1 + max([q["i"] for q in a["points"]] + [s["i"] for s in a["segments"]])
                    q = {**p, "i": nid, "x": nx + (p["x"] - nx) / d * 2000, "y": ny + (p["y"] - ny) / d * 2000, "z": level,
                         "lines": list(p.get("lines") or [])}
                    s = {**ss[0], "i": nid + 1, "a": i, "b": nid, "lines": list(ss[0].get("lines") or [])}
                    a["points"].append(q)
                    a["segments"].append(s)
                    by[nid] = q
                    st.retangent(by, s)
                    moved += 1
                continue
            d, nx, ny, level, half, onto, ux, uy = hit
            # where the end sits: inside the road it joins, its edge on that road's edge (never beside it with grass
            # between, Nov 12 2026). A ramp end gets its own piece: running along the road (a merge or a diverge)
            # it stops beside it and its gore widens out to the road's edge line; meeting it head on (a loop's foot,
            # a diamond ramp at the cross street) it ends on the road's edge and its flare meets the kerb line
            piece = ""
            tgt = max(0.0, half - own)
            if ss[0]["mesh"].split("/")[-1] in RAMP_MESHES:
                o = by[ss[0]["b"] if ss[0]["a"] == i else ss[0]["a"]]
                rx, ry = p["x"] - o["x"], p["y"] - o["y"]
                along = abs(rx * ux + ry * uy) / ((math.hypot(rx, ry) * math.hypot(ux, uy)) or 1.0) > 0.7
                along = along and onto in FREEWAY_MESHES | STRUCTURES  # a street always gets the flare
                piece = "SM_Road_RampGore30m" if along else "SM_Road_RampFlare15m"
                tgt = half + CONNECTOR_HALF[piece] - 50 if along else half - walk_of(half) + FLARE_CM - 50
            slide = abs(d - tgt) > 50 and d > 1
            if slide:
                p["x"], p["y"] = nx + (p["x"] - nx) / d * tgt, ny + (p["y"] - ny) / d * tgt
            if piece:
                if piece == "SM_Road_RampGore30m":  # along the road, the way the ramp was heading
                    sgn = 1.0 if rx * ux + ry * uy >= 0 else -1.0
                    yaw = math.degrees(math.atan2(sgn * uy, sgn * ux))
                else:  # straight at the street
                    yaw = math.degrees(math.atan2(ny - p["y"], nx - p["x"])) if d > 1 else \
                        math.degrees(math.atan2(ry, rx))
                _connector(p, yaw, ss[0], piece)
                st.retangent(by, ss[0])
            if not slide and abs(level - p["z"]) <= 30:
                continue
            p["z"] = level
            moved += 1
            touched, prev, cur = {i}, None, i
            for _ in range(80):  # ease the road back from its end
                nxt = [n for n in (s["b"] if s["a"] == cur else s["a"] for s in touch[cur] if s["mesh"]) if n != prev]
                q = by.get(nxt[0]) if len(nxt) == 1 else None
                if not q or q["mesh"] or len(touch[q["i"]]) < 2 or any(not s["mesh"] for s in touch[q["i"]]):
                    break  # a fork, a piece, a junction's mouth or another end (welded itself): that stays put
                lim = WELD_GRADE * math.hypot(q["x"] - by[cur]["x"], q["y"] - by[cur]["y"])
                z = max(by[cur]["z"] - lim, min(by[cur]["z"] + lim, q["z"]))
                if abs(z - q["z"]) < 1:
                    break
                q["z"] = z
                touched.add(q["i"])
                prev, cur = cur, q["i"]
            for s in a["segments"]:
                if s["a"] in touched or s["b"] in touched:
                    st.retangent(by, s)
    return moved


def _demo() -> None:
    cities = place_cities(3, 3, ((-200000, -200000), (200000, 200000)))
    assert len(cities) == 3 and all(c["style"] in STYLES for c in cities)
    ter = terrain(3, 400000, 6000, cities, grid=64)
    assert ter["heights"] == terrain(3, 400000, 6000, [], grid=64)["heights"], "natural ground ignores the cities"
    flat = terrain(3, 400000, 6000, cities, grid=64, opts={"city_ground": "flatten"})
    for c in cities:
        z, z2 = st.sample_grid(flat, c["x"], c["y"]), st.sample_grid(flat, c["x"] + c["radius"] * 0.5, c["y"])
        assert abs(z - z2) < 50, "flatten: a city sits on a flat plateau"
    actors = generate(cities, lambda x, y: st.sample_grid(ter, x, y) or 0.0, "/Game/X/WorldGen/Roads", seed=3)
    assert set(actors) == {f"{c['name']} Roads" for c in cities} | {"Highways"}
    # natural ground graded to the roads: every street point sits on the ground, no road on a wall
    gt = grade_to_roads(ter, actors)
    off = [abs(p["z"] - st.sample_grid(gt, p["x"], p["y"])) for c in cities for p in actors[f"{c['name']} Roads"]["points"]
           if abs(p["z"] - st.sample_grid(ter, p["x"], p["y"])) < 2000]
    before = [abs(p["z"] - st.sample_grid(ter, p["x"], p["y"])) for c in cities for p in actors[f"{c['name']} Roads"]["points"]]
    assert sorted(off)[int(len(off) * 0.95)] < 150, ("graded ground meets the streets", max(off), max(before))
    # the ground never covers a street between its points either (at a world's own grid, ~37 m a cell)
    fine = terrain(3, 400000, 6000, cities, grid=108)
    fa = generate(cities[:1], lambda x, y: st.sample_grid(fine, x, y) or 0.0, "/Game/X/WorldGen/Roads", seed=3)
    fg = grade_to_roads(fine, fa)
    over = []
    a = fa[f"{cities[0]['name']} Roads"]
    by = {p["i"]: p for p in a["points"]}
    for s in a["segments"]:
        p, q = by[s["a"]], by[s["b"]]
        for t in (0.25, 0.5, 0.75):
            x, y, z = (p[k] + (q[k] - p[k]) * t for k in ("x", "y", "z"))
            over.append(st.sample_grid(fg, x, y) - z)
    over.sort()
    assert over[int(len(over) * 0.95)] < 40, ("ground over the road", over[int(len(over) * 0.95)], over[-1])
    for label, a in actors.items():
        bad = st.problems(a["points"], a["segments"])
        assert not any(bad.values()), (label, {k: len(v) for k, v in bad.items()})
        back = st.parse(st.serialize(a["points"], a["segments"]))
        assert len(back["points"]) == len(a["points"]) and len(back["segments"]) == len(a["segments"])
    city = actors[f"{cities[0]['name']} Roads"]
    assert st.pending_nodes(city["points"], city["segments"]), "junctions wait for generated pieces"
    assert len(actors["Highways"]["segments"]) >= 2
    # super-blocks: outside the core, blocks are about twice as wide (room for subdivisions / complexes)
    from . import parcels
    sb = {"x": 0, "y": 0, "radius": 80000, "seed": 5, "style": "grid"}
    sn, se = city_graph(sb)
    areas = {"core": [], "out": []}
    for face in parcels.blocks(sn, se):
        poly = [sn[n] for n in face]
        cx, cy = sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)
        areas["core" if zone_share(sb, cx, cy) < 0.3 else "out"].append(abs(parcels._area(poly)))
    assert max(areas["core"]) < 1.5e8 < max(areas["out"]), {k: max(v) for k, v in areas.items()}
    # planned grid: every street between neighbouring intersections is one block long within the 8 % jitter
    # (both ends move at most 8 % each), and collectors run every 4th line
    for style in ("mixed", "organic"):
        city = {"x": 0, "y": 0, "radius": 60000, "seed": 11, "style": style}
        gn, ge = city_graph(city, {"wobble": 0.2})  # asked for more: clamped to 0.08
        block = OPTIONS["block_m"][0] * 100
        for a, b, _ in ge:
            L = math.dist(gn[a], gn[b])
            assert block * (1 - 2 * 0.08 * 1.42) <= L <= block * (1 + 2 * 0.08 * 1.42), (style, L)
        assert {"4L"} <= {k for _, _, k in ge}, "collectors / arterials exist"
    # graded roads: on a 10 % hill no ground-level street segment is steeper than its class allows (the cut and
    # fill it takes stay under MAX_CUT_CM); on a 20 % hill the street follows the hill instead of a 6 m+ wall
    for slope, holds in ((0.1, True), (0.2, False)):
        hill = lambda x, y, k=slope: x * k
        ga = build_actor({0: [0, 0], 1: [10000, 0], 2: [20000, 0], 3: [30000, 0]},
                         [[0, 1, "2L"], [1, 2, "2L"], [2, 3, "2L"]], hill, "/Game/X")
        gb = {p["i"]: p for p in ga["points"]}
        assert all(abs(p["z"] - hill(p["x"], p["y"])) <= MAX_CUT_CM + 1 for p in ga["points"] if p["raise"]), slope
        for s in ga["segments"]:
            a, b = gb[s["a"]], gb[s["b"]]
            run = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
            if holds and a["raise"] and b["raise"] and run > 0:
                assert abs(a["z"] - b["z"]) <= GRADE["2L"] * run + 2, (abs(a["z"] - b["z"]) / run)
    # SidewalkGenerator: a 6-lane boulevard gets a sidewalk spline on both sides; lot paths become splines
    sn, se = sidewalks({0: [0, 0], 1: [10000, 0]}, [[0, 1, "6L"]])
    assert len(se) == 2 and all(abs(abs(p[1]) - (HALF["6L"] + HALF["SW"])) < 1 for p in sn.values())
    rows = [[0, 0, 0, 0, 1, 7, 0, 12, 12, 0, [[1.6, [600, 0, 0, 1600, 0, 0]], [3.5, [600, -400, 0, 1000, -600, 0, 1600, -400, 0]],
                                            [18.0, [600, 0, 0, 1600, 0, 0]]], []]]
    pa = paths_actor({"x": 0, "y": 0, "radius": 1, "seed": 1}, None, rows, lambda x, y: 0.0, "/Game/X/WorldGen/Roads")
    meshes = {s["mesh"] for s in pa["segments"]}
    assert meshes == {"SM_Path_Walkway_10m"}, meshes  # pedestrians only; the driveway is private access
    ac = access_actor({"private": {"streets": [[[0, 0], [5000, 0], [5000, 5000]]]}}, rows, lambda x, y: 0.0,
                      "/Game/X/WorldGen/Roads")
    am = {s["mesh"] for s in ac["segments"]}
    assert {"SM_Path_PrivateStreet_10m", "SM_Path_Driveway_10m"} <= am, am
    # a square ring road keeps its straight sides: every corner becomes a 9 m arc, nothing bulges out
    sq = fillet([[0, 0], [10000, 0], [10000, 10000], [0, 10000], [0, 0]])
    assert all(-1 <= x <= 10001 and -1 <= y <= 10001 for x, y in sq), "inside the square"
    corner = min(sq, key=lambda p: math.dist(p, (10000, 0)))
    assert 200 < math.dist(corner, (10000, 0)) < 900, corner  # cut by the arc, not by a wide curve
    assert len(sq) == 4 * 4 + 2, len(sq)  # 4 arcs of 4 points + the restart point twice
    # a street turning 90 degrees gets an elbow piece (square corner), a gentle bend stays a spline point
    el = build_actor({0: [0, 0], 1: [10000, 0], 2: [10000, 10000], 3: [20000, 10500]},
                     [[0, 1, "2L"], [1, 2, "2L"], [2, 3, "2L"]], lambda x, y: 0.0, "/Game/X")
    pieces_at = {(round(p["x"]), round(p["y"])) for p in el["points"] if p["mesh"] == JUNCTION}
    assert pieces_at == {(10000, 0), (10000, 10000)}, pieces_at
    assert st.pending_nodes(el["points"], el["segments"]), "elbows wait for a generated piece too"
    # rule: one city alone gets no highway and so no interchange
    solo = generate(cities[:1], lambda x, y: 0.0, "/Game/X/WorldGen/Roads", seed=3, opts={"interchange": "cloverleaf"})
    assert "Highways" not in solo, "no stand-alone highway or cloverleaf"
    # 1:1 ground: buildings on a 20 % hillside checked against the landscape UEFN imports (heightmap samples,
    # bilinear): with their pads cut in nothing is buried or floating; without pads they would be
    hill = {"min": [-200000, -200000], "max": [200000, 200000], "grid": 64,
            "heights": [[0.2 * (-200000 + 400000 * (i + 0.5) / 64) for i in range(64)] for _ in range(64)]}
    rnd = random.Random(5)
    homes = []
    for k in range(25):
        x, y, yaw, w, d = rnd.uniform(-90000, 90000), rnd.uniform(-90000, 90000), rnd.uniform(0, 360), 12.0, 10.0
        c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        zs = [0.2 * (x + c * a * d * 50 - s * b * w * 50) for a in (-1, 1) for b in (-1, 1)]
        pad = sum(zs) / 4
        homes.append((f"h{k}", x, y, yaw, w, d, pad, round((pad - min(zs)) / 100 + 0.05, 1)))
    mg = pad_margin(hill)
    hp = [(x, y, math.cos(math.radians(yaw)), math.sin(math.radians(yaw)), w * 50 + mg / 2, d * 50 + mg / 2, z)
          for _l, x, y, yaw, w, d, z, _f in homes]
    fit = ground_fit(imported_ground(hill, pads=hp), homes)
    assert fit["ok"] and fit["checked"] == 25, fit
    assert ground_fit(imported_ground(hill), homes)["buried"] > 0, "without pads the hill buries them"
    # a row of neighbours up the hill, 6 m apart (their pads overlap): each keeps its own level, none buried
    row = [(f"r{k}", 2000.0 * k, 0.0, 0.0, 10.0, 14.0, 0.2 * 2000 * k, 2.0) for k in range(6)]
    rp = [(x, y, 1.0, 0.0, w * 50 + mg / 2, d * 50 + mg / 2, z) for _l, x, y, _yaw, w, d, z, _f in row]
    rf = ground_fit(imported_ground(hill, pads=rp), row)
    assert rf["buried"] == 0, rf
    # neighbours too close for the landscape to step between (no pads at all here): settled, nothing misfits
    moves, before, after = settle(imported_ground(hill), homes)
    assert before["buried"] and moves and after["ok"], (before["buried"], after)
    # rule: a bridge on a slope sits over the highest ground near it, never inside the hill
    slope = lambda x, y: x * 0.2  # 20 % grade along x
    br = build_actor({0: [0, 0], 1: [20000, 0]}, [[0, 1, "4L", [[10000, 0, BRIDGE_LIFT_CM, "BR"]]]], slope, "/Game/X")
    deck = max(p["z"] for p in br["points"] if not p["raise"])
    assert deck >= slope(10000 + 1500, 0) + BRIDGE_LIFT_CM - 1, deck
    # options: clamped, lakes flatten the lowlands, smaller blocks mean more streets, 2-lane highways
    o = options({"block_m": 9999, "sea_m": -5, "highway": "8L", "detail": 3.4, "interchange": "stack"})
    assert o["block_m"] == 250 and o["highway"] == "4L" and o["detail"] == 3 and o["interchange"] == "stack"
    # road types per district: a six-lane core with one-way side streets, dirt suburbs
    kinds = {e[2] for e in city_graph(cities[0], {"core_roads": "6L", "one_way_core": "yes", "suburb_roads": "DT"})[1]}
    assert {"6S", "1S", "DT"} <= kinds and not kinds & set(CITY_STREET), ("city streets all have sidewalks", kinds)
    # a highway bridges over a street it crosses: ramp up, deck, ramp down; bridges never shape the ground
    via, n = overpass_via([[0, 0], [200000, 0]], [([100000, -5000], [100000, 5000])], "4L")
    # (an avenue rides the wide deck up, across and down: its bridge is never narrower than the road)
    assert n == 1 and [v[3] for v in via if len(v) > 3] == ["ED", "ED", "ED", "4L"] and max(v[2] for v in via) == BRIDGE_LIFT_CM
    assert [v[3] for v in overpass_via([[0, 0], [200000, 0]], [([100000, -5000], [100000, 5000])], "2L")[0] if len(v) > 3] == \
        ["BRR", "BR", "BRR", "2L"], "a street takes the two-lane bridge"
    assert all(HALF[structure_kinds(k)[0]] >= HALF[k] - 351 for k in ("2L", "2S", "4L", "6L", "6S")), "no bridge narrower than its carriageway"
    assert HALF[structure_kinds("6S")[0]] == HALF["6S"] and WALK[structure_kinds("6S")[0]] == WALK["6S"], "a boulevard keeps its sidewalks across"
    br = build_actor({"a": [0, 0], "b": [200000, 0]}, [["a", "b", "4L", via]], lambda x, y: 0.0, "/Game/X/Roads")
    meshes = [s["mesh"] for s in br["segments"]]
    assert meshes.count("SM_Elev_Deck20m") == 3, meshes
    deck = next(s for s in br["segments"] if s["mesh"] == "SM_Elev_Deck20m")
    assert "bRaiseTerrain=False" in deck["lines"] and not deck["raise"]
    top = max(p["z"] for p in br["points"])
    assert abs(top - BRIDGE_LIFT_CM) < 1, top
    text = st.serialize(br["points"], br["segments"])
    assert "Elevated/SM_Elev_Deck20m" in text and "SM_Road_Bridge" not in text
    # interchanges: a stack rises onto elevated decks, a trumpet puts the loop piece on each highway end
    far = [{"name": "P", "x": -150000, "y": 0, "radius": 30000, "style": "grid", "seed": 3},
           {"name": "Q", "x": 150000, "y": 0, "radius": 30000, "style": "grid", "seed": 4}]
    stack = generate(far, lambda x, y: 0.0, "/Game/X/Roads", seed=3, opts={"interchange": "stack"})["Highways"]
    assert {"SM_Elev_Deck20m", "SM_Elev_Ramp20m"} <= {s["mesh"] for s in stack["segments"]}
    assert "/Game/X/Roads/Elevated/SM_Elev_Deck20m" in st.serialize(stack["points"], stack["segments"])
    # real interchanges, built from our pieces: a bridge over a cross road and loop / slip ramps at each end
    made = {}
    for kind in ("cloverleaf", "trumpet", "diamond"):
        world = generate(far, lambda x, y: 0.0, "/Game/X/Roads", seed=3, opts={"interchange": kind, "highway": "6L"})
        hw = world["Highways"]
        meshes = [s["mesh"] for s in hw["segments"]]
        made[kind] = hw
        assert meshes.count("SM_Elev_Deck20m") >= 2 and "SM_Road_Bridge_Straight20m" not in meshes, \
            (kind, "a bridge at each end, as wide as the six-lane highway it carries")
        loops = meshes.count("SM_Elev_Ramp20m")
        assert loops == {"cloverleaf": 2 * 4 * 12, "trumpet": 2 * 12, "diamond": 0}[kind], (kind, loops)
        top = max(p["z"] for p in hw["points"])
        assert abs(top - BRIDGE_LIFT_CM) < 1, (kind, top)
        # the cross road is a city street now, joined to the city: no Highways stub going nowhere
        assert "SM_Road_FourLane_Straight10m" not in meshes, kind
        for c in far:
            city = world[f"{c['name']} Roads"]
            assert any(s["mesh"] == "SM_Road_FourLane_Straight10m" for s in city["segments"]), (kind, c["name"])
    # several highways in one world (each one's interchange adds city streets before the next is routed)
    three = far + [{"name": "R", "x": 0, "y": 250000, "radius": 30000, "style": "mixed", "seed": 5}]
    multi = generate(three, lambda x, y: 0.0, "/Game/X/Roads", seed=3, opts={"interchange": "cloverleaf", "highway": "6L"})
    assert weld_ends(multi) == 0, "every ramp and highway end already sits on the road it reaches"
    ends = [p["mesh"] for a in multi.values() for p in a["points"] if p["mesh"].startswith("SM_Road_Ramp")]
    assert {"SM_Road_RampFlare15m", "SM_Road_RampGore30m"} <= set(ends), ("ramp ends get their connectors", set(ends))
    # a viaduct over bumpy ground runs level: never under its planned clearance, far smoother than the ground
    bumps = [_point(k, k * 2000.0, 0, 750 + 400 * math.sin(k * 1.3), 0, 1650, lifted=k not in (0, 60)) for k in range(61)]
    planned = [p["z"] for p in bumps]
    level_decks(bumps)
    wob = lambda zs: max(abs(zs[k - 1] - 2 * zs[k] + zs[k + 1]) for k in range(1, len(zs) - 1))
    assert all(p["z"] >= z - 1 for p, z in zip(bumps, planned)) and wob([p["z"] for p in bumps[8:53]]) < wob(planned) / 10
    # an end never joins its own road (the old weld slid ends onto their own previous point)
    rb = {k: {"i": k, "x": k * 1000.0, "y": 0.0} for k in range(4)}
    rs = [{"a": k, "b": k + 1} for k in range(3)]
    rt: dict = {}
    for s in rs:
        for k in (s["a"], s["b"]):
            rt.setdefault(k, []).append(s)
    assert own_run(rt, rb, 3) == {0, 1, 2, 3} and own_run(rt, rb, 3, 1500) == {1, 2, 3}
    # every ramp end lies inside the road it joins (its edge on that road's edge), never beside it
    half_of = {m.split("/")[-1]: HALF[k] for k, m in ROAD_MESH.items()}
    segs_all = [(lab, {p["i"]: p for p in a["points"]}, s) for lab, a in multi.items() for s in a["segments"] if s["mesh"]]
    for lab, a in multi.items():
        if not lab.endswith(("Freeways", "Highways")):
            continue
        by = {p["i"]: p for p in a["points"]}
        deg: dict = {}
        for s in a["segments"]:
            for i in (s["a"], s["b"]):
                deg.setdefault(i, []).append(s)
        for i, ss in deg.items():
            if len(ss) == 1 and ss[0]["mesh"] and not by[i]["mesh"]:
                p = by[i]
                gaps = [math.hypot(p["x"] - q["x"] - (r["x"] - q["x"]) * t, p["y"] - q["y"] - (r["y"] - q["y"]) * t)
                        - max(0.0, half_of.get(s2["mesh"].split("/")[-1], 800.0) - half_of.get(ss[0]["mesh"].split("/")[-1], 650.0))
                        for lab2, b2, s2 in segs_all if not (lab2 == lab and i in (s2["a"], s2["b"]))
                        for q, r in [(b2[s2["a"]], b2[s2["b"]])]
                        for t in [max(0.0, min(1.0, ((p["x"] - q["x"]) * (r["x"] - q["x"]) + (p["y"] - q["y"]) * (r["y"] - q["y"]))
                                                / (((r["x"] - q["x"]) ** 2 + (r["y"] - q["y"]) ** 2) or 1.0)))]]
                assert not gaps or min(gaps) <= 60, (lab, round(min(gaps)), "a ramp end beside its road")
    # real bridges: piers under every deck that stands clear of the ground, none where it sits on it
    cols, caps = bridge_piers({"x": {"points": [_point(0, 0, 0, 900, 0, 1650, lifted=True), _point(1, 12000, 0, 900, 0, 1650, lifted=True)],
                                     "segments": [_segment(2, 0, 1, "ED", "/G", lifted=True)]}}, lambda x, y: 0.0)
    assert len(caps) == 4 and len(cols) == 8 and all(abs(c[2] + c[6] * 100 - (900 - DECK_DEPTH_CM - CAP_CM)) < 2 for c in cols), (caps, cols)
    under = bridge_piers({"x": {"points": [_point(0, 0, 0, 900, 0, 1650, lifted=True), _point(1, 12000, 0, 900, 0, 1650, lifted=True),
                                          _point(3, 1500, -5000, 0, 0, 525), _point(4, 1500, 5000, 0, 0, 525)],
                                "segments": [_segment(2, 0, 1, "ED", "/G", lifted=True), _segment(5, 3, 4, "2L", "/G")]}},
                         lambda x, y: 0.0)[1]
    assert len(under) == 3 and all(abs(c[0] - 1500) > 525 for c in under), ("no pier in the street under the deck", under)
    assert not bridge_piers({"x": {"points": [_point(0, 0, 0, 200, 0, 650, lifted=True), _point(1, 6000, 0, 200, 0, 650, lifted=True)],
                                   "segments": [_segment(2, 0, 1, "ER", "/G", lifted=True)]}}, lambda x, y: 0.0)[0], "a deck on its fill"
    hilly = generate(three, lambda x, y: 800.0 * math.sin(x / 9000.0) + 600.0 * math.cos(y / 7000.0), "/Game/X/Roads", seed=3)
    assert weld_ends(hilly) == 0, "on hills too: generate welds the ends itself"
    assert all(not any(st.problems(a["points"], a["segments"]).values()) for a in multi.values())
    # every ramp touches its roads: a loop starts on the highway's edge, ends on the cross road's edge
    _, inodes, _, cross = interchange("cloverleaf", [0, 0], [100000, 0], ("t",), "6L")
    for (_, i, j, end), p in ((k[1:], v) for k, v in inodes.items() if k[1] == "l"):
        ax, by = p[0] - 18000, p[1]  # highway along +x, crossing at x = 180 m, cross road along y
        if end == 0:
            assert abs(abs(by) - HALF["6L"]) < 1 and p[2] > 0, ("loop starts on the highway edge, up on it", p)
        else:
            assert abs(abs(ax) - HALF["4L"]) < 1, ("loop ends on the cross road edge", p)
    assert abs(cross[0][0] - 18000) < 1 and abs(cross[1][0] - 18000) < 1, cross
    trumpet = made["trumpet"]
    for a in (stack, br, *made.values()):
        bad = st.problems(a["points"], a["segments"])
        assert not any(bad.values()), {k: len(v) for k, v in bad.items()}
    # full relief: the grid spans the hill height, not a third of it
    flat_hills = [v for row in terrain(3, 400000, 6000, [], grid=48)["heights"] for v in row]
    assert max(flat_hills) > 5500 and min(flat_hills) < -5500, (min(flat_hills), max(flat_hills))
    # water: every cell that was under the water line is pushed well below it, never a flat sheet on it
    dry = terrain(3, 400000, 6000, [], grid=48)["heights"]
    wet = terrain(3, 400000, 6000, [], grid=48, opts={"sea_m": -5, "water_depth_m": 12})
    sea = wet["sea"]
    under = [(d, w) for rd, rw in zip(dry, wet["heights"]) for d, w in zip(rd, rw) if d < sea - 1200]
    assert under and all(w < sea - 1200 for d, w in under), "deep water stays deep"
    assert sum(1 for d, w in under if abs(w - sea) < 50) == 0, "no lake bed sits flat at the water line"
    assert all(w <= d + 1 for rd, rw in zip(dry, wet["heights"]) for d, w in zip(rd, rw)), "water only lowers"
    # a city in a lake gets dry ground
    lake_city = [{"name": "L", "x": 0, "y": 0, "radius": 30000}]
    t = terrain(3, 400000, 6000, lake_city, grid=48, opts={"sea_m": 50, "city_ground": "flatten"})
    assert st.sample_grid(t, 0, 0) > t["sea"], "a city never sits under water"
    small = city_graph(cities[0], {"block_m": 60})[1]
    assert len(small) > len(city_graph(cities[0], {"block_m": 150})[1])
    two = generate(cities, lambda x, y: 0.0, "/Game/X/WorldGen/Roads", seed=3, opts={"highway": "2L"})
    assert set(two["Highways"]["segments"][k]["mesh"] for k in range(len(two["Highways"]["segments"]))) <= {"", ROAD_MESH["2L"]}
    steps = terrain(3, 400000, 6000, [], grid=32, opts={"terrace_m": 20})
    assert steps["heights"], "terraces build"
    png, settings = heightmap_png(ter, res=65)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and settings["scale"][2] == 100.0
    # a keep zone (the cinema): flat at its own height, no city on it, the highway goes round it
    keep = keep_zones([{"name": "Cinema", "x0": -10000, "y0": -12000, "x1": 14000, "y1": 11000, "z": 1341}, {"bad": 1}])
    assert len(keep) == 1
    kc = place_cities(3, 4, ((-200000, -200000), (200000, 200000)), keep)
    assert kc and all(keep_gap(keep[0], c["x"], c["y"]) > c["radius"] for c in kc)
    kt = terrain(3, 400000, 6000, kc, grid=96, keep=keep)
    for x, y in ((0, 0), (12000, 9000), (-8000, -10000)):
        assert abs(st.sample_grid(kt, x, y) - 1341) < 30, (x, y, st.sample_grid(kt, x, y))
    across = [{"name": "W", "x": -150000, "y": 0, "radius": 30000, "style": "grid", "seed": 1},
              {"name": "E", "x": 150000, "y": 0, "radius": 30000, "style": "grid", "seed": 2}]
    hw = generate(across, lambda x, y: 0.0, "/Game/X/Roads", seed=3, opts={"interchange": "direct"}, keep=keep)["Highways"]
    assert not any(in_keep(keep, p["x"], p["y"], 2000) for p in hw["points"]), "the highway goes round the cinema"
    # freeways: a big city gets two urban freeways, a stack and service interchanges, planned before its blocks;
    # the validator is clean, no lot sits in the right of way, streets still run under the viaducts
    mid = {"name": "Mid", "x": 0, "y": 0, "radius": 120000, "seed": 7, "style": "mixed"}
    mk = [i["kind"] for i in freeway_plan(mid).interchanges]
    assert mk and "stack" not in mk and freeway_report(mid)["validation"]["ok"], mk
    assert "diamond_frontage" in mk and "Mid Freeways" in generate([mid], lambda x, y: 0.0, "/G", seed=1)  # FR builds
    big = {"name": "Metro", "x": 0, "y": 0, "radius": 220000, "seed": 7, "style": "mixed"}
    fw = freeway_plan(big)
    kinds = [i["kind"] for i in fw.interchanges]
    assert "stack" in kinds and {"diamond", "parclo"} & set(kinds), kinds
    rep = freeway_report(big)
    assert rep["validation"]["ok"], {k: v for k, v in rep["validation"].items() if v}
    # every ramp lands on its road in jittered (mixed) cities of any seed: main roads are straight lines
    for s in range(1, 9):
        r = freeway_report({"name": "K", "x": 0, "y": 0, "radius": 160000, "seed": s, "style": "mixed"},
                           {"core_roads": "6L", "one_way_core": "yes"})["validation"]
        assert r["ok"], (s, {k: v for k, v in r.items() if v and k != "ok"})
    lay = city_layout(big)
    assert lay["lots"] and not any(fw.blocked(r[0], r[1], 0 if r[8] is True else 0.5 * max(r[3], r[4]))
                                   for r in lay["lots"])
    # the planned city: services from demand, complete developments, never across a freeway
    p = lay["plan"]
    assert p and p["counts"].get("fire_station") and p["counts"].get("elementary_school"), p and p["counts"]
    assert {g[5] for g in lay["grounds"]} >= {"apron", "field"}, "sites come complete"
    for f in p["facilities"]:
        assert not fw.blocked(f["x"], f["y"]), ("a site sits in the freeway", f["type"])
    gn, ge = city_graph(big)
    under = sum(1 for a, b, _ in ge if fw.street_blocked(gn[a], gn[b], 0) is False and
                any(_seg_cross(gn[a], gn[b], ln["points"][k][:2], ln["points"][k + 1][:2]) is not None
                    for ln in fw.lines if ln["cls"] == "freeway" for k in range(len(ln["points"]) - 1)))
    assert under > 10, under
    world = generate([big], lambda x, y: 0.0, "/Game/X/Roads", seed=3)
    fa = world["Metro Freeways"]
    assert {"SM_Elev_Deck20m", "SM_Elev_Ramp20m"} <= {s["mesh"] for s in fa["segments"]}
    assert max(p["z"] for p in fa["points"]) >= 4 * 750 - 1, "flyovers on level 4"
    small = {"name": "Town", "x": 0, "y": 0, "radius": 40000, "seed": 7}
    assert not freeway_plan(small).lines and "Town Freeways" not in generate([small], lambda x, y: 0.0, "/G", seed=1)
    assert not freeway_plan(big, {"freeways": "no"}).lines
    # water: a river (x 40..120 m) and a lake (x > 400 m). Main roads bridge the river, side streets stop at the
    # shore, nothing ends in the lake; a highway crosses the lake on a long bridge
    wet = lambda x, y: 4000 < x < 13000 or x > 40000
    wn, we = over_water({0: [-10000, 0], 1: [25000, 0], 2: [-10000, 5000], 3: [25000, 5000], 4: [30000, 0], 5: [50000, 0]},
                        [[0, 1, "4L"], [2, 3, "DT"], [4, 5, "4L"]], wet)
    assert [e[2] for e in we] == ["4L", "4L"], we  # the dirt road stops at the river
    cut = we[1]  # the street into the lake ends at the shore
    assert cut[0] == 4 and cut[1] == ("shore", 4, 5, 1) and not wet(*wn[cut[1]][:2]) and wn[cut[1]][0] > 37000, cut
    vb = we[0][3]
    assert [v[3] for v in vb] == ["ED", "ED", "ED", "4L"] and max(v[2] for v in vb) == BRIDGE_LIFT_CM, vb
    assert all(not wet(v[0], v[1]) for v in vb if v[2] == 0), "ramps start on land"
    hb: list = []
    hn, he = over_water({"a": [0, 0], "b": [130000, 0]}, [["a", "b", "4L"]], lambda x, y: 20000 < x < 110000,
                        {"4L": 1e9}, hb)
    assert he and max(v[2] for v in he[0][3]) == 3000, "900 m of navigable water: a high bridge (30 m clearance)"
    assert hb and hb[0]["type"] == "cable-stayed bridge" and len(hb[0]["towers"]) == 2, hb
    assert over_water({0: [0, 0], 1: [10000, 0]}, [[0, 1, "2L"]], lambda x, y: 5000 < x < 5500)[1], "creek bridged"
    lake = {"name": "Lake", "x": 0, "y": 0, "radius": 60000, "seed": 3, "style": "grid"}
    shore = lambda x, y: x > 30000
    ln, le = city_graph(lake, {"freeways": "no"}, shore)
    assert all(x <= 30000 or len(e) > 3 for e in le for x in (ln[e[0]][0], ln[e[1]][0])), "no street node in the lake"
    assert not any(shore(r[0], r[1]) for r in city_layout(lake, {"freeways": "no"}, shore)["lots"])
    # a 100 m river through a grid city: the streets cross it on bridges, none dead-ends at the bank
    river = lambda x, y: 10000 < x < 20000
    rb: list = []
    rn, re_ = city_graph(lake, {"freeways": "no"}, river, bridges=rb)
    assert not any(river(*rn[n][:2]) for e in re_ for n in e[:2]), "no junction in the river"
    over = [e for e in re_ if min(rn[e[0]][0], rn[e[1]][0]) < 10000 < 20000 < max(rn[e[0]][0], rn[e[1]][0])]
    assert len(over) >= 5 and all(max(v[2] for v in e[3]) >= 300 for e in over), over[:2]
    wide_river = city_graph(lake, {"freeways": "no", "core_roads": "6L"}, river)[1]
    assert all(v[3] in (*structure_kinds(e[2]), e[2]) for e in re_ + wide_river if len(e) > 3 and e[3]
               for v in e[3] if len(v) > 3), "a bridge is as wide as the street it carries"
    assert len(rb) == len(over), (len(rb), len(over))
    ends = [rn[n] for n in rn if isinstance(n, tuple) and n and n[0] == "shore"]
    assert all(math.hypot(*p[:2]) > 0.85 * lake["radius"] for p in ends), ("only the city edge ends at the bank", ends)
    # city size and distance between cities
    small_c = place_cities(5, 3, ((-300000, -300000), (300000, 300000)), size=0.6, gap_cm=50000)
    big_c = place_cities(5, 3, ((-300000, -300000), (300000, 300000)), size=1.5, gap_cm=50000)
    assert max(c["radius"] for c in small_c) < min(c["radius"] for c in big_c), "city size scales radii"
    for cs in (small_c, big_c):
        assert all(math.hypot(a["x"] - b["x"], a["y"] - b["y"]) >= a["radius"] + b["radius"] + 50000 - 1
                   for i, a in enumerate(cs) for b in cs[i + 1:])
    apart = spread([{"x": 0, "y": 0, "radius": 50000}, {"x": 60000, "y": 0, "radius": 50000}], 80000)
    assert abs(apart[1]["x"] - apart[0]["x"] - 180000) < 2 and apart[0]["x"] == -60000, apart
    # canal city: canals replace every third inner street line, cross streets bridge them on canal arches
    venice = {"name": "Canalle", "x": 0, "y": 0, "radius": 60000, "seed": 9, "style": "grid"}
    co = {"canals": "canal_city", "freeways": "no", "planner": "no"}
    assert canal_lines(venice, co)
    br: list = []
    vw = generate([venice], lambda x, y: 0.0, "/Game/X/Roads", seed=1, opts=co, bridges=br)
    assert "Canalle Canals" in vw and vw["Canalle Canals"]["segments"], sorted(vw)
    assert all(abs(p["z"] + CANAL_DEPTH_CM) < 1 for p in vw["Canalle Canals"]["points"]), "laid at the channel bed"
    assert br and all(b["type"] == "canal arch" and b["clearance_m"] == 3.0 for b in br), br[:2]
    cw = canal_wet(venice, co)
    vn, ve = city_graph(venice, co)
    assert not any(cw(*vn[n][:2]) for e in ve for n in e[:2]), "no street ends in a canal"
    assert not any(cw(r[0], r[1]) for r in city_layout(venice, co)["lots"]), "no lot in a canal"
    walks = paths_actor(venice, co, [], lambda x, y: 0.0, "/Game/X/Roads")
    assert len(walks["segments"]) > len(paths_actor(venice, {**co, "canals": "none"}, [], lambda x, y: 0.0,
                                                    "/Game/X/Roads")["segments"]), "canal-side walkways"
    # airports: planned before the streets, built as actors; no city street inside, no homes in the noise zone
    ac = {"name": "Aero", "x": 0, "y": 0, "radius": 120000, "seed": 4, "style": "grid"}  # ~22k people: regional
    aps = plan_airports([ac], lambda x, y: 0.0, None, [], ((-600000, -600000), (600000, 600000)), {})
    assert aps and aps[0]["type"] == "regional", aps
    # a city's own features beat the world's: its airport taken off, a small town given one, freeways off
    bounds6 = ((-600000, -600000), (600000, 600000))
    assert plan_airports([{**ac, "features": {"airports": "none"}}], lambda x, y: 0.0, None, [], bounds6, {}) == []
    town = {"name": "Tiny", "x": 0, "y": 0, "radius": 30000, "seed": 4, "style": "grid"}
    assert not plan_airports([dict(town)], lambda x, y: 0.0, None, [], bounds6, {})
    got = plan_airports([{**town, "features": {"airports": "auto"}}], lambda x, y: 0.0, None, [], bounds6, {"airports": "none"})
    assert got and got[0]["type"] == "general_aviation", got
    assert not freeway_plan({**big, "features": {"freeways": "no"}}).lines and freeway_plan(big).lines
    builds: dict = {}
    aw = generate([ac], lambda x, y: 0.0, "/Game/X/Roads", seed=1, opts={"freeways": "no"}, airports=aps,
                  airport_builds=builds)
    assert "Aero Airport" in aw and "Aero Airport Roads" in aw, sorted(aw)
    am = {s["mesh"] for s in aw["Aero Airport"]["segments"]}
    assert {"SM_Air_Runway_20m", "SM_Air_Taxiway_10m"} <= am, am
    assert builds["Aero Airport"]["report"]["ok"], builds["Aero Airport"]["report"]
    from . import airport as _ap
    gn, ge = city_graph(ac)
    assert not any(_ap.inside(aps[0], *gn[n][:2]) for n in gn), "no city street on the airfield"
    lay = city_layout(ac, {"planner": "no"})
    assert not any(r[7] in ("house", "apartment") and _ap.in_noise(aps[0], r[0], r[1]) for r in lay["lots"])
    # reserved land beats freeways: an airport across the freeway's path stops the mainline short and drops
    # the interchanges whose cross roads it took; every ramp left still lands on a road
    for s in (1, 2, 3):
        rc = {"name": "Res", "x": 0, "y": 0, "radius": 160000, "seed": s, "style": "mixed"}
        base = freeway_plan(rc)
        ns = next(l for l in base.lines if l["group"] == ("main", "ns"))
        tip = ns["points"][-1]
        rc["airports"] = [{"city": "Res", "type": "general_aviation", "center": [round(tip[0]), round(tip[1])],
                           "heading": 0.0}]
        fwr = freeway_plan(rc)
        main = next(l for l in fwr.lines if l["group"] == ("main", "ns"))
        assert not any(_ap.inside(rc["airports"][0], p[0], p[1]) for p in main["points"]), "freeway stops short"
        rep = freeway_report(rc)["validation"]
        assert rep["ok"], (s, {k: v for k, v in rep.items() if v and k != "ok"})
    print("citygen ok", {k: len(v["segments"]) for k, v in actors.items()}, "metro interchanges", kinds)


if __name__ == "__main__":
    _demo()

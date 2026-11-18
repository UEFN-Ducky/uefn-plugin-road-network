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


def _cubic_taps(n_from: int, n_to: int) -> list:
    """For each of n_to fine cells: four (coarse index, Catmull-Rom weight) taps over n_from coarse cell centres."""
    out = []
    for k in range(n_to):
        f = max(0.0, min(n_from - 1.0, (k + 0.5) * n_from / n_to - 0.5))
        i = min(n_from - 2, int(f)) if n_from > 1 else 0
        t = f - i
        w = (((-t + 2) * t - 1) * t / 2, ((3 * t - 5) * t * t + 2) / 2, ((-3 * t + 4) * t + 1) * t / 2, (t - 1) * t * t / 2)
        out.append([(max(0, min(n_from - 1, i + d)), w[d + 1]) for d in (-1, 0, 1, 2)])
    return out


def refine(ter: dict, grid: int) -> dict:
    """The same terrain on a finer grid, so roads can be cut into it a landscape pixel at a time. Bicubic
    (Catmull-Rom): bilinear left a crease on every coarse cell edge, hills looked cut from flat facets.
    A cell with no ground (None, off a scan) stays None."""
    H, n = ter["heights"], ter["grid"]
    if any(v is None for r in H for v in r):  # ponytail: scans with holes keep the bilinear sampler
        s = st.grid_sampler(ter)
        (x0, y0), (x1, y1) = ter["min"], ter["max"]
        cw, ch = (x1 - x0) / grid, (y1 - y0) / grid
        return {**ter, "grid": grid, "heights": [[round(s(x0 + (i + 0.5) * cw, y0 + (j + 0.5) * ch) or 0.0, 1)
                                                  for i in range(grid)] for j in range(grid)]}
    taps = _cubic_taps(n, grid)  # separable: along x for every coarse row, then along y
    wide = [[sum(r[a] * w for a, w in tp) for tp in taps] for r in H]
    cols = list(zip(*wide))
    out = [[0.0] * grid for _ in range(grid)]
    for i, col in enumerate(cols):
        for j, tp in enumerate(taps):
            out[j][i] = round(sum(col[a] * w for a, w in tp), 1)
    return {**ter, "grid": grid, "heights": out}
LOOP_PIECE = "SM_Road_Loop_Big"  # trumpet loop (point piece)
CLOVER_PIECE = "SM_Road_Freeway_Cloverleaf"  # in NeonSkyscrapers RoadKit: import it before using cloverleaf
# per-district road choices (the panel's Roads tab)
CHOICES = {
    "core_roads": (("4L", "6L", "2S"), "4L"),
    "neighbourhood_roads": (("2S", "2L", "4L"), "2S"),
    "suburb_roads": (("2L", "2S", "1W", "DT"), "2S"),  # the default street carries its sidewalks in its own mesh
    "highway": (("4L", "6L", "2L"), "4L"),
    "interchange": (("direct", "diamond", "trumpet", "cloverleaf", "stack"), "direct"),
    # Interchanges belong inside a city, on its freeway. A highway from another city runs straight into that
    # freeway's open end (or, in a town without one, into its gate street). 'yes' brings back the old stand-alone
    # interchange in the fields at each city's edge (the `interchange` kind above), which read as broken.
    "gate_interchanges": (("no", "yes"), "no"),
    "one_way_core": (("no", "yes"), "no"),  # core side streets as alternating one-way lanes
    "overpasses": (("yes", "no"), "yes"),  # highways bridge over the streets they cross
    # outside the core every other local street line is left out: ~200 m super-blocks that subdivisions and
    # apartment complexes fill with their own private streets (neighbourhoods.py)
    "superblocks": (("yes", "no"), "yes"),
    # the suburbs' own road strategy: 'blocks' keeps the city's grid out to its edge, 'winding' ends the grid at the
    # normal city and grows curving collectors, crescents, loops and cul-de-sacs from there (winding_suburbs)
    "suburbs": (("blocks", "winding"), "blocks"),
    # urban freeways through each big city, planned before its blocks (freeways.py): viaducts over the grid,
    # service interchanges on the main roads, a stack where two freeways cross
    "freeways": (("yes", "no"), "yes"),
    # the six Blender interchange models (assets/interchanges) where one fits wholly inside the city's circle,
    # joined to the freeway and the cross road at its sockets; elsewhere the generated ramps
    "interchange_models": (("yes", "no"), "yes"),
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
    # the land as an island in the sea: 'one' one big island round the map's middle, 'many' an island for each
    # city plus a few islets (highways bridge the straits); every city is always on dry land. Water level
    # Off with an island: the sea sits at 0 m
    "island": (("none", "one", "many"), "none"),
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
    "Every building lot is levelled (pad), and every building stands on its own "
    "foundation (a plinth course and a wall 1 m into the ground, deeper where the hill falls away); a walkway to "
    "the kerb and, for houses and businesses, a driveway.",
    "Nothing is built where the land falls more than 4 m away under it (its foundation would be a concrete cliff): "
    "the building is left out and its lot planted with trees. Parks stay, and so does a site the planner chose "
    "(hospital, school, city hall...). ground_fit.left_out counts them.",
    "Nothing stands on air and no loose wall slab holds anything up: ground roads carry their own 3 m side walls, "
    "a deck or ramp too low for piers stands on its approach wall, an uphill cut is graded ground.",
    "Forests grow in dense patches with meadows between, over the whole map (densest round the cities) and on the "
    "land a city leaves free; parks are dense groves.",
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
    "(15 m), cable-stayed (30 m) and suspension (45 m); ramps keep the road's grade. A cable-stayed or suspension "
    "bridge has two towers standing astride its deck (a leg outside each edge, beams across, never a column in "
    "the roadway) and stay cables from each leg down to the deck's edge; the deck they carry has no piers.",
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
    "smooth": (0.5, 0.0, 1.0),         # rounds the hills off: 0 = raw noise .. 1 = soft, rolling
    # the heightmap's pixels a side (snapped to 63 quads x components + 1). UEFN caps ALL landscapes together at
    # 2048 x 2048 samples: 2017 is the most that fits
    "landscape_px": (2017, 505, 4033),
    "ridges": (0.0, 0.0, 1.0),         # 0 rolling hills .. 1 sharp mountain ridges
    "peaks": (0.0, 0.0, 1.0),          # 0 even hills .. 1 wide low valleys with tall sharp peaks
    "water_depth_m": (12.0, 0.0, 80.0),  # how deep lake beds drop below the water line
    # cities
    "city_size": (1.0, 0.3, 4.0),     # every city's radius x this (new cities; regenerating scales the old ones)
    "city_gap_m": (300.0, 0.0, 6000.0),  # open country between two cities' edges, at least
    # roads
    "block_m": (100.0, 50.0, 250.0),   # city block, centre line to centre line (its short side)
    "block_ratio": (1.0, 1.0, 3.0),    # how long a block is for its width: 1 = square, 2 = twice as long as wide
    # planned grid (Parish & Mueller hierarchy; real cities ~80 % planned). A street between two main roads may
    # bow off its grid line in one smooth curve (a seeded wave along the whole street, _bow): it never jogs from
    # intersection to intersection, and its neighbours bow with it, so blocks keep their width. 0 = dead straight
    # how far a street bows off its grid line (share of a block). Any at all also lets the whole street net
    # drift in long waves, main roads with it (meander); 0 is a ruled grid
    "wobble": (0.12, 0.0, 0.45),
    # on sloping ground the streets follow the hills (plan_hills): along the hillside across a slope, winding up
    # it, the too-steep ones left out. 0 = the grid ignores the hills; never in a Grid city
    "follow_hills": (0.8, 0.0, 1.0),
    "green_lots": (0.0, 0.0, 0.6),     # share of each block's lots planted with trees instead of built on
    "prune": (0.12, 0.0, 0.35),        # share of local side streets removed in neighbourhoods / suburbs
    "collector_every": (4, 2, 8),      # every Nth grid line is a collector (wider, through the city)
    "downtown": (0.4, 0.1, 0.8),       # downtown ring, share of the radius
    "normal": (0.75, 0.2, 0.95),       # normal-city ring; housing beyond it
    "highway_curve": (0.08, 0.0, 0.3), # how far a highway bows out, share of its length
    "wind_deg": (240.0, 0.0, 360.0),   # prevailing wind (degrees, map yaw): runways are turned into it
}
HIGHWAYS = CHOICES["highway"][0]


# what one city may have or not, over the world's choice (city["features"], the Cities list's remove / + Add)
FEATURES = ("airports", "shipyards", "freeways", "waterfront", "canals", "planner", "suburbs")


CITY_SHARES = ("downtown", "normal",  # ... and its own district sizes (shares of its radius), kept with them
               "wobble", "prune")  # ponytail: a city's own street curviness rides the same path (clamped by OPTIONS)
CORE_MAX = 0.6  # a city's centre district may be moved this far toward its edge (a share of the way there)


# a city switched to Organic gets these as its own (unless it has its own already): on Valley's world wobble of
# 0.04, Northgate set to Organic came out the same ruled grid (Nov 17 2026)
ORGANIC_LOOK = {"wobble": 0.3, "suburbs": "winding"}


def city_options(city: dict, opts: dict | None = None) -> dict:
    """The world's options with this city's own feature choices and district sizes on top."""
    return options({**(opts or {}), **{k: v for k, v in (city.get("features") or {}).items()
                                       if k in FEATURES or k in CITY_SHARES}})


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


LAKE_WIDTH_M = (60.0, 200.0, 1500.0)  # a drawn lake's width: the least, what a new one gets, the most
LAKE_BANK_GRADE = 0.3  # how steeply the land comes down to a dug lake: about 1 in 3 ...
LAKE_BANK_CM = (3000.0, 60000.0)  # ... over at least 30 m and never more than 600 m


def lake_list(raw) -> list:
    """The lakes drawn by hand on a world, cleaned: [{id, pts [[x, y], ...] (cm, along its middle), width_m}]."""
    out, used = [], set()
    lo, _new, hi = LAKE_WIDTH_M
    for r in raw or []:
        try:
            pts = [[round(float(p[0])), round(float(p[1]))] for p in r["pts"]][:200]
            width = max(lo, min(hi, float(r.get("width_m") or _new)))
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        pts = [p for k, p in enumerate(pts) if k == 0 or p != pts[k - 1]]
        if not pts:
            continue
        lid = str(r.get("id") or "")
        if not lid or lid in used:
            lid = next(f"lake-{n}" for n in range(1, 10000) if f"lake-{n}" not in used and not any(
                str(o.get("id")) == f"lake-{n}" for o in raw or [] if isinstance(o, dict)))
        used.add(lid)
        out.append({"id": lid, "pts": pts, "width_m": round(width, 1)})
    return out


def _dig_lakes(base: list, xs: list, ys: list, lakes: list, sea, depth: float, shore: float):
    """The lakes drawn by hand, dug into the natural ground (rows `base`, cell centres xs / ys): (rows, water
    line). Each is a line with a width. Inside it the ground goes down to a bed: a beach at its edge, the world's
    water depth in its middle. Outside it the land comes down to the shore as a bank no steeper than
    LAKE_BANK_GRADE, so a lake drawn on a hill lies in a valley, never in a pit. A world has ONE water line: its
    own, or, with none, one just under its lowest ground (so no other land floods), and every lake is dug to it.
    A lake is never narrower than two cells of the terrain, the least the grid can hold."""
    if not lakes:
        return base, sea
    grid, cell = len(xs), (xs[1] - xs[0]) if len(xs) > 1 else 1.0
    if sea is None:
        sea = min(min(r) for r in base) - shore - 100.0
    top = sea + shore  # where a lake's edge lies: the last of the beach
    for lake in lakes:
        pts = lake["pts"]
        segs = list(zip(pts, pts[1:])) or [(pts[0], pts[0])]
        half = max(lake["width_m"] * 50.0, cell)
        reach = half + LAKE_BANK_CM[1]
        x0, x1 = min(p[0] for p in pts) - reach, max(p[0] for p in pts) + reach
        y0, y1 = min(p[1] for p in pts) - reach, max(p[1] for p in pts) + reach
        for j in range(max(0, int((y0 - ys[0]) // cell)), min(grid, int((y1 - ys[0]) // cell) + 2)):
            for i in range(max(0, int((x0 - xs[0]) // cell)), min(grid, int((x1 - xs[0]) // cell) + 2)):
                d = min(_dist_seg((xs[i], ys[j]), a, b) for a, b in segs)
                z = base[j][i]
                if d <= half:
                    bed = top - (depth + 2 * shore) * _smoothstep(0.0, 1.0, 1.25 * (1.0 - d / half))
                    base[j][i] = min(z, bed)
                elif z > top:
                    bank = max(LAKE_BANK_CM[0], min(LAKE_BANK_CM[1], (z - top) / LAKE_BANK_GRADE))
                    if d - half < bank:
                        base[j][i] = min(z, top + (z - top) * _smoothstep(0.0, 1.0, (d - half) / bank))
    return base, sea


def terrain(seed: int, size_cm: float, relief_cm: float, cities: list, grid: int = 160,
            center: tuple = (0.0, 0.0), opts: dict | None = None, keep: list | None = None,
            lakes: list | None = None) -> dict:
    """Hills (natural ground; option city_ground 'flatten' levels the land under every city, never its water). Same shape as a ground scan:
    {min, max, grid, heights[j][i]} with cell centres, so the 3D view and drape read it as is.
    lakes (lake_list): lakes drawn by hand, dug into the natural ground before anything is levelled on it."""
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
    for _ in range(round(o["smooth"] * 4)):  # 3x3 box passes: the octaves' sharp little bumps rounded off
        noise = [[sum(noise[b][a] for b in (max(0, j - 1), j, min(grid - 1, j + 1))
                      for a in (max(0, i - 1), i, min(grid - 1, i + 1))) / 9 for i in range(grid)] for j in range(grid)]
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
    if o["island"] != "none":
        sea = 0.0 if sea is None else sea
        base = _islands(base, xs, ys, seed, size_cm, center, cities, o["island"], sea, depth, shore,
                        o["flatten_m"] * 100)
    base, sea = _dig_lakes(base, xs, ys, lakes or [], sea, depth, shore)

    def at(x, y):
        i = max(0, min(grid - 1, int((x - lo[0]) / size_cm * grid)))
        j = max(0, min(grid - 1, int((y - lo[1]) / size_cm * grid)))
        return base[j][i]

    blend = o["flatten_m"] * 100
    # a city's plateau is its centre's ground (the level just over the water when the centre is in a lake)
    plateaus = [(c, max(at(c["x"], c["y"]), (sea + shore * 2) if sea is not None else -1e9)) for c in cities] \
        if o["city_ground"] == "flatten" else []
    # ... and it is the LAND's plateau: water is never lifted out of the map. A lake or the sea keeps its place and
    # its own shore whatever city sits over it, and the plateau comes up from the shore over the same blend, so
    # moving a city never moves the water (it filled the lake it landed on and let the one it left come back).
    cell = size_cm / grid
    dry: dict = {}  # (j, i) -> cells from the nearest water, for the cells near any
    if plateaus and sea is not None:
        edge = [(j, i) for j in range(grid) for i in range(grid) if base[j][i] < sea + shore]
        reach = int(max(blend, 2 * cell) // cell) + 1
        seen = set(edge)
        for d in range(1, reach + 1 if edge else 0):
            nxt = []
            for j, i in edge:
                for c in ((j - 1, i), (j + 1, i), (j, i - 1), (j, i + 1), (j - 1, i - 1), (j - 1, i + 1), (j + 1, i - 1), (j + 1, i + 1)):
                    if c not in seen and 0 <= c[0] < grid and 0 <= c[1] < grid:
                        seen.add(c)
                        dry[c] = d
                        nxt.append(c)
            edge = nxt
        dry.update({c: 0 for c in seen if c not in dry})
    rows = []
    for j, y in enumerate(ys):
        row = []
        for i, x in enumerate(xs):
            h = base[j][i]
            for c, flat in plateaus:
                w = 1 - _smoothstep(c["radius"] * 1.05, c["radius"] * 1.05 + blend, math.hypot(x - c["x"], y - c["y"]))
                if (j, i) in dry:
                    w *= _smoothstep(0, max(blend, 2 * cell), dry[(j, i)] * cell)
                h += (flat - h) * w
            for k in keep or []:  # last: whatever is built there sits exactly on its own ground
                h += (k["z"] - h) * (1 - _smoothstep(0, max(blend, 5000.0), keep_gap(k, x, y)))
            row.append(round(h, 1))
        rows.append(row)
    return {"min": lo, "max": hi, "grid": grid, "heights": rows, "sea": sea}


ISLAND_COAST = 0.25  # the coast: land falls into the sea over this share of an island's radius
ISLAND_WOBBLE = 0.22  # how far the coastline wanders in and out (share of the radius)


def _islands(base: list, xs: list, ys: list, seed: int, size_cm: float, center: tuple, cities: list, mode: str,
             sea: float, depth: float, shore: float, blend: float) -> list:
    """The hills cut to islands: land inside each island (raised so its lowest ground is a beach over the sea),
    open sea outside, a wandering coastline between. 'one': one island round the map's middle, big enough for
    every city; 'many': one per city and a few islets. Each city's circle (+ its flatten blend) is always land."""
    rnd = random.Random(seed * 31 + 7)
    half = size_cm / 2
    discs = []  # (x, y, radius)
    if mode == "one":
        r = 0.36 * size_cm
        for c in cities:
            r = max(r, math.hypot(c["x"] - center[0], c["y"] - center[1]) + c["radius"] * 1.15 + blend)
        discs.append((center[0], center[1], min(r, 0.47 * size_cm)))
    else:
        for c in cities:
            discs.append((c["x"], c["y"], c["radius"] * 1.3 + blend))
        for _ in range(rnd.randint(2, 4) + (3 if not cities else 0)):  # islets out in the sea
            discs.append((center[0] + rnd.uniform(-0.75, 0.75) * half, center[1] + rnd.uniform(-0.75, 0.75) * half,
                          size_cm * rnd.uniform(0.04, 0.09)))
    keep = [(c["x"], c["y"], c["radius"] * 1.08 + blend) for c in cities]  # never water inside these
    wob = _fbm(seed + 101, 3, 0.5)
    lo_h = min(min(r) for r in base)
    floor = sea - depth - shore  # the open sea's bed
    out = []
    for j, y in enumerate(ys):
        row = []
        for i, x in enumerate(xs):
            land = 0.0
            for cx, cy, r in discs:
                d = math.hypot(x - cx, y - cy)
                a = math.atan2(y - cy, x - cx)
                rr = r * (1 + ISLAND_WOBBLE * (2 * wob(3 + math.cos(a) * 1.7 + cx / 1e6, 3 + math.sin(a) * 1.7 + cy / 1e6) - 1))
                land = max(land, _smoothstep(0.0, ISLAND_COAST * r, rr - d))
            for cx, cy, r in keep:
                land = max(land, 1.0 - _smoothstep(r, r * 1.15, math.hypot(x - cx, y - cy)))
            high = sea + shore * 2 + (base[j][i] - lo_h) * 0.6  # the island's own hills, all above the water
            row.append(round(floor + (high - floor) * land, 1))
        out.append(row)
    return out


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


BANK_GRADE = 0.5  # the steepest a lot may leave the ground beside a road: 1 in 2, an earth bank that stands
BANK_RINGS = 3  # ... kept for this many cells out from the road's own


def _beside_roads(rows: list, locked, grid: int, rings: int = BANK_RINGS) -> dict:
    """{(j, i): (cells out from the nearest road cell, that cell's height)} for every cell within `rings` of a
    road's own cells."""
    near: dict = {}
    edge = [(j, i, rows[j][i]) for j, i in locked]
    for d in range(1, rings + 1):
        nxt = []
        for j, i, z in edge:
            for c in ((j - 1, i), (j + 1, i), (j, i - 1), (j, i + 1), (j - 1, i - 1), (j - 1, i + 1), (j + 1, i - 1), (j + 1, i + 1)):
                if c not in near and c not in locked and 0 <= c[0] < grid and 0 <= c[1] < grid:
                    near[c] = (d, z)
                    nxt.append((c[0], c[1], z))
        edge = nxt
    return near


def cut_pads(ter: dict, pads: list, locked=(), blend: float = 0.0) -> dict:
    """Every lot (pcg_layers.pads) cut level into a fine terrain grid, a cell at a time, blending back into the
    hillside. Cells a road owns (`locked`, from grade_to_roads) keep the road's height: a street always wins
    over the lot beside it, so the ground never climbs over a kerb.
    Beside a road a pad never leaves the ground steeper than BANK_GRADE: a lot levelled 6 m above the street it
    fronts stood on a dirt cliff one landscape pixel wide. The ground now comes down to the road as a bank and the
    building's own foundation holds the rest of the step (kit_bake.foundation, measured by footings)."""
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
    near = _beside_roads(rows, locked if isinstance(locked, (set, frozenset)) else set(locked), grid) if locked else {}
    step = BANK_GRADE * max(cw, ch)
    for (j, i), (e, pz) in best.items():
        z = rows[j][i]
        z += (pz - z) * (1 - _smoothstep(0, blend, e))
        if (j, i) in near:
            d, zr = near[(j, i)]
            z = max(zr - d * step, min(zr + d * step, z))
        rows[j][i] = round(z, 1)
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
        # graded to the roads exactly as they are pushed (straight from corner to corner, road_network_roads_pcg),
        # never to the point-per-bump line they were drawn with: the two must meet everywhere
        pts_, segs_ = st.straighten(a["points"], a["segments"])
        by_i = {p["i"]: p for p in pts_}
        links: dict = {}  # mesh-less links per point: a piece with one is a dead end's turnaround
        for s in segs_:
            if not s.get("mesh"):
                for n in (s["a"], s["b"]):
                    links[n] = links.get(n, 0) + 1
        for s in segs_:
            pa, pb = by_i.get(s["a"]), by_i.get(s["b"])
            mesh = (s.get("mesh") or "").split("/")[-1]
            if not pa or not pb:
                continue
            half = HALF.get(next((k for k, m in ROAD_MESH.items() if m.split("/")[-1] == mesh), ""), 800.0)
            if not mesh:  # a turnaround is graded round its whole width, like the road it ends (its Width)
                half = max([half] + [float(p.get("w") or 0) for p in (pa, pb) if p.get("mesh") and links.get(p["i"]) == 1])
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


HEIGHTMAP_RES = 2017  # 32 components x 63 quads + 1; the world being worked on sets its own (landscape_res)
UEFN_MAX_SAMPLES = 2048 * 2048  # UEFN: every landscape in the level together, at most this many height samples


def landscape_res(opts: dict | None) -> int:
    """The heightmap side a world asked for, snapped to 63 quads x whole components + 1."""
    return 63 * max(8, round((options(opts)["landscape_px"] - 1) / 63)) + 1


def heightmap_px(ter: dict, res: int = 0) -> float:
    """One heightmap pixel in cm: the finest step the imported landscape has."""
    res = res or HEIGHTMAP_RES
    return max(ter["max"][0] - ter["min"][0], ter["max"][1] - ter["min"][1]) / (res - 1)


def pad_margin(ter: dict, res: int = 0) -> float:
    """How much wider (cm, both sides together) a lot's pad is cut than the building: a pixel a side, so every
    landscape quad under the building has all four corners exactly at pad height after import."""
    return max(400.0, 2 * heightmap_px(ter, res) + 100)


def _pixel(ter: dict, res: int, pads: list | None):
    """(i, j) -> the heightmap's 16-bit sample there (32768 = 0, 128 per metre), lots levelled in."""
    lo, hi = ter["min"], ter["max"]
    ground = graded(ter, pads or [], max(PAD_BLEND_CM, 1.5 * heightmap_px(ter, res)))
    return lambda i, j: max(0, min(65535, int(round(32768 + ground(lo[0] + (hi[0] - lo[0]) * i / (res - 1),
                                                                   lo[1] + (hi[1] - lo[1]) * j / (res - 1)) * 128 / 100))))


def heightmap_rows(ter: dict, res: int = 0, pads: list | None = None) -> list:
    """The landscape UEFN gets: res x res 16-bit samples, lots levelled in."""
    res = res or HEIGHTMAP_RES
    px = _pixel(ter, res, pads)
    return [[px(i, j) for i in range(res)] for j in range(res)]


def imported_ground(ter: dict, res: int = 0, pads: list | None = None):
    """z(x, y) of the imported landscape exactly as UEFN builds it: bilinear between the heightmap's samples
    (computed only where asked, so checking every building costs a few thousand samples, not a million)."""
    res = res or HEIGHTMAP_RES
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
        pts = _under(x, y, yaw, w_m, d_m)
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


def misfit_gate(fit: dict | None) -> str:
    """'' when a push may go, else why not: buildings still buried or floating after the build gave each the
    foundation it needs (ground_fit's `after`). No fit yet (no ground to check against) never blocks."""
    if not fit or not (fit.get("buried") or fit.get("floating")):
        return ""
    spots = ", ".join(f"{m['label']} (x={m['x']} y={m['y']})" for m in (fit.get("misfits") or [])[:10])
    return (f"Not pushed: {fit.get('buried', 0)} buildings buried and {fit.get('floating', 0)} floating on this "
            f"ground: {spots}. Fix them (road_network_edit move/delete, or the lots) or push with allow_misfits=true.")


def _under(x, y, yaw, w_m, d_m) -> list:
    """The nine spots a building is checked at: its corners, the middle of each edge and its centre."""
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    return [(x + c * a * d_m * 50 - s * b * w_m * 50, y + s * a * d_m * 50 + c * b * w_m * 50)
            for a in (-1, 0, 1) for b in (-1, 0, 1)]


def footings(ground, buildings: list) -> list:
    """How far the ground falls away under each building's floor, in metres (0 on level ground): what its
    foundation has to cover, measured on the ground the landscape really has there. buildings as ground_fit takes
    them. (A lot's first estimate is made before its pad is cut: on a pad filled level it said metres of
    foundation where none shows.)"""
    return [round(max(0.0, z - min(ground(px, py) for px, py in _under(x, y, yaw, w_m, d_m))) / 100, 1)
            for _l, x, y, yaw, w_m, d_m, z, *_r in buildings]


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


def _grey_png(rows: list, bits: int) -> bytes:
    """Greyscale PNG (8 or 16 bit) of square rows of samples."""
    res = len(rows)
    fmt = f">{res}H" if bits == 16 else f"{res}B"
    raw = bytearray()
    for vals in rows:
        raw += b"\x00" + struct.pack(fmt, *vals)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", res, res, bits, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def weightmap_rows(ter: dict, res: int, discs: list, pads: list) -> list:
    """The paint-layer mask for Landscape > Import (same pixels as the heightmap): 255 under every road
    (discs: (x, y, half width) cm, pcg_layers.surface_samples) and every levelled lot (pcg_layers.pads), 0 elsewhere."""
    (x0, y0), (x1, y1) = ter["min"], ter["max"]
    sx, sy = (res - 1) / (x1 - x0), (res - 1) / (y1 - y0)
    rows = [bytearray(res) for _ in range(res)]

    def box(x, y, r):
        return (range(max(0, math.floor((x - r - x0) * sx)), min(res - 1, math.ceil((x + r - x0) * sx)) + 1),
                range(max(0, math.floor((y - r - y0) * sy)), min(res - 1, math.ceil((y + r - y0) * sy)) + 1))
    # ponytail: hard 0/255 edge (one pixel ~2 m); feather it here if the seam shows
    for x, y, r in discs:
        ii, jj = box(x, y, r)
        for j in jj:
            dy2 = (y0 + j / sy - y) ** 2
            row = rows[j]
            for i in ii:
                if (x0 + i / sx - x) ** 2 + dy2 <= r * r:
                    row[i] = 255
    for x, y, c, s, hw, hd, *_z in pads:  # same frame as graded(): hd along (c, s), hw across
        ii, jj = box(x, y, math.hypot(hw, hd))
        for j in jj:
            dy = y0 + j / sy - y
            row = rows[j]
            for i in ii:
                dx = x0 + i / sx - x
                if abs(dx * c + dy * s) <= hd and abs(-dx * s + dy * c) <= hw:
                    row[i] = 255
    return rows


def weightmap_png(ter: dict, res: int, discs: list, pads: list) -> bytes:
    return _grey_png(weightmap_rows(ter, res, discs, pads), 8)


def heightmap_png(ter: dict, res: int = 0, pads: list | None = None, rows: list | None = None) -> tuple:
    """16-bit greyscale PNG of the terrain for Landscape > Import, plus the import settings
    (Z scale 100: 1 unit = 100/128 cm, so +-256 m fits). pads: lots levelled into it."""
    lo, hi = ter["min"], ter["max"]
    rows = rows or heightmap_rows(ter, res or HEIGHTMAP_RES, pads)
    res = len(rows)
    png = _grey_png(rows, 16)
    scale = (hi[0] - lo[0]) / (res - 1)
    # Landscape mode's New > Location is the landscape's centre, not its corner; 1009 = 16 components x 63 quads + 1
    return png, {"resolution": f"{res} x {res}", "location": [round((lo[0] + hi[0]) / 2), round((lo[1] + hi[1]) / 2), 0],
                 "scale": [round(scale, 3), round(scale, 3), 100.0],
                 # more than 16 components a side spans several World Partition regions (region size 16), and
                 # UEFN's import repeated one region's heights in each (the map came in as 4 copies of a quarter):
                 # 2017 is imported as 16 x 16 components of 2 x 2 sections instead, one region
                 "components": (f"{(res - 1) // 63} x {(res - 1) // 63}, 63 quads, 1 section" if (res - 1) // 63 <= 16
                                else f"{(res - 1) // 126} x {(res - 1) // 126}, 63 quads, 2x2 sections"),
                 "metres_per_pixel": round(scale / 100, 2),
                 "uefn_limit": f"{res * res:,} of {UEFN_MAX_SAMPLES:,} samples (2048 x 2048)"}


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
                   for i, a in enumerate(towns) for b in towns[i + 1:] if not (a.get("pinned") or b.get("pinned")))

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
    of open country lies between every two edges; cities already far enough apart stay where they are. A city the
    person put somewhere by hand ('pinned') stays there and pushes nobody: it may lie as close to another city as
    it was put, even over it (yield_plan: the bigger city keeps the land they share)."""
    towns = [dict(t) for t in towns]
    for _ in range(60):
        moved = False
        for i in range(len(towns)):
            for j in range(i + 1, len(towns)):
                a, b = towns[i], towns[j]
                if a.get("pinned") or b.get("pinned"):
                    continue
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
    """A city's districts as the panel and the placement read them: the downtown and normal rings (radii along
    the way from its centre district to its edge, `zone_share`), the housing beyond, the industrial wedge, and
    `core` when the centre district was moved off the city's own centre (cm from it)."""
    o = city_options(city, opts)
    r = city["radius"]
    # one wedge of the outer rings is the industrial zone (factories, warehouses): its direction from the seed,
    # or the one it was given by hand (`industry_deg`), turned with the city
    base = float(city["industry_deg"]) if city.get("industry_deg") is not None else (int(city.get("seed", 0)) * 137) % 360
    core = city.get("core") or [0, 0]
    return {"downtown": {"radius": round(r * o["downtown"])}, "normal": {"radius": round(r * max(o["normal"], o["downtown"]))},
            "housing": {"radius": round(r)},
            "industrial": {"angle": (base + float(city.get("spin") or 0.0)) % 360, "spread": 75},
            **({"core": [round(core[0]), round(core[1])]} if core[0] or core[1] else {})}


SHAPE_POINTS = 16  # a hand-drawn outline: this many handles round the city, each a factor of its radius
SHAPE_RANGE = (0.74, 1.26)  # ... inside what every outline test already allows for (pcg_layers.OUTLINE_MIN / MAX)
BLOCK_ZONES = ("downtown", "normal", "housing", "industrial")  # what a block can be zoned as
BLOCK_KINDS = {  # what a block can be laid out as: neighbourhoods.TYPES (never 'collector', a superblock's own) + park
    "parcels": "Plain lots on the streets", "grid": "Subdivision: grid", "loop": "Subdivision: loop",
    "cul_de_sac": "Subdivision: cul-de-sacs", "organic": "Subdivision: curving streets",
    "branch": "Subdivision: winding street", "garden": "Garden apartments", "suburban": "Suburban apartments",
    "urban": "Urban apartments", "park": "Park (trees, nothing built)"}


def seed_turn(city: dict) -> float:
    """The grid angle a city's seed gives it, degrees (the first draw of city_graph's seeded random)."""
    return math.degrees(random.Random(city.get("seed", 1)).uniform(0, math.pi / 2))


def outline(city: dict, angle_deg: float) -> float:
    """How far the city (and each of its district rings) reaches in this direction, as a factor of its
    radius (~0.75..1.25): a few seeded waves, so no city or district is a perfect circle. A city turned by hand
    (`spin`, degrees) turns its outline with it; one whose outline was drawn by hand (`shape`, SHAPE_POINTS
    factors round it, the first at the city's own 0 degrees) follows those, eased from handle to handle."""
    angle_deg -= city.get("spin") or 0.0
    shape = city.get("shape")
    if shape:
        t = (angle_deg % 360.0) / 360.0 * len(shape)
        i = int(t) % len(shape)
        a, b = shape[i], shape[(i + 1) % len(shape)]
        return a + (b - a) * (1 - math.cos(math.pi * (t - int(t)))) / 2
    s = int(city.get("seed", 1))
    a = math.radians(angle_deg)
    f = 0.0
    for k, amp in ((2, 0.11), (3, 0.08), (5, 0.05), (7, 0.025)):
        phase = ((s * (k * 7919 + 13)) % 997) / 997 * 2 * math.pi
        f += amp * math.sin(k * a + phase)
    return 1.0 + f


YIELD_CM = 3000.0  # a city's streets stop this far short of the edge of a city it gives way to


def rank(city: dict) -> tuple:
    """Which of two cities keeps the land they share: the bigger one; the name settles a tie."""
    return (-float(city["radius"]), str(city.get("name") or ""))


def yield_plan(cities: list) -> None:
    """Cities that overlap (put close by hand, or their outlines bulging into each other): the bigger one keeps
    the shared land. Sets city['yield_to'] on every city that gives way: the outlines it stays out of ([{name, x,
    y, radius, seed, spin, shape}]). Its streets, lots and freeways stop at them, so no street is laid on another
    and nothing is built twice."""
    for c in cities:
        over = [{k: o[k] for k in ("name", "x", "y", "radius", "seed", "spin", "shape") if o.get(k) is not None}
                for o in cities if o is not c and rank(o) < rank(c)
                and math.hypot(o["x"] - c["x"], o["y"] - c["y"]) < (o["radius"] + c["radius"]) * 1.3]
        if over:
            c["yield_to"] = over
        else:
            c.pop("yield_to", None)


def yielded(city: dict, x: float, y: float, margin: float = YIELD_CM) -> bool:
    """Is this spot inside a city this one gives way to, or within `margin` of its edge?"""
    for o in city.get("yield_to") or ():
        dx, dy = x - o["x"], y - o["y"]
        d = math.hypot(dx, dy)
        if d < o["radius"] * 1.27 + margin and d < o["radius"] * outline(o, math.degrees(math.atan2(dy, dx))) + margin:
            return True
    return False


_REACH: dict = {}  # a city with its centre district moved -> how far its edge lies from that centre, per degree


def core_reach(city: dict) -> list | None:
    """For a city whose centre district was moved (`core`: cm from the city's own centre): the distance from that
    centre to the city's edge in each of 360 directions. None for a city whose districts are centred on it."""
    core = city.get("core")
    if not core or not (core[0] or core[1]):
        return None
    key = (city["x"], city["y"], city["radius"], city.get("seed"), city.get("spin"), tuple(city.get("shape") or ()),
           core[0], core[1])
    got = _REACH.get(key)
    if got is None:
        R, cx, cy = city["radius"], city["x"], city["y"]
        px, py = cx + core[0], cy + core[1]
        out_ = lambda x, y: math.hypot(x - cx, y - cy) > R * outline(city, math.degrees(math.atan2(y - cy, x - cx)))
        got = []
        for k in range(360):
            dx, dy = math.cos(math.radians(k)), math.sin(math.radians(k))
            t, step = 0.0, R / 40
            while t < 2.8 * R and not out_(px + dx * (t + step), py + dy * (t + step)):
                t += step
            lo, hi = t, t + step  # the first time the ray leaves the city
            for _ in range(16):
                mid = (lo + hi) / 2
                lo, hi = (lo, mid) if out_(px + dx * mid, py + dy * mid) else (mid, hi)
            got.append((lo + hi) / 2)
        if len(_REACH) > 64:
            _REACH.clear()
        _REACH[key] = got
    return got


def clamp_core(city: dict, dx: float, dy: float) -> list:
    """Where a city's centre district may be put: (dx, dy) from the city's own centre, pulled back to CORE_MAX
    of the way to its edge. [0, 0] when that is at (or within 20 m of) the city's centre."""
    d = math.hypot(dx, dy)
    if d < 2000:
        return [0, 0]
    edge = city["radius"] * outline(city, math.degrees(math.atan2(dy, dx)))
    k = min(1.0, CORE_MAX * edge / d)
    return [round(dx * k), round(dy * k)]


def zone_share(city: dict, x: float, y: float) -> float:
    """Distance from the centre as a share of the city's (irregular) edge in that direction: <downtown
    share = core, <normal share = neighbourhood, <1 = suburb. The centre is the city's own, or the spot its
    centre district was moved to (`core`): the rings then lie round that spot, squeezed on the side where the
    edge is near and stretched on the far side, and the city's edge stays where it is (share 1 on it)."""
    reach = core_reach(city)
    if reach is None:
        dx, dy = x - city["x"], y - city["y"]
        return math.hypot(dx, dy) / (city["radius"] * outline(city, math.degrees(math.atan2(dy, dx))))
    dx, dy = x - city["x"] - city["core"][0], y - city["y"] - city["core"][1]
    t = math.degrees(math.atan2(dy, dx)) % 360.0
    i = int(t) % 360
    return math.hypot(dx, dy) / (reach[i] + (reach[(i + 1) % 360] - reach[i]) * (t - int(t)))


def orientation(city: dict) -> float:
    """RoadOrientation: the city's grid angle in degrees: the one it was turned to by hand (`turn`), else the one
    its seed gives it."""
    return float(city["turn"]) % 360.0 if city.get("turn") is not None else seed_turn(city)


def block_xy(o: dict) -> tuple:
    """A block's two sides in cm, centre line to centre line: (across the city's grid angle, along it)."""
    b = o["block_m"] * 100
    return b, b * o.get("block_ratio", 1.0)


def _bow(seed, tag: str):
    """One seeded wave over the street grid, -1..1: (steps along a street, which street line) -> how far that
    street is off its grid line there. Long waves (5 to 14 blocks) that drift slowly from one line to the next:
    a street bends as a whole, and the streets beside it bend with it."""
    r = random.Random(f"bow:{seed}:{tag}")
    l1, l2, p1, p2, drift = r.uniform(5.0, 8.0), r.uniform(9.0, 14.0), r.random(), r.random(), r.uniform(0.03, 0.07)
    tau = 2 * math.pi
    return lambda along, line: (0.65 * math.sin(tau * (along / l1 + line * drift + p1))
                                + 0.35 * math.sin(tau * (along / l2 - line * drift + p2)))


HILL_MOVE = 1.5  # a crossing leaves its grid place by this many blocks at most, to follow a hillside
HILL_UNIT = 0.04  # ... kept in steps of this share of a block (_pack: -39..39 of them)
HILL_ZIG = 0.10  # a street that climbs steep ground swings this far to each side, block after block
HILL_TURN_DEG = 24.0  # ... and no street turns more than this at a crossing for the hills (it bends, never elbows)
HILL_PASSES = 40  # rounds of easing the streets onto the hillside
HILL_NET = 0.4  # how much of a crossing's move is its neighbours' (the net that keeps blocks whole)
HILL_HOLD = 0.99  # ... and how much of it lasts a round (a weak pull back to the grid)
STEEP_STREET = 0.12  # a local street that would climb steeper than this is left out: the blocks lie along the hill


def _pack(vals, unit: float = 0.01) -> str:
    """Numbers in steps of `unit`, -39..39 steps, a printable character each: a city's hills in a few KB of text."""
    return "".join(chr(87 + max(-39, min(39, int(round(v / unit))))) for v in vals)


def hill_at(hills: dict | None, ix: int, iy: int) -> tuple:
    """A grid crossing's part of the city's hills (plan_hills): (how far it moves along the grid's two directions
    for the hillside, in blocks; the ground's grade along those two directions where it ends up)."""
    if not hills:
        return 0.0, 0.0, 0.0, 0.0
    kx, ky = hills["k"]
    if abs(ix) > kx or abs(iy) > ky:
        return 0.0, 0.0, 0.0, 0.0
    n = (ix + kx) * (2 * ky + 1) + (iy + ky)
    return ((ord(hills["u"][n]) - 87) * HILL_UNIT, (ord(hills["v"][n]) - 87) * HILL_UNIT,
            (ord(hills["gu"][n]) - 87) / 100, (ord(hills["gv"][n]) - 87) / 100)


def plan_hills(cities: list, ground, opts: dict | None = None) -> None:
    """TerrainAnalyzer for the streets, before any is laid: sets city['hills'] where a city (not a strict grid)
    stands on sloping ground. Real towns do not lay one square grid over a hill, so the grid is eased onto it:
      - a street across a slope follows the hillside. Round after round every crossing moves up or down the
        slope toward the height of its neighbours along the street it lies across the slope of, so that street
        comes to run at one even grade round the hill; its neighbours are carried along (the grid stretches
        like a net, it never tears), and far more than a block where the hill asks for it;
      - a street up the slope winds: on steep ground its crossings swing to one side and the other (HILL_ZIG);
      - no street turns more than HILL_TURN_DEG for it, and no block is squeezed under half its size;
      - nothing moves beside a freeway (everything that lands on one is laid out straight), nor downtown in a
        Mixed city.
    Kept per crossing as text (_pack): the two moves in blocks and the ground's two grades there, which
    city_graph reads to leave out local streets that would climb steeper than STEEP_STREET."""
    from . import freeways
    o = options(opts)
    for c in cities:
        c.pop("hills", None)
        co = city_options(c, o)
        style = c.get("style", "mixed")
        if ground is None or style == "grid" or not co["follow_hills"]:
            continue
        bx, by = block_xy(co)
        turn = math.radians(orientation(c))
        cs, sn = math.cos(turn), math.sin(turn)
        R, down = c["radius"], co["downtown"]
        kx, ky = int(R * 1.3 // bx), int(R * 1.3 // by)
        nx, ny = 2 * kx + 1, 2 * ky + 1
        H = lambda lx, ly: ground(c["x"] + cs * lx - sn * ly, c["y"] + sn * lx + cs * ly)
        e = 0.25 * min(bx, by)
        fw = None
        if co.get("freeways", "yes") == "yes" and R >= freeways.FW_MIN_R:
            fw = (round(down * R / bx) + 0.5, -round(down * R / by) - 0.5)
        # how freely each crossing moves: not beside a freeway, not downtown in a mixed city
        free = [[0.0] * ny for _ in range(nx)]
        for i in range(nx):
            for j in range(ny):
                ix, iy = i - kx, j - ky
                share = zone_share(c, c["x"] + cs * ix * bx - sn * iy * by, c["y"] + sn * ix * bx + cs * iy * by)
                ease = _smoothstep(down, down + 0.15, share) if style == "mixed" else 1.0
                near = min(abs(ix - fw[0]), abs(iy - fw[1])) if fw else 9.0
                free[i][j] = ease * _smoothstep(1.5, 3.5, near)
        # (where each crossing stands before the hills move it: the grid, bowed and drifted)
        local = _grid_local(c, co)[0]
        base = [[local(i - kx, j - ky)[:2] for j in range(ny)] for i in range(nx)]
        du = [[0.0] * ny for _ in range(nx)]  # cm, along the grid's two directions
        dv = [[0.0] * ny for _ in range(nx)]
        h = [[None] * ny for _ in range(nx)]
        gu = [[0.0] * ny for _ in range(nx)]
        gv = [[0.0] * ny for _ in range(nx)]

        def read():  # the ground and its fall at every crossing, where it stands now
            live = 0.0
            for i in range(nx):
                for j in range(ny):
                    lx, ly = base[i][j][0] + du[i][j], base[i][j][1] + dv[i][j]
                    h[i][j] = H(lx, ly)
                    near = (H(lx + e, ly), H(lx - e, ly), H(lx, ly + e), H(lx, ly - e))
                    if h[i][j] is None or None in near:
                        h[i][j], gu[i][j], gv[i][j] = None, 0.0, 0.0
                    else:
                        gu[i][j], gv[i][j] = (near[0] - near[1]) / (2 * e), (near[2] - near[3]) / (2 * e)
                        if free[i][j] > 0:
                            live = max(live, _smoothstep(0.02, 0.07, math.hypot(gu[i][j], gv[i][j])))
            return live
        if read() < 0.3:
            continue  # flat ground: the grid stays as it is
        cap = HILL_MOVE * min(bx, by)
        for _ in range(HILL_PASSES):
            nu, nv = [row[:] for row in du], [row[:] for row in dv]
            for i in range(nx):
                for j in range(ny):
                    a, b = gu[i][j], gv[i][j]
                    s = math.hypot(a, b)
                    w = free[i][j] * _smoothstep(0.02, 0.07, s)
                    if h[i][j] is None or w <= 0:
                        continue
                    mu = mv = 0.0
                    for di, dj in ((1, 0), (0, 1)):  # the two streets through this crossing
                        nb = [(i + di * m, j + dj * m) for m in (-1, 1)
                              if 0 <= i + di * m < nx and 0 <= j + dj * m < ny and h[i + di * m][j + dj * m] is not None]
                        if not nb:
                            continue
                        p, q = nb[0], nb[-1]
                        tx = base[q[0]][q[1]][0] - base[p[0]][p[1]][0] + du[q[0]][q[1]] - du[p[0]][p[1]]
                        ty = base[q[0]][q[1]][1] - base[p[0]][p[1]][1] + dv[q[0]][q[1]] - dv[p[0]][p[1]]
                        tl = math.hypot(tx, ty)
                        if tl < 1:
                            tx, ty, tl = di * bx, dj * by, math.hypot(di * bx, dj * by)
                        across = 1.0 - ((tx * a + ty * b) / (tl * s)) ** 2  # 1: the street lies across the slope
                        want = sum(h[m][n] for m, n in nb) / len(nb) - h[i][j]
                        mu += a * want / (s * s) * across
                        mv += b * want / (s * s) * across
                    m = math.hypot(mu, mv)
                    lim = 0.12 * min(bx, by)
                    if m > lim:
                        mu, mv = mu * lim / m, mv * lim / m
                    nu[i][j] += 0.5 * w * mu
                    nv[i][j] += 0.5 * w * mv
            for f, g in ((du, nu), (dv, nv)):  # the net: each crossing is drawn toward its neighbours' move
                for i in range(nx):
                    for j in range(ny):
                        acc, n = 0.0, 0
                        for a, b in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)):
                            if 0 <= a < nx and 0 <= b < ny:
                                acc += g[a][b]
                                n += 1
                        v = ((1 - HILL_NET) * g[i][j] + HILL_NET * acc / n) * HILL_HOLD * min(1.0, free[i][j] * 4)
                        f[i][j] = max(-cap, min(cap, v))
            read()
        for i in range(nx):  # a street up a steep slope winds
            for j in range(ny):
                a, b = gu[i][j], gv[i][j]
                s = math.hypot(a, b)
                steep = _smoothstep(0.06, 0.14, s) * free[i][j]
                if steep > 0:
                    du[i][j] += HILL_ZIG * bx * (1 if (j - ky) % 2 else -1) * (b * b / (s * s)) * steep
                    dv[i][j] += HILL_ZIG * by * (1 if (i - kx) % 2 else -1) * (a * a / (s * s)) * steep

        def spot(i, j):
            return base[i][j][0] + du[i][j], base[i][j][1] + dv[i][j]

        def bend(i, j, di, dj):  # how far the street through three crossings turns at the middle one, degrees
            p0, p1, p2 = spot(i - di, j - dj), spot(i, j), spot(i + di, j + dj)
            a1, a2 = math.atan2(p1[1] - p0[1], p1[0] - p0[0]), math.atan2(p2[1] - p1[1], p2[0] - p1[0])
            return abs((math.degrees(a2 - a1) + 180) % 360 - 180)
        for _ in range(30):  # ease any crossing that turns too sharply, or squeezes a block, toward its neighbours
            moved = False
            for di, dj in ((1, 0), (0, 1)):
                rest = bx if di else by
                for i in range(di, nx - di):
                    for j in range(dj, ny - dj):
                        short = min(math.dist(spot(i, j), spot(i - di, j - dj)), math.dist(spot(i, j), spot(i + di, j + dj)))
                        if bend(i, j, di, dj) > HILL_TURN_DEG or short < 0.6 * rest:
                            for f in (du, dv):
                                f[i][j] = 0.5 * f[i][j] + 0.25 * (f[i - di][j - dj] + f[i + di][j + dj])
                            moved = True
            if not moved:
                break
        read()
        c["hills"] = {"k": [kx, ky],
                      "u": _pack((v / bx for row in du for v in row), HILL_UNIT),
                      "v": _pack((v / by for row in dv for v in row), HILL_UNIT),
                      "gu": _pack(v for row in gu for v in row), "gv": _pack(v for row in gv for v in row)}


MEANDER = 0.5  # where a city is not a strict grid its whole street net drifts off the grid's lines by up to this
# share of a block, main roads and the streets between them together ...
MEANDER_WAVES = ((10.0, 14.0, 0.5), (7.0, 10.0, 0.3), (7.0, 10.0, 0.2))  # ... as three waves (shortest, longest in
# blocks; its share): a road swings a block's width over half a kilometre, never more than ~15 degrees at a crossing
MEANDER_FROM = 0.03  # ... in full as soon as streets may bow at all (`wobble` of this is most of it; 0 is a ruled grid)


EDGE_BOW = 0.22  # toward a city's edge every street bows more, the main roads with it: by this share of a block
# more at the very edge than `wobble` alone gives (none where the normal city begins; none in a strict grid city)


def meander(city: dict, o: dict):
    """(grid line ix, iy) -> (du, dv): how far that crossing drifts off the grid, in blocks, along the grid's two
    directions. None in a strict grid city, or with `wobble` 0. One smooth field for the whole city, so
    neighbouring streets move together: blocks keep their size (never under 60 % of it), no street kinks.
    `wobble` alone bowed only the side streets, a few metres: the main roads of an Organic city still ruled a
    square every fourth line (Nov 12 2026)."""
    if city.get("style", "mixed") == "grid" or o["wobble"] <= 0:
        return None
    amount = MEANDER * (1.0 - math.exp(-o["wobble"] / MEANDER_FROM))
    r = random.Random(f"meander:{city.get('seed', 1)}")
    tau = 2 * math.pi
    waves = []
    for _axis in (0, 1):
        ws = []
        for lo, hi, share in MEANDER_WAVES:
            lam, way, phase = r.uniform(lo, hi), r.uniform(0, math.pi), r.random()
            ws.append((math.cos(way) / lam, math.sin(way) / lam, phase, share))
        waves.append(ws)
    return lambda ix, iy: tuple(amount * sum(k * math.sin(tau * (ix * a + iy * b + p)) for a, b, p, k in ws) for ws in waves)


def _grid_local(city: dict, o: dict) -> tuple:
    """(local, near_fw, fw_at). local(ix, iy) -> (x, y, share): where grid lines ix and iy cross, in cm from the
    city's centre along its own two grid directions, before the hills move it, and that spot's share of the way
    to the city's edge (zone_share of the plain grid point). near_fw(ix, iy): blocks from the nearest freeway
    line. fw_at: (the grid line ix the north-south freeway runs along, the line iy of the east-west one), each
    halfway between two streets, or None."""
    from . import freeways
    bx, by = block_xy(o)
    cx, cy, R, style = city["x"], city["y"], city["radius"], city.get("style", "mixed")
    turn = math.radians(orientation(city))
    c, s = math.cos(turn), math.sin(turn)
    ce, down, seed = o["collector_every"], o["downtown"], city.get("seed", 1)
    normal = max(o["normal"], down)
    edge_bow = 0.0 if style == "grid" or o["wobble"] <= 0 else EDGE_BOW * (1.0 - math.exp(-o["wobble"] / MEANDER_FROM))
    bow_x, bow_y = _bow(seed, "x"), _bow(seed, "y")
    still_x: set = set()
    still_y: set = set()
    if o.get("freeways", "yes") == "yes" and R >= freeways.FW_MIN_R:  # freeways.plan: mid-block past downtown
        fx, fy = round(down * R / bx), round(down * R / by)
        still_x, still_y = {fx, fx + 1}, {-fy - 1, -fy}
    off_x = lambda i: 0.0 if i in still_x else math.sin(math.pi * (i % ce) / ce)  # 0 on a main road, most
    off_y = lambda i: 0.0 if i in still_y else math.sin(math.pi * (i % ce) / ce)  # halfway between two
    drift = meander(city, o)

    def near_fw(ix, iy):  # blocks from the nearest freeway line (what lands on a freeway is laid out straight)
        d = 9.0
        if still_x:
            d = min(d, abs(ix - (min(still_x) + 0.5)))
        if still_y:
            d = min(d, abs(iy - (min(still_y) + 0.5)))
        return d

    def local(ix: int, iy: int) -> tuple:
        lx, ly = ix * bx, iy * by
        share = zone_share(city, cx + c * lx - s * ly, cy + s * lx + c * ly)
        ease = 0.0 if style == "grid" else _smoothstep(down, down + 0.15, share) if style == "mixed" else 1.0
        amount = o["wobble"] * ease
        if amount:
            kx_, ky_ = off_x(ix), off_y(iy)
            if style == "organic":
                # an organic city bends as a whole: its main roads bow with the streets between them, except
                # within a few blocks of a freeway (a town of straight avenues with wavy side streets is a grid)
                main = 0.7 * _smoothstep(1.5, 3.5, near_fw(ix, iy))
                kx_, ky_ = (0.0 if ix in still_x else max(kx_, main)), (0.0 if iy in still_y else max(ky_, main))
            lx += amount * bx * kx_ * bow_x(iy, ix)
            ly += amount * by * ky_ * bow_y(ix, iy)
        if edge_bow:
            # ... and the further out, the more: past the normal city every street bows, the main roads with the
            # side streets (they were ruled lines there in a mixed city, a square of them every fourth block),
            # most at the city's edge. The streets beside one bow with it, so blocks keep their size.
            more = edge_bow * _smoothstep(normal - 0.15, 1.0, share) * _smoothstep(1.5, 3.5, near_fw(ix, iy))
            if more:
                lx += more * bx * (0.0 if ix in still_x else 1.0) * bow_x(iy, ix)
                ly += more * by * (0.0 if iy in still_y else 1.0) * bow_y(ix, iy)
        if drift:
            # ... and the whole net drifts (meander): nothing downtown (the centre of a city is laid out
            # straight, whatever its style), more and more past it, in full a quarter of the way further out,
            # where the suburbs are; nothing beside a freeway
            k = _smoothstep(down, down + 0.25, share) * _smoothstep(1.5, 3.5, near_fw(ix, iy))
            if k:
                mu, mv = drift(ix, iy)
                lx += k * bx * mu
                ly += k * by * mv
        return lx, ly, share
    return local, near_fw, ((min(still_x) + 0.5) if still_x else None, (min(still_y) + 0.5) if still_y else None)


def street_grid(city: dict, o: dict) -> tuple:
    """The city's street grid before anything is cut from it: (at, kx, ky). at(ix, iy) -> (x, y, share): where
    grid lines ix and iy cross in world cm, and that spot's share of the way to the city's edge (zone_share of
    the plain grid point). Blocks are block_m wide and block_m x block_ratio long. Nowhere in a Grid city, and
    never within a few blocks of a freeway (interchanges land on straight roads, and the two streets under a
    viaduct never move), the streets leave the grid's lines three ways (_grid_local, plan_hills):
      - they bow by `wobble` of a block: the side streets, and in an Organic city the main roads with them;
      - the whole net drifts in long waves (meander), main roads too, so no road rules a line across the city
        and no four of them a square. Past downtown only: the centre is straight, the further out the curvier;
      - on sloping ground they follow the hills: every crossing moves by `follow_hills` of what the hillside
        asks (city['hills'])."""
    bx, by = block_xy(o)
    cx, cy, R, style = city["x"], city["y"], city["radius"], city.get("style", "mixed")
    turn = math.radians(orientation(city))
    c, s = math.cos(turn), math.sin(turn)
    local = _grid_local(city, o)[0]
    hills = city.get("hills") if style != "grid" else None
    follow = float(o.get("follow_hills", 0.0)) if hills else 0.0

    def at(ix: int, iy: int) -> tuple:
        lx, ly, share = local(ix, iy)
        if follow:
            hu, hv, _gu, _gv = hill_at(hills, ix, iy)
            lx += follow * bx * hu
            ly += follow * by * hv
        return cx + c * lx - s * ly, cy + s * lx + c * ly, share
    return at, int(R * 1.3 // bx), int(R * 1.3 // by)


def canal_lines(city: dict, opts: dict | None = None) -> list:
    """CanalGenerator: the canal city's canals, polylines in world cm: every third grid line (never a collector or
    an axis) through the downtown and normal rings, along the city's own grid (and its bows: a canal takes a
    street's place)."""
    o = city_options(city, opts)
    if o["canals"] != "canal_city":
        return []
    normal = max(o["normal"], o["downtown"])
    at, kx, ky = street_grid(city, o)
    out = []
    for ix in range(-kx, kx + 1):
        if ix % 3 != 1 or ix % o["collector_every"] == 0:
            continue
        run = [iy for iy in range(-ky, ky + 1) if at(ix, iy)[2] <= normal]
        if len(run) >= 3:
            out.append([list(at(ix, iy)[:2]) for iy in range(run[0], run[-1] + 1)])
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
    o = city_options(city, opts)
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


def plan_shipyards(cities: list, water: dict, opts: dict | None = None, ground=None) -> None:
    """ShipyardPlanner for every city, before its streets: sets city['shipyards'] (campuses + their docks).
    ground (the terrain as it lies, on a world that makes its own): every yard is settled on it as one level
    platform at the water (shipyard.settle); cut_yards then cuts those into the terrain."""
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
        if ground is not None:
            for y in c["shipyards"]:
                shipyard.settle(y, ground, water["sea"])
    # a yard is off limits to the city next door as well: its streets, lots and waterfront keep off it
    for c in cities:
        near = [y for u in cities if u is not c for y in u.get("shipyards") or [] if y.get("z") is not None
                and math.hypot(y["frame"]["x"] - c["x"], y["frame"]["y"] - c["y"]) < c["radius"] * 1.3 + y["L"] + y["D"]]
        if near:
            c["shipyards_near"] = near
        else:
            c.pop("shipyards_near", None)


def plan_shores(cities: list, water: dict, opts: dict | None = None, ground=None) -> None:
    """ShoreWorks for every city, before its streets: sets city['shores'], the stretches of shore that are made
    for what is built on them (waterfront.shores_for). The bigger city first: a stretch two cities reach is the
    bigger one's. cut_shores cuts them into the ground and the streets are laid on them (waterfront.Made)."""
    from . import waterfront
    o = options(opts)
    taken: set = set()
    for c in sorted(cities, key=lambda c: (-float(c["radius"]), str(c.get("name") or ""))):
        c["shores"] = []  # (always set: the waterfront plan builds on these and never works out its own)
        if city_options(c, o)["waterfront"] != "yes" or not water or water.get("sea") is None:
            continue
        ring_of, industrial = zoning(c, o)
        pop = round(math.pi * (c["radius"] / 100000) ** 2 * 4800)
        c["shores"] = waterfront.shores_for(c, water, ring_of, industrial, pop, ground, taken)


def cut_shores(ter: dict, cities: list) -> dict:
    """Every made shore cut into the fine terrain the landscape is made from (waterfront.profile): the quay's
    level promenade with water at its wall, the beach from the water up to its boardwalk, the lawn up to the
    homes, and the bank behind each back to the land as it was."""
    from . import waterfront
    look = waterfront.Made(cities)
    if not look.n or (ter or {}).get("sea") is None or not ter.get("heights"):
        return ter
    grid, (x0, y0), (x1, y1) = ter["grid"], ter["min"], ter["max"]
    cw, ch = (x1 - x0) / grid, (y1 - y0) / grid
    rows = [list(r) for r in ter["heights"]]
    seen: set = set()
    for px, py, _nx, _ny, _w, sh, _clear in look.rows:
        reach = waterfront.DEPTH[sh["use"]] + sh.get("bank", 0.0) + 2500.0
        for j in range(max(0, int((py - reach - y0) / ch)), min(grid, int((py + reach - y0) / ch) + 1)):
            for i in range(max(0, int((px - reach - x0) / cw)), min(grid, int((px + reach - x0) / cw) + 1)):
                if (j, i) in seen:
                    continue
                seen.add((j, i))
                z = rows[j][i]
                if z is not None:
                    rows[j][i] = round(look.level(x0 + (i + 0.5) * cw, y0 + (j + 0.5) * ch, z), 1)
    return {**ter, "heights": rows}


def on_made(cities: list, ground):
    """`ground` with everything a city makes of it: a shipyard's platform (on_yards) and its made shores. What is
    built there stands at the level it was designed for, whatever the coarse terrain says."""
    from . import waterfront
    base = on_yards(cities, ground)
    look = waterfront.Made(cities)
    if not look.n:
        return base

    def at(x, y):
        z = base(x, y)
        return look.level(x, y, z) if z is not None else z
    return at


def on_yards(cities: list, ground):
    """`ground`, but a point on a settled shipyard's platform answers the platform's level. The coarse terrain is
    only level where whole cells lie on a platform (cut_yards), and a hall at its edge stands on it all the same."""
    from . import shipyard
    yards = [(s, s["frame"]["x"] + s["frame"]["vx"] * s["D"] / 2, s["frame"]["y"] + s["frame"]["vy"] * s["D"] / 2,
              (s["L"] / 2) ** 2 + (s["D"] / 2) ** 2)
             for c in cities for s in c.get("shipyards") or [] if s.get("z") is not None]
    if not yards:
        return ground

    def at(x, y):
        for s, cx, cy, r2 in yards:
            if (x - cx) ** 2 + (y - cy) ** 2 <= r2 and shipyard.zone(s, *shipyard.uv(s, x, y)) == "platform":
                return s["z"]
        return ground(x, y)
    return at


def cut_yards(ter: dict, cities: list, whole: bool = False) -> dict:
    """Every settled shipyard's platform cut into the terrain (shipyard.shaped): level ground at the water, a
    basin dredged along its quay, the land beside it cut back to a bank. On the fine grid the landscape is made
    from, a cell at a time. whole=True is for the coarse terrain the roads and lots are planned on: there only a
    cell that lies wholly on a platform, or wholly in its basin, changes, so nothing of a yard reaches past its
    own edges however big the cells are (the fine cut makes the edges exact)."""
    from . import shipyard
    yards = [y for c in cities for y in c.get("shipyards") or [] if y.get("z") is not None]
    sea = (ter or {}).get("sea")
    if not yards or sea is None or not ter.get("heights"):
        return ter
    grid, (x0, y0), (x1, y1) = ter["grid"], ter["min"], ter["max"]
    cw, ch = (x1 - x0) / grid, (y1 - y0) / grid
    lip = 0.0 if whole else min(1.5 * max(cw, ch), 1500.0)
    rows = [list(r) for r in ter["heights"]]
    for s in yards:
        f, L, D = s["frame"], s["L"], s["D"]
        cx, cy = f["x"] + f["vx"] * D / 2, f["y"] + f["vy"] * D / 2
        reach = math.hypot(L / 2, D / 2) + shipyard.BASIN_CM + 20000 + max(cw, ch)
        for j in range(max(0, int((cy - reach - y0) / ch)), min(grid, int((cy + reach - y0) / ch) + 1)):
            y = y0 + (j + 0.5) * ch
            for i in range(max(0, int((cx - reach - x0) / cw)), min(grid, int((cx + reach - x0) / cw) + 1)):
                z = rows[j][i]
                if z is None:
                    continue
                x = x0 + (i + 0.5) * cw
                if whole:
                    zones = {shipyard.zone(s, *shipyard.uv(s, x + a * cw, y + b * ch))
                             for a in (-0.49, 0.49) for b in (-0.49, 0.49)}
                    if zones == {"platform"}:
                        rows[j][i] = s["z"]
                    elif zones == {"basin"}:
                        rows[j][i] = min(z, sea - shipyard.DREDGE_CM)
                else:
                    rows[j][i] = round(shipyard.shaped(s, *shipyard.uv(s, x, y), z, sea, lip), 1)
    return {**ter, "heights": rows}


def water_of(city: dict, opts: dict | None, wet):
    """The terrain's water and the city's canals as one (x, y) -> bool (None when there is neither)."""
    cw = canal_wet(city, opts)
    if not cw:
        return wet
    return lambda x, y: cw(x, y) or bool(wet and wet(x, y))


PARK_MIN_R = 100000.0  # a city of this radius or more has one big park in its centre district ...
PARK_BIG_R = 180000.0  # ... two blocks by three; from this radius two by four
PARK_AT = 0.6  # ... about this far from the middle of that district to its edge


def central_park(city: dict, o: dict, wet=None, grid: tuple | None = None):
    """The city's one big park, like the one in the middle of Manhattan: a rectangle of the centre district's own
    blocks with no street through it. (ix0, iy0, ix1, iy1): the grid lines of its four sides, or None (a small
    city, or no room). Wholly inside the centre district, on dry land, never across one of the city's two main
    axes or a freeway and the streets beside it. Its place is the city's own draw: the same every time.
    grid: (nodes, ring) of the street grid when the caller has them (city_graph); else worked out here."""
    R, down, seed = city["radius"], o["downtown"], city.get("seed", 1)
    if R < PARK_MIN_R:
        return None
    if grid is None:
        at, kx, ky = street_grid(city, o)
        nodes, ring = {}, {}
        for ix in range(-kx, kx + 1):
            for iy in range(-ky, ky + 1):
                wx, wy, share = at(ix, iy)
                if share <= down and not (city.get("yield_to") and yielded(city, wx, wy)):
                    nodes[(ix, iy)], ring[(ix, iy)] = [wx, wy], share
    else:
        nodes, ring = grid
    fw_x, fw_y = _grid_local(city, o)[2]
    long_ = 4 if R >= PARK_BIG_R else 3
    best = None
    for nx, ny in ((2, long_), (long_, 2)):
        for ix0, iy0 in nodes:
            ix1, iy1 = ix0 + nx, iy0 + ny
            if ix0 < 0 < ix1 or iy0 < 0 < iy1:
                continue  # the two main streets run on through the city
            if (fw_x is not None and ix0 - 1 < fw_x < ix1 + 1) or (fw_y is not None and iy0 - 1 < fw_y < iy1 + 1):
                continue
            ks = [(i, j) for i in range(ix0, ix1 + 1) for j in range(iy0, iy1 + 1)]
            if any(k not in nodes or ring[k] > down for k in ks) or (wet and any(wet(*nodes[k][:2]) for k in ks)):
                continue
            off = abs(sum(ring[k] for k in ks) / len(ks) - PARK_AT * down)
            key = (round(off / 0.04), random.Random(f"park:{seed}:{ix0}:{iy0}:{nx}").random())
            if best is None or key < best[0]:
                best = (key, (ix0, iy0, ix1, iy1))
    return best[1] if best else None


def city_graph(city: dict, opts: dict | None = None, wet=None, bridges: list | None = None,
               virtual: list | None = None) -> tuple:
    """Street network of one city: nodes {key: [x, y]}, edges [(a, b, kind)]. Real cities mix patterns
    (urban-design transect): a strict grid of short blocks in the core, a looser grid in the
    neighbourhoods, curving streets with cul-de-sacs in the suburbs, all inside an irregular outline.
    Downtown and the two main axes are four-lane; suburbs are two-lane; the rest has pavements.
    virtual (a list to fill): the streets left out between neighbouring blocks (_street_gaps), as
    [node, node, [x, y], [x, y]]: no road, but still the line the land is parted into blocks along."""
    o = city_options(city, opts)
    down, normal = o["downtown"], max(o["normal"], o["downtown"])
    rnd = random.Random(city.get("seed", 1))
    style = city.get("style", "mixed")
    rnd.uniform(0, math.pi / 2)  # the seed's own grid angle (seed_turn): drawn first, as always
    at, kx, ky = street_grid(city, o)  # the grid at the city's angle, its streets bowed (never jogged)
    nodes, ring = {}, {}
    for ix in range(-kx, kx + 1):
        for iy in range(-ky, ky + 1):
            wx, wy, share = at(ix, iy)
            if share > 1.0:
                continue  # outside this city's irregular edge
            if city.get("yield_to") and yielded(city, wx, wy):
                continue  # a bigger city's land: its streets are the streets there
            nodes[(ix, iy)] = [wx, wy]
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
    # the one big park: no street runs through it. The streets round it stay, whatever is thinned out below.
    # (on the same water city_layout reads: the lakes and the city's own canals)
    park, rim = central_park(city, o, water_of(city, o, wet), (nodes, ring)), set()
    if park:
        px0, py0, px1, py1 = park
        in_park = lambda n: px0 <= n[0] <= px1 and py0 <= n[1] <= py1
        on_rim = lambda a, b: (a[0] == b[0] and a[0] in (px0, px1)) or (a[1] == b[1] and a[1] in (py0, py1))
        edges = [e for e in edges if not (in_park(e[0]) and in_park(e[1]) and not on_rim(e[0], e[1]))]
        for n in [n for n in nodes if px0 < n[0] < px1 and py0 < n[1] < py1]:
            del nodes[n]
        rim = {(e[0], e[1]) for e in edges if in_park(e[0]) and in_park(e[1])}
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
                    fwp.lines and fwp.blocked(*nodes[(g[0] * ce + ce // 2, g[1] * ce + ce // 2)][:2], 0.75 * ce * max(block_xy(o)))
                ) and random.Random(f"superblock:{city.get('seed', 1)}:{g}").random() < SUPERBLOCK_SHARE
            return big[g]
        edges = [e for e in edges if (e[0][0] if e[1][0] == e[0][0] else e[0][1]) % ce == 0
                 or not superblock((e[0][0] // ce, e[0][1] // ce))]
        # (in a Grid city as well: its streets stay ruled, but its subdivisions need no ring of roads each either)
        edges = _street_gaps(city, o, nodes, ring, edges, {g for g, yes in big.items() if yes}, virtual,
                             [ic["at"] for ic in fwp.interchanges if ic.get("b_axis")])
    hills = city.get("hills") if style != "grid" and o["follow_hills"] else None
    if hills:
        # no local street straight up a steep hillside: the ones that would climb steeper than STEEP_STREET are
        # left out (never a main road, never downtown in a mixed city), so the blocks there lie along the hill
        def too_steep(e):
            a, b, kind = e[0], e[1], e[2]
            up_v = a[0] == b[0]
            line = a[0] if up_v else a[1]
            if line % o["collector_every"] == 0 or (a[0] == 0 == b[0]) or (a[1] == 0 == b[1]) or \
                    (style == "mixed" and max(ring[a], ring[b]) <= down):
                return False
            return (abs(hill_at(hills, *a)[3 if up_v else 2]) + abs(hill_at(hills, *b)[3 if up_v else 2])) / 2 > STEEP_STREET
        edges = [e for e in edges if not too_steep(e)]
        if not o["prune"]:  # (pruning keeps the network in one piece below; without it, here)
            adj: dict = {}
            for e in edges:
                adj.setdefault(e[0], []).append(e[1])
                adj.setdefault(e[1], []).append(e[0])
            seen, todo = set(), [min(adj, key=lambda n: abs(n[0]) + abs(n[1]))] if adj else []
            while todo:
                n = todo.pop()
                if n not in seen:
                    seen.add(n)
                    todo.extend(adj.get(n, []))
            edges = [e for e in edges if e[0] in seen]
            nodes = {n: p for n, p in nodes.items() if n in seen}
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
            local = kind != o["core_roads"] and kind != "4L" and line % o["collector_every"] != 0 and (a, b) not in rim
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
    if o["suburbs"] == "winding" and style != "grid":
        # the suburbs are no grid: public winding streets from the grid's last junctions out to the city's edge.
        # Everything below (water, shores, freeways, widths, one network) and every lot, house and PCG layer
        # treats them as the streets they are
        nodes, edges = winding_suburbs(city, o, nodes, ring, edges, water_of(city, o, wet))
        if virtual:  # (no block lines of the grid that is gone there)
            virtual[:] = [v for v in virtual if max(ring.get(v[0], 0), ring.get(v[1], 0)) <= normal]
    yards = (city.get("shipyards") or []) + (city.get("shipyards_near") or [])
    fields = city.get("airports") or []
    if yards or fields:  # shipyards and airports are dedicated districts with their own roads
        from . import airport, shipyard
        # (a settled yard is a walled platform with its bank round it: no street on either, nor 15 m from them)
        drop = {n for n, p in nodes.items()
                if any(shipyard.reserved(y, p[0], p[1], 1500) if y.get("z") is not None
                       else shipyard.inside(y, p[0], p[1], -2000) for y in yards)
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
        planned_before = len(bridges) if bridges is not None else 0
        nodes, edges = over_water(*cross_water(nodes, edges, wet), wet, bridges=bridges, stubs=False)
        nodes, edges = few_bridges(nodes, edges, bridges, planned_before)
        nodes, edges = join_parts(nodes, edges, wet, o["core_roads"], bridges)
    if city.get("shores"):
        # no street on a made shore where the land was cut down for it: it ends at the top of the bank, above
        # the promenade (laid there it stood on a ramp 10 m over the quay). A bridge keeps both its ends: a
        # crossing is never cut off for a promenade.
        from . import waterfront
        look = waterfront.Made([city])
        spans = {n for e in edges if len(e) > 3 and e[3] for n in e[:2]}
        drop = {n for n, p in nodes.items() if n not in spans and look.no_street(p[0], p[1])}
        if drop and spans:
            # ... nor is its own street: a bridge that lands on a made shore kept its end there and lost every
            # street that led to it, so it stood at the water joined to nothing (Nov 12 2026). From each end
            # that would be left bare, the shortest way inland to a junction that stays is kept.
            adj: dict = {}
            for e in edges:
                if not (len(e) > 3 and e[3]):
                    adj.setdefault(e[0], []).append(e[1])
                    adj.setdefault(e[1], []).append(e[0])
            for end in sorted(spans, key=str):
                if any(m not in drop for m in adj.get(end, ())):
                    continue  # a street still leaves it
                came, todo, found = {end: None}, [end], None
                while todo and found is None:
                    nxt = []
                    for n in todo:
                        for m in adj.get(n, ()):
                            if m in came:
                                continue
                            came[m] = n
                            if m not in drop and m not in spans:
                                found = m
                                break
                            nxt.append(m)
                        if found is not None:
                            break
                    todo = nxt
                while found is not None:
                    drop.discard(found)
                    found = came[found]
        if drop:
            edges = [e for e in edges if e[0] not in drop and e[1] not in drop]
            used = {n for e in edges for n in e[:2]}
            nodes = {n: p for n, p in nodes.items() if n in used}
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
    if city.get("fw_near"):  # ... and to a neighbouring city's ramps and frontage roads where they reach over its land
        cut = _near_cuts(city)
        edges = [e for e in edges if not cut(nodes[e[0]], nodes[e[1]], HALF.get(e[2], 500.0))]
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
    edges = _no_fans(nodes, edges)
    # no street laid along another: two that run the same way, share no junction and lie closer than their half
    # widths together are one road drawn twice (a bridge beside the shore street it left): the narrower goes. (At
    # 0.6 of them two of Riverton's 17.5 m streets stayed 11.6 m apart, pavements over each other, Nov 17 2026)
    geo = [(nodes[e[0]][:2], nodes[e[1]][:2], HALF.get(e[2], 500.0)) for e in edges]
    cell, near, lost = 20000.0, {}, set()
    for k, (p, q, _h) in enumerate(geo):
        near.setdefault((int((p[0] + q[0]) / 2 // cell), int((p[1] + q[1]) / 2 // cell)), []).append(k)
    for k, (p, q, h) in enumerate(geo):
        mx, my, ux, uy = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2, q[0] - p[0], q[1] - p[1]
        ul = math.hypot(ux, uy)
        for j in (j for gx in (-1, 0, 1) for gy in (-1, 0, 1) for j in near.get((int(mx // cell) + gx, int(my // cell) + gy), ())):
            if j == k or k in lost or j in lost or ul < 300 or {edges[k][0], edges[k][1]} & {edges[j][0], edges[j][1]}:
                continue
            p2, q2, h2 = geo[j]
            vx, vy = q2[0] - p2[0], q2[1] - p2[1]
            vl = math.hypot(vx, vy)
            if vl < 300 or abs(ux * vx + uy * vy) / (ul * vl) < 0.94:
                continue
            t = max(0.0, min(1.0, ((mx - p2[0]) * vx + (my - p2[1]) * vy) / (vl * vl)))
            if math.hypot(mx - p2[0] - vx * t, my - p2[1] - vy * t) < h + h2:
                lost.add(k if (h, ul) <= (h2, vl) else j)
    if lost:
        edges = [e for k, e in enumerate(edges) if k not in lost]
        used = {n for e in edges for n in e[:2]}
        nodes = {n: p for n, p in nodes.items() if n in used}
    nodes, edges = one_network(nodes, edges)
    if virtual:
        # the land is not parted where it is no land of the city's blocks: water, a shipyard, an airport, a made
        # shore, a freeway's ramps (the streets there are gone the same way)
        from . import airport, shipyard, waterfront
        look = waterfront.Made([city]) if city.get("shores") else None

        def gone(v) -> bool:
            for p in (v[2], v[3]):
                if (wet and wet(p[0], p[1])) or (look and look.no_street(p[0], p[1])) or \
                        any(shipyard.reserved(y, p[0], p[1], 1500) if y.get("z") is not None
                            else shipyard.inside(y, p[0], p[1], -2000) for y in yards) or \
                        any(airport.inside(a, p[0], p[1], 3000) for a in fields):
                    return True
            return bool(fw.lines and fw.street_blocked(v[2], v[3], 500.0))
        virtual[:] = [v for v in virtual if not gone(v)]
    return nodes, edges


FAN_DEG = 20.0  # two streets leaving one junction closer together than this overlap from its mouth on


def _no_fans(nodes: dict, edges: list) -> list:
    """No two streets out of one junction the same way: the same two junctions joined twice (Valley's Riverton drew
    both, bowed apart, Nov 17 2026) or a second street fanned off a few degrees beside the first. The overlap check
    after this skips streets that share a junction, so it never saw them. Bridges first, then the wider, then the
    longer street stays.
    ponytail: a dropped street that alone reached a corner leaves it to one_network (it goes, or is joined again)."""
    rank = sorted(range(len(edges)), key=lambda k: (not (len(edges[k]) > 3 and edges[k][3]), -HALF.get(edges[k][2], 500.0),
                                                    -math.dist(nodes[edges[k][0]][:2], nodes[edges[k][1]][:2]), k))
    out, kept, lim = {}, set(), math.radians(FAN_DEG)
    for k in rank:
        a, b = edges[k][0], edges[k][1]
        ends = [(n, math.atan2(nodes[m][1] - nodes[n][1], nodes[m][0] - nodes[n][0])) for n, m in ((a, b), (b, a))]
        if a == b or any(abs((t - u + math.pi) % (2 * math.pi) - math.pi) < lim for n, t in ends for u in out.get(n, ())):
            continue
        kept.add(k)
        for n, t in ends:
            out.setdefault(n, []).append(t)
    return [e for k, e in enumerate(edges) if k in kept]


SUB_STEP = 4500.0  # winding suburb streets: a point every 45 m
SUB_CLEAR = 7000.0  # ... never closer than this to another street's point (two rows of lots fit between)
SUB_OFF = 4000.0  # ... nor this close to a street of the grid
SUB_SEED_GAP = 20000.0  # a collector leaves the grid about every 200 m
SUB_KEY = 10 ** 6


def winding_suburbs(city: dict, o: dict, nodes: dict, ring: dict, edges: list, wet=None) -> tuple:
    """The suburbs as an organic street network instead of the grid: past the normal city every grid street goes
    (the two main axes stay: the roads out of town), and from the grid's last junctions winding collectors grow
    out to the city's edge, local streets branch off them on both sides (crescents; some close a loop on a
    neighbour), and short cul-de-sacs fill what room is left. The further out, the more a street winds. Nodes are
    (SUB_KEY + n, 0, 0) (three ints: they sort with the grid's (ix, iy) wherever nodes are ordered); all are public streets, so lots front them (frontage_lots) and every end gets its turnaround.
    ponytail: streets ignore the hills here (the ground is graded to them); follow hill_at if they must."""
    normal = max(o["normal"], o["downtown"])
    outer = lambda e: max(ring[e[0]], ring[e[1]]) > normal and not ((e[0][0] == 0 == e[1][0]) or (e[0][1] == 0 == e[1][1]))
    cut, edges, old = [e for e in edges if outer(e)], [e for e in edges if not outer(e)], nodes
    used = {n for e in edges for n in e[:2]}
    nodes = {n: p for n, p in old.items() if n in used}
    rnd = random.Random(f"suburbs:{city.get('seed', 1)}")
    core = city.get("core") or (0, 0)
    cx, cy = city["x"] + core[0], city["y"] + core[1]
    kind, cell, grid, adj, count = o["suburb_roads"], SUB_CLEAR, {}, {}, 0
    turn = lambda a, b: (a - b + math.pi) % (2 * math.pi) - math.pi

    def put(x, y, key):
        grid.setdefault((int(x // cell), int(y // cell)), []).append((x, y, key))

    for n, p in nodes.items():
        put(p[0], p[1], n)
    for e in edges:  # (and along the grid's streets, so none is crossed between its junctions)
        p, q = nodes[e[0]], nodes[e[1]]
        L = math.dist(p[:2], q[:2])
        for k in range(1, int(L // 3000)):
            put(p[0] + (q[0] - p[0]) * k * 3000 / L, p[1] + (q[1] - p[1]) * k * 3000 / L, None)

    def hit(x, y, skip):
        best = None
        for gx in (-1, 0, 1):
            for gy in (-1, 0, 1):
                for px, py, key in grid.get((int(x // cell) + gx, int(y // cell) + gy), ()):
                    d = math.hypot(px - x, py - y)
                    if key not in skip and d < (SUB_OFF if key is None else SUB_CLEAR) and (best is None or d < best[0]):
                        best = (d, key)
        return best

    def grow(start, h, steps, depth, skip):
        nonlocal count
        skip, last, line = set(skip) | {start}, start, []
        x, y = nodes[start][:2]
        ph, w, c = rnd.uniform(0, 2 * math.pi), rnd.uniform(0.5, 0.9), rnd.uniform(-0.22, 0.22)
        for i in range(steps):
            far = max(0.0, min(1.0, (zone_share(city, x, y) - normal) / max(1e-6, 1.0 - normal)))
            if depth == 0:  # a collector heads out of town, swinging wider the further out it is
                h += 0.2 * turn(math.atan2(y - cy, x - cx), h) + (0.1 + 0.4 * far) * math.sin(ph + i * w)
            elif i:  # a local street keeps one bend: a crescent
                h += c * (0.4 + 0.6 * far)
            nx, ny = x + SUB_STEP * math.cos(h), y + SUB_STEP * math.sin(h)
            if zone_share(city, nx, ny) > 0.97 or (wet and wet(nx, ny)) or (city.get("yield_to") and yielded(city, nx, ny)):
                break
            got = hit(nx, ny, skip)
            if got:
                key = got[1]  # another winding street in the way: close the loop on it, or end here (a cul-de-sac)
                if depth and line and key is not None and len(key) == 3 and key not in adj.get(last, ()) and \
                        math.dist(nodes[last][:2], nodes[key][:2]) < 2.2 * SUB_STEP and rnd.random() < 0.6:
                    edges.append([last, key, kind])
                    adj.setdefault(last, set()).add(key)
                    adj.setdefault(key, set()).add(last)
                break
            key = (SUB_KEY + count, 0, 0)
            count += 1
            nodes[key] = [nx, ny]
            put(nx, ny, key)
            skip.add(key)
            edges.append([last, key, kind])
            adj.setdefault(last, set()).add(key)
            adj.setdefault(key, set()).add(last)
            line.append((key, h))
            last, x, y = key, nx, ny
        return line

    seeds = []  # where a street out of the grid was cut: (junction that stays, the way that street ran)
    for e in cut:
        if (e[0] in used) != (e[1] in used):
            s, gone = (e[0], e[1]) if e[0] in used else (e[1], e[0])
            if all(math.dist(nodes[s][:2], nodes[t][:2]) >= SUB_SEED_GAP for t, _h in seeds):
                seeds.append((s, math.atan2(old[gone][1] - old[s][1], old[gone][0] - old[s][0])))
    level = [grow(s, h, 60, 0, ()) for s, h in seeds]
    for depth in (1, 2):  # local streets off the collectors, then cul-de-sacs off those where there is room
        nxt = []
        for line in level:
            for i, (key, h) in enumerate(line[1:], 1):
                for side in ((1 if i % 2 else -1,) if depth == 1 else (1, -1)):
                    if depth == 2 and rnd.random() > 0.6:
                        continue
                    steps = rnd.randint(3, 8) if depth == 1 else rnd.randint(1, 3)
                    nxt.append(grow(key, h + side * (math.pi / 2 + rnd.uniform(-0.25, 0.25)), steps, depth, adj.get(key, ())))
        level = nxt
    return nodes, edges


# Of the streets that would run between two neighbouring blocks, how many are not built: between two whole
# subdivisions (superblocks), between two blocks of the suburbs, between two of the neighbourhoods round downtown
GAPS = {"super": 0.55, "housing": 0.5, "normal": 0.35}


def _street_gaps(city: dict, o: dict, nodes: dict, ring: dict, edges: list, supers: set, virtual: list | None,
                 interchanges: list | None = None) -> list:
    """The street edges without the streets that are not built. Past downtown every block of the grid was ringed
    by its own square of roads, a subdivision or a complex inside each: housing on top of a chequerboard. Real
    neighbourhoods lie side by side with nothing but back fences between them. So of the streets between two
    neighbouring blocks many are left out (GAPS): each block is still laid out on its own ground and turned to a
    side that has a street (city_layout, neighbourhoods), and the streets that remain take the traffic.
    Never: downtown, a stretch of the city's two axes, the two streets beside a freeway, an interchange's cross
    road within three blocks of it (`interchanges`: where each one is), a block's last two streets, a crossing's
    last two roads, the street that holds the network together. The stretches left out are added to `virtual`. nodes: the grid crossings {(ix, iy): ...}; ring: each
    one's share of the way to the city's edge; supers: the collector cells that are one subdivision."""
    ce, down, normal = o["collector_every"], o["downtown"], max(o["normal"], o["downtown"])
    at = {}
    for e in edges:
        at[(e[0], e[1])] = at[(e[1], e[0])] = e
    fw_x, fw_y = _grid_local(city, o)[2]
    bx, by = block_xy(o)
    turn = math.radians(orientation(city))
    cross = set()  # ('x' | 'y', grid line) of every interchange's cross road
    for x, y in interchanges or []:
        gx = ((x - city["x"]) * math.cos(turn) + (y - city["y"]) * math.sin(turn)) / bx
        gy = (-(x - city["x"]) * math.sin(turn) + (y - city["y"]) * math.cos(turn)) / by
        if fw_x is not None and abs(gx - fw_x) < 0.6:
            cross.add(("y", round(gy)))
        if fw_y is not None and abs(gy - fw_y) < 0.6:
            cross.add(("x", round(gx)))
    cells = {n for n in nodes if (n[0] + 1, n[1]) in nodes and (n[0], n[1] + 1) in nodes and (n[0] + 1, n[1] + 1) in nodes}
    east = lambda c: ((c[0] + 1, c[1]), (c[0] + 1, c[1] + 1))  # the street between a cell and the one east of it
    north = lambda c: ((c[0], c[1] + 1), (c[0] + 1, c[1] + 1))
    block: dict = {}  # cell -> its block: the cells no street parts
    members: list = []
    for c in sorted(cells):
        if c in block:
            continue
        block[c], todo, got = len(members), [c], []
        while todo:
            u = todo.pop()
            got.append(u)
            for v, e in (((u[0] + 1, u[1]), east(u)), ((u[0], u[1] + 1), north(u)),
                         ((u[0] - 1, u[1]), east((u[0] - 1, u[1]))), ((u[0], u[1] - 1), north((u[0], u[1] - 1)))):
                if v in cells and v not in block and e not in at:
                    block[v] = len(members)
                    todo.append(v)
        members.append(got)
    sides: dict = {}  # (block, block, 'x' | 'y', grid line) -> the street between the two, piece by piece
    real = [set() for _ in members]  # each block's sides that have a street
    for c in sorted(cells):
        me = block[c]
        for v, e, way, line in (((c[0] + 1, c[1]), east(c), "x", c[0] + 1), ((c[0], c[1] + 1), north(c), "y", c[1] + 1)):
            if e in at and v in cells and block[v] != me:
                key = (min(me, block[v]), max(me, block[v]), way, line)
                sides.setdefault(key, []).append(e)
                real[me].add(key)
                real[block[v]].add(key)
            elif e in at and v not in cells:
                real[me].add((me, None, way, line))
        for v, e, way, line in (((c[0] - 1, c[1]), east((c[0] - 1, c[1])), "x", c[0]), ((c[0], c[1] - 1), north((c[0], c[1] - 1)), "y", c[1])):
            if v not in cells and e in at:
                real[me].add((me, None, way, line))
    zone, whole = [], []
    for got in members:
        lo = min(ring[n] for c in got for n in (c, (c[0] + 1, c[1]), (c[0], c[1] + 1), (c[0] + 1, c[1] + 1)))
        zone.append("housing" if lo > normal else "normal" if lo > down else "core")
        c0 = min(got)
        whole.append(len(got) == ce * ce and (c0[0] // ce, c0[1] // ce) in supers)
    todo = []
    for key in sorted(sides):
        ra, rb, way, line = key
        es = sorted(sides[key])
        if line == 0 or "core" in (zone[ra], zone[rb]) or min(len(members[ra]), len(members[rb])) < 4:
            continue  # an axis, downtown, or a block of the fine grid
        beside, across = (fw_x, fw_y) if way == "x" else (fw_y, fw_x)  # the freeway it runs along, the one it meets
        if any(p[1] != q[0] for p, q in zip(es, es[1:])) or (beside is not None and abs(line - beside) < 1.0) or \
                ((way, line) in cross and min(abs(n[1 if way == "x" else 0] - across) for e in es for n in e) < 3.5):
            continue  # (not one straight stretch; a street beside a freeway; an interchange's cross road)
        rate = GAPS["super"] if whole[ra] and whole[rb] else GAPS["housing"] if zone[ra] == zone[rb] == "housing" else GAPS["normal"]
        # (each stretch has its own draw, from where it is: a change in one corner of the city does not re-deal
        # the streets of the rest)
        draw = random.Random(f"gap:{city.get('seed', 1)}:{way}:{line}:{es[0][0]}").random()
        if draw < rate:
            todo.append((draw, key, es[0][0], es[-1][1], [at[e] for e in es]))
    todo.sort(key=lambda t: (t[0], t[1][2], t[1][3], t[2]))
    adj: dict = {}
    for e in edges:
        adj.setdefault(e[0], set()).add(e[1])
        adj.setdefault(e[1], set()).add(e[0])
    gone = set()
    for _draw, key, a, b, es in todo:
        if len(real[key[0]]) < 3 or len(real[key[1]]) < 3 or len(adj[a]) < 3 or len(adj[b]) < 3:
            continue
        cut = {(e[0], e[1]) for e in es} | {(e[1], e[0]) for e in es}
        seen, walk = {a}, [a]
        while walk and b not in seen:  # still one network without it?
            n = walk.pop()
            for m in adj[n]:
                if m not in seen and (n, m) not in cut:
                    seen.add(m)
                    walk.append(m)
        if b not in seen:
            continue
        real[key[0]].discard(key)
        real[key[1]].discard(key)
        for e in es:
            adj[e[0]].discard(e[1])
            adj[e[1]].discard(e[0])
            gone.add(id(e))
            if virtual is not None:
                virtual.append([e[0], e[1], list(nodes[e[0]][:2]), list(nodes[e[1]][:2])])
    return [e for e in edges if id(e) not in gone]


LONG_BRIDGE_CM = 30000.0  # a bridge over 300 m from end to end is a long one ...
BRIDGES_APART_CM = 80000.0  # ... no two long ones come within 800 m of each other ...
LONG_BRIDGES = 3  # ... and a city builds this many at most, unless a district across the water needs one more


def few_bridges(nodes: dict, edges: list, bridges: list | None = None, since: int = 0) -> tuple:
    """Every street that reached the water got its own bridge to the far bank. A city on a lake then had a
    dozen bridges a kilometre long side by side, several fanning out to one landing and crossing each other in
    the air: 15 km of bridge deck in a town with 60 km of streets (the owner's Valley, Nov 13 2026). Now:
      - no bridge crosses another;
      - first the bridges the city needs: the ones that join two parts of it no street joins (a district on the
        far bank, of LOOSE_MIN junctions or more when the bridge is a long one), shortest first. Where two of
        those would cross, the set that leaves most of the city in one piece is built;
      - then any other bridge only if it is short (creeks and rivers keep one on every street) or no long one
        (over LONG_BRIDGE_CM) comes within BRIDGES_APART_CM of it anywhere along its length: never two to one
        landing, never two side by side over the same water. And LONG_BRIDGES long ones in all, no more.
    The ones not built stop at the shore like any street with no crossing.
    bridges: the world's list of planned bridges; the entries added from index `since` for bridges not built go."""
    def crosses(j, k):
        (p, q), (a, b) = line[j], line[k]
        d = lambda o, u, v: (u[0] - o[0]) * (v[1] - o[1]) - (u[1] - o[1]) * (v[0] - o[0])
        return d(p, q, a) * d(p, q, b) < 0 and d(a, b, p) * d(a, b, q) < 0
    apart = lambda j, k: min(_dist_seg(line[j][0], *line[k]), _dist_seg(line[j][1], *line[k]),
                             _dist_seg(line[k][0], *line[j]), _dist_seg(line[k][1], *line[j]))
    line = {k: (nodes[e[0]][:2], nodes[e[1]][:2]) for k, e in enumerate(edges) if len(e) > 3 and e[3]}
    if not line:
        return nodes, edges
    length = {k: math.dist(p, q) for k, (p, q) in line.items()}
    spans = sorted(line, key=lambda k: (length[k], k))
    land: dict = {}  # each junction's part of the city, as the streets alone join it

    def part(of, n):
        while of.get(n, n) != n:
            n = of[n]
        return n
    for e in edges:
        land.setdefault(e[0], e[0])
        land.setdefault(e[1], e[1])
    for k, e in enumerate(edges):
        if k not in line:
            land[part(land, e[0])] = part(land, e[1])

    def joined(first=None):  # the needed bridges with `first` built before all others: (the biggest part's size, -deck length, which)
        of, got = dict(land), []
        sizes: dict = {}
        for n in of:
            sizes[part(of, n)] = sizes.get(part(of, n), 0) + 1
        for k in ([first] if first is not None else []) + [k for k in spans if k != first]:
            a, b = part(of, edges[k][0]), part(of, edges[k][1])
            if a == b or any(crosses(k, j) for j in got):
                continue
            if length[k] > LONG_BRIDGE_CM and min(sizes[a], sizes[b]) < LOOSE_MIN:
                continue  # (no long bridge for a corner of three streets: that corner is not built, one_network)
            got.append(k)
            of[a] = b
            sizes[b] += sizes.pop(a)
        return max(sizes.values()), -sum(length[k] for k in got), got
    needed = [k for k in spans if part(land, edges[k][0]) != part(land, edges[k][1])]
    built = max([joined()] + [joined(k) for k in needed[:40]], key=lambda t: t[:2])[2] if needed else []
    of, sizes = dict(land), {}
    for n in of:
        sizes[part(of, n)] = sizes.get(part(of, n), 0) + 1
    for k in built:
        a, b = part(of, edges[k][0]), part(of, edges[k][1])
        of[a] = b
        sizes[b] += sizes.pop(a)
    for k in spans:
        if k in built or any(crosses(k, j) for j in built):
            continue
        a, b = part(of, edges[k][0]), part(of, edges[k][1])
        if length[k] > LONG_BRIDGE_CM and (
                (a != b and min(sizes[a], sizes[b]) < LOOSE_MIN)
                or sum(1 for j in built if length[j] > LONG_BRIDGE_CM) >= LONG_BRIDGES
                or any(length[j] > LONG_BRIDGE_CM and apart(k, j) < BRIDGES_APART_CM for j in built)):
            continue
        built.append(k)
        if a != b:
            of[a] = b
            sizes[b] += sizes.pop(a)
    gone = set(line) - set(built)
    if not gone:
        return nodes, edges
    if bridges is not None:
        # (its own entry: on its line and running its way. One built right across it lies on its line too.)
        along = lambda b, p, q: abs(math.cos(math.radians(b.get("yaw", 0.0)) - math.atan2(q[1] - p[1], q[0] - p[0]))) > 0.95
        bridges[since:] = [b for b in bridges[since:]
                           if not any(_dist_seg((b["x"], b["y"]), *line[k]) < 500 and along(b, *line[k]) for k in gone)]
    edges = [e for k, e in enumerate(edges) if k not in gone]
    used = {n for e in edges for n in e[:2]}
    return {n: p for n, p in nodes.items() if n in used}, edges


LOOSE_MIN = 8  # a part of the street network that nothing joins to the rest is kept only with this many junctions
PART_BRIDGE_CM = 200000.0  # ... and such a part gets a bridge of its own to the rest when one this short will do


def join_parts(nodes: dict, edges: list, wet, kind: str, bridges: list | None = None, land: str = "") -> tuple:
    """A district on the far bank that no bridge reaches (its own street's crossing would have cut across another
    bridge, or was too long) is joined all the same: one bridge straight from one of its junctions to the nearest
    junction of the rest of the city, the shortest pair that crosses no street and no bridge, leaves neither
    junction along a street that is already there, and is no longer than PART_BRIDGE_CM. A district of fewer
    than LOOSE_MIN junctions gets none (one_network takes it out). With `land` (a street kind) a district cut off
    on dry land is joined too, by that street: Valley's Northgate lake homes and Riverton's gate were parted
    from their cities by 45 to 85 m of grass (Nov 18 2026)."""
    def crosses(p, q, a, b):
        d = lambda o, u, v: (u[0] - o[0]) * (v[1] - o[1]) - (u[1] - o[1]) * (v[0] - o[0])
        return d(p, q, a) * d(p, q, b) < 0 and d(a, b, p) * d(a, b, q) < 0
    for _round in range(8):
        adj: dict = {}
        for e in edges:
            adj.setdefault(e[0], []).append(e[1])
            adj.setdefault(e[1], []).append(e[0])
        parts, seen = [], set()
        for n in adj:
            if n not in seen:
                got, todo = [], [n]
                seen.add(n)
                while todo:
                    u = todo.pop()
                    got.append(u)
                    for m in adj[u]:
                        if m not in seen:
                            seen.add(m)
                            todo.append(m)
                parts.append(got)
        parts.sort(key=lambda g: (-len(g), str(g[0])))
        if len(parts) < 2:
            break
        lines = [(nodes[e[0]][:2], nodes[e[1]][:2], e[0], e[1]) for e in edges]
        new = None
        for far in parts[1:]:
            if len(far) < LOOSE_MIN:
                continue
            rest = [n for g in parts if g is not far and len(g) >= LOOSE_MIN for n in g]
            pairs = sorted(((math.dist(nodes[a][:2], nodes[b][:2]), str(a), str(b), a, b) for a in far for b in rest
                            if math.dist(nodes[a][:2], nodes[b][:2]) <= PART_BRIDGE_CM), key=lambda t: t[:3])
            for length, _sa, _sb, a, b in pairs[:60]:
                p, q = nodes[a][:2], nodes[b][:2]
                if any(crosses(p, q, u, v) for u, v, _m, _n in lines):
                    continue
                way = math.atan2(q[1] - p[1], q[0] - p[0])
                if any(abs(math.cos(way - math.atan2(nodes[m][1] - nodes[n][1], nodes[m][0] - nodes[n][0]))) > 0.9
                       for n in (a, b) for m in adj[n]):
                    continue  # (it would leave a junction along a street that is there: one road drawn twice)
                plans: list = []
                got_e = over_water({a: nodes[a], b: nodes[b]}, [[a, b, kind]], wet, bridges=plans, stubs=False)[1] \
                    if wet else [[a, b, kind]]
                if len(got_e) == 1 and got_e[0][:2] == [a, b]:
                    if len(got_e[0]) > 3 and got_e[0][3]:
                        new = (got_e[0], plans)
                    elif land:
                        new = ([a, b, land], [])
                if new:
                    break
            if new:
                break
        if not new:
            break
        edges = edges + [new[0]]
        if bridges is not None:
            bridges.extend(new[1])
    return nodes, edges


def one_network(nodes: dict, edges: list) -> tuple:
    """The streets as one network: a bridge with no street at one of its ends is not built (it led from a street
    to nothing, or stood on its own between two shores), and a few streets nothing joins to the rest of the
    city go too (a district of its own across the water stays)."""
    while True:
        deg: dict = {}
        for e in edges:
            deg[e[0]] = deg.get(e[0], 0) + 1
            deg[e[1]] = deg.get(e[1], 0) + 1
        bare = {id(e) for e in edges if len(e) > 3 and e[3] and (deg[e[0]] == 1 or deg[e[1]] == 1)}
        if not bare:
            break
        edges = [e for e in edges if id(e) not in bare]
    adj: dict = {}
    for e in edges:
        adj.setdefault(e[0], []).append(e[1])
        adj.setdefault(e[1], []).append(e[0])
    part: dict = {}
    sizes = []
    for n in adj:
        if n in part:
            continue
        k, todo = len(sizes), [n]
        part[n] = k
        count = 0
        while todo:
            u = todo.pop()
            count += 1
            for m in adj[u]:
                if m not in part:
                    part[m] = k
                    todo.append(m)
        sizes.append(count)
    if len(sizes) > 1:
        main = max(range(len(sizes)), key=lambda k: sizes[k])
        edges = [e for e in edges if part[e[0]] == main or sizes[part[e[0]]] >= LOOSE_MIN]
    used = {n for e in edges for n in e[:2]}
    return {n: p for n, p in nodes.items() if n in used}, edges


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


def over_water(nodes: dict, edges: list, wet, span: dict | None = None, bridges: list | None = None,
               stubs: bool = True) -> tuple:
    """Roads know where the water is. An edge that runs over water becomes a bridge (ramp up 40 m, deck
    BRIDGE_LIFT_CM over the water, ramp down) when its kind may span that far (WATER_SPAN; any paved street
    over a creek <= SHORT_SPAN) and it is not already high enough there; otherwise it is removed (a side street
    stops at the shore). An end in the water is cut back to the shore (new node ('shore', a, b, 0|1)).
    span: {kind: cm} overrides (highways pass a long one). stubs=False (city streets): a street whose end is in
    the water goes altogether, back to its last junction, instead of running down the bank as a dead end."""
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
        if not stubs and (na != a or nb != b):
            continue  # a city street never runs out to the water's edge and stops: it ends at the cross street before
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
GIRDER_CM = 120.0  # a bridge's girders under its slab (road_profiles); the pier cap carries them
ABUTMENT_CM = 250.0  # an abutment's wall, from the deck's underside down into the bank
FILL_STEP_CM = 500.0  # an approach wall's blocks under a low deck, one this long at a time (they overlap a little)
EMBED_CM = 100.0  # how far a wall or a foundation goes into the ground under its lowest corner


def _ground_roads(actors: dict):
    """in_road(x, y, pad): is this spot on a road that lies on the ground, or within `pad` cm of its edge."""
    cell = 5000.0
    grade: dict = {}  # roads on the ground, by 50 m cell
    for a in actors.values():
        by = {p["i"]: p for p in a["points"]}
        for s in a["segments"]:
            mesh = (s.get("mesh") or "").split("/")[-1]
            pa, pb = by.get(s["a"]), by.get(s["b"])
            if mesh and mesh not in STRUCTURES and pa and pb:
                h = HALF.get(next((k for k, m in ROAD_MESH.items() if m.split("/")[-1] == mesh), ""), 800.0)
                for gx in range(int(min(pa["x"], pb["x"]) // cell) - 1, int(max(pa["x"], pb["x"]) // cell) + 2):
                    for gy in range(int(min(pa["y"], pb["y"]) // cell) - 1, int(max(pa["y"], pb["y"]) // cell) + 2):
                        grade.setdefault((gx, gy), []).append((pa, pb, h))

    def in_road(x, y, pad=250.0):
        for pa, pb, h in grade.get((int(x // cell), int(y // cell)), ()):
            ux, uy = pb["x"] - pa["x"], pb["y"] - pa["y"]
            t = max(0.0, min(1.0, ((x - pa["x"]) * ux + (y - pa["y"]) * uy) / (ux * ux + uy * uy or 1.0)))
            if math.hypot(x - pa["x"] - ux * t, y - pa["y"] - uy * t) < h + pad:
                return True
        return False
    return in_road


def deck_fills(actors: dict, ground, sea=None) -> list:
    """A deck's or ramp's own side walls where it is too low for piers: an approach wall under it, from the ground
    up into its slab, as wide as the slab's flat underside. So a ramp coming down to its street, or a freeway deck
    a few metres over the land, stands on solid concrete the way a ground road stands on its skirts, never on air.
    Left open: over water (a bridge), over a road it crosses, where the deck already lies on the ground, and where
    it is high enough for piers (bridge_piers takes over there, the same test).
    Site rows [x, y, base z, yaw, length_m, width_m, height_m] for SM_Kit_ApproachWall (1 x 1 x 1 m at scale 1,
    its origin at the middle of its base)."""
    rows = []
    in_road = _ground_roads(actors)
    for a in actors.values():
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
            yaw = round(math.degrees(math.atan2(pb["y"] - pa["y"], pb["x"] - pa["x"])), 1)
            ux, uy = (pb["x"] - pa["x"]) / L, (pb["y"] - pa["y"]) / L
            inner = half - min(150.0, half * 0.3)  # the slab's flat underside (road_profiles.shape 'deck' tapers in)
            n = max(1, round(L / FILL_STEP_CM))
            rise = abs(pb["z"] - pa["z"]) / n / 2  # a block's higher end: its flat top goes up into the sloping slab there
            for k in range(n):
                t = (k + 0.5) / n
                x, y = pa["x"] + (pb["x"] - pa["x"]) * t, pa["y"] + (pb["y"] - pa["y"]) * t
                top = pa["z"] + (pb["z"] - pa["z"]) * t - DECK_DEPTH_CM - (GIRDER_CM if "Bridge" in mesh else 0.0)
                spots = [(x - uy * o, y + ux * o) for o in (-inner, 0.0, inner)]
                feet = [ground(px, py) for px, py in spots]
                if any(f is None for f in feet):
                    continue
                low = min(feet)
                if sea is not None and low < sea:
                    continue  # over water: a bridge stands on its piers
                if top - low < 20 or top - CAP_CM - low >= PIER_MIN_CM:
                    continue  # on the ground already, or high enough to stand on piers
                if any(in_road(px, py, -50.0) for px, py in spots):
                    continue  # a road runs under it here: the deck spans it
                rows.append([round(x), round(y), round(low - EMBED_CM), yaw, round(L / n * 1.04 / 100, 2),
                             round(inner * 2 / 100, 2), round((top + rise + 5 - low + EMBED_CM) / 100, 2)])
    return rows


def bridge_piers(actors: dict, ground) -> tuple:
    """Real bridges: under every deck and elevated ramp a pier bent every PIER_EVERY_CM, from the ground (or the
    lake bed) up to the deck's underside: a cap beam across the deck's width and one column (two under a deck
    16 m or wider), turned with the deck. Returns (columns, caps) as site rows [x, y, z, yaw, sx, sy, sz] for
    SM_Kit_BridgePier (200 x 200 x 100 cm at scale 1) and SM_Kit_PierCap (200 x 100 x 120 cm)."""
    cols, caps = [], []
    in_road = _ground_roads(actors)  # a pier never stands in a road the deck crosses
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
                # the deck's underside, or its girders' on a bridge
                top = pa["z"] + (pb["z"] - pa["z"]) * t - DECK_DEPTH_CM - (GIRDER_CM if "Bridge" in mesh else 0.0)
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


TOWER_OVER_CM = {"cable-stayed bridge": 6000.0, "suspension bridge": 9000.0}  # a tower's top over its deck
TOWER_LEG_CM = (400.0, 300.0)  # a tower leg: along the deck, across it
TOWER_CLEAR_CM = 150.0  # open air between the deck's edge and a leg
STAY_EVERY_CM = 2500.0  # a stay cable about every 25 m of deck
STAY_REACH = 0.19  # each fan reaches this share of the span from its tower: towers at 0.3 and 0.7 all but meet
STAY_WIDE = 2.5  # a stay's Y scale: the 12 cm bar becomes a 30 cm cable


def _decks(actors: dict) -> list:
    """Every bridge and viaduct segment: (ax, ay, az, bx, by, bz, half width, road surface to its underside)."""
    out = []
    for a in actors.values():
        by = {p["i"]: p for p in a["points"]}
        for s in a["segments"]:
            mesh = (s.get("mesh") or "").split("/")[-1]
            pa, pb = by.get(s["a"]), by.get(s["b"])
            if mesh in STRUCTURES and pa and pb:
                half = HALF.get(next((k for k, m in ROAD_MESH.items() if m.split("/")[-1] == mesh), ""), 800.0)
                out.append((pa["x"], pa["y"], pa["z"], pb["x"], pb["y"], pb["z"], half,
                            DECK_DEPTH_CM + (GIRDER_CM if "Bridge" in mesh else 0.0)))
    return out


def _deck_at(decks: list, x: float, y: float, within: float = 1500.0):
    """The deck over a spot (or within `within` cm beside it): (x, y on its centre line, z, half width, ux, uy,
    depth to its underside), or None."""
    best = None
    for ax, ay, az, bx, by, bz, half, deep in decks:
        L2 = (bx - ax) ** 2 + (by - ay) ** 2
        if L2 < 1:
            continue
        t = max(0.0, min(1.0, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / L2))
        px, py = ax + (bx - ax) * t, ay + (by - ay) * t
        d = math.hypot(x - px, y - py)
        if d <= within and (best is None or d < best[0]):
            L = math.sqrt(L2)
            best = (d, px, py, az + (bz - az) * t, half, (bx - ax) / L, (by - ay) / L, deep)
    return best[1:] if best else None


def bridge_towers(actors: dict, bridges: list, ground, sea=None) -> dict:
    """Real towers for the cable-stayed and suspension bridges (`bridges`: over_water's records, each with its two
    tower spots on the deck). A tower stands ASTRIDE the deck, never in it: a leg outside each edge, from the
    ground or the bed to 60 / 90 m over the road, joined by a beam under the deck (the deck rests on it), one
    high over the road and one at the top. From each leg's top a fan of stay cables runs down to that edge of
    the deck, both ways along it. Site rows [x, y, z, yaw, sx, sy, sz]:
      legs   SM_Kit_BridgePier (200 x 200 x 100 cm at scale 1)
      beams  SM_Kit_PierCap (200 x 100 x 120 cm)
      stays  SM_Kit_BridgeStay: a bar from 1 m up over its origin down to 1 m out along +X, so sx = how far out
             the cable lands (m) and sz = how high over the deck it starts (m); z is the deck's surface there
      spans  (x, y, ux, uy, reach, half) per tower: the deck its cables carry (under_cables: no pier there)."""
    out = {"legs": [], "beams": [], "stays": [], "spans": []}
    decks = _decks(actors)
    la, lc = TOWER_LEG_CM
    for b in bridges or []:
        over = TOWER_OVER_CM.get(b.get("type"))
        if not over or not b.get("towers"):
            continue
        span = float(b.get("span_m") or 0) * 100
        near = [d for d in decks if min(math.hypot(d[0] - b["x"], d[1] - b["y"]),
                                        math.hypot(d[3] - b["x"], d[4] - b["y"])) < span + 20000]
        reach = STAY_REACH * span
        n = max(3, min(8, round(reach / STAY_EVERY_CM)))
        for spot in b["towers"]:
            at = _deck_at(near, spot[0], spot[1])
            if not at:
                continue  # the deck is not there any more (the road was changed by hand): no tower in open water
            tx, ty, z, half, ux, uy, deep = at
            nx, ny = -uy, ux
            yaw = round(math.degrees(math.atan2(uy, ux)), 1)
            top = z + over
            off = half + TOWER_CLEAR_CM + lc / 2
            feet = []
            for side in (1, -1):
                lx, ly = tx + nx * off * side, ty + ny * off * side
                foot = ground(lx, ly)
                if foot is None:
                    foot = (sea if sea is not None else z) - 500.0
                feet.append((side, lx, ly))
                out["legs"].append([round(lx), round(ly), round(foot), yaw, la / 200, lc / 200, round((top - foot) / 100, 2)])
            for bz, tall in ((z - deep - 250.0, 250.0), (z + over * 0.55, 250.0), (top - 300.0, 300.0)):
                out["beams"].append([round(tx), round(ty), round(bz), yaw, round(la * 0.8 / 200, 2),
                                     round((2 * off + lc) / 100, 2), round(tall / 120, 2)])
            for side, lx, ly in feet:
                for way in (1, -1):
                    for k in range(1, n + 1):
                        d = reach * k / n
                        deck = _deck_at(near, tx + ux * d * way, ty + uy * d * way, 600.0)
                        if not deck:
                            continue  # past the deck's end: no cable into the air
                        ax, ay = deck[0] + nx * (half - 40.0) * side, deck[1] + ny * (half - 40.0) * side
                        up = top - 200.0 - (n - k) * 220.0 - deck[2]  # the longest cable leaves from the very top
                        run = math.hypot(ax - lx, ay - ly)
                        if up < 500 or run < 300:
                            continue
                        out["stays"].append([round(lx), round(ly), round(deck[2]), round(math.degrees(math.atan2(ay - ly, ax - lx)), 2),
                                             round(run / 100, 2), STAY_WIDE, round(up / 100, 2)])
            out["spans"].append((tx, ty, ux, uy, reach, half))
    return out


def under_cables(spans: list):
    """(x, y) -> True under a stretch of deck that hangs from a tower's cables: no pier bent stands there."""
    def held(x: float, y: float) -> bool:
        for tx, ty, ux, uy, reach, half in spans:
            if abs((x - tx) * ux + (y - ty) * uy) <= reach + 300 and abs(-(x - tx) * uy + (y - ty) * ux) <= half + 300:
                return True
        return False
    return held


def bridge_abutments(actors: dict) -> list:
    """Where a bridge, viaduct or ramp meets the road on the ground: an abutment under the deck's end, its wall
    across the deck's width and a wing wall back along each side of the approach. Site rows
    [x, y, z, yaw, 1, width_m, 1] for SM_Kit_BridgeAbutment (1 m wide at scale 1, ABUTMENT_CM tall, its wall's
    face at x 0 looking along +X under the deck)."""
    rows = []
    for a in actors.values():
        by = {p["i"]: p for p in a["points"]}
        at: dict = {}
        for s in a["segments"]:
            mesh = (s.get("mesh") or "").split("/")[-1]
            for i, j in ((s["a"], s["b"]), (s["b"], s["a"])):
                if mesh and i in by and j in by:
                    at.setdefault(i, []).append((mesh, by[j]))
        for i, legs in at.items():
            decks = [(m, q) for m, q in legs if m in STRUCTURES]
            if not decks or len(decks) == len(legs):
                continue  # no deck here, or the deck runs on
            p, (mesh, q) = by[i], decks[0]
            half = HALF.get(next((k for k, m in ROAD_MESH.items() if m.split("/")[-1] == mesh), ""), 800.0)
            rows.append([round(p["x"]), round(p["y"]), round(p["z"] - DECK_DEPTH_CM - ABUTMENT_CM),
                         round(math.degrees(math.atan2(q["y"] - p["y"], q["x"] - p["x"])), 1), 1.0, round(half * 2 / 100 + 1.0, 2), 1.0])
    return rows


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
                city.get("turn"), city.get("shape"), city.get("yield_to"), city.get("fw_shore"),
                [o[k] for k in ("freeways", "interchange_models", "block_m", "block_ratio", "downtown", "normal", "collector_every", "core_roads",
                                "neighbourhood_roads", "canals")],
                [(a.get("center"), a.get("heading"), a.get("type")) for a in city.get("airports") or []],
                [(y.get("frame"), y.get("L"), y.get("D"), y.get("bank"))
                 for y in (city.get("shipyards") or []) + (city.get("shipyards_near") or [])]))
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


JOIN_REACH_CM = 1200.0  # a private street end this far past a street's edge still joins it with a junction
_KIND_OF_MESH = {}
for _k, _m in ROAD_MESH.items():
    if HALF.get(_k, 0) >= HALF.get(_KIND_OF_MESH.get(_m, ""), 0):
        _KIND_OF_MESH[_m] = _k


def unjoin_private(roads: dict) -> dict:
    """The roads actor as it was before join_private: every joined private street out, every host road whole again."""
    pts = [p for p in roads["points"] if not p.get("join")]
    segs = [s for s in roads["segments"] if not s.get("join")]
    for p in roads["points"]:
        undo = p.get("undo")
        if p.get("join") and undo:  # what a join cut out of a host; a host a join made itself is not brought back
            pts += [dict(q) for q in undo["points"] if not q.get("join")]
            segs += [dict(s) for s in undo["segments"] if not s.get("join")]
    seen: set = set()
    pts = [p for p in pts if not (p["i"] in seen or seen.add(p["i"]))]
    seen = set()
    segs = [s for s in segs if not (s["i"] in seen or seen.add(s["i"]))]
    keep = {p["i"] for p in pts}
    return {"points": pts, "segments": [s for s in segs if s["a"] in keep and s["b"] in keep]}


def join_private(roads: dict, access: dict, folder: str) -> tuple:
    """Every private street joins the street (or the private street) its end meets with a real junction, in ONE
    spline actor: UEFN only connects control points of the same actor, so a private street kept in its own
    '<City> Access' actor ended on the grass beside its street. Each private street run moves from `access` into
    `roads`; where its end lies within the host's half width + JOIN_REACH_CM, the host is cut open there: a junction
    centre (pending piece, Build pieces makes the mesh) with a mouth MOUTH_CM up and down the host and one into the
    private street. Driveways stay in `access`. Returns (roads, access, joins). Re-running is safe: unjoin first."""
    roads = unjoin_private(roads)
    rp = {p["i"]: dict(p) for p in roads["points"]}
    rs = [dict(s) for s in roads["segments"]]
    ap = {p["i"]: dict(p) for p in access["points"]}
    pr_mesh = ROAD_MESH["PR"].rsplit("/", 1)[-1]
    moving = [dict(s) for s in access["segments"] if s["mesh"] == pr_mesh]
    moved_pts = {i for s in moving for i in (s["a"], s["b"])}
    rest_segs = [dict(s) for s in access["segments"] if s["mesh"] != pr_mesh and s["a"] not in moved_pts and s["b"] not in moved_pts]
    rest_pts = {i for s in rest_segs for i in (s["a"], s["b"])}
    nid = iter(range(max([0] + list(rp) + list(ap) + [s["i"] for s in rs + list(access["segments"])]) + 1, 1 << 30))
    remap = {}
    for i in moved_pts:  # the private street points come over under new ids
        q = dict(ap[i], i=next(nid), join=True)
        remap[i] = q["i"]
        rp[q["i"]] = q
    for s in moving:
        rs.append(dict(s, i=next(nid), a=remap[s["a"]], b=remap[s["b"]], join=True))

    def nbrs(i):
        return [s for s in rs if i in (s["a"], s["b"])]

    def half(s):
        return HALF.get(_KIND_OF_MESH.get(s["mesh"], ""), 400.0)

    def plain(i):  # a point along a road (never a junction's mouth: one of its links has no mesh)
        p, two = rp[i], nbrs(i)
        return len(two) == 2 and all(s["mesh"] for s in two) and not p.get("mesh") and p.get("raise", True)

    joins = 0
    ends = [remap[i] for i in moved_pts if len([s for s in moving if i in (s["a"], s["b"])]) == 1]
    for e in ends:
        if e not in rp or len(nbrs(e)) != 1:
            continue
        E = rp[e]
        own = {e}
        stack = [e]
        while stack:  # the end's own private street, never its own host
            c = stack.pop()
            for s in nbrs(c):
                o = s["b"] if s["a"] == c else s["a"]
                if o not in own and rp[o].get("join"):
                    own.add(o)
                    stack.append(o)
        best = None
        for s in rs:
            if not s["mesh"] or s["a"] in own or s["b"] in own or not s.get("raise", True):
                continue
            a, b = rp[s["a"]], rp[s["b"]]
            dx, dy = b["x"] - a["x"], b["y"] - a["y"]
            L2 = dx * dx + dy * dy
            if L2 < 1.0:
                continue
            t = max(0.0, min(1.0, ((E["x"] - a["x"]) * dx + (E["y"] - a["y"]) * dy) / L2))
            px, py = a["x"] + dx * t, a["y"] + dy * t
            d = math.hypot(E["x"] - px, E["y"] - py)
            if d <= half(s) + JOIN_REACH_CM and (best is None or d < best[0]):
                best = (d, s, t, px, py)
        if best is None:
            continue
        _d, host, t, px, py = best
        a, b = rp[host["a"]], rp[host["b"]]
        pz = a["z"] + (b["z"] - a["z"]) * t
        hx, hy = (b["x"] - a["x"]), (b["y"] - a["y"])
        hl = math.hypot(hx, hy) or 1.0
        ux, uy = hx / hl, hy / hl

        def walk(start_seg, from_pt, dist):
            """Points passed and the cut along the host from the centre, through plain points, `dist` cm one way."""
            gone, s, cur, (cx, cy), left = [], start_seg, from_pt, (px, py), dist
            while True:
                q = rp[cur]
                step = math.hypot(q["x"] - cx, q["y"] - cy)
                if step >= left:
                    f = left / (step or 1.0)
                    return gone, s, cur, (cx + (q["x"] - cx) * f, cy + (q["y"] - cy) * f)
                if not plain(cur):
                    return None
                left -= step
                gone.append(cur)
                cx, cy = q["x"], q["y"]
                s = next(x for x in nbrs(cur) if x is not s)
                cur = s["b"] if s["a"] == cur else s["a"]

        back = walk(host, host["a"], MOUTH_CM)
        fwd = walk(host, host["b"], MOUTH_CM)
        if not back or not fwd:
            continue  # too close to another junction or a piece: left as it is
        # the private street's side: drop its points inside the junction, the mouth on its line MOUTH_CM out
        run, s, cur = [e], next(iter(nbrs(e))), e
        while True:
            nxt = s["b"] if s["a"] == cur else s["a"]
            q = rp[nxt]
            if math.hypot(q["x"] - px, q["y"] - py) > MOUTH_CM or len(nbrs(nxt)) != 2:
                break
            run.append(nxt)
            s = next(x for x in nbrs(nxt) if x is not s)
            cur = nxt
        far = rp[nxt]
        vx, vy = far["x"] - px, far["y"] - py
        vl = math.hypot(vx, vy) or 1.0
        if vl <= MOUTH_CM + 100:
            continue  # a stub shorter than the junction: nothing to join
        undo = {"points": [dict(rp[i]) for i in back[0] + fwd[0]],
                "segments": [dict(x) for x in rs if x["a"] in back[0] + fwd[0] or x["b"] in back[0] + fwd[0] or x is host]}
        tmpl = dict(host)
        for i in back[0] + fwd[0]:
            rp.pop(i, None)
        dead = set(back[0] + fwd[0])
        rs = [x for x in rs if x is not host and x["a"] not in dead and x["b"] not in dead]
        for i in run:
            rp.pop(i, None)
        rs = [x for x in rs if x is not s and x["a"] not in run and x["b"] not in run]
        j = _point(next(nid), px, py, pz, 0.0, max(half(host), HALF["PR"]) + 300.0, JUNCTION)
        j.update(join=True, undo=undo)
        m1 = _point(next(nid), back[3][0], back[3][1], pz, math.degrees(math.atan2(-uy, -ux)), half(host))
        m2 = _point(next(nid), fwd[3][0], fwd[3][1], pz, math.degrees(math.atan2(uy, ux)), half(host))
        mx, my = px + vx / vl * MOUTH_CM, py + vy / vl * MOUTH_CM
        m3 = _point(next(nid), mx, my, pz, math.degrees(math.atan2(vy, vx)), HALF["PR"])
        for p in (j, m1, m2, m3):
            p["join"] = True
            rp[p["i"]] = p
        new = [_segment(next(nid), j["i"], m["i"], "", folder) for m in (m1, m2, m3)]
        new.append(dict(tmpl, i=next(nid), a=back[2], b=m1["i"], ta=0.0, tb=0.0, join=True))
        new.append(dict(tmpl, i=next(nid), a=m2["i"], b=fwd[2], ta=0.0, tb=0.0, join=True))
        new.append(_segment(next(nid), m3["i"], nxt, "PR", folder))
        for x in new:
            x["join"] = True
        rs += new
        joins += 1
    by_i = rp
    for s in rs:
        if s.get("join") and s["a"] in by_i and s["b"] in by_i:
            st.retangent(by_i, s)
    keep = set(rp)
    rs = [s for s in rs if s["a"] in keep and s["b"] in keep]
    out_access = {"points": [ap[i] for i in rest_pts], "segments": rest_segs}
    return {"points": list(rp.values()), "segments": rs}, out_access, joins


LINK_REACH_CM = 600.0  # a walkway / driveway / sidewalk end this far past a host's edge is tied to it
# ... onto the host's own point when one is this close along it, instead of a new one: the link draws nothing, so
# it may run aslant. At 1.5 m every driveway split its street once more (a street point per house, both sides)
SNAP_CM = 2500.0


def join_links(roads: dict, extra: list, folder: str) -> tuple:
    """Every spline in ONE actor and none on its own: each actor in `extra` (sidewalks, walkways, driveways) moves into
    `roads`, and every loose end (one segment) is tied to the nearest road / sidewalk it reaches with a mesh-less link
    to a point on that host (the host's own point when one is within SNAP_CM, else the host is split there). The link
    draws nothing and never shapes the ground: the pieces look as they did, but form one connected spline network.
    Marked like join_private's work, so unjoin_private takes it all out again. Returns (roads, links)."""
    rp = {p["i"]: dict(p) for p in roads["points"]}
    rs = [dict(s) for s in roads["segments"]]
    nid = iter(range(max([0] + list(rp) + [s["i"] for s in rs]) + 1, 1 << 30))
    for actor in extra:  # their points and segments come over under new ids
        remap = {}
        for p in actor["points"]:
            q = dict(p, i=next(nid), join=True)
            remap[p["i"]] = q["i"]
            rp[q["i"]] = q
        for s in actor["segments"]:
            if s["a"] in remap and s["b"] in remap:
                rs.append(dict(s, i=next(nid), a=remap[s["a"]], b=remap[s["b"]], join=True))
    at: dict = {}
    for s in rs:
        at.setdefault(s["a"], []).append(s)
        at.setdefault(s["b"], []).append(s)

    def half(s):
        return HALF.get(_KIND_OF_MESH.get(s["mesh"], ""), 200.0)

    def component(e):  # every point joined to e through segments that draw a mesh (its own road / path)
        seen, stack = {e}, [e]
        while stack:
            c = stack.pop()
            for s in at.get(c, []):
                o = s["b"] if s["a"] == c else s["a"]
                if o not in seen and s["mesh"]:
                    seen.add(o)
                    stack.append(o)
        return seen

    links = 0
    for e in [i for i in list(rp) if len(at.get(i, [])) == 1 and at[i][0]["mesh"] and rp[i].get("join")]:
        E, own = rp[e], component(e)
        best = None
        for s in rs:
            if not s["mesh"] or s["a"] in own or s["b"] in own:
                continue
            a, b = rp[s["a"]], rp[s["b"]]
            dx, dy = b["x"] - a["x"], b["y"] - a["y"]
            L2 = dx * dx + dy * dy
            if L2 < 1.0:
                continue
            t = max(0.0, min(1.0, ((E["x"] - a["x"]) * dx + (E["y"] - a["y"]) * dy) / L2))
            px, py = a["x"] + dx * t, a["y"] + dy * t
            d = math.hypot(E["x"] - px, E["y"] - py)
            if d <= half(s) + LINK_REACH_CM and (best is None or d < best[0]):
                best = (d, s, t, px, py)
        if best is None:
            continue
        _d, host, t, px, py = best
        a, b = rp[host["a"]], rp[host["b"]]
        # the driveway / walkway itself reaches the host's edge (its own mesh fills the gap; the link drew nothing
        # and the slab ended metres out on the grass), along its own line when that heads for the host
        own_seg = at[e][0]
        o = rp[own_seg["b"] if own_seg["a"] == e else own_seg["a"]]
        ux, uy = E["x"] - o["x"], E["y"] - o["y"]
        ul = math.hypot(ux, uy) or 1.0
        if _d > half(host) + 30 and _d > 1:
            c = (ux * (px - E["x"]) + uy * (py - E["y"])) / (ul * _d)
            if c >= 0.5:
                s_ = (_d - half(host)) / c
                E["x"], E["y"] = E["x"] + ux / ul * s_, E["y"] + uy / ul * s_
                E["z"] = a["z"] + (b["z"] - a["z"]) * t
                st.retangent(rp, own_seg)
        L = math.hypot(b["x"] - a["x"], b["y"] - a["y"])
        if min(t, 1 - t) * L <= SNAP_CM:
            h = a if t * L <= (1 - t) * L else b
        else:  # split the host there: two halves of the same piece, one shared point
            pz = a["z"] + (b["z"] - a["z"]) * t
            h = _point(next(nid), px, py, pz, a["yaw"], a.get("w", half(host)))
            h["lines"] = [l for l in (a.get("lines") or []) if not l.startswith(("Mesh=", "MeshScale="))] or h["lines"]
            h.update(join=True, undo={"points": [], "segments": [dict(host)]})
            rp[h["i"]] = h
            rs.remove(host)
            at[host["a"]].remove(host)
            at[host["b"]].remove(host)
            for u, v in ((host["a"], h["i"]), (h["i"], host["b"])):
                ns = dict(host, i=next(nid), a=u, b=v, ta=0.0, tb=0.0, join=True)
                rs.append(ns)
                at.setdefault(u, []).append(ns)
                at.setdefault(v, []).append(ns)
                st.retangent(rp, ns)
        link = _segment(next(nid), e, h["i"], "", folder, lifted=True)  # draws nothing, shapes nothing
        link["join"] = True
        rs.append(link)
        at.setdefault(e, []).append(link)
        at.setdefault(h["i"], []).append(link)
        links += 1
    return {"points": list(rp.values()), "segments": rs}, links


COLLECTOR_ROWS = 0.65  # of the suburbs' rows of blocks, how many share one winding street from block to block
SUPERBLOCK_SHARE = 0.8  # of the suburbs' whole collector cells, how many are one subdivision with no public street inside
LOT_W_CM, LOT_D_CM = 1600.0, 3500.0  # a lot: 16 m frontage, up to 35 m deep (two back to back in a block)
# ... wider where what is built there is wider: an office block or a tower downtown (a 16 m lot fits neither, and
# every downtown lot between two towers stood empty), a warehouse or a factory in the industrial zone
ZONE_LOT_W_CM = {"downtown": 2800.0, "industrial": 4000.0}


NO_ROAD = "--"  # a block's side where the street was not built (_street_gaps): the next block lies right there
NO_ROAD_INSET = 150.0  # ... so the block reaches to 1.5 m short of that line (back fences 3 m apart), no sidewalk
SOLID_MIN = 0.86  # a block that fills less of its own box than this (an L, a wedge, one a dead end reaches deep into)
# is cut into plain lots along its streets: a neighbourhood is laid out in the box


def city_layout(city: dict, opts: dict | None = None, wet=None, ground=None, keep: list | None = None,
                water: dict | None = None, clear: list | None = None) -> dict:
    """BlockGenerator + neighbourhoods: every block of the city's street grid becomes either plain street-fronting
    lots (parcels) or one neighbourhood (neighbourhoods.TYPES: subdivisions with private streets, apartment
    complexes with parking), picked per block from the city's seed and weighted by its district ring. Then
    every street side that no block lies on gets a row of lots of its own (frontage_lots): the land between
    the last street and the city's edge, and round every dead end, is built on like any other.
    Lots: [[x, y, yaw, width_cm, depth_cm, front x, front y, role ('' = any layer), footprint]].
    keep: the planned facilities to keep where they are; clear: the world's keep zones (nothing is laid there)."""
    from . import neighbourhoods as nb
    from . import parcels
    o = city_options(city, opts)
    unbuilt: list = []
    nodes, edges = city_graph(city, opts, wet, virtual=unbuilt)
    # the land is parted into blocks along every street, and along the streets that were not built between two
    # neighbouring blocks (_street_gaps): each is still laid out on its own ground, with nothing between it and
    # the next
    cut_nodes, cut_edges = nodes, edges
    if unbuilt:
        cut_nodes, cut_edges = dict(nodes), list(edges)
        have = {(e[0], e[1]) for e in edges} | {(e[1], e[0]) for e in edges}
        for a, b, pa, pb in unbuilt:
            if (a, b) not in have:
                cut_nodes.setdefault(a, pa)
                cut_nodes.setdefault(b, pb)
                cut_edges.append([a, b, NO_ROAD])
    cut_half = {**HALF, NO_ROAD: NO_ROAD_INSET - parcels.SIDEWALK_CM}
    wet = water_of(city, o, wet)  # canals are water too: no lot in them
    rnd = random.Random(f"lots:{city.get('seed', 1)}")
    fw = freeway_plan(city, o)
    out = {"lots": [], "streets": [], "parking": [], "stalls": [], "pads": [], "trees": [], "fences": [], "pools": [], "hoods": {},
           "blocks": [], "rezoned": []}
    down, normal = o["downtown"], max(o["normal"], o["downtown"])
    zones = [z for z in city.get("zones") or [] if isinstance(z, dict) and "x" in z and "y" in z]
    big_park = central_park(city, o, wet)
    if big_park:  # the block the streets left whole there is a park (the person's own choice for it still wins)
        grid_at = street_grid(city, o)[0]
        corners = [grid_at(i, j)[:2] for i in (big_park[0], big_park[2]) for j in (big_park[1], big_park[3])]
        zones = [{"x": sum(p[0] for p in corners) / 4, "y": sum(p[1] for p in corners) / 4, "kind": "park"}] + zones
    park_grounds: list = []
    in_wedge = zoning(city, o)[1]
    PARTS = ("lots", "streets", "parking", "stalls", "pads", "trees", "fences", "pools")
    fronted: set = set()  # (node, next node) of every street side a block lies on
    meets: set = set()  # the middle of every block side with no street: where two subdivisions' streets may meet
    ce = o["collector_every"]
    crossing = lambda n: isinstance(n, tuple) and len(n) == 2 and isinstance(n[0], int) and n[0] % ce == 0 and n[1] % ce == 0
    for area, sides in parcels.block_faces(cut_nodes, cut_edges, cut_half):
        fronted.update((a, b) for a, b, _k in sides)
        # where this block has no road on a side: that side's middle (each stretch from one main-road crossing
        # to the next), where its streets meet the neighbour's; and the lines no lot may front
        ghosts = [(cut_nodes[a][:2], cut_nodes[b][:2]) for a, b, k in sides if k == NO_ROAD]
        open_mids, run = [], []
        start = next((i for i, (a, _b, k) in enumerate(sides) if k != NO_ROAD or crossing(a)), 0) if ghosts else 0
        for a, b, k in (sides[start:] + sides[:start] + [(None, None, None)] if ghosts else ()):
            if k == NO_ROAD:
                run = (run or [a]) + [b]
            if run and (k != NO_ROAD or crossing(b)):
                m = len(run) // 2  # (the same point from either block: the middle crossing, or the middle of the middle piece)
                p, q = cut_nodes[run[m]], cut_nodes[run[m] if len(run) % 2 else run[m - 1]]
                open_mids.append([round((p[0] + q[0]) / 2), round((p[1] + q[1]) / 2)])
                run = []
        meets.update((p[0], p[1]) for p in open_mids)
        cx, cy = sum(p[0] for p in area) / len(area), sum(p[1] for p in area) / len(area)
        share = zone_share(city, cx, cy)
        ring = "core" if share <= down else "normal" if share <= normal else "housing"
        _, _, ux, uy, L, W = parcels._obb(area)
        if abs(ux) < abs(uy):  # the axis neighbourhoods' 'collector' street runs along (the same for every block)
            ux, uy = -uy, ux
        row = round((cx * uy - cy * ux) * (1 if ux > 0 else -1) / 6000)  # blocks side by side along it share a row
        kind = nb.pick(ring, rnd) if ring != "core" and min(L, W) >= nb.MIN_SIDE else "parcels"
        # (a block the freeway cuts gets plain lots, the ones clear of it kept; so does one that is no near
        # rectangle: a neighbourhood drawn in its box would not fit it)
        cut = bool(fw.lines and fw.blocked(cx, cy, 0.5 * max(L, W))) or parcels.solid(area) < SOLID_MIN
        if clear and not cut:
            # ... and one that a keep zone reaches into: nothing is laid in the zone, and what a neighbourhood has
            # left outside it is half a layout (its entrance gone, a block bare beside the zone)
            cut = any(in_keep(clear, p[0], p[1], 2000) for p in area) or in_keep(clear, cx, cy, 2000) or \
                any(_inside(area, x, y) for k in clear for x in (k["x0"], k["x1"]) for y in (k["y0"], k["y1"]))
        if cut:
            kind = "parcels"
        elif ring != "core" and min(L, W) >= nb.BIG_SIDE:
            kind = "collector"  # a superblock: plain lots would be landlocked in its middle
        elif ring == "housing" and kind != "parcels" and \
                random.Random(f"row:{city.get('seed', 1)}:{row}").random() < COLLECTOR_ROWS:
            kind = "collector"  # a whole row of suburb blocks: the one winding street runs on through them all
        zone = "downtown" if ring == "core" else "industrial" if in_wedge(cx, cy) else ring
        if zone == "industrial":
            kind = "parcels"  # industry stands on plain lots along the streets: no subdivision among the factories
        plain = lambda r, z: {"lots": [[x, y, yaw, w, d, fx, fy, "", False]
                                       for x, y, yaw, w, d, _poly, fx, fy in parcels.lots_in(area, ZONE_LOT_W_CM.get(z, LOT_W_CM), LOT_D_CM, r)
                                       # (no lot faces a side where no road was built)
                                       if not any(_dist_seg((fx, fy), p, q) < NO_ROAD_INSET + 150 for p, q in ghosts)]}
        laid = lambda k, r: nb.generate(area, k, r, joins=open_mids if k == "collector" else None, closed=ghosts)
        # the block as the city's own plan lays it, on the city's shared random as always ...
        add = plain(rnd, zone) if kind == "parcels" else laid(kind, rnd)
        own = next((z for z in reversed(zones) if _inside(area, z["x"], z["y"])), None) if zones else None
        if own:
            # ... then the person's own choice for it, on a random of the block's own: the plan above was still
            # drawn (and dropped), so no other block of the city changes when one is given a type
            local = random.Random(f"block:{city.get('seed', 1)}:{round(cx)}:{round(cy)}")
            want = own.get("kind") or ""
            if own.get("zone") in BLOCK_ZONES and own["zone"] != zone:
                zone = own["zone"]
                out["rezoned"].append([[[round(x), round(y)] for x, y in area], zone])
                if not want and zone == "downtown":  # a core block is plain lots for the towers; any other keeps
                    want = "parcels"  # its own layout and only changes what is built on it
            if want == "park":
                kind, add = "park", _park(area, local)
                park_grounds += add["grounds"]
            elif want == "parcels" or (want in BLOCK_KINDS and want in nb.TYPES and not cut
                                       and min(L, W) >= nb.MIN_SIDE):
                kind, add = want, plain(local, zone) if want == "parcels" else laid(want, local)
        # some of its lots are planted instead of built on: the block's own share, else the world's
        green = own["green"] if own and own.get("green") is not None else o["green_lots"]
        green = max(0.0, min(1.0, float(green)))
        if green and add.get("lots"):
            add = _green_lots(add, green, random.Random(f"green:{city.get('seed', 1)}:{round(cx)}:{round(cy)}"))
        if kind == "parcels" and add.get("lots"):
            # a block of plain lots is built along its streets only: whatever it leaves in the middle (a court
            # 130 m across in a 200 m block, the strip along a side with no street) is planted, never bare
            court = _court(area, add["lots"], random.Random(f"court:{city.get('seed', 1)}:{round(cx)}:{round(cy)}"))
            if len(court) >= COURT_MIN:
                add = {**add, "trees": list(add.get("trees") or []) + court}
        out["hoods"][kind] = out["hoods"].get(kind, 0) + 1
        for key in PARTS:
            out[key] += add.get(key) or []
        out["blocks"].append([round(cx), round(cy), kind, zone, [[round(x), round(y)] for x, y in area], round(green, 2)])
    # two subdivisions with no road between them: where the street of one ends on the street of the other, the
    # two are one street
    ends: dict = {}
    for s in out["streets"]:
        for at_end in (0, -1):
            ends.setdefault((s[at_end][0], s[at_end][1]), []).append(s)
    for point in [k for k, v in ends.items() if len(v) == 2 and v[0] is not v[1]]:
        a, b = ends[point]
        if a not in out["streets"] or b not in out["streets"]:
            continue
        a_ = a if (a[-1][0], a[-1][1]) == point else a[::-1]
        b_ = b if (b[0][0], b[0][1]) == point else b[::-1]
        one = a_ + b_[1:]
        out["streets"] = [s for s in out["streets"] if s is not a and s is not b] + [one]
        for k, v in ends.items():
            ends[k] = [one if s is a or s is b else s for s in v]
    # ... and where the block beside it has no street to meet (a complex, a loop, plain lots), the street does
    # not run up to a back fence: it stops 13 m short, in a turnaround
    for s in out["streets"]:
        for at_end, nxt in ((0, 1), (-1, -2)):
            p, q = s[at_end], s[nxt]
            if (p[0], p[1]) in meets:
                d = math.dist(p[:2], q[:2]) or 1.0
                back = min(1300.0, 0.9 * d)
                s[at_end] = [round(p[0] + (q[0] - p[0]) * back / d), round(p[1] + (q[1] - p[1]) * back / d)]
                out["pads"].append([s[at_end][0], s[at_end][1], nb.BULB])
    out["lots"] += frontage_lots(city, o, nodes, edges, fronted, wet, clear)
    if clear:
        # a keep zone is left alone: no street is laid into it (generate), so no lot, private street, parking or
        # yard is planned there either (lots stood there on streets that were never built, empty for good)
        for key in ("lots", "parking", "stalls", "pads", "fences", "pools"):
            out[key] = [r for r in out[key] if not in_keep(clear, r[0], r[1], 2000)]
        out["streets"] = [s for s in out["streets"] if not any(in_keep(clear, p[0], p[1], 2000) for p in s)]
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
    if out["rezoned"]:  # the planner and the waterfront see a rezoned block as what it now is
        ring0, ind0 = ring_of, industrial
        zone_at = lambda x, y: next((z for poly, z in out["rezoned"] if _inside(poly, x, y)), "")
        ring_of = lambda x, y: {"downtown": "core", "normal": "normal", "housing": "housing"}.get(zone_at(x, y)) or ring0(x, y)
        industrial = lambda x, y: (lambda z: z == "industrial" if z else ind0(x, y))(zone_at(x, y))
    if o["waterfront"] == "yes" and water and water.get("sea") is not None and ground:
        # waterfront first: the shore's quays, marinas, beaches and ports take their land before the services
        from . import waterfront
        pop = round(len(out["lots"]) * 9)  # a rough count for the port / ferry thresholds; urban.plan counts properly
        roads = [(*nodes[a][:2], *nodes[b][:2], HALF.get(k, 500.0)) for a, b, k, *_ in edges if a in nodes and b in nodes]
        roads += [(*p[:2], *q[:2], 400.0) for s in out["streets"] for p, q in zip(s, s[1:])]
        wf = waterfront.plan(city, water, out["lots"], ring_of, industrial, ground, nodes, pop, roads)
        out["lots"], out["water"] = wf["lots"], wf["rows"]
        # (a lane joins a street on the ground: one joined to a bridge's deck ran 12 m up to it)
        on_ground = [(*nodes[a][:2], *nodes[b][:2], HALF.get(k, 500.0)) for a, b, k, *via in edges
                     if a in nodes and b in nodes and not (via and via[0])]
        on_ground += [(*p[:2], *q[:2], 400.0) for s in out["streets"] for p, q in zip(s, s[1:])]
        out["streets"] += [s for s in home_lanes(out["lots"], on_ground) if not (wet and any(wet(*p[:2]) for p in s))]
        out["waterfront"] = {k: wf[k] for k in ("segments", "developments", "shoreline_m", "made_m", "report", "shipyards")}
    # the blocks' own trees keep off what is not a block's ground: a made shore, a shipyard, an airfield, a keep zone
    from . import airport, shipyard, waterfront
    look = waterfront.Made([city]) if city.get("shores") else None
    yards = (city.get("shipyards") or []) + (city.get("shipyards_near") or [])
    if look or yards or city.get("airports") or clear:
        out["trees"] = [t for t in out["trees"] if not (
            (look and look.shelf(t[0], t[1], 300)) or in_keep(clear, t[0], t[1], 2000)
            or any(shipyard.reserved(y, t[0], t[1], 500) for y in yards if y.get("z") is not None)
            or any(airport.inside(a, t[0], t[1], 2000) for a in city.get("airports") or []))]
    if o["planner"] == "yes" and out["lots"]:
        from . import urban
        barriers = [(p, q) for ln in fw.lines for p, q in zip(ln["points"], ln["points"][1:])]
        got = urban.plan(out["lots"], nodes, edges, ring_of, ground, [tuple(i["at"]) for i in fw.interchanges],
                         keep, (city["x"], city["y"]), city["radius"], industrial, barriers)
        out["lots"] = got["lots"]
        out["parking"] += got["parking"]
        out["stalls"] += got["stalls"]
        out["grounds"], out["plan"] = list(got["grounds"]), got["plan"]
        # the drives into the sites' car parks: private streets like a complex's own
        out["streets"] += [s for s in got.get("streets") or []
                           if not any(fw.blocked(p[0], p[1]) or (wet and wet(p[0], p[1])) for p in s)]
        if fw.lines and out["plan"]:
            # a site the planner merged from lots each clear of the freeway can still have its middle in the
            # freeway's corridor (a power plant did): that site is not built, its grounds go with it
            lost = [f for f in out["plan"]["facilities"] if fw.blocked(f["x"], f["y"])]
            if lost:
                out["plan"]["facilities"] = [f for f in out["plan"]["facilities"] if f not in lost]
                for f in lost:
                    out["plan"]["counts"][f["type"]] = out["plan"]["counts"].get(f["type"], 1) - 1
                out["lots"] = [r for r in out["lots"] if not (len(r) > 8 and r[8] is True and fw.blocked(r[0], r[1]))]
                out["grounds"] = [g for g in out["grounds"] if not fw.blocked(g[0], g[1])]
    # a port's road comes into the city across land the blocks were cut without it: no lot, tree or car park on it
    for line in city.get("port_roads") or []:
        segs = list(zip(line, line[1:]))
        near = lambda x, y, m: any(_dist_seg((x, y), p, q) < m for p, q in segs)
        out["lots"] = [r for r in out["lots"] if not near(r[0], r[1], HALF[PORT_ROAD] + 0.5 * max(r[3], r[4]) + 300)]
        for key in ("trees", "stalls", "pads", "fences", "pools"):
            out[key] = [r for r in out[key] if not near(r[0], r[1], HALF[PORT_ROAD] + 400)]
        out["parking"] = [r for r in out["parking"] if not near(r[0], r[1], HALF[PORT_ROAD] + 50 * max(r[3], r[4]))]
    # what the park blocks hold (paths, benches, playgrounds, a pond, playing fields): grounds like a planned site's
    out["grounds"] += [g for g in park_grounds if not fw.blocked(g[0], g[1]) and not (wet and wet(g[0], g[1]))
                       and not in_keep(clear, g[0], g[1], 2000)]
    # a stall is painted on a car park: where the slab was not built (in the water, under a freeway, in a keep
    # zone) its stalls go with it, and none is left as white lines on the grass
    slabs = [(s[0], s[1], math.cos(math.radians(s[2])), math.sin(math.radians(s[2])), s[3] * 50 + 60, s[4] * 50 + 60) for s in out["parking"]]
    by_cell: dict = {}
    for s in slabs:
        r = math.hypot(s[4], s[5])
        for gx in range(int((s[0] - r) // 20000), int((s[0] + r) // 20000) + 1):
            for gy in range(int((s[1] - r) // 20000), int((s[1] + r) // 20000) + 1):
                by_cell.setdefault((gx, gy), []).append(s)
    out["stalls"] = [t for t in out["stalls"]
                     if any(abs((t[0] - x) * c + (t[1] - y) * s_) < a and abs(-(t[0] - x) * s_ + (t[1] - y) * c) < b
                            for x, y, c, s_, a, b in by_cell.get((int(t[0] // 20000), int(t[1] // 20000)), ()))]
    return out


def city_lots(city: dict, opts: dict | None = None) -> list:
    return city_layout(city, opts)["lots"]


HOME_LOT = [1700, 2400]  # a home on the water (waterfront.HOUSE_LOT)
LANE_OUT_CM = 300.0  # its lane runs this far out from the lot's front edge
LANE_REACH_CM = 15000.0  # ... and joins a street no further than this from the row's end


def home_lanes(lots: list, roads: list) -> list:
    """Homes on the water face the land, their lawns down to the water, and every one can be driven to. They
    were laid facing the lake, their drives ran into the lawn, and no street came near them: Valley's Northgate
    and Riverton had 17 houses with no road (Nov 17 2026). Each home is turned round (in place in `lots`); a
    row with no street at its door gets a private lane along the fronts, joined at its nearer end to the nearest
    street. roads: [(x0, y0, x1, y1, half width)]. Returns the lanes, [[x, y], ...] each (private streets).
    ponytail: a row with no street within LANE_REACH_CM keeps its lane unjoined; reach further if one shows up."""
    def nearest(p):  # (distance from the street's edge, the point on its centre line)
        best = (math.inf, None)
        for x0, y0, x1, y1, h in roads:
            dx, dy = x1 - x0, y1 - y0
            t = max(0.0, min(1.0, ((p[0] - x0) * dx + (p[1] - y0) * dy) / ((dx * dx + dy * dy) or 1.0)))
            q = (x0 + dx * t, y0 + dy * t)
            best = min(best, (math.dist(p, q) - h, q), key=lambda b: b[0])
        return best
    rows, lanes = [], []
    for r in lots:
        if r[7] != "house" or [round(r[3]), round(r[4])] != HOME_LOT:
            continue
        nx, ny = (r[5] - r[0]) / (r[4] / 2), (r[6] - r[1]) / (r[4] / 2)  # toward the water (the old front)
        r[2] = round((r[2] + 180.0 + 180.0) % 360.0 - 180.0, 1)
        r[5], r[6] = round(r[0] - nx * r[4] / 2), round(r[1] - ny * r[4] / 2)
        at = (r[5] - nx * LANE_OUT_CM, r[6] - ny * LANE_OUT_CM)
        if nearest((r[5], r[6]))[0] < 600.0:
            continue  # a street at its door already
        if rows and math.dist(rows[-1][-1], at) < 2.5 * HOME_LOT[0]:
            rows[-1].append(at)
        else:
            rows.append([at])
    for row in rows:
        (da, qa), (db, qb) = nearest(row[0]), nearest(row[-1])
        if min(da, db) < LANE_REACH_CM:
            row = [qa] + row if da <= db else row + [qb]
        if len(row) > 1:
            lanes.append([[round(v) for v in p] for p in row])
    return lanes


FRONT_LOT_D_CM = 3000.0  # a lot along a street with open land behind it: 16 m of frontage, 30 m deep
FRONT_END_CM = 1400.0  # ... none this close to the street's end at a junction (the corner stays clear)


def frontage_lots(city: dict, o: dict, nodes: dict, edges: list, fronted: set, wet=None, clear: list | None = None) -> list:
    """Lots along every side of a street that no block lies on. Only land ringed by streets is a block, so the
    land behind a city's outermost streets, between two streets that never meet again and on both sides of a dead
    end had no lot at all: a street ran through bare ground with a lamp every 30 m (in the owner's Valley a
    quarter of each city, Nov 12 2026). Each such side now gets a row of lots facing it, as city_layout's:
    inside the city's edge, off the water, off every other street, a shipyard, an airport and a keep zone
    (`clear`), and never over a lot of the row round the corner. fronted: (node, next node) of every street
    side a block lies on (its land is on the left going from the one to the other)."""
    from . import airport, parcels, shipyard
    yards = (city.get("shipyards") or []) + (city.get("shipyards_near") or [])
    fields = city.get("airports") or []
    near_cut = _near_cuts(city) if city.get("fw_near") else None
    cell = 6000.0
    road: dict = {}
    for k, e in enumerate(edges):
        line = [nodes[e[0]][:2]] + [v[:2] for v in (e[3] if len(e) > 3 and e[3] else [])] + [nodes[e[1]][:2]]
        for p, q in zip(line, line[1:]):
            n = max(1, int(math.dist(p, q) // (cell / 2)))
            for i in range(n + 1):
                key = (int((p[0] + (q[0] - p[0]) * i / n) // cell), int((p[1] + (q[1] - p[1]) * i / n) // cell))
                road.setdefault(key, set()).add((k, tuple(p), tuple(q), HALF.get(e[2], 500.0)))

    def on_road(x, y, own) -> bool:
        gx, gy = int(x // cell), int(y // cell)
        return any(k != own and _dist_seg((x, y), p, q) < half + 0.5 * parcels.SIDEWALK_CM
                   for i in (-1, 0, 1) for j in (-1, 0, 1) for k, p, q, half in road.get((gx + i, gy + j), ()))
    deg: dict = {}
    for e in edges:
        deg[e[0]] = deg.get(e[0], 0) + 1
        deg[e[1]] = deg.get(e[1], 0) + 1
    made: dict = {}  # the rows laid so far, by 60 m cell: (x, y, ux, uy)
    W, D = LOT_W_CM, FRONT_LOT_D_CM
    out = []
    for k, e in enumerate(edges):
        if (len(e) > 3 and e[3]) or e[2] == "DT":
            continue  # a bridge, a dirt track
        for a, b in ((e[0], e[1]), (e[1], e[0])):
            if (a, b) in fronted:
                continue
            p, q = nodes[a], nodes[b]
            L = math.dist(p[:2], q[:2])
            if in_keep(clear, p[0], p[1], 2000) or in_keep(clear, q[0], q[1], 2000):
                continue  # (the street itself is not laid there)
            m0, m1 = (FRONT_END_CM if deg[a] > 1 else 300.0), (FRONT_END_CM if deg[b] > 1 else 300.0)
            n = int((L - m0 - m1) // W)
            if n < 1:
                continue
            ux, uy = (q[0] - p[0]) / L, (q[1] - p[1]) / L
            nx, ny = -uy, ux  # the open land: on the left
            back = HALF.get(e[2], 500.0) + parcels.SIDEWALK_CM
            yaw = round(math.degrees(math.atan2(-ny, -nx)), 1)  # +X faces the street
            for i in range(n):
                t = m0 + (L - m0 - m1 - n * W) / 2 + (i + 0.5) * W
                fx, fy = p[0] + ux * t + nx * back, p[1] + uy * t + ny * back
                cx, cy = fx + nx * D / 2, fy + ny * D / 2
                spots = [(cx, cy)] + [(fx + ux * s * (W / 2 - 50) + nx * d, fy + uy * s * (W / 2 - 50) + ny * d)
                                      for s in (1, -1) for d in (50.0, D)]
                if zone_share(city, cx, cy) > 1.0 or (city.get("yield_to") and yielded(city, cx, cy)):
                    continue
                if (wet and any(wet(x, y) for x, y in spots)) or in_keep(clear, cx, cy, 2500):
                    continue
                if any(shipyard.reserved(y_, cx, cy, 1500) if y_.get("z") is not None else shipyard.inside(y_, cx, cy, -2000)
                       for y_ in yards) or any(airport.inside(f, cx, cy, 3000) for f in fields):
                    continue
                if any(on_road(x, y, k) for x, y in spots) or (near_cut and near_cut((cx, cy), (cx, cy), D / 2)):
                    continue
                gx, gy = int(cx // cell), int(cy // cell)
                if any(abs((cx - x) * vx + (cy - y) * vy) < (W if abs(ux * vx + uy * vy) > 0.7 else (W + D) / 2) - 60 and
                       abs(-(cx - x) * vy + (cy - y) * vx) < (D if abs(ux * vx + uy * vy) > 0.7 else (W + D) / 2) - 60
                       for i_ in (-1, 0, 1) for j_ in (-1, 0, 1) for x, y, vx, vy in made.get((gx + i_, gy + j_), ())):
                    continue  # over a lot already laid (the row across a narrow strip, or round the corner)
                made.setdefault((gx, gy), []).append((cx, cy, ux, uy))
                out.append([round(cx), round(cy), yaw, round(W), round(D), round(fx), round(fy), "", False])
    return out


def _inside(poly: list, x: float, y: float) -> bool:
    """Point in polygon (even-odd ray cast)."""
    hit = False
    for (ax, ay), (bx, by) in zip(poly, list(poly[1:]) + list(poly[:1])):
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            hit = not hit
    return hit


GROVE_CM = 700.0  # a park block: a tree about every 7 m
COURT_CM = 1000.0  # the planted middle of a block of plain lots: one about every 10 m
COURT_MIN = 4  # ... when there is room for this many


def _court(area: list, lots: list, rnd) -> list:
    """Trees on the land a block's lots leave free ([[x, y]], world cm): a jittered grid inside the block, none
    on a lot or within 3 m of one."""
    rects = [(r[0], r[1], math.cos(math.radians(r[2])), math.sin(math.radians(r[2])), r[4] / 2 + 300, r[3] / 2 + 300) for r in lots]
    xs, ys = [p[0] for p in area], [p[1] for p in area]
    out = []
    y = min(ys) + COURT_CM / 2
    while y < max(ys):
        x = min(xs) + COURT_CM / 2
        while x < max(xs):
            px, py = x + rnd.uniform(-0.35, 0.35) * COURT_CM, y + rnd.uniform(-0.35, 0.35) * COURT_CM
            if _inside(area, px, py) and min(_dist_seg((px, py), a, b) for a, b in zip(area, area[1:] + area[:1])) > 300 and \
                    not any(abs((px - cx) * fx + (py - cy) * fy) < hd and abs(-(px - cx) * fy + (py - cy) * fx) < hw
                            for cx, cy, fx, fy, hd, hw in rects):
                out.append([round(px), round(py)])
            x += COURT_CM
        y += COURT_CM
    return out


def _green_lots(add: dict, share: float, rnd) -> dict:
    """A block's parts with `share` of its lots planted instead of built on: those lots leave 'lots' and their
    ground is filled with trees ('trees'). Only lots a layer would fill (never a footprint a complex planned).
    The lots are taken in one seeded order, so a bigger share keeps every lot a smaller one already planted."""
    free = [k for k, r in enumerate(add["lots"]) if not (len(r) > 8 and r[8] is True)]
    rnd.shuffle(free)
    gone = set(free[:round(share * len(free))])
    if not gone:
        return add
    trees = list(add.get("trees") or [])
    for k in sorted(gone):
        x, y, yaw, w, d = add["lots"][k][:5]
        fx, fy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))  # +X faces the street
        nu, nv = max(1, round((d - 300) / GROVE_CM)), max(1, round((w - 300) / GROVE_CM))
        for i in range(nu):
            for j in range(nv):
                u = ((i + 0.5) / nu - 0.5) * (d - 300) + rnd.uniform(-0.3, 0.3) * GROVE_CM
                v = ((j + 0.5) / nv - 0.5) * (w - 300) + rnd.uniform(-0.3, 0.3) * GROVE_CM
                u, v = max(-d / 2 + 120, min(d / 2 - 120, u)), max(-w / 2 + 120, min(w / 2 - 120, v))
                trees.append([round(x + fx * u - fy * v), round(y + fy * u + fx * v)])
    return {**add, "lots": [r for k, r in enumerate(add["lots"]) if k not in gone], "trees": trees}


def _grove(area: list, rnd) -> list:
    """Trees all over a block ([[x, y]], world cm): a jittered grid inside its polygon."""
    xs, ys = [p[0] for p in area], [p[1] for p in area]
    out = []
    y = min(ys) + GROVE_CM / 2
    while y < max(ys):
        x = min(xs) + GROVE_CM / 2
        while x < max(xs):
            px, py = x + rnd.uniform(-0.35, 0.35) * GROVE_CM, y + rnd.uniform(-0.35, 0.35) * GROVE_CM
            if _inside(area, px, py):
                out.append([round(px), round(py)])
            x += GROVE_CM
        y += GROVE_CM
    return out


PARK_PATH_CM = 300.0  # a park path is this wide ...
PARK_PIECE_CM = 600.0  # ... and laid in pieces this long, each on its own ground
PARK_BENCH_CM = 3000.0  # a bench beside a path about this often


def park_paths(to_world, yaw: float, half_l: float, half_w: float, boxes: list, rnd, inside=None) -> tuple:
    """The paths of a park and the benches along them, in the park's own frame (u along its length, v across;
    to_world(u, v) -> (x, y); yaw: the way u points). A walk round it, one down its length and one across about
    every 140 m, in pieces PARK_PIECE_CM long; none over what stands in it (`boxes` [(u0, u1, v0, v1)]) or
    outside it (`inside(x, y)`). Returns (ground rows [x, y, yaw, sx_m, sy_m, kind, 'park'], the path lines
    [((u, v), (u, v))])."""
    inset = max(600.0, min(1500.0, 0.08 * 2 * min(half_l, half_w)))
    a, b = half_l - inset, half_w - inset
    if a < 1200 or b < 1200:
        return [], []
    v0 = rnd.uniform(-0.25, 0.25) * b
    lines = [((-a, -b), (a, -b)), ((a, -b), (a, b)), ((a, b), (-a, b)), ((-a, b), (-a, -b)), ((-a, v0), (a, v0))]
    n_cross = max(1, int(2 * a // 14000))
    for k in range(n_cross):
        u0 = -a + (k + 0.5 + rnd.uniform(-0.2, 0.2)) * 2 * a / n_cross
        lines.append(((u0, -b), (u0, b)))
    rows = []
    for p, q in lines:
        length = math.dist(p, q)
        n = max(1, math.ceil(length / PARK_PIECE_CM))
        du, dv = (q[0] - p[0]) / length, (q[1] - p[1]) / length
        ang = (yaw + math.degrees(math.atan2(dv, du))) % 360
        since = PARK_BENCH_CM * rnd.random()
        for k in range(n):
            u, v = p[0] + (q[0] - p[0]) * (k + 0.5) / n, p[1] + (q[1] - p[1]) * (k + 0.5) / n
            since += length / n
            if any(u0 - 200 < u < u1 + 200 and w0 - 200 < v < w1 + 200 for u0, u1, w0, w1 in boxes):
                continue
            x, y = to_world(u, v)
            if inside and not inside(x, y):
                continue
            rows.append([round(x), round(y), round(ang, 1), round(length / n / 100 + 0.1, 2), PARK_PATH_CM / 100, "path", "park"])
            if since >= PARK_BENCH_CM:  # a bench at the path's edge, facing it
                side = rnd.choice((1, -1))
                bu, bv = u - dv * side * (PARK_PATH_CM / 2 + 80), v + du * side * (PARK_PATH_CM / 2 + 80)
                bx, by = to_world(bu, bv)
                if not any(u0 < bu < u1 and w0 < bv < w1 for u0, u1, w0, w1 in boxes) and not (inside and not inside(bx, by)):
                    rows.append([round(bx), round(by), round((ang + (180 if side > 0 else 0)) % 360, 1), 1.0, 1.0, "bench", "park"])
                    since = 0.0
    return rows, lines


def _park(area: list, rnd) -> dict:
    """What a park block holds ([[x, y]], world cm): trees in groves with open lawns between them and a row of
    trees round its edge (never the even grid of a plantation), paths and benches (park_paths) and, by its size,
    playgrounds, a pond and playing fields. {'trees': [[x, y]], 'grounds': [[x, y, yaw, sx_m, sy_m, kind,
    'park']]}: the grounds are laid like a planned site's (pcg_layers.GROUNDS)."""
    from . import parcels
    ox, oy, ux, uy, L, W = parcels._obb(area)
    to_world = lambda u, v: (ox + ux * u - uy * v, oy + uy * u + ux * v)
    yaw = math.degrees(math.atan2(uy, ux))
    ha = abs(parcels._area(area)) / 1e8
    boxes, grounds = [], []

    def put(kind, su, sv):
        """A rectangle su x sv somewhere in the park, clear of what is there already."""
        if L < su + 3000 or W < sv + 3000:
            return
        for _ in range(24):
            u, v = rnd.uniform(-L / 2 + su / 2 + 1500, L / 2 - su / 2 - 1500), rnd.uniform(-W / 2 + sv / 2 + 1500, W / 2 - sv / 2 - 1500)
            box = (u - su / 2, u + su / 2, v - sv / 2, v + sv / 2)
            if any(box[0] < b[1] + 800 and b[0] < box[1] + 800 and box[2] < b[3] + 800 and b[2] < box[3] + 800 for b in boxes):
                continue
            if not all(_inside(area, *to_world(p, q)) for p in (box[0], box[1]) for q in (box[2], box[3])):
                continue
            boxes.append(box)
            grounds.append([*map(round, to_world(u, v)), round(yaw, 1), round(su / 100, 2), round(sv / 100, 2), kind, "park"])
            return
    for _ in range(2 if ha >= 9 else 1 if ha >= 4 else 0):
        put("field", 10800.0, 7200.0)  # a full-size pitch (100 x 64 m) with its run-off (pcg_layers.fit_pitch)
    if ha >= 1.2:
        put("pond", min(15000.0, L * rnd.uniform(0.2, 0.3)), min(9000.0, W * rnd.uniform(0.25, 0.4)))
    for _ in range(1 if ha < 2 else 2 if ha < 6 else 3):
        put("playground", rnd.uniform(1600.0, 2400.0), rnd.uniform(1200.0, 1800.0))
    rows, lines = park_paths(to_world, yaw, L / 2, W / 2, boxes, rnd, lambda x, y: _inside(area, x, y))
    grounds += rows
    tau = 2 * math.pi
    waves = [(rnd.uniform(2500.0, 5500.0), rnd.uniform(0, tau), rnd.uniform(0, tau)) for _ in range(3)]
    inset = max(600.0, min(1500.0, 0.08 * min(L, W)))
    trees = []
    for x, y in _grove(area, rnd):
        u, v = (x - ox) * ux + (y - oy) * uy, -(x - ox) * uy + (y - oy) * ux
        if any(u0 - 300 < u < u1 + 300 and w0 - 300 < v < w1 + 300 for u0, u1, w0, w1 in boxes):
            continue
        if any(_dist_seg((u, v), p, q) < PARK_PATH_CM / 2 + 250 for p, q in lines):
            continue
        belt = abs(u) > L / 2 - inset or abs(v) > W / 2 - inset  # the trees round its edge, outside the walk
        if belt or sum(math.sin(x / lam + p) * math.sin(y / lam * 1.3 + q) for lam, p, q in waves) > 0.0:
            trees.append([x, y])
    return {"trees": trees, "grounds": grounds}


def gate(nodes: dict, city: dict, toward: tuple):
    """The city's street node furthest out in the direction of another city."""
    dx, dy = toward[0] - city["x"], toward[1] - city["y"]
    L = math.hypot(dx, dy) or 1.0
    streets = [n for n in nodes if isinstance(n[0], int)]  # never another highway's stub
    return max(streets, key=lambda n: ((nodes[n][0] - city["x"]) * dx + (nodes[n][1] - city["y"]) * dy) / L)


JOIN_MAX_CM = 60000.0  # cities whose edges are closer than this (600 m) are joined by streets
JOIN_ROADS = 5  # ... this many where they touch, one where they are at the limit: the closer, the more
JOIN_LONGEST_CM = 45000.0  # a joining street is never longer than the gap plus this
JOIN_GRADE = 0.08  # ... never steeper than this from one city's level to the other's
JOIN_OFF_IC_CM = 15000.0  # ... and never starts within 150 m of a freeway interchange


def joins(cities: list, graphs: list, opts: dict | None = None, wet=None, keep: list | None = None, ground=None) -> list:
    """Streets between cities that lie close together or touch: the closer two cities are, the more streets run
    from one to the other (JOIN_ROADS where they touch, one at JOIN_MAX_CM), spread across the side where they
    face each other. Each joins a street corner at the edge of one city to one of the other, straight. One is
    built only where a real street could be: never through or beside water, a keep zone, a freeway's ramps or
    an interchange (of any city), and never steeper than JOIN_GRADE from one city's level to the other's.
    Returns [(i, j, node of i, node of j, [mid x, mid y])]: each city lays its own half, from its corner to the
    middle (level_joins then makes the two halves one even street)."""
    o = options(opts)
    out = []
    edge_nodes = []
    for nodes, edges in graphs:
        used = {n for e in edges for n in e[:2]}
        edge_nodes.append([(n, nodes[n]) for n in nodes if n in used and isinstance(n, tuple) and len(n) == 2
                           and isinstance(n[0], int)])
    plans = [freeway_plan(c, o) for c in cities]
    half = HALF.get(CITY_STREET.get(o["neighbourhood_roads"], o["neighbourhood_roads"]), 875.0)

    def fits(p, q, L) -> bool:
        ux, uy = (q[0] - p[0]) / L, (q[1] - p[1]) / L
        steps = max(2, int(L // 500))
        for t in range(steps + 1):
            x, y = p[0] + (q[0] - p[0]) * t / steps, p[1] + (q[1] - p[1]) * t / steps
            # the street, its sidewalks and the houses along it: dry 30 m to either side as well
            if wet and (wet(x, y) or wet(x - uy * 3000, y + ux * 3000) or wet(x + uy * 3000, y - ux * 3000)):
                return False
            if t % 4 == 0 and in_keep(keep, x, y, 2000):
                return False
        for fw in plans:
            if not fw.lines:
                continue
            if fw.street_blocked(p[:2], q[:2], half):
                return False  # a ramp or an at-grade freeway would cut it
            if any(min(math.dist(p[:2], ic["at"]), math.dist(q[:2], ic["at"])) < JOIN_OFF_IC_CM for ic in fw.interchanges):
                return False  # a corner inside an interchange is no place for one more street
        if ground:  # the two cities may stand on different levels: a street, not a ski jump
            za, zb = ground(p[0], p[1]), ground(q[0], q[1])
            if za is not None and zb is not None and abs(za - zb) / L > JOIN_GRADE:
                return False
        return True

    for i in range(len(cities)):
        for j in range(i + 1, len(cities)):
            a, b = cities[i], cities[j]
            dx, dy = b["x"] - a["x"], b["y"] - a["y"]
            d = math.hypot(dx, dy) or 1.0
            if d > (a["radius"] + b["radius"]) * 1.27 + JOIN_MAX_CM:
                continue
            ux, uy = dx / d, dy / d
            way = math.degrees(math.atan2(dy, dx))
            gap = d - a["radius"] * outline(a, way) - b["radius"] * outline(b, way + 180.0)
            if gap > JOIN_MAX_CM:
                continue
            n = 1 + round((JOIN_ROADS - 1) * (1.0 - max(0.0, gap) / JOIN_MAX_CM))
            wide = 1.2 * min(a["radius"], b["radius"])  # the side where they face each other
            slot = wide / n
            # every corner as (how far along the line from a to b, how far to its side, node, spot)
            side = lambda p: (-(p[0] - a["x"]) * uy + (p[1] - a["y"]) * ux)
            along = lambda p: ((p[0] - a["x"]) * ux + (p[1] - a["y"]) * uy)
            mine = [(along(p), side(p), n_, p) for n_, p in edge_nodes[i]]
            theirs = [(along(p), side(p), n_, p) for n_, p in edge_nodes[j]]
            taken: set = set()
            for k in range(n):
                at = wide * ((k + 0.5) / n - 0.5)
                near_a = sorted((r for r in mine if abs(r[1] - at) <= slot / 2), key=lambda r: -r[0])[:5]
                near_b = sorted((r for r in theirs if abs(r[1] - at) <= slot / 2), key=lambda r: r[0])[:5]
                pairs = sorted(((math.dist(ra[3][:2], rb[3][:2]), ra, rb) for ra in near_a for rb in near_b
                                if ra[2] not in taken and rb[2] not in taken and rb[0] > ra[0]), key=lambda r: r[0])
                for L, ra, rb in pairs:  # the shortest pair of corners a street can really run between
                    if not 1500.0 <= L <= max(0.0, gap) + JOIN_LONGEST_CM or not fits(ra[3], rb[3], L):
                        continue
                    p, q = ra[3], rb[3]
                    taken.update((ra[2], rb[2]))
                    out.append((i, j, ra[2], rb[2], [round((p[0] + q[0]) / 2), round((p[1] + q[1]) / 2)]))
                    break
    return out


def meet_city(road: dict, city: dict | None, ends: list) -> int:
    """A road from outside (an airport's access and cargo roads) ends on its city's junction at that junction's
    height. It was graded on the raw ground, the city on its own cut, so Northgate's airport road stopped 24 m above
    the junction it was laid to (Nov 17 2026). Returns how many ends were brought down (or up) to the city."""
    done = 0
    for x, y in ends:
        cp = next((p for p in (city or {}).get("points", ()) if abs(p["x"] - x) < 2 and abs(p["y"] - y) < 2), None)
        rp = next((p for p in road.get("points", ()) if abs(p["x"] - x) < 2 and abs(p["y"] - y) < 2), None)
        if cp and rp and rp["z"] != cp["z"]:
            rp["z"] = cp["z"]
            done += 1
    return done


def level_joins(actors: dict, cities: list, rows: list) -> int:
    """The two halves of a joining street meet at ONE height. Each city's roads are graded on their own, and two
    cities on different levels (each on its own flattened ground, say) left a step in the middle of every joining
    street, up to 24 m (Nov 12 2026). The street now runs at one even grade from one city's corner to the other's:
    both halves end at the height that grade gives the middle. Returns how many streets were levelled."""
    done = 0
    for i, j, _ni, _nj, mid in rows:
        halves = []
        for c in (cities[i], cities[j]):
            a = actors.get(f"{c['name']} Roads")
            end = next((p for p in (a or {}).get("points", ()) if abs(p["x"] - mid[0]) < 2 and abs(p["y"] - mid[1]) < 2), None)
            segs = [s for s in a["segments"] if end["i"] in (s["a"], s["b"])] if end else []
            if len(segs) != 1:
                break  # (not laid after all: the water took it)
            by = {p["i"]: p for p in a["points"]}
            far = by[segs[0]["b"] if segs[0]["a"] == end["i"] else segs[0]["a"]]
            halves.append((by, end, far, segs[0], math.hypot(far["x"] - end["x"], far["y"] - end["y"])))
        if len(halves) != 2:
            continue
        (by_a, end_a, far_a, seg_a, len_a), (by_b, end_b, far_b, seg_b, len_b) = halves
        end_a["z"] = end_b["z"] = far_a["z"] + (far_b["z"] - far_a["z"]) * len_a / ((len_a + len_b) or 1.0)
        st.retangent(by_a, seg_a)
        st.retangent(by_b, seg_b)
        done += 1
    return done


def share_freeways(cities: list, opts: dict | None = None) -> None:
    """Each city learns where its neighbours' freeways, ramps and frontage roads run low over its land (sets
    city['fw_near']: [[x, y, half width]] every few metres along them, within the city's reach): its own streets
    give way to those as they do to its own freeway, so no street is laid through a neighbour's ramp."""
    for c in cities:
        c.pop("fw_near", None)
    for c in cities:
        low = freeway_plan(c, opts).low_points()
        for u in cities:
            if u is c or not low:
                continue
            reach = u["radius"] * 1.3 + 30000
            rows = [[x, y, h] for x, y, h in low if math.hypot(x - u["x"], y - u["y"]) < reach]
            if rows:
                u.setdefault("fw_near", []).extend(rows)


def _near_cuts(city: dict):
    """(p, q, half) -> True when a neighbour's low freeway part (city['fw_near']) runs over or into the street."""
    cells: dict = {}
    for x, y, h in city.get("fw_near") or ():
        cells.setdefault((int(x // 5000), int(y // 5000)), []).append((x, y, h))

    def cut(p, q, half: float) -> bool:
        L = math.dist(p[:2], q[:2])
        n = max(1, int(L // 1000))
        for k in range(n + 1):
            x, y = p[0] + (q[0] - p[0]) * k / n, p[1] + (q[1] - p[1]) * k / n
            for gx in (int(x // 5000) - 1, int(x // 5000), int(x // 5000) + 1):
                for gy in (int(y // 5000) - 1, int(y // 5000), int(y // 5000) + 1):
                    for px, py, h in cells.get((gx, gy), ()):
                        if math.hypot(px - x, py - y) < half + 200 + h:
                            return True
        return False
    return cut


JOIN_LOT_D_CM = 3000.0  # a house lot along a joining street: 16 m of frontage, 30 m deep


def join_lots(city: dict, cities: list, opts: dict | None = None, wet=None, keep: list | None = None) -> tuple:
    """The open land between two cities that lie close fills in: house lots along both sides of each of this
    city's joining streets (city['joins']: [[x0, y0, x1, y1]] from its corner to the middle), never inside a
    city's own edge (its blocks are there), in water or in a keep zone. Returns (lots, corridors): lots as
    city_layout makes them, and each street's corridor as a polygon the layers treat as housing."""
    o = options(opts)
    half = HALF.get(CITY_STREET.get(o["neighbourhood_roads"], o["neighbourhood_roads"]), 875.0)
    lots, corridors = [], []
    for x0, y0, x1, y1 in city.get("joins") or []:
        L = math.hypot(x1 - x0, y1 - y0)
        if L < LOT_W_CM * 2:
            continue
        ux, uy = (x1 - x0) / L, (y1 - y0) / L
        reach = half + 50 + JOIN_LOT_D_CM + 300
        corridors.append([[round(x0 + ux * t - uy * s * reach), round(y0 + uy * t + ux * s * reach)]
                          for t, s in ((0, 1), (L, 1), (L, -1), (0, -1))])
        n = int((L - 400) // LOT_W_CM)
        for k in range(n):
            t = 200 + (L - 400 - n * LOT_W_CM) / 2 + (k + 0.5) * LOT_W_CM
            for s in (1, -1):
                fx, fy = x0 + ux * t - uy * s * (half + 50), y0 + uy * t + ux * s * (half + 50)
                cx, cy = fx - uy * s * JOIN_LOT_D_CM / 2, fy + ux * s * JOIN_LOT_D_CM / 2
                if any(zone_share(c, cx, cy) <= 1.03 for c in cities) or (wet and wet(cx, cy)) or in_keep(keep, cx, cy, 2000):
                    continue
                yaw = math.degrees(math.atan2(-ux * s, uy * s))  # +X faces the street
                lots.append([round(cx), round(cy), round(yaw, 1), round(LOT_W_CM), round(JOIN_LOT_D_CM), round(fx), round(fy), "", False])
    return lots, corridors


def shore_plan(cities: list, opts: dict | None = None, wet=None) -> None:
    """Where each city's freeways must end: at the shore when only open water lies beyond, and before a
    neighbouring city's land (sets city['fw_shore'], see freeways.shore_reach). Before the streets: the freeway
    plan and everything laid round it follow."""
    from . import freeways
    for c in cities:
        others = [u for u in cities if u is not c and math.hypot(u["x"] - c["x"], u["y"] - c["y"]) < (u["radius"] + c["radius"]) * 1.3 + 400000]
        taken = (lambda x, y, c=c, others=others: zone_share(c, x, y) > 1.0 and any(zone_share(u, x, y) <= 1.02 for u in others))             if others else None
        got = freeways.shore_reach(c, city_options(c, opts), wet, taken) if wet or taken else None
        if got:
            c["fw_shore"] = got
        else:
            c.pop("fw_shore", None)


PORT_ROAD = "4L"  # the road to a port: four lanes, for its trucks
PORT_CELL_CM = 6000.0  # ... found on a grid this coarse
PORT_REACH_CM = 600000.0  # ... to a street or a highway no further than this from its gate
PORT_GRADE = 0.08  # ... keeping to ground no steeper than this where it can (steeper costs it dearly)


def overland(start, goals: list, ok, ground, cell: float = PORT_CELL_CM, reach: float = PORT_REACH_CM):
    """The way over open land from `start` (x, y) to the nearest of `goals` [(x, y, tag)]: the cheapest path on a
    grid, where a step costs its length, more on steep ground, and only land `ok(x, y)` is stepped on (a goal's own
    cell always is). Returns ([(x, y), ...] from start to the goal, both in, the goal's tag) or None."""
    import heapq
    key = lambda x, y: (int(x // cell), int(y // cell))
    at = {}
    for g in goals:
        if math.dist(g[:2], start) <= reach:
            at.setdefault(key(g[0], g[1]), g)
    if not at:
        return None
    s = key(*start)
    mid = lambda k: ((k[0] + 0.5) * cell, (k[1] + 0.5) * cell)
    height: dict = {}

    def z(k):
        if k not in height:
            height[k] = ground(*mid(k)) or 0.0
        return height[k]
    lim = int(reach // cell)
    best, prev, heap = {s: 0.0}, {}, [(0.0, s)]
    while heap:
        d, k = heapq.heappop(heap)
        if d > best.get(k, math.inf):
            continue
        if k in at:
            path = [tuple(at[k][:2])]
            while k != s:
                k = prev[k]
                path.append(mid(k))
            path[-1] = tuple(start)
            return path[::-1], at[key(*path[0])][2]
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                n = (k[0] + dx, k[1] + dy)
                if n == k or abs(n[0] - s[0]) > lim or abs(n[1] - s[1]) > lim or (n not in at and not ok(*mid(n))):
                    continue
                step = cell * math.hypot(dx, dy)
                nd = d + step * (1.0 + (abs(z(n) - z(k)) / step / PORT_GRADE) ** 2)
                if nd < best.get(n, math.inf):
                    best[n], prev[n] = nd, k
                    heapq.heappush(heap, (nd, n))
    return None


def port_roads(cities: list, graphs: list, hw_nodes: dict, hw_edges: list, ground, wet=None, keep: list | None = None,
               airports: list | None = None) -> list:
    """A road to every port. A settled shipyard is a walled platform at the water with one gate, and no street came
    near it (a city's streets keep 15 m off a yard): nothing could drive in. Each yard now gets a PORT_ROAD from
    just inside its gate, out through it, and overland (`overland`: off the water, off every yard and airfield,
    easy on the slopes) to the nearest street crossing of any city, or the nearest plain stretch of a highway,
    which gets a junction there. The road is the city's own street (or part of the highways) from then on.
    Sets city['port_roads'] ([[x, y], ...] each) and returns them all."""
    from . import airport, shipyard
    yards = [(ci, y) for ci, c in enumerate(cities) for y in c.get("shipyards") or [] if y.get("z") is not None]
    for c in cities:
        c.pop("port_roads", None)
    if not yards:
        return []
    every = [y for _ci, y in yards]

    def ok(x, y):
        if wet and (wet(x, y) or any(wet(x + a, y + b) for a, b in ((4000, 0), (-4000, 0), (0, 4000), (0, -4000)))):
            return False
        return not (any(shipyard.reserved(s, x, y, 3000) for s in every)
                    or any(airport.inside(a, x, y, 6000) for a in airports or [])
                    or in_keep(keep, x, y, 6000))
    goals = []
    for ci, (nodes, _edges) in enumerate(graphs):
        used = {n for e in _edges for n in e[:2]}
        goals += [(p[0], p[1], ("street", ci, n)) for n, p in nodes.items()
                  if n in used and not any(shipyard.reserved(s, p[0], p[1], 1000) for s in every)]
    for ei, e in enumerate(hw_edges):
        if len(e) > 3 and e[2] in HIGHWAYS:
            goals += [(v[0], v[1], ("highway", ei, k)) for k, v in enumerate(e[3])
                      if (len(v) < 3 or not v[2]) and (len(v) < 4 or v[3] in (None, e[2]))]
    made = []
    for yi, (ci, y) in enumerate(yards):
        f, L, D = y["frame"], y["L"], y["D"]
        w = lambda u, v: (f["x"] + f["ux"] * u + f["vx"] * v, f["y"] + f["uy"] * u + f["vy"] * v)
        for key in ("gate_u", "gate_side", "gate_v"):
            y.pop(key, None)
        got, bank = None, float(y.get("bank") or 0.0)
        # its gate: the back corner where shipyard.build puts it; with water or no way out behind that, further
        # along its back edge; with water all along its back, in one of its sides (a yard on a spit had the lake
        # behind its gate and got no road at all)
        ways = [(None, u) for u in (L / 2 - 9950, L / 4, 0.0, -L / 4, -(L / 2 - 9950))] + \
               [(s, v) for v in (D * 0.75, D * 0.5, D * 0.25) for s in (1, -1)]
        def straight(out_, reach=30000.0, dry=True):
            """The nearest street crossing within `reach` that a straight road reaches clear of every yard, and
            (dry) over dry land: (path, tag). A yard squeezed between the water and a street has no room for the
            grid's 60 m cells."""
            for g in sorted((g for g in goals if g[2][0] == "street" and math.dist(g[:2], out_) < reach), key=lambda g: math.dist(g[:2], out_)):
                n = max(1, int(math.dist(g[:2], out_) // 800))
                line = [(out_[0] + (g[0] - out_[0]) * i / n, out_[1] + (g[1] - out_[1]) * i / n) for i in range(n + 1)]
                if not any((dry and wet and wet(*p)) or any(shipyard.reserved(s, p[0], p[1], 300) for s in every) for p in line[1:]):
                    return [tuple(out_), tuple(g[:2])], g[2]
            return None
        for k, (side, at) in enumerate(ways):
            out_ = w(at, D + bank + 3500) if side is None else w(side * (L / 2 + bank + 3500), at)
            if wet and wet(*out_):
                continue
            got = straight(out_) or overland(out_, goals, ok, ground)
            if got:
                if side is not None:
                    y["gate_side"], y["gate_v"] = side, round(at + 950)  # (the gatehouse beside the road)
                elif k:
                    y["gate_u"] = round(at + 950)
                break
        if not got:
            # an island of made ground with water all round it: from its gate straight to the nearest street,
            # across the water (that stretch is bridged with the city's other crossings, over_water)
            k, side, at = 0, None, L / 2 - 9950
            out_ = w(at, D + bank + 3500)
            got = straight(out_, 60000.0, dry=False)
        if not got:
            continue
        if side is None:
            inside_, gate_ = w(at, D - (2500 if k == 0 else 700)), w(at, D)
        else:
            inside_, gate_ = w(side * (L / 2 - 700), at), w(side * L / 2, at)
        path, tag = got
        # eased: the grid's 45-degree steps become one line (a point kept every other cell, each the mean of three)
        mids = [((a[0] + b[0] + c_[0]) / 3, (a[1] + b[1] + c_[1]) / 3) for a, b, c_ in zip(path, path[1:], path[2:])][::2]
        via = [list(gate_), list(out_)] + [[round(x), round(y_)] for x, y_ in mids if math.dist((x, y_), path[-1]) > 4000
                                           and math.dist((x, y_), out_) > 4000]
        a = ("port", ci, yi)
        if tag[0] == "street":
            nodes, edges = graphs[tag[1]]
            nodes[a] = [round(inside_[0]), round(inside_[1])]
            edges.append([a, tag[2], PORT_ROAD, via])
            end = nodes[tag[2]]
        else:
            e = hw_edges[tag[1]]
            j = ("port", ci, yi, "hw")
            hw_nodes[a], hw_nodes[j] = [round(inside_[0]), round(inside_[1])], [e[3][tag[2]][0], e[3][tag[2]][1]]
            hw_edges[tag[1]] = [e[0], j, e[2], e[3][:tag[2]]]
            hw_edges.append([j, e[1], e[2], e[3][tag[2] + 1:]])
            hw_edges.append([a, j, PORT_ROAD, via])
            end = hw_nodes[j]
            # (the other goals on that highway edge: their places in it have moved)
            goals = [g for g in goals if not (g[2][0] == "highway" and g[2][1] == tag[1])]
        line = [[round(p[0]), round(p[1])] for p in [inside_] + via + [end]]
        cities[ci].setdefault("port_roads", []).append(line)
        made.append(line)
    return made


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
ROAD_OFFSET_CM = 6.0  # a road segment's mesh is drawn this far up its points (junction decks: road_io._OWN_DECK_Z)
# (10 left every deck ~12 cm over the graded ground with its kerb wall showing; 0 let bumps poke through)


def _point(i, x, y, z, yaw, w, mesh="", lifted=False, piece=""):
    """A control point. lifted: on a bridge / ramp (never shapes the ground). piece: a mesh path that sits
    on the point (a trumpet loop, a cloverleaf)."""
    live = "False" if lifted else "True"
    lines = [f"Width={w:.1f}", f"bRaiseTerrain={live}", f"bLowerTerrain={live}", "SideFalloff=800.000000",
             "EndFalloff=1000.000000"] + ([] if mesh else [f"SegmentMeshOffset={ROAD_OFFSET_CM:.1f}"])
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
    # a one-point bridge between two junctions a few metres apart (over_water's 'ramps meet mid-span') stood on the
    # highest ground within 15 m, 7 m over both its ends: a 55 % hump. It rises HUMP_GRADE from its lower end, no more
    for chain in chains:
        for a, m, b in zip(chain, chain[1:], chain[2:]):
            pa, pm, pb = by_i[a], by_i[m], by_i[b]
            if not pm["raise"] and pa["raise"] and pb["raise"] and \
                    max(math.hypot(q["x"] - pm["x"], q["y"] - pm["y"]) for q in (pa, pb)) < 3000:
                cap = min(q["z"] + HUMP_GRADE * math.hypot(q["x"] - pm["x"], q["y"] - pm["y"]) for q in (pa, pb))
                if pm["z"] > cap:
                    pm["z"] = cap
                    for s in segs:
                        if m in (s["a"], s["b"]):
                            st.retangent(by_i, s)
    return {"points": pts, "segments": segs}


HUMP_GRADE = 0.12  # a one-point bridge's rise from its ends


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
    gates = o["gate_interchanges"] == "yes"
    tips = []  # per city: its freeways' open ends [x, y, the way it was heading there (unit), in the water]
    for c in cities:
        runs = [ln["points"] for ln in freeway_plan(c, o).lines if ln["cls"] == "freeway" and len(ln["points"]) > 1]
        every_tip = [(p, q) for ps in runs for p, q in ((ps[0], ps[1]), (ps[-1], ps[-2]))]
        mine = []
        for p, q in every_tip:
            L = math.dist(p[:2], q[:2]) or 1.0
            if sum(1 for u, _v in every_tip if math.dist(u[:2], p[:2]) < 200) == 1 and not in_keep(keep, p[0], p[1], 6000):
                hx, hy = (p[0] - q[0]) / L, (p[1] - q[1]) / L
                # (in the water, or ended at a shore with the water right ahead: no highway carries on from it)
                mine.append([p[0], p[1], hx, hy, bool(wet and any(wet(p[0] + hx * d, p[1] + hy * d) for d in (0.0, 10000.0, 20000.0, 30000.0)))])
        tips.append(mine)
    # cities that lie close together are joined street to street, the closer the more; each lays its own half
    joined: set = set()
    for c in cities:
        c.pop("joins", None)
    join_kind = CITY_STREET.get(o["neighbourhood_roads"], o["neighbourhood_roads"])
    join_rows = joins(cities, graphs, o, wet, keep, ground)
    for k, (i, j, ni, nj, mid) in enumerate(join_rows):
        joined.add((i, j))
        for me, other, node in ((i, j, ni), (j, i, nj)):
            nodes, edges = graphs[me]
            end = ("join", other, k)
            nodes[end] = list(mid)
            edges.append([node, end, join_kind])
            cities[me].setdefault("joins", []).append([round(nodes[node][0]), round(nodes[node][1]), mid[0], mid[1]])
    for a, b in links(cities):
        if (min(a, b), max(a, b)) in joined:
            continue  # one town to the eye: its streets join them, no highway runs between
        ends, leads = [], []
        for me, other in ((a, b), (b, a)):
            cx, cy, ox, oy = cities[me]["x"], cities[me]["y"], cities[other]["x"], cities[other]["y"]
            D = math.hypot(ox - cx, oy - cy) or 1.0
            # (an end on the city's far side would send the highway back across the city: only ends within about
            # 100 degrees of the way to the other city)
            toward = [] if gates else [t for t in tips[me] if not t[4] and (t[0] - cx) * (ox - cx) + (t[1] - cy) * (oy - cy)
                                       > -0.2 * D * math.hypot(t[0] - cx, t[1] - cy)]
            if toward:  # the highway IS the city's freeway from here on: it starts where that ends, heading the same way
                tip = min(toward, key=lambda t: math.hypot(t[0] - ox, t[1] - oy))
                tips[me].remove(tip)
                ends.append([tip[0], tip[1]])
                leads.append(tip)
                continue
            leads.append(None)
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
        for k, tip in enumerate(leads):  # the freeway's own heading carries on 200 m, then swings to the other END
            leads[k] = []  # (not the other city's centre: its gate may lie 700 m to one side, and the road doubled back)
            if tip:
                tx, ty = ends[1 - k][0] - tip[0], ends[1 - k][1] - tip[1]
                tl = math.hypot(tx, ty) or 1.0
                cos = (tip[2] * tx + tip[3] * ty) / tl
                if cos > -0.3:
                    leads[k] = [[tip[0] + tip[2] * 20000, tip[1] + tip[3] * 20000]]
                    if cos < 0.8:  # (halfway between its heading and the way on from that first point: two even bends)
                        tx, ty = ends[1 - k][0] - leads[k][0][0], ends[1 - k][1] - leads[k][0][1]
                        tl = math.hypot(tx, ty) or 1.0
                        bx, by = tip[2] + tx / tl, tip[3] + ty / tl
                        bl = math.hypot(bx, by) or 1.0
                        leads[k].append([leads[k][0][0] + bx / bl * 30000, leads[k][0][1] + by / bl * 30000])
        via = [v for v in via if all(math.dist(v[:2], e) > 60000 for e, ld in zip(ends, leads) if ld)]
        via = leads[0] + via + leads[1][::-1]
        for field in airports or []:  # ... and round an airport, never through its terminal and car park
            from . import airport
            via = [v for v in via if not airport.inside(field, v[0], v[1], 10000)]
            line, went = [ends[0]] + via + [ends[1]], []
            for p, q in zip(line, line[1:]):
                went += airport.around(field, p, q) + [q]
            via = [[v[0], v[1]] for v in went[:-1]]
        if o["overpasses"] == "yes":
            via, _ = overpass_via([ends[0]] + via + [ends[1]], streets, o["highway"])
        if gates and o["interchange"] == "stack" and L > 70000:  # rise onto an elevated deck into each gate
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
        if gates and o["interchange"] in ("cloverleaf", "trumpet", "diamond") and L > 90000:
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
    # every port gets its road: from a street or a highway, overland, through its gate and onto its platform
    port_roads(cities, graphs, hw_nodes, hw_edges, ground, wet, keep, airports)
    for nodes, edges in graphs:  # a city street never runs into a keep zone
        gone = {n for n, p in nodes.items() if in_keep(keep, p[0], p[1], 2000)}
        edges[:] = [e for e in edges if e[0] not in gone and e[1] not in gone]
    if wet or any(canal_lines(c, o) for c in cities):  # cross roads added after city_graph bridge or stop too
        graphs = [over_water(n, e, water_of(c, o, wet), bridges=bridges, stubs=False) for c, (n, e) in zip(cities, graphs)]
    # every city one network, whatever above parted it: a district left on its own is joined to the rest
    graphs = [join_parts(n, e, water_of(c, o, wet), o["core_roads"], bridges, land=o["neighbourhood_roads"])
              for c, (n, e) in zip(cities, graphs)]
    out = {f"{c['name']} Roads": build_actor(n, e, ground, folder) for c, (n, e) in zip(cities, graphs)}
    level_joins(out, cities, join_rows)  # each city graded its half on its own: the two meet at one height
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
            meet_city(out[f"{label} Roads"], out.get(f"{ap['city']} Roads"), [l["to_city_node"] for l in got["links"]])
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
                # (12 m under the yard's platform; a yard that was never settled: under the ground as it lies)
                level = lambda x, y, yards=c["shipyards"]: next(
                    (s["z"] for s in yards if s.get("z") is not None and shipyard.inside(s, x, y, 4000)), ground(x, y))
                out[f"{c['name']} Docks"] = build_actor(dn, de, lambda x, y: level(x, y) - shipyard.DOCK_DEPTH_CM, folder)
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
            out[f"{c['name']} Freeways"] = model_pieces(build_actor(fn, fe, ground, folder), fw.interchanges, ground, folder)
    if wet and hw_edges:  # a highway crosses any water on a bridge (a long one if it has to)
        long = {k: 1e9 for k in (*HIGHWAYS, "2L", "ER", "ED", "BR", "BRR")}
        hw_nodes, hw_edges = over_water(hw_nodes, hw_edges, wet, long, bridges)
    if hw_edges:
        out["Highways"] = build_actor(hw_nodes, hw_edges, ground, folder, pieces)
    edge = {"box": cities[0].get("map") if cities else None, "ground": ground, "wet": wet}
    for _ in range(8):  # again while anything moved: an end may land on a road whose own end just moved
        if not weld_ends(out, edge):
            break
    for _ in range(3):  # the welds' height changes, spread along the whole ramp (through its forks too) ...
        for label, a in out.items():
            if label.endswith(("Freeways", "Highways")):
                settle_grades(a)
        if not weld_ends(out, edge):  # ... and any end whose road that moved, put back on it
            break
    for _ in range(4):  # (a ramp cut back in the last of those passes still has its new end to land)
        if not weld_ends(out, edge):
            break
    for label, a in out.items():  # a last piece under half a metre long (an end welded onto the point before it) goes
        if label.endswith(("Freeways", "Highways")):
            by = {p["i"]: p for p in a["points"]}
            deg: dict = {}
            for s in a["segments"]:
                for i in (s["a"], s["b"]):
                    deg[i] = deg.get(i, 0) + 1
            for s in list(a["segments"]):
                pa, pb = by.get(s["a"]), by.get(s["b"])
                tip = next((q for q in (pa, pb) if q and deg.get(q["i"]) == 1 and not q["mesh"]), None)
                if pa and pb and tip and math.dist((pa["x"], pa["y"], pa["z"]), (pb["x"], pb["y"], pb["z"])) < 50:
                    a["segments"].remove(s)
                    a["points"].remove(tip)
    # a point only where a road turns, meets another or carries a piece: straight from corner to corner (a point
    # per 20 m, each on its own bump, made every road wave); then every welded end put back on the road it reaches
    for label in list(out):
        pts, segs = st.straighten(out[label]["points"], out[label]["segments"])
        out[label] = {**out[label], "points": pts, "segments": segs}
    for _ in range(8):
        if not weld_ends(out, edge):
            break
    return out


INTERCHANGE_FOLDER = "Interchanges"  # under the roads folder: the IC_* models (assets/interchanges)


def model_pieces(a: dict, ics: list, ground, folder: str) -> dict:
    """Each Blender interchange model (freeways.place_model) as a piece on its own point, and every road end on
    one of its sockets held there (marked 'IC_Socket'): weld_ends never runs a freeway on into the model."""
    socks = [s for ic in ics if ic.get("model") for s in ic["model"]["sockets"]]
    for p in a["points"]:
        if not p["mesh"] and any(math.hypot(p["x"] - x, p["y"] - y) < 100 for x, y in socks):
            p["mesh"] = "IC_Socket"
    i = max([p["i"] for p in a["points"]] + [s["i"] for s in a["segments"]] + [0]) + 1
    for ic in ics:
        m = ic.get("model")
        if m:
            x, y = ic["at"]
            a["points"].append(_point(i, x, y, ground(x, y), m["yaw"], m["width"], lifted=True,
                                      piece=f"{folder}/{INTERCHANGE_FOLDER}/{m['mesh']}"))
            i += 1
    return a


WELD_REACH_CM = 2600.0  # an open end this close (in plan) to another road is meant to join it
WELD_GRADE = 0.08  # the end's own road eases to the new height at most this steeply
WELD_SNAP_CM = 6000.0  # an end that stops short of every road by less than this is brought up to the nearest one
FREEWAY_LINK_CM = 15000.0  # a freeway that ends this close to a road runs on to it (an urban freeway ending in a field)
FREEWAY_OUT_CM = 400000.0  # a freeway end no highway took runs on to the map's edge when that is this close
FREEWAY_REACH_CM = 150000.0  # ... or runs on up to 1.5 km to the nearest road ahead of it
FREEWAY_MESHES = {ROAD_MESH[k].split("/")[-1] for k in ("6L", "ED")}
RAMP_MESHES = {ROAD_MESH[k].split("/")[-1] for k in ("ER", "FR")}  # their ends get a connector piece
# a connector's half width at the ramp's end (road_profiles.connector: the gore widens to 10 m, the flare to 10.5 m)
CONNECTOR_HALF = {"SM_Road_RampGore30m": 1000.0, "SM_Road_RampFlare15m": 1050.0}
FLARE_CM = 1500.0  # the flare runs this far on past the ramp's end, to the street's kerb line


DECK_DIP_CM = 1000.0  # how far a freeway deck may come down to meet ramps too short to climb to it
SETTLE_GRADE = 0.15  # steeper than anything planned (an overpass's approach climbs at up to ~13 %): only faults


def settle_grades(a: dict, grade: float = SETTLE_GRADE, rounds: int = 400) -> int:
    """No piece of a freeway or ramp steeper than `grade`: wherever one is, its two ends move toward each other's
    height, again and again, so a step is shared out along the road and on through its forks (weld_ends eased an
    end's road only as far as the first fork: a ramp 30 m under the deck it joins climbed it in its last 10 m,
    Nov 12 2026). Welded ends (they carry their connector), open ends and junction mouths stay where they are;
    between two of those too close for the grade, the slope comes out even. Returns the points moved."""
    by = {p["i"]: p for p in a["points"]}
    segs = [s for s in a["segments"] if s["a"] in by and s["b"] in by]
    count: dict = {}
    fixed = {p["i"] for p in a["points"] if p.get("mesh")}
    for s in segs:
        for i in (s["a"], s["b"]):
            count[i] = count.get(i, 0) + 1
            if not s["mesh"]:
                fixed.add(i)
    fixed |= {i for i, n in count.items() if n == 1}
    # A ramp's merge end rides with the freeway deck beside it instead of being nailed to it: where the ramp is too
    # short for the climb (a viaduct 30 m over the street its ramps leave from, 150 m of ramp: 21 %), the deck
    # itself comes down toward the interchange, by at most DECK_DIP_CM and never steeper than it was laid or 6 %.
    decks = [s for s in segs if s["mesh"].split("/")[-1] in FREEWAY_MESHES]
    ties = []
    for p in a["points"]:
        if p.get("mesh") != "SM_Road_RampGore30m":
            continue
        best = None
        for s in decks:
            u, v = by[s["a"]], by[s["b"]]
            wx, wy = v["x"] - u["x"], v["y"] - u["y"]
            t = max(0.0, min(1.0, ((p["x"] - u["x"]) * wx + (p["y"] - u["y"]) * wy) / (wx * wx + wy * wy or 1.0)))
            d = math.hypot(p["x"] - u["x"] - wx * t, p["y"] - u["y"] - wy * t)
            if d < 3500 and (best is None or d < best[0]):
                best = (d, u["i"], v["i"], t)
        if best:
            ties.append((p["i"], *best[1:]))
            fixed.discard(p["i"])
    floor = {i: by[i]["z"] - DECK_DIP_CM for s in decks for i in (s["a"], s["b"])}
    runs = []
    for s in segs:
        if s["mesh"]:
            run = math.hypot(by[s["b"]]["x"] - by[s["a"]]["x"], by[s["b"]]["y"] - by[s["a"]]["y"])
            lim = max(abs(by[s["b"]]["z"] - by[s["a"]]["z"]), 0.06 * run) if s in decks else grade * run
            runs.append((s["a"], s["b"], min(lim, grade * run)))
    moved: set = set()
    for _ in range(rounds):
        worst = 0.0
        for i, ui, vi, t in ties:
            diff = by[i]["z"] - (by[ui]["z"] * (1 - t) + by[vi]["z"] * t)
            if abs(diff) < 1:
                continue
            wu, wv = (0.0 if ui in fixed else 1 - t), (0.0 if vi in fixed else t)
            lam = diff / (1 + wu * wu + wv * wv)
            by[i]["z"] -= lam
            by[ui]["z"] = max(floor[ui], by[ui]["z"] + lam * wu)
            by[vi]["z"] = max(floor[vi], by[vi]["z"] + lam * wv)
            moved.update((i, ui, vi))
        for i, j, lim in runs:
            dz = by[j]["z"] - by[i]["z"]
            over = abs(dz) - lim
            free = (i not in fixed) + (j not in fixed)
            if over <= 0 or not free:
                continue
            worst = max(worst, over)
            step = math.copysign(over / free, dz)
            if i not in fixed:
                by[i]["z"] += step
                moved.add(i)
            if j not in fixed:
                by[j]["z"] -= step
                moved.add(j)
        if worst < 2:
            break
    for s in segs:
        if s["a"] in moved or s["b"] in moved:
            st.retangent(by, s)
    return len(moved)


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


def weld_ends(actors: dict, edge: dict | None = None) -> int:
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
    # the roads near each end only (every road of the world for every open end was 5 s a rebuild): by 200 m cell
    cell, reach = 20000.0, max(WELD_REACH_CM, WELD_SNAP_CM, FREEWAY_REACH_CM)
    by_cell: dict = {}
    for k, (_lab, u, v, _mesh) in enumerate(every):
        for cx in range(int(min(u["x"], v["x"]) // cell), int(max(u["x"], v["x"]) // cell) + 1):
            for cy in range(int(min(u["y"], v["y"]) // cell), int(max(u["y"], v["y"]) // cell) + 1):
                by_cell.setdefault((cx, cy), []).append(k)

    def roads_near(p):  # in the order they were listed: the first of two equal finds still wins
        ks = {k for cx in range(int((p["x"] - reach) // cell), int((p["x"] + reach) // cell) + 1)
              for cy in range(int((p["y"] - reach) // cell), int((p["y"] + reach) // cell) + 1)
              for k in by_cell.get((cx, cy), ())}
        return [every[k] for k in sorted(ks)]
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
            o0 = by[ss[0]["b"] if ss[0]["a"] == i else ss[0]["a"]]
            best, near, link = None, None, None  # best: in reach, nearest the end's level; near / link: closest beyond
            for lab2, u, v, mesh in roads_near(p):
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
                # (another actor's road only: past own_run's 80 m a freeway's own deck passed as the road to link
                # to, and it was folded back over itself for 20 m. Beyond FREEWAY_LINK_CM only a road ahead of it,
                # within 60 degrees of the way it was going)
                elif d <= FREEWAY_REACH_CM and lab2 != label and (link is None or d < link[0]) and (
                        d <= FREEWAY_LINK_CM or (nx - p["x"]) * (p["x"] - o0["x"]) + (ny - p["y"]) * (p["y"] - o0["y"])
                        > 0.5 * d * math.hypot(p["x"] - o0["x"], p["y"] - o0["y"])):
                    link = hit
            hit = best or near
            if not hit:
                if link and ss[0]["mesh"].split("/")[-1] in FREEWAY_MESHES:
                    # a freeway that stops short in the fields runs on to the road it meets, a piece every 20 m,
                    # coming down evenly to that road's height (the next weld puts its new end inside that road)
                    d, nx, ny, level, half, *_r = link
                    n = max(1, round((d - 2000) / 2000))
                    nid = 1 + max([q["i"] for q in a["points"]] + [s["i"] for s in a["segments"]])
                    prev = i
                    for k in range(1, n + 1):
                        f = k / n * (d - 2000) / d
                        q = {**p, "i": nid, "x": p["x"] + (nx - p["x"]) * f, "y": p["y"] + (ny - p["y"]) * f,
                             "z": p["z"] + (level - p["z"]) * k / n, "lines": list(p.get("lines") or [])}
                        s = {**ss[0], "i": nid + 1, "a": prev, "b": nid, "lines": list(ss[0].get("lines") or [])}
                        a["points"].append(q)
                        a["segments"].append(s)
                        by[nid] = q
                        st.retangent(by, s)
                        prev, nid = nid, nid + 2
                    moved += 1
                elif edge and edge.get("box") and ss[0]["mesh"].split("/")[-1] in FREEWAY_MESHES:
                    # no road to reach at all: the freeway runs straight on out of the world, as far as the map's
                    # edge, at the height over the ground it ended at (over dry land only: no bridge is laid here)
                    box, gnd, wet = edge["box"], edge["ground"], edge.get("wet")
                    L0 = math.hypot(p["x"] - o0["x"], p["y"] - o0["y"]) or 1.0
                    ex, ey = (p["x"] - o0["x"]) / L0, (p["y"] - o0["y"]) / L0
                    d = min((box[2] - p["x"]) / ex if ex > 1e-6 else (box[0] - p["x"]) / ex if ex < -1e-6 else 1e18,
                            (box[3] - p["y"]) / ey if ey > 1e-6 else (box[1] - p["y"]) / ey if ey < -1e-6 else 1e18)
                    n = max(1, round(d / 2000))
                    line = [(p["x"] + ex * d * k / n, p["y"] + ey * d * k / n) for k in range(1, n + 1)]
                    if 2000 < d <= FREEWAY_OUT_CM and not (wet and any(wet(x, y) for x, y in line)):
                        up = p["z"] - (gnd(p["x"], p["y"]) or 0.0)
                        nid = 1 + max([q["i"] for q in a["points"]] + [s["i"] for s in a["segments"]])
                        prev = i
                        for x, y in line:
                            q = {**p, "i": nid, "x": x, "y": y, "z": (gnd(x, y) or 0.0) + up, "lines": list(p.get("lines") or [])}
                            s = {**ss[0], "i": nid + 1, "a": prev, "b": nid, "lines": list(ss[0].get("lines") or [])}
                            a["points"].append(q)
                            a["segments"].append(s)
                            by[nid] = q
                            st.retangent(by, s)
                            prev, nid = nid, nid + 2
                        moved += 1
                continue
            d, nx, ny, level, half, onto, ux, uy = hit
            if ss[0]["mesh"].split("/")[-1] in RAMP_MESHES and onto not in FREEWAY_MESHES | STRUCTURES:
                # (a street only: beside a freeway the ramp's last pieces are its merge lane)
                # a ramp that comes in at a shallow angle ran on INSIDE the road it joins for its last pieces (a slip
                # ramp along a frontage road, 100 m of deck lying in the street): while the point before the end is
                # in that road too, the end goes; the ramp stops where it first touches
                def inside(o):
                    for lab2, u, v, mesh in every:
                        if mesh != onto or (lab2 == label and u["i"] in mine and v["i"] in mine):
                            continue
                        wx, wy = v["x"] - u["x"], v["y"] - u["y"]
                        t = max(0.0, min(1.0, ((o["x"] - u["x"]) * wx + (o["y"] - u["y"]) * wy) / (wx * wx + wy * wy or 1.0)))
                        # (half its width in the road, up to a storey above or below: a piece brushing the kerb is
                        # its merge)
                        if math.hypot(o["x"] - u["x"] - wx * t, o["y"] - u["y"] - wy * t) < half + own * 0.5 and \
                                abs(u["z"] + (v["z"] - u["z"]) * t - o["z"]) < 600:
                            return True
                    return False
                end, seg, gone = p, ss[0], 0
                while gone < 40:  # (all of them in this pass: the next welds the new end)
                    o = by[seg["b"] if seg["a"] == end["i"] else seg["a"]]
                    if len(touch.get(o["i"], ())) != 2 or o["mesh"] or not inside(o) or \
                            seg not in a["segments"] or end not in a["points"]:  # (gone already, from its other end)
                        break
                    a["points"].remove(end)
                    a["segments"].remove(seg)
                    gone += 1
                    end, seg = o, next(s for s in touch[o["i"]] if s is not seg)
                if gone:
                    moved += 1
                    continue
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


def parcels_area(poly: list) -> float:
    from . import parcels
    return parcels._area(poly)


def _demo() -> None:
    cities = place_cities(3, 3, ((-200000, -200000), (200000, 200000)))
    assert len(cities) == 3 and all(c["style"] in STYLES for c in cities)
    ter = terrain(3, 400000, 6000, cities, grid=64)
    assert ter["heights"] == terrain(3, 400000, 6000, [], grid=64)["heights"], "natural ground ignores the cities"
    flat = terrain(3, 400000, 6000, cities, grid=64, opts={"city_ground": "flatten"})
    # islands: sea at the map's edge, every city on dry land, 'many' leaves water between cities
    for mode in ("one", "many"):
        isl = terrain(3, 400000, 6000, cities, grid=64, opts={"island": mode})
        wet_i = wet_from(isl)
        assert isl["sea"] == 0.0 and all(wet_i(x, y) for x, y in ((-195000, -195000), (195000, 195000))), mode
        assert not any(wet_i(c["x"] + c["radius"] * math.cos(a), c["y"] + c["radius"] * math.sin(a))
                       for c in cities for a in (k * math.pi / 4 for k in range(8))), (mode, "cities on land")
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
    # a dead end's turnaround on a hillside: the ground is cut level across its whole round, not just the road's width
    slope = {"grid": 40, "min": [-10000, -10000], "max": [10000, 10000], "heights": [[i * 50.0 for i in range(40)] for _ in range(40)]}
    road = ROAD_MESH["2S"].split("/")[-1]
    hill = {"T Roads": {"points": [{"i": 0, "x": -8000, "y": 0, "z": 0, "yaw": 0, "pitch": 0, "w": 875, "mesh": ""},
                                   {"i": 1, "x": -1200, "y": 0, "z": 0, "yaw": 180, "pitch": 0, "w": 875, "mesh": ""},
                                   {"i": 2, "x": 0, "y": 0, "z": 0, "yaw": 0, "pitch": 0, "w": 1425, "mesh": "SM_JK_2"}],
                        "segments": [{"i": 0, "a": 0, "b": 1, "mesh": road, "ta": 6800, "tb": -6800},
                                     {"i": 1, "a": 2, "b": 1, "mesh": "", "ta": 1200, "tb": -1200}]}}
    for x in hill["T Roads"]["points"] + hill["T Roads"]["segments"]:
        x["lines"] = []
    hg = grade_to_roads(slope, hill)
    assert all(abs(st.sample_grid(hg, 1000 * math.cos(a), 1000 * math.sin(a)) + ROAD_SINK_CM) < 60
               for a in (0.0, 1.57, -1.57, 0.8)), "the turnaround's whole round is cut level"
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
    # planned grid: streets bow, they never jog. Along one street the heading changes a little from block to block
    # (always a spline bend, never an elbow), no block is squeezed thin, and collectors run every 4th line
    block = OPTIONS["block_m"][0] * 100
    ce = OPTIONS["collector_every"][0]
    main_bent: dict = {}
    core_bent: dict = {}
    for style in ("mixed", "organic"):
        city = {"x": 0, "y": 0, "radius": 60000, "seed": 11, "style": style, "turn": 0.0}
        gn, ge = city_graph(city, {"wobble": 9, "freeways": "no"})  # asked for more: clamped to 0.45
        bent = 0.0
        for a, b, _ in ge:
            L = math.dist(gn[a], gn[b])
            assert block * 0.5 <= L <= block * 1.5, (style, L)
        for (ix, iy), p in gn.items():
            for da in ((1, 0), (0, 1)):
                q, r = gn.get((ix + da[0], iy + da[1])), gn.get((ix + 2 * da[0], iy + 2 * da[1]))
                if q and r:
                    turn = abs((math.degrees(math.atan2(r[1] - q[1], r[0] - q[0]) - math.atan2(q[1] - p[1], q[0] - p[0])) + 180) % 360 - 180)
                    assert turn < CORNER_DEG, (style, "a street bends as a spline, never an elbow", turn)
                    bent = max(bent, turn)
                    if (iy if da[0] else ix) % ce == 0:
                        main_bent[style] = max(main_bent.get(style, 0.0), turn)
                        if max(abs(ix + da[0]), abs(iy + da[1])) <= 1:
                            core_bent[style] = max(core_bent.get(style, 0.0), turn)
        assert bent > 5, (style, "streets between the main roads curve", bent)
        assert {"4L"} <= {k for _, _, k in ge}, "collectors / arterials exist"
    # streets follow the hills. A town on a round hill 60 m high: every street across the slope bends to run
    # along the hillside (the ground along it rises and falls less than along the plain grid line), no street
    # turns into an elbow for it, and nothing moves on flat ground or in a Grid town
    dome = lambda x, y: 6000.0 * math.exp(-(x * x + y * y) / (2 * 30000.0 ** 2))
    quiet = {"wobble": 0, "freeways": "no", "prune": 0, "superblocks": "no", "follow_hills": 1}
    hill_town = {"x": 0, "y": 0, "radius": 60000, "seed": 11, "style": "organic", "turn": 0.0}
    plan_hills([hill_town], dome, quiet)
    assert hill_town.get("hills") and len(hill_town["hills"]["u"]) == (2 * hill_town["hills"]["k"][0] + 1) * (2 * hill_town["hills"]["k"][1] + 1)
    import json as _json
    assert _json.loads(_json.dumps(hill_town["hills"])) == hill_town["hills"], "kept as plain text with the city"
    hn, he = city_graph(hill_town, quiet)
    plain_town = {k: v for k, v in hill_town.items() if k != "hills"}
    pn, pe = city_graph(plain_town, quiet)
    swing = lambda nodes_, line: (lambda zs: max(zs) - min(zs))([dome(*nodes_[(ix, line)]) for ix in range(-4, 5) if (ix, line) in nodes_])
    for line in (3, -3, 4, -4, 5):
        assert swing(hn, line) < swing(pn, line) * 0.8, ("a street across the hill follows it", line, swing(hn, line), swing(pn, line))
    assert max(math.dist(hn[n], pn[n]) for n in hn if n in pn) <= HILL_MOVE * block * 1.45, "never more than a block and a half"
    assert all(math.dist(hn[n], pn[n]) > 500 for n in ((2, 3), (-3, 2))) and math.dist(hn[(0, 0)], pn[(0, 0)]) < 50.0
    for a_, b_, _k in he:  # ... and the net never tears: no block squeezed under half its size or pulled to double
        assert block * 0.5 <= math.dist(hn[a_], hn[b_]) <= block * 1.7, (a_, b_, math.dist(hn[a_], hn[b_]))
    for (ix, iy), p in hn.items():
        for da in ((1, 0), (0, 1)):
            q, r = hn.get((ix + da[0], iy + da[1])), hn.get((ix + 2 * da[0], iy + 2 * da[1]))
            if q and r:
                turn = abs((math.degrees(math.atan2(r[1] - q[1], r[0] - q[0]) - math.atan2(q[1] - p[1], q[0] - p[0])) + 180) % 360 - 180)
                assert turn < CORNER_DEG, ("the hills bend a street, they never break it", (ix, iy), turn)
    # beside a freeway nothing moves: what lands on one is laid out straight
    fw_town = dict(plain_town)
    plan_hills([fw_town], dome, {**quiet, "freeways": "yes"})
    assert hill_at(fw_town["hills"], 2, 3)[:2] == (0.0, 0.0) and hill_at(fw_town["hills"], 3, -3)[:2] == (0.0, 0.0)
    assert math.hypot(*hill_at(fw_town["hills"], -3, 4)[:2]) > 0.1
    flat_town = dict(plain_town)
    plan_hills([flat_town], lambda x, y: 100.0 + x * 0.005, quiet)
    assert "hills" not in flat_town, "half a percent of fall is flat ground"
    grid_town = dict(plain_town, style="grid")
    plan_hills([grid_town], dome, quiet)
    assert "hills" not in grid_town and city_graph(dict(hill_town, style="grid"), quiet)[0] == city_graph(grid_town, quiet)[0]
    assert city_graph(hill_town, {**quiet, "follow_hills": 0})[0] == pn, "follow_hills 0: the grid ignores the hills"
    # a street that climbs steep ground winds (its crossings swing to one side, then the other), and a local
    # street that would climb steeper than 12 % is left out: the blocks there lie along the hill
    slope_town = dict(plain_town)
    plan_hills([slope_town], lambda x, y: 0.10 * y, quiet)
    sn_, se_ = city_graph(slope_town, quiet)
    side = [sn_[(2, j)][0] - 2 * block for j in range(-3, 4)]
    assert all(a * b < 0 for a, b in zip(side, side[1:])) and 300 < abs(side[0]) < HILL_ZIG * block, side
    assert all(abs(sn_[(i, 2)][1] - 2 * block) < 1.0 for i in range(-3, 4)), "on an even slope a street across it is level already"
    steep_town = dict(plain_town)
    plan_hills([steep_town], lambda x, y: 0.16 * y, quiet)
    tn, te = city_graph(steep_town, quiet)
    up = [e for e in te if e[0][0] == e[1][0]]
    assert up and all(e[0][0] % ce == 0 for e in up), "only main roads climb a 16 % hillside"
    assert len([e for e in te if e[0][1] == e[1][1]]) == len([e for e in pe if e[0][1] == e[1][1]]), "every street along it stays"
    # a mixed city's main roads are dead straight downtown and wander past it; an organic city's bow all the way
    assert core_bent["mixed"] < 1e-6 and main_bent["mixed"] > 3 and main_bent["organic"] > 3, (core_bent, main_bent)
    # the whole street net drifts off the grid in long waves, main roads with it, as soon as streets may bow at
    # all: with a wobble of 4 % a main road already leaves its line by tens of metres (it bowed 3 m: every
    # fourth line was a ruled road, every four of them a square). Never in a grid city, never with wobble 0.
    drifting = {"x": 0, "y": 0, "radius": 120000, "seed": 11, "style": "organic", "turn": 0.0}
    dn, de = city_graph(drifting, {"wobble": 0.04, "freeways": "no", "prune": 0, "superblocks": "no"})
    off_line = [abs(dn[(ix, 8)][1] - 8 * block) for ix in range(-10, 11) if (ix, 8) in dn]  # (a main road of the suburbs)
    assert max(off_line) > 2000 and max(off_line) <= (MEANDER + 0.04 + EDGE_BOW) * block, ("a main road wanders", max(off_line))
    # ... and the nearer the city's edge the more its streets bow, the main roads too; not in the normal city
    calm = city_graph(drifting, {"wobble": 0.04, "freeways": "no", "prune": 0, "superblocks": "no"})[0]
    bend_at = lambda net, line, lo, hi: max(abs(net[(i + 1, line)][1] - 2 * net[(i, line)][1] + net[(i - 1, line)][1])
                                            for i in range(lo, hi) if all((i + d, line) in net for d in (-1, 0, 1)))
    assert bend_at(calm, 8, -4, 5) > 2 * bend_at(calm, 4, -2, 3), ("a main road of the suburbs bends more than one of the inner city",
                                                             bend_at(calm, 8, -4, 5), bend_at(calm, 4, -2, 3))
    assert all(math.dist(dn[(i, j)], (i * block, j * block)) < 0.05 * block for i in range(-2, 3) for j in range(-2, 3)
               if (i, j) in dn), "the centre stays straight (less the crossings the big park took)"
    assert all(block * 0.6 <= math.dist(dn[a], dn[b]) <= block * 1.4 for a, b, _k in de), "blocks keep their size"
    for (ix, iy), p in dn.items():
        for da in ((1, 0), (0, 1)):
            q, r = dn.get((ix + da[0], iy + da[1])), dn.get((ix + 2 * da[0], iy + 2 * da[1]))
            if q and r:
                turn = abs((math.degrees(math.atan2(r[1] - q[1], r[0] - q[0]) - math.atan2(q[1] - p[1], q[0] - p[0])) + 180) % 360 - 180)
                assert turn < 18, ("a road swings, it never kinks", (ix, iy), turn)
    assert meander(dict(drifting, style="grid"), options({"wobble": 0.3})) is None and meander(drifting, options({"wobble": 0})) is None
    mixed_n, _me = city_graph(dict(drifting, style="mixed"), {"wobble": 0.04, "freeways": "no", "prune": 0, "superblocks": "no"})
    assert all(math.dist(mixed_n[(i, j)], (i * block, j * block)) < 1.0 for i in range(-3, 4) for j in range(-3, 4)
               if (i, j) in mixed_n), "downtown stays a grid"
    assert max(math.dist(p, (n[0] * block, n[1] * block)) for n, p in mixed_n.items()) > 2000
    # past downtown many of the streets between two neighbouring blocks are not built. Against the same city
    # with every one of them built: every street is either built or listed as left out, a fair share is
    # left out, none of them downtown or on an axis, the network is in one piece and no new dead end was made
    gap_city = {"x": 0, "y": 0, "radius": 150000, "seed": 5, "style": "organic", "turn": 0.0}
    gap_o = {"wobble": 0, "freeways": "no", "prune": 0, "planner": "no"}
    left_out: list = []
    gn2, ge2 = city_graph(gap_city, gap_o, virtual=left_out)
    every_gap = dict(GAPS)
    GAPS.update({k: 0.0 for k in GAPS})
    all_n, all_e = city_graph(gap_city, gap_o)
    lay_all = city_layout(gap_city, gap_o)
    GAPS.update(every_gap)
    ruled: list = []  # a Grid city leaves streets out too, and keeps every street it builds on the grid's lines
    rn_, re_ = city_graph(dict(gap_city, style="grid"), {**gap_o, "wobble": 0.3}, virtual=ruled)
    assert len(ruled) > 40 and all(math.dist(p, (n[0] * block, n[1] * block)) < 1e-6 for n, p in rn_.items()), len(ruled)
    pair = lambda e: frozenset(e[:2])
    assert {pair(e) for e in ge2} | {pair(v) for v in left_out} == {pair(e) for e in all_e} and not {pair(e) for e in ge2} & {pair(v) for v in left_out}
    assert 0.08 * len(all_e) < len(left_out) < 0.4 * len(all_e), (len(left_out), len(all_e))
    go = city_options(gap_city, gap_o)
    for a_, b_, pa, pb in left_out:
        assert min(zone_share(gap_city, *pa), zone_share(gap_city, *pb)) > go["downtown"], "never downtown"
        assert not (a_[0] == 0 == b_[0] or a_[1] == 0 == b_[1]), "never one of the two axes"
    ends_of = lambda es: {n: sum(1 for e in es if n in e[:2]) for e in es for n in e[:2]}
    d_now, d_all = ends_of(ge2), ends_of(all_e)
    assert sum(1 for n, d in d_now.items() if d == 1) <= sum(1 for n, d in d_all.items() if d == 1), "no new dead end"
    reach_, walk_ = {(0, 0)}, [(0, 0)]
    while walk_:
        n_ = walk_.pop()
        for e in ge2:
            if n_ in e[:2]:
                m_ = e[1] if e[0] == n_ else e[0]
                if m_ not in reach_:
                    reach_.add(m_)
                    walk_.append(m_)
    assert reach_ == set(gn2), "one network"
    assert city_graph(gap_city, gap_o)[1] == ge2, "the same streets whether or not the caller asks which were left out"
    # ... and the land is still parted along them: as many blocks as the strict grid has, each laid out on its
    # own ground; no lot fronts a street that is not there, and no block is left without a street
    lay_gap = city_layout(gap_city, gap_o)
    assert len(lay_gap["blocks"]) == len(lay_all["blocks"]), (len(lay_gap["blocks"]), len(lay_all["blocks"]))
    built = [(gn2[e[0]][:2], gn2[e[1]][:2]) for e in ge2]
    for r in lay_gap["lots"]:
        if r[7] == "":
            assert min(_dist_seg((r[5], r[6]), p, q) for p, q in built) < 2500, ("a lot's front is on a street", r[:7])
    # no private street runs up to a line where no street was built and stops there: it meets the street of the
    # subdivision beside it, or ends in a turnaround short of it
    on_line = lambda p: any(_dist_seg(p, v[2], v[3]) < 5 for v in left_out)
    tips = [tuple(s[k][:2]) for s in lay_gap["streets"] for k in (0, -1)]
    assert not [p for p in tips if on_line(p)], [p for p in tips if on_line(p)][:3]
    joined_ = [s for s in lay_gap["streets"] if any(on_line(p) for p in s[1:-1])]
    assert joined_ or len(lay_gap["hoods"]) and lay_gap["hoods"].get("collector", 0) < 2, "two subdivisions side by side share a street"
    # a block of plain lots is planted where its lots leave it free (a court in its middle), never on a lot
    courts = 0
    for bx_, by_, kind_, _zone, poly_, _green in lay_gap["blocks"]:
        if kind_ != "parcels":
            continue
        mine = [r for r in lay_gap["lots"] if _inside(poly_, r[0], r[1])]
        for t in (t for t in lay_gap["trees"] if _inside(poly_, t[0], t[1])):
            courts += 1
            for r in mine:
                fx_, fy_ = math.cos(math.radians(r[2])), math.sin(math.radians(r[2]))
                assert abs((t[0] - r[0]) * fx_ + (t[1] - r[1]) * fy_) >= r[4] / 2 or abs(-(t[0] - r[0]) * fy_ + (t[1] - r[1]) * fx_) >= r[3] / 2, "a court tree on a lot"
    assert courts > 100, courts
    # every side of a street that no block lies on gets a row of lots: the land beyond a city's last streets is
    # built on. Each such lot is inside the city, off every street and off the lot beside it.
    polys = [b[4] for b in lay_gap["blocks"]]
    beyond = [r for r in lay_gap["lots"] if r[7] == "" and not any(_inside(p, r[0], r[1]) for p in polys)]
    assert len(beyond) > 60, len(beyond)
    for k_, r in enumerate(beyond):
        assert zone_share(gap_city, r[0], r[1]) <= 1.0, "inside the city's edge"
        assert min(_dist_seg((r[0], r[1]), p, q) for p, q in built) > 1500, ("off every street", r[:5])
        assert all(math.dist(r[:2], s[:2]) > 1400 for s in beyond[:k_]), ("not on the lot beside it", r[:5])
    assert not frontage_lots(gap_city, go, gn2, ge2, {(e[0], e[1]) for e in ge2} | {(e[1], e[0]) for e in ge2}), "none where blocks lie"
    assert not [r for r in frontage_lots(gap_city, go, gn2, ge2, set(), clear=[{"x0": -9e9, "x1": 9e9, "y0": -9e9, "y1": 9e9}])], "none in a keep zone"
    kept_clear = [{"name": "K", "x0": 20000.0, "x1": 50000.0, "y0": -15000.0, "y1": 15000.0, "z": 0.0}]
    lay_keep = city_layout(gap_city, gap_o, clear=kept_clear)
    assert not any(in_keep(kept_clear, r[0], r[1], 2000) for key in ("lots", "trees", "parking", "stalls", "pools") for r in lay_keep[key])
    assert not any(in_keep(kept_clear, p[0], p[1], 2000) for s in lay_keep["streets"] for p in s), "nothing planned in a keep zone"
    assert any(in_keep(kept_clear, r[0], r[1], 2000) for r in lay_gap["lots"]), "(lots were planned there)"
    beside = [b for b in lay_keep["blocks"] if any(in_keep(kept_clear, p[0], p[1], 2000) for p in b[4])]
    assert beside and all(b[2] in ("parcels", "park") for b in beside), "a block a keep zone reaches into is plain lots"
    # few bridges. Two banks of a lake, a street along each: of five long bridges side by side 150 m apart only
    # the shortest is built, a sixth two kilometres on is, the one right across them is not; every creek keeps
    # its own; three to one landing are one bridge
    deck = [[0, 0, 800, "BR"]]
    lake_n, lake_e, plans = {}, [], []
    spots = [k * 15000.0 for k in range(5)] + [200000.0]
    for k, x in enumerate(spots):
        lake_n[("a", k)], lake_n[("b", k)] = [x, 0.0], [x, 60000.0 + k * 1000.0]
        lake_e.append([("a", k), ("b", k), "6S", deck])
        plans.append({"x": x, "y": 30000.0 + k * 500.0, "yaw": 90.0})
        if k:
            lake_e += [[("a", k - 1), ("a", k), "2S"], [("b", k - 1), ("b", k), "2S"]]
    lake_n["x0"], lake_n["x1"] = [-30000.0, 20000.0], [150000.0, 21000.0]  # one right across the first five
    lake_e += [["x0", "x1", "6S", deck], ["x0", ("a", 0), "2S"], ["x1", ("b", 5), "2S"]]
    for s, y in (("a", -10000.0), ("b", 80000.0)):  # (each bank a town of its own: more streets behind the shore)
        lake_n.update({(s, "back", k): [k * 10000.0, y] for k in range(4)})
        lake_e += [[(s, 0), (s, "back", 0), "2S"]] + [[(s, "back", k), (s, "back", k + 1), "2S"] for k in range(3)]
    for k in range(3):  # three streets over a creek, 100 m apart
        lake_n[("c", k)], lake_n[("d", k)] = [300000.0 + k * 10000.0, 0.0], [300000.0 + k * 10000.0, 6000.0]
        lake_e.append([("c", k), ("d", k), "2S", deck])
    few_n, few_e = few_bridges(lake_n, lake_e, plans, 0)
    left = {(e[0], e[1]) for e in few_e if len(e) > 3}
    assert left == {(("a", 0), ("b", 0)), (("a", 5), ("b", 5)), (("c", 0), ("d", 0)), (("c", 1), ("d", 1)), (("c", 2), ("d", 2))}, left
    assert [p["x"] for p in plans] == [0.0, 200000.0] and len(few_e) == len(lake_e) - 5, (plans, len(few_e))
    fan_n = {"h": [0.0, 0.0], "l": [0.0, 90000.0], "m": [30000.0, 0.0], "r": [60000.0, 0.0]}
    fan_e = [[n, "l", "6S", deck] for n in ("h", "m", "r")] + [["h", "m", "2S"], ["m", "r", "2S"]]
    for s, y in (("h", -10000.0), ("l", 100000.0)):  # (a town behind each bank)
        fan_n.update({(s, k): [k * 10000.0, y] for k in range(8)})
        fan_e += [[s, (s, 0), "2S"]] + [[(s, k), (s, k + 1), "2S"] for k in range(7)]
    fan = few_bridges(fan_n, fan_e)[1]
    assert [e[0] for e in fan if len(e) > 3] == ["h"], "three bridges to one landing are one bridge"
    # a district on the far bank is joined: where the bridges to two districts would cross, the one that joins
    # more of the city is built (the shorter one, to the smaller district, would have cut the bigger one off)
    isle_n = {"c0": [0.0, 0.0], "c1": [10000.0, 0.0], "s": [40000.0, 50000.0], "t0": [-40000.0, 50000.0]}
    isle_n.update({("big", k): [-40000.0 - k * 5000.0, 50000.0] for k in range(1, 12)})
    isle_n.update({("town", k): [k * 5000.0, -5000.0] for k in range(10)})
    isle_n.update({("sm", k): [40000.0 + k * 5000.0, 50000.0] for k in range(1, 8)})
    isle_e = [["c0", "c1", "2S"], ["c1", "t0", "6S", deck], ["c0", "s", "6S", deck], ["t0", ("big", 1), "2S"], ["s", ("sm", 1), "2S"],
              ["c0", ("town", 0), "2S"]] + [[("big", k), ("big", k + 1), "2S"] for k in range(1, 11)] + \
             [[("town", k), ("town", k + 1), "2S"] for k in range(9)] + [[("sm", k), ("sm", k + 1), "2S"] for k in range(1, 7)]
    assert math.dist(isle_n["c0"], isle_n["s"]) < math.dist(isle_n["c1"], isle_n["t0"])
    isle = few_bridges(isle_n, isle_e)[1]
    assert [e[1] for e in isle if len(e) > 3] == ["t0"], "the bridge to the bigger district is the one built"
    lone_n = {**{("town", k): [k * 5000.0, 0.0] for k in range(10)}, "x": [0.0, 60000.0], "y": [5000.0, 60000.0]}
    lone_e = [[("town", k), ("town", k + 1), "2S"] for k in range(9)] + [[("town", 0), "x", "6S", deck], ["x", "y", "2S"]]
    assert not [e for e in few_bridges(lone_n, lone_e)[1] if len(e) > 3], "no long bridge to a corner of two junctions"
    # a district across the water that no bridge reaches gets one: straight to the nearest junction of the rest,
    # never across a street or a bridge that is there; a part of three junctions gets none
    strait = lambda x, y: 20000 < x < 50000
    bank_n = {("w", k): [0.0, k * 10000.0] for k in range(LOOSE_MIN)}
    bank_n.update({("e", k): [70000.0, k * 10000.0 + 2000.0] for k in range(LOOSE_MIN)})
    bank_e = [[(s, k), (s, k + 1), "2S"] for s in "we" for k in range(LOOSE_MIN - 1)]
    plans2: list = []
    jn, je = join_parts(bank_n, bank_e, strait, "6L", plans2)
    span = [e for e in je if len(e) > 3 and e[3]]
    assert len(span) == 1 and {span[0][0][0], span[0][1][0]} == {"w", "e"} and len(plans2) == 1, (span, plans2)
    assert abs(jn[span[0][0]][1] - jn[span[0][1]][1]) == 2000.0, "the two nearest junctions"
    assert len(join_parts(jn, je, strait, "6L")[1]) == len(je), "once joined, no second one"
    tiny = {**{("w", k): bank_n[("w", k)] for k in range(LOOSE_MIN)}, ("e", 0): [70000.0, 0.0], ("e", 1): [70000.0, 10000.0], ("e", 2): [80000.0, 0.0]}
    tiny_e = [e for e in bank_e if e[0][0] == "w"] + [[("e", 0), ("e", 1), "2S"], [("e", 0), ("e", 2), "2S"]]
    assert len(join_parts(tiny, tiny_e, strait, "6L")[1]) == len(tiny_e)
    # one network: a bridge with a bare end is not built, a few streets joined to nothing go, a district stays
    net_n = {k: [k * 1000.0, 0.0] for k in range(13)}
    net_n.update({"far": [19000.0, 0.0], "x": [0.0, 9000.0], "y": [1000.0, 9000.0]})
    net_n.update({("d", k): [k * 1000.0, 20000.0] for k in range(LOOSE_MIN)})
    net_e = [[k, k + 1, "2S"] for k in range(12)] + [[12, "far", "2S", [[16000, 0, 800, "BR"]]], ["x", "y", "2S"]] + \
            [[("d", k), ("d", k + 1), "2S"] for k in range(LOOSE_MIN - 1)]
    one_n, one_e = one_network(net_n, net_e)
    assert "far" not in one_n and "x" not in one_n and ("d", 0) in one_n and len(one_e) == 12 + LOOSE_MIN - 1, one_e
    joined = net_e + [["far", "x", "2S"], ["y", 0, "2S"]]  # the bridge now leads somewhere: everything stays
    assert len(one_network(net_n, joined)[1]) == len(joined)
    # ... but not beside a freeway: the streets on either side of it and the main roads that cross it stay put
    fw_city = {"x": 0, "y": 0, "radius": 120000, "seed": 11, "style": "organic", "turn": 0.0}
    fo = city_options(fw_city, {"wobble": 0.45})
    fat, _fk, _fj = street_grid(fw_city, fo)
    fline = round(fo["downtown"] * 120000 / block)
    for iy in range(-8, 9):
        for ix in (fline, fline + 1):
            assert abs(fat(ix, iy)[0] - ix * block) < 1e-6, "the two streets beside the freeway never move"
    for iy in (-8, 0, 4, 8):  # (main roads: every 4th line)
        for ix in (fline - 1, fline, fline + 1, fline + 2):
            assert abs(fat(ix, iy)[1] - iy * block) < 1e-6, "a main road crosses the freeway straight"
    far = [abs(fat(ix, 8)[1] - 8 * block) for ix in range(-9, fline - 4)]
    assert max(far) > 500, ("and bows five blocks from it", far)
    still, _ = city_graph({"x": 0, "y": 0, "radius": 60000, "seed": 11, "style": "organic", "turn": 0.0}, {"wobble": 0, "freeways": "no"})
    assert all(abs(p[0] - ix * block) < 1e-6 and abs(p[1] - iy * block) < 1e-6 for (ix, iy), p in still.items()), "wobble 0: an exact grid"
    # long blocks: block_ratio 2 makes every block twice as long as it is wide, the same streets otherwise
    longn, longe = city_graph({"x": 0, "y": 0, "radius": 60000, "seed": 11, "style": "grid", "turn": 0.0}, {"block_ratio": 2, "freeways": "no"})
    assert all(abs(p[0] - ix * block) < 1e-6 and abs(p[1] - iy * 2 * block) < 1e-6 for (ix, iy), p in longn.items())
    assert {round(math.dist(longn[a], longn[b])) for a, b, _ in longe} == {round(block), round(2 * block)}
    for ratio in (1.5, 2.5):  # freeways and their interchanges still land on the long grid's main roads
        fr = freeway_report({"name": "L", "x": 0, "y": 0, "radius": 160000, "seed": 4, "style": "mixed"}, {"block_ratio": ratio})
        assert fr["interchanges"] and fr["validation"]["ok"], (ratio, {k: v for k, v in fr["validation"].items() if v})
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
    stack = generate(far, lambda x, y: 0.0, "/Game/X/Roads", seed=3, opts={"interchange": "stack", "gate_interchanges": "yes"})["Highways"]
    assert {"SM_Elev_Deck20m", "SM_Elev_Ramp20m"} <= {s["mesh"] for s in stack["segments"]}
    assert "/Game/X/Roads/Elevated/SM_Elev_Deck20m" in st.serialize(stack["points"], stack["segments"])
    # real interchanges, built from our pieces: a bridge over a cross road and loop / slip ramps at each end
    made = {}
    for kind in ("cloverleaf", "trumpet", "diamond"):
        world = generate(far, lambda x, y: 0.0, "/Game/X/Roads", seed=3, opts={"interchange": kind, "highway": "6L", "gate_interchanges": "yes"})
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
    multi = generate(three, lambda x, y: 0.0, "/Game/X/Roads", seed=3, opts={"interchange": "cloverleaf", "highway": "6L", "gate_interchanges": "yes"})
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
    # two cities that overlap: the bigger keeps the land (none of the smaller one's streets inside it), the bigger
    # one's own streets are exactly what they were, and a city put somewhere by hand is never pushed away
    big_c = {"name": "Big", "x": 0, "y": 0, "radius": 90000, "seed": 3, "style": "grid"}
    small_c = {"name": "Small", "x": 120000, "y": 0, "radius": 60000, "seed": 5, "style": "grid"}
    alone, _e0 = city_graph(dict(small_c))
    pair = [dict(big_c), dict(small_c)]
    yield_plan(pair)
    assert "yield_to" not in pair[0] and [o["name"] for o in pair[1]["yield_to"]] == ["Big"]
    clipped, _e1 = city_graph(pair[1])
    inside_big = lambda p: zone_share(big_c, p[0], p[1]) <= 1.0
    assert any(inside_big(p) for p in alone.values()) and not any(inside_big(p) for p in clipped.values()), "no street twice"
    assert city_graph(pair[0])[0] == city_graph(dict(big_c))[0], "the bigger city is as it was"
    assert len(clipped) > 0.5 * len(alone)
    kept_put = spread([dict(big_c), {**small_c, "pinned": True}], 30000)
    assert [(t["x"], t["y"]) for t in kept_put] == [(0, 0), (120000, 0)], "a city put by hand stays, and pushes nobody"
    assert spread([dict(big_c), dict(small_c)], 30000)[1]["x"] > 120000
    # cities that lie close are joined street to street, the closer the more; far apart, not at all
    def joined_at(gap_cm):
        cs = [{"name": "A", "x": 0, "y": 0, "radius": 60000, "seed": 3, "style": "grid", "shape": [1.0] * SHAPE_POINTS},
              {"name": "B", "x": 120000 + gap_cm, "y": 0, "radius": 60000, "seed": 5, "style": "grid", "shape": [1.0] * SHAPE_POINTS}]
        gs = [city_graph(c, {"freeways": "no"}) for c in cs]
        return cs, gs, joins(cs, gs, {"freeways": "no"})
    cs, gs, close = joined_at(8000)
    _cs, _gs, mid_far = joined_at(40000)
    assert len(close) >= 3 and len(close) > len(mid_far) >= 1 and not joined_at(90000)[2], (len(close), len(mid_far))
    for i, j, ni, nj, mid in close:
        p, q = gs[i][0][ni], gs[j][0][nj]
        assert p[0] < mid[0] < q[0] and math.dist(p, q) < 8000 + JOIN_LONGEST_CM and abs(mid[1]) < 60000, (p, mid, q)
    assert len({(ni, nj) for _i, _j, ni, nj, _m in close}) == len(close), "each street joins its own two corners"
    assert not joins(cs, gs, {"freeways": "no"}, wet=lambda x, y: 60000 < x < 68000), "never through water"
    assert len(joins(cs, gs, {"freeways": "no"}, wet=lambda x, y: y > 20000)) < len(close), "nor along a shore"
    # a shipyard's platform cut into the terrain: level on it, 9 m of water along its quay (the ground ends 15 m
    # behind the quay line, under the paving), the hill behind and beside it cut back to a bank, the rest as it was
    yard = {"type": "repair", "frame": {"x": 0, "y": 0, "ux": 0.0, "uy": 1.0, "vx": -1.0, "vy": 0.0}, "L": 40000.0,
            "D": 20000, "z": 250.0, "docks": []}
    hillside = {"min": [-40000, -40000], "max": [40000, 40000], "grid": 80, "sea": 0.0,
                "heights": [[3000.0 if i < 40 else -200.0 for i in range(80)] for _ in range(80)]}
    town = [{"name": "Y", "shipyards": [yard]}]
    cut = cut_yards(hillside, town)["heights"]
    assert (cut[40][29], cut[40][38], cut[40][39], cut[40][40], cut[40][19], cut[40][0], cut[60][29]) == \
        (250.0, 250.0, -900.0, -900.0, 550.0, 3000.0, 550.0), (cut[40][29], cut[40][38], cut[40][39], cut[40][40], cut[40][19])
    assert cut[40][60] == -200.0 and cut[40][45] == -900.0, "shallow water past the basin stays"
    assert cut_yards(hillside, [{"name": "N", "shipyards": [{**yard, "z": None}]}]) is hillside and cut_yards(hillside, []) is hillside
    level = on_yards(town, lambda x, y: 3000.0)
    assert (level(-100, 19900), level(-19900, -19900), level(100, 0), level(-20100, 0), level(-100, 20100)) == \
        (250.0, 250.0, 3000.0, 3000.0, 3000.0) and on_yards([], level) is level
    # a made shore cut into the terrain. The water lies at x > 0 and the land stands 6 m over it: a quay gets its
    # level promenade with 3 m of water at the wall, a beach runs from half a metre under the water up to its
    # boardwalk, homes get a lawn up to their row, and behind each the land comes back as a bank
    from . import waterfront as _wf
    bluff = {"min": [-20000, -20000], "max": [20000, 20000], "grid": 80, "sea": 0.0,
             "heights": [[600.0 if i < 40 else -800.0 for i in range(80)] for _ in range(80)]}
    line = [[0, y, 1.0, 0.0] for y in range(-18000, 18001, 2000)]
    cell = lambda use, i: cut_shores(bluff, [{"name": "S", "shores": [{"use": use, "sea": 0.0, "bank": 1000.0, "pts": line}]}])["heights"][40][i]
    assert (cell("commercial", 39), cell("commercial", 31), cell("commercial", 40), cell("commercial", 44)) == (200.0, 200.0, -800.0, -800.0)
    assert cell("commercial", 29) == 200 + 0.5 * 250 and cell("commercial", 20) == 600.0, "the bank, then the land as it was"
    assert cut_shores({**bluff, "heights": [[600.0 if i < 40 else -100.0 for i in range(80)] for _ in range(80)]},
                      [{"name": "S", "shores": [{"use": "public", "sea": 0.0, "bank": 0.0, "pts": line}]}])["heights"][40][40] == -300.0
    assert (cell("beach", 41), cell("beach", 39), cell("beach", 33), cell("beach", 31)) == (-18.8, 6.2, 81.2, 200.0), cell("beach", 41)
    assert (cell("residential", 39), cell("residential", 30), cell("residential", 40)) == (147.5, 450.0, -800.0)
    assert cut_shores(bluff, []) is bluff and cut_shores(bluff, [{"name": "S"}]) is bluff
    # a stretch fades into the natural shore at its ends: nothing is made past them
    ends = cut_shores(bluff, [{"name": "S", "shores": [{"use": "commercial", "sea": 0.0, "bank": 0.0, "pts": line[6:13]}]}])["heights"]
    assert ends[40][35] == 200.0 and ends[5][35] == 600.0 and 200.0 < ends[32][35] < 600.0, (ends[40][35], ends[32][35], ends[5][35])
    # what is built there stands at the made level, and a street stays off where the land was cut down for it
    made_z = on_made([{"name": "S", "shores": [{"use": "commercial", "sea": 0.0, "bank": 1000.0, "pts": [p + [3000.0] for p in line]}]}],
                     lambda x, y: 600.0 if x < 0 else -800.0)
    assert made_z(-2500, 0) == 200.0 and made_z(-15000, 0) == 600.0 and on_made([], made_z) is made_z
    look = _wf.Made([{"shores": [{"use": "commercial", "sea": 0.0, "bank": 1000.0, "pts": [p + [3000.0] for p in line]}]}])
    assert look.no_street(-2500, 0) and not look.no_street(-3500, 0) and not look.no_street(-2500, 19500)
    assert look.shelf(-4900, 0)["use"] == "commercial" and look.shelf(-5200, 0) is None and look.shelf(100, 0) is None
    # ... and into the coarse terrain the roads are planned on: only cells wholly on the platform or in its basin
    coarse = {**hillside, "grid": 8, "heights": [[3000.0 if i < 4 else -200.0 for i in range(8)] for _ in range(8)]}
    rough = cut_yards(coarse, town, whole=True)["heights"]
    assert [rough[4][i] for i in range(8)] == [3000.0, 3000.0, 250.0, 250.0, -200.0, -200.0, -200.0, -200.0], rough[4]
    finer = cut_yards({**hillside, "grid": 16, "heights": [[3000.0 if i < 8 else -200.0 for i in range(16)] for _ in range(16)]},
                      town, whole=True)["heights"]
    assert (finer[8][7], finer[8][8], finer[8][9]) == (250.0, -900.0, -200.0), "the basin: 90 m out from the quay"
    assert rough[1][2] == 3000.0 and rough[2][2] == 250.0 and rough[6][3] == 3000.0, "a cell half off the platform is left"
    # two cities on different levels: joined while a street can climb it, not when it would be a ski jump
    assert len(joins(cs, gs, {"freeways": "no"}, ground=lambda x, y: 0.0 if x < 64000 else 1200.0)) == len(close)
    assert not joins(cs, gs, {"freeways": "no"}, ground=lambda x, y: 0.0 if x < 64000 else 6000.0), "too steep to join"
    # ... and its two halves, each graded with its own city, meet at one height: an even grade corner to corner
    halves = {"A Roads": {"points": [_point(0, 0, 0, 1700, 0, 875), _point(1, 20000, 0, 1650, 0, 875)],
                          "segments": [_segment(2, 0, 1, "2S", "/G")]},
              "B Roads": {"points": [_point(0, 50000, 0, -1300, 0, 875), _point(1, 20000, 0, -500, 0, 875)],
                          "segments": [_segment(2, 0, 1, "2S", "/G")]}}
    assert level_joins(halves, cs, [(0, 1, None, None, [20000, 0])]) == 1
    za, zb = halves["A Roads"]["points"][1]["z"], halves["B Roads"]["points"][1]["z"]
    assert za == zb and abs(za - (1700 - 3000 * 0.4)) < 1e-6, (za, zb)
    assert level_joins(halves, cs, [(0, 1, None, None, [99999, 0])]) == 0, "a street the water took is left alone"
    # a neighbour's ramp running low over this city's land: the street it would cut gives way
    cut = _near_cuts({"fw_near": [[5000, 0, 650]]})
    assert cut((5000, -3000), (5000, 3000), 525.0) and cut((3800, -3000), (3800, 3000), 525.0)
    assert not cut((9000, -3000), (9000, 3000), 525.0) and not _near_cuts({})((0, 0), (1, 1), 500.0)
    # the way overland: round the water, never over it; and a road to every port, from a street of the city
    # through the port's gate onto its platform
    lake_ = lambda x, y: 20000 < x < 60000 and -30000 < y < 30000
    way, tag_ = overland((0, 0), [(80000, 0, "far"), (0, 200000, "further")], lambda x, y: not lake_(x, y), lambda x, y: 0.0)
    assert tag_ == "far" and way[0] == (0, 0) and way[-1] == (80000, 0) and not any(lake_(*p) for p in way), way
    assert max(abs(p[1]) for p in way) > 30000 and overland((0, 0), [(80000, 0, "far")], lambda x, y: x < 20000, lambda x, y: 0.0) is None
    hilly, _t = overland((0, 0), [(60000, 0, "g")], lambda x, y: True, lambda x, y: 3000.0 if 20000 < x < 40000 and abs(y) < 20000 else 0.0)
    assert max(abs(p[1]) for p in hilly) > 15000, "it goes round a steep hill"
    yard_ = {"type": "repair", "frame": {"x": 0, "y": -150000, "ux": 1.0, "uy": 0.0, "vx": 0.0, "vy": 1.0}, "L": 40000, "D": 26000,
             "z": 250.0, "bank": 2000.0, "docks": []}
    port_c = {"name": "Port", "x": 0, "y": 0, "radius": 90000, "seed": 3, "style": "grid", "shipyards": [yard_]}
    g_port = [list(city_graph(port_c))]
    before_n = len(g_port[0][1])
    roads_ = port_roads([port_c], g_port, {}, [], lambda x, y: 0.0)
    assert len(roads_) == 1 and port_c["port_roads"] == roads_ and len(g_port[0][1]) == before_n + 1, roads_
    r0 = roads_[0]
    from . import shipyard as _sy
    assert _sy.zone(yard_, *_sy.uv(yard_, *r0[0])) == "platform" and tuple(r0[-1]) in {(round(p[0]), round(p[1])) for p in g_port[0][0].values()}, "from on its platform to a street crossing"
    assert abs(_sy.uv(yard_, *r0[1])[1] - 26000) < 1 and abs(_sy.uv(yard_, *r0[1])[0] - (20000 - 9950)) < 1, "out through its gate"
    assert all(not _sy.reserved(yard_, p[0], p[1], 1000) for p in r0[3:]), "then clear of the yard"
    pond_ = lambda x, y: 0 < x < 20000 and -124000 + 2000 < y < -110000  # water right behind its gate corner
    spit = {**port_c, "name": "Spit", "shipyards": [dict(yard_)]}
    moved_ = port_roads([spit], [list(city_graph(spit))], {}, [], lambda x, y: 0.0, pond_)
    assert len(moved_) == 1 and spit["shipyards"][0].get("gate_u") is not None and spit["shipyards"][0]["gate_u"] < 9000, "its gate moves along the back edge to dry land"
    assert not any(pond_(*p) for p in moved_[0]) and "gate_u" not in yard_
    strait = lambda x, y: -124000 + 2000 < y < -112000 and -40000 < x < 40000  # water along the whole of its back
    side_c = {**port_c, "name": "Side", "shipyards": [dict(yard_)]}
    by_side = port_roads([side_c], [list(city_graph(side_c))], {}, [], lambda x, y: 0.0, strait)
    sy_ = side_c["shipyards"][0]
    assert len(by_side) == 1 and sy_.get("gate_side") in (1, -1) and "gate_u" not in sy_, "with water all along its back it is entered from a side"
    assert abs(abs(_sy.uv(sy_, *by_side[0][1])[0]) - 20000) < 1 and not any(strait(*p) for p in by_side[0])
    # ... and a yard with water all round it gets its road straight across the water, to be bridged
    moat = lambda x, y: abs(y + 137000) < 30000 and abs(x) < 60000 and not (abs(x) <= 20000 and -150000 <= y <= -124000)
    isle = {**port_c, "name": "Isle", "shipyards": [dict(yard_)]}
    over_ = port_roads([isle], [list(city_graph(isle))], {}, [], lambda x, y: 0.0, moat)
    assert len(over_) == 1 and any(moat(*p) for p in over_[0]) and "gate_side" not in isle["shipyards"][0], "straight over the water from its own gate"
    # a yard with a street right beside it joins it straight (no room there for the route's 60 m cells)
    tight = {**yard_, "frame": {**yard_["frame"], "y": -40000 - 26000 - 2000 - 3500 - 9000}}
    near_c = {**port_c, "name": "Near", "shipyards": [tight]}
    g_near = list(city_graph(near_c))
    short = port_roads([near_c], [g_near], {}, [], lambda x, y: 0.0)
    assert len(short) == 1 and len(short[0]) == 4 and math.dist(short[0][2], short[0][3]) < 30000, short
    hw_n, hw_e = {"a": [-300000, -230000], "b": [300000, -230000]}, [["a", "b", "4L", [[x, -230000] for x in range(-280000, 280001, 20000)]]]
    far_c = {**port_c, "name": "Far", "y": 400000}
    port_roads([far_c], [list(city_graph(far_c))], hw_n, hw_e, lambda x, y: 0.0)
    assert len(hw_e) == 3 and len(hw_n) == 4 and far_c["port_roads"], "a port far from every street joins the highway, at a junction of its own"
    j_ = hw_n[("port", 0, 0, "hw")]
    assert hw_e[0][1] == ("port", 0, 0, "hw") == hw_e[1][0] and len(hw_e[0][3]) + len(hw_e[1][3]) == 28 and j_[1] == -230000
    # one big park in the centre district: no street through it, the streets round it all there, the block they
    # leave a park (trees in groves, paths, benches, playgrounds, a pond, fields), nothing built on it
    park_c = {"name": "Parkville", "x": 0, "y": 0, "radius": 200000, "seed": 11, "style": "mixed"}
    po = city_options(park_c, None)
    pk = central_park(park_c, po)
    assert pk and sorted((pk[2] - pk[0], pk[3] - pk[1])) == [2, 4], pk
    assert central_park(park_c, po) == pk and central_park({**park_c, "radius": 60000}, po) is None, "the same every time; a small city has none"
    assert not (pk[0] < 0 < pk[2] or pk[1] < 0 < pk[3]), "never across a main axis"
    pn, pe = city_graph(park_c)
    inside_pk = lambda n: isinstance(n, tuple) and len(n) == 2 and pk[0] < n[0] < pk[2] and pk[1] < n[1] < pk[3]
    assert not any(inside_pk(n) for n in pn) and not any(inside_pk(e[0]) or inside_pk(e[1]) for e in pe), "no street in it"
    rim_n = [(i, j) for i in range(pk[0], pk[2] + 1) for j in range(pk[1], pk[3] + 1) if i in (pk[0], pk[2]) or j in (pk[1], pk[3])]
    have_e = {(e[0], e[1]) for e in pe} | {(e[1], e[0]) for e in pe}
    assert all(n in pn for n in rim_n) and all(((i, j), (i + 1, j)) in have_e for i in range(pk[0], pk[2]) for j in (pk[1], pk[3])) \
        and all(((i, j), (i, j + 1)) in have_e for j in range(pk[1], pk[3]) for i in (pk[0], pk[2])), "a street all the way round it"
    lay_p = city_layout(park_c, {"planner": "no"})
    mid_p = [sum(pn[(i, j)][k] for i in (pk[0], pk[2]) for j in (pk[1], pk[3])) / 4 for k in (0, 1)]
    blk = [b for b in lay_p["blocks"] if _inside(b[4], *mid_p)]
    assert len(blk) == 1 and blk[0][2] == "park" and abs(parcels_area(blk[0][4])) > 5 * 1e8, blk and blk[0][:4]
    kinds_p = [g[5] for g in lay_p["grounds"] if _inside(blk[0][4], g[0], g[1])]
    assert kinds_p.count("path") > 100 and "playground" in kinds_p and "pond" in kinds_p and "bench" in kinds_p, sorted(set(kinds_p))
    in_blk = [t for t in lay_p["trees"] if _inside(blk[0][4], t[0], t[1])]
    full = len(_grove(blk[0][4], random.Random(1)))
    assert 0.3 * full < len(in_blk) < 0.8 * full, ("groves and lawns, not a plantation", len(in_blk), full)
    assert not any(_inside(blk[0][4], r[0], r[1]) for r in lay_p["lots"]), "nothing built in the park"
    solid_g = [g for g in lay_p["grounds"] if g[5] in ("pond", "field", "playground") and _inside(blk[0][4], g[0], g[1])]
    for t in in_blk:
        for g in solid_g:
            f_ = math.radians(g[2])
            du_, dv_ = (t[0] - g[0]) * math.cos(f_) + (t[1] - g[1]) * math.sin(f_), -(t[0] - g[0]) * math.sin(f_) + (t[1] - g[1]) * math.cos(f_)
            assert not (abs(du_) < g[3] * 50 and abs(dv_) < g[4] * 50), ("a tree in a pond, a field or a playground", t, g)
    # a lake drawn by hand: dug to the world's water line (a world with none gets one under its lowest ground,
    # so nothing else floods), a bed in its middle, banks no steeper than 1 in 3, nothing changed far from it
    drawn = lake_list([{"pts": [[-60000, 0], [60000, 0]], "width_m": 300}, {"pts": [[0, 90000]]}, {"pts": "x"}, {"id": "lake-1", "pts": [[1, 1], [1, 1]], "width_m": 5}])
    assert [(l["id"], len(l["pts"]), l["width_m"]) for l in drawn] == [("lake-2", 2, 300.0), ("lake-3", 1, 200.0), ("lake-1", 1, 60.0)], drawn
    dry_t = terrain(5, 400000, 3000, [], grid=80)
    lake_t = terrain(5, 400000, 3000, [], grid=80, lakes=drawn[:1])
    assert dry_t["sea"] is None and lake_t["sea"] < min(min(r) for r in dry_t["heights"]), "its water line lies under all the land"
    wet_l = wet_from(lake_t)
    assert all(wet_l(x, 0) for x in range(-55000, 55001, 5000)) and wet_l(0, 10000) and not wet_l(0, 40000), "water along the line, as wide as drawn"
    flooded = sum(1 for j in range(80) for i in range(80) if lake_t["heights"][j][i] < lake_t["sea"])
    assert 0.6 * 120000 * 30000 < flooded * 5000 * 5000 < 1.6 * 150000 * 30000, flooded
    same = sum(1 for j in range(80) for i in range(80) if lake_t["heights"][j][i] == dry_t["heights"][j][i])
    assert same > 0.5 * 6400 and lake_t["heights"][2][2] == dry_t["heights"][2][2], "the land away from it is as it was"
    steep = max(abs(lake_t["heights"][j][40] - lake_t["heights"][j + 1][40]) / 5000 for j in range(46, 70))
    assert steep <= LAKE_BANK_GRADE * 1.6, ("its banks are slopes, not a pit", steep)
    sea_t = terrain(5, 400000, 3000, [], grid=80, opts={"sea_m": -10}, lakes=drawn[:1])
    assert sea_t["sea"] == -1000.0 and wet_from(sea_t)(0, 0), "a world with water: dug to that water's level"
    # a city's centre district moved off its centre: the rings lie round the new spot, the edge stays where it
    # is; its own district sizes and industrial side are the city's, whatever the world's are
    plain_c = {"name": "Plain", "x": 0, "y": 0, "radius": 100000, "seed": 4, "style": "grid", "turn": 0.0}
    moved_c = {**plain_c, "core": [40000, 0]}
    assert core_reach(plain_c) is None and zone_share(moved_c, 40000, 0) == 0.0
    for deg_ in range(0, 360, 7):
        ex, ey = (100000 * outline(plain_c, deg_) * f_ for f_ in (math.cos(math.radians(deg_)), math.sin(math.radians(deg_))))
        assert abs(zone_share(moved_c, ex, ey) - 1.0) < 0.004, (deg_, zone_share(moved_c, ex, ey))
        assert zone_share(moved_c, ex * 0.98, ey * 0.98) < 1.0 < zone_share(moved_c, ex * 1.02, ey * 1.02)
    assert zone_share(moved_c, 0, 0) > 0.25 and zone_share(plain_c, 40000, 0) > 0.3, "the old centre is no longer the middle"
    ring_m, _ind = zoning(moved_c, {"downtown": 0.3})
    assert ring_m(40000, 0) == "core" and ring_m(-20000, 0) != "core" and zoning(plain_c, {"downtown": 0.3})[0](0, 0) == "core"
    assert clamp_core(plain_c, 500, 500) == [0, 0] and math.hypot(*clamp_core(plain_c, 300000, 0)) <= 0.6 * 100000 * 1.27
    own = {**plain_c, "features": {"downtown": 0.2, "normal": 0.5}, "industry_deg": 90.0, "spin": 10.0}
    d_own = districts(own, {"downtown": 0.4})
    assert d_own["downtown"]["radius"] == 20000 and d_own["normal"]["radius"] == 50000 and d_own["industrial"]["angle"] == 100.0
    assert "core" not in d_own and districts(moved_c)["core"] == [40000, 0]
    g_plain, g_moved = city_graph(plain_c, {"downtown": 0.3}), city_graph(moved_c, {"downtown": 0.3})
    wide = lambda g, x0, x1: sum(1 for a, b, k, *_ in g[1] if k == options()["core_roads"] and x0 < g[0][a][0] < x1 and abs(g[0][a][1]) < 20000)
    assert wide(g_moved, 25000, 55000) > wide(g_plain, 25000, 55000), "downtown's streets are where the centre district is now"
    # a freeway never runs out over a lake to end in the water: it stops at the shore, back at grade; a river with
    # land beyond it is crossed as before
    from . import freeways as _fw
    lake_city = {"name": "Lake", "x": 0, "y": 0, "radius": 120000, "seed": 4, "style": "grid", "turn": 0.0}
    lake = lambda x, y: y > 60000
    shore_plan([lake_city], None, lake)
    assert lake_city["fw_shore"]["ns"][1] <= 60000 < lake_city["fw_shore"]["ns"][0], lake_city["fw_shore"]
    held_back = freeway_plan(lake_city)
    assert held_back.lines and max(p[1] for ln in held_back.lines if ln["cls"] == "freeway" for p in ln["points"]) <= 60000
    assert min(p[1] for ln in held_back.lines if ln["cls"] == "freeway" for p in ln["points"]) < -150000, "the dry end runs on"
    river_city = dict(lake_city, name="River")
    river_city.pop("fw_shore")
    shore_plan([river_city], None, lambda x, y: 60000 < y < 66000)
    assert min(river_city["fw_shore"]["ns"]) > 150000, "land beyond the water: the freeway bridges it"
    # ... a river, not a bay: water wider than freeways.FW_WATER_MAX ends it on the near shore, land beyond or not
    bay_city = dict(lake_city, name="Bay")
    bay_city.pop("fw_shore")
    shore_plan([bay_city], None, lambda x, y: 60000 < y < 60000 + _fw.FW_WATER_MAX + 30000)
    assert bay_city["fw_shore"]["ns"][1] <= 60000 < bay_city["fw_shore"]["ns"][0], bay_city["fw_shore"]
    # ... and no interchange with a ramp in the water: the ones a river runs past are left out, the rest stay
    dry_ics = [i["at"] for i in freeway_plan(dict(lake_city, name="Dry", fw_shore=None)).interchanges if i["kind"] != "stack"]
    pond_at = dry_ics[0]
    pond_city = dict(lake_city, name="Pond")
    pond_city.pop("fw_shore")
    shore_plan([pond_city], None, lambda x, y: math.hypot(x - pond_at[0], y - pond_at[1]) < 8000)
    assert min(pond_city["fw_shore"]["ns"]) > 150000, "(a pond 160 m across: the freeway itself bridges it)"
    left = [i["at"] for i in freeway_plan(pond_city).interchanges]
    assert left and all(math.dist(i, pond_at) > 30000 for i in left), (pond_at, left)
    assert _fw.shore_reach(lake_city, options(), None) is None and "fw_shore" not in (shore_plan([river_city], None, None) or river_city)
    # ... and it ends before a neighbouring city's land: none of it runs through the other city
    host = {"name": "Host", "x": 0, "y": 0, "radius": 120000, "seed": 4, "style": "grid", "turn": 0.0}
    guest = {"name": "Guest", "x": 48000, "y": 190000, "radius": 60000, "seed": 9, "style": "grid", "shape": [1.0] * SHAPE_POINTS}
    through = [p for ln in freeway_plan(dict(host)).lines if ln["cls"] == "freeway" for p in ln["points"] if zone_share(guest, p[0], p[1]) <= 1.0]
    shore_plan([host, guest], None, None)
    after = [p for ln in freeway_plan(host).lines if ln["cls"] == "freeway" for p in ln["points"]]
    assert through and after and not any(zone_share(guest, p[0], p[1]) <= 1.0 for p in after), (len(through), len(after))
    assert "fw_shore" not in guest or guest["fw_shore"], "the small one has no freeway to limit"
    # ... and the land along a joining street fills in with house lots, outside both cities' own edges
    cs2 = [{**cs[0], "joins": [[60000, 0, 80000, 0]]}, {**cs[1], "x": 220000}]
    jl, corridors = join_lots(cs2[0], cs2)
    assert len(jl) >= 12 and len(corridors) == 1 and all(_inside(corridors[0], r[0], r[1]) for r in jl), len(jl)
    assert all(abs(r[1]) > 875 and abs(r[6]) < abs(r[1]) and all(zone_share(c, r[0], r[1]) > 1.0 for c in cs2) for r in jl)
    assert {r[2] for r in jl} == {90.0, -90.0}, "each lot faces the street"
    # a cable-stayed bridge: each tower stands astride the deck (a leg outside each edge, nothing in the roadway),
    # beams join its legs, and its stays run from a leg's top down to that edge of the deck
    long_deck = {"x": {"points": [_point(k, k * 6000, 0, 3000, 0, 1650, lifted=True) for k in range(11)],
                       "segments": [_segment(20 + k, k, k + 1, "ED", "/G", lifted=True) for k in range(10)]}}
    stayed = [{"type": "cable-stayed bridge", "x": 30000, "y": 0, "span_m": 600, "towers": [[18000, 40], [42000, -30]]}]
    tw = bridge_towers(long_deck, stayed, lambda x, y: -800.0, 0.0)
    half = HALF["ED"]
    assert len(tw["legs"]) == 4 and all(abs(r[1]) >= half + TOWER_CLEAR_CM for r in tw["legs"]), ("no leg in the roadway", tw["legs"])
    assert all(r[2] == -800 and abs(r[2] + r[6] * 100 - (3000 + 6000)) < 2 for r in tw["legs"]), "from the bed to 60 m over the deck"
    assert len(tw["beams"]) == 6 and all(r[5] * 100 > 2 * half + 300 and r[1] == 0 for r in tw["beams"]), tw["beams"]
    assert min(r[2] + r[6] * 120 for r in tw["beams"]) <= 3000 - DECK_DEPTH_CM and \
        sorted(r[2] for r in tw["beams"])[2] > 3000 + 1500, "one beam under the deck, the others well over the traffic"
    assert len(tw["stays"]) == 2 * 2 * 2 * 5, len(tw["stays"])
    for x, y, z, yaw, sx, _sy, sz in tw["stays"]:
        ex, ey = x + math.cos(math.radians(yaw)) * sx * 100, y + math.sin(math.radians(yaw)) * sx * 100
        assert any(abs(x - r[0]) < 2 and abs(y - r[1]) < 2 for r in tw["legs"]), "a stay leaves from a leg"
        assert z == 3000 and abs(abs(ey) - (half - 40)) < 3 and 0 < ex < 60000 and y * ey > 0, ("and lands on its edge of the deck", ex, ey)
        assert 3000 + sz * 100 <= 3000 + 6000 and sz > 30
    held = under_cables(tw["spans"])
    assert held(18000, 0) and held(25000, 900) and not held(1000, 0) and not held(18000, 5000)
    assert [c for c in bridge_piers(long_deck, lambda x, y: -800.0)[1] if not held(c[0], c[1])], "the approaches keep their piers"
    assert not bridge_towers(long_deck, [{**stayed[0], "type": "girder bridge (navigable)"}], lambda x, y: -800.0)["legs"]
    assert not bridge_towers(long_deck, [{**stayed[0], "towers": [[18000, 9000]]}], lambda x, y: -800.0)["legs"], "no deck there: no tower"
    # a slip ramp sliding into a street at a shallow angle: the pieces lying inside the street go
    slip = {"T Roads": {"points": [_point(0, 0, 0, 0, 0, 525), _point(1, 20000, 0, 0, 0, 525)], "segments": [_segment(2, 0, 1, "2L", "/G")]},
            "T Freeways": {"points": [_point(k, 2000 + 2000 * k, y, 0, 0, 650, lifted=True) for k, y in enumerate((5000, 3000, 2000, 1200, 400, 200))],
                           "segments": [_segment(10 + k, k, k + 1, "ER", "/G", lifted=True) for k in range(5)]}}
    for _ in range(8):
        if not weld_ends(slip):
            break
    assert len(slip["T Freeways"]["points"]) == 5 and all(abs(p["y"]) > 1000 for p in slip["T Freeways"]["points"]), \
        [(p["x"], p["y"]) for p in slip["T Freeways"]["points"]]
    # a ramp welded 30 m up with a fork 10 m back: the climb is shared out along both branches, none left a cliff
    yp = [_point(0, 0, 0, 3000, 0, 650, lifted=True), _point(1, 1000, 0, 0, 0, 650, lifted=True)] + \
        [_point(2 + k, 3000 + 2000 * k, sd * 500 * (k + 1), 0, 0, 650, lifted=True) for sd in (1, -1) for k in range(30)][:60]
    for n, p in enumerate(yp):
        p["i"] = n
    yp[0]["mesh"] = "SM_Road_RampGore30m"
    ys = [_segment(100, 0, 1, "ER", "/G", lifted=True)] + [_segment(101 + n, 1 if n in (2, 32) else n - 1, n, "ER", "/G", lifted=True) for n in range(2, 62)]
    ya = {"points": yp, "segments": ys}
    assert settle_grades(ya) > 10
    yb = {p["i"]: p for p in yp}
    steep = max(abs(yb[s["a"]]["z"] - yb[s["b"]]["z"]) / math.hypot(yb[s["a"]]["x"] - yb[s["b"]]["x"], yb[s["a"]]["y"] - yb[s["b"]]["y"]) for s in ys)
    assert steep < SETTLE_GRADE + 0.005 and yp[0]["z"] == 3000 and yp[31]["z"] == 0 and yp[61]["z"] == 0, (steep, yp[1]["z"])
    ab = bridge_abutments({"x": {"points": [_point(0, 0, 0, 0, 0, 525), _point(1, 5000, 0, 100, 0, 925, lifted=True), _point(2, 9000, 0, 900, 0, 925, lifted=True)],
                                 "segments": [_segment(3, 0, 1, "2S", "/G"), _segment(4, 1, 2, "BR", "/G", lifted=True)]}})
    assert ab == [[5000, 0, 100 - 150 - 250, 0.0, 1.0, 19.5, 1.0]], ("one abutment, where the street becomes the bridge", ab)
    assert not bridge_piers({"x": {"points": [_point(0, 0, 0, 200, 0, 650, lifted=True), _point(1, 6000, 0, 200, 0, 650, lifted=True)],
                                   "segments": [_segment(2, 0, 1, "ER", "/G", lifted=True)]}}, lambda x, y: 0.0)[0], "a deck on its fill"
    # ... that low ramp stands on its own approach wall instead: from 1 m in the ground up into its slab, end to end
    low = {"x": {"points": [_point(0, 0, 0, 400, 0, 650, lifted=True), _point(1, 6000, 0, 400, 0, 650, lifted=True),
                            _point(3, 3000, -5000, 0, 0, 525), _point(4, 3000, 5000, 0, 0, 525)],
                 "segments": [_segment(2, 0, 1, "ER", "/G", lifted=True), _segment(5, 3, 4, "2L", "/G")]}}
    fills = deck_fills(low, lambda x, y: 0.0)
    assert fills and all(r[2] == -EMBED_CM and abs(r[2] + r[6] * 100 - (400 - DECK_DEPTH_CM + 5)) < 2 for r in fills), fills
    assert abs(sum(r[4] for r in fills) - (60 - 10.5) * 1.04) < 6 and all(abs(r[0] - 3000) > 500 for r in fills), "open over the street"
    assert not deck_fills(low, lambda x, y: -900.0), "high enough for piers: no wall"
    assert not deck_fills(low, lambda x, y: -200.0, sea=0.0), "over water: a bridge"
    assert not deck_fills(low, lambda x, y: 245.0), "a deck lying on the ground needs none"
    # a lot levelled 8 m above the street it fronts: the ground comes down to the road no steeper than 1 in 2 ...
    plain = {"min": [0, 0], "max": [10000, 10000], "grid": 10, "heights": [[0.0] * 10 for _ in range(10)]}
    lot8 = [(3500, 5000, 1.0, 0.0, 1500, 1500, 800.0)]
    by_road = cut_pads(plain, lot8, {(j, 0) for j in range(10)})["heights"][5]
    assert by_road[:3] == [0.0, 500.0, 800.0] and cut_pads(plain, lot8)["heights"][5][1] > 550, by_road
    # ... and a foundation is as deep as the ground really falls away under its building
    assert footings(lambda x, y: -0.3 * x, [("h", 0, 0, 0.0, 12, 12, 0)]) == [1.8]
    assert footings(lambda x, y: 0.0, [("h", 0, 0, 0.0, 12, 12, 0)]) == [0.0]
    # a city turned by hand: its grid, its outline and its industrial side turn together, nothing else changes
    town = {"name": "T", "x": 0, "y": 0, "radius": 60000, "seed": 5, "style": "grid"}
    spun = {**town, "turn": 30.0, "spin": 30.0 - seed_turn(town)}
    assert abs(orientation(spun) - 30.0) < 1e-9 and orientation(town) == seed_turn(town)
    assert abs(outline(spun, 100.0) - outline(town, 100.0 - spun["spin"])) < 1e-9
    off = (districts(spun)["industrial"]["angle"] - districts(town)["industrial"]["angle"] - spun["spin"]) % 360
    assert min(off, 360 - off) < 1e-6, off
    gn, _ge = city_graph(spun)
    far = max(gn.values(), key=lambda p: abs(p[0]) + abs(p[1]) if abs(p[1] - p[0] * math.tan(math.radians(30))) < 50 else 0)
    assert abs(math.degrees(math.atan2(far[1], far[0])) % 180 - 30.0) < 1.0, ("a street through the centre runs at 30 degrees", far)
    # an outline drawn by hand: each handle is where the city's edge is, eased between them, turned with the city
    hand = [1.0] * SHAPE_POINTS
    hand[0], hand[4] = 1.26, 0.74
    drawn = {**town, "shape": hand}
    assert outline(drawn, 0.0) == 1.26 and outline(drawn, 90.0) == 0.74 and abs(outline(drawn, 11.25) - 1.13) < 1e-9
    assert outline({**drawn, "spin": 45.0}, 45.0) == 1.26
    assert all(SHAPE_RANGE[0] <= outline(drawn, a) <= SHAPE_RANGE[1] for a in range(0, 360, 3))
    # one block given a type: only that block changes, every other block of the city is exactly as it was
    big = {"name": "B", "x": 0, "y": 0, "radius": 90000, "seed": 3, "style": "grid"}
    base = city_layout(big)
    pick = next(b for b in base["blocks"] if b[2] not in ("parcels", "collector") and b[3] == "housing")
    parked = city_layout({**big, "zones": [{"x": pick[0], "y": pick[1], "kind": "park"}]})
    there = lambda rows: [r for r in rows if _inside(pick[4], r[0], r[1])]
    assert [b[2] for b in parked["blocks"] if b[:2] == pick[:2]] == ["park"]
    assert not there(parked["lots"]) and len(there(parked["trees"])) > 20, "a park block: trees, nothing built"
    away = lambda rows: [r for r in rows if not _inside(pick[4], r[0], r[1])]
    bare = {"planner": "no"}  # the lots as the blocks cut them, before the planner picks its sites from them
    # (... which it does anew for the city as it now is: with the homes of that block gone a daycare may move)
    assert away(city_layout({**big, "zones": [{"x": pick[0], "y": pick[1], "kind": "park"}]}, bare)["lots"]) == \
        away(city_layout(big, bare)["lots"]) and [b for b in parked["blocks"] if b[:2] != pick[:2]] == \
        [b for b in base["blocks"] if b[:2] != pick[:2]], "no other block moved"
    # lots left green: that share of the block's lots is planted instead of built on, a bigger share keeps the
    # ones a smaller share planted, and no other block changes
    lots_at = lambda lay: {(r[0], r[1]) for r in lay["lots"] if _inside(pick[4], r[0], r[1])}
    plots = city_layout({**big, "zones": [{"x": pick[0], "y": pick[1], "kind": "parcels"}]}, bare)
    half = city_layout({**big, "zones": [{"x": pick[0], "y": pick[1], "kind": "parcels", "green": 0.5}]}, bare)
    most = city_layout({**big, "zones": [{"x": pick[0], "y": pick[1], "kind": "parcels", "green": 0.9}]}, bare)
    n0, n5, n9 = len(lots_at(plots)), len(lots_at(half)), len(lots_at(most))
    assert 0 < n9 < n5 < n0 and lots_at(most) <= lots_at(half) <= lots_at(plots), (n0, n5, n9)
    assert len(there(half["trees"])) > len(there(plots["trees"])) + 10 and away(half["lots"]) == away(plots["lots"])
    assert all(_inside(pick[4], *t) for t in there(half["trees"])), "its trees stand on its own lots"
    assert [b[5] for b in half["blocks"] if b[:2] == pick[:2]] == [0.5]
    all_lots, some_green = city_layout(big, bare)["lots"], city_layout(big, {**bare, "green_lots": 0.3})["lots"]
    assert 0.6 * len(all_lots) < len(some_green) < 0.85 * len(all_lots), (len(all_lots), len(some_green))
    core = city_layout({**big, "zones": [{"x": pick[0], "y": pick[1], "zone": "downtown"}]})
    assert core["rezoned"] and core["rezoned"][0][1] == "downtown" and \
        [b[2:4] for b in core["blocks"] if b[:2] == pick[:2]] == [["parcels", "downtown"]]
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
    # a city put over a lake leaves the lake where it is: flattening levels the land, never the water
    lake_city = [{"name": "L", "x": 0, "y": 0, "radius": 150000}]
    t = terrain(3, 400000, 6000, lake_city, grid=48, opts={"sea_m": 20, "city_ground": "flatten"})
    lake = terrain(3, 400000, 6000, [], grid=48, opts={"sea_m": 20})
    was_wet = [(j, i) for j in range(48) for i in range(48) if lake["heights"][j][i] < lake["sea"]]
    assert was_wet and all(t["heights"][j][i] == lake["heights"][j][i] for j, i in was_wet), "water stays water under a flattened city"
    assert t["heights"] != lake["heights"], "the land round it is still levelled"
    small = city_graph(cities[0], {"block_m": 60})[1]
    assert len(small) > len(city_graph(cities[0], {"block_m": 150})[1])
    two = generate(cities, lambda x, y: 0.0, "/Game/X/WorldGen/Roads", seed=3, opts={"highway": "2L"})
    assert set(two["Highways"]["segments"][k]["mesh"] for k in range(len(two["Highways"]["segments"]))) <= {"", ROAD_MESH["2L"]}
    steps = terrain(3, 400000, 6000, [], grid=32, opts={"terrace_m": 20})
    assert steps["heights"], "terraces build"
    png, settings = heightmap_png(ter, res=65)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and settings["scale"][2] == 100.0
    # paint mask: a road disc and a turned lot are painted, the corner far from both is not
    sq = {"min": [0, 0], "max": [6400, 6400]}
    wm = weightmap_rows(sq, 65, [(1000, 1000, 300)], [(4000, 4000, 0.0, 1.0, 500, 1000, 0)])
    assert wm[10][10] == 255 and wm[50][45] == 255 and wm[40][50] == 0 and wm[60][5] == 0, "roads and lots painted"
    assert weightmap_png(sq, 65, [], [])[:8] == b"\x89PNG\r\n\x1a\n"
    # landscape size: snapped to whole 63-quad components, 2017 by default (the most UEFN's 2048 x 2048 cap takes)
    assert landscape_res({}) == 2017 and landscape_res({"landscape_px": 1000}) == 1009 and 2017 ** 2 <= UEFN_MAX_SAMPLES
    assert landscape_res({"landscape_px": 2600}) ** 2 > UEFN_MAX_SAMPLES
    # 2017 stays one World Partition region: 16 x 16 components of 2 x 2 sections (32 x 32 came in as 4 copies)
    _png, big = heightmap_png(ter, rows=[[32768] * 2017] * 2017)
    assert big["components"].startswith("16 x 16") and "2x2" in big["components"], big["components"]
    # bicubic refine: a plane stays a plane (no creases), and the coarse samples are kept
    ramp = {"min": [0, 0], "max": [1000, 1000], "grid": 8, "heights": [[i * 10.0 + j for i in range(8)] for j in range(8)]}
    fr = refine(ramp, 32)
    assert all(abs(fr["heights"][j][i] - ((i + 0.5) / 4 - 0.5) * 10 - ((j + 0.5) / 4 - 0.5)) < 0.2
               for j in range(6, 26) for i in range(6, 26)), "a plane refines to the same plane"  # (inside: taps clamp at the border)
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
    # Blender interchange models: only where the whole square is inside the city's circle, freeway cut at its
    # sockets; 'no' (or a city too small for one) keeps the generated ramps
    from . import freeways as _fw
    if _fw.models():
        huge = {"name": "Huge", "x": 0, "y": 0, "radius": 450000, "seed": 7, "style": "mixed"}
        got = [i for i in freeway_plan(huge).interchanges if i.get("model")]
        assert got, [i["kind"] for i in freeway_plan(huge).interchanges]
        half = {k: math.hypot(*m["size"]) / 2 for k, m in _fw.models().items()}
        assert all(math.hypot(*i["at"]) + half[i["kind"]] <= 450000 for i in got), "inside the city circle"
        world = generate([huge], lambda x, y: 0.0, "/G", seed=1)["Huge Freeways"]
        held = {(round(p["x"]), round(p["y"])) for p in world["points"] if p["mesh"] == "IC_Socket"}
        for i in got:
            for sx_, sy_ in i["model"]["sockets"]:
                assert any(math.hypot(sx_ - x, sy_ - y) < 100 for x, y in held), (i["kind"], sx_, sy_)
        assert sum(1 for p in world["points"] if "/Interchanges/IC_" in "".join(p["lines"])) == len(got)
        assert not any(i.get("model") for i in freeway_plan(huge, {"interchange_models": "no"}).interchanges)
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

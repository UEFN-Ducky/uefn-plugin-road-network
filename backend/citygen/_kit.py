from __future__ import annotations

import math
import random
import struct
import zlib

from .. import splines_text as st

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
    # the six Blender interchange models (assets/interchanges) wherever one's footprint is on the city's free land,
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
# what a generated or added city gets when nobody picked: never a ruled grid (every second city of a new world was one,
# when Valley's had come out organic and mixed). Grid stays a choice in the city's own settings.
AUTO_STYLES = ("organic", "mixed")
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
    # mountain ranges in places only, on top of the everyday ground (relief_m): their tallest peak, and the share of
    # the map they cover. The heightmap's Z scale grows with them (z_scale)
    "mountain_m": (0.0, 0.0, 2500.0),
    "mountain_cover": (0.3, 0.05, 0.8),
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

# the terrain engine's sliders and pickers (backend/terrain/config.py, metres). 'Classic' is the old ground below,
# any other preset runs the engine. Its `detail` (0..1) and `relief_m` are left out: citygen's detail is octaves,
# and relief comes in as terrain()'s relief_cm
from ..terrain import config as _tcfg  # noqa: E402
OPTIONS.update({k: v for k, v in _tcfg.KEYS.items() if k not in OPTIONS and k not in ("detail", "relief_m")})
CHOICES["preset"] = (tuple(_tcfg.PRESETS), "Classic")
CHOICES["coast"] = (_tcfg.CHOICES["coast"][1], _tcfg.CHOICES["coast"][0])


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



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
    "6L": ("SM_Road_Freeway_Lane20m", 1650.0, 6, False, "six-lane boulevard / freeway"),
    "BR": ("SM_Road_Bridge_Straight20m", 925.0, 2, False, "bridge deck"),
    "BRR": ("SM_Road_Bridge_Ramp40m", 925.0, 2, False, "bridge ramp"),
    "ED": ("Elevated/SM_Elev_Deck20m", 1650.0, 4, False, "elevated deck"),
    "ER": ("Elevated/SM_Elev_Ramp20m", 650.0, 1, False, "elevated ramp"),
    # pedestrian and access splines (the '<City> Paths' actor): live like roads, so they bend, keep a grade
    # and cut / fill the ground. City Builder builds these three meshes itself (street kit).
    "SW": ("SM_Path_Sidewalk_10m", 190.0, 0, True, "sidewalk (1.8 m clear path + frontage + furniture strip)"),
    "WK": ("SM_Path_Walkway_10m", 80.0, 0, True, "walkway from a door to the sidewalk"),
    "DW": ("SM_Path_Driveway_10m", 175.0, 0, False, "driveway from a garage to the street"),
}
NO_SIDEWALK = ("6L", "1W", "2L")  # city roads whose mesh has no sidewalk: the sidewalk generator adds them
# (dirt roads stay bare); with these every city street has a pavement from kerb to lot line, never grass
ROAD_MESH = {k: v[0] for k, v in ROAD_TYPES.items()}
HALF = {k: v[1] for k, v in ROAD_TYPES.items()}
LOOP_PIECE = "SM_Road_Loop_Big"  # trumpet loop (point piece)
CLOVER_PIECE = "SM_Road_Freeway_Cloverleaf"  # in NeonSkyscrapers RoadKit: import it before using cloverleaf
# per-district road choices (the panel's Roads tab)
CHOICES = {
    "core_roads": (("4L", "6L", "2S"), "4L"),
    "neighbourhood_roads": (("2S", "2L", "4L"), "2S"),
    "suburb_roads": (("2L", "2S", "1W", "DT"), "2L"),
    "highway": (("4L", "6L", "2L"), "4L"),
    "interchange": (("direct", "diamond", "trumpet", "cloverleaf", "stack"), "direct"),
    "one_way_core": (("no", "yes"), "no"),  # core side streets as alternating one-way lanes
    "overpasses": (("yes", "no"), "yes"),  # highways bridge over the streets they cross
}
# steepest grade per road (rise / run): arterials and highways 4 %, collectors 5-6 %, local streets 8 %,
# dirt 10 % (CityEngine-style targets for a game, not legal limits). Steeper ground is cut and filled.
GRADE = {"6L": 0.04, "4L": 0.05, "2S": 0.06, "2L": 0.08, "1W": 0.08, "DT": 0.10,
         "SW": 0.08, "WK": 0.08, "DW": 0.15}  # walkways at ramp grade (8.3 %), driveways up to 15 %
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
    "One editor call does little (one mesh build, a few junctions, one tile): UEFN frees windows only per frame.",
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
}
HIGHWAYS = CHOICES["highway"][0]


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
    """Hills, flattened into a plateau under every city. Same shape as a ground scan:
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
    plateaus = [(c, max(at(c["x"], c["y"]), (sea + shore * 2) if sea is not None else -1e9)) for c in cities]
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


def graded(ter: dict, pads: list):
    """The terrain with every lot cut and filled to its level pad (pcg_layers.pads): z = f(x, y)."""
    cells: dict = {}
    for p in pads:
        r = max(p[4], p[5]) + PAD_BLEND_CM
        for i in range(int((p[0] - r) // PAD_CELL_CM), int((p[0] + r) // PAD_CELL_CM) + 1):
            for j in range(int((p[1] - r) // PAD_CELL_CM), int((p[1] + r) // PAD_CELL_CM) + 1):
                cells.setdefault((i, j), []).append(p)

    def at(x, y):
        z = st.sample_grid(ter, x, y) or 0.0
        for cx, cy, c, s, hw, hd, pz in cells.get((int(x // PAD_CELL_CM), int(y // PAD_CELL_CM)), ()):
            dx, dy = x - cx, y - cy
            e = max(abs(dx * c + dy * s) - hd, abs(-dx * s + dy * c) - hw)  # outside the pad, in cm
            if e < PAD_BLEND_CM:
                z += (pz - z) * (1 - _smoothstep(0, PAD_BLEND_CM, e))
        return z
    return at


def heightmap_png(ter: dict, res: int = 1009, pads: list | None = None) -> tuple:
    """16-bit greyscale PNG of the terrain for Landscape > Import, plus the import settings
    (Z scale 100: 1 unit = 100/128 cm, so +-256 m fits). pads: lots levelled into it."""
    lo, hi = ter["min"], ter["max"]
    ground = graded(ter, pads or [])
    raw = bytearray()
    for py in range(res):
        y = lo[1] + (hi[1] - lo[1]) * py / (res - 1)
        vals = []
        for px in range(res):
            x = lo[0] + (hi[0] - lo[0]) * px / (res - 1)
            z = ground(x, y)
            vals.append(max(0, min(65535, int(round(32768 + z * 128 / 100)))))
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
def place_cities(seed: int, count: int, bounds: tuple, keep: list | None = None) -> list:
    """count cities inside bounds ((x0, y0), (x1, y1)), never overlapping each other or a keep zone;
    their size follows the map (8-14 % of its width, so a 6 km map gets 500-850 m cities)."""
    rnd = random.Random(seed * 31 + 7)
    (x0, y0), (x1, y1) = bounds
    span = min(x1 - x0, y1 - y0)
    min_r, max_r = span * 0.08, span * 0.14
    out = []
    for _ in range(count * 60):
        if len(out) >= count:
            break
        r = rnd.uniform(min_r, max_r)
        x, y = rnd.uniform(x0 + r * 1.2, x1 - r * 1.2), rnd.uniform(y0 + r * 1.2, y1 - r * 1.2)
        if in_keep(keep, x, y, r * 1.5):
            continue
        if all(math.hypot(x - c["x"], y - c["y"]) > (r + c["radius"]) * 1.5 for c in out):
            out.append({"name": NAMES[len(out) % len(NAMES)], "x": round(x), "y": round(y), "radius": round(r),
                        "style": STYLES[len(out) % len(STYLES)], "seed": rnd.randrange(1 << 30)})
    return out


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


def city_graph(city: dict, opts: dict | None = None) -> tuple:
    """Street network of one city: nodes {key: [x, y]}, edges [(a, b, kind)]. Real cities mix patterns
    (urban-design transect): a strict grid of short blocks in the core, a looser grid in the
    neighbourhoods, curving streets with cul-de-sacs in the suburbs, all inside an irregular outline.
    Downtown and the two main axes are four-lane; suburbs are two-lane; the rest has pavements."""
    o = options(opts)
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
                lx += rnd.uniform(-1, 1) * amount * block
                ly += rnd.uniform(-1, 1) * amount * block
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
    return nodes, edges


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


def paths_graph(rows: list) -> tuple:
    """FrontageConnections: every walkway / driveway of the previewed lots ([.., paths [[width_m, flat xyz]]])
    as a spline edge (WK / DW). Wide frontage aprons (plazas) stay slabs, not splines."""
    nodes, edges = {}, []
    for k, row in enumerate(rows):
        for j, (width, flat) in enumerate(row[10] if len(row) > 10 else []):
            if width > 4.0 or len(flat) < 6:
                continue
            pts = [flat[i:i + 2] for i in range(0, len(flat) - 2, 3)]
            a, b = ("p", k, j, 0), ("p", k, j, 1)
            nodes[a], nodes[b] = list(pts[0]), list(pts[-1])
            edges.append([a, b, "DW" if width >= 3.0 else "WK", [list(p) for p in pts[1:-1]]])
    return nodes, edges


def paths_actor(city: dict, opts: dict | None, rows: list, ground, folder: str) -> dict:
    """The city's '<City> Paths' spline actor: its missing sidewalks plus every walkway and driveway."""
    cn, ce = city_graph(city, opts)
    sn, se = sidewalks(cn, ce)
    pn, pe = paths_graph(rows)
    return build_actor({**sn, **pn}, se + pe, ground, folder)


LOT_W_CM, LOT_D_CM = 1600.0, 3500.0  # a lot: 16 m frontage, up to 35 m deep (two back to back in a block)


def city_lots(city: dict, opts: dict | None = None) -> list:
    """The city's lots, cut from the blocks its streets enclose (parcels.lots): [[x, y, yaw, width_cm,
    depth_cm, front x, front y]]. Buildings fill these, so every one fronts a street square on."""
    from . import parcels
    nodes, edges = city_graph(city, opts)
    rnd = random.Random(f"lots:{city.get('seed', 1)}")
    return [[x, y, yaw, w, d, fx, fy] for x, y, yaw, w, d, _poly, fx, fy in
            parcels.lots(nodes, edges, HALF, LOT_W_CM, LOT_D_CM, rnd)]


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
    for e in edges:
        via = e[3] if len(e) > 3 else []
        chain = [ends[(id(e), e[0])]]
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
    for s in segs:
        st.retangent(by_i, s)
    st.drape(pts, segs, {p["i"]: p["z"] for p in pts}, iters=4, max_grade=glim)
    return {"points": pts, "segments": segs}


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
        keep += [(a - 7000, at(a - 7000) + [0.0, "BRR"]), (a - 3000, at(a - 3000) + [BRIDGE_LIFT_CM, "BR"]),
                 (b + 3000, at(b + 3000) + [BRIDGE_LIFT_CM, "BRR"]), (b + 7000, at(b + 7000) + [0.0, kind])]
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


def generate(cities: list, ground, folder: str, seed: int = 1, opts: dict | None = None, keep: list | None = None) -> dict:
    """{label: {points, segments}}: one '<City> Roads' actor per city and one 'Highways' actor.
    A highway ends 40 m outside its city's gate street, where the city's own stub road ends; there it
    meets the city through the chosen interchange (direct, a trumpet loop, a cloverleaf piece, or a
    stack: an elevated deck on ramps), and it bridges over every street it crosses on the way."""
    o = options(opts)
    graphs = [city_graph(c, o) for c in cities]
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
                    v[3] = "BR" if up > 0 and nxt_up > 0 else ("BRR" if up > 0 or nxt_up > 0 else o["highway"])
        hw_nodes[("hw", a, b, 0)], hw_nodes[("hw", a, b, 1)] = ends
        hw_edges.append([("hw", a, b, 0), ("hw", a, b, 1), o["highway"], via])
    for nodes, edges in graphs:  # a city street never runs into a keep zone
        gone = {n for n, p in nodes.items() if in_keep(keep, p[0], p[1], 2000)}
        edges[:] = [e for e in edges if e[0] not in gone and e[1] not in gone]
    out = {f"{c['name']} Roads": build_actor(n, e, ground, folder) for c, (n, e) in zip(cities, graphs)}
    if hw_edges:
        out["Highways"] = build_actor(hw_nodes, hw_edges, ground, folder, pieces)
    return out


def _demo() -> None:
    cities = place_cities(3, 3, ((-200000, -200000), (200000, 200000)))
    assert len(cities) == 3 and all(c["style"] in STYLES for c in cities)
    ter = terrain(3, 400000, 6000, cities, grid=64)
    for c in cities:
        z, z2 = st.sample_grid(ter, c["x"], c["y"]), st.sample_grid(ter, c["x"] + c["radius"] * 0.5, c["y"])
        assert abs(z - z2) < 50, "a city sits on a flat plateau"
    actors = generate(cities, lambda x, y: st.sample_grid(ter, x, y) or 0.0, "/Game/X/WorldGen/Roads", seed=3)
    assert set(actors) == {f"{c['name']} Roads" for c in cities} | {"Highways"}
    for label, a in actors.items():
        bad = st.problems(a["points"], a["segments"])
        assert not any(bad.values()), (label, {k: len(v) for k, v in bad.items()})
        back = st.parse(st.serialize(a["points"], a["segments"]))
        assert len(back["points"]) == len(a["points"]) and len(back["segments"]) == len(a["segments"])
    city = actors[f"{cities[0]['name']} Roads"]
    assert st.pending_nodes(city["points"], city["segments"]), "junctions wait for generated pieces"
    assert len(actors["Highways"]["segments"]) >= 2
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
    # graded roads: on a 20 % hill no ground-level street segment is steeper than its class allows
    hill = lambda x, y: x * 0.2
    ga = build_actor({0: [0, 0], 1: [10000, 0], 2: [20000, 0], 3: [30000, 0]},
                     [[0, 1, "2L"], [1, 2, "2L"], [2, 3, "2L"]], hill, "/Game/X")
    gb = {p["i"]: p for p in ga["points"]}
    for s in ga["segments"]:
        a, b = gb[s["a"]], gb[s["b"]]
        run = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
        if a["raise"] and b["raise"] and run > 0:
            assert abs(a["z"] - b["z"]) <= GRADE["2L"] * run + 2, (abs(a["z"] - b["z"]) / run)
    # SidewalkGenerator: a 6-lane boulevard gets a sidewalk spline on both sides; lot paths become splines
    sn, se = sidewalks({0: [0, 0], 1: [10000, 0]}, [[0, 1, "6L"]])
    assert len(se) == 2 and all(abs(abs(p[1]) - (HALF["6L"] + HALF["SW"])) < 1 for p in sn.values())
    rows = [[0, 0, 0, 0, 1, 7, 0, 12, 12, 0, [[1.6, [600, 0, 0, 1600, 0, 0]], [3.5, [600, -400, 0, 1000, -600, 0, 1600, -400, 0]],
                                            [18.0, [600, 0, 0, 1600, 0, 0]]], []]]
    pa = paths_actor({"x": 0, "y": 0, "radius": 1, "seed": 1}, None, rows, lambda x, y: 0.0, "/Game/X/WorldGen/Roads")
    meshes = {s["mesh"] for s in pa["segments"]}
    assert "SM_Path_Walkway_10m" in meshes and "SM_Path_Driveway_10m" in meshes, meshes
    # a street turning 90 degrees gets an elbow piece (square corner), a gentle bend stays a spline point
    el = build_actor({0: [0, 0], 1: [10000, 0], 2: [10000, 10000], 3: [20000, 10500]},
                     [[0, 1, "2L"], [1, 2, "2L"], [2, 3, "2L"]], lambda x, y: 0.0, "/Game/X")
    pieces_at = {(round(p["x"]), round(p["y"])) for p in el["points"] if p["mesh"] == JUNCTION}
    assert pieces_at == {(10000, 0), (10000, 10000)}, pieces_at
    assert st.pending_nodes(el["points"], el["segments"]), "elbows wait for a generated piece too"
    # rule: one city alone gets no highway and so no interchange
    solo = generate(cities[:1], lambda x, y: 0.0, "/Game/X/WorldGen/Roads", seed=3, opts={"interchange": "cloverleaf"})
    assert "Highways" not in solo, "no stand-alone highway or cloverleaf"
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
    assert {"6L", "1W", "DT"} <= kinds, kinds
    # a highway bridges over a street it crosses: ramp up, deck, ramp down; bridges never shape the ground
    via, n = overpass_via([[0, 0], [200000, 0]], [([100000, -5000], [100000, 5000])], "4L")
    assert n == 1 and [v[3] for v in via if len(v) > 3] == ["BRR", "BR", "BRR", "4L"] and max(v[2] for v in via) == BRIDGE_LIFT_CM
    br = build_actor({"a": [0, 0], "b": [200000, 0]}, [["a", "b", "4L", via]], lambda x, y: 0.0, "/Game/X/Roads")
    meshes = [s["mesh"] for s in br["segments"]]
    assert "SM_Road_Bridge_Straight20m" in meshes and meshes.count("SM_Road_Bridge_Ramp40m") == 2, meshes
    deck = next(s for s in br["segments"] if s["mesh"] == "SM_Road_Bridge_Straight20m")
    assert "bRaiseTerrain=False" in deck["lines"] and not deck["raise"]
    top = max(p["z"] for p in br["points"])
    assert abs(top - BRIDGE_LIFT_CM) < 1, top
    text = st.serialize(br["points"], br["segments"])
    assert "Elevated" not in text and "SM_Road_Bridge_Ramp40m" in text
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
        assert meshes.count("SM_Road_Bridge_Straight20m") >= 2, (kind, "a bridge at each end")
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
    t = terrain(3, 400000, 6000, lake_city, grid=48, opts={"sea_m": 50})
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
    print("citygen ok", {k: len(v["segments"]) for k, v in actors.items()})


if __name__ == "__main__":
    _demo()

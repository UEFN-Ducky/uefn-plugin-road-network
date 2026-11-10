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

ROAD_MESH = {"4L": "SM_Road_FourLane_Straight10m", "2S": "SM_Road_TwoLaneSidewalk_Straight10m",
             "2L": "SM_Road_TwoLane_Straight10m"}
HALF = {"4L": 1400.0, "2S": 875.0, "2L": 525.0}
STYLES = ("mixed", "grid", "organic")
JUNCTION = "SM_Junc_Gen"  # waits for Build pieces (st.pending_nodes picks it up)
MOUTH_CM = 1500.0
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
    "wobble": (0.28, 0.0, 0.45),       # how far organic streets drift (share of a block)
    "prune": (0.22, 0.0, 0.5),         # share of side streets removed in organic areas
    "downtown": (0.4, 0.1, 0.8),       # downtown ring, share of the radius
    "normal": (0.75, 0.2, 0.95),       # normal-city ring; housing beyond it
    "highway_curve": (0.08, 0.0, 0.3), # how far a highway bows out, share of its length
}
HIGHWAYS = ("4L", "2L")


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
    out["highway"] = given.get("highway") if given.get("highway") in HIGHWAYS else "4L"
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


def terrain(seed: int, size_cm: float, relief_cm: float, cities: list, grid: int = 160,
            center: tuple = (0.0, 0.0), opts: dict | None = None) -> dict:
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
            row.append(round(h, 1))
        rows.append(row)
    return {"min": lo, "max": hi, "grid": grid, "heights": rows, "sea": sea}


def heightmap_png(ter: dict, res: int = 1009) -> tuple:
    """16-bit greyscale PNG of the terrain for Landscape > Import, plus the import settings
    (Z scale 100: 1 unit = 100/128 cm, so +-256 m fits)."""
    lo, hi = ter["min"], ter["max"]
    raw = bytearray()
    for py in range(res):
        y = lo[1] + (hi[1] - lo[1]) * py / (res - 1)
        vals = []
        for px in range(res):
            x = lo[0] + (hi[0] - lo[0]) * px / (res - 1)
            z = st.sample_grid(ter, x, y) or 0.0
            vals.append(max(0, min(65535, int(round(32768 + z * 128 / 100)))))
        raw += b"\x00" + struct.pack(f">{res}H", *vals)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", res, res, 16, 0, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))
    scale = (hi[0] - lo[0]) / (res - 1)
    return png, {"resolution": f"{res} x {res}", "location": [round(lo[0]), round(lo[1]), 0],
                 "scale": [round(scale, 3), round(scale, 3), 100.0]}


# ------------------------------------------------------------------ cities
def place_cities(seed: int, count: int, bounds: tuple) -> list:
    """count cities inside bounds ((x0, y0), (x1, y1)), never overlapping each other; their size
    follows the map (8-14 % of its width, so a 6 km map gets 500-850 m cities)."""
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
            # wobble grows outward: core streets stay straight, suburbs curve (style can force either)
            if style == "grid":
                amount = 0.0
            elif style == "organic":
                amount = o["wobble"] * (0.6 if share <= down else 1.2)
            else:
                amount = 0.0 if share <= down else o["wobble"] * (0.5 if share <= normal else 1.3)
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
            axis = (ix == 0 == nb[0]) or (iy == 0 == nb[1])
            kind = "4L" if axis or share <= down else ("2L" if share > normal else "2S")
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
            if kind != "4L" and degree[a] > floor and degree[b] > floor and rnd.random() < chance:
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
def _point(i, x, y, z, yaw, w, mesh=""):
    lines = [f"Width={w:.1f}", "bRaiseTerrain=True", "bLowerTerrain=True", "SideFalloff=800.000000",
             "EndFalloff=1000.000000"] + ([] if mesh else ["SegmentMeshOffset=50.0"])
    return {"i": i, "x": x, "y": y, "z": z, "pitch": 0.0, "yaw": yaw, "roll": 0.0, "w": w, "side": 800.0,
            "mesh": mesh, "raise": True, "lines": lines}


def _segment(i, a, b, kind, folder):
    lines = ["bRaiseTerrain=True", "bLowerTerrain=True"]
    mesh = ROAD_MESH.get(kind, "")
    if mesh:
        lines.insert(0, f"SplineMeshes(0)=(Mesh=\"/Script/Engine.StaticMesh'{folder}/{mesh}.{mesh}'\",bScaleToWidth=False)")
    return {"i": i, "a": a, "b": b, "ta": 0.0, "tb": 0.0, "mesh": mesh, "raise": True, "lines": lines}


def build_actor(nodes: dict, edges: list, ground, folder: str) -> dict:
    """Street graph -> spline points and segments. A node with 3+ streets becomes a junction (a
    pending piece with a mouth point on every arm); edges may carry 'via' points (highways)."""
    adj: dict = {}
    for e in edges:
        adj.setdefault(e[0], []).append(e)
        adj.setdefault(e[1], []).append(e)
    pts, segs, ends = [], [], {}
    ids = iter(range(1 << 30))

    def angle(p, q):
        return math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))

    def first_step(e, n):  # the next position along e when leaving node n
        via = e[3] if len(e) > 3 else []
        if via:
            return via[0] if e[0] == n else via[-1]
        return nodes[e[1] if e[0] == n else e[0]]

    for n, arms in adj.items():
        x, y = nodes[n]
        if len(arms) >= 3:
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
        else:
            steps = [first_step(e, n) for e in arms]
            yaw = angle(steps[0], steps[1]) if len(steps) == 2 else angle((x, y), steps[0])
            p = _point(next(ids), x, y, ground(x, y), yaw, max(HALF[e[2]] for e in arms))
            pts.append(p)
            for e in arms:
                ends[(id(e), n)] = p["i"]
    for e in edges:
        via = e[3] if len(e) > 3 else []
        chain = [ends[(id(e), e[0])]]
        line = [nodes[e[0]]] + via + [nodes[e[1]]]
        for k, (x, y) in enumerate(via, start=1):
            p = _point(next(ids), x, y, ground(x, y), angle(line[k - 1], line[k + 1]), HALF[e[2]])
            pts.append(p)
            chain.append(p["i"])
        chain.append(ends[(id(e), e[1])])
        for a, b in zip(chain, chain[1:]):
            segs.append(_segment(next(ids), a, b, e[2], folder))
    by_i = {p["i"]: p for p in pts}
    for s in segs:
        st.retangent(by_i, s)
    st.drape(pts, segs, {p["i"]: p["z"] for p in pts}, iters=4)
    return {"points": pts, "segments": segs}


def generate(cities: list, ground, folder: str, seed: int = 1, opts: dict | None = None) -> dict:
    """{label: {points, segments}}: one '<City> Roads' actor per city and one 'Highways' actor.
    A highway ends 40 m outside its city's gate street, where the city's own stub road ends."""
    o = options(opts)
    graphs = [city_graph(c, o) for c in cities]
    rnd = random.Random(seed)
    hw_nodes, hw_edges = {}, []
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
        hw_nodes[("hw", a, b, 0)], hw_nodes[("hw", a, b, 1)] = ends
        hw_edges.append([("hw", a, b, 0), ("hw", a, b, 1), o["highway"], via])
    out = {f"{c['name']} Roads": build_actor(n, e, ground, folder) for c, (n, e) in zip(cities, graphs)}
    if hw_edges:
        out["Highways"] = build_actor(hw_nodes, hw_edges, ground, folder)
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
    # options: clamped, lakes flatten the lowlands, smaller blocks mean more streets, 2-lane highways
    o = options({"block_m": 9999, "sea_m": -5, "highway": "6L", "detail": 3.4})
    assert o["block_m"] == 250 and o["highway"] == "4L" and o["detail"] == 3
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
    print("citygen ok", {k: len(v["segments"]) for k, v in actors.items()})


if __name__ == "__main__":
    _demo()

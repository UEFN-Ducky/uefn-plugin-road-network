from __future__ import annotations

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
    if o["preset"] != "Classic":
        return _engine_terrain(seed, size_cm, relief_cm, cities, grid, center, o, keep, lakes)
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
    if o["mountain_m"]:
        base = _mountains(base, xs, ys, seed, size_cm, o["mountain_m"] * 100, o["mountain_cover"], cities,
                          None if sea is None else sea + shore)
    if o["island"] != "none":
        sea = 0.0 if sea is None else sea
        base = _islands(base, xs, ys, seed, size_cm, center, o["island"], sea, depth, shore)
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
    out = {"min": lo, "max": hi, "grid": grid, "heights": rows, "sea": sea}
    out["z_scale"] = z_scale(out)
    return out


ENGINE_MAPS = ("biome", "river", "lake", "build", "road_cost")  # engine maps a world keeps (flat j*n+i, grid size)
LAST_WORLD = None  # the last engine World made here, every map: the caller keeps it for debug views (never saved)


def _engine_terrain(seed, size_cm, relief_cm, cities, grid, center, o, keep, lakes) -> dict:
    """A preset world from the terrain engine (backend/terrain, metres) in terrain()'s cm shape. The engine levels
    the cities and keep zones itself; lakes drawn by hand are dug in after."""
    from ..terrain import generate
    half = size_cm / 2
    lo = [center[0] - half, center[1] - half]
    cfg = {**{k: v for k, v in o.items() if k != "detail"}, "seed": seed, "relief_m": relief_cm / 100.0}
    towns = [{"x": c["x"] / 100.0, "y": c["y"] / 100.0, "radius": c.get("radius", 0.0) / 100.0} for c in cities]
    # ponytail: a keep rectangle goes in as the circle round it; a rect zone in the engine if a long one matters
    held = [{"x": (k["x0"] + k["x1"]) / 200.0, "y": (k["y0"] + k["y1"]) / 200.0,
             "radius": math.hypot(k["x1"] - k["x0"], k["y1"] - k["y0"]) / 200.0, "z": k["z"] / 100.0}
            for k in keep or []]
    global LAST_WORLD
    world = LAST_WORLD = generate(cfg, grid, lo[0] / 100.0, lo[1] / 100.0, size_cm / 100.0, cities=towns, keep=held)
    out = world.to_citygen()
    out["maps"] = {k: [round(v, 3) for v in world.maps[k]] for k in ENGINE_MAPS if k in world.maps}
    # river polylines (cm) for the panel and wet_from, their widths beside them (a point's third number is its z)
    out["rivers"] = [[[round(x), round(y)] for x, y in r["pts"]] for r in world.maps.get("river_lines") or []]
    out["river_widths_cm"] = [round(r.get("width_cm", 0.0)) for r in world.maps.get("river_lines") or []]
    if not (lakes or keep):
        return out
    xs = [lo[0] + size_cm * (i + 0.5) / grid for i in range(grid)]
    ys = [lo[1] + size_cm * (j + 0.5) / grid for j in range(grid)]
    rows, out["sea"] = _dig_lakes(out["heights"], xs, ys, lakes or [], out["sea"], o["water_depth_m"] * 100, 400.0)
    blend = max(o["flatten_m"] * 100, 5000.0)
    for j, y in enumerate(ys):  # last, as in Classic: the engine's erosion and detail run after its own levelling
        for i, x in enumerate(xs):
            for k in keep or []:
                rows[j][i] += (k["z"] - rows[j][i]) * (1 - _smoothstep(0, blend, keep_gap(k, x, y)))
    out["heights"] = [[round(v, 1) for v in r] for r in rows]
    out.pop("z_scale")
    out["z_scale"] = z_scale(out)
    return out


MOUNTAIN_CREST_CM = 120000.0  # ponytail: ~1.2 km between crests, an option when someone wants other ranges
MOUNTAIN_CLEAR_CM = (60000.0, 250000.0)  # flat ground kept around towns and on the way between them: full, then blending


def _mountains(base: list, xs: list, ys: list, seed: int, size_cm: float, height: float, cover: float,
               cities: list, shore) -> list:
    """Mountain ranges raised on the ground in some places only, the rest of the map left as flat or rolling as it
    was. A slow noise picks where: exactly `cover` of the map, its highest share. Ridged noise shapes the crests up
    to `height` (cm), foothills where a range fades out. Each city and the way to its two nearest neighbours stays
    clear (a pass for its highway). Water (below `shore`) is never lifted out."""
    where = _fbm(seed + 101, 3, 0.5)
    crest = _fbm(seed + 202, 6, 0.5, 1.0)
    big = max(size_cm / 2.5, 150000.0)
    w = [[where(x / big, y / big) for x in xs] for y in ys]
    flat = sorted(v for r in w for v in r)
    th = flat[int((1.0 - cover) * (len(flat) - 1))]
    top = flat[-1] - th or 1.0
    c = [[crest(x / MOUNTAIN_CREST_CM, y / MOUNTAIN_CREST_CM) for x in xs] for y in ys]
    c_lo = min(min(r) for r in c)
    c_span = (max(max(r) for r in c) - c_lo) or 1.0
    segs = []
    for a in cities:
        near = sorted((b for b in cities if b is not a), key=lambda b: math.hypot(b["x"] - a["x"], b["y"] - a["y"]))
        segs += [((a["x"], a["y"]), (b["x"], b["y"])) for b in near[:2]]
    c0, c1 = MOUNTAIN_CLEAR_CM
    for j, y in enumerate(ys):
        for i, x in enumerate(xs):
            # the range: rises from its edge over a third of the way to its heart, so it has foothills
            m = _smoothstep(0.0, 0.35, (w[j][i] - th) / top)
            if m <= 0:
                continue
            for t in cities:
                m *= _smoothstep(c0, c1, math.hypot(x - t["x"], y - t["y"]) - t.get("radius", 0.0) * 1.2)
            for a, b in segs:
                m *= _smoothstep(c0, c1, _dist_seg((x, y), a, b))
            if shore is not None:
                m *= _smoothstep(shore, shore + 3000.0, base[j][i])
            if m > 0:
                t = (c[j][i] - c_lo) / c_span
                base[j][i] += m * height * (0.25 + 0.75 * t ** 1.5)
    return base


def z_scale(ter: dict) -> float:
    """The landscape import's Z scale: UEFN's 100 (+-256 m) unless the ground reaches further, then just enough
    (rounded up to 50). A sample is 32768 + z * 128 / Z scale (z in cm)."""
    if ter.get("z_scale"):
        return float(ter["z_scale"])
    peak = max((abs(v) for r in ter["heights"] for v in r), default=0.0)
    return max(100.0, math.ceil(peak * 1.02 * 128 / 32767 / 50) * 50.0)


def island_land(seed: int, size_cm: float, center: tuple, mode: str):
    """(x, y) -> 0 (sea) .. 1 (land) for the Island option, from noise alone: the land is shaped first and the
    cities are moved onto it (towns_on_land), never the coast drawn round the cities (Nov 18 2026: round blobs).
    'one': one big island with a wandering coast, sea all round the map's edge; 'many': an archipelago, the land
    where low-frequency noise is high, thinning out towards the edge."""
    coast = _fbm(seed + 101, 4, 0.5)
    half = size_cm / 2

    def land(x, y):
        u, v = (x - center[0]) / half, (y - center[1]) / half  # -1 .. 1 across the map
        r = math.hypot(u, v)
        if mode == "one":
            edge = 0.74 + (coast(u * 1.4 + 5.3, v * 1.4 + 5.3) - 0.5) * 1.2  # the coast's distance from the middle
            return _smoothstep(0.0, 0.1, edge - r)
        s = coast(u * 2.6 + 11.7, v * 2.6 + 11.7) - 0.5 - 0.45 * max(0.0, r - 0.45)
        return _smoothstep(-0.015, 0.03, s)
    return land


def towns_on_land(towns: list, land, bounds, gap: float = 0.0) -> list:
    """Each city whose circle is not on dry land (land(x, y) >= 0.5 at its middle and all round its edge) moves to
    the nearest spot that is, clear of the cities already settled; shrunk (to half at most) when no spot is big
    enough. A pinned city stays. Returns notes of what moved."""
    (x0, y0), (x1, y1) = bounds
    ring = [(0.0, 0.0)] + [(f * math.cos(k * math.pi / 6), f * math.sin(k * math.pi / 6)) for f in (0.55, 1.0) for k in range(12)]

    def dry(x, y, r):
        return (x0 + r <= x <= x1 - r and y0 + r <= y <= y1 - r) and all(land(x + dx * r, y + dy * r) >= 0.5 for dx, dy in ring)

    settled, notes = [], []
    for t in sorted(towns, key=lambda c: (not c.get("pinned"), -c["radius"])):
        clear = lambda x, y, r: all(math.hypot(x - o["x"], y - o["y"]) >= r + o["radius"] + gap for o in settled)
        if t.get("pinned") or (dry(t["x"], t["y"], t["radius"]) and clear(t["x"], t["y"], t["radius"])):
            settled.append(t)
            continue
        found = None
        for scale in (1.0, 0.85, 0.7, 0.5):
            r = t["radius"] * scale
            step = max(r / 2, 2000.0)
            spots = [(t["x"] + i * step, t["y"] + j * step) for i in range(-60, 61) for j in range(-60, 61)]
            for x, y in sorted(spots, key=lambda p: math.hypot(p[0] - t["x"], p[1] - t["y"])):
                if dry(x, y, r) and clear(x, y, r):
                    found = (x, y, r)
                    break
            if found:
                break
        if found:
            notes.append(f"{t.get('name')} moved {round(math.hypot(found[0] - t['x'], found[1] - t['y']) / 100)} m onto land"
                         + (f", {round(100 * found[2] / t['radius'])} % size" if found[2] < t["radius"] else ""))
            t["x"], t["y"], t["radius"] = found
        settled.append(t)
    return notes


def _islands(base: list, xs: list, ys: list, seed: int, size_cm: float, center: tuple, mode: str,
             sea: float, depth: float, shore: float) -> list:
    """The hills cut to islands (island_land): the island's own hills raised so its lowest ground is a beach over
    the sea, open sea outside, the coast sloping between."""
    land = island_land(seed, size_cm, center, mode)
    lo_h = min(min(r) for r in base)
    floor = sea - depth - shore  # the open sea's bed
    return [[round(floor + (sea + shore * 2 + (base[j][i] - lo_h) * 0.6 - floor) * land(x, y), 1)
             for i, x in enumerate(xs)] for j, y in enumerate(ys)]


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
        pts_, segs_ = st.straighten(a["points"], a["segments"], keep_z=label.endswith(("Freeways", "Highways")))
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
                half = max(half, float(s.get("half") or 0))  # (a Blender interchange's link: its whole square)
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
    """(i, j) -> the heightmap's 16-bit sample there (32768 = 0, 128 per Z scale cm: 128 a metre at 100), lots levelled in."""
    lo, hi = ter["min"], ter["max"]
    k = 128 / z_scale(ter)
    ground = graded(ter, pads or [], max(PAD_BLEND_CM, 1.5 * heightmap_px(ter, res)))
    return lambda i, j: max(0, min(65535, int(round(32768 + ground(lo[0] + (hi[0] - lo[0]) * i / (res - 1),
                                                                   lo[1] + (hi[1] - lo[1]) * j / (res - 1)) * k))))


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
    px, memo, k = _pixel(ter, res, pads), {}, z_scale(ter) / 128

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
        return ((a + (b - a) * tx) * (1 - ty) + (c + (d - c) * tx) * ty - 32768) * k
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
    (Z scale z_scale: 100 = 1 unit is 100/128 cm, so +-256 m; taller mountains get more). pads: lots levelled into it."""
    lo, hi = ter["min"], ter["max"]
    rows = rows or heightmap_rows(ter, res or HEIGHTMAP_RES, pads)
    res = len(rows)
    png = _grey_png(rows, 16)
    scale = (hi[0] - lo[0]) / (res - 1)
    # Landscape mode's New > Location is the landscape's centre, not its corner; 1009 = 16 components x 63 quads + 1
    return png, {"resolution": f"{res} x {res}", "location": [round((lo[0] + hi[0]) / 2), round((lo[1] + hi[1]) / 2), 0],
                 "scale": [round(scale, 3), round(scale, 3), z_scale(ter)],
                 # more than 16 components a side spans several World Partition regions (region size 16), and
                 # UEFN's import repeated one region's heights in each (the map came in as 4 copies of a quarter):
                 # 2017 is imported as 16 x 16 components of 2 x 2 sections instead, one region
                 "components": (f"{(res - 1) // 63} x {(res - 1) // 63}, 63 quads, 1 section" if (res - 1) // 63 <= 16
                                else f"{(res - 1) // 126} x {(res - 1) // 126}, 63 quads, 2x2 sections"),
                 "metres_per_pixel": round(scale / 100, 2),
                 "uefn_limit": f"{res * res:,} of {UEFN_MAX_SAMPLES:,} samples (2048 x 2048)"}



from __future__ import annotations

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
                        "style": AUTO_STYLES[len(out) % len(AUTO_STYLES)], "seed": rnd.randrange(1 << 30)})
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
    from .. import freeways
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
    from .. import freeways
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
    from .. import airport
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
    from .. import shipyard
    from .. import waterfront
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
    from .. import waterfront
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
    from .. import waterfront
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
    from .. import waterfront
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
    from .. import shipyard
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
    from .. import shipyard
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


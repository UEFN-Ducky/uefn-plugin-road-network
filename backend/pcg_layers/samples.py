"""Road and street samples, junction corners, planned sites' grounds and the sweeps that keep pieces off roads,
yards and buildings."""

from __future__ import annotations

import math

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
            z = round(ground(x, y) or 0)
            fx, fy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
            if kind == "field":  # the biggest real pitch that fits the field, at scale 1 (a stretched one is wrong)
                fit = fit_pitch(sx, sy)
                if fit:
                    grounds.setdefault(fit[0], []).append([x, y, z, round((yaw + fit[1]) % 360, 1), 1.0, 1.0, 1.0])
                continue
            if kind == "playground":  # its soft surface (below), and the swings, slide, ... on it at scale 1
                for pk, u, v, turn in play_layout(sx, sy):
                    grounds.setdefault(pk, []).append([round(x + fx * u - fy * v), round(y + fy * u + fx * v), z + 5,
                                                       round((yaw + turn) % 360, 1), 1.0, 1.0, 1.0])
            mesh, box, _tint = GROUNDS[kind]
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
           "bench": ("SM_Prop_Bench", [1.94, 0.67, 1.11], "fg-dim"),
           # football pitches at their real size (scale 1, never stretched: the lines and goals stay true) and
           # playground equipment; box_m is the whole piece (a pitch with its 2 m run-off all round)
           "pitch11": ("SM_Kit_Pitch11", [104, 68, 2.46], "mix:green:fg:0.25"),
           "pitch7": ("SM_Kit_Pitch7", [64, 44, 2.02], "mix:green:fg:0.25"),
           "pitch5": ("SM_Kit_Pitch5", [44, 24, 2.02], "mix:green:fg:0.25"),
           "pg_swings": ("SM_Kit_PG_Swings", [4.1, 1.3, 2.6], "mix:red:fg:0.4"),
           "pg_slide": ("SM_Kit_PG_Slide", [4.2, 1.3, 2.6], "mix:red:fg:0.4"),
           "pg_climber": ("SM_Kit_PG_Climber", [2.5, 2.5, 2.0], "mix:green:fg:0.4"),
           "pg_sandbox": ("SM_Kit_PG_Sandbox", [3.0, 3.0, 0.25], "mix:amber:fg:0.3"),
           "pg_seesaw": ("SM_Kit_PG_SeeSaw", [3.6, 0.4, 0.9], "mix:amber:fg:0.4"),
           "pg_roundabout": ("SM_Kit_PG_Roundabout", [2.0, 2.0, 0.3], "mix:red:fg:0.4")}
GROUND_NAMES = {"apron": "Service aprons", "field": "Sports fields", "playground": "Playgrounds", "pool": "Pools",
                "yard": "Utility yards", "pond": "Ponds and tanks", "tower": "Towers", "telecom": "Telecom masts",
                "path": "Park paths", "bench": "Park benches",
                "pitch11": "Football pitches (full size)", "pitch7": "Football pitches (7-a-side)",
                "pitch5": "Football pitches (5-a-side)", "pg_swings": "Playground swings", "pg_slide": "Playground slides",
                "pg_climber": "Playground climbing frames", "pg_sandbox": "Playground sandpits",
                "pg_seesaw": "Playground seesaws", "pg_roundabout": "Playground roundabouts"}
PITCH_FITS = (("pitch11", 10400, 6800), ("pitch7", 6400, 4400), ("pitch5", 4400, 2400))  # biggest first, with run-off
# what a playground holds, in order of priority: (kind, box min x, max x, min y, max y) cm about the piece's pivot
PLAY_KIT = (("pg_swings", -205, 205, -65, 65), ("pg_slide", -85, 335, -65, 65), ("pg_climber", -124, 124, -124, 124),
            ("pg_sandbox", -150, 150, -150, 150), ("pg_seesaw", -180, 180, -20, 20), ("pg_roundabout", -100, 100, -100, 100))
PLAY_GAP_CM = 150.0  # clear space round every piece (and in from the playground's edge)


def fit_pitch(sx_m: float, sy_m: float):
    """The biggest real football pitch that fits a field area (sx along its yaw, sy across, metres), and the
    quarter turn that fits it: (kind, 0 | 90), or None when not even a 5-a-side pitch fits. Never stretched."""
    a, b = sx_m * 100, sy_m * 100
    for kind, L, W in PITCH_FITS:
        if L <= a and W <= b:
            return kind, 0
        if L <= b and W <= a:
            return kind, 90
    return None


def play_layout(sx_m: float, sy_m: float) -> list:
    """Playground equipment packed into a playground (sx along its yaw, sy across, metres) in shelves along its
    long side, each piece at scale 1 with PLAY_GAP_CM clear round it: [(kind, u, v, turn)] about the centre."""
    turn = 0 if sx_m >= sy_m else 90
    L, W = max(sx_m, sy_m) * 100 - 2 * PLAY_GAP_CM, min(sx_m, sy_m) * 100 - 2 * PLAY_GAP_CM
    out, x, y, shelf = [], 0.0, 0.0, 0.0
    for kind, x0, x1, y0, y1 in PLAY_KIT:
        w, d = x1 - x0, y1 - y0
        if x + w > L:  # this shelf is full: the next one
            x, y, shelf = 0.0, y + shelf + PLAY_GAP_CM, 0.0
        if x + w > L or y + d > W:
            continue  # does not fit this playground
        a, b = -L / 2 + x - x0, -W / 2 + y - y0  # the piece's pivot, its box at (x, y) in the shelf
        u, v = (a, b) if turn == 0 else (-b, a)
        out.append((kind, round(u), round(v), turn))
        x, shelf = x + w + PLAY_GAP_CM, max(shelf, d)
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


STACKED = ("Container stacks",)  # site pieces that stand on a yard, not on a road: kept off every road too
YARDS = ("Port yards",)  # paved yards no building may stand on (whatever placed the building: a city lot, a site)


def fit_yards(placed: dict, samples: list) -> dict:
    """Container yards planned before the streets (the Valley's) were known can lie across them. Trimming one to
    the clear part still left a pad over the junction (Valley 2_1, Nov 17 2026), so a yard any road surface
    (surface_samples) reaches goes whole, with its stacks: clear_yards then leaves its lot's building standing.
    Returns {"removed": n} or {}."""
    gone = 0
    for name in YARDS:
        layer = placed.get(name) or {}
        keep = []
        for p in layer.get("points") or []:
            tx, ty = math.cos(math.radians(p[3])), math.sin(math.radians(p[3]))
            hl, hd = p[4] * 50, p[5] * 50

            def on(x, y, slack=0.0):
                return abs((x - p[0]) * tx + (y - p[1]) * ty) <= hl + slack and abs((y - p[1]) * tx - (x - p[0]) * ty) <= hd + slack
            if not any(on(x, y, h) for x, y, h in samples):
                keep.append(p)
                continue
            gone += 1
            for s_name in STACKED:
                st = placed.get(s_name)
                if st:
                    st["points"] = [s for s in st.get("points") or [] if not on(s[0], s[1])]
                    st["shown"] = st["count"] = len(st["points"])
        if gone:
            layer["points"] = keep
            layer["shown"] = layer["count"] = len(keep)
    return {"removed": gone} if gone else {}


EDIT_REACH_CM = 500.0  # an edit takes the piece standing nearest its point, if one stands this close


def nearest(placed: dict, x: float, y: float, layer: str = "", reach: float = EDIT_REACH_CM) -> tuple:
    """(layer name, point) of the piece standing nearest (x, y): of that layer, or of any; (None, None) if none is
    within reach cm."""
    best, d = (None, None), reach
    for name, lay in ([(layer, placed.get(layer) or {})] if layer else placed.items()):
        for p in lay.get("points") or []:
            if (dd := math.hypot(p[0] - x, p[1] - y)) <= d:
                best, d = (name, p), dd
    return best


def apply_edits(placed: dict, edits: list, ground=None) -> list:
    """The person's own hand on the city, applied after every rule so each rebuild keeps it: each edit
    {"op": "delete" | "move" | "copy", "layer", "x", "y", "reach", "every", "to": [x, y], "yaw"} takes the piece of
    its layer nearest (x, y), within reach cm (5 m), and deletes it (every=true: every piece of that layer, or of
    any, within reach), moves (and turns) it, or puts a copy of it at `to`; on the ground there unless its layer
    keeps its own level. In order, so a later edit finds a moved piece where it went. Returns the edits that found
    nothing (a regenerate moved or dropped their piece)."""
    missed = []
    for e in edits:
        reach = float(e.get("reach") or EDIT_REACH_CM)
        if e["op"] == "delete" and e.get("every"):
            hit = 0
            for name, lay in ([(e["layer"], placed.get(e["layer"]) or {})] if e.get("layer") else placed.items()):
                keep = [q for q in lay.get("points") or [] if math.hypot(q[0] - e["x"], q[1] - e["y"]) > reach]
                if len(keep) != len(lay.get("points") or []):
                    hit += len(lay["points"]) - len(keep)
                    lay["count"] = max(len(keep), int(lay.get("count", 0)) - (len(lay["points"]) - len(keep)))
                    lay["points"], lay["shown"] = keep, len(keep)
            if not hit:
                missed.append(e)
            continue
        name, p = nearest(placed, e["x"], e["y"], e.get("layer") or "", reach)
        if p is None:
            missed.append(e)
            continue
        lay = placed[name]
        if e["op"] == "delete":
            lay["points"] = [q for q in lay["points"] if q is not p]
            lay["shown"] = len(lay["points"])
            lay["count"] = max(len(lay["points"]), int(lay.get("count", 1)) - 1)
            continue
        if e["op"] == "copy":
            p = list(p)
            lay["points"].append(p)
            lay["shown"], lay["count"] = len(lay["points"]), int(lay.get("count", 0)) + 1
        p[0], p[1] = round(e["to"][0]), round(e["to"][1])
        if e.get("yaw") is not None:
            p[3] = float(e["yaw"])
        if ground and not lay.get("keep_z"):
            p[2] = round(ground(p[0], p[1]))
    return missed


def clear_yards(placed: dict) -> dict:
    """No building on a container yard. The port claims its land from the street grid's lots, but buildings placed
    later (planned sites, infill) knew nothing of it: a 24 m warehouse stood among the stacks (Valley 2_1, Nov 17
    2026). Every building whose footprint reaches into a yard goes. Returns {layer: removed}."""
    yards = [(p[0], p[1], math.cos(math.radians(p[3])), math.sin(math.radians(p[3])), p[4] * 50, p[5] * 50)
             for name in YARDS for p in (placed.get(name) or {}).get("points") or []]
    gone: dict = {}
    if not yards:
        return gone
    for name, layer in placed.items():
        if layer.get("kind") != "buildings":
            continue
        keep = []
        for p in layer.get("points") or []:
            r = max(p[7], p[8]) * 50 if len(p) > 8 else 0.0
            if not any(abs((p[0] - cx) * tx + (p[1] - cy) * ty) < hl + r and abs((p[1] - cy) * tx - (p[0] - cx) * ty) < hd + r
                       for cx, cy, tx, ty, hl, hd in yards):
                keep.append(p)
        lost = len(layer.get("points") or []) - len(keep)
        if lost:
            gone[name] = lost
            layer["points"] = keep
            layer["shown"] = len(keep)
            layer["count"] = max(len(keep), int(layer.get("count", len(keep))) - lost)
    return gone


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
        stacked = name in STACKED
        if not stacked and (layer.get("kind") in ("buildings", "site", "walls") or layer.get("role") in ("bridge", "sidewalk_corner")):
            continue
        natural = layer.get("role") in NATURAL or layer.get("kind") in SCATTERED
        slack = 650.0 if stacked else 50.0 if natural else -100.0  # (a 12 m stack: its half length clear of the road)
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
    # each building in every 40 m cell it can reach into: a tree looks in its own cell only, and a tree with no
    # building there, most of a forest, at nothing. (Cells as wide as the widest building were 500 m once an
    # airport's apron was in: every tree near a city tested thousands of buildings.)
    cell, grid = 4000.0, {}
    for x, y, yaw, hd, hw in rows:
        f, r = math.radians(yaw), math.hypot(hd, hw)
        row = (x, y, math.cos(f), math.sin(f), hd, hw)
        for i in range(int((x - r) // cell), int((x + r) // cell) + 1):
            for j in range(int((y - r) // cell), int((y + r) // cell) + 1):
                grid.setdefault((i, j), []).append(row)
    for name, layer in placed.items():
        if not (layer.get("role") in NATURAL or layer.get("kind") in SCATTERED):
            continue
        keep = []
        for p in layer.get("points") or []:
            here = grid.get((int(p[0] // cell), int(p[1] // cell)))
            if not here or not any(
                    abs((p[0] - x) * c + (p[1] - y) * s) < hd and abs(-(p[0] - x) * s + (p[1] - y) * c) < hw
                    for x, y, c, s, hd, hw in here):
                keep.append(p)
        lost = len(layer.get("points") or []) - len(keep)
        if lost:
            gone[name] = lost
            layer["points"] = keep
            layer["shown"] = len(keep)
            layer["count"] = max(len(keep), int(layer.get("count", len(keep))) - lost)
    return gone


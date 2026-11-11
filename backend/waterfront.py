"""Waterfront & marine development: water is a first-class part of the city plan, like roads and terrain.

WaterfrontGenerator
  ShorelineAnalyzer (`analyze`): the water line S(t) of the terrain grid (marching squares at the water level),
      resampled every 20 m, with per sample: tangent T = S'/|S'|, water normal N = (-T_y, T_x) (flipped to
      point at the water), signed curvature, shelter (share of rays from the water that hit land within
      400 m: bays, lakes and rivers are sheltered, open coast is not), water depth 30 m and 80 m out, water
      width along N, land slope, flood exposure (land within 1 m of the water 50 m inland) - and every
      waterbody (connected wet cells) typed ocean / lake / river by size, edge contact and shape.
  WaterfrontPlanner (`plan`): every 100 m of a city's shoreline gets one development type from depth, slope,
      shelter, the waterbody and the district: natural, beach, residential, commercial, marina, industrial,
      public. Neighbouring stretches of one type join into segments.
  Generators per segment type (all data, no meshes; the street kit / PCG builds them):
      commercial  - concrete quay seawall, 8 m promenade, a pier every 300 m, restaurants facing the water,
                    a ferry terminal on the longest stretch of a big waterbody
      marina      - MarinaGenerator: entrance (+ rock breakwater on open water), main pier, floating dock
                    branches, boat slips (with boats), gangway, boat launch, marina building and parking,
                    floating homes on the last branch when sheltered
      beach       - sand, a boardwalk on piles, a resort hotel with pools and a beach club, a long pier
      residential - a shared community marina on long stretches (compact, not one dock per house), else
                    private docks; stilt houses on pilings along a walkway in shallow water (fishing village)
      industrial  - PortAndHarborGenerator: quay wall, container cranes, container stacks on a yard, warehouses
      public      - waterfront park: promenade, fishing pier, the natural edge kept
      natural     - left natural: wetlands on flat shallow shores, nothing elsewhere (no seawall everywhere)
  WaterfrontConnectivityValidator (`validate`): piers start on land and end in water, never reach past a third
      of the water's width (navigation), piles reach the bed, land buildings are reached from a street,
      structures keep clear of each other.

Foundations: ground (land buildings), pilings (piers, boardwalks, stilt houses: piles from the bed to the
deck, deck above the flood level), floating (floating docks and homes: Z = Z_water + freeboard), quay (seawall).
Rows are [x, y, z, yaw, sx, sy, sz, kind] (z = the piece's bottom, yaw = its length direction, metres-scaled
for unit pieces), land buildings are lot rows [x, y, yaw, w, d, fx, fy, role, True].
"""

from __future__ import annotations

import math

STEP = 2000.0  # shoreline sample spacing (cm)
CHUNK = 5  # samples per classified stretch (100 m)
FLOOD_CM = 100.0
DECK_ABOVE = 150.0  # fixed pier / boardwalk deck above the water
STILT_FLOOR = 350.0  # stilt house floor above the water (flood elevation)
FREEBOARD = 40.0  # floating dock / home deck above the water
PIER_W = 400.0
SLIP_W, SLIP_L = 450.0, 1200.0
NAV_SHARE = 0.3  # a pier never reaches past this share of the water's width
SETBACK = 2500.0  # public waterfront: no private lot this close to the water line
# pieces: kind -> (mesh, size at scale 1 (m), preview tint, layer name)
KINDS = {
    "deck": ("SM_Kit_DockDeck", [1, 1, 0.3], "mix:amber:fg-dim:0.55", "Piers and boardwalks"),
    "float": ("SM_Kit_FloatDock", [1, 1, 0.5], "mix:fg-dim:bg:0.15", "Floating docks"),
    "pile": ("SM_Kit_Piling", [0.3, 0.3, 1], "mix:amber:bg:0.6", "Pilings"),
    "slip": ("SM_Kit_BoatSlip", [12, 4.5, 0.05], "fg", "Boat slips"),
    "boat": ("SM_Kit_Boat", [9, 3, 2], "mix:fg:blue:0.15", "Boats"),
    "seawall": ("SM_Kit_Seawall", [1, 0.6, 1], "mix:fg-dim:bg:0.25", "Seawalls and quays"),
    "riprap": ("SM_Kit_Riprap", [1, 1, 1], "fg-dim", "Breakwaters"),
    "sand": ("SM_Kit_Sand", [1, 1, 0.1], "mix:amber:fg:0.6", "Beaches"),
    "wetland": ("SM_Kit_Wetland", [1, 1, 0.6], "mix:green:amber:0.35", "Wetlands"),
    "promenade": ("SM_Kit_PathSlab", [1, 1, 0.08], "mix:fg:bg:0.25", "Promenades"),
    "crane": ("SM_Kit_PortCrane", [30, 20, 45], "mix:red:fg-dim:0.4", "Port cranes"),
    "containers": ("SM_Kit_ContainerStack", [12.2, 2.5, 7.8], "mix:blue:red:0.5", "Container stacks"),
    "yard": ("SM_Kit_UtilityYard", [1, 1, 0.05], "mix:fg-dim:bg:0.5", "Port yards"),
    "launch": ("SM_Kit_BoatLaunch", [1, 1, 0.2], "mix:fg-dim:bg:0.3", "Boat launches"),
    "stilt_house": ("SM_Kit_StiltHouse", [10, 8, 6], "mix:fg-dim:amber:0.45", "Stilt houses"),
    "float_home": ("SM_Kit_FloatHome", [10, 7, 5], "mix:fg:amber:0.3", "Floating homes"),
    "pylon": ("SM_Kit_BridgePylon", [6, 6, 100], "fg-dim", "Bridge towers"),
}
TYPES = ("natural", "beach", "residential", "commercial", "marina", "industrial", "public")


# ------------------------------------------------------------------ ShorelineAnalyzer
def _cell(ter):
    n = int(ter["grid"])
    lo, hi = ter["min"], ter["max"]
    return n, lo, (hi[0] - lo[0]) / n, (hi[1] - lo[1]) / n


def _contours(ter, sea):
    """Marching squares on the grid's cell centres at the water level -> polylines [(x, y)]."""
    n, lo, cx, cy = _cell(ter)
    h = ter["heights"]
    P = lambda i, j: (lo[0] + (i + 0.5) * cx, lo[1] + (j + 0.5) * cy)
    segs = []

    def edge(a, b, ha, hb):
        t = (sea - ha) / ((hb - ha) or 1e-9)
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    for j in range(n - 1):
        for i in range(n - 1):
            v = [h[j][i], h[j][i + 1], h[j + 1][i + 1], h[j + 1][i]]
            if None in v:
                continue
            p = [P(i, j), P(i + 1, j), P(i + 1, j + 1), P(i, j + 1)]
            pts = []
            for k in range(4):
                a, b = k, (k + 1) % 4
                if (v[a] < sea) != (v[b] < sea):
                    pts.append(edge(p[a], p[b], v[a], v[b]))
            if len(pts) == 2:
                segs.append(tuple(pts))
            elif len(pts) == 4:  # saddle: pair them in order
                segs += [(pts[0], pts[1]), (pts[2], pts[3])]
    # chain the segments into polylines
    key = lambda p: (round(p[0], 0), round(p[1], 0))
    ends: dict = {}
    for k, (a, b) in enumerate(segs):
        ends.setdefault(key(a), []).append((k, 0))
        ends.setdefault(key(b), []).append((k, 1))
    used = [False] * len(segs)
    lines = []
    for k in range(len(segs)):
        if used[k]:
            continue
        used[k] = True
        line = [segs[k][0], segs[k][1]]
        for forward in (True, False):
            while True:
                tip = line[-1] if forward else line[0]
                nxt = next(((m, e) for m, e in ends.get(key(tip), ()) if not used[m]), None)
                if not nxt:
                    break
                m, e = nxt
                used[m] = True
                p = segs[m][1 - e]
                if forward:
                    line.append(p)
                else:
                    line.insert(0, p)
        lines.append(line)
    return lines


def _resample(line, step=STEP):
    out = [line[0]]
    carry = 0.0
    for p, q in zip(line, line[1:]):
        L = math.dist(p, q)
        d = step - carry
        while d <= L:
            t = d / L
            out.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
            d += step
        carry = (carry + L) % step if L else carry
        carry = L - (d - step) if L else carry
    if math.dist(out[-1], line[-1]) > step * 0.3:
        out.append(line[-1])
    for _ in range(2):  # smooth the grid's staircase
        out = [out[0]] + [((a[0] + 2 * b[0] + c[0]) / 4, (a[1] + 2 * b[1] + c[1]) / 4)
                          for a, b, c in zip(out, out[1:], out[2:])] + [out[-1]]
    return out


def bodies(ter, sea) -> tuple:
    """Connected wet cells -> (label grid, {id: {type, area_m2, width_m, cells}})."""
    n, lo, cx, cy = _cell(ter)
    h = ter["heights"]
    lab = [[-1] * n for _ in range(n)]
    info = {}
    nid = 0
    for j in range(n):
        for i in range(n):
            if lab[j][i] >= 0 or h[j][i] is None or h[j][i] >= sea:
                continue
            todo, cells, edge, border = [(i, j)], 0, 0, False
            lab[j][i] = nid
            while todo:
                a, b = todo.pop()
                cells += 1
                if a in (0, n - 1) or b in (0, n - 1):
                    border = True
                for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    x, y = a + da, b + db
                    if not (0 <= x < n and 0 <= y < n):
                        continue
                    if h[y][x] is not None and h[y][x] < sea:
                        if lab[y][x] < 0:
                            lab[y][x] = nid
                            todo.append((x, y))
                    else:
                        edge += 1
            area = cells * cx * cy / 1e4
            perim = max(1.0, edge * cx / 100)
            width = 2 * area / perim
            if cells < 6:  # a puddle in the grid, not a waterbody: nothing is planned on it
                nid += 1
                continue
            if border and cells > n * n * 0.15:
                kind = "ocean"
            elif width < 250 and perim > 6 * width:
                kind = "river"
            else:
                kind = "lake"
            info[nid] = {"id": nid, "type": kind, "area_m2": round(area), "width_m": round(width), "cells": cells}
            nid += 1
    return lab, info


def analyze(ter: dict) -> dict:
    """ShorelineAnalyzer: waterbodies and shorelines (samples with geometry and site properties)."""
    from . import splines_text as st
    sea = (ter or {}).get("sea")
    if sea is None or not (ter or {}).get("heights"):
        return {"sea": None, "bodies": [], "shores": []}
    ground = lambda x, y: st.sample_grid(ter, x, y)
    wet = lambda x, y: (ground(x, y) if ground(x, y) is not None else sea + 1) < sea
    lab, info = bodies(ter, sea)
    n, lo, cx, cy = _cell(ter)

    def body_at(x, y):
        i, j = int((x - lo[0]) / cx), int((y - lo[1]) / cy)
        return lab[j][i] if 0 <= i < n and 0 <= j < n else -1
    shores = []
    for line in _contours(ter, sea):
        pts = _resample(line)
        if len(pts) < 4:
            continue
        samples = []
        for k, p in enumerate(pts):
            a, b = pts[max(0, k - 1)], pts[min(len(pts) - 1, k + 1)]
            tx, ty = b[0] - a[0], b[1] - a[1]
            L = math.hypot(tx, ty) or 1.0
            tx, ty = tx / L, ty / L
            nx, ny = -ty, tx
            if not wet(p[0] + nx * 1500, p[1] + ny * 1500) and wet(p[0] - nx * 1500, p[1] - ny * 1500):
                nx, ny = -nx, -ny
            # signed curvature: turning per length (+ when the shore bends round the water: a bay)
            if 0 < k < len(pts) - 1:
                ax, ay = p[0] - a[0], p[1] - a[1]
                bx, by = b[0] - p[0], b[1] - p[1]
                turn = math.atan2(ax * by - ay * bx, ax * bx + ay * by)
                curv = turn / (STEP / 100) * (1 if (bx - ax) * nx + (by - ay) * ny < 0 else -1)
            else:
                curv = 0.0
            # shelter: rays from a point 60 m out over the water that meet land within 400 m
            ox, oy = p[0] + nx * 6000, p[1] + ny * 6000
            hits = 0
            for r in range(12):
                ang = r * math.pi / 6
                dx, dy = math.cos(ang), math.sin(ang)
                if any(not wet(ox + dx * d, oy + dy * d) for d in range(5000, 40001, 5000)):
                    hits += 1
            width = 0.0
            for d in range(1000, 300001, 1000):
                if not wet(p[0] + nx * d, p[1] + ny * d):
                    break
                width = d
            gz = lambda d: ground(p[0] + nx * d, p[1] + ny * d)
            land = gz(-3000)
            inland = gz(-5000)
            d30, d80 = gz(3000), gz(8000)
            samples.append({"x": round(p[0]), "y": round(p[1]), "tx": round(tx, 4), "ty": round(ty, 4),
                            "nx": round(nx, 4), "ny": round(ny, 4), "curv": round(curv, 4),
                            "shelter": round(hits / 12, 2), "width_m": round(width / 100),
                            "depth30_m": round((sea - d30) / 100, 1) if d30 is not None else 0.0,
                            "depth80_m": round((sea - d80) / 100, 1) if d80 is not None else 0.0,
                            "slope": round(((land if land is not None else sea) - sea) / 3000, 3),
                            "flood": bool(inland is not None and inland - sea < FLOOD_CM)})
        b = body_at(samples[len(samples) // 2]["x"] + samples[len(samples) // 2]["nx"] * 3000,
                    samples[len(samples) // 2]["y"] + samples[len(samples) // 2]["ny"] * 3000)
        if b not in info and len(samples) < 10:
            continue  # the rim of a puddle
        shores.append({"id": len(shores), "body": b, "type": info.get(b, {}).get("type", "lake"),
                       "length_m": round(len(samples) * STEP / 100), "samples": samples})
    return {"sea": sea, "bodies": list(info.values()), "shores": shores}


# ------------------------------------------------------------------ WaterfrontPlanner
def classify(s: dict, water_type: str, ring: str, industrial: bool, pop: int) -> str:
    """One stretch's development type from its site and the district (the planner's rules)."""
    gentle, steep = s["slope"] < 0.06, s["slope"] > 0.25
    deep, shallow = s["depth80_m"] >= 4.0, s["depth30_m"] < 2.0
    sheltered = s["shelter"] >= 0.5 or water_type in ("lake", "river")
    if ring == "outside":
        return "wetland" if gentle and shallow else "natural"
    if industrial and deep and water_type in ("ocean", "river", "lake") and pop >= 8000:
        return "industrial"
    if steep:
        return "natural"
    if water_type == "ocean" and gentle and shallow and not sheltered:
        return "beach"
    if ring == "core":
        return "commercial"
    if sheltered and s["depth80_m"] >= 2.5 and s["width_m"] >= 120:
        return "marina"
    if ring == "housing":
        return "residential"
    return "public"


class _Out:
    def __init__(self):
        self.rows: list = []
        self.lots: list = []
        self.devs: list = []
        self.checks: list = []  # (what, ok, detail)

    def row(self, x, y, z, yaw, sx, sy, sz, kind):
        self.rows.append([round(x), round(y), round(z), round(yaw, 1), round(sx, 2), round(sy, 2), round(sz, 2), kind])


def _yaw(dx, dy):
    return math.degrees(math.atan2(dy, dx))


def _piles(out, ground, x0, y0, dx, dy, length, half, deck_z, step=600.0):
    """Two rows of piles under a deck from (x0, y0) along (dx, dy), down to the bed."""
    nx, ny = -dy, dx
    for k in range(int(length // step) + 1):
        for side in (-1, 1):
            px, py = x0 + dx * k * step + nx * side * half, y0 + dy * k * step + ny * side * half
            bed = ground(px, py)
            bed = deck_z - 300 if bed is None else bed
            if bed < deck_z - 20:
                out.row(px, py, bed, 0, 1, 1, (deck_z - bed) / 100, "pile")


def pier(out, ground, sea, s, length, width=PIER_W, z_top=None, why="pier"):
    """A fixed pier on piles straight out along the water normal."""
    nav = max(2000.0, s["width_m"] * 100 * NAV_SHARE)
    length = min(length, nav)
    z = (sea + DECK_ABOVE) if z_top is None else z_top
    x0, y0 = s["x"], s["y"]
    cx, cy = x0 + s["nx"] * length / 2, y0 + s["ny"] * length / 2
    out.row(cx, cy, z - 30, _yaw(s["nx"], s["ny"]), length / 100, width / 100, 1, "deck")
    _piles(out, ground, x0, y0, s["nx"], s["ny"], length, width / 2 - 30, z)
    out.checks.append(("pier", length <= nav + 1, f"{why}: {round(length / 100)} m of {s['width_m']} m water"))
    return length


def marina(out, ground, sea, s, water_type, sheltered, slips_wanted=60):
    """MarinaGenerator: main pier, floating branches with slips and boats, gangway, launch, breakwater."""
    nx, ny, tx, ty = s["nx"], s["ny"], s["tx"], s["ty"]
    L = min(14000.0, s["width_m"] * 100 * NAV_SHARE)
    if L < 4000:
        return 0
    x0, y0 = s["x"] + nx * 800, s["y"] + ny * 800  # the gangway lands the pier past the shallows
    out.row(s["x"] + nx * 400, s["y"] + ny * 400, sea + DECK_ABOVE - 30, _yaw(nx, ny), 8, 2.5, 1, "deck")  # gangway
    out.row(x0 + nx * L / 2, y0 + ny * L / 2, sea + FREEBOARD - 50, _yaw(nx, ny), L / 100, 3, 1, "float")  # main pier
    slips, k, branch = 0, 0, 2000.0
    while branch < L - 500 and slips < slips_wanted:
        bx, by = x0 + nx * branch, y0 + ny * branch
        for side in (-1, 1):
            arm = 3600.0
            out.row(bx + tx * side * (arm / 2 + 150), by + ty * side * (arm / 2 + 150), sea + FREEBOARD - 50,
                    _yaw(tx, ty), arm / 100, 2, 1, "float")
            for m in range(int(arm // SLIP_W)):
                along = 150 + (m + 0.5) * SLIP_W
                for off in (-1, 1):  # slips both sides of the branch
                    sx = bx + tx * side * along + nx * off * (100 + SLIP_L / 2)
                    sy = by + ty * side * along + ny * off * (100 + SLIP_L / 2)
                    out.row(sx, sy, sea, _yaw(nx, ny), 1, 1, 1, "slip")
                    slips += 1
                    if (slips * 7) % 10 < 7:  # ~70 % of slips taken
                        out.row(sx, sy, sea - 40, _yaw(nx, ny), 1, 1, 1, "boat")
        branch += 2 * SLIP_L + 400
        k += 1
    # floating homes on the outer branch side when the water is calm
    if sheltered:
        for m in range(4):
            hx = x0 + nx * (L - 600) + tx * (m - 1.5) * 1400
            hy = y0 + ny * (L - 600) + ty * (m - 1.5) * 1400
            out.row(hx, hy, sea + FREEBOARD - 50, _yaw(tx, ty), 12, 9, 1, "float")
            out.row(hx, hy, sea + FREEBOARD, _yaw(tx, ty), 1, 1, 1, "float_home")
    elif water_type == "ocean":  # open water: a rock breakwater shelters the entrance
        for m in range(9):
            a = m / 8
            bx = x0 + nx * (L + 1500) + tx * (a - 0.5) * 9000
            by = y0 + ny * (L + 1500) + ty * (a - 0.5) * 9000
            bed = ground(bx, by)
            bed = sea - 400 if bed is None else bed
            out.row(bx, by, bed, _yaw(tx, ty), 11, 5, (sea + 200 - bed) / 100, "riprap")
    # boat launch beside it
    lx, ly = s["x"] + tx * 3500, s["y"] + ty * 3500
    out.row(lx + nx * 500, ly + ny * 500, sea - 100, _yaw(nx, ny), 15, 5, 1, "launch")
    out.checks.append(("marina", L <= s["width_m"] * 100 * NAV_SHARE + 1, f"marina pier {round(L / 100)} m, {slips} slips"))
    return slips


def plan(city: dict, analysis: dict, lots: list, ring_of, industrial, ground, nodes: dict | None = None,
         population: int = 0) -> dict:
    """WaterfrontPlanner + generators for one city: classified segments, structures (rows), land buildings
    (lot rows), the lots kept, and the validator report."""
    sea = analysis.get("sea")
    out = _Out()
    if sea is None:
        return {"lots": lots, "rows": [], "segments": [], "developments": [], "report": None}
    R = city["radius"]
    near = lambda s: math.hypot(s["x"] - city["x"], s["y"] - city["y"]) < R * 1.25
    segments = []
    for sh in analysis["shores"]:
        S = sh["samples"]
        if not any(near(s) for s in S):
            continue
        for k in range(0, len(S), CHUNK):
            part = S[k:k + CHUNK]
            mid = part[len(part) // 2]
            inside = math.hypot(mid["x"] - city["x"], mid["y"] - city["y"]) < R * 1.05
            ring = ring_of(mid["x"], mid["y"]) if inside else "outside"
            kind = classify(mid, sh["type"], ring, bool(industrial and industrial(mid["x"], mid["y"])), population)
            if segments and segments[-1]["shore"] == sh["id"] and segments[-1]["type"] == kind:
                segments[-1]["samples"] += part
            else:
                segments.append({"shore": sh["id"], "water": sh["type"], "type": kind, "samples": list(part)})
    for seg in segments:
        seg["length_m"] = round(len(seg["samples"]) * STEP / 100)
    # no private lot on the public water's edge; residential keeps its yards but not in the water margin
    public = [s for seg in segments if seg["type"] in ("commercial", "beach", "public", "industrial", "marina")
              for s in seg["samples"]]
    allp = [s for seg in segments for s in seg["samples"]]

    def grid_of(pts):  # 50 m cells: a lot only looks at the shore samples near it
        g: dict = {}
        for s in pts:
            g.setdefault((int(s["x"] // 5000), int(s["y"] // 5000)), []).append(s)
        return g
    gpub, gall = grid_of(public), grid_of(allp)

    def too_close(x, y, g, d):
        r = int(d // 5000) + 1
        cx, cy = int(x // 5000), int(y // 5000)
        return any(math.hypot(s["x"] - x, s["y"] - y) < d
                   for i in range(cx - r, cx + r + 1) for j in range(cy - r, cy + r + 1) for s in g.get((i, j), ()))
    keep = [r for r in lots if not too_close(r[0], r[1], gpub, SETBACK + max(r[3], r[4]) / 2)
            and not too_close(r[0], r[1], gall, 1000 + max(r[3], r[4]) / 2)]
    devs = []

    def building(s, back, w, d, role, why):
        """A land building facing the water, `back` cm inland from the water line."""
        x, y = s["x"] - s["nx"] * back, s["y"] - s["ny"] * back
        yaw = _yaw(s["nx"], s["ny"])  # faces the water
        out.lots.append([round(x), round(y), round(yaw, 1), round(w), round(d), round(s["x"]), round(s["y"]), role, True])
        devs.append({"type": why, "x": round(x), "y": round(y)})
        if nodes:
            far = min(math.hypot(p[0] - x, p[1] - y) for p in nodes.values())
            out.checks.append(("access", far <= 15000, f"{why}: street {round(far / 100)} m away"))

    def along(seg, every):
        S = seg["samples"]
        return S[1:-1:max(1, int(every // STEP))] or S[len(S) // 2:len(S) // 2 + 1]

    marinas = 0
    ferry_done = False
    for seg in sorted(segments, key=lambda g: -g["length_m"]):
        S, t = seg["samples"], seg["type"]
        z_land = lambda s: ground(s["x"] - s["nx"] * 1000, s["y"] - s["ny"] * 1000) or sea
        if t in ("commercial", "industrial"):  # quay seawall: only where the city meets the water hard
            for a, b in zip(S, S[1:]):
                L = math.dist((a["x"], a["y"]), (b["x"], b["y"]))
                bed = min(ground(a["x"] + a["nx"] * 500, a["y"] + a["ny"] * 500) or sea - 300, sea - 300)
                out.row((a["x"] + b["x"]) / 2, (a["y"] + b["y"]) / 2, bed, _yaw(b["x"] - a["x"], b["y"] - a["y"]),
                        L / 100, 1, (max(z_land(a), sea + 100) - bed) / 100, "seawall")
        if t in ("commercial", "public", "beach"):  # promenade / boardwalk along the water
            for a, b in zip(S, S[1:]):
                L = math.dist((a["x"], a["y"]), (b["x"], b["y"]))
                mx, my = (a["x"] + b["x"]) / 2 - a["nx"] * 600, (a["y"] + b["y"]) / 2 - a["ny"] * 600
                if t == "beach":  # boardwalk on short piles behind the sand
                    mx, my = (a["x"] + b["x"]) / 2 - a["nx"] * 3500, (a["y"] + b["y"]) / 2 - a["ny"] * 3500
                    zt = (ground(mx, my) or sea) + 60
                    out.row(mx, my, zt - 30, _yaw(b["x"] - a["x"], b["y"] - a["y"]), L / 100, 4, 1, "deck")
                else:
                    out.row(mx, my, z_land(a), _yaw(b["x"] - a["x"], b["y"] - a["y"]), L / 100, 8, 1, "promenade")
        if t == "commercial":
            for s in along(seg, 30000):
                pier(out, ground, sea, s, 6000, 600, why="public pier")
            for s in along(seg, 6000)[:12]:
                building(s, 2200, 1600, 1400, "shop", "waterfront restaurant")
            devs.append({"type": "waterfront shopping district", "length_m": seg["length_m"]})
            if not ferry_done and population >= 15000 and seg["water"] in ("ocean", "river", "lake"):
                s = S[len(S) // 2]
                building(s, 3500, 4000, 2500, "transit", "ferry terminal")
                L = pier(out, ground, sea, s, 5000, 800, why="ferry pier")
                out.row(s["x"] + s["nx"] * (L + 600), s["y"] + s["ny"] * (L + 600), sea + FREEBOARD - 50,
                        _yaw(s["tx"], s["ty"]), 30, 6, 1, "float")
                ferry_done = True
        elif t == "marina" and marinas < max(1, population // 25000):
            s = max(S, key=lambda q: q["depth80_m"] + q["shelter"] * 5)
            n_slips = marina(out, ground, sea, s, seg["water"], s["shelter"] >= 0.5 or seg["water"] != "ocean")
            if n_slips:
                marinas += 1
                building(s, 2500, 2400, 1600, "shop", "marina building (office, boat rental, cafe)")
                devs.append({"type": "marina village", "slips": n_slips, "x": s["x"], "y": s["y"]})
        elif t == "beach":
            for a in S[::2]:  # sand from 25 m inland to 15 m out
                out.row(a["x"] - a["nx"] * 500, a["y"] - a["ny"] * 500, sea - 20, _yaw(a["tx"], a["ty"]),
                        STEP * 2 / 100, 40, 1, "sand")
            if seg["length_m"] >= 200:
                s = S[len(S) // 2]
                building(s, 7000, 3600, 2400, "office", "beach resort hotel")
                out.row(s["x"] - s["nx"] * 5000 + s["tx"] * 2500, s["y"] - s["ny"] * 5000 + s["ty"] * 2500,
                        ground(s["x"], s["y"]) or sea, _yaw(s["tx"], s["ty"]), 25, 12, 1, "deck")  # pool deck
                if seg["length_m"] >= 400:
                    pier(out, ground, sea, s, 15000, 800, why="boardwalk pier")
                    building(S[len(S) // 4], 5000, 1400, 1200, "shop", "beach club")
                devs.append({"type": "beach resort", "length_m": seg["length_m"]})
        elif t == "residential":
            if seg["length_m"] >= 400 and marinas < 2 and seg["water"] != "ocean":
                s = max(S, key=lambda q: q["depth80_m"])
                n_slips = marina(out, ground, sea, s, seg["water"], True, slips_wanted=30)
                if n_slips:  # one compact community marina instead of a dock behind every house
                    marinas += 1
                    devs.append({"type": "community marina", "slips": n_slips})
            elif seg["water"] == "ocean" and all(q["depth30_m"] < 2.5 for q in S[:3]):
                s = S[len(S) // 2]  # fishing village: a walkway out with stilt houses both sides
                L = pier(out, ground, sea, s, 9000, 300, why="stilt walkway")
                for m in range(1, int(L // 2200)):
                    for side in (-1, 1):
                        hx = s["x"] + s["nx"] * m * 2200 + s["tx"] * side * 900
                        hy = s["y"] + s["ny"] * m * 2200 + s["ty"] * side * 900
                        out.row(hx, hy, sea + STILT_FLOOR, _yaw(s["nx"], s["ny"]), 1, 1, 1, "stilt_house")
                        _piles(out, ground, hx - s["nx"] * 400, hy - s["ny"] * 400, s["nx"], s["ny"], 800, 350,
                               sea + STILT_FLOOR, 400)
                devs.append({"type": "fishing village (stilt houses)"})
            else:
                for s in along(seg, 6000):  # a few private docks, never one per house
                    pier(out, ground, sea, s, 1500, 250, why="private dock")
                devs.append({"type": "private docks", "count": len(along(seg, 6000))})
        elif t == "industrial":
            for s in along(seg, 6000):  # container cranes straddle the quay edge
                out.row(s["x"] - s["nx"] * 800, s["y"] - s["ny"] * 800, z_land(s), _yaw(s["tx"], s["ty"]), 1, 1, 1, "crane")
            s = S[len(S) // 2]
            yx, yy = s["x"] - s["nx"] * 6000, s["y"] - s["ny"] * 6000
            out.row(yx, yy, z_land(s), _yaw(s["tx"], s["ty"]), min(300, seg["length_m"]) * 0.9, 80, 1, "yard")
            for a in range(-3, 4):
                for b in range(-2, 3):
                    out.row(yx + s["tx"] * a * 1400 + s["nx"] * b * 1000, yy + s["ty"] * a * 1400 + s["ny"] * b * 1000,
                            z_land(s), _yaw(s["tx"], s["ty"]), 1, 1, 1, "containers")
            building(s, 12000, 6000, 3000, "warehouse", "port warehouse")
            devs.append({"type": "container port", "length_m": seg["length_m"]})
        elif t == "public":
            s = S[len(S) // 2]
            pier(out, ground, sea, s, 4000, 300, why="fishing pier")
            devs.append({"type": "waterfront park", "length_m": seg["length_m"]})
        elif t == "wetland":
            for a in S[::2]:
                out.row(a["x"], a["y"], sea - 40, _yaw(a["tx"], a["ty"]), STEP * 2 / 100, 20, 1, "wetland")
    # claimed land: no lot under a new waterfront building
    for b in out.lots:
        r = max(b[3], b[4]) / 2 + 800
        keep = [q for q in keep if math.hypot(q[0] - b[0], q[1] - b[1]) > r]
    report = validate(out, segments)
    counts: dict = {}
    for seg in segments:
        counts[seg["type"]] = counts.get(seg["type"], 0) + seg["length_m"]
    return {"lots": keep + out.lots, "rows": out.rows,
            "segments": [{"type": g["type"], "water": g["water"], "length_m": g["length_m"],
                          "from": [g["samples"][0]["x"], g["samples"][0]["y"]],
                          "to": [g["samples"][-1]["x"], g["samples"][-1]["y"]]} for g in segments],
            "developments": devs, "shoreline_m": counts, "report": report}


def validate(out: _Out, segments: list) -> dict:
    """WaterfrontConnectivityValidator: navigation, access, foundations, collisions."""
    bad = [d for what, ok, d in out.checks if not ok]
    piles = [r for r in out.rows if r[7] == "pile"]
    if any(r[6] <= 0 for r in piles):
        bad.append("a pile does not reach the bed")
    tall = [r for r in out.rows if r[7] in ("deck", "float", "stilt_house", "float_home")]
    clash = 0
    for k, a in enumerate(out.lots):  # two waterfront buildings on one spot
        for b in out.lots[k + 1:]:
            if math.hypot(a[0] - b[0], a[1] - b[1]) < (min(a[3], a[4]) + min(b[3], b[4])) / 2:
                clash += 1
    if clash:
        bad.append(f"{clash} waterfront buildings overlap")
    return {"ok": not bad, "problems": bad[:30], "checked": len(out.checks), "structures": len(tall),
            "piles": len(piles)}


def layers(cities: list, extra: list | None = None) -> dict:
    """Built-in PCG site layers for every city's waterfront rows (+ world rows such as bridge towers)."""
    by: dict = {}
    for c in cities:
        for r in ((c.get("private") or {}).get("water") or []):
            by.setdefault(r[7], []).append(r[:7])
    for r in extra or []:
        by.setdefault(r[7], []).append(r[:7])
    out = {}
    for kind, rows in by.items():
        mesh, box, tint, name = KINDS[kind]
        out[name] = {"kind": "site", "role": "site", "mesh": mesh, "box_m": box, "tint": tint, "count": len(rows),
                     "shown": len(rows), "points": rows, "meshes": [], "row": "x, y, z, yaw, sx, sy, sz"}
    return out


def bridge_kind(span_cm: float, kind: str) -> tuple:
    """BridgeGenerator: (type, deck lift cm) from the span and the road; navigation needs clearance."""
    if span_cm <= 4000:
        return ("footbridge" if kind in ("SW", "WK") else "canal arch"), 300.0
    if span_cm <= 15000:
        return "beam bridge", 750.0
    if span_cm <= 60000:
        return "girder bridge (navigable)", 1500.0
    if span_cm <= 150000:
        return "cable-stayed bridge", 3000.0
    return "suspension bridge", 4500.0


def towers(bridges: list, ground, sea) -> list:
    """Pylons for cable-stayed and suspension bridges, from the bed to 60 / 90 m over the deck:
    rows [x, y, z, yaw, 1, 1, sz, 'pylon'] (the pylon mesh is 100 m tall at scale 1)."""
    rows = []
    for b in bridges:
        if b["type"] not in ("cable-stayed bridge", "suspension bridge"):
            continue
        h = 6000 if b["type"].startswith("cable") else 9000
        for x, y in b["towers"]:
            bed = ground(x, y)
            bed = (sea or 0.0) - 500 if bed is None else bed
            top = max(bed, (sea or bed)) + 150 + b["deck_z_rel"] + h
            rows.append([round(x), round(y), round(bed), b["yaw"], 1, 1, round((top - bed) / 10000, 3), "pylon"])
    return rows


def _demo() -> None:
    # a 6 km terrain: ocean to the east (x > 1.5 km, shallow beach shelf in the south, deep in the north), a
    # sheltered lake in the west, land rising inland
    n, size = 120, 600000
    lo = [-size / 2, -size / 2]
    heights = []
    for j in range(n):
        row = []
        for i in range(n):
            x, y = lo[0] + (i + 0.5) * size / n, lo[1] + (j + 0.5) * size / n
            if x > 150000:  # ocean: deep north, shallow shelf south
                h = -300 - (x - 150000) * 0.08 if y > 0 else -(x - 150000) * 0.008
            else:
                h = 100 + (150000 - x) * 0.004
            if math.hypot(x + 20000, y - 60000) < 40000:  # lake
                h = -1200
            row.append(round(h, 1))
        heights.append(row)
    ter = {"min": lo, "max": [size / 2, size / 2], "grid": n, "heights": heights, "sea": 0.0}
    a = analyze(ter)
    kinds = sorted({b["type"] for b in a["bodies"]})
    assert "ocean" in kinds and "lake" in kinds, kinds
    assert a["shores"], "shorelines found"
    lake_shore = [sh for sh in a["shores"] if sh["type"] == "lake"]
    assert lake_shore and min(s["shelter"] for s in lake_shore[0]["samples"]) >= 0.5, "a lake is sheltered"
    for sh in a["shores"]:  # every normal points at the water
        for s in sh["samples"][1:-1]:
            from . import splines_text as st
            z = st.sample_grid(ter, s["x"] + s["nx"] * 2500, s["y"] + s["ny"] * 2500)
            assert z is None or z < 50, ("normal points to land", s)
    from . import splines_text as st
    ground = lambda x, y: st.sample_grid(ter, x, y)
    city = {"name": "Port", "x": 120000, "y": 0, "radius": 160000}
    ring = lambda x, y: "core" if math.hypot(x - 120000, y) < 60000 else "normal" if math.hypot(x - 120000, y) < 110000 else "housing"
    industrial = lambda x, y: y > 90000
    lots = [[x, y, 0.0, 1600, 3500, x + 1750, y, "", False] for x in range(-80000, 150000, 4000) for y in range(-150000, 150001, 4000)]
    nodes = {k: [x, y] for k, (x, y) in enumerate((x, y) for x in range(-80000, 150000, 10000) for y in range(-150000, 150001, 10000))}
    got = plan(city, a, lots, ring, industrial, ground, nodes, 30000)
    types = {g["type"] for g in got["segments"]}
    print("  segments", got["shoreline_m"], "developments", sorted({d["type"] for d in got["developments"]}))
    assert {"commercial", "beach", "industrial"} <= types, types
    assert {"marina", "residential"} & types, types
    kinds = {r[7] for r in got["rows"]}
    assert {"seawall", "pile", "deck", "sand"} <= kinds, kinds
    assert "crane" in kinds and "containers" in kinds, "a container port on deep industrial water"
    assert {"float", "slip", "boat"} <= kinds, "a marina with slips"
    assert got["report"]["ok"], got["report"]
    # no seawall where the shore is natural or a beach
    walls = [r for r in got["rows"] if r[7] == "seawall"]
    beach = [g for g in got["segments"] if g["type"] == "beach"]
    assert walls and beach
    # floating pieces sit on the water, pilings reach the bed, decks above the flood line
    assert all(abs(r[2] - (FREEBOARD - 50)) < 2 for r in got["rows"] if r[7] == "float")
    assert all(r[6] > 0 for r in got["rows"] if r[7] == "pile")
    # every pier leaves from land and stays inside its navigation share
    assert not [p for p in got["report"]["problems"] if "m water" in p]
    # buildings face the water and replaced the lots under them
    wb = [r for r in got["lots"] if r[8] is True]
    assert wb and len(got["lots"]) < len(lots) + len(wb)
    t, lift = bridge_kind(90000, "4L")
    assert t == "cable-stayed bridge" and lift == 3000.0
    print("waterfront ok", len(got["rows"]), "pieces", len(wb), "waterfront buildings")


if __name__ == "__main__":
    _demo()

"""Marine industry: shipyards as dedicated industrial districts, planned before the city's streets.

MarineIndustrialGenerator
  ShipyardPlanner (`sites`): on deep industrial shoreline (waterfront.segments_for), longest first, pick the yard
      type from the water, the stretch length and the city's size, then reserve a campus: u along the shore,
      v inland. City streets inside a campus are removed (citygen.city_graph); the yard has its own roads.
        commercial  - >= 600 m of deep water, 40k+ people: graving docks, panel line, fabrication, assembly yard,
                      outfitting quay
        naval       - a second big stretch on the ocean, 80k+ people: the same, behind a secure fence, secure berths
        offshore    - >= 400 m on the ocean, 30k+: huge hardstand, fabrication halls, jackets / monopiles, quay
        repair      - >= 250 m of deep water: floating dry docks, repair berths, paint and machine shops
        yacht       - on a sheltered marina stretch, 15k+: slipways, yacht halls, boat lift, finishing hall
        fishing     - >= 150 m: fishing docks, processing plant, cold storage, fish market
        recycling   - a shallow industrial stretch: ships beached for dismantling, scrap yard
  PlatformPlanner (`settle`): every yard is ONE level rectangle at the water, the way real yards are built: made
      ground with a straight quay, paved from edge to edge. It reaches out over the water (never past OUT_SHARE of
      the water's width) and inland only to where the land has risen CUT_OK_CM above it, so a yard under a hill is
      mostly made ground in front of the hill, not a pit dug into it. `shaped` is the ground it leaves (level
      platform, a basin dredged along the quay, the land beside it cut back to a bank), cut into the terrain by
      citygen.cut_yards. `paving` is the platform as PCG slabs: everything else of the yard stands on them, and
      so does any layer a person draws there or sets to the shipyards.
  Production flow (`build`), inland to the water - the order real yards are laid out in (EPA shipbuilding
  layouts, UFC 4-213-10 graving docks):
        administration + gate + parking (back, away from heavy traffic)
        -> warehouses + steel storage -> fabrication / panel line halls -> assembly yard (tower cranes)
        -> docks / slipways (goliath gantry cranes spanning them, keel blocks, a ship under construction)
        -> outfitting quay (portal cranes on rails, ships alongside) -> tug station, launch basin in the water
      Open hardstand stays open: yards are mostly working area, not buildings.
  DryDockGenerator: each graving dock is a straight '<City> Docks' spline (DK) from inland to 30 m past the quay,
      laid at the dock floor so the landscape spline digs the basin; a caisson gate closes its mouth.
  MarineIndustrialValidator (`validate`): docks open onto water, the launch basin in front of each dock mouth is
      clear, the production flow runs inland -> water, administration sits outside the heavy zone, floating docks
      stay within the navigation share of the water.

Rows: waterfront rows [x, y, z, yaw, sx, sy, sz, kind]; halls: lot rows [x, y, yaw, w, d, fx, fy, role, True].
"""

from __future__ import annotations

import math
import random

DOCK_DEPTH_CM = 1200.0  # graving dock floor below the ground (and the water)
DOCK_W = 4000.0  # dock width (the DK spline's width)
QUAY = 3000.0  # outfitting quay band along the water
PLATFORM_CM = 250.0  # every yard is one level platform this far above the water
DREDGE_CM = 900.0  # the water is kept this deep along the quay ...
BASIN_CM = 9000.0  # ... for this far out from it
BANK = 0.6  # the land beside the platform comes back up to its own height no steeper than this
BANK_MAX_CM = 8000.0  # ... and the yard keeps this much land for that bank, at most
CUT_OK_CM = 1200.0  # the platform reaches inland to where the land stands this far above it
OUT_SHARE = 0.3  # ... and out over the water by no more than this share of the water's width
SLAB_CM = 10000.0  # the platform's paving comes in slabs no bigger than this
WALL_DEEP_CM = 2000.0  # a quay wall goes down to the bed, or this far under the water where the bed is deeper
SLIP_TOP = 3000.0  # a slipway starts this far behind the quay
PIER_CM = (9000.0, 2600.0)  # a pier: this far out from the quay (the dredged basin's width), this wide ...
PIER_EVERY_CM = 12000.0  # ... one about every 120 m of open quay, a ship moored along it


def piers(site: dict) -> list:
    """Where a settled yard's piers leave the quay (u of each): along its open quay (past the graving docks'
    stretch), never at a dock's mouth or under the floating dry dock; none for a yacht yard or a breaker's beach."""
    L, kind = site["L"], site["type"]
    if site.get("z") is None or kind in ("recycling", "yacht"):
        return []
    u0 = -L / 2 + (L * 0.6 if site.get("docks") else 0)
    taken = [(d["u"], DOCK_W / 2 + 1500) for d in site.get("docks") or []]
    if kind in ("repair", "naval"):
        taken.append((L * 0.25, 13000.0))  # (the floating dry dock moored off the quay)
    out, u = [], u0 + PIER_EVERY_CM / 2
    while u <= L / 2 - PIER_CM[1] - 1500:
        if not any(abs(u - c) < half + PIER_CM[1] / 2 for c, half in taken):
            out.append(u)
        u += PIER_EVERY_CM
    return out
# the yards with a production line (steel store -> fabrication -> assembly -> dock or quay): how deep their
# fabrication halls are, and the depth the rows need one behind the other
FAB_DEPTH = {"commercial": 5000.0, "naval": 5000.0, "offshore": 7000.0, "repair": 4500.0}
NEED_CM = 26000.0
TYPES = ("commercial", "naval", "offshore", "repair", "yacht", "fishing", "recycling")


def _frame(s):
    """A campus frame at shore sample s: u along the shore, v inland."""
    return {"x": s["x"], "y": s["y"], "ux": s["tx"], "uy": s["ty"], "vx": -s["nx"], "vy": -s["ny"]}


def _w(f, u, v):
    return (f["x"] + f["ux"] * u + f["vx"] * v, f["y"] + f["uy"] * u + f["vy"] * v)


def _yaw(dx, dy):
    return round(math.degrees(math.atan2(dy, dx)), 1)


def rows_of(kind: str, D: float) -> dict | None:
    """Where a production yard's rows lie, inland from the quay (cm), one behind the other with a 10 m lane
    between: the back row (administration, gate, tanks, substation, fire station), the store row (warehouse, steel
    stacks, machine shop), the fabrication halls, and `front`: how far the docks and the assembly yard may reach.
    None for a yard with no production line (yacht, fishing, recycling)."""
    if kind not in FAB_DEPTH:
        return None
    back = D - 2500
    store = back - 1250 - 1000 - 2500
    fab = store - 2500 - 1000 - FAB_DEPTH[kind] / 2
    # (25 m from a dock's head to the halls: the dock's own walls and crane rails take 20 of them)
    return {"back": back, "store": store, "fab": fab, "front": fab - FAB_DEPTH[kind] / 2 - 2500}


def dock_len(kind: str, D: float) -> float:
    """A graving dock: up to 300 m, and never past the lane in front of the fabrication halls."""
    rows = rows_of(kind, D)
    return round(max(6000.0, min(30000.0, rows["front"] if rows else D * 0.62)))


def uv(site: dict, x: float, y: float) -> tuple:
    """(u along the quay, v inland from it) of a world point."""
    f = site["frame"]
    dx, dy = x - f["x"], y - f["y"]
    return dx * f["ux"] + dy * f["uy"], dx * f["vx"] + dy * f["vy"]


def inside(site: dict, x: float, y: float, margin: float = 0.0) -> bool:
    u, v = uv(site, x, y)
    return abs(u) <= site["L"] / 2 + margin and -margin - 2000 <= v <= site["D"] + margin


def reserved(site: dict, x: float, y: float, margin: float = 0.0) -> bool:
    """On the yard's land: its platform and the bank that brings the land beside it down to it."""
    return inside(site, x, y, margin + site.get("bank", 0.0))


def outline(site: dict) -> list:
    """The platform's four corners."""
    f, L, D = site["frame"], site["L"], site["D"]
    return [[round(c) for c in _w(f, u, v)] for u, v in ((-L / 2, 0), (L / 2, 0), (L / 2, D), (-L / 2, D))]


def sites(city: dict, segments: list, population: int, opts: dict | None = None) -> list:
    """ShipyardPlanner: the city's marine industrial campuses from its classified shoreline segments."""
    if (opts or {}).get("shipyards", "auto") == "none":
        return []
    out = []
    used = []

    def free(s):
        return all(math.hypot(s["x"] - o["frame"]["x"], s["y"] - o["frame"]["y"]) > (o["L"] / 2 + 20000) for o in used)

    for seg in sorted(segments, key=lambda g: -g["length_m"]):
        S = seg["samples"]
        mid = S[len(S) // 2]
        if not free(mid):
            continue
        L = min(seg["length_m"], 900) * 100
        deep = max(q["depth80_m"] for q in S)
        kind = None
        if seg["type"] == "industrial":
            have = {o["type"] for o in out}
            if L >= 60000 and deep >= 6 and population >= 40000 and "commercial" not in have:
                kind = "commercial"
            elif L >= 60000 and deep >= 6 and population >= 80000 and seg["water"] == "ocean" and "naval" not in have:
                kind = "naval"
            elif L >= 40000 and seg["water"] == "ocean" and population >= 30000 and "offshore" not in have:
                kind = "offshore"
            elif L >= 25000 and deep >= 5:
                kind = "repair"
            elif L >= 15000 and deep >= 3:
                kind = "fishing"
            elif L >= 20000:
                kind = "recycling"
        elif seg["type"] == "marina" and population >= 15000 and "yacht" not in {o["type"] for o in out} and L >= 20000:
            kind, L = "yacht", min(L, 30000)
        if not kind:
            continue
        D = {"commercial": 0.6, "naval": 0.6, "offshore": 0.55, "repair": 0.5, "yacht": 0.6, "fishing": 0.5,
             "recycling": 0.4}[kind] * L
        D = max(NEED_CM if kind in FAB_DEPTH else 15000.0, min(45000.0, D))
        site = {"type": kind, "frame": _frame(mid), "L": L, "D": D, "water": seg["water"],
                "width_m": mid["width_m"], "depth_m": deep}
        # graving docks: one per 300 m of commercial / naval frontage (max 3), one for a long repair yard
        n_docks = {"commercial": min(3, int(L // 30000)), "naval": min(2, int(L // 30000)),
                   "repair": 1 if L >= 50000 else 0}.get(kind, 0)
        site["docks"] = []
        for k in range(n_docks):
            u = -L / 2 + (k + 0.5) * (L * 0.6 / max(1, n_docks))  # docks on the first 60 % of the frontage
            site["docks"].append({"u": round(u), "len": dock_len(kind, D)})
        out.append(site)
        used.append(site)
    return out


def settle(site: dict, ground, sea: float) -> dict:
    """PlatformPlanner: fit the campus to the ground as ONE level rectangle at the water (site['z']). The quay
    line moves out over the water and the back edge stops where the land has risen CUT_OK_CM above the platform
    (see the module's notes); the frame moves with it, so v = 0 is the quay and v = D the back edge. Docks are as
    long as the new depth allows; site['bank'] is the land the yard keeps beside it for the bank down to it."""
    f, L, D0 = site["frame"], site["L"], site["D"]
    P = sea + PLATFORM_CM
    us = [-L / 2 + L * k / 16 for k in range(17)]

    def at(u, v, frame=f):
        g = ground(*_w(frame, u, v))
        return g if g is not None else sea - 1000.0

    def middle(v):
        zs = sorted(at(u, v) for u in us)
        return zs[len(zs) // 2]
    back, v = D0, 1000.0
    while v < D0:
        if middle(v) > P + CUT_OK_CM:
            back = v
            break
        v += 1000.0
    out = max(0.0, min(OUT_SHARE * float(site.get("width_m") or 0) * 100, D0 * 0.5, D0 - back))
    D = max(back + out, 15000.0, D0 * 0.6, NEED_CM if site["type"] in FAB_DEPTH else 0.0)
    ox, oy = _w(f, 0, -out)
    f = site["frame"] = {**f, "x": round(ox), "y": round(oy)}
    site["D"], site["z"], site["out"] = round(D), round(P, 1), round(out)
    for d in site.get("docks") or []:
        d["len"] = dock_len(site["type"], D)

    def high(w):  # the land that far out from the platform's back and sides, above the platform
        return max([at(u, D + w, f) for u in us] + [at(s * (L / 2 + w), D * k / 8, f) for s in (-1, 1) for k in range(9)]) - P
    bank = 0.0  # as wide as the land needs to come down to the platform at BANK
    while bank < BANK_MAX_CM and high(bank) > BANK * bank + 50:
        bank += 500.0
    site["bank"] = round(bank)
    return site


def shaped(site: dict, u: float, v: float, z: float, sea: float, lip: float = 0.0) -> float:
    """The ground at (u, v) of a settled yard once the yard is built, from what it was (z): level on the platform,
    with each graving dock a straight-walled basin DOCK_DEPTH_CM deep in it; in front of the quay a basin dredged
    DREDGE_CM deep for BASIN_CM (the bed rises again beyond it); on the other sides the land as it was, cut back
    to a bank no steeper than BANK where it stood higher.
    `lip`: the ground ends this far behind the quay line, under the paving. The quay wall is the edge a player
    sees; ground cut a landscape pixel at a time would stick out past it."""
    L, D, P = site["L"], site["D"], site["z"]
    du, front = abs(u) - L / 2, lip - v
    if du <= 0 and front <= 0 and v <= D:
        if any(abs(u - d["u"]) <= DOCK_W / 2 and v <= d["len"] for d in site.get("docks") or ()):
            return P - DOCK_DEPTH_CM
        return P
    if front > 0 and front >= du:
        return min(z, sea - DREDGE_CM + max(0.0, front - BASIN_CM) * 0.3)
    d = math.hypot(max(du, 0.0), max(v - D, 0.0)) if front <= 0 else du
    return min(z, P + BANK * d)


def zone(site: dict, u: float, v: float) -> str:
    """Where a point of the yard's frame lies: 'platform', 'basin' (the water dredged along the quay) or ''."""
    du = abs(u) - site["L"] / 2
    if du <= 0 and 0 <= v <= site["D"]:
        return "platform"
    return "basin" if du <= 0 and -BASIN_CM <= v < 0 else ""


def openings(site: dict) -> list:
    """What is cut out of the platform's paving: [(u, half width, how far in from the quay)] for every graving
    dock and slipway, by u."""
    out = [(float(d["u"]), DOCK_W / 2 + 100, d["len"] + 100.0) for d in site.get("docks") or []]
    kind, L = site["type"], site["L"]
    if kind in ("yacht", "recycling"):
        n, half = (3, 800.0) if kind == "yacht" else (2, 1350.0)
        out += [(-L / 2 + (k + 0.5) * L / n * 0.7, half, SLIP_TOP + 400) for k in range(n)]
    return sorted(out)


def paving(site: dict) -> list:
    """The platform as rectangles (u0, u1, v0, v1), slabs up to SLAB_CM: all of it but its docks and slipways."""
    L, D = site["L"], site["D"]
    cuts = openings(site)
    top = min(D, max((c[2] for c in cuts), default=0.0))
    big, u0 = [], -L / 2
    for u, half, v_end in cuts:
        a, b = max(u0, u - half), min(L / 2, u + half)
        if a > u0:
            big.append((u0, a, 0.0, top))
        if v_end < top and b > a:
            big.append((a, b, v_end, top))
        u0 = max(u0, b)
    if cuts and u0 < L / 2:
        big.append((u0, L / 2, 0.0, top))
    if top < D:
        big.append((-L / 2, L / 2, top, D))
    out = []
    for a, b, c, d in big:
        nu, nv = max(1, math.ceil((b - a) / SLAB_CM)), max(1, math.ceil((d - c) / SLAB_CM))
        out += [(a + (b - a) * i / nu, a + (b - a) * (i + 1) / nu, c + (d - c) * j / nv, c + (d - c) * (j + 1) / nv)
                for i in range(nu) for j in range(nv)]
    return out


def dock_edges(site: dict) -> tuple:
    """The graving docks as spline edges (kind DK): inland end -> 30 m past the quay."""
    nodes, edges = {}, []
    f = site["frame"]
    for k, d in enumerate(site.get("docks") or []):
        a, b = ("dk", round(f["x"]), k, 0), ("dk", round(f["x"]), k, 1)
        nodes[a] = list(map(round, _w(f, d["u"], d["len"])))
        nodes[b] = list(map(round, _w(f, d["u"], -3000)))
        edges.append([a, b, "DK"])
    return nodes, edges


def build(site: dict, ground, sea: float, nodes: dict | None = None) -> dict:
    """The campus: rows (paving, walls, equipment, yards, ships, docks' gates and blocks), halls (lot rows), its
    report. A settled yard (site['z']) is built on its platform: every piece at that one level."""
    f, L, D, kind = site["frame"], site["L"], site["D"], site["type"]
    rows, lots, flow, checks = [], [], {}, []
    yaw_u, yaw_v = _yaw(f["ux"], f["uy"]), _yaw(f["vx"], f["vy"])
    yaw_w = _yaw(-f["vx"], -f["vy"])  # toward the water
    P = site.get("z")  # the platform's level (None: a yard that was never settled lies on the ground as it is)
    top = (P if P is not None else sea + 140) + 10  # the quay's edge: the top of the paving
    rnd = random.Random(f"{round(f['x'])}:{round(f['y'])}:{kind}")  # the yard's own dice: the same every run

    def z(x, y):
        if P is not None:
            return P
        g = ground(x, y)
        return max(sea, g) if g is not None else sea

    def row(u, v, kind_, sx=1.0, sy=1.0, sz=1.0, yaw=None, zz=None):
        x, y = _w(f, u, v)
        rows.append([round(x), round(y), round(z(x, y) if zz is None else zz), yaw_u if yaw is None else yaw,
                     round(sx, 2), round(sy, 2), round(sz, 2), kind_])

    def hall(u, v, w, d, role, name):
        x, y = _w(f, u, v)
        fx, fy = _w(f, u, v + d / 2)  # its front faces the yard's road at the back
        # (a lot row: width across its front, then depth; they were the other way round, and every hall stood
        # turned 90 degrees to the yard: the long ones into the docks and each other)
        lots.append([round(x), round(y), yaw_v, round(w), round(d), round(fx), round(fy), role, True])
        flow.setdefault(name, v)

    def slab(u0, u1, v0, v1, kind_="hardstand"):
        row((u0 + u1) / 2, (v0 + v1) / 2, kind_, (u1 - u0) / 100, (v1 - v0) / 100)

    def wall(u, v, yaw, bed):
        x, y = _w(f, u, v)
        bed = max(bed, sea - WALL_DEEP_CM)
        rows.append([round(x), round(y), round(bed), yaw, 10, 1, round((top - bed) / 100, 2), "quay_wall"])

    # its gate: at the back corner; moved along the back edge, or into a side (gate_side: +1 / -1, gate_v), where
    # its road could not leave from there (citygen.port_roads)
    gate_u = float(site.get("gate_u", L / 2 - 9000))
    gate_side, gate_v = site.get("gate_side"), float(site.get("gate_v") or 0.0)
    line = rows_of(kind, D) or {}
    v_back = line.get("back", D - 2500)
    # 0. the platform: paved from edge to edge. On its sides and its back a wall wherever it stands above what
    # lies beside it (water, lower land) and a fence wherever it meets the land.
    if P is not None:
        for a, b, c, d in paving(site):
            slab(a, b, c, d)
        rim = [(s * L / 2, v + 500.0, s * (L / 2 + 2000), yaw_v) for s in (-1, 1) for v in range(0, int(D), 1000)]
        rim += [(u + 500.0, D, None, yaw_u) for u in range(int(-L / 2), int(L / 2), 1000)]
        for u, v, beside, yaw in rim:
            g = ground(*(_w(f, beside, v) if beside is not None else _w(f, u, D + 2000)))
            if beside is not None and gate_side and (u > 0) == (gate_side > 0) and abs(v - (gate_v - 950)) < 1500:
                continue  # the gate in its side stays open
            if v < 2000:  # the ground ends under the paving just behind the quay (shaped's lip): closed by a wall
                wall(u, v, yaw, min(g if g is not None else sea, sea - DREDGE_CM))
            elif g is None or g < P - 100:
                wall(u, v, yaw, g if g is not None else sea - 800)
            elif not (beside is None and not gate_side and abs(u - gate_u) < 1500):  # (the gate stays open)
                row(u, v, "fence", 10, 1, 1, yaw=yaw)
    # 1. administration, gate, employee parking (the back corner, outside the heavy zone)
    hall(L / 2 - 3500, D - 2500, 4000, 2500, "civic", "administration")
    if gate_side:
        row(gate_side * (L / 2 - 400), gate_v, "gatehouse", yaw=yaw_v)
    else:
        row(gate_u, D - 300, "gatehouse")
    if P is None:
        slab(L / 2 - 16000, L / 2 - 8000, D - 5000, D - 600, "hardstand")
        if kind == "naval":  # secure perimeter
            for a in range(int(L // 1000)):
                row(-L / 2 + (a + 0.5) * 1000, D, "fence", 10, 1, 1)
            for a in range(int(D // 1000)):
                for side in (-1, 1):
                    row(side * L / 2, (a + 0.5) * 1000, "fence", 10, 1, 1, yaw=yaw_v)
    # 2. warehouses + steel storage
    v_store = line.get("store", D * 0.82)
    if kind in ("commercial", "naval", "offshore", "repair"):
        hall(-L / 2 + 9500, v_store, 9000, 3500, "warehouse", "warehouses")  # (clear of the rail siding beside it)
        if P is None:
            slab(-L / 2 + 14000, -L / 2 + 31000, v_store - 2500, v_store + 2500)
        for a in range(8):
            for b in range(3):
                row(-L / 2 + 15500 + a * 2000, v_store - 1500 + b * 1200, "steel_stack")
        flow["steel storage"] = v_store
        hall(-L / 2 + 36000, v_store, 5000, 3000, "factory", "machine shop")
        row(L / 2 - 20000, v_back, "tank", 1, 1, 1)
        row(L / 2 - 23000, v_back, "tank", 1, 1, 1)
        hall(L / 2 - 30000, v_back, 2000, 1500, "utility", "substation")
        hall(L / 2 - 36000, v_back, 2500, 2000, "fire_station", "yard fire station")
        # rail siding from the back of the yard to the fabrication halls
        for a in range(int((D * 0.5) // 1000)):
            row(-L / 2 + 3000, D - (a + 0.5) * 1000, "rail", 10, 1, 1, yaw=yaw_v)
    # 3. fabrication: panel line + steel fabrication halls, behind the docks' heads
    v_fab = line.get("fab", D * 0.62)
    if kind in ("commercial", "naval"):
        hall(-L / 2 + 14000, v_fab, 18000, 5000, "factory", "panel line factory")
        hall(-L / 2 + 36000, v_fab, 14000, 5000, "factory", "steel fabrication hall")
        hall(-L / 2 + 54000 if L > 70000 else L / 2 - 7000, v_fab, 10000, 4500, "factory", "paint and blasting hall")
    elif kind == "offshore":
        hall(-L / 2 + 15000, v_fab, 20000, 7000, "factory", "offshore fabrication hall")
    elif kind == "repair":
        hall(-L / 2 + 12000, v_fab, 12000, 4500, "factory", "repair workshops")
        hall(-L / 2 + 28000, v_fab, 8000, 4000, "factory", "paint and blasting hall")
    elif kind == "yacht":
        for a in range(2):
            hall(-L / 2 + 6000 + a * 9000, D * 0.55, 7000, 4500, "factory", "yacht assembly hall")
        hall(L / 2 - 6000, D * 0.55, 6000, 3500, "factory", "finishing hall")
    elif kind == "fishing":
        hall(-L / 2 + 5000, D * 0.5, 6000, 3000, "factory", "fish processing plant")
        hall(-L / 2 + 13000, D * 0.5, 5000, 3000, "warehouse", "cold storage")
        hall(L / 2 - 5000, D * 0.5, 5000, 2500, "shop", "fish market")
    flow.setdefault("fabrication", v_fab)
    # 4. assembly yard: open hardstand with tower cranes and heavy transporters
    v_asm0, v_asm1 = QUAY + 1000, min(D * 0.5, line.get("front", D))
    if kind in ("commercial", "naval", "offshore", "repair"):
        u0 = -L / 2 + L * 0.6 if site.get("docks") else -L / 2
        if P is None:
            slab(u0, L / 2, v_asm0, v_asm1)
        flow["assembly"] = (v_asm0 + v_asm1) / 2
        cranes = [u0 + 7000 + a * 15000 for a in range(max(1, int((L / 2 - u0) // 15000)))]
        for u in cranes:
            row(u, (v_asm0 + v_asm1) / 2, "tower_crane", yaw=yaw_w)
        if kind == "offshore":  # jackets / wind turbine foundations standing on the yard
            for a in range(int((L / 2 - u0) // 6000)):
                row(u0 + 3000 + a * 6000, v_asm1 - 2500, "offshore_module")
        if P is not None:  # hull sections and container stacks waiting on the hardstand, clear of the cranes
            v_hi = v_asm1 - (5500 if kind == "offshore" else 1500)
            for a in range(int(max(0.0, L / 2 - u0 - 3000) // 6000)):
                for b in range(int(max(0.0, v_hi - v_asm0 - 2000) // 5000)):
                    u, v = u0 + 4500 + a * 6000 + rnd.uniform(-800, 800), v_asm0 + 3000 + b * 5000 + rnd.uniform(-600, 600)
                    pick, s, turn = rnd.random(), round(rnd.uniform(0.14, 0.3), 2), rnd.choice((yaw_u, yaw_v))
                    if pick > 0.6 or any(abs(u - c) < 3500 and abs(v - (v_asm0 + v_asm1) / 2) < 3500 for c in cranes):
                        continue
                    if pick < 0.42:
                        row(u, v, "ship_hull", s, s, s, yaw=turn)
                    else:
                        row(u, v, "containers", yaw=turn)
    # 5. graving docks with goliath gantries, keel blocks, gate and a hull on the blocks
    for d in site.get("docks") or []:
        u = d["u"]
        for side in (-1, 1):  # crane rails both sides
            row(u + side * (DOCK_W / 2 + 800), d["len"] / 2, "crane_rail", 1, d["len"] / 100, 1, yaw=yaw_u)
        row(u, d["len"] * 0.55, "goliath", 1, (DOCK_W + 1600) / 7000, 1, yaw=yaw_v)
        floor = z(*_w(f, u, d["len"] / 2)) - DOCK_DEPTH_CM
        for a in range(int((d["len"] - 4000) // 1500)):
            row(u, 2000 + a * 1500, "keel", yaw=yaw_v, zz=floor)
        row(u, d["len"] * 0.5, "ship_hull", d["len"] * 0.75 / 10000, 1, 1, yaw=yaw_v, zz=floor + 150)
        row(u, -200, "dock_gate", DOCK_W / 100, 3, (top - floor) / 100, yaw=yaw_u, zz=floor)
        hall(u + DOCK_W / 2 + 3500, d["len"] - 1500, 1200, 1200, "utility", "dock pump house")
        flow.setdefault("docks", d["len"] / 2)
        mx, my = _w(f, u, -2000)
        g = ground(mx, my)
        # (a settled yard dredges its own basin: the dock mouth is in the water whatever lay there before)
        checks.append(("dock opens onto water", P is not None or g is None or g < sea, f"dock at u={round(u / 100)} m"))
    # slipways (yacht yards, recycling beaches, small yards)
    if kind in ("yacht", "recycling"):
        n = 3 if kind == "yacht" else 2
        ramp = lambda a: P - (P - (sea - 300)) * a / 9  # from the platform down into the water, an even slope
        for k in range(n):
            u = -L / 2 + (k + 0.5) * L / n * 0.7
            for a in range(10):
                v = SLIP_TOP - a * 600
                x, y = _w(f, u, v)
                zz = ramp(a) if P is not None else (z(x, y) if v > 0 else sea - 60 * (a - 4))
                row(u, v, "slipway", 6, 14 if kind == "yacht" else 25, 1, yaw=yaw_v, zz=zz)
            hull = 0.35 if kind == "yacht" else 0.9
            row(u, 1200, "ship_hull", hull, hull, hull, yaw=yaw_v, zz=ramp(3) if P is not None else None)
            if kind == "recycling":
                row(u + 3000, 5000, "scrap", 1.5, 1.5, 1)
        if kind == "yacht":
            row(L / 2 - 4000, 600, "boat_lift", yaw=yaw_v)
            if P is not None:  # boats laid up ashore between the slipways and the halls
                for a in range(int(max(0.0, L - 8000) // 2400)):
                    if rnd.random() < 0.7:
                        row(-L / 2 + 4000 + a * 2400, D * 0.28 + rnd.uniform(-300, 300), "boat", 1.3, 1.3, 1.3, yaw=yaw_v)
        flow.setdefault("docks", 1500)
    if kind == "fishing" and P is not None:  # cold boxes stacked between the plant and the market
        for a in range(int(max(0.0, L - 30000) // 3000)):
            if rnd.random() < 0.5:
                row(-L / 2 + 20000 + a * 3000, D * 0.5, "containers")
    # floating dry docks (repair yards) moored off the quay
    if kind in ("repair", "naval"):
        nav = max(20000.0, site["width_m"] * 100 * 0.3)
        out_v = -min(nav * 0.5, 4500)
        row(L * 0.25, out_v, "float_drydock", 1, 1, 1, yaw=yaw_u, zz=sea - 600)
        row(L * 0.25, out_v, "ship_hull", 1.4, 1, 1, yaw=yaw_u, zz=sea + 500)
        checks.append(("floating dock in the navigation share", abs(out_v) + 2250 <= nav, f"{round(abs(out_v) / 100)} m out"))
    # 6. outfitting quay: wall, portal cranes on rails, ships alongside, tug station
    mouths = [d["u"] for d in site.get("docks") or []] if P is not None else []
    for a in range(int(L // 1000)):
        u = -L / 2 + (a + 0.5) * 1000
        if any(abs(u - m) < DOCK_W / 2 for m in mouths):
            continue  # a dock's mouth: its gate closes it
        g = ground(*_w(f, u, 0))
        bed = min(g if g is not None else sea - 800, sea - (DREDGE_CM if P is not None else 800))
        wall(u, 0, yaw_u, bed)
    if kind != "recycling":
        u0 = -L / 2 + (L * 0.6 if site.get("docks") else 0)
        for side_v in (800, 2200):
            row((u0 + L / 2) / 2, side_v, "crane_rail", (L / 2 - u0) / 100, 1, 1)
        for a in range(max(1, int((L / 2 - u0) // 8000))):
            row(u0 + 4000 + a * 8000, 1500, "portal_crane", yaw=yaw_w)
        if kind in ("commercial", "naval", "repair", "offshore") and not piers(site):
            # (ships alongside the quay: a yard with piers moors them at its piers instead)
            for a in range(max(1, int((L / 2 - u0) // 22000))):
                row(u0 + 11000 + a * 22000, -1800, "ship_hull", 1.6, 1, 1, yaw=yaw_u, zz=sea - 600)
        flow["outfitting quay"] = 1500
        tx, ty = _w(f, -L / 2 + 1500, -1500)
        rows.append([round(tx), round(ty), round(sea - 50), yaw_u, 4, 1.5, 1, "tug_float"])
        for a in (-1, 1):
            x, y = _w(f, -L / 2 + 1500 + a * 800, -2500)
            rows.append([round(x), round(y), round(sea - 60), yaw_u, 2.6, 2.2, 1.6, "tug"])
    # 7. piers: a port is not one straight edge. From the open quay (clear of the docks' mouths, the slipways and
    # the floating dock) a pier runs out over the dredged basin about every 220 m: a deck at the platform's level,
    # walled down to the bed on both sides and across its end, a crane on it.
    if piers(site):
        reach, wide = min(PIER_CM[0], BASIN_CM), PIER_CM[1]
        bed = sea - DREDGE_CM
        for a, u in enumerate(piers(site)):
            for k in range(int(reach // 1000)):  # the deck, in 10 m slabs, and a wall along each side of it
                v = -(k + 0.5) * 1000
                row(u, v, "pier_deck", wide / 100, 10)
                for side in (-1, 1):
                    x, y = _w(f, u + side * wide / 2, v)
                    rows.append([round(x), round(y), round(bed), yaw_v, 10, 1, round((top - bed) / 100, 2), "pier_wall"])
            n_end = math.ceil(wide / 1000)  # ... and across its end
            for b in range(n_end):
                x, y = _w(f, u - wide / 2 + (b + 0.5) * wide / n_end, -reach)
                rows.append([round(x), round(y), round(bed), yaw_u, round(wide / n_end / 100, 2), 1, round((top - bed) / 100, 2), "pier_wall"])
            row(u, -reach * 0.55, "portal_crane", yaw=yaw_u)
            if kind != "fishing" or a % 2 == 0:  # a ship at its berth along the pier, bow to the quay
                row(u + wide / 2 + 1300, -reach * 0.5, "ship_hull", 0.6, 0.8, 0.7, yaw=yaw_v, zz=sea - 400)
        flow.setdefault("piers", 0.0)
    # launch basin: nothing else in the water in front of the docks
    report = validate(site, rows, flow, checks)
    if P is not None:
        report["platform_m"] = [round(L / 100), round(D / 100)]
        report["out_over_water_m"] = round(site.get("out", 0) / 100)
    return {"rows": rows, "lots": lots, "flow": {k: round(v / 100) for k, v in flow.items()}, "report": report}


def validate(site, rows, flow, checks) -> dict:
    """MarineIndustrialValidator."""
    bad = [d for what, ok, d in checks if not ok]
    order = [flow.get(k) for k in ("steel storage", "fabrication", "assembly", "outfitting quay") if flow.get(k) is not None]
    if any(a < b for a, b in zip(order, order[1:])):
        bad.append(f"production flow does not run inland -> water: {flow}")
    if flow.get("administration", 0) < site["D"] * 0.8:
        bad.append("administration inside the heavy zone")
    f = site["frame"]
    for d in site.get("docks") or []:
        mx, my = _w(f, d["u"], -6000)
        blockers = [r for r in rows if r[7] in ("float_drydock", "tug", "tug_float")
                    and math.hypot(r[0] - mx, r[1] - my) < 5000]
        if blockers:
            bad.append(f"launch basin of the dock at u={round(d['u'] / 100)} m is blocked")
    return {"ok": not bad, "problems": bad, "docks": len(site.get("docks") or []),
            "pieces": len(rows), "flow_m": {k: round(v / 100) for k, v in flow.items()}}


def _demo() -> None:
    # a straight deep coast along y (water at x > 0), land flat at +2 m
    ground = lambda x, y: -1500.0 if x > 0 else 200.0
    seg = {"type": "industrial", "water": "ocean", "length_m": 900,
           "samples": [{"x": 0, "y": y, "tx": 0.0, "ty": 1.0, "nx": 1.0, "ny": 0.0, "depth80_m": 12.0,
                        "depth30_m": 10.0, "width_m": 2000, "shelter": 0.2} for y in range(-45000, 45001, 2000)]}
    got = sites({"x": -30000, "y": 0, "radius": 100000}, [seg], 90000)
    assert got and got[0]["type"] == "commercial" and len(got[0]["docks"]) == 3, got
    s = settle(got[0], ground, 0.0)
    # flat land at the water: the yard stays where it was sited, one platform 2.5 m above the water
    assert (s["z"], s["out"], s["D"], s["bank"]) == (250.0, 0, 45000, 0), s
    assert inside(s, -s["D"] + 1000, 0) and not inside(s, -s["D"] - 5000, 0), "campus reaches D inland"
    assert uv(s, -1000, 1500) == (500.0, 1000.0) and len(outline(s)) == 4
    out = build(s, ground, 0.0)
    kinds = {r[7] for r in out["rows"]}
    assert {"goliath", "keel", "ship_hull", "dock_gate", "portal_crane", "tower_crane", "steel_stack", "crane_rail",
            "quay_wall", "hardstand", "rail", "fence", "containers", "tug", "tug_float"} <= kinds, kinds
    roles = {r[7] for r in out["lots"]}
    assert {"factory", "warehouse", "civic", "utility"} <= roles, roles
    assert out["report"]["ok"] and out["report"]["platform_m"] == [900, 450], out["report"]
    fl = out["flow"]
    assert fl["steel storage"] > fl["fabrication"] > fl["assembly"] > fl["outfitting quay"], fl
    n, e = dock_edges(s)
    assert len(e) == 3 and all(n[b][0] > 0 for _, b, _ in e), "docks open past the quay into the water"
    # keel blocks sit on the dock floor, 12 m under the platform
    assert all(abs(r[2] - (250 - DOCK_DEPTH_CM)) < 1 for r in out["rows"] if r[7] == "keel")
    # the platform is paved from edge to edge, in slabs of 100 m at most, every one at the platform's level, and
    # only its docks are left open
    slabs = paving(s)
    open_cm2 = sum(2 * half * v_end for _, half, v_end in openings(s))
    assert abs(sum((b - a) * (d - c) for a, b, c, d in slabs) - (s["L"] * s["D"] - open_cm2)) < 1, "paved edge to edge"
    assert all(b - a <= SLAB_CM + 1 and d - c <= SLAB_CM + 1 for a, b, c, d in slabs)
    assert not any(a < u < b and c < 5000 < d for a, b, c, d in slabs for u, _, _ in openings(s)), "docks stay open"
    paved = [r for r in out["rows"] if r[7] == "hardstand"]
    assert len(paved) == len(slabs) and {r[2] for r in paved} == {250}
    # its piers: out over the basin from the open quay, never across a dock's mouth, decked at the platform's
    # level and walled to the bed on both sides and the end
    decks, pier_walls = [r for r in out["rows"] if r[7] == "pier_deck"], [r for r in out["rows"] if r[7] == "pier_wall"]
    assert len(decks) == 3 * 9 and {r[2] for r in decks} == {250}, ("three piers on its 360 m of open quay", len(decks))
    assert len({round(uv(s, r[0], r[1])[0]) for r in decks}) == 3 and len(piers({**s, "type": "yacht"})) == 0
    berthed = [r for r in out["rows"] if r[7] == "ship_hull" and uv(s, r[0], r[1])[1] < -1000 and r[4] == 0.6]
    assert len(berthed) == 3 and not [r for r in out["rows"] if r[7] == "ship_hull" and r[4] == 1.6], "a ship at each pier, none along the quay"
    assert all(-9000 <= uv(s, r[0], r[1])[1] < 0 for r in decks), "every deck slab is out over the water"
    assert not any(abs(uv(s, r[0], r[1])[0] - d["u"]) < DOCK_W / 2 + 1000 for r in decks for d in s["docks"]), "no pier across a dock's mouth"
    assert len(pier_walls) == len(decks) // 9 * 21 and all(abs(r[2] + r[6] * 100 - 260) < 1.5 for r in pier_walls), len(pier_walls)
    # a yard entered from its side (water behind it): the gap in its edge is there, and its back is fenced right across
    sided = [r for r in build({**s, "gate_side": -1, "gate_v": 20950.0}, lambda x, y: 4000.0, 0.0)["rows"] if r[7] == "fence"]
    assert not [r for r in sided if uv(s, r[0], r[1])[0] < -s["L"] / 2 + 100 and abs(uv(s, r[0], r[1])[1] - 20000) < 1500], "the gap in its side"
    back_gate = len([r for r in out["rows"] if r[7] == "fence"])
    assert len(sided) == back_gate + 2 - 2, ("two more along the back, two fewer in the side", len(sided), back_gate)
    assert all(r[2] == 250 for r in out["rows"] if r[7] in ("tower_crane", "portal_crane", "steel_stack", "fence", "containers"))
    # its edges: a fence where it meets the land (the gate stays open), a wall along the quay but not across a
    # dock's mouth, and a wall closing the first 20 m of each side behind the quay
    fences, walls = [r for r in out["rows"] if r[7] == "fence"], [r for r in out["rows"] if r[7] == "quay_wall"]
    assert len(fences) == 2 * 43 + 90 - 2 and len(walls) == 90 - 3 * 4 + 4, (len(fences), len(walls))
    assert all(abs(r[2] + r[6] * 100 - 260) < 1.5 for r in walls), "every wall reaches the paving's top"
    # the ground it leaves: level on the platform, 9 m of water along the quay, the hill behind cut to a bank
    assert shaped(s, 9000, 20000, 4000.0, 0.0) == 250.0 and shaped(s, 0, -100, 300.0, 0.0) == -900.0
    assert shaped(s, 0, -100, -3000.0, 0.0) == -3000.0 and shaped(s, 0, -19000, 5000.0, 0.0) == -900 + 3000.0
    assert shaped(s, 0, 46000, 4000.0, 0.0) == 250 + 600.0 and shaped(s, 0, 46000, 100.0, 0.0) == 100.0
    assert shaped(s, -46000, 20000, 4000.0, 0.0) == 250 + 600.0 and shaped(s, -46000, 46000, 9000.0, 0.0) == 250 + BANK * math.hypot(1000, 1000)
    assert shaped(s, 0, 1000, 4000.0, 0.0, lip=1500) == -900.0 and shaped(s, 9000, 1600, 4000.0, 0.0, lip=1500) == 250.0
    # ... and a graving dock in it: 12 m down between straight walls, from the quay to its head
    dk = s["docks"][0]
    assert shaped(s, dk["u"] + 1900, dk["len"] - 100, 4000.0, 0.0) == 250 - DOCK_DEPTH_CM
    assert shaped(s, dk["u"] + 2100, 5000, 4000.0, 0.0) == 250.0 and shaped(s, dk["u"], dk["len"] + 100, 4000.0, 0.0) == 250.0
    assert [zone(s, 0, v) for v in (-9500, -500, 0, 45000, 45500)] == ["", "basin", "platform", "platform", ""]
    assert zone(s, 45500, 100) == "" and zone(s, 45500, -100) == ""
    # a yard under a hill (the land climbs 1 in 5 from the water): it is built out over the water in front of
    # the hill and reaches inland only to where the hill stands 12 m above it
    hill = lambda x, y: -1500.0 if x > 0 else 200.0 - 0.2 * x
    h = settle(sites({"x": -30000, "y": 0, "radius": 100000}, [seg], 90000)[0], hill, 0.0)
    assert (h["out"], h["D"], h["frame"]["x"]) == (22500, 29500, 22500) and 0 < h["bank"] <= BANK_MAX_CM, h
    assert h["docks"][0]["len"] == dock_len("commercial", 29500) == 11250 and build(h, hill, 0.0)["report"]["ok"]
    assert reserved(h, 22500 - 29500 - h["bank"] + 100, 0) and not inside(h, 22500 - 29500 - h["bank"] + 100, 0)
    hw = [r for r in build(h, hill, 0.0)["rows"] if r[7] == "quay_wall"]
    assert h["bank"] == 3500, "the bank: as wide as a 1 in 5 hill needs to come down at 0.6"
    assert len(hw) == 78 + 2 * 22, "made ground out in the water: walls along its sides too"
    assert [r[3:5] for r in out["lots"] if r[7] == "factory"][1] == [18000, 5000], "a hall: its width along the quay, then its depth"
    # the rows lie one behind the other with a lane between, and no hall stands on a dock, a rail or another hall
    for yard_ in (s, h):
        rw = rows_of("commercial", yard_["D"])
        assert rw["back"] > rw["store"] > rw["fab"] > rw["front"] >= yard_["docks"][0]["len"] >= 6000, rw
        halls = [(uv(yard_, r[0], r[1]), r[3] / 2, r[4] / 2) for r in build(yard_, ground, 0.0)["lots"]]
        for k, ((u1, v1), w1, d1) in enumerate(halls):
            assert abs(u1) + w1 <= yard_["L"] / 2 and d1 <= v1 <= yard_["D"] - d1, "a hall stands on the platform"
            assert not any(abs(u1 - u2) < w1 + w2 and abs(v1 - v2) < d1 + d2 for (u2, v2), w2, d2 in halls[k + 1:]), "two halls overlap"
            assert not any(abs(u1 - du) < w1 + half and v1 - d1 < v_end for du, half, v_end in openings(yard_)), "a hall on a dock"
            assert abs(u1 - (-yard_["L"] / 2 + 3000)) > w1 + 150 or v1 + d1 < yard_["D"] * 0.5, "a hall on the rail siding"
    # the other yard types
    for pop, water, L, want in ((30000, "ocean", 500, "offshore"), (20000, "lake", 300, "repair"),
                                (5000, "lake", 200, "fishing")):
        seg2 = {**seg, "water": water, "length_m": L, "samples": seg["samples"][:L // 20]}
        st = sites({"x": -30000, "y": 0, "radius": 100000}, [seg2], pop)
        assert st and st[0]["type"] == want, (want, st and st[0]["type"])
        rep = build(settle(st[0], ground, 0.0), ground, 0.0)["report"]
        assert rep["ok"], (want, rep)
    marina = {**seg, "type": "marina", "water": "lake", "length_m": 300, "samples": seg["samples"][:15]}
    ys = sites({"x": -30000, "y": 0, "radius": 100000}, [marina], 20000)
    assert ys and ys[0]["type"] == "yacht"
    yb = build(settle(ys[0], ground, 0.0), ground, 0.0)
    assert {"slipway", "boat_lift", "boat"} <= {r[7] for r in yb["rows"]} and yb["report"]["ok"], yb["report"]
    ramp = sorted(r[2] for r in yb["rows"] if r[7] == "slipway")
    assert ramp[0] == -300 and ramp[-1] == 250 and len(openings(ys[0])) == 3, "slipways run from the platform into the water"
    # a yard that was never settled is still built, on the ground as it lies
    old = build(sites({"x": -30000, "y": 0, "radius": 100000}, [seg], 90000)[0], ground, 0.0)
    assert old["report"]["ok"] and all(r[2] == 200 for r in old["rows"] if r[7] == "hardstand")
    assert sites({"x": 0, "y": 0, "radius": 1}, [seg], 90000, {"shipyards": "none"}) == []
    print("shipyard ok", {k: v for k, v in fl.items()}, len(out["rows"]), "pieces", len(out["lots"]), "halls")


if __name__ == "__main__":
    _demo()

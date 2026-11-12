"""Modular buildings: a lot becomes BuildingKit modules through a small shape grammar (Epic's PCG Grammar syntax).

The default bake stretches one whole kit mesh to each lot, which smears windows and doors. A layer saved with
`build: "modular"` is built the way real buildings (and Epic's PCG city) are: the form is split into blocks
(`massing`), every block is stacked in whole storeys, and each wall is filled with 5.12 m modules picked by a
grammar string per storey (`fill`), corner posts at every vertex, a parapet and a roof slab on top.

Grammar syntax (same as UE PCG Grammar):  [A,B,C] sequence   X* repeat to fill   {A:2,B:1} weighted pick
<A,B> priority (biggest first; every BuildingKit module is 5.12 m wide, so it is simply the first one).
"""

from __future__ import annotations

import math
import random

MODULE_CM = 512.0  # every BuildingKit wall, parapet and floor tile is 5.12 m along its run
STOREY_CM = 384.0  # BuildingKit storey
# ponytail: BuildingKit walls face local -Y in Blender. If an FBX export flips Y and buildings come out inside
# out in UEFN, set this to +1 (the walls are then laid clockwise instead).
OUTWARD = -1
FOLDER = "BuildingKit"  # under <project>/WorldGen/

# style -> corner post, wall symbols -> meshes, and one grammar per storey kind
STYLES = {
    "tower": {"corner": "SM_BK_Tower_Corner",
              "walls": {"Window": "SM_BK_Tower_Wall_Curtain", "Band": "SM_BK_Tower_Wall_Band",
                        "Solid": "SM_BK_Tower_Wall_Solid", "Lobby": "SM_BK_Tower_Wall_Lobby",
                        "Door": "SM_BK_Tower_Wall_RevolvingDoor"},
              "front_ground": "[Lobby*,Door,Lobby*]", "ground": "[Solid,Window*,Solid]", "upper": "[{Window:5,Band:1}*]"},
    "shop": {"corner": "SM_BK_Shop_Corner", "awning": "SM_BK_Shop_Awning",
             "walls": {"Storefront": "SM_BK_Shop_Wall_Storefront", "Entrance": "SM_BK_Shop_Wall_Entrance",
                       "Window": "SM_BK_Shop_Wall_Window", "Solid": "SM_BK_Shop_Wall_Solid"},
             "front_ground": "[Storefront*,Entrance,Storefront*]", "ground": "[{Window:1,Solid:1}*]",
             "upper": "[{Window:3,Solid:1}*]"},
    "house": {"corner": "SM_BK_House_Corner",
              "walls": {"Window": "SM_BK_House_Wall_Window", "Double": "SM_BK_House_Wall_DoubleWindow",
                        "Door": "SM_BK_House_Wall_Door", "Garage": "SM_BK_House_Wall_Garage",
                        "Solid": "SM_BK_House_Wall_Solid", "Balcony": "SM_BK_House_Wall_Balcony"},
              "front_ground": "[Garage,Door,{Window:2,Double:1}*]", "ground": "[{Window:1,Solid:1}*]",
              "upper": "[{Window:2,Double:1}*]"},
    "apartment": {"corner": "SM_BK_House_Corner",
                  "walls": {"Window": "SM_BK_House_Wall_Window", "Double": "SM_BK_House_Wall_DoubleWindow",
                            "Door": "SM_BK_House_Wall_Door", "Solid": "SM_BK_House_Wall_Solid",
                            "Balcony": "SM_BK_House_Wall_Balcony"},
                  "front_ground": "[{Window:2,Double:1}*,Door,{Window:2,Double:1}*]", "ground": "[{Window:2,Solid:1}*]",
                  "upper": "[{Window:2,Balcony:1,Double:1}*]"},
    "modern": {"corner": "SM_BK_Modern_StonePillar",
               "walls": {"Glass": "SM_BK_Modern_SlidingGlassWall", "Wood": "SM_BK_Modern_Wall_Wood",
                         "Garage": "SM_BK_Modern_GarageDoor"},
               "front_ground": "[Glass*]", "ground": "[{Glass:1,Wood:1}*]", "upper": "[{Glass:2,Wood:1}*]"},
    "civic": {"corner": "SM_BK_Mansion_Column",  # city hall, courthouse: arched windows, a grand door
              "walls": {"Arch": "SM_BK_Mansion_Wall_ArchWindows", "Bay": "SM_BK_Mansion_Wall_BayWindow",
                        "Entrance": "SM_BK_Mansion_Wall_GrandEntrance"},
              "front_ground": "[Arch*,Entrance,Arch*]", "ground": "[Arch*]", "upper": "[{Arch:4,Bay:1}*]"},
    "school": {"corner": "SM_BK_Modern_StonePillar",  # schools, libraries, community and post offices
               "walls": {"Glass": "SM_BK_Modern_SlidingGlassWall", "Wood": "SM_BK_Modern_Wall_Wood",
                         "Door": "SM_BK_Shop_Wall_Entrance"},
               "front_ground": "[Glass*,Door,Glass*]", "ground": "[{Glass:2,Wood:1}*]", "upper": "[{Glass:3,Wood:1}*]"},
    "industrial": {"corner": "SM_BK_Ind_Corner",  # factories, plants, depots, fire and ambulance stations
                   "walls": {"Metal": "SM_BK_Ind_Wall_Metal", "Window": "SM_BK_Ind_Wall_Window",
                             "Roller": "SM_BK_Ind_Wall_RollerDoor", "Door": "SM_BK_Ind_Wall_Door"},
                   "front_ground": "[Roller*,Door,Roller*]", "ground": "[Door,{Metal:2,Window:1}*]",
                   "upper": "[{Window:1,Metal:1}*]"},
    "jail": {"corner": "SM_BK_Jail_Corner",
             "walls": {"Slit": "SM_BK_Jail_Wall_Slit", "Solid": "SM_BK_Jail_Wall_Solid",
                       "Gate": "SM_BK_Jail_Wall_SallyPort"},
             "front_ground": "[Slit*,Gate,Slit*]", "ground": "[{Slit:2,Solid:1}*]", "upper": "[{Slit:2,Solid:1}*]"},
    "mansion": {"corner": "SM_BK_Mansion_Column",
                "walls": {"Arch": "SM_BK_Mansion_Wall_ArchWindows", "Bay": "SM_BK_Mansion_Wall_BayWindow",
                          "Entrance": "SM_BK_Mansion_Wall_GrandEntrance"},
                "front_ground": "[Arch*,Entrance,Arch*]", "ground": "[{Arch:2,Bay:1}*]", "upper": "[{Arch:2,Bay:1}*]"},
}
SHARED = {"parapet": "SM_BK_Parapet", "roof": "SM_BK_Floor"}
# name -> (min x, y, z, max x, y, z) in cm, as modelled in BuildingKit.blend: a wall runs 0..512 along +X, its
# thickness on +Y (the inside), whatever sticks out (lobby, awning, balcony) on -Y. The panel draws these boxes
# (pcg_exact), and a mesh imported into UEFN must measure the same: an importer that mirrors Y puts every roof
# slab beside its building and every lobby inside it.
BOUNDS = {"SM_BK_" + k: tuple(int(v) for v in b.split(",")) for k, b in (s.split(":") for s in """
Facade_Camera:-9,-63,304,9,0,330 Facade_Downpipe:22,-22,0,42,0,384 Facade_FireEscape:-252,-132,5,252,0,498
Facade_SplitAC:-45,-45,30,45,0,260 Facade_Vent:-28,-9,280,28,0,315 Facade_WallLight:-10,-28,230,10,0,256
Facade_WindowAC:-35,-52,78,35,5,130 Floor:0,0,-20,512,512,0 House_Corner:0,0,0,20,20,384
House_Wall_Balcony:0,-120,0,512,21,384 House_Wall_Door:0,-6,0,512,21,384 House_Wall_DoubleWindow:0,-8,0,512,21,384
House_Wall_Garage:0,-6,0,512,21,384 House_Wall_Solid:0,0,0,512,20,384 House_Wall_Window:0,-8,0,512,21,384
Ind_Corner:-20,-20,0,20,20,384 Ind_Wall_Door:0,-100,0,512,25,384 Ind_Wall_Metal:0,-3,0,512,25,384
Ind_Wall_RollerDoor:0,-50,0,512,30,384 Ind_Wall_Window:0,-3,0,512,25,384 Jail_Corner:-20,-20,0,20,20,384
Jail_Wall_SallyPort:0,-3,0,512,30,384 Jail_Wall_Slit:0,0,0,512,30,384 Jail_Wall_Solid:0,-2,0,512,30,384
Mansion_Column:-39,-39,0,39,39,384 Mansion_Wall_ArchWindows:0,-20,0,512,30,384
Mansion_Wall_BayWindow:0,-175,0,512,30,384 Mansion_Wall_GrandEntrance:0,-120,-45,512,30,384
Modern_GarageDoor:0,-20,0,512,0,384 Modern_SlidingGlassWall:0,-20,0,512,0,384 Modern_StonePillar:-7,-68,0,68,7,384
Modern_Wall_Wood:0,-24,0,512,0,384 Parapet:0,-5,0,512,25,88 Roof_ACUnit_Large:-170,-120,0,170,120,185
Roof_ACUnit_Small:-70,-60,0,70,60,101 Roof_Antenna:-60,-30,0,60,30,532 Roof_Duct:-200,-45,0,205,45,130
Roof_ExhaustFan:-52,-55,0,52,55,115 Roof_Hatch:-50,-50,0,50,50,30 Roof_SatelliteDish:-58,-42,0,58,40,146
Roof_Skylight:-110,-110,0,110,110,80 Roof_SolarArray:-195,-180,0,195,180,109
Roof_StairBulkhead:-235,-235,0,235,235,300 Roof_VentStack:-16,-16,0,56,56,132 Roof_WaterTank:-151,-155,0,155,155,670
Shop_Awning:0,-140,255,512,0,330 Shop_Corner:-5,-5,0,25,25,384 Shop_Wall_Entrance:0,-12,0,512,21,384
Shop_Wall_Solid:0,0,0,512,20,384 Shop_Wall_Storefront:0,-12,0,512,21,384 Shop_Wall_Window:0,-8,0,512,21,384
Tower_Corner:-10,-10,0,30,30,384 Tower_Wall_Band:0,-1,0,512,21,384 Tower_Wall_Curtain:0,-1,0,512,21,384
Tower_Wall_Lobby:0,-150,0,512,21,384 Tower_Wall_RevolvingDoor:0,-125,0,512,150,384 Tower_Wall_Solid:0,0,0,512,20,384
""".split())}
# role -> style (a planned facility can pick its own style instead: kit_bake.FACILITIES)
STYLE_BY_ROLE = {"skyscraper": "tower", "office": "tower", "apartment": "apartment", "shop": "shop", "mall": "shop",
                 "transit": "shop", "gas_station": "shop", "house": "house", "mansion": "mansion", "school": "school",
                 "civic": "civic", "hospital": "tower", "police": "modern", "fire_station": "industrial",
                 "factory": "industrial", "warehouse": "industrial", "utility": "industrial"}


def modules() -> list:
    """Every BuildingKit mesh the grammar can place (for the piece manifest)."""
    names = set(SHARED.values()) | {BULKHEAD} | set(DETAILS)
    for s in STYLES.values():
        names |= set(s["walls"].values()) | {s["corner"]} | ({s["awning"]} if "awning" in s else set())
    for gear in ROOF.values():
        names |= {n for n in gear if n}
    return sorted(names)


def parse(grammar: str) -> list:
    """'[A*,B,{C:2,D}*]' -> [{'syms': [(name, weight)], 'rep': bool, 'prio': bool}, ...]."""
    g = grammar.strip()
    if g.startswith("[") and g.endswith("]"):
        g = g[1:-1]
    items, depth, cur = [], 0, ""
    for ch in g + ",":
        if ch in "{<":
            depth += 1
        elif ch in "}>":
            depth -= 1
        if ch == "," and depth == 0:
            if cur.strip():
                items.append(cur.strip())
            cur = ""
        else:
            cur += ch
    out = []
    for it in items:
        rep = it.endswith("*")
        body = it.rstrip("*").strip()
        prio = body.startswith("<")
        if body[:1] in "{<":
            syms = []
            for part in body[1:-1].split(","):
                name, _, w = part.strip().partition(":")
                syms.append((name.strip(), int(float(w)) if w.strip() else 1))  # whole weights only, like UE
        else:
            syms = [(body, 1)]
        out.append({"syms": syms, "rep": rep, "prio": prio})
    return out


def fill(length_cm: float, grammar: str, rnd) -> list:
    """The symbols filling a wall of length_cm, one per module; the caller stretches them to fit exactly.
    Fixed items are kept (the middle ones first when the wall is short); repeats share the rest evenly."""
    items = parse(grammar)
    n = max(1, round(length_cm / MODULE_CM))
    fixed = [i for i, it in enumerate(items) if not it["rep"]]
    reps = [i for i, it in enumerate(items) if it["rep"]]
    if len(fixed) > n:  # a short wall: keep the fixed items nearest the middle (the door, not the corners)
        mid = (len(items) - 1) / 2
        keep = set(sorted(fixed, key=lambda i: abs(i - mid))[:n])
        items = [it for i, it in enumerate(items) if i in keep or it["rep"]]
        fixed = [i for i, it in enumerate(items) if not it["rep"]]
        reps = [i for i, it in enumerate(items) if it["rep"]]
    slots = n - len(fixed)
    share = {i: slots // len(reps) + (1 if k < slots % len(reps) else 0) for k, i in enumerate(reps)} if reps else {}
    out = []
    for i, it in enumerate(items):
        for _ in range(1 if not it["rep"] else share.get(i, 0)):
            if it["prio"] or len(it["syms"]) == 1:
                out.append(it["syms"][0][0])
            else:
                names, weights = zip(*it["syms"])
                out.append(rnd.choices(names, weights)[0])
    return out or [items[0]["syms"][0][0]]


def massing(form: str, d: float, w: float, h: float) -> list:
    """A building form as blocks in its lot frame (+X = front, toward the street; cm):
    [(cx, cy, depth along X, width along Y, z0, z1)]. Round / sculpted forms fall back to a block."""
    s = STOREY_CM
    if form == "tower":
        return [(0, 0, d * 0.7, w * 0.7, 0, h)]
    if form == "slab":
        return [(0, 0, d * 0.6, w, 0, h)]
    if form in ("podium_tower", "round_podium"):
        pod = min(h * 0.35, 10 * s)
        return [(0, 0, d, w, 0, pod), (-d * 0.1, 0, d * 0.6, w * 0.6, pod, h)]
    if form == "stepped":
        return [(0, 0, d, w, 0, h * 0.55), (0, 0, d * 0.75, w * 0.75, h * 0.55, h * 0.8), (0, 0, d * 0.5, w * 0.5, h * 0.8, h)]
    if form == "twin":
        pod = min(h * 0.25, 6 * s)
        return [(0, 0, d, w, 0, pod), (0, -w * 0.25, d * 0.6, w * 0.4, pod, h), (0, w * 0.25, d * 0.6, w * 0.4, pod, h * 0.75)]
    if form == "L":
        return [(d * 0.275, 0, d * 0.45, w, 0, h), (-d * 0.225, -w * 0.3, d * 0.55, w * 0.4, 0, h)]
    if form == "courtyard":
        t = min(d, w) * 0.3
        return [(d / 2 - t / 2, 0, t, w, 0, h), (-d / 2 + t / 2, 0, t, w, 0, h),
                (0, w / 2 - t / 2, d - 2 * t, t, 0, h), (0, -w / 2 + t / 2, d - 2 * t, t, 0, h)]
    return [(0, 0, d, w, 0, h)]


# What goes on a building up close. Roof gear: one weighted pick per roof cell ('' = an empty cell), never under
# a higher block, the cell over the stair core kept for the roof-access hut. Facade details: storey-local pieces
# centred on a wall module (window AC, split AC, vents), on a corner (downpipes) or beside a door (lights, cameras).
ROOF = {
    "tower": {"SM_BK_Roof_ACUnit_Large": 3, "SM_BK_Roof_ExhaustFan": 2, "SM_BK_Roof_VentStack": 1, "SM_BK_Roof_Antenna": 1, "": 3},
    "apartment": {"SM_BK_Roof_ACUnit_Small": 3, "SM_BK_Roof_SatelliteDish": 2, "SM_BK_Roof_VentStack": 2,
                  "SM_BK_Roof_WaterTank": 1, "": 3},
    "house": {"SM_BK_Roof_SatelliteDish": 1, "SM_BK_Roof_SolarArray": 1, "SM_BK_Roof_VentStack": 1, "": 3},
    "shop": {"SM_BK_Roof_ACUnit_Large": 2, "SM_BK_Roof_ACUnit_Small": 2, "SM_BK_Roof_VentStack": 1, "": 2},
    "modern": {"SM_BK_Roof_ACUnit_Small": 2, "SM_BK_Roof_SolarArray": 2, "SM_BK_Roof_Skylight": 1, "": 3},
    "school": {"SM_BK_Roof_ACUnit_Large": 2, "SM_BK_Roof_SolarArray": 2, "SM_BK_Roof_Skylight": 2, "": 2},
    "civic": {"SM_BK_Roof_ACUnit_Large": 2, "SM_BK_Roof_Skylight": 1, "SM_BK_Roof_Hatch": 1, "": 4},
    "mansion": {"SM_BK_Roof_SatelliteDish": 1, "SM_BK_Roof_VentStack": 1, "": 4},
    "industrial": {"SM_BK_Roof_ExhaustFan": 3, "SM_BK_Roof_Skylight": 3, "SM_BK_Roof_Duct": 2, "SM_BK_Roof_SolarArray": 1, "": 1},
    "jail": {"SM_BK_Roof_ACUnit_Large": 2, "SM_BK_Roof_ExhaustFan": 2, "SM_BK_Roof_Hatch": 1, "": 3},
}
ROOF_CELL_CM = 450.0  # one piece of roof gear per 4.5 m cell, kept 2.5 m in from the parapet
HUT_CLEAR_CM = 400.0  # cells this close to the hut's centre (on both axes) would stand inside it
BULKHEAD = "SM_BK_Roof_StairBulkhead"  # the roof-access hut over the stair core (blocks of 2+ storeys with a core)
CORE_BLOCK_CM = 1100.0  # the interiors fit a stair core only in a block at least this wide (kit_bake.CORE_MIN_CM)
FACADE = {  # style -> (window AC chance on upper windows, split AC chance on ground side walls, vent chance,
            #           downpipes on corners, a fire escape up one side, cameras at doors and corners)
    "apartment": (0.22, 0.0, 0.05, True, True, False), "house": (0.18, 0.0, 0.0, True, False, False),
    "shop": (0.12, 0.0, 0.15, True, False, False), "modern": (0.0, 0.15, 0.05, True, False, False),
    "school": (0.0, 0.12, 0.05, True, False, False), "civic": (0.0, 0.08, 0.0, True, False, False),
    "mansion": (0.0, 0.0, 0.0, True, False, False), "industrial": (0.0, 0.0, 0.25, True, False, True),
    "jail": (0.0, 0.0, 0.1, False, False, True), "tower": (0.0, 0.0, 0.0, False, False, False),
}
DETAILS = ("SM_BK_Facade_WindowAC", "SM_BK_Facade_SplitAC", "SM_BK_Facade_Downpipe", "SM_BK_Facade_WallLight",
           "SM_BK_Facade_FireEscape", "SM_BK_Facade_Vent", "SM_BK_Facade_Camera")
WINDOWS = ("Window", "Double", "Arch", "Glass", "Slit")
DOORS = ("Door", "Entrance", "Gate", "Lobby", "Roller")


def build(form: str, role: str, d: float, w: float, h: float, seed: int, path, style_name: str = "") -> tuple:
    """(rows {mesh path: [[u, v, z, yaw, sx, sy, sz]]} in the lot frame, blocks for the interiors).
    path(name) -> the mesh's content path. Storeys snap to whole BuildingKit storeys (at least one). Roof gear
    and facade details come from their own random stream, so they never change which walls a building gets."""
    sname = style_name or STYLE_BY_ROLE.get(role, "tower")
    style = STYLES[sname]
    rnd = random.Random(seed)
    det = random.Random(seed ^ 0x5EED)
    wac, sac, vent, pipes, escape, cams = FACADE.get(sname, FACADE["tower"])
    rows: dict = {}
    blocks = []
    mass = massing(form, d, w, h)
    front = max(cx + bd / 2 for cx, _, bd, _, _, _ in mass)

    def put(name, u, v, z, yaw, sx=1.0, sz=1.0):
        rows.setdefault(path(name), []).append([round(u), round(v), round(z), round(yaw % 360, 1), round(sx, 3), 1.0, round(sz, 3)])

    snapped = []
    for cx, cy, bd, bw, z0, z1 in mass:
        z0 = round(z0 / STOREY_CM) * STOREY_CM
        storeys = max(1, round((z1 - z0) / STOREY_CM))
        snapped.append((cx - bd / 2, cy - bw / 2, z0, cx + bd / 2, cy + bw / 2, z0 + storeys * STOREY_CM))
    for (cx, cy, bd, bw, _, _), (x0, y0, z0, x1, y1, top) in zip(mass, snapped):
        storeys = round((top - z0) / STOREY_CM)
        poly = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]  # counter-clockwise: the inside is on each edge's left
        if OUTWARD > 0:
            poly.reverse()
        blocks.append((x0, y0, z0, x1, y1, top))
        edges = list(zip(poly, poly[1:] + poly[:1]))
        sides = [e for e, ((pu, _), (qu, _)) in enumerate(edges) if not (abs(pu - front) < 1 and abs(qu - front) < 1)]
        escape_edge = det.choice(sides) if escape and storeys >= 3 and sides else -1
        for e, ((pu, pv), (qu, qv)) in enumerate(edges):
            L = math.hypot(qu - pu, qv - pv)
            yaw = math.degrees(math.atan2(qv - pv, qu - pu))
            ux, uy = (qu - pu) / L, (qv - pv) / L
            is_front = abs(pu - front) < 1 and abs(qu - front) < 1 and z0 == 0
            n = max(1, round(L / MODULE_CM))
            for k in range(storeys):
                z = z0 + k * STOREY_CM
                kind = ("front_ground" if is_front else "ground") if z == 0 else "upper"
                syms = fill(L, style[kind], rnd)
                size = L / len(syms)
                for i, sym in enumerate(syms):
                    u, v = pu + ux * size * i, pv + uy * size * i
                    put(style["walls"][sym], u, v, z, yaw, size / MODULE_CM)
                    if kind == "front_ground" and "awning" in style and sym in ("Storefront", "Entrance"):
                        put(style["awning"], u, v, z, yaw, size / MODULE_CM)
                    mu, mv = u + ux * size / 2, v + uy * size / 2  # the module's middle on the wall face
                    if e == escape_edge and k >= 1 and i == len(syms) // 2:
                        put("SM_BK_Facade_FireEscape", mu, mv, z, yaw)
                    elif kind == "upper" and sym in WINDOWS and det.random() < wac:
                        put("SM_BK_Facade_WindowAC", mu, mv, z, yaw)
                    elif kind == "ground" and sym not in DOORS and det.random() < sac:
                        put("SM_BK_Facade_SplitAC", mu, mv, z, yaw)
                    elif sym not in DOORS and det.random() < vent:
                        put("SM_BK_Facade_Vent", mu, mv, z, yaw)
                    if z == 0 and sym in DOORS and sym != "Lobby":  # a lamp each side of a door
                        for s in (-0.42, 0.42):
                            put("SM_BK_Facade_WallLight", mu + ux * size * s, mv + uy * size * s, z, yaw)
                        if cams:
                            put("SM_BK_Facade_Camera", mu + ux * size * 0.3, mv + uy * size * 0.3, z, yaw)
                put(style["corner"], pu, pv, z, yaw)
                if pipes:
                    put("SM_BK_Facade_Downpipe", pu, pv, z, yaw)
            if cams and z0 == 0:
                put("SM_BK_Facade_Camera", pu + ux * 60, pv + uy * 60, 0, yaw)
            for i in range(n):  # parapet along the roof edge
                put(SHARED["parapet"], pu + ux * L / n * i, pv + uy * L / n * i, top, yaw, L / n / MODULE_CM)
        put(SHARED["roof"], x0, y0, top, 0, bd / MODULE_CM)
        rows[path(SHARED["roof"])][-1][5] = round(bw / MODULE_CM, 3)  # roof slab: depth x width
        # the roof: an access hut over the stair core, gear in the free cells, nothing under a higher block
        hut = storeys >= 2 and min(bd, bw) >= CORE_BLOCK_CM
        if hut:
            put(BULKHEAD, cx, cy, top, 0)
        gear = ROOF.get(sname, {})
        names, weights = list(gear), list(gear.values())
        ncx, ncy = max(1, int((bd - 500) // ROOF_CELL_CM)), max(1, int((bw - 500) // ROOF_CELL_CM))
        for a in range(ncx):
            for b_ in range(ncy):
                gu = x0 + (bd - (ncx - 1) * ROOF_CELL_CM) / 2 + a * ROOF_CELL_CM
                gv = y0 + (bw - (ncy - 1) * ROOF_CELL_CM) / 2 + b_ * ROOF_CELL_CM
                if not names or (hut and abs(gu - cx) < HUT_CLEAR_CM and abs(gv - cy) < HUT_CLEAR_CM):
                    continue
                if any(o[2] >= top - 1 and o[0] - 100 < gu < o[3] + 100 and o[1] - 100 < gv < o[4] + 100
                       for o in snapped if o[5] > top):
                    continue  # under the tower that stands on this podium
                pick = det.choices(names, weights)[0]
                if pick:
                    put(pick, gu, gv, top, det.choice((0, 90, 180, 270)))
    return rows, blocks


def _demo() -> None:
    p = lambda n: n
    # a 15 m x 12 m two-storey shop: front storefronts + awnings, a door in the middle, whole storeys
    rows, blocks = build("box", "shop", 1500, 1200, 700, 7, p)
    assert blocks == [(-750, -600, 0, 750, 600, 768)], blocks
    front = [r for r in rows["SM_BK_Shop_Wall_Storefront"]] + rows["SM_BK_Shop_Wall_Entrance"]
    assert len(rows["SM_BK_Shop_Wall_Entrance"]) == 1 and all(r[0] == 750 and r[2] == 0 for r in front), front
    assert len(rows["SM_BK_Shop_Awning"]) == len(front)
    assert all(0.65 <= r[4] <= 1.35 for rs in rows.values() for r in rs if rs is not rows["SM_BK_Floor"]), "modules barely stretch"
    walls = sum(len(v) for k, v in rows.items() if "_Wall_" in k)
    assert walls == 2 * 2 * (3 + 2), walls  # 2 storeys x (two 15 m walls of 3 + two 12 m walls of 2)
    assert len(rows["SM_BK_Shop_Corner"]) == 4 * 2 and len(rows["SM_BK_Parapet"]) == 10 and len(rows["SM_BK_Floor"]) == 1
    # each wall runs along its edge: modules on the front edge face +Y yaw (90) and start at the corner
    assert {r[3] for r in front} == {90.0}, front
    # grammar
    assert fill(5 * MODULE_CM, "[Lobby*,Door,Lobby*]", random.Random(1)) == ["Lobby", "Lobby", "Door", "Lobby", "Lobby"]
    assert fill(MODULE_CM, "[Garage,Door,Window*]", random.Random(1)) == ["Door"], "a short wall keeps the middle item"
    r = fill(400 * MODULE_CM, "[{A:3,B:1}*]", random.Random(2))
    assert 2.2 < r.count("A") / r.count("B") < 4.2, "weights hold"
    assert parse("[A*,<B,C>*,{D:2,E}]")[1] == {"syms": [("B", 1), ("C", 1)], "rep": True, "prio": True}
    # forms: a podium tower is two blocks, the tower on the podium's whole-storey top
    rows, blocks = build("podium_tower", "office", 3200, 3200, 11000, 3, p)
    assert len(blocks) == 2 and blocks[1][2] == blocks[0][5] and blocks[1][5] <= 11000 + STOREY_CM, blocks
    assert len(rows["SM_BK_Tower_Wall_RevolvingDoor"]) == 1, "one front door at street level"
    assert all(m in modules() for m in rows), "the manifest lists every placed module"
    assert not [m for m in modules() if m not in BOUNDS], "every module has its size: the panel draws it"
    # a roof slab covers exactly its block (podium, then tower): placed at the corner, stretched over +X / +Y
    b = BOUNDS["SM_BK_Floor"]
    for r, blk in zip(rows["SM_BK_Floor"], blocks):
        assert (r[0] + b[0] * r[4], r[1] + b[1] * r[5], r[0] + b[3] * r[4], r[1] + b[4] * r[5]) == \
            (blk[0], blk[1], blk[3], blk[4]), (r, blk)
    # on the podium roof no gear stands inside the tower's footprint, and the tower gets its roof-access hut
    tx0, ty0, tz0, tx1, ty1, _ = blocks[1]
    for name in ROOF["tower"]:
        for r in rows.get(name, []):
            assert not (r[2] == blocks[0][5] and tx0 - 100 < r[0] < tx1 + 100 and ty0 - 100 < r[1] < ty1 + 100), r
    assert len(rows[BULKHEAD]) == 2 and "SM_BK_Facade_Downpipe" not in rows, "glass towers: huts, no downpipes"
    tower_top = [r for n in ROOF["tower"] if n for r in rows.get(n, []) if r[2] == blocks[1][5]]
    assert len(tower_top) >= 3, ("a 19 m tower roof carries gear round its hut, not just the hut", len(tower_top))
    # an apartment block up close: a fire escape up one side, window AC units, downpipes, lights at the door
    rows, blocks = build("box", "apartment", 1800, 2600, 1600, 11, p)
    storeys = round(blocks[0][5] / STOREY_CM)
    assert len(rows["SM_BK_Facade_FireEscape"]) == storeys - 1, rows.get("SM_BK_Facade_FireEscape")
    assert rows.get("SM_BK_Facade_WindowAC") and len(rows["SM_BK_Facade_Downpipe"]) == 4 * storeys
    assert len(rows["SM_BK_Facade_WallLight"]) == 2 * len(rows["SM_BK_House_Wall_Door"])
    gear = sum(len(rows.get(n, [])) for n in ROOF["apartment"] if n)
    assert gear >= 3 and len(rows[BULKHEAD]) == 1, gear
    # details never change the walls: same seed, same wall picks as without them
    assert build("box", "apartment", 1800, 2600, 1600, 11, p)[0] == rows, "deterministic"
    print("grammar ok", sum(len(v) for v in rows.values()), "pieces in a 6-storey apartment block, details included")


if __name__ == "__main__":
    _demo()

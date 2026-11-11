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
    "mansion": {"corner": "SM_BK_Mansion_Column",
                "walls": {"Arch": "SM_BK_Mansion_Wall_ArchWindows", "Bay": "SM_BK_Mansion_Wall_BayWindow",
                          "Entrance": "SM_BK_Mansion_Wall_GrandEntrance"},
                "front_ground": "[Arch*,Entrance,Arch*]", "ground": "[{Arch:2,Bay:1}*]", "upper": "[{Arch:2,Bay:1}*]"},
}
SHARED = {"parapet": "SM_BK_Parapet", "roof": "SM_BK_Floor"}
# role -> style; roles left out (factories, warehouses, parks, parking) keep their whole meshes
STYLE_BY_ROLE = {"skyscraper": "tower", "office": "tower", "apartment": "apartment", "shop": "shop", "mall": "shop",
                 "transit": "shop", "gas_station": "shop", "house": "house", "mansion": "mansion", "school": "modern",
                 "civic": "modern", "hospital": "modern", "police": "modern", "fire_station": "modern"}


def modules() -> list:
    """Every BuildingKit mesh the grammar can place (for the piece manifest)."""
    names = set(SHARED.values())
    for s in STYLES.values():
        names |= set(s["walls"].values()) | {s["corner"]} | ({s["awning"]} if "awning" in s else set())
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


def build(form: str, role: str, d: float, w: float, h: float, seed: int, path) -> tuple:
    """(rows {mesh path: [[u, v, z, yaw, sx, sy, sz]]} in the lot frame, blocks for the interiors).
    path(name) -> the mesh's content path. Storeys snap to whole BuildingKit storeys (at least one)."""
    style = STYLES[STYLE_BY_ROLE.get(role, "tower")]
    rnd = random.Random(seed)
    rows: dict = {}
    blocks = []
    front = max(cx + bd / 2 for cx, _, bd, _, _, _ in massing(form, d, w, h))

    def put(name, u, v, z, yaw, sx=1.0, sz=1.0):
        rows.setdefault(path(name), []).append([round(u), round(v), round(z), round(yaw % 360, 1), round(sx, 3), 1.0, round(sz, 3)])

    for cx, cy, bd, bw, z0, z1 in massing(form, d, w, h):
        z0 = round(z0 / STOREY_CM) * STOREY_CM
        storeys = max(1, round((z1 - z0) / STOREY_CM))
        top = z0 + storeys * STOREY_CM
        x0, x1, y0, y1 = cx - bd / 2, cx + bd / 2, cy - bw / 2, cy + bw / 2
        poly = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]  # counter-clockwise: the inside is on each edge's left
        if OUTWARD > 0:
            poly.reverse()
        blocks.append((x0, y0, z0, x1, y1, top))
        for (pu, pv), (qu, qv) in zip(poly, poly[1:] + poly[:1]):
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
                put(style["corner"], pu, pv, z, yaw)
            for i in range(n):  # parapet along the roof edge
                put(SHARED["parapet"], pu + ux * L / n * i, pv + uy * L / n * i, top, yaw, L / n / MODULE_CM)
        put(SHARED["roof"], x0, y0, top, 0, bd / MODULE_CM)
        rows[path(SHARED["roof"])][-1][5] = round(bw / MODULE_CM, 3)  # roof slab: depth x width
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
    print("grammar ok", sum(len(v) for v in rows.values()), "modules in a 110 m podium tower")


if __name__ == "__main__":
    _demo()

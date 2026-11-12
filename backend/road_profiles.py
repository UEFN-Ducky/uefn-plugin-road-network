"""Every road piece's cross-section: the ONE source for the panel's preview, the low-poly mesh City Builder builds
in UEFN when the project has no model of that piece, and the modelling spec an AI gets in the build brief.

Units cm. A piece runs along +X (-length/2 .. +length/2, it bends as a spline mesh), Y is across (0 = centre
line), z 0 is the road surface. parts: bands the full length, (name, slot, y0, y1, z0, z1). paint: lines on the
surface, (y, width, dash, slot): dash 0 = solid, else that many cm painted in every PAINT_PERIOD (so dashes
line up from one 10 or 20 m tile to the next).
"""

from __future__ import annotations

import os
import re

from . import citygen as cg

SLOTS = ["Asphalt", "Pavers", "Plinth", "Paint", "PaintYellow"]  # material slots = the street kit's looks
PAINT_PERIOD = 1000.0  # a 3 m dash every 10 m
DASH = 300.0
DECK_CM = 150.0  # a structure's slab depth under the road surface
ROAD_CM = 30.0  # a ground road's own thickness (it sits on the cut ground)
KERB_CM = 15.0  # sidewalk top over the road
PARAPET_CM = 105.0
MEDIAN = ("6L", "ED")  # divided by a concrete median barrier
NO_PAINT = ("DT",)


def shape(name: str, y0: float, y1: float, z0: float, z1: float) -> list:
    """A part's cross-section as a closed polygon [(y, z), ...] (counter-clockwise seen from -X): what a model's
    edge loop is. Parapets and the median are Jersey barriers (the sloped face toward the traffic), a sidewalk's
    kerb is chamfered, a deck slab tapers to its beams underneath; the rest are plain bands."""
    w, h = y1 - y0, z1 - z0
    if name in ("parapet", "median"):
        def face(base, sd):  # one Jersey face from the bottom up: 7.5 cm upright, slope, steep top
            return [(base, z0), (base, z0 + 7.5), (base + sd * w * 0.3, z0 + min(33.0, h * 0.4)), (base + sd * w * 0.4, z1)]
        if name == "median":
            left, right = face(y0, 1), face(y1, -1)
            return left[:1] + right[:1] + right[1:] + left[1:][::-1]
        if (y0 + y1) / 2 < 0:  # left edge: the outside (y0) is upright, the face toward the road (y1) slopes
            inner = face(y1, -1)
            return [(y0, z0), inner[0], *inner[1:], (y0, z1)]
        inner = face(y0, 1)
        return [inner[0], (y1, z0), (y1, z1), *inner[1:][::-1]]
    if name == "sidewalk":  # the kerb's top edge on the road side is chamfered 4 cm
        if (y0 + y1) / 2 < 0:
            return [(y0, z0), (y1, z0), (y1, z1 - 4), (y1 - 4, z1), (y0, z1)]
        return [(y0, z0), (y1, z0), (y1, z1), (y0 + 4, z1), (y0, z1 - 4)]
    if name == "deck":  # the slab's underside tapers in to its beams
        t = min(150.0, w * 0.15)
        return [(y0 + t, z0), (y1 - t, z0), (y1, z1 - min(40.0, h * 0.3)), (y1, z1), (y0, z1), (y0, z1 - min(40.0, h * 0.3))]
    return [(y0, z0), (y1, z0), (y1, z1), (y0, z1)]


def length_of(mesh: str) -> float:
    m = re.search(r"(\d+)m$", mesh)
    return float(m.group(1)) * 100 if m else 1000.0


def profile(code: str) -> dict | None:
    """The cross-section of road type `code` (citygen.ROAD_TYPES), or None for a piece that is not a road
    (paths, canals, docks, runways keep their own meshes)."""
    if code not in cg.ROAD_TYPES or cg.ROAD_TYPES[code][0].split("/")[-1].startswith(("SM_Path", "SM_Water", "SM_Air")):
        return None
    mesh, half, lanes, _w, _what = cg.ROAD_TYPES[code]
    walk = cg.WALK.get(code, 0.0)
    c = half - walk  # carriageway half
    structure = mesh.split("/")[-1] in cg.STRUCTURES
    base = -DECK_CM if structure else -ROAD_CM
    parts = [("road", "Asphalt", -c, c, base, 0.0)]
    for sd in (-1, 1):
        if walk:  # kerb + sidewalk, 15 cm up, on the deck or the ground
            parts.append(("sidewalk", "Pavers", *sorted((sd * c, sd * half)), base, KERB_CM))
        if structure:  # a parapet on each edge
            top = KERB_CM if walk else 0.0
            parts.append(("parapet", "Plinth", *sorted((sd * (half - 35), sd * half)), top, top + PARAPET_CM))
    if structure:
        parts[0] = ("deck", "Plinth", -half, half, base, -20.0)
        parts.insert(1, ("road", "Asphalt", -c, c, -20.0, 0.0))
    if code in MEDIAN:
        parts.append(("median", "Plinth", -30.0, 30.0, 0.0, 85.0))
    paint = []
    if code not in NO_PAINT and lanes:
        for sd in (-1, 1):
            paint.append((sd * (c - 45), 15.0, 0.0, "Paint"))  # edge lines
        w = 2 * c / lanes
        for i in range(1, lanes):
            y = -c + i * w
            if i * 2 == lanes and code in MEDIAN:
                continue  # the median barrier is the middle
            if i * 2 == lanes and code not in ("1W", "1S", "ER"):  # two-way: a double yellow centre line
                paint += [(y - 14, 12.0, 0.0, "PaintYellow"), (y + 14, 12.0, 0.0, "PaintYellow")]
            else:
                paint.append((y, 15.0, DASH, "Paint"))
    return {"code": code, "mesh": mesh.split("/")[-1], "length": length_of(mesh), "half": half, "carriageway": c,
            "sidewalk": walk, "structure": structure, "lanes": lanes,
            "parts": [[n, s, round(y0, 1), round(y1, 1), z0, z1] for n, s, y0, y1, z0, z1 in parts],
            "shapes": [[n, s, [[round(y, 1), round(z, 1)] for y, z in shape(n, y0, y1, z0, z1)]]
                       for n, s, y0, y1, z0, z1 in parts],
            "paint": [[round(y, 1), wd, d, s] for y, wd, d, s in paint]}


# Pieces that sit on ONE point, a ramp's end (citygen.weld_ends puts them there): drawn in plan, the ramp's end at
# x 0 and the piece running on ahead along +X to the road (so the ramp's parapets stop where it starts).
# outline: the slab's top view [(x, y)], z SLAB..0; paint: [[[(x, y)...], slot]] 1 cm up.
CONNECTORS = {
    "SM_Road_RampFlare15m": "where a ramp meets a street at grade: the ramp's last 15 m widen from 13 to 21 m so its "
                            "edges run into the street's kerb lines, edge lines along the flare, a stop line at the street",
    "SM_Road_RampGore30m": "where a ramp leaves or joins the freeway (or another ramp): its last 30 m widen from 13 to "
                           "20 m, filling the wedge between ramp and freeway edge, chevrons painted in the gore",
}


def _bar(p, q, w):
    """A painted line from p to q, w cm wide, as a quad [(x, y)]."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    n = (dx * dx + dy * dy) ** 0.5 or 1.0
    ox, oy = -dy / n * w / 2, dx / n * w / 2
    return [(p[0] + ox, p[1] + oy), (p[0] - ox, p[1] - oy), (q[0] - ox, q[1] - oy), (q[0] + ox, q[1] + oy)]


def connector(mesh: str) -> dict | None:
    """A connector piece's plan (CONNECTORS)."""
    if mesh not in CONNECTORS:
        return None
    flare = mesh == "SM_Road_RampFlare15m"
    L, a, b = (1500.0, 650.0, 1050.0) if flare else (3000.0, 650.0, 1000.0)
    outline = [(0.0, -a), (L, -b), (L, b), (0.0, a)]
    paint = [[_bar((0.0, sd * (a - 45)), (L, sd * (b - 45)), 15.0), "Paint"] for sd in (-1, 1)]
    if flare:
        paint.append([_bar((L - 150.0, -(b - 90)), (L - 150.0, b - 90), 40.0), "Paint"])  # stop line at the street
    else:
        for sd in (-1, 1):  # gore chevrons in each widening wedge
            for x in (800.0, 1600.0, 2400.0):
                y0 = a - 45 + (b - a) * x / L
                paint.append([_bar((x - 300, sd * (a - 200)), (x, sd * y0), 30.0), "Paint"])
    r = lambda pts: [[round(x, 1), round(y, 1)] for x, y in pts]
    return {"mesh": mesh, "plan": True, "length": L, "half": b, "slab": -ROAD_CM, "outline": r(outline),
            "paint": [[r(poly), s] for poly, s in paint], "spec": CONNECTORS[mesh]}


def bundled(mesh: str) -> str:
    """The model of this road piece that ships with the plugin (assets/roads/<mesh>.fbx: forward +X, up +Z, cm,
    slots = SLOTS), or '' when there is none."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "roads", mesh + ".fbx")
    return path if os.path.isfile(path) else ""


def all_profiles() -> dict:
    """{mesh name: profile} for every road piece (a mesh two codes share keeps the first) and every connector."""
    out: dict = {m: connector(m) for m in CONNECTORS}
    for code in cg.ROAD_TYPES:
        p = profile(code)
        if p and p["mesh"] not in out:
            out[p["mesh"]] = p
    return out


def kit_parts(p: dict) -> list:
    """The street-kit parts (listener road_network_street_kit) that build this profile as one low-poly mesh:
    bands as slabs cut every metre along X (so the spline mesh bends smoothly), paint as thin boxes 1 cm proud."""
    if p.get("plan"):  # a connector: its outline extruded up from the slab's bottom, the paint 1 cm on it
        return [("poly", SLOTS.index("Asphalt"), (0, 0, p["slab"]), (0, 0, 0), (p["outline"], -p["slab"]))] + \
            [("poly", SLOTS.index(s), (0, 0, 0), (0, 0, 0), (poly, 1)) for poly, s in p["paint"]]
    L = p["length"]
    steps = max(1, int(L // 100))
    out = []
    for _n, slot, y0, y1, z0, z1 in p["parts"]:
        out.append(("slab", SLOTS.index(slot), (0, (y0 + y1) / 2, z0), (0, 0, 0), (L, y1 - y0, z1 - z0, steps)))
    for y, wd, dash, slot in p["paint"]:
        if not dash:
            out.append(("slab", SLOTS.index(slot), (0, y, 0), (0, 0, 0), (L, wd, 1, steps)))
            continue
        x = -L / 2
        while x < L / 2 - 1:
            out.append(("box", SLOTS.index(slot), (x + dash / 2, y, 0), (0, 0, 0), (dash, wd, 1)))
            x += PAINT_PERIOD
    return out


def kit_entry(p: dict) -> list:
    """[slots, parts, folder under WorldGen] as the listener's _STREET_KIT takes it."""
    code_mesh = cg.ROAD_TYPES[p["code"]][0] if "code" in p else ""
    return [SLOTS, kit_parts(p), "Roads/Elevated" if "/" in code_mesh else "Roads"]


def spec(p: dict) -> str:
    """The profile in words, for an AI modelling the piece by hand."""
    parts = "; ".join(f"{n} ({s}) y {y0:g}..{y1:g}, z {z0:g}..{z1:g}" for n, s, y0, y1, z0, z1 in p["parts"])
    paint = "; ".join(f"{'dashed ' + str(int(d)) + ' cm every ' + str(int(PAINT_PERIOD)) if d else 'solid'} "
                      f"{s} {w:g} cm wide at y {y:g}" for y, w, d, s in p["paint"])
    return (f"{p['mesh']}: {p['length']:g} cm long on X (-{p['length'] / 2:g}..{p['length'] / 2:g}), "
            f"{p['half'] * 2:g} cm wide, road surface z 0. Parts across Y: {parts}. Paint 1 cm proud: "
            f"{paint or 'none'}. Loops every 100 cm along X so it bends.")


def _demo() -> None:
    ps = all_profiles()
    assert {"SM_Road_Freeway_Lane20m", "SM_Elev_Deck20m", "SM_Road_BoulevardSidewalk_Straight20m",
            "SM_Road_BridgeSidewalk_Straight20m", "SM_Road_OneLaneSidewalk_Straight10m"} <= set(ps), sorted(ps)
    for m in CONNECTORS:  # connectors: the outline counter-clockwise, every paint stroke on the slab
        c = ps[m]
        area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(c["outline"], c["outline"][1:] + c["outline"][:1])) / 2
        assert area > 0 and all(-5 <= x <= c["length"] + 5 and abs(y) <= c["half"] + 1 for poly, _s in c["paint"]
                                for x, y in poly), m
    ps = {k: v for k, v in ps.items() if not v.get("plan")}
    for p in ps.values():  # every part inside the piece's width, every paint line on the carriageway
        assert all(-p["half"] - 0.1 <= y0 < y1 <= p["half"] + 0.1 for _n, _s, y0, y1, *_z in p["parts"]), p["mesh"]
        assert all(abs(y) + w / 2 <= p["carriageway"] for y, w, *_r in p["paint"]), p["mesh"]
    fw = ps["SM_Road_Freeway_Lane20m"]
    assert any(n == "median" for n, *_r in fw["parts"]) and sum(1 for r in fw["paint"] if r[2]) == 4, fw["paint"]
    deck = ps["SM_Elev_Deck20m"]
    assert sum(1 for n, *_r in deck["parts"] if n == "parapet") == 2 and deck["parts"][0][4] == -DECK_CM
    bw = ps["SM_Road_BridgeSidewalk_Straight20m"]
    assert {n for n, *_r in bw["parts"]} >= {"deck", "sidewalk", "parapet"}, "a boulevard bridge keeps its sidewalks"
    two = ps["SM_Road_TwoLaneSidewalk_Straight10m"]
    assert [r[3] for r in two["paint"]].count("PaintYellow") == 2, "two-way: double yellow"
    assert kit_entry(connector("SM_Road_RampGore30m"))[1][0][0] == "poly"
    assert not [m for m in all_profiles() if not bundled(m)], "every road piece ships with the plugin as a model"
    kp = kit_parts(fw)
    dashes = [k for k in kp if k[0] == "box"]
    assert len(dashes) == 4 * 2 and all(-1000 <= k[2][0] - k[4][0] / 2 and k[2][0] + k[4][0] / 2 <= 1000 for k in dashes)
    assert "median (Plinth)" in spec(fw)
    for p in ps.values():  # every cross-section closed, counter-clockwise, inside its part's box
        for (n, s, poly), (_n, _s, y0, y1, z0, z1) in zip(p["shapes"], p["parts"]):
            area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1])) / 2
            assert area > 0 and all(y0 - 0.1 <= y <= y1 + 0.1 and z0 - 0.1 <= z <= z1 + 0.1 for y, z in poly), (p["mesh"], n, poly)
    print("road profiles ok", len(ps), "pieces;", len(kp), "parts on the freeway")


if __name__ == "__main__":
    _demo()

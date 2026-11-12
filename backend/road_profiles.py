"""Every road piece's cross-section: the ONE source for the panel's preview, the low-poly mesh City Builder builds
in UEFN when the project has no model of that piece, and the modelling spec an AI gets in the build brief.

Units cm. A piece runs along +X (-length/2 .. +length/2, it bends as a spline mesh), Y is across (0 = centre
line), z 0 is the road surface. parts: bands the full length, (name, slot, y0, y1, z0, z1). paint: lines on the
surface, (y, width, dash, slot): dash 0 = solid, else that many cm painted in every PAINT_PERIOD (so dashes
line up from one 10 or 20 m tile to the next).
"""

from __future__ import annotations

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
            "paint": [[round(y, 1), wd, d, s] for y, wd, d, s in paint]}


def all_profiles() -> dict:
    """{mesh name: profile} for every road piece (a mesh two codes share keeps the first)."""
    out: dict = {}
    for code in cg.ROAD_TYPES:
        p = profile(code)
        if p and p["mesh"] not in out:
            out[p["mesh"]] = p
    return out


def kit_parts(p: dict) -> list:
    """The street-kit parts (listener road_network_street_kit) that build this profile as one low-poly mesh:
    bands as slabs cut every metre along X (so the spline mesh bends smoothly), paint as thin boxes 1 cm proud."""
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
    code_mesh = cg.ROAD_TYPES[p["code"]][0]
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
    kp = kit_parts(fw)
    dashes = [k for k in kp if k[0] == "box"]
    assert len(dashes) == 4 * 2 and all(-1000 <= k[2][0] - k[4][0] / 2 and k[2][0] + k[4][0] / 2 <= 1000 for k in dashes)
    assert "median (Plinth)" in spec(fw)
    print("road profiles ok", len(ps), "pieces;", len(kp), "parts on the freeway")


if __name__ == "__main__":
    _demo()

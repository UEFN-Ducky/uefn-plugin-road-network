"""The piece manifest: every mesh a City Builder city is made of, with the exact spec to model it.

An AI (or a person) reads this and builds each piece in Blender: name, folder, size in cm, pivot, which way
it faces, material slots, and which layer / system places it. Nothing about the city's look lives in a chat:
the manifest, the layers and the rules come from the plugin.
"""

from __future__ import annotations

from . import citygen as cg
from . import kit_bake as kb
from . import pcg_layers as pl

CONVENTIONS = {
    "units": "centimetres, real-world scale (a door is 210 cm tall, a storey 400 cm).",
    "axes": "Z up. +X is the FRONT: the facade with the door faces the street along +X. Y is the width.",
    "pivot": "bottom centre of the footprint (x 0, y 0, z 0 on the ground / floor line); roads: the deck "
             "centre line at z 0, running along +X (they bend as spline meshes).",
    "materials": "one slot per surface, named after what it is so City Builder's world-PBR instances map it: "
                 "Glass, Brick, Concrete, Stone, Trim, Roof, Metal, Frame, Wood, Siding, Gold, Neon, Floor, Foliage. "
                 "Glass slots become see-through M_City_Glass.",
    "budget": "low poly: instanced by the thousand (buildings < 5k tris, props < 1k, trees / bushes < 2k).",
    "collision": "simple boxes / convex hulls (UCX_) for walls and floors; none on leaves and bushes.",
    "export": "FBX, apply transforms, forward +X, up +Z, one mesh per file named exactly as listed.",
    "import": "UEFN Content Browser -> the listed folder under <project>/WorldGen/; the name must match.",
}
WANTED = [  # not in the project yet: model them, then put them on a layer (meshes) or replace the stand-in
    ("SM_Bld_School", "building", "school", "2-3 storey school, L or courtyard plan, big windows, entrance canopy"),
    ("SM_Bld_FireStation", "building", "fire_station", "2 storeys, 3 tall garage doors on +X, hose tower"),
    ("SM_Bld_Factory", "building", "factory", "saw-tooth roof shed, loading docks on +X, chimney"),
    ("SM_Bld_Warehouse", "building", "warehouse", "big box shed, roll-up doors on +X, low parapet"),
    ("SM_Bld_Mall", "building", "mall", "1-2 storey mall, glass entrance atrium on +X, flat roof"),
    ("SM_Bld_GasStation", "building", "gas_station", "kiosk + canopy over 4 pumps, canopy toward +X"),
    ("SM_Kit_Crosswalk", "street", "", "zebra crossing decal slab 400 x 300 x 2 cm, white stripes"),
    ("SM_Kit_CurbRamp", "street", "", "kerb ramp 150 x 120 cm, 8 % slope, tactile strip (where a walkway meets the kerb)"),
    ("SM_Kit_DrivewayApron", "street", "", "drop kerb + apron 460 deep x 400 wide, max 10 % (Toronto T-310.050-1)"),
    ("SM_Kit_SidewalkCorner", "street", "", "quarter-round sidewalk corner, radius 300 cm, kerb 15 cm high"),
]


def _size(name: str) -> list:
    b = kb.KIT[name][1:]
    return [round(b[3] - b[0]), round(b[4] - b[1]), round(b[5] - b[2])]


def manifest(root: str = "/<project>") -> dict:
    """{conventions, pieces: [...], wanted: [...]} - every piece with what places it and its spec."""
    users: dict = {}
    for role, opts in kb.BUILDINGS.items():
        for parts in opts:
            for part in parts:
                users.setdefault(part, set()).add(f"buildings: {role}")
    for role, names in kb.PROPS.items():
        for n in names:
            users.setdefault(n, set()).add(f"layer role: {role}")
    for role, n in kb.FLAT.items():
        users.setdefault(n, set()).add(f"flat lot: {role}")
    for role, n in kb.INTERIOR.items():
        users.setdefault(n, set()).add(f"interior: {role}")
    for n in ("SM_Kit_FloorPlate", "SM_Kit_FloorRound", "SM_Kit_FloorSolid", "SM_Kit_StairCore", "SM_Kit_ElevatorCar"):
        users.setdefault(n, set()).add("interior: every building")
    for n in ("SM_Kit_Plinth", "SM_Kit_PathSlab", "SM_Kit_RetainingWall"):
        users.setdefault(n, set()).add("lot grading: pad, walkway / frontage, uphill wall")
    pieces = []
    for name, (folder, *_b) in kb.KIT.items():
        pieces.append({"name": name, "folder": f"WorldGen/{folder}", "path": kb.path(name, root),
                       "size_cm": _size(name), "used_by": sorted(users.get(name, {"kit"})),
                       "made_by": "City Builder (GeometryScript stand-in: a Blender model with this name and size "
                                  "replaces it)" if folder == "Kit/Street" else "Blender / imported kit"})
    for code, (mesh, half, lanes, walk, what) in cg.ROAD_TYPES.items():
        length = 2000 if "20m" in mesh else 4000 if "40m" in mesh else 1000
        pieces.append({"name": mesh.rsplit("/", 1)[-1], "folder": "WorldGen/Roads" + ("/Elevated" if "/" in mesh else ""),
                       "size_cm": [length, round(half * 2), 30], "used_by": [f"road {code}: {what}"],
                       "made_by": "Blender road kit",
                       "spec": f"{lanes} lanes{', kerb + sidewalk both sides' if walk else ''}; deck top at z 0, "
                               f"centre line on X, slot Asphalt (+ Curb, Sidewalk); bends as a spline mesh, so "
                               f"enough length loops (every 100 cm)"})
    pieces.append({"name": "SM_JK_<world>_<actor>_<node>", "folder": "WorldGen/Roads/Junctions/Kit", "size_cm": None,
                   "used_by": ["every junction"], "made_by": "City Builder builds each one to fit its arms (no Blender)"})
    want = []
    for name, kind, role, what in WANTED:
        spec = {"name": name, "kind": kind, "spec": what, "folder": "WorldGen/Kit" if kind == "building" else "WorldGen/Kit/Street"}
        layer = next((l for l in pl.CITY_BUILDINGS if l.get("role") == role), None) if role else None
        if layer:  # the size the layer's lots ask for, so the model fits without stretching
            spec["size_cm"] = [round(layer["depth_m"] * 100), round(layer["width_m"] * 100),
                               round((layer["height_min_m"] + layer["height_max_m"]) * 50)]
            spec["then"] = (f"add it to the '{layer['name']}' layer's meshes with this size_cm "
                            f"(road_network_pcg_layer_save): lots fit it, no plugin code")
        want.append(spec)
    return {"conventions": CONVENTIONS, "pieces": pieces, "wanted": want, "rules": cg.RULES}


def _demo() -> None:
    m = manifest("/R")
    names = {p["name"] for p in m["pieces"]}
    for role, opts in kb.BUILDINGS.items():
        assert all(part in names for parts in opts for part in parts), role
    assert all(n in names for ns in kb.PROPS.values() for n in ns)
    assert {"SM_Road_TwoLane_Straight10m", "SM_Kit_RetainingWall", "SM_Kit_StairCore"} <= names
    assert all(p["size_cm"] for p in m["pieces"] if not p["name"].startswith("SM_JK_"))
    school = next(w for w in m["wanted"] if w["name"] == "SM_Bld_School")
    assert school["size_cm"][0] > 1000 and "Schools" in school["then"], school
    print("pieces ok", len(m["pieces"]), "pieces,", len(m["wanted"]), "wanted")


if __name__ == "__main__":
    _demo()

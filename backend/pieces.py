"""The piece manifest: every mesh a City Builder city is made of, with the exact spec to model it.

An AI (or a person) reads this and builds each piece in Blender: name, folder, size in cm, pivot, which way
it faces, material slots, and which layer / system places it. Nothing about the city's look lives in a chat:
the manifest, the layers and the rules come from the plugin.
"""

from __future__ import annotations

from . import citygen as cg
from . import grammar
from . import kit_bake as kb
from . import pcg_layers as pl
from . import road_profiles as rp

CONVENTIONS = {
    "units": "centimetres, real-world scale (a door is 210 cm tall, a storey 400 cm).",
    "axes": "Z up. +X is the FRONT: the facade with the door faces the street along +X. Y is the width.",
    "pivot": "bottom centre of the footprint (x 0, y 0, z 0 on the ground / floor line); roads: the deck "
             "centre line at z 0, running along +X (they bend as spline meshes).",
    "roads": "ONE mesh per road type: the deck, both kerbs and both sidewalks are the same mesh (slots Asphalt, Curb, "
             "Sidewalk), never separate sidewalk pieces. The sidewalk is the outer `sidewalk_cm` of each side, 15 cm "
             "above the deck. Junctions are built by City Builder as one mesh too: deck, stop lines, crossings, kerbs "
             "and the sidewalk corners that join the arms' sidewalks. Every road piece carries its `profile`: the exact "
             "cross-section (deck, kerbs, sidewalks, parapets, median, lane paint) the panel draws and City Builder "
             "builds itself when the project has no model; a hand-made model follows it part for part.",
    "bridges": "A bridge is its deck and what it stands on, all placed by City Builder: the deck (the road's own deck piece, SM_Elev_Deck20m "
               "for 4+ lanes, SM_Road_Bridge_Straight20m for streets; low poly: a 1.5 m slab with a concrete barrier "
               "on each edge, top at z 0; the SM_Road_Bridge* pieces also carry I-girders 1.2 m deep under the slab "
               "about every 3.5 m and a coping on each parapet), an SM_Kit_BridgeAbutment where each end meets the "
               "road on the ground (wall under the deck's end, bearing shelf, a wing wall each side), SM_Kit_ApproachWall blocks under every stretch too low for piers (the ramp's own side walls, from 1 m in the ground up into the slab), pier bents every 30 m (SM_Kit_BridgePier columns from the ground or lake "
               "bed to SM_Kit_PierCap under the deck; two columns under a deck 16 m or wider) and, over wide water, "
               "two towers astride the deck: a leg outside each edge (SM_Kit_BridgePier stretched from the bed to 60 or "
               "90 m over the road), three beams across (SM_Kit_PierCap: under the deck, over the traffic, at the top) "
               "and stay cables from each leg's top down to the deck's edge (SM_Kit_BridgeStay: a bar from 1 m up over "
               "its origin down to 1 m out along +X, stretched on X and Z to each cable); the deck the cables carry has "
               "no piers. To make them real, model these pieces at their bounds_cm: column and cap stretch on Z / Y, "
               "so keep their detail at the ends.",
    "materials": "one slot per surface, named after what it is so City Builder's world-PBR instances map it: "
                 "Glass, Brick, Concrete, Stone, Trim, Roof, Metal, Frame, Wood, Siding, Gold, Neon, Floor, Foliage. "
                 "Glass slots become see-through M_City_Glass.",
    "budget": "low poly: instanced by the thousand (buildings < 5k tris, props < 1k, trees / bushes < 2k).",
    "collision": "simple boxes / convex hulls (UCX_) for walls and floors; none on leaves and bushes.",
    "export": "FBX, apply transforms, forward +X, up +Z, one mesh per file named exactly as listed.",
    "import": "UEFN Content Browser -> the listed folder under <project>/WorldGen/; the name must match.",
    "import_check": "After import a mesh must measure its `bounds_cm` (min x, y, z, max x, y, z), not just its size: "
                    "a wall runs 0..512 on +X with its thickness on +Y, a roof slab covers 0..512 on +X and +Y. If Y "
                    "comes in mirrored (bounds on -Y instead), mirror Y in the export and import again: placement is "
                    "never changed to suit a wrong mesh.",
    "ground": "City Builder cuts the terrain itself (roads first, then every lot level) and writes it as the heightmap: "
              "road_network_ground_fit must show 0 buried / floating buildings and names the street points still off.",
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
    for n in ("SM_Kit_Plinth", "SM_Kit_PathSlab"):
        users.setdefault(n, set()).add("lot grading: foundation (every building), frontage")
    pieces = []
    for name, (folder, *_b) in kb.KIT.items():
        pieces.append({"name": name, "folder": f"WorldGen/{folder}", "path": kb.path(name, root),
                       "size_cm": _size(name), "bounds_cm": list(kb.KIT[name][1:]), "used_by": sorted(users.get(name, {"kit"})),
                       "made_by": "City Builder (GeometryScript stand-in: a Blender model with this name and size "
                                  "replaces it)" if folder == "Kit/Street" else "Blender / imported kit"})
    for code, (mesh, half, lanes, walk, what) in cg.ROAD_TYPES.items():
        length = 2000 if "20m" in mesh else 4000 if "40m" in mesh else 1000
        prof = rp.profile(code)
        if prof:  # the exact cross-section the panel draws and City Builder builds when the project has no model
            pieces.append({"name": prof["mesh"], "folder": "WorldGen/" + rp.kit_entry(prof)[2], "used_by": [f"road {code}: {what}"],
                           "size_cm": [prof["length"], round(half * 2), round(max(z1 for *_r, z1 in prof["parts"])
                                                                            - min(z0 for *_r, z0, _z in prof["parts"]))],
                           "sidewalk_cm": prof["sidewalk"], "carriageway_cm": round(prof["carriageway"] * 2),
                           "profile": {"parts": prof["parts"], "shapes": prof["shapes"], "paint": prof["paint"],
                                       "paint_period_cm": rp.PAINT_PERIOD,
                                       "row": "parts: name, slot, y0, y1, z0, z1; shapes: name, slot, cross-section "
                                              "[[y, z], ...] swept along X (an edge loop every 50 cm); paint: y, width, "
                                              "dash cm (0 solid), slot, 1 cm proud"},
                           "spec": rp.spec(prof),
                           "made_by": "City Builder builds it from this profile when the project has none (street kit); "
                                      "a Blender model with this name, size and slots replaces it"})
            continue
        pieces.append({"name": mesh.rsplit("/", 1)[-1], "folder": "WorldGen/Roads" + ("/Elevated" if "/" in mesh else ""),
                       "size_cm": [length, round(half * 2), 30], "used_by": [f"road {code}: {what}"],
                       "sidewalk_cm": cg.WALK.get(code, 0),
                       # the carriageway is the middle; each side's outer sidewalk_cm is kerb + sidewalk, 15 cm up
                       "carriageway_cm": round((half - cg.WALK.get(code, 0)) * 2),
                       "where": "city streets" if code in cg.WALK else "city streets become " + cg.CITY_STREET[code]
                                if code in cg.CITY_STREET else "outside the street grid",
                       "made_by": "City Builder (street kit; a Blender model with this name replaces it)"
                                  if code in ("SW", "WK", "DW") else "Blender road kit",
                       "spec": f"{lanes} lanes{', kerb + sidewalk both sides' if walk else ''}; deck top at z 0, "
                               f"centre line on X, slot Asphalt (+ Curb, Sidewalk); bends as a spline mesh, so "
                               f"enough length loops (every 100 cm)"})
    for name in rp.CONNECTORS:  # the pieces on every ramp's end (citygen.weld_ends places them)
        c = rp.connector(name)
        pieces.append({"name": name, "folder": "WorldGen/Roads", "size_cm": [c["length"], c["half"] * 2, -c["slab"]],
                       "used_by": ["every ramp end: a gore where it joins a freeway or ramp, a flare at a street"],
                       "plan": {"outline": c["outline"], "paint": c["paint"], "slab_cm": c["slab"],
                                "row": "top view [x, y]: the ramp's end at x 0, the ramp comes in from -X; slab from "
                                       "slab_cm up to z 0; paint 1 cm proud"},
                       "spec": c["spec"],
                       "made_by": "City Builder builds it from this plan when the project has none (street kit); a "
                                  "Blender model with this name, pivot and size replaces it"})
    for name in grammar.modules():  # the BuildingKit modules a `build: modular` layer stacks by grammar
        pieces.append({"name": name, "folder": f"WorldGen/{grammar.FOLDER}", "path": f"{root}/WorldGen/{grammar.FOLDER}/{name}.{name}",
                       "size_cm": [grammar.BOUNDS[name][k + 3] - grammar.BOUNDS[name][k] for k in range(3)],
                       "bounds_cm": list(grammar.BOUNDS[name]),
                       "used_by": ["modular buildings (grammar.py)"],
                       "made_by": "BuildingKit.blend (Blender, metres: export x100). Walls run +X from the pivot, "
                                  "outside on -Y, one storey tall"})
    pieces.append({"name": "SM_JK_<world>_<actor>_<node>", "folder": "WorldGen/Roads/Junctions/Kit", "size_cm": None,
                   "used_by": ["every junction"],
                   "made_by": "City Builder builds each one to fit its arms (no Blender): one mesh with the deck, "
                              "painted stop lines and crossings, kerbs and sidewalk corners (slots Asphalt, Paint, Sidewalk)"})
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
    assert {"SM_Road_TwoLane_Straight10m", "SM_Kit_Plinth", "SM_Kit_ApproachWall", "SM_Kit_StairCore", "SM_Kit_TreePit"} <= names
    assert set(grammar.modules()) <= names, "every module the grammar places is in the manifest"
    assert all(p["size_cm"] for p in m["pieces"] if not p["name"].startswith("SM_JK_"))
    assert {"SM_Kit_BridgePier", "SM_Kit_PierCap", "SM_Elev_Deck20m"} <= names and "bridges" in m["conventions"]
    school = next(w for w in m["wanted"] if w["name"] == "SM_Bld_School")
    assert school["size_cm"][0] > 1000 and "Schools" in school["then"], school
    print("pieces ok", len(m["pieces"]), "pieces,", len(m["wanted"]), "wanted")


if __name__ == "__main__":
    _demo()

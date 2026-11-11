"""Bake a City Builder preview into PCG points for the project's own kit (WorldGen/City, WorldGen/Kit).

Every previewed lot and street item becomes a point on one of the kit's meshes: a building takes the kit
building whose height is closest to the lot's (towers are several parts that share one pivot, so every
part gets the same point), scaled to the lot's width, depth and height; street furniture keeps its
preview scale. The result is {mesh path: [[x, y, z, yaw, sx, sy, sz], ...]} for Create Points ->
Static Mesh Spawner pairs, so the city in UEFN is the city in the preview.
"""

from __future__ import annotations

import math

# name: (folder under WorldGen, bounds min x/y/z, max x/y/z in cm) - measured from the imported meshes
KIT = {
    "SM_City_CT_Apartment": ("City", -832, -776, 0, 832, 776, 1644),
    "SM_City_CT_MidRise": ("City", -842, -1032, 0, 842, 1032, 2466),
    "SM_City_CT_ShopRow": ("City", -585, -776, 0, 584, 776, 492),
    "SM_City_CT_ShopRow2": ("City", -586, -776, 0, 586, 776, 876),
    "SM_City_DT_BandTower_P0": ("City", -1174, -1174, 0, 1174, 1174, 2324),
    "SM_City_DT_BandTower_P1": ("City", -1025, -1025, 1940, 1025, 1025, 5012),
    "SM_City_DT_BandTower_P2": ("City", -1029, -1029, 4628, 1029, 1029, 7852),
    "SM_City_DT_CrownTower_P0": ("City", -3081, -3081, 0, 3081, 3081, 2825),
    "SM_City_DT_CrownTower_P1": ("City", -2143, -2147, 2713, 2145, 2144, 6578),
    "SM_City_DT_CrownTower_P2": ("City", -1595, -1594, 6553, 1595, 1596, 11068),
    "SM_City_DT_GlassTower_P0": ("City", -1174, -1174, 0, 1174, 1174, 2324),
    "SM_City_DT_GlassTower_P1": ("City", -1025, -1025, 1940, 1025, 1025, 5012),
    "SM_City_DT_GlassTower_P2": ("City", -1025, -1025, 4628, 1025, 1025, 7341),
    "SM_City_DT_GlassTower_P3": ("City", -1029, -1029, 7316, 1029, 1029, 10891),
    "SM_City_DT_RoundTower_R10_P0": ("City", -1032, -1036, 0, 1032, 1024, 8473),
    "SM_City_DT_RoundTower_R10_P1": ("City", -1092, -1119, 8089, 1092, 1119, 12220),
    "SM_City_DT_RoundTower_R15_P0": ("City", -1541, -1546, 0, 1541, 1546, 5426),
    "SM_City_DT_RoundTower_R15_P1": ("City", -1541, -1546, 5401, 1541, 1546, 10128),
    "SM_City_DT_SlabTower_P0": ("City", -1174, -1686, 0, 1174, 1686, 1656),
    "SM_City_DT_SlabTower_P1": ("City", -1025, -1537, 1556, 1025, 1537, 3860),
    "SM_City_DT_SlabTower_P2": ("City", -1025, -1537, 3476, 1025, 1537, 5780),
    "SM_City_DT_SlabTower_P3": ("City", -1025, -1537, 5396, 1025, 1537, 7700),
    "SM_City_DT_SlabTower_P4": ("City", -1029, -1541, 7680, 1029, 1541, 8610),
    "SM_City_DT_Tower3_P0": ("City", -918, -918, 0, 918, 918, 3192),
    "SM_City_DT_Tower3_P1": ("City", -773, -773, 3092, 773, 773, 6252),
    "SM_City_HS_House2": ("City", -587, -520, 0, 587, 520, 1146),
    "SM_City_HS_HouseWide": ("City", -542, -775, 0, 542, 775, 762),
    "SM_City_HS_Modern": ("City", -622, -828, 0, 622, 828, 823),
    "SM_City_RC_Mansion_P0": ("City", -893, -1121, 0, 892, 1121, 905),
    "SM_City_RC_Mansion_P1": ("City", -875, -949, 867, 666, 1104, 917),
    "SM_City_RC_ModernVilla": ("City", -1134, -1596, 0, 1134, 1596, 823),
    "SM_Bld_Hospital": ("Kit", -4400, -3300, 0, 4400, 4120, 4435),
    "SM_Bld_House_Flat": ("Kit", -700, -600, 0, 700, 694, 850),
    "SM_Bld_House_Gable": ("Kit", -700, -600, 0, 700, 694, 1100),
    "SM_Bld_House_L": ("Kit", -700, -600, 0, 700, 694, 740),
    "SM_Bld_Police": ("Kit", -3500, -2600, 0, 4430, 2600, 4380),
    "SM_Sky04_DecoCrown": ("Kit", -1280, -1130, 0, 1280, 1130, 8260),
    "SM_Sky05_Strawberry": ("Kit", -1100, -1030, 0, 1100, 1030, 8510),
    "SM_Sky06_GoldRings": ("Kit", -1100, -1230, 0, 1100, 1230, 7560),
    "SM_Sky07_OrbNeedle": ("Kit", -1000, -1060, 0, 1000, 1060, 9550),
    "SM_Sky10_TwinSpire": ("Kit", -800, -830, 0, 800, 830, 6020),
    "SM_Sky11_HaloCylinder": ("Kit", -1000, -1050, 0, 1000, 1050, 5500),
    "SM_Sky13_GoldWedge": ("Kit", -1000, -930, 0, 1000, 930, 5910),
    "SM_Sky14_CrownSpire": ("Kit", -1000, -930, 0, 1000, 930, 6550),
    "SM_Sky15_SteppedGarden": ("Kit", -1100, -1030, 0, 1100, 1030, 7090),
    "SM_Sky16_SpiralRibbon": ("Kit", -1000, -1030, 0, 1000, 1029, 6410),
    "SM_V4_Container_Blue": ("Kit", -610, -123, 0, 610, 123, 260),
    "SM_V4_Container_Green": ("Kit", -610, -123, 0, 610, 123, 260),
    "SM_V4_Container_Orange": ("Kit", -610, -123, 0, 610, 123, 260),
    "SM_V4_Container_Red": ("Kit", -610, -123, 0, 610, 123, 260),
    "SM_V4_Crane": ("Kit", -700, -900, 0, 700, 3700, 3150),
    "SM_StreetLamp_NeonArm": ("Kit", -95, -95, 0, 383, 95, 777),
    "SM_StreetLamp_RoyalCrown": ("Kit", -100, -100, 0, 100, 100, 918),
    "SM_StreetLamp_TwinArm": ("Kit", -340, -95, 0, 340, 95, 880),
    "SM_V4_TreeRound": ("Kit", -286, -272, 0, 299, 272, 900),
    "SM_V4_TreePine": ("Kit", -280, -280, 0, 280, 280, 1075),
    "SM_Prop_BusShelter": ("Kit", -200, -93, 0, 268, 251, 300),
    "SM_Prop_Bench": ("Kit", -97, -36, 0, 97, 31, 111),
    "SM_Prop_TrashCan": ("Kit", -37, -37, 0, 37, 37, 95),
    "SM_Prop_RecycleBin": ("Kit", -33, -30, 0, 33, 32, 107),
    "SM_Prop_FireHydrant": ("Kit", -33, -27, 0, 33, 27, 83),
    "SM_Prop_ParkingMeter": ("Kit", -22, -18, 0, 22, 18, 165),
    "SM_Prop_Planter": ("Kit", -98, -46, 0, 98, 46, 107),
    # the street kit City Builder makes itself (listener road_network_street_kit), sizes as built there
    "SM_Kit_TrafficLight": ("Kit/Street", -15, -25, 0, 380, 25, 600),
    "SM_Kit_StopSign": ("Kit/Street", -40, -6, 0, 40, 6, 290),
    "SM_Kit_StreetSign": ("Kit/Street", -50, -50, 0, 50, 50, 330),
    "SM_Kit_Billboard": ("Kit/Street", -30, -600, 0, 30, 600, 1150),
    "SM_Kit_PowerPole": ("Kit/Street", -20, -120, 0, 20, 120, 1100),
    "SM_Kit_ParkingLot": ("Kit/Street", -50, -50, 0, 50, 50, 8),
    "SM_Kit_PathSlab": ("Kit/Street", -50, -50, 0, 50, 50, 8),
    "SM_Kit_Plinth": ("Kit/Street", -50, -50, -100, 50, 50, 0),
    # interiors (also listener-built): storey slabs with glowing ceiling panels under them (Plate / Round have
    # a well in the middle for the core), the one-storey stair + elevator core, the elevator car, furniture
    "SM_Kit_FloorPlate": ("Kit/Street", -50, -50, -4, 50, 50, 15),
    "SM_Kit_FloorRound": ("Kit/Street", -50, -50, -4, 50, 50, 15),
    "SM_Kit_FloorSolid": ("Kit/Street", -50, -50, -4, 50, 50, 15),
    "SM_Kit_StairCore": ("Kit/Street", -200, -200, 0, 200, 200, 415),
    "SM_Kit_ElevatorCar": ("Kit/Street", -110, -70, 0, 110, 70, 250),
    "SM_Kit_DeskSet": ("Kit/Street", -80, -40, 0, 85, 40, 115),
    "SM_Kit_SofaSet": ("Kit/Street", -55, -100, 0, 120, 100, 85),
    "SM_Kit_ShopShelf": ("Kit/Street", -30, -100, 0, 30, 100, 180),
}


def _parts(base: str) -> list:
    return sorted(n for n in KIT if n == base or n.startswith(base + "_P"))


# role -> kit buildings (each a list of parts placed at one point)
TOWERS = [_parts(b) for b in ("SM_City_DT_GlassTower", "SM_City_DT_BandTower", "SM_City_DT_CrownTower",
                               "SM_City_DT_SlabTower", "SM_City_DT_RoundTower_R10", "SM_City_DT_RoundTower_R15",
                               "SM_City_DT_Tower3")] + \
         [[n] for n in KIT if n.startswith("SM_Sky")]
BUILDINGS = {
    "skyscraper": TOWERS,
    "office": [["SM_City_CT_MidRise"], _parts("SM_City_DT_Tower3"), _parts("SM_City_DT_SlabTower")],
    "apartment": [["SM_City_CT_Apartment"], ["SM_City_CT_MidRise"]],
    "shop": [["SM_City_CT_ShopRow"], ["SM_City_CT_ShopRow2"]],
    "mall": [["SM_City_CT_ShopRow2"]],
    "gas_station": [["SM_City_CT_ShopRow"]],
    "house": [["SM_City_HS_House2"], ["SM_City_HS_HouseWide"], ["SM_City_HS_Modern"], ["SM_Bld_House_Flat"],
              ["SM_Bld_House_Gable"], ["SM_Bld_House_L"]],
    "mansion": [_parts("SM_City_RC_Mansion"), ["SM_City_RC_ModernVilla"]],
    "hospital": [["SM_Bld_Hospital"]],
    "police": [["SM_Bld_Police"]],
    "fire_station": [["SM_Bld_Police"]],
    "school": [["SM_City_CT_MidRise"]],
    "factory": [["SM_V4_Crane"], ["SM_V4_Container_Blue"], ["SM_V4_Container_Red"]],
    "warehouse": [["SM_V4_Container_Green"], ["SM_V4_Container_Orange"], ["SM_V4_Container_Blue"]],
}
PROPS = {
    "street_lamp": ["SM_StreetLamp_NeonArm", "SM_StreetLamp_RoyalCrown", "SM_StreetLamp_TwinArm"],
    "street_tree": ["SM_V4_TreeRound", "SM_V4_TreePine"],
    "bus_stop": ["SM_Prop_BusShelter"],
    "bench": ["SM_Prop_Bench"],
    "bin": ["SM_Prop_TrashCan", "SM_Prop_RecycleBin"],
    "hydrant": ["SM_Prop_FireHydrant"],
    "tree": ["SM_V4_TreeRound", "SM_V4_TreePine", "SM_V4_TreeRound"],
    "traffic_light": ["SM_Kit_TrafficLight"],
    "stop_sign": ["SM_Kit_StopSign"],
    "street_sign": ["SM_Kit_StreetSign"],
    "billboard": ["SM_Kit_Billboard"],
    "power_pole": ["SM_Kit_PowerPole"],
}
# what is inside a building, by role (factories / warehouses are open yards: nothing)
INTERIOR = {"skyscraper": "SM_Kit_DeskSet", "office": "SM_Kit_DeskSet", "hospital": "SM_Kit_DeskSet",
            "police": "SM_Kit_DeskSet", "fire_station": "SM_Kit_DeskSet", "school": "SM_Kit_DeskSet",
            "apartment": "SM_Kit_SofaSet", "house": "SM_Kit_SofaSet", "mansion": "SM_Kit_SofaSet",
            "shop": "SM_Kit_ShopShelf", "mall": "SM_Kit_ShopShelf", "gas_station": "SM_Kit_ShopShelf"}
STOREY_CM = 400  # = SM_Kit_StairCore's rise: its top landing is the next storey's floor
CORE_CM = 400  # SM_Kit_StairCore's footprint; scaled 0.7-1.6x to the slab's well
CORE_MIN_CM = 280  # a well narrower than this gets no core (solid slabs, one storey of furniture each)
MAX_FLOORS = 40  # ponytail: caps instances on the tallest towers; floors above it stay empty
SETS_PER_FLOOR = 8  # furniture sets per storey, the ones nearest the windows (what you see through glass)
FLAT = {"parking": "SM_Kit_ParkingLot"}  # lots that are ground, not buildings: stretched to the lot, never up
PARK_TREE_M = 6.0  # a park is a grove: a tree about every 6 m (jittered, 85 % filled), a path slab through it
SCALE_RANGE = (0.5, 2.0)  # a kit mesh is stretched at most this much to fit its lot


def _size(name: str) -> tuple:
    b = KIT[name][1:]
    return b[3] - b[0], b[4] - b[1], b[5] - b[2]


def _height(parts: list) -> float:
    return max(KIT[p][6] for p in parts)


def _pick(options: list, x: float, y: float) -> int:
    return abs(int(x) * 73856093 ^ int(y) * 19349663) % len(options)


def path(name: str, root: str) -> str:
    return f"{root}/WorldGen/{KIT[name][0]}/{name}.{name}"


def bake(previewed: dict, root: str) -> tuple:
    """(groups {mesh path: [[x, y, z, yaw, sx, sy, sz]]}, {layer: points baked}, [layers with no kit piece])."""
    lo, hi = SCALE_RANGE
    groups: dict = {}
    baked, missing = {}, []
    for name, layer in previewed.items():
        role = layer.get("role")
        pts = layer.get("points") or []
        if layer.get("kind") == "buildings" and role == "park":
            trees = 0
            for p in pts:
                trees += _grove(groups, root, p)
            baked[name] = len(pts)
        elif layer.get("kind") == "buildings" and role in FLAT:
            for p in pts:
                w = (p[7] if len(p) > 7 else 30) * 100
                d = (p[8] if len(p) > 8 else 30) * 100
                groups.setdefault(path(FLAT[role], root), []).append([p[0], p[1], p[2], p[3], round(d / 100, 2),
                                                                    round(w / 100, 2), 1.0])
            baked[name] = len(pts)
        elif layer.get("kind") == "buildings" and role in BUILDINGS:
            opts = BUILDINGS[role]
            for p in pts:
                x, y, z, yaw = p[0], p[1], p[2], p[3]
                h = (p[5] if len(p) > 5 else 8) * 100
                w = (p[7] if len(p) > 7 else 12) * 100
                d = (p[8] if len(p) > 8 else 12) * 100
                # the two kit buildings nearest the lot's height, one of them by position: a varied skyline
                near = sorted(range(len(opts)), key=lambda k: abs(_height(opts[k]) - h))[:2]
                parts = opts[near[_pick(near, x, y)]]
                mx, my, _ = _size(parts[0])
                sx = max(lo, min(hi, d / mx))
                sy = max(lo, min(hi, w / my))
                sz = max(lo, min(hi, h / _height(parts)))
                for part in parts:
                    groups.setdefault(path(part, root), []).append([x, y, z, yaw, round(sx, 3), round(sy, 3), round(sz, 3)])
                if role in INTERIOR:
                    _interior(groups, root, parts, INTERIOR[role], x, y, z, yaw, sx, sy, sz)
                _ground_pieces(groups, root, p, x, y, z, yaw, w, d)
            baked[name] = len(pts)
        elif role in PROPS:
            opts = PROPS[role]
            for p in pts:
                s = p[4] if len(p) > 4 else 1.0
                groups.setdefault(path(opts[_pick(opts, p[0], p[1])], root), []).append([p[0], p[1], p[2], p[3], s, s, s])
            baked[name] = len(pts)
        elif pts and layer.get("kind") != "foliage":
            missing.append(name)
    return groups, baked, missing


def _interior(groups: dict, root: str, parts: list, furniture: str, x, y, z, yaw, sx, sy, sz) -> None:
    """Floors inside a building shell, per storey: a slab inset from the walls (glowing ceiling panels under it)
    with a well in the middle, the stair + elevator core standing in that well (walkable switchback stairs,
    one storey per piece, so the stairs climb the whole building), and furniture along the windows facing
    out. One elevator car waits at the ground floor. Round / sculpted towers get a round slab well inside
    their outline so nothing pokes through the glass; a building too small for the core gets solid slabs."""
    fx, fy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))

    def put(mesh, u, v, h, turn, s):
        groups.setdefault(path(mesh, root), []).append(
            [round(x + fx * u - fy * v), round(y + fy * u + fx * v), round(z + h), round((yaw + turn) % 360, 1), *s])

    floors, below, car = 0, 0.0, False
    for part in parts:
        x0, y0, z0, x1, y1, z1 = KIT[part][1:]
        roof = 150 if part == parts[-1] else 0
        rnd = "Round" in part or "SM_Sky" in part
        odd = part in ("SM_Bld_Hospital", "SM_Bld_Police") or "Mansion_P1" in part
        inset = 0.6 if "SM_Sky" in part or odd else 0.66 if rnd else 0.86
        cu, cv = (x0 + x1) / 2 * sx, (y0 + y1) / 2 * sy
        ru, rv = (x1 - x0) / 2 * sx * inset, (y1 - y0) / 2 * sy * inset
        if rnd:
            ru = rv = min(ru, rv)
        # storeys on one building-wide grid, so the stairs run on through the tower's parts without a gap;
        # the last storey under the roof stays clear of it
        k0, k1 = math.ceil((below or z0 * sz) / STOREY_CM), int((z1 * sz - roof) // STOREY_CM)
        if k1 <= k0 or min(ru, rv) < 150:
            continue
        below = k1 * STOREY_CM
        # the well: square 0.6 r x 0.6 r (FloorPlate's hole), or the square inside FloorRound's round hole
        wu, wv = (0.594 * ru, 0.594 * ru) if rnd else (0.6 * ru, 0.6 * rv)
        has_core = min(wu, wv) >= CORE_MIN_CM
        plate = ("SM_Kit_FloorRound" if rnd else "SM_Kit_FloorPlate") if has_core else "SM_Kit_FloorSolid"
        cs = [round(max(0.7, min(2.2, wu / CORE_CM)), 3), round(max(0.7, min(2.2, wv / CORE_CM)), 3), 1.0]
        # furniture: a ring 70 % of the way out to the slab edge (between the well and the glass), ~6 m apart
        count = max(2, min(SETS_PER_FLOOR, int(2 * math.pi * 0.7 * (ru + rv) / 2 // 600)))
        spots = []
        for s in range(count):
            t = 2 * math.pi * (s + 0.5) / count
            c, n_ = math.cos(t), math.sin(t)
            m = 1.0 if rnd else max(abs(c), abs(n_))  # square ring: push the direction out to the rectangle
            spots.append((0, 0.7 * ru * c / m, 0.7 * rv * n_ / m))
        for k in range(k0, k1):
            if floors >= MAX_FLOORS:
                return
            floors += 1
            h = k * STOREY_CM + 10
            put(plate, cu, cv, h, 0, [round(2 * ru / 100, 2), round(2 * rv / 100, 2), 1.0])
            if has_core:
                put("SM_Kit_StairCore", cu, cv, h, 0, cs)
                if not car:
                    put("SM_Kit_ElevatorCar", cu, cv + 115 * cs[1], h + 15, 0, [cs[0], 1.0, 1.0])
                    car = True
            for _, u, v in spots:  # facing the nearest window
                if rnd:
                    turn = math.degrees(math.atan2(v, u))
                elif abs(u) / ru >= abs(v) / rv:
                    turn = 0 if u >= 0 else 180
                else:
                    turn = 90 if v >= 0 else -90
                put(furniture, cu + u, cv + v, h + 15, round(turn, 1), [1.0, 1.0, 1.0])


def _grove(groups: dict, root: str, p: list) -> int:
    """A park lot filled with trees on a jittered grid (deterministic per lot), plus a path across it."""
    x, y, z, yaw = p[0], p[1], p[2], p[3]
    w = (p[7] if len(p) > 7 else 40) * 100
    d = (p[8] if len(p) > 8 else 40) * 100
    fx, fy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    step = PARK_TREE_M * 100
    made = 0
    for i in range(int(d // step)):
        for j in range(int(w // step)):
            seed = abs(int(x) * 31 + int(y) * 17 + i * 7919 + j * 104729)
            if seed % 100 >= 85 or abs(j - w / step / 2 + 0.5) < 0.6:  # some gaps; the middle row is the path
                continue
            u = -d / 2 + (i + 0.5) * step + (seed % 97 - 48) / 48 * step * 0.35
            v = -w / 2 + (j + 0.5) * step + (seed % 89 - 44) / 44 * step * 0.35
            mesh = PROPS["tree"][seed % len(PROPS["tree"])]
            s = round(0.8 + (seed % 50) / 100, 2)
            groups.setdefault(path(mesh, root), []).append([round(x + fx * u - fy * v), round(y + fy * u + fx * v), z,
                                                            seed % 360, s, s, s])
            made += 1
    groups.setdefault(path("SM_Kit_PathSlab", root), []).append([x, y, z, yaw, round(d / 100, 2), 2.5, 1.0])
    return made


def _ground_pieces(groups: dict, root: str, p: list, x, y, z, yaw, w, d) -> None:
    """A lot's foundation (a plinth as deep as the hill falls away) and its walkway / driveway slabs."""
    found = p[9] if len(p) > 9 else 0.0
    if found > 0.3:
        groups.setdefault(path("SM_Kit_Plinth", root), []).append([x, y, z, yaw, round(d / 100, 2), round(w / 100, 2),
                                                                  round(found + 0.3, 2)])
    for width, flat in (p[10] if len(p) > 10 else []):
        for i in range(3, len(flat) - 2, 3):
            ax, ay, az, bx, by, bz = flat[i - 3], flat[i - 2], flat[i - 1], flat[i], flat[i + 1], flat[i + 2]
            run = math.hypot(bx - ax, by - ay)
            if run < 50:
                continue
            pitch = math.degrees(math.atan2(bz - az, run))
            groups.setdefault(path("SM_Kit_PathSlab", root), []).append(
                [(ax + bx) / 2, (ay + by) / 2, (az + bz) / 2, round(math.degrees(math.atan2(by - ay, bx - ax)), 1),
                 round(run / 100, 2), round(width, 2), 1.0, round(pitch, 1)])


def _demo() -> None:
    pv = {
        "Skyscrapers": {"kind": "buildings", "role": "skyscraper",
                        "points": [[0, 0, 500, 90, 1, 110, 0, 32, 32, 0, []], [9000, 0, 500, 0, 1, 40, 0, 24, 24, 0, []]]},
        "Houses": {"kind": "buildings", "role": "house", "points": [[100, 200, 500, 0, 1, 7, 0, 11, 13, 0, []]]},
        "Street lamps": {"kind": "along_roads", "role": "street_lamp", "points": [[1, 2, 3, 45, 1.1]]},
        "Trees": {"kind": "foliage", "role": "tree", "points": [[5, 5, 5, 0, 1]]},
        "Traffic lights": {"kind": "at_junctions", "role": "traffic_light", "points": [[7, 7, 7, 0, 1]]},
    }
    groups, baked, missing = bake(pv, "/R")
    assert baked == {"Skyscrapers": 2, "Houses": 1, "Street lamps": 1, "Trees": 1, "Traffic lights": 1} and missing == [], (baked, missing)
    # a house on a hill: a plinth under it and a sloped walkway slab from its door
    hill = {"Houses": {"kind": "buildings", "role": "house",
                       "points": [[0, 0, 500, 0, 1, 7, 0, 12, 12, 1.8, [[1.6, [600, 0, 500, 1100, 0, 350]]]]]},
            "Parking lots": {"kind": "buildings", "role": "parking", "points": [[0, 0, 0, 0, 1, 0, 0, 30, 40, 0, []]]}}
    g2, _, _ = bake(hill, "/R")
    plinth = g2[path("SM_Kit_Plinth", "/R")][0]
    assert plinth[4:7] == [12.0, 12.0, 2.1], plinth
    slab = g2[path("SM_Kit_PathSlab", "/R")][0]
    assert slab[4] == 5.0 and slab[5] == 1.6 and slab[7] < -15, slab
    assert g2[path("SM_Kit_ParkingLot", "/R")][0][4:7] == [40.0, 30.0, 1.0]
    tall = [p for p, rows in groups.items() if "SM_Kit_" not in p and any(r[0] == 0 for r in rows)]
    assert len(tall) >= 2 and all("SM_City_DT_" in p or "SM_Sky" in p for p in tall), "a 110 m lot gets a whole multi-part tower"
    assert len({tuple(r) for p in tall for r in groups[p] if r[0] == 0}) == 1, "every part of a tower shares one point"
    for p, rows in groups.items():
        for r in rows:
            assert "SM_Kit_" in p or all(SCALE_RANGE[0] <= v <= SCALE_RANGE[1] for v in r[4:7]) or r[4] == 1.1, r
    assert all(p.startswith("/R/WorldGen/") and p.count(".") == 1 for p in groups)
    assert any("SM_V4_Tree" in p for p in groups), "forest trees are baked"
    park = {"Parks": {"kind": "buildings", "role": "park", "points": [[0, 0, 0, 0, 1, 0, 0, 40, 40, 0, []]]}}
    g3, b3, _ = bake(park, "/R")
    trees = sum(len(v) for k, v in g3.items() if "SM_V4_Tree" in k)
    assert 25 <= trees <= 40 and b3 == {"Parks": 1}, ("a 40 m park is a grove", trees)
    # interiors: a 110 m tower has many lit floors, a stair core every storey, one car, desks inside its outline
    gt, _, _ = bake({"T": {"kind": "buildings", "role": "skyscraper", "points": [[0, 0, 500, 90, 1, 110, 0, 32, 32, 0, []]]}}, "/R")
    plates = [r for k in ("SM_Kit_FloorRound", "SM_Kit_FloorPlate", "SM_Kit_FloorSolid") for r in gt.get(path(k, "/R"), [])]
    assert 10 <= len(plates) <= MAX_FLOORS, len(plates)
    cores = sorted(r[2] for r in gt[path("SM_Kit_StairCore", "/R")])
    assert len(cores) >= 10 and all(b - a == STOREY_CM for a, b in zip(cores, cores[1:])), ("a stair core every storey", cores)
    assert len(gt[path("SM_Kit_ElevatorCar", "/R")]) == 1, "one car per tower"
    desks = gt[path("SM_Kit_DeskSet", "/R")]
    assert desks and all(math.hypot(r[0], r[1]) < 3200 for r in desks), "desks stay inside the tower"
    assert max(r[2] for r in plates) < 500 + 11068, "no floor above the roof"
    gh, _, _ = bake({"H": {"kind": "buildings", "role": "house", "points": [[0, 0, 0, 0, 1, 11, 0, 12, 12, 0, []]]}}, "/R")
    assert path("SM_Kit_SofaSet", "/R") in gh, "a house gets a living room"
    print("kit bake ok", len(groups), "meshes")


if __name__ == "__main__":
    _demo()

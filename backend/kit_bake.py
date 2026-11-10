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
    "park": ["SM_V4_TreeRound", "SM_Prop_Planter"],
}
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
        if layer.get("kind") == "buildings" and role in BUILDINGS:
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
            baked[name] = len(pts)
        elif role in PROPS and layer.get("kind") != "foliage":
            opts = PROPS[role]
            for p in pts:
                s = p[4] if len(p) > 4 else 1.0
                groups.setdefault(path(opts[_pick(opts, p[0], p[1])], root), []).append([p[0], p[1], p[2], p[3], s, s, s])
            baked[name] = len(pts)
        elif pts and layer.get("kind") != "foliage":
            missing.append(name)
    return groups, baked, missing


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
    assert baked == {"Skyscrapers": 2, "Houses": 1, "Street lamps": 1} and missing == ["Traffic lights"], (baked, missing)
    tall = [p for p, rows in groups.items() if any(r[0] == 0 for r in rows)]
    assert len(tall) >= 2 and all("SM_City_DT_" in p or "SM_Sky" in p for p in tall), "a 110 m lot gets a whole multi-part tower"
    assert len({tuple(r) for p in tall for r in groups[p] if r[0] == 0}) == 1, "every part of a tower shares one point"
    for rows in groups.values():
        for r in rows:
            assert all(SCALE_RANGE[0] <= v <= SCALE_RANGE[1] for v in r[4:]) or r[4] == 1.1, r
    assert all(p.startswith("/R/WorldGen/") and p.count(".") == 1 for p in groups)
    assert not any("Tree" in p for p in groups), "foliage is painted, not baked"
    print("kit bake ok", len(groups), "meshes")


if __name__ == "__main__":
    _demo()

"""Bake a City Builder preview into PCG points for the project's own kit (WorldGen/City, WorldGen/Kit).

Every previewed lot and street item becomes a point on one of the kit's meshes: a building takes the kit
building whose height is closest to the lot's (towers are several parts that share one pivot, so every
part gets the same point), scaled to the lot's width, depth and height; street furniture keeps its
preview scale. The result is {mesh path: [[x, y, z, yaw, sx, sy, sz], ...]} for Create Points ->
Static Mesh Spawner pairs, so the city in UEFN is the city in the preview.
"""

from __future__ import annotations

import hashlib
import json
import math
import random

from . import grammar
from .citygen import EMBED_CM
from .pcg_layers import FORM_NAMES

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
    "SM_City_RC_Mansion_P0": ("City", -893, -1121, 0, 892, 1121, 905),
    "SM_City_RC_Mansion_P1": ("City", -875, -949, 867, 666, 1104, 917),
    "SM_City_RC_ModernVilla": ("City", -1134, -1596, 0, 1134, 1596, 823),
    "SM_Bld_Hospital": ("Kit", -4400, -3300, 0, 4400, 4120, 4435),
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
    "SM_Kit_ParkingStall": ("Kit/Street", -275, -130, 0, 275, 130, 2),
    "SM_Kit_Turnaround": ("Kit/Street", -1000, -1000, -10, 1000, 1000, 0),
    "SM_Kit_SidewalkCorner": ("Kit/Street", -200, -200, -15, 200, 200, 0),
    "SM_Kit_Bush": ("Kit/Street", -83, -95, 0, 110, 85, 130),
    "SM_Kit_Rock": ("Kit/Street", -80, -70, -20, 85, 70, 70),
    "SM_Kit_TreePit": ("Kit/Street", -75, -75, -2, 75, 75, 2),  # dirt square + grate under every street tree
    # facility kit (BuildingKit.blend): what makes a power plant a power plant, a city hall a city hall
    "SM_Kit_EntranceSteps": ("Kit/Facility", 0, -150, -20, 180, 150, 200),  # 1 m rise (scaled to the plinth), down +X
    "SM_Kit_CoolingTower": ("Kit/Facility", -1210, -1200, 0, 1210, 1200, 3205),
    "SM_Kit_Chimney": ("Kit/Facility", -250, -250, 0, 250, 250, 4205),
    "SM_Kit_Transformer": ("Kit/Facility", -200, -160, 0, 200, 160, 320),
    "SM_Kit_StorageTank": ("Kit/Facility", -510, -510, 0, 510, 510, 920),
    "SM_Kit_Silo": ("Kit/Facility", -305, -305, 0, 305, 305, 2050),
    "SM_Kit_ClarifierTank": ("Kit/Facility", -1000, -1000, 0, 1040, 1000, 440),
    "SM_Kit_Conveyor": ("Kit/Facility", 0, -70, 0, 1200, 70, 720),
    "SM_Kit_FactoryMachine": ("Kit/Facility", -250, -150, 0, 250, 150, 330),
    "SM_Kit_TelecomMast": ("Kit/Facility", -160, -160, 0, 160, 160, 4030),
    "SM_Kit_SecurityFence_4m": ("Kit/Facility", -5, -25, 0, 405, 25, 360),
    "SM_Kit_WatchTower": ("Kit/Facility", -200, -200, 0, 205, 205, 1130),
    "SM_Kit_Flagpole": ("Kit/Facility", -40, -40, 0, 230, 40, 1045),
    "SM_Kit_Portico": ("Kit/Facility", 0, -620, -60, 360, 620, 1060),
    "SM_Kit_CivicDome": ("Kit/Facility", -620, -620, 0, 620, 620, 1060),
    "SM_Kit_Helipad": ("Kit/Facility", -905, -905, 0, 905, 905, 37),
    "SM_Kit_PlatformCanopy": ("Kit/Facility", -2000, -350, 0, 2000, 350, 580),
    "SM_Kit_Bleachers": ("Kit/Facility", -1005, -485, 0, 1005, 0, 370),
    "SM_Kit_AmbulanceCanopy": ("Kit/Facility", 0, -421, 0, 1010, 421, 560),
    # the planned developments' grounds (urban.ARCHETYPES): 1 m unit slabs stretched to each part, and towers
    "SM_Kit_Apron": ("Kit/Street", -50, -50, 0, 50, 50, 10),
    "SM_Kit_SportsField": ("Kit/Street", -50, -50, 0, 50, 50, 2),
    "SM_Kit_Playground": ("Kit/Street", -50, -50, 0, 50, 50, 5),
    # real-size football pitches (run-off and goals included) and playground equipment, all placed at scale 1
    "SM_Kit_Pitch5": ("Kit/Street", -2200, -1200, 0, 2200, 1200, 202),
    "SM_Kit_Pitch7": ("Kit/Street", -3200, -2200, 0, 3200, 2200, 202),
    "SM_Kit_Pitch11": ("Kit/Street", -5200, -3400, 0, 5200, 3400, 246),
    "SM_Kit_PG_Swings": ("Kit/Street", -205, -65, 0, 205, 65, 260),
    "SM_Kit_PG_Slide": ("Kit/Street", -85, -65, 0, 335, 65, 260),
    "SM_Kit_PG_Climber": ("Kit/Street", -124, -124, 0, 124, 124, 202),
    "SM_Kit_PG_SeeSaw": ("Kit/Street", -180, -20, 0, 180, 20, 86),
    "SM_Kit_PG_Roundabout": ("Kit/Street", -100, -100, 0, 100, 100, 27),
    "SM_Kit_PG_Sandbox": ("Kit/Street", -150, -150, 0, 150, 150, 25),
    "SM_Kit_Pool": ("Kit/Street", -50, -50, 0, 50, 50, 10),
    "SM_Kit_UtilityYard": ("Kit/Street", -50, -50, 0, 50, 50, 5),
    "SM_Kit_Pond": ("Kit/Street", -50, -50, -5, 50, 50, 0),
    "SM_Kit_WaterTower": ("Kit/Street", -600, -600, 0, 600, 600, 3000),
    # waterfront (waterfront.KINDS): unit decks / slabs stretched to each part, fixed boats, cranes, homes, pylons
    "SM_Kit_DockDeck": ("Kit/Street", -50, -50, 0, 50, 50, 30),
    "SM_Kit_FloatDock": ("Kit/Street", -50, -50, 0, 50, 50, 50),
    "SM_Kit_Piling": ("Kit/Street", -15, -15, 0, 15, 15, 100),
    "SM_Kit_BoatSlip": ("Kit/Street", -600, -225, 0, 600, 225, 5),
    "SM_Kit_Boat": ("Kit/Street", -450, -150, 0, 450, 150, 200),
    "SM_Kit_Seawall": ("Kit/Street", -50, -30, 0, 50, 30, 100),
    "SM_Kit_Riprap": ("Kit/Street", -50, -50, 0, 50, 50, 100),
    "SM_Kit_Sand": ("Kit/Street", -50, -50, 0, 50, 50, 10),
    "SM_Kit_Wetland": ("Kit/Street", -50, -50, 0, 50, 50, 60),
    "SM_Kit_PortCrane": ("Kit/Street", -1500, -1000, 0, 1500, 1000, 4500),
    "SM_Kit_ContainerStack": ("Kit/Street", -610, -125, 0, 610, 125, 780),
    "SM_Kit_BoatLaunch": ("Kit/Street", -50, -50, 0, 50, 50, 20),
    "SM_Kit_StiltHouse": ("Kit/Street", -500, -400, 0, 500, 400, 600),
    "SM_Kit_FloatHome": ("Kit/Street", -500, -350, 0, 500, 350, 500),
    # a stay cable: a bar from 1 m up over its origin down to 1 m out along +X (citygen.bridge_towers)
    "SM_Kit_BridgeStay": ("Kit/Street", 0, -6, 0, 100, 6, 100),
    # bridge piers (citygen.bridge_piers): a column stretched up from the ground to its cap, the cap across the deck
    "SM_Kit_BridgePier": ("Kit/Street", -100, -100, 0, 100, 100, 100),
    "SM_Kit_PierCap": ("Kit/Street", -100, -50, 0, 100, 50, 120),
    "SM_Kit_BridgeAbutment": ("Kit/Street", -450, -50, 0, 60, 50, 250),
    # a low deck's or ramp's approach wall (citygen.deck_fills): 1 m each way, stretched along, across and up
    "SM_Kit_ApproachWall": ("Kit/Street", -50, -50, 0, 50, 50, 100),
    # shipyards (shipyard.py)
    "SM_Kit_ShipHull": ("Kit/Street", -5000, -1000, 0, 5000, 1000, 2400),
    "SM_Kit_GoliathCrane": ("Kit/Street", -1000, -3500, 0, 1000, 3500, 8000),
    "SM_Kit_PortalCrane": ("Kit/Street", -600, -600, 0, 3000, 600, 4500),
    "SM_Kit_TowerCrane": ("Kit/Street", -300, -300, 0, 5000, 300, 6000),
    "SM_Kit_CraneRail": ("Kit/Street", -50, -50, 0, 50, 50, 20),
    "SM_Kit_KeelBlock": ("Kit/Street", -100, -75, 0, 100, 75, 150),
    "SM_Kit_SteelStack": ("Kit/Street", -600, -150, 0, 600, 150, 150),
    "SM_Kit_RailTrack": ("Kit/Street", -50, -150, 0, 50, 150, 20),
    "SM_Kit_FloatingDryDock": ("Kit/Street", -10000, -2250, 0, 10000, 2250, 1800),
    "SM_Kit_FuelTank": ("Kit/Street", -1000, -1000, 0, 1000, 1000, 1200),
    "SM_Kit_Fence": ("Kit/Street", -50, -5, 0, 50, 5, 250),
    "SM_Kit_BoatLift": ("Kit/Street", -600, -400, 0, 600, 400, 1000),
    "SM_Kit_OffshoreJacket": ("Kit/Street", -1000, -1000, 0, 1000, 1000, 6000),
    "SM_Kit_ScrapPile": ("Kit/Street", -800, -800, 0, 800, 800, 500),
    "SM_Kit_Gatehouse": ("Kit/Street", -400, -300, 0, 400, 300, 400),
    # airports (airport.py)
    "SM_Air_Airliner": ("Kit/Street", -2000, -1800, 0, 2000, 1800, 1200),
    "SM_Air_SmallPlane": ("Kit/Street", -450, -550, 0, 450, 550, 300),
    "SM_Air_JetBridge": ("Kit/Street", -1500, -200, 0, 1500, 200, 600),
    "SM_Air_Hangar": ("Kit/Street", -3000, -2500, 0, 3000, 2500, 2200),
    "SM_Air_Shelter": ("Kit/Street", -1500, -2000, 0, 1500, 2000, 1200),
    "SM_Air_ControlTower": ("Kit/Street", -600, -600, 0, 600, 600, 4500),
    "SM_Air_Radar": ("Kit/Street", -400, -400, 0, 400, 400, 1400),
    "SM_Air_Windsock": ("Kit/Street", -50, -200, 0, 50, 200, 600),
    "SM_Air_EdgeLight": ("Kit/Street", -20, -20, 0, 20, 20, 50),
    "SM_Air_ApproachBar": ("Kit/Street", -50, -1500, 0, 50, 1500, 200),
    "SM_Air_Threshold": ("Kit/Street", -1500, -2250, 0, 1500, 2250, 5),
    "SM_Air_HoldSign": ("Kit/Street", -20, -150, 0, 20, 150, 150),
    "SM_Air_GSE": ("Kit/Street", -300, -150, 0, 300, 150, 250),
}


# measured from the BuildingKit meshes in Blender (BuildingKit.blend): for every 4 m interior storey, the box of the
# glazed shell in City Builder's frame (cm, about the pivot) and 1 when it is round. Interiors follow it, so no floor
# stands through the glass, into a crown or past a setback. [storey, x0, y0, x1, y1, round]
PROFILES = {
    "SM_Bld_Hospital": [[0,-4385,-3695,4006,3695,0],[1,-4385,-3695,3985,3695,0],[2,-4385,-3695,3985,3695,0],[3,-1985,-2785,1185,2785,0],[4,-1985,-2785,1185,2785,0],[5,-1985,-2785,1185,2785,0],[6,-1985,-2785,1185,2785,0],[7,-1985,-2785,1185,2785,0],[8,-1985,-2785,1185,2785,0],[9,-1985,-2785,1185,2785,0],[10,-1985,-2785,1185,2785,0]],
    "SM_Bld_Police": [[0,-3950,-2585,3506,2585,0],[1,-3950,-2585,3485,2585,0],[2,-3950,-2585,3485,2585,0],[3,-1985,-1785,985,1785,0],[4,-1985,-1785,985,1785,0],[5,-1985,-1785,985,1785,0],[6,-1985,-1785,985,1785,0],[7,-1985,-1785,985,1785,0],[8,-1985,-1785,985,1785,0],[9,-1985,-1785,985,1785,0],[10,-1985,-1785,985,1785,0]],
    "SM_City_CT_Apartment": [[0,-703,-759,815,759,0],[1,-703,-759,815,759,0],[2,-703,-759,815,759,0],[3,-703,-759,815,759,0]],
    "SM_City_CT_MidRise": [[0,-693,-1015,825,1015,0],[1,-693,-1015,825,1015,0],[2,-693,-1015,825,1015,0],[3,-693,-1015,825,1015,0],[4,-693,-1015,825,1015,0],[5,-693,-1015,825,1015,0]],
    "SM_City_CT_ShopRow": [[0,-436,-759,493,759,0]],
    "SM_City_CT_ShopRow2": [[0,-437,-759,-20,736,0],[1,-437,-759,569,759,0]],
    "SM_City_DT_BandTower": [[0,-1015,-1103,1015,1015,0],[1,-1015,-1015,1015,1015,0],[2,-1015,-1015,1015,1015,0],[3,-1015,-1015,1015,1015,0],[4,-1015,-1015,1015,1015,0],[5,-1015,-1015,1015,1015,0],[6,-1015,-1015,1015,1015,0],[7,-1015,-1015,1015,1015,0],[8,-1015,-1015,1015,1015,0],[9,-1015,-1015,1015,1015,0],[10,-1015,-1015,1015,1015,0],[11,-1015,-1015,1015,1015,0],[12,-1015,-1015,1015,1015,0],[13,-1015,-1015,1015,1015,0],[14,-1015,-1015,1015,1015,0],[15,-1015,-1015,1015,1015,0],[16,-1015,-1015,1015,1015,0],[17,-1015,-1015,1015,1015,0]],
    "SM_City_DT_CrownTower": [[0,-3067,-3067,3067,3067,1],[1,-3067,-3067,3067,3067,1],[2,-3067,-3067,3067,3067,1],[3,-1533,-1527,1531,1527,1],[4,-1533,-1527,1531,1527,1],[5,-1533,-1527,1531,1527,1],[6,-1533,-1527,1531,1527,1],[7,-1533,-1527,1531,1527,1],[8,-1533,-1527,1531,1527,1],[9,-1533,-1527,1531,1527,1],[10,-1533,-1527,1531,1527,1],[11,-1533,-1527,1531,1527,1],[12,-1533,-1527,1531,1527,1],[13,-1533,-1527,1531,1527,1],[14,-1533,-1527,1531,1527,1],[15,-1533,-1527,1531,1527,1],[16,-1533,-1527,1531,1527,1],[17,-1533,-1527,1531,1527,1],[18,-1533,-1527,1531,1527,1],[19,-1013,-1018,1019,1018,1],[20,-1013,-1018,1019,1018,1],[21,-1013,-1018,1019,1018,1],[22,-1013,-1018,1019,1018,1],[23,-1013,-1018,1019,1018,1]],
    "SM_City_DT_GlassTower": [[0,-1015,-1103,1015,1015,0],[1,-1015,-1015,1015,1015,0],[2,-1015,-1015,1015,1015,0],[3,-1015,-1015,1015,1015,0],[4,-1015,-1015,1015,1015,0],[5,-1015,-1015,1015,1015,0],[6,-1015,-1015,1015,1015,0],[7,-1015,-1015,1015,1015,0],[8,-1015,-1015,1015,1015,0],[9,-1015,-1015,1015,1015,0],[10,-1015,-1015,1015,1015,0],[11,-1015,-1015,1015,1015,0],[12,-1015,-1015,1015,1015,0],[13,-1015,-1015,1015,1015,0],[14,-1015,-1015,1015,1015,0],[15,-1015,-1015,1015,1015,0],[16,-1015,-1015,1015,1015,0],[17,-1015,-1015,1015,1015,0],[18,-1015,-1015,1015,1015,0],[19,-1015,-1015,1015,1015,0],[20,-1015,-1015,1015,1015,0],[21,-1015,-1015,1015,1015,0],[22,-1015,-1015,1015,1015,0]],
    "SM_City_DT_RoundTower_R10": [[0,-1018,-1022,1018,1010,1],[1,-1018,-1022,1018,1010,1],[2,-1018,-1022,1018,1010,1],[3,-1018,-1022,1018,1010,1],[4,-1018,-1022,1018,1010,1],[5,-1018,-1022,1018,1010,1],[6,-1018,-1022,1018,1010,1],[7,-1018,-1022,1018,1010,1],[8,-1018,-1022,1018,1010,1],[9,-1018,-1022,1018,1010,1],[10,-1018,-1022,1018,1010,1],[11,-1018,-1022,1018,1010,1],[12,-1018,-1022,1018,1010,1],[13,-1018,-1022,1018,1010,1],[14,-1018,-1022,1018,1010,1],[15,-1018,-1022,1018,1010,1],[16,-1018,-1022,1018,1010,1],[17,-1018,-1022,1018,1010,1],[18,-1018,-1022,1018,1010,1],[19,-1018,-1022,1018,1010,1],[20,-1018,-1022,1018,1010,1],[21,-1018,-1022,1018,1010,1],[22,-1018,-1022,1018,1010,1],[23,-1018,-1022,1018,1010,1],[24,-1018,-1022,1018,1010,1],[25,-1018,-1022,1018,1010,1],[26,-1018,-1022,1018,1010,1]],
    "SM_City_DT_RoundTower_R15": [[0,-1527,-1532,1527,1532,1],[1,-1527,-1532,1527,1532,1],[2,-1527,-1532,1527,1532,1],[3,-1527,-1532,1527,1532,1],[4,-1527,-1532,1527,1532,1],[5,-1527,-1532,1527,1532,1],[6,-1527,-1532,1527,1532,1],[7,-1527,-1532,1527,1532,1],[8,-1527,-1532,1527,1532,1],[9,-1527,-1532,1527,1532,1],[10,-1527,-1532,1527,1532,1],[11,-1527,-1532,1527,1532,1],[12,-1527,-1532,1527,1532,1],[13,-1527,-1532,1527,1532,1],[14,-1527,-1532,1527,1532,1],[15,-1527,-1532,1527,1532,1],[16,-1018,-1020,1018,1012,1],[17,-1018,-1020,1018,1012,1],[18,-1018,-1020,1018,1012,1],[19,-1018,-1020,1018,1012,1],[20,-1018,-1020,1018,1012,1],[21,-1018,-1020,1018,1012,1]],
    "SM_City_DT_SlabTower": [[0,-1103,-1527,1015,1527,0],[1,-1015,-1527,1015,1527,0],[2,-1015,-1527,1015,1527,0],[3,-1015,-1527,1015,1527,0],[4,-1015,-1527,1015,1527,0],[5,-1015,-1527,1015,1527,0],[6,-1015,-1527,1015,1527,0],[7,-1015,-1527,1015,1527,0],[8,-1015,-1527,1015,1527,0],[9,-1015,-1527,1015,1527,0],[10,-1015,-1527,1015,1527,0],[11,-1015,-1527,1015,1527,0],[12,-1015,-1527,1015,1527,0],[13,-1015,-1527,1015,1527,0],[14,-1015,-1527,1015,1527,0],[15,-1015,-1527,1015,1527,0],[16,-1015,-1527,1015,1527,0],[17,-1015,-1527,1015,1527,0],[18,-1015,-1527,1015,1527,0],[19,-1015,-1527,1015,1527,0],[20,-1015,-1527,1015,1527,0],[21,-1015,-1527,1015,1527,0]],
    "SM_City_DT_Tower3": [[0,-759,-847,759,759,0],[1,-759,-759,759,759,0],[2,-759,-759,759,759,0],[3,-759,-759,759,759,0],[4,-759,-759,759,759,0],[5,-759,-759,759,759,0],[6,-759,-759,759,759,0],[7,-759,-759,759,759,0],[8,-759,-759,759,759,0],[9,-759,-759,759,759,0],[10,-759,-759,759,759,0],[11,-759,-759,759,759,0],[12,-759,-759,759,759,0],[13,-759,-759,759,759,0],[14,-759,-759,759,759,0],[15,-759,-759,759,759,0]],
    "SM_City_RC_Mansion": [[0,-817,-1100,858,1088,0],[1,-649,-932,858,1088,0]],
    "SM_City_RC_ModernVilla": [[0,-985,-1548,1086,1548,0],[1,-1130,-1548,1086,1019,0]],
    "SM_Sky04_DecoCrown": [[0,-1265,-1115,1265,1115,0],[1,-1265,-1115,1265,1115,0],[2,-1265,-1115,1265,1115,0],[3,-1265,-1115,1265,1115,0],[4,-1265,-1115,1265,1115,0],[5,-1265,-1115,1265,1115,0],[6,-1265,-1115,1265,1115,0],[7,-1265,-1115,1265,1115,0],[8,-1265,-1115,1265,1115,0],[9,-1265,-1115,1265,1115,0],[10,-1265,-1115,1265,1115,0],[11,-1265,-1115,1265,1115,0],[12,-985,-885,985,885,0],[13,-985,-885,985,885,0],[14,-985,-885,985,885,0],[15,-985,-885,985,885,0],[16,-985,-885,985,885,0],[17,-985,-885,985,885,0],[18,-635,-585,635,585,0],[19,-635,-585,635,585,0]],
    "SM_Sky05_Strawberry": [[0,-735,-735,735,735,1],[1,-735,-735,735,735,1],[2,-735,-735,735,735,1],[3,-735,-735,735,735,1],[4,-735,-735,735,735,1],[5,-735,-735,735,735,1],[6,-735,-735,735,735,1],[7,-735,-735,735,735,1],[8,-735,-735,735,735,1],[9,-735,-735,735,735,1],[10,-735,-735,735,735,1],[11,-735,-735,735,735,1],[12,-735,-735,735,735,1],[13,-735,-735,735,735,1],[14,-735,-735,735,735,1],[15,-735,-735,735,735,1]],
    "SM_Sky06_GoldRings": [[0,-1085,-1085,1085,1085,1],[1,-1085,-1085,1085,1085,1],[2,-1085,-1085,1085,1085,1],[3,-1085,-1085,1085,1085,1],[4,-1085,-1085,1085,1085,1],[5,-1085,-1085,1085,1085,1],[6,-1085,-1085,1085,1085,1],[7,-1085,-1085,1085,1085,1],[8,-1085,-1085,1085,1085,1],[9,-1085,-1085,1085,1085,1],[10,-1085,-1085,1085,1085,1],[11,-1085,-1085,1085,1085,1],[12,-1085,-1085,1085,1085,1],[13,-1085,-1085,1085,1085,1],[14,-1085,-1085,1085,1085,1],[15,-1085,-1085,1085,1085,1],[16,-1085,-1085,1085,1085,1],[17,-1085,-1085,1085,1085,1]],
    "SM_Sky07_OrbNeedle": [[0,-685,-685,685,685,1],[1,-685,-685,685,685,1],[2,-685,-685,685,685,1],[3,-685,-685,685,685,1],[4,-685,-685,685,685,1],[5,-685,-685,685,685,1],[6,-685,-685,685,685,1],[7,-685,-685,685,685,1],[8,-685,-685,685,685,1],[9,-685,-685,685,685,1],[10,-685,-685,685,685,1],[11,-685,-685,685,685,1],[12,-685,-685,685,685,1],[13,-685,-685,685,685,1],[14,-685,-685,685,685,1],[15,-685,-685,685,685,1],[16,-680,-680,680,680,1],[17,-450,-450,450,450,1],[18,-780,-780,780,780,1],[19,-850,-850,850,850,1],[20,-780,-780,780,780,1],[21,-480,-480,480,480,1]],
    "SM_Sky10_TwinSpire": [[0,-785,-815,785,-130,0],[0,-785,130,785,815,0],[1,-785,-815,785,-130,0],[1,-785,130,785,815,0],[2,-335,-815,335,-130,0],[2,-335,130,335,815,0],[3,-335,-815,335,-130,0],[3,-335,130,335,815,0],[4,-335,-815,335,-130,0],[4,-335,130,335,815,0],[5,-335,-815,335,-130,0],[5,-335,130,335,815,0],[6,-335,-815,335,-130,0],[6,-335,130,335,815,0],[7,-335,-815,335,-130,0],[7,-335,130,335,815,0],[8,-335,-815,335,-130,0],[8,-335,130,335,815,0],[9,-335,-815,335,-130,0],[9,-335,130,335,815,0],[10,-335,-815,335,-130,0],[10,-335,130,335,815,0],[11,-335,-815,335,-130,0],[11,-335,130,335,815,0]],
    "SM_Sky11_HaloCylinder": [[0,-885,-885,885,885,1],[1,-885,-885,885,885,1],[2,-885,-885,885,885,1],[3,-885,-885,885,885,1],[4,-885,-885,885,885,1],[5,-885,-885,885,885,1],[6,-885,-885,885,885,1],[7,-885,-885,885,885,1],[8,-885,-885,885,885,1],[9,-885,-885,885,885,1],[10,-885,-885,885,885,1],[11,-885,-885,885,885,1]],
    "SM_Sky13_GoldWedge": [[0,-985,-915,985,915,0],[1,-985,-915,985,915,0],[2,-985,-915,985,915,0],[3,-985,-915,985,915,0],[4,-985,-915,985,915,0],[5,-985,-915,985,915,0],[6,-985,-915,985,915,0],[7,-985,-915,985,915,0],[8,-985,-915,985,915,0],[9,-985,-915,985,915,0],[10,-985,-915,985,915,0],[11,-985,-915,985,915,0]],
    "SM_Sky14_CrownSpire": [[0,-985,-915,985,915,0],[1,-985,-915,985,915,0],[2,-985,-915,985,915,0],[3,-985,-915,985,915,0],[4,-985,-915,985,915,0],[5,-985,-915,985,915,0],[6,-985,-915,985,915,0],[7,-985,-915,985,915,0],[8,-985,-915,985,915,0],[9,-985,-915,985,915,0],[10,-985,-915,985,915,0],[11,-985,-915,985,915,0],[12,-685,-635,685,635,0]],
    "SM_Sky15_SteppedGarden": [[0,-1085,-1015,1085,1015,0],[1,-1085,-1015,1085,1015,0],[2,-1085,-1015,1085,1015,0],[3,-1085,-1015,1085,1015,0],[4,-1085,-1015,1085,1015,0],[5,-885,-835,885,835,0],[6,-885,-835,885,835,0],[7,-885,-835,885,835,0],[8,-885,-835,885,835,0],[9,-885,-835,885,835,0],[10,-685,-635,685,635,0],[11,-685,-635,685,635,0],[12,-685,-635,685,635,0],[13,-685,-635,685,635,0],[14,-485,-435,485,435,0],[15,-485,-435,485,435,0],[16,-485,-435,485,435,0]],
    "SM_Sky16_SpiralRibbon": [[0,-635,-635,635,635,1],[1,-635,-635,635,635,1],[2,-635,-635,635,635,1],[3,-635,-635,635,635,1],[4,-635,-635,635,635,1],[5,-635,-635,635,635,1],[6,-635,-635,635,635,1],[7,-635,-635,635,635,1],[8,-635,-635,635,635,1],[9,-635,-635,635,635,1],[10,-635,-635,635,635,1],[11,-635,-635,635,635,1],[12,-635,-635,635,635,1],[13,-635,-635,635,635,1],[14,-635,-635,635,635,1],[15,-635,-635,635,635,1]],
}


def profile_parts(parts: list) -> list:
    """A kit building's interior storeys: its measured glazed shell (PROFILES) when it has one, else its parts.
    Each measured storey is (x0, y0, z0, x1, y1, z1, round, more above): a stair core stands on every storey
    with another glazed storey above it, never on the last one."""
    rows = PROFILES.get(parts[0].rsplit("_P", 1)[0] if "_P" in parts[0] else parts[0]) if parts else None
    if not rows:
        return parts
    last = max(r[0] for r in rows)
    return [(x0, y0, k * STOREY_CM, x1, y1, (k + 1) * STOREY_CM, bool(rnd), k < last) for k, x0, y0, x1, y1, rnd in rows]


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
    # (no "house": every house is built from BuildingKit parts by the grammar, never one whole mesh: MODULAR_ROLES)
    "mansion": [_parts("SM_City_RC_Mansion"), ["SM_City_RC_ModernVilla"]],
    "hospital": [["SM_Bld_Hospital"]],
    "police": [["SM_Bld_Police"]],
    "fire_station": [["SM_Bld_Police"]],
    "school": [["SM_City_CT_MidRise"]],
    "civic": [["SM_City_CT_MidRise"], ["SM_Bld_Police"]],
    "utility": [["SM_V4_Container_Green"], ["SM_V4_Container_Blue"]],
    "transit": [["SM_City_CT_ShopRow2"]],
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
    "bush": ["SM_Kit_Bush"],
    "sidewalk_corner": ["SM_Kit_SidewalkCorner"],
    "rock": ["SM_Kit_Rock"],
    "traffic_light": ["SM_Kit_TrafficLight"],
    "stop_sign": ["SM_Kit_StopSign"],
    "street_sign": ["SM_Kit_StreetSign"],
    "billboard": ["SM_Kit_Billboard"],
    "power_pole": ["SM_Kit_PowerPole"],
}
# what is inside a building, by role (factories / warehouses are open yards: nothing)
INTERIOR = {"skyscraper": "SM_Kit_DeskSet", "office": "SM_Kit_DeskSet", "hospital": "SM_Kit_DeskSet",
            "police": "SM_Kit_DeskSet", "fire_station": "SM_Kit_DeskSet", "school": "SM_Kit_DeskSet",
            "civic": "SM_Kit_DeskSet",
            "apartment": "SM_Kit_SofaSet", "house": "SM_Kit_SofaSet", "mansion": "SM_Kit_SofaSet",
            "shop": "SM_Kit_ShopShelf", "mall": "SM_Kit_ShopShelf", "gas_station": "SM_Kit_ShopShelf"}
STOREY_CM = 400  # = SM_Kit_StairCore's rise: its top landing is the next storey's floor
CORE_CM = 400  # SM_Kit_StairCore's footprint; scaled 0.7-1.6x to the slab's well
CORE_MIN_CM = 250  # a well narrower than this gets no core (two-storey houses' 2.7 m wells must still get stairs)
MAX_FLOORS = 40  # ponytail: caps instances on the tallest towers; floors above it stay empty
SETS_PER_FLOOR = 8  # furniture sets per storey, the ones nearest the windows (what you see through glass)
FLAT = {"parking": "SM_Kit_ParkingLot"}  # lots that are ground, not buildings: stretched to the lot, never up
TREE_PIT_ROLES = ("street_tree",)  # trees planted in paving get a pit, never straight into the concrete
SCALE_RANGE = (0.5, 2.0)  # a kit mesh is stretched at most this much to fit its lot
# Fortnite's own trees, placed as props: actors a player can chop. PCG cannot instance their meshes (UEFN's asset
# reference policy refuses every Fortnite tree mesh in a spawner), so each of these trees is one actor in the
# level: right for a city's trees (streets, parks, gardens), never for the forest. role -> [(prop, height cm)]
_FN_TREES = "/Game/Creative/Environments/ApolloTrees/"
_FN_PARK_TREE = "/Game/Creative/BuildingActors/Props/CP_Neo_Park_Tree_01"
TREE_PROPS = {
    "street_tree": [(_FN_PARK_TREE, 1079), (_FN_TREES + "CP_Apollo_Tree_Birch_Small_NoSnow", 804)],
    "tree": [(_FN_TREES + "CP_Tree_Apollo_Medium_Frontier", 1344), (_FN_TREES + "CP_Apollo_Tree_Birch_Large_NoSnow", 1851),
             (_FN_TREES + "CP_Apollo_PineTree_Large2_NoSnow", 1354), (_FN_PARK_TREE, 1079),
             (_FN_TREES + "CP_Apollo_DarkPine_01_NoSnow", 1398), (_FN_TREES + "CP_Apollo_Tree_Birch_Small_NoSnow", 804)],
}
TREE_PROP_SCALE = (0.6, 1.3)  # a prop tree is scaled to the height the plan gave its spot, within this
TREE_PROPS_MAX = 6000  # a world places at most this many: every one is an actor in the level's memory budget
PUBLIC_TREES = ("Street trees", "Park trees", "Interchange trees")  # these come first; the others share the rest
PROP_RECIPE = "props-1"  # how a tile of props is placed: a change here places every tile again


def _size(name: str) -> tuple:
    b = KIT[name][1:]
    return b[3] - b[0], b[4] - b[1], b[5] - b[2]


def bounds(name: str):
    """A kit mesh's box (min x, y, z, max x, y, z) in cm, or None for a mesh the plugin has no size for."""
    return KIT[name][1:] if name in KIT else grammar.BOUNDS.get(name)


def _height(parts: list) -> float:
    return max(KIT[p][6] for p in parts)


def _pick(options: list, x: float, y: float) -> int:
    return abs(int(x) * 73856093 ^ int(y) * 19349663) % len(options)


def path(name: str, root: str) -> str:
    return f"{root}/WorldGen/{KIT[name][0]}/{name}.{name}"


MODULAR_ROLES = ("house",)  # always built from parts, whatever the layer says
HOUSE_STOREYS = (1, 2, 2, 3)  # a house's storeys, picked by its spot
HOUSE_FORMS = ("box", "box", "L")  # ... and its shape

_BAKE = {"interiors": True}  # ponytail: one switch for the bake in progress; bakes run one at a time


def bake(previewed: dict, root: str, interiors: bool = True, props: dict | None = None) -> tuple:
    """(groups {mesh path: [[x, y, z, yaw, sx, sy, sz]]}, {layer: points baked}, [layers with no kit piece]).
    interiors=False: building shells only (no floors, stair cores or furniture). props: a dict to fill with the
    city's trees as Fortnite props instead of kit trees ({layer: [[prop, x, y, z, yaw, scale]]}, see TREE_PROPS)."""
    _BAKE["interiors"] = interiors
    lo, hi = SCALE_RANGE
    groups: dict = {}
    baked, missing = {}, []
    for name, layer in previewed.items():
        role = layer.get("role")
        pts = layer.get("points") or []
        if layer.get("kind") == "site" and layer.get("mesh") in KIT:  # a neighbourhood's own pieces, pre-scaled
            for p in pts:
                groups.setdefault(path(layer["mesh"], root), []).append(list(p[:7]))
            baked[name] = len(pts)
        elif layer.get("meshes"):  # the person's / an AI's own layer: exactly its meshes, never a guessed kit role
            _own(groups, layer["meshes"], pts, layer.get("kind"), root)
            baked[name] = len(pts)
        elif layer.get("kind") == "buildings" and role == "park":
            # a park is what stands in it: the 'Park trees', 'Park paths', 'Park benches' and 'Playgrounds' layers
            # (pcg_layers.park_trees, citygen.park_paths), each baked as its own layer. Its lot row places nothing.
            baked[name] = len(pts)
        elif layer.get("kind") == "buildings" and role in FLAT:
            for p in pts:
                w = (p[7] if len(p) > 7 else 30) * 100
                d = (p[8] if len(p) > 8 else 30) * 100
                groups.setdefault(path(FLAT[role], root), []).append([p[0], p[1], p[2], p[3], round(d / 100, 2),
                                                                    round(w / 100, 2), 1.0])
                _ground_pieces(groups, root, p, p[0], p[1], p[2], p[3], w, d, steps=False)  # a slab has side walls too
            baked[name] = len(pts)
        elif layer.get("kind") == "buildings" and (role in grammar.STYLE_BY_ROLE or role in BUILDINGS):
            opts = BUILDINGS.get(role) or [["SM_City_CT_MidRise"]]
            modular = role in MODULAR_ROLES or (layer.get("build") == "modular" and role in grammar.STYLE_BY_ROLE)
            for p in pts:
                fac = p[12] if len(p) > 12 and isinstance(p[12], str) else ""
                if fac in FACILITIES or modular:  # planned facilities are always built for what they are
                    _modular(groups, root, role, p, fac)
                    continue
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
                # a building people go into stands on a lobby storey of kit parts with real doorways (whole meshes
                # have none): the mesh and its floors sit one storey up on it
                lift = _lobby(groups, root, role, parts, x, y, z, yaw, sx, sy) if role in LOBBY_ROLES else 0.0
                for part in parts:
                    groups.setdefault(path(part, root), []).append([x, y, z + lift, yaw, round(sx, 3), round(sy, 3), round(sz, 3)])
                if role in INTERIOR:
                    _interior(groups, root, profile_parts(parts), INTERIOR[role], x, y, z + lift, yaw, sx, sy, sz)
                _ground_pieces(groups, root, p, x, y, z, yaw, w, d)
            baked[name] = len(pts)
        elif role == "sidewalk_corner":
            baked[name] = 0  # every junction's mesh carries its own sidewalk corners: no loose pads on top of them
        elif role in PROPS:
            opts = PROPS[role]
            # a city's trees as Fortnite's own props (when `props` is given): each one an actor, scaled to the
            # height the plan gave its spot. The forest (a foliage layer) is far too many for actors: it stays
            # instanced kit trees.
            real = TREE_PROPS.get(role) if props is not None and layer.get("kind") != "foliage" else None
            for p in pts:
                s = p[4] if len(p) > 4 else 1.0
                kit = opts[_pick(opts, p[0], p[1])]
                if real:
                    prop, tall = real[_pick(real, p[0], p[1])]
                    fit = max(TREE_PROP_SCALE[0], min(TREE_PROP_SCALE[1], KIT[kit][6] * s / tall))
                    props.setdefault(name, []).append([prop, p[0], p[1], p[2], p[3], round(fit, 2)])
                else:
                    groups.setdefault(path(kit, root), []).append([p[0], p[1], p[2], p[3], s, s, s])
                if role in TREE_PIT_ROLES:
                    groups.setdefault(path("SM_Kit_TreePit", root), []).append([p[0], p[1], p[2], p[3], 1.0, 1.0, 1.0])
            baked[name] = len(pts)
        elif pts and layer.get("kind") != "foliage":
            missing.append(name)
    # BuildingKit meshes come into UEFN with Blender's +Y as UEFN's -Y (every one of them: the roof slab spans
    # -512..0, the lobby sticks out at +150). The grammar works in Blender's frame (grammar.BOUNDS), so each piece is
    # placed mirrored back on its own Y: roofs on their buildings, lobbies, awnings and door steps outside again.
    bk = f"/WorldGen/{grammar.FOLDER}/"
    for p, rows in groups.items():
        if bk in p:
            for r in rows:
                r[5] = -abs(r[5])
    return groups, baked, missing


TILE_CM = 40000  # PCG tile: 400 m squares, one graph + volume each, rebuilt only when its content changes
NATURE_TILE_CM = 160000  # nature is sparse: 1.6 km tiles
NATURE = ("SM_V4_Tree", "SM_Kit_Bush", "SM_Kit_Rock", "SM_Kit_Boulder")
# Everything the city places blocks players, except leaves and flat paint (a painted stall or pitch is walked on)
NO_COLLISION = ("SM_Kit_Bush", "SM_Kit_ParkingStall", "SM_Kit_SportsField", "SM_Air_Threshold", "SM_Kit_TreePit",
                "SM_Kit_Wetland", "SM_Kit_Pond")
RECIPE = "collision-1"  # how a tile is built: a change here rebuilds every tile on the next push


def blocks(mesh: str) -> bool:
    """Does this kit mesh stop a player?"""
    return not any(n in mesh for n in NO_COLLISION)


def tiles(groups: dict) -> dict:
    """{tile key 'tx_ty': {'meshes': {mesh: rows}, 'hash': sha1, 'centre': (x, y)}} - the baked city cut into
    TILE_CM squares by each point's position. An edit to the plan changes only the tiles it touches, so
    pcg_build rebuilds those and leaves the rest of the city (and the roads) alone."""
    out: dict = {}
    for mesh, rows in groups.items():
        # trees, bushes and rocks get their own, bigger tiles ('n' keys): thinning the forest then rebuilds a
        # few nature tiles, never the buildings (in shared tiles one tree setting rebuilt the whole city)
        nature = any(n in mesh for n in NATURE)
        size, pre = (NATURE_TILE_CM, "n") if nature else (TILE_CM, "")
        for r in rows:
            tx, ty = math.floor(r[0] / size), math.floor(r[1] / size)
            t = out.setdefault(f"{pre}{tx}_{ty}", {"meshes": {}, "size": size,
                                                   "centre": ((tx + 0.5) * size, (ty + 0.5) * size)})
            t["meshes"].setdefault(mesh, []).append(r)
    for t in out.values():
        t["hash"] = hashlib.sha1((RECIPE + json.dumps(t["meshes"], sort_keys=True)).encode()).hexdigest()
    return out


def tile_diff(new: dict, stored: dict, force: bool = False) -> tuple:
    """(build, remove, unchanged): tile keys to (re)build because they are new or their hash changed,
    stored tiles that are gone from the plan, and tiles left exactly as they are."""
    build = sorted(k for k, t in new.items() if force or (stored.get(k) or {}).get("hash") != t["hash"])
    remove = sorted(k for k in stored if k not in new)
    return build, remove, sorted(k for k in new if k not in build)


def prop_tiles(props: dict, limit: int = 0) -> tuple:
    """({tile key 'tx_ty': {'rows': [[prop, x, y, z, yaw, scale], ...], 'hash': sha1}}, {'placed', 'left_out'}):
    a world's prop trees (bake's `props`) cut into TILE_CM squares, at most `limit` of them (0: TREE_PROPS_MAX).
    Every one is an actor in the level, so the number is held: street, park and interchange trees
    (PUBLIC_TREES) come first, and what the limit leaves is shared out among the others (gardens, infill),
    thinned evenly by each tree's own position, so the same trees stay from build to build."""
    limit = int(limit or 0) or TREE_PROPS_MAX
    public = [r for n in props if n in PUBLIC_TREES for r in props[n]]
    rest = [r for n in props if n not in PUBLIC_TREES for r in props[n]]
    keep_public = min(1.0, limit / len(public)) if public else 1.0
    keep_rest = min(1.0, max(0, limit - len(public)) / len(rest)) if rest else 1.0
    roll = lambda r: (abs(int(r[1]) * 83492791 ^ int(r[2]) * 19349663) % 10000) / 10000
    rows = [r for r in public if roll(r) < keep_public] + [r for r in rest if roll(r) < keep_rest]
    out: dict = {}
    for r in rows:
        out.setdefault(f"{math.floor(r[1] / TILE_CM)}_{math.floor(r[2] / TILE_CM)}", {"rows": []})["rows"].append(r)
    for t in out.values():
        t["rows"].sort(key=lambda r: (r[1], r[2], r[0]))
        t["hash"] = hashlib.sha1((PROP_RECIPE + json.dumps(t["rows"])).encode()).hexdigest()
    return out, {"placed": len(rows), "left_out": len(public) + len(rest) - len(rows)}


FLAG = "SM_Kit_Flagpole"
FRONT_BOX = (-0.5, 0.4, -0.5, 0.5)  # a facility's shell leaves a front strip for its flags, portico, canopy
# planned facility (urban.CATALOG key) -> how it is built. style: grammar style; box: the shell in the lot
# (u0, u1 along the depth, -0.5 back .. 0.5 street; v0, v1 across); shell=False: no building (a stadium);
# hall: open sheds, one storey of interior; furniture: what stands on each floor; extras: [(mesh, u, v, where,
# turn)] with where 'ground' | 'roof' | 'box_front' (on the shell's front face); ring: (mesh, inset, piece cm,
# centred) pieces round the lot's edge, facing in (fences, bleachers).
FACILITIES = {
    "city_hall": dict(style="civic", box=(-0.5, 0.36, -0.5, 0.5), extras=[("SM_Kit_Portico", 0, 0, "box_front", 0),
                      ("SM_Kit_CivicDome", -0.07, 0, "roof", 0), (FLAG, 0.46, -0.42, "ground", 0), (FLAG, 0.46, 0.42, "ground", 0)]),
    "courthouse": dict(style="civic", box=(-0.5, 0.36, -0.5, 0.5),
                       extras=[("SM_Kit_Portico", 0, 0, "box_front", 0), (FLAG, 0.46, 0.42, "ground", 0)]),
    "post_office": dict(style="school", extras=[(FLAG, 0.46, 0.42, "ground", 0)]),
    "library": dict(style="school"),
    "community_center": dict(style="school"),
    "police_hq": dict(style="modern", extras=[(FLAG, 0.46, -0.42, "ground", 0), (FLAG, 0.46, 0.42, "ground", 0)]),
    "police_precinct": dict(style="modern", extras=[(FLAG, 0.46, 0.42, "ground", 0)]),
    "jail": dict(style="jail", box=(-0.36, 0.36, -0.36, 0.36), extras=[("SM_Kit_WatchTower", u, v, "ground", 0)
                 for u in (-0.45, 0.45) for v in (-0.45, 0.45)], ring=("SM_Kit_SecurityFence_4m", 0.49, 400, False)),
    "fire_station": dict(style="industrial", extras=[(FLAG, 0.46, 0.42, "ground", 0)]),
    "ambulance": dict(style="industrial", extras=[("SM_Kit_AmbulanceCanopy", 0, 0, "box_front", 0)]),
    "hospital": dict(style="tower", extras=[("SM_Kit_Helipad", 0, 0, "roof", 0),
                                           ("SM_Kit_AmbulanceCanopy", 0, 0.3, "box_front", 0)]),
    "elementary_school": dict(style="school", extras=[(FLAG, 0.46, 0.42, "ground", 0)]),
    "middle_school": dict(style="school", extras=[(FLAG, 0.46, 0.42, "ground", 0)]),
    "high_school": dict(style="school", extras=[(FLAG, 0.46, -0.42, "ground", 0), (FLAG, 0.46, 0.42, "ground", 0)]),
    "college": dict(style="civic", extras=[(FLAG, 0.46, 0.42, "ground", 0)]),
    "daycare": dict(style="school"),
    "power_plant": dict(style="industrial", box=(-0.5, -0.05, -0.5, 0.5), hall=True, furniture="SM_Kit_FactoryMachine",
                        extras=[("SM_Kit_CoolingTower", 0.24, -0.25, "ground", 0), ("SM_Kit_CoolingTower", 0.24, 0.25, "ground", 0),
                                ("SM_Kit_Chimney", 0.03, -0.44, "ground", 0), ("SM_Kit_Transformer", 0.45, 0, "ground", 0)]),
    "water_treatment": dict(style="industrial", box=(-0.5, 0.0, -0.5, 0.5), hall=True,
                            extras=[("SM_Kit_StorageTank", 0.25, -0.25, "ground", 0), ("SM_Kit_StorageTank", 0.25, 0.25, "ground", 0)]),
    "wastewater": dict(style="industrial", box=(-0.5, 0.0, -0.5, 0.5), hall=True,
                       extras=[("SM_Kit_StorageTank", 0.25, -0.25, "ground", 0), ("SM_Kit_StorageTank", 0.25, 0.25, "ground", 0)]),
    "recycling_center": dict(style="industrial", box=(-0.5, 0.1, -0.5, 0.5), hall=True, furniture="SM_Kit_FactoryMachine",
                             extras=[("SM_Kit_Conveyor", 0.12, -0.3, "ground", 0), ("SM_Kit_Silo", 0.32, 0.3, "ground", 0)]),
    "distribution_center": dict(style="industrial", hall=True),
    "substation": dict(style="industrial", hall=True),
    "train_station": dict(style="shop", box=(-0.3, 0.5, -0.5, 0.5),
                          extras=[("SM_Kit_PlatformCanopy", -0.45, 0, "ground", 90)]),
    "bus_terminal": dict(style="shop", box=(-0.5, 0.3, -0.5, 0.5), extras=[("SM_Kit_PlatformCanopy", 0.42, 0, "ground", 90)]),
    "stadium": dict(shell=False, ring=("SM_Kit_Bleachers", 0.44, 2000, True),
                    extras=[(FLAG, 0.49, -0.49, "ground", 0), (FLAG, 0.49, 0.49, "ground", 0)]),
    "sports_complex": dict(style="school"),
    "hotel": dict(style="tower"),
}
# industrial sheds hold machines, not desks
FURNITURE_BY_STYLE = {"industrial": "SM_Kit_FactoryMachine", "jail": "SM_Kit_DeskSet"}


def _modular(groups: dict, root: str, role: str, p: list, fac: str = "") -> None:
    """A lot built from BuildingKit modules by the grammar (grammar.py), placed in the lot's frame, with its
    interiors (floors and a stair core on every storey) on the same storey grid and its pad pieces. A planned
    facility gets its own style, its shell in part of the lot, and its machines / extras (FACILITIES)."""
    x, y, z, yaw = p[0], p[1], p[2], p[3]
    h = (p[5] if len(p) > 5 else 8) * 100
    form = FORM_NAMES[p[6]] if len(p) > 6 and isinstance(p[6], int) and 0 <= p[6] < len(FORM_NAMES) else "box"
    w = (p[7] if len(p) > 7 else 12) * 100
    d = (p[8] if len(p) > 8 else 12) * 100
    spec = FACILITIES.get(fac) or {}
    if role == "house" and not spec:  # every house its own: storeys and shape from its spot, so a street varies
        # (a seeded stream, not the position hash: lot centres are round numbers and that hash then repeats)
        spot = random.Random(f"house:{round(x)}:{round(y)}")
        h = spot.choice(HOUSE_STOREYS) * grammar.STOREY_CM
        form = spot.choice(HOUSE_FORMS)
    style = spec.get("style") or grammar.STYLE_BY_ROLE.get(role, "tower")
    u0, u1, v0, v1 = spec.get("box", FRONT_BOX if spec else (-0.5, 0.5, -0.5, 0.5))
    cu, cv = (u0 + u1) / 2 * d, (v0 + v1) / 2 * w
    fx, fy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))

    def put(mesh, u, v, h0, turn, s=(1.0, 1.0, 1.0)):
        groups.setdefault(mesh, []).append([round(x + fx * u - fy * v), round(y + fy * u + fx * v), round(z + h0),
                                            round((yaw + turn) % 360, 1), *s])

    top = 0.0
    if spec.get("shell", True):
        seed = abs(int(x) * 73856093 ^ int(y) * 19349663)
        rows, blocks = grammar.build(spec.get("form", form), role, (u1 - u0) * d, (v1 - v0) * w, h, seed,
                                     lambda n: f"{root}/WorldGen/{grammar.FOLDER}/{n}.{n}", style)
        for mesh, rs in rows.items():
            for u, v, h0, turn, sx, sy, sz in rs:
                put(mesh, u + cu, v + cv, h0, turn, (sx, sy, sz))
        blocks = [(bx0 + cu, by0 + cv, bz0, bx1 + cu, by1 + cv, bz1) for bx0, by0, bz0, bx1, by1, bz1 in blocks]
        top = max(b[5] for b in blocks)
        if spec.get("hall"):  # an open shed: one floor of machines, the hall above it stays open
            blocks = [(b[0], b[1], b[2], b[3], b[4], b[2] + grammar.STOREY_CM) for b in blocks]
        furniture = spec.get("furniture") or FURNITURE_BY_STYLE.get(style) or INTERIOR.get(role)
        if furniture:
            _interior(groups, root, blocks, furniture, x, y, z, yaw, 1.0, 1.0, 1.0, storey=grammar.STOREY_CM)
    for mesh, u, v, where, turn in spec.get("extras", []):
        if where == "box_front":  # against the shell's street face
            put(path(mesh, root), u1 * d, v * w + cv, 0, turn)
        else:
            put(path(mesh, root), u * d, v * w, top if where == "roof" else 0, turn)
    if spec.get("ring"):
        mesh, inset, piece, centred = spec["ring"]
        corners = [(-inset * d, -inset * w), (inset * d, -inset * w), (inset * d, inset * w), (-inset * d, inset * w)]
        for (pu, pv), (qu, qv) in zip(corners, corners[1:] + corners[:1]):  # counter-clockwise: the lot is on the left
            L = math.hypot(qu - pu, qv - pv)
            n = max(1, round(L / piece))
            du, dv = (qu - pu) / n, (qv - pv) / n
            turn = math.degrees(math.atan2(qv - pv, qu - pu))
            for i in range(n):
                k = i + 0.5 if centred else i
                put(path(mesh, root), pu + du * k, pv + dv * k, 0, turn, (round(L / n / piece, 3), 1.0, 1.0))
    _ground_pieces(groups, root, p, x, y, z, yaw, w, d, front=u1 * d)



# whole-mesh buildings people walk into: they get a lobby storey (_lobby). Factories, warehouses and utilities are
# open yards of containers and cranes.
LOBBY_ROLES = ("skyscraper", "office", "apartment", "shop", "mall", "gas_station", "mansion", "hospital", "police",
               "fire_station", "school", "civic", "transit")


def _lobby(groups: dict, root: str, role: str, parts: list, x, y, z, yaw, sx, sy) -> float:
    """One storey of BuildingKit walls under a whole kit mesh, on the footprint of its lowest storey (its glazed
    shell, PROFILES, else its box), in its role's style: the street front gets the style's entrance doorway (lobby,
    revolving door, storefront entrance...), the same doorway modules every modular building uses. Returns the lift
    (one storey) the mesh is placed up by."""
    base = parts[0].rsplit("_P", 1)[0] if "_P" in parts[0] else parts[0]
    row = next((r for r in PROFILES.get(base, []) if r[0] == 0), None)
    if row:
        u0, v0, u1, v1 = row[1], row[2], row[3], row[4]
    else:
        b = KIT[parts[0]][1:]
        u0, v0, u1, v1 = b[0], b[1], b[3], b[4]
    u0, u1, v0, v1 = u0 * sx, u1 * sx, v0 * sy, v1 * sy
    cu, cv = (u0 + u1) / 2, (v0 + v1) / 2
    style = grammar.STYLE_BY_ROLE.get(role, "tower")
    seed = abs(int(x) * 73856093 ^ int(y) * 19349663)
    rows, _ = grammar.build("box", role, u1 - u0, v1 - v0, grammar.STOREY_CM, seed,
                            lambda n: f"{root}/WorldGen/{grammar.FOLDER}/{n}.{n}", style, roof=False)
    fx, fy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    for mesh, rs in rows.items():
        for u, v, h0, turn, msx, msy, msz in rs:
            u, v = u + cu, v + cv
            groups.setdefault(mesh, []).append([round(x + fx * u - fy * v), round(y + fy * u + fx * v), round(z + h0),
                                                round((yaw + turn) % 360, 1), msx, msy, msz])
    return float(grammar.STOREY_CM)


def _interior(groups: dict, root: str, parts: list, furniture: str, x, y, z, yaw, sx, sy, sz,
              storey: float = 0.0) -> None:
    """Floors inside a building shell, per storey: a slab inset from the walls (glowing ceiling panels under it)
    with a well in the middle, the stair + elevator core standing in that well (walkable switchback stairs,
    one storey per piece, so the stairs climb the whole building), and furniture along the windows facing
    out. One elevator car waits at the ground floor. Round / sculpted towers get a round slab well inside
    their outline so nothing pokes through the glass; a building too small for the core gets solid slabs."""
    if not _BAKE["interiors"]:
        return
    fx, fy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))

    def put(mesh, u, v, h, turn, s):
        groups.setdefault(path(mesh, root), []).append(
            [round(x + fx * u - fy * v), round(y + fy * u + fx * v), round(z + h), round((yaw + turn) % 360, 1), *s])

    floors, below, car = 0, 0.0, False
    storey = storey or STOREY_CM
    if furniture in KIT and KIT[furniture][6] > storey - 40:
        furniture = ""  # taller than the room it stands in: it would stand through the floor above
    # parts may also be bounds (x0, y0, z0, x1, y1, z1): a modular building's blocks; or a measured storey
    # (x0, y0, z0, x1, y1, z1, round, more above) from PROFILES: the glazed shell itself, so the slab fills it
    for part in parts:
        x0, y0, z0, x1, y1, z1 = KIT[part][1:] if isinstance(part, str) else part[:6]
        measured = not isinstance(part, str) and len(part) > 6
        above = measured and part[7]
        name = part if isinstance(part, str) else ""
        roof = 150 if name and name == parts[-1] else 0
        rnd = ("Round" in name or "SM_Sky" in name) if not measured else bool(part[6])
        odd = name in ("SM_Bld_Hospital", "SM_Bld_Police") or "Mansion_P1" in name
        inset = 0.9 if measured else 0.6 if "SM_Sky" in name or odd else 0.66 if rnd else 0.86
        part = name
        cu, cv = (x0 + x1) / 2 * sx, (y0 + y1) / 2 * sy
        ru, rv = (x1 - x0) / 2 * sx * inset, (y1 - y0) / 2 * sy * inset
        if rnd:
            ru = rv = min(ru, rv)
        # storeys on one building-wide grid, so the stairs run on through the tower's parts without a gap;
        # the last storey under the roof stays clear of it
        # a tower's stacked parts continue one grid; a modular building's wings (L, courtyard) each start at their own base
        k0, k1 = math.ceil(((below if part else 0) or z0 * sz) / storey), int((z1 * sz - roof) // storey)
        if measured:  # one measured storey, stretched with the tower: rounded, so stretched rows tile without a gap
            k0, k1 = math.floor(z0 * sz / storey + 0.5), math.floor(z1 * sz / storey + 0.5)
        if k1 <= k0 or min(ru, rv) < 150:
            continue
        below = k1 * storey
        # the well: square 0.6 r x 0.6 r (FloorPlate's hole), or the square inside FloorRound's round hole
        wu, wv = (0.594 * ru, 0.594 * ru) if rnd else (0.6 * ru, 0.6 * rv)
        has_core = min(wu, wv) >= CORE_MIN_CM
        plate = ("SM_Kit_FloorRound" if rnd else "SM_Kit_FloorPlate") if has_core else "SM_Kit_FloorSolid"
        # the core's rise is STOREY_CM: squashed to the building's storey so its top landing is the next floor
        cs = [round(max(0.7, min(2.2, wu / CORE_CM)), 3), round(max(0.7, min(2.2, wv / CORE_CM)), 3),
              round(storey / STOREY_CM, 3)]
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
            h = k * storey + 10
            put(plate, cu, cv, h, 0, [round(2 * ru / 100, 2), round(2 * rv / 100, 2), 1.0])
            # a core climbs one storey; under a modular block's roof there is no storey to climb to (its shaft and
            # rails stood through the roof), the flight from below lands on this floor
            if has_core and (part or k < k1 - 1 or above):
                put("SM_Kit_StairCore", cu, cv, h, 0, cs)
                if not car:
                    put("SM_Kit_ElevatorCar", cu, cv + 115 * cs[1], h + 15, 0, [cs[0], 1.0, 1.0])
                    car = True
            for _, u, v in spots if furniture else []:  # facing the nearest window
                if rnd:
                    turn = math.degrees(math.atan2(v, u))
                elif abs(u) / ru >= abs(v) / rv:
                    turn = 0 if u >= 0 else 180
                else:
                    turn = 90 if v >= 0 else -90
                put(furniture, cu + u, cv + v, h + 15, round(turn, 1), [1.0, 1.0, 1.0])


def object_path(path: str) -> str:
    """'/Game/Props/SM_Bench' -> '/Game/Props/SM_Bench.SM_Bench' (Static Mesh Spawner wants the object path)."""
    path = str(path).strip().strip("'").split("'")[-1] if "'" in str(path) else str(path).strip()
    tail = path.rsplit("/", 1)[-1]
    return path if "." in tail else f"{path}.{tail}"


def _own(groups: dict, meshes: list, pts: list, kind: str = "", root: str = "") -> None:
    """Points of a layer with its own meshes: a weighted pick per point (stable for its position), the point's
    yaw and scale as the preview placed it (random yaw, scale range, sink: the layer's settings).
    A buildings layer whose mesh gives `size_cm` [x depth, y width, z height] (the piece manifest's spec) is
    fitted to each lot like a kit building and gets its pad pieces (plinth, paths, retaining walls)."""
    paths = [object_path(m["path"] if isinstance(m, dict) else m) for m in meshes]
    weights = [float(m.get("weight", 1)) if isinstance(m, dict) else 1.0 for m in meshes]
    total = sum(weights) or 1.0
    for p in pts:
        roll = (abs(int(p[0]) * 73856093 ^ int(p[1]) * 19349663) % 10000) / 10000 * total
        k = 0
        while k < len(paths) - 1 and roll >= weights[k]:
            roll -= weights[k]
            k += 1
        s = p[4] if len(p) > 4 else 1.0
        size = meshes[k].get("size_cm") if isinstance(meshes[k], dict) else None
        if kind == "buildings" and size and len(p) > 8:
            lo, hi = SCALE_RANGE
            fit = [max(lo, min(hi, p[8] * 100 / size[0])), max(lo, min(hi, p[7] * 100 / size[1])),
                   max(lo, min(hi, p[5] * 100 / size[2]))]
            groups.setdefault(paths[k], []).append([p[0], p[1], p[2], p[3], *[round(v, 3) for v in fit]])
            _ground_pieces(groups, root, p, p[0], p[1], p[2], p[3], p[7] * 100, p[8] * 100)
            continue
        groups.setdefault(paths[k], []).append([p[0], p[1], p[2], p[3], s, s, s])


def own_meshes(previewed: dict) -> list:
    """Every mesh the person's own layers use (their materials need the instanced-mesh usage for PCG)."""
    return sorted({object_path(m["path"] if isinstance(m, dict) else m)
                   for layer in previewed.values() for m in layer.get("meshes") or []})





FOUND_COURSE_M = 0.5  # the plinth course a building stands on: this tall ...
FOUND_OUT_M = 0.3  # ... and this far out past its walls
FOUND_WALL_OUT_M = 0.15  # the foundation wall under the course
FOUND_EMBED_M = EMBED_CM / 100  # into the ground below the lowest corner, as deep as an approach wall goes


def foundation(found_m: float) -> list:
    """A building's own foundation, the way a road carries its side walls (road_profiles 'skirt'): a plinth
    course round its foot and, under it, a wall down to FOUND_EMBED_M below the lowest ground it stands over.
    On EVERY building, however flat its lot: wherever the land falls away it stands on concrete, never on air
    or on a loose block. [(top below the floor m, depth m, out past the walls m)], the course first: one
    SM_Kit_Plinth box each, as wide and deep as the building plus twice `out`. The panel draws the same two
    (ui/app.js FOUND)."""
    return [(0.02, FOUND_COURSE_M, FOUND_OUT_M),
            (FOUND_COURSE_M, round(max(0.0, found_m) + FOUND_EMBED_M - FOUND_COURSE_M, 2), FOUND_WALL_OUT_M)]


def _ground_pieces(groups: dict, root: str, p: list, x, y, z, yaw, w, d, front: float = 0.0, steps: bool = True) -> None:
    """A lot's foundation (foundation()), steps down from its door when the land falls away under it, and its
    frontage slabs. front: the door's distance ahead of the lot centre (cm)."""
    found = p[9] if len(p) > 9 else 0.0
    for drop, deep, out in foundation(found):
        groups.setdefault(path("SM_Kit_Plinth", root), []).append(
            [x, y, round(z - drop * 100), yaw, round(d / 100 + 2 * out, 2), round(w / 100 + 2 * out, 2), deep])
    if found > 0.3 and steps:
        f = front or d / 2
        fx, fy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        groups.setdefault(path("SM_Kit_EntranceSteps", root), []).append(
            [round(x + fx * f), round(y + fy * f), round(z - found * 100), yaw, 1.0, 1.0, round(found, 2)])
    for width, flat in (p[10] if len(p) > 10 else []):
        if width <= 8.0:
            continue  # walkways (Paths actor) and driveways (Access actor) are splines, not slabs
        for i in range(3, len(flat) - 2, 3):  # a frontage apron (plaza) is a slab
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
    course, wall = g2[path("SM_Kit_Plinth", "/R")][:2]  # its foundation: the course, then the wall 1 m into the ground
    assert course[2:7] == [498, 0, 12.6, 12.6, 0.5] and wall[2:7] == [450, 0, 12.3, 12.3, 2.3], (course, wall)
    flat = bake({"Houses": {"kind": "buildings", "role": "house", "points": [[0, 0, 0, 0, 1, 7, 0, 12, 12, 0, []]]}}, "/R")[0]
    assert [r[6] for r in flat[path("SM_Kit_Plinth", "/R")]] == [0.5, 0.5] and path("SM_Kit_EntranceSteps", "/R") not in flat, \
        "a building on flat ground has its foundation too, and no steps"
    assert len(g2[path("SM_Kit_Plinth", "/R")]) == 4, "the parking lot's slab stands on side walls as well"
    assert path("SM_Kit_PathSlab", "/R") not in g2, "a walkway is a spline now, not a slab"
    plaza = {"Shops": {"kind": "buildings", "role": "shop",
                       "points": [[0, 0, 500, 0, 1, 7, 0, 12, 12, 0, [[10.8, [600, 0, 500, 1100, 0, 350]]]]]}}
    slab = bake(plaza, "/R")[0][path("SM_Kit_PathSlab", "/R")][0]
    assert slab[4] == 5.0 and slab[5] == 10.8 and slab[7] < -15, slab
    assert g2[path("SM_Kit_ParkingLot", "/R")][0][4:7] == [40.0, 30.0, 1.0]
    tall = [p for p, rows in groups.items() if "SM_Kit_" not in p and f"/{grammar.FOLDER}/" not in p
            and any(r[0] == 0 for r in rows)]
    assert len(tall) >= 2 and all("SM_City_DT_" in p or "SM_Sky" in p for p in tall), "a 110 m lot gets a whole multi-part tower"
    assert len({tuple(r) for p in tall for r in groups[p] if r[0] == 0}) == 1, "every part of a tower shares one point"
    # ... standing one storey up on a lobby of kit walls, with an entrance doorway on its street front at street level
    assert all(r[2] == 500 + grammar.STOREY_CM for p in tall for r in groups[p] if r[0] == 0), "the tower sits on its lobby"
    doors = [r for p, rows in groups.items() if p.endswith("_Doorway") for r in rows if abs(r[1]) < 4000 and r[2] == 500]
    assert doors, "the 110 m tower has a street-level entrance"
    for p, rows in groups.items():
        for r in rows:
            assert "SM_Kit_" in p or f"/{grammar.FOLDER}/" in p or all(SCALE_RANGE[0] <= v <= SCALE_RANGE[1] for v in r[4:7]) \
                or r[4] == 1.1, r
    # a house is BuildingKit parts only: never a whole-house mesh, no door leaf, open doorways
    hp = [p for p in groups if f"/{grammar.FOLDER}/" in p]
    assert any("SM_BK_House_Wall_Door_Doorway" in p for p in hp) and not any("DoorLeaf" in p for p in groups), hp
    assert not any("SM_City_HS_" in p or "SM_Bld_House" in p for p in groups)
    many = bake({"H": {"kind": "buildings", "role": "house", "points": [[x * 3000, x * 1700, 0, 0, 1, 7, 0, 12, 14, 0, []]
                                                                          for x in range(40)]}}, "/R")[0]
    tops = {r[2] for p, rows in many.items() if "SM_BK_Parapet" in p for r in rows}
    assert len(tops) >= 3, ("houses differ in storeys", sorted(tops))
    assert all(p.startswith("/R/WorldGen/") and p.count(".") == 1 for p in groups)
    assert any("SM_V4_Tree" in p for p in groups), "forest trees are baked"
    gb, bb, mb = bake({"Bushes": {"kind": "foliage", "role": "bush", "points": [[1, 1, 0, 0, 1.2]]},
                       "Rocks": {"kind": "foliage", "role": "rock", "points": [[2, 2, 0, 0, 0.9]]}}, "/R")
    assert path("SM_Kit_Bush", "/R") in gb and path("SM_Kit_Rock", "/R") in gb and not mb, "bushes and rocks are PCG layers too"
    # a layer of the person's own meshes: exactly those meshes, by weight, whatever its guessed role
    mine = {"Flower beds": {"kind": "scatter", "role": "street_lamp", "points": [[i * 997, i * 613, 0, 10, 1.1] for i in range(400)],
                            "meshes": [{"path": "/Game/Garden/SM_Tulips", "weight": 3}, {"path": "/Game/Garden/SM_Roses.SM_Roses", "weight": 1}]}}
    go, bo, mo = bake(mine, "/R")
    assert set(go) == {"/Game/Garden/SM_Tulips.SM_Tulips", "/Game/Garden/SM_Roses.SM_Roses"} and bo == {"Flower beds": 400} and not mo, go.keys()
    assert 2.0 < len(go["/Game/Garden/SM_Tulips.SM_Tulips"]) / len(go["/Game/Garden/SM_Roses.SM_Roses"]) < 4.5, "weights hold"
    assert own_meshes(mine) == sorted(go)
    gs = bake({"Parking stalls": {"kind": "site", "mesh": "SM_Kit_ParkingStall", "points": [[1, 2, 3, 90.0, 1.0, 1.0, 1.0]]}}, "/R")[0]
    assert gs[path("SM_Kit_ParkingStall", "/R")] == [[1, 2, 3, 90.0, 1.0, 1.0, 1.0]], gs
    # a Blender building with its manifest size fits its lot: 24 m deep lot / 12 m model = x2 (clamped to 2)
    blds = {"Mine": {"kind": "buildings", "role": "shop", "meshes": [{"path": "/G/SM_MyShop", "size_cm": [1200, 1500, 900]}],
                     "points": [[0, 0, 0, 0, 1, 9, 0, 15, 24, 1.0, [], []]]}}
    gm = bake(blds, "/R")[0]
    assert gm["/G/SM_MyShop.SM_MyShop"][0][4:7] == [2.0, 1.0, 1.0] and path("SM_Kit_Plinth", "/R") in gm, gm
    park = {"Parks": {"kind": "buildings", "role": "park", "points": [[0, 0, 0, 0, 1, 0, 0, 40, 40, 0, []]]},
            "Park paths": {"kind": "site", "role": "site", "mesh": "SM_Kit_PathSlab", "box_m": [1, 1, 0.08], "points": [[0, 0, 0, 0.0, 6.0, 3.0, 1.0]]},
            "Park benches": {"kind": "site", "role": "site", "mesh": "SM_Prop_Bench", "box_m": [1.94, 0.67, 1.11], "points": [[0, 200, 0, 0.0, 1.0, 1.0, 1.0]]}}
    g3, b3, _ = bake(park, "/R")
    assert b3 == {"Parks": 1, "Park paths": 1, "Park benches": 1} and set(g3) == {path("SM_Kit_PathSlab", "/R"), path("SM_Prop_Bench", "/R")}, \
        ("a park's lot places nothing itself: its paths, benches and trees are layers of their own", sorted(g3))
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
    # tiles: moving one house changes only its tile's hash
    city = {"H": {"kind": "buildings", "role": "house",
                  "points": [[x, 20000, 0, 0, 1, 7, 0, 12, 12, 0, []] for x in (10000, 20000, 50000, 60000, 90000, 100000)]}}
    t1 = tiles(bake(city, "/R")[0])
    city["H"]["points"][5][1] = 20500  # move the last house (x 100000 -> tile 2_0)
    t2 = tiles(bake(city, "/R")[0])
    assert set(t1) == set(t2) == {"0_0", "1_0", "2_0"}, sorted(t1)
    assert [k for k in t1 if t1[k]["hash"] != t2[k]["hash"]] == ["2_0"], "only the edited tile changes"
    stored = {k: {"hash": t["hash"]} for k, t in t1.items()}
    stored["9_9"] = {"hash": "x"}  # a tile the plan no longer has
    assert tile_diff(t2, stored) == (["2_0"], ["9_9"], ["0_0", "1_0"])
    assert tile_diff(t2, stored, force=True)[0] == ["0_0", "1_0", "2_0"]
    wild = tiles({path("SM_V4_TreeRound", "/R"): [[10, 10, 0, 0, 1, 1, 1], [150000, 10, 0, 0, 1, 1, 1]],
                  path("SM_Kit_Plinth", "/R"): [[10, 10, 0, 0, 1, 1, 1]]})
    assert set(wild) == {"n0_0", "0_0"} and wild["n0_0"]["size"] == NATURE_TILE_CM, "trees in their own big tile"
    assert blocks(path("SM_City_CT_Apartment", "/R")) and blocks(path("SM_V4_TreePine", "/R")) and not blocks(path("SM_Kit_Bush", "/R"))
    # street trees stand in a pit, forest trees do not
    gp = bake({"Street trees": {"kind": "along_roads", "role": "street_tree", "points": [[5, 6, 7, 30, 1.0]]},
               "Trees": {"kind": "foliage", "role": "tree", "points": [[9, 9, 0, 0, 1.0]]}}, "/R")[0]
    assert gp[path("SM_Kit_TreePit", "/R")] == [[5, 6, 7, 30, 1.0, 1.0, 1.0]], gp
    # Fortnite trees: a city's trees become props (one actor each), scaled to the height the plan gave them; the
    # pit under a street tree stays, and the forest stays instanced kit trees
    town = {"Street trees": {"kind": "along_roads", "role": "street_tree", "points": [[5, 6, 7, 30, 1.0]]},
            "Garden trees": {"kind": "scatter", "role": "tree", "points": [[100 * i, 50000 + 7 * i, 0, 0, 1.0] for i in range(400)]},
            "Trees": {"kind": "foliage", "role": "tree", "points": [[9, 9, 0, 0, 1.0]]}}
    real: dict = {}
    gf = bake(town, "/R", props=real)[0]
    assert set(real) == {"Street trees", "Garden trees"} and len(real["Garden trees"]) == 400, set(real)
    assert gf[path("SM_Kit_TreePit", "/R")] == [[5, 6, 7, 30, 1.0, 1.0, 1.0]] and sum(len(v) for k, v in gf.items() if "SM_V4_Tree" in k) == 1
    st = real["Street trees"][0]
    assert st[0] in [c for c, _h in TREE_PROPS["street_tree"]] and st[1:5] == [5, 6, 7, 30], st
    heights = dict(TREE_PROPS["tree"])
    assert all(TREE_PROP_SCALE[0] <= r[5] <= TREE_PROP_SCALE[1] and 500 < heights[r[0]] * r[5] < 1400 for r in real["Garden trees"]), \
        "every prop stands about as tall as the tree the plan drew"
    assert len({r[0] for r in real["Garden trees"]}) > 3, "a mix of kinds"
    assert bake(town, "/R")[0] != gf and "SM_V4_Tree" in "".join(bake(town, "/R")[0]), "without props: kit trees as before"
    pt, info = prop_tiles(real)
    assert info == {"placed": 401, "left_out": 0} and sum(len(t["rows"]) for t in pt.values()) == 401
    assert set(pt) == {"0_0", "0_1"} and prop_tiles(real)[0]["0_1"]["hash"] == pt["0_1"]["hash"], set(pt)
    few, info_few = prop_tiles(real, limit=101)
    kept = [r for t in few.values() for r in t["rows"]]
    assert st in kept and 60 < len(kept) < 140 and info_few["left_out"] == 401 - len(kept), "the street tree stays; gardens are thinned"
    assert prop_tiles(real, limit=101)[0] == few and all(r in real["Garden trees"] or r == st for r in kept), "the same ones every time"
    assert prop_tiles({}) == ({}, {"placed": 0, "left_out": 0})
    # modular: an L-shaped school on a hill, built from BuildingKit modules in the lot's frame (front = yaw)
    bk = lambda n: f"/R/WorldGen/{grammar.FOLDER}/{n}.{n}"
    lot = [10000, 0, 300, 90, 1, 11, FORM_NAMES.index("L"), 44, 36, 1.2, []]
    gm2, bm2, mm2 = bake({"Schools": {"kind": "buildings", "role": "school", "build": "modular", "points": [lot]}}, "/R")
    assert bm2 == {"Schools": 1} and not mm2, (bm2, mm2)
    # every BuildingKit piece mirrored back on its Y (UEFN imports the kit with Blender's +Y as its -Y)
    assert all(r[5] < 0 for k, rs in gm2.items() if f"/{grammar.FOLDER}/" in k for r in rs), "BuildingKit rows mirrored"
    assert not any("SM_City_" in k for k in gm2), "a modular lot never stretches a whole mesh"
    glass = gm2[bk("SM_BK_Modern_SlidingGlassWall")]
    assert glass and all(r[2] >= 300 for r in glass), "walls stand on the pad"
    assert max(r[1] for r in glass) <= 1800 + 1 and min(r[1] for r in glass) >= -1800 - 1, "walls stay on the 36 m deep lot (+X = north)"
    floors = gm2[path("SM_Kit_FloorPlate", "/R")] + gm2.get(path("SM_Kit_FloorSolid", "/R"), [])
    assert len({r[2] for r in floors}) == 3 and all((r[2] - 310) % grammar.STOREY_CM == 0 for r in floors), \
        ("3 storeys on the BuildingKit grid, both wings furnished", sorted({r[2] for r in floors}))
    assert all(r[6] == round(grammar.STOREY_CM / STOREY_CM, 3) for r in gm2[path("SM_Kit_StairCore", "/R")]), "core fits the storey"
    assert path("SM_Kit_Plinth", "/R") in gm2, "pad pieces still come with a modular lot"
    # planned facilities: each built for what it is, from its own style and machines, hollow with stairs
    def fac_lot(key, role, w=60, d=50, h=12, found=0.0):
        return {key: {"kind": "buildings", "role": role, "points": [[0, 0, 0, 0, 1, h, 0, w, d, found, [], [], key]]}}
    gc = bake(fac_lot("city_hall", "civic", found=1.0), "/R")[0]
    for piece in ("SM_Kit_Portico", "SM_Kit_CivicDome", "SM_Kit_Flagpole", "SM_Kit_EntranceSteps", "SM_Kit_StairCore"):
        assert path(piece, "/R") in gc, piece
    assert bk("SM_BK_Mansion_Wall_GrandEntrance_Doorway") in gc and not any("SM_City_" in k for k in gc), "a city hall, not a mid-rise"
    portico = gc[path("SM_Kit_Portico", "/R")][0]
    assert portico[0] == round(0.36 * 5000), ("the portico stands on the shell's front face", portico)
    assert gc[path("SM_Kit_CivicDome", "/R")][0][2] >= 1152 - 1, "the dome sits on the roof"
    gj = bake(fac_lot("jail", "police", 100, 80), "/R")[0]
    assert len(gj[path("SM_Kit_WatchTower", "/R")]) == 4 and len(gj[path("SM_Kit_SecurityFence_4m", "/R")]) > 30
    assert bk("SM_BK_Jail_Wall_SallyPort") in gj and path("SM_Kit_StairCore", "/R") in gj
    gpp = bake(fac_lot("power_plant", "factory", 90, 90, 18), "/R")[0]
    assert len(gpp[path("SM_Kit_CoolingTower", "/R")]) == 2 and path("SM_Kit_Chimney", "/R") in gpp
    assert path("SM_Kit_FactoryMachine", "/R") in gpp and bk("SM_BK_Ind_Wall_RollerDoor_Doorway") in gpp
    assert len({r[2] for r in gpp[path("SM_Kit_FactoryMachine", "/R")]}) == 1, "a power hall is one open storey, not 5 floors"
    assert path("SM_Kit_StairCore", "/R") not in gpp, "a one-storey hall has nowhere for stairs to go"
    # no stair core under a modular roof: the top storey's floor is where the last flight lands
    cores = gc[path("SM_Kit_StairCore", "/R")]
    assert max(r[2] for r in cores) + 415 <= 1152 + 10, ("no core pokes through the roof", sorted(r[2] for r in cores))
    gst = bake(fac_lot("stadium", "mall", 140, 116, 10), "/R")[0]
    assert len(gst[path("SM_Kit_Bleachers", "/R")]) >= 20 and not any("SM_BK_" in k for k in gst), "a bowl, no shell"
    assert path("SM_Kit_EntranceSteps", "/R") not in gst, "no plinth, no steps"
    gh2 = bake(fac_lot("hospital", "hospital", 80, 60, 30), "/R")[0]
    assert path("SM_Kit_Helipad", "/R") in gh2 and len({r[2] for r in gh2[path("SM_Kit_StairCore", "/R")]}) >= 7
    assert all(kb_ in KIT for kb_ in ("SM_Kit_CoolingTower", "SM_Kit_WatchTower", "SM_Kit_EntranceSteps"))
    for spec in FACILITIES.values():  # every extra is a kit piece and every style exists
        assert all(e[0] in KIT for e in spec.get("extras", [])) and (not spec.get("style") or spec["style"] in grammar.STYLES)
    # nothing inside stands through the ceiling: every furniture piece fits under the next storey's slab
    gfac = bake({"F": {"kind": "buildings", "role": "factory", "build": "modular", "points": [[0, 0, 0, 0, 1, 12, 0, 40, 30, 0, []]]}}, "/R")[0]
    roof_z = max(r[2] for r in gfac[bk("SM_BK_Floor")])
    assert all(r[2] + KIT["SM_Kit_FactoryMachine"][6] <= roof_z for r in gfac[path("SM_Kit_FactoryMachine", "/R")]), "machine through the roof"
    # a warehouse layer set to modular is an industrial shed now, not a stack of containers
    gw2 = bake({"W": {"kind": "buildings", "role": "warehouse", "build": "modular", "points": [lot]}}, "/R")[0]
    assert bk("SM_BK_Ind_Wall_RollerDoor_Doorway") in gw2 and not any("SM_V4_Container" in k for k in gw2)
    # a tower's interior follows its measured glass: no floor out past a setback, none in the crown, no core on top
    gt = {}
    _interior(gt, "/R", profile_parts(["SM_City_DT_CrownTower"]), "", 0, 0, 0, 0, 1.0, 1.0, 1.0)
    slabs = gt[path("SM_Kit_FloorRound", "/R")]
    assert len(slabs) == 24 and all(r[4] * 50 <= (3067 if r[2] < 3 * STOREY_CM else 1533 if r[2] < 19 * STOREY_CM else 1019)
                                    for r in slabs), "slabs inside the glass"
    assert max(r[2] for r in gt[path("SM_Kit_StairCore", "/R")]) < 23 * STOREY_CM, "no core above the top floor"
    assert profile_parts(["SM_City_CT_MidRise_P0", "SM_City_CT_MidRise_P1"])[0][7] and profile_parts(["X"]) == ["X"]
    print("kit bake ok", len(groups), "meshes")


if __name__ == "__main__":
    _demo()

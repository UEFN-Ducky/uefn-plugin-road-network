"""The City Builder pipeline: separate systems, each consuming the one before it.

CityGenerator
|- TerrainAnalyzer     Heightmap, SlopeMap, ObstacleMap
|- UrbanPlanner        Districts, RoadOrientation, Density
|- RoadGenerator       RoadGraph, Intersections, RoadProfiles
|- RoadMeshGenerator   Asphalt, Curbs, Intersections            (needs UEFN: junction meshes, road paste)
|- SidewalkGenerator   PedestrianPaths, Corners, FrontageConnections
|- BlockGenerator      BuildingParcels
|- FoundationGenerator BuildingPads, CutAndFill, Foundations
|- BuildingGenerator   Footprints, Buildings, Entrances

Running from a stage re-runs it and everything after it, never what came before: change the buildings and
the roads stay; change road widths and the terrain stays.
"""

from __future__ import annotations

import math

STAGES = [
    {"id": "terrain", "name": "TerrainAnalyzer", "parts": ["Heightmap", "SlopeMap", "ObstacleMap"],
     "consumes": [], "produces": "net.<id>.scan terrain grid, slope shares, keep zones and water",
     "code": "citygen.terrain / road_network_scan_ground (live terrain: read, never written)"},
    {"id": "planner", "name": "UrbanPlanner", "parts": ["Districts", "RoadOrientation", "Density"],
     "consumes": ["terrain"], "produces": "per city: district rings, grid orientation, lot density",
     "code": "citygen.place_cities, districts, orientation"},
    {"id": "roads", "name": "RoadGenerator", "parts": ["RoadGraph", "Intersections", "RoadProfiles"],
     "consumes": ["terrain", "planner"],
     "produces": "'<City> Freeways' (planned first) + '<City> Roads' + 'Highways' spline actors (graded)",
     "code": "freeways.plan (HighwayNetworkPlanner, interchanges, ramps, frontage roads, validate), "
             "citygen.city_graph (planned grid, hierarchy), generate (highways, interchanges), GRADE + drape"},
    {"id": "road_meshes", "name": "RoadMeshGenerator", "parts": ["Asphalt", "Curbs", "Intersections"],
     "consumes": ["roads", "sidewalks"], "produces": "junction meshes SM_JK_*, roads and paths pasted into UEFN",
     "editor": True,
     "code": "build_pieces (8 per call) + reimport, via Push roads"},
    {"id": "sidewalks", "name": "SidewalkGenerator", "parts": ["PedestrianPaths", "Corners", "FrontageConnections"],
     "consumes": ["roads", "foundations", "buildings"], "produces": "'<City> Paths' spline actor: sidewalks on "
     "roads without them, every walkway and driveway (live splines, graded)",
     "code": "citygen.sidewalks, paths_graph, paths_actor"},
    {"id": "blocks", "name": "BlockGenerator", "parts": ["BuildingParcels"],
     "consumes": ["roads"], "produces": "each city's lots (front, facing, width, depth)",
     "code": "parcels.blocks / inset / split / lots, citygen.city_lots"},
    {"id": "demand", "name": "CityDemandSystem",
     "parts": ["Population", "Demand", "FacilityLocation", "Developments"],
     "consumes": ["terrain", "roads", "blocks"],
     "produces": "per city: population, households, jobs, age groups; schools, emergency services, retail, parks, "
                 "utilities and transit placed by demand, road travel time and coverage, as complete developments "
                 "(building + parking + aprons / fields / playgrounds / yards) on whole lots; coverage report",
     "code": "urban.plan (Network, population, CATALOG, ARCHETYPES, develop), citygen.city_layout"},
    {"id": "foundations", "name": "FoundationGenerator", "parts": ["BuildingPads", "CutAndFill", "Foundations"],
     "consumes": ["terrain", "blocks", "demand"], "produces": "per lot: pad height, foundation depth (measured on the cut ground), paths",
     "code": "pcg_layers._lot_ground"},
    {"id": "buildings", "name": "BuildingGenerator", "parts": ["Footprints", "Buildings", "Entrances"],
     "consumes": ["blocks", "foundations"], "produces": "every PCG layer's placements, baked to kit meshes, "
     "pushed as 400 m PCG tiles", "code": "pcg_layers.preview, kit_bake.bake, pcg_build (Push city)"},
]
ORDER = [s["id"] for s in STAGES]
RUN_ORDER = ["terrain", "planner", "roads", "blocks", "demand", "foundations", "buildings", "sidewalks", "road_meshes"]


def after(stage: str) -> list:
    """Stages to run when `stage` changes: it and everything downstream, in run order."""
    if stage not in ORDER:
        return []
    need, grew = {stage}, True
    while grew:
        grew = False
        for s in STAGES:
            if s["id"] not in need and need & set(s["consumes"]):
                need.add(s["id"])
                grew = True
    return [s for s in RUN_ORDER if s in need]


def slope_map(ter: dict) -> dict:
    """SlopeMap: share of the terrain grid by grade (gentle < 8 %, moderate < 15 %, steep)."""
    h = ter.get("heights") or []
    if len(h) < 2:
        return {}
    lo, hi = ter["min"], ter["max"]
    cell = (hi[0] - lo[0]) / len(h[0])
    counts = {"gentle": 0, "moderate": 0, "steep": 0}
    for j in range(len(h) - 1):
        for i in range(len(h[0]) - 1):
            a, b, c = h[j][i], h[j][i + 1], h[j + 1][i]
            if None in (a, b, c):
                continue
            g = math.hypot(b - a, c - a) / cell
            counts["gentle" if g < 0.08 else "moderate" if g < 0.15 else "steep"] += 1
    total = sum(counts.values()) or 1
    return {k: round(v / total, 3) for k, v in counts.items()}


def _demo() -> None:
    assert after("buildings") == ["buildings", "sidewalks", "road_meshes"], after("buildings")
    assert after("terrain")[0] == "terrain" and len(after("terrain")) == len(STAGES)
    assert "terrain" not in after("roads") and "roads" in after("roads")
    ter = {"min": [0, 0], "max": [1000, 1000], "heights": [[0, 30, 60], [0, 30, 60], [0, 30, 60]]}
    s = slope_map(ter)
    assert s["moderate"] == 1.0, s  # 30 cm over a 333 cm cell = 9 % -> moderate
    print("pipeline ok", " -> ".join(s["name"] for s in STAGES))


if __name__ == "__main__":
    _demo()

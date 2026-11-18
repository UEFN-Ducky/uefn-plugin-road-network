import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import citygen as cg  # noqa: E402

ROAD = [(0, -50000, 0, 50000, 875.0)]  # a street along x = 0


def shed(x, y=0):  # 40 x 20 m, its front toward +x (away from the street)
    return [x, y, 0.0, 4000, 2000, x + 1000, y, "warehouse", False]


def test_lot_far_from_a_street_gets_a_drive_from_its_edge():
    drives = cg.lot_drives([shed(10000)], [], [], ROAD)
    assert drives == [[[9000, 0], [300, 0]]], drives  # (3 m short of the street's middle)


def test_lot_with_a_street_at_its_door_or_in_a_block_gets_none():
    assert cg.lot_drives([shed(-1500)], [], [], ROAD) == []
    block = [0, 0, "parcels", "normal", [[5000, -5000], [15000, -5000], [15000, 5000], [5000, 5000]]]
    assert cg.lot_drives([shed(10000)], [], [block], ROAD) == []


def test_no_drive_over_water_or_too_far():
    assert cg.lot_drives([shed(10000)], [], [], ROAD, wet=lambda x, y: 3000 < x < 6000) == []
    assert cg.lot_drives([shed(40000)], [], [], ROAD) == []


def test_lane_on_its_own_is_joined_from_its_nearer_end():
    lane = [[12000, 0], [12000, 3000]]
    joined = [[1000, 9000], [9000, 9000]]  # ... one with an end on the street is left alone
    assert cg.lot_drives([], [lane, joined], [], ROAD) == [[[12000, 0], [300, 0]]]


def test_lanes_that_touch_get_one_access_road_and_reach_further():
    a, b = [[12000, 0], [12000, 3000]], [[12000, 3000], [15000, 3000]]
    assert cg.lot_drives([], [a, b], [], ROAD) == [[[12000, 0], [300, 0]]], "one for the two"
    far = [[40000, 0], [41000, 0]]  # 400 m out: past a lot's drive, inside an access road's reach
    assert cg.lot_drives([], [far], [], ROAD) == [[[40000, 0], [300, 0]]]


def test_drives_to_one_street_end_keep_their_own_ends():
    lots = [shed(10000, 60000), [-10000, 60000, 0.0, 4000, 2000, -11000, 60000, "warehouse", False]]
    ends = {tuple(d[1]) for d in cg.lot_drives(lots, [], [], ROAD)}
    assert len(ends) == 2, ends


def test_airport_building_gets_a_drive_in_the_landside_roads():
    nodes, edges = {"a": [0, 0], "b": [0, 20000], "up": [0, -20000]}, [["a", "b", "4L"], ["up", "a", "ER"]]
    lots = [shed(9000, 20000), shed(-1500, 10000)]
    cg.airport_drives(lots, nodes, edges)
    assert edges[2:] == [[("drive", 0), "b", "PR"]] and nodes[("drive", 0)] == [8000.0, 20000.0], (edges, nodes)


def _actor(pts, segs):
    return {"points": [cg._point(i, x, y, z, 0.0, w) for i, x, y, z, w in pts],
            "segments": [cg._segment(i, a, b, k, "/Game/X") for i, a, b, k in segs]}


def test_private_street_carries_on_from_a_street_that_just_ends():
    roads = _actor([(0, 0, 0, 0, 875.0), (1, 10000, 0, 0, 875.0)], [(2, 0, 1, "2S")])
    access = _actor([(10, 10500, 0, -3000, 400.0), (11, 30000, 0, -3000, 400.0)], [(20, 10, 11, "PR")])
    out, _rest, joins = cg.join_private(roads, access, "/Game/X")
    far = next(p for p in out["points"] if p["x"] == 30000)
    assert joins == 1 and any({s["a"], s["b"]} == {1, far["i"]} and "PrivateStreet" in s["mesh"] for s in out["segments"])
    assert far["z"] == -2000.0, "climbs at most 10 % from the street's end"
    assert not any(p["x"] == 10500 for p in out["points"]), "its end inside the street's end is gone"


def test_drives_to_one_junction_all_join_it():
    g = lambda x, y: 0.0  # noqa: E731
    nodes = {"A": [0, 0], "B": [20000, 12000], "C": [0, 20000], "D": [-20000, 12000]}
    roads = cg.build_actor(nodes, [["A", n, "6S"] for n in "BCD"], g, "/Game/X")
    lots = [[500, -12000, 0.0, 4000, 2000, 500, -13000, "shop", False],
            [6000, -9000, 0.0, 4000, 2000, 6000, -10000, "shop", False],
            [-8000, -11000, 0.0, 4000, 2000, -8000, -12000, "shop", False]]
    drives = cg.lot_drives(lots, [], [], cg.ground_roads([roads]))
    acc = cg.access_actor({"private": {"streets": drives}}, [], g, "/Game/X")
    out, _rest, joins = cg.join_private(roads, acc, "/Game/X")
    assert joins == 3, "a drive never takes another's end for its street"
    rep = cg.network_report({"A": out})
    assert rep["pieces"] == 1 and not rep["stacked"], rep


def test_link_meets_its_host_at_the_host_height_and_once():
    roads = _actor([(0, 0, 0, 0, 400.0), (1, 10000, 0, 0, 400.0)], [(2, 0, 1, "PR")])
    path = _actor([(10, 5000, 3000, 800, 400.0), (11, 5000, 300, 800, 400.0)], [(20, 10, 11, "PR")])
    out, links = cg.join_links(roads, [path], "/Game/X")
    z = {(p["x"], p["y"]): p["z"] for p in out["points"]}
    assert links == 1 and z[(5000, 300)] == 0 and z[(5000, 3000)] == 270.0, z
    a = _actor([(10, 0, 50000, 0, 400.0), (11, 5000, 50000, 0, 400.0)], [(20, 10, 11, "PR")])
    b = _actor([(10, 5000, 50000, 0, 400.0), (11, 10000, 50000, 0, 400.0)], [(20, 10, 11, "PR")])
    _out, links = cg.join_links({"points": [], "segments": []}, [a, b], "/Game/X")
    assert links == 1, "two ends that meet: one link, not one each way"


def test_driveway_short_of_the_street_runs_on_to_it():
    roads = _actor([(0, 0, 0, 0, 875.0), (1, 20000, 0, 0, 875.0)], [(2, 0, 1, "2S")])
    drive = _actor([(10, 10000, 4000, 0, 175.0), (11, 10000, 3000, 0, 175.0)], [(20, 10, 11, "DW")])
    out, links = cg.join_links(roads, [drive], "/Game/X")
    P = {p["i"]: p for p in out["points"]}
    dw = [s for s in out["segments"] if "Driveway" in s["mesh"]]
    assert links == 1 and len(dw) == 2, "30 m out: its own mesh on to the street, then tied"
    assert any(round(P[s["b"]]["y"]) == 875 for s in dw), "the new end is at the street's edge"
    far = _actor([(10, 10000, 9000, 0, 175.0), (11, 10000, 8000, 0, 175.0)], [(20, 10, 11, "DW")])
    out, links = cg.join_links(roads, [far], "/Game/X")
    assert links == 0 and len(out["points"]) == 2, "past FAR_LINK_CM: reaches nothing, so it is dropped"


def test_ramp_climbs_to_the_street_at_most_ten_percent():
    rp = {k: {"i": k, "x": k * 1000.0, "y": 0.0, "z": -1200.0} for k in range(1, 20)}
    rp[0] = {"i": 0, "x": 0.0, "y": 0.0, "z": 0.0}  # the mouth, at the street's height
    rs = [{"a": k, "b": k + 1} for k in range(19)]
    cg._ramp(rp, rs, 1, 0, 0.0)
    assert [round(rp[k]["z"]) for k in range(1, 5)] == [-100, -200, -300, -400]
    assert round(rp[11]["z"]) == -1100 and round(rp[12]["z"]) == -1200, "back on its own ground after 12 m of climb"

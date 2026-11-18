import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import citygen as cg  # noqa: E402


def home(y):  # lake to the east (+x): the lot faces it, front edge 12 m east of its middle
    return [100000, y, 0.0, 1700, 2400, 101200, y, "house", False]


def test_row_gets_a_lane_joined_to_the_street():
    lots = [home(0), home(2000), home(4000)]
    street = [(90000, -20000, 90000, 20000, 875.0)]  # 100 m inland, running along the shore
    lanes = cg.home_lanes(lots, street)
    assert all(abs(r[2]) == 180.0 and r[5] == 98800 for r in lots), "each home faces the land now"
    assert len(lanes) == 1 and len(lanes[0]) == 4
    assert lanes[0][0] == [90000, 0], "joined to the street at the row's nearer end"
    assert all(p[0] == 98500 for p in lanes[0][1:]), "the lane runs 3 m beyond the fronts"


def test_home_on_a_street_gets_no_lane():
    lots = [home(0)]
    assert cg.home_lanes(lots, [(98500, -9000, 98500, 9000, 875.0)]) == []
    assert lots[0][5] == 98800


def test_organic_look_wanders_over_a_calm_world():
    world = {"wobble": 0.04, "suburbs": "blocks"}
    o = cg.city_options({"style": "organic", "features": dict(cg.ORGANIC_LOOK)}, world)
    assert o["wobble"] == 0.3 and o["suburbs"] == "winding"
    assert cg.city_options({"style": "mixed"}, world)["wobble"] == 0.04


def test_district_cut_off_on_land_is_joined():
    n = cg.LOOSE_MIN
    nodes = {("w", k): [0.0, k * 10000.0] for k in range(n)}
    nodes.update({("e", k): [5000.0, k * 10000.0 + 300.0] for k in range(n)})
    edges = [[(s, k), (s, k + 1), "2S"] for s in "we" for k in range(n - 1)]
    assert len(cg.join_parts(nodes, edges, None, "6L")[1]) == len(edges), "no bridge to build: left alone as before"
    got = cg.join_parts(nodes, edges, None, "6L", land="2S")[1]
    new = got[len(edges):]
    assert len(new) == 1 and new[0][2] == "2S" and {new[0][0][0], new[0][1][0]} == {"w", "e"}, new


def test_other_lots_untouched():
    lot = [0, 0, 0.0, 1600, 3500, 1750, 0, "house", False]
    assert cg.home_lanes([lot], []) == [] and lot[2] == 0.0
    assert math.isclose(lot[5], 1750)

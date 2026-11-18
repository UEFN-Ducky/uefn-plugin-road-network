import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import citygen as cg  # noqa: E402


def test_same_pair_twice_keeps_one():
    nodes = {"a": [0, 0], "b": [10000, 0], "c": [0, 10000]}
    edges = [["a", "b", "2L"], ["b", "a", "2L"], ["a", "c", "2L"]]
    assert cg._no_fans(nodes, edges) == [["a", "b", "2L"], ["a", "c", "2L"]]


def test_fan_keeps_wider_grid_stays():
    nodes = {"j": [0, 0], "e": [-10000, 0], "f": [-10000, 1400], "n": [0, 10000], "s": [0, -10000], "w": [10000, 0]}
    edges = [["j", "f", "2L"], ["j", "e", "4L"], ["j", "n", "2L"], ["j", "s", "2L"], ["j", "w", "2L"]]
    kept = cg._no_fans(nodes, edges)
    assert ["j", "f", "2L"] not in kept and len(kept) == 4  # 8 degrees off the wider street: gone; the cross stays


def test_airport_road_meets_city_height():
    road = {"points": [{"x": 0, "y": 0, "z": 9179}, {"x": 30494, "y": 193110, "z": 1773}]}
    city = {"points": [{"x": 30494, "y": 193110, "z": -634}]}
    assert cg.meet_city(road, city, [[30494, 193110]]) == 1 and road["points"][1]["z"] == -634
    assert road["points"][0]["z"] == 9179 and cg.meet_city(road, None, [[30494, 193110]]) == 0


def test_bridge_wins():
    nodes = {"a": [0, 0], "b": [10000, 0]}
    bridge = ["a", "b", "2L", [[1, 2, 3, "BR"]]]
    assert cg._no_fans(nodes, [["a", "b", "4L"], bridge]) == [bridge]

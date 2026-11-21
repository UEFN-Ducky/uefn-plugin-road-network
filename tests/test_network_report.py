import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import citygen as cg  # noqa: E402
from backend import splines_text as st  # noqa: E402

FOLDER = "/Game/X/WorldGen/Roads"


def street(nodes, edges, z=0.0):
    return cg.build_actor(nodes, edges, lambda x, y: z, FOLDER)


def test_one_piece_is_ok_and_a_cut_off_street_is_not():
    a = street({0: [0, 0], 1: [20000, 0]}, [[0, 1, "2S"]])
    assert cg.network_report({"A": a})["ok"]
    far = street({0: [0, 50000], 1: [20000, 50000]}, [[0, 1, "2S"]])
    rep = cg.network_report({"A": a, "B": far})
    assert not rep["ok"] and rep["pieces"] == 2 and rep["loose"][0][2] == "B", rep


def test_two_actors_meeting_in_one_spot_are_one_piece_but_a_height_step_is_caught():
    a = street({0: [0, 0], 1: [20000, 0]}, [[0, 1, "2S"]])
    b = street({0: [20000, 0], 1: [40000, 0]}, [[0, 1, "2S"]])
    assert cg.network_report({"A": a, "B": b})["ok"]
    high = street({0: [20000, 0], 1: [40000, 0]}, [[0, 1, "2S"]], z=800.0)
    rep = cg.network_report({"A": a, "B": high})
    assert rep["pieces"] == 1 and rep["steps"] == [[20000, 0, 8.0]], rep


def test_a_road_laid_twice_is_caught_but_a_bridge_over_it_is_not():
    a = street({0: [0, 0], 1: [20000, 0]}, [[0, 1, "2S"]])
    twice = street({0: [-5000, 0], 1: [0, 0], 2: [25000, 0]}, [[0, 1, "2S"], [1, 2, "2S"]])
    assert cg.network_report({"A": a, "B": twice})["stacked"], "one street drawn on top of another"
    over = street({0: [-5000, 0], 1: [0, 0], 2: [25000, 0]}, [[0, 1, "2S"], [1, 2, "2S"]], z=900.0)
    beside = street({0: [-5000, 3000], 1: [25000, 3000]}, [[0, 1, "2S"]])
    assert not cg.network_report({"A": a, "B": over})["stacked"]
    assert not cg.network_report({"A": a, "C": beside})["stacked"]


def test_runways_and_docks_may_stand_apart():
    a = street({0: [0, 0], 1: [20000, 0]}, [[0, 1, "2S"]])
    rw = street({0: [0, 90000], 1: [40000, 90000]}, [[0, 1, "RW"]])
    assert cg.network_report({"A": a, "Air": rw})["ok"]


def test_sheds_side_by_side_get_drives_that_branch_not_stack():
    road = [(0, -50000, 0, 50000, 875.0)]
    sheds = [[x, 0, 0.0, 4000, 2000, x + 1000, 0, "warehouse", False] for x in (9000, 14000, 19000)]
    drives = cg.lot_drives(sheds, [], [], road)
    assert len(drives) == 3, drives
    acc = cg.access_actor({"private": {"streets": drives}}, [], lambda x, y: 0.0, FOLDER)
    assert not cg.network_report({"A": acc})["stacked"], drives
    assert all(d[1][0] > 1000 for d in drives[1:]), "the further sheds drive to the first's drive, not all the way"


def test_a_highway_on_a_gate_stub_stays_on_its_end_never_along_the_street():
    # the stub's point 20 m back lies nearer the highway's height: welded there, the highway lay on the street
    town = cg.build_actor({0: [0, 0], 1: [2000, 0], 2: [4000, 0]}, [[0, 1, "4L"], [1, 2, "4L"]],
                          lambda x, y: -x / 10, FOLDER)
    hw = street({0: [4000, 0], 1: [12000, 1000]}, [[0, 1, "4L"]])
    out = {"Town Roads": town, "Highways": hw}
    cg.weld_ends(out)
    tip = next(p for p in town["points"] if round(p["x"]) == 4000)
    end = next(p for p in hw["points"] if math.hypot(p["x"] - 4000, p["y"]) < 1500)
    assert (round(end["x"]), round(end["y"]), round(end["z"])) == (4000, 0, round(tip["z"])), (end, tip)
    assert not cg.network_report(out)["stacked"]


@pytest.mark.parametrize("seed", [3, 7, 11])
def test_generated_worlds_are_one_network_with_no_steps_and_no_road_twice(seed):
    cities = cg.place_cities(seed, 3, ((-200000, -200000), (200000, 200000)))
    ter = cg.terrain(seed, 400000, 6000, cities, grid=64)
    actors = cg.generate(cities, lambda x, y: st.sample_grid(ter, x, y) or 0.0, FOLDER, seed=seed)
    rep = cg.network_report(actors)
    assert rep["ok"], rep

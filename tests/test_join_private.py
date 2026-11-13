"""Private streets join their street with a real junction, in one actor: py -3 tests/test_join_private.py"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from backend import citygen as cg  # noqa: E402
from backend import splines_text as st  # noqa: E402

FLAT = lambda x, y: 0.0  # noqa: E731


def test_join_private():
    # a 1 km four-lane street along X, sampled every 20 m like a generated street
    roads = cg.build_actor({0: [0, 0], 1: [100000, 0]}, [[0, 1, "4L", [[x, 0] for x in range(2000, 100000, 2000)]]],
                           FLAT, "/Game/X/Roads")
    before = (len(roads["points"]), len(roads["segments"]))
    # a private street running north from 6 m past the street's edge, and a driveway on its own
    half = cg.HALF["4L"]
    nodes = {("pr", 0, 0): [50000, half + 600], ("pr", 0, 1): [50000, 30000],
             "d0": [80000, half + 300], "d1": [80000, half + 2000]}
    edges = [[("pr", 0, 0), ("pr", 0, 1), "PR", [[50000, y] for y in range(4000, 30000, 2000)]], ["d0", "d1", "DW"]]
    access = cg.build_actor(nodes, edges, FLAT, "/Game/X/Roads")
    joined, rest, n = cg.join_private(roads, access, "/Game/X/Roads")
    assert n == 1, n
    pr = cg.ROAD_MESH["PR"]
    assert any(s["mesh"] == pr for s in joined["segments"]), "the private street is in the roads actor now"
    assert not any(s["mesh"] == pr for s in rest["segments"]) and rest["segments"], "only the driveway stays in Access"
    waiting = st.pending_nodes(joined["points"], joined["segments"])
    tee = [w for w in waiting if len(w[1]) == 3]
    assert len(tee) == 1, waiting
    node, arms, angles = tee[0]
    centre = next(p for p in joined["points"] if p["i"] == node)
    assert abs(centre["x"] - 50000) < 1 and abs(centre["y"]) < 1, (centre["x"], centre["y"])
    assert sorted(round(a) % 360 for a in angles) == [0, 90, 180], angles
    ids = {p["i"] for p in joined["points"]}
    assert all(s["a"] in ids and s["b"] in ids for s in joined["segments"]), "no segment to a missing point"
    # every arm mouth carries on: the street both ways and the private street north
    for m in arms:
        assert any(m in (s["a"], s["b"]) and s["mesh"] for s in joined["segments"]), m
    # again: the same single junction, never a second copy of the street
    again, _rest, n2 = cg.join_private(joined, access, "/Game/X/Roads")
    assert n2 == 1 and len(again["points"]) == len(joined["points"]), (n2, len(again["points"]), len(joined["points"]))
    back = cg.unjoin_private(joined)
    assert (len(back["points"]), len(back["segments"])) == before, ((len(back["points"]), len(back["segments"])), before)
    # a private street ending right at an existing crossing joins nothing rather than cutting into that junction
    cross = cg.build_actor({0: [0, 0], 1: [100000, 0], 2: [50000, -30000], 3: [50000, 0]},
                           [[0, 3, "4L"], [3, 1, "4L"], [2, 3, "4L"]], FLAT, "/Game/X/Roads")
    _j, _r, n3 = cg.join_private(cross, access, "/Game/X/Roads")
    assert n3 == 0, n3
    # walkways / driveways / sidewalks: one network, every loose end tied to the road it meets by a link
    paths = cg.build_actor({"w0": [20000, half + 200], "w1": [20000, half + 1500]}, [["w0", "w1", "WK"]], FLAT, "/Game/X/Roads")
    net, links = cg.join_links(joined, [rest, paths], "/Game/X/Roads")
    assert links >= 2, links  # the driveway and the walkway, each at its street end
    seg_at: dict = {}
    for s in net["segments"]:
        for i in (s["a"], s["b"]):
            seg_at.setdefault(i, []).append(s)
    loose = [p for p in net["points"] if len(seg_at.get(p["i"], [])) == 1 and abs(p["y"]) < half + 1000 and p.get("join")
             and not any(abs(p["x"] - x) < 1 for x in (0, 100000))]
    assert not loose, [(p["x"], p["y"]) for p in loose]
    for s in net["segments"]:
        if s.get("join") and not s["mesh"] and s["a"] not in {w[0] for w in st.pending_nodes(net["points"], net["segments"])}:
            assert "bRaiseTerrain=False" in s["lines"], "a link shapes nothing"
    back2 = cg.unjoin_private(net)
    assert (len(back2["points"]), len(back2["segments"])) == before, "links come out again too"
    # the driveway's own mesh reaches the street's edge: no gap of grass for the mesh-less link to bridge
    dw = cg.ROAD_MESH["DW"]
    ends = [net_p for s in net["segments"] if s["mesh"] == dw for net_p in net["points"] if net_p["i"] in (s["a"], s["b"])]
    assert min(abs(p["y"]) for p in ends) <= half + 31, [round(p["y"]) for p in ends]
    # ten driveways a side along one street: the street gets a few shared points, never one per driveway
    many = cg.build_actor({**{f"n{k}": [10000 + k * 1600, half + 400] for k in range(10)},
                           **{f"f{k}": [10000 + k * 1600, half + 2000] for k in range(10)},
                           **{f"s{k}": [10000 + k * 1600, -half - 400] for k in range(10)},
                           **{f"g{k}": [10000 + k * 1600, -half - 2000] for k in range(10)}},
                          [[f"n{k}", f"f{k}", "DW"] for k in range(10)] + [[f"s{k}", f"g{k}", "DW"] for k in range(10)],
                          FLAT, "/Game/X/Roads")
    street = cg.build_actor({0: [0, 0], 1: [100000, 0]}, [[0, 1, "4L", [[x, 0] for x in range(5000, 100000, 5000)]]],
                            FLAT, "/Game/X/Roads")
    net2, links2 = cg.join_links(street, [many], "/Game/X/Roads")
    assert links2 == 20, links2
    on_street = [p for p in net2["points"] if abs(p["y"]) < 1]
    assert len(on_street) <= len(street["points"]) + 2, (len(on_street), len(street["points"]))
    print("join_private ok:", len(joined["points"]), "points,", len(waiting), "pieces waiting")


if __name__ == "__main__":
    test_join_private()

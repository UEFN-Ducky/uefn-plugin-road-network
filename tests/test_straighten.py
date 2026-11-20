"""Long roads go to UEFN as straight segments: py -3 tests/test_straighten.py"""
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))
import splines_text as st  # noqa: E402

ROAD = ["SplineMeshes(0)=(Mesh=\"road\")", "bRaiseTerrain=True", "bLowerTerrain=True"]


def road(xy_z: list, junction_at: int | None = None) -> tuple:
    pts = [{"i": i, "x": x, "y": y, "z": z, "pitch": 0.0, "yaw": 0.0, "roll": 0.0, "raise": True,
            "lines": ["Mesh=\"junction\""] if i == junction_at else []} for i, (x, y, z) in enumerate(xy_z)]
    segs = [{"i": 1000 + i, "a": i, "b": i + 1, "ta": 1.0, "tb": -1.0, "lines": list(ROAD)} for i in range(len(pts) - 1)]
    return pts, segs


def test_straighten():
    rnd = random.Random(3)
    # a 2 km straight street sampled every 20 m, each point draped on its own little bump: two points, one segment
    pts, segs = road([(k * 2000.0, rnd.uniform(-40, 40), 500 + rnd.uniform(-80, 80)) for k in range(101)])
    p2, s2 = st.straighten(pts, segs)
    assert len(p2) == 2 and len(s2) == 1, (len(p2), len(s2))
    assert {s2[0]["a"], s2[0]["b"]} == {0, 100} and abs(s2[0]["ta"]) > 190000
    # an L: straight east 1 km, then straight north 1 km -> three points
    pts, segs = road([(k * 2000.0, 0.0, 0.0) for k in range(51)] + [(100000.0, k * 2000.0, 0.0) for k in range(1, 51)])
    p2, s2 = st.straighten(pts, segs)
    assert sorted(p["i"] for p in p2) == [0, 50, 100], sorted(p["i"] for p in p2)
    # a street over a 20 m hill keeps its crest: it goes up and over, never straight through in a trench
    pts, segs = road([(k * 2000.0, 0.0, 2000.0 - abs(k - 25) * 80.0) for k in range(51)])
    assert sorted(p["i"] for p in st.straighten(pts, segs)[0]) == [0, 25, 50]
    # ... but a deck (never shaping the ground) keeps the rise and fall it was designed with
    for p in pts:
        p["raise"] = False
    assert 25 in {p["i"] for p in st.straighten(pts, segs)[0]}
    # a junction piece in the middle is never dropped, and nothing is changed in the caller's lists
    pts, segs = road([(k * 2000.0, 0.0, 0.0) for k in range(21)], junction_at=10)
    p2, s2 = st.straighten(pts, segs)
    assert sorted(p["i"] for p in p2) == [0, 10, 20] and len(pts) == 21 and len(segs) == 20
    # a bridge (another segment kind) starting mid-road keeps the point where it starts
    pts, segs = road([(k * 2000.0, 0.0, 0.0) for k in range(21)])
    for s in segs[10:]:
        s["lines"] = ["SplineMeshes(0)=(Mesh=\"deck\")", "bRaiseTerrain=False", "bLowerTerrain=False"]
    assert 10 in {p["i"] for p in st.straighten(pts, segs)[0]}
    print("straighten ok")


if __name__ == "__main__":
    test_straighten()

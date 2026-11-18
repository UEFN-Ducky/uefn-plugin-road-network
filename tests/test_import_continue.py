"""A road push repairs broken data by itself (reimport runs st.repair): py -3 tests/test_import_continue.py"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))
import splines_text as st  # noqa: E402


def point(i, x, w=700.0, mesh=""):
    return {"i": i, "x": x, "y": 0.0, "z": 0.0, "w": w, "mesh": mesh, "pitch": 0.0, "yaw": 0.0, "roll": 0.0,
            "raise": True, "lines": [f"Width={w}"] + ([f"Mesh=\"{mesh}\""] if mesh else [])}


def seg(i, a, b, mesh="SM_Road"):
    return {"i": i, "a": a, "b": b, "ta": 1000.0, "tb": -1000.0, "mesh": mesh, "lines": []}


def test_repair_clears_what_problems_finds():
    # a giant 200 m junction node and a 150 m join link: both stamp giant shapes into the terrain
    pts = [point(0, 0.0), point(1, 2000.0, w=20000.0, mesh="SM_Junc_X"), point(2, 17000.0)]
    segs = [seg(10, 0, 1), seg(11, 1, 2, mesh="")]
    found = st.problems(pts, segs)
    assert found["giant_nodes"] and found["long_links"], found
    st.repair(pts, segs)
    left = st.problems(pts, segs)
    assert not left["giant_nodes"] and not left["long_links"], left
    assert [s["i"] for s in segs] == [10] and pts[1]["mesh"] == ""


if __name__ == "__main__":
    test_repair_clears_what_problems_finds()
    print("ok")

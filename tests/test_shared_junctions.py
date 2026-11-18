import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import splines_text as st  # noqa: E402


def test_turned_junctions_share_a_shape() -> None:
    cross = st.junction_shape([0, 90, 180, 270], [500] * 4, [200] * 4)
    turned = st.junction_shape([37.3, 127.6, 217.1, 307.4], [500] * 4, [200] * 4)
    assert cross[1] == turned[1] == [0, 90, 180, 270]
    # a T keeps its widths with their arms whichever arm it starts from
    t1 = st.junction_shape([0, 90, 180], [800, 400, 800], [0, 0, 0])
    t2 = st.junction_shape([200, 290, 20], [800, 400, 800], [0, 0, 0])
    assert t1[1] == t2[1] and [[800, 400, 800][k] for k in t1[0]] == [[800, 400, 800][k] for k in t2[0]]


def test_a_shared_piece_puts_each_mouth_on_its_arm() -> None:
    angles = [37.0, 127.0, 217.0]
    order, rel, rot = st.junction_shape(angles, None, None)
    pts = [{"i": 0, "x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "pitch": 0.0, "w": 500.0, "mesh": "", "lines": []}]
    for k, a in enumerate(angles, 1):
        pts.append({"i": k, "x": 2000 * math.cos(math.radians(a)), "y": 2000 * math.sin(math.radians(a)), "z": 0.0,
                    "yaw": 0.0, "pitch": 0.0, "w": 500.0, "mesh": "", "lines": []})
    segs = [{"a": 0, "b": k, "mesh": "", "lines": [], "ta": 0.0, "tb": 0.0} for k in (1, 2, 3)]
    arms = [order[k] + 1 for k in range(3)]
    st.place_generated(pts, segs, 0, arms, [900.0] * 3, "/G/K/SM_JK_Sabc", yaw=rot, rel=rel)
    assert pts[0]["mesh"] == "SM_JK_Sabc" and abs(((pts[0]["yaw"] - 37.0) + 180) % 360 - 180) < 1e-6
    for k, a in enumerate(angles, 1):  # every mouth 9 m out along its own arm
        assert math.hypot(pts[k]["x"] - 900 * math.cos(math.radians(a)), pts[k]["y"] - 900 * math.sin(math.radians(a))) < 1


def test_a_turned_turnaround_tilts_in_its_own_axes() -> None:
    pts = [{"i": 0, "x": 0.0, "y": 0.0, "z": 0.0, "yaw": 90.0, "pitch": 0.0, "roll": 0.0, "w": 1500.0, "mesh": "x",
            "lines": []}]
    st.lay_on_ground(pts, [], 0, (0.1, 0.0, 5.0), 0.0)  # the ground rises along world +X
    # turned 90 degrees, the piece's own +X is world +Y (flat) and its +Y is world -X (falling): roll, no pitch
    assert abs(pts[0]["pitch"]) < 1e-6 and abs(pts[0]["roll"] - math.degrees(math.atan(0.1))) < 1e-6

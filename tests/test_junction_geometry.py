"""Junction geometry check, run without UEFN: py -3 -m pytest tests/test_junction_geometry.py
(or py -3 tests/test_junction_geometry.py).

The listener module imports `unreal`, so the pure geometry function and its constants are lifted out of
its source here (tests are never shipped).
"""
import ast
import math
import pathlib

src = pathlib.Path(__file__).resolve().parents[1] / "listener" / "road_io.py"
tree = ast.parse(src.read_text(encoding="utf-8"))
keep = [n for n in tree.body
        if (isinstance(n, ast.FunctionDef) and n.name == "_junction_geometry")
        or (isinstance(n, ast.Assign) and all(isinstance(t, (ast.Name, ast.Tuple)) for t in n.targets)
            and ast.unparse(n.targets[0]).lstrip("(").startswith(("_R1_", "_U_", "_OWN_", "_PAINT_", "_STOP_", "_ZEBRA", "_KERB_", "_BULB")))]
ns = {"math": math}
exec(compile(ast.Module(keep, []), str(src), "exec"), ns)
geo = ns["_junction_geometry"]
DECK = ns["_OWN_DECK_Z"]


def test_junction_geometry():
    # a + crossing of a six-lane boulevard (east-west) and a lane (north-south)
    verts, uvs, tris, mouth, (pv, pu, pt), bare = geo([0, 90, 180, 270], flat=True, halves=[1650, 525, 1650, 525])
    assert bare == ([], [], []), "roads with no sidewalk: a junction with none"
    assert len(mouth) == 4 and all(700 <= m <= 3000 for m in mouth), mouth

    def mouth_width(deg, dist):
        c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        on = [(-s * x + c * y) for x, y, _ in verts[1:] if abs(c * x + s * y - dist) < 1 and abs(_ - DECK) < 1e-6]
        return max(on) - min(on)

    assert abs(mouth_width(0, mouth[0]) - 3300) < 2, "the boulevard's mouth is boulevard wide"
    assert abs(mouth_width(90, mouth[1]) - 1050) < 2, "the lane's mouth is lane wide, not the boulevard's"
    assert all(max(i) < len(verts) for i in tris) and len(uvs) == len(verts)
    assert pt and len(pv) % 4 == 0 and len(pt) == len(pv) // 2 and all(abs(z - DECK - 1.0) < 1e-6 for _, _, z in pv), "painted bars above the deck"
    assert min(z for _, _, z in verts) == -330.0, "a 3.3 m skirt under the edges, like the road tiles"
    for src_v, group in ((verts, [t for t in tris if t[0] == 0]), (pv, pt)):  # deck fan and paint: all face up
        for a, b, c in group:  # UE: clockwise seen from above
            (x0, y0, _), (x1, y1, _), (x2, y2, _) = src_v[a], src_v[b], src_v[c]
            assert (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0) <= 1e-6, "faces up"
    # equal widths: the same corner as before (edge lines meet W / tan(gap / 2) along the arm)
    v2 = geo([0, 90, 180], half=525, flat=True)[0]
    assert any(abs(x - 525) < 1 and abs(y - 525) < 1 for x, y, _ in v2) is False, "the corner is rounded, not a vertex"
    # an elbow and an airport junction get no paint
    assert geo([0, 90], half=525, flat=True)[4][2] == [] and geo([0, 90, 180], half=900, flat=True, paint=False)[4][2] == []
    # two streets with 3.5 m sidewalks: the junction carries its four sidewalk corners and kerbs in the same mesh
    _, _, _, m4, (qv, _, _), (sv, su, st_) = geo([0, 90, 180, 270], flat=True, halves=[875] * 4, walks=[350] * 4)
    tops = [t for t in st_ if all(abs(sv[i][2] - (DECK + 15.0)) < 1e-6 for i in t)]
    assert len(tops) == 4 * 6 * 2 and len(su) == len(sv), (len(tops), "6 paving quads a corner, a kerb height up")
    for a, b, c in tops:
        (x0, y0, _), (x1, y1, _), (x2, y2, _) = sv[a], sv[b], sv[c]
        assert (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0) <= 1e-6, "paving faces up"
    # the paving starts at each mouth exactly where the road's own sidewalk runs: 5.25 m to 8.75 m from the centre line
    at_mouth = sorted({round(abs(y)) for x, y, z in sv if abs(x - m4[0]) < 1 and abs(z - (DECK + 15.0)) < 1e-6})
    assert at_mouth == [525, 875], at_mouth
    assert max(abs(y) for x, y, _ in qv if abs(x) > 600) <= 525 - 60 + 1, "crossings stay on the carriageway"
    # a dead end (one arm going west): a round turnaround, the street's sidewalk carried on round it
    bv, _, bt, bm, (bpv, _, _), (bsv, _, bst) = geo([180], flat=True, halves=[875], walks=[350])
    assert len(bm) == 1 and 700 <= bm[0] < 1300 and bpv == [], (bm, "a mouth just past the round, no paint")
    deck = [(x, y) for x, y, z in bv[1:] if abs(z - DECK) < 1e-6]
    assert abs(max(x for x, _ in deck) - 1375) < 1 and abs(max(abs(y) for _, y in deck) - 1375) < 10, "13.75 m round"
    assert abs(min(x for x, _ in deck) + bm[0]) < 1 and all(max(t) < len(bv) for t in bt), "the mouth faces the road"
    for a, b, c in [t for t in bt if t[0] == 0]:
        (x0, y0, _), (x1, y1, _), (x2, y2, _) = bv[a], bv[b], bv[c]
        assert (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0) <= 1e-6, "the turnaround's deck faces up"
    inner = max(math.hypot(x, y) for x, y, z in bsv if abs(z - (DECK + 15.0)) < 1e-6 and x > 0 and abs(y) < 300)
    assert abs(inner - 1375) < 1 and bst, "its sidewalk runs round the outside, kerb height up"
    assert min(math.hypot(x, y) for x, y, z in bsv if abs(z - (DECK + 15.0)) < 1e-6 and x > 0 and abs(y) < 300) > 1024, "3.5 m wide"
    print("junction geometry ok:", len(verts), "deck verts,", len(pv) // 4, "painted bars, mouths", [round(m) for m in mouth])


if __name__ == "__main__":
    test_junction_geometry()

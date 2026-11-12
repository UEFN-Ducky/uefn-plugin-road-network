"""Junction geometry check, run without UEFN: py -3 tests/test_junction_geometry.py

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
            and ast.unparse(n.targets[0]).lstrip("(").startswith(("_R1_", "_U_", "_OWN_", "_PAINT_", "_STOP_", "_ZEBRA")))]
ns = {"math": math}
exec(compile(ast.Module(keep, []), str(src), "exec"), ns)
geo = ns["_junction_geometry"]

# a + crossing of a six-lane boulevard (east-west) and a lane (north-south)
verts, uvs, tris, mouth, (pv, pu, pt) = geo([0, 90, 180, 270], flat=True, halves=[1650, 525, 1650, 525])
assert len(mouth) == 4 and all(700 <= m <= 3000 for m in mouth), mouth


def mouth_width(deg, dist):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    on = [(-s * x + c * y) for x, y, _ in verts[1:] if abs(c * x + s * y - dist) < 1 and abs(_ - 2.0) < 1e-6]
    return max(on) - min(on)


assert abs(mouth_width(0, mouth[0]) - 3300) < 2, "the boulevard's mouth is boulevard wide"
assert abs(mouth_width(90, mouth[1]) - 1050) < 2, "the lane's mouth is lane wide, not the boulevard's"
assert all(max(i) < len(verts) for i in tris) and len(uvs) == len(verts)
assert pt and len(pv) % 4 == 0 and len(pt) == len(pv) // 2 and all(abs(z - 3.0) < 1e-6 for _, _, z in pv), "painted bars above the deck"
for src_v, group in ((verts, [t for t in tris if t[0] == 0]), (pv, pt)):  # deck fan and paint: all face up
    for a, b, c in group:  # UE: clockwise seen from above
        (x0, y0, _), (x1, y1, _), (x2, y2, _) = src_v[a], src_v[b], src_v[c]
        assert (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0) <= 1e-6, "faces up"
# equal widths: the same corner as before (edge lines meet W / tan(gap / 2) along the arm)
v2 = geo([0, 90, 180], half=525, flat=True)[0]
assert any(abs(x - 525) < 1 and abs(y - 525) < 1 for x, y, _ in v2) is False, "the corner is rounded, not a vertex"
# an elbow and an airport junction get no paint
assert geo([0, 90], half=525, flat=True)[4][2] == [] and geo([0, 90, 180], half=900, flat=True, paint=False)[4][2] == []
print("junction geometry ok:", len(verts), "deck verts,", len(pv) // 4, "painted bars, mouths", [round(m) for m in mouth])

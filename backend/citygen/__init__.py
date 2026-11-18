"""Generate a world offline: a terrain heightmap, several cities (each with its own districts and
road style) and highways joining them, as road actors in the same point/segment form the
spline text parser makes. Nothing here talks to UEFN.

Units are UE centimetres. A city is {name, x, y, radius, style, seed}; style is
"mixed" (straight downtown grid, wobbly suburbs), "grid" or "organic". Every knob lives in
OPTIONS (metres and shares, as the panel shows them); options() fills in the defaults.
"""

# ponytail: the files below are one module cut by system, run in order into this package's own namespace, so
# every name (privates too) stays `citygen.X` and a test that patches `cg.X` still reaches every caller. A part
# may use any name an earlier part defined; relative imports in them climb one more level (`from .. import`).
import os as _cg_os

# (each file starts with `_` so no part's name hides a function of the same name: terrain, generate, districts)
_CG_PARTS = ("kit", "terrain", "districts", "street_graph", "bridges", "paths", "layout", "joins", "generate",
             "selftest")
for _cg_part in _CG_PARTS:
    _cg_path = _cg_os.path.join(_cg_os.path.dirname(__file__), "_" + _cg_part + ".py")
    with open(_cg_path, encoding="utf-8") as _cg_file:
        exec(compile(_cg_file.read(), _cg_path, "exec", dont_inherit=True), globals())
del _cg_os, _cg_part, _cg_path, _cg_file

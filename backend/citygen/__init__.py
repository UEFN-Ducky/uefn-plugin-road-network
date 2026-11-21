"""Generate a world offline: a terrain heightmap, several cities (each with its own districts and
road style) and highways joining them, as road actors in the same point/segment form the
spline text parser makes. Nothing here talks to UEFN.

Units are UE centimetres. A city is {name, x, y, radius, style, seed}; style is
"mixed" (straight downtown grid, wobbly suburbs), "grid" or "organic". Every knob lives in
OPTIONS (metres and shares, as the panel shows them); options() fills in the defaults.
"""

from __future__ import annotations

# ponytail: the files below are one module cut by system, run in order into this package's own namespace, so
# every name (privates too) stays `citygen.X` and a test that patches `cg.X` still reaches every caller. A part
# may use any name an earlier part defined; relative imports in them climb one more level (`from .. import`).
#
# The Store build has no .py files to read: Ducky's build engine sees the mark on the list below, compiles the
# listed files into this module (their source goes where the list is) and leaves the list empty, so the loop
# under it runs nothing there. For that, every part starts with this file's own `from __future__` line, and a
# new part is one more name in the list (tests/test_citygen_parts.py holds both).
# (each file starts with `_` so no part's name hides a function of the same name: terrain, generate, districts)
_CG_PARTS = ("_kit.py", "_terrain.py", "_districts.py", "_street_graph.py", "_bridges.py", "_paths.py",
             "_layout.py", "_joins.py", "_generate.py", "_selftest.py")  # ducky-build: one module
if _CG_PARTS:
    import os as _cg_os

    for _cg_part in _CG_PARTS:
        _cg_path = _cg_os.path.join(_cg_os.path.dirname(__file__), _cg_part)
        with open(_cg_path, encoding="utf-8") as _cg_file:
            exec(compile(_cg_file.read(), _cg_path, "exec", dont_inherit=True), globals())
    del _cg_os, _cg_part, _cg_path, _cg_file

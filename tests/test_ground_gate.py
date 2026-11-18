"""A PCG push is refused while buildings are buried or floating: py -3 tests/test_ground_gate.py"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from backend import citygen as cg  # noqa: E402


def test_gate():
    assert cg.misfit_gate(None) == ""  # no ground to check against yet: never blocks
    assert cg.misfit_gate({"buried": 0, "floating": 0, "misfits": []}) == ""
    why = cg.misfit_gate({"buried": 2, "floating": 1, "misfits": [
        {"label": "Houses", "x": 1200, "y": -400}, {"label": "Shops", "x": 50, "y": 9}]})
    assert why.startswith("Not pushed: 2 buildings buried and 1 floating"), why
    assert "Houses (x=1200 y=-400)" in why and "allow_misfits=true" in why


if __name__ == "__main__":
    test_gate()
    print("ok")

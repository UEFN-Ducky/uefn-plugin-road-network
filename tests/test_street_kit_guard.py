"""A street kit look that can't be made skips only the pieces that wear it: py -3 tests/test_street_kit_guard.py"""
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
# road_io runs inside UEFN: stand in for its two imports
sys.modules.setdefault("unreal", types.ModuleType("unreal"))
dispatch = types.ModuleType("listener.dispatch")
dispatch.register = lambda name: (lambda fn: fn)
sys.modules["listener.dispatch"] = dispatch
from listener import road_io  # noqa: E402


def test_skips_only_pieces_with_a_missing_look():
    todo = {"TrafficLight": (["Metal", "Signal"], []), "StopSign": (["Metal", "SignRed"], []),
            "Bench": (["Wood"], [], "Kit/Street")}
    keep, skipped = road_io._pieces_without(todo, {"SignRed"})
    assert list(keep) == ["TrafficLight", "Bench"] and skipped == ["StopSign"], (keep, skipped)
    keep, skipped = road_io._pieces_without(todo, set())
    assert len(keep) == 3 and skipped == []


if __name__ == "__main__":
    test_skips_only_pieces_with_a_missing_look()
    print("ok")

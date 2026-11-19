"""Nature meshes get a cull distance (and ground cover no shadow); city pieces get none: py -3 tests/test_draw_limits.py"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from backend import kit_bake  # noqa: E402


def test_draw_limits():
    tree = kit_bake.draw_limits("/x/WorldGen/Nature/SM_Kit_Tree_Pine.SM_Kit_Tree_Pine")
    assert tree == {"instanceStartCullDistance": 40000, "instanceEndCullDistance": 50000}, tree  # trees keep shadows
    bush = kit_bake.draw_limits("/x/SM_Kit_Bush_Round.SM_Kit_Bush_Round")
    assert bush["instanceEndCullDistance"] == 15000 and bush["castShadow"] is False, bush
    assert kit_bake.draw_limits("/x/SM_Kit_Rock_03.SM_Kit_Rock_03")["instanceEndCullDistance"] == 25000
    assert kit_bake.draw_limits("/x/SM_Kit_Plinth.SM_Kit_Plinth") == {}  # buildings and streets are never culled


if __name__ == "__main__":
    test_draw_limits()
    print("ok")

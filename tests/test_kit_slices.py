"""kit_materials retries a slice only after a memory-guard trip, never after the RAM limit:
py -3 tests/test_kit_slices.py"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))
import editor_heal  # noqa: E402
import uefn_memory as um  # noqa: E402


def test_trip_message_is_the_retryable_one():
    um._state["trip"] = {"cmd": "road_network_kit_materials", "grew": 7.0, "before": 20.0}
    why = um.check(30.0, 64.0)
    assert why.startswith("Stopped: '"), why  # kit_materials waits for a settle and tries this slice again
    assert um.check(21.0, 64.0) == ""  # back within SETTLE_GB: the trip clears by itself
    assert not editor_heal.kind(why)  # and editor_heal never resends it as a lost call


def test_ram_limit_is_never_retried():
    um._state["trip"] = None
    why = um.check(60.0, 64.0)
    assert why.startswith("Stopped: UEFN is using"), why  # not "Stopped: '": the person's call
    assert not why.startswith("Stopped: '") and not editor_heal.kind(why)


if __name__ == "__main__":
    test_trip_message_is_the_retryable_one()
    test_ram_limit_is_never_retried()
    print("ok")

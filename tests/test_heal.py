"""Editor calls heal themselves once: py -3 tests/test_heal.py"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))
import editor_heal as eh  # noqa: E402


def tools(popups=()):
    calls = []

    def call_tool(name, args):
        calls.append(name)
        return {"popups": list(popups)} if name == "uefn_popups" else {"ok": True}
    return call_tool, calls


def flaky(err, fails=1):
    n = {"left": fails}

    def fn():
        if n["left"]:
            n["left"] -= 1
            raise ConnectionError(err)
        return {"ok": True}
    return fn


def test_lost_connection_reloads_and_retries_once():
    call_tool, calls = tools()
    healed = []
    assert eh.retry_once(flaky("[WinError 10054] forcibly closed by the remote host"), call_tool, healed) == {"ok": True}
    assert "reload_listener" in calls and healed == ["reloaded the UEFN listener"]


def test_second_failure_raises():
    call_tool, _ = tools()
    try:
        eh.retry_once(flaky("connection reset", fails=2), call_tool)
    except ConnectionError:
        return
    raise AssertionError("a second failure must raise")


def test_timeout_presses_popup_but_never_resends():
    call_tool, calls = tools([{"hwnd": 7, "title": "Message", "blocking": True, "known": {"button": 1}},
                              {"hwnd": 8, "title": "Session Inspector", "blocking": False}])
    fn = flaky("timed out")
    try:
        eh.retry_once(fn, call_tool)
    except ConnectionError:
        assert calls == ["uefn_popups", "uefn_popup_press"], calls
        return
    raise AssertionError("a late reply must re-raise for the caller's own wait")


def test_other_errors_untouched():
    call_tool, calls = tools()
    try:
        eh.retry_once(flaky("No road network 'x'."), call_tool)
    except ConnectionError:
        assert calls == []
        return
    raise AssertionError


if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f()
    print("ok")

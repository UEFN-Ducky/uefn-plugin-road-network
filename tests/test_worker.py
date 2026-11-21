"""City Builder's own process (backend/worker.py).

Nov 20 2026: the plugin's work ran inside Ducky's window: 1.8 GB held there, and a preview or a rebuild on
the one Python core everything in Ducky shares. It runs in one worker process now. These tests hold it to
the promise that nothing else changes: the same answers, the same data, the same calls into Ducky, made
on the caller's own thread; and that the worker starts once, is shared, closes when idle or too big, can
be stopped, and that every call still works when there is no worker at all.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backend  # noqa: E402
from backend import worker  # noqa: E402
from ducky_stand_in import Api  # noqa: E402

TOY = '''
"""A small plugin shaped like City Builder's register(): state where the handlers run, Ducky's own with the caller."""
import os
import threading
import time

from . import worker


class Refused(PermissionError):
    """Like Ducky's own refusals: a class of its own with an __init__ that plain pickling cannot rebuild."""

    def __init__(self, message, lock):
        super().__init__(message)
        self.lock = lock


def register(api):
    served = worker.SERVING
    link = worker.link_for(api)
    if served:
        data = api.data
    else:
        from backend import history
        data = history.Recorder(api.data, lambda: os.path.join(api.local_dir(), "history"))
    state = {"n": 0}
    guard = threading.Lock()

    def who():
        return {"pid": os.getpid(), "thread": threading.current_thread().name}

    def refuse(key):
        raise Refused(f"Read only: {key} is locked by Sam.", {"by": "Sam"})

    if served:
        who, guard, refuse = api.caller("who guard refuse")
    elif link is not None:
        link.provide(api=api, data=data, who=who, guard=guard, refuse=refuse)

    def rpc(name, fn):
        api.register_panel_rpc(name, fn if served else worker.forward(link, "rpc", name, fn))

    def count():
        state["n"] += 1
        return {"n": state["n"], "pid": os.getpid()}

    def save(key="", value=None):
        before = data.get(key)
        with guard:
            data.put(key, value)
        api.changeset.record(command="save", ident=key)
        return {"ok": True, "before": before, "by": who(), "ran_in": os.getpid()}

    def read(key=""):
        return {"value": data.get(key), "ran_in": os.getpid()}

    def fail(kind=""):
        if kind == "key":
            raise KeyError("nope")
        if kind == "refused":
            try:
                refuse("net.w1")
            except PermissionError as exc:
                return {"ok": False, "error": str(exc), "type": type(exc).__name__, "is_permission": True}
        raise RuntimeError("it broke")

    def later(key="", wait=0.2):
        def run():
            time.sleep(wait)
            data.put(key, {"late": True})
            api.call_tool("late", {})
        threading.Thread(target=run, name="toy-late").start()
        return {"ok": True}

    def step(label=""):
        with data.step(label):
            data.put("net.w1", {"label": label})
        return {"ok": True, "steps": data.steps("w1")}

    def hog(mb=0):
        state["hog"] = bytearray(int(mb) * 1048576)
        return {"ok": True}

    def wait_then(key="", seconds=0.0):
        time.sleep(seconds)
        return {"value": data.get(key)}

    def echo(value=None):
        return {"value": value}

    for name, fn in (("count", count), ("save", save), ("read", read), ("fail", fail), ("later", later),
                     ("step", step), ("hog", hog), ("wait_then", wait_then), ("echo", echo)):
        rpc(name, fn)
'''


@pytest.fixture
def toy(tmp_path, monkeypatch):
    """The toy plugin above with this repo's worker.py, and Ducky processes that use it. make(folder) is one
    Ducky process's registration (the same folder: the same PC's Ducky; another folder: another Ducky)."""
    pkg = tmp_path / "toy_plugin"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(TOY, encoding="utf-8")
    shutil.copy(REPO / "backend" / "worker.py", pkg / "worker.py")
    name = f"toy_plugin_{os.getpid()}_{time.monotonic_ns()}"
    spec = importlib.util.spec_from_file_location(name, pkg / "__init__.py", submodule_search_locations=[str(pkg)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    monkeypatch.setenv("CITY_BUILDER_WORKER", "1")
    made = []

    def make(folder: str = "ducky") -> Api:
        (tmp_path / folder).mkdir(exist_ok=True)
        api = Api(str(tmp_path / folder))
        module.register(api)
        made.append(api)
        return api

    make.worker = sys.modules[f"{name}.worker"]
    make.link = lambda folder="ducky": make.worker.Link(name, str(tmp_path / folder))
    yield make
    for folder in {a.folder for a in made}:
        try:
            make.worker.Link(name, folder).stop()
        except Exception:  # noqa: BLE001
            pass
    sys.modules.pop(name, None)
    sys.modules.pop(f"{name}.worker", None)


def test_the_work_runs_in_the_worker_and_duckys_own_stays_on_the_callers_thread(toy) -> None:
    api = toy()
    link = toy.link()
    assert link.status() is None  # nothing started until something is asked
    first = api.rpcs["count"]()
    assert first["n"] == 1 and first["pid"] != os.getpid()
    assert api.rpcs["count"]() == {"n": 2, "pid": first["pid"]}  # its state lives there, once
    got = api.rpcs["save"](key="net.w1.road", value={"a": [1, 2]})
    assert got["ok"] and got["before"] is None and got["ran_in"] == first["pid"]
    # who is calling, the store and the change record are the caller's: asked here, on the thread that called
    assert got["by"] == {"pid": os.getpid(), "thread": threading.current_thread().name}
    assert json.loads(api.data.docs["net.w1.road"]) == {"a": [1, 2]}
    assert [(what, key) for what, key, _t in api.seen] == [("put", "net.w1.road"), ("record", "save:net.w1.road")]
    assert {t for *_x, t in api.seen} == {threading.current_thread().name}
    assert api.rpcs["read"](key="net.w1.road")["value"] == {"a": [1, 2]}
    status = link.status()
    assert status["pid"] == first["pid"] and status["calls"] == 4 and status["busy"] == 0


def test_a_step_of_history_is_opened_and_kept_where_the_call_came_from(toy) -> None:
    api = toy()
    got = api.rpcs["step"](label="Move Northgate")
    assert got["ok"] and [s["label"] for s in got["steps"]["undo"]] == ["Move Northgate"]
    assert os.path.isdir(os.path.join(api.folder, "history"))  # the caller's own folder: nothing kept in the worker


def test_errors_come_back_as_what_they_were(toy) -> None:
    api = toy()
    with pytest.raises(KeyError) as key:
        api.rpcs["fail"](kind="key")
    assert key.value.args == ("nope",)
    with pytest.raises(RuntimeError, match="it broke"):
        api.rpcs["fail"]()
    # Ducky's refusal, raised in the caller, reaches the worker's code as the PermissionError it is, under its name
    assert api.rpcs["fail"](kind="refused") == {
        "ok": False, "error": "Read only: net.w1 is locked by Sam.", "type": "Refused", "is_permission": True}
    assert api.rpcs["count"]()["n"] == 1  # and the worker is still there


def test_every_ducky_process_shares_the_one_worker_and_calls_run_side_by_side(toy) -> None:
    window, agent_host = toy(), toy()
    assert window.rpcs["count"]()["n"] == 1
    assert agent_host.rpcs["count"]()["n"] == 2  # the same worker: one copy of the plugin's state on the PC
    assert window.rpcs["save"](key="k", value=1)["by"]["pid"] == os.getpid()
    assert "k" in window.data.docs and "k" not in agent_host.data.docs  # each caller's own store was written
    out: dict = {}
    slow = threading.Thread(target=lambda: out.update(slow=window.rpcs["wait_then"](key="k", seconds=1.0)))
    slow.start()
    t0 = time.perf_counter()
    assert agent_host.rpcs["echo"](value=[1, {"x": None}]) == {"value": [1, {"x": None}]}
    assert time.perf_counter() - t0 < 0.8  # it did not wait for the slow one
    slow.join()
    assert out["slow"] == {"value": 1}
    other = toy("second-ducky")  # another Ducky on this PC, with its own data
    assert other.rpcs["count"]()["n"] == 1 and toy.link("second-ducky").status()["pid"] != toy.link().status()["pid"]


def test_a_thread_the_call_started_still_reaches_its_caller(toy) -> None:
    api = toy()
    assert api.rpcs["later"](key="net.w1.late", wait=1.5) == {"ok": True}
    assert "net.w1.late" not in api.data.docs and toy.link().status()["busy"] == 1  # still at work
    end = time.time() + 5
    while time.time() < end and ("call_tool", "late") not in [(w, k) for w, k, _t in api.seen]:
        time.sleep(0.05)
    assert json.loads(api.data.docs["net.w1.late"]) == {"late": True}
    assert [(w, k) for w, k, _t in api.seen] == [("put", "net.w1.late"), ("call_tool", "late")]
    end = time.time() + 5
    while time.time() < end and toy.link().status()["busy"]:
        time.sleep(0.05)
    assert toy.link().status()["busy"] == 0


def test_it_closes_when_idle_and_the_next_call_starts_a_new_one(toy, monkeypatch) -> None:
    monkeypatch.setenv("CITY_BUILDER_WORKER_IDLE_S", "0.6")
    api = toy()
    first = api.rpcs["count"]()
    end = time.time() + 10
    while time.time() < end and toy.link().status() is not None:
        time.sleep(0.2)
    assert toy.link().status() is None and not os.path.exists(toy.link().keyfile)
    again = api.rpcs["count"]()
    assert again["n"] == 1 and again["pid"] != first["pid"]


def test_a_call_that_left_it_too_big_closes_it(toy, monkeypatch) -> None:
    monkeypatch.setenv("CITY_BUILDER_WORKER_MB", "150")
    api = toy()
    first = api.rpcs["count"]()
    assert api.rpcs["hog"](mb=300) == {"ok": True}
    end = time.time() + 10
    while time.time() < end and toy.link().status() is not None:
        time.sleep(0.1)
    assert toy.link().status() is None  # all of it went back to Windows
    assert api.rpcs["count"]()["pid"] != first["pid"]


def test_stop_ends_a_run_that_changed_nothing_and_leaves_one_that_did(toy) -> None:
    api = toy()
    link = toy.link()
    api.rpcs["count"]()
    out: dict = {}
    run = threading.Thread(target=lambda: out.update(got=api.rpcs["wait_then"](key="k", seconds=30.0)))
    run.start()
    time.sleep(0.5)
    assert link.status()["running"] == ["rpc:wait_then"]
    # the link the call went through heard Stop: its answer is the plain one
    registered = [c for c in api.rpcs["wait_then"].__closure__ if isinstance(c.cell_contents, toy.worker.Link)]
    registered[0].cell_contents.give_up(0.2)
    run.join(10)
    assert out["got"] == {"ok": False, "stopped": True, "error": "Stopped. Nothing was changed."}
    assert link.status() is None
    # a run that already wrote is never cut short: Stop is heard, the worker stays, the call finishes
    release, reached = threading.Event(), threading.Event()
    record = api.changeset.record
    api.changeset.record = lambda **kw: (reached.set(), release.wait(20), record(**kw))[2]
    run = threading.Thread(target=lambda: out.update(saved=api.rpcs["save"](key="net.w1.x", value=7)))
    run.start()
    assert reached.wait(30)  # (a fresh worker started for it; it has written, and now waits in the caller)
    link.give_up(0.2)
    time.sleep(1.0)
    assert link.status() is not None and link.status()["running"] == ["rpc:save"]
    release.set()
    run.join(10)
    assert out["saved"]["ok"] and json.loads(api.data.docs["net.w1.x"]) == 7


def test_a_worker_that_goes_mid_call_answers_plainly_and_holds_nothing(toy) -> None:
    api = toy()
    link = toy.link()
    out: dict = {}
    run = threading.Thread(target=lambda: out.update(got=api.rpcs["wait_then"](key="k", seconds=30.0)))
    run.start()
    time.sleep(0.5)
    assert link.stop()
    run.join(10)
    assert out["got"]["ok"] is False and out["got"]["stopped"] and "Try again" in out["got"]["error"]
    assert api.rpcs["count"]()["n"] == 1  # the next call starts a fresh one


def test_with_no_worker_every_call_runs_in_the_caller_as_before(toy, monkeypatch) -> None:
    monkeypatch.setenv("CITY_BUILDER_WORKER", "0")
    api = toy()
    assert api.rpcs["count"]() == {"n": 1, "pid": os.getpid()} and toy.link().status() is None
    # a PC where a process cannot be started: said once, then the old way; tried again later
    monkeypatch.setenv("CITY_BUILDER_WORKER", "1")
    said = []
    api = toy("no-spawn")
    link = [c.cell_contents for c in api.rpcs["count"].__closure__ if isinstance(c.cell_contents, toy.worker.Link)][0]
    link.log = said.append
    monkeypatch.setattr(link, "_spawn", lambda: (_ for _ in ()).throw(OSError("no processes here")))
    assert api.rpcs["count"]()["pid"] == os.getpid() and api.rpcs["count"]()["n"] == 2
    assert len(said) == 1 and "runs inside Ducky for now" in said[0]
    # what cannot be sent (never the case for a panel call or a tool, whose arguments are JSON) also runs here
    monkeypatch.undo()
    monkeypatch.setenv("CITY_BUILDER_WORKER", "1")
    api = toy("odd-args")
    assert api.rpcs["echo"](value=threading.Lock())["value"] is not None


def _flow(api: Api) -> dict:
    """A small world end to end: generated, its cities read, layers added, placed, read by the panel."""
    out = {"generate": api.tools["road_network_generate"](name="Tiny", seed=7, cities="1", size_km=2.0)}
    out["cities"] = api.tools["road_network_cities"](net_id="tiny")
    out["preset"] = api.tools["road_network_pcg_preset"](net_id="tiny", preset="city")
    out["preview"] = api.tools["road_network_pcg_preview"](net_id="tiny", summary=True)
    out["panel"] = api.rpcs["pcg_preview"](net_id="tiny")
    out["stats"] = api.tools["road_network_stats"](net_id="tiny")
    out["history"] = api.rpcs["history"](net_id="tiny")
    out["holds"] = api.rpcs["holds"](net_id="tiny")
    api.built = (dict(api.data.blobs), dict(api.data.docs))  # the world as built, before the undo takes it back
    out["undo"] = api.rpcs["undo"](net_id="tiny")
    out["after_undo"] = api.tools["road_network_cities"](net_id="tiny")
    return out


# What two honest runs of the same flow differ in: the clock, and (in the history) step ids made from it.
_CLOCK = ("updated", "created", "took_s")
_STEP_IDS = _CLOCK + ("id", "at")


def _steady(value, drop=_CLOCK):
    if isinstance(value, dict):
        return {k: _steady(v, drop) for k, v in value.items() if k not in drop}
    if isinstance(value, list):
        return [_steady(v, drop) for v in value]
    return value


def test_the_real_plugin_gives_the_same_world_through_the_worker(tmp_path, monkeypatch) -> None:
    """The whole backend, one process against the worker: answers, stored docs and files, and every call into
    Ducky (in order, on the caller's thread) are the same. Only the clock differs."""
    runs = {}
    for mode in ("0", "1"):
        monkeypatch.setenv("CITY_BUILDER_WORKER", mode)
        folder = tmp_path / f"ducky-{mode}"
        folder.mkdir()
        api = Api(str(folder))
        backend.register(api)
        try:
            runs[mode] = (api, _flow(api))
            said = api.tools["road_network_worker"]()  # (answered by the caller itself: it is not a call to the worker)
            assert said["ok"] and said["on"] is (mode == "1") and said["running"] is (mode == "1")
            assert (said.get("memory_mb", 0) > 50) is (mode == "1")
        finally:
            if mode == "1":
                status = worker.Link("backend", str(folder)).status()
                worker.Link("backend", str(folder)).stop()
    (here, here_out), (there, there_out) = runs["0"], runs["1"]
    assert here_out["generate"]["ok"] and here_out["preview"]["ok"] and here_out["panel"]["layers"]
    assert status and status["pid"] != os.getpid() and status["calls"] == len(here_out)
    for name in here_out:
        drop = _STEP_IDS if name in ("history", "undo") else _CLOCK
        assert _steady(there_out[name], drop) == _steady(here_out[name], drop), name
    docs = lambda raw: _steady({k: json.loads(v) for k, v in raw.items()})  # noqa: E731
    assert there.built[0] == here.built[0] and len(here.built[0]) >= 1  # the world's files: byte for byte
    assert docs(there.built[1]) == docs(here.built[1]) and len(here.built[1]) > 10
    assert there.data.blobs == here.data.blobs and docs(there.data.docs) == docs(here.data.docs)  # and after the undo
    assert [(what, key) for what, key, _t in there.seen] == [(what, key) for what, key, _t in here.seen]
    assert {t for *_x, t in there.seen} == {threading.current_thread().name}


def test_this_pc_switches_it_on_and_off_and_it_is_off_until_then(toy, monkeypatch) -> None:
    monkeypatch.delenv("CITY_BUILDER_WORKER", raising=False)  # nothing forced: the PC's own choice
    api = toy()
    link = [c.cell_contents for c in api.rpcs["count"].__closure__ if isinstance(c.cell_contents, toy.worker.Link)][0]
    assert link.on() is False and api.rpcs["count"]() == {"n": 1, "pid": os.getpid()}  # off: inside Ducky, as before
    assert link.status() is None
    assert link.switch(True) is True and os.path.exists(os.path.join(api.folder, "worker.on"))
    there = api.rpcs["count"]()  # at once, with no reload of the plugin
    assert there["n"] == 1 and there["pid"] != os.getpid() and link.status()["calls"] == 1
    assert toy.link().on() is True  # the choice is the PC's: every Ducky process reads the same file
    assert link.switch(False) is False and link.status() is None  # off closes the running one too
    assert api.rpcs["count"]() == {"n": 2, "pid": os.getpid()}
    monkeypatch.setenv("CITY_BUILDER_WORKER", "1")  # said from outside: it wins over the PC's choice
    assert link.on() is True


def test_a_compiled_build_starts_its_worker_from_the_one_file_it_is(tmp_path) -> None:
    """A compiled plugin is one extension file, and Python is told its code lives in a folder that is not on
    disk: <plugin>/<package>/__init__.py for <plugin>/<package>.cp313-win_amd64.pyd (seen Nov 20 2026)."""
    import types
    from importlib.machinery import ModuleSpec

    plugin = tmp_path / "road-network"
    plugin.mkdir()
    built = plugin / "uefn_plugin_x_v2000000.cp313-win_amd64.pyd"
    built.write_bytes(b"MZ")
    (plugin / "uefn_plugin_x_v2000000_other.cp313-win_amd64.pyd").write_bytes(b"MZ")  # another package's file
    name = "uefn_plugin_x_v2000000"
    module = types.ModuleType(name)
    module.__spec__ = ModuleSpec(name, None, origin=str(plugin / name / "__init__.py"))
    module.__spec__.submodule_search_locations = [str(plugin / name), "\\not_existing"]
    sys.modules[name] = module
    try:
        stamp, origin, search = worker._stamp(name, str(tmp_path / "data"))
        assert origin == str(built) and search == [] and len(stamp) == 20
        built.unlink()
        (plugin / "uefn_plugin_x_v2000000_other.cp313-win_amd64.pyd").unlink()
        _stamp2, origin, _search = worker._stamp(name, str(tmp_path / "data"))
        assert not os.path.isfile(origin)  # nothing to load a second time: link_for() gives no link, the old way
    finally:
        sys.modules.pop(name, None)


def test_only_the_callers_side_reaches_into_ducky() -> None:
    """The parts run in the worker, where Ducky's own modules must never be imported: what a part needs of
    Ducky it takes from ctx, and backend/__init__.py register() hands the worker the caller's own for it.
    So Ducky's modules are imported in __init__.py only, inside functions the worker is given as the caller's."""
    import ast

    handed_over = set()
    tree = ast.parse((REPO / "backend" / "__init__.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):  # api.caller("actor team_live ...") names what the worker takes from the caller
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "caller"
                and node.args and isinstance(node.args[0], (ast.Constant, ast.BinOp, ast.JoinedStr))):
            handed_over |= set(ast.literal_eval(node.args[0]).split())
    assert {"actor", "team_live", "push", "epic_call", "held_cities", "held_lock"} <= handed_over

    def ducky_imports(tree_: ast.AST) -> list:
        found = []
        for node in ast.walk(tree_):
            names = ([node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0
                     else [a.name for a in node.names] if isinstance(node, ast.Import) else [])
            found += [(n, node.lineno) for n in names if n.split(".")[0] in ("frontend", "backend")]
        return found

    wrong = []
    for path in sorted((REPO / "backend").rglob("*.py")):
        if path.name == "__init__.py" and path.parent == REPO / "backend":
            continue
        wrong += [f"{path.relative_to(REPO)}:{line} imports {name}" for name, line in ducky_imports(
            ast.parse(path.read_text(encoding="utf-8")))]
    assert not wrong, wrong
    register = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "register")
    for fn in [n for n in ast.walk(register) if isinstance(n, ast.FunctionDef) and n is not register]:
        if ducky_imports(fn):
            assert fn.name in handed_over, f"{fn.name}() imports Ducky's modules but the worker is not given the caller's"

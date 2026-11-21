"""City Builder's own process: the plugin's work runs there, not inside Ducky.

Nov 20 2026: Ducky's window process held 1.86 GB, about 1.8 GB of it this plugin's placements, and a
preview or a rebuild ran for up to a minute on the one Python core the window, every chat and every
other plugin share. The panel's calls ran in the window; each agent process ran its own copy, with its
own caches and its own "one run at a time".

Now every Ducky process registers the plugin as before, but a panel call, a tool or a workflow node is
passed to ONE worker process on this PC (`Link.call`). The worker runs the plugin's parts, so their
caches, locks and progress live once. Everything that belongs to Ducky stays with the process that
asked and is called back there, on the very thread that made the call: the data store and its history
(`data`), UEFN and Ducky's tools (`api`), who is calling (`actor`), holds, events. So a change is
recorded, shared and attributed exactly as it was.

The worker starts on the first call, closes when nothing has called it for IDLE_S, and closes after a
call that left it holding more than its memory limit: all of its memory goes back to Windows, and the
next call starts a fresh one. When it cannot start, the call runs in the caller, as it always did.

    CITY_BUILDER_WORKER=1     on (off unless set, until it has been checked inside the installed app)
    CITY_BUILDER_WORKER_MB=N  the memory limit, in MB
"""

from __future__ import annotations

import contextvars
import functools
import hashlib
import json
import os
import pickle
import secrets
import sys
import threading
import time
from multiprocessing.connection import Client, Listener

IDLE_S = 180.0    # no call from any Ducky process for this long: the worker closes (CITY_BUILDER_WORKER_IDLE_S)
START_S = 60.0    # how long a caller waits for a new worker to answer
RETRY_S = 60.0    # after a start that failed: calls run in the caller this long before it is tried again
STOP_S = 5.0      # after Stop: a run that has changed nothing and is still going by then is ended with the worker
RUNAWAY = 1.5     # a run holding this many times the memory limit, having changed nothing, is ended the same way
# What a call does to the world outside the worker: once it has done one of these it is never cut short.
CHANGES = {("data", "put"), ("data", "delete"), ("data", "put_file"), ("data", "delete_file"), ("data", "undo"),
           ("data", "redo"), ("data", "forget"), ("api", "listener"), ("api", "call_tool"), ("api", "search"),
           ("api", "changeset"), ("epic_call",), ("record",)}
FAMILY = "AF_PIPE" if os.name == "nt" else "AF_UNIX"
SERVING = False   # True in the worker process itself

_BOOT = """
import importlib, importlib.machinery, importlib.util, os, sys, traceback
os.environ["UEFN_DUCKY_PROCESS_ROLE"] = "tool_server"  # never the window: nothing of Ducky's own starts here
try:
    name, origin, search = ARGS["name"], ARGS["origin"], ARGS["search"]
    if origin.lower().endswith((".pyd", ".so")):
        spec = importlib.util.spec_from_file_location(
            name, origin, loader=importlib.machinery.ExtensionFileLoader(name, origin))
    else:
        spec = importlib.util.spec_from_file_location(name, origin, submodule_search_locations=search)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    importlib.import_module(name + ".worker").serve(ARGS)
except BaseException:  # why it could not start, for the caller's log (never a silent no-show)
    try:
        with open(ARGS["keyfile"] + ".err", "w", encoding="utf-8") as fh:
            fh.write(traceback.format_exc()[-4000:])
    finally:
        os._exit(1)
"""


class Unavailable(Exception):
    """No worker and none could be started: the call runs in the caller."""


class WorkerGone(Exception):
    """The worker closed while it ran a call."""


def enabled() -> bool:
    return os.environ.get("CITY_BUILDER_WORKER", "0").strip().lower() in ("1", "on", "true", "yes")


# ---------------------------------------------------------------- errors across the two processes
def _pack_error(exc: BaseException) -> tuple:
    """An exception as plain data: its class names (nearest first) and its arguments."""
    names = [c.__name__ for c in type(exc).__mro__ if c not in (object, BaseException)]
    try:
        args = pickle.loads(pickle.dumps(exc.args))
    except Exception:  # noqa: BLE001 - arguments that cannot travel: the message alone
        args = (str(exc),)
    return names, args, str(exc)


def _unpack_error(packed: tuple) -> BaseException:
    """The same exception on the other side: a built-in comes back as itself; any other class comes back under
    its own name, built on its nearest built-in base, so `except PermissionError` and checks on the name still hold."""
    import builtins
    names, args, text = packed
    base = next((getattr(builtins, n) for n in names if isinstance(getattr(builtins, n, None), type)
                 and issubclass(getattr(builtins, n), BaseException)), RuntimeError)
    cls = base if names and names[0] == base.__name__ else type(names[0] if names else "Error", (base,), {})
    try:
        out = cls(*args)
    except Exception:  # noqa: BLE001
        out = cls(text)
    if str(out) != text:
        out = cls(text)
    return out


# ---------------------------------------------------------------- where the worker is
def _stamp(package: str, folder: str = "") -> tuple[str, str, list]:
    """(stamp, origin, search): the stamp changes with the plugin's code, so an updated plugin never talks to a
    worker that still runs the old code (that one closes when it has been idle). It is the folder's too: a
    second Ducky on this PC with its own data (a test copy) gets a worker of its own."""
    spec = sys.modules[package].__spec__
    origin = str(spec.origin or "")
    search = [str(p) for p in (spec.submodule_search_locations or [])]
    newest, size = 0, 0
    try:
        if origin.lower().endswith((".pyd", ".so")):
            st = os.stat(origin)
            newest, size = st.st_mtime_ns, st.st_size
        else:
            for here, _dirs, files in os.walk(os.path.dirname(origin)):
                for f in files:
                    if f.endswith(".py"):
                        st = os.stat(os.path.join(here, f))
                        newest, size = max(newest, st.st_mtime_ns), size + st.st_size
    except OSError:
        pass
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    raw = f"{user}|{os.path.normcase(origin)}|{newest}|{size}|{os.path.normcase(os.path.abspath(folder))}"
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:20], origin, search


def _address(stamp: str, folder: str) -> str:
    return rf"\\.\pipe\city-builder-{stamp}" if FAMILY == "AF_PIPE" else os.path.join(folder, f"worker-{stamp[:12]}.sock")


# ---------------------------------------------------------------- the caller's side
class Link:
    """One per Ducky process: passes a call to the worker and answers what the worker asks back."""

    def __init__(self, package: str, folder: str, log=None, plugin_id: str = "road-network") -> None:
        self.package, self.folder, self.log = package, folder, log or (lambda text: None)
        self.plugin_id = plugin_id
        self.stamp, self.origin, self.search = _stamp(package, folder)
        self.address = _address(self.stamp, folder)
        self.keyfile = os.path.join(folder, f"worker-{self.stamp[:12]}.json")
        self.objects: dict = {}    # what the worker may call back, by name
        self._starting = threading.Lock()
        self._down_until = 0.0
        self._said = ""
        self._gave_up = -1e9  # when this process last asked for the running work to be stopped

    def provide(self, **objects) -> None:
        self.objects.update(objects)

    # ---- reaching it
    def _key(self) -> bytes | None:
        try:
            with open(self.keyfile, encoding="utf-8") as fh:
                return bytes.fromhex(json.load(fh)["key"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _try(self):
        key = self._key()
        if key is None:
            return None
        try:
            return Client(self.address, family=FAMILY, authkey=key)
        except Exception:  # noqa: BLE001 - not there, an old key, busy starting: all "not yet"
            return None

    def _spawn(self) -> None:
        import multiprocessing
        os.makedirs(self.folder, exist_ok=True)
        args = {"name": self.package, "origin": self.origin, "search": self.search, "address": self.address,
                "keyfile": self.keyfile, "folder": self.folder, "plugin_id": self.plugin_id}
        proc = multiprocessing.get_context("spawn").Process(
            target=exec, args=(_BOOT, {"ARGS": args}), name="city-builder-worker", daemon=True)
        proc.start()

    def connect(self, start: bool = True):
        conn = self._try()
        if conn is not None or not start:
            if conn is None:
                raise Unavailable("no worker is running")
            return conn
        if time.monotonic() < self._down_until:
            raise Unavailable(self._said or "the worker could not start")
        with self._starting:
            conn = self._try()  # another thread started it while this one waited
            if conn is not None:
                return conn
            try:
                self._spawn()
            except Exception as exc:  # noqa: BLE001 - no way to start a process here: stay in the caller
                return self._down(f"City Builder's worker could not start ({type(exc).__name__}: {exc}); "
                                  "its work runs inside Ducky for now.")
            end = time.monotonic() + START_S
            while time.monotonic() < end:
                conn = self._try()
                if conn is not None:
                    return conn
                why = self._why()
                if why:  # it said why it cannot start: no point waiting the minute out
                    return self._down(f"City Builder's worker could not start ({why}); its work runs inside Ducky for now.")
                time.sleep(0.05)
            return self._down(f"City Builder's worker did not answer in {round(START_S)} s; "
                              "its work runs inside Ducky for now.")

    def _why(self) -> str:
        """The last line of what a worker that failed to start wrote down ('' when none did); read once."""
        try:
            with open(self.keyfile + ".err", encoding="utf-8") as fh:
                lines = [ln.strip() for ln in fh.read().splitlines() if ln.strip()]
            os.remove(self.keyfile + ".err")
            return lines[-1][:300] if lines else "no reason given"
        except OSError:
            return ""

    def _down(self, text: str):
        self._down_until, self._said = time.monotonic() + RETRY_S, text
        try:
            self.log(text)
        except Exception:  # noqa: BLE001
            pass
        raise Unavailable(text)

    # ---- a call
    def call(self, kind: str, name: str, args: tuple, kwargs: dict, local):
        """Run handler `name` in the worker. `local` is the same handler here: used when there is no worker."""
        if SERVING or not enabled():
            return local(*args, **kwargs)
        try:
            conn = self.connect()
        except Unavailable:
            return local(*args, **kwargs)
        try:
            conn.send(("call", kind, name, args, kwargs, os.getpid()))
        except (pickle.PicklingError, TypeError, AttributeError):  # arguments that cannot travel
            conn.close()
            return local(*args, **kwargs)
        except OSError:  # the worker went as the call was sent: nothing of it ran
            conn.close()
            return local(*args, **kwargs)
        try:
            return _Answering(self, conn).until_done()
        except WorkerGone:
            if time.monotonic() - self._gave_up < STOP_S + 30.0:  # it was stopped from here: the plain answer
                return {"ok": False, "stopped": True, "error": "Stopped. Nothing was changed."}
            return _gone()

    def give_up(self, grace_s: float = STOP_S) -> bool:
        """Stop was pressed. The plugin's own stop ends a run at its next stage; one that is still going after
        `grace_s`, with nothing changed yet, is ended by closing the worker. True when a worker heard it."""
        try:
            conn = self.connect(start=False)
        except Unavailable:
            return False
        self._gave_up = time.monotonic()
        try:
            conn.send(("give_up", float(grace_s)))
            conn.recv()
        except (EOFError, OSError):
            pass
        finally:
            conn.close()
        return True

    def stop(self) -> bool:
        """Close the worker now (its memory goes back at once). True when one was running."""
        try:
            conn = self.connect(start=False)
        except Unavailable:
            return False
        try:
            conn.send(("stop",))
            conn.recv()
        except (EOFError, OSError):
            pass
        finally:
            conn.close()
        return True

    def status(self) -> dict | None:
        """What the running worker says of itself, or None when there is none (never starts one)."""
        try:
            conn = self.connect(start=False)
        except Unavailable:
            return None
        try:
            conn.send(("status",))
            return conn.recv()
        except (EOFError, OSError):
            return None
        finally:
            conn.close()


def _gone() -> dict:
    return {"ok": False, "stopped": True,
            "error": "City Builder's worker closed while it ran this. Nothing more was changed. Try again."}


class _Answering:
    """One call in flight, on the caller's side: answers what the worker asks back until the result comes."""

    def __init__(self, link: Link, conn) -> None:
        self.link, self.conn = link, conn
        self.kept: dict = {}  # what the caller made that cannot travel (a `with` block, a lock), by number
        self.entered: list = []

    def until_done(self):
        context = contextvars.copy_context()
        while True:
            try:
                msg = self.conn.recv()
            except (EOFError, OSError):
                self._leave()
                raise WorkerGone() from None
            tag = msg[0]
            if tag == "back":
                try:
                    self._reply(msg)
                except (EOFError, OSError):
                    self._leave()
                    raise WorkerGone() from None
            elif tag == "ret":
                if msg[2]:  # a thread the call started goes on working: keep answering it, off this thread
                    threading.Thread(target=context.run, args=(self._background,),
                                     name="city-builder-link", daemon=True).start()
                else:
                    self._leave()
                return msg[1]
            elif tag == "raise":
                self._leave()
                raise _unpack_error(msg[1])

    def _background(self) -> None:
        while True:
            try:
                msg = self.conn.recv()
            except (EOFError, OSError):
                break
            if msg[0] != "back":
                break  # "done"
            try:
                self._reply(msg)
            except (EOFError, OSError):
                break
        self._leave()

    def _leave(self) -> None:
        for held in reversed(self.entered):  # a `with` block the worker never closed (it died inside it)
            try:
                held.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        self.entered.clear()
        try:
            self.conn.close()
        except OSError:
            pass

    def _target(self, path: tuple):
        root = path[0]
        obj = self.kept[root] if isinstance(root, int) else self.link.objects[root]
        for name in path[1:]:
            if name.startswith("_"):
                raise AttributeError(name)
            obj = getattr(obj, name)
        return obj

    def _reply(self, msg: tuple) -> None:
        _tag, op, path, args, kwargs = msg
        try:
            if op == "call":
                out = self._target(path)(*args, **kwargs)
            elif op == "get":
                out = self._target(path)
            elif op == "enter":
                held = self._target(path)
                held.__enter__()
                self.entered.append(held)
                out = None
            elif op == "exit":
                held = self._target(path)
                if held in self.entered:
                    self.entered.remove(held)
                held.__exit__(None, None, None)
                out = None
            else:
                raise ValueError(f"unknown request {op!r}")
            reply = ("ok", out)
        except Exception as exc:  # noqa: BLE001 - the caller's own error: the worker's code decides what to do with it
            reply = ("err", _pack_error(exc))
        try:
            self.conn.send(reply)
        except (pickle.PicklingError, TypeError, AttributeError):
            if reply[0] != "ok":
                raise
            number = len(self.kept) + 1  # it cannot travel (a `with` block, a lock): it stays here, by number
            self.kept[number] = reply[1]
            self.conn.send(("kept", number))


def forward(link: Link | None, kind: str, name: str, fn):
    """`fn`, run in the worker when there is a link (same name, signature and doc)."""
    if link is None:
        return fn

    @functools.wraps(fn)
    def run(*args, **kwargs):
        return link.call(kind, name, args, kwargs, fn)
    return run


class NodeShim:
    """The caller's `api` as the parts see it: itself, but a workflow node they register runs in the worker."""

    def __init__(self, api, link: Link) -> None:
        self._api, self._link = api, link

    def __getattr__(self, name):
        return getattr(self._api, name)

    def register_pipeline_node(self, node_type: str, handler=None):
        def deco(fn):
            self._api.register_pipeline_node(node_type, forward(self._link, "node", node_type, fn))
            return fn
        return deco(handler) if handler is not None else deco


def link_for(api) -> Link | None:
    """This process's link to the worker; None inside the worker and when workers are off."""
    if SERVING or not enabled():
        return None
    try:
        folder = str(api.local_dir() if callable(api.local_dir) else api.local_dir)
        link = Link(__package__, folder, getattr(api, "log", None), str(getattr(api, "plugin_id", "") or "road-network"))
    except Exception:  # noqa: BLE001 - an api with no folder (a test's stand-in): the old way
        return None
    # a build whose file is not on disk under the name Python knows cannot be loaded a second time: the old way
    return link if os.path.isfile(link.origin) else None


# ---------------------------------------------------------------- the worker's side
_mine = threading.local()  # .session: the call this thread works for


class _Session:
    """One call in flight, on the worker's side: the pipe back to the caller, shared by the call's own thread
    and any thread it started."""

    def __init__(self, conn, state, name: str = "") -> None:
        self.conn, self.state, self.name = conn, state, name
        self.lock = threading.Lock()
        self.threads = 0      # threads the call started that are still running
        self.answered = False
        self.changed = False  # it wrote, or called UEFN or a Ducky tool: never cut short after that

    def back(self, op: str, path: tuple, args: tuple = (), kwargs: dict | None = None):
        if not self.changed and (tuple(path[:2]) in CHANGES or tuple(path[:1]) in CHANGES):
            self.changed = True
        with self.lock:
            try:
                self.conn.send(("back", op, path, args, kwargs or {}))
                reply = self.conn.recv()
            except (EOFError, OSError):
                raise ConnectionError("The Ducky process that asked for this has gone.") from None
        if reply[0] == "ok":
            return reply[1]
        if reply[0] == "kept":
            return Remote(reply[1])
        raise _unpack_error(reply[1])

    def answer(self, reply: tuple) -> None:
        with self.lock:
            self.answered = True
            more = self.threads > 0
            try:
                self.conn.send(reply + (more,) if reply[0] == "ret" else reply)
            except (pickle.PicklingError, TypeError, AttributeError) as exc:
                self.conn.send(("raise", _pack_error(TypeError(f"The answer cannot be sent back: {exc}"))))
            except (EOFError, OSError):
                pass
            if not more or reply[0] != "ret":
                self._close()

    def thread_started(self) -> None:
        with self.lock:
            self.threads += 1
        self.state.enter()

    def thread_ended(self) -> None:
        with self.lock:
            self.threads -= 1
            if self.threads == 0 and self.answered:
                try:
                    self.conn.send(("done",))
                except (EOFError, OSError):
                    pass
                self._close()
        self.state.leave()

    def _close(self) -> None:
        try:
            self.conn.close()
        except OSError:
            pass


def _session() -> _Session:
    session = getattr(_mine, "session", None)
    if session is None:
        raise RuntimeError("City Builder worker: this thread works for no caller, so it cannot reach Ducky.")
    return session


class Remote:
    """Something of the caller's, by name: calling it, reading from it or a `with` on it happens in the caller."""

    __slots__ = ("_path",)

    def __init__(self, *path) -> None:
        object.__setattr__(self, "_path", path)

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return Remote(*self._path, name)

    def __call__(self, *args, **kwargs):
        return _session().back("call", self._path, args, kwargs)

    def __enter__(self):
        _session().back("enter", self._path)
        return self

    def __exit__(self, *exc) -> bool:
        _session().back("exit", self._path)
        return False

    def __repr__(self) -> str:
        return f"<caller's {'.'.join(str(p) for p in self._path)}>"

    @property
    def __name__(self) -> str:  # (the parts wrap handlers and copy their name: safe(hold))
        return str(self._path[-1])


class WorkerApi(Remote):
    """The `api` the parts get inside the worker. What they register is kept here, by name; every other
    member is the caller's own api."""

    __slots__ = ("plugin_id", "handlers", "city_builder_worker")

    def __init__(self, plugin_id: str) -> None:
        super().__init__("api")
        object.__setattr__(self, "plugin_id", plugin_id)
        object.__setattr__(self, "handlers", {})
        object.__setattr__(self, "city_builder_worker", True)

    @property
    def data(self):
        return Remote("data")

    def caller(self, names: str) -> list:
        return [Remote(n) for n in names.split()]

    def register_panel_rpc(self, name: str, fn) -> None:
        self.handlers[("rpc", name)] = fn

    def tool(self, **_kw):
        def deco(fn):
            self.handlers[("tool", fn.__name__)] = fn
            return fn
        return deco

    def register_pipeline_node(self, node_type: str, handler=None):
        def deco(fn):
            self.handlers[("node", node_type)] = fn
            return fn
        return deco(handler) if handler is not None else deco


class _State:
    """What the worker knows of itself: how many calls run, when the last one ended."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.busy, self.last, self.calls, self.started = 0, time.monotonic(), 0, time.time()
        self.running: set = set()  # the calls in flight (their sessions)

    def may_end(self, only: set | None = None) -> bool:
        """Is some call (of `only`, when given) still going, and has no call in flight changed anything?"""
        with self.lock:
            if self.busy > len(self.running):  # a thread some call started is still at work (a build in UEFN)
                return False
            going = [x for x in self.running if only is None or x in only]
            return bool(going) and not any(x.changed for x in self.running)

    def enter(self) -> None:
        with self.lock:
            self.busy += 1

    def leave(self) -> None:
        with self.lock:
            self.busy -= 1
            self.last = time.monotonic()

    def idle_for(self) -> float:
        with self.lock:
            return 0.0 if self.busy else time.monotonic() - self.last


def memory_mb() -> float:
    """What this process holds (0 where it cannot be read)."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                    (n, ctypes.c_size_t) for n in ("peak_wset", "wset", "peak_paged", "paged", "peak_nonpaged",
                                                   "nonpaged", "pagefile", "peak_pagefile", "private")]
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
            if psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return counters.private / 1048576.0
            return 0.0
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:  # noqa: BLE001
        return 0.0


def memory_limit_mb() -> float:
    """A quarter of the PC's memory, between 2 and 8 GB, unless CITY_BUILDER_WORKER_MB says otherwise."""
    try:
        given = float(os.environ.get("CITY_BUILDER_WORKER_MB", "") or 0)
    except ValueError:
        given = 0.0
    if given > 0:
        return given
    total = 16384.0
    try:
        if os.name == "nt":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (n, ctypes.c_ulonglong) for n in ("total", "avail", "total_page", "avail_page", "total_virtual",
                                                      "avail_virtual", "avail_extended")]
            status = Status()
            status.length = ctypes.sizeof(status)
            if ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(status)):
                total = status.total / 1048576.0
    except Exception:  # noqa: BLE001
        pass
    return min(8192.0, max(2048.0, total / 4))


def _carry_sessions() -> None:
    """A thread started inside a call works for the same caller (a build that goes on after its call answered)."""
    start = threading.Thread.start

    def carried_start(self):
        session = getattr(_mine, "session", None)
        if session is not None:
            run = self.run
            session.thread_started()

            def carried() -> None:
                _mine.session = session
                try:
                    run()
                finally:
                    session.thread_ended()
            self.run = carried
        return start(self)
    threading.Thread.start = carried_start


def _quit(args: dict, code: int = 0) -> None:
    try:
        with open(args["keyfile"], encoding="utf-8") as fh:
            mine = json.load(fh).get("pid") == os.getpid()
        if mine:
            os.remove(args["keyfile"])
    except (OSError, ValueError):
        pass
    os._exit(code)


def _one(conn, api: WorkerApi, state: _State, args: dict, limit_mb: float) -> None:
    try:
        msg = conn.recv()
    except (EOFError, OSError):
        return
    if msg[0] == "status":
        with state.lock:
            going = sorted(x.name for x in state.running)
        conn.send({"pid": os.getpid(), "memory_mb": round(memory_mb(), 1), "busy": state.busy, "calls": state.calls,
                   "running": going,
                   "idle_s": round(state.idle_for(), 1), "limit_mb": round(limit_mb), "up_s": round(time.time() - state.started)})
        conn.close()
        return
    if msg[0] == "stop":
        try:
            conn.send(("ok", None))
        except (EOFError, OSError):
            pass
        _quit(args)
    if msg[0] == "give_up":
        with state.lock:
            then = set(state.running)
        try:
            conn.send(("ok", len(then)))
            conn.close()
        except (EOFError, OSError):
            pass
        time.sleep(max(0.0, float(msg[1])))
        if state.may_end(then):  # still computing, nothing changed anywhere: end it, the memory goes back too
            _quit(args)
        return
    _tag, kind, name, call_args, call_kwargs, _pid = msg
    session = _mine.session = _Session(conn, state, f"{kind}:{name}")
    state.enter()
    state.calls += 1
    with state.lock:
        state.running.add(session)
    try:
        fn = api.handlers.get((kind, name))
        if fn is None:
            raise LookupError(f"City Builder's worker has no {kind} named {name!r}.")
        reply = ("ret", fn(*call_args, **call_kwargs))
    except BaseException as exc:  # noqa: BLE001 - it is the caller's to raise
        reply = ("raise", _pack_error(exc))
    finally:
        _mine.session = None
    with state.lock:
        state.running.discard(session)
    session.answer(reply)
    state.leave()
    # a call that left the worker holding too much: close once nothing else runs (the next call starts afresh)
    if limit_mb and state.busy == 0 and memory_mb() > limit_mb:
        _quit(args)


def serve(args: dict) -> None:
    """The worker process: register the plugin's parts here, then answer calls until idle."""
    global SERVING
    SERVING = True
    key = secrets.token_bytes(32)
    if FAMILY == "AF_UNIX" and os.path.exists(args["address"]):
        try:  # a socket file with nobody behind it (a worker that was killed) is cleared; a live one stays
            Client(args["address"], family=FAMILY).close()
        except ConnectionRefusedError:
            os.unlink(args["address"])
        except Exception:  # noqa: BLE001
            pass
    try:
        listener = Listener(args["address"], family=FAMILY, authkey=key)
    except PermissionError:  # another worker won the start (the pipe is taken): it serves, this one leaves
        os._exit(0)
    package = sys.modules[__package__]
    api = WorkerApi(str(args.get("plugin_id") or "road-network"))
    package.register(api)
    _carry_sessions()
    state, limit_mb = _State(), memory_limit_mb()
    tmp = args["keyfile"] + f".{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"key": key.hex(), "pid": os.getpid(), "started": time.time()}, fh)
    os.replace(tmp, args["keyfile"])  # only now can a caller find it: it is ready to answer

    try:
        idle_s = float(os.environ.get("CITY_BUILDER_WORKER_IDLE_S", "") or IDLE_S)
    except ValueError:
        idle_s = IDLE_S

    def watch() -> None:
        while True:
            time.sleep(min(5.0, max(0.2, idle_s / 4)))
            if state.idle_for() > idle_s:
                _quit(args)
            # a run that only grows (Nov 11 2026: previews that never ended took Ducky to 13 GB): ended here
            # while it has changed nothing; one that is saving or building in UEFN is left to finish
            if limit_mb and memory_mb() > limit_mb * RUNAWAY and state.may_end():
                _quit(args)
    threading.Thread(target=watch, name="city-builder-idle", daemon=True).start()
    while True:
        try:
            conn = listener.accept()
        except Exception:  # noqa: BLE001 - a caller with an old key, or one that left mid-hello
            continue
        threading.Thread(target=_one, args=(conn, api, state, args, limit_mb), name="city-builder-call", daemon=True).start()

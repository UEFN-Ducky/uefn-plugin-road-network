"""A stand-in for Ducky's plugin api, so the whole backend runs in a test with no Ducky and no UEFN.

Docs and files are kept in memory and go through JSON / bytes exactly like the real store, so a value read
back is a copy. Everything the plugin does to "Ducky" is written down in `seen`, in order, with the thread
it happened on: the worker tests compare it between "all in one process" and "work in the worker".
"""

from __future__ import annotations

import hashlib
import json
import threading


class Data:
    def __init__(self, log: list) -> None:
        self.docs: dict = {}
        self.blobs: dict = {}
        self._log = log
        self._lock = threading.RLock()

    def _note(self, what: str, key: str) -> None:
        self._log.append((what, key, threading.current_thread().name))

    def get(self, key, default=None, **_kw):
        with self._lock:
            raw = self.docs.get(key)
        return default if raw is None else json.loads(raw)

    def put(self, key, value, **_kw):
        raw = json.dumps(value, sort_keys=True)
        with self._lock:
            changed = self.docs.get(key) != raw
            self.docs[key] = raw
        self._note("put", key)
        return {"ok": True, "changed": changed}

    def delete(self, key):
        with self._lock:
            had = self.docs.pop(key, None) is not None
        self._note("delete", key)
        return had

    def keys(self, prefix=""):
        with self._lock:
            return sorted(k for k in self.docs if k.startswith(prefix))

    def items(self, prefix=""):
        with self._lock:
            return {k: json.loads(v) for k, v in sorted(self.docs.items()) if k.startswith(prefix)}

    def put_file(self, path, data):
        with self._lock:
            changed = self.blobs.get(path) != bytes(data)
            self.blobs[path] = bytes(data)
        self._note("put_file", path)
        return {"ok": True, "changed": changed}

    def has_file(self, path):
        with self._lock:
            return path in self.blobs

    def get_file(self, path):
        with self._lock:
            return self.blobs.get(path)

    def files(self, prefix=""):
        with self._lock:
            return [{"path": p, "sha256": hashlib.sha256(b).hexdigest(), "size": len(b)}
                    for p, b in sorted(self.blobs.items()) if p.startswith(prefix)]

    def delete_file(self, path):
        with self._lock:
            had = self.blobs.pop(path, None) is not None
        self._note("delete_file", path)
        return had


class _Search:
    def __init__(self, log: list) -> None:
        self._log = log

    def upsert(self, key, title, text="", **_kw):
        self._log.append(("search", key, threading.current_thread().name))
        return {"ok": True}


class _Changeset:
    def __init__(self, log: list) -> None:
        self._log = log

    def record(self, **kw):
        self._log.append(("record", f"{kw.get('command')}:{kw.get('ident')}", threading.current_thread().name))


class Api:
    """What `register(api)` gets. `tools`, `rpcs` and `nodes` hold what the plugin registered, by name."""

    plugin_id = "road-network"

    def __init__(self, folder: str) -> None:
        self.folder = folder
        self.seen: list = []  # (what, key, thread) for every write, record, tool call and UEFN call
        self.tools: dict = {}
        self.rpcs: dict = {}
        self.nodes: dict = {}
        self.said: list = []
        self._data = Data(self.seen)
        self.search = _Search(self.seen)
        self.changeset = _Changeset(self.seen)

    @property
    def data(self):
        return self._data

    def local_dir(self, name: str = "") -> str:
        return self.folder

    def log(self, text: str) -> None:
        self.said.append(text)

    def tool(self, **_kw):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco

    def register_panel_rpc(self, name, fn) -> None:
        self.rpcs[name] = fn

    def register_pipeline_node(self, node_type, handler=None):
        def deco(fn):
            self.nodes[node_type] = fn
            return fn
        return deco(handler) if handler is not None else deco

    def call_tool(self, name, arguments=None):
        self.seen.append(("call_tool", name, threading.current_thread().name))
        if name == "get_project_info":
            return {"content_root": "/Game/"}
        return {}

    def listener(self, command, params=None, timeout=120):
        self.seen.append(("listener", command, threading.current_thread().name))
        raise ConnectionError("UEFN listener not running")

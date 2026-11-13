"""History: undo and redo for a world.

Everything the panel or a tool changes goes through one data store. A Recorder stands in front of it. While a
step is open (one panel call, one tool call), the first time a doc or a file of a world is written or deleted,
what it was before is remembered. A step that changed something is kept; one that changed nothing leaves no
trace. Undo writes back what was there before and keeps what it replaced, so redo can put it back.

Steps live on this PC only, in the plugin's own folder: a gzipped file a step and a small index per world.
Nothing of the history is stored with the world or sent to a team (what can be made again stays on the PC).
"""

from __future__ import annotations

import base64
import contextlib
import gzip
import json
import os
import shutil
import threading
import time

KEEP = 40  # steps kept per world; the oldest go first
JOIN_S = 900  # a rebuild within this many seconds of the edit that asked for it is part of that edit's step


def world_of(name: str) -> str:
    """The world a doc key ('net.<world>...') or a file path ('world/<world>/...') belongs to, '' for any other."""
    if name.startswith("net."):
        return name.split(".")[1] if name.count(".") else ""
    if name.startswith("world/"):
        return name.split("/")[1] if name.count("/") else ""
    return ""


class Recorder:
    """The plugin's data store with a memory: every read and write passes straight through; inside `step(label)`
    the worlds' docs and files that get written or deleted are recorded as they were."""

    def __init__(self, inner, folder):
        self._in, self._folder = inner, folder  # folder(): where the steps are kept
        self._now = threading.local()
        self._lock = threading.RLock()

    def __getattr__(self, name):
        return getattr(self._in, name)

    # ------------------------------------------------------------ the four ways a world changes
    def put(self, key, value, *a, **k):
        self._doc(key)
        return self._in.put(key, value, *a, **k)

    def delete(self, key, *a, **k):
        self._doc(key)
        return self._in.delete(key, *a, **k)

    def put_file(self, path, raw, *a, **k):
        self._file(path)
        return self._in.put_file(path, raw, *a, **k)

    def delete_file(self, path, *a, **k):
        self._file(path)
        return self._in.delete_file(path, *a, **k)

    def _doc(self, key) -> None:
        st = getattr(self._now, "step", None)
        if st is not None and world_of(key) and key not in st["docs"]:
            st["docs"][key] = self._in.get(key)

    def _file(self, path) -> None:
        st = getattr(self._now, "step", None)
        if st is not None and world_of(path) and path not in st["files"]:
            st["files"][path] = self._in.get_file(path)

    # ------------------------------------------------------------ steps
    @contextlib.contextmanager
    def step(self, label: str, opens: bool = False, joins: bool = False):
        """One thing the person (or a tool) did. A step opened inside another belongs to the outer one.
        opens: an edit that the world is rebuilt for afterwards (a city moved, resized, added, removed);
        joins: that rebuild. It becomes part of the edit's step, so one undo takes back both: the moved city and
        the streets and buildings that were made for it."""
        if getattr(self._now, "step", None) is not None:
            yield
            return
        st = self._now.step = {"label": str(label), "docs": {}, "files": {}, "opens": bool(opens), "joins": bool(joins)}
        try:
            yield
        finally:
            self._now.step = None
            try:
                self._keep(st)
            except Exception:  # history never breaks the change it was recording
                pass

    def _keep(self, st: dict) -> None:
        docs = {k: v for k, v in st["docs"].items() if self._in.get(k) != v}
        files = {p: v for p, v in st["files"].items() if self._in.get_file(p) != v}
        if not docs and not files:
            return
        by_world: dict = {}
        for name in list(docs) + list(files):
            by_world.setdefault(world_of(name), [{}, {}])
        for k, v in docs.items():
            by_world[world_of(k)][0][k] = v
        for p, v in files.items():
            by_world[world_of(p)][1][p] = v
        with self._lock:
            for nid, (d, f) in by_world.items():
                idx = self._index(nid)
                # a rebuild joins the edits that were waiting for it (several made in a row are rebuilt once):
                # they become one step. The oldest "before" of each doc stays; the rebuild adds what only it changed.
                waiting = []
                while st["joins"] and not idx["redo"] and len(waiting) < len(idx["undo"]):
                    ent = idx["undo"][-1 - len(waiting)]
                    if not ent.get("open") or time.time() - ent["at"] > JOIN_S:
                        break
                    waiting.append(ent)
                loaded = [self._load(nid, ent["id"]) for ent in waiting]
                if waiting and None not in loaded:
                    docs_, files_ = dict(d), dict(f)
                    for got in loaded:  # newest first: an older one's "before" overrides
                        docs_.update(got[0])
                        files_.update(got[1])
                    first = waiting[-1]
                    self._save(nid, first["id"], docs_, files_)
                    names = list(dict.fromkeys(ent["label"] for ent in reversed(waiting)))
                    first.update(open=False, docs=len(docs_), files=len(files_),
                                 label=", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else ""))
                    for ent in waiting[:-1]:
                        self._drop(nid, ent["id"])
                    del idx["undo"][len(idx["undo"]) - len(waiting) + 1:]
                    self._write_index(nid, idx)
                    continue
                sid = f"{int(time.time() * 1000)}-{os.getpid()}-{len(idx['undo'])}"
                self._save(nid, sid, d, f)
                idx["undo"].append({"id": sid, "label": st["label"], "at": round(time.time()), "docs": len(d), "files": len(f),
                                    "open": st["opens"]})
                for old in idx["redo"]:  # a new change: what was undone can no longer be put back
                    self._drop(nid, old["id"])
                idx["redo"] = []
                while len(idx["undo"]) > KEEP:
                    self._drop(nid, idx["undo"].pop(0)["id"])
                self._write_index(nid, idx)

    def steps(self, nid: str) -> dict:
        """{'undo': [...], 'redo': [...]}: each {'id', 'label', 'at'}; the newest undo step and the next redo step last."""
        with self._lock:
            idx = self._index(nid)
        return {"undo": idx["undo"], "redo": idx["redo"]}

    def undo(self, nid: str, count: int = 1) -> list:
        """Take back the last `count` steps. Returns their labels, newest first ([] when there was nothing)."""
        return self._move(nid, "undo", "redo", count)

    def redo(self, nid: str, count: int = 1) -> list:
        return self._move(nid, "redo", "undo", count)

    def _move(self, nid: str, src: str, dst: str, count: int) -> list:
        done = []
        with self._lock:
            idx = self._index(nid)
            for _ in range(max(1, int(count))):
                if not idx[src]:
                    break
                ent = idx[src].pop()
                got = self._load(nid, ent["id"])
                if got is None:  # its file is gone: nothing to put back, and nothing older can be trusted either
                    idx[src] = []
                    break
                self._save(nid, ent["id"], *self._apply(*got))  # the file now holds what the other direction needs
                idx[dst].append(ent)
                done.append(ent["label"])
            self._write_index(nid, idx)
        return done

    def _apply(self, docs: dict, files: dict) -> tuple:
        """Write these docs and files back (None: it did not exist). Returns what they replaced."""
        was_d, was_f = {}, {}
        for path, raw in files.items():  # files first: a city's or an actor's head doc names its file's hash
            was_f[path] = self._in.get_file(path)
            if raw is None:
                self._in.delete_file(path)
            else:
                self._in.put_file(path, raw)
        for key, val in docs.items():
            was_d[key] = self._in.get(key)
            if val is None:
                self._in.delete(key)
            else:
                self._in.put(key, val)
        return was_d, was_f

    def forget(self, nid: str) -> None:
        """A world that is deleted takes its history with it."""
        with self._lock:
            shutil.rmtree(os.path.join(self._folder(), nid), ignore_errors=True)

    # ------------------------------------------------------------ on disk
    def _dir(self, nid: str) -> str:
        d = os.path.join(self._folder(), nid)
        os.makedirs(d, exist_ok=True)
        return d

    def _index(self, nid: str) -> dict:
        try:
            with open(os.path.join(self._dir(nid), "index.json"), encoding="utf-8") as f:
                idx = json.load(f)
            return {"undo": list(idx.get("undo") or []), "redo": list(idx.get("redo") or [])}
        except (OSError, ValueError):
            return {"undo": [], "redo": []}

    def _write_index(self, nid: str, idx: dict) -> None:
        path = os.path.join(self._dir(nid), "index.json")
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(idx, f)
        os.replace(path + ".tmp", path)

    def _save(self, nid: str, sid: str, docs: dict, files: dict) -> None:
        packed = {p: None if raw is None else base64.b64encode(bytes(raw)).decode("ascii") for p, raw in files.items()}
        with gzip.open(os.path.join(self._dir(nid), sid + ".json.gz"), "wt", encoding="utf-8", compresslevel=3) as f:
            json.dump({"docs": docs, "files": packed}, f, separators=(",", ":"))

    def _load(self, nid: str, sid: str):
        try:
            with gzip.open(os.path.join(self._dir(nid), sid + ".json.gz"), "rt", encoding="utf-8") as f:
                got = json.load(f)
        except (OSError, ValueError, EOFError):
            return None
        return got.get("docs") or {}, {p: None if raw is None else base64.b64decode(raw) for p, raw in (got.get("files") or {}).items()}

    def _drop(self, nid: str, sid: str) -> None:
        try:
            os.remove(os.path.join(self._dir(nid), sid + ".json.gz"))
        except OSError:
            pass


def _demo() -> None:
    import tempfile

    class Store:
        def __init__(self):
            self.rows, self.blobs = {}, {}

        def get(self, key, default=None):
            return json.loads(self.rows[key]) if key in self.rows else default

        def put(self, key, value):
            self.rows[key] = json.dumps(value, sort_keys=True)

        def delete(self, key):
            return self.rows.pop(key, None) is not None

        def get_file(self, path):
            return self.blobs.get(path)

        def put_file(self, path, raw):
            self.blobs[path] = bytes(raw)

        def delete_file(self, path):
            return self.blobs.pop(path, None) is not None

        def keys(self, prefix=""):
            return [k for k in self.rows if k.startswith(prefix)]

    with tempfile.TemporaryDirectory() as tmp:
        store = Store()
        data = Recorder(store, lambda: tmp)
        data.put("net.valley", {"name": "Valley"})  # outside a step: stored, not recorded
        assert data.steps("valley") == {"undo": [], "redo": []} and data.keys("net.") == ["net.valley"]
        with data.step("Move Northgate"):
            data.put("net.valley.city.northgate", {"x": 1})
            data.put_file("world/valley/city/northgate.json", b"lots one")
            with data.step("inner"):  # (part of the outer step)
                data.put("net.valley", {"name": "Valley", "updated": 1})
            data.put("preset.mine", {"layers": []})  # not a world's: never recorded
        with data.step("Looked at it"):
            data.put("net.valley", {"name": "Valley", "updated": 1})  # the same as stored: no step
        with data.step("Move Northgate again"):
            data.put("net.valley.city.northgate", {"x": 2})
            data.put_file("world/valley/city/northgate.json", b"lots two")
        assert [s["label"] for s in data.steps("valley")["undo"]] == ["Move Northgate", "Move Northgate again"]
        assert data.undo("valley") == ["Move Northgate again"]
        assert store.get("net.valley.city.northgate") == {"x": 1} and store.blobs["world/valley/city/northgate.json"] == b"lots one"
        assert data.undo("valley") == ["Move Northgate"]
        assert store.get("net.valley.city.northgate") is None and "world/valley/city/northgate.json" not in store.blobs
        assert store.get("net.valley") == {"name": "Valley"} and store.get("preset.mine") == {"layers": []}
        assert data.undo("valley") == [] and [s["label"] for s in data.steps("valley")["redo"]] == ["Move Northgate again", "Move Northgate"]
        assert data.redo("valley", 2) == ["Move Northgate", "Move Northgate again"]
        assert store.get("net.valley.city.northgate") == {"x": 2} and store.blobs["world/valley/city/northgate.json"] == b"lots two"
        assert data.undo("valley") == ["Move Northgate again"]
        with data.step("Delete Northgate"):  # a new change after an undo: nothing is left to redo
            data.delete("net.valley.city.northgate")
            data.delete_file("world/valley/city/northgate.json")
        assert data.steps("valley")["redo"] == [] and len(data.steps("valley")["undo"]) == 2
        assert data.undo("valley") == ["Delete Northgate"] and store.get("net.valley.city.northgate") == {"x": 1}
        try:  # a step that fails half way still records what it changed
            with data.step("Broke"):
                data.put("net.valley.city.northgate", {"x": 9})
                raise ValueError("stop")
        except ValueError:
            pass
        assert data.undo("valley") == ["Broke"] and store.get("net.valley.city.northgate") == {"x": 1}
        # an edit and the rebuild that follows it are one step
        with data.step("Resize Northgate", opens=True):
            data.put("net.valley.city.northgate", {"x": 1, "radius": 9})
        with data.step("Regenerate the world", joins=True):
            data.put("net.valley.city.northgate", {"x": 1, "radius": 9, "lots": 40})
            data.put_file("world/valley/roads.json", b"new roads")
        assert [s["label"] for s in data.steps("valley")["undo"]] == ["Move Northgate", "Resize Northgate"]
        with data.step("Turn Northgate", opens=True):  # two edits in a row, rebuilt once: one step, both names
            data.put("net.valley.city.northgate", {"x": 1, "radius": 9, "lots": 40, "turn": 30})
        with data.step("Move Kingsport", opens=True):
            data.put("net.valley.city.kingsport", {"x": 5})
        with data.step("Regenerate the world", joins=True):
            data.put_file("world/valley/roads.json", b"roads after both")
        assert [s["label"] for s in data.steps("valley")["undo"]][-1] == "Turn Northgate, Move Kingsport"
        assert data.undo("valley") == ["Turn Northgate, Move Kingsport"] and store.get("net.valley.city.kingsport") is None
        assert store.get("net.valley.city.northgate") == {"x": 1, "radius": 9, "lots": 40} and store.blobs["world/valley/roads.json"] == b"new roads"
        assert data.redo("valley") == ["Turn Northgate, Move Kingsport"] and store.blobs["world/valley/roads.json"] == b"roads after both"
        assert data.undo("valley") == ["Turn Northgate, Move Kingsport"]
        with data.step("Regenerate the world", joins=True):  # a second rebuild is a step of its own
            data.put_file("world/valley/roads.json", b"newer roads")
        assert [s["label"] for s in data.steps("valley")["undo"]][-2:] == ["Resize Northgate", "Regenerate the world"]
        assert data.undo("valley", 2) == ["Regenerate the world", "Resize Northgate"]
        assert store.get("net.valley.city.northgate") == {"x": 1} and "world/valley/roads.json" not in store.blobs
        other = Recorder(store, lambda: tmp)  # another process (agent tools run in one): the same history
        assert [s["label"] for s in other.steps("valley")["undo"]] == ["Move Northgate"]
        for k in range(KEEP + 5):
            with data.step(f"step {k}"):
                data.put("net.valley", {"name": "Valley", "updated": 100 + k})
        assert len(data.steps("valley")["undo"]) == KEEP and len(os.listdir(os.path.join(tmp, "valley"))) == KEEP + 1
        data.forget("valley")
        assert data.steps("valley") == {"undo": [], "redo": []}
    print("history ok")


if __name__ == "__main__":
    _demo()

"""Projects and road networks (worlds): list, save, delete, pull from UEFN, stats.

Handlers moved out of backend/__init__.py register(): ctx carries what the parts share."""

from __future__ import annotations

import hashlib
import json
import threading
import time



def register(ctx) -> None:
    data, push, record, slug = ctx.take("data push record slug")

    # ---------------------------------------------------------------- projects
    def list_projects() -> dict:
        items = [{"id": k.split(".", 1)[1], **v} for k, v in data.items("project.").items()]
        return {"ok": True, "items": sorted(items, key=lambda r: r.get("name", "")), "count": len(items)}

    def save_project(project_id: str = "", name: str = "", description: str = "") -> dict:
        pid = slug(project_id or name)
        if not pid:
            return {"ok": False, "error": "Give the project a name."}
        before = data.get(f"project.{pid}")
        doc = {**(before or {}), "name": name or (before or {}).get("name") or pid,
               "description": description or (before or {}).get("description", ""), "updated": time.time()}
        data.put(f"project.{pid}", doc)
        undo = ({"command": "road_network_project_save", "params": {"project_id": pid, **before}} if before
                else {"command": "road_network_project_delete", "params": {"project_id": pid}})
        record("road_network_project_save", "project", pid, before, [undo])
        push({"what": "project", "id": pid})
        return {"ok": True, "id": pid, "item": doc}

    def delete_project(project_id: str = "") -> dict:
        pid = slug(project_id)
        before = data.get(f"project.{pid}") if pid else None
        if before is None:
            return {"ok": False, "error": f"No project {project_id!r}."}
        data.delete(f"project.{pid}")
        record("road_network_project_delete", "project", pid, before,
               [{"command": "road_network_project_save", "params": {"project_id": pid, **before}}])
        push({"what": "project", "id": pid})
        return {"ok": True, "id": pid}

    # ---------------------------------------------------------------- networks
    def list_networks(project: str = "") -> dict:
        items = []
        for key, doc in data.items("net.").items():
            if key.count(".") != 1:
                continue
            if project and doc.get("project") != slug(project):
                continue
            items.append({"id": key.split(".", 1)[1], **{k: v for k, v in doc.items() if k != "history"}})
        return {"ok": True, "items": sorted(items, key=lambda r: -float(r.get("updated") or 0)), "count": len(items)}

    # A world's big data is files, one per road actor and one per city (world/<world>/...), next to small
    # head docs: one change is one updated file, and team sync sends only the parts of it that changed. A
    # rebuild of 1,000-point chunk docs sent 800+ changes, mostly deletes of old chunks (Nov 11 2026). A
    # file is a line per row, so a change touches only its own lines (and a diff reads like one). A head
    # carries its file's sha: an unchanged actor or city writes nothing at all.
    file_cache: dict = {}  # (path, sha) -> parsed actor file, the last few read (panel pages hit the same one)
    file_cache_lock = threading.Lock()
    FILE_CACHE_MAX = 8

    def actor_path(nid: str, aslug: str) -> str:
        return f"world/{nid}/{aslug}.json"

    def city_path(nid: str, cslug: str) -> str:
        return f"world/{nid}/city/{cslug}.json"

    def canon(obj: dict) -> bytes:
        """``obj`` as JSON with every list item on a line of its own (a dict of lists nests the same way)."""
        dump = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":"))
        out = []
        for k, v in obj.items():
            if isinstance(v, list):
                out.append(f"{json.dumps(k)}:[\n" + ",\n".join(dump(x) for x in v) + "\n]")
            elif isinstance(v, dict) and v and all(isinstance(x, list) for x in v.values()):
                out.append(f"{json.dumps(k)}:" + canon(v).decode("utf-8").rstrip("\n"))
            else:
                out.append(f"{json.dumps(k)}:{dump(v)}")
        return ("{\n" + ",\n".join(out) + "\n}\n").encode("utf-8")

    def put_json_file(path: str, obj: dict) -> str:
        raw = canon(obj)
        data.put_file(path, raw)  # the same bytes as stored: no write, nothing to send
        return hashlib.sha256(raw).hexdigest()

    def read_json_file(path: str, cache: bool = False) -> dict:
        """A world file parsed ({} when it isn't here: not written yet, or still on its way from a teammate).
        ``cache``: the parsed object is shared, by its stored sha (callers never change it)."""
        sha = next((r["sha256"] for r in data.files(path) if r["path"] == path), "") if path else ""
        if not sha:
            return {}
        if cache:
            with file_cache_lock:
                hit = file_cache.get((path, sha))
            if hit is not None:
                return hit
        raw = data.get_file(path)
        try:
            got = json.loads(raw) if raw else {}
        except ValueError:
            return {}
        if cache:
            with file_cache_lock:
                file_cache[(path, sha)] = got
                while len(file_cache) > FILE_CACHE_MAX:
                    file_cache.pop(next(iter(file_cache)))
        return got

    def drop_world_files(prefix: str) -> None:
        for f in data.files(prefix):
            data.delete_file(f["path"])

    ctx.share(locals())

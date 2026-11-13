"""Road Network: city road networks for UEFN, viewed and edited in 3D, reimported in one go.

Data (team-shared through api.data: small head docs, and a file per road actor and per city):
  project.<pid>                         {name, description}
  net.<nid>                             {name, project, actors, terrain_mode, ...}
  net.<nid>.actor.<slug>                {name, label, file, sha, points, segments, stats}
  world/<nid>/<slug>.json               file {pts: [...], segs: [...]}, a line per point / segment
  net.<nid>.scan                        {name, props, terrain}
  net.<nid>.city.<slug>                 {name, x, y, radius, style, districts, buildings, file, sha}  (generated worlds)
  world/<nid>/city/<slug>.json          file {private: {...}, lots, plan, ...}: the city's big fields
A world from before files keeps net.<nid>.actor.<slug>.p.<k> / .s.<k> chunk docs and city .private / .piece
docs until its next write.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import pickle
import re
import subprocess
import threading
import time

from . import citygen as cg
from . import holds as hd
from . import kit_bake
from . import pcg_batch
from . import pcg_layers as pl
from . import pieces
from . import pipeline
from . import ramp_joins
from . import road_profiles as rp
from . import splines_text as st
from . import uefn_input
from . import uefn_memory
from . import waterfront
from . import airport

CHUNK = 1000


def register(api) -> None:
    data = api.data
    pid_ = api.plugin_id

    # ---------------------------------------------------------------- helpers
    def slug(text: str) -> str:
        return re.sub(r"[^a-z0-9-]+", "-", str(text or "").strip().lower()).strip("-")[:60]

    def safe(fn):
        def run(*a, **k):
            try:
                return fn(*a, **k)
            except PermissionError as exc:
                return {"ok": False, "error": str(exc)}
            except (KeyError, ValueError, TypeError, IndexError) as exc:
                # never a bare traceback: what failed, the plugin line it failed on, and where the names that do
                # exist are listed
                import traceback
                own = [f for f in traceback.extract_tb(exc.__traceback__) if "road-network" in f.filename
                       or "road_network" in f.filename or "backend" in f.filename]
                at = f" (at {os.path.basename(own[-1].filename)}:{own[-1].lineno} in {own[-1].name})" if own else ""
                return {"ok": False, "error": f"{fn.__name__}: {type(exc).__name__}: {exc}{at}",
                        "fix": "Check the arguments against road_network_describe(net_id): it lists the worlds, "
                               "cities, layers, roles, forms, build modes and stages that exist. Ids are "
                               "lower-case slugs; layer and options arguments are JSON strings."}
        run.__name__ = fn.__name__
        run.__doc__ = fn.__doc__
        return run

    def one_run_at_a_time(fn):
        """Full-city work runs once at a time. A caller that waited gets the answer of a run that started after
        it asked (fresh), so a pile of the same request costs at most two runs. The panel asks on every open
        and reload: these minutes-long runs side by side took Ducky to 13 GB (Nov 11 2026)."""
        lock, last = threading.RLock(), {}

        def run(*a, **k):
            key, asked = json.dumps([a, k], sort_keys=True, default=str), time.perf_counter()
            with lock:
                hit = last.get(key)
                if hit and hit[0] > asked:
                    return hit[1]
                started = time.perf_counter()
                out = fn(*a, **k)
                last.clear()  # ponytail: only the newest answer is kept
                last[key] = (started, out)
                return out
        run.__name__ = fn.__name__
        run.__doc__ = fn.__doc__
        return run

    world_lock = threading.RLock()

    def holds_the_world(fn):
        """A rebuild holds the world for its whole run and a city edit (add, move, resize, delete) waits for it: a
        rebuild writes back the cities it started with, so an edit made during one was undone (Nov 11 2026)."""
        def run(*a, **k):
            with world_lock:
                return fn(*a, **k)
        run.__name__ = fn.__name__
        run.__doc__ = fn.__doc__
        return run

    # ---------------------------------------------------------------- holds: who is working on which city
    # (holds.py: in memory on this PC, nothing stored or synced). A person's pick in the panel, a ducky's change,
    # and what teammates hold as the panel hears it from the live group. Every change to a city asks city_guard.
    held_cities = hd.Holds()
    held_lock = threading.Lock()  # (their own: a pick is taken and kept while a rebuild holds the world)
    me_cache = {"at": -1e9, "name": "", "user": ""}

    def actor() -> dict:
        """Who is calling: the signed-in person, or their ducky (a ducky's call is the ducky's, never the person's)."""
        if time.monotonic() - me_cache["at"] > 30:
            try:
                from frontend.duckyos_account import get_status
                got = get_status()
                me_cache.update(name=str(got.get("display_name") or ""), user=str(got.get("user_id") or ""))
            except Exception:  # signed out, or run outside the app: holds still work on this PC
                pass
            me_cache["at"] = time.monotonic()
        try:
            from backend.uefn_plugins.scopes import caller
            kind, ducky = caller()
        except Exception:
            kind, ducky = "user", ""
        if kind == "agent":
            return {"kind": "ducky", "name": ducky, "for": me_cache["name"] or "You", "user": me_cache["user"]}
        return {"kind": "person", "name": me_cache["name"] or "You", "user": me_cache["user"]}

    def team_live() -> bool:
        """Is this PC live with teammates on this plugin's team copy right now?"""
        try:
            from backend.uefn_plugins import live as host_live
            return bool(host_live.is_live(pid_))
        except Exception:
            return False

    NO_PANEL = ("Your team is live on this world and City Builder is not open on this PC, so there is no telling "
                "which cities teammates are working on. Open City Builder here (or leave Live), then try again. "
                "Nothing was changed.")

    def city_guard(nid: str, cslug: str, name: str, what: str) -> dict | None:
        """Before a city changes: the refusal when someone else has it, else None. A ducky that goes ahead holds
        the city while it works (every screen shows whose ducky has it)."""
        who = actor()
        with held_lock:
            if who["kind"] == "ducky" and team_live() and not held_cities.panel_open(nid):
                return {"ok": False, "error": NO_PANEL}
            held = held_cities.blocks(nid, cslug, who)
            if held:
                return {"ok": False, "error": hd.refusal(held, name, held_cities.clock()), "held_by": hd.label(held["by"])}
            if who["kind"] == "ducky":
                held_cities.take(nid, cslug, who, what)
        if who["kind"] == "ducky":
            push({"what": "hold", "id": nid})
        return None

    def hold(net_id: str = "", city: str = "", task: str = "", minutes: float = 0.0) -> dict:
        nid, cslug = slug(net_id), slug(city)
        doc = data.get(f"net.{nid}.city.{cslug}") if nid and cslug else None
        if not doc:
            return {"ok": False, "error": f"No city {city!r} in {net_id!r}. List them with road_network_cities."}
        who = actor()
        with held_lock:
            if who["kind"] == "ducky" and team_live() and not held_cities.panel_open(nid):
                return {"ok": False, "error": NO_PANEL}
            got = held_cities.take(nid, cslug, who, task, minutes, doc.get("name") or city)
        if got.get("ok") and (who["kind"] == "ducky" or got.get("new")):
            push({"what": "hold", "id": nid})
        return got

    def release(net_id: str = "", city: str = "") -> dict:
        nid, cslug = slug(net_id), slug(city)
        who = actor()
        with held_lock:
            got = held_cities.release(nid, cslug, who)
        if got.get("released"):
            push({"what": "hold", "id": nid})
        return got

    def holds(net_id: str = "", teammates: str | None = None) -> dict:
        """Who holds which city. teammates (the panel only, never a tool): its word on what teammates hold right
        now, from the live group ([{city, who}], the whole list every time); a ducky can't say it, so a ducky
        can't clear a teammate's hold."""
        nid, who = slug(net_id), actor()
        with held_lock:
            if teammates is not None and who["kind"] == "person":
                try:
                    rows = json.loads(teammates) if str(teammates).strip() else []
                except ValueError:
                    rows = []
                held_cities.said(nid, rows if isinstance(rows, list) else [])
            return {"ok": True, "id": nid, "holds": held_cities.all(nid, who), "live": team_live(),
                    "panel_open": held_cities.panel_open(nid)}

    def record(command: str, kind: str, ident: str, before, inverse: list) -> None:
        api.changeset.record(command=command, kind=kind, ident=ident, facet="record", before=before,
                             inverse=inverse, slot=f"{pid_}://{kind}/{ident}/record")

    def push(event: dict) -> None:
        try:
            from frontend.ui_web.verse_editor.panel_events import push_agent_event
            push_agent_event({"type": "road_network_changed", **event})
        except Exception:
            pass

    def listener(command: str, params: dict | None = None, timeout: float = 120):
        # every editor call is metered: UEFN's memory before/after, refused when the last call blew up
        return uefn_memory.metered(command, lambda: api.listener(command, params or {}, timeout=timeout))

    def listener_long(command: str, params: dict | None = None, timeout: float = 300) -> dict:
        """A slow editor call (a mesh build, a heavy slice). Ducky's bridge stops waiting after ~30 s but UEFN
        finishes the call anyway; then wait for UEFN to answer again (it runs one command at a time) instead of
        failing the whole job. Returns {"ok": True, "late": True} when the reply itself was lost."""
        try:
            return listener(command, params, timeout=timeout)
        except Exception as err:  # noqa: BLE001 - the bridge reports a lost reply in several wordings
            text = str(err).lower()
            if not any(w in text for w in ("timed out", "timeout", "not running")):
                raise
        end = time.time() + timeout
        while time.time() < end:
            time.sleep(5)
            try:
                api.listener("road_network_count", {"label": ""}, timeout=20)  # answers once UEFN is free
                return {"ok": True, "late": True}
            except Exception:  # noqa: BLE001 - still busy
                continue
        raise TimeoutError(f"UEFN is still busy with {command} after {round(timeout)} s.")

    def epic(toolset: str, tool: str, arguments: dict, timeout: float = 120):
        # Same route as Ducky's own Epic callers: the pool keeps the nested arguments intact
        # (api.call_tool's dispatcher drops them for unreal__call_tool).
        from backend.mcp_plugins.client_pool import get_plugin_pool
        pool = get_plugin_pool()
        out = uefn_memory.metered(f"{toolset.rsplit('.', 1)[-1]}.{tool}", lambda: pool.run_sync(pool.call_tool(
            "unreal__call_tool", {"toolset_name": toolset, "tool_name": tool, "arguments": arguments}), timeout=timeout))
        if not isinstance(out, (str, dict)):
            out = getattr(out, "text", None) or str(out)
        if isinstance(out, str):
            try:
                out = json.loads(out)
            except ValueError:
                raise ValueError(out[:300]) from None
        if isinstance(out, dict) and out.get("ok") is False:
            raise ValueError(str(out.get("error") or out)[:300])
        return out

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

    def actor_docs(nid: str) -> dict:
        # the heads only: items() under this prefix would read and decode every point and segment chunk too
        heads = [k for k in data.keys(f"net.{nid}.actor.") if k.count(".") == 3]
        return {k.rsplit(".", 1)[1]: v for k in heads if (v := data.get(k)) is not None}

    def load_actor(nid: str, aslug: str) -> tuple:
        head = data.get(f"net.{nid}.actor.{aslug}") if nid and aslug else None
        if not head:
            return None, [], []
        if head.get("file"):  # copies: the parsed file is shared
            got = read_json_file(head["file"], cache=True)
            return head, [dict(p) for p in got.get("pts") or []], [dict(s) for s in got.get("segs") or []]
        pts, segs = [], []  # a world from before files: its point and segment chunk docs
        for k in range(int(head.get("p_chunks") or 0)):
            pts += (data.get(f"net.{nid}.actor.{aslug}.p.{k}") or {}).get("pts", [])
        for k in range(int(head.get("s_chunks") or 0)):
            segs += (data.get(f"net.{nid}.actor.{aslug}.s.{k}") or {}).get("segs", [])
        return head, pts, segs

    def save_actor(nid: str, aslug: str, label: str, name: str, pts: list, segs: list) -> dict:
        """An actor's file and head; an older world's chunk docs of it go (once). Unchanged: nothing written."""
        key = f"net.{nid}.actor.{aslug}"
        old = data.get(key) or {}
        path = actor_path(nid, aslug)
        sha = put_json_file(path, {"pts": pts, "segs": segs})
        for k in range(int(old.get("p_chunks") or 0)):
            data.delete(f"{key}.p.{k}")
        for k in range(int(old.get("s_chunks") or 0)):
            data.delete(f"{key}.s.{k}")
        if old.get("file") == path and old.get("sha") == sha and old.get("name") == name:
            return old
        head = {"name": name, "label": label, "file": path, "sha": sha, "points": len(pts), "segments": len(segs),
                "stats": st.stats(pts, segs), "updated": time.time()}
        data.put(key, head)
        return head

    def store_actor(nid: str, label: str, parsed: dict, net_name: str) -> dict:
        return save_actor(nid, slug(label), label, f"{net_name} / {label}", parsed["points"], parsed["segments"])

    def drop_actor(nid: str, aslug: str) -> None:
        head = f"net.{nid}.actor.{aslug}"
        for key in [k for k in data.keys(head) if k == head or k.startswith(head + ".")]:
            data.delete(key)
        data.delete_file(actor_path(nid, aslug))

    def get_network(net_id: str = "") -> dict:
        nid = slug(net_id)
        doc = data.get(f"net.{nid}") if nid else None
        if not doc:
            return {"ok": False, "error": f"No road network {net_id!r}. List them with road_network_list."}
        actors = {k: {"label": v.get("label"), "stats": v.get("stats")} for k, v in actor_docs(nid).items()}
        return {"ok": True, "id": nid, "item": doc, "actors": actors}

    def save_network(net_id: str = "", name: str = "", project: str = "", terrain_mode: str = "") -> dict:
        nid = slug(net_id or name)
        if not nid:
            return {"ok": False, "error": "Give the road network a name."}
        if terrain_mode and terrain_mode not in ("live", "layer"):
            return {"ok": False, "error": "terrain_mode is 'live' (splines shape the ground) or 'layer' (beds baked into the base Layer)."}
        before = data.get(f"net.{nid}")
        doc = {**(before or {}), "name": name or (before or {}).get("name") or nid,
               "project": slug(project) or (before or {}).get("project", ""),
               "terrain_mode": terrain_mode or (before or {}).get("terrain_mode", "layer"), "updated": time.time()}
        doc.setdefault("created", time.time())
        data.put(f"net.{nid}", doc)
        undo = ({"command": "road_network_save", "params": {"net_id": nid, "name": before.get("name", ""),
                 "project": before.get("project", ""), "terrain_mode": before.get("terrain_mode", "")}} if before
                else {"command": "road_network_delete", "params": {"net_id": nid}})
        record("road_network_save", "network", nid, before, [undo])
        push({"what": "network", "id": nid})
        return {"ok": True, "id": nid, "item": doc}

    def delete_network(net_id: str = "") -> dict:
        nid = slug(net_id)
        before = data.get(f"net.{nid}") if nid else None
        if before is None:
            return {"ok": False, "error": f"No road network {net_id!r}."}
        for key in list(data.keys(f"net.{nid}.")):
            data.delete(key)
        drop_world_files(f"world/{nid}/")
        data.delete(f"net.{nid}")
        record("road_network_delete", "network", nid, before, [])
        push({"what": "network", "id": nid})
        return {"ok": True, "id": nid, "note": "Pull it from UEFN again to get it back."}

    def import_text(net_id: str = "", label: str = "", text: str = "") -> dict:
        nid = slug(net_id)
        doc = data.get(f"net.{nid}") if nid else None
        if not doc:
            return {"ok": False, "error": "Create the network first (road_network_save)."}
        if not label or not str(text).startswith("BEGIN_SPLINES"):
            return {"ok": False, "error": "Give the actor label and spline text that starts with BEGIN_SPLINES (Splines tool, Ctrl+C)."}
        head = store_actor(nid, label, st.parse(text), doc["name"])
        doc["actors"] = sorted(set((doc.get("actors") or []) + [label]))
        doc["updated"] = time.time()
        data.put(f"net.{nid}", doc)
        push({"what": "actor", "id": nid, "label": label})
        return {"ok": True, "id": nid, "label": label, "stats": head["stats"]}

    def pull(net_id: str = "", name: str = "", project: str = "", actors: str = "") -> dict:
        nid = slug(net_id or name)
        if not nid:
            return {"ok": False, "error": "Give the network a name (for example 'City')."}
        try:
            listing = listener("road_network_actors")
        except (ConnectionError, RuntimeError, TimeoutError) as exc:
            return {"ok": False, "error": f"UEFN didn't answer: {exc}"}
        wanted = {a.strip() for a in (actors or "").split(",") if a.strip()}
        saved = save_network(nid, name or nid, project)
        if not saved.get("ok"):
            return saved
        doc = data.get(f"net.{nid}")
        done, skipped = {}, []
        for row in listing.get("actors", []):
            label = row.get("label", "")
            if wanted and label not in wanted:
                continue
            if not row.get("has_text"):
                skipped.append(label)
                continue
            text, offset, total = "", 0, 1
            while offset < total:
                part = listener("road_network_text_chunk", {"label": label, "offset": offset})
                if not part.get("ok"):
                    break
                text += part["text"]
                total = int(part["total"])
                offset += len(part["text"]) or total
            if text.startswith("BEGIN_SPLINES"):
                head = store_actor(nid, label, st.parse(text), doc["name"])
                done[label] = head["stats"]
        try:
            scan = listener("road_network_scan", {"grid": 200}, timeout=600)
            data.put(f"net.{nid}.scan", {"name": f"{doc['name']} / terrain and props",
                                          "props": scan.get("props", []), "terrain": scan.get("terrain", {})})
        except (ConnectionError, RuntimeError, TimeoutError):
            pass
        doc["actors"] = sorted(set((doc.get("actors") or []) + list(done)))
        doc["updated"] = time.time()
        data.put(f"net.{nid}", doc)
        api.search.upsert(f"net.{nid}", doc["name"], f"Road network {doc['name']}: " + ", ".join(doc["actors"]))
        push({"what": "network", "id": nid})
        return {"ok": True, "id": nid, "actors": done,
                "skipped_no_text": skipped, "note": "Actors without known text: copy them in the Splines tool (Ctrl+C) and use road_network_import_text." if skipped else ""}

    def stats(net_id: str = "") -> dict:
        got = get_network(net_id)
        if not got.get("ok"):
            return got
        total: dict = {"segment_meshes": {}, "point_meshes": {}, "road_length_m": 0, "points": 0, "segments": 0}
        for a in got["actors"].values():
            s = a.get("stats") or {}
            for key in ("road_length_m", "points", "segments"):
                total[key] += s.get(key, 0)
            for key in ("segment_meshes", "point_meshes"):
                for mesh, n in (s.get(key) or {}).items():
                    total[key][mesh] = total[key].get(mesh, 0) + n
        return {"ok": True, "id": got["id"], "total": total, "actors": got["actors"]}

    def actor_page(nid: str, aslug: str, head: dict, lo: int, hi: int) -> tuple | None:
        """(points, segments, points total, segments total) for [lo, hi) of one actor, reading only the chunks
        that hold it and the last one (for the totals). Every chunk but the last holds CHUNK items (store_actor
        and move_point write them that way); None for any other shape: then the caller reads the actor whole.
        An actor in a file: its slice of the (cached) file."""
        if head.get("file"):
            whole = read_json_file(head["file"], cache=True)
            pts, segs = whole.get("pts") or [], whole.get("segs") or []
            return pts[lo:hi], segs[lo:hi], len(pts), len(segs)
        got = []
        for kind, field, count in (("p", "pts", "p_chunks"), ("s", "segs", "s_chunks")):
            n = int(head.get(count) or 0)
            if n <= 0:
                got += [[], 0]
                continue
            first = min(lo // CHUNK, n - 1)
            last = max(first, min(n - 1, (hi - 1) // CHUNK))
            chunks = {k: (data.get(f"net.{nid}.actor.{aslug}.{kind}.{k}") or {}).get(field, [])
                      for k in {*range(first, last + 1), n - 1}}
            if any(len(items) != CHUNK for k, items in chunks.items() if k != n - 1):
                return None
            rows = [x for k in range(first, last + 1) for x in chunks[k]]
            got += [rows[lo - first * CHUNK:hi - first * CHUNK], (n - 1) * CHUNK + len(chunks[n - 1])]
        pts, n_pts, segs, n_segs = got
        return pts, segs, n_pts, n_segs

    def geometry(net_id: str = "", actor: str = "", offset: int = 0, limit: int = 0, terrain: bool = True) -> dict:
        """Everything the 3D view draws for one actor (or all), plus terrain and props.
        limit > 0 pages: points and segments [offset, offset + limit) of one actor, or of several ("a,b,c": the
        panel asks for small actors together), with their totals. A page reads only its own chunks (the panel
        loads a world in pages: every page reading every chunk of the actor made loading quadratic)."""
        lo, hi = max(0, int(offset)), (max(0, int(offset)) + int(limit)) if int(limit) > 0 else None
        row = lambda p: [p["i"], round(p["x"]), round(p["y"]), round(p["z"]), round(p["pitch"], 2),
                         round(p["yaw"], 2), round(p["w"]), p["mesh"], 1 if p["raise"] else 0]
        seg = lambda s: [s["i"], s["a"], s["b"], round(s["ta"]), round(s["tb"]), s["mesh"]]
        nid = slug(net_id)
        if hi is not None and actor and actor != "-" and data.get(f"net.{nid}"):
            out = {}
            for aslug in dict.fromkeys(slug(a) for a in str(actor).split(",")):
                head = data.get(f"net.{nid}.actor.{aslug}")
                if not head:
                    continue
                page = actor_page(nid, aslug, head, lo, hi)
                if page is None:
                    _, pts, segs = load_actor(nid, aslug)
                    page = (pts[lo:hi], segs[lo:hi], len(pts), len(segs))
                pts, segs, n_pts, n_segs = page
                out[aslug] = {"label": head.get("label"), "points_total": n_pts, "segments_total": n_segs,
                              "pts": [row(p) for p in pts], "segs": [seg(s) for s in segs]}
            scan = (data.get(f"net.{nid}.scan") or {}) if terrain else {}
            return {"ok": True, "id": nid, "actors": out, "terrain": scan.get("terrain", {}), "props": scan.get("props", [])}
        got = get_network(net_id)
        if not got.get("ok"):
            return got
        nid = got["id"]
        out = {}
        for aslug, head in actor_docs(nid).items():
            if actor and aslug != slug(actor) and head.get("label") != actor:
                continue
            _, pts, segs = load_actor(nid, aslug)
            out[aslug] = {"label": head.get("label"), "points_total": len(pts), "segments_total": len(segs),
                          "pts": [row(p) for p in pts[lo:hi]], "segs": [seg(s) for s in segs[lo:hi]]}
        scan = (data.get(f"net.{nid}.scan") or {}) if terrain else {}
        # every road piece's cross-section: the panel draws each road as its real mesh is built
        return {"ok": True, "id": nid, "actors": out, "terrain": scan.get("terrain", {}), "props": scan.get("props", []),
                "profiles": rp.all_profiles(), "joins": road_joins(nid) if terrain else None}

    _JOINS: dict = {}  # ponytail: one entry, the world being looked at

    def road_joins(nid: str) -> dict:
        """Every ramp end's merge lane or street mouth (ramp_joins), worked out again only when a road changed."""
        docs = {a: h for a, h in actor_docs(nid).items() if " before kit" not in str(h.get("label") or "")}
        heads = {a: h for a, h in docs.items() if not str(h.get("label") or "").endswith((" Paths", " Access"))}
        private = [a for a, h in docs.items() if str(h.get("label") or "").endswith(" Access")]  # roads too, only narrower
        key = (nid, tuple(sorted((a, h.get("sha")) for a, h in docs.items())))
        if _JOINS.get("key") != key:
            _JOINS.update(key=key, out=ramp_joins.joins({a: load_actor(nid, a)[1:] for a in heads},
                                                        {a: load_actor(nid, a)[1:] for a in private}))
        return _JOINS["out"]

    def move_point(net_id: str = "", actor: str = "", index: int = -1, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> dict:
        nid, aslug = slug(net_id), slug(actor)
        head, pts, segs = load_actor(nid, aslug)
        if not head:
            return {"ok": False, "error": f"No actor {actor!r} in network {net_id!r}."}
        before = next((dict(p) for p in pts if p["i"] == int(index)), None)
        if before is None:
            return {"ok": False, "error": f"No point {index} in {actor}."}
        st.move_point(pts, segs, int(index), x, y, z)
        # the actor's file again: team sync sends only the lines (parts) that changed
        save_actor(nid, aslug, head.get("label") or aslug, head.get("name") or aslug, pts, segs)
        record("road_network_move_point", "point", f"{nid}-{aslug}-{index}", before,
               [{"command": "road_network_move_point", "params": {"net_id": nid, "actor": aslug, "index": int(index),
                                                                  "x": before["x"], "y": before["y"], "z": before["z"]}}])
        push({"what": "point", "id": nid, "actor": aslug, "index": int(index)})
        return {"ok": True, "id": nid, "actor": aslug, "index": int(index), "x": x, "y": y, "z": z}

    def text_for(net_id: str, actor: str, terrain: str = "") -> tuple:
        nid, aslug = slug(net_id), slug(actor)
        head, pts, segs = load_actor(nid, aslug)
        if not head:
            return None, None
        return head, st.serialize(pts, segs, terrain=terrain)

    def copy_text(net_id: str = "", actor: str = "") -> dict:
        head, text = text_for(net_id, actor)
        if not head:
            return {"ok": False, "error": f"No actor {actor!r} in network {net_id!r}."}
        subprocess.run(["clip"], input=text.encode("utf-16le"), creationflags=0x08000000, check=False)
        return {"ok": True, "label": head["label"], "chars": len(text),
                "note": "On the clipboard. In UEFN: Landscape mode > Manage > Splines, hover the viewport, Ctrl+V."}

    def drape(net_id: str = "", actor: str = "", smooth: int = 4, all_points: bool = False) -> dict:
        """Set the actor's live road points (or all points) onto the base ground (Splines layer hidden while sampling)."""
        nid, aslug = slug(net_id), slug(actor)
        head, pts, segs = load_actor(nid, aslug)
        if not head:
            return {"ok": False, "error": f"No actor {actor!r} in network {net_id!r}."}
        live = [p for p in pts if p["raise"] or all_points]
        if not live:
            return {"ok": False, "error": f"{head['label']} has no live (Raise/Lower) points to drape."}
        # Offline: the ground comes from the network's stored terrain scan, never from UEFN.
        terrain = (data.get(f"net.{nid}.scan") or {}).get("terrain") or {}
        ground = {}
        for p in live:
            g = st.sample_grid(terrain, p["x"], p["y"])
            if g is not None:
                ground[p["i"]] = g
        if not ground:
            return {"ok": False, "error": "This network has no terrain scan yet. Rescan the ground "
                                          "(road_network_scan_ground) while only the terrain is in the level."}
        before = [dict(p) for p in pts]
        result = st.drape(pts, segs, ground, iters=max(0, min(20, int(smooth))), pin_joints=not all_points)
        result["bad_tangents"] = len(st.bad_tangents(pts, segs))
        store_actor(nid, head["label"], {"points": pts, "segments": segs}, head["name"].split(" / ")[0])
        record("road_network_drape", "network", f"{nid}-{aslug}", {"points": len(before)}, [])
        push({"what": "actor", "id": nid, "label": head["label"]})
        return {"ok": True, "label": head["label"], **result,
                "note": "Stored network updated. Reimport the actor to see it in UEFN."}

    def check(net_id: str = "", actor: str = "") -> dict:
        nid, aslug = slug(net_id), slug(actor)
        head, pts, segs = load_actor(nid, aslug)
        if not head:
            return {"ok": False, "error": f"No actor {actor!r} in network {net_id!r}."}
        found = st.problems(pts, segs)
        return {"ok": True, "label": head["label"], "clean": not any(found.values()),
                "counts": {k: len(v) for k, v in found.items()}, "problems": {k: v[:50] for k, v in found.items()}}

    def repair_actor(net_id: str = "", actor: str = "") -> dict:
        nid, aslug = slug(net_id), slug(actor)
        head, pts, segs = load_actor(nid, aslug)
        if not head:
            return {"ok": False, "error": f"No actor {actor!r} in network {net_id!r}."}
        result = st.repair(pts, segs)
        store_actor(nid, head["label"], {"points": pts, "segments": segs}, head["name"].split(" / ")[0])
        record("road_network_repair", "network", f"{nid}-{aslug}", None, [])
        push({"what": "actor", "id": nid, "label": head["label"]})
        return {"ok": True, "label": head["label"], **result}

    def tune_actor(net_id: str = "", actor: str = "", thin_tolerance_cm: float = 50.0, side_falloff: float = 800.0,
                   end_falloff: float = 1000.0, width: float = 0.0, paint_layer: str = "",
                   mesh_offset_cm: float = -1.0, height_tolerance_cm: float = 150.0) -> dict:
        nid, aslug = slug(net_id), slug(actor)
        head, pts, segs = load_actor(nid, aslug)
        if not head:
            return {"ok": False, "error": f"No actor {actor!r} in network {net_id!r}."}
        before = len(pts)
        # height tolerance is looser: the live spline flattens the ground to the road anyway, so a road
        # a little off the old ground only reshapes its bed; plan tolerance keeps the road where it was
        thinned = st.thin(pts, segs, tol_xy=max(5.0, float(thin_tolerance_cm)), tol_z=max(5.0, float(height_tolerance_cm)))
        tuned = st.tune(pts, segs, width=float(width) if width else None,
                        side=max(0.0, float(side_falloff)), end=max(0.0, float(end_falloff)),
                        paint_layer=str(paint_layer or "").strip(),
                        mesh_offset=float(mesh_offset_cm) if float(mesh_offset_cm) >= 0 else None)
        store_actor(nid, head["label"], {"points": pts, "segments": segs}, head["name"].split(" / ")[0])
        record("road_network_tune", "network", f"{nid}-{aslug}", {"points": before}, [])
        push({"what": "actor", "id": nid, "label": head["label"]})
        return {"ok": True, "label": head["label"], "points_before": before, **thinned, **tuned,
                "problems": {k: len(v) for k, v in st.problems(pts, segs).items()}}

    def kit_convert(net_id: str = "", actor: str = "") -> dict:
        nid, aslug = slug(net_id), slug(actor)
        head, pts, segs = load_actor(nid, aslug)
        if not head:
            return {"ok": False, "error": f"No actor {actor!r} in network {net_id!r}."}
        import copy
        net_name = head["name"].split(" / ")[0]
        backup = f"{head['label']} before kit"
        if not data.get(f"net.{nid}.actor.{slug(backup)}"):
            store_actor(nid, backup, {"points": copy.deepcopy(pts), "segments": copy.deepcopy(segs)}, net_name)
        result = st.kit_convert(pts, segs)
        result["backup"] = backup
        store_actor(nid, head["label"], {"points": pts, "segments": segs}, head["name"].split(" / ")[0])
        record("road_network_kit_convert", "network", f"{nid}-{aslug}", None, [])
        push({"what": "actor", "id": nid, "label": head["label"]})
        return {"ok": True, "label": head["label"], **result,
                "problems": {k: len(v) for k, v in st.problems(pts, segs).items()}}

    JUNCTIONS_PER_CALL = 8
    SAVE_BATCH = 40  # assets per save call

    def build_pieces(net_id: str = "", actor: str = "", rebuild: bool = False) -> dict:
        """Generate a plain-asphalt piece for every junction the kit has no piece for (rebuild: also
        regenerate the pieces already made, in place)."""
        nid, aslug = slug(net_id), slug(actor)
        head, pts, segs = load_actor(nid, aslug)
        if not head:
            return {"ok": False, "error": f"No actor {actor!r} in network {net_id!r}."}
        todo = st.pending_nodes(pts, segs, rebuild)
        if not todo:
            return {"ok": True, "label": head["label"], "built": 0, "note": "No junction needs a generated piece."}
        folder = junction_folder(pts, segs)
        if not folder:
            return {"ok": False, "error": "Couldn't find the project's road folder from the road pieces."}
        # generated networks name their pieces per network so two cities never share SM_JK_<node>
        gen = bool((data.get(f"net.{nid}") or {}).get("generated"))
        prefix = f"SM_JK_{nid}_{aslug}_".replace("-", "_") if gen else "SM_JK_"
        by_i = {p["i"]: p for p in pts}
        half = (lambda arms: max(by_i[m]["w"] for m in arms)) if gen else (lambda arms: 545.0)  # kit roads: Road1 width
        built, unsaved = 0, []
        try:
            # few meshes per call: each mesh build opens a progress window that UEFN frees only when the
            # call's frame ends (see street_kit_build)
            for k in range(0, len(todo), JUNCTIONS_PER_CALL):
                batch = todo[k:k + JUNCTIONS_PER_CALL]
                out = listener("road_network_build_junctions", {"folder": folder, "pieces": [
                    {"name": f"{prefix}{node}", "arms": angles, "half": half(arms),
                     # each mouth as wide as its own road; runways and taxiways get no crossings painted
                     "halves": [by_i[m]["w"] for m in arms] if gen else None,
                     # a street's sidewalks carry on round the junction, in the junction's own mesh
                     "walks": [cg.walk_of(by_i[m]["w"]) for m in arms] if gen else None,
                     "paint": not (aslug.endswith("airport"))}
                    for node, arms, angles in batch]}, timeout=600)
                for node, arms, _ in batch:
                    dists = (out.get("mouths") or {}).get(f"{prefix}{node}")
                    if dists:
                        st.place_generated(pts, segs, node, arms, dists, f"{folder}/{prefix}{node}")
                        unsaved.append(f"{folder}/{prefix}{node}")
                        built += 1
                # saved as we go, a batch per call: left unsaved, hundreds of meshes all went out in the
                # person's next level save (one frame, a checkout per package)
                if len(unsaved) >= SAVE_BATCH:
                    listener("road_network_save_assets", {"paths": unsaved}, timeout=300)
                    unsaved = []
            if unsaved:
                listener("road_network_save_assets", {"paths": unsaved}, timeout=300)
        except (ConnectionError, RuntimeError, TimeoutError) as exc:
            return {"ok": False, "error": f"UEFN stopped after {built} pieces: {exc}"}
        finally:
            store_actor(nid, head["label"], {"points": pts, "segments": segs}, head["name"].split(" / ")[0])
            push({"what": "actor", "id": nid, "label": head["label"]})
        record("road_network_build_pieces", "network", f"{nid}-{aslug}", None, [])
        return {"ok": True, "label": head["label"], "built": built, "folder": folder,
                "problems": {k: len(v) for k, v in st.problems(pts, segs).items()}}

    def restore_actor(net_id: str = "", actor: str = "", from_actor: str = "") -> dict:
        """Copy a saved version (e.g. 'City Roads before kit') back over an actor in the stored network."""
        nid = slug(net_id)
        src, pts, segs = load_actor(nid, slug(from_actor))
        if not src:
            return {"ok": False, "error": f"No saved actor {from_actor!r} in network {net_id!r}."}
        dst = data.get(f"net.{nid}.actor.{slug(actor)}")
        label = (dst or {}).get("label") or actor
        head = store_actor(nid, label, {"points": pts, "segments": segs}, src["name"].split(" / ")[0])
        record("road_network_restore", "network", f"{nid}-{slug(actor)}", None, [])
        push({"what": "actor", "id": nid, "label": label})
        return {"ok": True, "label": label, "from": src["label"], "stats": head["stats"]}

    def circle(net_id: str = "", actor: str = "", nodes: str = "", cx: float = 0.0, cy: float = 0.0,
               fit_center: bool = True, radius: float = 0.0) -> dict:
        nid, aslug = slug(net_id), slug(actor)
        head, pts, segs = load_actor(nid, aslug)
        if not head:
            return {"ok": False, "error": f"No actor {actor!r} in network {net_id!r}."}
        ids = [int(x) for x in re.findall(r"-?\d+", str(nodes))]
        result = st.circularize(pts, segs, ids, None if fit_center else (float(cx), float(cy)), float(radius))
        if not result.get("ok"):
            return result
        store_actor(nid, head["label"], {"points": pts, "segments": segs}, head["name"].split(" / ")[0])
        record("road_network_circle", "network", f"{nid}-{aslug}", None, [])
        push({"what": "actor", "id": nid, "label": head["label"]})
        return {**result, "label": head["label"], "problems": {k: len(v) for k, v in st.problems(pts, segs).items()}}

    def scan_ground(net_id: str = "", grid: int = 200) -> dict:
        """Re-read the terrain height grid (read-only traces) into the network's stored scan."""
        nid = slug(net_id)
        doc = data.get(f"net.{nid}") if nid else None
        if not doc:
            return {"ok": False, "error": f"No road network {net_id!r}."}
        try:
            scan = listener("road_network_scan", {"grid": int(grid)}, timeout=600)
        except (ConnectionError, RuntimeError, TimeoutError) as exc:
            return {"ok": False, "error": f"UEFN didn't answer: {exc}"}
        terrain = scan.get("terrain", {})
        data.put(f"net.{nid}.scan", {"name": f"{doc['name']} / terrain and props",
                                     "props": scan.get("props", []), "terrain": terrain})
        cells = [v for row in terrain.get("heights", []) for v in row if v is not None]
        push({"what": "network", "id": nid})
        return {"ok": True, "id": nid, "grid": terrain.get("grid"), "cells": len(cells),
                "min_z": round(min(cells)) if cells else None, "max_z": round(max(cells)) if cells else None,
                "note": "Scan with only the terrain in the level: pasted roads shape the ground and would be read too."}

    def reimport(net_id: str = "", actor: str = "", confirm: bool = False) -> dict:
        """Replace the actor in UEFN with this network's version (live splines, terrain untouched)."""
        nid, aslug = slug(net_id), slug(actor)
        net = data.get(f"net.{nid}") if nid else None
        head, pts, segs = load_actor(nid, aslug) if net else (None, [], [])
        if not head:
            return {"ok": False, "error": "Pick a network and one of its road actors."}
        if not confirm:
            return {"ok": False, "error": f"This replaces '{head['label']}' in the open UEFN level. Call again with confirm=true."}
        # Never writes the terrain: the splines keep their own Raise/Lower flags and the
        # landscape's reserved Splines edit layer shapes the ground, exactly like a hand-made spline.
        label = head["label"]
        found = st.problems(pts, segs)
        if any(found.values()):
            counts = {k: len(v) for k, v in found.items()}
            return {"ok": False, "error": f"{label} has broken data that would stamp giant shapes into the terrain "
                                          f"({counts}). Run Repair first (road_network_repair). Nothing was changed in UEFN.",
                    "problems": {k: v[:20] for k, v in found.items()}}
        err = paste_actor(label, pts, segs)
        if err:
            return err
        after = listener("road_network_count", {"label": label})
        record("road_network_reimport", "network", nid, None, [])
        return {"ok": True, "label": label, "pieces": after.get("pieces"),
                "note": "Live splines: the ground follows them through the landscape's Splines edit layer."}

    def paste_actor(label: str, pts: list, segs: list):
        """Remove the labelled actor, paste the new text with the Splines tool, label it. None = ok."""
        text = st.serialize(pts, segs)
        try:
            before = listener("road_network_count", {"label": label})
            # same PC: the clipboard is set here, never pushed through the listener socket (MBs abort it)
            subprocess.run(["clip"], input=text.encode("utf-16le"), creationflags=0x08000000, check=True)
            if before.get("path"):
                epic("EditorToolset.LevelToolset", "RemoveFromScene", {"actor": {"refPath": before["path"]}})
            epic("EditorToolset.EditorAppToolset", "SetEditorMode", {"modeId": "EM_Landscape"})
            time.sleep(3)  # the Landscape panel opens a few frames after the call returns
        except (ConnectionError, RuntimeError, TimeoutError, ValueError) as exc:
            return {"ok": False, "error": f"UEFN step failed: {exc}"}
        expected = len([s for s in segs if s["mesh"]])
        known = {row["path"] for row in listener("road_network_actors").get("actors", [])}
        pasted = None
        for attempt in range(2):
            res = uefn_memory.metered(f"paste {label}", uefn_input.paste_in_splines_tool)
            if not res.get("ok"):
                if attempt == 0:  # a window over UEFN or a slow panel: one more go
                    time.sleep(1.5)
                    continue
                return {"ok": False, "error": res.get("error"), "note": "The text is on the clipboard: Manage > Splines, Ctrl+V."}
            time.sleep(min(90, 5 + len(text) / 80000))
            for row in listener("road_network_actors").get("actors", []):
                if row["path"] not in known and row["spline_pieces"] >= expected * 0.5:
                    pasted = row["path"]
            if pasted:
                break
        if not pasted:
            return {"ok": False, "error": "The paste did not land (the Splines tool may not have been active).",
                    "note": "The text is on the clipboard: Landscape mode > Manage > Splines, hover the viewport, Ctrl+V."}
        try:
            epic("EditorToolset.ActorToolset", "SetLabel", {"actor": {"refPath": pasted}, "label": label})
        except ValueError:
            pass
        return None

    # ---------------------------------------------------------------- generated worlds
    def junction_folder(pts: list, segs: list) -> str:
        old = next((ln for p in pts for ln in p["lines"] if "/WorldGen/Roads/Junctions/SM_Junc_" in ln), "")
        m = re.search(r"'(/[^']+?/WorldGen/Roads/Junctions)/SM_Junc_", old)
        if m:
            return m.group(1) + "/Kit"
        for s in segs:
            for ln in s["lines"]:
                # any piece straight in the roads folder: an airport has only SM_Air_ runways, docks SM_Water_
                m = re.search(r"'(/[^']+?/WorldGen/Roads)/SM_[A-Za-z]+_", ln)
                if m:
                    return m.group(1) + "/Junctions/Kit"
        folder = road_folder()  # nothing to read it off: the world's own roads folder
        return folder + "/Junctions/Kit" if folder else ""

    def road_folder() -> str:
        """The project's own road meshes folder: a generated world's own, else read off any stored network's
        first segments (an actor's file, or an older world's first segment chunk)."""
        keys = data.keys("net.")
        for key in keys:
            if re.fullmatch(r"net\.[^.]+", key):
                folder = ((data.get(key) or {}).get("generated") or {}).get("folder")
                if folder:
                    return folder
        for key in keys:
            head_key = re.fullmatch(r"net\.[^.]+\.actor\.[^.]+", key)
            old_chunk = re.fullmatch(r"net\.[^.]+\.actor\.[^.]+\.s\.0", key)
            if not head_key and not old_chunk:
                continue
            doc = data.get(key) or {}
            if head_key and not doc.get("file"):
                continue
            for s in (read_json_file(doc["file"], cache=True) if head_key else doc).get("segs", []):
                for ln in s.get("lines", []):
                    m = re.search(r"'(/[^']+?/WorldGen/Roads)/SM_Road_", ln)
                    if m:
                        return m.group(1)
        return ""

    def heightmap_path(nid: str) -> str:
        # this PC's copy: rebuilt from the shared terrain every time it is asked for
        return os.path.join(str(api.local_dir()), "heightmaps", f"{nid}.png")

    def write_heightmap(nid: str, ter: dict) -> dict:
        # every previewed lot is cut into its level pad: only ever this new landscape, never the level's terrain
        got = pcg_preview(nid)
        ter = _PREVIEW_CACHE.get("ter") or ter  # with the roads cut in: the ground the buildings were fitted to
        fit = _PREVIEW_CACHE.get("fit") or {}
        pads = fit.get("pads") if fit.get("pads") is not None else pl.pads(got.get("layers") or {}, cg.pad_margin(ter))
        png, settings = cg.heightmap_png(ter, pads=pads)
        path = heightmap_path(nid)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "wb") as fh:
            fh.write(png)
        os.replace(path + ".tmp", path)
        return {"path": path, **settings}

    def _layout_doc(layout: dict) -> dict:
        return {"lots": layout["lots"], "hoods": layout["hoods"], "plan": layout.get("plan"),
                "waterfront": layout.get("waterfront"),
                # every block: [x, y, kind, zone, polygon] (what the panel highlights and names), and the ones the
                # person rezoned: [[polygon, zone]] (what the layers place by)
                "blocks": layout.get("blocks") or [], "rezoned": layout.get("rezoned") or [],
                "private": {k: layout.get(k) or [] for k in ("streets", "parking", "stalls", "pads", "grounds", "water",
                                                             "airport", "trees", "fences", "pools")}}

    FIELD_SPLIT = 32_000  # a city field whose JSON is bigger (its lots, its plan) goes in the city's file
    CITY_KEEP = ("name", "x", "y", "radius", "style", "seed", "roads")  # always in the head itself

    def city_parts(head: str) -> list:
        """The chunk and piece docs a city head of an older world owns (deleted with it)."""
        return list(data.keys(f"{head}.private.")) + list(data.keys(f"{head}.piece."))

    def drop_city(nid: str, cslug: str) -> None:
        head = f"net.{nid}.city.{cslug}"
        for k in [head] + city_parts(head):
            data.delete(k)
        data.delete_file(city_path(nid, cslug))

    def city_docs(nid: str) -> dict:
        """Every city of a world: its head doc with its file's fields (private pieces, big fields such as a
        large city's lots); an older world's city from its chunk and piece docs."""
        items = data.items(f"net.{nid}.city.")
        out = {}
        for k, v in items.items():
            if k.count(".") != 3:
                continue
            if v.get("file"):
                got = read_json_file(v["file"])  # parsed here each time: callers may change what they get
                v = {**v, "private": got.get("private") or {},
                     **{key: val for key, val in got.items() if key != "private" and key not in v}}
            if v.get("private_chunks"):
                priv: dict = {}
                for i in range(int(v["private_chunks"])):
                    part = items.get(f"{k}.private.{i}") or {}
                    priv.setdefault(part.get("key", ""), []).extend(part.get("rows") or [])
                v = {**v, "private": priv}
            if v.get("piece_chunks"):
                texts: dict = {}
                for i in range(int(v["piece_chunks"])):
                    part = items.get(f"{k}.piece.{i}") or {}
                    texts[part.get("key", "")] = texts.get(part.get("key", ""), "") + (part.get("text") or "")
                fields = {}
                for key, text in texts.items():
                    try:
                        fields[key] = json.loads(text)
                    except ValueError:  # a piece still on its way from a teammate: generate rebuilds that field
                        pass
                v = {**v, **{key: val for key, val in fields.items() if key and key not in v}}  # an edit in the head wins
            out[k.rsplit(".", 1)[1]] = v
        return out

    def put_city(nid: str, cslug: str, doc: dict) -> None:
        """Store a city: its private pieces and every big field (a large city's lots run past 1 MB: 'doc too
        big to share' lost the whole rebuild, Nov 11 2026) in its file, the rest in its head doc. Unchanged:
        nothing written. An older world's chunk and piece docs of it go (once)."""
        head = f"net.{nid}.city.{cslug}"
        rest = {k: v for k, v in doc.items() if k not in ("private", "private_chunks", "piece_chunks", "file", "sha")}
        big = [k for k, v in rest.items()
               if k not in CITY_KEEP and len(json.dumps(v, ensure_ascii=False, separators=(",", ":"))) > FIELD_SPLIT]
        path = city_path(nid, cslug)
        sha = put_json_file(path, {"private": doc.get("private") or {}, **{k: rest[k] for k in big}})
        for k in city_parts(head):
            data.delete(k)
        data.put(head, {**{k: v for k, v in rest.items() if k not in big}, "file": path, "sha": sha})

    @one_run_at_a_time  # a waiting rebuild with the same settings takes one that started after it asked
    @holds_the_world
    def generate(name: str = "", seed: int = 7, cities: str = "", size_km: float = 6.0, relief_m: float = 60.0,
                 ground_from: str = "", folder: str = "", center_x: float = 0.0, center_y: float = 0.0,
                 options: str = "") -> dict:
        nid = slug(name)
        if not nid:
            return {"ok": False, "error": "Give the world a name (for example 'Valley')."}
        try:
            given = json.loads(options) if str(options or "").strip() else {}
        except ValueError as exc:
            return {"ok": False, "error": f"options is not valid JSON: {exc}"}
        # options left out keep this world's last values; so do its keep-out zones (options.keep)
        last = (data.get(f"net.{nid}") or {}).get("generated") or {}
        keep = cg.keep_zones(given.pop("keep") if "keep" in given else last.get("keep"))
        opts = cg.options({**last.get("options", {}), **given})
        folder = folder or road_folder()
        if not folder:
            return {"ok": False, "error": "Give folder: the project's road meshes folder (…/WorldGen/Roads)."}
        size = max(1.0, min(20.0, float(size_km or 6))) * 100000.0
        base = None
        if ground_from:
            base = (data.get(f"net.{slug(ground_from)}.scan") or {}).get("terrain")
            if not base or not base.get("heights"):
                return {"ok": False, "error": f"Network {ground_from!r} has no ground scan. Rescan its ground first."}
            bounds = (tuple(base["min"]), tuple(base["max"]))
        else:
            bounds = ((center_x - size / 2, center_y - size / 2), (center_x + size / 2, center_y + size / 2))
        old = city_docs(nid)
        text = str(cities or "").strip()
        who = actor()
        if who["kind"] == "ducky" and (given or text):
            # a ducky changing the world's settings or its cities rebuilds every city: not while someone has one
            with held_lock:
                if team_live() and not held_cities.panel_open(nid):
                    return {"ok": False, "error": NO_PANEL}
                for cslug, c in old.items():
                    held = held_cities.blocks(nid, cslug, who)
                    if held:
                        return {"ok": False, "error": "This would rebuild every city, and " + hd.refusal(
                            held, c.get("name") or cslug, held_cities.clock()), "held_by": hd.label(held["by"])}
        if text.startswith("["):
            try:
                given = json.loads(text)
            except ValueError as exc:
                return {"ok": False, "error": f"cities is not valid JSON: {exc}"}
            towns = [{"name": str(c.get("name") or cg.NAMES[k % len(cg.NAMES)]), "x": float(c["x"]), "y": float(c["y"]),
                      "radius": float(c.get("radius_m", 450)) * 100, "style": c.get("style") if c.get("style") in cg.STYLES else "mixed",
                      "seed": int(c.get("seed", seed + k))} for k, c in enumerate(given)]
        elif not text and old:  # regenerate the cities already there (their names, styles and buildings stay)
            towns = [{k: v for k, v in c.items() if k in ("name", "x", "y", "radius", "style", "seed", "features",
                                                          "turn", "spin", "shape", "zones", "pinned")}
                     for c in old.values()]
            # City size changed since the last run: every city grows / shrinks by the same factor; then they are
            # pushed apart until the distance between cities holds
            grow = opts["city_size"] / float((last.get("options") or {}).get("city_size", 1.0) or 1.0)
            for t in towns:
                t["radius"] = max(15000.0, t["radius"] * grow)
        else:
            towns = cg.place_cities(int(seed), max(1, min(12, int(text or 3))), bounds, keep, opts["city_size"],
                                    opts["city_gap_m"] * 100)
        if not towns:
            return {"ok": False, "error": "No room for that many cities: make the map bigger or ask for fewer."}
        # every city stays on the map: pushed apart (a grown city's neighbours make room) and pulled back inside,
        # smaller if the map has no room (a grown city pushed its neighbours off the map, Nov 11 2026)
        towns, fit_notes = cg.fit_inside(towns, bounds, opts["city_gap_m"] * 100, keep)
        for t in towns:  # the map's edges: freeways come back to grade and end before them
            t["map"] = [bounds[0][0], bounds[0][1], bounds[1][0], bounds[1][1]]
        cg.yield_plan(towns)  # where two cities overlap the bigger keeps the land: no street or house twice
        steps = 4 + len(towns)

        def progress(step: int, text: str) -> None:  # the panel's loading card follows these
            push({"what": "progress", "id": nid, "step": step, "of": steps, "text": text})
        progress(0, "Reading the ground" if base else "Shaping the terrain")
        ter = base or cg.terrain(int(seed), size, max(0.0, float(relief_m)) * 100, towns, center=(center_x, center_y),
                                 opts=opts, keep=keep)
        sea_level = ter.get("sea")
        # the hills under each city, read before any street is laid: its streets follow them (cg.plan_hills)
        cg.plan_hills(towns, (lambda x, y, t=ter: st.sample_grid(t, x, y)) if ter.get("heights") else None, opts)
        # roads never dive into a lake: across water they stay 1.5 m above it (a causeway)
        road_ground = ((lambda x, y: max(st.sample_grid(ter, x, y) or 0.0, sea_level + 150)) if sea_level is not None
                       else (lambda x, y: st.sample_grid(ter, x, y) or 0.0))
        wet = cg.wet_from(ter)  # roads know the water: main roads and highways bridge it, side streets stop
        bridges: list = []
        progress(1, "Reading the shorelines, siting shipyards and airports")
        water = waterfront.analyze(ter)  # ShorelineAnalyzer: waterbodies and shorelines for the planner
        # shipyards are districts: planned before the streets. On a world that makes its own ground every yard
        # is one level platform at the water, cut into the terrain here (the shore is then the yard's quay)
        cg.plan_shipyards(towns, water, opts, None if base else (lambda x, y, t=ter: st.sample_grid(t, x, y)))
        if not base and any(y.get("z") is not None for t in towns for y in t.get("shipyards") or []):
            ter = cg.cut_yards(ter, towns, whole=True)
            wet = cg.wet_from(ter)
            water = waterfront.analyze(ter)
        # ... and so is the shore a city builds on: where it gets a quay, a beach or homes on the water it is made
        # for that (cut into the ground with the yards), and the streets are laid on it as it will be
        cg.plan_shores(towns, water, opts, lambda x, y, t=ter: st.sample_grid(t, x, y))
        shores = waterfront.Made(towns)
        if shores.n and sea_level is not None:
            road_ground = lambda x, y: max(shores.level(x, y, st.sample_grid(ter, x, y) or 0.0), sea_level + 150)
        # airports too: site (flat, dry, into the wind, outside the cities), then the streets keep clear of them
        airports = cg.plan_airports(towns, lambda x, y: st.sample_grid(ter, x, y), wet, keep,
                                    (tuple(ter["min"]), tuple(ter["max"])), opts) if ter.get("heights") else []
        airport_builds: dict = {}
        # a freeway ends at the shore when only open water lies beyond it, and each city's streets give way to its
        # neighbours' ramps where those reach over its land
        cg.shore_plan(towns, opts, wet)
        cg.share_freeways(towns, opts)
        progress(2, "Laying roads, freeways, bridges and runways")
        actors = cg.generate(towns, road_ground, folder, int(seed), opts, keep, wet, bridges, airports, airport_builds)
        if not base:  # our own terrain is cut and filled to the roads (a scanned landscape never is)
            ter = cg.grade_to_roads(ter, actors)
            for label, a in actors.items():  # so freeways and highways stand on it as meshes, never reshaping it
                if label.endswith(("Freeways", "Highways")):
                    cg.detach(a)
        before = data.get(f"net.{nid}")
        saved = save_network(nid, name, "", "live")
        if not saved.get("ok"):
            return saved
        stale = [a for a in actor_docs(nid) if a not in {slug(label) for label in actors}]  # gone after the paths
        for cslug in old:
            if cslug not in {slug(t["name"]) for t in towns}:
                drop_city(nid, cslug)
        stats_out = {label: store_actor(nid, label, a, name)["stats"] for label, a in actors.items()}
        # terrain is a big blob (a 160 grid is ~200 KB): one doc, never edited by hand, so it never conflicts
        data.put(f"net.{nid}.scan", {"name": f"{name} / terrain", "props": [], "terrain": ter})
        # the world's water: its bodies and every bridge the roads needed (type, span, clearance, where its
        # towers stand: cg.bridge_towers builds them on the deck as it lies when the city is laid out)
        data.put(f"net.{nid}.water", {"name": f"{name} / water", "sea": water["sea"], "bodies": water["bodies"],
                                       "shorelines_m": sum(s["length_m"] for s in water["shores"]),
                                       "bridges": bridges})
        freeways = {t["name"]: cg.freeway_report(t, opts) for t in towns}
        summary = [{"label": label, "city": a["city"], "type": a["type"], "heading": a["heading"],
                    "center": a["center"], "grading_m": a.get("grading_m"), "facilities": got["facilities"],
                    "links": got["links"], "noise_zone": got["noise_zone"], "report": got["report"]}
                   for a, (label, got) in zip(airports, airport_builds.items())]
        data.put(f"net.{nid}.airports", {"name": f"{name} / airports", "items": summary})
        for k, t in enumerate(towns):
            cslug = slug(t["name"])
            prev = old.get(cslug) or {}
            progress(3 + k, f"Planning {t['name']}: lots, services, waterfront")
            api.search.upsert(f"net.{nid}.city.{cslug}", t["name"], f"City {t['name']} in world {name}: {t['style']} roads")
            layout = cg.city_layout(t, opts, wet, lambda x, y: st.sample_grid(ter, x, y),
                                    ((prev.get("plan") or {}).get("facilities")), water, clear=keep)
            # the open land along a street that joins this city to one close by fills in with house lots
            extra, corridors = cg.join_lots(t, towns, opts, wet, keep)
            layout["lots"] += extra
            layout["rezoned"] += [[poly, "housing"] for poly in corridors]
            layout["airport"] = []
            for a, got in zip(airports, airport_builds.values()):  # the city's airport: its buildings and pieces
                if a["city"] == t["name"]:
                    layout["lots"] += got["lots"]
                    layout["parking"] += got["parking"]
                    layout["stalls"] += got["stalls"]
                    layout["airport"] += got["rows"]
            put_city(nid, cslug, {**t, "name": t["name"], "roads": f"{t['name']} Roads",
                                                 "districts": cg.districts(t, opts),
                                                 # BlockGenerator + neighbourhoods: lots, private streets, parking
                                                 **_layout_doc(layout),
                                                 "airport": next((s for s in summary if s["city"] == t["name"]), None),
                                                 "freeways": freeways[t["name"]],
                                                 "buildings": prev.get("buildings") or {"downtown": [], "normal": [], "housing": []}})
        doc = data.get(f"net.{nid}")
        doc["generated"] = {"seed": int(seed), "size_km": size / 100000, "relief_m": float(relief_m),
                            "ground_from": slug(ground_from), "folder": folder, "options": opts,
                            "cities": len(towns), "center": [center_x, center_y], "keep": keep,
                            # a world that has its own heightmap keeps it: the ground stays cut to the city
                            "heightmap": bool(((before or {}).get("generated") or {}).get("heightmap"))}
        doc.pop("heightmap", None)  # written on demand (road_network_heightmap): keeps live preview fast
        doc["actors"] = sorted(actors)
        doc["updated"] = time.time()
        data.put(f"net.{nid}", doc)
        api.search.upsert(f"net.{nid}", name, f"Generated world {name}: cities " + ", ".join(t["name"] for t in towns))
        if not layer_docs(nid):  # a new world comes with every layer a city needs: generate -> push, nothing else
            pcg_preset(nid, "full_city")
        progress(steps - 1, "Sidewalks, walkways and driveways")
        paths = sidewalks_stage(nid)  # SidewalkGenerator: sidewalks, walkways, driveways as spline actors
        stats_out.update((paths.get("actors") or {}) if paths.get("ok") else {})
        # roads of a previous run that this one didn't make again: dropped only now, so a city's Paths and Access
        # actors (written by the sidewalk stage) are rewritten in place, not deleted and re-made every rebuild
        for aslug in stale:
            if aslug not in {slug(label) for label in stats_out}:
                drop_actor(nid, aslug)
        record("road_network_generate", "network", nid, before,
               [] if before else [{"command": "road_network_delete", "params": {"net_id": nid}}])
        push({"what": "network", "id": nid})
        return {"ok": True, "id": nid, "cities": [{"name": t["name"], "style": t["style"], "x": round(t["x"]),
                                                    "y": round(t["y"]), "radius_m": round(t["radius"] / 100)} for t in towns],
                **({"fitted": fit_notes} if fit_notes else {}),
                "actors": stats_out,
                "freeways": {n: {"interchanges": [i["kind"] for i in r["interchanges"]],
                                 "validator_ok": r["validation"]["ok"],
                                 **({} if r["validation"]["ok"] else {"validation": r["validation"]})}
                             for n, r in freeways.items()},
                "note": "Preview it in the Road Network panel. For the generated terrain, make the heightmap "
                        "(road_network_heightmap) and import it first, then Import into UEFN." if not base else
                        "Roads follow this network's ground. Import into UEFN when it looks right."}

    @holds_the_world
    def add_city(net_id: str = "", name: str = "", x: float | None = None, y: float | None = None,
                 radius_m: float = 0.0, style: str = "") -> dict:
        """Add a city to a generated world (generate again to build it): at x, y (cm) or, without them, a free
        spot on the map; radius_m (default: the world's city size); style mixed / grid / organic."""
        nid = slug(net_id)
        doc = data.get(f"net.{nid}") if nid else None
        if not doc or not doc.get("generated"):
            return {"ok": False, "error": f"{net_id!r} is not a generated world."}
        gen = doc["generated"]
        o = cg.options(gen.get("options"))
        have = city_docs(nid)
        taken = {d["name"] for d in have.values()}
        name = str(name or "").strip() or next((n for n in cg.NAMES if n not in taken), f"City {len(have) + 1}")
        if slug(name) in have:
            return {"ok": False, "error": f"There is already a city called {name!r}."}
        if style and style not in cg.STYLES:
            return {"ok": False, "error": f"style is one of {', '.join(cg.STYLES)}."}
        ter = (data.get(f"net.{nid}.scan") or {}).get("terrain") or {}
        lo, hi = ter.get("min"), ter.get("max")
        radius = float(radius_m) * 100 if radius_m else 60000.0 * o["city_size"]
        wet = cg.wet_from(ter)

        def watery(cx, cy, r):  # share of the city's disc under water (a centre and three rings of samples)
            if not wet:
                return 0.0
            pts = [(cx, cy)] + [(cx + math.cos(a * math.pi / 6) * r * f, cy + math.sin(a * math.pi / 6) * r * f)
                                for f in (0.33, 0.66, 0.95) for a in range(12)]
            return sum(1 for p in pts if wet(*p)) / len(pts)
        if x is None or y is None or str(x) == "" or str(y) == "":
            if not lo or not hi:
                return {"ok": False, "error": "Give x and y: this world has no stored terrain to find room on."}
            free = None
            for s in range(40):  # a free spot: clear of every city (with the world's spacing) and the keep zones
                for c in cg.place_cities(int(gen.get("seed", 7)) + 101 + s, 1, (tuple(lo), tuple(hi)),
                                         gen.get("keep"), o["city_size"], o["city_gap_m"] * 100):
                    if all(math.hypot(c["x"] - d["x"], c["y"] - d["y"]) > c["radius"] + d["radius"] + o["city_gap_m"] * 100
                           for d in have.values()) and watery(c["x"], c["y"], c["radius"]) <= 0.25:
                        free = c
                        break
                if free:
                    break
            if not free:
                return {"ok": False, "error": "No free room for another city: make the cities smaller or move one."}
            x, y, radius = free["x"], free["y"], float(radius_m) * 100 if radius_m else free["radius"]
        elif watery(float(x), float(y), radius) > 0.5:
            return {"ok": False, "error": "That spot is mostly water: a city needs dry land. Pick a spot on land, or "
                                          "Find a free spot."}
        city = {"name": name, "x": round(float(x)), "y": round(float(y)), "radius": round(radius),
                "style": style or cg.STYLES[len(have) % len(cg.STYLES)], "seed": int(gen.get("seed", 7)) * 31 + len(have) + 7}
        put_city(nid, slug(name), {**city, "roads": f"{name} Roads", "districts": cg.districts(city, o),
                                   "buildings": {"downtown": [], "normal": [], "housing": []}})
        api.search.upsert(f"net.{nid}.city.{slug(name)}", name, f"City {name} in world {doc.get('name')}")
        push({"what": "city", "id": nid})
        return {"ok": True, "id": slug(name), "item": city,
                "note": "Generate again (cities empty) to build it and join it to the others by highway."}

    @holds_the_world
    def delete_city(net_id: str = "", city: str = "", confirm: bool = False) -> dict:
        """Remove a city from a generated world (generate again to rebuild the rest without it)."""
        nid, cslug = slug(net_id), slug(city)
        head = f"net.{nid}.city.{cslug}"
        before = data.get(head) if nid and cslug else None
        if not before:
            return {"ok": False, "error": f"No city {city!r} in {net_id!r}. List them with road_network_cities."}
        stop = city_guard(nid, cslug, before.get("name") or city, "deleting it")
        if stop:
            return stop
        if len(city_docs(nid)) <= 1:
            return {"ok": False, "error": "A world keeps at least one city."}
        if not confirm:
            return {"ok": False, "error": f"This removes {before.get('name')} and everything on it. Call again with "
                                          f"confirm=true."}
        drop_city(nid, cslug)
        push({"what": "city", "id": nid})
        return {"ok": True, "id": cslug, "note": "Generate again (cities empty) to rebuild the world without it."}

    def list_cities(net_id: str = "") -> dict:
        nid = slug(net_id)
        items = [{"id": k, **v, "turn_deg": round(cg.orientation(v), 1)} for k, v in city_docs(nid).items()]
        return {"ok": True, "id": nid, "items": sorted(items, key=lambda c: c["name"]), "count": len(items),
                # what one block of a city can be made (city_save zones): its layout and its zone
                "block_kinds": cg.BLOCK_KINDS, "block_zones": list(cg.BLOCK_ZONES), "shape_points": cg.SHAPE_POINTS,
                "shape_range": list(cg.SHAPE_RANGE)}

    @holds_the_world
    def save_city(net_id: str = "", city: str = "", name: str = "", style: str = "", radius_m: float = 0.0,
                  buildings: str = "", x: float | None = None, y: float | None = None, features: str = "",
                  turn_deg: float | None = None, shape: str = "", zones: str = "") -> dict:
        """Change one city's style, size, place (x, y: its new centre in cm), building sets
        ({"downtown": [mesh paths], ...}), features ({"airports": "none", ...}: cg.FEATURES), the way it is turned
        (turn_deg), its outline (shape) or the type of its blocks (zones)."""
        nid, cslug = slug(net_id), slug(city)
        before = data.get(f"net.{nid}.city.{cslug}") if nid and cslug else None
        if not before:
            return {"ok": False, "error": f"No city {city!r} in {net_id!r}. List them with road_network_cities."}
        stop = city_guard(nid, cslug, before.get("name") or city, "changing it")  # someone else has it: nothing changes
        if stop:
            return stop
        if style and style not in cg.STYLES:
            return {"ok": False, "error": f"style is one of {', '.join(cg.STYLES)}."}
        doc = dict(before)
        if style:
            doc["style"] = style
        if x is not None and y is not None and str(x) != "" and str(y) != "":
            doc["x"], doc["y"] = round(float(x)), round(float(y))  # moved: generate again rebuilds it there
            doc["pinned"] = True  # put there by hand: it stays, however close to another city (never pushed apart)
        if radius_m:
            doc["radius"] = max(150.0, float(radius_m)) * 100
            doc["districts"] = cg.districts(doc)
        if turn_deg is not None and str(turn_deg) != "":
            # the whole city turns: its street grid, its outline and its industrial side
            doc["turn"] = round(float(turn_deg) % 360.0, 1)
            doc["spin"] = round(doc["turn"] - cg.seed_turn(doc), 3)
            doc["districts"] = cg.districts(doc)
        if shape:
            if str(shape).strip().lower() in ("reset", "none", "[]"):
                doc.pop("shape", None)  # back to the outline its seed gives it
            else:
                try:
                    factors = [float(v) for v in json.loads(shape)]
                except (ValueError, TypeError) as exc:
                    return {"ok": False, "error": f"shape is a JSON list of {cg.SHAPE_POINTS} numbers (or 'reset'): {exc}"}
                if len(factors) != cg.SHAPE_POINTS:
                    return {"ok": False, "error": f"shape needs {cg.SHAPE_POINTS} numbers: how far the city reaches at each "
                                                  f"of {cg.SHAPE_POINTS} directions round it, as a share of its radius "
                                                  f"({cg.SHAPE_RANGE[0]} to {cg.SHAPE_RANGE[1]})."}
                lo, hi = cg.SHAPE_RANGE
                doc["shape"] = [round(max(lo, min(hi, v)), 3) for v in factors]
        if zones:
            try:
                given = json.loads(zones)
            except ValueError as exc:
                return {"ok": False, "error": f"zones is not valid JSON: {exc}"}
            rows = [dict(z) for z in doc.get("zones") or []]
            for z in ([given] if isinstance(given, dict) else given):
                if not isinstance(z, dict) or "x" not in z or "y" not in z:
                    return {"ok": False, "error": 'zones: {"x": cm, "y": cm, "kind": ..., "zone": ..., "green": 0 to 1} (a spot inside the block), or a list of them.'}
                kind, zone = str(z.get("kind") or ""), str(z.get("zone") or "")
                if (kind and kind not in cg.BLOCK_KINDS) or (zone and zone not in cg.BLOCK_ZONES):
                    return {"ok": False, "error": f"kind is one of {', '.join(cg.BLOCK_KINDS)}; zone is one of {', '.join(cg.BLOCK_ZONES)}."}
                green = z.get("green")
                if green is not None and green != "":
                    try:
                        green = round(max(0.0, min(1.0, float(green))), 2)
                    except (TypeError, ValueError):
                        return {"ok": False, "error": "green is the share of the block's lots planted instead of built on: a number from 0 to 1."}
                else:
                    green = None
                at = (float(z["x"]), float(z["y"]))
                # one choice a block: the block this spot is in (as last laid out), else anything within 30 m of it
                block = next((b for b in (city_docs(nid).get(cslug) or {}).get("blocks") or [] if cg._inside(b[4], *at)), None)
                same = (lambda r: cg._inside(block[4], r["x"], r["y"])) if block else \
                    (lambda r: math.hypot(r["x"] - at[0], r["y"] - at[1]) < 3000)
                rows = [r for r in rows if not same(r)]
                if kind or zone or green is not None:  # all empty: the block goes back to the city's own plan
                    rows.append({"x": round(at[0]), "y": round(at[1]), **({"kind": kind} if kind else {}), **({"zone": zone} if zone else {}),
                                 **({"green": green} if green is not None else {})})
            doc["zones"] = rows
        if features:
            try:
                given = json.loads(features)
            except ValueError as exc:
                return {"ok": False, "error": f"features is not valid JSON: {exc}"}
            bad = [k for k, v in given.items() if k not in cg.FEATURES or (v is not None and v not in cg.CHOICES[k][0])]
            if bad:
                return {"ok": False, "error": f"Unknown feature or value: {bad}. Features: " + ", ".join(
                    f"{k} ({'/'.join(cg.CHOICES[k][0])})" for k in cg.FEATURES)}
            merged = {**(doc.get("features") or {}), **given}
            doc["features"] = {k: v for k, v in merged.items() if v is not None}  # null = back to the world's choice
        if buildings:
            try:
                sets = json.loads(buildings)
            except ValueError as exc:
                return {"ok": False, "error": f"buildings is not valid JSON: {exc}"}
            doc["buildings"] = {k: [str(p) for p in sets.get(k, doc["buildings"].get(k, []))] for k in ("downtown", "normal", "housing")}
        key = cslug
        if name and slug(name) != cslug:
            key = slug(name)
            doc["name"], doc["roads"] = name, f"{name} Roads"
            drop_city(nid, cslug)  # its file's fields come back on generate
            doc["private_chunks"] = doc["piece_chunks"] = 0
            doc["file"] = doc["sha"] = ""
        data.put(f"net.{nid}.city.{key}", doc)
        api.search.upsert(f"net.{nid}.city.{key}", doc["name"], f"City {doc['name']}: {doc.get('style')} roads, "
                                                                 f"{round(doc['radius'] / 100)} m radius")
        record("road_network_city_save", "city", f"{nid}-{key}", before,
               [{"command": "road_network_city_save", "params": {"net_id": nid, "city": key, "name": before["name"],
                                                                 "style": before.get("style", ""),
                                                                 "radius_m": round(before["radius"] / 100),
                                                                 "buildings": json.dumps(before.get("buildings") or {})}}])
        push({"what": "city", "id": nid})
        return {"ok": True, "id": key, "item": doc, "note": "Generate again (cities empty) to rebuild its roads."}

    def heightmap(net_id: str = "") -> dict:
        nid = slug(net_id)
        doc = data.get(f"net.{nid}") if nid else None
        if not doc or not doc.get("generated"):
            return {"ok": False, "error": f"{net_id!r} is not a generated world."}
        # a world laid over an existing terrain rebuilds that terrain from its ground scan (the landscape it
        # was scanned from can be gone from the level: then the roads would have nothing to sit on)
        src = slug(doc["generated"].get("ground_from") or "") or nid
        ter = (data.get(f"net.{src}.scan") or {}).get("terrain") or {}
        if not ter.get("heights"):
            return {"ok": False, "error": f"{src!r} has no stored ground scan. Generate the world again."}
        if not doc["generated"].get("heightmap"):  # from now on the buildings are fitted to this landscape
            doc["generated"]["heightmap"] = True
            data.put(f"net.{nid}", doc)
        hm = write_heightmap(nid, ter)  # always from the stored terrain, so every PC gets the same file
        return {"ok": True, "id": nid, **hm, "ground_fit": fit_summary(),
                "how": f"Landscape mode > Manage > New > Import from File: pick the PNG, resolution {hm['resolution']}, "
                       f"Location (the centre) {hm['location']}, Scale {hm['scale']}, components {hm['components']}. "
                       f"It makes a new landscape; your current one is not touched. "
                       f"Every building lot is already levelled into it (cut and fill), so buildings sit flat on hills."}

    def import_all(net_id: str = "", confirm: bool = False, force: bool = False) -> dict:
        """Push roads: build the junction pieces and paste every road actor that changed since its last push
        (or is missing from the level). Unchanged actors are left alone. force=true pushes them all."""
        nid = slug(net_id)
        doc = data.get(f"net.{nid}") if nid else None
        if not doc:
            return {"ok": False, "error": f"No road network {net_id!r}."}
        todo, same = [], []
        for aslug in actor_docs(nid):
            head, pts, segs = load_actor(nid, aslug)
            digest = hashlib.sha1(st.serialize(pts, segs).encode()).hexdigest()
            if not force and head.get("pushed") == digest:
                same.append(head["label"])
            else:
                todo.append((aslug, head["label"], digest))
        if same:  # pushed before: still in the level? (someone may have deleted it there)
            try:
                present = {r["label"] for r in listener("road_network_actors").get("actors", [])}
                for label in [s for s in same if s not in present]:
                    same.remove(label)
                    aslug = slug(label)
                    todo.append((aslug, label, hashlib.sha1(st.serialize(*load_actor(nid, aslug)[1:]).encode()).hexdigest()))
            except (ConnectionError, RuntimeError, TimeoutError):
                pass
        if not todo:
            return {"ok": True, "id": nid, "done": {}, "unchanged": same, "note": "Every road actor is up to date."}
        if not confirm:
            return {"ok": False, "error": f"This pastes {len(todo)} changed road actors into the open UEFN level "
                                          f"({', '.join(t[1] for t in todo)}); {len(same)} unchanged are left alone. "
                                          f"Call again with confirm=true.", "unchanged": same}
        done = {}
        for aslug, label, digest in todo:
            pieces = build_pieces(nid, label)
            if not pieces.get("ok"):
                return {"ok": False, "error": f"{label}: {pieces.get('error')}", "done": done}
            out = reimport(nid, label, True)
            if not out.get("ok"):
                return {"ok": False, "error": f"{label}: {out.get('error')}", "done": done}
            head = data.get(f"net.{nid}.actor.{aslug}") or {}
            # the hash of what is in the level now (build_pieces may have placed junction meshes)
            _, pts, segs = load_actor(nid, aslug)
            data.put(f"net.{nid}.actor.{aslug}", {**head, "pushed": hashlib.sha1(st.serialize(pts, segs).encode()).hexdigest()})
            done[label] = {"junction_pieces": pieces.get("built", 0), "pieces": out.get("pieces")}
        return {"ok": True, "id": nid, "done": done, "unchanged": same}

    # ---------------------------------------------------------------- PCG placement layers
    def layer_docs(nid: str) -> dict:
        return {k.rsplit(".", 1)[1]: v for k, v in data.items(f"net.{nid}.pcg.").items() if k.count(".") == 3}

    def pcg_layers(net_id: str = "") -> dict:
        nid = slug(net_id)
        if not data.get(f"net.{nid}"):
            return {"ok": False, "error": f"No road network {net_id!r}."}
        items = sorted(({"id": k, **pl.clean(v)} for k, v in layer_docs(nid).items()), key=lambda l: (l["order"], l["name"]))
        return {"ok": True, "id": nid, "items": items, "count": len(items),
                "kinds": list(pl.KINDS), "where": list(pl.WHERE), "roles": list(pl.ROLES),
                "fields": {k: {"default": d, "min": lo, "max": hi} for k, (d, lo, hi) in pl.NUMBERS.items()},
                "example": {"name": "Flower beds", "kind": "scatter", "where": "housing", "density": 0.5,
                            "cover": 0.4, "meshes": [{"path": "/<your folder>/SM_Tulips", "weight": 3},
                                                     {"path": "/<your folder>/SM_Roses", "weight": 1}],
                            "scale_min": 0.8, "scale_max": 1.3, "sink_cm": 5, "avoid": ["Houses"], "order": 20},
                "note": "" if items else "No layers yet: road_network_pcg_preset adds a starter set."}

    def pcg_layer_save(net_id: str = "", layer: str = "", layer_id: str = "") -> dict:
        nid = slug(net_id)
        doc = data.get(f"net.{nid}") if nid else None
        if not doc:
            return {"ok": False, "error": f"No road network {net_id!r}."}
        try:
            raw = json.loads(layer) if isinstance(layer, str) else dict(layer or {})
        except ValueError as exc:
            return {"ok": False, "error": f"layer is not valid JSON: {exc}"}
        clean = pl.clean(raw)
        lid = slug(layer_id or clean["name"])
        if not lid:
            return {"ok": False, "error": "Give the layer a name."}
        before = data.get(f"net.{nid}.pcg.{lid}")
        data.put(f"net.{nid}.pcg.{lid}", {**clean, "name": clean["name"]})
        api.search.upsert(f"net.{nid}.pcg.{lid}", f"{clean['name']} ({doc.get('name', nid)})",
                          f"PCG layer {clean['name']} in {doc.get('name', nid)}: {clean['kind']} {clean['where']}, "
                          f"{len(clean['meshes'])} meshes")
        undo = ({"command": "road_network_pcg_layer_save", "params": {"net_id": nid, "layer_id": lid, "layer": json.dumps(before)}}
                if before else {"command": "road_network_pcg_layer_delete", "params": {"net_id": nid, "layer_id": lid}})
        record("road_network_pcg_layer_save", "pcg-layer", f"{nid}-{lid}", before, [undo])
        push({"what": "pcg", "id": nid})
        return {"ok": True, "id": lid, "item": clean}

    def pcg_layer_delete(net_id: str = "", layer_id: str = "") -> dict:
        nid, lid = slug(net_id), slug(layer_id)
        before = data.get(f"net.{nid}.pcg.{lid}") if nid and lid else None
        if before is None:
            return {"ok": False, "error": f"No layer {layer_id!r} in {net_id!r}."}
        data.delete(f"net.{nid}.pcg.{lid}")
        record("road_network_pcg_layer_delete", "pcg-layer", f"{nid}-{lid}", before,
               [{"command": "road_network_pcg_layer_save", "params": {"net_id": nid, "layer_id": lid, "layer": json.dumps(before)}}])
        push({"what": "pcg", "id": nid})
        return {"ok": True, "id": lid}

    # A preset is a set of layers by name: the built-in ones (pl.PRESETS) and any a person or an AI saved here, with
    # the world options (terrain, streets) it was made for. Saved ones live in this plugin's data, like the worlds.
    def presets() -> dict:
        built = [{"id": n, "name": n, "built_in": True, "layers": len(pl.preset(n))} for n in pl.PRESETS]
        saved = [{"id": k.split(".", 1)[1], "name": d.get("name") or k.split(".", 1)[1], "built_in": False,
                  "layers": len(d.get("layers") or []), "layer_names": [l.get("name") for l in d.get("layers") or []],
                  "options": d.get("options") or {}, "note": d.get("note") or ""}
                 for k, d in sorted(data.items("preset.").items()) if k.count(".") == 1]
        return {"ok": True, "items": built + saved, "count": len(built) + len(saved)}

    def preset_save(name: str = "", net_id: str = "", layers: str = "", options: str = "", note: str = "") -> dict:
        pid = slug(name)
        if not pid:
            return {"ok": False, "error": "Give the preset a name."}
        if pid in pl.PRESETS:
            return {"ok": False, "error": f"{name!r} is a built-in preset: pick another name."}
        nid = slug(net_id)
        world = data.get(f"net.{nid}") if nid else None
        if nid and not world:
            return {"ok": False, "error": f"No road network {net_id!r}."}
        rows, opts = [], {}
        if str(layers or "").strip():
            try:
                given = json.loads(layers)
            except ValueError as exc:
                return {"ok": False, "error": f"layers is not valid JSON: {exc}"}
            if not isinstance(given, list) or not all(isinstance(l, dict) and l.get("name") for l in given):
                return {"ok": False, "error": "layers is a JSON list of layers, each with a name (as road_network_pcg_layer_save takes one)."}
            rows = [pl.clean(dict(l)) for l in given]
        elif world:
            rows = sorted(layer_docs(nid).values(), key=lambda l: (l.get("order", 0), l.get("name", "")))
        if not rows:
            return {"ok": False, "error": "Nothing to save: give layers (a JSON list), or net_id for a world whose layers to take."}
        if str(options or "").strip():
            try:
                given = json.loads(options)
            except ValueError as exc:
                return {"ok": False, "error": f"options is not valid JSON: {exc}"}
            unknown = [k for k in (given if isinstance(given, dict) else {"?": 0}) if k not in cg.OPTIONS and k not in cg.CHOICES]
            if unknown:
                return {"ok": False, "error": f"Unknown option(s): {', '.join(unknown)}. List them with road_network_generate_options."}
            clamped = cg.options(given)
            opts = {k: clamped[k] for k in given}
        elif world and not str(layers or "").strip():
            opts = dict((world.get("generated") or {}).get("options") or {})  # the world it was taken from: its own
        before = data.get(f"preset.{pid}")
        data.put(f"preset.{pid}", {"name": str(name).strip(), "note": str(note or ""), "layers": rows, "options": opts})
        record("road_network_preset_save", "preset", pid, before,
               [{"command": "road_network_preset_delete", "params": {"preset": pid, "confirm": True}}] if not before else
               [{"command": "road_network_preset_save", "params": {"name": before.get("name") or pid, "layers": json.dumps(before.get("layers") or []),
                                                                   "options": json.dumps(before.get("options") or {}),
                                                                   "note": before.get("note") or ""}}])
        push({"what": "presets", "id": pid})
        return {"ok": True, "id": pid, "layers": len(rows), "options": opts,
                "note": f"Apply it to a world with road_network_pcg_preset(net_id, preset='{pid}')."}

    def preset_delete(preset: str = "", confirm: bool = False) -> dict:
        pid = slug(preset)
        before = data.get(f"preset.{pid}") if pid else None
        if not before:
            return {"ok": False, "error": f"No saved preset {preset!r}. List them with road_network_presets."}
        if not confirm:
            return {"ok": False, "error": f"This deletes the preset {before.get('name') or pid} ({len(before.get('layers') or [])} layers). "
                                          "Call again with confirm=true. Worlds it was applied to keep their layers."}
        data.delete(f"preset.{pid}")
        record("road_network_preset_delete", "preset", pid, before,
               [{"command": "road_network_preset_save", "params": {"name": before.get("name") or pid, "layers": json.dumps(before.get("layers") or []),
                                                                   "options": json.dumps(before.get("options") or {}),
                                                                   "note": before.get("note") or ""}}])
        push({"what": "presets", "id": pid})
        return {"ok": True, "id": pid, "deleted": True}

    def pcg_preset(net_id: str = "", preset: str = "full_city", reorder: bool = False, replace: bool = False) -> dict:
        nid = slug(net_id)
        if not data.get(f"net.{nid}"):
            return {"ok": False, "error": f"No road network {net_id!r}."}
        saved = data.get(f"preset.{slug(preset)}") if preset not in pl.PRESETS else None
        if preset not in pl.PRESETS and not saved:
            return {"ok": False, "error": f"No preset {preset!r}. Built in: {', '.join(pl.PRESETS)}; saved ones: road_network_presets."}
        added, reordered, replaced, undo = [], [], [], []
        for layer in (saved["layers"] if saved else pl.preset(preset)):
            lid = slug(layer["name"])
            have = data.get(f"net.{nid}.pcg.{lid}")
            if have and replace and have != layer:  # asked for: the preset's own settings over the world's
                data.put(f"net.{nid}.pcg.{lid}", layer)
                replaced.append(layer["name"])
                undo.append({"command": "road_network_pcg_layer_save",
                             "params": {"net_id": nid, "layer_id": lid, "layer": json.dumps(have)}})
            elif have and replace:
                continue
            elif not have:  # never overwrite a layer the person already tuned
                data.put(f"net.{nid}.pcg.{lid}", layer)
                added.append(layer["name"])
                undo.append({"command": "road_network_pcg_layer_delete", "params": {"net_id": nid, "layer_id": lid}})
            elif reorder and have.get("order") != layer["order"]:  # only the planning order, every other setting kept
                data.put(f"net.{nid}.pcg.{lid}", {**have, "order": layer["order"]})
                reordered.append(layer["name"])
                undo.append({"command": "road_network_pcg_layer_save",
                             "params": {"net_id": nid, "layer_id": lid, "layer": json.dumps(have)}})
        record("road_network_pcg_preset", "pcg-layer", nid, None, undo)
        push({"what": "pcg", "id": nid})
        out = {"ok": True, "id": nid, "added": added, "reordered": reordered, "replaced": replaced}
        if saved and saved.get("options"):  # the terrain and street settings it was made for: the caller's to use
            out.update(options=saved["options"], next="To give the world this preset's terrain and streets too, generate it "
                                                       "again with options=<these, as JSON> (road_network_generate).")
        return out

    def road_tag_for(nid: str) -> str:
        return "CityRoads" if not (data.get(f"net.{nid}") or {}).get("generated") else f"{nid}_roads"

    def asset_set_of(nid: str) -> str:
        return (data.get(f"net.{nid}.assets") or {}).get("asset_set") or "own"

    def interiors_of(nid: str) -> bool:
        return (data.get(f"net.{nid}.assets") or {}).get("interiors", True) is not False

    def pcg_assets(net_id: str = "", asset_set: str = "", interiors: str = "") -> dict:
        """Which pieces a world's layers use: 'own' (the project's kit), 'fortnite' or 'mixed', and whether
        buildings get interiors ('yes' / 'no'). Empty = read them."""
        nid = slug(net_id)
        doc = data.get(f"net.{nid}") if nid else None
        if not doc:
            return {"ok": False, "error": f"No road network {net_id!r}."}
        if asset_set and asset_set not in pl.ASSET_SETS:
            return {"ok": False, "error": f"asset_set is one of {', '.join(pl.ASSET_SETS)}."}
        if interiors and str(interiors).lower() not in ("yes", "no", "true", "false"):
            return {"ok": False, "error": "interiors is yes or no."}
        if asset_set or interiors:
            before = data.get(f"net.{nid}.assets")
            new = {"name": f"{doc.get('name', nid)} pieces", "asset_set": asset_set or asset_set_of(nid),
                   "interiors": interiors_of(nid) if not interiors else str(interiors).lower() in ("yes", "true")}
            data.put(f"net.{nid}.assets", new)
            record("road_network_pcg_assets", "pcg-assets", nid, before,
                   [{"command": "road_network_pcg_assets", "params": {
                       "net_id": nid, "asset_set": (before or {}).get("asset_set", "own"),
                       "interiors": "no" if (before or {}).get("interiors") is False else "yes"}}])
            push({"what": "pcg", "id": nid})
        cur = asset_set_of(nid)
        return {"ok": True, "id": nid, "asset_set": cur, "interiors": interiors_of(nid),
                "roles": {r: pl.asset_hint(r, cur) for r in pl.ROLES}}

    def world_actors(nid: str) -> list:
        actors = []
        for aslug, head in actor_docs(nid).items():
            label = str(head.get("label") or "")
            if " before kit" in label or label.endswith((" Paths", " Access")):
                continue  # a saved backup, or the sidewalks / walkways / driveways: not streets PCG lines up on
            _, pts, segs = load_actor(nid, aslug)
            actors.append((pts, segs))
        return actors

    def world_roads(nid: str, step: float = 500.0) -> list:
        return pl.road_samples(world_actors(nid), step)

    def pcg_plan(net_id: str = "", road_tag: str = "") -> dict:
        nid = slug(net_id)
        doc = data.get(f"net.{nid}") if nid else None
        if not doc:
            return {"ok": False, "error": f"No road network {net_id!r}."}
        layers = list(layer_docs(nid).values())
        if not layers:
            return {"ok": False, "error": "This world has no PCG layers yet (road_network_pcg_preset adds a starter set)."}
        cities = list(city_docs(nid).values())
        actors = world_actors(nid)
        halves = sorted(s[2] for s in pl.road_samples(actors, 5000.0)) or [875.0]
        out = pl.plan(doc, layers, cities, road_tag or road_tag_for(nid), road_half_cm=halves[len(halves) // 2],
                      asset_set=asset_set_of(nid), juncs=pl.junctions(actors))
        out["road_actors"] = doc.get("actors") or []
        out["how"] = ("Build in UEFN with Epic PCGToolset, following skill_read_subskill('pcg', 'graph_layers'): "
                      "tag every road actor with road_tag, CreateGraph(name=graph, path=<content_root>/WorldGen/PCG), "
                      "AddNode per node in order with its settings, ConnectNodePins per edge, SetGraphParams for params "
                      "(each wired through a Get Graph Parameter node), SpawnGraphInstance with volume, ExecuteGraphInstance. "
                      "Then the foliage[] layers in Foliage mode (foliage_how).")
        return out

    def private_plans(nid: str, towns: list) -> list:
        """[(city, its private streets)] as the city's plan has them, less any that would run across a freeway, a
        ramp or a highway (those are laid after the blocks are cut)."""
        fast = pl.road_samples([load_actor(nid, a)[1:] for a, h in actor_docs(nid).items()
                                if str(h.get("label") or "").endswith(("Freeways", "Highways"))], 400.0)
        return [(t, pl.off_roads((t.get("private") or {}).get("streets") or [], fast)) for t in towns]

    def private_streets(plans: list, public: dict) -> list:
        """Every city's private streets as (points, segments), graded like roads on the ground the public roads left
        (`public`, a terrain grid): what the '<City> Access' actors hold once the sidewalk stage has run."""
        on = st.grid_sampler(public)
        out = []
        for town, streets in plans:
            if streets:
                a = cg.access_actor({"private": {"streets": streets}}, [], lambda x, y: on(x, y) or 0.0, road_folder())
                segs = [s for s in a["segments"] if "PrivateStreet" in str(s.get("mesh") or "")]
                if segs:
                    out.append((a["points"], segs))
        return out

    _PREVIEW_CACHE: dict = {}
    FOREST_LIMIT = 200000  # foliage points per layer kept and baked: very dense forests; tiled PCG builds them 400 m at a time

    @one_run_at_a_time
    def fit_summary():
        fit = _PREVIEW_CACHE.get("fit")
        return {k: v for k, v in fit.items() if k not in ("pads", "ground")} if fit else None

    def fit_buildings(placed: dict, terrain: dict, cut: bool = True, final: bool = False) -> dict:
        """Every building against the landscape the heightmap will import (pads cut in, sampled exactly as UEFN
        does): a building that would float gets the deeper foundation it needs, here, so the build uses it.
        cut=False: the level keeps its own landscape (no pads cut): buildings sit on the scanned ground as it is."""
        # frozen: the heightmap is cut from exactly these (final: the terrain given already has them cut in)
        pads = pl.pads(placed, cg.pad_margin(terrain)) if cut and not final else []
        scan = st.grid_sampler(terrain)
        ground = cg.imported_ground(terrain, pads=pads) if cut else (lambda x, y: scan(x, y) or 0.0)
        rows = [(name, r) for name, v in placed.items() if v.get("kind") == "buildings" for r in v["points"] if len(r) > 9]
        moves, before, after = cg.settle(ground, [(name, r[0], r[1], r[3], r[7], r[8], r[2], r[9]) for name, r in rows])
        for k, (z, found) in moves.items():
            r = rows[k][1]
            for p in r[10] if len(r) > 10 and isinstance(r[10], list) else []:  # paths leave from the new floor
                if len(p) > 1 and len(p[1]) > 2:
                    p[1][2] = z
            r[2], r[9] = z, found
        # every foundation as deep as the ground really falls away under it (the lot's own figure was a guess
        # made before its pad was cut)
        for (_n, r), need in zip(rows, cg.footings(ground, [(n, r[0], r[1], r[3], r[7], r[8], r[2]) for n, r in rows])):
            r[9] = need
        return {**after, "settled": len(moves), "before": {k: before[k] for k in ("buried", "floating", "overlapping")},
                "pads": pads, "ground": ground}

    _PREVIEW_MEMO: dict = {}  # per world: its last placement, unit by unit

    def pcg_preview(net_id: str = "", layer_id: str = "", summary: bool = False) -> dict:
        """Every enabled layer placed offline with the graph's rules; one layer per call when layer_id is
        given (keeps each reply small). Points: [x, y, z, yaw, scale(, height m for buildings)]."""
        nid = slug(net_id)
        if not data.get(f"net.{nid}"):
            return {"ok": False, "error": f"No road network {net_id!r}."}
        docs = layer_docs(nid)
        if not docs:
            return {"ok": True, "id": nid, "layers": {}}
        terrain = (data.get(f"net.{nid}.scan") or {}).get("terrain") or {}
        actors = world_actors(nid)
        cities = list(city_docs(nid).values())
        # Every layer avoids the ones before it, so one layer still needs the whole preview: compute it
        # once per input set and serve the panel's per-layer calls from that.
        keep = ((data.get(f"net.{nid}") or {}).get("generated") or {}).get("keep") or []
        # pickle, not sorted JSON: the same inputs read back the same way give the same bytes, several times faster
        gen = (data.get(f"net.{nid}") or {}).get("generated") or {}
        # the ground the buildings stand on: this world's own heightmap (lots cut in) once it has one; a world laid
        # over another's landscape keeps that landscape as scanned until its heightmap is made
        cut = not gen.get("ground_from") or bool(gen.get("heightmap"))
        # (without each city's plan report: the placement reads its lots, never the report, and the report's wording
        # alone changes on the rebuild after an edit)
        key = hashlib.sha1(pickle.dumps([nid, docs, [{k: v for k, v in c.items() if k != "plan"} for c in cities],
                                         terrain, actors, keep, cut], protocol=5)).hexdigest()
        if _PREVIEW_CACHE.get("key") != key:  # ponytail: one entry, the world being looked at
            ground = st.grid_sampler(terrain) if terrain.get("heights") else (lambda x, y: 0.0)
            ground = cg.on_made(cities, ground)  # (a shipyard's platform and a made shore: at their own levels)
            # the whole city, unit by unit: what no change can reach is put back as it stood (pl.PreviewMemo), so an
            # edit to one city places that city again and leaves every other building and tree where it was
            memo = _PREVIEW_MEMO.setdefault(nid, pl.PreviewMemo())
            placed = pl.preview(list(docs.values()), cities, terrain, pl.road_samples(actors), ground,
                                limit=6000, juncs=pl.junctions(actors), keep=keep, foliage_limit=FOREST_LIMIT, memo=memo,
                                arms=pl.arm_samples(actors))
            _PREVIEW_CACHE["placing"] = dict(memo.last)
            # (no loose wall slabs along the roads: every road piece carries its own side walls down into the
            # ground, road_profiles 'skirt'; every building its own foundation, kit_bake.foundation; every low deck
            # its approach wall, citygen.deck_fills; and the ground is graded to the roads)
            placed.update(pl.site_layers(cities, ground))  # complexes' parking, stalls, turnarounds, garages
            placed.update(pl.road_ends(actors, terrain))  # a cap on every street that ends
            placed.update(pl.aprons(placed))  # a dropped kerb where each driveway meets its street
            # fences only round houses that were built, through no building, across no street, never crossing
            pl.fit_fences(placed, pl.road_samples(actors, 400.0) + pl.street_samples(cities))
            # nothing on or under a freeway, a ramp or a highway (lots are cut before those are laid)
            fast = [load_actor(nid, a)[1:] for a, h in actor_docs(nid).items()
                    if str(h.get("label") or "").endswith(("Freeways", "Highways"))]
            # the land inside an interchange's loops is planted, never built on: trees there, off every road
            groves = pl.interchange_groves(fast, ground, terrain.get("sea"))
            pl.clear_of(groves, pl.road_samples(actors, 400.0), 400.0)
            blds = [(p[0], p[1], 50.0 * max(p[7], p[8]) + 400) for v in placed.values() if v.get("kind") == "buildings"
                    for p in v.get("points") or [] if len(p) > 8]
            for g in groves.values():  # and none in a building at the interchange's edge
                g["points"] = [t for t in g["points"] if not any(abs(t[0] - x) < r and abs(t[1] - y) < r for x, y, r in blds)]
                g["count"] = g["shown"] = len(g["points"])
            placed.update(groves)
            pl.clear_of(placed, pl.road_samples(fast, 400.0))
            # last of all: nothing on a road surface (junctions and private streets are road too), no tree in a
            # building. Every layer above was placed by its own rule, and each knew some roads and not others.
            caps = [(p[0], p[1], p[4] * 1000.0) for p in (placed.get("Road ends") or {}).get("points") or []]
            _PREVIEW_CACHE["swept"] = {"off_roads": pl.clear_roads(placed, pl.surface_samples(actors, cities) + caps),
                                       "out_of_buildings": pl.clear_buildings(placed)}
            # waterfront structures (piers, marinas, quays, beaches, ports, stilt / floating homes)
            placed.update(waterfront.layers(cities))
            # airports: aircraft, jet bridges, hangars, tower, lights, aprons, fuel farm
            placed.update(airport.layers([r for c in cities for r in (c.get("private") or {}).get("airport") or []]))
            # every generated world: a building the ground would bury is lifted onto it, here, before any push
            # (worlds over another's landscape were skipped once: their houses went in with the hill inside them)
            if gen and cut and terrain.get("heights"):
                # the ground the city leaves, a landscape pixel at a time: every road cut and filled in first
                # (its own cells locked), then every lot levelled round them. Re-cut whenever the city changes;
                # the road pass is kept while the terrain and roads stay the same.
                # ... the subdivisions' and complexes' private streets with them: left out, the hill covered them
                # and the houses faced a strip of grass. They come from each city's PLAN, laid on the ground the
                # public roads left, never from the stored '<City> Access' actors: right after a regenerate those
                # are still the last world's (the sidewalk stage rewrites them after this preview), and the new
                # ground was cut down to streets that were no longer there, 100 m off after a new seed. Every
                # block with a complex in it became a pit, and the preview kept it (its key never saw them).
                plans = private_plans(nid, cities)
                rk = hashlib.sha1(pickle.dumps([terrain, actors, [s for _c, s in plans],
                                                [(c.get("shipyards"), c.get("shores")) for c in cities]],
                                               protocol=5)).hexdigest()
                if _GROUND.get("key") != rk:
                    locked: set = set()
                    # (first of all every shipyard's platform, exact to the pixel: citygen.cut_yards. Its graving
                    # docks are cut with it, straight-walled: laid as a road a dock was graded 30 m out to either
                    # side, and the ground under the halls beside it went with that)
                    settled = any(y.get("z") is not None for c in cities for y in c.get("shipyards") or [])
                    dock = cg.ROAD_MESH["DK"].split("/")[-1]
                    roads = cg.grade_to_roads(cg.cut_shores(cg.cut_yards(cg.refine(terrain, cg.HEIGHTMAP_RES), cities),
                                                            cities), {
                        str(k): {"points": p, "segments": [g for g in s if not (
                            settled and str(g.get("mesh") or "").split("/")[-1] == dock)]}
                        for k, (p, s) in enumerate(actors)}, locked)
                    public = roads
                    private = private_streets(plans, public)
                    if private:  # after the public roads, which keep every cell of their own where the two meet
                        own: set = set()
                        cut2 = cg.grade_to_roads(roads, {str(k): {"points": p, "segments": s}
                                                         for k, (p, s) in enumerate(private)}, own)
                        for j, i in locked:
                            cut2["heights"][j][i] = roads["heights"][j][i]
                        roads = cut2
                        locked |= own
                    _GROUND.update(key=rk, ter=roads, locked=locked, public=public)
                _GROUND["for"] = key  # (the sidewalk stage builds on this preview's own public ground)

            first_pad: dict = {}  # (x, y) -> the level a lot was first cut at

            def cut_and_fit():
                ter = terrain
                if gen and cut and terrain.get("heights"):
                    # every round cuts a lot where it was FIRST levelled: the fit lifts a building onto the highest
                    # ground under it, and cut again at that lifted floor each round raised the ground further
                    pads = pl.pads(placed, cg.pad_margin(terrain))
                    for p in pads:
                        first_pad.setdefault(p[:2], p[6])
                    ter = cg.cut_pads(_GROUND["ter"], [(*p[:6], first_pad[p[:2]]) for p in pads], _GROUND["locked"])
                return ter, fit_buildings(placed, ter, cut, ter is not terrain) if gen and terrain.get("heights") else None
            fit_ter, fit = cut_and_fit()
            # Nothing is built where the land falls more than pl.FOUND_MAX_M away under it: those buildings go, the
            # ground is cut again without their pads (which moves their neighbours' a little, so a few rounds) and
            # their lots are planted. Then the kerbs and fences that were theirs go too.
            lots, sites, lost = [], 0, {}
            for last in (False, False, False, True) if fit else ():
                gone, sites, went = pl.leave_out_deep(placed)
                lots += gone
                for name, n in went.items():
                    lost[name] = lost.get(name, 0) + n
                if not gone:
                    break
                if last:  # the fourth round's few go without another cut: their pads stay as level ground
                    fit["checked"] -= len(gone)
                else:
                    fit_ter, fit = cut_and_fit()
            if fit:
                fit["left_out"] = {"deeper_than_m": pl.FOUND_MAX_M, "buildings": len(lots), "planned_sites_kept": sites,
                                   "by_layer": lost}
            if lots:
                fit["left_out"]["trees"] = pl.plant_lots(placed, lots, fit["ground"], terrain.get("sea"))
                if "Driveway aprons" in placed:
                    rows = (pl.aprons(placed).get("Driveway aprons") or {}).get("points") or []
                    placed["Driveway aprons"].update(points=rows, count=len(rows), shown=len(rows))
                pl.fit_fences(placed, pl.road_samples(actors, 400.0) + pl.street_samples(cities))
            if fit:
                fit["roads"] = road_fit(fit["ground"], actors, terrain)
                for name, v in placed.items():  # the developments' pieces stand on the cut ground too (stalls on their slab)
                    if v.get("keep_z"):
                        continue  # (a shipyard's: built at the platform's level, on the bed or afloat)
                    if name == "Garden trees" or v.get("kind") == "site" and v.get("role") == "site":
                        # (an apron lies on its road's sidewalk: the ground there is ROAD_SINK_CM under the road)
                        up = 9 if v.get("mesh") == "SM_Kit_ParkingStall" else cg.ROAD_SINK_CM + 6 if v.get("on_road") else 0
                        for p in v["points"]:
                            p[2] = round(fit["ground"](p[0], p[1]) + up)
            # real bridges: a pier bent every 30 m under every deck and elevated ramp, down to the ground it stands
            # on (the cut ground once there is one) or the lake bed
            decks = {str(k): {"points": p, "segments": s} for k, (p, s) in enumerate(actors)}
            stands_on = (fit or {}).get("ground") or (lambda x, y: ground(x, y) or 0.0)
            cols, caps = cg.bridge_piers(decks, stands_on)
            # ... a cable-stayed or suspension bridge on its two towers, each astride the deck with its stay cables;
            # the deck those cables carry needs no piers
            towers = cg.bridge_towers(decks, (data.get(f"net.{nid}.water") or {}).get("bridges") or [], stands_on,
                                      terrain.get("sea"))
            held = cg.under_cables(towers["spans"])
            cols, caps = [r for r in cols if not held(r[0], r[1])], [r for r in caps if not held(r[0], r[1])]
            ends = cg.bridge_abutments(decks)
            # ... and where a deck or ramp is too low for piers it stands on its own approach wall, never on air
            fills = cg.deck_fills(decks, stands_on, terrain.get("sea"))
            for name, mesh, rows, box in (("Bridge piers", "SM_Kit_BridgePier", cols, [2.0, 2.0, 1.0]),
                                          ("Pier caps", "SM_Kit_PierCap", caps, [2.0, 1.0, 1.2]),
                                          ("Bridge abutments", "SM_Kit_BridgeAbutment", ends, [1.0, 1.0, 2.5]),
                                          ("Approach walls", "SM_Kit_ApproachWall", fills, [1.0, 1.0, 1.0]),
                                          ("Bridge towers", "SM_Kit_BridgePier", towers["legs"], [2.0, 2.0, 1.0]),
                                          ("Tower beams", "SM_Kit_PierCap", towers["beams"], [2.0, 1.0, 1.2]),
                                          ("Bridge cables", "SM_Kit_BridgeStay", towers["stays"], [1.0, 0.12, 1.0])):
                placed[name] = {"kind": "site", "role": "bridge", "mesh": mesh, "box_m": box, "tint": "fg-dim",
                                "count": len(rows), "shown": len(rows), "points": rows}
            _PREVIEW_CACHE.update(key=key, out=placed, fit=fit, ter=fit_ter)
        out = _PREVIEW_CACHE["out"]
        if layer_id:  # one layer, or several "a,b,c" (the panel asks for small layers together: fewer calls)
            pick: dict = {}
            for lid in str(layer_id).split(","):
                if lid == "road-walls":
                    # every built-in layer, the ones no layer of the world's own makes: bridges and approach walls,
                    # neighbourhood sites, and the trees planted in gardens, interchanges and unbuilt lots (sites
                    # only came once: the panel never drew a planted lot, it showed bare ground)
                    own = {d.get("name") for d in docs.values()}
                    pick.update({k: v for k, v in out.items() if k not in own})
                else:
                    name = (docs.get(slug(lid)) or {}).get("name")
                    if name in out:
                        pick[name] = out[name]
            out = pick
        if summary:  # counts only: a quick check for an agent, no point lists
            return {"ok": True, "id": nid, "counts": {k: v["count"] for k, v in out.items()},
                    "ground_fit": fit_summary(),
                    # what the last pass took off the roads and out of the buildings, per layer
                    "swept": _PREVIEW_CACHE.get("swept")}
        return {"ok": True, "id": nid, "layers": out}

    _GROUND: dict = {}

    def road_fit(ground, actors: list, terrain: dict) -> dict:
        """Every street point against the ground the buildings were fitted to: how many the ground covers (over)
        or leaves in the air (under) by more than 30 cm, and why. Bridges, viaducts, decks 20 m up and points
        over water stand free. A point off beside the water or beside a road at another height is a place the
        landscape cannot follow both (a wall or a bridge stands there); `unexplained` are the real misses."""
        n = over = under = 0
        worst = 0.0
        sea, raw = terrain.get("sea"), st.grid_sampler(terrain)
        bad: dict = {}
        worst_rows: list = []
        every: list = []  # every meshed segment, structures too: (ax, ay, az, bx, by, bz)
        grades: list = []  # every street segment's slope: a street steeper than 15 % is a kink the ground can't follow
        decks, deck_rows = [0, 0], []  # samples along decks and ramps, and those the ground reaches
        checks: list = []
        for pts, segs in actors:
            by = {p["i"]: p for p in pts}
            ids: dict = {}
            for s in segs:
                mesh = (s.get("mesh") or "").split("/")[-1]
                a, b = by.get(s["a"]), by.get(s["b"])
                if not mesh or not a or not b:
                    continue
                every.append((a["x"], a["y"], a["z"], b["x"], b["y"], b["z"]))
                if mesh in cg.STRUCTURES:  # a deck or ramp: checked along its length, the ground must stay under it
                    for k in range(5):
                        t = k / 4
                        x, y, z = a["x"] + (b["x"] - a["x"]) * t, a["y"] + (b["y"] - a["y"]) * t, a["z"] + (b["z"] - a["z"]) * t
                        decks[0] += 1
                        if ground(x, y) > z - 50 and not (sea is not None and (raw(x, y) or 0.0) < sea):
                            decks[1] += 1
                            deck_rows.append((round(ground(x, y) - z), round(x), round(y), round(z), mesh))
                if mesh not in cg.STRUCTURES and "Water_" not in mesh:
                    run = math.hypot(b["x"] - a["x"], b["y"] - a["y"])
                    if run > 300:
                        grades.append((abs(b["z"] - a["z"]) / run, round(a["x"]), round(a["y"]), round(run), mesh))
                    ids.setdefault(s["a"], mesh)
                    ids.setdefault(s["b"], mesh)
            checks += [(by[i], mesh) for i, mesh in ids.items()]
        ring = [(math.cos(k * math.pi / 4) * r, math.sin(k * math.pi / 4) * r) for r in (1500.0, 3000.0) for k in range(8)]

        def why(x, y, z):
            if sea is not None and any((raw(x + dx, y + dy) or 0.0) < sea for dx, dy in ring):
                return "at the water's edge"
            for ax, ay, az, bx, by_, bz in every:
                ux, uy = bx - ax, by_ - ay
                t = max(0.0, min(1.0, ((x - ax) * ux + (y - ay) * uy) / (ux * ux + uy * uy or 1.0)))
                if math.hypot(x - ax - ux * t, y - ay - uy * t) < 4000 and abs(az + (bz - az) * t - z) > 150:
                    return "beside or across a road at another height"
            return "unexplained"
        reasons: dict = {}
        for p, mesh in checks:
            gap = ground(p["x"], p["y"]) - p["z"] + cg.ROAD_SINK_CM  # measured from where the ground should be
            if gap < -2000 or (sea is not None and (raw(p["x"], p["y"]) or 0.0) < sea):
                continue
            n += 1
            over += gap > 30
            under += gap < -30
            worst = max(worst, abs(gap))
            if abs(gap) > 30:
                bad[mesh] = bad.get(mesh, 0) + 1
                r = why(p["x"], p["y"], p["z"])
                reasons[r] = reasons.get(r, 0) + 1
                worst_rows.append((round(gap), round(p["x"]), round(p["y"]), round(p["z"]), mesh, r))
        worst_rows.sort(key=lambda r: -abs(r[0]))
        loose = [r for r in worst_rows if r[5] == "unexplained"]
        # a junction's mesh is flat at its node: every arm must arrive at that height, or the road steps at its mouth
        steps = []
        for pts, segs in actors:
            by = {p["i"]: p for p in pts}
            for node, arms, _a in st.pending_nodes(pts, segs, True):
                if node in by:
                    steps.append(max([abs(by[m]["z"] - by[node]["z"]) for m in arms if m in by] or [0.0]))
        junction_steps = {"checked": len(steps), "stepped": sum(1 for s in steps if s > 10), "over_50cm": sum(1 for s in steps if s > 50),
                          "worst_cm": round(max(steps or [0.0]))}
        return {"checked": n, "ground_over_road": over, "road_in_air": under, "worst_cm": round(worst), "by_mesh": bad,
                "why": reasons, "unexplained": len(loose), "junction_steps": junction_steps,
                "decks": {"samples": decks[0], "ground_through_deck": decks[1],
                          "worst": sorted(deck_rows, reverse=True)[:6], "row": "ground minus deck cm, x, y, deck z, mesh"},
                "steep": {"segments": len(grades), "over_15pct": sum(1 for g in grades if g[0] > 0.15),
                          "over_30pct": sum(1 for g in grades if g[0] > 0.30),
                          "worst": [(round(g[0] * 100), *g[1:]) for g in sorted(grades, reverse=True)[:6]],
                          "row": "grade %, x, y, run cm, mesh"},
                "unexplained_over": sum(1 for r in loose if r[0] > 0), "unexplained_worst_cm": abs(loose[0][0]) if loose else 0,
                "over_1m": sum(1 for r in worst_rows if r[0] > 100), "in_air_1m": sum(1 for r in worst_rows if r[0] < -100),
                "worst_unexplained": loose[:10],
                "worst_over": [r for r in worst_rows if r[0] > 0][:6], "worst_in_air": [r for r in worst_rows if r[0] < 0][:6],
                "row": "ground minus road cm, x, y, road z, mesh, why"}

    def loose_ends(nid: str, terrain: dict) -> dict:
        """Every open end of a freeway, ramp or highway as it was BUILT (the spline points, not the plan): it must
        land on another road, at that road's height. far = no road within 26 m; stepped = a road is there but more
        than 1 m above or below it. An end at the map's edge is the road leaving the world."""
        roads = []
        for aslug, head in actor_docs(nid).items():
            label = str(head.get("label") or "")
            if " before kit" in label or label.endswith((" Paths", " Access")):
                continue
            _, pts, segs = load_actor(nid, aslug)
            roads.append((label, {p["i"]: p for p in pts}, segs))
        every = [(label, by[s["a"]], by[s["b"]]) for label, by, segs in roads for s in segs
                 if s.get("mesh") and s["a"] in by and s["b"] in by]
        (x0, y0), (x1, y1) = terrain.get("min") or (-1e12, -1e12), terrain.get("max") or (1e12, 1e12)
        ends = edge = 0
        bad = []
        for label, by, segs in roads:
            if not label.endswith(("Freeways", "Highways")):
                continue
            touch: dict = {}
            for s in segs:
                for i in (s["a"], s["b"]):
                    touch.setdefault(i, []).append(s)
            for i, ss in touch.items():
                p = by.get(i)
                if not p or len(ss) != 1 or not ss[0].get("mesh") or (p.get("mesh") and p["mesh"] not in rp.CONNECTORS):
                    continue  # (a ramp's end carries its connector piece: still an end to check)
                ends += 1
                if min(p["x"] - x0, x1 - p["x"], p["y"] - y0, y1 - p["y"]) < 20000:
                    edge += 1
                    continue
                near, level, onto = 1e12, None, ""  # nearest road in plan, the smallest step among the roads in reach
                mine = cg.own_run(touch, by, i)  # never its own road: the piece behind it is no road it meets
                for lab2, a, b in every:
                    if lab2 == label and a["i"] in mine and b["i"] in mine:
                        continue
                    ux, uy = b["x"] - a["x"], b["y"] - a["y"]
                    t = max(0.0, min(1.0, ((p["x"] - a["x"]) * ux + (p["y"] - a["y"]) * uy) / (ux * ux + uy * uy or 1.0)))
                    d = math.hypot(p["x"] - a["x"] - ux * t, p["y"] - a["y"] - uy * t)
                    near = min(near, d)
                    if d <= 3500:  # a ramp stops beside its road or 15 m short of a kerb: its gore / flare spans the rest
                        dz = a["z"] + (b["z"] - a["z"]) * t - p["z"]
                        if level is None or abs(dz) < abs(level):
                            level, onto = dz, f"{lab2} {round(d)} cm"
                mesh = ss[0]["mesh"].split("/")[-1]
                if level is None:
                    bad.append(("far", round(p["x"]), round(p["y"]), round(p["z"]), round(near), label, mesh))
                elif abs(level) > 100:
                    bad.append(("stepped", round(p["x"]), round(p["y"]), round(p["z"]), round(level), label, mesh, onto))
        # a road that changes width at a plain point (no junction piece there): the two meshes do not meet
        half_of = {m.split("/")[-1]: cg.HALF[k] for k, m in cg.ROAD_MESH.items()}
        jumps = []
        for label, by, segs in roads:
            at: dict = {}
            for s in segs:
                for i in (s["a"], s["b"]):
                    at.setdefault(i, []).append(s)
            for i, ss in at.items():
                p = by.get(i)
                if p and len(ss) == 2 and all(s.get("mesh") for s in ss) and not p.get("mesh"):
                    h = [half_of.get(s["mesh"].split("/")[-1], 0.0) for s in ss]
                    if abs(h[0] - h[1]) > 100:
                        jumps.append((round(abs(h[0] - h[1]) * 2), round(p["x"]), round(p["y"]), label,
                                      ss[0]["mesh"].split("/")[-1], ss[1]["mesh"].split("/")[-1]))
        # one road laid along another: two pieces that run the same way with their middles closer than 0.6 of
        # their half widths together and share no point. 'in' = at the same level (one inside the other),
        # 'over' = a deck lengthwise over a road on the ground
        flat = [(label, by[s["a"]], by[s["b"]], s["mesh"].split("/")[-1]) for label, by, segs in roads for s in segs
                if s.get("mesh") and s["a"] in by and s["b"] in by]
        cells: dict = {}
        for k, (_l, a, b, _m) in enumerate(flat):
            cells.setdefault((int((a["x"] + b["x"]) / 2 // 6000), int((a["y"] + b["y"]) / 2 // 6000)), []).append(k)
        lap = []
        for k, (l1, a, b, m1) in enumerate(flat):
            mx, my, mz = (a["x"] + b["x"]) / 2, (a["y"] + b["y"]) / 2, (a["z"] + b["z"]) / 2
            ux, uy = b["x"] - a["x"], b["y"] - a["y"]
            ul = math.hypot(ux, uy)
            if ul < 300:
                continue
            for gx in range(int(mx // 6000) - 1, int(mx // 6000) + 2):
                for gy in range(int(my // 6000) - 1, int(my // 6000) + 2):
                    for j in cells.get((gx, gy), ()):
                        l2, c, d, m2 = flat[j]
                        if j <= k or (l1 == l2 and {a["i"], b["i"]} & {c["i"], d["i"]}):
                            continue
                        vx, vy = d["x"] - c["x"], d["y"] - c["y"]
                        vl = math.hypot(vx, vy)
                        if vl < 300 or abs(ux * vx + uy * vy) / (ul * vl) < 0.94:
                            continue
                        t = ((mx - c["x"]) * vx + (my - c["y"]) * vy) / (vl * vl)
                        if not 0.0 <= t <= 1.0:
                            continue  # beyond its end: the next piece of the same line, not one laid along it
                        off = math.hypot(mx - c["x"] - vx * t, my - c["y"] - vy * t)
                        if off < 0.6 * (half_of.get(m1, 500.0) + half_of.get(m2, 500.0)):
                            dz = mz - (c["z"] + (d["z"] - c["z"]) * t)
                            lap.append(("in" if abs(dz) < 300 else "over", round(off), round(mx), round(my), round(dz),
                                        f"{l1} {m1}", f"{l2} {m2}",
                                        [[q["i"], round(q["x"]), round(q["y"]), round(q["z"])] for q in (a, b, c, d)]))
        # a freeway or ramp nobody could drive: a piece steeper than 15 %, shorter than 3 m, or a bend sharper than
        # 60 degrees at one point (an end pulled onto its road with too little ramp left to get there)
        shape = []
        for label, by, segs in roads:
            if not label.endswith(("Freeways", "Highways")):
                continue
            at = {}
            for s in segs:
                a, b = by.get(s["a"]), by.get(s["b"])
                if not s.get("mesh") or not a or not b:
                    continue
                run = math.hypot(b["x"] - a["x"], b["y"] - a["y"])
                grade = abs(b["z"] - a["z"]) / max(run, 1.0)
                mesh = s["mesh"].split("/")[-1]
                if grade > cg.SETTLE_GRADE + 0.005 or run < 300:
                    shape.append(("steep" if grade > cg.SETTLE_GRADE + 0.005 else "short", round(grade * 100), round(a["x"]), round(a["y"]),
                                  round(a["z"]), round(b["z"]), round(run), label, mesh, a.get("mesh") or b.get("mesh") or ""))
                for p, q in ((a, b), (b, a)):
                    at.setdefault(p["i"], []).append((q, run, mesh))
            for i, legs in at.items():
                if len(legs) == 2 and min(legs[0][1], legs[1][1]) > 1:
                    p, (q0, r0, mesh), (q1, r1, _m) = by[i], *legs
                    cos = ((p["x"] - q0["x"]) * (q1["x"] - p["x"]) + (p["y"] - q0["y"]) * (q1["y"] - p["y"])) / (r0 * r1)
                    turn = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
                    if turn > 60:  # (a loop ramp turns ~50 degrees a point by design: the spline rounds it)
                        shape.append(("kinked", round(turn), round(p["x"]), round(p["y"]), round(p["z"]), round(p["z"]),
                                      round(min(r0, r1)), label, mesh, p.get("mesh") or ""))
        pairs: dict = {}
        for r in lap:
            key = f"{r[0]}: {r[5].rsplit(' ', 1)[0].split()[-1]} / {r[6].rsplit(' ', 1)[0].split()[-1]}"
            pairs[key] = pairs.get(key, 0) + 1
        return {"laid_along": {"in": sum(1 for r in lap if r[0] == "in"), "over": sum(1 for r in lap if r[0] == "over"),
                               "by_actors": pairs, "worst": sorted(lap, key=lambda r: (r[0] != "in", r[1]))[:12],
                               "row": "in | over, cm between their middles, x, y, cm above the other, piece, piece, "
                                      "the two pieces' end points [index, x, y, z]"},
                "shape": {k: sum(1 for r in shape if r[0] == k) for k in ("steep", "short", "kinked")},
                "shape_worst": sorted((r for r in shape if r[0] == "steep"), key=lambda r: -r[1])[:10]
                + sorted((r for r in shape if r[0] != "steep"), key=lambda r: -r[1])[:6],
                "shape_row": "steep (grade %) | short | kinked (degrees), x, y, z, z at the other end, run cm, actor, mesh, connector",
                "ends": ends, "at_map_edge": edge, "far": sum(1 for b in bad if b[0] == "far"),
                "width_jumps": len(jumps), "width_jump_at": sorted(jumps, reverse=True)[:8],
                "width_row": "cm wider, x, y, actor, mesh, mesh",
                "stepped": sum(1 for b in bad if b[0] == "stepped"), "worst": sorted(bad, key=lambda b: -abs(b[4]))[:12],
                "row": "far | stepped, x, y, z, cm to the nearest road | cm above (+) or below (-) it, actor, mesh[, the road it meets]"}

    def ground_fit(net_id: str = "", heights: bool = True) -> dict:
        """The ground as the city leaves it: every lot cut level, every road cut and filled in, on a grid the
        panel draws ({grid, heights[j][i] at cell centres, cm}), with the building and road fit. It follows the
        city: any change to the layers, cities or roads gives a new ground. heights=false: the fit only."""
        nid = slug(net_id)
        got = pcg_preview(nid, summary=True)
        if got.get("ok") is False:
            return got
        ter = (data.get(f"net.{nid}.scan") or {}).get("terrain") or {}
        out = {"ok": True, "id": nid, "grid": 0, "ground_fit": fit_summary()}
        if not heights:
            out["ramp_ends"] = loose_ends(nid, ter)
        fine = _PREVIEW_CACHE.get("ter") or {}
        if not heights or fine.get("grid") != cg.HEIGHTMAP_RES or not ter.get("heights"):
            return out  # no ground of its own yet (laid over another world's landscape, no heightmap made)
        grid = cg.HEIGHTMAP_RES // 2  # two landscape pixels a cell: light enough to redraw, still shows every cut
        (x0, y0), (x1, y1) = ter["min"], ter["max"]
        span = 2 * grid / cg.HEIGHTMAP_RES
        if _EXACT.get("ground_key") != (_PREVIEW_CACHE.get("key"), grid):
            H = fine["heights"]
            cw, ch = (x1 - x0) * span / grid, (y1 - y0) * span / grid
            known = st.grid_sampler(ter)  # cells with no landscape under them stay empty
            locked = _GROUND.get("locked") or ()

            def cell(j, i):
                # A cell with a road in it shows the road's level, not the mean with the pad or bank beside it: the
                # panel's cells are 20 m, as wide as a street, and the averaged ground lay over half the carriageway
                four = ((2 * j, 2 * i), (2 * j, 2 * i + 1), (2 * j + 1, 2 * i), (2 * j + 1, 2 * i + 1))
                vals = [H[a][b] for a, b in four if (a, b) in locked] or [H[a][b] for a, b in four]
                return round(sum(vals) / len(vals))
            rows = [[cell(j, i) if known(x0 + (i + 0.5) * cw, y0 + (j + 0.5) * ch) is not None else None
                     for i in range(grid)] for j in range(grid)]
            _EXACT.update(ground_key=(_PREVIEW_CACHE.get("key"), grid), ground_rows=rows)
        return {**out, "grid": grid, "heights": _EXACT["ground_rows"],
                "max": [x0 + (x1 - x0) * span, y0 + (y1 - y0) * span]}

    _EXACT: dict = {}

    def pcg_exact(net_id: str = "", x: float = 0.0, y: float = 0.0, radius_m: float = 400.0, summary: bool = False) -> dict:
        """What the push places within radius_m of (x, y), before anything is pushed: every building, site and wall
        piece as its mesh's real box ([name, min x, y, z, max x, y, z] + rows [x, y, z, yaw, sx, sy, sz]) and the
        ground under them sampled as the landscape has it. The panel draws it; summary=true: counts only."""
        nid = slug(net_id)
        got = pcg_preview(nid)
        if got.get("ok") is False:
            return got
        if _EXACT.get("key") != _PREVIEW_CACHE.get("key"):  # ponytail: one entry, as the preview
            solid = {k: v for k, v in (got.get("layers") or {}).items() if v.get("kind") in ("buildings", "site", "walls")}
            _EXACT.update(key=_PREVIEW_CACHE.get("key"), groups=kit_bake.bake(solid, "", interiors_of(nid))[0])
        r = float(radius_m) * 100
        meshes, rows, unknown = [], [], {}
        for path, rs in _EXACT["groups"].items():
            name = path.rsplit(".", 1)[-1]
            near = [p for p in rs if abs(p[0] - x) <= r and abs(p[1] - y) <= r]
            box = kit_bake.bounds(name)
            if near and box:
                meshes.append([name, *box])
                rows.append(near)
            elif near:
                unknown[name] = len(near)
        out = {"ok": True, "id": nid, "x": x, "y": y, "r": r, "pieces": sum(len(v) for v in rows),
               "unknown": unknown, "ground_fit": fit_summary()}
        if summary:
            return {**out, "meshes": {m[0]: len(v) for m, v in zip(meshes, rows)}}
        ter = (data.get(f"net.{nid}.scan") or {}).get("terrain") or {}
        ground = (_PREVIEW_CACHE.get("fit") or {}).get("ground")
        if ground and ter.get("heights"):  # on the heightmap's own pixels: the landscape's real triangles
            (x0, y0), (x1, y1) = ter["min"], ter["max"]
            dx, dy = (x1 - x0) / (cg.HEIGHTMAP_RES - 1), (y1 - y0) / (cg.HEIGHTMAP_RES - 1)
            i0, i1 = max(0, int((x - r - x0) // dx)), min(cg.HEIGHTMAP_RES - 1, int((x + r - x0) // dx) + 1)
            j0, j1 = max(0, int((y - r - y0) // dy)), min(cg.HEIGHTMAP_RES - 1, int((y + r - y0) // dy) + 1)
            if i1 > i0 and j1 > j0:
                out["ground"] = {"x": x0 + i0 * dx, "y": y0 + j0 * dy, "dx": dx, "dy": dy,
                                 "z": [[round(ground(x0 + i * dx, y0 + j * dy)) for i in range(i0, i1 + 1)]
                                       for j in range(j0, j1 + 1)]}
        return {**out, "meshes": meshes, "rows": rows}

    def kit_root() -> str:
        folder = road_folder()
        return folder.split("/WorldGen/Roads")[0] if "/WorldGen/Roads" in folder else ""

    def kit_materials(net_id: str = "", street_kit: bool = True) -> dict:
        root = kit_root()
        if not root:
            return {"ok": False, "error": "Couldn't find the project's WorldGen folder (import the roads first)."}
        street = street_kit_build(root) if street_kit else None
        # Slices of 5 meshes, each its own editor call. Loading a kit mesh compiles its materials' shaders (up to
        # ~4 GB each, freed a little later): the listener stops a slice once UEFN grew 3 GB, and we wait for it
        # to settle before the next. One call for the whole kit took UEFN past 100 GB and crashed it.
        folders = ["City", "Kit", "Kit/Street", "Kit/Facility", "BuildingKit"]
        out = {"ok": True, "instances": 0, "created": 0, "meshes": 0, "overrides": {}}
        offset = 0
        while offset is not None:
            base = uefn_memory.uefn_gb()
            args = {"root": root, "folders": folders, "offset": offset, "limit": 5, "max_grow_gb": 3.0}
            r = listener_long("road_network_kit_materials", args)
            uefn_memory.settle(base)
            if r.get("late"):  # the reply (and its overrides) was lost: the same slice again is quick now
                r = listener_long("road_network_kit_materials", args)
                uefn_memory.settle(base)
            if not r.get("ok"):
                return {**r, "street_kit": street}
            out["overrides"].update(r.get("overrides") or {})
            out["created"] += r.get("created") or 0
            out["master"] = r.get("master")
            nxt = r.get("next")
            offset = None if nxt is None or nxt <= offset else nxt  # no progress: stop rather than loop
        out["meshes"] = len(out["overrides"])
        out["instances"] = len({m for v in out["overrides"].values() for m in v if m})
        return {**out, "street_kit": street}

    def street_kit_build(root: str, rebuild: bool = False) -> dict:
        """The missing street kit pieces, ONE listener call (one editor frame) per mesh, then one save call.
        Every mesh build and save opens a progress window, and UEFN frees a window's GPU swap chain only when
        the frame ends: 16 meshes in one call stacked 32 swap chains in one frame, took UEFN past 120 GB and
        crashed it on the D3D12 descriptor limit."""
        # every road piece the project has no model of is built from its profile too (a project without the
        # Blender road kit still gets real decks, kerbs, sidewalks, parapets and paint; a model replaces it)
        roads = {m: rp.kit_entry(p) for m, p in rp.all_profiles().items()}
        todo = listener("road_network_street_kit", {"root": root, "rebuild": rebuild, "plan": True,
                                                    "extra": roads}).get("todo") or []
        made, errors = [], []
        late = False
        for name in todo:
            # a road piece comes with the plugin as a model (assets/roads/<name>.fbx, built in Blender from the same
            # profile: Jersey barriers, chamfered kerbs, tapered decks); the box build below is the fallback
            fbx = rp.bundled(name)
            if name in roads and fbx:
                dest = f"{root}/WorldGen/{roads[name][2]}"
                try:
                    got = listener_long("import_asset", {"source_file": fbx, "destination_path": dest, "replace_existing": False})
                except Exception as exc:
                    got = {"ok": False, "error": str(exc)}
                if isinstance(got, dict) and got.get("ok") is not False and not got.get("error"):
                    made.append(f"{dest}/{name}")
                    continue
            got = listener_long("road_network_street_kit",
                                {"root": root, "rebuild": rebuild, "only": [name], "save": False,
                                 "extra": {name: roads[name]} if name in roads else {}})
            late = late or bool(got.get("late"))
            made += got.get("paths") or []
            errors += got.get("errors") or []
        if made:
            listener_long("road_network_save_assets", {"paths": made})
        if late:  # a lost reply lost that piece's paths too: save whatever content is still unsaved
            listener_long("save_all_dirty", {"content": True, "maps": False})
        return {"ok": not errors, "made": [p.rsplit("/", 1)[-1] for p in made], "errors": errors}

    BAKE_CHUNK = 1500  # points per Create Points node: keeps each node's settings a sensible size

    # ---------------------------------------------------------------- the pipeline (backend/pipeline.py)
    def sidewalks_stage(nid: str) -> dict:
        """SidewalkGenerator: each city's '<City> Paths' spline actor (missing sidewalks + every walkway and
        driveway of the previewed lots), stored like a road actor so Push roads pastes it when it changed."""
        doc = data.get(f"net.{nid}") or {}
        opts = (doc.get("generated") or {}).get("options")
        ter = (data.get(f"net.{nid}.scan") or {}).get("terrain") or {}
        ground = (lambda x, y: st.sample_grid(ter, x, y) or 0.0) if ter.get("heights") else (lambda x, y: 0.0)
        folder = road_folder()
        towns = list(city_docs(nid).values())
        if not towns or "/WorldGen/Roads" not in folder:
            return {"ok": False, "error": "No generated cities or road folder yet."}
        got = pcg_preview(nid)
        # ... all of them laid on the ground the public roads left (the preview's own, when it cut one): on the raw
        # hills a private street dipped or climbed where its public street was cut or filled
        if _GROUND.get("public") and _GROUND.get("for") == _PREVIEW_CACHE.get("key"):
            on_public = st.grid_sampler(_GROUND["public"])
            ground = lambda x, y: on_public(x, y) or 0.0
        rows_by: dict = {t["name"]: [] for t in towns}
        for layer in (got.get("layers") or {}).values():
            if layer.get("kind") != "buildings":
                continue
            for r in layer.get("points") or []:
                t = min(towns, key=lambda c: (c["x"] - r[0]) ** 2 + (c["y"] - r[1]) ** 2)
                rows_by[t["name"]].append(r)
        out = {}
        # no private street runs across a freeway, a ramp or a highway (they are laid after the blocks are cut)
        towns = [{**t, "private": {**(t.get("private") or {}), "streets": streets}} for t, streets in private_plans(nid, towns)]
        for t in towns:
            for label, actor in ((f"{t['name']} Paths", cg.paths_actor(t, opts, rows_by[t["name"]], ground, folder,
                                                                        cg.wet_from(ter))),
                                 (f"{t['name']} Access", cg.access_actor(t, rows_by[t["name"]], ground, folder))):
                if actor["segments"]:
                    out[label] = store_actor(nid, label, actor, doc.get("name") or nid)["stats"]
        doc = data.get(f"net.{nid}") or {}
        doc["actors"] = sorted(set(doc.get("actors") or []) | set(out))
        data.put(f"net.{nid}", doc)
        push({"what": "network", "id": nid})
        return {"ok": True, "actors": out}

    def pipeline_run(net_id: str = "", run_from: str = "", confirm: bool = False) -> dict:
        """Status of every stage, or (run_from) re-run that stage and everything downstream, offline."""
        nid = slug(net_id)
        doc = data.get(f"net.{nid}") if nid else None
        if not doc:
            return {"ok": False, "error": f"No road network {net_id!r}."}
        towns = list(city_docs(nid).values())
        ter = (data.get(f"net.{nid}.scan") or {}).get("terrain") or {}
        gen = doc.get("generated") or {}
        ran, notes = [], []
        todo = pipeline.after(run_from) if run_from else []
        if run_from and not todo:
            return {"ok": False, "error": f"Unknown stage {run_from!r}: one of {pipeline.ORDER}."}
        if todo and not confirm:
            return {"ok": False, "error": f"This re-runs {', '.join(todo)} for {doc.get('name')}. Call again with "
                                          f"confirm=true.", "stages": todo}
        for stage in todo:
            if stage in ("terrain", "planner", "roads", "blocks"):
                if "roads" in ran:
                    continue
                if not gen:
                    return {"ok": False, "error": "Only generated worlds can re-run their roads."}
                r = generate(doc.get("name") or nid, gen.get("seed", 7), "", gen.get("size_km", 6), gen.get("relief_m", 60),
                             gen.get("ground_from") or nid, "", *(gen.get("center") or [0, 0]), "")
                if not r.get("ok"):
                    return r
                ran += ["terrain", "planner", "roads", "blocks"]
            elif stage == "demand":  # planned with the blocks in generate (city_layout -> urban.plan)
                if "blocks" not in ran:
                    r = generate(doc.get("name") or nid, gen.get("seed", 7), "", gen.get("size_km", 6),
                                 gen.get("relief_m", 60), gen.get("ground_from") or nid, "",
                                 *(gen.get("center") or [0, 0]), "")
                    if not r.get("ok"):
                        return r
                    ran += ["terrain", "planner", "roads", "blocks"]
                ran.append(stage)
            elif stage in ("foundations", "buildings"):
                _PREVIEW_CACHE.clear()
                ran.append(stage)
            elif stage == "sidewalks":
                r = sidewalks_stage(nid)
                ran.append(stage)
                if not r.get("ok"):
                    notes.append(r.get("error"))
            elif stage == "road_meshes":
                notes.append("RoadMeshGenerator runs in UEFN: Push roads (only changed actors are pasted).")
        towns = list(city_docs(nid).values())
        doc = data.get(f"net.{nid}") or doc  # the stages may have changed it (new actors, new cities)
        got = pcg_preview(nid, summary=True) if towns else {}
        counts = got.get("counts") or {}
        stages = []
        for s in pipeline.STAGES:
            info: dict = {}
            if s["id"] == "terrain":
                flat = [v for row in ter.get("heights") or [] for v in row if v is not None]
                wdoc = data.get(f"net.{nid}.water") or {}
                info = {"grid": ter.get("grid"), "slope": pipeline.slope_map(ter), "keep_zones": len(gen.get("keep") or []),
                        "water_level_cm": ter.get("sea"),
                        "height_cm": [round(min(flat)), round(max(flat))] if flat else None,
                        "below_water_cells": sum(1 for v in flat if ter.get("sea") is not None and v < ter["sea"]),
                        "waterbodies": [(b["type"], b["area_m2"]) for b in wdoc.get("bodies") or []]}
            elif s["id"] == "planner":
                info = {"cities": [{"name": t["name"], "style": t.get("style"), "radius_m": round(t["radius"] / 100),
                                    "orientation_deg": round(cg.orientation(t), 1),
                                    "districts": {k: v.get("radius") for k, v in (t.get("districts") or {}).items()}}
                                   for t in towns]}
            elif s["id"] == "roads":
                info = {"actors": [a for a in doc.get("actors") or [] if not a.endswith(" Paths")]}
            elif s["id"] == "sidewalks":
                info = {"actors": [a for a in doc.get("actors") or [] if a.endswith(" Paths")]}
            elif s["id"] == "blocks":
                info = {"lots": {t["name"]: len(t.get("lots") or []) for t in towns}}
            elif s["id"] == "demand":
                info = {t["name"]: {"population": ((t.get("plan") or {}).get("population") or {}).get("total"),
                                    "facilities": (t.get("plan") or {}).get("counts"),
                                    "coverage": (t.get("plan") or {}).get("coverage"),
                                    "unmet": [u["type"] for u in (t.get("plan") or {}).get("unmet") or []]}
                        for t in towns}
            elif s["id"] in ("foundations", "buildings"):
                info = {"placed": sum(v for k, v in counts.items()), "layers": len(counts)}
            stages.append({**s, "ran": s["id"] in ran, "status": info})
        return {"ok": True, "id": nid, "ran": ran, "notes": notes, "stages": stages,
                "tree": "CityGenerator\n" + "\n".join(f"  {s['name']}: {', '.join(s['parts'])}" for s in pipeline.STAGES)}

    def legacy_pcg_cleanup(nid: str, root: str, name: str, tag: str) -> list:
        """Before the first tiled build: the whole-world (1.11) and whole-city (1.15) volumes and graphs of this
        world, matched by their exact names only (tiles, roads and everything else are never touched)."""
        cities = [c["name"] for c in city_docs(nid).values()]
        labels = {f"City Builder {name}"} | {f"City Builder {name} {c}" for c in cities}
        removed = []
        for a in epic("EditorToolset.LevelToolset", "FindActors", {"filter": {"name": f"City Builder {name}"}}).get("returnValue", []):
            if str(a.get("label") or "") in labels:
                epic("EditorToolset.LevelToolset", "RemoveFromScene", {"actor": {"refPath": a["actorPath"]}})
                removed.append(a.get("label"))
        for g in [f"PCG_{tag}_City"] + [f"PCG_{tag}_{re.sub(r'[^A-Za-z0-9]', '', c)}" for c in cities]:
            path = f"{root}/WorldGen/CityPCG/{g}"
            if epic("EditorToolset.AssetToolset", "Exists", {"path": path}).get("returnValue"):
                epic("EditorToolset.AssetToolset", "Delete", {"path": path})
                removed.append(g)
        return removed

    @one_run_at_a_time
    def sync_status(net_id: str = "") -> dict:
        """How far UEFN is behind this world (offline: against what was last pushed, nothing is read from the level):
        road actors whose splines changed since their push, PCG tiles to (re)build or remove. Generating is never
        blocked by this; it only says what the next build in UEFN will change."""
        nid = slug(net_id)
        doc = data.get(f"net.{nid}") if nid else None
        if not doc:
            return {"ok": False, "error": f"No road network {net_id!r}."}
        changed, never, same = [], [], 0
        for aslug in actor_docs(nid):
            head, pts, segs = load_actor(nid, aslug)
            digest = hashlib.sha1(st.serialize(pts, segs).encode()).hexdigest()
            if not head.get("pushed"):
                never.append(head.get("label") or aslug)
            elif head["pushed"] != digest:
                changed.append(head.get("label") or aslug)
            else:
                same += 1
        tiles = {"build": 0, "remove": 0, "same": 0}
        folder = road_folder()
        state = data.get(f"net.{nid}.pcg") or {"tiles": {}}
        if "/WorldGen/Roads" in folder:
            got = pcg_preview(nid)
            if got.get("ok"):
                groups = kit_bake.bake(got.get("layers") or {}, folder.split("/WorldGen/Roads")[0], interiors_of(nid))[0]
                build, remove, keep = kit_bake.tile_diff(kit_bake.tiles(groups), state.get("tiles") or {}, False)
                tiles = {"build": len(build), "remove": len(remove), "same": len(keep)}
        pushed_any = same or changed or state.get("tiles")
        behind = len(changed) + len(never) + tiles["build"] + tiles["remove"]
        return {"ok": True, "id": nid, "in_sync": behind == 0, "never_built": not pushed_any,
                "roads_changed": changed, "roads_never_pushed": never, "roads_same": same, "tiles": tiles,
                "plugin_version": plugin_version().get("version"),
                "summary": ("UEFN matches this world" if behind == 0 else
                            "Never built in UEFN" if not pushed_any else
                            f"UEFN is behind: {len(changed) + len(never)} road actors, "
                            f"{tiles['build'] + tiles['remove']} city tiles to update")}

    _PCG_JOBS: dict = {}  # world id -> the build job running (or last run) for it

    def pcg_build(net_id: str = "", confirm: bool = False, force: bool = False, wait: bool = True,
                  stop: bool = False) -> dict:
        """The world's previewed city as PCG in the open UEFN level, in tiles (one graph + volume each).
        Only tiles whose content changed since the last build are rebuilt, tiles gone from the plan are
        removed, and every other tile, the roads and the rest of the level are left alone: edit the plan,
        build again, and just the edited part changes. force=true rebuilds every tile. The build runs as a
        background job in batches; wait=false returns at once, a later call reports progress, stop=true
        stops it after the batch in flight."""
        nid = slug(net_id)
        doc = data.get(f"net.{nid}") if nid else None
        if not doc:
            return {"ok": False, "error": f"No road network {net_id!r}."}
        last = _PCG_JOBS.get(nid)
        if last and last["running"]:
            if stop:
                last["stop"] = True
            return {"ok": True, "running": True, "stopping": last["stop"], "done": last["done"],
                    "total": last["total"], "seconds": round(time.time() - last["started"]),
                    "minutes_left": round(last.get("eta_s", 0) / 60, 1),
                    "uefn_gb": uefn_memory.uefn_gb()}
        if stop:
            return {"ok": True, "running": False, "last_build": (last or {}).get("result")}
        folder = road_folder()
        if "/WorldGen/Roads" not in folder:
            return {"ok": False, "error": "Couldn't find the project's WorldGen folder (import the roads first)."}
        root = folder.split("/WorldGen/Roads")[0]
        got = pcg_preview(nid)
        if not got.get("ok"):
            return got
        groups, baked, missing = kit_bake.bake(got.get("layers") or {}, root, interiors_of(nid))
        name = doc.get("name") or nid
        tag = re.sub(r"[^A-Za-z0-9]", "", name)
        new = kit_bake.tiles(groups)
        state = data.get(f"net.{nid}.pcg") or {"name": f"{name} / PCG tiles", "tiles": {}}
        orphans = []
        try:  # pull: what is really in the level decides, never only what we remember
            present = {str(a.get("label") or "") for a in epic("EditorToolset.LevelToolset", "FindActors", {
                "filter": {"name": f"City Builder {name}"}}).get("returnValue", [])}
            # a tile whose volume was deleted in the level is built again
            for key in [k for k in state["tiles"] if f"City Builder {name} {k}" not in present]:
                state["tiles"][key] = {**state["tiles"][key], "hash": "gone from the level", "volume": ""}
            # a volume in the level that the plan no longer has and we no longer track (a build that was cut
            # short): removed too, or its old instances stay forever
            pre = f"City Builder {name} "
            orphans = sorted(lab[len(pre):] for lab in present
                             if lab.startswith(pre) and lab[len(pre):] not in new and lab[len(pre):] not in state["tiles"])
        except (ConnectionError, RuntimeError, TimeoutError, ValueError):
            pass  # UEFN not reachable: diff against what was pushed last
        build, remove, same = kit_bake.tile_diff(new, state["tiles"], force)
        remove = sorted(set(remove) | set(orphans))
        if not confirm:
            return {"ok": False, "error": f"{name}: {len(build)} PCG tiles to (re)build, {len(remove)} to remove, "
                                          f"{len(same)} unchanged (left alone). {sum(baked.values())} buildings and "
                                          f"street pieces on {len(groups)} kit meshes. Call again with confirm=true.",
                    "build": build, "remove": remove, "unchanged": len(same),
                    "last_build": (last or {}).get("result")}
        if not build and not remove:
            return {"ok": True, "id": nid, "note": "Nothing changed since the last build: every tile left as it is.",
                    "unchanged": len(same), "last_build": (last or {}).get("result")}
        job = _PCG_JOBS[nid] = {"running": True, "stop": False, "done": 0, "total": len(build) + len(remove),
                                "started": time.time(), "result": None}

        def work() -> dict:
            mats = None
            if build:
                # the kit's instance-safe materials: made once, then reused. Making them loads every kit mesh
                # (minutes); a build that only needs meshes it already has overrides for skips that.
                cache = data.get(f"net.{nid}.mats") or {}
                need = {m for k in build for m in new[k]["meshes"] if m.startswith(f"{root}/WorldGen/")}
                if need and need <= set(cache.get("overrides") or {}):  # force rebuilds tiles, not materials
                    mats = cache
                else:
                    mats = kit_materials(nid)
                    if not mats.get("ok"):
                        return {"ok": False, "error": f"Kit materials: {mats.get('error')}"}
                    data.put(f"net.{nid}.mats", {"name": f"{name} / kit materials",
                                                 "overrides": mats.get("overrides") or {}})
            # the person's own layers: their meshes' materials need the instanced-mesh usage too (10 meshes per call)
            own = kit_bake.own_meshes(got.get("layers") or {}) if build else []
            own_report = {"fixed": [], "locked": [], "missing": []}
            for k in range(0, len(own), 10):
                r = listener("road_network_ism_ready", {"meshes": own[k:k + 10]}, timeout=300)
                for key in own_report:
                    own_report[key] += r.get(key) or []
            if own_report["missing"]:
                return {"ok": False, "error": f"These layer meshes aren't in the project: {own_report['missing'][:10]}. "
                                              f"Fix the layer's meshes (Content Browser path) and push again."}
            legacy_removed = legacy_pcg_cleanup(nid, root, name, tag) if not state["tiles"] else []
            # collision: every placed mesh blocks players except leaves and flat paint (kit_bake.NO_COLLISION).
            # A mesh with no collision shapes of its own gets complex-as-simple, or its instances block nothing.
            used = sorted({m for k in build for m in new[k]["meshes"]})
            solid = [m for m in used if kit_bake.blocks(m)]
            made_exact = []
            for k in range(0, len(solid), 20):
                for m in listener_long("road_network_collision_ready", {"meshes": solid[k:k + 20]}).get("need") or []:
                    epic("EditorToolset.StaticMeshToolset", "SetCollisionComplexity",
                         {"mesh": {"refPath": m}, "complexity": "CTF_UseComplexAsSimple"})
                    made_exact.append(m.split(".")[0])
            for k in range(0, len(made_exact), SAVE_BATCH):
                listener_long("road_network_save_assets", {"paths": made_exact[k:k + SAVE_BATCH]})
            collision = {"blocking_meshes": len(solid), "made_exact": [m.rsplit("/", 1)[-1] for m in made_exact],
                         "no_collision": [m.rsplit(".", 1)[-1] for m in used if not kit_bake.blocks(m)],
                         "note": "Every placed mesh blocks players except no_collision (leaves, flat paint). made_exact "
                                 "had no collision shapes, so they collide by their own triangles now. Playtest it."}

            # Deleting a graph asset costs UEFN ~6 s, and a rebuilt tile had to delete its old one first: that was
            # most of a build. A build that redoes every tile writes into a fresh folder instead and the old
            # folder goes in ONE delete at the end; only a partial build still frees names tile by tile.
            full = bool(build) and not same
            if full:
                state["gen"] = int(state.get("gen") or 0) + 1
                state["folder"] = f"CityPCG_G{state['gen']}"
            folder_name = state.get("folder") or "CityPCG"
            old_folders = {(t.get("graph_path") or "").rsplit("/", 1)[0] for t in state["tiles"].values()}
            old_folders |= {f"{root}/WorldGen/CityPCG"}

            def tile_job(key: str, tile: dict | None) -> dict:
                old = state["tiles"].get(key) or {}
                graph_path = f"{root}/WorldGen/{folder_name}/PCG_{tag}_T{key.replace('-', 'm')}"
                meshes = []
                for mesh, rows in sorted((tile or {}).get("meshes", {}).items()):
                    # PCG's spawner places instances with NO collision unless told: say it per mesh
                    # (both fields: the profile name alone left the instances at No Collision)
                    solid_ = kit_bake.blocks(mesh)
                    desc = {"bodyInstance": {"collisionProfileName": "BlockAll" if solid_ else "NoCollision",
                                             "collisionEnabled": "QueryAndPhysics" if solid_ else "NoCollision"}}
                    over = ((mats or {}).get("overrides") or {}).get(mesh)
                    if over and any(over):  # textured MIs as overrides: the kit mesh itself is never edited
                        desc["overrideMaterials"] = [m or None for m in over]
                    selector = {"meshes": [mesh], "templateDescriptor": {"descriptor": desc}}
                    meshes.append({"mesh": mesh, "title": mesh.rsplit(".", 1)[1], "selector": selector, "rows": rows})
                size = (tile or {}).get("size", kit_bake.TILE_CM)
                return {"key": key, "meshes": meshes, "graph_path": graph_path, "find": f"City Builder {name}",
                        "drop_graphs": [] if full else sorted({g for g in (old.get("graph_path"), graph_path) if g}),
                        "graph_name": graph_path.rsplit("/", 1)[1], "graph_folder": graph_path.rsplit("/", 1)[0],
                        "description": f"City Builder world {name}, tile {key}: the previewed lots in this "
                                       f"{round(size / 100)} m square on our own kit. Rebuilt by road_network_pcg_build.",
                        "label": f"City Builder {name} {key}", "centre": list((tile or {}).get("centre", (0, 0))),
                        "half": size / 2 + 5000.0}

            nodes, messages, done, gone = 0, [], [], []
            todo = [tile_job(k, None) for k in remove] + [tile_job(k, new[k]) for k in build]
            try:
                # one editor script per batch of tiles (pcg_batch): metered like any call, so the memory guard
                # refuses the next batch when one grew UEFN by GBs, and UEFN settles between batches
                for batch in pcg_batch.batches(todo):
                    if job["stop"]:
                        return {"ok": False, "error": f"Stopped after {len(done)} of {len(build)} tiles.", "built": done,
                                "note": "Build again: finished tiles are kept and skipped."}
                    base = uefn_memory.uefn_gb()
                    out = epic("editor_toolset.toolsets.programmatic.ProgrammaticToolset", "execute_tool_script",
                               {"script": {"language": "Python", "content": pcg_batch.script(batch)}}, timeout=900)
                    res = out.get("returnValue") if isinstance(out, dict) else out
                    res = json.loads(res) if isinstance(res, str) else (res or {})
                    failed = ""
                    for j in batch:
                        r = res.get(j["key"])
                        if r is None:
                            continue  # the script stopped before this tile: it stays as it was
                        if r.get("error"):
                            # its old volume may be gone already: never trust the stored one again
                            state["tiles"][j["key"]] = {"hash": "failed", "graph_path": j["graph_path"], "volume": ""}
                            failed = f"tile {j['key']}: {r['error']}"
                        elif r.get("removed"):
                            state["tiles"].pop(j["key"], None)
                            gone.append(j["key"])
                        else:
                            tile = new[j["key"]]
                            state["tiles"][j["key"]] = {"hash": tile["hash"], "graph_path": j["graph_path"],
                                                        "volume": r["volume"],
                                                        "pieces": sum(len(v) for v in tile["meshes"].values())}
                            messages += r.get("messages") or []
                            nodes += sum(1 + -(-len(m["rows"]) // pcg_batch.CHUNK) for m in j["meshes"])
                            done.append(j["key"])
                    data.put(f"net.{nid}.pcg", state)  # after every batch: a stop keeps what was built
                    job["done"] = len(done) + len(gone)
                    left = (time.time() - job["started"]) / max(1, job["done"]) * (job["total"] - job["done"])
                    job["eta_s"] = round(left)
                    push({"what": "progress", "id": nid, "title": "Pushing the city to UEFN", "step": job["done"],
                          "of": job["total"], "text": f"{job['done']} of {job['total']} tiles, about "
                                                      f"{max(1, round(left / 60))} min left"})
                    if failed:
                        raise ValueError(failed)
                    uefn_memory.settle(base, slack=2.0, timeout=60)
            except (ConnectionError, RuntimeError, TimeoutError, ValueError) as exc:
                return {"ok": False, "error": f"Stopped after {len(done)} of {len(build)} tiles: {exc}",
                        "built": done, "note": "Build again: finished tiles are kept and skipped.",
                        "memory": uefn_memory.report(5)}
            # graph folders no tile uses any more: one delete each (never anything outside WorldGen/CityPCG*)
            in_use = {(t.get("graph_path") or "").rsplit("/", 1)[0] for t in state["tiles"].values()}
            cleaned = []
            for f in sorted(old_folders - in_use):
                if f.startswith(f"{root}/WorldGen/CityPCG"):
                    try:
                        if epic("EditorToolset.AssetToolset", "Exists", {"path": f}).get("returnValue"):
                            epic("EditorToolset.AssetToolset", "Delete", {"path": f}, timeout=900)
                            cleaned.append(f.rsplit("/", 1)[1])
                    except (ConnectionError, RuntimeError, TimeoutError, ValueError):
                        pass  # left for the next build's clean-up: the city itself is built
            return {"ok": True, "id": nid, "built_tiles": done, "old_graph_folders_removed": cleaned, "removed_tiles": gone, "unchanged_tiles": len(same),
                    "legacy_removed": legacy_removed, "nodes": nodes, "meshes": len(groups), "built": baked,
                    "no_kit_piece": missing, "messages": messages[:20], "memory": uefn_memory.report(5)["worst"],
                    "collision": collision, "seconds": round(time.time() - job["started"]),
                    "own_meshes": {"count": len(own), "materials_fixed": own_report["fixed"],
                                   "materials_locked": own_report["locked"]},
                    "note": "One volume per tile (400 m city tiles, 1.6 km nature tiles), Generate On Demand (the "
                            "level never rebuilds them on load). Edit the plan and build again: only changed tiles "
                            "rebuild. Layers in no_kit_piece have no mesh in the kit yet."}

        def runner() -> None:
            try:
                job["result"] = work()
            except Exception as exc:  # noqa: BLE001 - a background job must always leave a result
                job["result"] = {"ok": False, "error": f"The build stopped: {exc}"[:400]}
            finally:
                job["running"] = False
                push({"what": "progress", "id": nid, "done": True})

        thread = threading.Thread(target=runner, name=f"city-builder-pcg-{nid}", daemon=True)
        thread.start()
        if wait:
            thread.join()
            return job["result"]
        return {"ok": True, "started": True, "id": nid, "tiles": job["total"],
                "note": "Building in the background, in batches. Call road_network_pcg_build(net_id) again for "
                        "progress; stop=true stops it after the batch in flight."}

    def pcg_place(net_id: str = "", confirm: bool = False) -> dict:
        """Hand the plan to an AI chat that builds it in UEFN (Epic PCGToolset); returns the chat id."""
        got = pcg_plan(net_id)
        if not got.get("ok"):
            return got
        nid = slug(net_id)
        name = (data.get(f"net.{nid}") or {}).get("name") or net_id
        source = {"own": "our own kit (the project's WorldGen/City and WorldGen/Kit meshes, BuildingKit storeys)",
                  "fortnite": "Fortnite's own props and buildings (search the Fortnite content)",
                  "mixed": "our own kit first, Fortnite pieces for anything missing"}[got["asset_set"]]
        if not confirm:
            return {"ok": False, "error": f"This starts an AI chat that builds {len(got['layers'])} city layers and "
                                          f"{len(got['foliage'])} foliage layers in the open UEFN level with {source}. "
                                          f"Call again with confirm=true."}
        try:
            chat = api.call_tool("ducky_create_chat", {"title": f"City Builder: {name}"})
            conv = (chat or {}).get("conv_id") or (chat or {}).get("id") or ((chat or {}).get("chat") or {}).get("id")
            if not conv:
                return {"ok": False, "error": "Couldn't start a chat for the build.", "plan": got}
            api.call_tool("ducky_send_chat_message", {"conv_id": conv, "wait_for_reply": False, "message": (
                f"Build the city layers of world '{name}' (City Builder) in the open UEFN level.\n"
                f"1. road_network_pcg_plan(net_id='{nid}') gives everything: the PCG graph (nodes in data-flow order, "
                f"wires, params, road tag, volume), pieces[] (each layer's role and where its pieces come from), "
                f"foliage[] and buildings_how.\n"
                f"2. Pieces: use {source}. For each layer pick real assets that fit its role and size (buildings: "
                f"lot_m and storeys), and save them with road_network_pcg_layer_save before building.\n"
                f"3. Read skill_read_subskill('pcg', 'graph_layers') and follow it (older UEFN plugin: "
                f"skill_read_subskill('leveldesign', 'procedural_generation', section='Route A: PCG graphs (UEFN 43.00+)')). "
                f"Epic PCGToolset; read argument names with unreal__describe_toolset, never guess.\n"
                f"4. foliage[] is NOT PCG: import those meshes as Foliage Types and paint them in Foliage mode by mouse. "
                f"No grass.\n"
                f"5. Execute once, report each layer's count and seed. Never touch the terrain.")})
        except (ValueError, RuntimeError, PermissionError) as exc:
            return {"ok": False, "error": f"Couldn't start the build chat: {exc}"}
        return {"ok": True, "chat": conv, "layers": got["layers"], "foliage": [f["layer"] for f in got["foliage"]],
                "nodes": len(got["nodes"]), "asset_set": got["asset_set"]}
        return {"ok": True, "chat": conv, "layers": got["layers"], "nodes": len(got["nodes"])}

    # ---------------------------------------------------------------- MCP tools
    INTENT = r"\b(road network|city roads?|road splines?|interchange|road pieces?)\b"

    @api.tool(intent=INTENT, listener=False)
    def road_network_projects() -> dict:
        """List the road network projects (each groups one city's networks)."""
        return safe(list_projects)()

    @api.tool(intent=INTENT, listener=False)
    def road_network_project_save(project_id: str = "", name: str = "", description: str = "") -> dict:
        """Create or rename a road network project."""
        return safe(save_project)(project_id, name, description)

    @api.tool(intent=INTENT, listener=False)
    def road_network_project_delete(project_id: str = "") -> dict:
        """Delete a road network project (its networks stay)."""
        return safe(delete_project)(project_id)

    @api.tool(intent=INTENT, listener=False)
    def road_network_list(project: str = "") -> dict:
        """List saved city road networks, optionally only one project's."""
        return safe(list_networks)(project)

    @api.tool(intent=INTENT, listener=False)
    def road_network_get(net_id: str = "") -> dict:
        """One road network: its settings and every road actor's piece counts."""
        return safe(get_network)(net_id)

    @api.tool(intent=INTENT, listener=False)
    def road_network_save(net_id: str = "", name: str = "", project: str = "", terrain_mode: str = "") -> dict:
        """Create or update a road network (name, project, terrain_mode 'live' or 'layer')."""
        return safe(save_network)(net_id, name, project, terrain_mode)

    @api.tool(intent=INTENT, listener=False)
    def road_network_delete(net_id: str = "") -> dict:
        """Delete a saved road network and all its stored roads (UEFN is not touched)."""
        return safe(delete_network)(net_id)

    @api.tool(intent=INTENT, listener=True)
    def road_network_pull(net_id: str = "", name: str = "", project: str = "", actors: str = "") -> dict:
        """Read the city's road actors from the open UEFN level into a road network (all, or a comma list of labels)."""
        return safe(pull)(net_id, name, project, actors)

    @api.tool(intent=INTENT, listener=False)
    def road_network_import_text(net_id: str = "", label: str = "", text: str = "") -> dict:
        """Add one road actor from spline clipboard text (BEGIN_SPLINES, copied in the Splines tool)."""
        return safe(import_text)(net_id, label, text)

    @api.tool(intent=INTENT, listener=False)
    def road_network_stats(net_id: str = "") -> dict:
        """Which pieces a road network uses: road meshes, junction pieces, kit pieces, length."""
        return safe(stats)(net_id)

    @api.tool(intent=INTENT, listener=False)
    def road_network_memory(last: int = 60, reset_guard: bool = False) -> dict:
        """UEFN's memory now and per editor call City Builder made (before / after / grew GB, seconds), the
        10 calls that grew it most, and whether the guard has stopped work (a call grew UEFN 6+ GB, or UEFN is
        above 80 % of RAM). reset_guard=true clears a trip once UEFN has been saved / restarted."""
        if reset_guard:
            uefn_memory._state["trip"] = None
        return {"ok": True, **uefn_memory.report(int(last))}

    @api.tool(intent=INTENT, listener=False)
    def road_network_move_point(net_id: str = "", actor: str = "", index: int = -1, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> dict:
        """Move one spline control point of a road actor to x, y, z (cm); attached tangents follow."""
        return safe(move_point)(net_id, actor, index, x, y, z)

    @api.tool(intent=INTENT, listener=False)
    def road_network_check(net_id: str = "", actor: str = "") -> dict:
        """Find broken road data before it reaches UEFN: giant junction nodes (Width over 80 m),
        join links over 80 m and runaway tangents."""
        return safe(check)(net_id, actor)

    @api.tool(intent=INTENT, listener=False)
    def road_network_repair(net_id: str = "", actor: str = "") -> dict:
        """Fix what road_network_check finds: drop the over-long join links and strip the giant junction
        piece off those nodes (they become plain shared points with a road width). Offline."""
        return safe(repair_actor)(net_id, actor)

    @api.tool(intent=INTENT, listener=False)
    def road_network_tune(net_id: str = "", actor: str = "", thin_tolerance_cm: float = 50.0,
                          side_falloff: float = 800.0, end_falloff: float = 1000.0, width: float = 0.0,
                          paint_layer: str = "", mesh_offset_cm: float = -1.0,
                          height_tolerance_cm: float = 150.0) -> dict:
        """Fewer points and sane terrain settings: drop every point the spline doesn't need (a straight road
        between two junctions becomes one segment; its end tangents enter the junctions square), set
        SideFalloff / EndFalloff (cm) on every live point, optional road half Width (0 keeps each road's),
        optional landscape paint layer, optional Mesh Vertical Offset in cm (-1 keeps each point's; our road
        set uses 50). Offline; reimport to apply."""
        return safe(tune_actor)(net_id, actor, thin_tolerance_cm, side_falloff, end_falloff, width, paint_layer,
                                mesh_offset_cm, height_tolerance_cm)

    @api.tool(intent=INTENT, listener=False)
    def road_network_kit_convert(net_id: str = "", actor: str = "") -> dict:
        """Move a road actor onto the Fortnite Road1 kit (/Game/Creative/Environments/Meshes/Roads): tiles
        become Road1_Straight, fitting 3-way junctions become T_Section / Y_Split / Split with their mouths on
        the kit ends. Lists the 4+ way and unfit nodes that still need generated pieces. Offline."""
        return safe(kit_convert)(net_id, actor)

    @api.tool(intent=INTENT, listener=True)
    def road_network_build_pieces(net_id: str = "", actor: str = "", rebuild: bool = False) -> dict:
        """Build plain-asphalt junction meshes (GeometryScript, in the project's WorldGen/Roads/Junctions/Kit
        folder) for every junction that has no piece, and put them on the nodes. With our own road kit they
        are flat M_Asphalt at deck height; with the Fortnite kit, Road1 crowned. rebuild=true regenerates the
        existing pieces in place (after a kit or material change). Mesh assets only; never the landscape."""
        return safe(build_pieces)(net_id, actor, rebuild)

    @api.tool(intent=INTENT, listener=True)
    def road_network_kit_materials(net_id: str = "", street_kit: bool = True) -> dict:
        """Give the own kit (WorldGen/City + WorldGen/Kit meshes) textured materials that work on PCG
        instances: one MI of M_City_WorldPBR (world-aligned PBR, ISM + Nanite usage on) per old flat-colour
        material, its colour kept as Tint. pcg_build runs it first; run it alone after importing new kit meshes.
        street_kit=false skips (re)building the street kit and only fixes the kit meshes' materials."""
        return safe(kit_materials)(net_id, street_kit)

    @api.tool(intent=INTENT, listener=False)
    def road_network_restore(net_id: str = "", actor: str = "", from_actor: str = "") -> dict:
        """Put a saved version of a road actor back (for example from_actor='City Roads before kit'). Offline;
        reimport to apply."""
        return safe(restore_actor)(net_id, actor, from_actor)

    @api.tool(intent=INTENT, listener=False)
    def road_network_circle(net_id: str = "", actor: str = "", nodes: str = "", cx: float = 0.0, cy: float = 0.0,
                            fit_center: bool = True, radius: float = 0.0) -> dict:
        """Make a ring road a true circle. nodes: the ring's junction point ids (comma list). The centre is
        fitted through them (fit_center) or given as cx, cy; radius 0 = their mean distance. Each stretch
        between two ring junctions becomes one arc segment. Offline; reimport to apply."""
        return safe(circle)(net_id, actor, nodes, cx, cy, fit_center, radius)

    @api.tool(intent=INTENT, listener=True)
    def road_network_scan_ground(net_id: str = "", grid: int = 200) -> dict:
        """Re-read the terrain heights (read-only) for a network's 3D view and for drape. Run it while
        only the terrain is in the level."""
        return safe(scan_ground)(net_id, grid)

    @api.tool(intent=INTENT, listener=False)
    def road_network_drape(net_id: str = "", actor: str = "", smooth: int = 4, all_points: bool = False) -> dict:
        """Put a road actor's live points onto the base ground (smoothed along the roads, junctions kept flat).
        all_points=true also drapes ground-level kit pieces and bridge ends (never use it on elevated decks).
        Works offline on the stored terrain scan: never touches UEFN or the landscape."""
        return safe(drape)(net_id, actor, smooth, all_points)

    @api.tool(intent=INTENT, listener=False)
    def road_network_copy_text(net_id: str = "", actor: str = "") -> dict:
        """Put one road actor's spline text on the clipboard for a manual paste in the Splines tool."""
        return safe(copy_text)(net_id, actor)

    @api.tool(intent=INTENT, listener=True)
    def road_network_reimport(net_id: str = "", actor: str = "", confirm: bool = False) -> dict:
        """Replace a road actor in the open UEFN level with the network's version: the old actor is
        removed and the new one pasted as live splines (the terrain itself is never written)."""
        return safe(reimport)(net_id, actor, confirm)

    GEN_INTENT = r"\b(generate|auto.?generat\w*|procedural)\b.*\b(city|cities|roads?|terrain|heightmap|world)\b"

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_generate(name: str = "", seed: int = 7, cities: str = "", size_km: float = 6.0,
                              relief_m: float = 60.0, ground_from: str = "", folder: str = "",
                              center_x: float = 0.0, center_y: float = 0.0, options: str = "") -> dict:
        """Generate a world as a road network you can preview, then import: a hilly terrain (16-bit PNG
        heightmap; natural ground, or option city_ground 'flatten' for a plateau under each city), several cities each with its own roads actor, districts
        (downtown / normal / housing rings) and style ('mixed' grid downtown with wobbly suburbs, 'grid',
        'organic'), and highways joining them. cities: a count ('3'), a JSON list
        [{name, x, y, radius_m, style}] in cm, or empty to rebuild the cities already in this world.
        ground_from: an existing network whose ground scan the roads follow instead (no heightmap).
        options: JSON, any of: terrain hill_km, roughness (0.2-0.8), detail (octaves 1-8), ridges (0-1),
        flatten_m, sea_m (lake level, -1000 = off), terrace_m; roads block_m (a block's short side), block_ratio
        (1 = square blocks, up to 3 = three times as long as wide), wobble (0-0.45: 0 = dead straight streets,
        higher = the streets between main roads bow in smooth curves), green_lots (0-0.6: share of every block's
        lots planted with trees instead of built on), prune (0-0.5), downtown / normal (ring shares), highway
        ('4L' or '2L'), highway_curve. Left-out
        options keep the world's last values. Offline: never touches UEFN or any terrain."""
        return safe(generate)(name, seed, cities, size_km, relief_m, ground_from, folder, center_x, center_y, options)

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_generate_options() -> dict:
        """Every generate option with its default and range (what the Terrain and Roads tabs show), and the
        design `rules` City Builder follows (where interchanges go, bridge heights, keep zones, lots, forests):
        an AI building a world with this plugin follows these rules, never its own."""
        return {"ok": True, "options": {k: {"default": d, "min": lo, "max": hi} for k, (d, lo, hi) in cg.OPTIONS.items()},
                "highway": list(cg.HIGHWAYS), "styles": list(cg.STYLES), "rules": cg.RULES}

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_pipeline(net_id: str = "", run_from: str = "", confirm: bool = False) -> dict:
        """The city as separate systems, each consuming the one before (backend/pipeline.py): TerrainAnalyzer
        (heightmap, slope map, obstacles) -> UrbanPlanner (districts, orientation, density) -> RoadGenerator
        (graph, intersections, graded profiles) -> BlockGenerator (lots) -> FoundationGenerator (pads, cut/fill,
        retaining walls) -> BuildingGenerator (footprints, buildings, entrances) -> SidewalkGenerator (sidewalks,
        walkways, driveways as splines) -> RoadMeshGenerator (junction meshes + paste, in UEFN: Push roads).
        Without run_from: each stage's status. run_from=<stage id> (+confirm) re-runs it and everything
        downstream, never what came before (e.g. 'buildings' keeps the roads, 'sidewalks' only the paths)."""
        return safe(pipeline_run)(net_id, run_from, confirm)

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_piece_manifest(check: bool = False) -> dict:
        """Every mesh a City Builder city is made of, with the spec to model it in Blender: name, folder, size
        in cm, pivot / facing / material-slot conventions, what places it (building role, layer, interior,
        grading) and the pieces still WANTED (with the lot size they must fit). An AI builds the missing ones
        in Blender from this, imports them under the listed folder, and adds new buildings to a layer's meshes
        with `size_cm` - no plugin code. check=true also asks UEFN which pieces the project already has."""
        root = kit_root() or "/<project>"
        out = pieces.manifest(root)
        if check and kit_root():
            paths = [p["path"] for p in out["pieces"] if p.get("path")]
            have = {}
            for k in range(0, len(paths), 50):
                have.update(listener("road_network_assets_exist", {"paths": paths[k:k + 50]}).get("exist") or {})
            for p in out["pieces"]:
                if p.get("path"):
                    p["in_project"] = have.get(p["path"])
        return {"ok": True, **out}

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_describe(net_id: str = "") -> dict:
        """Start here: everything City Builder accepts, read from the code so it is never out of date. Layer
        kinds, where-zones, roles, building forms, build modes ('mesh' stretches one kit building to a lot,
        'modular' stacks BuildingKit storeys and wall modules by grammar), which roles have a modular style and
        the grammar each storey uses, the pipeline stages in run order, and the kit folders. With net_id: that
        world's cities, its layers (name, kind, role, build, order) and its stage states."""
        from . import grammar
        out = {"ok": True,
               "stages": pipeline.RUN_ORDER,
               "layer": {"kinds": list(pl.KINDS), "where": list(pl.WHERE), "roles": {r: v[0] for r, v in pl.ROLES.items()},
                         "forms": pl.FORMS, "build": list(pl.BUILD_MODES),
                         "numbers": {k: {"default": d, "min": lo, "max": hi} for k, (d, lo, hi) in pl.NUMBERS.items()}},
               "modular": {"styles": grammar.STYLE_BY_ROLE, "storey_cm": grammar.STOREY_CM, "module_cm": grammar.MODULE_CM,
                           "grammars": {s: {k: v for k, v in st_.items() if k in ("front_ground", "ground", "upper")}
                                        for s, st_ in grammar.STYLES.items()},
                           "folder": f"WorldGen/{grammar.FOLDER}"},
               "kit": {"pieces": len(kit_bake.KIT), "folders": sorted({f"WorldGen/{v[0]}" for v in kit_bake.KIT.values()})},
               "next": "road_network_pcg_layers -> road_network_pcg_layer_save -> road_network_pcg_preview -> "
                       "road_network_pcg_build; road_network_piece_manifest for meshes to model"}
        nid = slug(net_id)
        if nid:
            if not data.get(f"net.{nid}"):
                return {"ok": False, "error": f"no world '{net_id}'",
                        "fix": "road_network_list() lists the worlds; pass its id."}
            out["cities"] = sorted(v.get("name") or k for k, v in city_docs(nid).items())
            out["layers"] = sorted(({"id": k, "name": v.get("name"), "kind": v.get("kind"), "role": v.get("role"),
                                     "build": v.get("build", "mesh"), "order": v.get("order")}
                                    for k, v in layer_docs(nid).items()), key=lambda l: (l["order"] or 0, l["name"] or ""))
        return out

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_holds(net_id: str = "") -> dict:
        """Who is working on which city of a world right now: [{city, who, kind (person / ducky / teammate), mine,
        task, left_s}]. A city someone else holds can't be changed: road_network_city_save, city_delete and a
        generate that changes the world's settings are refused and say whose it is. A person holds the city they
        have picked in the panel; a ducky holds a city for two minutes from each change it makes to it; a person's
        own ducky may change the city that person has open. Look here before planning changes to several cities."""
        return safe(holds)(net_id)

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_hold(net_id: str = "", city: str = "", task: str = "", minutes: float = 0.0) -> dict:
        """Take a city before a longer job on it, so no one else changes it meanwhile: task says what you are doing
        in a few words (everyone sees it: "Builder (Ilya's ducky) · re-planning the harbour"), minutes how long
        (default 2; every change you make to the city starts the two minutes again, so plain edits need no call).
        Refused, with the holder's name, while someone else has the city. Let go with road_network_release."""
        return safe(hold)(net_id, city, task, minutes)

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_release(net_id: str = "", city: str = "") -> dict:
        """Let go of a city you hold (road_network_hold, or the hold your last change to it took), so the next
        person or ducky can change it at once instead of after two minutes."""
        return safe(release)(net_id, city)

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_cities(net_id: str = "") -> dict:
        """The cities of a generated world: centre, radius, road style, district rings and building sets."""
        return safe(list_cities)(net_id)

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_city_plan(net_id: str = "", city: str = "", facilities: bool = False) -> dict:
        """The urban planner's plan for one city (urban.py): population, households, jobs and age groups; the
        demand (retail, school seats); every facility the city got by category (commercial, education,
        government and emergency, industrial, recreation, utilities, transport) with why it is there;
        service coverage measured along the roads; school enrolment per school; and what could not be met.
        facilities=true lists each facility with its site (x, y, yaw, size, lots claimed) and reason.
        Facilities are placed by the plan, never by hand: change population (city size, district rings) or
        the catalogue (urban.CATALOG) and generate again; existing facilities stay where they are."""
        def run():
            nid = slug(net_id)
            docs = city_docs(nid)
            if not docs:
                return {"ok": False, "error": f"No generated cities in {net_id!r}."}
            cslug = slug(city) if city else sorted(docs)[0]
            doc = docs.get(cslug)
            if not doc:
                return {"ok": False, "error": f"No city {city!r}. Cities: {', '.join(sorted(docs))}."}
            p = dict(doc.get("plan") or {})
            if not p:
                return {"ok": False, "error": "This city has no plan yet: generate the world again."}
            if not facilities:
                p.pop("facilities", None)
            return {"ok": True, "city": doc["name"], **p}
        return safe(run)()

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_city_add(net_id: str = "", name: str = "", x: float | None = None, y: float | None = None,
                              radius_m: float = 0.0, style: str = "") -> dict:
        """Add a city to a generated world: at x, y (cm, UE world) or, without them, on a free spot of the map;
        radius_m (default: the world's city size); style mixed / grid / organic. Then generate again (cities
        empty): it gets its own roads, services and waterfront, and highways join it to the other cities."""
        return safe(add_city)(net_id, name, x, y, radius_m, style)

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_city_delete(net_id: str = "", city: str = "", confirm: bool = False) -> dict:
        """Remove a city (and everything on it) from a generated world; confirm=true. Then generate again: the
        other cities' highways re-join without it. A world keeps at least one city."""
        return safe(delete_city)(net_id, city, confirm)

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_airports(net_id: str = "") -> dict:
        """The airports of a generated world (airport.py): per airport its type (general_aviation / regional /
        international / military), city, runway heading (into the wind_deg option), grading, every facility it
        got (terminal, piers, cargo, hangars, fire station, tower, fuel farm, parking, hotel, ...), its access and
        cargo roads to the city, its noise zone, and the validator (connected taxiways, reachable stands, clear
        runways and approaches, separate cargo trucks, no water under a runway). Change airports by option
        (airports, military_base, wind_deg) and generate again; never by hand."""
        def run():
            nid = slug(net_id)
            if not nid or not data.get(f"net.{nid}"):
                return {"ok": False, "error": f"No road network {net_id!r}."}
            doc = data.get(f"net.{nid}.airports") or {}
            return {"ok": True, "id": nid, "airports": doc.get("items") or [],
                    "note": "" if doc.get("items") else "No airport: the cities are too small, the land around them "
                                                          "too steep or wet, or airports is set to none."}
        return safe(run)()

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_waterfront(net_id: str = "", city: str = "") -> dict:
        """The water of a generated world (waterfront.py): waterbodies (ocean / lake / river, area, width),
        every bridge the roads needed (type by span: canal arch, beam, navigable girder, cable-stayed,
        suspension; clearance; pylons), and per city its shoreline by development type (natural, wetland,
        beach, residential, commercial, marina, industrial, public), the developments built on it (quays and
        promenades, marinas with slips, beach resorts, container ports, stilt and floating homes, ferry
        terminals, waterfront parks) and the connectivity validator (navigation, access, foundations).
        Change it by option (waterfront, canals, sea_m) and generate again; never by placing docks by hand."""
        def run():
            nid = slug(net_id)
            if not nid or not data.get(f"net.{nid}"):
                return {"ok": False, "error": f"No road network {net_id!r}."}
            w = data.get(f"net.{nid}.water")
            if not w:
                return {"ok": False, "error": f"{net_id!r} has no water data yet: generate it again."}
            docs = city_docs(nid)
            cities = {d["name"]: d.get("waterfront") for k, d in docs.items() if not city or k == slug(city)}
            return {"ok": True, "id": nid, "water_level_cm": w.get("sea"), "bodies": w.get("bodies"),
                    "bridges": w.get("bridges"), "cities": cities}
        return safe(run)()

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_city_save(net_id: str = "", city: str = "", name: str = "", style: str = "",
                               radius_m: float = 0.0, buildings: str = "", x: float | None = None,
                               y: float | None = None, features: str = "", turn_deg: float | None = None,
                               shape: str = "", zones: str = "") -> dict:
        """Change one city of a generated world: rename it, its road style (mixed / grid / organic), its
        radius, move it (x, y: its new centre in cm, UE world), its own building sets (JSON {"downtown":
        [mesh paths], "normal": [...], "housing": [...]}), or what it has: features JSON, e.g.
        {"airports": "none"} takes its airport off, {"shipyards": "auto"} adds shipyards, null returns a feature
        to the world's choice (keys: airports, shipyards, freeways, waterfront, canals, planner).
        turn_deg turns the whole city (street grid, outline, industrial side). shape draws its outline by hand: a
        JSON list of 16 numbers, how far it reaches in each of 16 directions round it as a share of its radius
        (0.74 to 1.26; 'reset' = the outline its seed gives it). zones gives one block a type: JSON {"x", "y" (a
        spot inside the block, cm), "kind" (how it is laid out), "zone" (downtown / normal / housing /
        industrial), "green" (0 to 1: the share of its lots planted with trees instead of built on)}, or a list of
        them; all empty returns the block to the city's own plan. road_network_cities lists every block ([x, y,
        kind, zone, polygon, green share]) and the kinds and zones a block can take. Generate again
        with cities empty to rebuild its roads, freeways, airport, waterfront and everything on it."""
        return safe(save_city)(net_id, city, name, style, radius_m, buildings, x, y, features, turn_deg, shape, zones)

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_heightmap(net_id: str = "") -> dict:
        """Where a generated world's heightmap PNG is and the Landscape import settings (resolution,
        location, scale) to bring it in as a new landscape."""
        return safe(heightmap)(net_id)

    @api.tool(intent=GEN_INTENT, listener=True)
    def road_network_import_all(net_id: str = "", confirm: bool = False, force: bool = False) -> dict:
        """Push roads into UEFN: build junction pieces, then paste every road actor that CHANGED since its last
        push (or is missing from the level) as live splines; unchanged actors are left alone. force=true pushes
        all. Ask the person first and keep their hands off UEFN while it runs."""
        return safe(import_all)(net_id, confirm, force)

    api.register_panel_rpc("generate", lambda name="", seed=7, cities="", size_km=6.0, relief_m=60.0, ground_from="",
                           folder="", center_x=0.0, center_y=0.0, options="": safe(generate)(
                               name, seed, cities, size_km, relief_m, ground_from, folder, center_x, center_y, options))
    api.register_panel_rpc("generate_options", lambda: road_network_generate_options())
    api.register_panel_rpc("cities", lambda net_id="": safe(list_cities)(net_id))
    api.register_panel_rpc("hold", lambda net_id="", city="", task="", minutes=0.0: safe(hold)(net_id, city, task, minutes))
    api.register_panel_rpc("release", lambda net_id="", city="": safe(release)(net_id, city))
    api.register_panel_rpc("holds", lambda net_id="", teammates=None: safe(holds)(net_id, teammates))
    api.register_panel_rpc("city_save", lambda net_id="", city="", name="", style="", radius_m=0.0, buildings="",
                           x=None, y=None, features="", turn_deg=None, shape="", zones="":
                           safe(save_city)(net_id, city, name, style, radius_m, buildings, x, y, features,
                                           turn_deg, shape, zones))
    api.register_panel_rpc("heightmap", lambda net_id="": safe(heightmap)(net_id))
    api.register_panel_rpc("city_add", lambda net_id="", name="", x=None, y=None, radius_m=0.0, style="":
                           safe(add_city)(net_id, name, x, y, radius_m, style))
    api.register_panel_rpc("city_delete", lambda net_id="", city="", confirm=False: safe(delete_city)(net_id, city, confirm))
    api.register_panel_rpc("pipeline", lambda net_id="", run_from="", confirm=False: safe(pipeline_run)(net_id, run_from, confirm))

    # ---------------------------------------------------------------- panel: version, AI notes on the map
    def plugin_version() -> dict:
        try:
            with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin.json"), encoding="utf-8") as fh:
                return {"ok": True, "version": json.load(fh).get("version", "")}
        except (OSError, ValueError):
            return {"ok": True, "version": ""}

    def list_chats() -> dict:
        """Recent Ducky chats a note on the map can go to (plus 'new chat' in the panel)."""
        got = api.call_tool("ducky_list_chats", {"limit": 30}) or {}
        if not isinstance(got, (dict, list)):  # a tool reply can arrive as JSON text
            got = getattr(got, "text", None) or str(got)
            try:
                got = json.loads(got)
            except ValueError:
                got = {}
        if isinstance(got, dict) and got.get("truncated") and got.get("path"):  # a big list is spilled to a file
            try:
                with open(got["path"], encoding="utf-8") as fh:
                    got = json.load(fh)
            except (OSError, ValueError):
                got = {}
        rows = (got.get("conversations") or got.get("chats") or got.get("items") or []) if isinstance(got, dict) else got
        rows = sorted((c for c in rows or [] if isinstance(c, dict) and (c.get("id") or c.get("conv_id"))
                       and c.get("folder_id") != "archive"), key=lambda c: -float(c.get("updated") or 0))
        return {"ok": True, "chats": [{"id": c.get("id") or c.get("conv_id"),
                                       "title": (c.get("title") or c.get("name") or "Chat")
                                       + (f" ({c['ducky']})" if c.get("ducky") and c["ducky"] != c.get("title") else "")}
                                      for c in rows][:30]}

    def note_text(nid: str, text: str, kind: str, points: list) -> str:
        """The message an AI gets for a pin or a drawn area: where it is, what is there, how to act on it."""
        doc = data.get(f"net.{nid}") or {}
        towns = list(city_docs(nid).values())
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        near = min(towns, key=lambda c: (c["x"] - cx) ** 2 + (c["y"] - cy) ** 2) if towns else None
        lines = [f"City Builder note on world '{doc.get('name') or nid}' (net_id '{nid}'): {text.strip()}"]
        if near:
            share = cg.zone_share(near, cx, cy)
            ring = "downtown" if share <= 0.4 else "normal city" if share <= 0.75 else "housing" if share <= 1 else "outside the city"
            lines.append(f"Where: {'an area' if kind == 'area' else 'a spot'} in {near['name']} ({ring}), centre x {round(cx)} "
                         f"y {round(cy)} cm.")
        if kind == "area":
            lines.append(f"Area outline (world cm, {len(points)} points): {json.dumps([[round(p[0]), round(p[1])] for p in points])}")
            got = pcg_preview(nid)
            have = {}
            for name, layer in (got.get("layers") or {}).items():
                n = sum(1 for p in layer.get("points") or [] if pl.inside(points, p[0], p[1]))
                if n:
                    have[name] = n
            lines.append("Already inside: " + (", ".join(f"{k} {v}" for k, v in sorted(have.items(), key=lambda kv: -kv[1])[:12])
                                               or "nothing yet"))
        lines.append("How: City Builder tools only (skill road-network). To add things in this area, save a PCG layer "
                     "with road_network_pcg_layer_save and set its \"area\" to the outline above (the layer then places "
                     "only inside it); road_network_pcg_layers lists fields and an example; check with "
                     "road_network_pcg_preview, then Push city (road_network_pcg_build, confirm=true) rebuilds only the "
                     "tiles that changed. Follow road_network_generate_options().rules.")
        return "\n".join(lines)

    STREET_KIT_PREFIXES = ("SM_Path_", "SM_Water_", "SM_Air_")  # road pieces City Builder makes itself

    def build_brief(net_id: str = "", check: bool = False) -> dict:
        """Everything a build of this world in UEFN needs, what is missing, and what to ask the person first."""
        nid = slug(net_id)
        doc = data.get(f"net.{nid}") if nid else None
        if not doc:
            return {"ok": False, "error": f"No road network {net_id!r}."}
        root = kit_root() or "/<project>"
        folder = road_folder() or f"{root}/WorldGen/Roads"
        cities = list(city_docs(nid).values())
        # 1. the pieces: every road mesh the splines use, every kit mesh the PCG layers and sites place
        by_name = {v.rsplit("/", 1)[-1]: v for v in cg.ROAD_MESH.values()}
        road_meshes: dict = {}
        for aslug in actor_docs(nid):
            _, _, segs = load_actor(nid, aslug)
            for s in segs:
                if s.get("mesh"):
                    road_meshes[s["mesh"]] = road_meshes.get(s["mesh"], 0) + 1
        needs = []
        for name, n in sorted(road_meshes.items()):
            sub = by_name.get(name, name)
            needs.append({"mesh": name, "path": f"{folder}/{sub}.{name}", "uses": n, "for": "road splines",
                          "source": "street_kit" if name.startswith(STREET_KIT_PREFIXES) else "road_kit"})
        got = pcg_preview(nid)
        layers = got.get("layers") or {} if got.get("ok") else {}
        groups = kit_bake.bake(layers, root, interiors_of(nid))[0] if layers else {}
        for path, rows in sorted(groups.items()):
            name = path.rsplit(".", 1)[-1]
            src = "street_kit" if "/Kit/Street/" in path else "building_kit" if "/BuildingKit/" in path else \
                "fortnite" if path.startswith("/Game/Athena") or path.startswith("/Fortnite") else "world_kit"
            needs.append({"mesh": name, "path": path, "uses": len(rows), "for": "PCG city", "source": src})
        # doors: every door module the building grammar placed is a door spot; each becomes an openable Scene Graph
        # entity placed by PCG (cg.RULES), so the brief names them and how many
        # the exact piece to build for every mesh named above: size, bounds, sidewalk, slots (the piece manifest)
        spec_of = {p["name"]: {k: p[k] for k in ("size_cm", "bounds_cm", "sidewalk_cm", "carriageway_cm", "where", "spec",
                                                 "made_by", "folder") if p.get(k) is not None}
                   for p in pieces.manifest(root)["pieces"]}
        # every junction of the world by its number of arms: City Builder builds each as ONE mesh (never Blender)
        junctions: dict = {}
        for aslug in actor_docs(nid):
            _, pts, segs = load_actor(nid, aslug)
            for _node, arms, _angles in st.pending_nodes(pts, segs, True):
                kind = {2: "elbow (2 arms)", 3: "T (3 arms)", 4: "cross (4 arms)"}.get(len(arms), f"{len(arms)} arms")
                junctions[kind] = junctions.get(kind, 0) + 1
        doors = {path.rsplit(".", 1)[-1]: len(rows) for path, rows in groups.items()
                 if re.match(r"SM_BK_.*(Door|Entrance|Garage)", path.rsplit(".", 1)[-1])}
        # collision: what each PCG layer is set to (cg.RULES: everything solid collides, simple by default)
        set_layers = [pl.clean(l) for l in layer_docs(nid).values() if l.get("enabled", True)]
        no_collision = sorted(l["name"] for l in set_layers if not l["collision"])
        # 2. what the project already has (only with UEFN open)
        uefn = {"checked": False}
        if check:
            try:
                have = {}
                paths = [n["path"] for n in needs]
                for k in range(0, len(paths), 50):
                    have.update(listener("road_network_assets_exist", {"paths": paths[k:k + 50]}, timeout=120).get("exist") or {})
                for n in needs:
                    n["in_project"] = bool(have.get(n["path"]))
                uefn = {"checked": True, "online": True}
            except (ConnectionError, RuntimeError, TimeoutError, ValueError) as exc:
                uefn = {"checked": True, "online": False, "why": str(exc)[:200]}
        missing = [n for n in needs if n.get("in_project") is False]
        # 3. the tools around: UEFN, Epic's MCP, Blender, AI 3D models
        env = {}
        try:
            stt = api.call_tool("ducky_get_status", {}) or {}
            env.update(uefn_online=bool(stt.get("online")), epic_mcp=bool(stt.get("epic_mcp_online")),
                       island=stt.get("uefn_project_name") or "")
        except Exception:
            env.update(uefn_online=None)
        for key, pat in (("blender", "blender_"), ("ai_3d", "meshy"), ("ai_3d_tripo", "tripo")):
            try:
                found = api.call_tool("ducky_get_tools", {"pattern": pat, "limit": 1}) or {}
                env[key] = bool(found.get("count") or found.get("matches"))
            except Exception:
                env[key] = None
        sync = sync_status(nid) if not check or uefn.get("online") is not False else {}
        asset_set = asset_set_of(nid)
        counts = {name: l.get("count", 0) for name, l in layers.items()}
        total = sum(counts.values())
        street_missing = [n["mesh"] for n in missing if n["source"] == "street_kit"]
        art_missing = [n["mesh"] for n in missing if n["source"] in ("road_kit", "world_kit", "building_kit")]
        gen = doc.get("generated") or {}
        # 4. the decisions only the person can make, ready for one ducky_ask_user
        q = [{"id": "uefn", "prompt": f"Build '{doc.get('name')}' in the open UEFN island now?" if env.get("uefn_online")
              else f"UEFN is closed. Build '{doc.get('name')}' into which island?",
              "options": ([{"id": "now", "label": "Yes, build in the open island", "description": env.get("island") or ""}]
                          if env.get("uefn_online") else
                          [{"id": "open", "label": "Open my island and build", "description": "Ducky asks before launching UEFN"}]) +
              [{"id": "later", "label": "Not now", "description": "Only check what is missing"}]},
             {"id": "pieces", "prompt": "Which pieces should the city be built from?",
              "options": [{"id": "own", "label": "City Builder's own kit", "description": "WorldGen kit, BuildingKit storeys, street kit"},
                          {"id": "fortnite", "label": "Fortnite's own props and buildings", "description": "No modelling needed"},
                          {"id": "mixed", "label": "Mixed", "description": "Own kit first, Fortnite for anything missing"}]},
             {"id": "scope", "prompt": "What should be built?",
              "options": [{"id": "changed", "label": "Only what changed", "description": "Roads and city tiles that differ from the last build"},
                          {"id": "all", "label": "Everything again", "description": "Every road actor and every city tile"},
                          {"id": "roads", "label": "Roads only", "description": "Splines, junctions, freeways, runways"},
                          {"id": "city", "label": "City only", "description": "PCG buildings, props and trees"}]},
             {"id": "detail", "prompt": f"The city places {total:,} pieces. How dense?",
              "options": [{"id": "full", "label": "Full detail", "description": "Every tree, prop and building"},
                          {"id": "lighter", "label": "Lighter", "description": "Half the trees, bushes and rocks: faster, less memory"},
                          {"id": "buildings", "label": "Buildings and roads only", "description": "No foliage or street props"}]},
             {"id": "interiors", "prompt": "Furnish building interiors (floors, stairs, furniture seen through windows)?",
              "options": [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No, shells only"}]}]
        if art_missing or street_missing:
            opts = []
            if street_missing:
                opts.append({"id": "street_kit", "label": f"Make the {len(street_missing)} street pieces automatically",
                             "description": "City Builder builds them in UEFN itself"})
            if art_missing and env.get("blender"):
                opts.append({"id": "blender", "label": f"Model the {len(art_missing)} missing kit pieces in Blender",
                             "description": "From road_network_piece_manifest specs"})
            if art_missing and (env.get("ai_3d") or env.get("ai_3d_tripo")):
                opts.append({"id": "ai_3d", "label": "Generate the missing pieces with AI 3D", "description": "Billed per model"})
            opts += [{"id": "fortnite", "label": "Use Fortnite pieces for what is missing"},
                     {"id": "skip", "label": "Skip what is missing", "description": "Those layers stay empty"}]
            q.insert(2, {"id": "missing", "prompt": f"{len(missing)} pieces are not in the project yet. How should they be made?",
                         "options": opts, "allow_multiple": True})
        if gen and not gen.get("ground_from"):
            q.append({"id": "terrain", "prompt": "This world has its own generated terrain. Import it?",
                      "options": [{"id": "new_landscape", "label": "Import the heightmap as a new landscape",
                                   "description": "Your current landscape is never changed"},
                                  {"id": "keep", "label": "Keep my current landscape", "description": "Roads still grade it"}]})
        q.append({"id": "collision", "prompt": "How exact should collision be? Every building, wall, floor, stair, road "
                                               "and prop gets collision either way; only grass, leaves and painted lines "
                                               "have none.",
                  "options": [{"id": "simple", "label": "Simple shapes",
                               "description": "Boxes and convex hulls; stairs, ramps, doorways and curved decks still "
                                              "get exact collision"},
                              {"id": "exact", "label": "Exact everywhere",
                               "description": "Complex as simple on every mesh: what you see is what you hit, heavier "
                                              "on memory"}]})
        q.append({"id": "save", "prompt": "Save the level after each build step?",
                  "options": [{"id": "yes", "label": "Yes, save after each step"}, {"id": "end", "label": "Only at the end"}]})
        ready = [f"{len(cities)} cities, {len(actor_docs(nid))} road actors, {total:,} city pieces on {len(groups)} meshes"]
        for c in cities:
            p = (c.get("plan") or {}).get("population") or {}
            extras = [x for x in ((c.get("airport") or {}).get("type"), ", ".join(y["type"] + " shipyard" for y in
                      ((c.get("waterfront") or {}).get("shipyards") or []))) if x]
            ready.append(f"{c['name']}: {p.get('total', 0):,} people" + (f", {', '.join(extras)}" if extras else ""))
        return {"ok": True, "id": nid, "world": doc.get("name"), "asset_set": asset_set, "summary": ready,
                "environment": env, "uefn_check": uefn, "pending": {k: sync.get(k) for k in ("summary", "roads_changed",
                                                                                                "roads_never_pushed", "tiles")},
                # compact: counts per source, the most-used 40 meshes, and with check=true every missing one
                # every building against the landscape the heightmap imports: buried / floating counts (none = 1:1)
                "ground_fit": {k: v for k, v in (fit_summary() or {}).items() if k != "misfits"} or None,
                "needs_by_source": {s: sum(1 for n in needs if n["source"] == s) for s in {n["source"] for n in needs}},
                "needs_top": [{k: n[k] for k in ("mesh", "uses", "for", "source")}
                              for n in sorted(needs, key=lambda n: -n["uses"])[:40]],
                "needs_total": len(needs),
                # what to BUILD, exactly: one entry per mesh this world uses (size, bounds after import, sidewalk)
                "specs": {n["mesh"]: spec_of[n["mesh"]] for n in needs if n["mesh"] in spec_of},
                "no_spec": sorted(n["mesh"] for n in needs if n["mesh"] not in spec_of),
                "rules": {k: pieces.CONVENTIONS[k] for k in ("roads", "bridges", "axes", "pivot", "materials", "collision",
                                                          "export", "import_check", "ground")},
                "junctions": {"by_type": junctions, "total": sum(junctions.values()),
                              "do": "Nothing to model: road_network_build_pieces builds every junction as one mesh fitted "
                                    "to its arms (deck, stop lines, zebra crossings, kerbs, and sidewalk corners joining "
                                    "the arms' own sidewalks), slots Asphalt / Paint / Sidewalk."},
                "road_fit": (fit_summary() or {}).get("roads"),
                # what joins what: every freeway / ramp / highway end against the road it lands on, and every place
                # a road changes width with no junction (all should be 0; each row says where and by how much)
                "connections": loose_ends(nid, (data.get(f"net.{nid}.scan") or {}).get("terrain") or {}),
                "missing": {"street_kit": street_missing, "art": art_missing,
                            "paths": [n["path"] for n in missing][:200],
                            "unknown": None if uefn.get("online") else "call again with check=true while UEFN is open"},
                "questions": q,
                # the two rules every build keeps (cg.RULES has the full text)
                "doors": {"spots": sum(doors.values()), "by_module": doors,
                          "do": "Each door module's spots get an openable Scene Graph door: one Entity Prefab per kind "
                                "(the leaf + a component that opens it on interact), the wall module modelled as an "
                                "open doorway, placed by PCG at those spots (or, if this UEFN's PCG spawn step can't "
                                "take a prefab, at that step's points with the Scene Graph tools). Never a fixed door."},
                "collision": {"none_on": no_collision,
                              "do": "Collision on every mesh before the PCG build: simple (boxes, convex hulls) or, "
                                    "per the person's answer, complex as simple; stairs, ramps, doorways and curved "
                                    "decks complex as simple either way. The layers in none_on place without the PCG "
                                    "collision flag: foliage stays without, but site pieces among them (parks, parking "
                                    "lots) still need collision on their solid parts (trees, benches, kerbs, lamps; "
                                    "per-part UCX, never one box over the site). Tell the person which pieces got "
                                    "which, then playtest that nothing can be walked through."},
                "how": ("Ask every question in ONE ducky_ask_user (pass `questions` as they are), then follow the "
                        "answers: pieces -> road_network_pcg_assets; missing street pieces -> road_network_kit_materials "
                        "(builds them); interiors -> road_network_pcg_assets(interiors='yes'|'no'); missing art -> "
                        "road_network_piece_manifest specs in Blender or AI 3D, or Fortnite "
                        "pieces; collision -> set it on each mesh as `collision` says before the city build; doors "
                        "-> the door prefabs as `doors` says (scenegraph skill), placed by PCG; roads -> "
                        "road_network_import_all; city -> road_network_pcg_build; lighter detail -> "
                        "lower the foliage layers' density with road_network_pcg_layer_save; read road_network_memory "
                        "after every step.")}

    def build_text(nid: str, text: str) -> str:
        """The job an AI gets to build the blueprint in UEFN, with UEFN's own systems, only what changed."""
        doc = data.get(f"net.{nid}") or {}
        actors = sorted((h_.get("label") or a) for a, h_ in actor_docs(nid).items())
        lines = [f"Build City Builder world '{doc.get('name') or nid}' (net_id '{nid}') in the open UEFN level. City Builder "
                 f"is the blueprint; build it with UEFN's own systems, only what changed since the last build."]
        if str(text or "").strip():
            lines.append(f"The person adds: {text.strip()}")
        lines += [
            "Read skill road-network first (SKILL.md: pipeline, rules, memory-safe calls).",
            f"0. FIRST call road_network_build_brief(net_id='{nid}', check=true): what this world needs, what is missing "
            f"from the project, what tools are around (UEFN, Epic MCP, Blender, AI 3D) and what is waiting to be built. "
            f"Tell the person in a few lines, then ask its `questions` in ONE ducky_ask_user (as they are) and wait. "
            f"Do no editor work before the answers; follow them (its `how` maps each answer to a tool). If UEFN is "
            f"closed and they chose to open it, launch it with ducky_launch_uefn (it asks them) and wait for it.",
            f"1. Roads, paths and private access are landscape splines: road_network_import_all(net_id='{nid}') lists the "
            f"actors that changed ({', '.join(actors)}); call it again with confirm=true (junction meshes 8 per call, "
            f"then a paste per actor). Keep the person's hands off UEFN while it pastes.",
            f"2. The city is PCG: road_network_pcg_build(net_id='{nid}') says how many 400 m tiles changed; call it with "
            f"confirm=true (one graph + volume per tile, Generate On Demand).",
            "3. Doors and collision (the brief's `doors` and `collision`): every door a player walks through is an "
            "openable Scene Graph entity placed by PCG at the door spots, never a fixed mesh; every mesh has collision "
            "(simple, or complex as simple where the shape matters; none only on grass, leaves and flat paint). Tell "
            "the person which collision each piece got.",
            "4. After each step read road_network_memory: if a call grew UEFN 6+ GB or the guard tripped, stop and "
            "report it instead of pushing on. Never restart or close UEFN without asking.",
            "5. Never touch the cinema (keep zones) or write the existing terrain. Report what was built and what was "
            "left unchanged."]
        return "\n".join(lines)

    def send_note(net_id: str = "", chat_id: str = "", text: str = "", kind: str = "pin", points: str = "") -> dict:
        nid = slug(net_id)
        if not data.get(f"net.{nid}"):
            return {"ok": False, "error": "Open a world first."}
        try:
            pts = [[float(p[0]), float(p[1])] for p in (json.loads(points) if isinstance(points, str) else points or [])]
        except (ValueError, TypeError, IndexError):
            return {"ok": False, "error": "The marked points could not be read."}
        if kind == "build":
            message = build_text(nid, text)
        elif not pts or not str(text or "").strip():
            return {"ok": False, "error": "Mark a spot or an area and write what should happen there."}
        else:
            message = note_text(nid, text, "area" if kind == "area" and len(pts) >= 3 else "pin", pts)
        conv = chat_id
        if not conv or conv == "new":
            made = api.call_tool("ducky_create_chat", {"title": f"City Builder: {str(text).strip()[:40]}"}) or {}
            conv = made.get("conv_id") or made.get("id") or (made.get("chat") or {}).get("id")
            if not conv:
                return {"ok": False, "error": "Couldn't start a chat."}
        api.call_tool("ducky_send_chat_message", {"conv_id": conv, "wait_for_reply": False, "message": message})
        return {"ok": True, "chat": conv}

    def note_draft(net_id: str = "", kind: str = "pin", points: str = "") -> dict:
        """What the chat's typing box starts with after a pin, an area or a build click: where it is and what is
        there, ending where the person types what they want. Nothing is sent: they finish it and press Enter."""
        nid = slug(net_id)
        if not data.get(f"net.{nid}"):
            return {"ok": False, "error": "Open a world first."}
        if kind == "build":
            return {"ok": True, "draft": build_text(nid, "")}
        try:
            pts = [[float(p[0]), float(p[1])] for p in (json.loads(points) if isinstance(points, str) else points or [])]
        except (ValueError, TypeError, IndexError):
            return {"ok": False, "error": "The marked points could not be read."}
        if not pts:
            return {"ok": False, "error": "Place a pin or draw an area first."}
        lines = [ln for ln in note_text(nid, "", kind, pts).split("\n") if not ln.startswith("How:")]
        lines[0] = lines[0].rstrip(": ").rstrip() + "."
        lines.append("How: City Builder tools (skill road-network): an area-limited PCG layer (road_network_pcg_layer_save "
                     "with \"area\"), checked with road_network_pcg_preview.")
        return {"ok": True, "draft": "\n".join(lines) + "\n\nWhat I want here: "}

    api.register_panel_rpc("note_draft", lambda net_id="", kind="pin", points="": safe(note_draft)(net_id, kind, points))

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_note_draft(net_id: str = "", kind: str = "pin", points: str = "") -> dict:
        """The text the City Builder panel puts in the chat's typing box for a pin (kind 'pin', one [x, y]), an
        area (kind 'area', an outline [[x, y], ...], cm) or a build (kind 'build'): where it is, what is there and
        how to act on it, ending where the person says what they want. Sends nothing."""
        return safe(note_draft)(net_id, kind, points)
    api.register_panel_rpc("version", lambda: plugin_version())
    api.register_panel_rpc("chats", lambda: safe(list_chats)())
    api.register_panel_rpc("send_note", lambda net_id="", chat_id="", text="", kind="pin", points="":
                           safe(send_note)(net_id, chat_id, text, kind, points))

    api.register_panel_rpc("sync", lambda net_id="": safe(sync_status)(net_id))
    api.register_panel_rpc("build_brief", lambda net_id="", check=False: safe(build_brief)(net_id, check))

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_build_brief(net_id: str = "", check: bool = False) -> dict:
        """Start of every build in UEFN: everything this world needs (every road mesh the splines use and every kit
        mesh the PCG city places, how often, and where each comes from: street kit City Builder makes itself,
        road / world / building kit art, Fortnite), what the project is missing (check=true asks the open UEFN),
        what tools are around (UEFN, Epic MCP, Blender, AI 3D), what is waiting to be built (roads to paste, city
        tiles), a summary per city, and the `questions` to ask the person in ONE ducky_ask_user before any editor
        work (open UEFN?, which pieces, how to make missing ones, scope, density, interiors, terrain, saving)."""
        return safe(build_brief)(net_id, check)

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_sync(net_id: str = "") -> dict:
        """Is UEFN behind this world? Road actors changed or never pushed, and PCG tiles to rebuild / remove,
        against what was last built (offline). The panel header shows it; generating again is never blocked."""
        return safe(sync_status)(net_id)

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_version() -> dict:
        """City Builder's version (the panel shows it at the foot of its sidebar)."""
        return plugin_version()

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_chats() -> dict:
        """The recent Ducky chats a map note (road_network_send_note) can be sent to."""
        return safe(list_chats)()

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_send_note(net_id: str = "", chat_id: str = "", text: str = "", kind: str = "pin", points: str = "") -> dict:
        """Send a note about a spot (kind 'pin', one [x, y]) or an area (kind 'area', an outline [[x, y], ...], cm)
        of a world to a Ducky chat (chat_id, or 'new'), with where it is, what is already there and how to act
        on it with City Builder (an area-limited PCG layer); or kind 'build' (no points): ask that chat to build
        the world in UEFN (landscape splines + PCG tiles, only what changed). The panel's tools call this."""
        return safe(send_note)(net_id, chat_id, text, kind, points)
    api.register_panel_rpc("import_all", lambda net_id="", confirm=False, force=False: safe(import_all)(net_id, confirm, force))

    PCG_INTENT = r"\b(pcg|procedural|scatter|placement layers?|foliage|trees|rocks|street (trees|lamps))\b"

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_layers(net_id: str = "") -> dict:
        """A world's PCG placement layers (trees, bushes, rocks, street pieces, buildings, and any the person or an
        AI added): what each places, where, how dense, what it keeps clear of. Also `fields` (every setting with
        its default / min / max), `kinds`, `where`, `roles` and an `example` custom layer: everything needed to
        write a new layer with road_network_pcg_layer_save, no plugin code involved."""
        return safe(pcg_layers)(net_id)

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_layer_save(net_id: str = "", layer: str = "", layer_id: str = "") -> dict:
        """Create or change one PCG layer (the person's own or an AI's: layers are data, never plugin code).
        layer: JSON {name, kind (scatter | foliage | along_roads | at_junctions | buildings), where (outside |
        cities | downtown | normal | housing | industrial | shipyards | everywhere), meshes [{path, weight}] (Content Browser
        paths: a layer WITH meshes places exactly those, weighted; without, the kit piece for its role), density
        (per 100 m2), cover (forest share), reach (foliage is this dense within so many city radii of a city;
        beyond it the layer goes on over the whole map, thinner if it must; 0 = one density everywhere),
        spacing_m, setback_m, scale_min, scale_max,
        sink_cm, footprint, road_clear_m, avoid_roads, avoid [earlier layer names], self_prune, upright,
        random_yaw, collision, seed, order, enabled}. Ranges: road_network_pcg_layers `fields`. Preview with
        road_network_pcg_preview, then Push city (road_network_pcg_build) builds only the changed tiles."""
        return safe(pcg_layer_save)(net_id, layer, layer_id)

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_layer_delete(net_id: str = "", layer_id: str = "") -> dict:
        """Remove one PCG layer from a world (UEFN is not touched)."""
        return safe(pcg_layer_delete)(net_id, layer_id)

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_preset(net_id: str = "", preset: str = "full_city", reorder: bool = False, replace: bool = False) -> dict:
        """Apply a preset to a world: every layer it holds in one call. Built in: 'full_city' (16 building types,
        traffic lights / stop and street signs on junction corners, lamps, street trees, bus stops, benches, bins,
        hydrants, billboards, power poles, and trees / rocks / bushes as Foliage-mode layers, no grass), 'city',
        'buildings', 'street' or 'nature'. Or one saved with road_network_preset_save (road_network_presets lists
        them). Existing layers keep their settings unless replace=true (the preset's settings win); reorder=true
        only moves them into planning order (big civic sites first, dense fill after). A saved preset's world
        options come back as `options`: pass them to road_network_generate to get its terrain and streets too."""
        return safe(pcg_preset)(net_id, preset, reorder, replace)

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_presets() -> dict:
        """Every City Builder preset: the built-in layer sets and the ones saved here by a person or an AI (name,
        how many layers, their names, the world options kept with it, its note). Apply one with
        road_network_pcg_preset."""
        return safe(presets)()

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_preset_save(name: str = "", net_id: str = "", layers: str = "", options: str = "", note: str = "") -> dict:
        """Make or change a preset on the fly: a named set of placement layers, kept with this plugin's data and
        usable on any world. layers: a JSON list of layers exactly as road_network_pcg_layer_save takes one (name,
        kind, role, where, density, sizes, meshes...; road_network_describe lists the kinds, roles and areas). Or
        leave layers empty and give net_id: that world's layers as they are now, with its world options. options:
        JSON of generate options to keep with it (road_network_generate_options lists them), e.g.
        {"block_m": 80, "wobble": 0, "city_ground": "flatten"}. note: what the preset is for, in a line. Saving an
        existing name replaces that preset. Apply it with road_network_pcg_preset(net_id, preset=name)."""
        return safe(preset_save)(name, net_id, layers, options, note)

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_preset_delete(preset: str = "", confirm: bool = False) -> dict:
        """Delete a saved City Builder preset (never a built-in one). Worlds it was applied to keep their layers.
        confirm=true to go ahead."""
        return safe(preset_delete)(preset, confirm)

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_plan(net_id: str = "", road_tag: str = "") -> dict:
        """The PCG graph that places a world's layers in UEFN: native nodes in data-flow order with their
        settings, the wires, the graph params, the road tag and the volume. Build it with Epic PCGToolset
        (skill pcg/graph_layers)."""
        return safe(pcg_plan)(net_id, road_tag)

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_assets(net_id: str = "", asset_set: str = "", interiors: str = "") -> dict:
        """Which pieces City Builder uses for a world's layers: 'own' (the project's own kit: WorldGen/City,
        WorldGen/Kit, BuildingKit storeys), 'fortnite' (Fortnite's props and buildings) or 'mixed', and whether
        buildings get interiors (interiors 'yes' / 'no': floors, stair cores, furniture seen through windows).
        Empty arguments read them, with where each role (traffic light, skyscraper, bench...) comes from."""
        return safe(pcg_assets)(net_id, asset_set, interiors)

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_preview(net_id: str = "", layer_id: str = "", summary: bool = False) -> dict:
        """Run a world's PCG layers offline with the graph's rules (district, off the roads, clear of earlier
        layers, no overlaps, nothing under water) and return where each would place things plus its count;
        the panel draws it in 3D. layer_id: just that layer. summary=true: only the count per layer."""
        return safe(pcg_preview)(net_id, layer_id, summary)

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_ground_fit(net_id: str = "") -> dict:
        """Is the ground right for the city as it is now? Buildings the ground would bury or leave floating and
        street points it would cover or leave in the air (all must be 0 before the heightmap is imported and the
        city pushed). The ground is re-cut from the layout on every change: lots level, roads cut and filled."""
        return safe(ground_fit)(net_id, False)

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_exact(net_id: str = "", x: float = 0.0, y: float = 0.0, radius_m: float = 400.0,
                               summary: bool = True) -> dict:
        """What road_network_pcg_build would place within radius_m of (x, y) (cm), before pushing: each building,
        site and wall piece with its mesh's real box and the ground under them as the landscape has it. The
        panel's 'Real pieces here' draws it. summary=true (default): piece counts per mesh, meshes with no known
        size, and ground_fit (buildings the ground would bury or leave floating; must be 0 before a push)."""
        return safe(pcg_exact)(net_id, float(x), float(y), float(radius_m), summary)

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_build(net_id: str = "", confirm: bool = False, force: bool = False,
                               stop: bool = False) -> dict:
        """Build the world's previewed city in the open UEFN level as PCG of the project's own kit, in 400 m
        tiles (one graph + volume each). Incremental: only tiles whose lots changed since the last build are
        rebuilt, tiles gone from the plan are removed, the rest of the city, the roads and the level are left
        alone, so editing the city plan never means deleting everything. Without confirm it reports how many
        tiles would change. force=true rebuilds every tile. Ask the person first. confirm=true starts the build
        in the background (batches of tiles, memory checked between them) and returns at once: call again
        for progress (done / total) and the last result; stop=true stops it after the batch in flight."""
        return safe(pcg_build)(net_id, confirm, force, False, stop)

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_place(net_id: str = "", confirm: bool = False) -> dict:
        """One click: start an AI chat that builds the world's PCG layers in the open UEFN level from
        road_network_pcg_plan. Ask the person first."""
        return safe(pcg_place)(net_id, confirm)

    api.register_panel_rpc("pcg_layers", lambda net_id="": safe(pcg_layers)(net_id))
    api.register_panel_rpc("pcg_layer_save", lambda net_id="", layer="", layer_id="": safe(pcg_layer_save)(net_id, layer, layer_id))
    api.register_panel_rpc("pcg_layer_delete", lambda net_id="", layer_id="": safe(pcg_layer_delete)(net_id, layer_id))
    api.register_panel_rpc("pcg_preset", lambda net_id="", preset="full_city", reorder=False, replace=False:
                           safe(pcg_preset)(net_id, preset, reorder, replace))
    api.register_panel_rpc("presets", lambda: safe(presets)())
    api.register_panel_rpc("preset_save", lambda name="", net_id="", layers="", options="", note="":
                           safe(preset_save)(name, net_id, layers, options, note))
    api.register_panel_rpc("preset_delete", lambda preset="", confirm=False: safe(preset_delete)(preset, confirm))
    api.register_panel_rpc("pcg_plan", lambda net_id="", road_tag="": safe(pcg_plan)(net_id, road_tag))
    api.register_panel_rpc("pcg_assets", lambda net_id="", asset_set="", interiors="": safe(pcg_assets)(net_id, asset_set, interiors))
    api.register_panel_rpc("pcg_preview", lambda net_id="", layer_id="": safe(pcg_preview)(net_id, layer_id))
    api.register_panel_rpc("ground_fit", lambda net_id="": safe(ground_fit)(net_id))
    api.register_panel_rpc("pcg_exact", lambda net_id="", x=0.0, y=0.0, radius_m=400.0: safe(pcg_exact)(
        net_id, float(x), float(y), float(radius_m)))
    api.register_panel_rpc("pcg_place", lambda net_id="", confirm=False: safe(pcg_place)(net_id, confirm))
    api.register_panel_rpc("pcg_build", lambda net_id="", confirm=False, force=False: safe(pcg_build)(net_id, confirm, force))

    @api.register_pipeline_node("road-network.pcg_plan")
    def node_pcg_plan(ctx: dict) -> dict:
        cfg = ctx.get("config") or {}
        out = safe(pcg_plan)(str(cfg.get("net_id") or ""), str(cfg.get("road_tag") or ""))
        return {**out, "plan": out if out.get("ok") else None}

    @api.register_pipeline_node("road-network.generate")
    def node_generate(ctx: dict) -> dict:
        cfg = ctx.get("config") or {}
        if not cfg.get("name"):
            return {"ok": False, "error": "Set the world name."}
        out = safe(generate)(str(cfg["name"]), int(cfg.get("seed") or 7), str(cfg.get("cities") or ""),
                             float(cfg.get("size_km") or 6), float(cfg.get("relief_m") or 60),
                             str(cfg.get("ground_from") or ""), "", 0.0, 0.0, str(cfg.get("options") or ""))
        hm = safe(heightmap)(out["id"]) if out.get("ok") and not cfg.get("ground_from") else {}
        return {**out, "network": out.get("id"), "heightmap": hm.get("path", "")}

    @api.register_pipeline_node("road-network.import_all")
    def node_import_all(ctx: dict) -> dict:
        cfg = ctx.get("config") or {}
        return safe(import_all)(str(cfg.get("net_id") or ""), bool(cfg.get("confirm")))

    # ---------------------------------------------------------------- workflow nodes
    @api.register_pipeline_node("road-network.stats")
    def node_stats(ctx: dict) -> dict:
        cfg = ctx.get("config") or {}
        out = safe(stats)(str(cfg.get("net_id") or ""))
        return {**out, "stats": out.get("total")}

    @api.register_pipeline_node("road-network.pull")
    def node_pull(ctx: dict) -> dict:
        cfg = ctx.get("config") or {}
        if not cfg.get("name"):
            return {"ok": False, "error": "Set the network name."}
        out = safe(pull)("", str(cfg.get("name")), str(cfg.get("project") or ""), str(cfg.get("actors") or ""))
        return {**out, "network": out.get("id")}

    @api.register_pipeline_node("road-network.reimport")
    def node_reimport(ctx: dict) -> dict:
        cfg = ctx.get("config") or {}
        return safe(reimport)(str(cfg.get("net_id") or ""), str(cfg.get("actor") or ""), bool(cfg.get("confirm")))

    # ---------------------------------------------------------------- panel RPCs
    api.register_panel_rpc("projects", lambda: safe(list_projects)())
    api.register_panel_rpc("project_save", lambda project_id="", name="", description="": safe(save_project)(project_id, name, description))
    api.register_panel_rpc("list", lambda project="": safe(list_networks)(project))
    api.register_panel_rpc("get", lambda net_id="": safe(get_network)(net_id))
    api.register_panel_rpc("save", lambda net_id="", name="", project="", terrain_mode="": safe(save_network)(net_id, name, project, terrain_mode))
    api.register_panel_rpc("delete", lambda net_id="": safe(delete_network)(net_id))
    api.register_panel_rpc("pull", lambda net_id="", name="", project="", actors="": safe(pull)(net_id, name, project, actors))
    api.register_panel_rpc("stats", lambda net_id="": safe(stats)(net_id))
    api.register_panel_rpc("geometry", lambda net_id="", actor="", offset=0, limit=0, terrain=True:
                           safe(geometry)(net_id, actor, offset, limit, terrain))
    api.register_panel_rpc("move_point", lambda net_id="", actor="", index=-1, x=0.0, y=0.0, z=0.0: safe(move_point)(net_id, actor, index, x, y, z))
    api.register_panel_rpc("tune", lambda net_id="", actor="", thin_tolerance_cm=50.0, side_falloff=800.0,
                           end_falloff=1000.0, width=0.0, paint_layer="", mesh_offset_cm=-1.0: safe(tune_actor)(
                               net_id, actor, thin_tolerance_cm, side_falloff, end_falloff, width, paint_layer,
                               mesh_offset_cm))
    api.register_panel_rpc("build_pieces", lambda net_id="", actor="", rebuild=False: safe(build_pieces)(net_id, actor, rebuild))
    api.register_panel_rpc("kit_materials", lambda net_id="", street_kit=True: safe(kit_materials)(net_id, street_kit))
    api.register_panel_rpc("kit_convert", lambda net_id="", actor="": safe(kit_convert)(net_id, actor))
    api.register_panel_rpc("check", lambda net_id="", actor="": safe(check)(net_id, actor))
    api.register_panel_rpc("repair", lambda net_id="", actor="": safe(repair_actor)(net_id, actor))
    api.register_panel_rpc("scan_ground", lambda net_id="", grid=200: safe(scan_ground)(net_id, grid))
    api.register_panel_rpc("drape", lambda net_id="", actor="", smooth=4, all_points=False: safe(drape)(net_id, actor, smooth, all_points))
    api.register_panel_rpc("copy_text", lambda net_id="", actor="": safe(copy_text)(net_id, actor))
    api.register_panel_rpc("reimport", lambda net_id="", actor="", confirm=False: safe(reimport)(net_id, actor, confirm))

    @api.tool(intent=INTENT, listener=False)
    def road_network_geometry(net_id: str = "", actor: str = "", offset: int = 0, limit: int = 0,
                              terrain: bool = True) -> dict:
        """Control points and segments of a road network (what the 3D view draws); limit pages one actor."""
        return safe(geometry)(net_id, actor, offset, limit, terrain)

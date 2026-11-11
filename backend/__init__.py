"""Road Network: city road networks for UEFN, viewed and edited in 3D, reimported in one go.

Data (team-shared through api.data, one doc per record):
  project.<pid>                         {name, description}
  net.<nid>                             {name, project, actors, terrain_mode, ...}
  net.<nid>.actor.<slug>                {name, label, chunks, counts}
  net.<nid>.actor.<slug>.p.<k>          {name, pts: [...]}   (1000 control points each)
  net.<nid>.actor.<slug>.s.<k>          {name, segs: [...]}  (1000 segments each)
  net.<nid>.scan                        {name, props, terrain}
  net.<nid>.city.<slug>                 {name, x, y, radius, style, districts, buildings}  (generated worlds)
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import time

from . import citygen as cg
from . import kit_bake
from . import pcg_layers as pl
from . import pieces
from . import pipeline
from . import splines_text as st
from . import uefn_input
from . import uefn_memory

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
        run.__name__ = fn.__name__
        run.__doc__ = fn.__doc__
        return run

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

    def epic(toolset: str, tool: str, arguments: dict):
        # Same route as Ducky's own Epic callers: the pool keeps the nested arguments intact
        # (api.call_tool's dispatcher drops them for unreal__call_tool).
        from backend.mcp_plugins.client_pool import get_plugin_pool
        pool = get_plugin_pool()
        out = uefn_memory.metered(f"{toolset.rsplit('.', 1)[-1]}.{tool}", lambda: pool.run_sync(pool.call_tool(
            "unreal__call_tool", {"toolset_name": toolset, "tool_name": tool, "arguments": arguments}), timeout=120))
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

    def actor_docs(nid: str) -> dict:
        return {k.rsplit(".", 1)[1]: v for k, v in data.items(f"net.{nid}.actor.").items() if k.count(".") == 3}

    def load_actor(nid: str, aslug: str) -> tuple:
        head = data.get(f"net.{nid}.actor.{aslug}") if nid and aslug else None
        if not head:
            return None, [], []
        pts, segs = [], []
        for k in range(int(head.get("p_chunks") or 0)):
            pts += (data.get(f"net.{nid}.actor.{aslug}.p.{k}") or {}).get("pts", [])
        for k in range(int(head.get("s_chunks") or 0)):
            segs += (data.get(f"net.{nid}.actor.{aslug}.s.{k}") or {}).get("segs", [])
        return head, pts, segs

    def store_actor(nid: str, label: str, parsed: dict, net_name: str) -> dict:
        aslug = slug(label)
        pts, segs = parsed["points"], parsed["segments"]
        old = data.get(f"net.{nid}.actor.{aslug}") or {}
        pc = (len(pts) + CHUNK - 1) // CHUNK
        sc = (len(segs) + CHUNK - 1) // CHUNK
        for k in range(pc):
            data.put(f"net.{nid}.actor.{aslug}.p.{k}", {"name": f"{net_name} / {label} points {k + 1}", "pts": pts[k * CHUNK:(k + 1) * CHUNK]})
        for k in range(sc):
            data.put(f"net.{nid}.actor.{aslug}.s.{k}", {"name": f"{net_name} / {label} segments {k + 1}", "segs": segs[k * CHUNK:(k + 1) * CHUNK]})
        for k in range(pc, int(old.get("p_chunks") or 0)):
            data.delete(f"net.{nid}.actor.{aslug}.p.{k}")
        for k in range(sc, int(old.get("s_chunks") or 0)):
            data.delete(f"net.{nid}.actor.{aslug}.s.{k}")
        head = {"name": f"{net_name} / {label}", "label": label, "p_chunks": pc, "s_chunks": sc,
                "stats": st.stats(pts, segs), "updated": time.time()}
        data.put(f"net.{nid}.actor.{aslug}", head)
        return head

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

    def geometry(net_id: str = "", actor: str = "", offset: int = 0, limit: int = 0, terrain: bool = True) -> dict:
        """Everything the 3D view draws for one actor (or all), plus terrain and props.
        limit > 0 pages one actor: points and segments [offset, offset + limit), with their totals,
        so no reply nears the 1 MB panel bridge line (a big city is several MB)."""
        got = get_network(net_id)
        if not got.get("ok"):
            return got
        nid = got["id"]
        out = {}
        lo, hi = max(0, int(offset)), (max(0, int(offset)) + int(limit)) if int(limit) > 0 else None
        for aslug, head in actor_docs(nid).items():
            if actor and aslug != slug(actor) and head.get("label") != actor:
                continue
            _, pts, segs = load_actor(nid, aslug)
            out[aslug] = {"label": head.get("label"), "points_total": len(pts), "segments_total": len(segs),
                          "pts": [[p["i"], round(p["x"]), round(p["y"]), round(p["z"]), round(p["pitch"], 2),
                                   round(p["yaw"], 2), round(p["w"]), p["mesh"], 1 if p["raise"] else 0] for p in pts[lo:hi]],
                          "segs": [[s["i"], s["a"], s["b"], round(s["ta"]), round(s["tb"]), s["mesh"]] for s in segs[lo:hi]]}
        scan = (data.get(f"net.{nid}.scan") or {}) if terrain else {}
        return {"ok": True, "id": nid, "actors": out, "terrain": scan.get("terrain", {}), "props": scan.get("props", [])}

    def move_point(net_id: str = "", actor: str = "", index: int = -1, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> dict:
        nid, aslug = slug(net_id), slug(actor)
        head, pts, segs = load_actor(nid, aslug)
        if not head:
            return {"ok": False, "error": f"No actor {actor!r} in network {net_id!r}."}
        before = next((dict(p) for p in pts if p["i"] == int(index)), None)
        if before is None:
            return {"ok": False, "error": f"No point {index} in {actor}."}
        st.move_point(pts, segs, int(index), x, y, z)
        # rewrite only the chunks that changed: the point's chunk and the touched segments' chunks
        k = next(n for n, p in enumerate(pts) if p["i"] == int(index)) // CHUNK
        data.put(f"net.{nid}.actor.{aslug}.p.{k}", {"name": f"{head['name']} points {k + 1}", "pts": pts[k * CHUNK:(k + 1) * CHUNK]})
        touched = {n // CHUNK for n, s in enumerate(segs) if int(index) in (s["a"], s["b"])}
        for c in touched:
            data.put(f"net.{nid}.actor.{aslug}.s.{c}", {"name": f"{head['name']} segments {c + 1}", "segs": segs[c * CHUNK:(c + 1) * CHUNK]})
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
                    {"name": f"{prefix}{node}", "arms": angles, "half": half(arms)}
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
                m = re.search(r"'(/[^']+?/WorldGen/Roads)/SM_Road_", ln)
                if m:
                    return m.group(1) + "/Junctions/Kit"
        return ""

    def road_folder() -> str:
        """The project's own road meshes folder, read off any stored network's first segments."""
        for key in data.keys("net."):
            if re.fullmatch(r"net\.[^.]+\.actor\.[^.]+\.s\.0", key):
                for s in (data.get(key) or {}).get("segs", []):
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
        png, settings = cg.heightmap_png(ter, pads=pl.pads(got.get("layers") or {}))
        path = heightmap_path(nid)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "wb") as fh:
            fh.write(png)
        os.replace(path + ".tmp", path)
        return {"path": path, **settings}

    def _layout_doc(layout: dict) -> dict:
        return {"lots": layout["lots"], "hoods": layout["hoods"], "plan": layout.get("plan"),
                "private": {k: layout.get(k) or [] for k in ("streets", "parking", "stalls", "pads", "grounds")}}

    def city_docs(nid: str) -> dict:
        return {k.rsplit(".", 1)[1]: v for k, v in data.items(f"net.{nid}.city.").items() if k.count(".") == 3}

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
        if text.startswith("["):
            try:
                given = json.loads(text)
            except ValueError as exc:
                return {"ok": False, "error": f"cities is not valid JSON: {exc}"}
            towns = [{"name": str(c.get("name") or cg.NAMES[k % len(cg.NAMES)]), "x": float(c["x"]), "y": float(c["y"]),
                      "radius": float(c.get("radius_m", 450)) * 100, "style": c.get("style") if c.get("style") in cg.STYLES else "mixed",
                      "seed": int(c.get("seed", seed + k))} for k, c in enumerate(given)]
        elif not text and old:  # regenerate the cities already there (their names, styles and buildings stay)
            towns = [{k: v for k, v in c.items() if k in ("name", "x", "y", "radius", "style", "seed")} for c in old.values()]
            # City size changed since the last run: every city grows / shrinks by the same factor; then they are
            # pushed apart until the distance between cities holds
            grow = opts["city_size"] / float((last.get("options") or {}).get("city_size", 1.0) or 1.0)
            for t in towns:
                t["radius"] = max(15000.0, t["radius"] * grow)
            towns = cg.spread(towns, opts["city_gap_m"] * 100, keep)
        else:
            towns = cg.place_cities(int(seed), max(1, min(12, int(text or 3))), bounds, keep, opts["city_size"],
                                    opts["city_gap_m"] * 100)
        if not towns:
            return {"ok": False, "error": "No room for that many cities: make the map bigger or ask for fewer."}
        ter = base or cg.terrain(int(seed), size, max(0.0, float(relief_m)) * 100, towns, center=(center_x, center_y),
                                 opts=opts, keep=keep)
        water = ter.get("sea")
        # roads never dive into a lake: across water they stay 1.5 m above it (a causeway)
        road_ground = ((lambda x, y: max(st.sample_grid(ter, x, y) or 0.0, water + 150)) if water is not None
                       else (lambda x, y: st.sample_grid(ter, x, y) or 0.0))
        wet = cg.wet_from(ter)  # roads know the water: main roads and highways bridge it, side streets stop
        actors = cg.generate(towns, road_ground, folder, int(seed), opts, keep, wet)
        before = data.get(f"net.{nid}")
        saved = save_network(nid, name, "", "live")
        if not saved.get("ok"):
            return saved
        for aslug in actor_docs(nid):  # roads of a previous run
            if aslug not in {slug(label) for label in actors}:
                head = f"net.{nid}.actor.{aslug}"
                for key in [k for k in data.keys(head) if k == head or k.startswith(head + ".")]:
                    data.delete(key)
        for cslug in old:
            if cslug not in {slug(t["name"]) for t in towns}:
                data.delete(f"net.{nid}.city.{cslug}")
        stats_out = {label: store_actor(nid, label, a, name)["stats"] for label, a in actors.items()}
        # terrain is a big blob (a 160 grid is ~200 KB): one doc, never edited by hand, so it never conflicts
        data.put(f"net.{nid}.scan", {"name": f"{name} / terrain", "props": [], "terrain": ter})
        freeways = {t["name"]: cg.freeway_report(t, opts) for t in towns}
        for t in towns:
            cslug = slug(t["name"])
            prev = old.get(cslug) or {}
            api.search.upsert(f"net.{nid}.city.{cslug}", t["name"], f"City {t['name']} in world {name}: {t['style']} roads")
            data.put(f"net.{nid}.city.{cslug}", {**t, "name": t["name"], "roads": f"{t['name']} Roads",
                                                 "districts": cg.districts(t, opts),
                                                 # BlockGenerator + neighbourhoods: lots, private streets, parking
                                                 **_layout_doc(cg.city_layout(
                                                     t, opts, wet, lambda x, y: st.sample_grid(ter, x, y) or 0.0,
                                                     ((prev.get("plan") or {}).get("facilities")))),
                                                 "freeways": freeways[t["name"]],
                                                 "buildings": prev.get("buildings") or {"downtown": [], "normal": [], "housing": []}})
        doc = data.get(f"net.{nid}")
        doc["generated"] = {"seed": int(seed), "size_km": size / 100000, "relief_m": float(relief_m),
                            "ground_from": slug(ground_from), "folder": folder, "options": opts,
                            "cities": len(towns), "center": [center_x, center_y], "keep": keep}
        doc.pop("heightmap", None)  # written on demand (road_network_heightmap): keeps live preview fast
        doc["actors"] = sorted(actors)
        doc["updated"] = time.time()
        data.put(f"net.{nid}", doc)
        api.search.upsert(f"net.{nid}", name, f"Generated world {name}: cities " + ", ".join(t["name"] for t in towns))
        if not layer_docs(nid):  # a new world comes with every layer a city needs: generate -> push, nothing else
            pcg_preset(nid, "full_city")
        paths = sidewalks_stage(nid)  # SidewalkGenerator: sidewalks, walkways, driveways as spline actors
        stats_out.update((paths.get("actors") or {}) if paths.get("ok") else {})
        record("road_network_generate", "network", nid, before,
               [] if before else [{"command": "road_network_delete", "params": {"net_id": nid}}])
        push({"what": "network", "id": nid})
        return {"ok": True, "id": nid, "cities": [{"name": t["name"], "style": t["style"], "x": round(t["x"]),
                                                    "y": round(t["y"]), "radius_m": round(t["radius"] / 100)} for t in towns],
                "actors": stats_out,
                "freeways": {n: {"interchanges": [i["kind"] for i in r["interchanges"]],
                                 "validator_ok": r["validation"]["ok"],
                                 **({} if r["validation"]["ok"] else {"validation": r["validation"]})}
                             for n, r in freeways.items()},
                "note": "Preview it in the Road Network panel. For the generated terrain, make the heightmap "
                        "(road_network_heightmap) and import it first, then Import into UEFN." if not base else
                        "Roads follow this network's ground. Import into UEFN when it looks right."}

    def list_cities(net_id: str = "") -> dict:
        nid = slug(net_id)
        items = [{"id": k, **v} for k, v in city_docs(nid).items()]
        return {"ok": True, "id": nid, "items": sorted(items, key=lambda c: c["name"]), "count": len(items)}

    def save_city(net_id: str = "", city: str = "", name: str = "", style: str = "", radius_m: float = 0.0,
                  buildings: str = "") -> dict:
        """Change one city's style, size or building sets ({"downtown": [mesh paths], ...})."""
        nid, cslug = slug(net_id), slug(city)
        before = data.get(f"net.{nid}.city.{cslug}") if nid and cslug else None
        if not before:
            return {"ok": False, "error": f"No city {city!r} in {net_id!r}. List them with road_network_cities."}
        if style and style not in cg.STYLES:
            return {"ok": False, "error": f"style is one of {', '.join(cg.STYLES)}."}
        doc = dict(before)
        if style:
            doc["style"] = style
        if radius_m:
            doc["radius"] = max(150.0, float(radius_m)) * 100
            doc["districts"] = cg.districts(doc)
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
            data.delete(f"net.{nid}.city.{cslug}")
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
        if doc["generated"].get("ground_from"):
            return {"ok": False, "error": "This world uses an existing terrain, so it has no heightmap of its own."}
        ter = (data.get(f"net.{nid}.scan") or {}).get("terrain") or {}
        if not ter.get("heights"):
            return {"ok": False, "error": "This world has no stored terrain. Generate it again."}
        hm = write_heightmap(nid, ter)  # always from the stored terrain, so every PC gets the same file
        return {"ok": True, "id": nid, **hm,
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

    def pcg_preset(net_id: str = "", preset: str = "full_city", reorder: bool = False) -> dict:
        nid = slug(net_id)
        if not data.get(f"net.{nid}"):
            return {"ok": False, "error": f"No road network {net_id!r}."}
        added, reordered, undo = [], [], []
        for layer in pl.preset(preset):
            lid = slug(layer["name"])
            have = data.get(f"net.{nid}.pcg.{lid}")
            if not have:  # never overwrite a layer the person already tuned
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
        return {"ok": True, "id": nid, "added": added, "reordered": reordered}

    def road_tag_for(nid: str) -> str:
        return "CityRoads" if not (data.get(f"net.{nid}") or {}).get("generated") else f"{nid}_roads"

    def asset_set_of(nid: str) -> str:
        return (data.get(f"net.{nid}.assets") or {}).get("asset_set") or "own"

    def pcg_assets(net_id: str = "", asset_set: str = "") -> dict:
        """Which pieces a world's layers use: 'own' (the project's kit), 'fortnite' or 'mixed'. Empty = read it."""
        nid = slug(net_id)
        doc = data.get(f"net.{nid}") if nid else None
        if not doc:
            return {"ok": False, "error": f"No road network {net_id!r}."}
        if asset_set:
            if asset_set not in pl.ASSET_SETS:
                return {"ok": False, "error": f"asset_set is one of {', '.join(pl.ASSET_SETS)}."}
            before = data.get(f"net.{nid}.assets")
            data.put(f"net.{nid}.assets", {"name": f"{doc.get('name', nid)} pieces", "asset_set": asset_set})
            record("road_network_pcg_assets", "pcg-assets", nid, before,
                   [{"command": "road_network_pcg_assets", "params": {"net_id": nid,
                                                                      "asset_set": (before or {}).get("asset_set", "own")}}])
            push({"what": "pcg", "id": nid})
        cur = asset_set_of(nid)
        return {"ok": True, "id": nid, "asset_set": cur,
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

    _PREVIEW_CACHE: dict = {}
    FOREST_LIMIT = 200000  # foliage points per layer kept and baked: very dense forests; tiled PCG builds them 400 m at a time

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
        key = hashlib.sha1(json.dumps([nid, docs, cities, terrain, actors, keep], sort_keys=True, default=str)
                           .encode()).hexdigest()
        if _PREVIEW_CACHE.get("key") != key:  # ponytail: one entry, the world being looked at
            ground = lambda x, y: st.sample_grid(terrain, x, y) if terrain.get("heights") else 0.0
            placed = pl.preview(list(docs.values()), cities, terrain, pl.road_samples(actors), ground,
                                limit=6000, juncs=pl.junctions(actors), keep=keep, foliage_limit=FOREST_LIMIT)  # the whole city
            # FoundationGenerator for the roads: stone walls where the graded road is cut into / built up on the
            # ground, from the height difference (not a layer to edit: it follows the roads and the terrain)
            walls = pl.road_walls(actors, ground) if terrain.get("heights") else []
            placed["Road walls"] = {"kind": "walls", "role": "road_wall", "count": len(walls), "shown": len(walls),
                                    "points": walls, "meshes": [],
                                    "row": "x, y, base z, yaw, 1, length_m, height_m"}
            placed.update(pl.site_layers(cities, ground))  # complexes' parking, stalls, turnarounds, garages
            _PREVIEW_CACHE.update(key=key, out=placed)
        out = _PREVIEW_CACHE["out"]
        if layer_id == "road-walls":  # the built-in layers the plan never lists: walls and neighbourhood sites
            out = {k: v for k, v in out.items() if v.get("kind") in ("walls", "site")}
        elif layer_id:
            name = (docs.get(slug(layer_id)) or {}).get("name")
            out = {name: out[name]} if name in out else {}
        if summary:  # counts only: a quick check for an agent, no point lists
            return {"ok": True, "id": nid, "counts": {k: v["count"] for k, v in out.items()}}
        return {"ok": True, "id": nid, "layers": out}

    def kit_root() -> str:
        folder = road_folder()
        return folder.split("/WorldGen/Roads")[0] if "/WorldGen/Roads" in folder else ""

    def kit_materials(net_id: str = "", street_kit: bool = True) -> dict:
        root = kit_root()
        if not root:
            return {"ok": False, "error": "Couldn't find the project's WorldGen folder (import the roads first)."}
        street = street_kit_build(root) if street_kit else None
        out = listener("road_network_kit_materials", {"root": root}, timeout=900)
        return {**out, "street_kit": street}

    def street_kit_build(root: str, rebuild: bool = False) -> dict:
        """The missing street kit pieces, ONE listener call (one editor frame) per mesh, then one save call.
        Every mesh build and save opens a progress window, and UEFN frees a window's GPU swap chain only when
        the frame ends: 16 meshes in one call stacked 32 swap chains in one frame, took UEFN past 120 GB and
        crashed it on the D3D12 descriptor limit."""
        todo = listener("road_network_street_kit", {"root": root, "rebuild": rebuild, "plan": True}).get("todo") or []
        made, errors = [], []
        for name in todo:
            got = listener("road_network_street_kit", {"root": root, "rebuild": rebuild, "only": [name], "save": False},
                           timeout=300)
            made += got.get("paths") or []
            errors += got.get("errors") or []
        if made:
            listener("road_network_save_assets", {"paths": made}, timeout=300)
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
        rows_by: dict = {t["name"]: [] for t in towns}
        for layer in (got.get("layers") or {}).values():
            if layer.get("kind") != "buildings":
                continue
            for r in layer.get("points") or []:
                t = min(towns, key=lambda c: (c["x"] - r[0]) ** 2 + (c["y"] - r[1]) ** 2)
                rows_by[t["name"]].append(r)
        out = {}
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
                info = {"grid": ter.get("grid"), "slope": pipeline.slope_map(ter), "keep_zones": len(gen.get("keep") or []),
                        "water_level_cm": ter.get("sea")}
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
                groups = kit_bake.bake(got.get("layers") or {}, folder.split("/WorldGen/Roads")[0])[0]
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

    def pcg_build(net_id: str = "", confirm: bool = False, force: bool = False) -> dict:
        """The world's previewed city as PCG in the open UEFN level, in 400 m tiles (one graph + volume each).
        Only tiles whose content changed since the last build are rebuilt, tiles gone from the plan are
        removed, and every other tile, the roads and the rest of the level are left alone: edit the plan,
        build again, and just the edited part changes. force=true rebuilds every tile."""
        nid = slug(net_id)
        doc = data.get(f"net.{nid}") if nid else None
        if not doc:
            return {"ok": False, "error": f"No road network {net_id!r}."}
        folder = road_folder()
        if "/WorldGen/Roads" not in folder:
            return {"ok": False, "error": "Couldn't find the project's WorldGen folder (import the roads first)."}
        root = folder.split("/WorldGen/Roads")[0]
        got = pcg_preview(nid)
        if not got.get("ok"):
            return got
        groups, baked, missing = kit_bake.bake(got.get("layers") or {}, root)
        name = doc.get("name") or nid
        tag = re.sub(r"[^A-Za-z0-9]", "", name)
        new = kit_bake.tiles(groups)
        state = data.get(f"net.{nid}.pcg") or {"name": f"{name} / PCG tiles", "tiles": {}}
        if state["tiles"]:  # pull: a tile whose volume was deleted in the level is built again
            try:
                present = {str(a.get("label") or "") for a in epic("EditorToolset.LevelToolset", "FindActors", {
                    "filter": {"name": f"City Builder {name}"}}).get("returnValue", [])}
                for key in [k for k in state["tiles"] if f"City Builder {name} {k}" not in present]:
                    state["tiles"][key] = {**state["tiles"][key], "hash": "gone from the level", "volume": ""}
            except (ConnectionError, RuntimeError, TimeoutError, ValueError):
                pass  # UEFN not reachable: diff against what was pushed last
        build, remove, same = kit_bake.tile_diff(new, state["tiles"], force)
        if not confirm:
            return {"ok": False, "error": f"{name}: {len(build)} PCG tiles to (re)build, {len(remove)} to remove, "
                                          f"{len(same)} unchanged (left alone). {sum(baked.values())} buildings and "
                                          f"street pieces on {len(groups)} kit meshes. Call again with confirm=true.",
                    "build": build, "remove": remove, "unchanged": len(same)}
        if not build and not remove:
            return {"ok": True, "id": nid, "note": "Nothing changed since the last build: every tile left as it is.",
                    "unchanged": len(same)}
        mats = kit_materials(nid) if build else None  # instance-safe textured materials first, or the grey checker
        if mats is not None and not mats.get("ok"):
            return {"ok": False, "error": f"Kit materials: {mats.get('error')}"}
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

        def drop(key: str) -> None:
            old = state["tiles"].pop(key, None) or {}
            if old.get("volume"):
                try:
                    epic("EditorToolset.LevelToolset", "RemoveFromScene", {"actor": {"refPath": old["volume"]}})
                except ValueError:
                    pass  # already gone from the level
            if old.get("graph_path") and epic("EditorToolset.AssetToolset", "Exists", {"path": old["graph_path"]}).get("returnValue"):
                epic("EditorToolset.AssetToolset", "Delete", {"path": old["graph_path"]})

        nodes, messages, done = 0, [], []
        try:
            for key in remove:
                drop(key)
                data.put(f"net.{nid}.pcg", state)
            for key in build:
                drop(key)
                tile = new[key]
                graph_path = f"{root}/WorldGen/CityPCG/PCG_{tag}_T{key.replace('-', 'm')}"
                if epic("EditorToolset.AssetToolset", "Exists", {"path": graph_path}).get("returnValue"):
                    epic("EditorToolset.AssetToolset", "Delete", {"path": graph_path})
                graph = epic("PCGToolset.PCGToolset", "CreateGraph", {"name": graph_path.rsplit("/", 1)[1],
                                                                      "path": graph_path.rsplit("/", 1)[0]})["returnValue"]
                for k, (mesh, rows) in enumerate(sorted(tile["meshes"].items())):
                    selector = {"meshes": [mesh]}
                    over = ((mats or {}).get("overrides") or {}).get(mesh)
                    if over and any(over):  # textured MIs as overrides: the kit mesh itself is never edited
                        selector["templateDescriptor"] = {"descriptor": {"overrideMaterials": [m or None for m in over]}}
                    spawn = epic("PCGToolset.PCGToolset", "AddNode", {
                        "graph": graph, "nativeNodeType": "Static Mesh Spawner", "nodeName": f"Spawn{k}",
                        "nodeTitle": mesh.rsplit(".", 1)[1], "xPositionIdx": 400, "yPositionIdx": k * 160,
                        "jsonParams": json.dumps({"meshSelector": selector})})["returnValue"]
                    for c in range(0, len(rows), BAKE_CHUNK):
                        pts = [{"transform": {"location": {"x": r[0], "y": r[1], "z": r[2]},
                                              "rotation": {"pitch": r[7] if len(r) > 7 else 0, "yaw": r[3], "roll": 0},
                                              "scale": {"x": r[4], "y": r[5], "z": r[6]}}} for r in rows[c:c + BAKE_CHUNK]]
                        made = epic("PCGToolset.PCGToolset", "AddNode", {
                            "graph": graph, "nativeNodeType": "Create Points", "nodeName": f"Lots{k}_{c // BAKE_CHUNK}",
                            "xPositionIdx": 0, "yPositionIdx": k * 160 + c // BAKE_CHUNK * 40,
                            "jsonParams": json.dumps({"pointsToCreate": pts, "coordinateSpace": "World",
                                                      "bCullPointsOutsideVolume": False})})["returnValue"]
                        epic("PCGToolset.PCGToolset", "ConnectNodePins", {"fromNode": made, "fromPinLabel": "Out",
                                                                        "toNode": spawn, "toPinLabel": "In"})
                        nodes += 1
                    nodes += 1
                epic("PCGToolset.PCGToolset", "SetGraphDescription", {
                    "graph": graph, "description": f"City Builder world {name}, tile {key}: the previewed lots in this "
                                                   f"400 m square on our own kit. Rebuilt by road_network_pcg_build."})
                half = kit_bake.TILE_CM / 2 + 5000.0
                volume = epic("PCGToolset.PCGToolset", "SpawnGraphInstance", {
                    "graph": graph, "name": f"City Builder {name} {key}",
                    "transform": {"location": {"x": tile["centre"][0], "y": tile["centre"][1], "z": 0},
                                  "rotation": {"pitch": 0, "yaw": 0, "roll": 0},
                                  "scale": {"x": half / 100, "y": half / 100, "z": 200}}})["returnValue"]
                # never regenerate on level load: the instances are saved with the level, rebuild on demand only
                for prop in ("PCGComponent.GenerationTrigger", "pCGComponent.generationTrigger"):
                    try:
                        epic("EditorToolset.ObjectToolset", "SetProperties",
                             {"instance": volume["actor"], "values": json.dumps({prop: "GenerateOnDemand"})})
                        break
                    except ValueError:
                        continue
                messages += epic("PCGToolset.PCGToolset", "ExecuteGraphInstance", {"pCGVolume": volume["actor"]}).get("returnValue", [])
                state["tiles"][key] = {"hash": tile["hash"], "graph_path": graph_path,
                                       "volume": volume["actor"]["refPath"],
                                       "pieces": sum(len(v) for v in tile["meshes"].values())}
                data.put(f"net.{nid}.pcg", state)  # after every tile: a stop keeps what was built
                done.append(key)
        except (ConnectionError, RuntimeError, TimeoutError, ValueError) as exc:
            return {"ok": False, "error": f"Stopped after {len(done)} of {len(build)} tiles: {exc}",
                    "built": done, "note": "Build again: finished tiles are kept and skipped.",
                    "memory": uefn_memory.report(5)}
        return {"ok": True, "id": nid, "built_tiles": done, "removed_tiles": remove, "unchanged_tiles": len(same),
                "legacy_removed": legacy_removed, "nodes": nodes, "meshes": len(groups), "built": baked,
                "no_kit_piece": missing, "messages": messages[:20], "memory": uefn_memory.report(5)["worst"],
                "own_meshes": {"count": len(own), "materials_fixed": own_report["fixed"],
                               "materials_locked": own_report["locked"]},
                "note": "One volume per 400 m tile, Generate On Demand (the level never rebuilds them on load). Edit "
                        "the plan and build again: only changed tiles rebuild. Foliage layers stay for painting; "
                        "layers in no_kit_piece have no mesh in the kit yet."}

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
        heightmap, flat under each city), several cities each with its own roads actor, districts
        (downtown / normal / housing rings) and style ('mixed' grid downtown with wobbly suburbs, 'grid',
        'organic'), and highways joining them. cities: a count ('3'), a JSON list
        [{name, x, y, radius_m, style}] in cm, or empty to rebuild the cities already in this world.
        ground_from: an existing network whose ground scan the roads follow instead (no heightmap).
        options: JSON, any of: terrain hill_km, roughness (0.2-0.8), detail (octaves 1-8), ridges (0-1),
        flatten_m, sea_m (lake level, -1000 = off), terrace_m; roads block_m, wobble (0-0.45),
        prune (0-0.5), downtown / normal (ring shares), highway ('4L' or '2L'), highway_curve. Left-out
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
    def road_network_city_save(net_id: str = "", city: str = "", name: str = "", style: str = "",
                               radius_m: float = 0.0, buildings: str = "") -> dict:
        """Change one city of a generated world: rename it, its road style (mixed / grid / organic), its
        radius, or its own building sets (JSON {"downtown": [mesh paths], "normal": [...], "housing": [...]}).
        Generate again with cities empty to rebuild its roads."""
        return safe(save_city)(net_id, city, name, style, radius_m, buildings)

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
    api.register_panel_rpc("city_save", lambda net_id="", city="", name="", style="", radius_m=0.0, buildings="":
                           safe(save_city)(net_id, city, name, style, radius_m, buildings))
    api.register_panel_rpc("heightmap", lambda net_id="": safe(heightmap)(net_id))
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
            f"1. Roads, paths and private access are landscape splines: road_network_import_all(net_id='{nid}') lists the "
            f"actors that changed ({', '.join(actors)}); call it again with confirm=true (junction meshes 8 per call, "
            f"then a paste per actor). Keep the person's hands off UEFN while it pastes.",
            f"2. The city is PCG: road_network_pcg_build(net_id='{nid}') says how many 400 m tiles changed; call it with "
            f"confirm=true (one graph + volume per tile, Generate On Demand).",
            "3. After each step read road_network_memory: if a call grew UEFN 6+ GB or the guard tripped, stop and "
            "report it instead of pushing on. Never restart or close UEFN without asking.",
            "4. Never touch the cinema (keep zones) or write the existing terrain. Report what was built and what was "
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

    api.register_panel_rpc("version", lambda: plugin_version())
    api.register_panel_rpc("chats", lambda: safe(list_chats)())
    api.register_panel_rpc("send_note", lambda net_id="", chat_id="", text="", kind="pin", points="":
                           safe(send_note)(net_id, chat_id, text, kind, points))

    api.register_panel_rpc("sync", lambda net_id="": safe(sync_status)(net_id))

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
        cities | downtown | normal | housing | industrial | everywhere), meshes [{path, weight}] (Content Browser
        paths: a layer WITH meshes places exactly those, weighted; without, the kit piece for its role), density
        (per 100 m2), cover (forest share), reach (city radii), spacing_m, setback_m, scale_min, scale_max,
        sink_cm, footprint, road_clear_m, avoid_roads, avoid [earlier layer names], self_prune, upright,
        random_yaw, collision, seed, order, enabled}. Ranges: road_network_pcg_layers `fields`. Preview with
        road_network_pcg_preview, then Push city (road_network_pcg_build) builds only the changed tiles."""
        return safe(pcg_layer_save)(net_id, layer, layer_id)

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_layer_delete(net_id: str = "", layer_id: str = "") -> dict:
        """Remove one PCG layer from a world (UEFN is not touched)."""
        return safe(pcg_layer_delete)(net_id, layer_id)

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_preset(net_id: str = "", preset: str = "full_city", reorder: bool = False) -> dict:
        """Add every layer a city needs in one call: 'full_city' (16 building types, traffic lights / stop and
        street signs on junction corners, lamps, street trees, bus stops, benches, bins, hydrants, billboards,
        power poles, and trees / rocks / bushes as Foliage-mode layers, no grass), 'city', 'buildings', 'street'
        or 'nature'. Existing layers keep their settings; reorder=true only moves them into planning order
        (big civic sites first, dense fill after)."""
        return safe(pcg_preset)(net_id, preset, reorder)

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_plan(net_id: str = "", road_tag: str = "") -> dict:
        """The PCG graph that places a world's layers in UEFN: native nodes in data-flow order with their
        settings, the wires, the graph params, the road tag and the volume. Build it with Epic PCGToolset
        (skill pcg/graph_layers)."""
        return safe(pcg_plan)(net_id, road_tag)

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_assets(net_id: str = "", asset_set: str = "") -> dict:
        """Which pieces City Builder uses for a world's layers: 'own' (the project's own kit: WorldGen/City,
        WorldGen/Kit, BuildingKit storeys), 'fortnite' (Fortnite's props and buildings) or 'mixed'. Empty
        asset_set reads it, with where each role (traffic light, skyscraper, bench...) comes from."""
        return safe(pcg_assets)(net_id, asset_set)

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_preview(net_id: str = "", layer_id: str = "", summary: bool = False) -> dict:
        """Run a world's PCG layers offline with the graph's rules (district, off the roads, clear of earlier
        layers, no overlaps, nothing under water) and return where each would place things plus its count;
        the panel draws it in 3D. layer_id: just that layer. summary=true: only the count per layer."""
        return safe(pcg_preview)(net_id, layer_id, summary)

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_build(net_id: str = "", confirm: bool = False, force: bool = False) -> dict:
        """Build the world's previewed city in the open UEFN level as PCG of the project's own kit, in 400 m
        tiles (one graph + volume each). Incremental: only tiles whose lots changed since the last build are
        rebuilt, tiles gone from the plan are removed, the rest of the city, the roads and the level are left
        alone, so editing the city plan never means deleting everything. Without confirm it reports how many
        tiles would change. force=true rebuilds every tile. Ask the person first."""
        return safe(pcg_build)(net_id, confirm, force)

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_place(net_id: str = "", confirm: bool = False) -> dict:
        """One click: start an AI chat that builds the world's PCG layers in the open UEFN level from
        road_network_pcg_plan. Ask the person first."""
        return safe(pcg_place)(net_id, confirm)

    api.register_panel_rpc("pcg_layers", lambda net_id="": safe(pcg_layers)(net_id))
    api.register_panel_rpc("pcg_layer_save", lambda net_id="", layer="", layer_id="": safe(pcg_layer_save)(net_id, layer, layer_id))
    api.register_panel_rpc("pcg_layer_delete", lambda net_id="", layer_id="": safe(pcg_layer_delete)(net_id, layer_id))
    api.register_panel_rpc("pcg_preset", lambda net_id="", preset="full_city", reorder=False:
                           safe(pcg_preset)(net_id, preset, reorder))
    api.register_panel_rpc("pcg_plan", lambda net_id="", road_tag="": safe(pcg_plan)(net_id, road_tag))
    api.register_panel_rpc("pcg_assets", lambda net_id="", asset_set="": safe(pcg_assets)(net_id, asset_set))
    api.register_panel_rpc("pcg_preview", lambda net_id="", layer_id="": safe(pcg_preview)(net_id, layer_id))
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

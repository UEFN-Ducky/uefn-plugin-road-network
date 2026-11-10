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

import json
import math
import os
import re
import subprocess
import time

from . import citygen as cg
from . import pcg_layers as pl
from . import splines_text as st
from . import uefn_input

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
        return api.listener(command, params or {}, timeout=timeout)

    def epic(toolset: str, tool: str, arguments: dict):
        # Same route as Ducky's own Epic callers: the pool keeps the nested arguments intact
        # (api.call_tool's dispatcher drops them for unreal__call_tool).
        from backend.mcp_plugins.client_pool import get_plugin_pool
        pool = get_plugin_pool()
        out = pool.run_sync(pool.call_tool("unreal__call_tool", {"toolset_name": toolset, "tool_name": tool,
                                                                  "arguments": arguments}), timeout=120)
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

    def build_pieces(net_id: str = "", actor: str = "") -> dict:
        """Generate a Road1-width plain-asphalt piece for every junction the kit has no piece for."""
        nid, aslug = slug(net_id), slug(actor)
        head, pts, segs = load_actor(nid, aslug)
        if not head:
            return {"ok": False, "error": f"No actor {actor!r} in network {net_id!r}."}
        todo = st.pending_nodes(pts, segs)
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
        built = 0
        try:
            for k in range(0, len(todo), 40):
                batch = todo[k:k + 40]
                out = listener("road_network_build_junctions", {"folder": folder, "pieces": [
                    {"name": f"{prefix}{node}", "arms": angles, "half": half(arms)}
                    for node, arms, angles in batch]}, timeout=600)
                for node, arms, _ in batch:
                    dists = (out.get("mouths") or {}).get(f"{prefix}{node}")
                    if dists:
                        st.place_generated(pts, segs, node, arms, dists, f"{folder}/{prefix}{node}")
                        built += 1
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
            res = uefn_input.paste_in_splines_tool()
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
        png, settings = cg.heightmap_png(ter)
        path = heightmap_path(nid)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "wb") as fh:
            fh.write(png)
        os.replace(path + ".tmp", path)
        return {"path": path, **settings}

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
        # options left out keep this world's last values
        opts = cg.options({**((data.get(f"net.{nid}") or {}).get("generated") or {}).get("options", {}), **given})
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
        else:
            towns = cg.place_cities(int(seed), max(1, min(12, int(text or 3))), bounds)
        if not towns:
            return {"ok": False, "error": "No room for that many cities: make the map bigger or ask for fewer."}
        ter = base or cg.terrain(int(seed), size, max(0.0, float(relief_m)) * 100, towns, center=(center_x, center_y),
                                 opts=opts)
        water = ter.get("sea")
        # roads never dive into a lake: across water they stay 1.5 m above it (a causeway)
        road_ground = ((lambda x, y: max(st.sample_grid(ter, x, y) or 0.0, water + 150)) if water is not None
                       else (lambda x, y: st.sample_grid(ter, x, y) or 0.0))
        actors = cg.generate(towns, road_ground, folder, int(seed), opts)
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
        for t in towns:
            cslug = slug(t["name"])
            prev = old.get(cslug) or {}
            api.search.upsert(f"net.{nid}.city.{cslug}", t["name"], f"City {t['name']} in world {name}: {t['style']} roads")
            data.put(f"net.{nid}.city.{cslug}", {**t, "name": t["name"], "roads": f"{t['name']} Roads",
                                                 "districts": cg.districts(t, opts),
                                                 "buildings": prev.get("buildings") or {"downtown": [], "normal": [], "housing": []}})
        doc = data.get(f"net.{nid}")
        doc["generated"] = {"seed": int(seed), "size_km": size / 100000, "relief_m": float(relief_m),
                            "ground_from": slug(ground_from), "folder": folder, "options": opts,
                            "cities": len(towns), "center": [center_x, center_y]}
        doc.pop("heightmap", None)  # written on demand (road_network_heightmap): keeps live preview fast
        doc["actors"] = sorted(actors)
        doc["updated"] = time.time()
        data.put(f"net.{nid}", doc)
        api.search.upsert(f"net.{nid}", name, f"Generated world {name}: cities " + ", ".join(t["name"] for t in towns))
        record("road_network_generate", "network", nid, before,
               [] if before else [{"command": "road_network_delete", "params": {"net_id": nid}}])
        push({"what": "network", "id": nid})
        return {"ok": True, "id": nid, "cities": [{"name": t["name"], "style": t["style"], "x": round(t["x"]),
                                                    "y": round(t["y"]), "radius_m": round(t["radius"] / 100)} for t in towns],
                "actors": stats_out,
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
                       f"Location {hm['location']}, Scale {hm['scale']}. It makes a new landscape; your current one is not touched."}

    def import_all(net_id: str = "", confirm: bool = False) -> dict:
        """One click: build the junction pieces of every road actor, then paste each into UEFN."""
        nid = slug(net_id)
        doc = data.get(f"net.{nid}") if nid else None
        if not doc:
            return {"ok": False, "error": f"No road network {net_id!r}."}
        labels = [actor_docs(nid)[a]["label"] for a in actor_docs(nid)]
        if not confirm:
            return {"ok": False, "error": f"This pastes {len(labels)} road actors into the open UEFN level "
                                          f"({', '.join(labels)}). Call again with confirm=true."}
        done = {}
        for label in labels:
            pieces = build_pieces(nid, label)
            if not pieces.get("ok"):
                return {"ok": False, "error": f"{label}: {pieces.get('error')}", "done": done}
            out = reimport(nid, label, True)
            if not out.get("ok"):
                return {"ok": False, "error": f"{label}: {out.get('error')}", "done": done}
            done[label] = {"junction_pieces": pieces.get("built", 0), "pieces": out.get("pieces")}
        return {"ok": True, "id": nid, "done": done}

    # ---------------------------------------------------------------- PCG placement layers
    def layer_docs(nid: str) -> dict:
        return {k.rsplit(".", 1)[1]: v for k, v in data.items(f"net.{nid}.pcg.").items() if k.count(".") == 3}

    def pcg_layers(net_id: str = "") -> dict:
        nid = slug(net_id)
        if not data.get(f"net.{nid}"):
            return {"ok": False, "error": f"No road network {net_id!r}."}
        items = sorted(({"id": k, **pl.clean(v)} for k, v in layer_docs(nid).items()), key=lambda l: (l["order"], l["name"]))
        return {"ok": True, "id": nid, "items": items, "count": len(items),
                "kinds": list(pl.KINDS), "where": list(pl.WHERE),
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
            if " before kit" in str(head.get("label") or ""):
                continue  # a saved backup, not a road in the level
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
        out = pl.preview(list(docs.values()), list(city_docs(nid).values()), terrain, pl.road_samples(actors),
                         lambda x, y: st.sample_grid(terrain, x, y) if terrain.get("heights") else 0.0,
                         limit=2500, juncs=pl.junctions(actors))
        if layer_id:
            name = (docs.get(slug(layer_id)) or {}).get("name")
            out = {name: out[name]} if name in out else {}
        if summary:  # counts only: a quick check for an agent, no point lists
            return {"ok": True, "id": nid, "counts": {k: v["count"] for k, v in out.items()}}
        return {"ok": True, "id": nid, "layers": out}

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
    def road_network_build_pieces(net_id: str = "", actor: str = "") -> dict:
        """Build Road1-width plain-asphalt junction meshes (GeometryScript, in the project's
        WorldGen/Roads/Junctions/Kit folder) for every junction the Fortnite kit has no piece for, and put
        them on the nodes. Creates mesh assets only; never touches the landscape."""
        return safe(build_pieces)(net_id, actor)

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
        """Every generate option with its default and range (what the Terrain and Roads tabs show)."""
        return {"ok": True, "options": {k: {"default": d, "min": lo, "max": hi} for k, (d, lo, hi) in cg.OPTIONS.items()},
                "highway": list(cg.HIGHWAYS), "styles": list(cg.STYLES)}

    @api.tool(intent=GEN_INTENT, listener=False)
    def road_network_cities(net_id: str = "") -> dict:
        """The cities of a generated world: centre, radius, road style, district rings and building sets."""
        return safe(list_cities)(net_id)

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
    def road_network_import_all(net_id: str = "", confirm: bool = False) -> dict:
        """One click into UEFN: build every road actor's junction pieces, then paste each actor as live
        splines (reimport). Ask the person first and keep their hands off UEFN while it runs."""
        return safe(import_all)(net_id, confirm)

    api.register_panel_rpc("generate", lambda name="", seed=7, cities="", size_km=6.0, relief_m=60.0, ground_from="",
                           folder="", center_x=0.0, center_y=0.0, options="": safe(generate)(
                               name, seed, cities, size_km, relief_m, ground_from, folder, center_x, center_y, options))
    api.register_panel_rpc("generate_options", lambda: road_network_generate_options())
    api.register_panel_rpc("cities", lambda net_id="": safe(list_cities)(net_id))
    api.register_panel_rpc("city_save", lambda net_id="", city="", name="", style="", radius_m=0.0, buildings="":
                           safe(save_city)(net_id, city, name, style, radius_m, buildings))
    api.register_panel_rpc("heightmap", lambda net_id="": safe(heightmap)(net_id))
    api.register_panel_rpc("import_all", lambda net_id="", confirm=False: safe(import_all)(net_id, confirm))

    PCG_INTENT = r"\b(pcg|procedural|scatter|placement layers?|foliage|trees|rocks|street (trees|lamps))\b"

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_layers(net_id: str = "") -> dict:
        """A world's PCG placement layers (trees, rocks, grass, street trees, lamps, houses...): what each
        places, where (outside cities, a district ring, everywhere), how dense, and what it keeps clear of."""
        return safe(pcg_layers)(net_id)

    @api.tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_layer_save(net_id: str = "", layer: str = "", layer_id: str = "") -> dict:
        """Create or change one PCG layer. layer: JSON {name, kind (scatter | along_roads | buildings), where
        (outside | cities | downtown | normal | housing | everywhere), meshes [{path, weight}], density (per 100 m2),
        spacing_m, setback_m, scale_min, scale_max, sink_cm, footprint (bounds scale for pruning), road_clear_m,
        avoid_roads, avoid [layer names placed earlier], self_prune, upright, random_yaw, collision, seed, order, enabled}."""
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
    api.register_panel_rpc("build_pieces", lambda net_id="", actor="": safe(build_pieces)(net_id, actor))
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

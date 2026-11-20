"""The ground as UEFN has it, and one road actor at a time: scan, geometry, drape, check, repair, tune, kit convert.

Handlers moved out of backend/__init__.py register(): ctx carries what the parts share."""

from __future__ import annotations

import subprocess
import time

from . import citygen as cg
from . import ramp_joins
from . import road_profiles as rp
from . import splines_text as st
from . import CHUNK


def register(ctx) -> None:
    (actor, actor_path, api, data, drop_world_files, listener, push, put_json_file, read_json_file,
     record, slug) = ctx.take(
        "actor actor_path api data drop_world_files listener push put_json_file read_json_file record slug")

    # ---------------------------------------------------------------- the ground, as UEFN has it
    SCAN_GRID = 1024  # ~7 m a sample on a 7 km map: the landscape as the person shaped it in UEFN
    SCAN_BAND = 40000  # traces per editor call: a fine scan comes in bands so UEFN never stalls on one frame

    def scan_into(nid: str, name: str, grid: int = SCAN_GRID) -> dict:
        """Read the landscape in the open UEFN level (heightfield only: roads, props, trees and water are skipped)
        into net.<nid>.scan. A fine grid is stored as a world file, the doc keeps only its bounds."""
        grid = max(16, min(int(grid or SCAN_GRID), 2048))
        step = max(1, SCAN_BAND // grid)
        first = listener("road_network_scan", {"grid": grid, "j0": 0, "j1": step}, timeout=600)
        ter = dict(first.get("terrain") or {})
        heights = list(ter.get("heights") or [])
        for j0 in range(step, grid, step):
            band = listener("road_network_scan", {"grid": grid, "j0": j0, "j1": j0 + step}, timeout=600)
            heights += (band.get("terrain") or {}).get("heights") or []
        if ter.get("min") is None or len(heights) != grid:
            raise RuntimeError(f"the landscape scan came back with {len(heights)} of {grid} rows")
        ter["heights"] = heights
        doc = {"name": f"{name} / terrain and props", "props": first.get("props", []), "source": "uefn",
               "scanned": time.time()}
        if grid > 256:  # (a 1024 grid is ~7 MB: a world file, never a data doc)
            path = f"world/{nid}/terrain.json"
            sha = put_json_file(path, {"heights": heights})
            doc["terrain"] = {**{k: v for k, v in ter.items() if k != "heights"}, "file": path, "sha": sha}
        else:
            doc["terrain"] = ter
        data.put(f"net.{nid}.scan", doc)
        return ter

    def scan_terrain(nid: str) -> dict:
        """The world's ground grid {min, max, grid, heights}, wherever it is stored (doc or world file)."""
        ter = (data.get(f"net.{nid}.scan") or {}).get("terrain") or {}
        if ter.get("file") and not ter.get("heights"):
            got = read_json_file(ter["file"], cache=True)
            ter = {**ter, "heights": got.get("heights") or []}
            if got.get("maps"):  # a generated world's engine maps live in the file with its heights
                ter["maps"] = got["maps"]
        if ter and ter.get("sea") is None:
            # a ground pulled from UEFN is land only: the world's water level stays the one its water was laid at
            # (without it the panel drew no water and the forest grew on the sea bed)
            sea = (data.get(f"net.{nid}.water") or {}).get("sea")
            if sea is not None:
                ter = {**ter, "sea": sea}
        return ter

    def panel_terrain(nid: str, most: int = 512) -> dict:
        """The ground for the 3D view: a fine scan thinned to at most `most` cells a side (display only)."""
        ter = scan_terrain(nid)
        n = int(ter.get("grid") or 0)
        if n <= most or not ter.get("heights"):
            return cg.panel_maps(ter)
        s = -(-n // most)
        while n % s:
            s += 1
        rows = [r[::s] for r in ter["heights"][::s]]
        return cg.panel_maps({**{k: v for k, v in ter.items() if k != "maps"}, "grid": len(rows), "heights": rows})

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
            scan_into(nid, doc["name"])
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
            return {"ok": True, "id": nid, "actors": out, "terrain": panel_terrain(nid) if terrain else {},
                    "props": scan.get("props", [])}
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
        return {"ok": True, "id": nid, "actors": out, "terrain": panel_terrain(nid) if terrain else {},
                "props": scan.get("props", []),
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
        fw = st.keeps_heights(head.get("label"))  # (they keep their height bends)
        return head, st.serialize(*st.straighten(pts, segs, keep_z=fw), terrain=terrain)  # long roads straight, not a point per bump

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
        terrain = scan_terrain(nid) or {}
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

    ctx.share(locals())

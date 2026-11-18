"""Junction pieces (the build loop), restore, circles, ground scans, reimport and pasting actors into UEFN; heightmaps.

Handlers moved out of backend/__init__.py register(): ctx carries what the parts share."""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import time

from . import citygen as cg
from . import editor_heal
from . import pcg_layers as pl
from . import splines_text as st
from . import uefn_input
from . import uefn_memory


def register(ctx) -> None:
    (SCAN_GRID, actor, actor_docs, api, data, epic, junction_folder, listener, load_actor, push, record,
     repair_actor, roads_digest, scan_into, scan_terrain, slug, store_actor) = ctx.take(
        "SCAN_GRID actor actor_docs api data epic junction_folder listener load_actor push record "
        "repair_actor roads_digest scan_into scan_terrain slug store_actor")

    KERB_ONLY_CM = 30.0  # a private street's kerb (SM_Path_PrivateStreet: asphalt to 3.4 m, kerb to 3.7 m)
    JUNCTIONS_PER_CALL = 8
    SAVE_BATCH = 40  # assets per save call
    PROP_BATCH = 20  # prop trees placed per editor call (one call is one frame: a long loop of spawns freezes UEFN)

    def build_pieces(net_id: str = "", actor: str = "", rebuild: bool = False) -> dict:
        """Generate a plain-asphalt piece for every junction the kit has no piece for (rebuild: also
        regenerate the pieces already made, in place)."""
        nid, aslug = slug(net_id), slug(actor)
        head, pts, segs = load_actor(nid, aslug)
        if not head:
            return {"ok": False, "error": f"No actor {actor!r} in network {net_id!r}."}
        gen = bool((data.get(f"net.{nid}") or {}).get("generated"))
        # a piece only slides its mouths along their own roads, a turnaround and a path only settle onto the live
        # ground: a heightmap cut for these roads stays theirs (read before anything here moves a point)
        net = data.get(f"net.{nid}") or {}
        hm_current = bool((net.get("generated") or {}).get("heightmap_roads")) and \
            net["generated"]["heightmap_roads"] == roads_digest(nid)

        def keep_heightmap() -> None:
            if hm_current:
                doc = data.get(f"net.{nid}")
                doc["generated"]["heightmap_roads"] = roads_digest(nid)
                data.put(f"net.{nid}", doc)
        capped = 0
        if gen:
            # roads drawn 10 cm up their points (worlds generated with 50 floated every road and kerb)
            for p in pts:
                if not p["mesh"] and any(ln.startswith("SegmentMeshOffset=") for ln in p["lines"]):
                    p["lines"] = st._set_line(p["lines"], "SegmentMeshOffset", f"{cg.ROAD_OFFSET_CM:.1f}")
            # every street that ends gets a turnaround piece on its own spline (no PCG disc)
            others = [load_actor(nid, a)[1:] for a, h in actor_docs(nid).items() if a != aslug and not (
                " before kit" in str(h.get("label") or "") or str(h.get("label") or "").endswith((" Paths", " Access")))]
            terrain = scan_terrain(nid) or {}
            capped = st.cap_ends(pts, segs, pl.dead_ends([(pts, segs)] + others, terrain)[0])
            # every driveway, walkway and private street lies on the live ground, point by point (their points were
            # set from the plan's lot pads and stood up to metres over the grass)
            loose = st.path_points(pts, segs)
            if loose:
                try:
                    got = listener("road_network_ground_check", {"points": [[p["x"], p["y"], p["z"]] for p in loose]},
                                   timeout=600).get("points") or []
                except (ConnectionError, RuntimeError, TimeoutError) as exc:
                    return {"ok": False, "error": f"UEFN didn't answer the ground check: {exc}"}
                st.drape_to(pts, segs, {p["i"]: r.get("ground") for p, r in zip(loose, got)})
        todo = st.pending_nodes(pts, segs, rebuild)
        if not todo:
            if gen:
                store_actor(nid, head["label"], {"points": pts, "segments": segs}, head["name"].split(" / ")[0])
                keep_heightmap()
            return {"ok": True, "label": head["label"], "built": 0,
                    "note": "No junction needs a generated piece."}
        folder = junction_folder(pts, segs)
        if not folder:
            return {"ok": False, "error": "Couldn't find the project's road folder from the road pieces."}
        by_i = {p["i"]: p for p in pts}
        half = (lambda arms: max(by_i[m]["w"] for m in arms)) if gen else (lambda arms: 545.0)  # kit roads: Road1 width
        road_of: dict = {}  # mouth point -> the road mesh on it (asset path)
        for s in segs:
            hit = s["mesh"] and re.search(r"StaticMesh'([^'.]+)", " ".join(s["lines"]))
            if hit:
                road_of.setdefault(s["a"], hit.group(1))
                road_of.setdefault(s["b"], hit.group(1))
        # a private street has no sidewalk but a kerb: its junctions and turnarounds get the kerb round them too
        walk = lambda m: cg.walk_of(by_i[m]["w"]) or (KERB_ONLY_CM if "PrivateStreet" in road_of.get(m, "") else 0.0)  # noqa: E731
        # a dead end's turnaround: its outer radius (road_io._junction_geometry builds it to this)
        bulb = lambda arms: max(1000.0, by_i[arms[0]]["w"] + 500.0) if len(arms) == 1 else 0.0  # noqa: E731
        # each turnaround sits on the live ground like the road points do: the ground under its round, as a plane
        ground_of: dict = {}
        ends = [(node, arms) for node, arms, _ in todo if len(arms) == 1]
        if ends:
            ring = [(0.0, 0.0)] + [(f * math.cos(k * math.pi / 4), f * math.sin(k * math.pi / 4))
                                   for f in (0.5, 0.95) for k in range(8)]
            try:
                got = listener("road_network_ground_check", {"points": [
                    [by_i[n]["x"] + u * bulb(arms), by_i[n]["y"] + v * bulb(arms), by_i[n]["z"]]
                    for n, arms in ends for u, v in ring]}, timeout=300).get("points") or []
            except (ConnectionError, RuntimeError, TimeoutError) as exc:
                return {"ok": False, "error": f"UEFN didn't answer the ground check: {exc}"}
            for k, (n, _arms) in enumerate(ends):
                rows = got[k * len(ring):(k + 1) * len(ring)]
                plane = st.fit_plane([(r["x"] - by_i[n]["x"], r["y"] - by_i[n]["y"], r["ground"])
                                      for r in rows if r.get("ground") is not None])
                if plane:
                    ground_of[n] = plane
        # Junctions of the same shape share ONE mesh, turned by its yaw: a mesh per junction was thousands of
        # assets, each its own draw call and instanced-mesh component (PCG can't batch different meshes).
        paint = not aslug.endswith("airport")
        built, unsaved = 0, []
        shapes: dict = {}  # mesh name -> the piece to build
        placing = []  # (node, arms in the piece's order, mesh name, yaw, rel angles)
        for node, arms, angles in todo:
            hv = [by_i[m]["w"] for m in arms] if gen else None
            wk = [walk(m) for m in arms] if gen else None
            order, rel, rot = st.junction_shape(angles, hv, wk)
            piece = {"arms": rel, "half": half(arms),
                     # each mouth as wide as its own road; runways and taxiways get no crossings painted
                     "halves": [hv[k] for k in order] if gen else None,
                     # a street's sidewalks carry on round the junction, in the junction's own mesh
                     "walks": [wk[k] for k in order] if gen else None,
                     # the junction takes the asphalt, paint and sidewalk materials of its widest road's mesh,
                     # so it is the same colour as the road it joins (house streets and city streets differ)
                     "like": road_of.get(max(arms, key=lambda m: by_i[m]["w"]), ""),
                     "bulb": bulb(arms), "paint": paint}
            name = "SM_JK_S" + hashlib.sha1(json.dumps(piece, sort_keys=True).encode()).hexdigest()[:12]
            shapes[name] = {"name": name, **piece}
            placing.append((node, [arms[k] for k in order], name, rot, rel))
        mouths: dict = {}
        try:
            # few meshes per call: each mesh build opens a progress window that UEFN frees only when the
            # call's frame ends (see street_kit_build)
            todo_shapes = list(shapes.values())
            for k in range(0, len(todo_shapes), JUNCTIONS_PER_CALL):
                batch = todo_shapes[k:k + JUNCTIONS_PER_CALL]
                out = listener("road_network_build_junctions", {"folder": folder, "pieces": batch}, timeout=600)
                mouths.update(out.get("mouths") or {})
                unsaved += [f"{folder}/{p['name']}" for p in batch if p["name"] in mouths]
                # saved as we go, a batch per call: left unsaved, hundreds of meshes all went out in the
                # person's next level save (one frame, a checkout per package)
                if len(unsaved) >= SAVE_BATCH:
                    listener("road_network_save_assets", {"paths": unsaved}, timeout=300)
                    unsaved = []
            for node, arms, name, rot, rel in placing:
                dists = mouths.get(name)
                if dists:
                    st.place_generated(pts, segs, node, arms, dists, f"{folder}/{name}", yaw=rot, rel=rel)
                    if len(arms) == 1:  # a turnaround's Width is its whole round: the ground is graded to all of it
                        p = by_i[node]
                        p["w"] = bulb(arms) + 50.0
                        p["lines"] = st._set_line(p["lines"], "Width", f"{p['w']:.1f}")
                        if node in ground_of:
                            # on the ground itself, as the road points are (measured ~2 cm over it)
                            st.lay_on_ground(pts, segs, node, ground_of[node], 0.0)
                    built += 1
            if unsaved:
                listener("road_network_save_assets", {"paths": unsaved}, timeout=300)
        except (ConnectionError, RuntimeError, TimeoutError) as exc:
            return {"ok": False, "error": f"UEFN stopped after {built} pieces: {exc}"}
        finally:
            store_actor(nid, head["label"], {"points": pts, "segments": segs}, head["name"].split(" / ")[0])
            push({"what": "actor", "id": nid, "label": head["label"]})
            keep_heightmap()
        record("road_network_build_pieces", "network", f"{nid}-{aslug}", None, [])
        return {"ok": True, "label": head["label"], "built": built, "folder": folder, "turnarounds": capped,
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

    def scan_ground(net_id: str = "", grid: int = 0) -> dict:
        """Pull the landscape as it is in the open UEFN level (read-only traces, heightfield only) into the
        world: the preview, ground fit, lots and the PCG push all stand on it from then on. Never writes the terrain."""
        nid = slug(net_id)
        doc = data.get(f"net.{nid}") if nid else None
        if not doc:
            return {"ok": False, "error": f"No road network {net_id!r}."}
        try:
            terrain = scan_into(nid, doc["name"], int(grid or SCAN_GRID))
        except (ConnectionError, RuntimeError, TimeoutError) as exc:
            return {"ok": False, "error": f"UEFN didn't answer: {exc}"}
        cells = [v for row in terrain.get("heights", []) for v in row if v is not None]
        push({"what": "network", "id": nid})
        (x0, y0), (x1, y1) = terrain["min"], terrain["max"]
        return {"ok": True, "id": nid, "grid": terrain.get("grid"), "cells": len(cells), "holes": terrain["grid"] ** 2 - len(cells),
                "cell_m": round((x1 - x0) / terrain["grid"] / 100, 2),
                "min_z": round(min(cells)) if cells else None, "max_z": round(max(cells)) if cells else None,
                "note": "The landscape as it is in UEFN now (heightfield only; roads, props, trees and water skipped). "
                        "Never push a heightmap over it: that would undo the edits made in UEFN."}

    def reimport(net_id: str = "", actor: str = "", confirm: bool = False) -> dict:
        """Replace the actor in UEFN with this network's version (live splines, terrain untouched)."""
        nid, aslug = slug(net_id), slug(actor)
        net = data.get(f"net.{nid}") if nid else None
        head, pts, segs = load_actor(nid, aslug) if net else (None, [], [])
        if not head:
            return {"ok": False, "error": "Pick a network and one of its road actors."}
        gen = net.get("generated") or {}
        if gen.get("heightmap") and not gen.get("ground_from") and gen.get("heightmap_roads") != roads_digest(nid):
            return {"ok": False, "error": "The roads changed after the heightmap was made, so they would not sit in their "
                                          "cuts. Run road_network_heightmap, import that terrain, then push the roads."}
        if not confirm:
            return {"ok": False, "error": f"This replaces '{head['label']}' in the open UEFN level. Call again with confirm=true."}
        # Never writes the terrain: the splines keep their own Raise/Lower flags and the
        # landscape's reserved Splines edit layer shapes the ground, exactly like a hand-made spline.
        label = head["label"]
        found = st.problems(pts, segs)
        repaired = None
        if any(found.values()):  # broken data: repair it (as road_network_repair does), then check again
            repaired = repair_actor(nid, aslug)
            head, pts, segs = load_actor(nid, aslug)
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
                **({"repaired": {k: v for k, v in repaired.items() if k != "ok"}} if repaired else {}),
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
            time.sleep(1.0)  # the Landscape panel opens a few frames after the call returns (a miss: the retry below)
        except (ConnectionError, RuntimeError, TimeoutError, ValueError) as exc:
            return {"ok": False, "error": f"UEFN step failed: {exc}"}
        expected = len([s for s in segs if s["mesh"]])
        known = {row["path"] for row in listener("road_network_actors").get("actors", [])}
        pasted = None
        for attempt in range(2):
            res = uefn_memory.metered(f"paste {label}", uefn_input.paste_in_splines_tool)
            if not res.get("ok"):
                if attempt == 0:  # a popup over UEFN or the Splines tool left: clear it, back to Landscape, one more go
                    editor_heal.heal("", api.call_tool)
                    try:
                        epic("EditorToolset.EditorAppToolset", "SetEditorMode", {"modeId": "EM_Landscape"})
                    except (ConnectionError, RuntimeError, TimeoutError, ValueError):
                        pass
                    time.sleep(3)
                    continue
                return {"ok": False, "error": res.get("error"), "note": "The text is on the clipboard: Manage > Splines, Ctrl+V."}
            # poll instead of one long sleep: UEFN pastes on its own thread, so the listener answers as soon as the
            # paste is done; the old fixed wait (up to 90 s) is now only the deadline
            end = time.time() + min(90, 5 + len(text) / 80000)
            time.sleep(1.0)
            while not pasted and time.time() < end:
                try:
                    rows = listener("road_network_actors", timeout=max(5.0, end - time.time())).get("actors", [])
                except (ConnectionError, RuntimeError, TimeoutError, ValueError):
                    rows = []
                pasted = next((r["path"] for r in rows
                               if r["path"] not in known and r["spline_pieces"] >= expected * 0.5), None)
                if not pasted:
                    time.sleep(2.0)
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

    ctx.share(locals())

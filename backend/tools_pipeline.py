"""The pipeline, sync status, roads as PCG, the tiled PCG build and pcg_place.

Handlers moved out of backend/__init__.py register(): ctx carries what the parts share."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time

from . import citygen as cg
from . import editor_heal
from . import kit_bake
from . import pcg_batch
from . import pcg_layers as pl
from . import pipeline
from . import splines_text as st
from . import uefn_memory


def register(ctx) -> None:
    (PROP_BATCH, SAVE_BATCH, _GROUND, _PREVIEW_CACHE, actor, actor_docs, api, city_docs, data, epic,
     fit_summary, generate, interiors_of, kit_materials, listener, listener_long, load_actor,
     one_run_at_a_time, paint_ground, pcg_plan, pcg_preview, plugin_version, private_plans, push,
     road_folder, scan_terrain, slug, store_actor, tree_props) = ctx.take(
        "PROP_BATCH SAVE_BATCH _GROUND _PREVIEW_CACHE actor actor_docs api city_docs data epic fit_summary "
        "generate interiors_of kit_materials listener listener_long load_actor one_run_at_a_time paint_ground "
        "pcg_plan pcg_preview plugin_version private_plans push road_folder scan_terrain slug store_actor "
        "tree_props")

    # ---------------------------------------------------------------- the pipeline (backend/pipeline.py)
    def sidewalks_stage(nid: str) -> dict:
        """SidewalkGenerator: each city's '<City> Paths' spline actor (missing sidewalks + every walkway and
        driveway of the previewed lots), stored like a road actor so Push roads pastes it when it changed."""
        doc = data.get(f"net.{nid}") or {}
        opts = (doc.get("generated") or {}).get("options")
        ter = scan_terrain(nid) or {}
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
        joins = {}
        gone = []
        for t in towns:
            access = cg.access_actor(t, rows_by[t["name"]], ground, folder)
            paths = cg.paths_actor(t, opts, rows_by[t["name"]], ground, folder, cg.wet_from(ter))
            # ONE spline network per city: UEFN only connects points of one actor, so private streets, driveways,
            # sidewalks and walkways in actors of their own all ended on the grass beside the street they belong to.
            # Private streets get a real junction; every other loose end a mesh-less link onto the road it meets.
            rhead, rpts, rsegs = load_actor(nid, slug(f"{t['name']} Roads"))
            if not rhead:
                for label, actor in ((f"{t['name']} Paths", paths), (f"{t['name']} Access", access)):
                    if actor["segments"]:
                        out[label] = store_actor(nid, label, actor, doc.get("name") or nid)["stats"]
                continue
            roads, rest, n_pr = cg.join_private({"points": rpts, "segments": rsegs}, access, folder)
            roads, n_links = cg.join_links(roads, [rest, paths], folder)
            pts, segs = st.straighten(roads["points"], roads["segments"])
            # a private street that ends (a subdivision lane, a complex's drive) closes in a cul-de-sac too: they
            # join the roads only here, after generate capped the public streets, so they all ended square
            mine = slug(f"{t['name']} Roads")
            others = [load_actor(nid, a)[1:] for a, h in actor_docs(nid).items() if a != mine and not (
                " before kit" in str(h.get("label") or "") or str(h.get("label") or "").endswith((" Paths", " Access")))]
            n_cap = pl.cap_private(pts, segs, others, ter)
            joins[t["name"]] = {"junctions": n_pr, "links": n_links, "cul_de_sacs": n_cap}
            out[f"{t['name']} Roads"] = store_actor(nid, f"{t['name']} Roads", {"points": pts, "segments": segs},
                                                    doc.get("name") or nid)["stats"]
            for label in (f"{t['name']} Paths", f"{t['name']} Access"):  # now inside the Roads actor
                if data.get(f"net.{nid}.actor.{slug(label)}"):
                    data.delete(f"net.{nid}.actor.{slug(label)}")
                    gone.append(label)
        doc = data.get(f"net.{nid}") or {}
        doc["actors"] = sorted((set(doc.get("actors") or []) | set(out)) - set(gone))
        # the whole network checked after every generate: one piece, no height step, no road on top of another
        every = {}
        for a, h in actor_docs(nid).items():
            if " before kit" not in str(h.get("label") or ""):
                _h, apts, asegs = load_actor(nid, a)
                every[a] = {"points": apts, "segments": asegs}
        doc["network_check"] = cg.network_report(every)
        data.put(f"net.{nid}", doc)
        push({"what": "network", "id": nid})
        return {"ok": True, "actors": out, "joined": joins, "merged_away": gone, "network": doc["network_check"]}

    def pipeline_run(net_id: str = "", run_from: str = "", confirm: bool = False) -> dict:
        """Status of every stage, or (run_from) re-run that stage and everything downstream, offline."""
        nid = slug(net_id)
        doc = data.get(f"net.{nid}") if nid else None
        if not doc:
            return {"ok": False, "error": f"No road network {net_id!r}."}
        towns = list(city_docs(nid).values())
        ter = scan_terrain(nid) or {}
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
                elif not (r.get("network") or {}).get("ok", True):
                    n = r["network"]
                    notes.append(f"Road network NOT whole: {n['pieces']} pieces, {len(n['steps'])} height steps, "
                                 f"{len(n['stacked'])} roads on top of another (see sidewalks status).")
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
                info = {"actors": [a for a in doc.get("actors") or [] if a.endswith(" Paths")],
                        "network": doc.get("network_check")}
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
                real = tree_props(nid)
                groups = kit_bake.bake(got.get("layers") or {}, folder.split("/WorldGen/Roads")[0], interiors_of(nid), real)[0]
                build, remove, keep = kit_bake.tile_diff(kit_bake.tiles(groups), state.get("tiles") or {}, False)
                most = int((data.get(f"net.{nid}.assets") or {}).get("trees_max") or 0)
                pb, pr, pk = kit_bake.tile_diff(kit_bake.prop_tiles(real or {}, most)[0], state.get("props") or {}, False)
                tiles = {"build": len(build) + len(pb), "remove": len(remove) + len(pr), "same": len(keep) + len(pk)}
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

    def roads_pcg(net_id: str = "", confirm: bool = False, force: bool = False, actors: str = "") -> dict:
        """Every stored road actor (or a comma list) into the open level as PCG road tiles: spline meshes bent along
        the roads (BlockAll), junction and interchange pieces on their nodes. No landscape splines, no clipboard:
        the terrain must already be graded to the roads (the generated heightmap is). Only changed tiles rebuild."""
        from . import road_pcg
        nid = slug(net_id)
        doc = data.get(f"net.{nid}") if nid else None
        if not doc:
            return {"ok": False, "error": f"No road network {net_id!r}."}
        folder = road_folder()
        if "/WorldGen/Roads" not in folder:
            return {"ok": False, "error": "Couldn't find the project's WorldGen folder (import the road kit first)."}
        root = folder.split("/WorldGen/Roads")[0]
        name = doc.get("name") or nid
        tag = re.sub(r"[^A-Za-z0-9]", "", name)
        want = {a.strip() for a in actors.split(",") if a.strip()}
        acts = {}
        for aslug, head in actor_docs(nid).items():
            label = str(head.get("label") or aslug)
            if " before kit" in label or (want and label not in want):
                continue
            _h, pts, segs = load_actor(nid, aslug)
            if segs:
                p2, s2 = st.straighten(pts, segs, keep_z=label.endswith(("Freeways", "Highways")))  # straight from corner to corner
                acts[label] = {"points": p2, "segments": s2}
        if not acts:
            return {"ok": False, "error": "No road actors to push (generate the world first)."}
        tiles = road_pcg.tile_jobs(acts)
        state = data.get(f"net.{nid}.roads_pcg") or {"name": f"{name} / PCG roads", "tiles": {}}
        hashes = {k: hashlib.sha1(json.dumps(v, sort_keys=True).encode()).hexdigest() for k, v in tiles.items()}
        build = sorted(k for k in tiles if force or (state["tiles"].get(k) or {}).get("hash") != hashes[k])
        remove = sorted(k for k in state["tiles"] if k not in tiles)
        runs = sum(len(t["chains"]) for t in tiles.values())
        nodes_ = sum(len(r) for t in tiles.values() for r in t["pieces"].values())
        if not confirm:
            return {"ok": False, "error": f"{name}: {len(tiles)} road tiles ({len(build)} to build, {len(remove)} to "
                                          f"remove), {runs} road runs, {nodes_} junction / interchange pieces. "
                                          f"Call again with confirm=true.",
                    "build": build, "remove": remove, "actors": sorted(acts)}
        label = f"City Builder Roads {name}"

        def job(key: str) -> dict:
            t = tiles.get(key) or {"chains": [], "pieces": {}}
            graph_path = f"{root}/WorldGen/CityPCGRoads/PCG_Roads_{tag}_T{key.replace('-', 'm')}"
            meshes = [{"mesh": f"{m}.{m.rsplit('/', 1)[1]}", "title": m.rsplit("/", 1)[1], "rows": rows,
                       "selector": {"meshes": [f"{m}.{m.rsplit('/', 1)[1]}"], "templateDescriptor": {"descriptor": {
                           "bodyInstance": {"collisionProfileName": "Custom", "collisionEnabled": "QueryAndPhysics"}}}}}
                      for m, rows in sorted(t["pieces"].items())]
            i, j = (int(v) for v in key.split("_"))
            size = road_pcg.TILE_CM
            old = (state["tiles"].get(key) or {}).get("graph_path")
            return {"key": key, "find": label, "label": f"{label} {key}", "meshes": meshes, "chains": t["chains"],
                    "graph_path": graph_path, "graph_name": graph_path.rsplit("/", 1)[1],
                    "graph_folder": graph_path.rsplit("/", 1)[0],
                    "drop_graphs": sorted({g for g in (old, graph_path) if g}),
                    "description": f"City Builder world {name}, road tile {key}: roads as spline meshes and junction "
                                   f"pieces. Rebuilt by road_network_roads_pcg.",
                    "centre": [(i + 0.5) * size, (j + 0.5) * size], "half": size / 2 + 5000.0}

        done, gone = [], []
        try:
            for batch in pcg_batch.batches([job(k) for k in remove + build], max_tiles=6):
                base = uefn_memory.uefn_gb()
                out = epic("editor_toolset.toolsets.programmatic.ProgrammaticToolset", "execute_tool_script",
                           {"script": {"language": "Python", "content": pcg_batch.script(batch)}}, timeout=900)
                res = out.get("returnValue") if isinstance(out, dict) else out
                res = json.loads(res) if isinstance(res, str) else (res or {})
                for j_ in batch:
                    r = res.get(j_["key"])
                    if r is None:
                        continue
                    if r.get("error"):
                        data.put(f"net.{nid}.roads_pcg", state)
                        return {"ok": False, "error": f"road tile {j_['key']}: {r['error']}", "built": done}
                    if r.get("removed"):
                        state["tiles"].pop(j_["key"], None)
                        gone.append(j_["key"])
                    else:
                        state["tiles"][j_["key"]] = {"hash": hashes[j_["key"]], "graph_path": j_["graph_path"],
                                                     "volume": r["volume"]}
                        done.append(j_["key"])
                data.put(f"net.{nid}.roads_pcg", state)
                uefn_memory.settle(base, slack=2.0, timeout=60)
        except (ConnectionError, RuntimeError, TimeoutError, ValueError) as exc:
            data.put(f"net.{nid}.roads_pcg", state)
            return {"ok": False, "error": f"Stopped after {len(done)} road tiles: {exc}", "built": done,
                    "note": "Run again: finished tiles are kept and skipped."}
        return {"ok": True, "id": nid, "built_tiles": done, "removed_tiles": gone, "runs": runs,
                "pieces": nodes_, "memory": uefn_memory.report(5)["worst"]}

    def pick_tiles(tiles: str) -> set:
        """Tile keys from what the person names: '2_1', 'n0_-1', '2_1, 2_2' or the volume label 'City Builder Valley 2_1'."""
        return {m.group(0) for m in re.finditer(r"n?-?\d+_-?\d+", str(tiles or ""))}

    def pcg_build(net_id: str = "", confirm: bool = False, force: bool = False, wait: bool = True,
                  stop: bool = False, nature_only: bool = False, city_only: bool = False, tiles: str = "",
                  allow_misfits: bool = True) -> dict:
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
        # buried / floating buildings never reach UEFN unasked (the AI's tool path; the panel's own push warns)
        misfit = "" if nature_only else cg.misfit_gate(fit_summary())
        if misfit and not allow_misfits:
            return {"ok": False, "error": misfit, "ground_fit": fit_summary()}
        real = tree_props(nid)
        groups, baked, missing = kit_bake.bake(got.get("layers") or {}, root, interiors_of(nid), real)
        name = doc.get("name") or nid
        tag = re.sub(r"[^A-Za-z0-9]", "", name)
        new = kit_bake.tiles(groups)
        state = data.get(f"net.{nid}.pcg") or {"name": f"{name} / PCG tiles", "tiles": {}}
        # the city's trees as Fortnite props: actors in tiles of their own, diffed like the PCG tiles
        new_props, prop_info = kit_bake.prop_tiles(real or {}, int((data.get(f"net.{nid}.assets") or {}).get("trees_max") or 0))
        p_build, p_remove, p_same = kit_bake.tile_diff(new_props, state.get("props") or {}, force)
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
        if nature_only:  # only the forest's 'n' tiles: every city tile stays exactly as it is in the level
            same = sorted(set(same) | {k for k in build if not k.startswith("n")})
            build = [k for k in build if k.startswith("n")]
            remove = [k for k in remove if k.startswith("n")]
        if city_only:  # only the city tiles: the forest's 'n' tiles and the prop trees stay as they are in the level
            same = sorted(set(same) | {k for k in build if k.startswith("n")})
            build = [k for k in build if not k.startswith("n")]
            remove = [k for k in remove if not k.startswith("n")]
            p_build, p_remove = [], []
        pick = pick_tiles(tiles)
        if pick:  # just these tiles (e.g. '2_1' or '2_1, 2_2'): every other tile stays as it is in the level
            same = sorted(set(same) | {k for k in build if k not in pick})
            build, remove = [k for k in build if k in pick], [k for k in remove if k in pick]
            if force:  # a picked tile is rebuilt even when nothing in it changed
                build = sorted(set(build) | {k for k in pick if k in new})
            p_build, p_remove = [k for k in p_build if k in pick], [k for k in p_remove if k in pick]
        if not confirm:
            trees_line = "" if not (p_build or p_remove) else (
                f" {prop_info['placed']} Fortnite tree props in {len(new_props)} tiles: {len(p_build)} tiles to place, "
                f"{len(p_remove)} to remove.")
            return {"ok": False, "error": f"{name}: {len(build)} PCG tiles to (re)build, {len(remove)} to remove, "
                                          f"{len(same)} unchanged (left alone). {sum(baked.values())} buildings and "
                                          f"street pieces on {len(groups)} kit meshes.{trees_line} "
                                          f"Call again with confirm=true.",
                    "build": build, "remove": remove, "unchanged": len(same),
                    "tree_props": {**prop_info, "tiles_to_place": len(p_build), "tiles_to_remove": len(p_remove),
                                   "tiles_unchanged": len(p_same)},
                    "last_build": (last or {}).get("result"), **({"ground_warning": misfit} if misfit else {})}
        if not build and not remove and not p_build and not p_remove:
            return {"ok": True, "id": nid, "note": "Nothing changed since the last build: every tile left as it is.",
                    "unchanged": len(same), "last_build": (last or {}).get("result")}
        job = _PCG_JOBS[nid] = {"running": True, "stop": False, "done": 0,
                                "total": len(build) + len(remove) + len(p_build) + len(p_remove),
                                "started": time.time(), "result": None}

        def place_props() -> dict:
            """The prop trees' tiles: a tile's old trees go (found by their labels in the level), then its new
            ones are placed, a handful per editor call. Kept after every tile, so a stop loses nothing."""
            stem = lambda key: f"Tree {tag} {key} "  # (the space ends the key: tile 1_2 never matches 1_21)
            folder_ = f"City Builder/{name}/Trees"
            stored = state.setdefault("props", {})
            placed = gone = 0
            failed: set = set()
            for key in p_remove + p_build:
                if job["stop"]:
                    return {"ok": False, "error": f"Stopped after {placed} trees.", "placed": placed}
                rows = new_props[key]["rows"] if key in p_build else []
                base = uefn_memory.uefn_gb()
                sent = [[f"{stem(key)}{i}", *r] for i, r in enumerate(rows)]
                first = True
                for k in range(0, max(1, len(sent)), PROP_BATCH):
                    r = listener_long("road_network_props", {"remove": [stem(key)] if first else [],
                                                              "place": sent[k:k + PROP_BATCH], "folder": folder_})
                    first = False
                    if not r.get("ok"):
                        raise ValueError(f"trees of tile {key}: {r.get('error') or r}")
                    placed += int(r.get("placed") or 0)
                    gone += int(r.get("removed") or 0)
                    failed |= set(r.get("failed") or [])
                if rows:
                    stored[key] = {"hash": new_props[key]["hash"], "count": len(rows)}
                else:
                    stored.pop(key, None)
                data.put(f"net.{nid}.pcg", state)
                job["done"] += 1
                push({"what": "progress", "id": nid, "title": "Planting the city's trees", "step": job["done"],
                      "of": job["total"], "text": f"{placed} trees placed"})
                uefn_memory.settle(base, slack=2.0, timeout=60)
            return {"ok": True, "placed": placed, "removed": gone, "tiles": len(p_build), "removed_tiles": len(p_remove),
                    "left_out": prop_info["left_out"], "could_not_place": sorted(failed),
                    "note": "Fortnite's own tree props, one actor each, in the outliner folder "
                            f"'{folder_}'. left_out: trees over the world's limit (road_network_pcg_assets trees_max)."}

        def work() -> dict:
            timings: dict = {}  # seconds per phase, in the result: where a slow push spent its time
            t_phase = time.time()
            mats = None
            if build:
                # the kit's instance-safe materials: made once, then reused. Making them loads every kit mesh
                # (minutes); a build that only needs meshes it already has overrides for skips that.
                cache = data.get(f"net.{nid}.mats") or {}
                # (the nature kit brings its own instance-ready materials: WorldGen/Nature never needs overrides)
                need = {m for k in build for m in new[k]["meshes"]
                        if m.startswith(f"{root}/WorldGen/") and not m.startswith(f"{root}/WorldGen/Nature/")}
                if need <= set(cache.get("overrides") or {}):  # force rebuilds tiles, not materials (none needed: skip)
                    mats = cache
                else:
                    mats = kit_materials(nid)
                    if not mats.get("ok"):
                        return {"ok": False, "error": f"Kit materials: {mats.get('error')}"}
                    # a whole pass (a lost slice fails above) saw every kit mesh: one it didn't see isn't in the
                    # project (e.g. SM_BK_Roof_SatelliteDish), so PCG places nothing for it. Kept as absent ([]),
                    # or every build would redo the minutes-long pass for it.
                    # ponytail: a mesh imported into the kit later stays [] until net.<id>.mats is cleared
                    over = mats.get("overrides") or {}
                    absent = sorted(m.rsplit(".", 1)[-1] for m in need - set(over))
                    over.update({m: [] for m in need - set(over)})
                    data.put(f"net.{nid}.mats", {"name": f"{name} / kit materials", "overrides": over,
                                                 "absent": absent})
                    mats = {**mats, "overrides": over}
            timings["materials_s"] = round(time.time() - t_phase, 1)
            t_phase = time.time()
            # the person's own layers: their meshes' materials need the instanced-mesh usage too (10 meshes per call)
            # meshes already checked by an earlier build skip their checks: each check LOADS the mesh in UEFN
            # (compiling its shaders), which was most of a small rebuild. force=true checks everything again.
            ok_before = {k: set(v) for k, v in ({} if force else (data.get("kit.checked") or {})).items()}
            passed = {k: set(v) for k, v in ok_before.items()}
            own = kit_bake.own_meshes(got.get("layers") or {}) if build else []
            own_new = [m for m in own if m not in ok_before.get("ism", ())]
            own_report = {"fixed": [], "locked": [], "missing": []}
            for k in range(0, len(own_new), 10):
                r = listener("road_network_ism_ready", {"meshes": own_new[k:k + 10]}, timeout=300)
                for key in own_report:
                    own_report[key] += r.get(key) or []
            passed.setdefault("ism", set()).update(set(own_new) - set(own_report["missing"]) - set(own_report["locked"]))
            if own_report["missing"]:
                return {"ok": False, "error": f"These layer meshes aren't in the project: {own_report['missing'][:10]}. "
                                              f"Fix the layer's meshes (Content Browser path) and push again."}
            legacy_removed = legacy_pcg_cleanup(nid, root, name, tag) if not state["tiles"] else []
            # collision: every placed mesh blocks players except leaves and flat paint (kit_bake.NO_COLLISION).
            # A mesh with no collision shapes of its own gets complex-as-simple, or its instances block nothing.
            used = sorted({m for k in build for m in new[k]["meshes"]})
            solid = [m for m in used if kit_bake.blocks(m)]
            made_exact = []
            solid_new = [m for m in solid if m not in ok_before.get("collision", ())]
            for k in range(0, len(solid_new), 20):
                for m in listener_long("road_network_collision_ready", {"meshes": solid_new[k:k + 20]}).get("need") or []:
                    epic("EditorToolset.StaticMeshToolset", "SetCollisionComplexity",
                         {"mesh": {"refPath": m}, "complexity": "CTF_UseComplexAsSimple"})
                    made_exact.append(m.split(".")[0])
            for k in range(0, len(made_exact), SAVE_BATCH):
                listener_long("road_network_save_assets", {"paths": made_exact[k:k + SAVE_BATCH]})
            # the project's own meshes: LODs + Low/Medium Min LOD, or UEFN's validator flags each one
            passed.setdefault("collision", set()).update(solid_new)
            mine = [m for m in used if m.startswith(f"{root}/") and m not in ok_before.get("lod", ())]
            lods_added = 0
            for k in range(0, len(mine), 20):
                lods_added += int(listener_long("road_network_lod_ready", {"meshes": mine[k:k + 20]}).get("fixed") or 0)
            passed.setdefault("lod", set()).update(mine)
            data.put("kit.checked", {k: sorted(v) for k, v in passed.items()})
            timings["mesh_checks_s"] = round(time.time() - t_phase, 1)
            t_phase = time.time()
            collision = {"blocking_meshes": len(solid), "lods_added": lods_added,
                         "made_exact": [m.rsplit("/", 1)[-1] for m in made_exact],
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
                    # 'Custom', never 'BlockAll': Fortnite's BlockAll preset is query-only, which overrides
                    # collisionEnabled, so players were stopped and cars (physics) drove through buildings
                    desc = {"bodyInstance": {"collisionProfileName": "Custom" if solid_ else "NoCollision",
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
            failed_tiles: dict = {}  # key -> error; a tile that fails gets one more try, then the build goes on
            todo = [tile_job(k, None) for k in remove] + [tile_job(k, new[k]) for k in build]
            try:
                # one editor script per batch of tiles (pcg_batch): metered like any call, so the memory guard
                # refuses the next batch when one grew UEFN by GBs, and UEFN settles between batches
                for batch in pcg_batch.with_retry(todo, failed_tiles, lambda: editor_heal.heal("", api.call_tool)):
                    if job["stop"]:
                        return {"ok": False, "error": f"Stopped after {len(done)} of {len(build)} tiles.", "built": done,
                                "note": "Build again: finished tiles are kept and skipped."}
                    base = uefn_memory.uefn_gb()
                    out = epic("editor_toolset.toolsets.programmatic.ProgrammaticToolset", "execute_tool_script",
                               {"script": {"language": "Python", "content": pcg_batch.script(batch)}}, timeout=900)
                    res = out.get("returnValue") if isinstance(out, dict) else out
                    res = json.loads(res) if isinstance(res, str) else (res or {})
                    for j in batch:
                        r = res.get(j["key"])
                        if r is None:  # the script stopped before this tile: it stays as it was, tried again
                            failed_tiles[j["key"]] = "the batch stopped before this tile"
                            continue
                        if r.get("error"):
                            # its old volume may be gone already: never trust the stored one again
                            state["tiles"][j["key"]] = {"hash": "failed", "graph_path": j["graph_path"], "volume": ""}
                            failed_tiles[j["key"]] = str(r["error"])[:200]
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
                    uefn_memory.settle(base, slack=2.0, timeout=60)
            except (ConnectionError, RuntimeError, TimeoutError, ValueError) as exc:
                return {"ok": False, "error": f"Stopped after {len(done)} of {len(build)} tiles: {exc}",
                        "built": done, "note": "Build again: finished tiles are kept and skipped.",
                        "memory": uefn_memory.report(5), "timings": timings}
            timings["tiles_s"] = round(time.time() - t_phase, 1)
            t_phase = time.time()
            # recount what was really placed: a solid mesh's component left at No Collision is switched on,
            # so 'collision' in the result is a count from the level, never just what the build asked for
            if done:
                try:
                    collision["level"] = listener_long("road_network_collision_count", {
                        "prefix": f"City Builder {name} ", "solid": solid, "fix": True})
                except (ConnectionError, RuntimeError, TimeoutError, ValueError) as exc:
                    collision["level"] = {"ok": False, "error": f"couldn't count collision in the level: {exc}"[:200]}
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
            timings["collision_and_cleanup_s"] = round(time.time() - t_phase, 1)
            t_phase = time.time()
            try:
                trees = place_props() if p_build or p_remove else {"ok": True, "placed": 0, "unchanged_tiles": len(p_same)}
            except (ConnectionError, RuntimeError, TimeoutError, ValueError) as exc:
                return {"ok": False, "error": f"The city is built; planting its trees stopped: {exc}", "built": done,
                        "note": "Build again: finished tiles are kept and skipped.", "memory": uefn_memory.report(5)}
            if not trees.get("ok"):
                return {**trees, "built": done, "note": "Build again: finished tiles are kept and skipped."}
            timings["tree_props_s"] = round(time.time() - t_phase, 1)
            t_phase = time.time()
            paint = paint_ground()
            timings["ground_paint_s"] = round(time.time() - t_phase, 1)
            return {"ok": True, "id": nid, "partial": bool(failed_tiles), "timings": timings,
                    "failed_tiles": [{"key": k, "error": e} for k, e in sorted(failed_tiles.items())],
                    "built_tiles": done, "old_graph_folders_removed": cleaned, "removed_tiles": gone, "unchanged_tiles": len(same),
                    "tree_props": trees, "ground_paint": paint,
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

    ctx.share(locals())

"""Panel history (undo, redo), version, chats, build briefs and AI notes on the map.

Handlers moved out of backend/__init__.py register(): ctx carries what the parts share."""

from __future__ import annotations

import json
import os
import re

from . import citygen as cg
from . import kit_bake
from . import pcg_layers as pl
from . import pieces
from . import splines_text as st


def register(ctx) -> None:
    (GEN_INTENT, actor_docs, add_city, api, asset_set_of, check, city_docs, data, delete_city,
     fit_summary, heightmap, hold, holds, holds_the_world, import_all, interiors_of, kit_root, layer_docs,
     list_cities, listener, load_actor, loose_ends, panel_rpc, pcg_preview, pipeline_run, release,
     road_folder, safe, save_city, save_lake, scan_terrain, slug, sync_status, tool, tree_props) = ctx.take(
        "GEN_INTENT actor_docs add_city api asset_set_of check city_docs data delete_city fit_summary "
        "heightmap hold holds holds_the_world import_all interiors_of kit_root layer_docs list_cities "
        "listener load_actor loose_ends panel_rpc pcg_preview pipeline_run release road_folder safe save_city "
        "save_lake scan_terrain slug sync_status tool tree_props")

    # ---------------------------------------------------------------- panel: history (undo, redo)
    def history_list(net_id: str = "") -> dict:
        nid = slug(net_id)
        got = data.steps(nid)
        return {"ok": True, "id": nid, "undo": got["undo"][-30:], "redo": got["redo"][-30:]}

    @holds_the_world
    def history_move(net_id: str = "", back: bool = True, steps: int = 1) -> dict:
        """Undo (or redo) the last steps of a world: its docs and files go back to exactly what they were. UEFN is
        not touched: the panel shows what is now waiting to be pushed."""
        nid = slug(net_id)
        done_ = data.undo(nid, steps) if back else data.redo(nid, steps)
        return {**history_list(nid), "done": done_}

    panel_rpc("history", lambda net_id="": safe(history_list)(net_id))
    panel_rpc("undo", lambda net_id="", steps=1: safe(history_move)(net_id, True, steps))
    panel_rpc("redo", lambda net_id="", steps=1: safe(history_move)(net_id, False, steps))
    panel_rpc("cities", lambda net_id="": safe(list_cities)(net_id))
    panel_rpc("hold", lambda net_id="", city="", task="", minutes=0.0: safe(hold)(net_id, city, task, minutes))
    panel_rpc("release", lambda net_id="", city="": safe(release)(net_id, city))
    panel_rpc("holds", lambda net_id="", teammates=None: safe(holds)(net_id, teammates))
    panel_rpc("city_save", lambda net_id="", city="", name="", style="", radius_m=0.0, buildings="",
                           x=None, y=None, features="", turn_deg=None, shape="", zones="", core="", industry_deg=None:
                           safe(save_city)(net_id, city, name, style, radius_m, buildings, x, y, features,
                                           turn_deg, shape, zones, core, industry_deg))
    panel_rpc("lake_save", lambda net_id="", lake_id="", points="", width_m=0.0: safe(holds_the_world(save_lake))(net_id, lake_id, points, width_m))
    panel_rpc("lake_delete", lambda net_id="", lake_id="": safe(holds_the_world(save_lake))(net_id, lake_id, "", 0.0, True))
    panel_rpc("heightmap", lambda net_id="": safe(heightmap)(net_id))
    panel_rpc("city_add", lambda net_id="", name="", x=None, y=None, radius_m=0.0, style="":
                           safe(add_city)(net_id, name, x, y, radius_m, style))
    panel_rpc("city_delete", lambda net_id="", city="", confirm=False: safe(delete_city)(net_id, city, confirm))
    panel_rpc("pipeline", lambda net_id="", run_from="", confirm=False: safe(pipeline_run)(net_id, run_from, confirm))

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
        groups = kit_bake.bake(layers, root, interiors_of(nid), tree_props(nid))[0] if layers else {}
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
                kind = {1: "turnaround (1 arm)", 2: "elbow (2 arms)", 3: "T (3 arms)", 4: "cross (4 arms)"}.get(len(arms), f"{len(arms)} arms")
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
                "connections": loose_ends(nid, scan_terrain(nid) or {}),
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
                        "-> the door prefabs as `doors` says (scenegraph skill), placed by PCG; terrain -> "
                        "import road_network_heightmap's PNG as a new landscape (already graded to the roads); roads -> "
                        "road_network_roads_pcg (PCG spline meshes + junction pieces, never landscape splines); "
                        "city -> road_network_pcg_build; lighter detail -> "
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

    panel_rpc("note_draft", lambda net_id="", kind="pin", points="": safe(note_draft)(net_id, kind, points))

    @tool(intent=GEN_INTENT, listener=False)
    def road_network_note_draft(net_id: str = "", kind: str = "pin", points: str = "") -> dict:
        """The text the City Builder panel puts in the chat's typing box for a pin (kind 'pin', one [x, y]), an
        area (kind 'area', an outline [[x, y], ...], cm) or a build (kind 'build'): where it is, what is there and
        how to act on it, ending where the person says what they want. Sends nothing."""
        return safe(note_draft)(net_id, kind, points)
    panel_rpc("version", lambda: plugin_version())
    panel_rpc("chats", lambda: safe(list_chats)())
    panel_rpc("send_note", lambda net_id="", chat_id="", text="", kind="pin", points="":
                           safe(send_note)(net_id, chat_id, text, kind, points))

    panel_rpc("sync", lambda net_id="": safe(sync_status)(net_id))
    panel_rpc("build_brief", lambda net_id="", check=False: safe(build_brief)(net_id, check))

    @tool(intent=GEN_INTENT, listener=False)
    def road_network_build_brief(net_id: str = "", check: bool = False) -> dict:
        """Start of every build in UEFN: everything this world needs (every road mesh the splines use and every kit
        mesh the PCG city places, how often, and where each comes from: street kit City Builder makes itself,
        road / world / building kit art, Fortnite), what the project is missing (check=true asks the open UEFN),
        what tools are around (UEFN, Epic MCP, Blender, AI 3D), what is waiting to be built (roads to paste, city
        tiles), a summary per city, and the `questions` to ask the person in ONE ducky_ask_user before any editor
        work (open UEFN?, which pieces, how to make missing ones, scope, density, interiors, terrain, saving)."""
        return safe(build_brief)(net_id, check)

    @tool(intent=GEN_INTENT, listener=False)
    def road_network_sync(net_id: str = "") -> dict:
        """Is UEFN behind this world? Road actors changed or never pushed, and PCG tiles to rebuild / remove,
        against what was last built (offline). The panel header shows it; generating again is never blocked."""
        return safe(sync_status)(net_id)

    @tool(intent=GEN_INTENT, listener=False)
    def road_network_version() -> dict:
        """City Builder's version (the panel shows it at the foot of its sidebar)."""
        return plugin_version()

    @tool(intent=GEN_INTENT, listener=False)
    def road_network_chats() -> dict:
        """The recent Ducky chats a map note (road_network_send_note) can be sent to."""
        return safe(list_chats)()

    @tool(intent=GEN_INTENT, listener=False)
    def road_network_send_note(net_id: str = "", chat_id: str = "", text: str = "", kind: str = "pin", points: str = "") -> dict:
        """Send a note about a spot (kind 'pin', one [x, y]) or an area (kind 'area', an outline [[x, y], ...], cm)
        of a world to a Ducky chat (chat_id, or 'new'), with where it is, what is already there and how to act
        on it with City Builder (an area-limited PCG layer); or kind 'build' (no points): ask that chat to build
        the world in UEFN (landscape splines + PCG tiles, only what changed). The panel's tools call this."""
        return safe(send_note)(net_id, chat_id, text, kind, points)
    panel_rpc("import_all", lambda net_id="", confirm=False, force=False: safe(import_all)(net_id, confirm, force))

    ctx.share(locals())

"""MCP tools for PCG: layers, presets, plan, assets, door spots, preview, ground fit, edits, build and place.

Handlers moved out of backend/__init__.py register(): ctx carries what the parts share."""

from __future__ import annotations

import os
import threading
import time

from . import kit_bake


def register(ctx) -> None:
    (_GROUND, _PREVIEW_CACHE, _PREVIEW_MEMO, edit, edits, ground_fit, interiors_of, panel_preview,
     panel_rpc, pcg_assets, pcg_build, pcg_exact, pcg_layer_delete, pcg_layer_save, pcg_layers, pcg_place,
     pcg_plan, pcg_preset, pcg_preview, preset_delete, preset_save, presets, road_folder, safe, slug,
     terrain_map, tool, tree_props) = ctx.take(
        "_GROUND _PREVIEW_CACHE _PREVIEW_MEMO edit edits ground_fit interiors_of panel_preview panel_rpc "
        "pcg_assets pcg_build pcg_exact pcg_layer_delete pcg_layer_save pcg_layers pcg_place pcg_plan "
        "pcg_preset pcg_preview preset_delete preset_save presets road_folder safe slug terrain_map tool tree_props")

    PCG_INTENT = r"\b(pcg|procedural|scatter|placement layers?|foliage|trees|rocks|street (trees|lamps))\b"

    @tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_layers(net_id: str = "") -> dict:
        """A world's PCG placement layers (trees, bushes, rocks, street pieces, buildings, and any the person or an
        AI added): what each places, where, how dense, what it keeps clear of. Also `fields` (every setting with
        its default / min / max), `kinds`, `where`, `roles` and an `example` custom layer: everything needed to
        write a new layer with road_network_pcg_layer_save, no plugin code involved."""
        return safe(pcg_layers)(net_id)

    @tool(intent=PCG_INTENT, listener=False)
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

    @tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_layer_delete(net_id: str = "", layer_id: str = "") -> dict:
        """Remove one PCG layer from a world (UEFN is not touched)."""
        return safe(pcg_layer_delete)(net_id, layer_id)

    @tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_preset(net_id: str = "", preset: str = "full_city", reorder: bool = False, replace: bool = False) -> dict:
        """Apply a preset to a world: every layer it holds in one call. Built in: 'full_city' (16 building types,
        traffic lights / stop and street signs on junction corners, lamps, street trees, bus stops, benches, bins,
        hydrants, billboards, power poles, and trees / rocks / bushes as Foliage-mode layers, no grass), 'city',
        'buildings', 'street' or 'nature'. Or one saved with road_network_preset_save (road_network_presets lists
        them). Existing layers keep their settings unless replace=true (the preset's settings win); reorder=true
        only moves them into planning order (big civic sites first, dense fill after). A saved preset's world
        options come back as `options`: pass them to road_network_generate to get its terrain and streets too."""
        return safe(pcg_preset)(net_id, preset, reorder, replace)

    @tool(intent=PCG_INTENT, listener=False)
    def road_network_presets() -> dict:
        """Every City Builder preset: the built-in layer sets and the ones saved here by a person or an AI (name,
        how many layers, their names, the world options kept with it, its note). Apply one with
        road_network_pcg_preset."""
        return safe(presets)()

    @tool(intent=PCG_INTENT, listener=False)
    def road_network_preset_save(name: str = "", net_id: str = "", layers: str = "", options: str = "", note: str = "") -> dict:
        """Make or change a preset on the fly: a named set of placement layers, kept with this plugin's data and
        usable on any world. layers: a JSON list of layers exactly as road_network_pcg_layer_save takes one (name,
        kind, role, where, density, sizes, meshes...; road_network_describe lists the kinds, roles and areas). Or
        leave layers empty and give net_id: that world's layers as they are now, with its world options. options:
        JSON of generate options to keep with it (road_network_generate_options lists them), e.g.
        {"block_m": 80, "wobble": 0, "city_ground": "flatten"}. note: what the preset is for, in a line. Saving an
        existing name replaces that preset. Apply it with road_network_pcg_preset(net_id, preset=name)."""
        return safe(preset_save)(name, net_id, layers, options, note)

    @tool(intent=PCG_INTENT, listener=False)
    def road_network_preset_delete(preset: str = "", confirm: bool = False) -> dict:
        """Delete a saved City Builder preset (never a built-in one). Worlds it was applied to keep their layers.
        confirm=true to go ahead."""
        return safe(preset_delete)(preset, confirm)

    @tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_plan(net_id: str = "", road_tag: str = "") -> dict:
        """The PCG graph that places a world's layers in UEFN: native nodes in data-flow order with their
        settings, the wires, the graph params, the road tag and the volume. Build it with Epic PCGToolset
        (skill pcg/graph_layers)."""
        return safe(pcg_plan)(net_id, road_tag)

    @tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_assets(net_id: str = "", asset_set: str = "", interiors: str = "", trees: str = "",
                                trees_max: int = 0) -> dict:
        """Which pieces City Builder uses for a world's layers: 'own' (the project's own kit: WorldGen/City,
        WorldGen/Kit, BuildingKit storeys), 'fortnite' (Fortnite's props and buildings) or 'mixed', and whether
        buildings get interiors (interiors 'yes' / 'no': floors, stair cores, furniture seen through windows).
        trees 'fortnite': a city's trees (street, park, garden, infill and interchange trees) are Fortnite's own
        tree props, the ones players can chop, each placed as an actor by road_network_pcg_build; 'kit': the
        kit's instanced trees. trees_max: the most prop trees a world places (6000 unless set): every one is an
        actor in the level's memory budget, so street and park trees come first and garden trees are thinned to
        what is left. The forest outside the cities always stays instanced kit trees: UEFN refuses Fortnite tree
        meshes in PCG. Empty arguments read them, with where each role (traffic light, skyscraper, bench...)
        comes from."""
        return safe(pcg_assets)(net_id, asset_set, interiors, trees, trees_max)

    def door_spots(net_id: str = "") -> dict:
        """Every door leaf the city's doorways need, where the building grammar put each doorway."""
        from . import grammar
        nid = slug(net_id)
        got = pcg_preview(nid)
        if not got.get("ok"):
            return got
        folder = road_folder()
        root = folder.split("/WorldGen/Roads")[0] if "/WorldGen/Roads" in folder else ""
        groups = kit_bake.bake(got.get("layers") or {}, root, interiors_of(nid), tree_props(nid))[0]
        leaves: dict = {}
        for path, rows in groups.items():
            module = path.rsplit(".", 1)[-1]
            for row in rows:
                for leaf, xf, how in grammar.door_leaves(module, row):
                    leaves.setdefault(leaf, {"opens": how, "folder": f"{root}/WorldGen/{grammar.FOLDER}",
                                             "spots": []})["spots"].append(xf)
        return {"ok": True, "id": nid, "leaves": leaves, "count": sum(len(v["spots"]) for v in leaves.values()),
                "row": "[x, y, z, yaw, sx, sy, sz] (cm, degrees): the leaf's hinge, world space",
                "do": "One Entity Prefab per leaf mesh (the leaf + walkup_door_component, SlideUpCm for 'slide'), "
                      "one instance at each spot with that transform."}

    @tool(intent=PCG_INTENT, listener=False)
    def road_network_door_spots(net_id: str = "") -> dict:
        """Where every door leaf goes: the building grammar puts a doorway module (the door cut out) in each
        door slot, and each doorway needs its leaf (one, or two for double doors) as an openable Scene Graph
        prefab. Returns {leaf mesh: {opens: 'swing' | 'slide', spots: [[x, y, z, yaw, sx, sy, sz]]}}."""
        return safe(door_spots)(net_id)

    @tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_preview(net_id: str = "", layer_id: str = "", summary: bool = False,
                                 profile: bool = False) -> dict:
        """Run a world's PCG layers offline with the graph's rules (district, off the roads, clear of earlier
        layers, no overlaps, nothing under water) and return where each would place things plus its count;
        the panel draws it in 3D. layer_id: just that layer. summary=true: only the count per layer.
        profile=true: place the whole city again from nothing and return the 30 slowest functions."""
        if not profile:
            return safe(pcg_preview)(net_id, layer_id, summary)
        import sys
        # ponytail: a sampling profiler (the embedded Python has no cProfile): every 20 ms, where the run is
        _PREVIEW_CACHE.clear()
        _PREVIEW_MEMO.pop(slug(net_id), None)
        _GROUND.clear()
        me, own, total, done = threading.get_ident(), {}, {}, threading.Event()
        where = lambda fr: f"{os.path.basename(fr.f_code.co_filename)}:{fr.f_code.co_name}"

        def sample() -> None:
            while not done.wait(0.02):
                f = sys._current_frames().get(me)
                if f is None:
                    continue
                at = f"{where(f)}:{f.f_lineno}"  # (the line: a generator expression has no name of its own)
                own[at] = own.get(at, 0) + 1
                seen = set()
                while f is not None:
                    w = where(f)
                    if w not in seen:
                        seen.add(w)
                        total[w] = total.get(w, 0) + 1
                    f = f.f_back
        threading.Thread(target=sample, daemon=True).start()
        t0 = time.perf_counter()
        try:
            safe(pcg_preview)(net_id, "", True)
        finally:
            done.set()
        top = lambda d: [[k, round(v * 0.02, 1)] for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:30]]
        return {"ok": True, "seconds": round(time.perf_counter() - t0, 1), "took_s": _PREVIEW_CACHE.get("took_s"),
                "row": "[file:function, seconds]", "by_total": top(total), "by_own": top(own)}

    @tool(intent=PCG_INTENT, listener=False)
    def road_network_ground_fit(net_id: str = "") -> dict:
        """Is the ground right for the city as it is now? Buildings the ground would bury or leave floating and
        street points it would cover or leave in the air (all must be 0 before the heightmap is imported and the
        city pushed). The ground is re-cut from the layout on every change: lots level, roads cut and filled."""
        return safe(ground_fit)(net_id, False)

    @tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_exact(net_id: str = "", x: float = 0.0, y: float = 0.0, radius_m: float = 400.0,
                               summary: bool = True) -> dict:
        """What road_network_pcg_build would place within radius_m of (x, y) (cm), before pushing: each building,
        site and wall piece with its mesh's real box and the ground under them as the landscape has it. The
        panel's 'Real pieces here' draws it. summary=true (default): piece counts per mesh, meshes with no known
        size, and ground_fit (buildings the ground would bury or leave floating; must be 0 before a push)."""
        return safe(pcg_exact)(net_id, float(x), float(y), float(radius_m), summary)

    @tool(intent=PCG_INTENT, listener=False)
    def road_network_edit(net_id: str = "", op: str = "delete", x: float | None = None, y: float | None = None,
                          layer: str = "", to_x: float | None = None, to_y: float | None = None,
                          yaw: float | None = None, at: str = "", to: str = "", reach_m: float = 0.0,
                          every: bool = False) -> dict:
        """Delete, move or copy a placed piece (a building, yard, slab, prop, tree...) for good: the edit is kept
        with the world and applied after every placement rule, so every rebuild keeps it. op='delete' | 'move' |
        'copy'. The piece is the one standing nearest (x, y) cm within reach_m (default 5 m), of `layer` if given.
        at='camera': the piece the middle of the UEFN viewport looks at (the person points the editor camera at
        it). every=true with delete: every piece (of `layer`, or of every layer) within reach_m. A move or copy goes
        to (to_x, to_y), or to='camera', turned to yaw degrees if given: copy a house from next door to put a house
        on an emptied lot. Returns the tiles to push (pcg_build tiles=)."""
        return safe(edit)(net_id, op, x, y, layer, to_x, to_y, yaw, at, to, reach_m, every)

    @tool(intent=PCG_INTENT, listener=False)
    def road_network_edits(net_id: str = "", undo: bool = False, clear: bool = False) -> dict:
        """The person's deletes and moves on this world (road_network_edit), in order. undo=true drops the last,
        clear=true drops them all; tiles_to_push names the tiles to push again. missed: edits whose piece a
        regenerate moved or dropped."""
        return safe(edits)(net_id, undo, clear)

    @tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_build(net_id: str = "", confirm: bool = False, force: bool = False,
                               stop: bool = False, nature_only: bool = False, city_only: bool = False,
                               tiles: str = "", allow_misfits: bool = False) -> dict:
        """Build the world's previewed city in the open UEFN level as PCG of the project's own kit, in 400 m
        tiles (one graph + volume each). Incremental: only tiles whose lots changed since the last build are
        rebuilt, tiles gone from the plan are removed, the rest of the city, the roads and the level are left
        alone, so editing the city plan never means deleting everything. Without confirm it reports how many
        tiles would change. force=true rebuilds every tile. Ask the person first. confirm=true starts the build
        in the background (batches of tiles, memory checked between them) and returns at once: call again
        for progress (done / total) and the last result; stop=true stops it after the batch in flight.
        nature_only=true builds and removes only the trees / bushes / rocks tiles: no city tile is touched.
        city_only=true is the other way round: only city tiles; the forest tiles and the prop trees stay as they are.
        tiles='2_1' (or '2_1, 2_2', or the volume's label 'City Builder Valley 2_1'): only those tiles are built or
        removed, one batch, every other tile left alone; with force=true they are rebuilt even if unchanged.
        Refused while buildings are still buried or floating (road_network_ground_fit): fix them, or
        allow_misfits=true when the person said to push anyway."""
        return safe(pcg_build)(net_id, confirm, force, False, stop, nature_only, city_only, tiles, allow_misfits)

    @tool(intent=PCG_INTENT, listener=False)
    def road_network_pcg_place(net_id: str = "", confirm: bool = False) -> dict:
        """One click: start an AI chat that builds the world's PCG layers in the open UEFN level from
        road_network_pcg_plan. Ask the person first."""
        return safe(pcg_place)(net_id, confirm)

    panel_rpc("pcg_layers", lambda net_id="": safe(pcg_layers)(net_id))
    panel_rpc("pcg_layer_save", lambda net_id="", layer="", layer_id="": safe(pcg_layer_save)(net_id, layer, layer_id))
    panel_rpc("pcg_layer_delete", lambda net_id="", layer_id="": safe(pcg_layer_delete)(net_id, layer_id))
    panel_rpc("pcg_preset", lambda net_id="", preset="full_city", reorder=False, replace=False:
                           safe(pcg_preset)(net_id, preset, reorder, replace))
    panel_rpc("presets", lambda: safe(presets)())
    panel_rpc("preset_save", lambda name="", net_id="", layers="", options="", note="":
                           safe(preset_save)(name, net_id, layers, options, note))
    panel_rpc("preset_delete", lambda preset="", confirm=False: safe(preset_delete)(preset, confirm))
    panel_rpc("pcg_plan", lambda net_id="", road_tag="": safe(pcg_plan)(net_id, road_tag))
    panel_rpc("pcg_assets", lambda net_id="", asset_set="", interiors="", trees="", trees_max=0:
              safe(pcg_assets)(net_id, asset_set, interiors, trees, trees_max))
    panel_rpc("pcg_preview", lambda net_id="", layer_id="", again=False: safe(panel_preview)(net_id, layer_id, bool(again)))
    panel_rpc("ground_fit", lambda net_id="": safe(ground_fit)(net_id))
    panel_rpc("terrain_map", lambda net_id="", name="": safe(terrain_map)(net_id, name))
    panel_rpc("pcg_exact", lambda net_id="", x=0.0, y=0.0, radius_m=400.0: safe(pcg_exact)(
        net_id, float(x), float(y), float(radius_m)))
    panel_rpc("pcg_place", lambda net_id="", confirm=False: safe(pcg_place)(net_id, confirm))
    panel_rpc("edit", lambda net_id="", op="delete", x=None, y=None, layer="", to_x=None, to_y=None, yaw=None, at="", to="",
              reach_m=0.0, every=False: safe(edit)(net_id, op, x, y, layer, to_x, to_y, yaw, at, to, reach_m, every))
    panel_rpc("edits", lambda net_id="", undo=False, clear=False: safe(edits)(net_id, undo, clear))
    panel_rpc("pcg_build", lambda net_id="", confirm=False, force=False, tiles="":  # tiles: the inspector's one-tile push
              safe(pcg_build)(net_id, confirm, force, tiles=tiles))

    ctx.share(locals())

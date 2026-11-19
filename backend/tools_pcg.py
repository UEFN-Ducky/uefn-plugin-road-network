"""PCG placement layers, presets, assets, the plan and the preview (cut and fit).

Handlers moved out of backend/__init__.py register(): ctx carries what the parts share."""

from __future__ import annotations

import hashlib
import json
import math
import pickle
import time

from . import airport
from . import citygen as cg
from . import kit_bake
from . import pcg_layers as pl
from . import splines_text as st
from . import waterfront


def register(ctx) -> None:
    (_PREVIEW_CACHE, actor_docs, api, city_docs, data, edit_list, edits, load_actor, one_run_at_a_time,
     push, record, road_fit, road_folder, scan_terrain, slug) = ctx.take(
        "_PREVIEW_CACHE actor_docs api city_docs data edit_list edits load_actor one_run_at_a_time push "
        "record road_fit road_folder scan_terrain slug")

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

    def trees_of(nid: str) -> str:
        return "fortnite" if (data.get(f"net.{nid}.assets") or {}).get("trees") == "fortnite" else "kit"

    def tree_props(nid: str):
        """The dict kit_bake.bake fills with a world's prop trees, or None when its trees are the kit's."""
        return {} if trees_of(nid) == "fortnite" else None

    def pcg_assets(net_id: str = "", asset_set: str = "", interiors: str = "", trees: str = "", trees_max: int = 0) -> dict:
        """Which pieces a world's layers use: 'own' (the project's kit), 'fortnite' or 'mixed', whether
        buildings get interiors ('yes' / 'no'), and whether a city's trees are the kit's or Fortnite's own
        tree props (trees 'kit' / 'fortnite', at most trees_max of them). Empty = read them."""
        nid = slug(net_id)
        doc = data.get(f"net.{nid}") if nid else None
        if not doc:
            return {"ok": False, "error": f"No road network {net_id!r}."}
        if asset_set and asset_set not in pl.ASSET_SETS:
            return {"ok": False, "error": f"asset_set is one of {', '.join(pl.ASSET_SETS)}."}
        if interiors and str(interiors).lower() not in ("yes", "no", "true", "false"):
            return {"ok": False, "error": "interiors is yes or no."}
        if trees and str(trees).lower() not in ("kit", "fortnite"):
            return {"ok": False, "error": "trees is kit or fortnite."}
        trees_max = int(trees_max or 0)
        if trees_max < 0 or trees_max > 50000:
            return {"ok": False, "error": "trees_max is 1 to 50000 (0 leaves it as it is)."}
        was = data.get(f"net.{nid}.assets") or {}
        if asset_set or interiors or trees or trees_max:
            before = data.get(f"net.{nid}.assets")
            new = {"name": f"{doc.get('name', nid)} pieces", "asset_set": asset_set or asset_set_of(nid),
                   "interiors": interiors_of(nid) if not interiors else str(interiors).lower() in ("yes", "true"),
                   "trees": str(trees).lower() if trees else trees_of(nid),
                   "trees_max": trees_max or int(was.get("trees_max") or 0)}
            data.put(f"net.{nid}.assets", new)
            record("road_network_pcg_assets", "pcg-assets", nid, before,
                   [{"command": "road_network_pcg_assets", "params": {
                       "net_id": nid, "asset_set": (before or {}).get("asset_set", "own"),
                       "interiors": "no" if (before or {}).get("interiors") is False else "yes",
                       "trees": "fortnite" if (before or {}).get("trees") == "fortnite" else "kit"}}])
            push({"what": "pcg", "id": nid})
        cur = asset_set_of(nid)
        most = int((data.get(f"net.{nid}.assets") or {}).get("trees_max") or 0) or kit_bake.TREE_PROPS_MAX
        return {"ok": True, "id": nid, "asset_set": cur, "interiors": interiors_of(nid), "trees": trees_of(nid),
                "trees_max": most,
                "trees_note": "fortnite: a city's trees (streets, parks, gardens) are Fortnite's own tree props, one "
                              "actor each, at most trees_max of them; the forest stays instanced kit trees (UEFN "
                              "refuses Fortnite tree meshes in PCG).",
                "roles": {r: pl.asset_hint(r, cur) for r in pl.ROLES} | (
                    {} if trees_of(nid) != "fortnite" else {
                        "street_tree": "Fortnite's tree props (actors): Neo park tree, small birch",
                        "tree": "in the cities Fortnite's tree props (actors): medium tree, birches, pines; the "
                                "forest outside: own WorldGen/Kit V4 trees (instances)"})}

    def roads_digest(nid: str) -> str:
        # where every road point lies (meshes left out: build_pieces adds junction meshes without moving a road)
        return hashlib.sha1(json.dumps([[(round(p["x"]), round(p["y"]), round(p["z"])) for p in pts]
                                        for pts, _segs in world_actors(nid, paths=True)]).encode()).hexdigest()

    def world_actors(nid: str, paths: bool = False) -> list:
        actors = []
        for aslug, head in actor_docs(nid).items():
            label = str(head.get("label") or "")
            if " before kit" in label or label.endswith((" Paths", " Access")):
                continue  # a saved backup, or the sidewalks / walkways / driveways: not streets PCG lines up on
            _, pts, segs = load_actor(nid, aslug)
            actors.append((pts, segs) if paths else public_only(pts, segs))
        return actors

    LOT_PATHS = {cg.ROAD_MESH[k].rsplit("/", 1)[-1] for k in ("SW", "WK", "DW", "PR")}

    def public_only(pts: list, segs: list) -> tuple:
        """A roads actor without the lot paths joined into it (sidewalks, walkways, driveways, private streets) and
        the mesh-less links that only lead to them. Those paths were joined into '<City> Roads' so the spline network
        is one piece; read as roads, every house stood on its own driveway and 768 of them were never built."""
        path_seg = lambda s: str(s.get("mesh") or "").rsplit("/", 1)[-1] in LOT_PATHS
        if not any(path_seg(s) for s in segs):
            return pts, segs
        road_pts = {i for s in segs if s.get("mesh") and not path_seg(s) for i in (s["a"], s["b"])}
        path_pts = {i for s in segs if path_seg(s) for i in (s["a"], s["b"])}
        keep = [s for s in segs if not path_seg(s) and not (
            not s.get("mesh") and ((s["a"] in path_pts and s["a"] not in road_pts) or
                                   (s["b"] in path_pts and s["b"] not in road_pts)))]
        used = {i for s in keep for i in (s["a"], s["b"])}
        return [p for p in pts if p["i"] in used], keep

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

    _PREVIEW_STASH: dict = {}  # the world looked at before this one: {nid: (its preview cache, its ground)}
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
        # (not a park: it is planted ground that lies as the land does, with nothing to fit)
        rows = [(name, r) for name, v in placed.items() if v.get("kind") == "buildings" and v.get("role") != "park"
                for r in v["points"] if len(r) > 9]
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

    _PANEL_WANTS: dict = {}  # the world the panel last asked a city for: an older world's placement stops

    def panel_preview(net_id: str = "", layer_id: str = "", again: bool = False) -> dict:
        _PANEL_WANTS["net"] = slug(net_id)
        return pcg_preview(net_id, layer_id, panel=True, again=again)

    def pcg_preview(net_id: str = "", layer_id: str = "", summary: bool = False, panel: bool = False,
                    again: bool = False) -> dict:
        """Every enabled layer placed offline with the graph's rules; one layer per call when layer_id is
        given (keeps each reply small). Points: [x, y, z, yaw, scale(, height m for buildings)].
        panel: the panel's own call, stopped (at the next layer or stage) once it opens another world."""
        nid = slug(net_id)
        if not data.get(f"net.{nid}"):
            return {"ok": False, "error": f"No road network {net_id!r}."}
        docs = layer_docs(nid)
        if not docs:
            return {"ok": True, "id": nid, "layers": {}}
        cur = _PREVIEW_CACHE.get("nid")
        if cur != nid:
            # another world: the last one's placement is put aside and this one's taken back, so going back to a
            # world just looked at is instant (it was placed again from nothing, a minute, every time)
            # ponytail: one world put aside (each is a few hundred MB of points); more if people hop between three
            back = _PREVIEW_STASH.pop(nid, None)
            _PREVIEW_STASH.clear()
            if cur:
                _PREVIEW_STASH[cur] = (dict(_PREVIEW_CACHE), dict(_GROUND))
            _PREVIEW_CACHE.clear()
            _GROUND.clear()
            if back:
                _PREVIEW_CACHE.update(back[0])
                _GROUND.update(back[1])
            _PREVIEW_CACHE["nid"] = nid
        # the panel's later calls of one load (again): the first call just checked the inputs, so they read the
        # placement as it stands instead of re-reading and hashing the whole world (terrain, every actor and city)
        # once more each. ponytail: a change landing between those calls is caught by the panel's next load
        reuse = again and cur == nid and "out" in _PREVIEW_CACHE
        if not reuse:
            if panel:  # (reading a big world's ground and hashing it takes seconds before the first layer)
                push({"what": "progress", "id": nid, "step": 0, "of": 100, "text": "Reading the world…"})
            terrain = scan_terrain(nid) or {}
            actors = world_actors(nid)
            cities = list(city_docs(nid).values())
            # Every layer avoids the ones before it, so one layer still needs the whole preview: compute it
            # once per input set and serve the panel's per-layer calls from that.
            keep = ((data.get(f"net.{nid}") or {}).get("generated") or {}).get("keep") or []
            # pickle, not sorted JSON: the same inputs read back the same way give the same bytes, several times faster
            gen = (data.get(f"net.{nid}") or {}).get("generated") or {}
            # the ground the buildings stand on: this world's own heightmap (lots cut in) once it has one; a world laid
            # over another's landscape keeps that landscape as scanned until its heightmap is made
            # ... and a world whose ground was pulled from UEFN stands on it exactly as it is there: no road grading, no
            # lot pads cut over it (the person shapes that landscape in UEFN; City Builder never writes it)
            cut = (not gen.get("ground_from") or bool(gen.get("heightmap"))) and \
                (data.get(f"net.{nid}.scan") or {}).get("source") != "uefn"
            # ponytail: one world is worked on at a time (the cache has one entry too), so its landscape size is global
            cg.HEIGHTMAP_RES = cg.landscape_res(gen.get("options"))
            # (without each city's plan report: the placement reads its lots, never the report, and the report's wording
            # alone changes on the rebuild after an edit)
            edits = edit_list(nid)
            key = hashlib.sha1(pickle.dumps([nid, docs, [{k: v for k, v in c.items() if k != "plan"} for c in cities],
                                             terrain, actors, keep, cut, cg.HEIGHTMAP_RES, edits], protocol=5)).hexdigest()
        if not reuse and _PREVIEW_CACHE.get("key") != key:  # ponytail: one entry, the world being looked at
            ground = st.grid_sampler(terrain) if terrain.get("heights") else (lambda x, y: 0.0)
            ground = cg.on_made(cities, ground)  # (a shipyard's platform and a made shore: at their own levels)
            # the whole city, unit by unit: what no change can reach is put back as it stood (pl.PreviewMemo), so an
            # edit to one city places that city again and leaves every other building and tree where it was
            memo = _PREVIEW_MEMO.setdefault(nid, pl.PreviewMemo())
            # the panel's 'Placing the city' bar follows these (it sat at 0 % for the whole first call), and the
            # seconds each stage took come back in the summary (took_s)
            took, clock = {}, [time.perf_counter(), ""]

            def tick(step: int, text: str) -> None:
                if panel and _PANEL_WANTS.get("net") != nid:
                    _PREVIEW_MEMO.pop(nid, None)  # (half placed: its next run places everything again)
                    raise RuntimeError("Stopped: another world was opened.")
                now = time.perf_counter()
                if clock[1]:
                    took[clock[1]] = round(took.get(clock[1], 0.0) + now - clock[0], 2)
                clock[:] = [now, text]
                if text:
                    push({"what": "progress", "id": nid, "step": step, "of": 100, "text": text})
            # the bar by work, not by layer count: the forest layers are nearly all of it (a terrain-only world sat
            # at 0-3 % through them, then jumped through 32 empty layers in a blink)
            # ponytail: a fixed weight per kind; time each kind if the bar still lurches
            lays = sorted((pl.clean(l) for l in docs.values() if l.get("enabled", True)), key=lambda l: (l["order"], l["name"]))
            ws = [20 if l["kind"] in pl.SCATTERED else 1 for l in lays]
            done_w = [sum(ws[:i]) for i in range(len(ws) + 1)]
            placed = pl.preview(list(docs.values()), cities, terrain, pl.road_samples(actors), ground,
                                limit=6000, juncs=pl.junctions(actors), keep=keep, foliage_limit=FOREST_LIMIT, memo=memo,
                                arms=pl.arm_samples(actors),
                                on_layer=lambda i, n, name: tick(int(60 * done_w[min(i, len(ws))] / max(1, done_w[-1])),
                                                                 f"Placing {name.lower()} (layer {i + 1} of {n})…"))
            tick(60, "Keeping everything off the roads and out of the buildings…")
            _PREVIEW_CACHE["placing"] = dict(memo.last)
            # (no loose wall slabs along the roads: every road piece carries its own side walls down into the
            # ground, road_profiles 'skirt'; every building its own foundation, kit_bake.foundation; every low deck
            # its approach wall, citygen.deck_fills; and the ground is graded to the roads)
            placed.update(pl.site_layers(cities, ground))  # complexes' parking, stalls, garages
            # (a cap on every street that ends is a turnaround piece on its spline, cap_ends: kept clear, not placed)
            ends = pl.road_ends(actors, terrain)
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
            caps = [(p[0], p[1], p[4] * 1000.0) for p in (ends.get("Road ends") or {}).get("points") or []]
            # waterfront structures (piers, marinas, quays, beaches, ports, stilt / floating homes)
            placed.update(waterfront.layers(cities))
            # airports: aircraft, jet bridges, hangars, tower, lights, aprons, fuel farm
            placed.update(airport.layers([r for c in cities for r in (c.get("private") or {}).get("airport") or []]))
            # (swept after the ports and airports are in: a container yard keeps its trees out and its stacks off
            # the streets like any other paved slab)
            surface = pl.surface_samples(actors, cities) + caps
            _PREVIEW_CACHE["swept"] = {"yards_fitted": pl.fit_yards(placed, surface),
                                       "off_yards": pl.clear_yards(placed),
                                       "off_roads": pl.clear_roads(placed, surface),
                                       "out_of_buildings": pl.clear_buildings(placed)}
            # every generated world: a building the ground would bury is lifted onto it, here, before any push
            # (worlds over another's landscape were skipped once: their houses went in with the hill inside them)
            tick(70, "Cutting the roads into the ground…")
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
                rk = hashlib.sha1(pickle.dumps([cg.HEIGHTMAP_RES, terrain, actors, [s for _c, s in plans],
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
            tick(80, "Fitting every building to the ground…")
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
                fit["left_out"] = {"deeper_than_m": {"house, mansion": pl.FOUND_MAX_M, "any other": pl.PODIUM_MAX_M},
                                   "buildings": len(lots), "planned_sites_kept": sites,
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
                # forest, bushes, rocks: on the cut ground too, and never on its banks. They were placed on the coarse
                # scan, which has none of the slopes the cut makes (a flattened city's edge, a shore): trees stood on
                # those banks, at the coarse height
                g, own_layers = fit["ground"], {d.get("name"): pl.clean(d) for d in docs.values()}
                for name, v in placed.items():
                    lay = own_layers.get(name)
                    if not lay or lay["kind"] not in pl.SCATTERED or v.get("keep_z"):
                        continue
                    lim = 600 * math.tan(math.radians(lay["max_slope_deg"])) if lay["max_slope_deg"] < 90 else None
                    keep, sea = [], terrain.get("sea")
                    for p in v["points"]:
                        z = g(p[0], p[1])
                        if sea is not None and z < sea + 50:
                            continue  # under (or right at) the water on the real ground
                        if lim is not None and math.hypot(g(p[0] + 300, p[1]) - g(p[0] - 300, p[1]),
                                                          g(p[0], p[1] + 300) - g(p[0], p[1] - 300)) > lim:
                            continue
                        p[2] = round(z - lay["sink_cm"])
                        keep.append(p)
                    if len(keep) != len(v["points"]):
                        v["count"] = round(v["count"] * len(keep) / max(1, len(v["points"])))
                        v["points"], v["shown"] = keep, len(keep)
            # real bridges: a pier bent every 30 m under every deck and elevated ramp, down to the ground it stands
            # on (the cut ground once there is one) or the lake bed
            tick(92, "Bridges, piers and paving…")
            decks = {str(k): {"points": p, "segments": s} for k, (p, s) in enumerate(actors)}
            stands_on = (fit or {}).get("ground") or (lambda x, y: ground(x, y) or 0.0)
            # the city centre stands on concrete, not grass: laid last, on the ground as the lots left it
            placed.update(pl.paved_lots(cities, placed, stands_on))
            placed.update(pl.park_trees(placed, cities, stands_on))  # a planned park is trees in groves, not a slab
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
            # last of all, the person's own deletes and moves (road_network_edit): no rule above undoes them
            _PREVIEW_CACHE["edits_missed"] = pl.apply_edits(placed, edits, stands_on)
            tick(100, "")
            _PREVIEW_CACHE.update(key=key, out=placed, fit=fit, ter=fit_ter, ground=stands_on,
                                  took_s=dict(sorted(took.items(), key=lambda kv: -kv[1])[:12]))
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
                    # why lots stood empty, per building layer (only units placed again since the plugin started)
                    "skipped": {k: v["skipped"] for k, v in out.items() if v.get("skipped")},
                    "ground_fit": fit_summary(),
                    # what the last pass took off the roads and out of the buildings, per layer
                    "swept": _PREVIEW_CACHE.get("swept"),
                    # the slowest stages of the last full placement, in seconds
                    "took_s": _PREVIEW_CACHE.get("took_s")}
        return {"ok": True, "id": nid, "layers": out}

    _GROUND: dict = {}

    ctx.share(locals())

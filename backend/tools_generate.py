"""Generated worlds: generate, lakes, cities (add, save, delete), the heightmap and import_all.

Handlers moved out of backend/__init__.py register(): ctx carries what the parts share."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import time

from . import citygen as cg
from . import freeways as fwmod
from . import holds as hd
from . import pcg_layers as pl
from . import pieces
from . import splines_text as st
from . import waterfront
from .terrain import export as tex


def register(ctx) -> None:
    (NO_PANEL, _PREVIEW_CACHE, actor, actor_docs, api, build_pieces, city_guard, city_path, data,
     drop_actor, fit_summary, held_cities, held_lock, holds_the_world, layer_docs, listener,
     listener_long, load_actor, one_run_at_a_time, pcg_preset, pcg_preview, push, put_json_file,
     read_json_file, record, reimport, roads_digest, save_network, scan_terrain, sidewalks_stage, slug,
     store_actor, team_live, world_actors) = ctx.take(
        "NO_PANEL _PREVIEW_CACHE actor actor_docs api build_pieces city_guard city_path data drop_actor "
        "fit_summary held_cities held_lock holds_the_world layer_docs listener listener_long load_actor "
        "one_run_at_a_time pcg_preset pcg_preview push put_json_file read_json_file record reimport "
        "roads_digest save_network scan_terrain sidewalks_stage slug store_actor team_live world_actors")

    # ---------------------------------------------------------------- generated worlds
    engine_maps: dict = {}  # world id -> every map of its last engine run (this session only: debug views)

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

    def open_root() -> str:
        """The content root of the island UEFN has open ('/<mount>'), '' when UEFN is closed."""
        try:
            got = api.call_tool("get_project_info", {}) or {}
        except Exception:
            return ""
        root = str(got.get("content_root") or "") if isinstance(got, dict) else ""
        return root.rstrip("/") if root.startswith("/") else ""

    def road_folder() -> str:
        """The open island's road meshes folder. Worlds are shared between islands, so a stored folder counts only
        when it lies in the open island (a world made in one island pushed its PCG graphs to that island's folder
        from another, which UEFN refuses); none there: '<root>/WorldGen/Roads'. UEFN closed: the first stored one."""
        root = open_root()
        for folder in stored_road_folders():
            if not root or folder.startswith(root + "/"):
                return folder
        return root + "/WorldGen/Roads" if root else ""

    def stored_road_folders():
        """Every road folder the stored worlds name: a generated world's own, else read off a stored network's
        first segments (an actor's file, or an older world's first segment chunk)."""
        keys = data.keys("net.")
        for key in keys:
            if re.fullmatch(r"net\.[^.]+", key):
                folder = ((data.get(key) or {}).get("generated") or {}).get("folder")
                if folder:
                    yield folder
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
                        yield m.group(1)

    def heightmap_path(nid: str, png: bytes) -> str:
        # this PC's copy: rebuilt from the shared terrain every time it is asked for. Named by its content: UEFN
        # keeps the pixels of a file it has read and picking the same name again imported the OLD terrain
        return os.path.join(str(api.local_dir()), "heightmaps", f"{nid}_{hashlib.sha1(png).hexdigest()[:8]}.png")

    def write_heightmap(nid: str, ter: dict) -> dict:
        # every previewed lot is cut into its level pad: only ever this new landscape, never the level's terrain
        got = pcg_preview(nid)
        src_maps = ter.get("maps")
        ter = _PREVIEW_CACHE.get("ter") or ter  # with the roads cut in: the ground the buildings were fitted to
        fit = _PREVIEW_CACHE.get("fit") or {}
        pads = fit.get("pads") if fit.get("pads") is not None else pl.pads(got.get("layers") or {}, cg.pad_margin(ter))
        png, settings = cg.heightmap_png(ter, pads=pads)
        path = heightmap_path(nid, png)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mine = re.compile(re.escape(nid) + r"(_[0-9a-f]{8}(_paved|_" + "|_".join(tex.LAYERS) + r")?)?\.png")
        for old in os.listdir(os.path.dirname(path)):  # one set of files per world: the last one made
            if mine.fullmatch(old) and not old.startswith(os.path.basename(path)[:-4]):
                os.remove(os.path.join(os.path.dirname(path), old))
        with open(path + ".tmp", "wb") as fh:
            fh.write(png)
        os.replace(path + ".tmp", path)
        # the paint layer under every road, junction, private street and lot: picked for a layer in the same import,
        # so the ground there comes in painted (not grass) with no brush work
        mask = cg.weightmap_png(ter, int(settings["resolution"].split(" x ")[0]),
                                pl.surface_samples(world_actors(nid), list(city_docs(nid).values())), pads)
        wpath = path[:-4] + "_paved.png"
        with open(wpath + ".tmp", "wb") as fh:
            fh.write(mask)
        os.replace(wpath + ".tmp", wpath)
        # an engine world's ground materials, one weightmap per layer (they sum to 255): grass, rock, sand, snow, dirt
        layers = {}
        for layer, mpng in tex.material_pngs({**ter, "maps": ter.get("maps") or src_maps},
                                             int(settings["resolution"].split(" x ")[0])).items():
            layers[layer] = path[:-4] + f"_{layer}.png"
            with open(layers[layer] + ".tmp", "wb") as fh:
                fh.write(mpng)
            os.replace(layers[layer] + ".tmp", layers[layer])
        return {"path": path, "paint_layer_path": wpath, "material_layer_paths": layers, **settings}

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

    # worlds whose Stop was pressed: their rebuild quits at its next stage, always before anything is saved
    stops: set = set()

    class Stopped(ValueError):  # safe() answers it as {"ok": False, "error": ...}; one_run_at_a_time keeps no answer
        pass

    def generate_stop(name: str = "", clear: bool = False) -> dict:
        """The panel's Stop (clear: the rebuild returned, so a late press never stops the next one)."""
        (stops.discard if clear else stops.add)(slug(name))
        return {"ok": True}

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
        # drawn by hand (lake_save): a list. options.lakes as a number is the terrain engine's Lakes slider, an option
        lakes = cg.lake_list(given.pop("lakes") if isinstance(given.get("lakes"), list) else last.get("lakes"))
        opts = cg.options({**last.get("options", {}), **given})
        folder = folder or road_folder()
        if not folder:
            return {"ok": False, "error": "Give folder: the project's road meshes folder (…/WorldGen/Roads)."}
        size = max(1.0, min(20.0, float(size_km or 6))) * 100000.0
        base = None
        if ground_from:
            base = scan_terrain(slug(ground_from))
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
        elif not text and (old or last):  # regenerate the cities already there (their names, styles and buildings stay;
            # a world whose cities were all removed stays terrain only)
            towns = [{k: v for k, v in c.items() if k in ("name", "x", "y", "radius", "style", "seed", "features",
                                                          "turn", "spin", "shape", "zones", "pinned", "core",
                                                          "industry_deg")}
                     for c in old.values()]
            # City size changed since the last run: every city grows / shrinks by the same factor; then they are
            # pushed apart until the distance between cities holds
            grow = opts["city_size"] / float((last.get("options") or {}).get("city_size", 1.0) or 1.0)
            for t in towns:
                t["radius"] = max(15000.0, t["radius"] * grow)
        else:  # cities = 0: terrain only (the ground and its heightmap, cities added later)
            count = max(0, min(12, int(text or 3)))
            towns = cg.place_cities(int(seed), count, bounds, keep, opts["city_size"], opts["city_gap_m"] * 100) if count else []
            if count and not towns:
                return {"ok": False, "error": "No room for that many cities: make the map bigger or ask for fewer."}
        # every city stays on the map: pushed apart (a grown city's neighbours make room) and pulled back inside,
        # smaller if the map has no room (a grown city pushed its neighbours off the map, Nov 11 2026)
        towns, fit_notes = cg.fit_inside(towns, bounds, opts["city_gap_m"] * 100, keep)
        if opts["island"] != "none" and not base:  # the land is shaped first; a city in the sea moves onto it
            fit_notes = list(fit_notes or []) + cg.towns_on_land(
                towns, cg.island_land(int(seed), size, (center_x, center_y), opts["island"]), bounds,
                opts["city_gap_m"] * 100)
        for t in towns:  # the map's edges: freeways come back to grade and end before them
            t["map"] = [bounds[0][0], bounds[0][1], bounds[1][0], bounds[1][1]]
        cg.yield_plan(towns)  # where two cities overlap the bigger keeps the land: no street or house twice
        steps = 4 + len(towns)

        def stop_here() -> None:
            if nid in stops:
                stops.discard(nid)
                raise Stopped("Stopped. Nothing was changed.")

        def progress(step: int, text: str) -> None:  # the panel's loading card follows these
            if step < 3:  # (city steps come after the save: those run to the end)
                stop_here()
            push({"what": "progress", "id": nid, "step": step, "of": steps, "text": text})
        progress(0, "Reading the ground" if base else "Shaping the terrain")
        ter = base or cg.terrain(int(seed), size, max(0.0, float(relief_m)) * 100, towns, grid=cg.GROUND_GRID,
                                 center=(center_x, center_y), opts=opts, keep=keep, lakes=lakes)
        if not base and ter.get("maps"):  # an engine world: its full maps stay here for the panel's Debug looks
            engine_maps[nid] = cg.LAST_WORLD.maps
        else:
            engine_maps.pop(nid, None)
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
        # a scanned landscape is never reshaped: a Blender interchange model only where it is flat already. Ground
        # the world owns is always cut to the model's pad (grade_to_roads): every interchange is a Blender one there
        if base:
            cg.slope_plan(towns, opts, road_ground, cg.MODEL_FLAT_CM)
        else:
            for t in towns:
                t.pop("ic_steep", None)
        cg.share_freeways(towns, opts)
        progress(2, "Laying roads, freeways, bridges and runways")
        # the ground goes in too: on an engine world (maps.road_cost) each highway takes the cheapest way through
        # the passes; a scan or Classic ground has no cost map and keeps its one curve
        actors = cg.generate(towns, road_ground, folder, int(seed), opts, keep, wet, bridges, airports, airport_builds,
                             terrain=ter if ter.get("heights") else None)
        # every street that ends closes in a cul-de-sac now, before the ground is graded to it: not only once
        # Build pieces ran in UEFN (a regenerate dropped every turnaround until then)
        pl.cap_all([(a["points"], a["segments"]) for a in actors.values()], ter)
        if not base:  # our own terrain is cut and filled to the roads (a scanned landscape never is)
            ter = cg.grade_to_roads(ter, actors)
            for label, a in actors.items():  # so freeways and highways stand on it as meshes, never reshaping it
                if label.endswith(("Freeways", "Highways")):
                    cg.detach(a)
        stop_here()  # the last chance: from here on the world is written
        before = data.get(f"net.{nid}")
        saved = save_network(nid, name, "", "live")
        if not saved.get("ok"):
            return saved
        stale = [a for a in actor_docs(nid) if a not in {slug(label) for label in actors}]  # gone after the paths
        for cslug in old:
            if cslug not in {slug(t["name"]) for t in towns}:
                drop_city(nid, cslug)
        stats_out = {label: store_actor(nid, label, a, name)["stats"] for label, a in actors.items()}
        # terrain is a big blob (a 320 grid with its maps is ~4 MB): a world file, the doc keeps its bounds (scan_terrain
        # reads both back); never edited by hand, so it never conflicts
        if ter.get("grid", 0) > 256:
            path = f"world/{nid}/terrain.json"
            sha = put_json_file(path, {"heights": ter["heights"], "maps": ter.get("maps") or {}})
            doc_ter = {**{k: v for k, v in ter.items() if k not in ("heights", "maps")}, "file": path, "sha": sha}
        else:
            doc_ter = ter
        data.put(f"net.{nid}.scan", {"name": f"{name} / terrain", "props": [], "terrain": doc_ter})
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
            # every building and lane can be driven to: a drive to the nearest street where none reaches it
            # (its own Roads only: a drive is joined inside that one actor; airport buildings got theirs in generate)
            mine = [a for label, a in actors.items() if label == f"{t['name']} Roads"]
            layout["streets"] += cg.lot_drives(layout["lots"], layout["streets"], layout.get("blocks") or [],
                                              cg.ground_roads(mine), wet)
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
                            "cities": len(towns), "center": [center_x, center_y], "keep": keep, "lakes": lakes,
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
                "interchange_models": {"registered": sorted(fwmod.models()),
                                       "placed": {k: sum(1 for r in freeways.values() for i in r["interchanges"]
                                                         if i.get("model") and i["kind"] == k) for k in sorted(fwmod.models())}},
                "freeways": {n: {"interchanges": [i["kind"] + ("" if i.get("model") else " (generated)") for i in r["interchanges"]],
                                 **({"not_modelled": r["rejected"]} if r.get("rejected") else {}),
                                 "validator_ok": r["validation"]["ok"],
                                 **({} if r["validation"]["ok"] else {"validation": r["validation"]})}
                             for n, r in freeways.items()},
                "note": "Preview it in the Road Network panel. For the generated terrain, make the heightmap "
                        "(road_network_heightmap) and import it first, then Import into UEFN." if not base else
                        "Roads follow this network's ground. Import into UEFN when it looks right."}

    @holds_the_world
    def save_lake(net_id: str = "", lake_id: str = "", points: str = "", width_m: float = 0.0, remove: bool = False) -> dict:
        """Draw a lake on a generated world, change one or take one away (generate again to dig it and rebuild
        the cities round it). points: JSON [[x, y], ...] in cm along its middle (one point: a round pond);
        width_m: how wide it is. lake_id: the lake to change (empty: a new one). The lake is dug into the world's
        own terrain down to its water line."""
        nid = slug(net_id)
        doc = data.get(f"net.{nid}") if nid else None
        gen = (doc or {}).get("generated")
        if not gen:
            return {"ok": False, "error": f"{net_id!r} is not a generated world."}
        if gen.get("ground_from"):
            return {"ok": False, "error": "This world lies on a landscape scanned from UEFN, and that ground is never "
                                          "changed: a lake cannot be dug into it. Lakes are for worlds that make their own terrain."}
        lakes = cg.lake_list(gen.get("lakes"))
        mine = next((l for l in lakes if l["id"] == str(lake_id)), None) if lake_id else None
        if lake_id and not mine:
            return {"ok": False, "error": f"No lake {lake_id!r} on this world. It has: {', '.join(l['id'] for l in lakes) or 'none'}."}
        if remove:
            if not mine:
                return {"ok": False, "error": "Give lake_id: the lake to take away."}
            lakes = [l for l in lakes if l is not mine]
        else:
            row = dict(mine or {})
            if str(points or "").strip():
                try:
                    row["pts"] = [[float(p[0]), float(p[1])] for p in json.loads(points)]
                except (ValueError, TypeError, IndexError) as exc:
                    return {"ok": False, "error": f"points is a JSON list of [x, y] in cm along the lake's middle: {exc}"}
            if width_m:
                row["width_m"] = float(width_m)
            if not row.get("pts"):
                return {"ok": False, "error": "Give points: where the lake lies, [[x, y], ...] in cm."}
            half = float(gen.get("size_km") or 6.0) * 50000.0
            cx, cy = (gen.get("center") or [0.0, 0.0])[:2]
            if not any(abs(p[0] - cx) < half and abs(p[1] - cy) < half for p in row["pts"]):
                return {"ok": False, "error": "That lake lies off the map."}
            lakes = [row if l is mine else l for l in lakes] if mine else lakes + [row]
        lakes = cg.lake_list(lakes)
        doc["generated"] = {**gen, "lakes": lakes}
        data.put(f"net.{nid}", doc)
        push({"what": "network", "id": nid})
        return {"ok": True, "id": nid, "lakes": lakes, "lake": None if remove else (mine or {}).get("id") or lakes[-1]["id"],
                "width_m": list(cg.LAKE_WIDTH_M), "note": "Generate again (cities empty) to dig it and rebuild the cities round it."}

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
        ter = scan_terrain(nid) or {}
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
                "style": style or cg.AUTO_STYLES[len(have) % len(cg.AUTO_STYLES)], "seed": int(gen.get("seed", 7)) * 31 + len(have) + 7}
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
                  turn_deg: float | None = None, shape: str = "", zones: str = "", core: str = "",
                  industry_deg: float | None = None) -> dict:
        """Change one city's style, size, place (x, y: its new centre in cm), building sets
        ({"downtown": [mesh paths], ...}), features ({"airports": "none", ...}: cg.FEATURES, and its own district
        sizes {"downtown": 0.3, "normal": 0.7}: cg.CITY_SHARES), the way it is turned (turn_deg), its outline
        (shape), the type of its blocks (zones), where its centre district is (core) or the way its industrial
        side points (industry_deg)."""
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
        world_opts = ((data.get(f"net.{nid}") or {}).get("generated") or {}).get("options") or {}
        if style:
            if style == "organic" and before.get("style") != "organic":  # it wanders, whatever the world's wobble
                doc["features"] = {**cg.ORGANIC_LOOK, **(doc.get("features") or {})}
            doc["style"] = style
        if x is not None and y is not None and str(x) != "" and str(y) != "":
            doc["x"], doc["y"] = round(float(x)), round(float(y))  # moved: generate again rebuilds it there
            doc["pinned"] = True  # put there by hand: it stays, however close to another city (never pushed apart)
        if radius_m:
            doc["radius"] = max(150.0, float(radius_m)) * 100
        if turn_deg is not None and str(turn_deg) != "":
            # the whole city turns: its street grid, its outline and its industrial side
            doc["turn"] = round(float(turn_deg) % 360.0, 1)
            doc["spin"] = round(doc["turn"] - cg.seed_turn(doc), 3)
        if core:
            # the centre district (the tall middle, and the rings round it) at another spot inside the city: the
            # city itself stays where it is
            if str(core).strip().lower() in ("reset", "none", "[]"):
                doc.pop("core", None)
            else:
                try:
                    dx, dy = (float(v) for v in json.loads(core))
                except (ValueError, TypeError) as exc:
                    return {"ok": False, "error": f"core is a JSON pair [dx, dy]: cm from the city's own centre (or 'reset'): {exc}"}
                at = cg.clamp_core(doc, dx, dy)
                if at[0] or at[1]:
                    doc["core"] = at
                else:
                    doc.pop("core", None)
        if industry_deg is not None and str(industry_deg) != "":
            # the direction given is where it points on the map now; kept from before the city's turn, so it turns with it
            doc["industry_deg"] = round((float(industry_deg) - float(doc.get("spin") or 0.0)) % 360.0, 1)
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
            sizes = {}
            for k in [k for k in given if k in cg.CITY_SHARES]:  # its own district sizes: shares of its radius
                v = given.pop(k)
                try:
                    _d, lo, hi = cg.OPTIONS[k]
                    sizes[k] = None if v is None else round(max(lo, min(hi, float(v))), 3)
                except (TypeError, ValueError):
                    return {"ok": False, "error": f"{k} is a share of the city's radius (a number), or null for the world's."}
            bad = [k for k, v in given.items() if k not in cg.FEATURES or (v is not None and v not in cg.CHOICES[k][0])]
            if bad:
                return {"ok": False, "error": f"Unknown feature or value: {bad}. Features: " + ", ".join(
                    f"{k} ({'/'.join(cg.CHOICES[k][0])})" for k in cg.FEATURES) + "; district sizes: downtown, normal (0 to 1)."}
            merged = {**(doc.get("features") or {}), **given, **sizes}
            doc["features"] = {k: v for k, v in merged.items() if v is not None}  # null = back to the world's choice
            if doc["features"].get("downtown") and doc["features"].get("normal"):  # the normal ring lies outside downtown
                doc["features"]["normal"] = max(doc["features"]["normal"], round(doc["features"]["downtown"] + 0.05, 3))
        if radius_m or core or features or (turn_deg is not None and str(turn_deg) != "") or \
                (industry_deg is not None and str(industry_deg) != ""):
            doc["districts"] = cg.districts(doc, world_opts)
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
        ter = scan_terrain(src) or {}
        if not ter.get("heights"):
            return {"ok": False, "error": f"{src!r} has no stored ground scan. Generate the world again."}
        res = cg.landscape_res(doc["generated"].get("options"))
        if res * res > cg.UEFN_MAX_SAMPLES:
            return {"ok": False, "error": f"Landscape resolution {res} x {res} ({res * res:,} samples) is above UEFN's "
                                          f"limit of 2048 x 2048 ({cg.UEFN_MAX_SAMPLES:,}): UEFN refuses to import it. "
                                          f"Set it to 2017 or lower (Terrain > Landscape) and generate again."}
        if not doc["generated"].get("heightmap"):  # from now on the buildings are fitted to this landscape
            doc["generated"]["heightmap"] = True
            data.put(f"net.{nid}", doc)
        hm = write_heightmap(nid, ter)  # always from the stored terrain, so every PC gets the same file
        # the roads this ground was cut for: a road pushed after they change would sit off its cut (reimport checks)
        doc["generated"]["heightmap_roads"] = roads_digest(nid)
        data.put(f"net.{nid}", doc)
        return {"ok": True, "id": nid, **hm, "ground_fit": fit_summary(),
                "how": f"Landscape mode > Manage > New > Import from File: pick the PNG, resolution {hm['resolution']}, "
                       f"Location (the centre) {hm['location']}, Scale {hm['scale']}, components {hm['components']}. "
                       f"Under Layers, give your road/dirt paint layer (not the grass one) the file {hm['paint_layer_path']}: "
                       f"every road, junction and building lot comes in painted with it. "
                       f"Z scale is {hm['scale'][2]} (the third Scale number: it holds the ground's full height). "
                       + (f"Give these ground layers their weightmaps (they sum to full): " + ", ".join(
                           f"{k} {v}" for k, v in hm["material_layer_paths"].items()) + ". "
                          if hm.get("material_layer_paths") else "")
                       + f"It makes a new landscape; your current one is not touched. "
                       f"Every building lot is already levelled into it (cut and fill), so buildings sit flat on hills."}

    def terrain_preview(name: str = "", seed: int = 7, size_km: float = 6.0, relief_m: float = 60.0,
                        center_x: float = 0.0, center_y: float = 0.0, options: str = "") -> dict:
        """Only the ground, for trying terrain settings in seconds: the world's cities, roads and saved terrain are
        not touched (Regenerate everything applies it). The cities that are there keep their ground flat as usual."""
        nid = slug(name)
        try:
            given = json.loads(options) if str(options or "").strip() else {}
        except ValueError as exc:
            return {"ok": False, "error": f"options is not valid JSON: {exc}"}
        last = (data.get(f"net.{nid}") or {}).get("generated") or {}
        if last.get("ground_from"):
            return {"ok": False, "error": "This world sits on a scanned landscape: its ground is never regenerated."}
        keep = cg.keep_zones(given.pop("keep") if "keep" in given else last.get("keep"))
        lakes = cg.lake_list(given.pop("lakes") if isinstance(given.get("lakes"), list) else last.get("lakes"))
        opts = cg.options({**last.get("options", {}), **given})
        size = max(1.0, min(20.0, float(size_km or 6))) * 100000.0
        towns = [{"name": c.get("name"), "x": float(c["x"]), "y": float(c["y"]), "radius": float(c.get("radius") or 45000)}
                 for c in city_docs(nid).values() if "x" in c and "y" in c] if nid else []
        ter = cg.terrain(int(seed), size, max(0.0, float(relief_m)) * 100, towns, grid=cg.GROUND_GRID,
                         center=(center_x, center_y), opts=opts, keep=keep, lakes=lakes)
        if ter.get("maps") and nid:  # the Debug looks show what is on screen
            engine_maps[nid] = cg.LAST_WORLD.maps
        return {"ok": True, "terrain": cg.panel_maps(ter), "cities": len(towns)}

    def terrain_map(net_id: str = "", name: str = "") -> dict:
        """The panel's Debug look: one engine map of a world (bare name: height, biome, river ...) as a base64 RGB
        PNG, row 0 = the grid's j 0. Every map from this session's last generate, else the few the world keeps."""
        nid = slug(net_id)
        ter = scan_terrain(nid) or {}
        if not ter.get("heights"):
            return {"ok": False, "error": f"{net_id!r} has no terrain. Generate the world first."}
        name = str(name or "").removeprefix("debug:")
        try:
            png = tex.debug_map({**ter, "maps": {**(ter.get("maps") or {}), **engine_maps.get(nid, {})}}, name)
        except KeyError:
            return {"ok": False, "error": f"This world has no {name!r} map: pick an engine preset (not Classic) and "
                                          f"generate again."}
        return {"ok": True, "name": name, "png": base64.b64encode(png).decode()}

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
        done, failed = {}, []
        for aslug, label, digest in todo:  # one road that fails never stops the others: it is pushed next time
            t0 = time.time()
            pieces = build_pieces(nid, label)
            t_pieces = round(time.time() - t0, 1)
            if not pieces.get("ok"):
                failed.append({"label": label, "error": str(pieces.get("error"))[:200]})
                continue
            out = reimport(nid, label, True)
            if not out.get("ok"):
                failed.append({"label": label, "error": str(out.get("error"))[:200]})
                continue
            head = data.get(f"net.{nid}.actor.{aslug}") or {}
            # the hash of what is in the level now (build_pieces may have placed junction meshes)
            _, pts, segs = load_actor(nid, aslug)
            data.put(f"net.{nid}.actor.{aslug}", {**head, "pushed": hashlib.sha1(st.serialize(pts, segs).encode()).hexdigest()})
            done[label] = {"junction_pieces": pieces.get("built", 0), "pieces": out.get("pieces"),
                           "seconds": {"junctions": t_pieces, "paste": round(time.time() - t0 - t_pieces, 1)}}
        if not done:
            return {"ok": False, "error": f"No road actor was pushed: {failed[0]['label']}: {failed[0]['error']}",
                    "failed": failed, "unchanged": same}
        return {"ok": True, "id": nid, "done": done, "failed": failed, "partial": bool(failed), "unchanged": same,
                "ground_paint": paint_ground()}

    def paint_ground() -> dict:
        """After a push: dirt paint under every road, junction and building, so no grass shows there.
        Paint only (never heights): the layer is rewritten to the current footprints, so moved or removed
        roads leave no dirt behind. A failure never fails the push."""
        try:
            return listener_long("road_network_paint_ground", {"layer": "Layer1", "margin_cm": 150.0})
        except (ConnectionError, RuntimeError, TimeoutError, ValueError) as exc:
            return {"ok": False, "error": f"Roads pushed; painting the ground under them failed: {exc}"[:300]}

    ctx.share(locals())

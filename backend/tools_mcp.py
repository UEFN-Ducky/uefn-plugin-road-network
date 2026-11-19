"""MCP tools: worlds, actors, generate, the pipeline, cities, holds, lakes, heightmap, import_all.

Handlers moved out of backend/__init__.py register(): ctx carries what the parts share."""

from __future__ import annotations

from . import citygen as cg
from . import kit_bake
from . import pcg_layers as pl
from . import pieces
from . import pipeline
from . import uefn_memory


def register(ctx) -> None:
    (actor, add_city, build_pieces, check, circle, city_docs, copy_text, data, delete_city,
     delete_network, delete_project, drape, generate, get_network, heightmap, hold, holds,
     holds_the_world, import_all, import_text, kit_convert, kit_materials, kit_root, layer_docs,
     list_cities, list_networks, list_projects, listener, move_point, panel_rpc, pipeline_run, pull,
     reimport, release, repair_actor, restore_actor, roads_pcg, safe, save_city, save_lake, save_network,
     save_project, scan_ground, slug, stats, terrain_preview, tool, tune_actor) = ctx.take(
        "actor add_city build_pieces check circle city_docs copy_text data delete_city delete_network "
        "delete_project drape generate get_network heightmap hold holds holds_the_world import_all "
        "import_text kit_convert kit_materials kit_root layer_docs list_cities list_networks list_projects "
        "listener move_point panel_rpc pipeline_run pull reimport release repair_actor restore_actor "
        "roads_pcg safe save_city save_lake save_network save_project scan_ground slug stats terrain_preview tool "
        "tune_actor")

    # ---------------------------------------------------------------- MCP tools
    INTENT = r"\b(road network|city roads?|road splines?|interchange|road pieces?)\b"

    @tool(intent=INTENT, listener=False)
    def road_network_projects() -> dict:
        """List the road network projects (each groups one city's networks)."""
        return safe(list_projects)()

    @tool(intent=INTENT, listener=False)
    def road_network_project_save(project_id: str = "", name: str = "", description: str = "") -> dict:
        """Create or rename a road network project."""
        return safe(save_project)(project_id, name, description)

    @tool(intent=INTENT, listener=False)
    def road_network_project_delete(project_id: str = "") -> dict:
        """Delete a road network project (its networks stay)."""
        return safe(delete_project)(project_id)

    @tool(intent=INTENT, listener=False)
    def road_network_list(project: str = "") -> dict:
        """List saved city road networks, optionally only one project's."""
        return safe(list_networks)(project)

    @tool(intent=INTENT, listener=False)
    def road_network_get(net_id: str = "") -> dict:
        """One road network: its settings and every road actor's piece counts."""
        return safe(get_network)(net_id)

    @tool(intent=INTENT, listener=False)
    def road_network_save(net_id: str = "", name: str = "", project: str = "", terrain_mode: str = "") -> dict:
        """Create or update a road network (name, project, terrain_mode 'live' or 'layer')."""
        return safe(save_network)(net_id, name, project, terrain_mode)

    @tool(intent=INTENT, listener=False)
    def road_network_delete(net_id: str = "") -> dict:
        """Delete a saved road network and all its stored roads (UEFN is not touched)."""
        return safe(delete_network)(net_id)

    @tool(intent=INTENT, listener=True)
    def road_network_pull(net_id: str = "", name: str = "", project: str = "", actors: str = "") -> dict:
        """Read the city's road actors from the open UEFN level into a road network (all, or a comma list of labels)."""
        return safe(pull)(net_id, name, project, actors)

    @tool(intent=INTENT, listener=False)
    def road_network_import_text(net_id: str = "", label: str = "", text: str = "") -> dict:
        """Add one road actor from spline clipboard text (BEGIN_SPLINES, copied in the Splines tool)."""
        return safe(import_text)(net_id, label, text)

    @tool(intent=INTENT, listener=False)
    def road_network_stats(net_id: str = "") -> dict:
        """Which pieces a road network uses: road meshes, junction pieces, kit pieces, length."""
        return safe(stats)(net_id)

    @tool(intent=INTENT, listener=False)
    def road_network_memory(last: int = 60, reset_guard: bool = False) -> dict:
        """UEFN's memory now and per editor call City Builder made (before / after / grew GB, seconds), the
        10 calls that grew it most, and whether the guard has stopped work (a call grew UEFN 6+ GB, or UEFN is
        above 80 % of RAM). reset_guard=true clears a trip once UEFN has been saved / restarted."""
        if reset_guard:
            uefn_memory._state["trip"] = None
        return {"ok": True, **uefn_memory.report(int(last))}

    @tool(intent=INTENT, listener=False)
    def road_network_move_point(net_id: str = "", actor: str = "", index: int = -1, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> dict:
        """Move one spline control point of a road actor to x, y, z (cm); attached tangents follow."""
        return safe(move_point)(net_id, actor, index, x, y, z)

    @tool(intent=INTENT, listener=False)
    def road_network_check(net_id: str = "", actor: str = "") -> dict:
        """Find broken road data before it reaches UEFN: giant junction nodes (Width over 80 m),
        join links over 80 m and runaway tangents."""
        return safe(check)(net_id, actor)

    @tool(intent=INTENT, listener=False)
    def road_network_repair(net_id: str = "", actor: str = "") -> dict:
        """Fix what road_network_check finds: drop the over-long join links and strip the giant junction
        piece off those nodes (they become plain shared points with a road width). Offline."""
        return safe(repair_actor)(net_id, actor)

    @tool(intent=INTENT, listener=False)
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

    @tool(intent=INTENT, listener=False)
    def road_network_kit_convert(net_id: str = "", actor: str = "") -> dict:
        """Move a road actor onto the Fortnite Road1 kit (/Game/Creative/Environments/Meshes/Roads): tiles
        become Road1_Straight, fitting 3-way junctions become T_Section / Y_Split / Split with their mouths on
        the kit ends. Lists the 4+ way and unfit nodes that still need generated pieces. Offline."""
        return safe(kit_convert)(net_id, actor)

    @tool(intent=INTENT, listener=True)
    def road_network_build_pieces(net_id: str = "", actor: str = "", rebuild: bool = False) -> dict:
        """Build plain-asphalt junction meshes (GeometryScript, in the project's WorldGen/Roads/Junctions/Kit
        folder) for every junction that has no piece, and put them on the nodes. With our own road kit they
        are flat M_Asphalt at deck height; with the Fortnite kit, Road1 crowned. rebuild=true regenerates the
        existing pieces in place (after a kit or material change). Mesh assets only; never the landscape."""
        return safe(build_pieces)(net_id, actor, rebuild)

    @tool(intent=INTENT, listener=True)
    def road_network_kit_materials(net_id: str = "", street_kit: bool = True) -> dict:
        """Give the own kit (WorldGen/City + WorldGen/Kit meshes) textured materials that work on PCG
        instances: one MI of M_City_WorldPBR (world-aligned PBR, ISM + Nanite usage on) per old flat-colour
        material, its colour kept as Tint. pcg_build runs it first; run it alone after importing new kit meshes.
        street_kit=false skips (re)building the street kit and only fixes the kit meshes' materials."""
        return safe(kit_materials)(net_id, street_kit)

    @tool(intent=INTENT, listener=False)
    def road_network_restore(net_id: str = "", actor: str = "", from_actor: str = "") -> dict:
        """Put a saved version of a road actor back (for example from_actor='City Roads before kit'). Offline;
        reimport to apply."""
        return safe(restore_actor)(net_id, actor, from_actor)

    @tool(intent=INTENT, listener=False)
    def road_network_circle(net_id: str = "", actor: str = "", nodes: str = "", cx: float = 0.0, cy: float = 0.0,
                            fit_center: bool = True, radius: float = 0.0) -> dict:
        """Make a ring road a true circle. nodes: the ring's junction point ids (comma list). The centre is
        fitted through them (fit_center) or given as cx, cy; radius 0 = their mean distance. Each stretch
        between two ring junctions becomes one arc segment. Offline; reimport to apply."""
        return safe(circle)(net_id, actor, nodes, cx, cy, fit_center, radius)

    @tool(intent=INTENT, listener=True)
    def road_network_scan_ground(net_id: str = "", grid: int = 0) -> dict:
        """Pull the terrain from the open UEFN level into the world (read-only, heightfield only), so City
        Builder stands everything on the ground exactly as it was shaped in UEFN. grid: samples a side
        (default 1024, max 2048), read in bands."""
        return safe(scan_ground)(net_id, grid)

    @tool(intent=INTENT, listener=False)
    def road_network_drape(net_id: str = "", actor: str = "", smooth: int = 4, all_points: bool = False) -> dict:
        """Put a road actor's live points onto the base ground (smoothed along the roads, junctions kept flat).
        all_points=true also drapes ground-level kit pieces and bridge ends (never use it on elevated decks).
        Works offline on the stored terrain scan: never touches UEFN or the landscape."""
        return safe(drape)(net_id, actor, smooth, all_points)

    @tool(intent=INTENT, listener=False)
    def road_network_copy_text(net_id: str = "", actor: str = "") -> dict:
        """Put one road actor's spline text on the clipboard for a manual paste in the Splines tool."""
        return safe(copy_text)(net_id, actor)

    @tool(intent=INTENT, listener=True)
    def road_network_roads_pcg(net_id: str = "", confirm: bool = False, force: bool = False, actors: str = "") -> dict:
        """Push the roads as PCG (the way to put roads in UEFN): every road actor becomes spline meshes bent along
        it with BlockAll collision, plus its junction / interchange pieces, in 800 m road tiles labelled
        'City Builder Roads <world> <tile>'. No landscape splines, no clipboard; the terrain must already be graded
        to the roads (import the world's heightmap first). confirm=false is a dry run; only changed tiles rebuild,
        force=true rebuilds all; actors = a comma list to push only those."""
        return safe(roads_pcg)(net_id, confirm, force, actors)

    @tool(intent=INTENT, listener=True)
    def road_network_reimport(net_id: str = "", actor: str = "", confirm: bool = False) -> dict:
        """Replace a road actor in the open UEFN level with the network's version: the old actor is
        removed and the new one pasted as live splines (the terrain itself is never written)."""
        return safe(reimport)(net_id, actor, confirm)

    GEN_INTENT = r"\b(generate|auto.?generat\w*|procedural)\b.*\b(city|cities|roads?|terrain|heightmap|world)\b"

    @tool(intent=GEN_INTENT, listener=False)
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
        flatten_m, sea_m (lake level, -1000 = off), terrace_m, smooth (0-1: rounds the
        hills off), landscape_px (heightmap pixels a side, default 2017: UEFN takes at most 2048 x 2048); roads block_m (a block's short side), block_ratio
        (1 = square blocks, up to 3 = three times as long as wide), wobble (0-0.45: 0 = dead straight streets,
        higher = the streets between main roads bow in smooth curves), green_lots (0-0.6: share of every block's
        lots planted with trees instead of built on), prune (0-0.5), downtown / normal (ring shares), highway
        ('4L' or '2L'), highway_curve. Left-out
        options keep the world's last values. Offline: never touches UEFN or any terrain."""
        return safe(generate)(name, seed, cities, size_km, relief_m, ground_from, folder, center_x, center_y, options)

    @tool(intent=GEN_INTENT, listener=False)
    def road_network_generate_options() -> dict:
        """Every generate option with its default and range (what the Terrain and Roads tabs show), and the
        design `rules` City Builder follows (where interchanges go, bridge heights, keep zones, lots, forests):
        an AI building a world with this plugin follows these rules, never its own."""
        return {"ok": True, "options": {k: {"default": d, "min": lo, "max": hi} for k, (d, lo, hi) in cg.OPTIONS.items()},
                "highway": list(cg.HIGHWAYS), "styles": list(cg.STYLES), "rules": cg.RULES}

    @tool(intent=GEN_INTENT, listener=False)
    def road_network_pipeline(net_id: str = "", run_from: str = "", confirm: bool = False) -> dict:
        """The city as separate systems, each consuming the one before (backend/pipeline.py): TerrainAnalyzer
        (heightmap, slope map, obstacles) -> UrbanPlanner (districts, orientation, density) -> RoadGenerator
        (graph, intersections, graded profiles) -> BlockGenerator (lots) -> FoundationGenerator (pads, cut/fill,
        retaining walls) -> BuildingGenerator (footprints, buildings, entrances) -> SidewalkGenerator (sidewalks,
        walkways, driveways as splines) -> RoadMeshGenerator (junction meshes + paste, in UEFN: Push roads).
        Without run_from: each stage's status. run_from=<stage id> (+confirm) re-runs it and everything
        downstream, never what came before (e.g. 'buildings' keeps the roads, 'sidewalks' only the paths)."""
        return safe(pipeline_run)(net_id, run_from, confirm)

    @tool(intent=GEN_INTENT, listener=False)
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

    @tool(intent=GEN_INTENT, listener=False)
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

    @tool(intent=GEN_INTENT, listener=False)
    def road_network_holds(net_id: str = "") -> dict:
        """Who is working on which city of a world right now: [{city, who, kind (person / ducky / teammate), mine,
        task, left_s}]. A city someone else holds can't be changed: road_network_city_save, city_delete and a
        generate that changes the world's settings are refused and say whose it is. A person holds the city they
        have picked in the panel; a ducky holds a city for two minutes from each change it makes to it; a person's
        own ducky may change the city that person has open. Look here before planning changes to several cities."""
        return safe(holds)(net_id)

    @tool(intent=GEN_INTENT, listener=False)
    def road_network_hold(net_id: str = "", city: str = "", task: str = "", minutes: float = 0.0) -> dict:
        """Take a city before a longer job on it, so no one else changes it meanwhile: task says what you are doing
        in a few words (everyone sees it: "Builder (Ilya's ducky) · re-planning the harbour"), minutes how long
        (default 2; every change you make to the city starts the two minutes again, so plain edits need no call).
        Refused, with the holder's name, while someone else has the city. Let go with road_network_release."""
        return safe(hold)(net_id, city, task, minutes)

    @tool(intent=GEN_INTENT, listener=False)
    def road_network_release(net_id: str = "", city: str = "") -> dict:
        """Let go of a city you hold (road_network_hold, or the hold your last change to it took), so the next
        person or ducky can change it at once instead of after two minutes."""
        return safe(release)(net_id, city)

    @tool(intent=GEN_INTENT, listener=False)
    def road_network_cities(net_id: str = "") -> dict:
        """The cities of a generated world: centre, radius, road style, district rings and building sets."""
        return safe(list_cities)(net_id)

    @tool(intent=GEN_INTENT, listener=False)
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

    @tool(intent=GEN_INTENT, listener=False)
    def road_network_city_add(net_id: str = "", name: str = "", x: float | None = None, y: float | None = None,
                              radius_m: float = 0.0, style: str = "") -> dict:
        """Add a city to a generated world: at x, y (cm, UE world) or, without them, on a free spot of the map;
        radius_m (default: the world's city size); style mixed / grid / organic. Then generate again (cities
        empty): it gets its own roads, services and waterfront, and highways join it to the other cities."""
        return safe(add_city)(net_id, name, x, y, radius_m, style)

    @tool(intent=GEN_INTENT, listener=False)
    def road_network_city_delete(net_id: str = "", city: str = "", confirm: bool = False) -> dict:
        """Remove a city (and everything on it) from a generated world; confirm=true. Then generate again: the
        other cities' highways re-join without it. Removing the last city leaves the world terrain only."""
        return safe(delete_city)(net_id, city, confirm)

    @tool(intent=GEN_INTENT, listener=False)
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

    @tool(intent=GEN_INTENT, listener=False)
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

    @tool(intent=GEN_INTENT, listener=False)
    def road_network_city_save(net_id: str = "", city: str = "", name: str = "", style: str = "",
                               radius_m: float = 0.0, buildings: str = "", x: float | None = None,
                               y: float | None = None, features: str = "", turn_deg: float | None = None,
                               shape: str = "", zones: str = "", core: str = "",
                               industry_deg: float | None = None) -> dict:
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

    @tool(intent=GEN_INTENT, listener=False)
    def road_network_lake(net_id: str = "", lake_id: str = "", points: str = "", width_m: float = 0.0,
                          remove: bool = False) -> dict:
        """Draw a lake on a generated world, change one, or take one away (remove=true). points: JSON [[x, y], ...]
        in cm (UE world) along the lake's middle, as many as its shape needs (one point: a round pond); width_m:
        how wide it is (60 to 1500, a new one 200). lake_id: the lake to change (empty: a new one; the world's
        lakes are in road_network_describe / its generated settings). The lake is dug into the world's own terrain
        down to its water line, with sloping banks; a world with no water gets a water line under its lowest
        ground. Then generate again with cities empty: the streets, bridges, waterfront and buildings of every
        city are rebuilt round it. Not for a world laid over a landscape scanned from UEFN."""
        return safe(holds_the_world(save_lake))(net_id, lake_id, points, width_m, remove)

    @tool(intent=GEN_INTENT, listener=False)
    def road_network_heightmap(net_id: str = "") -> dict:
        """Where a generated world's heightmap PNG is and the Landscape import settings (resolution,
        location, scale) to bring it in as a new landscape."""
        return safe(heightmap)(net_id)

    @tool(intent=GEN_INTENT, listener=True)
    def road_network_import_all(net_id: str = "", confirm: bool = False, force: bool = False) -> dict:
        """Push roads into UEFN: build junction pieces, then paste every road actor that CHANGED since its last
        push (or is missing from the level) as live splines; unchanged actors are left alone. force=true pushes
        all. Ask the person first and keep their hands off UEFN while it runs."""
        return safe(import_all)(net_id, confirm, force)

    panel_rpc("terrain_preview", lambda name="", seed=7, size_km=6.0, relief_m=60.0, center_x=0.0, center_y=0.0,
              options="": safe(terrain_preview)(name, seed, size_km, relief_m, center_x, center_y, options))
    panel_rpc("generate", lambda name="", seed=7, cities="", size_km=6.0, relief_m=60.0, ground_from="",
                           folder="", center_x=0.0, center_y=0.0, options="": safe(generate)(
                               name, seed, cities, size_km, relief_m, ground_from, folder, center_x, center_y, options))
    panel_rpc("generate_options", lambda: road_network_generate_options())

    ctx.share(locals())

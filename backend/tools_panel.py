"""Workflow nodes and the remaining panel RPCs.

Handlers moved out of backend/__init__.py register(): ctx carries what the parts share."""

from __future__ import annotations



def register(ctx) -> None:
    (INTENT, actor, api, build_pieces, check, copy_text, delete_network, drape, generate, geometry,
     get_network, heightmap, import_all, kit_convert, kit_materials, list_networks, list_projects,
     move_point, panel_rpc, pcg_plan, pull, reimport, repair_actor, safe, save_network, save_project,
     scan_ground, stats, tool, tune_actor) = ctx.take(
        "INTENT actor api build_pieces check copy_text delete_network drape generate geometry get_network "
        "heightmap import_all kit_convert kit_materials list_networks list_projects move_point panel_rpc "
        "pcg_plan pull reimport repair_actor safe save_network save_project scan_ground stats tool tune_actor")

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
    panel_rpc("projects", lambda: safe(list_projects)())
    panel_rpc("project_save", lambda project_id="", name="", description="": safe(save_project)(project_id, name, description))
    panel_rpc("list", lambda project="": safe(list_networks)(project))
    panel_rpc("get", lambda net_id="": safe(get_network)(net_id))
    panel_rpc("save", lambda net_id="", name="", project="", terrain_mode="": safe(save_network)(net_id, name, project, terrain_mode))
    panel_rpc("delete", lambda net_id="": safe(delete_network)(net_id))
    panel_rpc("pull", lambda net_id="", name="", project="", actors="": safe(pull)(net_id, name, project, actors))
    panel_rpc("stats", lambda net_id="": safe(stats)(net_id))
    panel_rpc("geometry", lambda net_id="", actor="", offset=0, limit=0, terrain=True:
                           safe(geometry)(net_id, actor, offset, limit, terrain))
    panel_rpc("move_point", lambda net_id="", actor="", index=-1, x=0.0, y=0.0, z=0.0: safe(move_point)(net_id, actor, index, x, y, z))
    panel_rpc("tune", lambda net_id="", actor="", thin_tolerance_cm=50.0, side_falloff=800.0,
                           end_falloff=1000.0, width=0.0, paint_layer="", mesh_offset_cm=-1.0: safe(tune_actor)(
                               net_id, actor, thin_tolerance_cm, side_falloff, end_falloff, width, paint_layer,
                               mesh_offset_cm))
    panel_rpc("build_pieces", lambda net_id="", actor="", rebuild=False: safe(build_pieces)(net_id, actor, rebuild))
    panel_rpc("kit_materials", lambda net_id="", street_kit=True: safe(kit_materials)(net_id, street_kit))
    panel_rpc("kit_convert", lambda net_id="", actor="": safe(kit_convert)(net_id, actor))
    panel_rpc("check", lambda net_id="", actor="": safe(check)(net_id, actor))
    panel_rpc("repair", lambda net_id="", actor="": safe(repair_actor)(net_id, actor))
    panel_rpc("scan_ground", lambda net_id="", grid=200: safe(scan_ground)(net_id, grid))
    panel_rpc("drape", lambda net_id="", actor="", smooth=4, all_points=False: safe(drape)(net_id, actor, smooth, all_points))
    panel_rpc("copy_text", lambda net_id="", actor="": safe(copy_text)(net_id, actor))
    panel_rpc("reimport", lambda net_id="", actor="", confirm=False: safe(reimport)(net_id, actor, confirm))

    @tool(intent=INTENT, listener=False)
    def road_network_geometry(net_id: str = "", actor: str = "", offset: int = 0, limit: int = 0,
                              terrain: bool = True) -> dict:
        """Control points and segments of a road network (what the 3D view draws); limit pages one actor."""
        return safe(geometry)(net_id, actor, offset, limit, terrain)

    ctx.share(locals())

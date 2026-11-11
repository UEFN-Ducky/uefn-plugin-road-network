"""Road Network: read and shape UEFN landscape spline road actors.

Runs inside UEFN's Python. Landscape spline control points are not readable from
Python, so a road actor's full definition travels as its BEGIN_SPLINES clipboard text
(the text the Splines tool copies and pastes). These handlers hand that text out in
chunks, list the road actors and their pieces and sample the terrain for the 3D view.
Nothing here writes the landscape.
"""

from __future__ import annotations

import math

import unreal

from listener.dispatch import register

_LANDSCAPE_CLASSES = ("LandscapeStreamingProxy", "Landscape", "LandscapeProxy")
_TEXT_CACHE: dict = {}


def _world():
    return unreal.EditorLevelLibrary.get_editor_world()


def _spline_actors():
    return list(unreal.GameplayStatics.get_all_actors_of_class(_world(), unreal.LandscapeSplineActor))


def _ground(x: float, y: float, ignore: list):
    """Landscape height at x, y (cm). Read-only.

    Only a hit on the landscape's own heightfield collision counts. Anything else in the
    way (roads, props, the water surface, which has no owning actor) is skipped: its actor
    is ignored, or the trace restarts just below it.
    """
    w = _world()
    top = 90000.0
    for _ in range(16):
        hit = unreal.SystemLibrary.line_trace_single(
            w, unreal.Vector(x, y, top), unreal.Vector(x, y, -60000),
            unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, False, ignore,
            unreal.DrawDebugTrace.NONE, True)
        if hit is None:  # nothing under x, y (off the landscape)
            return None
        hit = hit.to_tuple()
        if not hit[0]:
            return None
        comp, actor = hit[10], hit[9]
        if comp is not None and comp.get_class().get_name() == "LandscapeHeightfieldCollisionComponent":
            return hit[4].z
        if actor is not None and actor.get_class().get_name() not in _LANDSCAPE_CLASSES:
            ignore.append(actor)
        else:
            top = hit[4].z - 5.0
    return None


def _generated_text(label: str) -> str:
    """Spline text for an actor this session generated (City Roads and friends)."""
    if label == "City Roads":
        gen = getattr(unreal, "_J", {}).get("gen_live") if isinstance(getattr(unreal, "_J", None), dict) else None
        if gen:
            return gen()
    for attr, name in (("_elev_txt2", "Elevated Freeways"), ("_stack_txt", "Stack Interchange")):
        if name == label and getattr(unreal, attr, None):
            return getattr(unreal, attr)
    return ""


@register("road_network_actors")
def road_network_actors() -> dict:
    """Every landscape spline actor: label, path, piece counts and whether its text is known."""
    rows = []
    for actor in _spline_actors():
        comps = actor.get_components_by_class(unreal.StaticMeshComponent)
        spline = [c for c in comps if isinstance(c, unreal.SplineMeshComponent)]
        label = actor.get_actor_label()
        rows.append({
            "label": label,
            "path": actor.get_path_name(),
            "spline_pieces": len(spline),
            "point_meshes": len(comps) - len(spline),
            "has_text": bool(_TEXT_CACHE.get(label) or _generated_text(label)),
        })
    return {"ok": True, "actors": rows}


@register("road_network_text_chunk")
def road_network_text_chunk(label: str = "", offset: int = 0, size: int = 700000) -> dict:
    """One chunk of an actor's BEGIN_SPLINES text. offset 0 refreshes the cached text."""
    if not label:
        return {"ok": False, "error": "Give the road actor's label."}
    if offset == 0 or label not in _TEXT_CACHE:
        _TEXT_CACHE[label] = _generated_text(label)
    text = _TEXT_CACHE.get(label) or ""
    if not text:
        return {"ok": False, "error": f"No spline text known for {label!r}. Copy it in the Splines tool (Ctrl+C) and import it as text."}
    size = max(10000, min(int(size or 700000), 900000))
    return {"ok": True, "label": label, "total": len(text), "offset": offset, "text": text[offset:offset + size]}


@register("road_network_scan")
def road_network_scan(grid: int = 96, folder: str = "City/Elevated Pillars") -> dict:
    """Pillars and other placed road props, plus a terrain height grid for the 3D view."""
    w = _world()
    props = []
    for actor in unreal.GameplayStatics.get_all_actors_of_class(w, unreal.StaticMeshActor):
        try:
            path = str(actor.get_folder_path())
        except Exception:
            path = ""
        if not path.startswith(folder):
            continue
        comp = actor.get_component_by_class(unreal.StaticMeshComponent)
        mesh = comp.static_mesh.get_name() if comp and comp.static_mesh else ""
        loc = actor.get_actor_location()
        rot = actor.get_actor_rotation()
        props.append({"label": actor.get_actor_label(), "mesh": mesh,
                      "x": loc.x, "y": loc.y, "z": loc.z, "yaw": rot.yaw})
    lo = [1e18, 1e18]
    hi = [-1e18, -1e18]
    for proxy in unreal.GameplayStatics.get_all_actors_of_class(w, unreal.LandscapeStreamingProxy):
        origin, extent = proxy.get_actor_bounds(False)
        lo = [min(lo[0], origin.x - extent.x), min(lo[1], origin.y - extent.y)]
        hi = [max(hi[0], origin.x + extent.x), max(hi[1], origin.y + extent.y)]
    heights = []
    grid = max(16, min(int(grid or 200), 256))
    if hi[0] > lo[0]:
        ignore: list = []
        for j in range(grid):
            row = []
            for i in range(grid):
                x = lo[0] + (hi[0] - lo[0]) * (i + 0.5) / grid
                y = lo[1] + (hi[1] - lo[1]) * (j + 0.5) / grid
                g = _ground(x, y, ignore)
                row.append(round(g, 1) if g is not None else None)
            heights.append(row)
    return {"ok": True, "props": props,
            "terrain": {"min": lo, "max": hi, "grid": grid, "heights": heights}}


@register("road_network_count")
def road_network_count(label: str = "") -> dict:
    """Spline mesh pieces in the actor with this label (0 when it doesn't exist)."""
    for actor in _spline_actors():
        if actor.get_actor_label() == label:
            comps = actor.get_components_by_class(unreal.StaticMeshComponent)
            return {"ok": True, "label": label, "path": actor.get_path_name(), "pieces": len(comps)}
    return {"ok": True, "label": label, "path": "", "pieces": 0}



# Road1 cross-section (Fortnite kit, measured): offset across the road -> height, and the
# texture U that is plain lane asphalt / shoulder in MI_Apollo_Roads_Straight_NO_RVT.
_R1_PROFILE = [(-545, -2), (-461, 8), (-347, 14), (-192, 15), (0, 16), (192, 15), (347, 14), (461, 8), (545, -2)]
_R1_W, _R1_SKIRT, _R1_SKIRT_Z = 545.0, 50.0, -21.0
_U_ASPHALT, _U_SHOULDER = 0.27, 0.02
_R1_MATERIAL = "/Game/Athena/Apollo/Environments/Landscape/Materials/MI_Apollo_Roads_Straight_NO_RVT"


# Our own road kit (WorldGen/Roads): a flat deck at z 0 in M_Asphalt. A junction for it is flat too,
# a hair above the deck so the road ends it overlaps never flicker, with planar UVs in metres.
_OWN_DECK_Z = 2.0
_OWN_UV_CM = 400.0


def _junction_geometry(arm_deg: list, half: float = _R1_W, flat: bool = False) -> tuple:
    """Plain-asphalt junction for arms at these world angles (degrees), as wide as the roads it joins
    (half: road half width, cm). flat: our own kit's flat deck and planar UVs; otherwise the Fortnite
    Road1 crown and its texture strip. Returns (verts, uvs, tris, mouth distance per arm in the given order)."""
    k_w = max(0.5, float(half) / _R1_W)
    _R1_W_ = _R1_W * k_w
    profile = [(s * k_w, _OWN_DECK_Z if flat else z) for s, z in _R1_PROFILE]
    edge_z = _OWN_DECK_Z if flat else -2.0

    def uv(x, y, u_strip):
        return (x / _OWN_UV_CM, y / _OWN_UV_CM) if flat else (u_strip, x / 1024.0)
    order = sorted(range(len(arm_deg)), key=lambda k: arm_deg[k] % 360.0)
    ang = [math.radians(arm_deg[k] % 360.0) for k in order]
    n = len(ang)
    U = [(math.cos(a), math.sin(a)) for a in ang]
    N = [(-math.sin(a), math.cos(a)) for a in ang]
    gap = [((ang[(k + 1) % n] - ang[k]) % (2 * math.pi)) or 2 * math.pi for k in range(n)]
    need = [0.0] * n
    for k in range(n):
        g = gap[k]
        c = _R1_W_ / math.tan(g / 2) + 300.0 if g < math.radians(179) else 0.0
        need[k] = max(need[k], c)
        need[(k + 1) % n] = max(need[(k + 1) % n], c)
    d = [max(700.0, min(3000.0, x)) for x in need]

    def pt(k, along, side):
        return (U[k][0] * along + N[k][0] * side, U[k][1] * along + N[k][1] * side)

    ring = []  # (x, y, z, is_fillet_chain)
    chains = []
    for k in range(n):
        for s, z in profile:
            x, y = pt(k, d[k], s)
            ring.append((x, y, z))
        j = (k + 1) % n
        L, R = pt(k, d[k], _R1_W_), pt(j, d[j], -_R1_W_)
        g = gap[k]
        if g < math.radians(179):
            c = _R1_W_ / math.tan(g / 2)
            C = pt(k, c, _R1_W_)
        else:
            C = ((L[0] + R[0]) / 2, (L[1] + R[1]) / 2)
        chain = [L]
        for t in (1 / 6, 2 / 6, 3 / 6, 4 / 6, 5 / 6):
            a, b, cc = (1 - t) ** 2, 2 * (1 - t) * t, t * t
            q = (a * L[0] + b * C[0] + cc * R[0], a * L[1] + b * C[1] + cc * R[1])
            chain.append(q)
            ring.append((q[0], q[1], edge_z))
        chain.append(R)
        chains.append((chain, k, j))
    verts, uvs, tris = [(0.0, 0.0, _OWN_DECK_Z if flat else 16.0)], [uv(0.0, 0.0, _U_ASPHALT)], []
    for x, y, z in ring:
        verts.append((x, y, z))
        uvs.append(uv(x, y, _U_ASPHALT))
    m = len(ring)
    for i in range(m):
        a, b = 1 + i, 1 + (i + 1) % m
        tris.append((0, b, a))  # ring runs counter-clockwise in x/y: (centre, next, this) faces up in UE
    for chain, k, j in chains:
        outs = []
        for i, p in enumerate(chain):
            if i == 0:
                o = N[k]
            elif i == len(chain) - 1:
                o = (-N[j][0], -N[j][1])
            else:
                tx, ty = chain[i + 1][0] - chain[i - 1][0], chain[i + 1][1] - chain[i - 1][1]
                L2 = math.hypot(tx, ty) or 1.0
                o = (ty / L2, -tx / L2)
                if o[0] * p[0] + o[1] * p[1] < 0:
                    o = (-o[0], -o[1])
            outs.append((p[0] + o[0] * _R1_SKIRT, p[1] + o[1] * _R1_SKIRT))
        for side in (0, 1):  # both faces, separate vertices (shared ones cancel the normals)
            for i in range(len(chain) - 1):
                base = len(verts)
                for (x, y), z in ((chain[i], edge_z), (chain[i + 1], edge_z), (outs[i + 1], _R1_SKIRT_Z), (outs[i], _R1_SKIRT_Z)):
                    verts.append((x, y, z))
                    uvs.append(uv(x, y, _U_SHOULDER))
                q = (base, base + 1, base + 2, base + 3)
                tris += ([(q[0], q[1], q[2]), (q[0], q[2], q[3])] if side == 0 else [(q[0], q[2], q[1]), (q[0], q[3], q[2])])
    mouth = [0.0] * n
    for idx, k in enumerate(order):
        mouth[k] = d[idx]
    return verts, uvs, tris, mouth


@register("road_network_build_junctions")
def road_network_build_junctions(folder: str = "", pieces: list | None = None) -> dict:
    """Create or update plain-asphalt Road1 junction meshes. pieces: [{name, arms: [world degrees]}].
    Writes mesh assets only (never the landscape). Returns each piece's mouth distance per arm."""
    if not folder.startswith("/"):
        return {"ok": False, "error": "Give the asset folder (for example /<mount>/MovieTheater/WorldGen/Roads/Junctions/Kit)."}
    # our own road kit: the junction is the same flat asphalt as the road deck it joins
    own = folder.split("/WorldGen/Roads")[0] + "/WorldGen/Roads/M_Asphalt" if "/WorldGen/Roads" in folder else ""
    flat = bool(own) and unreal.EditorAssetLibrary.does_asset_exist(own)
    mat = unreal.load_asset(own if flat else _R1_MATERIAL)
    out, errors = {}, []
    for piece in pieces or []:
        name, arms = str(piece.get("name") or ""), list(piece.get("arms") or [])
        if not name or len(arms) < 3:
            errors.append(f"{name or '?'}: needs a name and 3+ arms")
            continue
        verts, uvs, tris, mouth = _junction_geometry(arms, float(piece.get("half") or _R1_W), flat)
        buf = unreal.GeometryScriptSimpleMeshBuffers()
        buf.vertices = [unreal.Vector(*v) for v in verts]
        buf.uv0 = [unreal.Vector2D(*u) for u in uvs]
        buf.triangles = [unreal.IntVector(*t) for t in tris]
        dm = unreal.DynamicMesh()
        unreal.GeometryScript_Materials.enable_material_i_ds(dm)
        unreal.GeometryScript_MeshEdits.append_buffers_to_mesh(dm, buf, 0)
        unreal.GeometryScript_Normals.compute_split_normals(dm, unreal.GeometryScriptSplitNormalsOptions(),
                                                           unreal.GeometryScriptCalculateNormalsOptions())
        path = f"{folder}/{name}"
        sm = unreal.load_asset(path) if unreal.EditorAssetLibrary.does_asset_exist(path) else None
        if sm is None:
            res = unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(
                dm, path, unreal.GeometryScriptCreateNewStaticMeshAssetOptions())
            sm = res[0] if isinstance(res, tuple) else res
        opts = unreal.GeometryScriptCopyMeshToAssetOptionsV2()
        opts.replace_materials = True
        opts.new_materials = [mat]
        opts.new_material_slot_names = ["Asphalt" if flat else "Road1"]
        lod = unreal.GeometryScriptMeshWriteLOD()
        unreal.GeometryScript_AssetUtils.copy_mesh_to_static_mesh_v2(dm, sm, opts, lod)
        out[name] = mouth
    return {"ok": not errors, "built": len(out), "mouths": out, "errors": errors[:20]}


@register("road_network_ground_check")
def road_network_ground_check(points: list | None = None) -> dict:
    """Ground height under each [x, y, z] point, and how far below the point it is."""
    ignore: list = []
    out = []
    for p in points or []:
        g = _ground(p[0], p[1], ignore)
        out.append({"x": p[0], "y": p[1], "z": p[2], "ground": g,
                    "below": (p[2] - g) if g is not None else None})
    return {"ok": True, "points": out}


# ------------------------------------------------------------------ kit materials
# The kit came in from FBX with one flat colour per material (no textures) and none of them is
# allowed on instanced meshes, so PCG's instances drew the grey checker. Every kit slot gets an
# instance of one world-aligned PBR master instead: textures projected by world position (a lot
# stretched to fit never smears them), the old colour kept as Tint, usage flags for ISM/Nanite on.
_MASTER = "M_City_WorldPBR"
_PBR_SET = "{root}/Cinema/PBR/Textures/T_CinemaPBR_{set}_{ch}"
# material name keyword -> (texture set, tile cm, roughness x, metallic, emissive)
_LOOKS = [
    ("Glass", ("Tile", 300, 0.15, 0.4, 0.0)), ("Neon", ("Concrete", 400, 0.5, 0.0, 6.0)),
    ("LampLED", ("Concrete", 400, 0.5, 0.0, 6.0)), ("Brick", ("Pavers", 150, 1.0, 0.0, 0.0)),
    ("Siding", ("Wood", 250, 1.0, 0.0, 0.0)), ("DoorWood", ("Oak", 200, 1.0, 0.0, 0.0)),
    ("Wood", ("Oak", 200, 1.0, 0.0, 0.0)), ("Roof", ("Tile", 200, 1.0, 0.0, 0.0)),
    ("Trim", ("Stone", 200, 1.0, 0.0, 0.0)), ("Stone", ("Stone", 300, 1.0, 0.0, 0.0)),
    ("Gold", ("Frame", 200, 0.5, 0.9, 0.0)), ("Metal", ("Frame", 200, 0.6, 0.7, 0.0)),
    ("Spire", ("Frame", 200, 0.5, 0.6, 0.0)), ("Mullion", ("Frame", 200, 0.6, 0.6, 0.0)),
    ("Frame", ("Frame", 200, 0.8, 0.3, 0.0)), ("Foliage", ("Rock", 150, 1.0, 0.0, 0.0)),
    ("Floor", ("Concrete", 300, 1.0, 0.0, 0.0)), ("Concrete", ("Concrete", 400, 1.0, 0.0, 0.0)),
]
_DEFAULT_LOOK = ("Concrete", 400, 1.0, 0.0, 0.0)


def _look(name: str) -> tuple:
    return next((v for k, v in _LOOKS if k.lower() in name.lower()), _DEFAULT_LOOK)


def _world_pbr_master(folder: str, root: str):
    path = f"{folder}/{_MASTER}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        return unreal.load_asset(path)
    mel = unreal.MaterialEditingLibrary
    m = unreal.AssetToolsHelpers.get_asset_tools().create_asset(_MASTER, folder, unreal.Material, unreal.MaterialFactoryNew())

    def node(cls, x, y, **props):
        e = mel.create_material_expression(m, cls, x, y)
        for k, v in props.items():
            e.set_editor_property(k, v)
        return e

    def wire(a, b, out="", inp=""):
        mel.connect_material_expressions(a, out, b, inp)

    def param(name, value, y):
        return node(unreal.MaterialExpressionScalarParameter, -400, y, parameter_name=name, default_value=value)

    wp = node(unreal.MaterialExpressionWorldPosition, -2200, 0)
    tile = param("TileCm", 400.0, 160)
    p = node(unreal.MaterialExpressionDivide, -2000, 0)
    wire(wp, p, inp="A")
    wire(tile, p, inp="B")
    uvs = {}
    for k, (key, rgb) in enumerate((("yz", (False, True, True)), ("xz", (True, False, True)), ("xy", (True, True, False)))):
        cm = node(unreal.MaterialExpressionComponentMask, -1800, k * 120, r=rgb[0], g=rgb[1], b=rgb[2], a=False)
        wire(p, cm)
        uvs[key] = cm
    nrm = node(unreal.MaterialExpressionVertexNormalWS, -2200, 500)
    ab = node(unreal.MaterialExpressionAbs, -2000, 500)
    wire(nrm, ab)
    sq = node(unreal.MaterialExpressionMultiply, -1800, 500)
    wire(ab, sq, inp="A")
    wire(ab, sq, inp="B")
    one = node(unreal.MaterialExpressionConstant3Vector, -1800, 640, constant=unreal.LinearColor(1, 1, 1, 1))
    tot = node(unreal.MaterialExpressionDotProduct, -1600, 560)
    wire(sq, tot, inp="A")
    wire(one, tot, inp="B")
    w = node(unreal.MaterialExpressionDivide, -1400, 500)
    wire(sq, w, inp="A")
    wire(tot, w, inp="B")
    weights = {}
    for k, (key, rgb) in enumerate((("yz", (True, False, False)), ("xz", (False, True, False)), ("xy", (False, False, True)))):
        cm = node(unreal.MaterialExpressionComponentMask, -1200, 500 + k * 120, r=rgb[0], g=rgb[1], b=rgb[2], a=False)
        wire(w, cm)
        weights[key] = cm

    def triplanar(name, tex, out, y0):
        total = None
        for k, key in enumerate(("yz", "xz", "xy")):
            t = node(unreal.MaterialExpressionTextureSampleParameter2D, -1000, y0 + k * 260, parameter_name=name,
                     texture=tex)
            wire(uvs[key], t, inp="UVs")
            mul = node(unreal.MaterialExpressionMultiply, -760, y0 + k * 260)
            wire(t, mul, out=out, inp="A")
            wire(weights[key], mul, inp="B")
            if total is None:
                total = mul
            else:
                add = node(unreal.MaterialExpressionAdd, -560, y0 + k * 260)
                wire(total, add, inp="A")
                wire(mul, add, inp="B")
                total = add
        return total

    base = triplanar("Base", unreal.load_asset(_PBR_SET.format(root=root, set="Concrete", ch="B")), "RGB", 0)
    rough = triplanar("Rough", unreal.load_asset(_PBR_SET.format(root=root, set="Concrete", ch="R")), "R", 900)
    tint = node(unreal.MaterialExpressionVectorParameter, -400, -200, parameter_name="Tint",
                default_value=unreal.LinearColor(1, 1, 1, 1))
    bc = node(unreal.MaterialExpressionMultiply, -200, 0)
    wire(base, bc, inp="A")
    wire(tint, bc, inp="B")
    mel.connect_material_property(bc, "", unreal.MaterialProperty.MP_BASE_COLOR)
    rmul = node(unreal.MaterialExpressionMultiply, -200, 900)
    wire(rough, rmul, inp="A")
    wire(param("RoughMul", 1.0, 1000), rmul, inp="B")
    mel.connect_material_property(rmul, "", unreal.MaterialProperty.MP_ROUGHNESS)
    mel.connect_material_property(param("Metallic", 0.0, 1200), "", unreal.MaterialProperty.MP_METALLIC)
    glow = node(unreal.MaterialExpressionMultiply, -200, 1400)
    wire(tint, glow, inp="A")
    wire(param("Emissive", 0.0, 1400), glow, inp="B")
    mel.connect_material_property(glow, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    for flag in ("used_with_instanced_static_meshes", "used_with_nanite", "used_with_spline_meshes"):
        try:
            m.set_editor_property(flag, True)
        except Exception:
            pass
    mel.recompile_material(m)
    _save_batch([m])
    return m


def _glass_master(folder: str):
    """M_City_Glass: see-through window glass (Tint, Opacity 0.3). Translucent with per-pixel lighting, so
    sun and sky light pass into the building (translucency casts no shadow-map shadow)."""
    path = f"{folder}/M_City_Glass"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        return unreal.load_asset(path)
    mel = unreal.MaterialEditingLibrary
    m = unreal.AssetToolsHelpers.get_asset_tools().create_asset("M_City_Glass", folder, unreal.Material,
                                                                  unreal.MaterialFactoryNew())
    m.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    m.set_editor_property("translucency_lighting_mode", unreal.TranslucencyLightingMode.TLM_SURFACE_PER_PIXEL_LIGHTING)
    m.set_editor_property("two_sided", True)
    m.set_editor_property("used_with_instanced_static_meshes", True)
    tint = mel.create_material_expression(m, unreal.MaterialExpressionVectorParameter, -400, 0)
    tint.set_editor_property("parameter_name", "Tint")
    tint.set_editor_property("default_value", unreal.LinearColor(0.35, 0.55, 0.8, 1))
    mel.connect_material_property(tint, "", unreal.MaterialProperty.MP_BASE_COLOR)
    for k, (name, v, prop) in enumerate((("Opacity", 0.3, unreal.MaterialProperty.MP_OPACITY),
                                         ("Roughness", 0.05, unreal.MaterialProperty.MP_ROUGHNESS),
                                         ("Metallic", 0.3, unreal.MaterialProperty.MP_METALLIC))):
        p = mel.create_material_expression(m, unreal.MaterialExpressionScalarParameter, -400, 200 + k * 150)
        p.set_editor_property("parameter_name", name)
        p.set_editor_property("default_value", v)
        mel.connect_material_property(p, "", prop)
    mel.recompile_material(m)
    _save_batch([m])
    return m


def _old_tint(mat) -> unreal.LinearColor:
    mel = unreal.MaterialEditingLibrary
    try:
        for name in mel.get_vector_parameter_names(mat):
            return mel.get_material_default_vector_parameter_value(mat, name)
    except Exception:
        pass
    return unreal.LinearColor(1, 1, 1, 1)


def _save_batch(assets: list) -> None:
    """Save many assets in ONE call. Never save_loaded_asset in a loop: with UEFN revision control every
    such save runs the checkout prompt, and each failed checkout opens a modal dialog with its own swap
    chain - hundreds of saves leaked tens of GB and froze the editor."""
    pkgs = [a.get_outermost() for a in assets if a is not None]
    if pkgs:
        unreal.EditorLoadingAndSavingUtils.save_packages(pkgs, True)


def _mi(folder: str, name: str, master, root: str, look: tuple, tint=None):
    """An instance of the world PBR master: look = (texture set, tile cm, roughness x, metallic, emissive)."""
    mel, eal = unreal.MaterialEditingLibrary, unreal.EditorAssetLibrary
    path = f"{folder}/{name}"
    mi = eal.load_asset(path) if eal.does_asset_exist(path) else unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, folder, unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew())
    mel.set_material_instance_parent(mi, master)
    tex, tile, rough, metal, glow = look
    for ch, pname in (("B", "Base"), ("R", "Rough")):
        t = eal.load_asset(_PBR_SET.format(root=root, set=tex, ch=ch))
        if t:
            mel.set_material_instance_texture_parameter_value(mi, pname, t)
    if tint is not None:
        mel.set_material_instance_vector_parameter_value(mi, "Tint", tint)
    for pname, v in (("TileCm", tile), ("RoughMul", rough), ("Metallic", metal), ("Emissive", glow)):
        mel.set_material_instance_scalar_parameter_value(mi, pname, float(v))
    mel.update_material_instance(mi)
    return mi  # saved by the caller in one batch (_save_batch)


# The street kit City Builder makes itself: (look name -> look, tint) and per mesh its parts
# (box|cyl, material slot, location, (roll, pitch, yaw), dims) in cm; box dims x/y/z, cyl radius/height/sides.
_STREET_LOOKS = {
    "Metal": (("Frame", 200, 0.6, 0.7, 0.0), (0.25, 0.27, 0.3)), "Signal": (("Frame", 200, 0.4, 0.2, 0.3), (0.06, 0.06, 0.05)),
    "SignRed": (("Tile", 200, 0.5, 0.0, 0.3), (0.7, 0.03, 0.03)), "SignGreen": (("Tile", 200, 0.5, 0.0, 0.2), (0.02, 0.35, 0.12)),
    "Panel": (("Concrete", 400, 0.6, 0.0, 1.5), (0.9, 0.9, 0.85)), "Wood": (("Oak", 200, 1.0, 0.0, 0.0), (0.35, 0.25, 0.15)),
    "Asphalt": (("Asphalt", 600, 1.0, 0.0, 0.0), (0.6, 0.6, 0.6)), "Pavers": (("Pavers", 150, 1.0, 0.0, 0.0), (0.8, 0.8, 0.78)),
    "Plinth": (("Concrete", 300, 1.0, 0.0, 0.0), (0.7, 0.7, 0.68)),
    "Floor": (("Tile", 200, 0.5, 0.0, 0.0), (0.72, 0.7, 0.66)), "Light": (("Concrete", 400, 0.5, 0.0, 10.0), (1.0, 0.95, 0.85)),
    "Shaft": (("Concrete", 300, 0.9, 0.0, 0.0), (0.55, 0.55, 0.58)), "Fabric": (("Concrete", 150, 1.0, 0.0, 0.0), (0.45, 0.22, 0.18)),
    "Goods": (("Tile", 100, 0.6, 0.0, 0.6), (0.85, 0.45, 0.1)),
    "StoneWall": (("Stone", 120, 1.0, 0.0, 0.0), (0.55, 0.5, 0.44)),
    "Leaf": (("Rock", 120, 1.0, 0.0, 0.0), (0.13, 0.32, 0.09)), "RockGrey": (("Rock", 200, 1.0, 0.0, 0.0), (0.52, 0.5, 0.47)),
}


def _square_ring() -> list:
    """A 100 x 100 x 15 slab with a 30 x 30 well in the middle (4 strips), light panels under each strip."""
    out = []
    for (cx, cy, dx, dy) in ((0, 32.5, 100, 35), (0, -32.5, 100, 35), (32.5, 0, 35, 30), (-32.5, 0, 35, 30)):
        out.append(("box", 0, (cx, cy, 0), (0, 0, 0), (dx, dy, 15)))
        out.append(("box", 1, (cx, cy, -3), (0, 0, 0), (dx * 0.6, dy * 0.4, 3)))
    return out


def _round_ring() -> list:
    """A round slab (radius 50) with a round well (radius 21): 16 radial boxes, light panels under them."""
    out = []
    for k in range(16):
        a = k * 22.5
        c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
        out.append(("box", 0, (35.5 * c, 35.5 * s, 0), (0, 0, a), (30, 20, 15)))
        out.append(("box", 1, (35.5 * c, 35.5 * s, -3), (0, 0, a), (14, 6, 3)))
    return out


def _stair_core() -> list:
    """One storey (400 cm, floor top at 15) of a 400 x 400 core: a walkable switchback stair on y < 10
    (two 11-step flights, 25 cm treads, a landing between) and an elevator shaft on y > 30 with a door
    opening facing the stairs. Stacked every storey, the stairs climb the building."""
    rise = 200 / 11
    out = [("box", 0, (-175, -90, 0), (0, 0, 0), (50, 200, 15)),  # floor landing the flights start from
           ("box", 0, (160, -90, 0), (0, 0, 0), (80, 200, 215))]  # half landing
    for i in range(11):
        out.append(("box", 0, (-137.5 + 25 * i, -140, 0), (0, 0, 0), (25, 100, 15 + rise * (i + 1))))  # up +x
        top = 215 + rise * (i + 1)
        out.append(("box", 0, (107.5 - 25 * i, -40, top - 30), (0, 0, 0), (25, 100, 30)))  # back up -x
    out += [("box", 2, (0, 195, 0), (0, 0, 0), (360, 10, 400)),  # shaft walls
            ("box", 2, (-175, 115, 0), (0, 0, 0), (10, 170, 400)), ("box", 2, (175, 115, 0), (0, 0, 0), (10, 170, 400)),
            ("box", 2, (-115, 35, 0), (0, 0, 0), (110, 10, 400)), ("box", 2, (115, 35, 0), (0, 0, 0), (110, 10, 400)),
            ("box", 2, (0, 35, 250), (0, 0, 0), (120, 10, 150)),  # over the door
            ("box", 1, (0, 29, 255), (0, 0, 0), (40, 2, 10))]  # lit floor indicator
    return out

_STREET_KIT = {
    "SM_Kit_TrafficLight": (["Metal", "Signal"], [("cyl", 0, (0, 0, 0), (0, 0, 0), (12, 560, 12)),
                                                  ("box", 0, (190, 0, 532), (0, 0, 0), (380, 15, 15)),
                                                  ("box", 1, (360, 0, 420), (0, 0, 0), (40, 40, 110))]),
    "SM_Kit_StopSign": (["Metal", "SignRed"], [("cyl", 0, (0, 0, 0), (0, 0, 0), (4, 250, 8)),
                                               ("cyl", 1, (0, 2, 252), (90, 0, 0), (38, 4, 8))]),
    "SM_Kit_StreetSign": (["Metal", "SignGreen"], [("cyl", 0, (0, 0, 0), (0, 0, 0), (4, 300, 8)),
                                                   ("box", 1, (0, 0, 290), (0, 0, 0), (90, 2, 22)),
                                                   ("box", 1, (0, 0, 314), (0, 0, 0), (2, 90, 22))]),
    "SM_Kit_Billboard": (["Metal", "Panel"], [("box", 0, (0, -400, 0), (0, 0, 0), (30, 30, 700)),
                                              ("box", 0, (0, 400, 0), (0, 0, 0), (30, 30, 700)),
                                              ("box", 1, (0, 0, 700), (0, 0, 0), (30, 1200, 450))]),
    "SM_Kit_PowerPole": (["Wood"], [("cyl", 0, (0, 0, 0), (0, 0, 0), (18, 1100, 10)),
                                    ("box", 0, (0, 0, 1000), (0, 0, 0), (20, 240, 15))]),
    "SM_Kit_ParkingLot": (["Asphalt"], [("box", 0, (0, 0, 0), (0, 0, 0), (100, 100, 8))]),
    "SM_Kit_PathSlab": (["Pavers"], [("box", 0, (0, 0, 0), (0, 0, 0), (100, 100, 8))]),
    "SM_Kit_Plinth": (["Plinth"], [("box", 0, (0, 0, -100), (0, 0, 0), (100, 100, 100))]),
    "SM_Kit_FloorPlate": (["Floor", "Light"], _square_ring()),
    "SM_Kit_FloorRound": (["Floor", "Light"], _round_ring()),
    "SM_Kit_FloorSolid": (["Floor", "Light"], [("box", 0, (0, 0, 0), (0, 0, 0), (100, 100, 15)),
                                              ("box", 1, (0, 0, -3), (0, 0, 0), (60, 60, 3))]),
    "SM_Kit_StairCore": (["Plinth", "Light", "Shaft"], _stair_core()),
    "SM_Kit_ElevatorCar": (["Metal", "Light"], [("box", 0, (0, 0, 0), (0, 0, 0), (220, 140, 5)),
                                                ("box", 0, (0, 65, 0), (0, 0, 0), (220, 10, 250)),
                                                ("box", 0, (-105, 0, 0), (0, 0, 0), (10, 140, 250)),
                                                ("box", 0, (105, 0, 0), (0, 0, 0), (10, 140, 250)),
                                                ("box", 0, (0, 0, 245), (0, 0, 0), (220, 140, 5)),
                                                ("box", 1, (0, 0, 241), (0, 0, 0), (120, 60, 4))]),
    "SM_Kit_DeskSet": (["Wood", "Signal"], [("box", 0, (0, 0, 72), (0, 0, 0), (160, 80, 4)),
                                            ("box", 0, (0, 35, 0), (0, 0, 0), (150, 4, 72)),
                                            ("box", 1, (25, 0, 76), (0, 0, 0), (4, 60, 38)),
                                            ("cyl", 1, (-55, 0, 0), (0, 0, 0), (25, 48, 12)),
                                            ("box", 1, (-78, 0, 48), (0, 0, 0), (5, 45, 50))]),
    "SM_Kit_SofaSet": (["Fabric", "Wood"], [("box", 0, (0, 0, 0), (0, 0, 0), (90, 200, 42)),
                                            ("box", 0, (-40, 0, 42), (0, 0, 0), (20, 200, 43)),
                                            ("box", 1, (90, 0, 0), (0, 0, 0), (60, 100, 38))]),
    # spline meshes for the '<City> Paths' actor (third item: folder under WorldGen): 10 m along +X in 10
    # sections so they bend, walking surface at z 0, widths = the spline widths (SW 3.8, WK 1.6, DW 3.5 m)
    "SM_Path_Sidewalk_10m": (["Pavers", "Plinth"], [("slab", 0, (500, 0, -15), (0, 0, 0), (1000, 380, 15, 10)),
                                                    ("slab", 1, (500, -185, -15), (0, 0, 0), (1000, 10, 17, 10))],
                             "Roads"),
    "SM_Path_Walkway_10m": (["Pavers"], [("slab", 0, (500, 0, -10), (0, 0, 0), (1000, 160, 10, 10))], "Roads"),
    "SM_Path_Driveway_10m": (["Asphalt"], [("slab", 0, (500, 0, -12), (0, 0, 0), (1000, 350, 12, 10))], "Roads"),
    # stone retaining wall along a cut / filled road: 1 m long, 50 cm thick, 1 m tall, a capping course on top
    "SM_Kit_StoneWall": (["StoneWall", "Plinth"], [("box", 0, (0, 0, 0), (0, 0, 0), (100, 50, 95)),
                                                  ("box", 1, (0, 0, 95), (0, 0, 0), (100, 56, 5))]),
    # the sidewalk carried round a junction corner: a round 4 m paving pad (any corner angle), top at z 0
    "SM_Kit_SidewalkCorner": (["Pavers"], [("cyl", 0, (0, 0, -15), (0, 0, 0), (200, 15, 16))]),
    # retaining wall for an uphill lot edge: 1 m long, 30 cm thick, 1 m tall, scaled to the edge and the cut
    "SM_Kit_RetainingWall": (["Plinth"], [("box", 0, (0, 0, 0), (0, 0, 0), (100, 30, 100))]),
    # nature for the Bushes / Rocks PCG layers: low-poly lumps (instanced by the hundred thousand)
    "SM_Kit_Bush": (["Leaf"], [("sph", 0, (0, 0, 0), (0, 0, 0), (65, 5, 7)), ("sph", 0, (45, 20, 0), (0, 0, 0), (50, 5, 7)),
                               ("sph", 0, (-35, -30, 0), (0, 0, 0), (48, 5, 7))]),
    "SM_Kit_Rock": (["RockGrey"], [("box", 0, (0, 0, -20), (12, 8, 25), (160, 120, 90)),
                                   ("box", 0, (40, -10, -10), (-10, 15, 70), (90, 80, 70))]),
    "SM_Kit_ShopShelf": (["Metal", "Goods"], [("box", 0, (0, 0, 0), (0, 0, 0), (60, 200, 180)),
                                              ("box", 1, (31, 0, 40), (0, 0, 0), (4, 180, 30)),
                                              ("box", 1, (31, 0, 110), (0, 0, 0), (4, 180, 30))]),
}


@register("road_network_street_kit")
def road_network_street_kit(root: str = "", rebuild: bool = False, plan: bool = False, only: list | None = None,
                            save: bool = True) -> dict:
    """Make the street pieces the kit lacks (traffic lights, stop / street signs, billboards, power poles,
    parking lot, path slab, plinth) as GeometryScript meshes in WorldGen/Kit/Street, in M_City_WorldPBR
    instances. Mesh and material assets only; safe to run again (rebuilt in place).
    plan=true only lists what is missing; only=[names] builds just those; save=false leaves saving to one
    road_network_save_assets call. Call it once per mesh (the backend does): each mesh build opens a
    progress window whose GPU swap chain UEFN frees only at the end of the frame."""
    if not root.startswith("/"):
        return {"ok": False, "error": "Give root, the project's content root (for example /<mount>/MovieTheater)."}
    eal = unreal.EditorAssetLibrary
    mat_folder, folder = f"{root}/WorldGen/Materials", f"{root}/WorldGen/Kit/Street"
    for f in (mat_folder, folder):
        if not eal.does_directory_exist(f):
            eal.make_directory(f)
    # only the pieces that aren't there yet (rebuild=true remakes all): remaking and saving all of them
    # on every PCG build kept UEFN busy for minutes
    where = {n: (f"{root}/WorldGen/{v[2]}" if len(v) > 2 else folder) for n, v in _STREET_KIT.items()}
    todo = {n: v for n, v in _STREET_KIT.items()
            if (only is None or n in only) and (rebuild or not eal.does_asset_exist(f"{where[n]}/{n}"))}
    if plan:
        return {"ok": True, "todo": list(todo)}
    if not todo:
        return {"ok": True, "made": [], "paths": [], "errors": [], "note": "every street kit piece is already there"}
    master = _world_pbr_master(mat_folder, root)
    need = {m for mats, _ in todo.values() for m in mats}
    looks = {k: _mi(mat_folder, f"MI_Street_{k}", master, root, look, unreal.LinearColor(*tint, 1))
             for k, (look, tint) in _STREET_LOOKS.items() if k in need}
    gp = unreal.GeometryScript_Primitives
    base = unreal.GeometryScriptPrimitiveOriginMode.BASE
    made, errors = [], []
    for name, (mats, parts, *_f) in todo.items():
        try:
            dm = unreal.DynamicMesh()
            unreal.GeometryScript_Materials.enable_material_i_ds(dm)
            for kind, slot, loc, rot, dims in parts:
                o = unreal.GeometryScriptPrimitiveOptions()
                o.material_id = slot
                t = unreal.Transform(unreal.Vector(*loc), unreal.Rotator(roll=rot[0], pitch=rot[1], yaw=rot[2]),
                                     unreal.Vector(1, 1, 1))
                if kind == "box":
                    gp.append_box(dm, o, t, dimension_x=dims[0], dimension_y=dims[1], dimension_z=dims[2], origin=base)
                elif kind == "slab":  # a box cut into sections along X, so a spline mesh bends smoothly
                    gp.append_box(dm, o, t, dimension_x=dims[0], dimension_y=dims[1], dimension_z=dims[2],
                                  steps_x=int(dims[3]), origin=base)
                elif kind == "sph":  # radius, latitude steps, longitude steps (few: a low-poly lump)
                    gp.append_sphere_lat_long(dm, o, t, radius=dims[0], steps_phi=int(dims[1]), steps_theta=int(dims[2]),
                                              origin=base)
                else:
                    gp.append_cylinder(dm, o, t, radius=dims[0], height=dims[1], radial_steps=int(dims[2]), origin=base)
            path = f"{where[name]}/{name}"
            sm = eal.load_asset(path) if eal.does_asset_exist(path) else None
            if sm is None:
                res = unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(
                    dm, path, unreal.GeometryScriptCreateNewStaticMeshAssetOptions())
                sm = res[0] if isinstance(res, tuple) else res
            opts = unreal.GeometryScriptCopyMeshToAssetOptionsV2()
            opts.replace_materials = True
            opts.new_materials = [looks[m] for m in mats]
            opts.new_material_slot_names = mats
            unreal.GeometryScript_AssetUtils.copy_mesh_to_static_mesh_v2(dm, sm, opts, unreal.GeometryScriptMeshWriteLOD())
            try:  # walkable / bumpable: simple collision from the parts
                unreal.GeometryScript_Collision.set_static_mesh_collision_from_mesh(
                    dm, sm, unreal.GeometryScriptCollisionFromMeshOptions())
            except Exception:
                pass
            made.append(sm)
        except Exception as exc:  # one bad piece must not stop the rest
            errors.append(f"{name}: {exc}"[:200])
    assets = made + list(looks.values())
    if save:
        _save_batch(assets)
    return {"ok": not errors, "made": [m.get_name() for m in made], "errors": errors,
            "paths": [a.get_path_name().split(".")[0] for a in assets]}


@register("road_network_ism_ready")
def road_network_ism_ready(meshes: list | None = None) -> dict:
    """Make the materials of these meshes usable on instanced meshes (PCG spawns instances; a material without
    the usage draws the grey checker). Only the base Material gets the flag (a shader recompile); the meshes
    are never touched. Materials outside the project (Fortnite /Game) can't be changed and are listed."""
    fixed, locked, missing = [], [], []
    for path in meshes or []:
        mesh = unreal.load_asset(path.split(".")[0])
        if not isinstance(mesh, unreal.StaticMesh):
            missing.append(path)
            continue
        for slot in mesh.static_materials:
            mat = slot.material_interface
            base = mat.get_base_material() if mat else None
            if base is None or base in fixed or base.get_editor_property("used_with_instanced_static_meshes"):
                continue
            if base.get_path_name().startswith("/Game/"):
                locked.append(base.get_path_name())
                continue
            base.set_editor_property("used_with_instanced_static_meshes", True)
            unreal.MaterialEditingLibrary.recompile_material(base)
            fixed.append(base)
    _save_batch(fixed)
    return {"ok": True, "fixed": [m.get_name() for m in fixed], "locked": sorted(set(locked)), "missing": missing}


@register("road_network_assets_exist")
def road_network_assets_exist(paths: list | None = None) -> dict:
    """Which of these asset paths the project has (read-only, no loading)."""
    eal = unreal.EditorAssetLibrary
    return {"ok": True, "exist": {p: bool(eal.does_asset_exist(p.split(".")[0])) for p in (paths or [])}}


@register("road_network_save_assets")
def road_network_save_assets(paths: list | None = None) -> dict:
    """Save these assets in ONE save_packages batch (its own editor frame, one progress window)."""
    assets = [unreal.load_asset(p) for p in dict.fromkeys(paths or [])]
    _save_batch([a for a in assets if a is not None])
    return {"ok": True, "saved": len([a for a in assets if a is not None])}


@register("road_network_kit_materials")
def road_network_kit_materials(root: str = "", folders: list | None = None) -> dict:
    """Textured, instance-safe materials for every mesh in the kit folders (WorldGen/City, WorldGen/Kit):
    one MI of M_City_WorldPBR per old flat-colour material (its colour kept as Tint), and per mesh the MI for
    each slot, returned as `overrides` {mesh path: [MI path per slot]} for PCG's material overrides.
    The meshes themselves are NEVER edited: changing a mesh's material rebuilds the whole mesh (about 1.5 min
    and GBs each, all inside one editor frame), which froze UEFN for hours on 80 kit meshes.
    Material assets only, saved in one batch. Safe to run again: existing instances are reused."""
    if not root.startswith("/"):
        return {"ok": False, "error": "Give root, the project's content root (for example /<mount>/MovieTheater)."}
    mel = unreal.MaterialEditingLibrary
    eal = unreal.EditorAssetLibrary
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    mat_folder = f"{root}/WorldGen/Materials"
    if not eal.does_directory_exist(mat_folder):
        eal.make_directory(mat_folder)
    master = _world_pbr_master(mat_folder, root)
    glass = _glass_master(mat_folder)
    made, created, overrides = {}, [], {}
    for sub in folders or ["City", "Kit"]:
        for path in eal.list_assets(f"{root}/WorldGen/{sub}", recursive=False):
            mesh = eal.load_asset(path)
            if not isinstance(mesh, unreal.StaticMesh):
                continue
            per_slot = []
            for slot in mesh.static_materials:
                old = slot.material_interface
                if old is None:
                    per_slot.append("")
                    continue
                if old.get_path_name().startswith(mat_folder):  # an earlier run already gave it one
                    per_slot.append(old.get_path_name())
                    continue
                key = old.get_name()
                if key not in made:
                    mi_path = f"{mat_folder}/MI_City_{key.removeprefix('M_')}"
                    fresh = not eal.does_asset_exist(mi_path)
                    mi = tools.create_asset(mi_path.rsplit("/", 1)[1], mat_folder, unreal.MaterialInstanceConstant,
                                            unreal.MaterialInstanceConstantFactoryNew()) if fresh else eal.load_asset(mi_path)
                    if "glass" in key.lower() and mi.get_editor_property("parent") != glass:
                        mel.set_material_instance_parent(mi, glass)  # see-through windows
                        mel.set_material_instance_vector_parameter_value(mi, "Tint", _old_tint(old))
                        mel.update_material_instance(mi)
                        created.append(mi)
                    elif fresh:
                        mel.set_material_instance_parent(mi, master)
                        tex, tile, rough, metal, glow = _look(key)
                        for ch, pname in (("B", "Base"), ("R", "Rough")):
                            t = eal.load_asset(_PBR_SET.format(root=root, set=tex, ch=ch))
                            if t:
                                mel.set_material_instance_texture_parameter_value(mi, pname, t)
                        mel.set_material_instance_vector_parameter_value(mi, "Tint", _old_tint(old))
                        for pname, v in (("TileCm", tile), ("RoughMul", rough), ("Metallic", metal), ("Emissive", glow)):
                            mel.set_material_instance_scalar_parameter_value(mi, pname, float(v))
                        mel.update_material_instance(mi)
                        created.append(mi)
                    made[key] = mi
                per_slot.append(made[key].get_path_name())
            overrides[mesh.get_path_name()] = per_slot
    _save_batch(created)
    return {"ok": True, "master": master.get_path_name(), "instances": len(made), "created": len(created),
            "meshes": len(overrides), "overrides": overrides}

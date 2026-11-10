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


def _junction_geometry(arm_deg: list, half: float = _R1_W) -> tuple:
    """Plain-asphalt junction in the Road1 shape for arms at these world angles (degrees), as wide
    as the roads it joins (half: road half width, cm). Returns (verts, uvs, tris, mouth distance
    per arm in the given order)."""
    k_w = max(0.5, float(half) / _R1_W)
    _R1_W_ = _R1_W * k_w
    profile = [(s * k_w, z) for s, z in _R1_PROFILE]
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
            ring.append((q[0], q[1], -2.0))
        chain.append(R)
        chains.append((chain, k, j))
    verts, uvs, tris = [(0.0, 0.0, 16.0)], [(_U_ASPHALT, 0.0)], []
    for x, y, z in ring:
        verts.append((x, y, z))
        uvs.append((_U_ASPHALT, x / 1024.0))
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
                for (x, y), z in ((chain[i], -2.0), (chain[i + 1], -2.0), (outs[i + 1], _R1_SKIRT_Z), (outs[i], _R1_SKIRT_Z)):
                    verts.append((x, y, z))
                    uvs.append((_U_SHOULDER, x / 1024.0))
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
    mat = unreal.load_asset(_R1_MATERIAL)
    out, errors = {}, []
    for piece in pieces or []:
        name, arms = str(piece.get("name") or ""), list(piece.get("arms") or [])
        if not name or len(arms) < 3:
            errors.append(f"{name or '?'}: needs a name and 3+ arms")
            continue
        verts, uvs, tris, mouth = _junction_geometry(arms, float(piece.get("half") or _R1_W))
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
        opts.new_material_slot_names = ["Road1"]
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

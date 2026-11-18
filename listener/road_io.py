"""Road Network: read and shape UEFN landscape spline road actors.

Runs inside UEFN's Python. Landscape spline control points are not readable from
Python, so a road actor's full definition travels as its BEGIN_SPLINES clipboard text
(the text the Splines tool copies and pastes). These handlers hand that text out in
chunks, list the road actors and their pieces and sample the terrain for the 3D view.
Nothing here writes the landscape's heights; road_network_paint_ground rewrites one dirt paint layer.
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
def road_network_scan(grid: int = 96, folder: str = "City/Elevated Pillars", j0: int = 0, j1: int = -1) -> dict:
    """Pillars and other placed road props, plus a terrain height grid for the 3D view.
    j0 / j1: only rows j0..j1-1 of the grid (a fine scan comes in bands, one editor call each, so UEFN never
    stalls on a million traces in one frame); props come with the first band only."""
    w = _world()
    props = []
    for actor in (unreal.GameplayStatics.get_all_actors_of_class(w, unreal.StaticMeshActor) if j0 == 0 else []):
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
    grid = max(16, min(int(grid or 200), 2048))
    rows = range(max(0, int(j0)), grid if int(j1) < 0 else min(grid, int(j1)))
    if hi[0] > lo[0]:
        ignore: list = []
        for j in rows:
            row = []
            for i in range(grid):
                x = lo[0] + (hi[0] - lo[0]) * (i + 0.5) / grid
                y = lo[1] + (hi[1] - lo[1]) * (j + 0.5) / grid
                g = _ground(x, y, ignore)
                row.append(round(g, 1) if g is not None else None)
            heights.append(row)
    terrain = {"min": lo, "max": hi, "grid": grid, "heights": heights}
    if rows.start == 0:
        # the level's own water surface: the top of its water bodies (Creative water volumes, Water body actors),
        # so a ground pulled from UEFN keeps the water exactly where it is there
        tops = []
        for a in unreal.GameplayStatics.get_all_actors_of_class(w, unreal.Actor):
            if "waterbody" in a.get_class().get_name().lower():
                origin, extent = a.get_actor_bounds(False)
                tops.append(origin.z + extent.z)
        if tops:
            terrain["sea"] = round(max(tops), 1)
    return {"ok": True, "props": props, "j0": rows.start, "terrain": terrain}


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


# Our own road kit (WorldGen/Roads): a flat deck at z 0 in M_Asphalt, drawn on its points (SegmentMeshOffset
# citygen.ROAD_OFFSET_CM = 6; 50 and 10 left every road and kerb floating, 0 let bumps of ground poke through).
# A junction piece sits on its point with the road's deck height, plus a 1 cm hair so the road ends it meets
# never flicker, with planar UVs in metres.
_OWN_DECK_Z = 7.0
_OWN_UV_CM = 400.0
_OWN_SKIRT_Z = -330.0  # a straight wall down under every fillet, like the road tiles' 3 m skirt


_PAINT_Z = 1.0  # painted lines sit this far above the junction deck
_STOP_LINE = (70.0, 40.0)  # a stop line runs from this far to this far inside an arm's mouth
_ZEBRA = (420.0, 120.0, 50.0, 110.0)  # crosswalk bars: from / to inside the mouth, bar width, pitch
_KERB_Z = 15.0  # a junction's sidewalk stands this far above its deck: the road meshes' kerb height
_BULB_MIN, _BULB_PAST = 1000.0, 500.0  # a turnaround's outer radius: at least this, and this much past the road's half
_BULB_STEPS = 24


def _junction_geometry(arm_deg: list, half: float = _R1_W, flat: bool = False, halves: list | None = None,
                       paint: bool = True, walks: list | None = None, bulb: float = 0.0) -> tuple:
    """Junction for arms at these world angles (degrees). Each arm's mouth is as wide as ITS road (halves:
    half width per arm, cm; else `half` for all): a lane meeting a boulevard gets a lane-wide mouth, never
    the boulevard's. flat: our own kit's flat deck and planar UVs; otherwise the Fortnite Road1 crown and its
    texture strip. On a flat 3+ way junction every arm also gets a stop line and a zebra crossing (paint).
    walks: per arm, how much of its half width is sidewalk (cm; 0 = none). The junction then carries its own
    kerbs and sidewalk corners in the same mesh (a third buffer), joining each arm's sidewalks round the corner.
    Returns (verts, uvs, tris, mouth distance per arm in the given order, (paint verts, uvs, tris),
    (sidewalk verts, uvs, tris)). One arm: a dead end's turnaround (cul-de-sac), a round deck of outer radius `bulb`
    (0 = the default) round the node, the road's sidewalks carried on round it."""
    widths = [max(0.5 * _R1_W, float(h)) for h in (halves if halves and len(halves) == len(arm_deg) else [half] * len(arm_deg))]
    walk_in = walks if flat and walks and len(walks) == len(arm_deg) else [0.0] * len(arm_deg)
    edge_z = _OWN_DECK_Z if flat else -2.0

    def uv(x, y, u_strip):
        return (x / _OWN_UV_CM, y / _OWN_UV_CM) if flat else (u_strip, x / 1024.0)
    order = sorted(range(len(arm_deg)), key=lambda k: arm_deg[k] % 360.0)
    ang = [math.radians(arm_deg[k] % 360.0) for k in order]
    W = [widths[k] for k in order]
    K = [max(0.0, min(W[i] - 150.0, float(walk_in[k] or 0.0))) for i, k in enumerate(order)]  # sidewalk per arm
    profiles = [[(s * w / _R1_W, _OWN_DECK_Z if flat else z) for s, z in _R1_PROFILE] for w in W]
    n = len(ang)
    U = [(math.cos(a), math.sin(a)) for a in ang]
    N = [(-math.sin(a), math.cos(a)) for a in ang]
    gap = [((ang[(k + 1) % n] - ang[k]) % (2 * math.pi)) or 2 * math.pi for k in range(n)]
    need = [0.0] * n
    for k in range(n):
        g = gap[k]
        c = max(W[k], W[(k + 1) % n]) / math.tan(g / 2) + 300.0 if g < math.radians(179) else 0.0
        need[k] = max(need[k], c)
        need[(k + 1) % n] = max(need[(k + 1) % n], c)
    d = [max(700.0, min(3000.0, x)) for x in need]
    rb = max(_BULB_MIN, W[0] + _BULB_PAST, float(bulb or 0.0)) if n == 1 else 0.0
    if n == 1:  # the mouth just past where the road's edges meet the round
        d = [max(700.0, math.sqrt(max(0.0, rb * rb - W[0] * W[0])) + 150.0)]

    def pt(k, along, side):
        return (U[k][0] * along + N[k][0] * side, U[k][1] * along + N[k][1] * side)

    def edges_meet(k, j, wk, wj):
        # where arm k's left edge (N_k*wk + U_k*s) meets arm j's right edge (-N_j*wj + U_j*t), or None
        p1, p2 = (N[k][0] * wk, N[k][1] * wk), (-N[j][0] * wj, -N[j][1] * wj)
        den = U[k][0] * U[j][1] - U[k][1] * U[j][0]
        if abs(den) < 1e-6:
            return None
        s_ = ((p2[0] - p1[0]) * U[j][1] - (p2[1] - p1[1]) * U[j][0]) / den
        return p1[0] + U[k][0] * s_, p1[1] + U[k][1] * s_

    def corner(k, j, wk, wj):
        # the edge from arm k's left side (wk from its centre line) round to arm j's right side (wj): 7 points
        L, R = pt(k, d[k], wk), pt(j, d[j], -wj)
        if n == 1:  # the round of a turnaround, as far in from its outer edge as wk is in from the road's edge
            r = rb - (W[k] - wk)
            x1 = math.sqrt(max(1.0, r * r - wk * wk))
            a0 = math.atan2(wk, x1)
            arc = [pt(k, r * math.cos(a0 + (2 * math.pi - 2 * a0) * i / _BULB_STEPS),
                      r * math.sin(a0 + (2 * math.pi - 2 * a0) * i / _BULB_STEPS)) for i in range(_BULB_STEPS + 1)]
            return [L] + arc + [R]
        g = gap[k]
        square = False
        mid = ((L[0] + R[0]) / 2, (L[1] + R[1]) / 2)
        if g < math.radians(179):
            C = edges_meet(k, j, wk, wj) or mid  # the corner between two roads of their own widths
        elif g > math.radians(181) and n == 2:
            # the outside of an elbow: the two road edges run on to meet in a SQUARE corner
            C = edges_meet(k, j, wk, wj) or mid
            square = True
        else:
            C = mid
        chain = [L]
        for t in (1 / 6, 2 / 6, 3 / 6, 4 / 6, 5 / 6):
            if square:  # straight in to the corner and straight out: no rounding
                u = min(1.0, 2 * t)
                v = max(0.0, 2 * t - 1)
                q = (L[0] + (C[0] - L[0]) * u + (R[0] - C[0]) * v, L[1] + (C[1] - L[1]) * u + (R[1] - C[1]) * v)
            else:
                a, b, cc = (1 - t) ** 2, 2 * (1 - t) * t, t * t
                q = (a * L[0] + b * C[0] + cc * R[0], a * L[1] + b * C[1] + cc * R[1])
            chain.append(q)
        chain.append(R)
        return chain

    ring = []  # (x, y, z, is_fillet_chain)
    chains = []
    for k in range(n):
        for s, z in profiles[k]:
            x, y = pt(k, d[k], s)
            ring.append((x, y, z))
        j = (k + 1) % n
        chain = corner(k, j, W[k], W[j])
        ring.extend((q[0], q[1], edge_z) for q in chain[1:-1])
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
            out = 0.0 if flat else _R1_SKIRT  # own kit: a vertical wall; Road1: a 50 cm flare
            outs.append((p[0] + o[0] * out, p[1] + o[1] * out))
        skirt_z = _OWN_SKIRT_Z if flat else _R1_SKIRT_Z
        for side in (0, 1):  # both faces, separate vertices (shared ones cancel the normals)
            for i in range(len(chain) - 1):
                base = len(verts)
                for (x, y), z in ((chain[i], edge_z), (chain[i + 1], edge_z), (outs[i + 1], skirt_z), (outs[i], skirt_z)):
                    verts.append((x, y, z))
                    uvs.append(uv(x, y, _U_SHOULDER))
                q = (base, base + 1, base + 2, base + 3)
                tris += ([(q[0], q[1], q[2]), (q[0], q[2], q[3])] if side == 0 else [(q[0], q[2], q[1]), (q[0], q[3], q[2])])
    mouth = [0.0] * n
    for idx, k in enumerate(order):
        mouth[k] = d[idx]
    pv, pu, ptri = [], [], []
    if paint and flat and n >= 3:
        z = _OWN_DECK_Z + _PAINT_Z

        def bar(k, a0, a1, s0, s1):
            base = len(pv)
            for along, side in ((a0, s0), (a1, s0), (a1, s1), (a0, s1)):
                x, y = pt(k, along, side)
                pv.append((x, y, z))
                pu.append(uv(x, y, _U_ASPHALT))
            ptri.extend([(base, base + 2, base + 1), (base, base + 3, base + 2)])  # facing up, like the deck
        for k in range(n):
            w = W[k] - K[k] - 60.0  # the carriageway only: never across the sidewalk
            if w < 150.0:
                continue  # a footpath-wide arm: nothing to paint
            bar(k, d[k] - _STOP_LINE[0], d[k] - _STOP_LINE[1], -w, w)
            s = -w
            while s + _ZEBRA[2] <= w:
                bar(k, d[k] - _ZEBRA[0], d[k] - _ZEBRA[1], s, s + _ZEBRA[2])
                s += _ZEBRA[3]
    sv, su, stri = [], [], []
    z0, z1 = _OWN_DECK_Z, _OWN_DECK_Z + _KERB_Z

    def quad(p, top):
        (ax, ay, _), (bx, by, _), (cx, cy, _) = p[0], p[1], p[2]
        up = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) <= 0  # UE: clockwise seen from above faces up
        for flip in ([not up] if top else [False, True]):  # a kerb face: both sides, on vertices of their own
            base = len(sv)
            for x, y, z in p:
                sv.append((x, y, z))
                su.append(uv(x, y, _U_SHOULDER))
            stri.extend([(base, base + 2, base + 1), (base, base + 3, base + 2)] if flip
                        else [(base, base + 1, base + 2), (base, base + 2, base + 3)])
    for chain, k, j in chains:
        if K[k] <= 0 and K[j] <= 0:
            continue
        inner = corner(k, j, W[k] - K[k], W[j] - K[j])
        for i in range(len(chain) - 1):
            a, b, c, e = inner[i], inner[i + 1], chain[i + 1], chain[i]
            quad([(a[0], a[1], z1), (b[0], b[1], z1), (c[0], c[1], z1), (e[0], e[1], z1)], True)  # the paving
            quad([(a[0], a[1], z0), (b[0], b[1], z0), (b[0], b[1], z1), (a[0], a[1], z1)], False)  # the kerb
            quad([(e[0], e[1], z0), (c[0], c[1], z0), (c[0], c[1], z1), (e[0], e[1], z1)], False)  # its back edge
    return verts, uvs, tris, mouth, (pv, pu, ptri), (sv, su, stri)


def _road_materials(like: str, mat, line_mat, walk_mat) -> tuple:
    """(asphalt, white paint, sidewalk) materials from the road mesh a junction joins, by slot name, so the piece
    is the same colour as that road (city streets use M_Asphalt, house streets the kit's M_BK_Asphalt). The
    sidewalk is the paving slot (Pavers / Concrete, never the PaverBand), else the kerb. Falls back per slot."""
    if not like or not unreal.EditorAssetLibrary.does_asset_exist(like):
        return mat, line_mat, walk_mat
    slots = [(str(s.material_slot_name), s.material_interface) for s in unreal.load_asset(like).static_materials
             if s.material_interface]

    def pick(*ends):
        return next((m for e in ends for n, m in slots if n.endswith(e)), None)
    return (pick("Asphalt") or mat, pick("PaintW", "PaintWhite") or line_mat,
            pick("_Pavers", "_Concrete", "Curb") or walk_mat)


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
    white = own.replace("M_Asphalt", "M_PaintW")
    line_mat = unreal.load_asset(white) if flat and unreal.EditorAssetLibrary.does_asset_exist(white) else None
    # the kit's own sidewalk material, so a junction's corners match the sidewalks of the roads that meet there
    walk_path = next((p for p in (own.replace("M_Asphalt", n) for n in ("M_Sidewalk", "M_Concrete", "M_Curb"))
                      if flat and unreal.EditorAssetLibrary.does_asset_exist(p)), "")
    walk_mat = unreal.load_asset(walk_path) if walk_path else line_mat
    out, errors = {}, []
    for piece in pieces or []:
        name, arms = str(piece.get("name") or ""), list(piece.get("arms") or [])
        if not name or not arms:
            errors.append(f"{name or '?'}: needs a name and 1+ arms")
            continue
        p_mat, p_line, p_walk = _road_materials(str(piece.get("like") or ""), mat, line_mat, walk_mat) if flat \
            else (mat, line_mat, walk_mat)
        verts, uvs, tris, mouth, lines, walk = _junction_geometry(
            arms, float(piece.get("half") or _R1_W), flat, piece.get("halves"),
            bool(piece.get("paint", True)) and p_line is not None, piece.get("walks") if p_walk else None,
            float(piece.get("bulb") or 0.0))
        buf = unreal.GeometryScriptSimpleMeshBuffers()
        buf.vertices = [unreal.Vector(*v) for v in verts]
        buf.uv0 = [unreal.Vector2D(*u) for u in uvs]
        buf.triangles = [unreal.IntVector(*t) for t in tris]
        dm = unreal.DynamicMesh()
        unreal.GeometryScript_Materials.enable_material_i_ds(dm)
        unreal.GeometryScript_MeshEdits.append_buffers_to_mesh(dm, buf, 0)
        if lines[2]:  # stop lines and crossings: a second material slot on the same mesh
            lb = unreal.GeometryScriptSimpleMeshBuffers()
            lb.vertices = [unreal.Vector(*v) for v in lines[0]]
            lb.uv0 = [unreal.Vector2D(*u) for u in lines[1]]
            lb.triangles = [unreal.IntVector(*t) for t in lines[2]]
            unreal.GeometryScript_MeshEdits.append_buffers_to_mesh(dm, lb, 1)
        if walk[2]:  # kerbs and sidewalk corners: one mesh with the deck, its own slot
            wb = unreal.GeometryScriptSimpleMeshBuffers()
            wb.vertices = [unreal.Vector(*v) for v in walk[0]]
            wb.uv0 = [unreal.Vector2D(*u) for u in walk[1]]
            wb.triangles = [unreal.IntVector(*t) for t in walk[2]]
            unreal.GeometryScript_MeshEdits.append_buffers_to_mesh(dm, wb, 2 if lines[2] else 1)
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
        opts.new_materials = [p_mat] + ([p_line] if lines[2] else []) + ([p_walk] if walk[2] else [])
        opts.new_material_slot_names = (["Asphalt" if flat else "Road1"] + (["Paint"] if lines[2] else [])
                                        + (["Sidewalk"] if walk[2] else []))
        lod = unreal.GeometryScriptMeshWriteLOD()
        unreal.GeometryScript_AssetUtils.copy_mesh_to_static_mesh_v2(dm, sm, opts, lod)
        _lod_ready(sm)  # the copy leaves one LOD: the validator flags every junction without this
        # no simple shapes come with the copy: without complex-as-simple cars (physics) fall through junctions
        sm.get_editor_property("body_setup").set_editor_property(
            "collision_trace_flag", unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE)
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


def _load_or_create(name: str, folder: str, cls, factory):
    """The asset at folder/name, made when it isn't there. create_asset gives None when the name is taken (an
    unsaved asset in memory, a redirector): load that one instead, or say which path is stuck."""
    eal, path = unreal.EditorAssetLibrary, f"{folder}/{name}"
    got = eal.load_asset(path) if eal.does_asset_exist(path) else None
    if got is None:
        got = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, cls, factory)
    if got is None:
        got = unreal.load_object(None, f"{path}.{name}")
    if got is None:
        raise RuntimeError(f"couldn't load or make {path} (a redirector or a locked file there?)")
    return got


def _pieces_without(todo: dict, missing_looks: set) -> tuple:
    """Street kit pieces that can still be built, and the names of those that need a look that couldn't be made."""
    keep = {n: v for n, v in todo.items() if not set(v[0]) & missing_looks}
    return keep, [n for n in todo if n not in keep]


def _world_pbr_master(folder: str, root: str):
    path = f"{folder}/{_MASTER}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        return _load_or_create(_MASTER, folder, unreal.Material, unreal.MaterialFactoryNew())
    mel = unreal.MaterialEditingLibrary
    m = _load_or_create(_MASTER, folder, unreal.Material, unreal.MaterialFactoryNew())

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
    mi = _load_or_create(name, folder, unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew())
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
    # road paint (backend/road_profiles): lane lines and the two-way centre line
    "Paint": (("Concrete", 200, 0.6, 0.0, 0.0), (0.95, 0.95, 0.92)), "PaintYellow": (("Concrete", 200, 0.6, 0.0, 0.0), (0.9, 0.68, 0.08)),
    "Leaf": (("Rock", 120, 1.0, 0.0, 0.0), (0.13, 0.32, 0.09)), "RockGrey": (("Rock", 200, 1.0, 0.0, 0.0), (0.52, 0.5, 0.47)),
    "Turf": (("Rock", 300, 1.0, 0.0, 0.0), (0.16, 0.4, 0.12)), "Rubber": (("Concrete", 150, 1.0, 0.0, 0.0), (0.6, 0.2, 0.12)),
    "Water": (("Tile", 200, 0.05, 0.0, 0.0), (0.08, 0.35, 0.55)), "Gravel": (("Rock", 80, 1.0, 0.0, 0.0), (0.45, 0.44, 0.42)),
    "Sand": (("Concrete", 300, 1.0, 0.0, 0.0), (0.86, 0.76, 0.55)), "Hull": (("Frame", 200, 0.35, 0.1, 0.0), (0.92, 0.92, 0.94)),
    "Steel": (("Frame", 200, 0.5, 0.8, 0.0), (0.8, 0.45, 0.1)),
    "PaintWhite": (("Concrete", 400, 0.6, 0.0, 0.0), (0.95, 0.95, 0.95)),
    "PaintYellow": (("Concrete", 400, 0.6, 0.0, 0.0), (0.92, 0.75, 0.1)),
    "LampAmber": (("Concrete", 400, 0.5, 0.0, 8.0), (1.0, 0.75, 0.3)),
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


LINE_CM = 10  # pitch lines: 10 cm wide, 1 cm proud of the turf


def _pitch(L: float, W: float, circle: float, box_l: float, box_w: float, goal_w: float, goal_h: float) -> list:
    """A football pitch at its real size, never stretched: turf with 2 m run-off all round (slot 0), its lines
    (slot 1: touch and goal lines, halfway line, centre circle and spot, both penalty boxes) and two goals
    (slot 2: posts and crossbar just behind each goal line). Centred, X along the length, top of the turf z 2."""
    t, z = LINE_CM, 2
    out = [("box", 0, (0, 0, 0), (0, 0, 0), (L + 400, W + 400, 2)),
           ("box", 1, (0, W / 2, z), (0, 0, 0), (L + t, t, 1)), ("box", 1, (0, -W / 2, z), (0, 0, 0), (L + t, t, 1)),
           ("box", 1, (L / 2, 0, z), (0, 0, 0), (t, W, 1)), ("box", 1, (-L / 2, 0, z), (0, 0, 0), (t, W, 1)),
           ("box", 1, (0, 0, z), (0, 0, 0), (t, W, 1)), ("cyl", 1, (0, 0, z), (0, 0, 0), (15, 1, 12))]
    for k in range(24):  # the centre circle, 24 short strips
        a = k * 15.0
        out.append(("box", 1, (circle * math.cos(math.radians(a)), circle * math.sin(math.radians(a)), z), (0, 0, a + 90),
                    (2 * math.pi * circle / 24 + 2, t, 1)))
    for s in (-1, 1):
        front = s * (L / 2 - box_l)
        out += [("box", 1, (front, 0, z), (0, 0, 0), (t, box_w, 1)),
                ("box", 1, (s * (L / 2 - box_l / 2), box_w / 2, z), (0, 0, 0), (box_l, t, 1)),
                ("box", 1, (s * (L / 2 - box_l / 2), -box_w / 2, z), (0, 0, 0), (box_l, t, 1)),
                ("cyl", 2, (s * (L / 2 + 6), goal_w / 2, z), (0, 0, 0), (6, goal_h, 10)),
                ("cyl", 2, (s * (L / 2 + 6), -goal_w / 2, z), (0, 0, 0), (6, goal_h, 10)),
                ("box", 2, (s * (L / 2 + 6), 0, z + goal_h - 12), (0, 0, 0), (12, goal_w + 12, 12)),
                ("box", 2, (s * (L / 2 + 120), 0, z), (0, 0, 0), (8, goal_w, 8))]
    return out


# football pitches: (length, width, centre circle radius, penalty box depth, its width, goal width, goal height) cm
PITCHES = {"SM_Kit_Pitch5": (4000, 2000, 300, 600, 1000, 300, 200),     # 5-a-side
           "SM_Kit_Pitch7": (6000, 4000, 600, 1000, 2400, 500, 200),    # 7-a-side
           "SM_Kit_Pitch11": (10000, 6400, 915, 1650, 4030, 732, 244)}  # full size


def _swings() -> list:
    out = [("cyl", 0, (x, y, 0), (0, 0, 0), (5, 260, 10)) for x in (-200, 200) for y in (-60, 60)]
    out.append(("box", 0, (0, 0, 250), (0, 0, 0), (412, 10, 10)))
    for x in (-90, 90):
        out += [("box", 1, (x, 0, 45), (0, 0, 0), (50, 22, 5)),
                ("box", 0, (x - 22, 0, 50), (0, 0, 0), (2, 2, 200)), ("box", 0, (x + 22, 0, 50), (0, 0, 0), (2, 2, 200))]
    return out


def _slide() -> list:
    out = [("cyl", 0, (x, y, 0), (0, 0, 0), (5, 260, 10)) for x in (-60, 60) for y in (-60, 60)]
    out += [("box", 2, (0, 0, 150), (0, 0, 0), (130, 130, 8)),  # the deck, 1.5 m up
            ("box", 0, (0, 62, 158), (0, 0, 0), (130, 4, 80)), ("box", 0, (0, -62, 158), (0, 0, 0), (130, 4, 80)),
            ("box", 0, (-80, 30, 0), (0, 0, 0), (5, 5, 158)), ("box", 0, (-80, -30, 0), (0, 0, 0), (5, 5, 158))]
    out += [("box", 0, (-80, 0, 25 + 30 * k), (0, 0, 0), (5, 60, 4)) for k in range(5)]  # ladder rungs
    out += [("box", 1, (200, 0, 70), (0, -30, 0), (300, 60, 5)),  # the chute, down 30 degrees to +X
            ("box", 1, (200, 31, 70), (0, -30, 0), (300, 4, 22)), ("box", 1, (200, -31, 70), (0, -30, 0), (300, 4, 22))]
    return out


def _climber() -> list:
    out = [("cyl", 0, (x, y, 0), (0, 0, 0), (4, 200, 8)) for x in (-120, 0, 120) for y in (-120, 0, 120)]
    for z in (100, 196):
        out += [("box", 1, (0, y, z), (0, 0, 0), (244, 6, 6)) for y in (-120, 0, 120)]
        out += [("box", 1, (x, 0, z), (0, 0, 0), (6, 244, 6)) for x in (-120, 0, 120)]
    return out


def _seesaw() -> list:
    return [("box", 0, (0, 0, 0), (0, 0, 0), (30, 40, 50)), ("box", 1, (0, 0, 50), (0, 0, 0), (360, 30, 6)),
            ("cyl", 0, (150, 0, 56), (0, 0, 0), (3, 30, 8)), ("cyl", 0, (-150, 0, 56), (0, 0, 0), (3, 30, 8))]


def _roundabout() -> list:
    out = [("cyl", 0, (0, 0, 0), (0, 0, 0), (20, 15, 12)), ("cyl", 1, (0, 0, 15), (0, 0, 0), (100, 8, 24))]
    out += [("box", 0, (55 * math.cos(math.radians(a)), 55 * math.sin(math.radians(a)), 23), (0, 0, a), (80, 4, 4))
            for a in (0, 90, 180, 270)]
    return out


def _sandbox() -> list:
    out = [("box", 1, (0, 0, 0), (0, 0, 0), (280, 280, 12))]
    out += [("box", 0, (x, 0, 0), (0, 0, 0), (10, 300, 25)) for x in (-145, 145)]
    out += [("box", 0, (0, y, 0), (0, 0, 0), (280, 10, 25)) for y in (-145, 145)]
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
    "SM_Path_DrivewayDouble_10m": (["Asphalt"], [("slab", 0, (500, 0, -12), (0, 0, 0), (1000, 550, 12, 10))], "Roads"),
    # a subdivision / apartment complex street: 8 m asphalt with a 15 cm kerb strip each side
    "SM_Path_PrivateStreet_10m": (["Asphalt", "Plinth"], [("slab", 0, (500, 0, -15), (0, 0, 0), (1000, 800, 15, 10)),
                                                         ("slab", 1, (500, 405, -15), (0, 0, 0), (1000, 10, 17, 10)),
                                                         ("slab", 1, (500, -405, -15), (0, 0, 0), (1000, 10, 17, 10))],
                                  "Roads"),
    # parking: a painted stall (two white lines 5.5 m long, 2.6 m apart) and a cul-de-sac turnaround (r 10 m)
    "SM_Kit_ParkingStall": (["Panel"], [("box", 0, (0, -125, 0), (0, 0, 0), (550, 10, 2)),
                                        ("box", 0, (0, 125, 0), (0, 0, 0), (550, 10, 2))]),
    "SM_Kit_Turnaround": (["Asphalt"], [("cyl", 0, (0, 0, -10), (0, 0, 0), (1000, 10, 24))]),
    # a yard's timber fence along a lot line: 1 m long, 10 cm thick, 2.5 m tall (placed at 0.6: 1.5 m)
    "SM_Kit_Fence": (["Wood"], [("box", 0, (0, 0, 0), (0, 0, 0), (100, 10, 250))]),
    # the sidewalk carried round a junction corner: a round 4 m paving pad (any corner angle), top at z 0
    "SM_Kit_SidewalkCorner": (["Pavers"], [("cyl", 0, (0, 0, -15), (0, 0, 0), (200, 15, 16))]),
    # (bushes and rocks are the Blender nature kit now, WorldGen/Nature: kit_bake.KIT; never built from boxes here)
    # a street tree's pit in the paving: soil square with a steel grate frame, flush with the sidewalk
    "SM_Kit_TreePit": (["RockGrey", "Metal"], [("box", 0, (0, 0, -2), (0, 0, 0), (150, 150, 3)),
                                               ("box", 1, (70, 0, -1), (0, 0, 0), (10, 150, 3)),
                                               ("box", 1, (-70, 0, -1), (0, 0, 0), (10, 150, 3)),
                                               ("box", 1, (0, 70, -1), (0, 0, 0), (130, 10, 3)),
                                               ("box", 1, (0, -70, -1), (0, 0, 0), (130, 10, 3))]),
    # the planned developments' grounds: 1 m unit slabs (stretched to the part), top at their height
    "SM_Kit_Apron": (["Plinth"], [("box", 0, (0, 0, 0), (0, 0, 0), (100, 100, 10))]),
    "SM_Kit_SportsField": (["Turf"], [("box", 0, (0, 0, 0), (0, 0, 0), (100, 100, 2))]),
    # real-size pitches (never stretched) and playground equipment, placed at scale 1 (pcg_layers.site_layers)
    **{n: (["Turf", "PaintWhite", "Metal"], _pitch(*dims)) for n, dims in PITCHES.items()},
    "SM_Kit_PG_Swings": (["Metal", "SignRed"], _swings()),
    "SM_Kit_PG_Slide": (["Metal", "SignRed", "Wood"], _slide()),
    "SM_Kit_PG_Climber": (["Metal", "SignGreen"], _climber()),
    "SM_Kit_PG_SeeSaw": (["Metal", "Wood"], _seesaw()),
    "SM_Kit_PG_Roundabout": (["Metal", "SignRed"], _roundabout()),
    "SM_Kit_PG_Sandbox": (["Wood", "Sand"], _sandbox()),
    "SM_Kit_Playground": (["Rubber"], [("box", 0, (0, 0, 0), (0, 0, 0), (100, 100, 5))]),
    "SM_Kit_Pool": (["Panel", "Water"], [("box", 0, (0, 0, 0), (0, 0, 0), (100, 100, 10)),
                                         ("box", 1, (0, 0, 9), (0, 0, 0), (80, 80, 2))]),
    "SM_Kit_UtilityYard": (["Gravel"], [("box", 0, (0, 0, 0), (0, 0, 0), (100, 100, 5))]),
    "SM_Kit_Pond": (["Water"], [("box", 0, (0, 0, -5), (0, 0, 0), (100, 100, 5))]),
    # water tower: four legs, a shaft and a round tank 30 m up
    "SM_Kit_WaterTower": (["Metal", "Panel"], [("cyl", 0, (300, 300, 0), (0, 0, 0), (25, 2400, 8)),
                                               ("cyl", 0, (-300, 300, 0), (0, 0, 0), (25, 2400, 8)),
                                               ("cyl", 0, (300, -300, 0), (0, 0, 0), (25, 2400, 8)),
                                               ("cyl", 0, (-300, -300, 0), (0, 0, 0), (25, 2400, 8)),
                                               ("cyl", 0, (0, 0, 0), (0, 0, 0), (80, 2400, 12)),
                                               ("sph", 1, (0, 0, 2550), (0, 0, 0), (550, 8, 10))]),
    # waterfront (backend/waterfront.py): decks and slabs are 1 m units stretched to each piece
    "SM_Kit_DockDeck": (["Wood"], [("box", 0, (0, 0, 0), (0, 0, 0), (100, 100, 30))]),
    "SM_Kit_FloatDock": (["Panel", "Wood"], [("box", 0, (0, 0, 0), (0, 0, 0), (100, 100, 40)),
                                              ("box", 1, (0, 0, 40), (0, 0, 0), (100, 100, 10))]),
    "SM_Kit_Piling": (["Wood"], [("cyl", 0, (0, 0, 0), (0, 0, 0), (15, 100, 8))]),
    "SM_Kit_BoatSlip": (["Panel"], [("box", 0, (0, -220, 0), (0, 0, 0), (1200, 8, 5)),
                                     ("box", 0, (0, 220, 0), (0, 0, 0), (1200, 8, 5))]),
    "SM_Kit_Boat": (["Hull", "Wood"], [("box", 0, (0, 0, 0), (0, 0, 0), (800, 280, 120)),
                                       ("box", 0, (420, 0, 30), (0, 0, 45), (110, 110, 90)),
                                       ("box", 1, (-150, 0, 120), (0, 0, 0), (250, 200, 80))]),
    "SM_Kit_Seawall": (["Plinth"], [("box", 0, (0, 0, 0), (0, 0, 0), (100, 60, 100))]),
    "SM_Kit_Riprap": (["RockGrey"], [("box", 0, (-20, 10, 0), (10, 15, 30), (80, 70, 90)),
                                      ("box", 0, (25, -15, 0), (-12, 8, 60), (70, 75, 80))]),
    "SM_Kit_Sand": (["Sand"], [("box", 0, (0, 0, 0), (0, 0, 0), (100, 100, 10))]),
    "SM_Kit_Wetland": (["Leaf"], [("box", 0, (0, 0, 0), (0, 0, 0), (100, 100, 20)),
                                  ("cyl", 0, (-25, -20, 0), (0, 0, 0), (12, 60, 6)),
                                  ("cyl", 0, (25, 20, 0), (0, 0, 0), (12, 55, 6))]),
    "SM_Kit_PortCrane": (["Steel"], [("box", 0, (-1100, -900, 0), (0, 0, 0), (100, 100, 3500)),
                                     ("box", 0, (-1100, 900, 0), (0, 0, 0), (100, 100, 3500)),
                                     ("box", 0, (1100, -900, 0), (0, 0, 0), (100, 100, 3500)),
                                     ("box", 0, (1100, 900, 0), (0, 0, 0), (100, 100, 3500)),
                                     ("box", 0, (0, 0, 3500), (0, 0, 0), (3000, 300, 250)),
                                     ("box", 0, (0, 0, 3750), (0, 0, 0), (600, 800, 400))]),
    "SM_Kit_ContainerStack": (["Steel", "Metal"], [("box", 0, (0, 0, 0), (0, 0, 0), (1220, 245, 260)),
                                                   ("box", 1, (0, 0, 260), (0, 0, 0), (1220, 245, 260)),
                                                   ("box", 0, (0, 0, 520), (0, 0, 0), (1220, 245, 260))]),
    "SM_Kit_BoatLaunch": (["Plinth"], [("box", 0, (0, 0, 0), (0, 0, 0), (100, 100, 20))]),
    "SM_Kit_StiltHouse": (["Wood", "Fabric"], [("box", 0, (0, 0, 0), (0, 0, 0), (1000, 800, 40)),
                                                ("box", 0, (0, 0, 40), (0, 0, 0), (800, 600, 300)),
                                                ("box", 1, (0, 0, 340), (0, 0, 0), (900, 700, 120))]),
    "SM_Kit_FloatHome": (["Panel", "Light"], [("box", 0, (0, 0, 0), (0, 0, 0), (900, 600, 300)),
                                              ("box", 1, (460, 0, 60), (0, 0, 0), (10, 500, 200)),
                                              ("box", 0, (0, 0, 300), (0, 0, 0), (1000, 700, 40))]),
    # a stay cable (citygen.bridge_towers): one bar from 1 m up over the origin down to 1 m out along +X, so the
    # row's X scale is how far out it lands and its Z scale how high it starts. Thin on X (it thickens with both
    # scales: about 20 to 50 cm on a real cable); a bridge tower's legs and beams are the pier and cap pieces
    "SM_Kit_BridgeStay": (["Steel"], [("box", 0, (100, 0, 0), (0, 45, 0), (0.6, 12, 141.42))]),
    # bridge piers: a 2 x 2 m column 1 m tall (stretched to its height) and a cap beam 1 m across (stretched to the
    # deck's width), both in concrete; placed by citygen.bridge_piers every 30 m under every deck
    "SM_Kit_BridgePier": (["Plinth"], [("box", 0, (0, 0, 0), (0, 0, 0), (200, 200, 100))]),
    "SM_Kit_PierCap": (["Plinth"], [("box", 0, (0, 0, 0), (0, 0, 0), (200, 100, 120))]),
    # a bridge's abutment (citygen.bridge_abutments), 1 m wide and stretched on Y to the deck: the wall under the
    # deck's end (its face at x 0), the bearing shelf the girders sit on, a wing wall back along each side
    "SM_Kit_BridgeAbutment": (["Plinth"], [("box", 0, (-50, 0, 0), (0, 0, 0), (100, 100, 250)),
                                           ("box", 0, (30, 0, 0), (0, 0, 0), (60, 100, 130)),
                                           ("box", 0, (-250, 48.5, 0), (0, 0, 0), (400, 3, 250)),
                                           ("box", 0, (-250, -48.5, 0), (0, 0, 0), (400, 3, 250))]),
    # a low deck's or ramp's approach wall (citygen.deck_fills): a 1 m concrete block, its origin at the middle of
    # its base, stretched along the deck, across its underside and up from 1 m in the ground into the slab
    "SM_Kit_ApproachWall": (["Plinth"], [("box", 0, (0, 0, 0), (0, 0, 0), (100, 100, 100))]),
    # shipyards (backend/shipyard.py)
    "SM_Kit_ShipHull": (["Steel", "Hull"], [("box", 0, (0, 0, 0), (0, 0, 0), (9000, 1900, 1400)),
                                           ("box", 0, (4700, 0, 300), (0, 0, 45), (900, 900, 1100)),
                                           ("box", 1, (0, 0, 1400), (0, 0, 0), (9400, 2000, 60)),
                                           ("box", 1, (-3600, 0, 1460), (0, 0, 0), (1400, 1600, 900))]),
    "SM_Kit_GoliathCrane": (["Steel"], [("box", 0, (0, -3300, 0), (0, 0, 0), (1600, 300, 7200)),
                                        ("box", 0, (0, 3300, 0), (0, 0, 0), (1600, 300, 7200)),
                                        ("box", 0, (0, 0, 7200), (0, 0, 0), (1200, 7000, 600)),
                                        ("box", 0, (0, 0, 7800), (0, 0, 0), (600, 800, 200))]),
    "SM_Kit_PortalCrane": (["Steel"], [("box", 0, (0, -450, 0), (0, 0, 0), (150, 150, 2200)),
                                       ("box", 0, (0, 450, 0), (0, 0, 0), (150, 150, 2200)),
                                       ("box", 0, (0, 0, 2200), (0, 0, 0), (500, 1100, 1200)),
                                       ("box", 0, (1400, 0, 3400), (0, 0, 0), (3200, 150, 150))]),
    "SM_Kit_TowerCrane": (["Steel"], [("box", 0, (0, 0, 0), (0, 0, 0), (200, 200, 5600)),
                                      ("box", 0, (2200, 0, 5600), (0, 0, 0), (5600, 120, 150)),
                                      ("box", 0, (-200, 0, 5300), (0, 0, 0), (300, 300, 300))]),
    "SM_Kit_CraneRail": (["Metal"], [("box", 0, (0, 0, 0), (0, 0, 0), (100, 100, 20))]),
    "SM_Kit_KeelBlock": (["Wood", "Plinth"], [("box", 1, (0, 0, 0), (0, 0, 0), (200, 150, 100)),
                                              ("box", 0, (0, 0, 100), (0, 0, 0), (200, 150, 50))]),
    "SM_Kit_SteelStack": (["Metal"], [("box", 0, (0, 0, 0), (0, 0, 0), (1200, 300, 150))]),
    "SM_Kit_RailTrack": (["Gravel", "Metal"], [("box", 0, (0, 0, 0), (0, 0, 0), (100, 300, 10)),
                                               ("box", 1, (0, -72, 10), (0, 0, 0), (100, 8, 10)),
                                               ("box", 1, (0, 72, 10), (0, 0, 0), (100, 8, 10))]),
    "SM_Kit_FloatingDryDock": (["Steel", "Panel"], [("box", 1, (0, 0, 0), (0, 0, 0), (20000, 4500, 600)),
                                                    ("box", 0, (0, -2000, 600), (0, 0, 0), (20000, 500, 1200)),
                                                    ("box", 0, (0, 2000, 600), (0, 0, 0), (20000, 500, 1200))]),
    "SM_Kit_FuelTank": (["Panel"], [("cyl", 0, (0, 0, 0), (0, 0, 0), (1000, 1200, 20))]),
    "SM_Kit_Fence": (["Metal"], [("box", 0, (0, 0, 0), (0, 0, 0), (100, 6, 250))]),
    "SM_Kit_BoatLift": (["Steel"], [("box", 0, (-500, -350, 0), (0, 0, 0), (100, 100, 1000)),
                                    ("box", 0, (500, -350, 0), (0, 0, 0), (100, 100, 1000)),
                                    ("box", 0, (-500, 350, 0), (0, 0, 0), (100, 100, 1000)),
                                    ("box", 0, (500, 350, 0), (0, 0, 0), (100, 100, 1000)),
                                    ("box", 0, (0, -350, 900), (0, 0, 0), (1200, 100, 100)),
                                    ("box", 0, (0, 350, 900), (0, 0, 0), (1200, 100, 100))]),
    "SM_Kit_OffshoreJacket": (["Steel"], [("box", 0, (-800, -800, 0), (0, 0, 0), (150, 150, 6000)),
                                          ("box", 0, (800, -800, 0), (0, 0, 0), (150, 150, 6000)),
                                          ("box", 0, (-800, 800, 0), (0, 0, 0), (150, 150, 6000)),
                                          ("box", 0, (800, 800, 0), (0, 0, 0), (150, 150, 6000)),
                                          ("box", 0, (0, 0, 3000), (0, 0, 0), (1700, 1700, 100)),
                                          ("box", 0, (0, 0, 5900), (0, 0, 0), (2000, 2000, 100))]),
    "SM_Kit_ScrapPile": (["Steel", "RockGrey"], [("box", 0, (0, 0, 0), (8, 5, 20), (1400, 1200, 350)),
                                                 ("box", 1, (300, -200, 0), (-6, 10, 50), (900, 800, 250))]),
    "SM_Kit_Gatehouse": (["Panel", "Light"], [("box", 0, (0, 0, 0), (0, 0, 0), (800, 600, 350)),
                                              ("box", 1, (410, 0, 120), (0, 0, 0), (10, 400, 150))]),
    # graving dry dock (road piece, 10 m along +X): floor at z 0, walls to 12 m, a walkway on each wall
    "SM_Water_DryDock_10m": (["Plinth", "Metal"], [("box", 0, (500, 0, 0), (0, 0, 0), (1000, 4000, 50)),
                                                   ("box", 0, (500, -1950, 0), (0, 0, 0), (1000, 100, 1200)),
                                                   ("box", 0, (500, 1950, 0), (0, 0, 0), (1000, 100, 1200)),
                                                   ("box", 1, (500, -2050, 1180), (0, 0, 0), (1000, 120, 20)),
                                                   ("box", 1, (500, 2050, 1180), (0, 0, 0), (1000, 120, 20))],
                             "Roads"),
    # airports (backend/airport.py): runway / taxiway road pieces along +X, pavement top at z 0
    "SM_Air_Runway_20m": (["Asphalt", "PaintWhite"], [("slab", 0, (1000, 0, -20), (0, 0, 0), (2000, 4500, 20, 10)),
                                                      ("box", 1, (500, 0, 0), (0, 0, 0), (1000, 90, 1)),
                                                      ("box", 1, (1000, -1950, 0), (0, 0, 0), (2000, 90, 1)),
                                                      ("box", 1, (1000, 1950, 0), (0, 0, 0), (2000, 90, 1))],
                          "Roads"),
    "SM_Air_RunwaySmall_20m": (["Asphalt", "PaintWhite"], [("slab", 0, (1000, 0, -20), (0, 0, 0), (2000, 1800, 20, 10)),
                                                           ("box", 1, (500, 0, 0), (0, 0, 0), (1000, 45, 1))],
                               "Roads"),
    "SM_Air_Taxiway_10m": (["Asphalt", "PaintYellow"], [("slab", 0, (500, 0, -20), (0, 0, 0), (1000, 2300, 20, 10)),
                                                        ("box", 1, (500, 0, 0), (0, 0, 0), (1000, 15, 1))],
                           "Roads"),
    "SM_Air_Airliner": (["Hull", "Panel"], [("cyl", 0, (-1900, 0, 300), (0, 90, 0), (200, 3800, 16)),
                                             ("box", 0, (100, 0, 380), (0, 0, 0), (600, 3400, 40)),
                                             ("box", 0, (-1700, 0, 420), (0, 0, 0), (300, 1200, 30)),
                                             ("box", 1, (-1750, 0, 500), (0, 0, 0), (400, 30, 700)),
                                             ("box", 1, (300, -800, 150), (0, 0, 0), (300, 120, 120)),
                                             ("box", 1, (300, 800, 150), (0, 0, 0), (300, 120, 120))]),
    "SM_Air_SmallPlane": (["Hull"], [("box", 0, (0, 0, 80), (0, 0, 0), (850, 120, 120)),
                                     ("box", 0, (100, 0, 190), (0, 0, 0), (150, 1100, 15)),
                                     ("box", 0, (-380, 0, 150), (0, 0, 0), (80, 15, 150))]),
    "SM_Air_JetBridge": (["Panel", "Metal"], [("box", 0, (0, 0, 300), (0, 0, 0), (3000, 300, 280)),
                                              ("box", 1, (900, 0, 0), (0, 0, 0), (60, 60, 300))]),
    "SM_Air_Hangar": (["Panel", "Metal"], [("box", 0, (0, 0, 0), (0, 0, 0), (6000, 5000, 1600)),
                                           ("box", 0, (0, 0, 1600), (0, 0, 0), (6000, 4600, 600)),
                                           ("box", 1, (0, 2510, 50), (0, 0, 0), (5600, 20, 1400))]),
    "SM_Air_Shelter": (["Plinth", "Metal"], [("box", 0, (0, 0, 0), (0, 0, 0), (3000, 4000, 800)),
                                             ("box", 0, (0, 0, 800), (0, 0, 0), (2400, 4000, 400)),
                                             ("box", 1, (0, 2010, 50), (0, 0, 0), (2400, 20, 700))]),
    "SM_Air_ControlTower": (["Plinth", "Light"], [("cyl", 0, (0, 0, 0), (0, 0, 0), (250, 3600, 16)),
                                                  ("cyl", 1, (0, 0, 3600), (0, 0, 0), (600, 500, 12)),
                                                  ("cyl", 0, (0, 0, 4100), (0, 0, 0), (620, 60, 12))]),
    "SM_Air_Radar": (["Metal", "Panel"], [("box", 0, (0, 0, 0), (0, 0, 0), (200, 200, 1000)),
                                          ("box", 1, (0, 0, 1000), (0, 0, 0), (100, 800, 400))]),
    "SM_Air_Windsock": (["Metal", "PaintYellow"], [("cyl", 0, (0, 0, 0), (0, 0, 0), (8, 600, 6)),
                                                   ("box", 1, (0, 150, 550), (0, 0, 0), (60, 300, 60))]),
    "SM_Air_EdgeLight": (["LampAmber"], [("cyl", 0, (0, 0, 0), (0, 0, 0), (15, 50, 6))]),
    "SM_Air_ApproachBar": (["Metal", "LampAmber"], [("box", 0, (0, 0, 0), (0, 0, 0), (30, 3000, 150)),
                                                    ("box", 1, (0, 0, 150), (0, 0, 0), (40, 3000, 40))]),
    "SM_Air_Threshold": (["PaintWhite"], [("box", 0, (0, -1800, 0), (0, 0, 0), (3000, 180, 2)),
                                          ("box", 0, (0, -1200, 0), (0, 0, 0), (3000, 180, 2)),
                                          ("box", 0, (0, -600, 0), (0, 0, 0), (3000, 180, 2)),
                                          ("box", 0, (0, 600, 0), (0, 0, 0), (3000, 180, 2)),
                                          ("box", 0, (0, 1200, 0), (0, 0, 0), (3000, 180, 2)),
                                          ("box", 0, (0, 1800, 0), (0, 0, 0), (3000, 180, 2))]),
    "SM_Air_HoldSign": (["SignRed"], [("box", 0, (0, 0, 0), (0, 0, 0), (30, 300, 150))]),
    "SM_Air_GSE": (["PaintYellow", "Metal"], [("box", 0, (0, 0, 50), (0, 0, 0), (600, 280, 150)),
                                              ("box", 1, (-200, 0, 200), (0, 0, 0), (180, 260, 50))]),
    # canal channel (road piece, 10 m along +X): bed at z 0, quay walls to 3 m, water 1.5 m up
    "SM_Water_Canal_10m": (["Plinth", "Water"], [("box", 0, (500, 0, 0), (0, 0, 0), (1000, 2400, 20)),
                                                 ("box", 0, (500, -1170, 0), (0, 0, 0), (1000, 60, 300)),
                                                 ("box", 0, (500, 1170, 0), (0, 0, 0), (1000, 60, 300)),
                                                 ("box", 1, (500, 0, 150), (0, 0, 0), (1000, 2280, 5))],
                           "Roads"),
    "SM_Kit_ShopShelf": (["Metal", "Goods"], [("box", 0, (0, 0, 0), (0, 0, 0), (60, 200, 180)),
                                              ("box", 1, (31, 0, 40), (0, 0, 0), (4, 180, 30)),
                                              ("box", 1, (31, 0, 110), (0, 0, 0), (4, 180, 30))]),
}


@register("road_network_street_kit")
def road_network_street_kit(root: str = "", rebuild: bool = False, plan: bool = False, only: list | None = None,
                            save: bool = True, extra: dict | None = None) -> dict:
    """Make the street pieces the kit lacks (traffic lights, stop / street signs, billboards, power poles,
    parking lot, path slab, plinth) as GeometryScript meshes in WorldGen/Kit/Street, in M_City_WorldPBR
    instances. Mesh and material assets only; safe to run again (rebuilt in place).
    plan=true only lists what is missing; only=[names] builds just those; save=false leaves saving to one
    road_network_save_assets call. Call it once per mesh (the backend does): each mesh build opens a
    progress window whose GPU swap chain UEFN frees only at the end of the frame.
    extra: more pieces in the same form, {name: [looks, parts, folder under WorldGen]} (the backend's road
    pieces, built from their cross-sections when the project has no model of them)."""
    if not root.startswith("/"):
        return {"ok": False, "error": "Give root, the project's content root (for example /<mount>/MovieTheater)."}
    eal = unreal.EditorAssetLibrary
    mat_folder, folder = f"{root}/WorldGen/Materials", f"{root}/WorldGen/Kit/Street"
    for f in (mat_folder, folder):
        if not eal.does_directory_exist(f):
            eal.make_directory(f)
    # only the pieces that aren't there yet (rebuild=true remakes all): remaking and saving all of them
    # on every PCG build kept UEFN busy for minutes
    kit = {**_STREET_KIT, **{n: tuple(v) for n, v in (extra or {}).items()}}
    where = {n: (f"{root}/WorldGen/{v[2]}" if len(v) > 2 else folder) for n, v in kit.items()}
    todo = {n: v for n, v in kit.items()
            if (only is None or n in only) and (rebuild or not eal.does_asset_exist(f"{where[n]}/{n}"))}
    if plan:
        return {"ok": True, "todo": list(todo)}
    if not todo:
        return {"ok": True, "made": [], "paths": [], "errors": [], "note": "every street kit piece is already there"}
    for n in todo:
        if not eal.does_directory_exist(where[n]):
            eal.make_directory(where[n])
    try:
        master = _world_pbr_master(mat_folder, root)
    except Exception as exc:  # noqa: BLE001 - nothing can be built without it: say why, never a NoneType crash
        return {"ok": False, "made": [], "paths": [], "errors": [f"{_MASTER}: {exc}"[:200]]}
    need = {m for mats, *_ in todo.values() for m in mats}  # some pieces carry a 3rd value: their folder
    looks, errors = {}, []
    for k, (look, tint) in _STREET_LOOKS.items():
        if k in need:
            try:
                looks[k] = _mi(mat_folder, f"MI_Street_{k}", master, root, look, unreal.LinearColor(*tint, 1))
            except Exception as exc:  # noqa: BLE001 - one bad look skips only the pieces that wear it
                errors.append(f"MI_Street_{k}: {exc}"[:200])
    todo, skipped = _pieces_without(todo, need - set(looks))
    errors += [f"{n}: skipped, its material couldn't be made" for n in skipped]
    gp = unreal.GeometryScript_Primitives
    base = unreal.GeometryScriptPrimitiveOriginMode.BASE
    made = []
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
                elif kind == "poly":  # a top-view outline [[x, y], ...] extruded up by its height (road connectors)
                    gp.append_simple_extruded_polygon(dm, o, t, [unreal.Vector2D(x, y) for x, y in dims[0]],
                                                      height=dims[1], height_steps=0, capped=True, origin=base)
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
                if sm is None:
                    raise RuntimeError(f"couldn't make the mesh asset {path}")
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


_LOD_STEPS = ((1.0, 1.0), (0.5, 0.3), (0.25, 0.1))  # (triangles kept, screen size) for LOD0..2
_LOD_MIN_VERTS = 200  # the Valkyrie validator calls a one-LOD mesh above about this "complex"


def _lod_ready(sm) -> bool:
    """Give a complex one-LOD mesh LODs and a Min LOD of 1 on Low and Medium (ValkyrieValidator_StaticMesh asks
    for both). Nanite and tiny meshes are left alone. True when it changed the mesh."""
    sub = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    if sm.get_editor_property("nanite_settings").get_editor_property("enabled"):
        return False
    changed = False
    if sub.get_lod_count(sm) < 2:
        if sub.get_number_verts(sm, 0) < _LOD_MIN_VERTS:
            return False
        opts = unreal.StaticMeshReductionOptions()
        opts.set_editor_property("auto_compute_lod_screen_size", False)
        opts.set_editor_property("reduction_settings", [unreal.StaticMeshReductionSettings(
            percent_triangles=t, screen_size=s) for t, s in _LOD_STEPS])
        sub.set_lods(sm, opts)
        changed = True
    lows = dict(sm.get_min_lod_for_quality_levels()[0])
    if lows.get(unreal.PerQualityLevels.LOW, 0) < 1 or lows.get(unreal.PerQualityLevels.MEDIUM, 0) < 1:
        sm.set_min_lod_for_quality_levels({unreal.PerQualityLevels.LOW: 1, unreal.PerQualityLevels.MEDIUM: 1}, -1)
        changed = True
    return changed


@register("road_network_lod_ready")
def road_network_lod_ready(meshes: list | None = None, folder: str = "", offset: int = 0, limit: int = 40) -> dict:
    """_lod_ready on these meshes, or on a slice of a folder's meshes (offset / limit: a few per call, each
    LOD build is a mesh rebuild). Saves what it changed. Returns fixed, the folder's total and next offset."""
    paths = [p.split(".")[0] for p in meshes or []]
    total = len(paths)
    if folder:
        every = _kit_meshes(folder)
        total = len(every)
        paths = [p.split(".")[0] for p in every[int(offset):int(offset) + int(limit)]]
    fixed = []
    for path in paths:
        sm = unreal.load_asset(path)
        if isinstance(sm, unreal.StaticMesh) and _lod_ready(sm):
            fixed.append(sm)
    _save_batch(fixed)
    nxt = int(offset) + len(paths)
    return {"ok": True, "fixed": len(fixed), "checked": len(paths), "total": total,
            "next_offset": nxt if folder and nxt < total else None}


@register("road_network_collision_ready")
def road_network_collision_ready(meshes: list | None = None) -> dict:
    """Which of these meshes can't stop a player yet: no simple collision shapes (an FBX with no UCX boxes) and
    not complex-as-simple either. Read-only; the backend switches those to complex-as-simple (their own
    triangles collide) through Epic's StaticMeshToolset. Returns `need` and `missing` (object paths)."""
    need, missing = [], []
    for path in meshes or []:
        sm = unreal.load_asset(path.split(".")[0])
        body = sm.get_editor_property("body_setup") if isinstance(sm, unreal.StaticMesh) else None
        if body is None:
            missing.append(path)
        elif unreal.EditorStaticMeshLibrary.get_simple_collision_count(sm) <= 0 and body.get_editor_property(
                "collision_trace_flag") != unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE:
            need.append(path)
    return {"ok": True, "need": need, "missing": missing}


@register("road_network_collision_count")
def road_network_collision_count(prefix: str = "", solid: list | None = None, fix: bool = True) -> dict:
    """Count collision on what a PCG build really placed: every instanced mesh component on actors labelled
    `prefix...`. A component of a `solid` mesh (object paths) left at No Collision is switched to Query and
    Physics when fix=true (players AND physics cars stop). Returns per-mesh counts."""
    if not prefix:
        return {"ok": False, "error": "Give prefix, the build's actor label start (City Builder <world>)."}
    want = {p.split(".")[0] for p in solid or []}
    sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    blocking = off = fixed = instances = 0
    open_meshes: dict = {}
    for actor in sub.get_all_level_actors():
        if not actor.get_actor_label().startswith(prefix):
            continue
        for comp in actor.get_components_by_class(unreal.InstancedStaticMeshComponent):
            sm = comp.get_editor_property("static_mesh")
            path = sm.get_path_name().split(".")[0] if sm else ""
            instances += comp.get_instance_count()
            if comp.get_collision_enabled() != unreal.CollisionEnabled.NO_COLLISION:
                blocking += 1
            elif path in want:
                if fix:
                    comp.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS)
                    fixed += 1
                else:
                    open_meshes[path.rsplit("/", 1)[-1]] = open_meshes.get(path.rsplit("/", 1)[-1], 0) + 1
            else:
                off += 1  # leaves, flat paint: no collision on purpose
    return {"ok": True, "components_blocking": blocking, "components_fixed": fixed, "components_no_collision": off,
            "solid_still_open": open_meshes, "instances": instances}


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


def _prop_class(path: str):
    """The class the Content Drawer places for a Fortnite prop ('/Game/.../CP_X' -> CP_X_C), or None."""
    for load in (lambda: unreal.EditorAssetLibrary.load_blueprint_class(path),
                 lambda: unreal.load_class(None, f"{path}.{path.rsplit('/', 1)[-1]}_C")):
        try:
            cls = load()
        except Exception:  # noqa: BLE001 - the next way of loading it is tried
            cls = None
        if cls is not None:
            return cls
    return None


@register("road_network_props")
def road_network_props(remove: list | None = None, place: list | None = None, folder: str = "") -> dict:
    """Fortnite props as actors: a city's trees. remove: label stems; every actor in the level whose label starts
    with one of them goes (found in the level as it is now, never by a stored path). place: [[label, prop path,
    x, y, z, yaw, scale], ...], one actor each, put in the outliner folder `folder`. The caller sends a handful
    per call: one editor call is one frame."""
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    stems = tuple(s for s in (remove or []) if s)
    removed = 0
    if stems:
        for actor in list(actors.get_all_level_actors()):
            try:
                if actor.get_actor_label().startswith(stems) and actors.destroy_actor(actor):
                    removed += 1
            except Exception:  # noqa: BLE001 - an actor that went away while we looked
                continue
    classes: dict = {}
    placed, failed = 0, []
    for label, prop, x, y, z, yaw, scale in place or []:
        if prop not in classes:
            classes[prop] = _prop_class(prop)
        cls = classes[prop]
        actor = None
        if cls is not None:
            at, turn = unreal.Vector(x, y, z), unreal.Rotator(roll=0.0, pitch=0.0, yaw=yaw)
            try:
                actor = unreal.EditorLevelLibrary.spawn_actor_from_class(cls, at, turn)
            except Exception:  # noqa: BLE001 - the actor factory takes what the class call refuses
                actor = unreal.EditorLevelLibrary.spawn_actor_from_object(cls, at, turn)
        if actor is None:
            failed.append(prop.rsplit("/", 1)[-1])
            continue
        actor.set_actor_label(label)
        if folder:
            actor.set_folder_path(folder)
        if abs(scale - 1.0) > 1e-3:
            actor.set_actor_scale3d(unreal.Vector(scale, scale, scale))
        placed += 1
    return {"ok": True, "removed": removed, "placed": placed, "failed": sorted(set(failed))}


@register("road_network_save_assets")
def road_network_save_assets(paths: list | None = None) -> dict:
    """Save these assets in ONE save_packages batch (its own editor frame, one progress window)."""
    assets = [unreal.load_asset(p) for p in dict.fromkeys(paths or [])]
    _save_batch([a for a in assets if a is not None])
    return {"ok": True, "saved": len([a for a in assets if a is not None])}


# ponytail: the island's hand-built cinema is listed here too; make it a per-network setting when a second
# island needs its own landmark folders.
_GROUND_FOLDERS = ("/WorldGen/BuildingKit/", "/WorldGen/City/", "/WorldGen/CityPCG", "/WorldGen/Kit/Facility/",
                   "/Cinema/")


def _box_tri(x, y, yaw, bb, sx, sy, m, to_px) -> list:
    """Two triangles (pixel coords) for a mesh's footprint: its local box, scaled, turned by yaw, grown by m cm."""
    ca, sa = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    pts = [to_px(x + lx * ca - ly * sa, y + lx * sa + ly * ca)
           for lx, ly in ((bb.min.x * sx - m, bb.min.y * sy - m), (bb.max.x * sx + m, bb.min.y * sy - m),
                          (bb.max.x * sx + m, bb.max.y * sy + m), (bb.min.x * sx - m, bb.max.y * sy + m))]
    return [pts[0] + pts[1] + pts[2], pts[0] + pts[2] + pts[3]]


@register("road_network_paint_ground")
def road_network_paint_ground(layer: str = "Layer1", margin_cm: float = 150.0, folders: list | None = None) -> dict:
    """Paint `layer` (a dirt layer) on the landscape under every road piece, junction and building piece, so no
    grass shows or grows there. The layer is owned by the city: it is rewritten to exactly the current footprints,
    so a road or building that moved or went leaves no dirt behind. (Reading the layer back to keep other paint
    doesn't work: render_weightmap stays stale after an import in the same session.) Heights are untouched.
    The weightmap import reads render-target pixel i as landscape vertex i, so the target is exactly one pixel
    per vertex and footprints are drawn in vertex units."""
    acts = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    land = next((a for a in acts if a.get_class().get_name() == "Landscape"), None)
    if land is None:
        return {"ok": False, "error": "No landscape in the level."}
    if layer not in [str(n) for n in land.get_target_layer_names()]:
        return {"ok": False, "error": f"The landscape has no paint layer {layer!r} with a layer info."}
    x0, y0, q = land.get_actor_location().x, land.get_actor_location().y, land.get_actor_scale3d().x
    far = max((o.x + e.x for o, e in (p.get_actor_bounds(False) for p in acts
                                      if p.get_class().get_name() == "LandscapeStreamingProxy")), default=0.0)
    n = int(round((far - x0) / q)) + 1 if far > x0 else 2049
    to_px = lambda x, y: [(x - x0) / q + 0.5, (y - y0) / q + 0.5]
    tris, roads, junctions, pieces = [], 0, 0, 0
    keys = tuple(folders or _GROUND_FOLDERS)
    for a in acts:
        spline = a.get_class().get_name() == "LandscapeSplineActor"
        for c in a.get_components_by_class(unreal.StaticMeshComponent):
            mesh = c.static_mesh
            if mesh is None:
                continue
            bb = mesh.get_bounding_box()
            if spline and isinstance(c, unreal.SplineMeshComponent):
                t = c.get_world_transform()
                s, e = t.transform_location(c.get_start_position()), t.transform_location(c.get_end_position())
                ln = math.hypot(e.x - s.x, e.y - s.y) or 1.0
                hw = (bb.max.y - bb.min.y) / 2 + margin_cm
                nx, ny = -(e.y - s.y) / ln * hw, (e.x - s.x) / ln * hw
                p = [to_px(s.x + nx, s.y + ny), to_px(e.x + nx, e.y + ny), to_px(e.x - nx, e.y - ny), to_px(s.x - nx, s.y - ny)]
                tris += [p[0] + p[1] + p[2], p[0] + p[2] + p[3]]
                roads += 1
                continue
            if not spline and not any(k in mesh.get_path_name() for k in keys):
                continue
            if isinstance(c, unreal.InstancedStaticMeshComponent):
                ts = [c.get_instance_transform(i, True) for i in range(c.get_instance_count())]
            else:
                ts = [c.get_world_transform()]
            for t in ts:
                tris += _box_tri(t.translation.x, t.translation.y, t.rotation.rotator().yaw, bb,
                                 t.scale3d.x, t.scale3d.y, margin_cm, to_px)
            if spline:
                junctions += len(ts)
            else:
                pieces += len(ts)
    w, rl = _world(), unreal.RenderingLibrary
    fmt = unreal.TextureRenderTargetFormat.RTF_RGBA8
    rt = rl.create_render_target2d(w, n, n, fmt)
    rl.clear_render_target2d(w, rt, unreal.LinearColor(0, 0, 0, 1))
    canvas, _, ctx = rl.begin_draw_canvas_to_render_target(w, rt)
    one, V = unreal.LinearColor(1, 1, 1, 1), unreal.Vector2D
    uv = [unreal.CanvasUVTri(v0_pos=V(t[0], t[1]), v1_pos=V(t[2], t[3]), v2_pos=V(t[4], t[5]),
                             v0_color=one, v1_color=one, v2_color=one) for t in tris]
    for i in range(0, len(uv), 20000):
        canvas.draw_triangles(None, uv[i:i + 20000])  # no texture: plain white, weight 1
    rl.end_draw_canvas_to_render_target(w, ctx)
    if not land.landscape_import_weightmap_from_render_target(rt, layer, 0):
        return {"ok": False, "error": f"UEFN refused the {layer} weightmap import."}
    return {"ok": True, "layer": layer, "vertices": n, "road_pieces": roads, "junctions": junctions,
            "building_pieces": pieces, "note": "The layer now covers exactly the current roads and buildings (heights untouched). "
                    "Save the level to keep it."}


def _self_gb() -> float:
    """UEFN's own private memory in GB (Windows), 0 when it can't be read."""
    import ctypes
    from ctypes import wintypes

    class _C(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                           "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                           "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
                                           "PrivateUsage")]
    try:
        c = _C()
        c.cb = ctypes.sizeof(c)
        k32, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
        k32.GetCurrentProcess.restype = wintypes.HANDLE  # the pseudo-handle -1: needs argtypes below
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return 0.0
        return c.PrivateUsage / 2 ** 30
    except (OSError, AttributeError, ctypes.ArgumentError):
        return 0.0  # no reading: the slice still runs, only the in-call stop is off


def _kit_meshes(folder: str) -> list:
    """Static mesh paths in one folder, read from the asset registry: nothing is loaded. Loading every asset to
    check its type pulled in 256 leftover materials (each compiling shaders) and took UEFN past 100 GB."""
    reg = unreal.AssetRegistryHelpers.get_asset_registry()
    return sorted(f"{a.package_name}.{a.asset_name}" for a in reg.get_assets_by_path(folder, recursive=False)
                  if str(a.asset_class_path.asset_name) == "StaticMesh")


@register("road_network_kit_materials")
def road_network_kit_materials(root: str = "", folders: list | None = None, offset: int = 0, limit: int = 0,
                               max_grow_gb: float = 4.0) -> dict:
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
    # ponytail: one flat slice over every folder's meshes; the backend walks it `limit` meshes per call so each
    # call is its own editor frame and the memory guard sees it. max_grow_gb stops a slice early from inside.
    paths = [p for sub in folders or ["City", "Kit"] for p in _kit_meshes(f"{root}/WorldGen/{sub}")]
    total, start_gb = len(paths), _self_gb()
    end = total if limit <= 0 else min(total, offset + limit)
    i = offset
    for i, path in enumerate(paths[offset:end], offset):
        if start_gb and _self_gb() - start_gb > max_grow_gb:
            break  # handed back as `next`: the rest waits for the next call, after this frame frees its memory
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
                mi = None if fresh else eal.load_asset(mi_path)
                if mi is None and not fresh:
                    # a file cut short by a crash mid-save (~500 bytes) never loads, and its empty package then
                    # fails every save ("Packages Failed To Save"): delete it and make the MI again
                    eal.delete_asset(mi_path)
                    fresh = True
                if fresh:
                    mi = tools.create_asset(mi_path.rsplit("/", 1)[1], mat_folder, unreal.MaterialInstanceConstant,
                                            unreal.MaterialInstanceConstantFactoryNew())
                if mi is None:  # still locked by this session: the slot keeps the mesh's own material
                    per_slot.append("")
                    continue
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
    else:
        i = end
    _save_batch(created)
    try:  # free this slice's loaded meshes before the next call
        unreal.SystemLibrary.collect_garbage()
    except AttributeError:
        pass
    return {"ok": True, "master": master.get_path_name(), "instances": len(made), "created": len(created),
            "meshes": len(overrides), "overrides": overrides, "total": total,
            "next": i if i < total else None, "grew_gb": round(_self_gb() - start_gb, 1) if start_gb else None}

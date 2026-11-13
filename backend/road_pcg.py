"""Roads into UEFN as PCG instead of landscape splines.

A stored road actor (points + segments, the same data the old clipboard paste wrote) becomes, per tile:
  chains  every run of one road mesh as a polyline at road-surface height. In the tile's graph each chain is a
          Create Points node; all of a mesh's chains feed ONE Create Spline -> Spline Sampler (one tile length) ->
          Create Spline -> Spawn Spline Mesh (BlockAll), because PCG runs a node once per input data: one spline
          per chain, one mesh bent onto every sampled step.
  pieces  junction / interchange pieces sitting on a point: Static Mesh Spawner rows.
No clipboard, no Splines tool, no edit layer: the terrain is graded to the roads before it is imported.
"""

from __future__ import annotations

import math
import re

SEGMENT_OFFSET_CM = 50.0  # a segment's mesh is drawn this far up its points (SegmentMeshOffset)
TILE_CM = 80000.0  # road tiles: 800 m squares (a road graph is light, a city tile's 400 m would be 4x the volumes)
STEP_CM = {"20m": 2000.0}  # tile length by the mesh name's ending; every other road mesh is a 10 m tile
_MESH = re.compile(r"StaticMesh'([^'.]+)\.[^']+'")


def mesh_path(lines: list, key: str) -> str:
    """The /Path/To/Mesh (no .Object) of a 'SplineMeshes(0)=(Mesh=...' or 'Mesh=...' line."""
    for ln in lines or []:
        if ln.startswith(key):
            m = _MESH.search(ln)
            if m:
                return m.group(1)
    return ""


def step_of(path: str) -> float:
    name = path.rsplit("/", 1)[-1]
    return next((v for k, v in STEP_CM.items() if name.endswith(k)), 1000.0)


def chains(points: list, segments: list) -> list:
    """[{mesh, step, pts: [[x, y, z], ...]}]: every run of segments drawing the same mesh, walked point to point
    (a point where a different mesh, a link or a piece meets ends the run)."""
    by = {p["i"]: p for p in points}
    segs = []
    for s in segments:
        path = mesh_path(s.get("lines"), "SplineMeshes(0)")
        if path and s["a"] in by and s["b"] in by:
            segs.append((s, path))
    at: dict = {}
    for s, path in segs:
        for i in (s["a"], s["b"]):
            at.setdefault(i, []).append((s, path))
    done, out = set(), []

    def surface(p):
        return [round(p["x"], 1), round(p["y"], 1), round(p["z"] + SEGMENT_OFFSET_CM, 1)]

    def through(i, path):  # a point the run carries on through: exactly two segments, both this mesh, no piece
        two = at.get(i, [])
        return len(two) == 2 and all(q == path for _s, q in two) and not by[i].get("mesh")

    starts = [i for i in at if not through(i, at[i][0][1])] + list(at)  # loops: any point once the ends are used
    for start in starts:
        for s0, path in at[start]:
            if id(s0) in done:
                continue
            run, cur, s = [start], start, s0
            while True:
                done.add(id(s))
                cur = s["b"] if s["a"] == cur else s["a"]
                run.append(cur)
                if cur == start or not through(cur, path):
                    break
                s = next(x for x, _q in at[cur] if x is not s)
                if id(s) in done:
                    break
            out.append({"mesh": path, "step": step_of(path), "pts": [surface(by[i]) for i in run]})
    return out


def pieces(points: list) -> dict:
    """{mesh path: [[x, y, z, yaw, 1, 1, 1], ...]}: the meshes that sit on a point (junctions, interchanges)."""
    out: dict = {}
    for p in points:
        path = mesh_path(p.get("lines"), "Mesh=")
        if path:
            out.setdefault(path, []).append([round(p["x"], 1), round(p["y"], 1), round(p["z"], 1),
                                             round(p.get("yaw", 0.0), 3), 1, 1, 1])
    return out


def _clip(pts: list, x0: float, y0: float, x1: float, y1: float) -> list:
    """The parts of a polyline inside the box [x0, x1) x [y0, y1) (Liang-Barsky per segment, joined)."""
    parts, cur = [], []
    for a, b in zip(pts, pts[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        t0, t1 = 0.0, 1.0
        ok = True
        for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
            if abs(p) < 1e-9:
                if q < 0:
                    ok = False
                    break
                continue
            r = q / p
            if p < 0:
                t0 = max(t0, r)
            else:
                t1 = min(t1, r)
        if not ok or t0 > t1:
            if len(cur) > 1:
                parts.append(cur)
            cur = []
            continue
        pa = [a[k] + (b[k] - a[k]) * t0 for k in range(3)]
        pb = [a[k] + (b[k] - a[k]) * t1 for k in range(3)]
        if cur and math.dist(cur[-1][:2], pa[:2]) < 1.0:
            cur.append(pb)
        else:
            if len(cur) > 1:
                parts.append(cur)
            cur = [pa, pb]
        if t1 < 1.0:
            parts.append(cur)
            cur = []
    if len(cur) > 1:
        parts.append(cur)
    return [p for p in parts if sum(math.dist(u[:2], v[:2]) for u, v in zip(p, p[1:])) >= 100.0]


def tile_jobs(actors: dict, tile_cm: float = TILE_CM) -> dict:
    """{tile key: {"chains": [...], "pieces": {mesh: rows}}} for these actors ({label: {points, segments}}).
    A chain crossing tiles is cut at the tile's edge: each tile draws only its own part."""
    out: dict = {}
    for a in actors.values():
        for c in chains(a["points"], a["segments"]):
            xs, ys = [p[0] for p in c["pts"]], [p[1] for p in c["pts"]]
            for i in range(math.floor(min(xs) / tile_cm), math.floor(max(xs) / tile_cm) + 1):
                for j in range(math.floor(min(ys) / tile_cm), math.floor(max(ys) / tile_cm) + 1):
                    for part in _clip(c["pts"], i * tile_cm, j * tile_cm, (i + 1) * tile_cm, (j + 1) * tile_cm):
                        t = out.setdefault(f"{i}_{j}", {"chains": [], "pieces": {}})
                        t["chains"].append({**c, "pts": [[round(v, 1) for v in p] for p in part]})
        for mesh, rows in pieces(a["points"]).items():
            for r in rows:
                key = f"{math.floor(r[0] / tile_cm)}_{math.floor(r[1] / tile_cm)}"
                out.setdefault(key, {"chains": [], "pieces": {}})["pieces"].setdefault(mesh, []).append(r)
    return out


def _demo() -> None:
    road = "SplineMeshes(0)=(Mesh=\"/Script/Engine.StaticMesh'/G/R/SM_Road_FourLane_Straight10m.SM_Road_FourLane_Straight10m'\",bScaleToWidth=False)"
    fw = road.replace("SM_Road_FourLane_Straight10m", "SM_Road_Freeway_Lane20m")
    jk = "Mesh=\"/Script/Engine.StaticMesh'/G/R/J/SM_JK_1.SM_JK_1'\""
    P = lambda i, x, y, z=0.0, ln=(): {"i": i, "x": x, "y": y, "z": z, "yaw": 0.0, "lines": list(ln), "mesh": "SM_JK_1" if ln else ""}  # noqa: E731
    pts = [P(0, 0, 0), P(1, 50000, 0), P(2, 100000, 0, 0, [jk]), P(3, 100000, 1500), P(4, 100000, 90000), P(5, 150000, 0)]
    S = lambda i, a, b, ln: {"i": i, "a": a, "b": b, "lines": [ln] if ln else []}  # noqa: E731
    segs = [S(10, 0, 1, road), S(11, 1, 2, road), S(12, 2, 3, ""), S(13, 3, 4, fw), S(14, 2, 5, road)]
    ch = chains(pts, segs)
    by_mesh = sorted((c["mesh"].rsplit("/", 1)[1], len(c["pts"])) for c in ch)
    assert by_mesh == [("SM_Road_FourLane_Straight10m", 2), ("SM_Road_FourLane_Straight10m", 3),
                       ("SM_Road_Freeway_Lane20m", 2)], by_mesh  # the piece at 2 ends both runs; the link draws nothing
    assert all(p[2] == SEGMENT_OFFSET_CM for c in ch for p in c["pts"]), "at road-surface height"
    assert next(c for c in ch if "Freeway" in c["mesh"])["step"] == 2000.0
    assert pieces(pts) == {"/G/R/J/SM_JK_1": [[100000, 0, 0, 0.0, 1, 1, 1]]}
    tiles = tile_jobs({"R": {"points": pts, "segments": segs}})
    assert set(tiles) == {"0_0", "1_0", "1_1"}, sorted(tiles)  # the 1.5 km road and the freeway cross three tiles
    total = sum(math.dist(a[:2], b[:2]) for t in tiles.values() for c in t["chains"] for a, b in zip(c["pts"], c["pts"][1:]))
    assert abs(total - (150000 + 88500)) < 5, total  # cut at the edges, nothing lost or drawn twice
    assert tiles["1_0"]["pieces"] == {"/G/R/J/SM_JK_1": [[100000, 0, 0, 0.0, 1, 1, 1]]}
    print("road pcg ok", {k: len(v["chains"]) for k, v in sorted(tiles.items())})


if __name__ == "__main__":
    _demo()
